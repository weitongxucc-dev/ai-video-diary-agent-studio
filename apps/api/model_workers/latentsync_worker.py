"""Isolated local LatentSync worker using the same request contract as MuseTalk."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

# Standalone file execution resolves this helper beside the worker.
from media_timing import MediaTimingError, probe_avatar_media, probe_media_duration


MODEL_ID = "latentsync-1.5-mlx-fidelity-brow"


def _file(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 缺失")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"{label} 不可读取")
    return path


def _render(payload: dict[str, object], runtime_root: Path, pose_root: Path) -> dict[str, object]:
    if payload.get("schema_version") != 1 or payload.get("model_id") != MODEL_ID:
        raise ValueError("高精度口型请求与模型版本不匹配")
    if payload.get("language") != "zh" or payload.get("motion") != "natural" or payload.get("expression") != "neutral":
        raise ValueError("当前高精度口型只支持中文、自然动作和中性表情")
    source = _file(payload.get("visual_path"), "人物视频")
    audio = _file(payload.get("audio_path"), "配音")
    output_value = payload.get("output_path")
    if not isinstance(output_value, str) or not output_value.strip():
        raise ValueError("输出路径缺失")
    output = Path(output_value).expanduser().resolve()
    for path in (
        runtime_root / "inference_fidelity.py",
        runtime_root / "checkpoints" / "latentsync_unet_mlx.safetensors",
        runtime_root / "checkpoints" / "vae_mlx.safetensors",
        runtime_root / "checkpoints" / "whisper" / "tiny.pt",
        pose_root / "official_onnx.py",
        pose_root / "dw-ll_ucoco_384.onnx",
    ):
        _file(str(path), path.name)
    audio_duration = probe_media_duration(audio)
    work_root = runtime_root.parent / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="latentsync_job_", dir=work_root) as temporary:
        work = Path(temporary)
        staged_video = work / "source.mp4"
        staged_audio = work / "audio.wav"
        rendered = work / "rendered.mp4"
        staged = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-stream_loop", "-1", "-i", str(source), "-t", f"{audio_duration:.3f}",
            "-map", "0:v:0", "-an", "-vf", "fps=25", "-c:v", "libx264",
            "-preset", "veryfast", "-pix_fmt", "yuv420p", str(staged_video),
        ], capture_output=True, text=True, timeout=300, check=False)
        if staged.returncode != 0 or not staged_video.is_file():
            raise RuntimeError(staged.stderr[-2000:] or "人物视频准备失败")
        prepared_audio = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio),
            "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(staged_audio),
        ], capture_output=True, text=True, timeout=300, check=False)
        if prepared_audio.returncode != 0 or not staged_audio.is_file():
            raise RuntimeError(prepared_audio.stderr[-2000:] or "配音准备失败")
        paths = [str(runtime_root), str(runtime_root / "dependencies"), str(runtime_root / "upstream"),
                 str(pose_root), str(pose_root / "dependencies")]
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join([*paths, os.environ.get("PYTHONPATH", "")]),
            "LATENTSYNC_POSE_ROOT_PATH": str(pose_root),
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONUNBUFFERED": "1",
        }
        command = [sys.executable, str(Path(__file__).with_name("latentsync_entry.py")),
            "--video_path", str(staged_video), "--audio_path", str(staged_audio),
            "--video_out_path", str(rendered), "--temp_dir", str(work / "temp")]
        result = subprocess.run(command, cwd=runtime_root, env=env, capture_output=True,
            text=True, check=False, timeout=6 * 60 * 60)
        if result.returncode != 0 or not rendered.is_file():
            raise RuntimeError((result.stderr.strip() or result.stdout.strip())[-3000:] or "LatentSync 未生成视频")
        duration, evidence = probe_avatar_media(rendered, expected_duration=audio_duration, engine=MODEL_ID)
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_suffix(output.suffix + ".partial")
        shutil.copy2(rendered, partial)
        os.replace(partial, output)
    return {"schema_version": 1, "status": "succeeded", "artifact_path": str(output),
        "duration_seconds": round(duration, 3), "alignment_evidence": evidence}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--pose-root", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.request).read_text(encoding="utf-8"))
        result = _render(payload, Path(args.runtime_root).expanduser().resolve(), Path(args.pose_root).expanduser().resolve())
    except (OSError, ValueError, RuntimeError, MediaTimingError, subprocess.SubprocessError) as exc:
        result = {"schema_version": 1, "status": "failed", "reason": str(exc)[:4000]}
    response = Path(args.response).expanduser().resolve()
    response.parent.mkdir(parents=True, exist_ok=True)
    response.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
