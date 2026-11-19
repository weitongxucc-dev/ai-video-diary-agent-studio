"""Candidate-only DDIM parity with upstream scheduler_config.json, eta=0."""
import mlx.core as mx
from latentsync_mlx.sampler import DDIMSampler

class CorrectedDDIMSampler(DDIMSampler):
    def set_timesteps(self, num_inference_steps):
        step_ratio=self.num_train_timesteps//num_inference_steps
        self._timesteps=[i*step_ratio+1 for i in range(num_inference_steps)][::-1]

    def step(self, noise_pred, timestep, sample, eta=0.0):
        if eta != 0:
            raise ValueError('This isolated parity candidate only supports deterministic eta=0')
        previous=timestep-self.num_train_timesteps//len(self._timesteps)
        alpha=self.alphas_cumprod[timestep]
        # Upstream set_alpha_to_one=False.
        alpha_previous=self.alphas_cumprod[max(previous,0)]
        # PyTorch 0-d scalar factors retain the tensor's inference dtype.
        # Explicit MLX casts prevent the first sample promoting the whole UNet.
        sqrt_beta=mx.sqrt(1-alpha).astype(sample.dtype)
        sqrt_alpha=mx.sqrt(alpha).astype(sample.dtype)
        original=(sample-sqrt_beta*noise_pred)/sqrt_alpha
        direction=mx.sqrt(1-alpha_previous).astype(sample.dtype)*noise_pred
        return mx.sqrt(alpha_previous).astype(sample.dtype)*original+direction
