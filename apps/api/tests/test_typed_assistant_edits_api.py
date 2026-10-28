"""Typed edits through isolated domain/API state; no live model or service."""
import json
from dataclasses import replace
from uuid import uuid4

import pytest

import app.main as main
from app.conversation_repository import ConversationRepository
from app.schemas import CreationTaskExactEditRequest
from test_material_edit_qualification import material_edit_state as material_edit_state  # noqa: F401
from test_montage_pipeline import person_render_edit_state as person_render_edit_state, _granted_domain_preview_adapter  # noqa: F401
from test_linked_timeline_reorder import _build_linked_state


@pytest.fixture(autouse=True)
def typed_settings(request, monkeypatch):
    if "material_edit_state" in request.fixturenames:
        material_edit_state = request.getfixturevalue("material_edit_state")
        monkeypatch.setattr(main, "settings", replace(main.settings,
            video_inbox_path=str(material_edit_state.production.video_library_path)))


def bound(state, **action):
    task = state.production.get_creation_task(state.workspace_id, state.task.id)
    version = state.production.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    return {"request_id": str(uuid4()), "expected_timeline_version_id": version.id,
            "expected_timeline_sequence": version.sequence, **action}


def replace_action(state):
    return {"operation_type": "ReplaceClip", "clip_id": state.clip.id,
            "asset_id": state.asset.id, "segment_id": state.asset.segments[0].id}


