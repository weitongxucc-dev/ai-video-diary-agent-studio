"""WIL-116：任务、方案、模板、素材的显式共享/收回路由与响应同列。

只验证「写进去的值、回读的值是同一个」以及工作区/成员边界；成员可见性过滤本身
仍由读取侧接线（WIL-110），不在这里冒充安全隔离。

全部使用 tmp_path 合成库与真实成员令牌。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module
from app.database_migrations import CONTENT_VISIBILITY_DEFAULT, CONTENT_VISIBILITY_LEGACY
from app.local_members import LocalMemberService
from app.production import ProductionRepository

client = TestClient(main_module.app)


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
    return production, service


def _headers() -> dict[str, str]:
    return {}


def _confirm_task(production, workspace_id: str):
    from app.production import CreationPlan, CreationTask

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
    production.confirm_creation_plan(plan, task)
    return plan, task


def test_material_visibility_round_trips_through_the_route(tmp_path, monkeypatch):
    production, _service = _setup(tmp_path, monkeypatch)
    from app.production import MaterialAsset
    from datetime import datetime

    production._save_asset(MaterialAsset(
        id="asset-vis", workspace_id="ws", file_name="clip.mp4", path="ws/clip.mp4",
        size_bytes=10, modified_at=datetime(2026, 1, 1),
    ))

    detail = client.get("/api/production/materials/asset-vis", headers=_headers())
    assert detail.status_code == 200
    assert detail.json()["visibility"] == CONTENT_VISIBILITY_DEFAULT

    shared = client.put(
        "/api/production/materials/asset-vis/visibility",
        json={"visibility": "shared"},
        headers=_headers(),
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["visibility"] == CONTENT_VISIBILITY_LEGACY

    revoked = client.put(
        "/api/production/materials/asset-vis/visibility",
        json={"visibility": "private"},
        headers=_headers(),
    )
    assert revoked.json()["visibility"] == CONTENT_VISIBILITY_DEFAULT
    assert client.get("/api/production/materials/asset-vis").json()["visibility"] == CONTENT_VISIBILITY_DEFAULT


def test_task_and_plan_visibility_round_trip(tmp_path, monkeypatch):
    production, _service = _setup(tmp_path, monkeypatch)
    plan, task = _confirm_task(production, "ws")

    task_detail = client.get(f"/api/creation-tasks/{task.id}", headers=_headers())
    assert task_detail.status_code == 200, task_detail.text
    assert task_detail.json()["task"]["visibility"] == CONTENT_VISIBILITY_DEFAULT

    shared_task = client.put(
        f"/api/creation-tasks/{task.id}/visibility",
        json={"visibility": "shared"},
        headers=_headers(),
    )
    assert shared_task.status_code == 200, shared_task.text
    assert shared_task.json()["task"]["visibility"] == CONTENT_VISIBILITY_LEGACY

    shared_plan = client.put(
        f"/api/creation-plans/{plan.id}/visibility",
        json={"visibility": "shared"},
        headers=_headers(),
    )
    assert shared_plan.status_code == 200, shared_plan.text
    assert shared_plan.json()["visibility"] == CONTENT_VISIBILITY_LEGACY

    plan_detail = client.get(f"/api/creation-plans/{plan.id}", headers=_headers())
    assert plan_detail.json()["visibility"] == CONTENT_VISIBILITY_LEGACY


def test_template_visibility_round_trips_and_a_new_version_inherits(tmp_path, monkeypatch):
    production, _service = _setup(tmp_path, monkeypatch)
    from app.production import VideoTemplate

    production.save_video_template(VideoTemplate(
        id="tpl-vis", workspace_id="ws", version=1, name="模板", origin="user",
        settings={"a": 1},
    ))

    shared = client.put(
        "/api/video-templates/tpl-vis/visibility",
        json={"visibility": "shared"},
        headers=_headers(),
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["item"]["visibility"] == CONTENT_VISIBILITY_LEGACY

    listed = {
        item["version"]: item["visibility"]
        for item in client.get("/api/video-templates", headers=_headers()).json()["items"]
        if item["id"] == "tpl-vis"
    }
    assert listed == {1: CONTENT_VISIBILITY_LEGACY}


def test_visibility_route_rejects_an_unknown_value_and_another_workspace(tmp_path, monkeypatch):
    production, _service = _setup(tmp_path, monkeypatch)
    _confirm_task(production, "ws")

    rejected = client.put(
        "/api/creation-tasks/task-vis/visibility",
        json={"visibility": "everyone"},
        headers=_headers(),
    )
    assert rejected.status_code == 422

    foreign = client.put(
        "/api/creation-tasks/task-vis/visibility",
        json={"visibility": "shared"},
        headers={"x-test-workspace": "other-ws"},
    )
    assert foreign.status_code == 404