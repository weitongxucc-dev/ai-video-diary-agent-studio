"""Queued storyboard replacement over isolated SQLite; no codecs or models."""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.main as main
from app.creation.execution_control import CreationExecutionControl
from app.creation_execution import (CreationExecution, CreationExecutionActor, CreationJobOwnerVerifier,
    CreationJobUnitExecutor, revision_digest)
from app.device_scheduler import DeviceScheduler
from app.host_resource_probes import MontageBudget
from app.montage import creation_task_montage_plan_from_payload
from app.montage import creation_task_montage_plan_to_payload
from app.audio_timeline import narration_audio_timeline
from app.graphic_overlays import TextOverlay
from app.creation.contracts import CreationPlanVoiceReference
from app.schemas import StoryboardRegenerationRequest
from app.creation.transactions import clone_subtitle_track, clone_cover_asset
from _media_preview_test_fixtures import register_synthetic_profiles
from test_timeline_restore_application import restore_http_state
from test_device_scheduler import Resources, Processes


@pytest.fixture
def state(tmp_path, monkeypatch):
    state = restore_http_state(tmp_path, monkeypatch)
    person, voice, *_ = register_synthetic_profiles(state.repo, workspace_id="ws",
        member_id=state.member.member_id, voice_usage_scopes=("narration",))
    plan = state.repo.get_creation_plan("ws", state.task.plan_id)
    plan.selected_voice = CreationPlanVoiceReference(voice.id, voice.version, person.id, person.version,
        person.authorization.id, person.authorization.version, voice.authorization.id, voice.authorization.version,
        "zh-CN", voice.model_id)
    plan.duration_target_seconds = 4
    with state.repo._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE id=?", (json.dumps(asdict(plan), default=str), plan.id))
    state.segments = [replace(item, audio_reference=state.repo.narration_artifact_reference(state.narration.cache_key))
        for item in state.segments]
    state.repo.save_narration_segments("ws", state.task.id, "fixed-script", state.segments)
    old = creation_task_montage_plan_from_payload(state.repo.get_creation_task_montage_plan("ws", state.task.id))
    old.narration_audio_path = state.repo.narration_artifact_reference(state.narration.cache_key)
    old.graphic_overlays = [TextOverlay(text="保留人工文字", start_seconds=0, end_seconds=4, font_size=42).model_dump()]
    old.audio_timeline = narration_audio_timeline(old.narration_audio_path, 4)
    old.audio_timeline.tracks[0].gain_db = -3
    old.audio_timeline.tracks[0].clips[0].fade_out_ms = 200
    old_payload = creation_task_montage_plan_to_payload(old)
    state.repo.save_creation_task_montage_plan("ws", state.task.id, old.id, old_payload)
    tracks = [clone_subtitle_track(state.repo.get_subtitle_track("ws", state.task.id, track_id),
        timeline_version_id="regeneration-base") for track_id in state.base.snapshot["subtitle_track_ids"]]
    cover = clone_cover_asset(state.cover, timeline_version_id="regeneration-base")
    state.base = replace(state.base, id="regeneration-base", parent_version_id=state.base.id,
        sequence=state.base.sequence + 1, snapshot={**state.base.snapshot, "montage": old_payload,
            "subtitle_track_ids": [track.id for track in tracks], "cover_asset_id": cover.id,
            "narration_segments": [asdict(item) for item in state.segments]})
    state.repo.create_timeline_version(state.base)
    for track in tracks:
        state.repo.create_subtitle_track(track)
    state.repo.create_cover_asset(cover)
    state.task = state.repo.get_creation_task("ws", state.task.id)
    state.actor = CreationExecutionActor("ws", state.member.member_id)
    def forbidden(*args, **kwargs):
        pytest.fail("storyboard-only work must not execute narration, rendering or a synchronous job")
    state.execution = CreationExecution(state.repo,
        run_montage_plan=forbidden, run_render=forbidden, render_is_valid=lambda *_: False)
    state.stage = main._montage_stage_use_case(state.repo)
    state.builds = []
    def build(workspace_id, task_id, *, grant_context):
        grant_context.check()
        state.builds.append((workspace_id, task_id))
        old = creation_task_montage_plan_from_payload(state.repo.get_creation_task_montage_plan(workspace_id, task_id))
        return replace(old, id=f"regenerated_{len(state.builds)}", status="ready",
            clips=[replace(clip, id=f"regenerated_{clip.id}") for clip in old.clips])
    monkeypatch.setattr(state.stage, "build_candidate", build)
    state.owner = CreationJobOwnerVerifier(state.repo, montage_budget=MontageBudget(20, 20, 1_000_000, 1_000_000, 30, 1_000_000),
        verify_identity=lambda actor: "ready")
    state.scheduler = DeviceScheduler(state.repo.database_path, tmp_path / "permit.lock",
        owners=state.owner, resources=Resources(), processes=Processes())
    state.executor = CreationJobUnitExecutor(state.repo, render_action=forbidden, render_is_valid=forbidden,
        montage_stage=state.stage)
    state.runtime = SimpleNamespace(scheduler=state.scheduler, wake=lambda: None,
        registrations={"creation_job": SimpleNamespace(owner=state.owner, executor=state.executor)})
    state.control = CreationExecutionControl(state.repo, state.execution, SimpleNamespace(valid=lambda *_: False), lambda _: "ready")
    monkeypatch.setattr(main, "_creation_runtime", lambda: state.runtime)
    monkeypatch.setattr(main, "_creation_execution_control", lambda: state.control)
    monkeypatch.setattr(main, "_require_permit_actor", lambda *_: state.actor)
    state.url = state.base_url + "/montage/plan"
    yield state
    state.scheduler.close()