@pytest.fixture
def metadata_http_state(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_metadata_edit_use_case import metadata_state
    state = metadata_state(tmp_path)
    monkeypatch.setattr(main, "production_repo", state.repo)
    monkeypatch.setattr(main, "artifact_store", state.store)
    monkeypatch.setattr(main, "local_members_service", state.members)
    monkeypatch.setattr(main, "current_workspace_id", lambda request: request.headers.get("x-test-workspace", "ws"))
    monkeypatch.setattr(main, "settings", replace(main.settings,
        video_inbox_path=str(state.repo.video_library_path), video_output_path=str(tmp_path / "outputs")))
    state.client = TestClient(main.app, headers={"X-Local-Member-Token": state.token})
    state.url = f"/api/creation-tasks/{state.task.id}"
    return state


def metadata_payload(state):
    return {"operation_type": "ChangeCrop", "clip_id": "clip", "fit_mode": "fit", "crop_x": .3, "crop_y": .7,
        "request_id": str(uuid4()), "expected_timeline_version_id": state.base.id, "expected_timeline_sequence": 1}


def test_execution_capability_read_is_authorized_and_does_not_start_runtime(metadata_http_state, monkeypatch):
    state = metadata_http_state
    monkeypatch.setattr(main, "device_runtime", None)

    def forbidden(*args, **kwargs):
        pytest.fail("capability read must not start or assemble execution")

    monkeypatch.setattr(main, "_creation_runtime", forbidden)
    monkeypatch.setattr(main, "_build_device_components", forbidden)
    monkeypatch.setattr(main, "_start_device_runtime", forbidden)
    response = state.client.get(state.url + "/assistant/execution-capabilities")
    assert response.status_code == 200, response.text
    facts = response.json()
    assert facts["admission"] == "not_evaluated"
    assert facts["whole_task_support"] == "not_inferred"
    assert all(not unit["configured"] for unit in facts["capabilities"])
    assert state.client.get(state.url + "/assistant/execution-capabilities",
                            headers={"x-test-workspace": "outside"}).status_code == 404
    assert state.client.get("/api/creation-tasks/missing/assistant/execution-capabilities").status_code == 404


def test_script_section_http_reads_complete_versioned_windows_without_full_hydration(metadata_http_state, monkeypatch):
    from app.creation.script_windows import write_script_sections
    state = metadata_http_state
    version = '2026-10-24T00:00:00+00:00'
    script_id = 'script-window-http'
    sections = [{'id': f'section-{i}', 'title': str(i), 'content': f'content {i}',
                 'visual_prompt': 'visual', 'evidence_reference_ids': []} for i in range(1, 46)]
    header = {'id': script_id, 'workspace_id': state.task.workspace_id, 'task_id': state.task.id,
              'run_id': '', 'topic_id': '', 'title': 'Window read', 'language': 'en-US',
              'selection_status': 'draft', 'created_at': version, 'updated_at': version,
              'confirmed_at': None, '_sections_storage': 'rows'}
    with state.repo._connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('INSERT INTO creation_task_scripts '
            '(id,workspace_id,task_id,run_id,payload,updated_at) VALUES (?,?,?,?,?,?)',
            (script_id, state.task.workspace_id, state.task.id, '', json.dumps(header), version))
        write_script_sections(connection, state.task.workspace_id, script_id, sections)

    def forbidden(*args, **kwargs):
        pytest.fail('section reads must not hydrate the full script or start execution')

    monkeypatch.setattr(state.repo, 'get_creation_task_script', forbidden)
    monkeypatch.setattr(state.repo, 'list_creation_task_scripts', forbidden)
    monkeypatch.setattr(main, '_start_device_runtime', forbidden)
    for read_url in (state.url, state.url + '/scripts'):
        initial = state.client.get(read_url)
        assert initial.status_code == 200, initial.text
        assert initial.json()['scripts_scope'] == 'active'
        summary = initial.json()['scripts'][0]
        assert summary['sections'] is None and summary['sections_loaded'] is False
        assert summary['sections_total_count'] == 45
    from types import SimpleNamespace
    plan = state.repo.get_creation_plan(state.task.workspace_id, state.task.plan_id)
    monkeypatch.setattr(main.ScriptContextUseCase, 'read_inputs', lambda *_:
        (state.task, plan, SimpleNamespace(title='Topic', angle='', audience='', outline=[]), [], []))
    context = main._script_context_use_case().read_context(state.task.workspace_id, state.task.id, state.member.member_id, local_diagnostic=False)
    assert context['draft']['sections'] is None and context['draft']['sections_loaded'] is False
    assert context['draft']['sections_total_count'] == 45
    url = state.url + f'/scripts/{script_id}/sections'
    received, cursor = [], 0
    while cursor is not None:
        response = state.client.get(url, params={'after_ordinal': cursor, 'expected_updated_at': version})
        assert response.status_code == 200, response.text
        page = response.json()
        assert page['total_count'] == 45 and page['updated_at'] == version
        received.extend(item['section'] for item in page['items'])
        cursor = page['next_after_ordinal']
    assert received == sections
    assert state.client.get(url, params={'after_ordinal': 20}).status_code == 422
    assert state.client.get(url, params={'limit': 21}).status_code == 422
    assert state.client.get(url, params={'expected_updated_at': 'invalid'}).status_code == 422
    with state.repo._connect() as connection:
        connection.execute('UPDATE creation_task_scripts SET updated_at=? WHERE id=?',
                           ('2026-10-24T00:00:01+00:00', script_id))
    assert state.client.get(url, params={'after_ordinal': 20, 'expected_updated_at': version}).status_code == 409
    assert state.client.get(url, headers={'x-test-workspace': 'outside'}).status_code == 404
    assert state.client.get(state.url + '/scripts/missing/sections').status_code == 404
    with state.repo._connect() as connection:
        connection.execute('UPDATE creation_task_scripts SET task_id=? WHERE id=?', ('other-task', script_id))
    assert state.client.get(url).status_code == 404


def test_task_budget_http_preserves_scope_meaning_and_owner_boundary(metadata_http_state, monkeypatch):
    from app.creation.contracts import JobRun
    from app.creation_execution import CreationExecutionActor
    state = metadata_http_state
    # This fixture uses "ws" as its isolated local workspace; retain real roster/token verification.
    monkeypatch.setattr(main, 'LEGACY_WORKSPACE_ID', 'ws')
    actor = CreationExecutionActor('ws', state.member.member_id)

    def forbidden(*args, **kwargs):
        pytest.fail('budget reads must not start execution')

    monkeypatch.setattr(main, '_creation_runtime', forbidden)
    monkeypatch.setattr(main, '_start_device_runtime', forbidden)
    url = state.url + '/budget'
    empty = state.client.get(url)
    assert empty.status_code == 200, empty.text
    assert empty.json()['scopes'] == []
    assert empty.json()['authorization_coverage'] == 'scope_unbound'
    assert empty.json()['status'] == 'authorization_scope_unbound'
    job = state.repo.create_job_run(JobRun(id='job-http-budget-scope', workspace_id='ws',
        task_id=state.task.id, kind='creation_task_execution', idempotency_key='http-budget-scope',
        input_digest='1' * 64))
    command = dict(scope_id='http-goal', expected_revision=-1, request_id='original-http-test',
        source_ref='isolated-test:original', target_task_ids=[state.task.id],
        limits={'execution_seconds': None, 'generated_bytes': None}, policy_kind='local_goal',
        authorization_guard=lambda connection: None)
    state.repo.authorize_execution_budget_scope('ws', state.task.id, actor, **command)
    state.repo.bind_execution_budget_scope('ws', job.id, actor, scope_id='http-goal', expected_revision=0)
    response = state.client.get(url)
    assert response.status_code == 200, response.text
    facts = response.json()
    assert facts['authorization_coverage'] == 'tts_pcm_only'
    assert facts['scopes'][0]['remaining'] == {'execution_seconds': None, 'generated_bytes': None}
    assert facts['scopes'][0]['usage']['reserved_bytes'] == 0
    assert facts['actual_cost']['amount'] is None
    state.repo.authorize_execution_budget_scope('ws', state.task.id, actor,
        **{**command, 'request_id': 'revoke-http-test', 'expected_revision': 0, 'revoked': True})
    revoked = state.client.get(url)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()['scopes'][0]['status'] == 'revoked'
    assert state.client.get(url, headers={'x-test-workspace': 'outside'}).status_code == 403
    assert state.client.get('/api/creation-tasks/missing/budget').status_code == 404
    from dataclasses import asdict
    other_owner = state.repo.get_creation_task('ws', state.task.id)
    other_owner.owner_user_id = 'different-owner'
    with state.repo._connect() as connection:
        connection.execute('UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?',
                           (json.dumps(asdict(other_owner), default=str), 'ws', state.task.id))
    assert state.client.get(url).status_code == 403


def test_script_response_distinguishes_unloaded_body_from_empty_or_partial_script():
    from pydantic import ValidationError
    from app.schemas import CreationTaskScriptResponse
    metadata = dict(id='script', task_id='task', run_id='', topic_id='', title='Draft', language='en-US',
        selection_status='draft', created_at='2026-10-24T00:00:00Z',
        updated_at='2026-10-24T00:00:00Z', confirmed_at=None)
    header = CreationTaskScriptResponse.model_validate({**metadata, 'sections': None,
        'sections_loaded': False, 'sections_total_count': 25})
    assert header.sections is None and header.sections_total_count == 25
    empty = CreationTaskScriptResponse.model_validate({**metadata, 'sections': []})
    assert empty.sections_loaded and empty.sections_total_count == 0
    for fields in ({'sections': None}, {'sections': None, 'sections_loaded': False},
                   {'sections': [], 'sections_loaded': False, 'sections_total_count': 25},
                   {'sections': [], 'sections_total_count': 25}):
        with pytest.raises(ValidationError):
            CreationTaskScriptResponse.model_validate({**metadata, **fields})


def test_metadata_http_save_replay_input_conflict_and_lost_receipt(metadata_http_state, monkeypatch):
    state = metadata_http_state
    wakes = []
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: wakes.append('notified'))
    payload = metadata_payload(state)
    response = state.client.post(state.url + "/edits/exact", json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert len(wakes) == 1
    assert state.client.post(state.url + "/edits/exact", json=payload).json() == saved
    assert len(wakes) == 1
    assert state.client.post(state.url + "/edits/exact", json={**payload, "crop_x": .9}).status_code == 409
    with state.repo._connect() as conn:
        conn.execute("DELETE FROM task_events WHERE event_type='edit_request_result'")
    recovered = state.client.post(state.url + "/edits/exact", json=payload)
    assert recovered.status_code == 200, recovered.text
    assert len(wakes) == 2
    assert recovered.json()["operation"] == saved["operation"]
    assert state.repo.get_creation_task('ws', state.task.id).narration_cache_key == state.narration.cache_key
    assert state.repo.get_timeline_version('ws', state.base.id).snapshot == state.base.snapshot
    assert len(state.repo.list_edit_operations('ws', state.task.id)) == 1


def test_minimal_graphic_http_save_replay_and_undo_preserve_existing_metadata(tmp_path, monkeypatch):
    from test_timeline_restore_application import restore_http_state
    state = restore_http_state(tmp_path, monkeypatch)
    url = state.base_url
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: None)
    cropped = state.client.post(url + '/edits/exact', json=bound(state,
        operation_type='ChangeCrop', clip_id='clip', fit_mode='fit', crop_x=.3, crop_y=.7))
    assert cropped.status_code == 200, cropped.text
    before = state.repo.get_creation_task('ws', state.task.id)
    base = state.repo.get_timeline_version('ws', before.current_timeline_version_id)
    original = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    payload = {'operation_type': 'EditGraphicOverlay', 'overlay_action': 'add',
        'graphic_overlay': {'kind': 'text', 'text': '公开素材演示', 'start_seconds': 0, 'end_seconds': 3},
        'request_id': str(uuid4()), 'expected_timeline_version_id': base.id,
        'expected_timeline_sequence': base.sequence}
    response = state.client.post(url + '/edits/exact', json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    task = state.repo.get_creation_task('ws', state.task.id)
    version = state.repo.get_timeline_version('ws', task.current_timeline_version_id)
    montage = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    layer, = montage['graphic_overlays']
    assert layer['id'].startswith('overlay_') and layer['text'] == '公开素材演示'
    assert layer['x'] == .5 and layer['y'] == .15 and layer['font_size'] == 64
    assert montage['clips'] == original['clips']
    assert montage['cues'] == original['cues'] and montage['audio_timeline'] == original['audio_timeline']
    assert montage['narration_audio_path'] == original['narration_audio_path']
    assert task.narration_cache_key == before.narration_cache_key
    versions = state.repo.list_timeline_versions('ws', state.task.id)
    operations = state.repo.list_edit_operations('ws', state.task.id)
    with monkeypatch.context() as replay:
        replay.setattr(CreationTaskExactEditRequest, 'action_values',
            lambda self: pytest.fail('recorded replay must not normalize or save another graphic'))
        repeated = state.client.post(url + '/edits/exact', json=payload)
    assert repeated.status_code == 200 and repeated.json() == saved
    assert state.repo.list_timeline_versions('ws', state.task.id) == versions
    assert state.repo.list_edit_operations('ws', state.task.id) == operations
    assert state.repo.get_creation_task_montage_plan('ws', state.task.id)['graphic_overlays'] == [layer]
    journal = state.client.get(url + f"/assistant/edit-requests/{payload['request_id']}?phase=apply")
    assert journal.status_code == 200, journal.text
    assert journal.json()['submitted_input'] == payload
    assert journal.json()['result'] == saved
    stale = state.client.post(url + '/timeline/undo', json={
        'expected_timeline_version_id': base.id, 'expected_timeline_sequence': base.sequence})
    assert stale.status_code == 409
    assert state.repo.get_creation_task('ws', state.task.id).current_timeline_version_id == version.id
    undone = state.client.post(url + '/timeline/undo', json={
        'expected_timeline_version_id': version.id, 'expected_timeline_sequence': version.sequence})
    assert undone.status_code == 200, undone.text
    restored = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    assert restored['graphic_overlays'] == original['graphic_overlays'] == []
    assert restored['clips'] == original['clips'] and restored['audio_timeline'] == original['audio_timeline']
    after_undo = state.repo.list_timeline_versions('ws', state.task.id)
    replayed = state.client.post(url + '/edits/exact', json=payload)
    assert replayed.status_code == 200 and replayed.json() == saved
    assert state.repo.list_timeline_versions('ws', state.task.id) == after_undo
    assert state.repo.get_creation_task_montage_plan('ws', state.task.id)['graphic_overlays'] == []


def test_metadata_patch_preserves_no_change_and_missing_target_responses(metadata_http_state):
    state = metadata_http_state
    unchanged = state.client.patch(state.url + '/montage/cues/cue', json={'text': '原字幕'})
    assert unchanged.status_code == 409, unchanged.text
    assert unchanged.json()['detail'] == {'error': 'edit_no_change', 'reason': '字幕文字、时轴和样式没有变化。',
        'impact': '没有创建新的时间线版本。', 'repair_action': '修改文字、断行、强调词或字幕样式后再预览。', 'retryable': False}
    missing = state.client.patch(state.url + '/montage/cues/missing', json={'text': '新字幕'})
    assert missing.status_code == 404, missing.text
    assert state.repo.get_creation_task('ws', state.task.id).current_timeline_version_id == state.base.id
    assert state.repo.list_edit_operations('ws', state.task.id) == []


def test_metadata_patch_routes_consume_use_case_without_route_reentry(metadata_http_state, monkeypatch):
    state = metadata_http_state
    def forbidden(*args, **kwargs):
        pytest.fail("metadata PATCH must not invoke another HTTP route")
    monkeypatch.setattr(main, "apply_creation_task_exact_edit", forbidden)
    clip = state.client.patch(state.url + "/montage/clips/clip", json={"locked": True})
    assert clip.status_code == 200, clip.text
    subtitle = state.client.patch(state.url + "/montage/cues/cue", json={"text": "只更新这句字幕"})
    assert subtitle.status_code == 200, subtitle.text
    current = state.repo.get_creation_task('ws', state.task.id)
    plan = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    assert plan['clips'][0]['locked'] is True
    assert plan['cues'][0]['text'] == "只更新这句字幕"
    assert plan['narration_audio_path'] == state.plan.narration_audio_path
    assert current.narration_cache_key == state.narration.cache_key
    assert len(state.repo.list_timeline_versions('ws', state.task.id)) == 3


def test_metadata_http_refuses_stale_or_foreign_inputs(metadata_http_state):
    state = metadata_http_state
    payload = metadata_payload(state)
    assert state.client.post(state.url + "/edits/exact", json={**payload, "expected_timeline_sequence": 2}).status_code == 409
    assert state.client.post(state.url + "/edits/exact", headers={"x-test-workspace": "foreign"}, json=metadata_payload(state)).status_code == 404
    assert state.client.post("/api/creation-tasks/foreign/edits/exact", json=metadata_payload(state)).status_code == 404
    _, token = state.members.join(state.members.create_invite_code(), "other metadata member")
    denied = state.client.post(state.url + "/edits/exact", headers={"X-Local-Member-Token": token}, json=metadata_payload(state))
    assert denied.status_code in {403, 404}, denied.text
    assert state.repo.get_creation_task('ws', state.task.id).current_timeline_version_id == state.base.id
    assert state.repo.list_edit_operations('ws', state.task.id) == []


def test_capabilities_are_real_schema_and_exclude_unmigrated_actions(material_edit_state):
    state = material_edit_state
    response = state.client.get(state.base_url + "/assistant/edit-capabilities")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["schema"] == CreationTaskExactEditRequest.model_json_schema()
    assert {"ReplaceClip", "TrimClip", "ChangeClipSpeed", "ChangeCrop", "UpdateSubtitleCue"} <= set(value["supported_operations"])
    assert not {"RegenerateNarrationSegment", "ChangePersonSegment", "RegeneratePersonRender"} & set(value["supported_operations"])
    assert "保留" in value["units"]["playback_rate"]
    assert "source_span=timeline_span*rate" in value["units"]["playback_rate"]
    assert "2.5*0.7=1.75" in value["units"]["playback_rate"]
    assert "ChangeLinkedSpeed：相对当前速度倍率" in value["units"]["playback_rate"]
    assert "source_span=timeline_span*rate" in value["schema"]["properties"]["playback_rate"]["description"]
    assert state.client.get("/api/creation-tasks/missing/assistant/edit-capabilities").status_code == 404


def test_typed_replace_replay_lost_receipt_and_undo_keep_narration(material_edit_state):
    state = material_edit_state
    before = state.client.get(state.base_url).json()
    original_audio = state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)["narration_audio_path"]
    payload = bound(state, **replace_action(state))
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload).json() == preview.json()
    proposal = preview.json()["proposal"]
    result_url = state.base_url + f"/edits/{proposal['id']}/result"
    binding = state.client.get(result_url).json()
    apply = {"request_id": str(uuid4()), "expected_base_version_id": binding["base_version_id"],
             "expected_input_digest": binding["input_digest"]}
    url = state.base_url + f"/edits/{proposal['id']}/confirm"
    confirmed = state.client.post(url, json=apply)
    assert confirmed.status_code == 200, confirmed.text
    after = state.client.get(state.base_url).json()
    assert state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)["narration_audio_path"] == original_audio
    assert after["montage"]["cues"] == before["montage"]["cues"]
    assert after["task"]["timeline_sequence"] == before["task"]["timeline_sequence"] + 1
    assert state.client.post(url, json=apply).json() == confirmed.json()
    # Simulate receipt loss after the actual operation commit. Recover from
    # existing proposal/operation/version lineage, without a second apply.
    with state.production._connect() as conn:
        conn.execute("DELETE FROM task_events WHERE event_type='edit_request_result' AND json_extract(payload_json,'$.response.operation.proposal_id')=?", (proposal["id"],))
    recovered = state.client.post(url, json=apply)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["operation"] == confirmed.json()["operation"]
    assert state.client.get(result_url).json()["result"]["operation"] == confirmed.json()["operation"]
    assert len([op for op in state.production.list_edit_operations(state.workspace_id, state.task.id) if op.proposal_id == proposal["id"]]) == 1
    assert state.client.post(state.base_url + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(state.base_url).json()["montage"]["clips"] == before["montage"]["clips"]
    assert state.client.post(url, json=apply).json()["operation"] == confirmed.json()["operation"]
    changed = {**payload, "source_start_seconds": 5}
    assert state.client.post(state.base_url + "/edits/exact/preview", json=changed).status_code == 409


@pytest.mark.parametrize("action", [
    {"operation_type": "TrimClip", "source_start_seconds": 9},
    {"operation_type": "ChangeClipSpeed", "playback_rate": 0.7},
    {"operation_type": "ChangeCrop", "fit_mode": "fill", "crop_x": 0.2, "crop_y": 0.5},
])
def test_exact_typed_actions_use_existing_domain_preview(material_edit_state, action):
    state = material_edit_state
    request = bound(state, clip_id=state.clip.id, **action)
    response = state.client.post(state.base_url + "/edits/exact/preview?draft=true", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["operation"]["operation_type"] == action["operation_type"]
    intent = state.production.get_edit_intent(state.workspace_id, response.json()["intent"]["id"])
    assert intent.constraints["request_source"]["request_id"] == request["request_id"]


@pytest.mark.parametrize("mutation", ["old_version", "old_sequence", "other_clip", "unrelated_field", "extra", "nonfinite"])
def test_typed_requests_refuse_invalid_scope_and_fields(material_edit_state, mutation):
    state = material_edit_state
    payload = bound(state, **replace_action(state))
    if mutation == "old_version":
        payload["expected_timeline_version_id"] = "old"
    elif mutation == "old_sequence":
        payload["expected_timeline_sequence"] += 1
    elif mutation == "other_clip":
        payload["clip_id"] = "another-task-clip"
    elif mutation == "unrelated_field":
        payload["text"] = "must not be silently ignored"
    elif mutation == "extra":
        payload["invented"] = 1
    else:
        payload["source_start_seconds"] = float("inf")
    before = state.client.get(state.base_url).json()["task"]["current_timeline_version_id"]
    response = state.client.post(state.base_url + "/edits/exact/preview", content=json.dumps(payload), headers={"Content-Type": "application/json"})
    assert response.status_code in {404, 409, 422}, response.text
    assert state.client.get(state.base_url).json()["task"]["current_timeline_version_id"] == before


def test_assistant_typed_source_is_host_bound_and_not_parsed_again(material_edit_state, tmp_path, monkeypatch):
    state = material_edit_state
    conversations = ConversationRepository(tmp_path / "conversations.sqlite3")
    conversations.initialize()
    monkeypatch.setattr(main, "conversation_repo", conversations)
    member = state.members.resolve_member(state.client.headers.get("X-Local-Member-Token"))
    conversation = conversations.create(state.workspace_id, member.member_id, "task", state.task.id)
    payload = bound(state, **replace_action(state))
    raw = "请把第二段换成那个更合适的画面，旁白维持原样"
    conversation = conversations.save_draft(conversation["id"], state.workspace_id, member.member_id, raw, conversation["draft_version"])
    turn, _ = conversations.create_turn_idempotent(conversation["id"], state.workspace_id, member.member_id,
        raw, str(uuid4()), conversation["draft_version"], request_context={"kind": "task", "task_id": state.task.id,
        "target_snapshot": {"timeline_version_id": payload["expected_timeline_version_id"], "timeline_sequence": payload["expected_timeline_sequence"]}})
    payload["assistant_turn_id"] = turn["id"]
    response = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["intent"]["raw_command"] == raw
    # Submit through the real persisted JobRun/DeviceScheduler path using the
    # existing explicit media substitutes, then end Pi before executing it.
    original_post = state.client.post
    runtime = _granted_domain_preview_adapter(state, monkeypatch)
    from test_creation_resource_contract import _render_budget
    selected_budget_for = main._device_operation_budget
    monkeypatch.setattr(main, '_device_operation_budget',
        lambda name: _render_budget() if name == 'clip_preview' else selected_budget_for(name))
    media = runtime.registrations['creation_job'].owner.task_media
    media.facts = main._task_media_facts
    def render_input(inputs, path, context):
        # Domain lineage seam only; playable media has separate R1 regressions.
        context.check()
        assert context.grant.workspace_id == state.workspace_id
        state.preview_inputs.append(inputs)
        path.write_bytes(b'domain-render-receipt')
    monkeypatch.setattr('app.media.clip_preview.render_clip_input', render_input)
    crop = bound(state, operation_type="ChangeCrop", clip_id=state.clip.id,
                 fit_mode="fill", crop_x=0.2, crop_y=0.5, assistant_turn_id=turn["id"])
    queued = original_post(state.base_url + "/edits/exact/preview", json=crop)
    assert queued.status_code == 202, queued.text
    event = state.production.execution_records.get_task_event(state.workspace_id, f"event_{queued.json()['job_id']}_start")
    command = event.payload['arguments']['_clip_preview']
    assert command['base_version_id'] == crop['expected_timeline_version_id']
    assert command['values'] == {'fit_mode':'fill', 'crop_x':.2, 'crop_y':.5}
    assert command['source']['assistant_turn_id'] == turn['id']
    conversations.finish_turn(turn["id"], "completed", "已准备修改")
    assert runtime.run_once()
    settled = state.client.get(queued.json()["result_url"])
    assert settled.status_code == 200, settled.text
    assert settled.json()["status"] == "succeeded", settled.text
    assert settled.json()["result"]["intent"]["raw_command"] == raw
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload).json() == response.json()
    payload["request_id"] = str(uuid4())
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload).status_code == 409


