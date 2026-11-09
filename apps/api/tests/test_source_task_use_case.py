"""Real isolated SQL and roster; declared source bytes are not media acceptance."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import subprocess
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.creation.source_task_use_case import (
    SourceTaskCommand, SourceTaskConflict, SourceTaskIdentityUnavailable, SourceTaskUseCase,
)
from app.creation_execution import CreationExecutionActor
from app.local_members import LocalMemberService
from app.materials.contracts import MaterialAsset
from app.montage import creation_task_montage_plan_from_payload
from app.production import ProductionRepository


@pytest.fixture
def source_case(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("source bootstrap metadata tests cannot start media or model processes")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    library = tmp_path / "library"
    library.mkdir()
    path = library / "original.mp4"
    path.write_bytes(b"declared metadata fixture, not encoded media")
    repo = ProductionRepository(tmp_path / "isolated.sqlite3", library)
    members = LocalMemberService(repo.database_path)
    member = members.add_member("source owner")
    actor = CreationExecutionActor("local", member.member_id)
    observed = path.stat()
    asset = repo._save_asset(MaterialAsset("source_asset", "local", path.name, str(path), observed.st_size,
        datetime.fromtimestamp(observed.st_mtime, timezone.utc), duration_seconds=9.25,
        analysis_status="ready", review_status="raw", owner_user_id=actor.member_id, segments=[]))
    reference = repo.workspace_media_reference_for_asset("local", asset.id)
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id="local", reference=reference)
    command = SourceTaskCommand(str(uuid4()), asset.id, asset.updated_at, 0, 9.25,
                                "原声接续", "16:9", True)
    def verify(current):
        assert current == actor
        # A second writer can enter while the external identity port runs.
        with repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
        return "ready"
    return SimpleNamespace(repo=repo, members=members, actor=actor, asset=asset, path=path,
        reference=reference, command=command, verify=verify, use_case=SourceTaskUseCase(repo, verify))


def counts(repo):
    with repo._connect() as conn:
        return {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("creation_plans", "creation_tasks", "creation_task_montage_plans",
                              "timeline_versions", "task_events", "creation_task_scripts", "narration_artifacts")}


def test_source_range_creates_real_task_without_script_or_analysis_segment(source_case):
    case = source_case
    before = asdict(case.repo.get_asset("local", case.asset.id))
    source_bytes = case.path.read_bytes()
    command = replace(case.command, source_start_seconds=1.25, source_end_seconds=7.75)
    receipt = case.use_case.create(command, case.actor)
    task = case.repo.task_records.get_creation_task("local", receipt["task_id"])
    plan = case.repo.plan_records.get_creation_plan("local", task.plan_id)
    version = case.repo.timeline_records.get_timeline_version("local", task.current_timeline_version_id)
    montage = creation_task_montage_plan_from_payload(version.snapshot["montage"])
    assert task.confirmed_script_id is None and task.narration_cache_key is None
    assert task.stage == "montage" and not task.output_ready
    assert plan.duration_target_seconds == montage.duration_seconds == 6.5
    assert plan.source_media == receipt["source_media"]
    assert plan.selected_voice is None and plan.selected_person is None and not plan.blockers
    assert montage.script_id is None and montage.cues == [] and montage.narration_audio_path is None
    clip, = montage.clips
    assert clip.segment_id is None and clip.cue_id is None and clip.manually_selected
    assert (clip.source_path, clip.source_start_seconds, clip.source_end_seconds) == (case.reference, 1.25, 7.75)
    track, = montage.audio_timeline.tracks
    voice, = track.clips
    assert track.role == "original" and voice.source_ref == case.reference
    assert (voice.source_start_ms, voice.source_end_ms, voice.timeline_start_ms) == (1250, 7750, 0)
    assert counts(case.repo) == {"creation_plans": 1, "creation_tasks": 1, "creation_task_montage_plans": 1,
        "timeline_versions": 1, "task_events": 1, "creation_task_scripts": 0, "narration_artifacts": 0}
    assert asdict(case.repo.get_asset("local", case.asset.id)) == before
    assert case.path.read_bytes() == source_bytes


def test_same_request_reads_committed_receipt_before_source_or_identity_observation(source_case, monkeypatch):
    case = source_case
    receipt = case.use_case.create(case.command, case.actor)
    before = counts(case.repo)
    case.path.unlink()
    monkeypatch.setattr(case.use_case, "verify_identity", lambda _: pytest.fail("replay must query original outcome"))
    assert case.use_case.read(case.command.request_id, case.actor) == receipt
    assert case.use_case.create(case.command, case.actor) == receipt
    with pytest.raises(SourceTaskConflict, match="UUID"):
        case.use_case.create(replace(case.command, preserve_audio=False), case.actor)
    other = case.members.add_member("different owner")
    assert case.use_case.read(case.command.request_id, CreationExecutionActor("local", other.member_id)) is None
    assert case.use_case.read(str(uuid4()), case.actor) is None
    assert counts(case.repo) == before


@pytest.mark.parametrize("change", ["private", "excluded", "reference_only", "rejected", "pending", "version"])
def test_source_adoption_obeys_actual_asset_permissions_and_manual_eligibility(source_case, change):
    case = source_case
    asset = case.repo.get_asset("local", case.asset.id)
    if change == "private":
        asset.owner_user_id = case.members.add_member("asset owner").member_id
    elif change in {"excluded", "reference_only"}:
        asset.use_state = change
    elif change == "rejected":
        asset.review_status = "rejected"
    elif change == "pending":
        asset.analysis_status = "pending"
    else:
        asset.user_description = "new source revision"
    changed = case.repo._save_asset(asset)
    command = case.command if change == "version" else replace(case.command, expected_asset_updated_at=changed.updated_at)
    with pytest.raises(PermissionError if change == "private" else SourceTaskConflict):
        case.use_case.create(command, case.actor)
    assert not any(counts(case.repo).values())


@pytest.mark.parametrize("change", ["member", "asset"])
def test_identity_observation_races_are_rechecked_in_original_writer(source_case, change):
    case = source_case
    def observe(actor):
        case.verify(actor)
        if change == "member":
            case.members.remove_member(actor.member_id)
        else:
            asset = case.repo.get_asset("local", case.asset.id)
            asset.use_state = "excluded"
            case.repo._save_asset(asset)
        return "ready"
    use_case = SourceTaskUseCase(case.repo, observe)
    with pytest.raises(PermissionError if change == "member" else SourceTaskConflict):
        use_case.create(case.command, case.actor)
    assert not any(counts(case.repo).values())


def test_source_creation_rolls_back_every_object_if_initial_version_fails(source_case, monkeypatch):
    case = source_case
    def fail(version, *, connection, expected_current_version_id):
        assert connection.in_transaction
        assert connection.execute("SELECT count(*) FROM creation_task_montage_plans").fetchone()[0] == 1
        raise RuntimeError("initial version storage failed")
    monkeypatch.setattr(case.repo.timeline_records, "create_timeline_version", fail)
    with pytest.raises(RuntimeError, match="initial version storage failed"):
        case.use_case.create(case.command, case.actor)
    assert not any(counts(case.repo).values())
    assert case.use_case.read(case.command.request_id, case.actor) is None
    assert case.path.is_file() and case.repo.get_asset("local", case.asset.id).segments == []


def test_same_source_request_race_creates_only_one_atomic_result(source_case):
    case = source_case
    barrier = Barrier(2)
    def observe(actor):
        case.verify(actor)
        barrier.wait(timeout=5)
        return "ready"
    use_case = SourceTaskUseCase(case.repo, observe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(use_case.create, case.command, case.actor) for _ in range(2)]
        first, second = [future.result(timeout=10) for future in futures]
    assert first == second == use_case.read(case.command.request_id, case.actor)
    assert counts(case.repo)["creation_tasks"] == counts(case.repo)["task_events"] == 1


def test_source_creation_keeps_filesystem_and_identity_outside_writer(source_case, monkeypatch):
    case = source_case
    original_connect, original_stat = sqlite3.connect, Path.stat
    connections = []
    def connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        connections.append(conn)
        return conn
    def observed_stat(path, *args, **kwargs):
        if path.name == case.path.name:
            for conn in connections:
                try:
                    assert not conn.in_transaction
                except sqlite3.ProgrammingError:
                    pass  # Closed connections hold no writer.
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(Path, "stat", observed_stat)
    receipt = case.use_case.create(replace(case.command, preserve_audio=False), case.actor)
    version = case.repo.timeline_records.get_timeline_version("local", receipt["timeline_version_id"])
    assert version.snapshot["montage"]["audio_timeline"]["tracks"] == []


@pytest.mark.parametrize("reject_guard", [False, True])
def test_agent_source_is_fixed_and_original_input_guard_shares_atomic_writer(source_case, reject_guard):
    case = source_case
    source = {"kind": "assistant_input", "member_id": case.actor.member_id,
              "assistant_turn_id": str(uuid4()), "source_input_id": str(uuid4())}
    def guard(conn):
        assert conn.in_transaction
        assert conn.execute("SELECT count(*) FROM creation_tasks").fetchone()[0] == 0
        if reject_guard:
            raise PermissionError("original turn stopped")
    if reject_guard:
        with pytest.raises(PermissionError, match="original turn stopped"):
            case.use_case.create(case.command, case.actor, source=source, write_guard=guard)
        assert not any(counts(case.repo).values())
        return
    receipt = case.use_case.create(case.command, case.actor, source=source, write_guard=guard)
    plan = case.repo.plan_records.get_creation_plan("local", receipt["plan_id"])
    assert plan.adoption["kind"] == "delegated_system" and plan.adoption["operator"] == "system"
    assert plan.adoption["authorization_source"] == source
    with pytest.raises(SourceTaskConflict, match="UUID"):
        case.use_case.create(case.command, case.actor, source={**source, "source_input_id": str(uuid4())})
    assert counts(case.repo)["creation_tasks"] == 1


@pytest.mark.parametrize("change", ["unregistered", "duration_unknown", "file_changed", "range"])
def test_bootstrap_never_guesses_missing_or_changed_source_facts(source_case, change):
    case = source_case
    command = case.command
    if change == "unregistered":
        with case.repo._connect() as conn:
            conn.execute("DELETE FROM workspace_media_references WHERE reference=?", (case.reference,))
    elif change == "duration_unknown":
        asset = case.repo._save_asset(replace(case.asset, duration_seconds=None))
        command = replace(command, expected_asset_updated_at=asset.updated_at)
    elif change == "file_changed":
        case.path.write_bytes(b"different source revision")
    else:
        command = replace(command, source_end_seconds=10)
    with pytest.raises(ValueError):
        case.use_case.create(command, case.actor)
    assert not any(counts(case.repo).values())


@pytest.mark.parametrize("verdict", ["unknown", "unauthorized"])
def test_unavailable_identity_is_distinct_from_revocation_and_writes_neither(source_case, verdict):
    case = source_case
    use_case = SourceTaskUseCase(case.repo, lambda _: verdict)
    with pytest.raises(SourceTaskIdentityUnavailable if verdict == "unknown" else PermissionError):
        use_case.create(case.command, case.actor)
    assert not any(counts(case.repo).values())


def test_explicit_source_selection_keeps_manual_not_automatic_use_scope(source_case):
    case = source_case
    asset = case.repo._save_asset(replace(case.asset, use_state="not_for_automation"))
    receipt = case.use_case.create(replace(case.command, expected_asset_updated_at=asset.updated_at), case.actor)
    assert receipt["source_media"]["asset_id"] == asset.id
    assert case.repo.get_asset("local", asset.id).use_state == "not_for_automation"