def inputs(state):
    task = state.repo.get_creation_task("ws", state.task.id)
    script = state.repo.get_creation_task_script("ws", task.confirmed_script_id)
    plan = state.repo.get_creation_plan("ws", task.plan_id)
    version = state.repo.get_timeline_version("ws", task.current_timeline_version_id)
    return {"request_id": str(uuid4()), "script_id": script.id, "input_digest": revision_digest(script, plan),
        "expected_timeline_version_id": version.id, "expected_timeline_sequence": version.sequence}


def run(state):
    grant = state.scheduler.pick_and_grant()
    assert grant is not None
    state.executor.execute(grant, state.scheduler)
    return grant


def test_http_queue_original_receipt_granted_commit_and_undo(state):
    payload = inputs(state)
    task = state.repo.get_creation_task("ws", state.task.id)
    script, plan = state.execution.validate_input("ws", task, payload["script_id"], payload["input_digest"])
    assert state.repo.get_narration_artifact("ws", task.narration_cache_key).script_id == script.id
    state.owner.stage_facts(task, script, plan, "montage_plan")
    before = state.repo.get_creation_task_montage_plan("ws", state.task.id)
    versions = state.repo.list_timeline_versions("ws", state.task.id)
    response = state.client.post(state.url, json=payload)
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert receipt["status"] == "queued" and receipt["result"] is None
    assert state.builds == [] and state.repo.get_creation_task_montage_plan("ws", state.task.id) == before
    assert state.client.post(state.url, json=payload).json() == receipt
    assert state.client.get(receipt["result_url"]).json() == receipt
    with pytest.raises(PermissionError, match="设备队列"):
        state.execution.execute("ws", receipt["job_id"])
    run(state)
    completed = state.client.get(receipt["result_url"]).json()
    assert completed["status"] == "succeeded" and len(state.builds) == 1, state.repo.get_job_run("ws", receipt["job_id"]).failure_reason
    result = completed["result"]
    current = state.repo.get_creation_task_montage_plan("ws", state.task.id)
    assert current["id"] == result["montage_plan_id"] == before["id"]
    assert current["clips"] != before["clips"]
    assert all(current[key] == before[key] for key in ("graphic_overlays", "audio_timeline", "narration_audio_path", "aspect_ratio"))
    version = state.repo.get_timeline_version("ws", result["timeline_version_id"])
    assert version.parent_version_id == payload["expected_timeline_version_id"]
    old_version = state.repo.get_timeline_version("ws", payload["expected_timeline_version_id"])
    for old_id, new_id in zip(old_version.snapshot["subtitle_track_ids"], version.snapshot["subtitle_track_ids"]):
        old_track, new_track = (state.repo.get_subtitle_track("ws", state.task.id, key) for key in (old_id, new_id))
        assert old_track.cues == new_track.cues and old_track.style == new_track.style
        assert (old_track.artifact_ref, old_track.transform_id) == (new_track.artifact_ref, new_track.transform_id)
    old_cover = state.repo.get_cover_asset("ws", state.task.id, old_version.snapshot["cover_asset_id"])
    new_cover = state.repo.get_cover_asset("ws", state.task.id, version.snapshot["cover_asset_id"])
    assert replace(new_cover, id=old_cover.id, timeline_version_id=old_cover.timeline_version_id,
        created_at=old_cover.created_at, updated_at=old_cover.updated_at) == old_cover
    assert state.repo.list_narration_segments("ws", state.task.id, "fixed-script") == state.segments
    assert state.repo.get_timeline_version("ws", versions[-1].id) == versions[-1]
    count = len(state.repo.list_timeline_versions("ws", state.task.id))
    assert state.client.post(state.url, json=payload).json() == completed
    state.control.recover(state.runtime)
    assert state.scheduler.pick_and_grant() is None and len(state.builds) == 1
    assert state.repo.list_timeline_versions("ws", state.task.id)[-1].snapshot["montage"] == version.snapshot["montage"]
    undone = state.client.post(state.base_url + "/timeline/undo", json={
        "request_id": str(uuid4()),
        "expected_timeline_version_id": version.id, "expected_timeline_sequence": version.sequence})
    assert undone.status_code == 200, undone.text
    assert state.repo.get_creation_task_montage_plan("ws", state.task.id)["id"] == before["id"]
    restored = state.repo.get_creation_task_montage_plan("ws", state.task.id)
    assert all(restored[key] == before[key] for key in ("clips", "graphic_overlays", "audio_timeline"))
    assert state.client.post(state.url, json=payload).json() == completed
    assert len(state.builds) == 1 and len(state.repo.list_timeline_versions("ws", state.task.id)) >= count