@pytest.mark.parametrize("use_request_identity", [True, False])
def test_source_revision_and_apply_identity_cannot_change_after_preview(material_edit_state, use_request_identity):
    state = material_edit_state
    payload = bound(state, **replace_action(state))
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    before = state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id
    with open(state.asset.path, "ab") as source:
        source.write(b"changed fixture revision")
    identity = str(uuid4())
    response = state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm", json={"request_id": identity} if use_request_identity else {})
    assert response.status_code == 409, response.text
    assert state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == before
    assert state.client.get(state.base_url + f"/assistant/edit-requests/{payload['request_id']}").json()["status"] == "recorded"


def test_other_workspace_and_task_do_not_resolve_original_request(material_edit_state, monkeypatch):
    state = material_edit_state
    payload = bound(state, **replace_action(state))
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()["proposal"]["id"]
    assert state.client.post(f"/api/creation-tasks/another-task/edits/{proposal_id}/confirm", json={"request_id": str(uuid4())}).status_code == 404
    monkeypatch.setattr(main, "current_workspace_id", lambda _: "another-workspace")
    assert state.client.get(state.base_url + "/assistant/edit-capabilities").status_code == 404
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload).status_code == 404
    assert state.client.get(state.base_url + f"/edits/{proposal_id}/result").status_code == 404


