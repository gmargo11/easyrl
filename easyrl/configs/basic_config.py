import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from easyrl.utils.common import get_git_infos
from easyrl.utils.common import save_to_json
from easyrl.utils.rl_logger import logger


@dataclass
class BasicConfig:
    env_id: str = None
    seed: int = 1
    device: str = 'cuda'
    save_dir: str = 'data'
    eval_interval: int = 100
    log_interval: int = 10
    weight_decay: float = 0.00
    max_grad_norm: float = None
    batch_size: int = 32
    save_best_only: bool = False
    smooth_eval_tau: float = 0.70
    max_saved_models: int = 2
    test: bool = False
    test_num: int = 1
    save_test_traj: bool = False
    resume: bool = False
    resume_step: int = None
    render: bool = False
    pretrain_model: str = None
    sample_action: bool = True
    extra_cfgs: dict = None

    @property
    def root_dir(self):
        return Path(__file__).resolve().parents[2]

    @property
    def data_dir(self):
        if hasattr(self, 'diff_cfg') and 'save_dir' in self.diff_cfg:
            # if 'save_dir' is given, then it will just
            # use it as the data dir
            save_dir = Path(self.save_dir)
            if save_dir.is_absolute():
                data_dir = save_dir
            else:
                data_dir = Path.cwd().joinpath(self.save_dir)
            if 'seed_' in data_dir.name:
                return data_dir
            else:
                return data_dir.joinpath(f'seed_{self.seed}')
        data_dir = Path.cwd().joinpath(self.save_dir)
        if self.env_id is not None:
            data_dir = data_dir.joinpath(self.env_id)
        skip_params = ['env_id',
                       'save_dir',
                       'resume',
                       'resume_step',
                       'test',
                       'save_best_only',
                       'log_interval',
                       'eval_interval',
                       'render',
                       'seed',
                       'pretrain_model']
        if hasattr(self, 'diff_cfg'):
            if 'test' in self.diff_cfg:
                skip_params.append('num_envs')
            diff_cfg = {k: v for k, v in self.diff_cfg.items()
                        if k not in skip_params}
        else:
            diff_cfg = {}
        if len(diff_cfg) > 0:
            path_name = ''
            for key, val in diff_cfg.items():
                if not path_name:
                    path_name += f'{key}_{val}'
                else:
                    path_name += f'_{key}_{val}'
            data_dir = data_dir.joinpath(path_name)
            data_dir = data_dir.joinpath(f'seed_{self.seed}')
        else:
            data_dir = data_dir.joinpath(f'seed_{self.seed}')

        return data_dir

    @property
    def model_dir(self):
        return self.data_dir.joinpath('model')

    @property
    def log_dir(self):
        return self.data_dir.joinpath('log')

    @property
    def eval_dir(self):
        return self.data_dir.joinpath('eval')

    def create_model_log_dir(self):
        if self.model_dir.exists():
            shutil.rmtree(self.model_dir, ignore_errors=True)
        if self.log_dir.exists():
            shutil.rmtree(self.log_dir, ignore_errors=True)
        Path.mkdir(self.model_dir, parents=True)
        Path.mkdir(self.log_dir, parents=True)
        hp_file = self.data_dir.joinpath('hp.json')
        hps = self.__dict__
        hps['git_info'] = get_git_infos(self.root_dir)
        save_to_json(hps, hp_file)

    def create_eval_dir(self):
        if self.eval_dir.exists():
            shutil.rmtree(self.eval_dir)
        Path.mkdir(self.eval_dir, parents=True)

    def restore_cfg(self, skip_params=None, path=None):
        if path is None:
            path = self.data_dir
        hp_file = path.joinpath('hp.json')
        with hp_file.open() as f:
            cfg_stored = json.load(f)
        if skip_params is None:
            skip_params = []

        skip_params.extend(['resume',
                            'resume_step',
                            'render',
                            'test',
                            'test_num',
                            'eval_num_envs',
                            'save_test_traj',
                            'save_dir',
                            'diff_cfg'])
        for key, val in cfg_stored.items():
            if hasattr(self, key) and key not in skip_params:
                setattr(self, key, val)
                logger.info(f'Restoring {key} to {val}.')