def adopted(state):
    return (state.repo.get_creation_task("ws", state.task.id),
        state.repo.get_creation_task_montage_plan("ws", state.task.id),
        state.repo.list_timeline_versions("ws", state.task.id),
        state.repo.list_narration_segments("ws", state.task.id, "fixed-script"))


def enqueue(state, payload=None):
    response = state.client.post(state.url, json=payload or inputs(state))
    assert response.status_code == 202, response.text
    return response.json()


def test_rejects_changed_original_input_stale_CAS_and_foreign_identity(state):
    payload = inputs(state)
    before = adopted(state)
    assert state.client.post(state.url).status_code == 422
    assert state.client.post(state.url, json={**payload, "expected_timeline_sequence": 999}).status_code == 409
    receipt = enqueue(state, payload)
    assert state.client.post(state.url, json={**payload, "input_digest": "a" * 64}).status_code == 409
    other = replace(state.actor, member_id="other-member")
    with pytest.raises(PermissionError):
        from app.creation.storyboard_regeneration import StoryboardRegeneration
        StoryboardRegeneration(state.repo).read("ws", state.task.id, payload["request_id"], other)
    wrong_workspace = replace(state.actor, workspace_id="foreign")
    with pytest.raises(PermissionError):
        StoryboardRegeneration(state.repo).read("ws", state.task.id, payload["request_id"], wrong_workspace)
    assert state.client.get(receipt["result_url"], headers={"x-test-workspace": "foreign"}).status_code == 404
    assert adopted(state) == before and not state.builds


def test_existing_queued_work_requires_explicit_product_cancel_and_release(state):
    payload = inputs(state)
    old = state.execution.start("ws", state.task.id, payload["script_id"], payload["input_digest"], actor=state.actor)
    response = state.client.post(state.url, json=payload)
    assert response.status_code == 409 and "取消" in response.text
    state.execution.cancel("ws", state.task.id)
    receipt = enqueue(state, payload)
    grant = state.scheduler.pick_and_grant()
    state.execution.cancel("ws", state.task.id)
    blocked = state.client.post(state.url, json=inputs(state))
    assert blocked.status_code == 409 and "释放" in blocked.text
    state.scheduler.cancel(grant.request_id, state.actor)
    state.executor.execute(grant, state.scheduler)
    state.scheduler.assert_released(grant)
    assert state.client.get(receipt["result_url"]).json()["status"] == "cancelled"
    assert state.repo.get_job_run("ws", old.id).status == "cancelled"
    assert not state.builds


@pytest.mark.parametrize("failure", ["planning", "commit", "budget", "cancel", "CAS", "narration", "material"])
def test_granted_failure_or_cancellation_preserves_adopted_work(state, monkeypatch, failure):
    before = adopted(state)
    receipt = enqueue(state)
    build = state.stage.build_candidate
    def changed(*args, **kwargs):
        if failure == "planning":
            raise ValueError("fixture planner failure")
        candidate = build(*args, **kwargs)
        if failure == "budget":
            candidate.message = "x" * 1_000_001
        elif failure == "cancel":
            state.control.cancel("ws", state.task.id, state.actor, state.runtime, verify_media=False)
        elif failure == "CAS":
            base = state.repo.get_timeline_version("ws", before[0].current_timeline_version_id)
            state.repo.create_timeline_version(replace(base, id="manual-concurrent", parent_version_id=base.id,
                sequence=base.sequence + 1))
        elif failure == "narration":
            segment = state.repo.list_narration_segments("ws", state.task.id, "fixed-script")[0]
            state.repo.save_narration_segments("ws", state.task.id, "fixed-script", [replace(segment, text="concurrent change")])
        elif failure == "material":
            state.source.write_bytes(b"changed physical source")
        return candidate
    monkeypatch.setattr(state.stage, "build_candidate", changed)
    if failure == "commit":
        append = state.repo.execution_records.append_task_event
        def reject(event, **kwargs):
            if event.event_type == "storyboard_regenerated":
                raise ValueError("fixture result checkpoint failure")
            return append(event, **kwargs)
        monkeypatch.setattr(state.repo.execution_records, "append_task_event", reject)
    grant = run(state)
    state.scheduler.assert_released(grant)
    result = state.client.get(receipt["result_url"]).json()
    assert result["status"] == ("cancelled" if failure == "cancel" else "failed") and result["result"] is None
    assert state.repo.get_creation_task_montage_plan("ws", state.task.id) == before[1]
    if failure not in {"CAS", "narration"}:
        assert adopted(state) == before
    assert not state.repo.list_edit_operations("ws", state.task.id)