def test_typed_subtitle_update_and_apply_share_the_current_version(material_edit_state):
    state = _build_linked_state(material_edit_state)
    task = state.production.get_creation_task(state.workspace_id, state.task.id)
    track = next(item for item in state.production.list_subtitle_tracks(state.workspace_id, task.id, timeline_version_id=task.current_timeline_version_id) if item.language == "zh-CN")
    payload = bound(state, operation_type="UpdateSubtitleCue", track_id=track.id,
                    cue_id=track.cues[0].id, text="这是修改后的第一句。")
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()["proposal"]["id"]
    apply = state.client.post(state.base_url + f"/edits/{proposal_id}/confirm", json={"request_id": str(uuid4())})
    assert apply.status_code == 200, apply.text
    updated = state.production.get_creation_task(state.workspace_id, task.id)
    result_track = next(item for item in state.production.list_subtitle_tracks(state.workspace_id, task.id, timeline_version_id=updated.current_timeline_version_id) if item.language == "zh-CN")
    assert result_track.cues[0].text == payload["text"]
    assert result_track.cues[0].start_seconds == track.cues[0].start_seconds
    assert result_track.cues[0].end_seconds == track.cues[0].end_seconds


def test_subtitle_configuration_rejects_mixed_modes(material_edit_state):
    state = material_edit_state
    response = state.client.post(state.base_url + "/edits/exact/preview", json={
        "operation_type": "ConfigureSubtitleTrack", "style": {"font_size": 40}, "target_language": "en-US"})
    assert response.status_code == 422, response.text
    assert "不同动作" in response.text


def test_apply_request_identity_is_not_reusable_for_another_proposal(material_edit_state):
    state = material_edit_state
    first = state.client.post(state.base_url + "/edits/exact/preview", json=bound(state, **replace_action(state)))
    second = state.client.post(state.base_url + "/edits/exact/preview?draft=true", json=bound(state,
        operation_type="ChangeCrop", clip_id=state.clip.id, fit_mode="fill", crop_x=0.2, crop_y=0.5))
    assert first.status_code == second.status_code == 200
    identity = {"request_id": str(uuid4())}
    assert state.client.post(state.base_url + f"/edits/{first.json()['proposal']['id']}/confirm", json=identity).status_code == 200
    version = state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id
    response = state.client.post(state.base_url + f"/edits/{second.json()['proposal']['id']}/confirm", json=identity)
    assert response.status_code == 409 and "不同编辑输入" in response.text
    assert state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == version


