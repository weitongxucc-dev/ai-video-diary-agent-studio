import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "musetalk_worker", Path(__file__).parents[1] / "model_workers" / "musetalk_worker.py"
)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def _video(path, picture_seconds, audio_seconds):
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color=blue:size=64x64:rate=30:duration={picture_seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=24000:duration={audio_seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], check=True, capture_output=True)


@pytest.mark.parametrize("picture,audio,expected,stream", [
    (0.3, 1, 1, "video"),
    (1, 0.3, 1, "audio"),
    (0.5, 0.5, 1, "video"),
    (1.5, 1, 1, "video"),
])
def test_rejects_incomplete_or_extended_stream_even_with_valid_mp4(
    tmp_path, picture, audio, expected, stream,
):
    path = tmp_path / "result.mp4"
    _video(path, picture, audio)
    with pytest.raises(worker.WorkerFailure, match=f"rendered {stream} duration"):
        worker._probe_output(path, expected_duration=expected)


def test_frame_quantized_video_passes_without_claiming_lip_sync_quality(tmp_path):
    path = tmp_path / "result.mp4"
    _video(path, 1, 1.02)
    duration, evidence = worker._probe_output(path, expected_duration=1.02)
    assert duration == pytest.approx(1.02, abs=0.001)
    details = json.loads(evidence)
    assert details["video_duration_seconds"] == 1
    assert details["audio_duration_seconds"] == pytest.approx(1.02, abs=0.001)
    assert details["frame_rate"] == 30
    assert details["validation_scope"] == "media_timing_only_not_lip_sync_quality"


def test_truncated_inference_cannot_replace_existing_output(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    _video(source, 0.3, 1)
    output = tmp_path / "result.mp4"
    output.write_bytes(b"previous output stays unchanged")
    run = subprocess.run

    def inference_or_media(command, **kwargs):
        if "scripts.inference" in command:
            result_root = Path(command[command.index("--result_dir") + 1]) / "v15"
            result_root.mkdir(parents=True)
            shutil.copy2(source, result_root / "render.mp4")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return run(command, **kwargs)

    monkeypatch.setattr(worker, "_validate_runtime", lambda root: None)
    monkeypatch.setattr(worker.subprocess, "run", inference_or_media)
    with pytest.raises(worker.WorkerFailure, match="rendered video duration"):
        worker._render({
            "schema_version": 1, "language": "zh", "motion": "natural", "expression": "neutral",
            "visual_path": str(source), "audio_path": str(source), "output_path": str(output),
        }, tmp_path / "runtime")
    assert output.read_bytes() == b"previous output stays unchanged"


def test_inference_stdout_error_is_not_hidden_by_stderr_warnings(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    audio = tmp_path / "audio.wav"
    source.write_bytes(b"source")
    audio.write_bytes(b"audio")
    monkeypatch.setattr(worker, "_validate_runtime", lambda root: None)
    monkeypatch.setattr(worker, "_probe_duration", lambda path: 1)
    monkeypatch.setattr(worker, "_stage_visual_source", lambda *args: None)
    monkeypatch.setattr(worker.subprocess, "run", lambda *args, **kwargs:
        subprocess.CompletedProcess(args, 1, stdout="Face crop exceeds source bounds",
                                    stderr="Native library initialization warning"))
    with pytest.raises(worker.WorkerFailure, match="Face crop exceeds source bounds") as failure:
        worker._render({
            "schema_version": 1, "language": "zh", "motion": "natural", "expression": "neutral",
            "visual_path": str(source), "audio_path": str(audio),
            "output_path": str(tmp_path / "result.mp4"),
        }, tmp_path / "runtime")
    assert "Native library initialization warning" in str(failure.value)
    assert not (tmp_path / "result.mp4").exists()


@pytest.mark.parametrize("duration", ["nan", "inf", "0", "-1"])
def test_input_media_duration_must_be_finite_and_positive(tmp_path, monkeypatch, duration):
    monkeypatch.setattr(worker.subprocess, "run", lambda command, **kwargs:
        subprocess.CompletedProcess(command, 0, stdout=duration, stderr=""))
    with pytest.raises(worker.WorkerFailure, match="finite and positive"):
        worker._probe_duration(tmp_path / "audio.wav")
