"""Expired waveforms leave the existing write transaction before any identity RPC."""
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from app.creation.contracts import EditIntent, TimelineVersion
from app.creation_execution import CreationExecutionActor, CreationJobOwnerVerifier
from app.device_runtime import OwnerRegistration, build_device_runtime
from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import CreationTask
from app.task_audio import TaskMediaJobs
from test_device_runtime import Access, CompletingExecutor, Processes, Resources


@pytest.fixture
def media_case(tmp_path):
    runtimes = []
    def create(operation="audio_waveform", version="timeline-base", include_version=True, *, task_authorization=False):
        root = tmp_path / str(len(runtimes))
        root.mkdir()
        repo = ProductionRepository(root / "studio.sqlite", root / "library")
        plan = CreationPlan(id="plan", workspace_id="local", goal="test", name="test", target_count=1,
            language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
            knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
            approved_asset_count=0, approved_clip_count=0, material_rule_summary="", model_snapshot=[],
            confirmation_points=[], blockers=[], preflight_fingerprint="fixture")
        repo.plan_confirmation.confirm_creation_plan(plan, CreationTask(id="task", workspace_id="local", plan_id="plan",
            name="test", scenario="knowledge_explanation"))
        base = repo.timeline_records.create_timeline_version(TimelineVersion("timeline-base", "local", "task", None, 1, {}, None))
        state = {"identity_calls": 0, "facts_calls": 0, "verdict": "ready", "revision": 1,
            "block_identity": False}
        entered, release = threading.Event(), threading.Event()
        def identity(actor):
            state["identity_calls"] += 1
            if state["block_identity"]:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Controlled identity callback was not released")
            return state["verdict"]
        def facts(*args):
            state["facts_calls"] += 1
            value = {"revision": state["revision"]}
            if include_version:
                value["base_version_id"] = version
            return value, {"kind": "task_audio", "execution_mode": "cpu_ffmpeg"}
        actor = CreationExecutionActor("local", "fixture-member")
        authorizations = None
        if task_authorization:
            from app.creation.task_authorization import TaskAuthorizationUseCase
            from app.local_members import LocalMemberService
            member, _token = LocalMemberService(repo.database_path).whoami(None)
            actor = CreationExecutionActor("local", member.member_id)
            authorizations = TaskAuthorizationUseCase(repo, identity)
        media = TaskMediaJobs(repo, facts=facts, verify_identity=identity)
        media.task_authorizations = authorizations
        runtime = build_device_runtime(database_path=repo.database_path, device_lock_path=root / "device.lock",
            device_access=Access(), registrations={"creation_job": OwnerRegistration(
                CreationJobOwnerVerifier(repo, task_media=media, verify_identity=identity,
                    task_authorizations=authorizations), CompletingExecutor())},
            resources=Resources(), processes=Processes(), poll_interval_seconds=.01)
        runtimes.append(runtime)
        job = media.submit(runtime, actor, "task", operation, {"_media_kind": "waveform", "clip_id": "voice"}, "waveform")
        with repo._connect() as conn:
            row = conn.execute("SELECT * FROM device_work_requests WHERE owner_id=?", (job.id,)).fetchone()
            request = runtime.scheduler._request(row)
        state.update(identity_calls=0, facts_calls=0)
        return SimpleNamespace(repo=repo, media=media, runtime=runtime, actor=actor, job=job, request=request,
            base=base, state=state, entered=entered, release=release)
    yield create
    for runtime in runtimes:
        assert runtime.stop(.2)


def advance(case, connection=None):
    return case.repo.timeline_records.create_timeline_version(TimelineVersion("timeline-next", "local", "task",
        case.base.id, 2, {}, None), connection=connection)


def test_old_waveform_returns_stale_without_identity_or_source_work(media_case):
    case = media_case()
    advance(case)
    with case.repo._connect() as conn:
        assert case.media.check_current(case.request, conn) == "stale"
    assert case.state["identity_calls"] == case.state["facts_calls"] == 0


@pytest.mark.parametrize("verdict", ["ready", "unknown", "unauthorized"])
def test_current_waveform_keeps_fresh_identity_on_every_check(media_case, verdict):
    case = media_case()
    case.state["verdict"] = verdict
    with case.repo._connect() as conn:
        assert case.media.check_current(case.request, conn) == verdict
        assert case.media.check_current(case.request, conn) == verdict
    assert case.state["identity_calls"] == 2
    assert case.state["facts_calls"] == (2 if verdict == "ready" else 0)


def test_current_version_still_revalidates_changed_dependencies(media_case):
    case = media_case()
    case.state["revision"] = 2
    with case.repo._connect() as conn:
        assert case.media.check_current(case.request, conn) == "stale"
    assert case.state["identity_calls"] == case.state["facts_calls"] == 1


@pytest.mark.parametrize("operation,version,include_version", [
    ("audio_waveform", None, True), ("audio_waveform", "", True),
    ("audio_waveform", " ", True), ("audio_waveform", None, False),
    ("audio_import", "timeline-base", True), ("audio_listen", "timeline-base", True),
])
def test_other_or_unbound_contracts_keep_original_identity_check(media_case, operation, version, include_version):
    case = media_case(operation, version, include_version)
    advance(case)
    case.state["verdict"] = "unknown"
    with case.repo._connect() as conn:
        assert case.media.check_current(case.request, conn) == "unknown"
    assert case.state["identity_calls"] == 1 and case.state["facts_calls"] == 0


