"""Restore official stage2 norm axes and epsilon; candidate implementation only."""
import mlx.nn as nn
from latentsync_mlx.resnet import InflatedGroupNorm
from latentsync_mlx.attention import Transformer3DModel
from latentsync_mlx.motion_module import TemporalTransformer3DModel
from latentsync_mlx.vae import Autoencoder

def apply_norm_corrections():
    # Official stage2 omits use_inflated_groupnorm; default False means
    # GroupNorm normalizes across F,H,W jointly in ResNets and conv_norm_out.
    InflatedGroupNorm.__call__ = nn.GroupNorm.__call__
    for cls in (Transformer3DModel, TemporalTransformer3DModel):
        original = cls.__init__
        def corrected(self, *args, _init=original, **kwargs):
            _init(self, *args, **kwargs)
            self.norm.eps = 1e-6
        cls.__init__ = corrected
    original_vae = Autoencoder.__init__
    def corrected_vae(self, *args, **kwargs):
        original_vae(self, *args, **kwargs)
        for _, module in self.named_modules():
            if isinstance(module, nn.GroupNorm):
                module.eps = 1e-6
    Autoencoder.__init__ = corrected_vae
