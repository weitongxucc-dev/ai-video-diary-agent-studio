import math
from dataclasses import replace
from pathlib import Path
import struct
from types import SimpleNamespace
import wave

import pytest
from fastapi.testclient import TestClient

from app.audio_waveform import AudioWaveformError, audio_waveform


def write_audio(path: Path, amplitude: int) -> None:
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, 8000, 0, "NONE", "not compressed"))
        frames = []
        for index in range(8000):
            value = int(amplitude * math.sin(index * math.tau * 440 / 8000)) if index >= 4000 else 0
            frames.append(struct.pack("<hh", value, -value))
        output.writeframes(b"".join(frames))


def test_waveform_tracks_silence_and_opposite_phase_stereo_and_invalidates_cache(tmp_path):
    source = tmp_path / "audio.wav"
    write_audio(source, 16384)
    result = audio_waveform(source, tmp_path / "cache")
    assert result["duration_seconds"] == 1
    assert len(result["peaks"]) == 100
    assert max(result["peaks"][:50]) == 0
    assert max(result["peaks"][50:]) == pytest.approx(0.5)
    assert audio_waveform(source, tmp_path / "cache") == result
    write_audio(source, 8192)
    updated = audio_waveform(source, tmp_path / "cache")
    assert updated["source_id"] != result["source_id"]
    assert max(updated["peaks"]) == pytest.approx(0.25)
    assert all(path.suffix == ".json" for path in (tmp_path / "cache").iterdir())


def test_waveform_reports_unreadable_audio(tmp_path):
    source = tmp_path / "bad.wav"
    source.write_text("not audio")
    with pytest.raises(AudioWaveformError, match="无法生成音频波形"):
        audio_waveform(source, tmp_path / "cache")


def test_waveform_api_rejects_another_tasks_artifact(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "_creation_task_or_404", lambda workspace_id, task_id:
                        SimpleNamespace(id=task_id, narration_cache_key="cache"))
    monkeypatch.setattr(main.production_repo, "get_narration_artifact", lambda *args:
                        SimpleNamespace(task_id="another-task", audio_path="unread"))
    response = TestClient(main.app).get("/api/creation-tasks/this-task/narration/waveform")
    assert response.status_code == 404


def test_timeline_waveform_uses_default_cache_directory_when_not_configured(monkeypatch, tmp_path):
    import app.main as main

    source = tmp_path / "voice.wav"
    write_audio(source, 16384)
    monkeypatch.setattr(main, "settings", replace(main.settings, temporary_root_path=None, model_root_path=str(tmp_path / "models")))
    monkeypatch.setattr(main, "_creation_task_or_404", lambda *args: None)
    monkeypatch.setattr(main.production_repo, "get_creation_task_montage_plan", lambda *args: {})
    monkeypatch.setattr(main, "creation_task_montage_plan_from_payload", lambda value: value)
    monkeypatch.setattr(main, "_effective_audio_timeline", lambda plan: SimpleNamespace(
        tracks=[SimpleNamespace(clips=[SimpleNamespace(id="voice", source_ref="voice-source")])]))
    monkeypatch.setattr(main, "_task_audio", lambda: SimpleNamespace(source=lambda *args: source))
    response = TestClient(main.app).get("/api/creation-tasks/this-task/audio/waveform/voice")
    assert response.status_code == 200
    assert max(response.json()["peaks"]) == pytest.approx(0.5)
    assert list((tmp_path / "temporary" / "audio-waveforms").rglob("*.json"))