@pytest.mark.parametrize("changes,expected", [
    ({"owner_type": "other"}, "unauthorized"), ({"member_id": "other"}, "unauthorized"),
    ({"work_class": "normal_make"}, "unauthorized"), ({"source_refs": ("other",)}, "unauthorized"),
    ({"input_digest": "changed"}, "stale"), ({"unit_key": "task_media:changed"}, "stale"),
    ({"resources": {"changed": True}}, "stale"),
])
def test_original_request_fences_precede_fast_version_read(media_case, monkeypatch, changes, expected):
    case = media_case()
    advance(case)
    monkeypatch.setattr(case.repo.task_records, "get_creation_task", lambda *args, **kwargs: pytest.fail("Fence bypassed"))
    with case.repo._connect() as conn:
        assert case.media.check_current(replace(case.request, **changes), conn) == expected
    assert case.state["identity_calls"] == 0


def test_full_sealed_digest_is_checked_before_fast_version_read(media_case, monkeypatch):
    case = media_case()
    with case.repo._connect() as conn:
        event = deepcopy(case.media.read("local", case.job.id, conn)[1])
        event["input_facts"]["base_version_id"] = "forged-version"
        conn.execute("UPDATE task_events SET payload_json=? WHERE id=?",
            (json.dumps(event), "event_" + case.job.id + "_start"))
        monkeypatch.setattr(case.repo.task_records, "get_creation_task", lambda *args, **kwargs: pytest.fail("Corrupt seal bypassed"))
        assert case.media.check_current(case.request, conn) == "stale"
    assert case.state["identity_calls"] == 0


def test_same_connection_sees_uncommitted_version_without_opening_another_connection(media_case, monkeypatch):
    case = media_case()
    with case.repo._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        next_version = advance(case, conn)
        assert case.repo.task_records.get_creation_task("local", "task").current_timeline_version_id == case.base.id
        with monkeypatch.context() as patch:
            patch.setattr(case.repo, "_connect", lambda: pytest.fail("Unexpected extra connection"))
            assert case.repo.task_records.get_creation_task("local", "task", connection=conn).current_timeline_version_id == next_version.id
            with pytest.raises(KeyError):
                case.repo.task_records.get_creation_task("other-workspace", "task", connection=conn)
            with pytest.raises(KeyError):
                case.repo.task_records.get_creation_task("local", "task", include_private=False, connection=conn)
            assert case.media.check_current(case.request, conn) == "stale"
        conn.rollback()
    assert case.repo.task_records.get_creation_task("local", "task").current_timeline_version_id == case.base.id
    with pytest.raises(KeyError):
        case.repo.task_records.get_creation_task("local", "task", include_private=False)
    assert case.state["identity_calls"] == 0


@pytest.mark.parametrize("expired", [True, False])
def test_old_scan_does_not_hold_write_lock_waiting_for_identity(media_case, monkeypatch, expired):
    case = media_case(task_authorization=True)
    if expired:
        advance(case)
    original_check = case.media.check_current
    def slow_observation(request, conn):
        verdict = original_check(request, conn)
        # Only the scheduler's external observation is delayed. The real task
        # authorization still checks local roster/revisions at commit time.
        if verdict == "ready":
            case.entered.set()
            if not case.release.wait(3):
                raise RuntimeError("Controlled owner observation was not released")
        return verdict
    monkeypatch.setattr(case.media, "check_current", slow_observation)
    def short_writer_connection():
        # Fail promptly if a reader/writer is retained during the observation.
        conn = sqlite3.connect(case.repo.database_path, timeout=.05)
        conn.row_factory = sqlite3.Row
        return conn
    monkeypatch.setattr(case.repo, "_connect", short_writer_connection)
    results, errors, done = [], [], threading.Event()
    def select():
        try:
            results.append(case.runtime.scheduler.pick_and_grant())
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()
    worker = threading.Thread(target=select)
    worker.start()
    intent = EditIntent("lock-test-intent", "local", "task", "undo fixture", "montage", [], None,
        "undo_timeline", {}, {}, 1., "interpreted")
    try:
        if expired:
            assert done.wait(1) and not case.entered.is_set()
            assert case.repo.edit_records.create_edit_intent(intent, source=None).id == intent.id
            assert case.state["identity_calls"] == 0
        else:
            assert case.entered.wait(1) and not done.is_set()
            assert case.repo.edit_records.create_edit_intent(intent, source=None).id == intent.id
            advance(case)
    finally:
        case.release.set()
        worker.join(3)
        assert not worker.is_alive()
        if results and results[0] is not None:
            case.runtime.scheduler.fail_unit(results[0], reason="fixture_finished", failure_write=lambda *args: None)
    assert errors == []
    assert results == [None]  # The observed input changed before grant commit.
    assert case.state["identity_calls"] == 0
    assert case.state["facts_calls"] == (0 if expired else 1)
    with case.repo._connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
