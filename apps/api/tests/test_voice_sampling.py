"""Synthetic PCM/SQLite contracts only; no microphone, model, ffmpeg or GUI."""
from dataclasses import asdict
import io
import sqlite3
import wave
import app.profiles.voice_sampling as sampling_module

import pytest

from app.database_migrations import VOICE_SAMPLING_TASKS_DDL
from app.production import ProductionRepository
from app.profiles.voice_sampling import VoiceSamplingConflict, VoiceSamplingUseCase


def pcm(seconds=2, value=b"\x01\x00"):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(value * int(seconds * 8000))
    return output.getvalue()


@pytest.fixture
def setup(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    repository = ProductionRepository(database_path=tmp_path / "studio.sqlite3", video_library_path=library)
    # Isolated table uses the actual shared DDL, including both uniqueness keys.
    with repository._connect() as conn:
        conn.executescript(VOICE_SAMPLING_TASKS_DDL)
    service = repository.voice_sampling
    task = service.create("workspace-a", "member-a", request_id="create-1", display_name="我的声音")
    return repository, service, task


def receive(service, task, **overrides):
    values = dict(request_id="submit-1", text_version=task.text_version, purpose="voice_profile",
                  source_device="desktop", content=pcm(), model_id="configured-test-model")
    values.update(overrides)
    return service.receive(task.workspace_id, task.owner_user_id, task.id, **values)


def counts(repository):
    with repository._connect() as conn:
        return {name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                for name in ("material_assets", "versioned_voice_profiles", "workspace_media_references")}


def test_create_is_member_bound_idempotent_and_does_not_create_a_profile(setup):
    repo, service, task = setup
    assert service.create("workspace-a", "member-a", request_id="create-1", display_name="我的声音") == task
    assert task.purpose == "voice_profile" and task.reference_text and task.text_version
    assert counts(repo) == dict(material_assets=0, versioned_voice_profiles=0, workspace_media_references=0)
    assert service.list("workspace-a", "member-b") == []
    assert service.list("workspace-b", "member-a") == []
    for workspace, owner in [("workspace-b", "member-a"), ("workspace-a", "member-b")]:
        with pytest.raises(KeyError):
            service.get(workspace, owner, task.id)
        with pytest.raises(KeyError):
            service.receive(workspace, owner, task.id, request_id="x", purpose="voice_profile", text_version=task.text_version,
                            source_device="desktop", content=pcm())
    with pytest.raises(VoiceSamplingConflict):
        service.create("workspace-a", "member-a", request_id="create-1", display_name="改名")
    with pytest.raises(ValueError):
        service.create("workspace-a", "member-a", request_id="create-2", display_name="配音", purpose="narration")


def test_receive_replays_exact_receipt_after_restart_without_readiness_or_default(setup):
    repo, service, task = setup
    received = receive(service, task)
    fresh = VoiceSamplingUseCase(repo._connect, library_path=repo.video_library_path, registration=repo._profile_registration)
    assert receive(fresh, task) == received
    assert fresh.get(task.workspace_id, task.owner_user_id, task.id) == received
    assert counts(repo) == dict(material_assets=1, versioned_voice_profiles=1, workspace_media_references=1)
    voice = repo.get_versioned_voice_profile(task.workspace_id, received.voice_profile_id, received.voice_version)
    source = repo._material_assets.get_asset(task.workspace_id, voice.reference.asset_id)
    assert (source.file_name, repo.resolve_workspace_media_reference(task.workspace_id, voice.reference_audio_path).name) == (
        f"{task.display_name}.wav", f"{task.id}-{received.audio_sha256}.wav")
    assert source.analysis_status == "blocked" and "音频内容分析尚未接入" in source.analysis_message
    assert voice.owner_user_id == task.owner_user_id
    assert voice.reference_text == task.reference_text
    assert voice.model_id == "configured-test-model"
    assert not voice.person_profile_id
    assert repo.get_default_versioned_voice(task.workspace_id) is None
    assert received.status == "received" and received.duration_seconds == 2
    assert "ready" not in asdict(received).values()
    assert len(list(repo.video_library_path.rglob("*.wav"))) == 1


@pytest.mark.parametrize("changed", [dict(text_version="stale"), dict(purpose="narration"),
    dict(request_id="another"), dict(content=pcm(value=b"\x02\x00")), dict(source_device="import")])
def test_reception_rejects_changed_identity_and_original_receipt_is_immutable(setup, changed):
    repo, service, task = setup
    received = receive(service, task)
    with pytest.raises(VoiceSamplingConflict):
        receive(service, task, **changed)
    assert service.get(task.workspace_id, task.owner_user_id, task.id) == received
    assert len(list(repo.video_library_path.rglob("*.wav"))) == 1


@pytest.mark.parametrize("content,reason", [(b"not audio", "PCM WAV"), (pcm(.5), "1–120"), (pcm()[:-4], "不完整")])
def test_incomplete_or_invalid_sample_keeps_failure_receipt_and_creates_no_profile(setup, content, reason):
    repo, service, task = setup
    with pytest.raises(ValueError, match=reason):
        receive(service, task, content=content)
    failed = service.get(task.workspace_id, task.owner_user_id, task.id)
    assert failed.status == "awaiting_sample" and reason in failed.last_error
    assert failed.failed_request_id == "submit-1"
    assert counts(repo) == dict(material_assets=0, versioned_voice_profiles=0, workspace_media_references=0)
    assert not list(repo.video_library_path.rglob("*.wav"))
    assert receive(service, task, request_id="re-recorded").last_error is None


def test_late_receipt_failure_rolls_back_profile_source_claim_and_file(setup):
    repo, service, task = setup
    with repo._connect() as conn:
        conn.execute("CREATE TRIGGER fail_sample_receipt BEFORE UPDATE ON voice_sampling_tasks BEGIN SELECT RAISE(ABORT,'receipt unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError, match="receipt unavailable"):
        receive(service, task)
    assert counts(repo) == dict(material_assets=0, versioned_voice_profiles=0, workspace_media_references=0)
    assert not list(repo.video_library_path.rglob("*.wav"))
    assert service.get(task.workspace_id, task.owner_user_id, task.id) == task


def test_missing_disk_does_not_recreate_root_or_accept_sample(setup):
    repo, service, task = setup
    repo.video_library_path.rmdir()
    with pytest.raises(ValueError, match="目录不可用"):
        receive(service, task)
    assert not repo.video_library_path.exists()
    assert counts(repo)["versioned_voice_profiles"] == 0


def test_file_flush_failure_keeps_actionable_receipt_and_cleans_unpublished_bytes(setup, monkeypatch):
    repo, service, task = setup
    def failed_flush(_fd):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(sampling_module.os, "fsync", failed_flush)
    with pytest.raises(OSError):
        receive(service, task)
    failed = service.get(task.workspace_id, task.owner_user_id, task.id)
    assert failed.status == "awaiting_sample" and "原磁盘和目录权限" in failed.last_error
    assert not list(repo.video_library_path.rglob("*.partial"))
    assert not list(repo.video_library_path.rglob("*.wav"))
    assert counts(repo)["versioned_voice_profiles"] == 0


def test_sampling_reference_cannot_escape_library_through_symlink(setup, tmp_path):
    repo, service, task = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo.video_library_path / "approved").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="越出"):
        receive(service, task)
    assert list(outside.iterdir()) == []
    assert counts(repo)["material_assets"] == 0