def test_manual_undo_redo_require_current_expected_pair_without_assistant_turn(tmp_path, monkeypatch):
    from test_timeline_restore_application import restore_http_state
    state = restore_http_state(tmp_path, monkeypatch)
    original = state.production.get_creation_task(state.workspace_id, state.task.id)
    original_version = state.production.get_timeline_version(state.workspace_id, original.current_timeline_version_id)
    saved = state.client.post(state.base_url + "/edits/exact", json=bound(state,
        operation_type="ChangeCrop", clip_id="clip", fit_mode="fit", crop_x=.3, crop_y=.7))
    assert saved.status_code == 200, saved.text
    task = state.production.get_creation_task(state.workspace_id, state.task.id)
    version = state.production.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    undo = state.base_url + "/timeline/undo"
    stale = state.client.post(undo, json={"expected_timeline_version_id": original_version.id, "expected_timeline_sequence": original_version.sequence})
    assert stale.status_code == 409
    assert state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == version.id
    assert state.client.post(undo, json={"expected_timeline_version_id": version.id}).status_code == 422
    assert state.client.post(undo, json={"expected_timeline_sequence": version.sequence}).status_code == 422
    assert state.client.post(undo, json={"expected_timeline_version_id": None, "expected_timeline_sequence": None}).status_code == 422
    assert state.client.post(undo, json={"expected_timeline_version_id": None, "expected_timeline_sequence": version.sequence}).status_code == 409
    _other, token = state.members.join(state.members.create_invite_code(), "other history caller")
    expected = {"expected_timeline_version_id": version.id, "expected_timeline_sequence": version.sequence}
    assert state.client.post(undo, json=expected, headers={"X-Local-Member-Token": token}).status_code in {403, 404}
    undone = state.client.post(undo, json=expected)
    assert undone.status_code == 200, undone.text
    task = state.production.get_creation_task(state.workspace_id, state.task.id)
    undo_version = state.production.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    redone = state.client.post(state.base_url + "/timeline/redo", json={
        "expected_timeline_version_id": undo_version.id, "expected_timeline_sequence": undo_version.sequence})
    assert redone.status_code == 200, redone.text


    legacy = state.client.post(undo, json={})
    assert legacy.status_code == 200, legacy.text
    unavailable = state.client.post(undo, json={})
    assert unavailable.status_code == 409 and unavailable.json()["detail"]["error"] == "timeline_undo_unavailable"

def test_other_member_cannot_read_or_replay_private_typed_edit(material_edit_state):
    state = material_edit_state
    payload = bound(state, **replace_action(state))
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    _member, token = state.members.join(state.members.create_invite_code(), "other")
    headers = {"X-Local-Member-Token": token}
    assert state.client.get(state.base_url + "/assistant/edit-capabilities", headers=headers).status_code in {403, 404}
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload, headers=headers).status_code in {403, 404}
    proposal = preview.json()["proposal"]
    assert state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm", json={"request_id": str(uuid4())}, headers=headers).status_code in {403, 404}


@pytest.fixture
def linked_speed_http_state(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_linked_speed_application import speed_state
    state = speed_state(tmp_path)
    monkeypatch.setattr(main, 'production_repo', state.repo)
    monkeypatch.setattr(main, 'artifact_store', state.store)
    monkeypatch.setattr(main, 'local_members_service', state.members)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: request.headers.get('x-test-workspace', 'ws'))
    monkeypatch.setattr(main, 'settings', replace(main.settings,
        video_inbox_path=str(state.repo.video_library_path), video_output_path=str(tmp_path / 'outputs')))
    state.client = TestClient(main.app, headers={'X-Local-Member-Token': state.token})
    state.url = f'/api/creation-tasks/{state.task.id}'
    return state


def test_linked_speed_HTTP_confirm_and_fixed_result_do_not_use_HTTP_recovery(linked_speed_http_state, monkeypatch):
    state = linked_speed_http_state
    wakes = []
    wake = main._wake_assistant_continuations
    def observed_wake():
        wakes.append(True)
        return wake()
    monkeypatch.setattr(main, '_wake_assistant_continuations', observed_wake)
    preview = state.client.post(state.url + '/edits/exact/preview', json={
        'operation_type': 'ChangeLinkedSpeed', 'clip_id': 'clip', 'playback_rate': 2,
        'request_id': str(uuid4()), 'expected_timeline_version_id': state.base.id, 'expected_timeline_sequence': 2})
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()['proposal']['id']
    result_url = state.url + f'/edits/{proposal_id}/result'
    original = state.client.get(result_url).json()
    request = {'request_id': str(uuid4()), 'expected_base_version_id': state.base.id, 'expected_input_digest': original['input_digest']}
    monkeypatch.setattr(main, '_original_edit_result', lambda *args: pytest.fail('speed must recover durable facts directly'))
    before_confirm = len(wakes)
    accepted = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert accepted.status_code == 200, accepted.text
    # Preserve the existing first-save helper wake and explicit fresh-apply wake.
    assert len(wakes) == before_confirm + 2
    operation = accepted.json()['operation']
    # A later real direct edit changes HEAD; the original result must stay fixed.
    later = state.client.post(state.url + '/edits/exact', json={'operation_type': 'ChangeLinkedSpeed', 'clip_id': 'clip', 'playback_rate': 2})
    assert later.status_code == 200, later.text
    from app.task_audio import media_digest
    event = 'event_edit_request_' + media_digest(['ws', state.member.member_id, request['request_id'], 'apply'])
    recorded = state.repo.read_edit_request_result(state.task, event)
    assert recorded['status_code'] == 200 and recorded['response'] == accepted.json()
    with state.repo._connect() as conn:
        conn.execute('DELETE FROM task_events WHERE id=?', (event + '_result',))
    before_recovery = len(wakes)
    replay = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert replay.status_code == 200 and replay.json()['operation']['id'] == operation['id']
    assert len(wakes) == before_recovery
    restored = state.repo.read_edit_request_result(state.task, event)
    assert restored['status_code'] == 200 and restored['response'] == replay.json()
    read = state.client.get(result_url)
    assert read.status_code == 200 and read.json()['result']['operation']['result_version_id'] == operation['result_version_id']
    assert read.json()['result']['montage']['duration_seconds'] == 3
    assert len(state.repo.list_edit_operations('ws', state.task.id)) == 2


def test_linked_speed_direct_and_natural_consumers_call_real_application(linked_speed_http_state, monkeypatch):
    from app.creation.editing.linked_timeline_use_case import LinkedTimelineEditUseCase
    state = linked_speed_http_state
    calls = []
    preview = LinkedTimelineEditUseCase.preview
    def observed(self, command):
        assert self.repository is state.repo
        calls.append(command)
        return preview(self, command)
    monkeypatch.setattr(LinkedTimelineEditUseCase, 'preview', observed)
    monkeypatch.setattr(main, '_preview_exact_edit_request', lambda *args: pytest.fail('direct cannot reenter preview facade'))
    monkeypatch.setattr(main, '_confirm_creation_task_edit', lambda *args: pytest.fail('direct cannot reenter confirm facade'))
    applied = state.client.post(state.url + '/edits/exact', json={'operation_type': 'ChangeLinkedSpeed', 'clip_id': 'clip', 'playback_rate': 2})
    assert applied.status_code == 200, applied.text
    natural = state.client.post(state.url + '/edits/preview', json={'raw_command': '这个镜头改成2倍速 同步声音和字幕',
        'context_stage': 'montage', 'selected_object_refs': ['clip:clip']})
    assert natural.status_code == 200, natural.text
    assert natural.json()['proposal']['operation']['operation_type'] == 'ChangeLinkedSpeed'
    assert len(calls) == 2 and all(command.actor_identity['member_id'] == state.member.member_id for command in calls)


