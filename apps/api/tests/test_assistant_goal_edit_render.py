"""Committed edit/render metadata with real SQLite; no model or media worker."""
from script_seed_fixtures import script_seed_payload
import json
from dataclasses import asdict, replace
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi.encoders import jsonable_encoder
from starlette.requests import Request

import app.main as main
from app.task_observation_routes import TaskObservationResponseAdapter
from app.assistant_goal_continuation import GoalContinuationService, GoalResultProjection
from app.conversation_repository import ConversationRepository
from app.creation.contracts import JobRun, TaskEvent
from app.creation_execution import CreationExecutionActor
from test_typed_assistant_edits_api import metadata_http_state as metadata_http_state  # noqa: F401


@pytest.fixture(autouse=True)
def goal_readiness_no_execution(monkeypatch):
    from test_production_foundation import edit_adoption_no_execution
    edit_adoption_no_execution(monkeypatch)


@pytest.mark.parametrize("fast_render", [False, True])
def test_same_edit_waits_for_own_render_then_rebases_only_derived_selection(metadata_http_state, tmp_path, monkeypatch, fast_render):
    state = metadata_http_state
    from app.creation.contracts import ArtifactRecord
    # The editorial fixture declares an already-produced narration. Give that
    # opaque input its real metadata provenance; never decode or generate WAV.
    source_job = state.repo.job_records.create_job_run(JobRun("declared-existing-audio", "ws", state.task.id,
        "declared_fixture_source", "declared-existing-audio", "a"*64))
    state.repo.job_records.claim_job_run("ws", source_job.id)
    blob = state.store.publish_bytes("artifacts/task/fixed-narration.wav", b"opaque prior narration input; not audio execution evidence")
    state.repo.execution_records.create_artifact_record(ArtifactRecord("declared-existing-audio", "ws", state.task.id,
        "audio_source", blob.logical_ref, blob.sha256, blob.size_bytes, source_job.id,
        [state.repo.workspace_media_reference_for_asset("ws", state.asset.id)]))
    state.repo.job_records.finish_job_run("ws", source_job.id, status="succeeded", output_refs=[blob.logical_ref])
    from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
    script = CreationTaskScript("fixed-script", "ws", state.task.id, "fixed-run", "fixed-topic", "已确认文案", "zh-CN",
        [CreationTaskScriptSection("section", "开头", "原字幕", "既有画面", ["fixture-source"])], selection_status="confirmed")
    with state.repo._connect() as conn:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO creation_task_scripts(id,workspace_id,task_id,run_id,payload,updated_at) VALUES(?,?,?,?,?,?)",
            (script.id,"ws",state.task.id,script.run_id,script_seed_payload(state.repo, conn, script),script.updated_at.isoformat()))
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id=?", (json.dumps(asdict(replace(state.task,confirmed_script_id=script.id)),default=str),state.task.id))
    # TaskBusiness's real borrowed-connection guard reads the same isolated database.
    repo = ConversationRepository(state.repo.database_path)
    monkeypatch.setattr(main, "settings", replace(main.settings, database_path=str(state.repo.database_path)))
    monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", "ws")
    monkeypatch.setattr(main, "auth_context", SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, "device_runtime", None)
    monkeypatch.setattr(main, "conversation_repo", repo)
    request = Request({"type": "http", "headers": [(b"x-local-member-token", state.token.encode())]})
    monkeypatch.setattr(main, "_require_permit_actor", lambda *_: CreationExecutionActor("ws", state.member.member_id))
    monkeypatch.setattr(main, "_goal_readiness_completed_sources", lambda *_: [])
    monkeypatch.setattr(TaskObservationResponseAdapter, "metadata", lambda *_: {})
    monkeypatch.setattr(main.GoalReadinessUseCase, "output_checkpoint", lambda *_: None)
    chat = repo.create("ws", state.member.member_id, "home", "home")
    selected = {"kind": "selection", "items": [{"kind": "subtitle", "task_id": state.task.id, "object_id": "cue", "version": state.base.id, "label": "字幕1"}]}
    chat = repo.save_reference(chat["id"], "ws", state.member.member_id, selected, 0)
    chat = repo.save_draft(chat["id"], "ws", state.member.member_id, "修改字幕并更新成片", chat["draft_version"])
    context = {"kind": "task", "task_id": state.task.id, "stage": "subtitle_cover", "selected_reference": selected,
        "selected_object_refs": ["cue:cue", "subtitle-track:"+state.track.id], "expected_timeline_version_id": state.base.id,
        "expected_timeline_sequence": state.base.sequence, "target_snapshot": {"timeline_version_id": state.base.id,
        "timeline_sequence": state.base.sequence, "selected_references": selected["items"]}}
    turn, _ = repo.create_turn_idempotent(chat["id"], "ws", state.member.member_id, chat["draft_text"], str(uuid4()), chat["draft_version"], context,
        expected_reference_version=chat["reference_version"])
    request_id = str(uuid4())
    changed = state.client.post(state.url+"/edits/exact", json={"operation_type": "UpdateSubtitleCue", "cue_id": "cue", "text": "已更新",
        "assistant_turn_id": turn["id"], "request_id": request_id, "expected_timeline_version_id": state.base.id, "expected_timeline_sequence": state.base.sequence})
    assert changed.status_code == 200, changed.text
    operation = changed.json()["operation"]
    edit_version = state.repo.timeline_records.get_timeline_version("ws", operation["result_version_id"])
    job = state.repo.job_records.create_job_run(JobRun("render", "ws", state.task.id, "creation_task_execution", "render", "declared-input"))
    state.repo.job_records.claim_job_run("ws", job.id)
    source = {"workspace_id": "ws", "task_id": state.task.id, "member_id": state.member.member_id,
        "conversation_id": chat["id"], "assistant_turn_id": turn["id"], "expected_timeline_version_id": edit_version.id,
        "expected_timeline_sequence": edit_version.sequence}
    state.repo.execution_records.append_task_event(TaskEvent("located-render", "ws", state.task.id, job.id, "assistant_execution_located", {"source": source, "job_id": job.id}))
    def actual(*_):
        return jsonable_encoder({"task_id": state.task.id, "job": asdict(state.repo.job_records.get_job_run("ws", job.id)), "video_ready": state.repo.job_records.get_job_run("ws", job.id).status == "succeeded"})
    # Keep real TaskBusiness provenance checks; declare only the execution read result.
    monkeypatch.setattr(main.CreationExecutionControl, "read_bound", lambda *_args, **_kwargs: actual())
    # The render-publication fact is isolated; no worker, model or decoding runs.
    monkeypatch.setattr(state.repo, "goal_render_request_row", lambda *_: {"owner_id": job.id, "state": "done"})
    def finish_render():
        rendered_montage = state.repo.get_creation_task_montage_plan("ws", state.task.id)
        rendered_montage.update(status="rendered", output_path=str(tmp_path/"declared-output.mp4"), subtitle_path=state.track.artifact_ref)
        state.repo.montage_plan_adoption.save_creation_task_montage_plan("ws", state.task.id, rendered_montage["id"], rendered_montage)
        snapshot = {key: value for key, value in edit_version.snapshot.items() if key != "subtitle_track_ids"}
        state.repo.timeline_records.create_timeline_version(replace(edit_version, id="render-version", parent_version_id=edit_version.id, sequence=edit_version.sequence+1,
            snapshot={**snapshot, "montage": rendered_montage, "transform_run_id": "declared-render", "creation_render_request_id": "published-render", "render_input_timeline_version_id": edit_version.id}))
        state.repo.job_records.finish_job_run("ws", job.id, status="succeeded")
    repo.record_task_execution(turn['id'], 'ws', state.member.member_id, state.task.id, actual())
    if fast_render:
        finish_render()
    from app.assistant.goal_versions import observation_turn_origin
    running_task = state.repo.task_records.get_creation_task('ws', state.task.id)
    assert observation_turn_origin(state.repo, repo, running_task, state.member.member_id, turn['id'],
        state.repo.timeline_records.get_timeline_version('ws', running_task.current_timeline_version_id))['conversation_id'] == chat['id']
    repo.finish_turn(turn["id"], "completed", "修改已保存；尚未重新生成视频。", {"kind": "task_turn", "response": {
        "reply": "修改已保存；尚未重新生成视频。", "execution_status": actual(), "edit_request": {"request_id": request_id, "status": "recorded"}},
        "goal_business_actions": [{"kind": "execution_status", "id": job.id}]})
    repo.register_task_goal(turn["id"])
    owner = GoalResultProjection(state.repo, repo)
    from app.assistant.goal_versions import effective_goal_version
    owned_task = state.repo.task_records.get_creation_task('ws', state.task.id)
    frozen = context['target_snapshot']
    expected = {key: frozen[key] for key in ('timeline_version_id', 'timeline_sequence')}
    effective = effective_goal_version(state.repo, repo, repo.get_task_goal(turn['id']), expected, owned_task)
    assert effective['target_snapshot']['timeline_version_id'] == owned_task.current_timeline_version_id
    assert repo.get_task_goal(turn['id'])['request_context'] == context
    from app.assistant.task_business import TaskBusiness
    business = object.__new__(TaskBusiness)
    business.production, business.conversations = state.repo, repo
    business.workspace_facts = main._task_workspace_response_adapter().response
    workspace = business.execute('read_task_workspace', {'expected_task_version': expected},
        CreationExecutionActor('ws', state.member.member_id), owned_task, repo.get_task_goal(turn['id']), {}, {}, None)
    assert workspace['owned_version_transition']['from'] == expected
    assert workspace['owned_version_transition']['to']['timeline_version_id'] == owned_task.current_timeline_version_id
    op = state.repo.edit_records.get_edit_operation("ws", operation["id"])
    proposal = state.repo.edit_records.get_edit_proposal("ws", operation["proposal_id"])
    typed_source = state.repo.edit_records.get_edit_intent("ws", proposal.intent_id).constraints["request_source"]
    current_task = state.repo.task_records.get_creation_task("ws", state.task.id)
    from app.task_audio import media_digest
    observed_version = state.repo.timeline_records.get_timeline_version('ws', current_task.current_timeline_version_id)
    observed_source = {**typed_source, 'expected_timeline_version_id': observed_version.id,
        'expected_timeline_sequence': observed_version.sequence}
    observed_start = {'operation': 'output_observation', 'result_kind': 'observation',
        'member_id': state.member.member_id, 'arguments': {'_typed_source': observed_source}}
    observed_job = state.repo.job_records.create_job_run(JobRun('declared-observation', 'ws', state.task.id,
        main.TASK_MEDIA_KIND, 'declared-observation', media_digest(observed_start)))
    state.repo.job_records.claim_job_run('ws', observed_job.id)
    state.repo.job_records.finish_job_run('ws', observed_job.id, status='failed', failure_reason='metadata-only fixture',
        impact='No media was executed', repair_action='Use the real observation worker for media acceptance')
    state.repo.execution_records.append_task_event(TaskEvent(f'event_{observed_job.id}_start', 'ws', state.task.id,
        observed_job.id, 'task_media_submitted', {**observed_start, 'input_digest': observed_job.input_digest}))
    observed_candidate = {'kind': 'job', 'id': observed_job.id, 'workspace_id': 'ws', 'task_id': state.task.id,
        'source': observed_source, 'status': 'failed', 'event_id': 'declared-observation-result',
        'created_at': observed_job.created_at.isoformat()}
    observed_context = owner.business_candidate(observed_candidate, media_available=True)
    assert observed_context.job.id == observed_job.id  # Own advanced observations reach result projection.
    projection = owner.business_projection(SimpleNamespace(
        candidate={"kind": "proposal", "event_id": "proposal-applied", "workspace_id": "ws", "task_id": state.task.id, "status": "confirmed"},
        source=typed_source, binding={"status": "completed"}, task=current_task, proposal=proposal, job=None, start=None), media=None, outputs=None)
    edit = projection.event
    assert edit["status"] == "confirmed" and edit["operation_id"] == op.id
    repo.project_business_result(turn["id"], "ws", state.member.member_id, edit, response_patch=projection.response_patch, message=projection.message)
    request_projection = owner.request_projection(SimpleNamespace(
        source=typed_source, binding={"status": "completed"}, task=current_task,
        candidate={"event_id": "request-applied", "task_id": state.task.id}),
        SimpleNamespace(applied=op, locator={"request_id": request_id}), {})
    duplicate = request_projection.event
    assert duplicate["status"] == "confirmed"
    repo.project_business_result(turn["id"], "ws", state.member.member_id, duplicate,
        response_patch=request_projection.response_patch, message=request_projection.message)
    assert len([e for e in repo.get_turn(turn["id"])["events"] if e.get("phase") == "apply"]) == 1
    children = []
    def continue_once(claim, fresh):
        ctx = claim["request_context"]
        assert ctx["expected_timeline_version_id"] == "render-version"
        assert ctx["target_snapshot"]["selected_references"][0]["version"] == "render-version"
        assert "subtitle-track:"+state.track.id not in ctx["selected_object_refs"]
        task = state.repo.task_records.get_creation_task("ws", state.task.id)
        payload = main.CreationTaskAssistantTurnRequest(conversation_id=chat["id"], message="回读真实结果", client_request_id=uuid4(), expected_draft_version=0,
            context_stage=ctx["stage"], selected_object_refs=ctx["selected_object_refs"])
        main._assistant_turn_scope("ws", task, payload)
        children.append(claim["turn_id"])
        repo.finish_turn(claim["turn_id"], "completed", "已回读", {"response": {}})
    authorizations = main._task_authorizations_for_goal()
    current = state.repo.task_records.get_creation_task("ws", state.task.id)
    authorizations.ensure(current, CreationExecutionActor("ws", state.member.member_id), interactive=True)
    assert authorizations.status("ws", state.task.id) == "valid"
    main._bind_goal_business_scope(request, repo.get_task_goal(turn["id"]))
    service = GoalContinuationService(repo, verify_current_goal=main._goal_readiness_use_case().verify, run_goal_turn=continue_once)
    service.reconcile()
    assert children == [] and repo.get_task_goal(turn["id"])["target_snapshot"]["timeline_version_id"] == edit_version.id
    if not fast_render:
        finish_render()
    # A persisted duplicate from an older candidate also cannot invalidate the
    # cursor or start another round after the operation has been consumed.
    events = repo.get_turn(turn["id"])["events"]
    with repo._connect() as conn:
        conn.execute("UPDATE assistant_conversation_turns SET events_json=? WHERE id=?",
            (json.dumps([*events, {**duplicate, "event_id": "legacy-duplicate", "status": "stale"}]), turn["id"]))
    terminal = {"type": "business_result", "event_id": "render-finished", "task_id": state.task.id, "phase": "execution", "job_id": job.id,
        "base_version_id": edit_version.id, "result_version_id": "render-version", "status": "succeeded", "video_ready": True}
    repo.project_business_result(turn["id"], "ws", state.member.member_id, terminal, response_patch={"execution_status": actual()}, message="制作完成")
    assert "尚未" not in repo.get_turn(turn["id"])["assistant_text"]
    assert service.reconcile() == 1 and len(children) == 1, repo.get_task_goal(turn["id"])["waiting_reason"]
    assert service.reconcile() == 0
    settled = repo.get_task_goal(turn["id"])
    assert settled["status"] == "production_completed" and settled["waiting_reason"] == "output_not_checked"
    assert settled["production_result"] == {"scope": "production", "status": "succeeded", "job_id": job.id,
        "timeline_version_id": "render-version", "video_ready": True}
    assert "completed_output_checkpoint" not in settled
    assert ConversationRepository(repo.database_path).get_task_goal(turn["id"])["production_result"] == settled["production_result"]
    stored = repo.get_task_goal(turn["id"])["request_context"]
    assert stored == context, "the original user snapshot must stay immutable"
    assert repo.get(chat["id"], "ws", state.member.member_id)["selected_reference"] == selected
    foreign = replace(state.repo.timeline_records.get_timeline_version("ws", "render-version"), id="unrelated", parent_version_id="render-version", sequence=edit_version.sequence+2, snapshot={})
    state.repo.timeline_records.create_timeline_version(foreign)
    owner = GoalResultProjection(state.repo, repo)
    assert owner.selection_version_chain(repo.get_task_goal(turn["id"]), foreign) is None
    from app.assistant.task_business_errors import TaskBusinessRejected
    with pytest.raises(TaskBusinessRejected):
        effective_goal_version(state.repo, repo, repo.get_task_goal(turn['id']), expected,
            state.repo.task_records.get_creation_task('ws', state.task.id))
    with pytest.raises(ValueError, match='当前有效助手轮次'):
        observation_turn_origin(state.repo, repo, state.repo.task_records.get_creation_task('ws', state.task.id),
            state.member.member_id, turn['id'], foreign)
