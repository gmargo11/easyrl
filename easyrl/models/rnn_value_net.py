import torch.nn as nn


class RNNValueNet(nn.Module):
    def __init__(self,
                 body_net,
                 in_features=None):  # add tanh on the action distribution
        super().__init__()
        self.body = body_net
        if in_features is None:
            for i in reversed(range(len(self.body.fcs))):
                layer = self.body.fcs[i]
                if hasattr(layer, 'out_features'):
                    in_features = layer.out_features
                    break
        self.head = nn.Linear(in_features, 1)

    def forward(self, x=None, body_x=None, hidden_state=None, **kwargs):
        if x is None and body_x is None:
            raise ValueError('One of [x, body_x] should be provided!')
        if body_x is None:
            body_x, hidden_state = self.body(x,
                                             hidden_state=hidden_state,
                                             **kwargs)
        val = self.head(body_x)
        #print('val', val.shape)
        #val = val.reshape(-1, 1)
        #print('val', val.shape)
        return val, body_x, hidden_state
