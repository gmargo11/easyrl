import time
from collections import deque
from itertools import chain
from itertools import count

import numpy as np
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

from easyrl.configs.ppo_config import ppo_cfg
from easyrl.engine.basic_engine import BasicEngine
from easyrl.utils.common import get_list_stats
from easyrl.utils.common import save_traj
from easyrl.utils.gae import cal_gae
from easyrl.utils.rl_logger import TensorboardLogger
from easyrl.utils.torch_util import EpisodeDataset
from easyrl.utils.torch_util import torch_to_np


class PPOEngine(BasicEngine):
    def __init__(self, agent, runner):
        super().__init__(agent=agent,
                         runner=runner)
        self.cur_step = 0
        self._best_eval_ret = -np.inf
        self._eval_is_best = False
        if ppo_cfg.test or ppo_cfg.resume:
            self.cur_step = self.agent.load_model(step=ppo_cfg.resume_step)
        else:
            if ppo_cfg.pretrain_model is not None:
                self.agent.load_model(pretrain_model=ppo_cfg.pretrain_model)
            ppo_cfg.create_model_log_dir()
        self.train_ep_return = deque(maxlen=100)
        self.smooth_eval_return = None
        self.smooth_tau = ppo_cfg.smooth_eval_tau
        self.optim_stime = None
        if not ppo_cfg.test:
            self.tf_logger = TensorboardLogger(log_dir=ppo_cfg.log_dir)

    def train(self):
        for iter_t in count():
            traj, rollout_time = self.rollout_once(sample=ppo_cfg.sample_action,
                                                   time_steps=ppo_cfg.episode_steps)
            train_log_info = self.train_once(traj)
            if iter_t % ppo_cfg.eval_interval == 0:
                eval_log_info, _ = self.eval()
                self.agent.save_model(is_best=self._eval_is_best,
                                      step=self.cur_step)
            else:
                eval_log_info = None
            if iter_t % ppo_cfg.log_interval == 0:
                train_log_info['train/rollout_time'] = rollout_time
                if eval_log_info is not None:
                    train_log_info.update(eval_log_info)
                if ppo_cfg.linear_decay_lr:
                    train_log_info.update(self.agent.get_lr())
                if ppo_cfg.linear_decay_clip_range:
                    train_log_info.update(dict(clip_range=ppo_cfg.clip_range))
                scalar_log = {'scalar': train_log_info}
                self.tf_logger.save_dict(scalar_log, step=self.cur_step)
            if self.cur_step > ppo_cfg.max_steps:
                break
            if ppo_cfg.linear_decay_lr:
                self.agent.decay_lr()
            if ppo_cfg.linear_decay_clip_range:
                self.agent.decay_clip_range()

    @torch.no_grad()
    def eval(self, render=False, save_eval_traj=False, eval_num=1, sleep_time=0, smooth=True, no_tqdm=None):
        time_steps = []
        rets = []
        lst_step_infos = []
        if no_tqdm:
            disable_tqdm = bool(no_tqdm)
        else:
            disable_tqdm = not ppo_cfg.test
        for idx in tqdm(range(eval_num), disable=disable_tqdm):
            traj, _ = self.rollout_once(time_steps=ppo_cfg.episode_steps,
                                        return_on_done=True,
                                        sample=ppo_cfg.sample_action,
                                        render=render,
                                        sleep_time=sleep_time,
                                        render_image=save_eval_traj,
                                        evaluation=True)
            tsps = traj.steps_til_done.copy().tolist()
            rewards = traj.raw_rewards
            infos = traj.infos
            for ej in range(traj.num_envs):
                ret = np.sum(rewards[:tsps[ej], ej])
                rets.append(ret)
                lst_step_infos.append(infos[tsps[ej] - 1][ej])
            time_steps.extend(tsps)
            if save_eval_traj:
                save_traj(traj, ppo_cfg.eval_dir)

        raw_traj_info = {'return': rets,
                         'episode_length': time_steps,
                         'lst_step_info': lst_step_infos}
        log_info = dict()
        for key, val in raw_traj_info.items():
            if 'info' in key:
                continue
            val_stats = get_list_stats(val)
            for sk, sv in val_stats.items():
                log_info['eval/' + key + '/' + sk] = sv
        if smooth:
            if self.smooth_eval_return is None:
                self.smooth_eval_return = log_info['eval/return/mean']
            else:
                self.smooth_eval_return = self.smooth_eval_return * self.smooth_tau
                self.smooth_eval_return += (1 - self.smooth_tau) * log_info['eval/return/mean']
            log_info['eval/smooth_return/mean'] = self.smooth_eval_return
            if self.smooth_eval_return > self._best_eval_ret:
                self._eval_is_best = True
                self._best_eval_ret = self.smooth_eval_return
            else:
                self._eval_is_best = False
        return log_info, raw_traj_info

    def rollout_once(self, *args, **kwargs):
        t0 = time.perf_counter()
        self.agent.eval_mode()
        traj = self.runner(**kwargs)
        t1 = time.perf_counter()
        elapsed_time = t1 - t0
        return traj, elapsed_time

    def train_once(self, traj):
        self.optim_stime = time.perf_counter()
        self.cur_step += traj.total_steps
        rollout_dataloader = self.traj_preprocess(traj)
        optim_infos = []
        for oe in range(ppo_cfg.opt_epochs):
            for batch_ndx, batch_data in enumerate(rollout_dataloader):
                optim_info = self.agent.optimize(batch_data)
                optim_infos.append(optim_info)
        return self.get_train_log(optim_infos, traj)

    def traj_preprocess(self, traj):
        action_infos = traj.action_infos
        vals = np.array([ainfo['val'] for ainfo in action_infos])
        log_prob = np.array([ainfo['log_prob'] for ainfo in action_infos])
        adv = self.cal_advantages(traj)
        ret = adv + vals
        if ppo_cfg.normalize_adv:
            adv = adv.astype(np.float64)
            adv = (adv - np.mean(adv)) / (np.std(adv) + 1e-8)
        data = dict(
            ob=traj.obs,
            action=traj.actions,
            ret=ret,
            adv=adv,
            log_prob=log_prob,
            val=vals
        )
        rollout_dataset = EpisodeDataset(**data)
        rollout_dataloader = DataLoader(rollout_dataset,
                                        batch_size=ppo_cfg.batch_size,
                                        shuffle=True)
        return rollout_dataloader

    def cal_advantages(self, traj):
        rewards = traj.rewards
        action_infos = traj.action_infos
        vals = np.array([ainfo['val'] for ainfo in action_infos])
        last_val = traj.extra_data['last_val']
        adv = cal_gae(gamma=ppo_cfg.rew_discount,
                      lam=ppo_cfg.gae_lambda,
                      rewards=rewards,
                      value_estimates=vals,
                      last_value=last_val,
                      dones=traj.dones)
        return adv

    def get_train_log(self, optim_infos, traj):
        log_info = dict()
        for key in optim_infos[0].keys():
            log_info[key] = np.mean([inf[key] for inf in optim_infos if key in inf])
        t1 = time.perf_counter()
        actions_stats = get_list_stats(traj.actions)
        for sk, sv in actions_stats.items():
            log_info['rollout_action/' + sk] = sv
        log_info['optim_time'] = t1 - self.optim_stime
        log_info['rollout_steps_per_iter'] = traj.total_steps

        # log infos
        for key in traj.infos[0][0].keys():
            info_list = [tuple([info.get(key, default=0) for info in infos]) for infos in traj.infos]
            info_stats = get_list_stats(info_list)
            for sk, sv in info_stats.items():
                log_info['rollout_{}.'.format(key) + sk] = sv

        ep_returns = list(chain(*traj.episode_returns))
        for epr in ep_returns:
            self.train_ep_return.append(epr)
        ep_returns_stats = get_list_stats(self.train_ep_return)
        for sk, sv in ep_returns_stats.items():
            log_info['episode_return/' + sk] = sv

        train_log_info = dict()
        for key, val in log_info.items():
            train_log_info['train/' + key] = val
        # histogram_log = {'histogram': {'rollout_action': traj.actions}}
        # self.tf_logger.save_dict(histogram_log, step=self.cur_step)
        return train_log_info
