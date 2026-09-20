from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory


MODEL_FILES = (
    "models/musetalkV15/musetalk.json",
    "models/musetalkV15/unet.pth",
    "models/sd-vae/config.json",
    "models/sd-vae/diffusion_pytorch_model.bin",
    "models/whisper/config.json",
    "models/whisper/preprocessor_config.json",
    "models/whisper/pytorch_model.bin",
    "models/face-parse-bisent/79999_iter.pth",
    "models/face-parse-bisent/resnet18-5c106cde.pth",
)


class WorkerFailure(RuntimeError):
    pass


def _required_path(payload: dict[str, object], key: str) -> Path:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkerFailure(f"{key} is required")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise WorkerFailure(f"{key} is missing or empty")
    return path


def _validate_runtime(runtime_root: Path) -> None:
    if not (runtime_root / "scripts" / "inference.py").is_file():
        raise WorkerFailure("MuseTalk inference entrypoint is missing")
    missing = [item for item in MODEL_FILES if not (runtime_root / item).is_file()]
    if missing:
        raise WorkerFailure(f"MuseTalk model files are missing: {', '.join(missing)}")


def _probe_output(path: Path, *, expected_duration: float) -> tuple[float, str]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height,sample_rate,channels,duration,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise WorkerFailure(completed.stderr.strip() or "ffprobe failed")
    try:
        payload = json.loads(completed.stdout)
        duration = float(payload["format"]["duration"])
        streams = payload["streams"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkerFailure("ffprobe returned invalid media metadata") from exc
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if duration <= 0 or video is None or audio is None:
        raise WorkerFailure("rendered output must contain non-empty video and audio streams")
    try:
        video_duration = float(video["duration"])
        audio_duration = float(audio["duration"])
        frame_rate = float(Fraction(video["avg_frame_rate"]))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise WorkerFailure("rendered output is missing valid stream timing") from exc
    if not all(math.isfinite(value) and value > 0 for value in (
        duration, video_duration, audio_duration, frame_rate, expected_duration,
    )):
        raise WorkerFailure("rendered output has invalid stream timing")
    # Container duration can be supplied entirely by the audio even when face
    # detection/inference has dropped video frames. Check each actual stream.
    frame_seconds = 1 / frame_rate
    for name, stream_duration in (("video", video_duration), ("audio", audio_duration)):
        if abs(stream_duration - expected_duration) > frame_seconds + 0.001:
            raise WorkerFailure(
                f"rendered {name} duration {stream_duration:.3f}s does not match "
                f"narration {expected_duration:.3f}s within one output frame; "
                "check face detection and inference output before retrying"
            )
    evidence = json.dumps(
        {
            "engine": "musetalk-v1.5",
            "duration_seconds": round(duration, 3),
            "video_duration_seconds": video_duration,
            "audio_duration_seconds": audio_duration,
            "expected_duration_seconds": expected_duration,
            "frame_rate": frame_rate,
            "validation_scope": "media_timing_only_not_lip_sync_quality",
            "video_codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "audio_codec": audio.get("codec_name"),
            "sample_rate": audio.get("sample_rate"),
            "channels": audio.get("channels"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return duration, evidence


def _probe_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise WorkerFailure(completed.stderr.strip() or "ffprobe failed")
    try:
        duration = float(completed.stdout.strip())
    except ValueError as exc:
        raise WorkerFailure("ffprobe returned an invalid duration") from exc
    if duration <= 0:
        raise WorkerFailure("media duration must be positive")
    return duration


def _stage_visual_source(source: Path, target: Path, audio_duration: float) -> None:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-t",
            f"{audio_duration:.3f}",
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        raise WorkerFailure(completed.stderr.strip() or "failed to stage visual source")


def _render(payload: dict[str, object], runtime_root: Path) -> dict[str, object]:
    if payload.get("schema_version") != 1:
        raise WorkerFailure("unsupported request schema")
    if payload.get("language") != "zh":
        raise WorkerFailure("this local MuseTalk runtime currently supports Chinese tasks only")
    if payload.get("motion") != "natural" or payload.get("expression") != "neutral":
        raise WorkerFailure("this runtime currently supports natural motion and neutral expression only")
    source = _required_path(payload, "visual_path")
    audio = _required_path(payload, "audio_path")
    output_value = payload.get("output_path")
    if not isinstance(output_value, str) or not output_value.strip():
        raise WorkerFailure("output_path is required")
    output = Path(output_value).expanduser().resolve()
    _validate_runtime(runtime_root)

    staging_root = runtime_root.parent / "work"
    staging_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="avatar_job_", dir=staging_root) as temporary:
        work = Path(temporary)
        staged_source = work / "source.mp4"
        staged_audio = work / "audio.wav"
        audio_duration = _probe_duration(audio)
        _stage_visual_source(source, staged_source, audio_duration)
        shutil.copy2(audio, staged_audio)
        config = work / "job.yaml"
        result_root = work / "results"
        config.write_text(
            "avatar_job:\n"
            f"  video_path: {json.dumps(str(staged_source))}\n"
            f"  audio_path: {json.dumps(str(staged_audio))}\n"
            '  result_name: "render.mp4"\n',
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "scripts.inference",
            "--inference_config",
            str(config),
            "--result_dir",
            str(result_root),
            "--unet_config",
            str(runtime_root / "models" / "musetalkV15" / "musetalk.json"),
            "--unet_model_path",
            str(runtime_root / "models" / "musetalkV15" / "unet.pth"),
            "--whisper_dir",
            str(runtime_root / "models" / "whisper"),
            "--batch_size",
            "1",
            "--use_float16",
            "--version",
            "v15",
        ]
        completed = subprocess.run(
            command,
            cwd=runtime_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=60 * 60,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        rendered = result_root / "v15" / "render.mp4"
        if completed.returncode != 0 or not rendered.is_file():
            # The inference entrypoint reports some failures on stdout while
            # native libraries emit unrelated warnings on stderr.
            detail = "\n".join(part[-1800:] for part in (
                completed.stderr.strip(), completed.stdout.strip(),
            ) if part)
            raise WorkerFailure(detail or "MuseTalk did not produce output")
        duration, evidence = _probe_output(rendered, expected_duration=audio_duration)
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_suffix(output.suffix + ".partial")
        shutil.copy2(rendered, partial)
        os.replace(partial, output)

    return {
        "schema_version": 1,
        "status": "succeeded",
        "artifact_path": str(output),
        "duration_seconds": round(duration, 3),
        "alignment_evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()
    request_path = Path(args.request).expanduser().resolve()
    response_path = Path(args.response).expanduser().resolve()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        response = _render(payload, runtime_root)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, WorkerFailure) as exc:
        response = {
            "schema_version": 1,
            "status": "failed",
            "reason": str(exc)[:4000],
        }
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