def test_linked_speed_apply_control_preserves_unsubmitted_and_explicit_null(linked_speed_http_state):
    state = linked_speed_http_state
    preview = state.client.post(state.url + '/edits/exact/preview', json={'operation_type': 'ChangeLinkedSpeed', 'clip_id': 'clip', 'playback_rate': 2})
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()['proposal']['id']
    url = state.url + f'/edits/{proposal_id}/confirm'
    refused = state.client.post(url, json={'request_id': str(uuid4()), 'expected_base_version_id': None})
    assert refused.status_code == 409 and '基础版本' in refused.text
    accepted = state.client.post(url, json={'request_id': str(uuid4())})
    assert accepted.status_code == 200, accepted.text


def linked_clip_action(state, operation):
    action = {'operation_type': operation, 'clip_id': 'clip'}
    if operation == 'InsertClip':
        action.update(asset_id=state.neighbour.id, segment_id=state.neighbour.segments[0].id)
    return action


@pytest.mark.parametrize('operation,duration', [('DeleteClip', 2), ('DuplicateClip', 6), ('InsertClip', 8)])
def test_linked_clip_public_preview_confirm_and_fixed_read(linked_speed_http_state, monkeypatch, operation, duration):
    state = linked_speed_http_state
    preview = state.client.post(state.url + '/edits/exact/preview', json={**linked_clip_action(state, operation),
        'request_id': str(uuid4()), 'expected_timeline_version_id': state.base.id, 'expected_timeline_sequence': 2})
    assert preview.status_code == 200, preview.text
    proposal = preview.json()['proposal']
    result_url = state.url + f"/edits/{proposal['id']}/result"
    original = state.client.get(result_url)
    assert original.status_code == 200 and original.json()['result'] is None
    monkeypatch.setattr(main, '_original_edit_result', lambda *args: pytest.fail('family must read durable fixed results'))
    monkeypatch.setattr(main, '_confirm_creation_task_edit', lambda *args: pytest.fail('family must bypass generic confirm'))
    accepted = state.client.post(state.url + f"/edits/{proposal['id']}/confirm", json={
        'request_id': str(uuid4()), 'expected_base_version_id': state.base.id,
        'expected_input_digest': original.json()['input_digest']})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['operation']['operation_type'] == operation
    assert accepted.json()['montage']['duration_seconds'] == duration
    fixed = state.client.get(result_url)
    assert fixed.status_code == 200 and fixed.json()['result']['operation'] == accepted.json()['operation']
    assert fixed.json()['result']['montage']['duration_seconds'] == duration
    assert len(state.repo.list_edit_operations('ws', state.task.id)) == 1


@pytest.mark.parametrize('operation,command', [('DeleteClip', '删除这个镜头'),
    ('DuplicateClip', '复制这个镜头'), ('InsertClip', '在这个镜头后插入镜头')])
def test_linked_clip_direct_and_natural_use_single_application(linked_speed_http_state, monkeypatch, operation, command):
    from app.creation.editing.linked_timeline_use_case import LinkedTimelineEditUseCase
    state = linked_speed_http_state
    calls = []
    preview = LinkedTimelineEditUseCase.preview
    def observed(self, command):
        assert self.repository is state.repo and self.montage_edits.repository is state.repo
        calls.append(command)
        return preview(self, command)
    monkeypatch.setattr(LinkedTimelineEditUseCase, 'preview', observed)
    natural = state.client.post(state.url + '/edits/preview', json={'raw_command': command,
        'context_stage': 'montage', 'selected_object_refs': ['clip:clip']})
    if operation == 'InsertClip':
        assert natural.status_code == 422 and natural.json()['detail'] == '插入镜头需要指定素材片段'
    else:
        assert natural.status_code == 200, natural.text
        assert natural.json()['proposal']['operation']['operation_type'] == operation
    monkeypatch.setattr(main, '_preview_exact_edit_request', lambda *args: pytest.fail('direct cannot reenter preview facade'))
    monkeypatch.setattr(main, '_confirm_creation_task_edit', lambda *args: pytest.fail('direct cannot reenter confirm facade'))
    direct = state.client.post(state.url + '/edits/exact', json=linked_clip_action(state, operation))
    assert direct.status_code == 200, direct.text
    assert direct.json()['operation']['operation_type'] == operation
    assert len(calls) == 2 and all(item.operation_type == operation for item in calls)
    assert all(item.actor_identity['member_id'] == state.member.member_id for item in calls)


def test_linked_clip_recovery_keeps_original_version_and_does_not_wake(linked_speed_http_state, monkeypatch):
    from app.task_audio import media_digest
    state = linked_speed_http_state
    wakes = []
    wake = main._wake_assistant_continuations
    def observed_wake():
        wakes.append(True)
        return wake()
    monkeypatch.setattr(main, '_wake_assistant_continuations', observed_wake)
    preview = state.client.post(state.url + '/edits/exact/preview', json=linked_clip_action(state, 'DuplicateClip'))
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()['proposal']['id']
    result_url = state.url + f'/edits/{proposal_id}/result'
    request = {'request_id': str(uuid4()), 'expected_base_version_id': state.base.id}
    accepted = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert accepted.status_code == 200, accepted.text
    operation = accepted.json()['operation']
    later = state.client.post(state.url + '/edits/exact', json=linked_clip_action(state, 'DeleteClip'))
    assert later.status_code == 200, later.text
    later_head = state.repo.get_creation_task('ws', state.task.id).current_timeline_version_id
    assert later_head != operation['result_version_id']
    event = 'event_edit_request_' + media_digest(['ws', state.member.member_id, request['request_id'], 'apply'])
    with state.repo._connect() as conn:
        conn.execute('DELETE FROM task_events WHERE id=?', (event + '_result',))
    before_wakes = len(wakes)
    recovered = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert recovered.status_code == 200 and recovered.json()['operation'] == operation
    assert recovered.json()['montage']['duration_seconds'] == 6
    assert len(wakes) == before_wakes
    assert state.repo.read_edit_request_result(state.task, event)['response'] == recovered.json()
    fixed = state.client.get(result_url)
    assert fixed.status_code == 200 and fixed.json()['result']['operation'] == operation
    assert state.repo.get_creation_task('ws', state.task.id).current_timeline_version_id == later_head
    assert len(state.repo.list_edit_operations('ws', state.task.id)) == 2
    changed = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json={**request,
        'expected_input_digest': fixed.json()['input_digest']})
    assert changed.status_code == 409 and len(wakes) == before_wakes


def test_linked_insert_permission_and_range_keep_original_error_projection(linked_speed_http_state):
    state = linked_speed_http_state
    action = linked_clip_action(state, 'InsertClip')
    state.repo._save_asset(replace(state.neighbour, owner_user_id='outside', visibility='private'))
    denied = state.client.post(state.url + '/edits/exact/preview', json={**action, 'source_start_seconds': 4})
    assert denied.status_code == 403 and denied.json()['detail'] == '当前成员无权使用该素材'
    state.repo._save_asset(state.neighbour)
    out_of_range = state.client.post(state.url + '/edits/exact/preview', json={**action, 'source_start_seconds': 4})
    assert out_of_range.status_code == 400 and out_of_range.json()['detail'] == '插入起点须在 0.0–4.0 秒之间。'
    assert not state.repo.list_edit_operations('ws', state.task.id)


@pytest.fixture
def subtitle_http_state(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_subtitle_application import subtitle_state
    state = subtitle_state(tmp_path)
    monkeypatch.setattr(main, 'production_repo', state.repo)
    monkeypatch.setattr(main, 'artifact_store', state.store)
    monkeypatch.setattr(main, 'local_members_service', state.members)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: request.headers.get('x-test-workspace', 'ws'))
    monkeypatch.setattr(main, 'settings', replace(main.settings,
        video_inbox_path=str(state.repo.video_library_path), video_output_path=str(state.case.output_root)))
    state.client = TestClient(main.app, headers={'X-Local-Member-Token': state.token})
    state.url = f'/api/creation-tasks/{state.task.id}'
    return state


