"""Task list, workspace and controls expose the same authoritative history facts."""
from dataclasses import asdict, replace

import pytest

from test_task_media_grants import _visual_scope_case
from test_task_media_grants import case
from fastapi.testclient import TestClient


from test_montage_pipeline import person_render_edit_state as person_render_edit_state
from test_timeline_history import publish_legacy_media_version


@pytest.mark.parametrize("legacy_media", [False, True])
def test_list_workspace_controls_agree_after_apply_undo_and_redo(tmp_path, monkeypatch, legacy_media):
    main,repo,actor,media,runtime,client,original,track,store=_visual_scope_case(tmp_path,monkeypatch)
    try:
        # Workspace has richer Plan/Script prerequisites; assert its shared projection
        # through the existing montage fixture in the companion regression below.
        def read():
            item=next(value for value in client.get('/api/creation-tasks').json()['items'] if value['id']=='task')
            control_response=client.get('/api/creation-tasks/task/edit-controls')
            assert control_response.status_code==200,control_response.text
            controls=control_response.json()
            assert (item['timeline_sequence'],item['can_undo'],item['can_redo'])==(
                controls['timeline_sequence'],controls['can_undo'],controls['can_redo'])
            return item

        def publish_after(item):
            base = repo.timeline_records.get_timeline_version('local', item['current_timeline_version_id'])
            return publish_legacy_media_version(repo, base)

        initial=read()
        assert initial['timeline_sequence']==1 and not initial['can_undo'] and not initial['can_redo']
        saved=client.post('/api/creation-tasks/task/edits/exact',json={
            'operation_type':'AdjustVisualEffects','clip_ids':['clip_1'],'effects':{'exposure':.15}})
        assert saved.status_code==200,saved.text
        applied=read()
        assert applied['timeline_sequence']==2 and applied['can_undo'] and not applied['can_redo']
        if legacy_media:
            cut = publish_after(applied)
            after_cut = read()
            assert after_cut['current_timeline_version_id'] == cut.id
            assert after_cut['can_undo'] and not after_cut['can_redo']
        undone_response = client.post('/api/creation-tasks/task/timeline/undo', json={'request_id': 'projection-undo'})
        assert undone_response.status_code == 200, undone_response.text
        undone=read()
        assert undone['timeline_sequence']==3 + legacy_media and undone['can_redo'] and not undone['can_undo']
        if legacy_media:
            cut = publish_after(undone)
            assert read()['can_redo']
            event_id = f"event_{cut.created_by_operation_id}_media_result"
            with repo.connection_factory() as conn:
                conn.execute("UPDATE task_events SET payload_json=json_set(payload_json,'$.timeline_version_id',?) WHERE id=?",
                             ("other-version", event_id))
            assert not read()['can_redo']
            refused = client.post('/api/creation-tasks/task/timeline/redo', json={'request_id': 'invalid-media-redo'})
            assert refused.status_code == 409, refused.text
            assert read()['current_timeline_version_id'] == cut.id
            with repo.connection_factory() as conn:
                conn.execute("UPDATE task_events SET payload_json=json_set(payload_json,'$.timeline_version_id',?) WHERE id=?",
                             (cut.id, event_id))
        redone_response = client.post('/api/creation-tasks/task/timeline/redo', json={'request_id': 'projection-redo'})
        assert redone_response.status_code == 200, redone_response.text
        redone=read()
        assert redone['timeline_sequence']==4 + 2 * legacy_media and redone['can_undo'] and not redone['can_redo']
        if legacy_media:
            base = repo.timeline_records.get_timeline_version('local', redone['current_timeline_version_id'])
            wrong_owner = repo.timeline_records.create_timeline_version(replace(
                base, id='timeline_wrong_operation_result', parent_version_id=base.id, sequence=base.sequence + 1))
            invalid = read()
            assert invalid['current_timeline_version_id'] == wrong_owner.id
            assert not invalid['can_undo'] and not invalid['can_redo']
    finally:
        runtime.stop(0)


def test_workspace_and_list_share_revision_and_script_rule(person_render_edit_state):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    detail=state.client.get(base)
    assert detail.status_code==200,detail.text
    listed=state.client.get('/api/creation-tasks')
    assert listed.status_code==200,listed.text
    item=next(value for value in listed.json()['items'] if value['id']==state.task.id)
    for key in ('current_timeline_version_id','timeline_sequence','can_undo','can_redo','can_revise_script'):
        assert item[key]==detail.json()['task'][key]