def test_queued_restart_reuses_original_request_and_commits_once(state, tmp_path):
    receipt = enqueue(state)
    state.scheduler.close()
    state.scheduler = DeviceScheduler(state.repo.database_path, tmp_path / "permit.lock",
        owners=state.owner, resources=Resources(), processes=Processes())
    state.runtime.scheduler = state.scheduler
    state.control.recover(state.runtime)
    state.control.recover(state.runtime)
    grant = run(state)
    state.scheduler.assert_released(grant)
    state.control.recover(state.runtime)
    assert state.scheduler.pick_and_grant() is None
    assert len(state.builds) == len(state.repo.list_edit_operations("ws", state.task.id)) == 1
    assert state.client.get(receipt["result_url"]).json()["status"] == "succeeded"


def test_normal_start_resume_keeps_manual_storyboard_and_does_not_request_replanning(state):
    from app.creation_execution import next_creation_intent
    payload = inputs(state)
    normal = state.execution.start("ws", state.task.id, payload["script_id"], payload["input_digest"], actor=state.actor)
    state.owner.render_facts = lambda *args, **kwargs: ("render:current-manual", {})
    assert next_creation_intent(state.repo, state.scheduler, normal).unit_key == "render:current-manual"
    state.execution.cancel("ws", state.task.id)
    enqueue(state)
    run(state)
    resumed = state.execution.retry("ws", state.task.id, member_id=state.actor.member_id)
    event = state.repo.execution_records.get_task_event("ws", f"event_{resumed.id}_start").payload
    assert "operation" not in event and "request_inputs" not in event
    assert next_creation_intent(state.repo, state.scheduler, resumed).unit_key == "render:current-manual"
    assert len(state.builds) == 1


def test_lost_submission_after_job_registration_is_locatable_and_recovered_once(state, monkeypatch):
    payload = inputs(state)
    submit = state.scheduler.submit
    monkeypatch.setattr(state.scheduler, "submit", lambda *_: (_ for _ in ()).throw(RuntimeError("fixture crash before device submission")))
    with pytest.raises(RuntimeError, match="fixture crash"):
        state.client.post(state.url, json=payload)
    url = state.url + "/requests/" + payload["request_id"]
    receipt = state.client.get(url).json()
    assert receipt["status"] == "queued" and receipt["result"] is None
    assert state.client.post(state.url, json=payload).json() == receipt
    monkeypatch.setattr(state.scheduler, "submit", submit)
    state.control.recover(state.runtime)
    state.control.recover(state.runtime)
    run(state)
    assert state.scheduler.pick_and_grant() is None and len(state.builds) == 1
    assert state.client.get(url).json()["status"] == "succeeded"


@pytest.mark.parametrize("change", ["narration", "material", "identity"])
def test_changed_source_or_permission_before_permit_never_runs_planner(state, change):
    receipt = enqueue(state)
    before = state.repo.get_creation_task_montage_plan("ws", state.task.id)
    from pathlib import Path
    if change == "narration":
        Path(state.narration.audio_path).write_bytes(b"changed input")
    elif change == "material":
        state.source.write_bytes(b"changed physical source")
    else:
        state.owner.verify_identity = lambda actor: "unauthorized"
    assert state.scheduler.pick_and_grant() is None
    assert not state.builds and state.repo.get_creation_task_montage_plan("ws", state.task.id) == before
    assert state.client.get(receipt["result_url"]).json()["result"] is None


def test_real_metadata_planner_builds_candidate_only_after_permit_without_media_or_model(state):
    # Discard only the candidate test double; use the real planner and application.
    state.executor.montage_stage = main._montage_stage_use_case(state.repo)
    before = adopted(state)
    receipt = enqueue(state)
    assert adopted(state) == before
    grant = run(state)
    state.scheduler.assert_released(grant)
    result = state.client.get(receipt["result_url"]).json()
    assert result["status"] == "succeeded", state.repo.get_job_run("ws", receipt["job_id"]).failure_reason
    plan = state.repo.get_creation_task_montage_plan("ws", state.task.id)
    assert plan["clips"] != before[1]["clips"] and plan["duration_seconds"] == 4
    assert plan["graphic_overlays"] == before[1]["graphic_overlays"]
    assert plan["audio_timeline"] == before[1]["audio_timeline"]