def subtitle_action(state, operation):
    if operation == 'ConfigureSubtitleTrack':
        return {'operation_type': operation, 'style': {'font_size': 64}}
    if operation == 'ReplaceSubtitleText':
        return {'operation_type': operation, 'track_id': state.zh.id, 'find_text': '原', 'replacement_text': '新'}
    return {'operation_type': operation, 'track_id': state.zh.id, 'cue_id': 'cue', 'end_seconds': 1.5}


@pytest.mark.parametrize('operation', ['ReplaceSubtitleText', 'ConfigureSubtitleTrack', 'UpdateSubtitleCue'])
def test_subtitle_family_public_preview_confirm_fixed_read_preserves_other_facts(subtitle_http_state, monkeypatch, operation):
    state = subtitle_http_state
    before_plan = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    preview = state.client.post(state.url + '/edits/exact/preview?draft=true', json={**subtitle_action(state, operation),
        'request_id': str(uuid4()), 'expected_timeline_version_id': state.base.id, 'expected_timeline_sequence': 2})
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()['proposal']['id']
    result_url = state.url + f'/edits/{proposal_id}/result'
    original = state.client.get(result_url)
    assert original.status_code == 200 and original.json()['result'] is None
    monkeypatch.setattr(main, '_original_edit_result', lambda *args: pytest.fail('subtitle reads must consume durable business facts'))
    request = {'request_id': str(uuid4()), 'expected_base_version_id': state.base.id,
        'expected_input_digest': original.json()['input_digest']}
    null_base = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json={**request, 'expected_base_version_id': None})
    assert null_base.status_code == 409
    accepted = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert accepted.status_code == 200, accepted.text
    result = state.client.get(result_url)
    assert result.status_code == 200 and result.json()['result']['operation'] == accepted.json()['operation']
    current = state.repo.get_creation_task('ws', state.task.id)
    version = state.repo.get_timeline_version('ws', current.current_timeline_version_id)
    after_plan = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    assert after_plan['clips'] == before_plan['clips'] and after_plan['audio_timeline'] == before_plan['audio_timeline']
    assert current.narration_cache_key == state.narration.cache_key
    assert (state.case.output_root / state.narration.audio_path).read_bytes() == state.voice_bytes
    cover = state.repo.get_cover_asset('ws', state.task.id, version.snapshot['cover_asset_id'])
    assert cover.title == state.cover.title and cover.artifact_ref == state.cover.artifact_ref
    tracks = state.repo.list_subtitle_tracks('ws', state.task.id, timeline_version_id=version.id)
    if operation == 'ReplaceSubtitleText':
        assert len(tracks) == 1 and tracks[0].cues[0].text == '新字幕'
    elif operation == 'ConfigureSubtitleTrack':
        assert [track.style['font_size'] for track in tracks] == [64, 64]
        assert next(track for track in tracks if track.language == 'en-US').style['enabled'] is False
    else:
        assert len(tracks) == 2 and all(track.cues[0].end_seconds == 1.5 for track in tracks)
        assert next(track for track in tracks if track.language == 'en-US').cues[0].text == 'Original caption'


def test_subtitle_public_structure_sequence_ends_with_explicit_empty_track_snapshot(subtitle_http_state):
    state = subtitle_http_state
    # This structure inlet uses an independent English track; bilingual merge mapping is a separate boundary.
    from app.creation.contracts import TimelineVersion
    from app.creation.transactions import clone_subtitle_track, clone_cover_asset
    zh = clone_subtitle_track(state.zh, timeline_version_id='structure-start', track_id='structure-zh')
    en = replace(clone_subtitle_track(state.en, timeline_version_id='structure-start', track_id='structure-en'),
        source_language=None, generation_method='timeline')
    cover = clone_cover_asset(state.cover, timeline_version_id='structure-start')
    state.repo.create_timeline_version(TimelineVersion('structure-start', 'ws', state.task.id, state.base.id, 3,
        {**state.base.snapshot, 'subtitle_track_ids': [zh.id, en.id], 'cover_asset_id': cover.id}, None))
    state.repo.create_subtitle_track(zh)
    state.repo.create_subtitle_track(en)
    state.repo.create_cover_asset(cover)
    def preview_confirm(operation, **values):
        task = state.repo.get_creation_task('ws', state.task.id)
        track = next(item for item in state.repo.list_subtitle_tracks('ws', task.id,
            timeline_version_id=task.current_timeline_version_id) if item.language == 'en-US')
        preview = state.client.post(state.url + '/edits/exact/preview', json={
            'operation_type': operation, 'track_id': track.id, 'cue_id': track.cues[0].id, **values})
        assert preview.status_code == 200, preview.text
        proposal_id = preview.json()['proposal']['id']
        accepted = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json={'request_id': str(uuid4())})
        assert accepted.status_code == 200, accepted.text
        fixed = state.client.get(state.url + f'/edits/{proposal_id}/result')
        assert fixed.status_code == 200 and fixed.json()['result']['operation'] == accepted.json()['operation']
    preview_confirm('SplitSubtitleCue', split_offset=8, split_time_seconds=1)
    preview_confirm('MergeSubtitleCue')
    preview_confirm('DeleteSubtitleCue')
    preview_confirm('DeleteSubtitleCue')
    task = state.repo.get_creation_task('ws', state.task.id)
    tracks = state.repo.list_subtitle_tracks('ws', task.id, timeline_version_id=task.current_timeline_version_id)
    assert len(tracks) == 1 and tracks[0].language == 'zh-CN'
    preview = state.client.post(state.url + '/edits/exact/preview', json={
        'operation_type': 'DeleteSubtitleCue', 'track_id': tracks[0].id, 'cue_id': tracks[0].cues[0].id})
    assert preview.status_code == 200, preview.text
    assert state.client.post(state.url + f"/edits/{preview.json()['proposal']['id']}/confirm").status_code == 200
    task = state.repo.get_creation_task('ws', task.id)
    remaining = state.repo.list_subtitle_tracks('ws', task.id, timeline_version_id=task.current_timeline_version_id)[0]
    direct = state.client.post(state.url + '/edits/exact', json={
        'operation_type': 'DeleteSubtitleCue', 'track_id': remaining.id, 'cue_id': remaining.cues[0].id})
    assert direct.status_code == 200, direct.text
    task = state.repo.get_creation_task('ws', task.id)
    version = state.repo.get_timeline_version('ws', task.current_timeline_version_id)
    assert version.snapshot['subtitle_track_ids'] == []
    assert state.case.current_subtitle_tracks('ws', task, current_version=version, montage=state.plan) == []


@pytest.mark.parametrize('operation,raw_command,refs', [
    ('ReplaceSubtitleText', '全文把“原”替换成“新”', ['subtitle-track:family-zh']),
    ('ConfigureSubtitleTrack', '字幕字号改成64', []),
    ('UpdateSubtitleCue', '当前字幕时间改成0到1.5秒', ['cue:cue', 'subtitle-track:family-zh'])])
def test_subtitle_direct_and_natural_consumers_use_real_family(subtitle_http_state, monkeypatch, operation, raw_command, refs):
    from app.creation.editing.subtitle_use_case import SubtitleEditUseCase
    state = subtitle_http_state
    calls = []
    prepare = SubtitleEditUseCase.prepare_preview
    def observed(self, command):
        assert self.repository is self.montage_edits.repository is state.repo
        calls.append(command)
        return prepare(self, command)
    monkeypatch.setattr(SubtitleEditUseCase, 'prepare_preview', observed)
    monkeypatch.setattr(main, '_publish_task_picture_preview', lambda *args, **kwargs: pytest.fail('draft preview must not generate media'))
    natural = state.client.post(state.url + '/edits/preview?draft=true', json={
        'raw_command': raw_command, 'context_stage': 'subtitle_cover', 'selected_object_refs': refs})
    assert natural.status_code == 200, natural.text
    assert natural.json()['proposal']['operation']['operation_type'] == operation
    monkeypatch.setattr(main, '_preview_exact_edit_request', lambda *args: pytest.fail('direct cannot reenter HTTP preview'))
    monkeypatch.setattr(main, '_confirm_creation_task_edit', lambda *args: pytest.fail('direct cannot reenter HTTP confirm'))
    direct = state.client.post(state.url + '/edits/exact', json=subtitle_action(state, operation))
    assert direct.status_code == 200, direct.text
    assert direct.json()['operation']['operation_type'] == operation
    assert len(calls) == 2 and all(item.operation_type == operation for item in calls)


