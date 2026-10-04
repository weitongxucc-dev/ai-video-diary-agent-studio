"""素材详情回查片段使用记录：只读既有渲染写入的行，指向真实任务与当前版本。

使用记录本身没有时间线版本外键，所以这里不猜使用当时是哪个版本：能证明的只报
任务当前版本并标成current，任务没有当前版本就如实标unrecorded。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.production import ProductionRepository

WORKSPACE = "workspace-usage"
OTHER_WORKSPACE = "workspace-other-usage"


def make_repo(tmp_path: Path) -> tuple[ProductionRepository, Path]:
    library = tmp_path / "曜核AI IP一体机"
    library.mkdir(exist_ok=True)
    repo = ProductionRepository(
        tmp_path / "production.sqlite",
        library,
        duration_reader=lambda path: 4.0,
        scene_reader=lambda path, duration: [2.0],
    )
    return repo, library


def register(
    repo: ProductionRepository,
    library: Path,
    name: str,
    *,
    workspace_id: str = WORKSPACE,
    owner_user_id: str | None = None,
) -> str:
    """登记一个真实落盘的素材并分析出片段，供使用记录指向。

    绑定目录时给出可信身份：新入库素材的上传者就是它，沿既有创建语义写入，
    不在事后直接改库里的 owner 字段。
    """
    path = library / name
    path.write_bytes(b"material-bytes")
    repo.bind_material_ingestion(
        workspace_id,
        expected_directory=str(library),
        verified_owner_user_id=owner_user_id,
    )
    repo.confirm_material_copy_complete(
        workspace_id, relative_path=name,
        expected_revision=repo.material_copy_revision(path.stat()),
        confirmation_source="manual",
    )
    asset_id = next(asset.id for asset in repo.scan_library(workspace_id) if asset.file_name == name)
    repo.analyze_asset(workspace_id, asset_id)
    return asset_id


def write_task(repo: ProductionRepository, task_id: str, *, current_version_id: str | None,
               workspace_id: str = WORKSPACE) -> None:
    payload = {
        "id": task_id,
        "workspace_id": workspace_id,
        "plan_id": f"creation_plan_{task_id}",
        "name": "使用记录测试任务",
        "stage": "review",
        "status": "waiting_for_user",
        "current_timeline_version_id": current_version_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with repo._connect() as conn:
        conn.execute(
            "INSERT INTO creation_tasks (id, workspace_id, plan_id, payload, updated_at)"
            " VALUES (?,?,?,?,?)",
            (task_id, workspace_id, f"creation_plan_{task_id}", json.dumps(payload), payload["updated_at"]),
        )


def write_plan(repo: ProductionRepository, plan_id: str, task_id: str, clips: list[dict[str, object]],
               *, workspace_id: str = WORKSPACE) -> None:
    payload = {"id": plan_id, "task_id": task_id, "status": "rendered", "clips": clips}
    now = datetime.now(timezone.utc).isoformat()
    with repo._connect() as conn:
        conn.execute(
            "INSERT INTO creation_task_montage_plans (id, workspace_id, task_id, payload, updated_at)"
            " VALUES (?,?,?,?,?)",
            (plan_id, workspace_id, task_id, json.dumps(payload), now),
        )


def clip(clip_id: str, asset_id: str, segment_id: str, *, role: str = "b_roll",
         manually_selected: bool = True) -> dict[str, object]:
    return {
        "id": clip_id,
        "cue_id": "cue_1",
        "asset_id": asset_id,
        "segment_id": segment_id,
        "role": role,
        "manually_selected": manually_selected,
    }


def test_a_record_names_the_task_clip_and_current_version(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    asset_id = register(repo, library, "回查素材甲.mp4")
    other_asset_id = register(repo, library, "回查素材乙.mp4")
    mine = repo.get_asset(WORKSPACE, asset_id).segments[0]
    theirs = repo.get_asset(WORKSPACE, other_asset_id).segments[0]
    write_task(repo, "task_usage_1", current_version_id="timeline_current_1")
    write_plan(
        repo,
        "plan_usage_1",
        "task_usage_1",
        [
            clip("clip_usage_1", asset_id, mine.id, role="a_roll"),
            clip("clip_usage_2", other_asset_id, theirs.id),
        ],
    )
    repo.record_segment_usage(WORKSPACE, asset_id, mine.id, plan_id="plan_usage_1", clip_id="clip_usage_1")

    records = repo.list_segment_usage_records(WORKSPACE, asset_id, mine.id)

    assert len(records) == 1
    record = records[0]
    assert (record["plan_id"], record["clip_id"]) == ("plan_usage_1", "clip_usage_1")
    assert record["task"] == {
        "id": "task_usage_1",
        "name": "使用记录测试任务",
        "stage": "review",
        "status": "waiting_for_user",
        "current_timeline_version_id": "timeline_current_1",
    }
    assert record["role"] == "a_roll"
    assert record["manually_selected"] is True
    # 使用当时的版本没有记录；这里只给出任务的当前版本并标明它是当前值。
    assert record["resolution"] == "current"
    assert record["timeline_version_id"] == "timeline_current_1"


def test_another_segment_and_another_workspace_are_not_mixed_in(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    asset_id = register(repo, library, "回查素材丙.mp4")
    segments = repo.get_asset(WORKSPACE, asset_id).segments
    mine, theirs = segments[0], segments[1]
    foreign_root = tmp_path / "foreign"
    foreign_root.mkdir()
    foreign_repo, foreign_library = make_repo(foreign_root)
    foreign = register(foreign_repo, foreign_library, "他区素材.mp4", workspace_id=OTHER_WORKSPACE)
    # Import already-confirmed foreign facts for the read-isolation assertions.
    repo._save_asset(foreign_repo.get_asset(OTHER_WORKSPACE, foreign))
    foreign_segment = repo.get_asset(OTHER_WORKSPACE, foreign).segments[0]
    write_task(repo, "task_usage_2", current_version_id="timeline_current_2")
    write_plan(repo, "plan_usage_2", "task_usage_2", [clip("clip_usage_3", asset_id, theirs.id)])
    write_task(repo, "task_usage_foreign", current_version_id="timeline_foreign",
               workspace_id=OTHER_WORKSPACE)
    write_plan(repo, "plan_usage_foreign", "task_usage_foreign",
               [clip("clip_usage_foreign", foreign, foreign_segment.id)],
               workspace_id=OTHER_WORKSPACE)
    repo.record_segment_usage(WORKSPACE, asset_id, theirs.id, plan_id="plan_usage_2", clip_id="clip_usage_3")
    repo.record_segment_usage(OTHER_WORKSPACE, foreign, foreign_segment.id,
                              plan_id="plan_usage_foreign", clip_id="clip_usage_foreign")

    assert repo.list_segment_usage_records(WORKSPACE, asset_id, mine.id) == []
    other_workspace = repo.list_segment_usage_records(OTHER_WORKSPACE, foreign, foreign_segment.id)
    assert [record["task"]["id"] for record in other_workspace] == ["task_usage_foreign"]
    with pytest.raises(KeyError):
        repo.list_segment_usage_records(OTHER_WORKSPACE, foreign, mine.id)


def test_reading_again_changes_no_counter_and_writes_nothing(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    asset_id = register(repo, library, "回查素材戊.mp4")
    segment = repo.get_asset(WORKSPACE, asset_id).segments[0]
    write_task(repo, "task_usage_3", current_version_id=None)
    write_plan(repo, "plan_usage_3", "task_usage_3", [clip("clip_usage_4", asset_id, segment.id)])
    repo.record_segment_usage(WORKSPACE, asset_id, segment.id, plan_id="plan_usage_3", clip_id="clip_usage_4")
    before = repo.get_asset(WORKSPACE, asset_id)

    first = repo.list_segment_usage_records(WORKSPACE, asset_id, segment.id)
    second = repo.list_segment_usage_records(WORKSPACE, asset_id, segment.id)

    assert first == second
    # 任务没有当前版本时不猜任何版本。
    assert first[0]["resolution"] == "unknown"
    assert first[0]["timeline_version_id"] is None
    after = repo.get_asset(WORKSPACE, asset_id)
    assert (after.segments[0].usage_count, after.updated_at) == (
        before.segments[0].usage_count,
        before.updated_at,
    )


@pytest.fixture
def usage_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo, library = make_repo(tmp_path)
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", WORKSPACE),
    )
    # 私有素材只对上传者本人可读：这里给出真实成员身份，而不是靠匿名本地范围。
    member_id = "member-uploader"
    service = SimpleNamespace(
        resolve_member=lambda token: (
            SimpleNamespace(member_id=token, role="member", name="上传者", created_at=None)
            if token == member_id else None
        )
    )
    monkeypatch.setattr(main_module, "local_members_service", service)
    monkeypatch.setattr(main_module, "_local_member_token", lambda request: "member-uploader")
    return SimpleNamespace(
        client=TestClient(main_module.app), repository=repo, library=library,
        member_id=member_id,
    )


def test_route_returns_the_records_for_the_named_segment_only(usage_client) -> None:
    repo, library = usage_client.repository, usage_client.library
    asset_id = register(repo, library, "接口回查甲.mp4", owner_user_id=usage_client.member_id)
    mine, theirs = repo.get_asset(WORKSPACE, asset_id).segments[:2]
    write_task(repo, "task_usage_4", current_version_id="timeline_current_4")
    write_plan(repo, "plan_usage_4", "task_usage_4", [
        clip("clip_usage_5", asset_id, mine.id),
        clip("clip_usage_6", asset_id, theirs.id),
    ])
    repo.record_segment_usage(WORKSPACE, asset_id, mine.id, plan_id="plan_usage_4", clip_id="clip_usage_5")
    repo.record_segment_usage(WORKSPACE, asset_id, theirs.id,
                              plan_id="plan_usage_4", clip_id="clip_usage_6")

    response = usage_client.client.get(
        f"/api/production/materials/{asset_id}/usage-records",
        params={"segment_id": mine.id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    item = body["items"][0]
    assert item["clip_id"] == "clip_usage_5"
    assert item["task"]["id"] == "task_usage_4"
    assert item["resolution"] == "current"

    missing = usage_client.client.get(
        f"/api/production/materials/{asset_id}/usage-records",
        params={"segment_id": "segment-not-here"},
    )
    assert missing.status_code == 404