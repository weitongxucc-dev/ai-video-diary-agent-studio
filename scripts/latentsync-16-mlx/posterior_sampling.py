"""Match upstream DiagonalGaussianDistribution sampling, isolated candidate only."""
import mlx.core as mx

def sample_posterior(mean, logvar, noise=None):
    logvar = mx.clip(logvar, -30.0, 20.0)
    std = mx.exp(0.5 * logvar).astype(mean.dtype)
    if noise is None:
        noise = mx.random.normal(mean.shape).astype(mean.dtype)
    return mean + std * noise

def encode_with_posterior(self, images, batch_size=1):
    latents = []
    for i in range(0, images.shape[0], batch_size):
        mean, logvar = self.vae.encode(images[i:i + batch_size])
        sampled = sample_posterior(mean, logvar)
        latents.append((sampled - self.vae.shift_factor) * self.vae.scaling_factor)
        mx.eval(latents[-1])
    return mx.concatenate(latents, axis=0)