def test_subtitle_unknown_claim_and_HTTP_projection_are_not_application_facts(subtitle_http_state):
    from app.task_audio import media_digest
    state = subtitle_http_state
    preview = state.client.post(state.url + '/edits/exact/preview', json=subtitle_action(state, 'ReplaceSubtitleText'))
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()['proposal']['id']
    fixed = state.client.get(state.url + f'/edits/{proposal_id}/result').json()
    request = {'request_id': str(uuid4())}
    event, fresh = state.repo.claim_edit_request(state.task, state.member.member_id, request['request_id'], 'apply',
        {'proposal_id': proposal_id, 'base_version_id': state.base.id, 'input_digest': fixed['input_digest'], 'request': request})
    assert fresh
    state.repo.save_edit_request_result(state.task, event, status_code=200, response={'only': 'a transport projection'})
    refused = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert refused.status_code == 409 and '尚未确定' in refused.text
    assert not state.repo.list_edit_operations('ws', state.task.id)


@pytest.fixture
def compound_http_state(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_compound_application import compound_state
    state = compound_state(tmp_path)
    monkeypatch.setattr(main, 'production_repo', state.repo)
    monkeypatch.setattr(main, 'artifact_store', state.store)
    monkeypatch.setattr(main, 'local_members_service', state.members)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: request.headers.get('x-test-workspace', 'ws'))
    monkeypatch.setattr(main, 'settings', replace(main.settings,
        video_inbox_path=str(state.repo.video_library_path), video_output_path=str(state.case.output_root)))
    state.client = TestClient(main.app, headers={'X-Local-Member-Token': state.token})
    state.url = f'/api/creation-tasks/{state.task.id}'
    return state


def test_compound_public_selection_restore_confirm_and_fixed_read_are_one_joint_edit(compound_http_state, monkeypatch):
    from test_compound_application import COMPOUND_TEXT
    from app.task_audio import TaskAudio
    state = compound_http_state
    validations = []
    validate = TaskAudio.validate
    def observed(self, *args, **kwargs):
        assert self.repository is state.repo
        validations.append(kwargs.get('draft_only'))
        return validate(self, *args, **kwargs)
    monkeypatch.setattr(TaskAudio, 'validate', observed)
    monkeypatch.setattr(main, '_publish_task_picture_preview', lambda *args, **kwargs: pytest.fail('draft must not publish video'))
    before = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    full = state.client.post(state.url + '/edits/preview?draft=true', json={
        'raw_command': COMPOUND_TEXT, 'context_stage': 'montage', 'selected_object_refs': ['clip:clip'], 'playhead_ms': 750})
    assert full.status_code == 200, full.text
    original = full.json()['proposal']
    assert original['operation']['operation_type'] == 'EditCompoundTimeline'
    assert [step['enabled'] for step in original['operation']['payload']['steps']] == [True, True, True]
    selection_url = state.url + f"/edits/{original['id']}/selection/preview?draft=true"
    invalid = state.client.post(selection_url, json={'included_step_indices': [99]})
    assert invalid.status_code == 422
    partial = state.client.post(selection_url, json={'included_step_indices': [0, 2]})
    assert partial.status_code == 200, partial.text
    selected = partial.json()['proposal']
    assert [step['index'] for step in selected['operation']['payload']['steps']] == [0, 1, 2]
    assert [step['enabled'] for step in selected['operation']['payload']['steps']] == [True, False, True]
    assert selected['operation']['payload']['audio_timeline']['tracks'][0]['clips'][0]['gain_db'] == 0
    restored = state.client.post(state.url + f"/edits/{selected['id']}/selection/preview?draft=true",
        json={'raw_command': '恢复第2项'})
    assert restored.status_code == 200, restored.text
    proposal = restored.json()['proposal']
    assert [step['enabled'] for step in proposal['operation']['payload']['steps']] == [True, True, True]
    assert proposal['operation']['payload']['audio_timeline']['tracks'][0]['clips'][0]['gain_db'] == -3
    assert state.repo.get_edit_proposal('ws', original['id']).status == 'proposed'
    assert state.repo.get_edit_proposal('ws', selected['id']).status == 'proposed'
    assert state.repo.get_creation_task_montage_plan('ws', state.task.id) == before
    result_url = state.url + f"/edits/{proposal['id']}/result"
    fixed = state.client.get(result_url)
    assert fixed.status_code == 200 and fixed.json()['result'] is None
    monkeypatch.setattr(main, '_original_edit_result', lambda *args: pytest.fail('compound must read durable business results'))
    accepted = state.client.post(state.url + f"/edits/{proposal['id']}/confirm", json={
        'request_id': str(uuid4()), 'expected_base_version_id': state.base.id,
        'expected_input_digest': fixed.json()['input_digest']})
    assert accepted.status_code == 200, accepted.text
    task = state.repo.get_creation_task('ws', state.task.id)
    version = state.repo.get_timeline_version('ws', task.current_timeline_version_id)
    assert version.sequence == 4 and len(state.repo.list_edit_operations('ws', state.task.id)) == 1
    after = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    assert after['clips'][0]['visual_effects']['exposure'] == .25 and after['clips'][1] == before['clips'][1]
    assert after['audio_timeline']['tracks'][0]['clips'][0]['gain_db'] == -3
    tracks = state.repo.list_subtitle_tracks('ws', state.task.id, timeline_version_id=version.id)
    assert len(tracks) == 2 and all(track.style['font_size'] == 64 for track in tracks)
    assert next(track for track in tracks if track.language == 'en-US').style['enabled'] is False
    assert {track.language: [cue.text for cue in track.cues] for track in tracks} == {
        'zh-CN': ['原字幕', '后续字幕'], 'en-US': ['Original caption', 'Next caption']}
    fixed = state.client.get(result_url)
    assert fixed.status_code == 200 and fixed.json()['result']['operation'] == accepted.json()['operation']
    assert len(validations) >= 4 and all(validations)


def test_compound_unknown_claim_with_projection_has_no_durable_edit_and_no_exact_API(compound_http_state):
    from test_compound_application import COMPOUND_TEXT
    state = compound_http_state
    exact = state.client.post(state.url + '/edits/exact', json={'operation_type': 'EditCompoundTimeline'})
    assert exact.status_code == 422
    preview = state.client.post(state.url + '/edits/preview?draft=true', json={
        'raw_command': COMPOUND_TEXT, 'context_stage': 'montage', 'selected_object_refs': ['clip:clip']})
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()['proposal']['id']
    fixed = state.client.get(state.url + f'/edits/{proposal_id}/result').json()
    request = {'request_id': str(uuid4())}
    event, fresh = state.repo.claim_edit_request(state.task, state.member.member_id, request['request_id'], 'apply',
        {'proposal_id': proposal_id, 'base_version_id': state.base.id, 'input_digest': fixed['input_digest'], 'request': request})
    assert fresh
    state.repo.save_edit_request_result(state.task, event, status_code=200, response={'only': 'transport projection'})
    refused = state.client.post(state.url + f'/edits/{proposal_id}/confirm', json=request)
    assert refused.status_code == 409 and '尚未确定' in refused.text
    assert not state.repo.list_edit_operations('ws', state.task.id)
