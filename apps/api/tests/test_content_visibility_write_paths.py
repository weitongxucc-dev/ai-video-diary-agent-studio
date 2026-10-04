"""任务、方案、模板、素材的可见性写入与回读（WIL-116 第②段）。

新建默认 private；存量行回填 shared；明确共享/收回后列与载荷一致；重开、重扫、
再存一版都不会把明确设置改回去。

诚实边界：这里只验证写入与回读的一致性。成员可见性过滤本身仍由读取侧承担
（WIL-110），本段不当作「权限已隔离」的证据。

全部使用 tmp_path 合成库；不打开真实用户库。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from app.production import (
    CreationPlan,
    CreationTask,
    MaterialAsset,
    ProductionRepository,
    VideoTemplate,
)


WORKSPACE = "workspace_116"
TEMPLATE_OWNER = "member-template-owner"
UPLOADER = "member-content-uploader"


def _repository(tmp_path: Path, name: str = "library") -> ProductionRepository:
    library = tmp_path / name
    library.mkdir(exist_ok=True)
    return ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path / "library_legacy",
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [5.0, 17.0],
    )


def _asset(
    workspace_id: str,
    *,
    asset_id: str = "asset-1",
    file_name: str = "clip.mp4",
    owner_user_id: str | None = UPLOADER,
) -> MaterialAsset:
    return MaterialAsset(
        id=asset_id,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        file_name=file_name,
        path=f"{workspace_id}/{file_name}",
        size_bytes=1024,
        modified_at=datetime(2026, 1, 1, 12, 0, 0),
    )


def _plan(workspace_id: str, plan_id: str = "plan-1") -> CreationPlan:
    return CreationPlan(
        id=plan_id,
        workspace_id=workspace_id,
        goal="做一条真实短视频",
        name="真实计划",
        target_count=1,
        language="zh",
        aspect_ratio="9:16",
        duration_target_seconds=30,
        knowledge_references=[],
        source_references=[],
        selected_voice=None,
        pronunciation_references=[],
        approved_asset_count=0,
        approved_clip_count=0,
        material_rule_summary="",
        model_snapshot=[],
        confirmation_points=[],
        blockers=[],
        preflight_fingerprint="fingerprint",
    )


def _task(workspace_id: str, plan_id: str, task_id: str = "task-1") -> CreationTask:
    return CreationTask(
        id=task_id,
        workspace_id=workspace_id,
        plan_id=plan_id,
        name="真实任务",
        scenario="talking_head",
    )


def _confirm(production: ProductionRepository, plan: CreationPlan, task: CreationTask) -> None:
    # 沿既有确认契约：方案与任务的上传者取自这次已验证身份。
    production.confirm_creation_plan(
        plan, task, verified_owner_user_id=UPLOADER
    )


def _stored_visibility(production: ProductionRepository, table: str, row_id: str, workspace_id: str) -> str:
    with production._connect() as conn:
        row = conn.execute(
            f"SELECT visibility FROM {table} WHERE workspace_id = ? AND id = ?",
            (workspace_id, row_id),
        ).fetchone()
    return str(row["visibility"])


def test_new_content_is_private_in_both_column_and_read_back(tmp_path):
    production = _repository(tmp_path)
    plan, task = _plan(WORKSPACE), _task(WORKSPACE, "plan-1")
    _confirm(production, plan, task)

    assert _stored_visibility(production, "creation_plans", plan.id, WORKSPACE) == "private"
    assert _stored_visibility(production, "creation_tasks", task.id, WORKSPACE) == "private"
    assert production.get_creation_plan(WORKSPACE, plan.id).visibility == "private"
    assert production.get_creation_task(WORKSPACE, task.id).visibility == "private"


def test_new_material_is_private_and_reads_back_private(tmp_path):
    production = _repository(tmp_path)
    production._save_asset(_asset(WORKSPACE))

    assert _stored_visibility(production, "material_assets", "asset-1", WORKSPACE) == "private"
    assert production.get_asset(WORKSPACE, "asset-1").visibility == "private"
    assert [item.visibility for item in production.list_assets(WORKSPACE)] == ["private"]


def test_explicit_share_and_revoke_are_visible_to_readers(tmp_path):
    production = _repository(tmp_path)
    plan, task = _plan(WORKSPACE), _task(WORKSPACE, "plan-1")
    _confirm(production, plan, task)
    production._save_asset(_asset(WORKSPACE))

    production.set_creation_task_visibility(WORKSPACE, task.id, "shared")
    production.set_creation_plan_visibility(WORKSPACE, plan.id, "shared")
    production.set_asset_visibility(WORKSPACE, "asset-1", "shared")

    assert production.get_creation_task(WORKSPACE, task.id).visibility == "shared"
    assert production.get_creation_plan(WORKSPACE, plan.id).visibility == "shared"
    assert production.get_asset(WORKSPACE, "asset-1").visibility == "shared"

    production.set_asset_visibility(WORKSPACE, "asset-1", "private")

    assert production.get_asset(WORKSPACE, "asset-1").visibility == "private"
    assert _stored_visibility(production, "material_assets", "asset-1", WORKSPACE) == "private"


def test_shared_and_private_read_paths_split_on_the_stored_value(tmp_path):
    production = _repository(tmp_path)
    production._save_asset(_asset(WORKSPACE, asset_id="private-asset", file_name="a.mp4"))
    production._save_asset(_asset(WORKSPACE, asset_id="shared-asset", file_name="b.mp4"))
    production.set_asset_visibility(WORKSPACE, "shared-asset", "shared")

    shared_only = production.list_assets(WORKSPACE, include_private=False)
    everything = production.list_assets(WORKSPACE)

    assert {item.id for item in shared_only} == {"shared-asset"}
    assert {item.id for item in everything} == {"private-asset", "shared-asset"}


def test_updating_a_shared_asset_does_not_revive_or_reset_its_visibility(tmp_path):
    production = _repository(tmp_path)
    production._save_asset(_asset(WORKSPACE))
    production.set_asset_visibility(WORKSPACE, "asset-1", "shared")

    production.update_asset_tags(WORKSPACE, "asset-1", ["重点"])

    assert production.get_asset(WORKSPACE, "asset-1").visibility == "shared"
    production.update_asset_tags(WORKSPACE, "asset-1", ["重点", "第二版"])
    assert production.get_asset(WORKSPACE, "asset-1").visibility == "shared"


def test_reopen_keeps_visibility(tmp_path):
    production = _repository(tmp_path)
    production._save_asset(_asset(WORKSPACE))
    production.set_asset_visibility(WORKSPACE, "asset-1", "shared")

    reopened = _repository(tmp_path)

    assert reopened.get_asset(WORKSPACE, "asset-1").visibility == "shared"


def test_legacy_rows_without_the_column_are_shared_after_migration(tmp_path):
    """迁移前的旧行没有 visibility，列加上后按存量回填 shared。"""
    database = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(database)
    conn.executescript(
        """
        CREATE TABLE creation_tasks (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, plan_id)
        );
        CREATE TABLE creation_plans (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO creation_plans (id, workspace_id, payload, updated_at) VALUES (?, ?, ?, ?)",
        ("legacy-plan", WORKSPACE, json.dumps({"id": "legacy-plan", "workspace_id": WORKSPACE}), "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO creation_tasks (id, workspace_id, plan_id, payload, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("legacy-task", WORKSPACE, "legacy-plan", json.dumps({
            "id": "legacy-task", "workspace_id": WORKSPACE, "plan_id": "legacy-plan",
            "name": "旧任务", "scenario": "talking_head",
        }), "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    production = _repository_with_database(tmp_path, database)

    assert _stored_visibility(production, "creation_tasks", "legacy-task", WORKSPACE) == "shared"
    assert _stored_visibility(production, "creation_plans", "legacy-plan", WORKSPACE) == "shared"
    assert production.get_creation_task(WORKSPACE, "legacy-task").visibility == "shared"


