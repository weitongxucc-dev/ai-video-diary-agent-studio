"""制作域读取权限：任务/方案/模板私有只对上传者，共享对成员；local 受控诊断可用。

复用知识域同一套判据（app.sources.knowledge_item_readable），不新造第二套规则。
只用隔离临时库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.production import (
    CreationPlan,
    CreationTask,
    ProductionRepository,
    VideoTemplate,
    content_readable,
)

ALICE = "member-alice"
BOB = "member-bob"
WORKSPACE = "workspace-110"
LOCAL = "local"


@pytest.fixture
def production(tmp_path: Path) -> ProductionRepository:
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    return ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path / "library_legacy",
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [5.0, 17.0],
    )


def plan_for(workspace_id: str, plan_id: str = "plan-1") -> CreationPlan:
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


def task_for(workspace_id: str, plan_id: str, task_id: str = "task-1") -> CreationTask:
    return CreationTask(
        id=task_id, workspace_id=workspace_id, plan_id=plan_id, name="真实任务", scenario="talking_head"
    )


def test_task_and_plan_owner_binds_to_verified_identity_and_is_not_self_reported(
    production: ProductionRepository,
) -> None:
    plan, task = production.confirm_creation_plan(
        plan_for(WORKSPACE), task_for(WORKSPACE, "plan-1"),
        verified_owner_user_id=ALICE,
    )
    # 归属来自已验证身份并随持久化回读。
    assert plan.owner_user_id == ALICE
    assert task.owner_user_id == ALICE
    assert production.get_creation_plan(WORKSPACE, "plan-1").owner_user_id == ALICE
    assert production.get_creation_task(WORKSPACE, "task-1").owner_user_id == ALICE

    # 私有只对上传者：另一位成员读不到。
    assert content_readable(
        user_id=BOB, local_diagnostic=False, visibility=plan.visibility, owner_user_id=plan.owner_user_id
    ) is False
    assert content_readable(
        user_id=ALICE, local_diagnostic=False, visibility=task.visibility, owner_user_id=task.owner_user_id
    ) is True
    # 显式共享后对成员可读；收回共享立即对他人失效。
    production.set_creation_plan_visibility(WORKSPACE, "plan-1", "shared")
    production.set_creation_task_visibility(WORKSPACE, "task-1", "shared")
    shared = production.get_creation_task(WORKSPACE, "task-1")
    assert content_readable(
        user_id=BOB, local_diagnostic=False, visibility=shared.visibility, owner_user_id=shared.owner_user_id
    ) is True
    production.set_creation_task_visibility(WORKSPACE, "task-1", "private")
    revoked = production.get_creation_task(WORKSPACE, "task-1")
    assert content_readable(
        user_id=BOB, local_diagnostic=False, visibility=revoked.visibility, owner_user_id=revoked.owner_user_id
    ) is False


def test_template_owner_binds_and_survives_version_save(production: ProductionRepository) -> None:
    template = VideoTemplate(
        id="tpl-1", workspace_id=WORKSPACE, version=1, name="我的模板", origin="user", settings={}
    )
    saved = production.save_video_template(template, verified_owner_user_id=ALICE)
    assert saved.owner_user_id == ALICE
    assert production.get_video_template(WORKSPACE, "tpl-1", 1).owner_user_id == ALICE
    # 后续版本不覆盖已有归属。
    v2 = production.save_video_template(
        VideoTemplate(id="tpl-1", workspace_id=WORKSPACE, version=2, name="我的模板", origin="user", settings={}),
        verified_owner_user_id=BOB,
    )
    assert v2.owner_user_id == ALICE


def test_local_members_in_one_workspace_are_isolated_by_member_id(production: ProductionRepository) -> None:
    # 本地产品多名成员同处 local：私有按 member_id 隔离，不因有令牌就全量可见。
    production.confirm_creation_plan(
        plan_for(LOCAL), task_for(LOCAL, "plan-1"),
        verified_owner_user_id="member-owner",
    )
    task = production.get_creation_task(LOCAL, "task-1")
    assert content_readable(
        user_id="member-other", local_diagnostic=False, visibility=task.visibility, owner_user_id=task.owner_user_id
    ) is False
    assert content_readable(
        user_id="member-owner", local_diagnostic=False, visibility=task.visibility, owner_user_id=task.owner_user_id
    ) is True
    # 受控本地诊断：本机私有照常可读（产品单机可用），但不是成员隔离结论。
    assert content_readable(
        user_id=None, local_diagnostic=True, visibility=task.visibility, owner_user_id=task.owner_user_id
    ) is True


def test_confirm_without_verified_identity_keeps_legacy_ownerless_row(production: ProductionRepository) -> None:
    # legacy 调用不传身份：归属留空，不被算成任何成员的私有资产，且不报错。
    plan, task = production.confirm_creation_plan(plan_for(WORKSPACE), task_for(WORKSPACE, "plan-1"))
    assert plan.owner_user_id is None
    assert task.owner_user_id is None
    assert production.get_creation_task(WORKSPACE, "task-1").owner_user_id is None


def test_content_readable_matches_the_knowledge_domain_rule() -> None:
    # 转发同一规则，不是第二套：无身份不可读、未知范围保守、local 仅诊断范围可见。
    assert content_readable(
        user_id=None, local_diagnostic=False, visibility="shared", owner_user_id="x"
    ) is False
    assert content_readable(
        user_id="x", local_diagnostic=False, visibility="unexpected", owner_user_id="x"
    ) is False
    assert content_readable(
        user_id="x", local_diagnostic=False, visibility="private", owner_user_id=None
    ) is False