def test_all_nested_task_responses_agree_after_edit_undo_and_redo(person_render_edit_state, monkeypatch):
    import app.main as main
    from app.config import Settings
    from app.schemas import CreationRequestItemResponse

    state = person_render_edit_state
    # This fixture originally supplies only render settings. Topic/script readers
    # require the real dataclass for the existing workspace configuration adapter.
    monkeypatch.setattr(main, "settings", replace(
        Settings.from_env(), video_output_path=main.settings.video_output_path,
        temporary_root_path=str(main.settings.effective_temporary_root_path),
    ))
    main._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    base = f"/api/creation-tasks/{state.task.id}"
    keys = ("current_timeline_version_id", "timeline_sequence", "can_undo", "can_redo", "can_revise_script")

    def read():
        detail = state.client.get(base)
        assert detail.status_code == 200, detail.text
        workspace = detail.json()
        expected = {key: workspace["task"][key] for key in keys}
        nested = [workspace["narration"]["task"]]
        for url, field in (
            (f"/api/creation-plans/{state.task.plan_id}", "creation_task"),
            (base + "/topics", "task"),
            (base + "/scripts", "task"),
        ):
            response = state.client.get(url)
            assert response.status_code == 200, response.text
            nested.append(response.json()[field])
        current = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
        plan = state.production.plan_records.get_creation_plan(state.workspace_id, current.plan_id)
        item = CreationRequestItemResponse.model_validate(main._creation_request_item_response({
            "ordinal": 1, "plan_id": plan.id, "task_id": current.id,
            "plan": asdict(plan), "task": asdict(current),
        })).model_dump(mode="json")
        nested.append(item["task"])
        for task in nested:
            assert {key: task[key] for key in keys} == expected
        return expected

    initial = read()
    preview = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "SplitClip", "clip_id": state.montage.clips[0].id,
        "split_time_seconds": 1.25,
    })
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    confirmed = state.client.post(base + f"/edits/{proposal['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    edited = read()
    assert edited["timeline_sequence"] == initial["timeline_sequence"] + 1
    assert edited["can_undo"] and not edited["can_redo"]
    assert state.client.post(base + "/timeline/undo", json={'request_id': 'test_all_nested_task_responses_agree_after_edit_undo_and_redo:undo:106'}).status_code == 200
    undone = read()
    assert undone["timeline_sequence"] == edited["timeline_sequence"] + 1 and undone["can_redo"]
    assert state.client.post(base + "/timeline/redo", json={'request_id': 'test_all_nested_task_responses_agree_after_edit_undo_and_redo:redo:109'}).status_code == 200
    redone = read()
    assert redone["timeline_sequence"] == undone["timeline_sequence"] + 1
    assert redone["can_undo"] and not redone["can_redo"]
    version = state.production.timeline_records.get_timeline_version(
        state.workspace_id, redone["current_timeline_version_id"])
    cut = publish_legacy_media_version(state.production, version)
    assert read()["can_undo"]
    blocked = state.client.post(base + "/timeline/undo", json={"request_id": "whole-restore-after-cut"})
    assert blocked.status_code == 409, blocked.text
    assert "不能覆盖后来成果" in blocked.text
    assert read()["current_timeline_version_id"] == cut.id


def test_no_history_and_script_revision_rule_are_real_entity_facts(tmp_path,monkeypatch):
    import app.main as main
    repo,actor,*_=case(tmp_path)
    monkeypatch.setattr(main,'production_repo',repo)
    monkeypatch.setattr(main,'current_workspace_id',lambda request:'local')
    client=TestClient(main.app)
    item=client.get('/api/creation-tasks').json()['items'][0]
    assert item['timeline_sequence']==0 and not item['can_undo'] and not item['can_redo']
    from app.creation.projection import project_task_summary
    task=repo.task_records.get_creation_task('local','task')
    script=replace(task,confirmed_script_id='script',stage='script',stage_status='failed')
    assert script.can_revise_script
    assert project_task_summary(repo,script).summary['can_revise_script'] is True
