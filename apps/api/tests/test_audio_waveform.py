import math
from dataclasses import replace
from pathlib import Path
import struct
from types import SimpleNamespace
import wave

import pytest
from fastapi.testclient import TestClient

from app.audio_waveform import AudioWaveformError, audio_waveform


@pytest.fixture(autouse=True)
def permitted_waveform(granted_media, monkeypatch):
    original = audio_waveform
    def decode(path, cache, **kwargs):
        with granted_media([path]) as context:
            return original(path, cache, context=context, **kwargs)
    monkeypatch.setitem(globals(), "audio_waveform", decode)


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


def test_granted_timeline_waveform_uses_default_cache_directory_when_not_configured(monkeypatch, tmp_path, granted_media):
    import app.main as main

    source = tmp_path / "voice.wav"
    write_audio(source, 16384)
    monkeypatch.setattr(main, "settings", replace(main.settings, temporary_root_path=None, model_root_path=str(tmp_path / "models")))
    from app.creation import audio_ingress_use_case as ingress_module
    from app.creation_execution import CreationExecutionActor
    from app.local_members import LocalMemberService
    from app.production import ProductionRepository
    with granted_media([source]) as context:
        # Keep the actual granted Job and factory-owned default cache configuration.
        repository = ProductionRepository(context.grant.database_path, context.grant.database_path.parent / 'library')
        monkeypatch.setattr(main, 'production_repo', repository)
        use_case = main._audio_ingress_use_case()
        monkeypatch.setattr(repository.task_records, 'get_creation_task', lambda *args: SimpleNamespace(id='task'))
        monkeypatch.setattr(repository, 'get_creation_task_montage_plan', lambda *args: {})
        monkeypatch.setattr(ingress_module, 'creation_task_montage_plan_from_payload', lambda value: value)
        monkeypatch.setattr(ingress_module, 'effective_audio_timeline', lambda plan: SimpleNamespace(
            tracks=[SimpleNamespace(clips=[SimpleNamespace(id='voice', source_ref='voice-source')])]))
        monkeypatch.setattr(use_case.task_audio, 'source', lambda *args, **kwargs: source)
        job = repository.job_records.get_job_run(context.grant.workspace_id, context.grant.owner_id)
        member, _ = LocalMemberService(repository.database_path).whoami(None)
        actor = CreationExecutionActor(context.grant.workspace_id, member.member_id)
        result = use_case.audio_waveform(job, {'arguments': {'clip_id': 'voice'}}, actor, context)
    assert max(result["peaks"]) == pytest.approx(0.5)
    assert list((tmp_path / "temporary" / "audio-waveforms").rglob("*.json"))
