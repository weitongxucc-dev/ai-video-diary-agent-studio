"""Apple Silicon adapter for the verified FP16 conversion of LatentSync 1.6."""

from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path
import platform
import shutil
import sys

FRAME_RATE = 25
SAMPLE_RATE = 16_000
WINDOW_FRAMES = 16
RESOLUTION = 512
STEPS = 20
GUIDANCE = 1.5
SEED = 1247
UPSTREAM_REVISION = "a229c3948406bc2cf6eaf4873e662e70c6a04746"
WEIGHTS_REVISION = "c42c7e6c8e9c213626389fa7d9a3c444b8536353"
MLX_REVISION = "8370f1ff2b7c240c9ea78cbeb134c254336f6806"
# Apache-2.0 upstream port and official utility snapshot with verified local
# timing, memory and inference corrections, including the pose adapter.
SOURCE_SHA256 = "0486e1768ac1a461ba7a4d28bfd9d8a4e8aa4f3010d364ab99ce099d096a419c"
UNET_FILE = ("unet/latentsync_unet_mlx.safetensors", 2536073912,
             "dac23792c44475e48f8a3fa40f8db53c56b9d5ab97f41044297f12040bf3972b")
RUNTIME_WEIGHTS = (
    ("vae/vae_mlx.safetensors", 334642751, "72ecb7078b4eb683204cf6e1501ac3d3945bfcd248c5d82be31d98bc1c355044"),
    ("pose/dw-ll_ucoco_384.onnx", 134399116, "724f4ff2439ed61afb86fb8a1951ec39c6220682803b4a8bd4f598cd913b1843"),
)
CALIBRATION_FILES = {
    "visual.mp4": "7895b7d22283215aa9555b657964d6a84fd0bfc8c9e51271e9f981ceb80f32e3",
    "driver.wav": "b8370f2f08477303202fd54f2f61ef27f175decc1fef27a8d6a430b491b36946",
}
_REQUIRED_SOURCE = (
    "LICENSE", "upstream/LICENSE", "corrected_norms.py", "corrected_sampler.py",
    "posterior_sampling.py", "sliced_spatial_attention.py", "pose/official_onnx.py",
    "upstream/latentsync/utils/affine_transform.py", "upstream/latentsync/utils/image_processor.py",
    "upstream/latentsync/utils/util.py", "upstream/latentsync/utils/mask.png",
)


def source_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted([
        *(root / name for name in _REQUIRED_SOURCE),
        *(root / "latentsync_mlx").rglob("*.py"),
        *(path for path in (root / "upstream/latentsync/whisper").rglob("*")
          if path.is_file() and "__pycache__" not in path.parts),
    ]))


def verify_source(root: Path) -> None:
    digest = hashlib.sha256()
    for path in source_files(root):
        if not path.is_file():
            raise ValueError(f"LatentSync 1.6 MLX 安装缺少 {path.relative_to(root)}")
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    if digest.hexdigest() != SOURCE_SHA256:
        raise ValueError("LatentSync 1.6 MLX 源码与已验证的推理版本不一致，请重新准备本地运行环境。")


def verify_weights(root: Path, *, checksums: bool) -> None:
    for relative, size, expected in (UNET_FILE, *RUNTIME_WEIGHTS):
        path = root / relative
        if not path.is_file() or path.stat().st_size != size:
            raise ValueError(f"LatentSync 1.6 MLX 离线权重缺失或大小不符：{relative}")
        if checksums:
            digest = hashlib.sha256()
            with path.open("rb") as file:
                for block in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise ValueError(f"LatentSync 1.6 MLX 离线权重校验失败：{relative}")


def configure_imports(root: Path) -> None:
    sys.path[:0] = [str(root / name) for name in ("", "dependencies", "upstream", "pose", "pose/dependencies")]
    os.environ["LATENTSYNC_POSE_ROOT_PATH"] = str(root / "pose")
    os.environ["TQDM_DISABLE"] = "1"


def check_runtime(root: Path, revision: str) -> None:
    if revision != UPSTREAM_REVISION:
        raise ValueError(f"LatentSync 1.6 MLX 对应的官方源码版本应为 {UPSTREAM_REVISION}")
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ValueError("LatentSync 1.6 MLX 需要原生 Apple Silicon Python。")
    verify_source(root)
    verify_weights(root, checksums=False)
    for name in ("ffmpeg", "ffprobe"):
        if shutil.which(name) is None:
            raise ValueError(f"LatentSync 1.6 MLX 缺少 {name}")
    configure_imports(root)
    import mlx.core as mx
    import torch
    for module in ("librosa", "soundfile", "corrected_norms", "corrected_sampler", "latentsync_entry",
                   "latentsync_mlx.pipeline", "latentsync.whisper.audio2feature",
                   "posterior_sampling", "sliced_spatial_attention"):
        importlib.import_module(module)

    if not mx.metal.is_available() or not torch.backends.mps.is_available():
        raise ValueError("LatentSync 1.6 MLX 需要可用的 Metal 和 PyTorch MPS。")