def _repository_with_database(tmp_path: Path, database: Path) -> ProductionRepository:
    legacy_library = tmp_path / "library_legacy"
    legacy_library.mkdir(exist_ok=True)
    return ProductionRepository(
        database_path=database,
        video_library_path=legacy_library,
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [5.0, 17.0],
    )


def test_template_visibility_applies_to_new_versions(tmp_path):
    production = _repository(tmp_path)
    template = VideoTemplate(
        id="tpl-1", workspace_id=WORKSPACE, version=1, name="模板", origin="user",
        settings={"a": 1},
    )
    production.save_video_template(template, verified_owner_user_id=TEMPLATE_OWNER)
    production.set_video_template_visibility(WORKSPACE, "tpl-1", "shared")

    production.save_video_template(VideoTemplate(
        id="tpl-1", workspace_id=WORKSPACE, version=2, name="模板", origin="user",
        settings={"a": 2},
    ))

    latest = production.get_video_template(WORKSPACE, "tpl-1", 2)
    assert latest.visibility == "shared"
    assert _stored_visibility(production, "video_templates", "tpl-1", WORKSPACE) == "shared"


def test_visibility_never_crosses_workspace(tmp_path):
    production = _repository(tmp_path)
    production._save_asset(_asset("ws_a", asset_id="asset-a", file_name="a.mp4"))
    production.set_asset_visibility("ws_a", "asset-a", "shared")

    with pytest.raises(KeyError):
        production.set_asset_visibility("ws_b", "asset-a", "private")

def test_template_second_version_reads_back_the_stored_visibility_everywhere(tmp_path):
    production = _repository(tmp_path)
    production.save_video_template(VideoTemplate(
        id="tpl-2", workspace_id=WORKSPACE, version=1, name="模板", origin="user",
        settings={"a": 1},
    ), verified_owner_user_id=TEMPLATE_OWNER)
    production.set_video_template_visibility(WORKSPACE, "tpl-2", "shared")

    saved = production.save_video_template(VideoTemplate(
        id="tpl-2", workspace_id=WORKSPACE, version=2, name="模板", origin="user",
        settings={"a": 2},
    ))

    # 存进去的、立刻拿到的、列里的，三者必须是同一个值。
    assert saved.visibility == "shared"
    assert _stored_visibility(production, "video_templates", "tpl-2", WORKSPACE) == "shared"
    # 列出是带读取身份的读：这里用与创建同一条可信身份。
    listed = {
        item.version: item.visibility
        for item in production.list_video_templates(WORKSPACE, viewer_user_id=TEMPLATE_OWNER)
    }
    assert listed == {2: "shared", 1: "shared"}

    reopened = _repository(tmp_path)
    assert {
        item.version: item.visibility
        for item in reopened.list_video_templates(WORKSPACE, viewer_user_id=TEMPLATE_OWNER)
    } == {2: "shared", 1: "shared"}


def test_analysis_only_write_keeps_an_explicitly_shared_asset_shared(tmp_path):
    production = _repository(tmp_path)
    production._save_asset(_asset(WORKSPACE))
    production.set_asset_visibility(WORKSPACE, "asset-1", "shared")

    analyzed = replace(
        production.get_asset(WORKSPACE, "asset-1"),
        analysis_message="本次分析的说明。",
    )
    production._save_asset(analyzed, analysis_only=True)

    assert production.get_asset(WORKSPACE, "asset-1").visibility == "shared"
    assert _stored_visibility(production, "material_assets", "asset-1", WORKSPACE) == "shared"
