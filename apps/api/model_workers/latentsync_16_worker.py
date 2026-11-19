"""Execute LatentSync 1.6 offline on CUDA or Apple Silicon."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import wave

import latentsync_16_mlx as mlx

from media_timing import MediaTimingError, probe_avatar_media, probe_media_duration


def _runtime_check(runtime: Path, revision: str) -> None:
    upstream = runtime / "upstream"
    result = subprocess.run(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], check=True,
        text=True, capture_output=True, timeout=5,
    )
    if result.stdout.strip() != revision:
        raise ValueError(f"LatentSync 1.6 官方推理代码修订不匹配，应为 {revision}")
    subprocess.run(
        ["git", "-C", str(upstream), "diff", "--quiet", "HEAD", "--", "scripts", "configs", "latentsync"],
        check=True, capture_output=True, timeout=5,
    )
    status = subprocess.run(
        ["git", "-C", str(upstream), "ls-files", "--others", "--exclude-standard", "--",
         "scripts", "configs", "latentsync"], check=True, text=True, capture_output=True, timeout=5,
    )
    if status.stdout.strip():
        raise ValueError("LatentSync 1.6 CUDA 源码目录含非官方文件，无法核验运行时。")
    from omegaconf import OmegaConf
    config = OmegaConf.load(upstream / "configs/unet/stage2_512.yaml")
    if config.data.resolution != 512 or config.data.video_fps != 25 or config.model.cross_attention_dim != 384:
        raise ValueError("LatentSync 1.6 需要官方 512×512、25fps 和 Whisper tiny 的推理配置")
    for package in ("diffusers", "decord", "insightface", "accelerate", "DeepCache"):
        if importlib.util.find_spec(package) is None:
            raise ValueError(f"LatentSync 1.6 CUDA 环境缺少 {package}")
    import torch
    import onnxruntime

    if not torch.cuda.is_available():
        raise ValueError("LatentSync 1.6 官方运行时未检测到可用 NVIDIA CUDA 设备")
    device = torch.cuda.get_device_properties(torch.cuda.current_device())
    if device.total_memory < 18_000_000_000:
        raise ValueError(f"LatentSync 1.6 官方 512 推理需要至少 18 GB 显存，当前设备约 {device.total_memory / 1e9:.1f} GB")
    if "CUDAExecutionProvider" not in onnxruntime.get_available_providers():
        raise ValueError("LatentSync 1.6 人脸定位缺少 ONNX Runtime CUDA 执行环境")
    for name in ("ffmpeg", "ffprobe"):
        if shutil.which(name) is None:
            raise ValueError(f"LatentSync 1.6 运行环境缺少 {name}")


def _file(path: Path) -> Path:
    if not path.is_file() or not path.stat().st_size:
        raise ValueError(f"LatentSync 1.6 输入或离线依赖缺失：{path.name}")
    return path.resolve()


def _stage_file(source: Path, destination: Path) -> None:
    """Hard link data on the same volume, with portable cross-volume staging."""
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _command(command, *, cwd=None, env=None, timeout=300) -> None:
    completed = subprocess.run(command, cwd=cwd, env=env, text=True,
                               capture_output=True, check=False, timeout=timeout)
    if completed.returncode:
        raise RuntimeError((completed.stderr.strip() or completed.stdout.strip())[-3200:] or
                           f"LatentSync 1.6 子进程退出：{completed.returncode}")


def _render(payload, runtime: Path, weights: Path, revision: str, backend: str,
            presenter_shape: dict | None = None) -> dict[str, object]:
    if payload.get("schema_version") != 1 or payload.get("model_id") != "latentsync-1.6-512":
        raise ValueError("口型请求必须固定为 LatentSync 1.6 · 512")
    if (payload.get("language") not in {"zh", "en"} or payload.get("motion") != "natural"
            or payload.get("expression") != "neutral"):
        raise ValueError("LatentSync 1.6 工作器接受中文或英文配音、自然动作和中性表情")
    source = _file(Path(payload["visual_path"]))
    audio = _file(Path(payload["audio_path"]))
    output = Path(payload["output_path"]).resolve()
    duration = probe_media_duration(audio)
    if backend == "mlx":
        return _render_mlx(runtime, weights, revision, source, audio, output, duration, presenter_shape)
    _runtime_check(runtime, revision)
    upstream = runtime / "upstream"
    checkpoint = _file(weights / "latentsync_unet.pt")
    whisper = _file(weights / "whisper/tiny.pt")
    vae = runtime / "vae"
    face = runtime / "face/buffalo_l"
    _file(face / "det_10g.onnx")
    _file(face / "2d106det.onnx")
    _file(vae / "config.json")
    if not any((vae / name).is_file() for name in (
        "diffusion_pytorch_model.safetensors", "diffusion_pytorch_model.bin"
    )):
        raise ValueError("LatentSync 1.6 未找到离线 VAE 权重")

    with TemporaryDirectory(prefix="latentsync_16_") as temporary:
        work = Path(temporary)
        corpus = work / "checkpoints"
        for name in ("scripts", "configs", "latentsync"):
            shutil.copytree(upstream / name, work / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        detection_models = corpus / "auxiliary/models/buffalo_l"
        for path in (corpus / "whisper", detection_models):
            path.mkdir(parents=True)
        _stage_file(whisper, corpus / "whisper/tiny.pt")
        for name in ("det_10g.onnx", "2d106det.onnx"):
            _stage_file(face / name, detection_models / name)

        # Upstream looks up this VAE by repository name. Bind that lookup to the
        # installation's local snapshot and prevent implicit Hub downloads.
        hub = work / "hub"
        local = hub / "models--stabilityai--sd-vae-ft-mse"
        snapshot = "latentsync-local-vae"
        (local / "refs").mkdir(parents=True)
        staged_vae = local / "snapshots" / snapshot
        staged_vae.mkdir(parents=True)
        (local / "refs/main").write_text(snapshot, encoding="utf-8")
        _stage_file(vae / "config.json", staged_vae / "config.json")
        for name in ("diffusion_pytorch_model.safetensors", "diffusion_pytorch_model.bin"):
            if (vae / name).is_file():
                _stage_file(vae / name, staged_vae / name)
                break

        video = work / "source.mp4"
        speech = work / "speech.wav"
        rendered = work / "render.mp4"
        _command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1",
                  "-i", str(source), "-t", f"{duration:.6f}", "-map", "0:v:0", "-an", "-vf", "fps=25",
                  "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(video)])
        _command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(audio), "-vn", "-ar", "16000",
                  "-ac", "1", "-c:a", "pcm_s16le", str(speech)])
        env = {**os.environ, "HF_HUB_CACHE": str(hub), "HUGGINGFACE_HUB_CACHE": str(hub),
               "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        _command([
            sys.executable, "-m", "scripts.inference",
            "--unet_config_path", "configs/unet/stage2_512.yaml",
            "--inference_ckpt_path", str(checkpoint),
            "--inference_steps", "20", "--guidance_scale", "1.5", "--enable_deepcache", "--seed", "1247",
            "--video_path", str(video), "--audio_path", str(speech),
            "--video_out_path", str(rendered), "--temp_dir", str(work / "temp"),
        ], cwd=work, env=env, timeout=6 * 60 * 60)
        measured, evidence = probe_avatar_media(rendered, expected_duration=duration, engine="latentsync-1.6-512")
        return _publish(rendered, output, measured, evidence)


def _publish(rendered: Path, output: Path, duration: float, evidence: str) -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    shutil.copy2(rendered, partial)
    os.replace(partial, output)
    return {"schema_version": 1, "status": "succeeded", "artifact_path": str(output),
            "duration_seconds": round(duration, 3), "alignment_evidence": evidence}


def _mlx_media_evidence(rendered: Path, expected_frames: int, duration: float) -> tuple[float, dict]:
    measured, evidence = probe_avatar_media(rendered, expected_duration=duration, engine="latentsync-1.6-512")
    info = json.loads(evidence)
    result = subprocess.run([
        "ffprobe", "-v", "error", "-count_frames",
        "-show_entries", "stream=codec_type,nb_read_frames,start_time", "-of", "json", str(rendered),
    ], check=True, capture_output=True, text=True, timeout=300)
    streams = {stream["codec_type"]: stream for stream in json.loads(result.stdout)["streams"]}
    if (int(streams["video"].get("nb_read_frames", 0)) != expected_frames
            or any(float(streams[name].get("start_time", -1)) != 0 for name in ("video", "audio"))
            or info["frame_rate"] != mlx.FRAME_RATE
            or abs(info["video_duration_seconds"] - expected_frames / mlx.FRAME_RATE) > 0.001):
        raise MediaTimingError("LatentSync 1.6 MLX 生成的帧数或音画起始时间与配音时基不符")
    return measured, info


def _check_presenter_shape(source: Path, duration: float, limits: dict) -> None:
    if duration > limits["duration_seconds"] + 0.001:
        raise ValueError("配音超出已标定的时长，需准备对应时长的设备预算。")
    probed = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:stream_side_data=rotation", "-of", "json", str(source),
    ], check=True, capture_output=True, text=True, timeout=30)
    stream, = json.loads(probed.stdout)["streams"]
    dimensions = (int(stream["width"]), int(stream["height"]))
    if any(abs(item.get("rotation", 0)) % 180 == 90 for item in stream.get("side_data_list", ())):
        dimensions = dimensions[::-1]
    if dimensions != (limits["width_px"], limits["height_px"]):
        raise ValueError("人物素材画幅与设备实测画幅不同，请准备对应画幅。")


def _render_mlx(runtime: Path, weights: Path, revision: str, source: Path, audio: Path,
                output: Path, duration: float, presenter_shape: dict | None) -> dict[str, object]:
    if presenter_shape is not None:
        _check_presenter_shape(source, duration, presenter_shape)
    mlx.check_runtime(runtime, revision)
    mlx.verify_weights(runtime, checksums=True)
    whisper = _file(weights / "whisper/tiny.pt")
    with TemporaryDirectory(prefix="latentsync_16_mlx_") as temporary:
        work = Path(temporary)
        video, speech = work / "source.mp4", work / "speech.wav"
        synced, rendered = work / "synced.mp4", work / "render.mp4"
        ffmpeg = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        _command([*ffmpeg, "-i", str(audio), "-map", "0:a:0", "-ar", str(mlx.SAMPLE_RATE),
                  "-ac", "1", "-c:a", "pcm_s16le", str(speech)])
        with wave.open(str(speech), "rb") as wav:
            samples = wav.getnframes()
        frames = samples * mlx.FRAME_RATE // mlx.SAMPLE_RATE
        if presenter_shape is not None and samples > round(presenter_shape["duration_seconds"] * mlx.SAMPLE_RATE):
            raise MediaTimingError("实际配音样本数超过人物口型实测时长。")
        if frames < 1:
            raise MediaTimingError("配音短于一帧，无法生成 LatentSync 1.6 口型")
        _command([*ffmpeg, "-stream_loop", "-1", "-i", str(source), "-map", "0:v:0", "-an",
                  "-vf", f"fps={mlx.FRAME_RATE}", "-frames:v", str(frames), "-c:v", "libx264",
                  "-preset", "veryfast", "-pix_fmt", "yuv420p", str(video)])
        memory_limit = min(8 * 1024**3, presenter_shape["memory_bytes"]) if presenter_shape else 8 * 1024**3
        peak_bytes = mlx.render(runtime, whisper, video, speech, synced, work / "inference", memory_limit=memory_limit)
        # The port emits only complete output-frame periods. Attach the entire
        # conditioning track so a fractional last frame never clips audio.
        _command([*ffmpeg, "-i", str(synced), "-i", str(speech), "-map", "0:v:0", "-map", "1:a:0",
                  "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(rendered)])
        measured, evidence = _mlx_media_evidence(rendered, frames, duration)
        scratch_bytes = sum(path.stat().st_size for path in work.rglob("*") if path.is_file())
        if presenter_shape is not None and scratch_bytes > presenter_shape["scratch_bytes"]:
            raise ValueError("生成文件超过本次人物口型临时卷额度。")
        evidence.update({
            "backend": "apple_mlx", "upstream_revision": revision, "mlx_port_revision": mlx.MLX_REVISION,
            "runtime_source_sha256": mlx.SOURCE_SHA256, "unet_sha256": mlx.UNET_FILE[2],
            "weights_revision": mlx.WEIGHTS_REVISION,
            "alignment": "dwpose_brow", "inference_resolution": mlx.RESOLUTION,
            "inference_steps": mlx.STEPS, "guidance_scale": mlx.GUIDANCE, "seed": mlx.SEED,
            "audio_conditioning_samples": samples, "audio_conditioning_sample_rate": mlx.SAMPLE_RATE,
            "window_frames": mlx.WINDOW_FRAMES, "rendered_frames": frames,
            "window_count": (frames + mlx.WINDOW_FRAMES - 1) // mlx.WINDOW_FRAMES,
            "peak_mlx_allocation_bytes": peak_bytes, "scratch_file_bytes": scratch_bytes,
        })
        return _publish(rendered, output, measured, json.dumps(evidence, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-runtime", action="store_true")
    parser.add_argument("--backend", choices=("cuda", "mlx"), default="cuda")
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--weights-root", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--response", type=Path)
    parser.add_argument("--presenter-shape")
    args = parser.parse_args()
    runtime = args.runtime_root.resolve()
    if args.check_runtime:
        try:
            if args.backend == "mlx":
                mlx.check_runtime(runtime, args.code_revision)
            else:
                _runtime_check(runtime, args.code_revision)
        except (ImportError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            print(f"LatentSync 1.6 离线 {args.backend.upper()} 检查失败：{exc}", file=sys.stderr)
            return 1
        return 0
    if args.weights_root is None or args.request is None or args.response is None:
        parser.error("generation requires --weights-root, --request and --response")
    try:
        payload = json.loads(args.request.read_text(encoding="utf-8"))
        shape = json.loads(args.presenter_shape) if args.presenter_shape else None
        result = _render(payload, runtime, args.weights_root.resolve(), args.code_revision, args.backend, shape)
    except (ImportError, KeyError, OSError, ValueError, RuntimeError, MediaTimingError, subprocess.SubprocessError) as exc:
        result = {"schema_version": 1, "status": "failed", "reason": str(exc)[-4000:]}
    args.response.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