class _StagedWeights:
    """Retain the lazy model structures while materializing one checkpoint at a time."""

    def __init__(self, mx, *, unet, vae, root: Path):
        self.mx = mx
        self.models = {"vae": (vae, root / RUNTIME_WEIGHTS[0][0]),
                       "unet": (unet, root / UNET_FILE[0])}
        # Capture the unevaluated initialization trees before the pipeline
        # seeds its noise. Reusing them avoids advancing its seeded PRNG when
        # the checkpoint changes, and preserves the strict load shape checks.
        self.placeholders = {name: model.parameters() for name, (model, _) in self.models.items()}
        self.active = None

    def load(self, name):
        model, path = self.models[name]
        if self.active != name:
            self.release()
            self.active = name
            model.load_weights([(key, value.astype(self.mx.float16))
                                for key, value in self.mx.load(str(path)).items()])
            self.mx.eval(model.parameters())
            print(f"LatentSync MLX weights={name} active_bytes={self.mx.get_active_memory()} "
                  f"peak_bytes={self.mx.get_peak_memory()}", flush=True)
        return model

    def release(self):
        if self.active is not None:
            self.mx.synchronize()
            self.models[self.active][0].update(self.placeholders[self.active])
            self.active = None
            self.mx.clear_cache()

def render(root: Path, whisper: Path, video: Path, audio: Path, output: Path, scratch: Path,
           *, memory_limit: int = 8 * 1024**3) -> int:
    import cv2
    import mlx.core as mx
    import torch

    from corrected_norms import apply_norm_corrections
    from corrected_sampler import CorrectedDDIMSampler
    from latentsync_entry import BrowImageProcessor
    from latentsync_mlx.pipeline import LipsyncPipelineMLX
    from latentsync_mlx.unet import UNet3DConditionModel
    from latentsync_mlx.vae import Autoencoder
    from latentsync.utils.image_processor import load_fixed_mask
    from latentsync.whisper.audio2feature import Audio2Feature
    from posterior_sampling import encode_with_posterior
    from sliced_spatial_attention import apply_sliced_attention

    class SerialGuidanceUNet(UNet3DConditionModel):
        def __call__(self, x, timestep, encoder_hidden_states=None):
            predictions = []
            for index in range(x.shape[0]):
                cond = encoder_hidden_states[index:index + 1]
                value = super().__call__(x[index:index + 1], timestep, encoder_hidden_states=cond)
                mx.eval(value)
                if mx.get_peak_memory() > memory_limit:
                    raise RuntimeError(f"LatentSync 1.6 MLX 峰值超过本机许可的 {memory_limit / 1024**3:.2f} GiB")
                predictions.append(value)
                mx.clear_cache()
            return mx.concatenate(predictions, axis=0)

    class PosteriorPipeline(LipsyncPipelineMLX):
        def _stage_face_windows(self, video_path, frame_count, frames_per_window, temp_dir):
            windows = super()._stage_face_windows(video_path, frame_count, frames_per_window, temp_dir)
            # BrowImageProcessor keeps the real ONNX detector in `pose`.
            # Restoring the output frames only needs its affine restorer.
            self.image_processor.pose = None
            return windows

        def _vae_encode(self, images, batch_size=1):
            staged.load("vae")
            latents = encode_with_posterior(self, images, batch_size)
            mx.eval(latents)
            return latents

        def _vae_decode(self, latents, batch_size=1):
            staged.load("vae")
            frames = super()._vae_decode(latents, batch_size)
            mx.eval(frames)
            return frames

        def _restore_video(self, *args):
            staged.release()
            return super()._restore_video(*args)

    threads = int(os.environ.get("OMP_NUM_THREADS", "1"))
    torch.set_num_threads(threads)
    cv2.setNumThreads(threads)
    apply_norm_corrections()
    apply_sliced_attention()
    mx.set_cache_limit(256 * 1024**2)
    mx.set_memory_limit(memory_limit)
    unet, vae = SerialGuidanceUNet(), Autoencoder()
    staged = _StagedWeights(mx, unet=unet, vae=vae, root=root)

    def run_unet(sample, timestep, *, encoder_hidden_states):
        return staged.load("unet")(sample, timestep, encoder_hidden_states=encoder_hidden_states)

    encoder = Audio2Feature(model_path=str(whisper), device=torch.device("mps"),
                            num_frames=WINDOW_FRAMES, audio_feat_length=[2, 2])
    mask = load_fixed_mask(RESOLUTION, mask_image_path=str(root / "upstream/latentsync/utils/mask.png"))
    processor = BrowImageProcessor(resolution=RESOLUTION, device="mps", mask_image=mask, threads=threads)
    pipeline = PosteriorPipeline(unet=run_unet, vae=vae, sampler=CorrectedDDIMSampler(),
                                audio_encoder=encoder, image_processor=processor,
                                resolution=RESOLUTION, dtype=mx.float16)
    # Let the pipeline release Whisper before any MLX checkpoint is loaded.
    del encoder, processor, unet, vae
    try:
        pipeline(video_path=str(video), audio_path=str(audio), video_out_path=str(output),
                 num_frames=WINDOW_FRAMES, num_inference_steps=STEPS, guidance_scale=GUIDANCE,
                 video_fps=FRAME_RATE, audio_sample_rate=SAMPLE_RATE, seed=SEED, temp_dir=str(scratch))
        peak = mx.get_peak_memory()
        if peak > memory_limit:
            raise RuntimeError(f"LatentSync 1.6 MLX 峰值超过本机许可的 {memory_limit / 1024**3:.2f} GiB")
        return peak
    finally:
        staged.release()
