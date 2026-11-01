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
from app.local_members import LocalMemberService
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
    uploader_user_id: str | None = None,
) -> str:
    """登记一个真实落盘的素材并分析出片段，供使用记录指向。

    上传者身份写在复制完成确认这一步：确认才创建入库记录，沿既有创建语义把
    已验证身份绑定为这条素材的上传者，不在素材建好之后直接改库里的 owner 字段。
    """
    path = library / name
    path.write_bytes(b"material-bytes")
    repo.material_source_files.bind_material_ingestion(
        workspace_id, expected_directory=str(library),
    )
    repo.material_source_files.confirm_material_copy_complete(
        workspace_id, relative_path=name,
        expected_revision=repo.material_source_files.material_copy_revision(path.stat()),
        confirmation_source="manual",
        verified_owner_user_id=uploader_user_id,
    )
    asset_id = next(asset.id for asset in repo.material_intake.scan_library(workspace_id) if asset.file_name == name)
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
    repo.material_assets.record_segment_usage(WORKSPACE, asset_id, mine.id, plan_id="plan_usage_1", clip_id="clip_usage_1")

    records = repo.material_usage_records.list_segment_usage_records(WORKSPACE, asset_id, mine.id)

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
    repo.material_assets.record_segment_usage(WORKSPACE, asset_id, theirs.id, plan_id="plan_usage_2", clip_id="clip_usage_3")
    repo.material_assets.record_segment_usage(OTHER_WORKSPACE, foreign, foreign_segment.id,
                              plan_id="plan_usage_foreign", clip_id="clip_usage_foreign")

    assert repo.material_usage_records.list_segment_usage_records(WORKSPACE, asset_id, mine.id) == []
    other_workspace = repo.material_usage_records.list_segment_usage_records(OTHER_WORKSPACE, foreign, foreign_segment.id)
    assert [record["task"]["id"] for record in other_workspace] == ["task_usage_foreign"]
    with pytest.raises(KeyError):
        repo.material_usage_records.list_segment_usage_records(OTHER_WORKSPACE, foreign, mine.id)


