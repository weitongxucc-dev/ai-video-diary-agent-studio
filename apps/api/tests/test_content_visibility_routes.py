"""WIL-116：任务、方案、模板、素材的显式共享/收回路由与响应同列。

只验证「写进去的值、回读的值是同一个」以及工作区/成员边界；成员可见性过滤本身
仍由读取侧接线（WIL-110），不在这里冒充安全隔离。

全部使用 tmp_path 合成库与真实成员令牌。
"""
from __future__ import annotations

from app.materials.intake import MaterialIntakeUseCase

from fastapi.testclient import TestClient

import app.main as main_module
from app.database_migrations import CONTENT_VISIBILITY_DEFAULT, CONTENT_VISIBILITY_LEGACY
from app.local_members import LocalMemberService
from app.production import ProductionRepository

client = TestClient(main_module.app)


def material_library_no_execution(monkeypatch):
    """HTTP metadata facts only; fail before any provider/media/runtime work."""
    import subprocess
    import wave

    def forbidden(*_args, **_kwargs):
        raise AssertionError("material HTTP metadata must not execute media or runtime")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(wave, "open", forbidden)
    for name in ("run_plan_turn", "run_task_turn", "_start_device_runtime", "_creation_runtime"):
        monkeypatch.setattr(main_module, name, forbidden)
    monkeypatch.setattr(MaterialIntakeUseCase, "scan_library", forbidden)
    monkeypatch.setattr(ProductionRepository, "analyze_asset", forbidden)


