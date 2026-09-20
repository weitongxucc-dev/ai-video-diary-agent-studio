from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

# Workers run as file entrypoints in a separate Python environment. Share the
# standard-library-only timing contract with the API from the same source root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.media_timing import MediaTimingError, probe_avatar_media, probe_media_duration


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
    try:
        return probe_avatar_media(path, expected_duration=expected_duration, engine="musetalk-v1.5")
    except MediaTimingError as exc:
        raise WorkerFailure(str(exc)) from exc


def _probe_duration(path: Path) -> float:
    try:
        return probe_media_duration(path)
    except MediaTimingError as exc:
        raise WorkerFailure(str(exc)) from exc


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
