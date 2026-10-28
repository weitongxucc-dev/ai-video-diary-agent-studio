"""Linked use cases keep one clock, source scope and repository CAS transaction."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import shutil

import pytest

from app.creation.contracts import EditCommand, TimelineVersion
from app.creation.editing.linked import (
    prepare_linked_insert, prepare_linked_speed, prepare_linked_splice,
)
from app.creation.editing.sources import EditSourceVerifier
from app.creation.transactions import clone_subtitle_track, commit_change_set, prepare_change_set
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from test_task_media_grants import _visual_scope_case


@pytest.fixture
def linked_state(tmp_path, monkeypatch):
    main, repo, actor, media, runtime, client, payload, track, store = _visual_scope_case(tmp_path, monkeypatch)
    plan = creation_task_montage_plan_from_payload(payload)
    audio = plan.audio_timeline.model_copy(deep=True)
    audio.tracks[0].locked = audio.tracks[0].clips[0].locked = False
    plan.audio_timeline = audio
    # A distinct owned neighbour makes a real later permission revocation visible.
    original = repo.get_asset("local", "asset")
    path = tmp_path / "library" / "neighbour.mp4"
    shutil.copyfile(original.path, path)
    stat = path.stat()
    neighbour = replace(
        original, id="neighbour", file_name=path.name, path=str(path), size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        segments=[replace(original.segments[0], id="neighbour-segment", asset_id="neighbour")],
    )
    repo._save_asset(neighbour)
    plan.clips[2].asset_id = neighbour.id
    plan.clips[2].segment_id = neighbour.segments[0].id
    plan.clips[2].source_path = repo.workspace_media_reference_for_asset("local", neighbour.id)
    seeded = creation_task_montage_plan_to_payload(plan)
    repo.save_creation_task_montage_plan("local", "task", plan.id, seeded)
    captions = clone_subtitle_track(track, timeline_version_id="linked-base")
    repo.create_timeline_version(TimelineVersion(
        "linked-base", "local", "task", "timeline", 2,
        {"montage": seeded, "subtitle_track_ids": [captions.id]}, None,
    ))
    repo.create_subtitle_track(captions)
    task = repo.task_records.get_creation_task("local", "task")
    identity = {"member_id": actor.member_id, "local_diagnostic": False}
    verifier = EditSourceVerifier(repo, identity=identity)
    monkeypatch.setattr(main, "_content_reader", lambda *_: (actor.member_id, False))
    try:
        yield repo, task, plan, captions, identity, verifier
    finally:
        runtime.stop(0)


def prepare(state, operation):
    repo, task, plan, track, identity, verifier = state
    values = {"clip_id": "clip_1"}
    if operation == "ChangeLinkedSpeed":
        values["playback_rate"] = 2.0
    if operation == "InsertClip":
        values.update(asset_id="asset", segment_id="asset-segment", source_start_seconds=0.0)
    command = EditCommand("local", "task", identity, "linked-base", operation, values)
    kwargs = {"verifier": verifier, "output_refs": ["artifacts/task/previous.mp4"]}
    if operation == "ChangeLinkedSpeed":
        checked = []
        result = prepare_linked_speed(task, plan, [track], plan.audio_timeline, command,
            validate_audio=lambda audio, duration: checked.append((audio, duration)), **kwargs)
        assert checked == [(result.plan.audio_timeline, result.plan.duration_seconds)]
        return result
    if operation == "InsertClip":
        inserted = replace(plan.clips[1], id="inserted", source_start_seconds=0, source_end_seconds=2,
            transition_in_seconds=0)
        return prepare_linked_insert(task, plan, [track], plan.audio_timeline, command, inserted, **kwargs)
    return prepare_linked_splice(task, plan, [track], plan.audio_timeline, command, **kwargs)


def change_set(state, prepared):
    import app.main as main
    from app.subtitle_tracks import render_srt

    repo, task, plan, track, identity, verifier = state
    repo.create_edit_intent(prepared.intent)
    repo.create_edit_proposal(prepared.proposal)
    captions = []
    for item in prepared.subtitle_tracks:
        reference, transform = main._publish_subtitle_text(
            workspace_id="local", task_id="task", proposal_id=prepared.proposal.id,
            kind="subtitle_retime", input_refs=["timeline:linked-base", prepared.plan.narration_audio_path],
            content=render_srt(main._execution_subtitle_track(item)), engine="timeline-clock-v1",
            parameters={"language": item.language, "operation": prepared.command.operation_type},
        )
        captions.append(clone_subtitle_track(item, timeline_version_id="linked-result",
            artifact_ref=reference, transform_id=transform))
    return prepare_change_set(
        proposal=prepared.proposal, base_version=repo.get_timeline_version("local", "linked-base"),
        operation_type=prepared.command.operation_type, operation_payload=prepared.proposal.operation["payload"],
        result_version_id="linked-result", montage_plan_id=prepared.plan.id,
        montage_payload=creation_task_montage_plan_to_payload(prepared.plan),
        subtitle_tracks=captions, cover_asset=None,
    )


@pytest.mark.parametrize("operation,delta,outside_start", [
    ("DeleteClip", -6, 3), ("DuplicateClip", 6, 15),
    ("ChangeLinkedSpeed", -3, 6), ("InsertClip", 2, 11),
])
def test_linked_use_case_prepares_without_writes_then_jointly_commits_all_lanes(linked_state, operation, delta, outside_start):
    repo, task, plan, track, identity, verifier = linked_state
    before = asdict(plan)
    with repo._connect() as conn:
        persisted = tuple(conn.iterdump())
    prepared = prepare(linked_state, operation)
    with repo._connect() as conn:
        assert tuple(conn.iterdump()) == persisted
    assert asdict(plan) == before
    assert prepared.plan.duration_seconds == plan.duration_seconds + delta
    outside = next(cue for cue in prepared.subtitle_tracks[0].cues if cue.id == "outside")
    assert outside.start_seconds == outside_start
    old_refs = {clip.source_ref for lane in plan.audio_timeline.tracks for clip in lane.clips}
    assert {clip.source_ref for lane in prepared.plan.audio_timeline.tracks for clip in lane.clips} == old_refs
    assert "clip_2" in prepared.command.affected_clip_ids
    assert prepared.intent.constraints["affected_source_scope"] == verifier.scope_payload(prepared.command)
    change = change_set(linked_state, prepared)
    _, operation_record, version, _ = commit_change_set(
        repo, change, verify_sources=lambda conn: verifier.verify(prepared.command, conn)
    )
    assert version.id == repo.task_records.get_creation_task("local", "task").current_timeline_version_id == "linked-result"
    assert version.sequence == 3 and operation_record.result_version_id == version.id
    actual = repo.get_creation_task_montage_plan("local", "task")
    assert actual["audio_timeline"] == prepared.plan.audio_timeline.model_dump()
    assert actual["clips"] == creation_task_montage_plan_to_payload(prepared.plan)["clips"]
    saved = repo.list_subtitle_tracks("local", "task", timeline_version_id=version.id)
    assert [asdict(cue) for cue in saved[0].cues] == [asdict(cue) for cue in prepared.subtitle_tracks[0].cues]


def test_linked_commit_rechecks_moved_neighbour_inside_same_transaction(linked_state):
    repo, task, plan, track, identity, verifier = linked_state
    prepared = prepare(linked_state, "DeleteClip")
    change = change_set(linked_state, prepared)
    neighbour = repo.get_asset("local", "neighbour")
    repo._save_asset(replace(neighbour, owner_user_id="another-member", visibility="private"))
    with pytest.raises(PermissionError):
        commit_change_set(repo, change, verify_sources=lambda conn: verifier.verify(prepared.command, conn))
    assert repo.task_records.get_creation_task("local", "task").current_timeline_version_id == "linked-base"
    assert repo.get_edit_proposal("local", prepared.proposal.id).status == "proposed"
    assert repo.list_edit_operations("local", "task") == []
    assert len(repo.list_timeline_versions("local", "task")) == 2


def test_linked_command_rejects_stale_version_before_candidate_or_persistence(linked_state):
    repo, task, plan, track, identity, verifier = linked_state
    command = EditCommand("local", "task", identity, "old-version", "DeleteClip", {"clip_id": "clip_1"})
    from app.creation.editing.picture import EditConflict
    with pytest.raises(EditConflict, match="时间线已经变化"):
        prepare_linked_splice(task, plan, [track], plan.audio_timeline, command,
            verifier=verifier, output_refs=[])
    assert repo.list_edit_proposals("local", "task") == []


def test_actual_linked_http_adoption_rechecks_neighbour_revoked_at_commit(linked_state, monkeypatch):
    import app.main as main
    from fastapi.testclient import TestClient

    repo, task, plan, track, identity, verifier = linked_state
    client = TestClient(main.app)
    preview = client.post("/api/creation-tasks/task/edits/exact/preview?draft=true", json={
        "operation_type": "DeleteClip", "clip_id": "clip_1",
    })
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    intent = repo.get_edit_intent("local", proposal["intent_id"])
    assert "clip_2" in intent.constraints["affected_source_scope"]["clip_ids"]
    commit = repo.edit_adoption.confirm

    def revoke_before_commit(*args, **kwargs):
        assert kwargs["verify_sources"] is not None
        repo._save_asset(replace(repo.get_asset("local", "neighbour"),
            owner_user_id="another-member", visibility="private"))
        return commit(*args, **kwargs)

    monkeypatch.setattr(repo.edit_adoption, "confirm", revoke_before_commit)
    result = client.post(f"/api/creation-tasks/task/edits/{proposal['id']}/confirm")
    assert result.status_code == 403, result.text
    assert repo.task_records.get_creation_task("local", "task").current_timeline_version_id == "linked-base"
    assert repo.get_edit_proposal("local", proposal["id"]).status == "proposed"
    assert repo.list_edit_operations("local", "task") == []
    assert len(repo.list_timeline_versions("local", "task")) == 2


@pytest.mark.parametrize("operation", ["DeleteClip", "DuplicateClip", "ChangeLinkedSpeed", "InsertClip"])
def test_linked_preview_sql_refusal_rolls_back_intent_and_proposal_together(linked_state, operation):
    import app.main as main
    from fastapi.testclient import TestClient

    repo, task, plan, track, identity, verifier = linked_state
    with repo._connect() as conn:
        conn.execute("""CREATE TRIGGER reject_linked_proposal BEFORE INSERT ON edit_proposals
            BEGIN SELECT RAISE(ABORT, 'qa_linked_proposal_refusal'); END""")
        before = tuple(conn.iterdump())
    payload = {"operation_type": operation, "clip_id": "clip_1"}
    if operation == "ChangeLinkedSpeed":
        payload["playback_rate"] = 2.0
    if operation == "InsertClip":
        payload.update(asset_id="asset", segment_id="asset-segment", source_start_seconds=0.0)
    response = TestClient(main.app, raise_server_exceptions=False).post(
        "/api/creation-tasks/task/edits/exact/preview?draft=true", json=payload)
    assert response.status_code == 500, response.text
    with repo._connect() as conn:
        assert tuple(conn.iterdump()) == before
    assert repo.list_edit_intents("local", "task") == repo.list_edit_proposals("local", "task") == []
    assert repo.task_records.get_creation_task("local", "task").current_timeline_version_id == "linked-base"


def test_linked_preview_cas_refuses_version_advanced_at_repository_save(linked_state, monkeypatch):
    import app.main as main
    from fastapi.testclient import TestClient

    repo, task, plan, track, identity, verifier = linked_state
    save = repo.save_clip_preview_proposal

    def advance_before_save(intent, proposal, *, expected_version_id):
        assert expected_version_id == "linked-base"
        base = repo.get_timeline_version("local", expected_version_id)
        repo.create_timeline_version(replace(base, id="concurrent-version", parent_version_id=base.id,
            sequence=base.sequence + 1))
        return save(intent, proposal, expected_version_id=expected_version_id)

    monkeypatch.setattr(repo, "save_clip_preview_proposal", advance_before_save)
    response = TestClient(main.app).post("/api/creation-tasks/task/edits/exact/preview?draft=true", json={
        "operation_type": "DeleteClip", "clip_id": "clip_1",
    })
    assert response.status_code == 409, response.text
    assert repo.list_edit_intents("local", "task") == repo.list_edit_proposals("local", "task") == []
    assert repo.task_records.get_creation_task("local", "task").current_timeline_version_id == "concurrent-version"


def test_fixed_silence_ranges_adopt_once_without_detection(tmp_path):
    import json
    import os
    from pathlib import Path
    from app.audio_timeline import AudioTimeline
    from app.creation.editing.linked_timeline_use_case import LinkedTimelineEditUseCase
    from app.creation.editing.metadata import make_metadata_proposal
    from app.creation.editing.metadata_use_case import metadata_edit_command
    from app.production import ProductionRepository
    from test_metadata_edit_use_case import metadata_state

    state = metadata_state(tmp_path)
    # A declared silent editorial graph keeps this node metadata-only.
    state.plan.audio_timeline = AudioTimeline(tracks=[])
    payload = creation_task_montage_plan_to_payload(state.plan)
    state.repo.save_creation_task_montage_plan('ws', state.task.id, state.plan.id, payload)
    base = TimelineVersion('silence-base', 'ws', state.task.id, state.base.id, 2,
        {**state.base.snapshot, 'montage': payload, 'subtitle_track_ids': ['silence-zh']}, None)
    state.repo.create_timeline_version(base)
    state.repo.create_subtitle_track(replace(state.track, id='silence-zh', timeline_version_id=base.id))
    task = state.repo.task_records.get_creation_task('ws', state.task.id)
    values = {'base_version_id': base.id, 'removed_ranges_ms': [[1000, 1500]]}
    command = metadata_edit_command(task, state.identity, 'RemoveSilence', values)
    intent, proposal = make_metadata_proposal(command, ['clip:clip'], '采用既有固定停顿范围', values)
    state.repo.create_edit_intent(intent)
    state.repo.create_edit_proposal(proposal)
    source_identity = (state.source.stat().st_size, state.source.stat().st_mtime_ns)
    before = len(state.repo.list_timeline_versions('ws', task.id))
    case = LinkedTimelineEditUseCase(state.repo, state.case)
    result = case.confirm_remove_silence(task, proposal)
    fresh = ProductionRepository(state.repo.database_path, tmp_path / 'library')
    current = fresh.task_records.get_creation_task('ws', task.id)
    version = fresh.get_timeline_version('ws', current.current_timeline_version_id)
    operations = fresh.list_edit_operations('ws', task.id)
    tracks = fresh.list_subtitle_tracks('ws', task.id, timeline_version_id=version.id)
    assert len(fresh.list_timeline_versions('ws', task.id)) == before + 1
    assert len(operations) == 1 and operations[0].proposal_id == proposal.id
    assert result.operation.id == operations[0].id and result.operation.result_version_id == version.id
    assert version.parent_version_id == base.id and version.sequence == 3
    assert result.montage.duration_seconds == 3.5
    assert sum(clip.timeline_end_seconds - clip.timeline_start_seconds for clip in result.montage.clips) == 3.5
    assert version.snapshot['montage']['duration_seconds'] == 3.5
    assert tracks and max(cue.end_seconds for track in tracks for cue in track.cues) <= 3.5
    assert result.montage.audio_timeline.tracks == []
    assert result.montage.narration_audio_path == state.plan.narration_audio_path
    assert fresh.get_timeline_version('ws', base.id).snapshot == base.snapshot
    assert fresh.get_edit_proposal('ws', proposal.id).status == 'confirmed'
    assert source_identity == (state.source.stat().st_size, state.source.stat().st_mtime_ns)
    assert len(fresh.list_edit_operations('ws', task.id)) == 1
    evidence = os.environ.get('LEGACY_CONFIRM_EVIDENCE_DIR')
    if evidence:
        Path(evidence, 'SILENCE_NORMAL_FACTS.json').write_text(json.dumps({'proposal': proposal.id,
            'operation': operations[0].id, 'base_version': base.id, 'result_version': version.id,
            'versions_added': 1, 'operations': 1, 'fixed_ranges_ms': values['removed_ranges_ms'],
            'result_duration_seconds': 3.5, 'graph': 'declared silent editorial fixture',
            'fresh_readback': True, 'detection_native_ASR_calls': 0}, indent=2))
