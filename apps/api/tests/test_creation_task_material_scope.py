"""A work's chosen video remains a bound input across saved requests and execution."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.creation.editing.metadata_use_case import MetadataEditUseCase
from app.creation.storyboard_regeneration import material_signature
from app.creation.task_authorization import TaskAuthorizationDenied, TaskAuthorizationUseCase
from app.creation_execution import CreationExecutionActor
from app.local_members import LocalMemberService
from _media_preview_test_fixtures import register_synthetic_video_asset
from test_creation_execution import _seed


@pytest.fixture
def scoped_work(tmp_path, monkeypatch):
    repository, task_id, _, _ = _seed(tmp_path, workspace_id="local", voice=False)
    members = LocalMemberService(repository.database_path)
    member, token = members.join(members.create_invite_code(), "选材测试成员")
    another, another_token = members.join(members.create_invite_code(), "其他成员")
    task = replace(repository.task_records.get_creation_task("local", task_id), owner_user_id=member.member_id)
    plan = replace(repository.plan_records.get_creation_plan("local", task.plan_id), owner_user_id=member.member_id)
    with repository._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=json_set(payload,'$.owner_user_id',?) WHERE id=?",
            (member.member_id, task.id))
        repository.plan_records.write_plan_payload(conn, "local", plan)
    assets = [register_synthetic_video_asset(repository, workspace_id="local", member_id=member.member_id,
        path=tmp_path / "library" / f"{key}.mp4", asset_id=key, seconds=3)[0]
        for key in ("chosen", "other")]
    monkeypatch.setattr(main, "production_repo", repository)
    monkeypatch.setattr(main, "local_members_service", members)
    return SimpleNamespace(repo=repository, task=task, plan=plan, assets=assets, client=TestClient(main.app),
        headers={"X-Local-Member-Token": token}, other_headers={"X-Local-Member-Token": another_token},
        actor=CreationExecutionActor("local", member.member_id), another_member=another)


def command(plan, *assets, request_id=None):
    return {"request_id": request_id or str(uuid4()), "expected_plan_updated_at": plan.updated_at.isoformat(),
            "assets": [{"asset_id": asset.id, "expected_asset_updated_at": asset.updated_at.isoformat()} for asset in assets]}


def test_scope_receipt_replays_and_stale_saves_do_not_change_the_current_plan(scoped_work):
    work = scoped_work
    chosen, other = work.assets
    url = f"/api/creation-tasks/{work.task.id}/material-scope"
    original = command(work.plan, chosen)
    saved = work.client.put(url, json=original, headers=work.headers)
    assert saved.status_code == 200, saved.text
    receipt = saved.json()
    assert receipt["selected_material_asset_ids"] == [chosen.id]
    assert receipt["status"] == "saved"
    assert work.client.put(url, json=command(work.plan, other), headers=work.headers).status_code == 409
    assert work.client.put(url, json=command(work.plan, other, request_id=original["request_id"]),
        headers=work.headers).status_code == 409

    latest = work.repo.plan_records.get_creation_plan("local", work.plan.id)
    assert latest.selected_material_asset_ids == [chosen.id]
    stale_asset = replace(chosen, updated_at=chosen.updated_at - timedelta(seconds=1))
    assert work.client.put(url, json=command(latest, stale_asset), headers=work.headers).status_code == 409
    original_url = f"{url}/{original['request_id']}"
    assert work.client.get(original_url, headers=work.other_headers).status_code in (403, 404)

    cleared = work.client.put(url, json=command(latest), headers=work.headers)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["selected_material_asset_ids"] == []
    assert work.client.put(url, json=original, headers=work.headers).json() == receipt
    assert work.client.get(original_url, headers=work.headers).json() == receipt
    assert not work.repo.plan_records.get_creation_plan("local", work.plan.id).selected_material_asset_ids


def test_work_scope_renews_original_authorization_and_never_expands_on_lost_access(scoped_work):
    work = scoped_work
    chosen, other = work.assets
    auth = TaskAuthorizationUseCase(work.repo, lambda _identity: "ready")
    auth.ensure(work.task, work.actor, interactive=True)
    assert auth.material_asset_ids("local", work.task.id) == {chosen.id, other.id}
    broad_signature = material_signature(work.repo, work.task)
    url = f"/api/creation-tasks/{work.task.id}/material-scope"
    response = work.client.put(url, json=command(work.plan, chosen), headers=work.headers)
    assert response.status_code == 200, response.text

    with pytest.raises(PermissionError):
        auth.material_asset_ids("local", work.task.id)
    auth.ensure(work.task, work.actor, interactive=True)
    allowed = auth.material_asset_ids("local", work.task.id)
    assert allowed == {chosen.id}
    scoped_signature = material_signature(work.repo, work.task)
    assert scoped_signature != broad_signature

    edit = MetadataEditUseCase(work.repo, None, output_root=None, audio_service=None)
    before, after = [SimpleNamespace(clips=[SimpleNamespace(asset_id=asset.id, person_render_id=None)])
        for asset in (chosen, other)]
    with pytest.raises(PermissionError, match="超出本作品"):
        edit.prepare_material_scope(work.task, before, after, work.actor.identity_payload(), connection=None)

    with work.repo._connect() as conn:
        conn.execute("UPDATE material_assets SET visibility='private',payload=json_set(payload,'$.owner_user_id',?) WHERE id=?",
            (work.another_member.member_id, chosen.id))
    assert auth.status("local", work.task.id) == "outside_scope"
    with pytest.raises(TaskAuthorizationDenied, match="不可读取"):
        auth.ensure(work.task, work.actor, interactive=True)
    with pytest.raises(PermissionError):
        auth.material_asset_ids("local", work.task.id)
    assert not work.repo.automatic_material_segments("local", viewer_user_id=work.task.owner_user_id, asset_ids=allowed)
    assert material_signature(work.repo, work.task) != scoped_signature
    assert work.repo.plan_records.get_creation_plan("local", work.plan.id).selected_material_asset_ids == [chosen.id]
