"""Real preview service/repository/device request; synthetic provider, no model load."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import pytest

from app.creation_execution import CreationExecutionActor
from app.device_scheduler import RecoveryBlocked
from app.granted_subprocess import GrantedCommandMemoryPressure
from app.host_memory import MemoryDecision, MemorySample
from app.host_resource_probes import HostResourceDecision, HostResourceRefusal
from app.persistence_json import _json_payload
from tests.test_profile_api_contract import repo as repo, setup_preview, VoiceProvider, record_only_preview


class PressureProvider(VoiceProvider):
    def __init__(self, error):
        self.error, self.calls = error, 0

    def generate_granted(self, **kwargs):
        if self.error is not None:
            self.calls += 1
            raise self.error
        return super().generate_granted(**kwargs)


def _pressure():
    sample = MemorySample("darwin", 0., "synthetic", "critical", 1000, None,
                          {"pressure": {"value": "critical", "unit": "state", "source": "synthetic"}})
    return GrantedCommandMemoryPressure(MemoryDecision(False, "memory_pressure_critical", sample, 100, None, 0.))


def test_voice_preview_text_budget_includes_fixed_reference_transcript(repo, tmp_path):
    runtime, _, _, queued = setup_preview(repo, tmp_path)
    owner = runtime.registrations["media_preview"].owner
    _, _, resources = owner.facts(queued)
    voice = repo.profile_records.get_versioned_voice_profile("ws", queued.voice_profile_id, queued.voice_version)
    assert resources["text_chars"] == len(queued.preview_input["preview_text"]) + len(voice.reference_text)
    with repo._connect() as connection:
        request = connection.execute("SELECT resources_json FROM device_work_requests WHERE id=?",
            (queued.device_request_id,)).fetchone()
    assert json.loads(request[0])["text_chars"] == resources["text_chars"]


def test_pressure_keeps_original_attempt_waits_and_then_succeeds(repo, tmp_path, caplog):
    provider = PressureProvider(_pressure())
    runtime, resources, provider, queued = setup_preview(repo, tmp_path, provider=provider)
    resources.admit_value = True
    clock = [datetime.now(timezone.utc)]
    runtime.scheduler.clock = lambda: clock[0]
    assert runtime.run_once()
    owner = runtime.registrations["media_preview"].owner
    waiting = owner.read("ws", queued.id)
    assert waiting.status == "queued" and waiting.reason == "resource_pressure"
    assert waiting.device_request_id == queued.device_request_id and waiting.attempt == queued.attempt
    assert waiting.output_path is None and waiting.finished_at is None
    log = next(r for r in caplog.records if r.msg == "Profile preview resource refusal: %s")
    payload = json.loads(log.args[0])
    assert payload["request_id"] == queued.device_request_id and payload["preview_attempt"] == queued.attempt
    assert payload["decision"]["reason"] == "memory_pressure_critical"
    assert payload["decision"]["checks"]["memory_policy"]["sample"]["available_bytes"] == 1000
    assert "preview_input" not in payload and "member_identity" not in payload
    view = runtime.scheduler.view_for(queued.device_request_id, CreationExecutionActor("ws", "member"))
    assert view["state"] == "blocked" and view["wait_reason"] == "resource_pressure"
    with repo._connect() as conn:
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"
    assert not runtime.run_once()  # Existing scheduler backoff; no immediate blind rerun.
    provider.error = None
    clock[0] += timedelta(seconds=31)
    assert runtime.run_once()
    completed = owner.read("ws", queued.id)
    assert completed.status == "succeeded" and completed.id == queued.id
    assert completed.device_request_id == queued.device_request_id and completed.attempt == queued.attempt
    assert provider.calls == 2 and Path(completed.output_path).is_file()


def test_unknown_child_recovery_does_not_release_or_publish_failed_preview(repo, tmp_path):
    provider = PressureProvider(RecoveryBlocked("synthetic group release unknown"))
    runtime, resources, _, queued = setup_preview(repo, tmp_path, provider=provider)
    resources.admit_value = True
    with pytest.raises(RecoveryBlocked):
        runtime.run_once()
    record = runtime.registrations["media_preview"].owner.read("ws", queued.id)
    assert record.status in {"queued", "running"} and record.output_path is None
    with repo._connect() as conn:
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] != "free"


def test_invalid_resource_contract_releases_proven_group_without_pressure_retry(repo, tmp_path):
    provider = PressureProvider(HostResourceRefusal(HostResourceDecision(False, {}, {}, "declared_resources_exceed_budget")))
    runtime, resources, _, queued = setup_preview(repo, tmp_path, provider=provider)
    resources.admit_value = True
    runtime.run_once()
    record = runtime.registrations["media_preview"].owner.read("ws", queued.id)
    assert record.status == "failed" and record.reason == "declared_resources_exceed_budget"
    with repo._connect() as conn:
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"
    assert not runtime.run_once()


def test_owner_write_failure_rolls_back_owner_queue_and_permit_together(repo, tmp_path, monkeypatch):
    runtime, resources, _, queued = record_only_preview(repo, tmp_path, monkeypatch)
    resources.admit_value = True
    registration = runtime.registrations["media_preview"]
    grant = runtime.scheduler.pick_and_grant()
    repo.preview_records.save_media_preview_run(replace(queued, status="running"))
    original = repo.preview_records.bind_preview_device_request
    def fail_after_owner_write(run, request_id, *, conn=None):
        current = original(run, request_id, conn=conn)
        if run.status == "queued" and run.reason == "resource_pressure":
            conn.execute("UPDATE media_preview_attempts SET payload=? WHERE workspace_id=? AND id=?",
                         (_json_payload(run), run.workspace_id, run.id))
            raise OSError("injected owner checkpoint failure")
        return current
    monkeypatch.setattr(repo.preview_records, "bind_preview_device_request", fail_after_owner_write)
    with pytest.raises(OSError, match="checkpoint failure"):
        waiting = replace(queued, status='queued', reason='resource_pressure',
            impact='Declared pressure observation', repair_action='Wait for resources')
        runtime.scheduler.defer_unit(grant, reason='resource_pressure',
            retry_not_before_utc=datetime.now(timezone.utc)+timedelta(seconds=30),
            owner_database_path=repo.database_path, owner_write=lambda conn, _:
                repo.preview_records.checkpoint(waiting, grant.request_id, connection=conn))
    assert registration.owner.read("ws", queued.id).status == "running"
    with repo._connect() as conn:
        assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (queued.device_request_id,)).fetchone()[0] == "granted"
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] != "free"
        assert conn.execute("SELECT count(*) FROM device_request_events WHERE request_id=? AND event_type='deferred'", (queued.device_request_id,)).fetchone()[0] == 0


def test_defer_owner_callback_requires_same_database(repo, tmp_path):
    runtime, resources, _, _ = setup_preview(repo, tmp_path, provider=PressureProvider(_pressure()))
    resources.admit_value = True
    grant = runtime.scheduler.pick_and_grant()
    with pytest.raises(ValueError, match="同一"):
        runtime.scheduler.defer_unit(grant, reason="resource_pressure",
            retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=30),
            owner_database_path=tmp_path / "different.sqlite", owner_write=lambda *_: pytest.fail("foreign callback ran"))


def test_unknown_release_prevents_owner_callback_and_defer_commit(repo, tmp_path, monkeypatch):
    runtime, resources, _, queued = record_only_preview(repo, tmp_path, monkeypatch)
    resources.admit_value = True
    grant = runtime.scheduler.pick_and_grant()
    monkeypatch.setattr(resources, "released", lambda _: None)
    monkeypatch.setattr(repo.preview_records, "bind_preview_device_request", lambda *_a, **_k: pytest.fail("owner changed before release proof"))
    with pytest.raises(RecoveryBlocked):
        runtime.scheduler.defer_unit(grant, reason='resource_pressure',
            retry_not_before_utc=datetime.now(timezone.utc)+timedelta(seconds=30),
            owner_database_path=repo.database_path, owner_write=lambda conn, _:
                repo.preview_records.checkpoint(queued, grant.request_id, connection=conn))
    with repo._connect() as conn:
        assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (queued.device_request_id,)).fetchone()[0] == "granted"
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] != "free"