def test_reading_again_changes_no_counter_and_writes_nothing(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    asset_id = register(repo, library, "回查素材戊.mp4")
    segment = repo.get_asset(WORKSPACE, asset_id).segments[0]
    write_task(repo, "task_usage_3", current_version_id=None)
    write_plan(repo, "plan_usage_3", "task_usage_3", [clip("clip_usage_4", asset_id, segment.id)])
    repo.material_assets.record_segment_usage(WORKSPACE, asset_id, segment.id, plan_id="plan_usage_3", clip_id="clip_usage_4")
    before = repo.get_asset(WORKSPACE, asset_id)

    first = repo.material_usage_records.list_segment_usage_records(WORKSPACE, asset_id, segment.id)
    second = repo.material_usage_records.list_segment_usage_records(WORKSPACE, asset_id, segment.id)

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
    # 真实成员服务与真实令牌：私有素材只对上传者本人可读，匿名请求读不到。
    service = LocalMemberService(tmp_path / "members.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", service)
    uploader, uploader_token = service.join(service.create_invite_code(), "上传者")
    return SimpleNamespace(
        client=TestClient(main_module.app),
        repository=repo,
        library=library,
        uploader_user_id=uploader.member_id,
        uploader_token=uploader_token,
    )


def _uploader_headers(usage_client) -> dict[str, str]:
    return {"X-Local-Member-Token": usage_client.uploader_token}


def test_route_returns_the_records_for_the_named_segment_only(usage_client) -> None:
    repo, library = usage_client.repository, usage_client.library
    asset_id = register(
        repo, library, "接口回查甲.mp4", uploader_user_id=usage_client.uploader_user_id
    )
    mine, theirs = repo.get_asset(WORKSPACE, asset_id).segments[:2]
    write_task(repo, "task_usage_4", current_version_id="timeline_current_4")
    write_plan(repo, "plan_usage_4", "task_usage_4", [
        clip("clip_usage_5", asset_id, mine.id),
        clip("clip_usage_6", asset_id, theirs.id),
    ])
    repo.material_assets.record_segment_usage(WORKSPACE, asset_id, mine.id, plan_id="plan_usage_4", clip_id="clip_usage_5")
    repo.material_assets.record_segment_usage(WORKSPACE, asset_id, theirs.id,
                              plan_id="plan_usage_4", clip_id="clip_usage_6")

    response = usage_client.client.get(
        f"/api/production/materials/{asset_id}/usage-records",
        params={"segment_id": mine.id},
        headers=_uploader_headers(usage_client),
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
        headers=_uploader_headers(usage_client),
    )
    assert missing.status_code == 404


def test_metadata_usage_writer_and_task_traceback_without_analysis(tmp_path, monkeypatch):
    from datetime import timedelta
    from app.creation.render_stage_use_case import RenderStageUseCase
    from app.materials import assets as asset_storage
    from app.materials.contracts import MaterialAsset, MaterialSegment

    repo, library = make_repo(tmp_path)
    when = datetime(2026, 11, 1, tzinfo=timezone.utc)
    source = library / "declared.mp4"
    source.write_bytes(b"opaque metadata declaration, never decoded")
    asset = MaterialAsset("declared-asset", WORKSPACE, source.name, str(source), source.stat().st_size,
        when, duration_seconds=4, analysis_status="ready", segments=[
            MaterialSegment("used-segment", "declared-asset", 0, 2),
            MaterialSegment("unused-segment", "declared-asset", 2, 4),
        ])
    repo.material_assets.save_asset(asset)
    write_task(repo, "metadata-task", current_version_id="current-head")
    entry = clip("render-clip", asset.id, "used-segment", role="a_roll")
    write_plan(repo, "metadata-plan", "metadata-task", [entry])
    monkeypatch.setattr(asset_storage, "utc_now", lambda: when)
    def no_media(*args, **kwargs):
        pytest.fail("usage metadata must not execute media")
    render = RenderStageUseCase(repo, None, None, no_media, None)
    plan = SimpleNamespace(id="metadata-plan", clips=[
        SimpleNamespace(**entry, person_render_id=None),
        SimpleNamespace(person_render_id="excluded-person-render"),
    ])
    render.record_usage(WORKSPACE, plan)
    render.record_usage(WORKSPACE, plan)
    assert repo.material_assets.get_asset(WORKSPACE, asset.id).segments[0].usage_count == 1

    write_plan(repo, "orphan-task-plan", "missing-task", [])
    monkeypatch.setattr(asset_storage, "utc_now", lambda: when + timedelta(seconds=1))
    repo.material_assets.record_segment_usage(WORKSPACE, asset.id, "used-segment",
        plan_id="orphan-task-plan", clip_id="missing-clip")
    monkeypatch.setattr(asset_storage, "utc_now", lambda: when + timedelta(seconds=2))
    repo.material_assets.record_segment_usage(WORKSPACE, asset.id, "used-segment",
        plan_id="missing-plan", clip_id="orphan-clip")
    foreign = MaterialAsset("foreign-asset", OTHER_WORKSPACE, "foreign.mp4", str(library / "foreign.mp4"),
        1, when, segments=[MaterialSegment("foreign-segment", "foreign-asset", 0, 1)])
    repo.material_assets.save_asset(foreign)
    repo.material_assets.record_segment_usage(OTHER_WORKSPACE, foreign.id, "foreign-segment",
        plan_id="metadata-plan", clip_id="render-clip")
    before = repo.material_assets.get_asset(WORKSPACE, asset.id)
    assert before.segments[0].usage_count == 3

    original_connect = repo._connect
    traces = []
    connections = []
    def read_only_connect():
        conn = original_connect()
        number = len(connections)
        connections.append(number)
        conn.execute("PRAGMA query_only=ON")
        conn.set_trace_callback(lambda sql: traces.append((number, " ".join(sql.split()))))
        return conn
    monkeypatch.setattr(repo, "_connect", read_only_connect)
    reader = repo.material_usage_records
    records = reader.list_segment_usage_records(WORKSPACE, asset.id, "used-segment")
    joined_reads = [(number, sql) for number, sql in traces
        if any("FROM " + table in sql for table in (
            "material_segment_usage", "creation_task_montage_plans", "creation_tasks"))]
    assert len(joined_reads) == 3 and len({number for number, _sql in joined_reads}) == 1
    assert [item["plan_id"] for item in records] == ["missing-plan", "orphan-task-plan", "metadata-plan"]
    assert [item["used_at"] for item in records] == [when + timedelta(seconds=2), when + timedelta(seconds=1), when]
    assert records[0]["task"] is None and records[0]["role"] is None
    assert records[0]["resolution"] == "unknown" and records[0]["timeline_version_id"] is None
    assert records[1]["task"] == {"id": "missing-task", "name": None, "stage": None,
        "status": None, "current_timeline_version_id": None}
    assert records[1]["role"] is None and records[1]["manually_selected"] is None
    current = records[2]
    assert current["task"] == {"id": "metadata-task", "name": "使用记录测试任务", "stage": "review",
        "status": "waiting_for_user", "current_timeline_version_id": "current-head"}
    assert current["timeline_version_id"] == "current-head" and current["resolution"] == "current"
    assert current["role"] == "a_roll" and current["manually_selected"] is True
    assert reader.list_segment_usage_records(WORKSPACE, asset.id, "unused-segment") == []
    assert reader.list_segment_usage_records(OTHER_WORKSPACE, foreign.id, "foreign-segment")[0]["task"] is None
    with pytest.raises(KeyError):
        reader.list_segment_usage_records(OTHER_WORKSPACE, asset.id, "used-segment")
    with pytest.raises(KeyError):
        reader.list_segment_usage_records(WORKSPACE, asset.id, "not-a-segment")
    assert reader.list_segment_usage_records(WORKSPACE, asset.id, "used-segment") == records
    after = repo.material_assets.get_asset(WORKSPACE, asset.id)
    assert (after.segments[0].usage_count, after.segments[0].last_used_at, after.updated_at) == (
        before.segments[0].usage_count, before.segments[0].last_used_at, before.updated_at)