def test_concurrent_same_UUID_claims_one_job_and_device_request(state):
    from concurrent.futures import ThreadPoolExecutor
    payload = StoryboardRegenerationRequest.model_validate(inputs(state))
    def submit(_):
        return state.control.regenerate_storyboard("ws", state.task.id, payload, state.actor, state.runtime)
    with ThreadPoolExecutor(max_workers=3) as pool:
        receipts = list(pool.map(submit, range(3)))
    assert receipts[0] == receipts[1] == receipts[2]
    assert len(state.repo.list_creation_job_request_states("ws", receipts[0]["job_id"])) == 1
    run(state)
    assert len(state.builds) == len(state.repo.list_edit_operations("ws", state.task.id)) == 1


def test_original_ready_audio_replans_30_through_existing_storyboard_request(state):
    # This port consumes declared source metadata; physical rendering is covered separately.
    asset = state.repo.get_asset('ws', state.asset.id)
    state.repo._save_asset(replace(asset, duration_seconds=40,
        segments=[replace(asset.segments[0], end_seconds=40)]))
    task = state.repo.get_creation_task('ws', state.task.id)
    plan = state.repo.get_creation_plan('ws', task.plan_id)
    plan.duration_target_seconds = 30
    with state.repo._connect() as conn:
        conn.execute('UPDATE creation_plans SET payload=? WHERE id=?', (json.dumps(asdict(plan), default=str), plan.id))
    before = state.repo.get_creation_task_montage_plan('ws', task.id)
    segments = state.repo.list_narration_segments('ws', task.id, task.confirmed_script_id)
    state.executor.montage_stage = main._montage_stage_use_case(state.repo)
    receipt = enqueue(state)
    run(state)
    completed = state.client.get(receipt['result_url']).json()
    assert completed['status'] == 'succeeded', state.repo.get_job_run('ws', receipt['job_id']).failure_reason
    after = state.repo.get_creation_task_montage_plan('ws', task.id)
    assert after['duration_seconds'] == 30
    assert sum(cue['duration_seconds'] for cue in after['cues']) == 4
    assert after['audio_timeline'] == before['audio_timeline']
    assert after['narration_audio_path'] == before['narration_audio_path']
    assert after['graphic_overlays'] == before['graphic_overlays']
    assert after['clips'][-1]['timeline_end_seconds'] == 30
    assert state.repo.list_narration_segments('ws', task.id, task.confirmed_script_id) == segments


@pytest.mark.parametrize('aggregate', [False, True])
def test_replanned_sparse_voice_preserves_ranges_envelopes_and_translated_captions(aggregate):
    from app.audio_timeline import narration_audio_timeline
    from app.creation.contracts import SubtitleCue, SubtitleTrack
    from app.creation.storyboard_regeneration import move_preserved_voice, move_preserved_subtitles
    from app.montage import CreationTaskMontagePlan, NarrationCue
    a, b = 'narration://artifact/a', 'narration://artifact/b'
    old_graph = narration_audio_timeline(a, 2 if aggregate else 1)
    if not aggregate:
        extra = narration_audio_timeline(b, 1).tracks[0].clips[0]
        old_graph.tracks[0].clips.append(extra.model_copy(update={'id': 'second-voice', 'timeline_start_ms': 1000}))
    old_graph.tracks[0].gain_db = -4
    old_graph.tracks[0].clips[0].fade_in_ms = 100
    cues = [NarrationCue('first', 'one', '第一段', 1), NarrationCue('second', 'two', '第二段', 1)]
    previous = CreationTaskMontagePlan('p', 'ws', 'task', 'script', 'title', 'ready', cues, [], 2, 0,
        narration_audio_path=a, audio_timeline=old_graph)
    new_graph = narration_audio_timeline(a, 1)
    extra = narration_audio_timeline(b, 1).tracks[0].clips[0]
    new_graph.tracks[0].clips.append(extra.model_copy(update={'id': 'new-second', 'timeline_start_ms': 2000}))
    candidate = replace(previous, duration_seconds=4, audio_timeline=new_graph,
        cues=[replace(cues[0], timeline_start_seconds=0), replace(cues[1], timeline_start_seconds=2)])
    if not aggregate:
        locked = old_graph.model_copy(deep=True)
        locked.tracks[0].locked = True
        with pytest.raises(ValueError, match='配音轨已锁定'):
            move_preserved_voice(replace(previous, audio_timeline=locked), candidate)
    moved = move_preserved_voice(previous, candidate)
    assert moved.tracks[0].gain_db == -4
    assert [clip.timeline_start_ms for clip in moved.tracks[0].clips] == [0, 2000]
    assert sum(clip.duration_ms for clip in moved.tracks[0].clips) == 2000
    assert moved.tracks[0].clips[0].fade_in_ms == 100
    assert old_graph.tracks[0].clips[0].timeline_start_ms == 0
    assert moved.tracks[1:] == old_graph.tracks[1:]
    if aggregate:
        assert [(clip.source_ref, clip.source_start_ms, clip.source_end_ms) for clip in moved.tracks[0].clips] == [(a, 0, 1000), (a, 1000, 2000)]
    else:
        assert [(clip.source_ref, clip.source_start_ms, clip.source_end_ms) for clip in moved.tracks[0].clips] == [(a, 0, 1000), (b, 0, 1000)]
    zh = SubtitleTrack('zh', 'ws', 'task', 'base', 'zh-CN', None, {},
        [SubtitleCue('zh-two', 'second', '保留修改后的字稿', 1, 2)], 'timeline', None, None, None, 'valid')
    en = replace(zh, id='en', language='en-US', source_language='zh-CN',
        cues=[SubtitleCue('en-two', 'zh-two', 'Preserved translation', 1, 2)])
    shifted = move_preserved_subtitles(previous, candidate, [zh, en])
    assert [(t.cues[0].start_seconds, t.cues[0].end_seconds) for t in shifted] == [(2, 3), (2, 3)]
    assert [t.cues[0].text for t in shifted] == ['保留修改后的字稿', 'Preserved translation']
    assert zh.cues[0].start_seconds == en.cues[0].start_seconds == 1