def _setup(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=library,
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [5.0, 17.0],
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    service = LocalMemberService(tmp_path / "agent.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", service)
    monkeypatch.setattr(
        main_module, "current_workspace_id", lambda request: request.headers.get("x-test-workspace", "ws")
    )
    # 真实成员令牌：私有内容只对上传者本人可读，匿名请求读不到自己的私有条目。
    owner, owner_token = service.join(service.create_invite_code(), "所有者")
    service.set_role(owner.member_id, "owner")
    return production, service, owner_token, owner.member_id


def _headers(token: str) -> dict[str, str]:
    return {"X-Local-Member-Token": token}


def _confirm_task(production, workspace_id: str, owner_user_id: str):
    from app.creation.plan_contracts import CreationPlan
    from app.creation.contracts import CreationTask

    plan = CreationPlan(
        id="plan-vis", workspace_id=workspace_id, goal="做一条真实短视频", name="计划",
        target_count=1, language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
        knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="", model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint="fingerprint",
    )
    task = CreationTask(id="task-vis", workspace_id=workspace_id, plan_id=plan.id,
                        name="任务", scenario="talking_head")
    production.plan_confirmation.confirm_creation_plan(
        plan, task, verified_owner_user_id=owner_user_id
    )
    return plan, task


def test_material_visibility_round_trips_through_the_route(tmp_path, monkeypatch):
    material_library_no_execution(monkeypatch)
    production, _service, owner_token, owner_member_id = _setup(tmp_path, monkeypatch)
    from app.production import MaterialAsset
    from datetime import datetime

    # 归属写在素材本身上：这条用例要断言的正是「本人私有 → 共享 → 收回」，
    # 因此给出与请求令牌同一个已验证上传者。
    production._save_asset(MaterialAsset(
        id="asset-vis", workspace_id="ws", file_name="clip.mp4", path="ws/clip.mp4",
        size_bytes=10, modified_at=datetime(2026, 1, 1),
        owner_user_id=owner_member_id,
    ))

    _other, other_token = _service.join(_service.create_invite_code(), "另一成员")
    hidden = client.get("/api/production/materials/asset-vis", headers=_headers(other_token))
    assert hidden.status_code == 404
    detail = client.get("/api/production/materials/asset-vis", headers=_headers(owner_token))
    assert detail.status_code == 200
    assert detail.json()["visibility"] == CONTENT_VISIBILITY_DEFAULT

    shared = client.put(
        "/api/production/materials/asset-vis/visibility",
        json={"visibility": "shared"},
        headers=_headers(owner_token),
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["visibility"] == CONTENT_VISIBILITY_LEGACY
    assert client.get("/api/production/materials/asset-vis", headers=_headers(other_token)).status_code == 200

    revoked = client.put(
        "/api/production/materials/asset-vis/visibility",
        json={"visibility": "private"},
        headers=_headers(owner_token),
    )
    assert revoked.json()["visibility"] == CONTENT_VISIBILITY_DEFAULT
    # 收回后回到私有：读取仍需带上与创建同一个真实身份。
    after_revoke = client.get(
        "/api/production/materials/asset-vis", headers=_headers(owner_token)
    )
    assert after_revoke.status_code == 200
    assert after_revoke.json()["visibility"] == CONTENT_VISIBILITY_DEFAULT
    assert client.get("/api/production/materials/asset-vis", headers=_headers(other_token)).status_code == 404


def test_task_and_plan_visibility_round_trip(tmp_path, monkeypatch):
    production, _service, owner_token, owner_member_id = _setup(tmp_path, monkeypatch)
    plan, task = _confirm_task(production, "ws", owner_member_id)

    task_detail = client.get(f"/api/creation-tasks/{task.id}", headers=_headers(owner_token))
    assert task_detail.status_code == 200, task_detail.text
    assert task_detail.json()["task"]["visibility"] == CONTENT_VISIBILITY_DEFAULT

    shared_task = client.put(
        f"/api/creation-tasks/{task.id}/visibility",
        json={"visibility": "shared"},
        headers=_headers(owner_token),
    )
    assert shared_task.status_code == 200, shared_task.text
    assert shared_task.json()["task"]["visibility"] == CONTENT_VISIBILITY_LEGACY

    shared_plan = client.put(
        f"/api/creation-plans/{plan.id}/visibility",
        json={"visibility": "shared"},
        headers=_headers(owner_token),
    )
    assert shared_plan.status_code == 200, shared_plan.text
    assert shared_plan.json()["visibility"] == CONTENT_VISIBILITY_LEGACY

    plan_detail = client.get(f"/api/creation-plans/{plan.id}", headers=_headers(owner_token))
    assert plan_detail.json()["visibility"] == CONTENT_VISIBILITY_LEGACY


def test_template_visibility_round_trips_and_a_new_version_inherits(tmp_path, monkeypatch):
    material_library_no_execution(monkeypatch)
    production, _service, owner_token, owner_member_id = _setup(tmp_path, monkeypatch)
    from app.production import VideoTemplate

    production.save_video_template(VideoTemplate(
        id="tpl-vis", workspace_id="ws", version=1, name="模板", origin="user",
        settings={"a": 1},
    ), verified_owner_user_id=owner_member_id)

    shared = client.put(
        "/api/video-templates/tpl-vis/visibility",
        json={"visibility": "shared"},
        headers=_headers(owner_token),
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["item"]["visibility"] == CONTENT_VISIBILITY_LEGACY

    listed = {
        item["version"]: item["visibility"]
        for item in client.get("/api/video-templates", headers=_headers(owner_token)).json()["items"]
        if item["id"] == "tpl-vis"
    }
    assert listed == {1: CONTENT_VISIBILITY_LEGACY}


def test_visibility_route_rejects_an_unknown_value_and_another_workspace(tmp_path, monkeypatch):
    production, _service, owner_token, owner_member_id = _setup(tmp_path, monkeypatch)
    _confirm_task(production, "ws", owner_member_id)

    rejected = client.put(
        "/api/creation-tasks/task-vis/visibility",
        json={"visibility": "everyone"},
        headers=_headers(owner_token),
    )
    assert rejected.status_code == 422

    foreign = client.put(
        "/api/creation-tasks/task-vis/visibility",
        json={"visibility": "shared"},
        headers={"x-test-workspace": "other-ws"},
    )
    assert foreign.status_code == 404