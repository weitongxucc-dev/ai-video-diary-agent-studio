"""Bound spatial attention batches without splitting any attention sequence."""
import mlx.core as mx
from latentsync_mlx.attention import Attention

original_attention = Attention.__call__

def sliced_attention(self, x, encoder_hidden_states=None):
    if x.shape[0] <= 1 or x.shape[1] < 1024:
        return original_attention(self,x,encoder_hidden_states)
    parts = []
    for index in range(x.shape[0]):
        context = None if encoder_hidden_states is None else encoder_hidden_states[index:index+1]
        part = original_attention(self,x[index:index+1],context)
        mx.eval(part)
        parts.append(part)
    return mx.concatenate(parts,axis=0)

def apply_sliced_attention():
    Attention.__call__ = sliced_attention