@pytest.mark.parametrize('stale_head', [False, True])
def test_completed_storyboard_can_request_only_current_render_without_replaying_old_head(state, stale_head):
    from pathlib import Path
    from app.creation_execution import CpuRenderLimits
    from app.task_audio import TaskAudio
    from test_creation_audio_mix import audio_budget
    plan = state.repo.get_creation_plan('ws', state.task.plan_id)
    state.repo.save_narration_artifact(replace(state.narration, voice_profile_id=plan.selected_voice.profile_id))
    receipt = enqueue(state)
    run(state)
    completed = state.client.get(receipt['result_url']).json()
    assert completed['status'] == 'succeeded'
    job = state.repo.get_job_run('ws', receipt['job_id'])
    start = state.repo.execution_records.get_task_event('ws', f'event_{job.id}_start')
    task = state.repo.get_creation_task('ws', state.task.id)
    base = state.repo.get_timeline_version('ws', task.current_timeline_version_id)
    confirmed = replace(base, id='confirmed-storyboard-head', parent_version_id=base.id, sequence=base.sequence+1,
        created_by_operation_id=None)
    state.repo.create_timeline_version(confirmed)
    audio = state.repo.get_narration_artifact('ws', task.narration_cache_key)
    import hashlib
    audio_sha = hashlib.sha256(Path(audio.audio_path).read_bytes()).hexdigest()
    usage = state.repo.creation_narration_usage('ws', job.id)
    service = TaskAudio(state.repo, state.case.artifact_store,
        state.case.output_root)
    state.owner.cpu_render_limits = CpuRenderLimits(8_000_000, 1, 30)
    state.owner.audio_mix_facts = lambda workspace, target, plan, *, actor: service.mix_facts(workspace,
        target, plan.audio_timeline, plan.duration_seconds, actor=actor, budget=audio_budget(),
        normalize_voice_sources=plan.narration_linkage_created_audio_timeline)
    state.control.render_stage.valid = lambda *args, **kwargs: False
    state.control.resume('ws', task.id, state.actor, state.runtime, verify_media=False)
    requests = state.repo.list_creation_job_request_states('ws', job.id)
    pending = [row for row in requests if row['state'] == 'waiting']
    assert len(pending) == 1 and pending[0]['unit_key'].startswith('render:')
    assert state.repo.get_job_run('ws', job.id).status == 'queued'
    state.control.resume('ws', task.id, state.actor, state.runtime, verify_media=False)
    assert state.repo.list_creation_job_request_states('ws', job.id) == requests
    from app.creation_execution import next_creation_intent
    from app.device_scheduler import SubmissionIntent, SchedulePolicy
    effective = state.repo.creation_execution_input('ws', job.id)
    assert effective['operation'] == 'render_current' and effective['render_unit_key'] == pending[0]['unit_key']
    assert effective['render_input_timeline_version_id'] == confirmed.id
    assert next_creation_intent(state.repo, state.scheduler, job).unit_key == pending[0]['unit_key']
    original_unit = next(row['unit_key'] for row in requests if row['unit_key'].startswith('montage_plan:'))
    with pytest.raises(PermissionError, match='本次明确绑定'):
        state.owner.verify_submission(SubmissionIntent('ws', 'creation_job', job.id, job.input_digest,
            original_unit, SchedulePolicy()), state.actor)
    if stale_head:
        state.repo.create_timeline_version(replace(confirmed, id='changed-after-render-request',
            parent_version_id=confirmed.id, sequence=confirmed.sequence+1))
        with state.repo._connect() as conn:
            assert state.owner.check_current(state.scheduler._get(pending[0]['id']), conn) == 'stale'
        assert state.scheduler.pick_and_grant() is None
        state.scheduler.cancel(pending[0]['id'], state.actor)
    else:
        grant = state.scheduler.pick_and_grant()
        assert grant.request_id == pending[0]['id'] and grant.owner_id == job.id
        state.scheduler.cancel(grant.request_id, state.actor)
    assert state.repo.execution_records.get_task_event('ws', start.id) == start
    assert state.repo.read_storyboard_regeneration_receipt('ws', task.id, receipt['request_id'])[2] == completed['result']
    assert state.repo.creation_narration_usage('ws', job.id) == usage
    assert hashlib.sha256(Path(audio.audio_path).read_bytes()).hexdigest() == audio_sha


def test_montage_stream_preserves_raw_stage_person_and_narration_signature_bytes(tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path
    from app.creation_execution import creation_stage_facts
    import app.creation_execution as execution_module
    from app.creation.storyboard_regeneration import narration_signature
    from app.creation.person_renders import PersonRender
    from app.avatar_lipsync import AvatarAndLipSyncRequest
    from test_montage_stage_application import _montage_stream_case
    case = _montage_stream_case(tmp_path, monkeypatch)
    try:
        rows = case.repo.list_narration_segments('local', case.task.id, case.script.id)
        references = dict.fromkeys([case.repo.narration_artifact_reference(case.record.cache_key),
            *(row.audio_reference for row in rows)])
        files = []
        for reference in references:
            path = case.repo.resolve_authorized_narration_audio_reference('local', reference)
            info = path.stat()
            files.append((reference, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        signature = hashlib.sha256(json.dumps([asdict(case.record), [asdict(row) for row in rows], files],
            sort_keys=True, default=str).encode()).hexdigest()
        person, voice, *_ = register_synthetic_profiles(case.repo, workspace_id='local', member_id=case.actor.member_id)
        request = AvatarAndLipSyncRequest('local', person, voice, case.record.audio_path, rows[0].audio_reference,
            'zh', 'natural', 'neutral', person.authorization, 'fixture')
        render = PersonRender('paid-person', 'local', case.task.id, person.id, person.version, voice.id, voice.version,
            rows[0].id, 'paid-visual', 'fixture', 'natural', 'neutral', 'paid-person-key', 'ready',
            output_path=case.record.audio_path, duration_seconds=2)
        assets = case.repo.list_assets('local')
        expected = json.dumps({'assets': [asdict(asset) for asset in assets], 'narration': asdict(case.record),
            'script': asdict(case.script)}, sort_keys=True, default=str).encode()
        base_key = 'montage_plan:' + hashlib.sha256(expected).hexdigest()
        for name in ('get_creation_task_script', '_creation_task_script_from_payload',
                     'effective_creation_script', 'list_narration_segments'):
            monkeypatch.setattr(case.repo, name, case.forbidden)
        with case.repo.creation_execution_read() as conn:
            snapshot = case.repo.read_creation_execution_snapshot('local', case.task.id, connection=conn)
            key, resources = creation_stage_facts(case.repo, snapshot.task, snapshot.script_header, snapshot.plan,
                'montage_plan', None, case.owner.montage_budget, connection=conn)
            assert key == base_key and resources['input_bytes'] == len(expected)
            assert narration_signature(case.repo, snapshot.task, connection=conn) == signature
            # Encoding oracle for already-qualified paid person facts; no avatar provider runs.
            monkeypatch.setattr(execution_module, 'iter_presenter_inputs', lambda *args, **kwargs: iter([(rows[0], request)]))
            monkeypatch.setattr(execution_module, 'current_presenter_render', lambda *args, **kwargs: render)
            case.owner.resolve_presenter_output = lambda _: Path(case.record.audio_path)
            info = Path(case.record.audio_path).stat()
            extra = [[asdict(render), asdict(request), [info.st_size, info.st_mtime_ns, info.st_ctime_ns]]]
            expected_person = 'montage_plan:' + hashlib.sha256(json.dumps([base_key, extra], sort_keys=True, default=str).encode()).hexdigest()
            actual, unchanged = case.owner.stage_facts(snapshot.task, snapshot.script_header,
                replace(snapshot.plan, presenter_share_target=40), 'montage_plan', connection=conn)
            assert actual == expected_person and unchanged == resources and conn.in_transaction
    finally:
        case.scheduler.close()


@pytest.mark.parametrize('outcome', ['normal', 'unknown_commit', 'tail', 'source', 'revoked', 'head'])
def test_whole_montage_stream_real_regeneration_owner_and_original_receipt(tmp_path, monkeypatch, outcome):
    from app.creation.contracts import TimelineVersion
    from app.creation.storyboard_regeneration import StoryboardRegeneration
    from test_montage_stage_application import _montage_stream_case
    case = _montage_stream_case(tmp_path, monkeypatch)
    repo = case.repo
    try:
        case.control.start('local', case.task.id, case.script.id, case.digest, case.actor, case.runtime, verify_media=False)
        first = case.scheduler.pick_and_grant(); assert first is not None
        case.executor.execute(first, case.scheduler)
        case.control.cancel('local', case.task.id, case.actor, case.runtime, verify_media=False)
        task = repo.get_creation_task('local', case.task.id)
        base = repo.get_timeline_version('local', task.current_timeline_version_id)
        before = repo.get_creation_task_montage_plan('local', task.id)
        payload = StoryboardRegenerationRequest.model_validate({'request_id': str(uuid4()),
            'script_id': case.script.id, 'input_digest': case.digest,
            'expected_timeline_version_id': base.id, 'expected_timeline_sequence': base.sequence})
        for name in ('get_creation_task_script', '_creation_task_script_from_payload',
                     'effective_creation_script', 'list_narration_segments'):
            monkeypatch.setattr(repo, name, case.forbidden)
        monkeypatch.setattr('app.granted_subprocess.run_granted_command', case.forbidden)
        observed = []
        original_build = case.stage.build_candidate
        def build(*args, **kwargs):
            observed.append(True)
            return original_build(*args, **kwargs)
        monkeypatch.setattr(case.stage, 'build_candidate', build)
        receipt = case.control.regenerate_storyboard('local', task.id, payload, case.actor, case.runtime)
        grant = case.scheduler.pick_and_grant(); assert grant is not None
        changed = []
        if outcome in {'tail', 'source', 'revoked', 'head'}:
            released = case.scheduler._released_for_commit
            def inject(conn, current):
                result = released(conn, current)
                if not changed and current.request_id == grant.request_id:
                    changed.append(True)
                    if outcome == 'tail':
                        conn.execute("UPDATE creation_task_script_sections SET section_json=json_set(section_json,'$.content','changed tail') WHERE script_id=? AND section_id='second'", (case.script.id,))
                    elif outcome == 'source':
                        from pathlib import Path
                        path = Path(case.record.audio_path); path.write_bytes(path.read_bytes()+b'changed')
                    elif outcome == 'revoked':
                        monkeypatch.setattr(case.owner, 'verify_identity', lambda _: 'unauthorized')
                    else:
                        repo.create_timeline_version(TimelineVersion('later-storyboard-head', 'local', task.id,
                            base.id, base.sequence+1, base.snapshot, None), connection=conn)
                return result
            monkeypatch.setattr(case.scheduler, '_released_for_commit', inject)
        elif outcome == 'unknown_commit':
            complete = case.scheduler.complete_unit
            def lost(*args, **kwargs):
                result = complete(*args, **kwargs)
                raise KeyboardInterrupt('transport exits after original complete transaction')
            monkeypatch.setattr(case.scheduler, 'complete_unit', lost)
        if outcome == 'unknown_commit':
            with pytest.raises(KeyboardInterrupt):
                case.executor.execute(grant, case.scheduler)
        else:
            case.executor.execute(grant, case.scheduler)
        assert len(observed) == 1
        current = repo.get_creation_task('local', task.id)
        if outcome in {'normal', 'unknown_commit'}:
            assert current.current_timeline_version_id != base.id, repo.get_job_run('local', receipt['job_id']).failure_reason
            result = case.control.regenerate_storyboard('local', task.id, payload, case.actor, case.runtime)
            assert result['job_id'] == receipt['job_id'] and result['status'] == 'succeeded'
            assert case.scheduler._get(grant.request_id).state == 'done'
            assert len(repo.list_edit_operations('local', task.id)) == 1
            after = repo.get_creation_task_montage_plan('local', task.id)
            assert after['audio_timeline'] == before['audio_timeline']
            assert after['graphic_overlays'] == before['graphic_overlays']
            assert repo.get_timeline_version('local', current.current_timeline_version_id).snapshot['narration_segments'] == base.snapshot['narration_segments']
            assert len(observed) == 1
        else:
            assert changed
            assert current.current_timeline_version_id == base.id
            assert repo.get_creation_task_montage_plan('local', task.id) == before
            assert not repo.list_edit_operations('local', task.id)
            assert not [e for e in repo.execution_records.list_task_events('local', task.id)
                if e.event_type == 'storyboard_regenerated']
    finally:
        case.scheduler.close()
