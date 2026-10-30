"""WIL-98：首页一次取齐真实聚合，且完全沿用既有读过滤。

只回答三个问题：
1. 身份——聚合数字跟既有可见性判据走：owner 身份读不到别人的私有素材，
   成员身份只读得到共享；响应里不出现别人的文件名或路径。
2. 口径——``days`` 只改日趋势跨度；趋势按真实接收日分桶，缺失的日子补 0。
3. 诚实未知——整机匿名计数跨工作区聚合但不带归属与路径；容量读不到就说未知；
   素材没有行业字段就如实声明未知，不编造分布。

全部使用 tmp_path 合成库与真实成员令牌，不连接真实用户库、不读真实输出目录。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fastapi.testclient import TestClient

import app.main as main_module
from app.local_members import LocalMemberService
from app.production import MaterialAsset, ProductionRepository
from app.source_repository import SourceRepository

client = TestClient(main_module.app)
WORKSPACE = "ws-98-home"


@pytest.fixture(autouse=True)
def home_metadata_guard(request, monkeypatch, tmp_path):
    if request.node.originalname not in {
        "test_home_counts_follow_the_existing_visibility_predicate",
        "test_home_trend_spans_the_requested_days_only",
    }:
        return None
    import sqlite3
    import subprocess
    import wave
    from pathlib import Path
    import app.home_aggregation as aggregation
    from app.auth import SupabaseAuthContext
    from app.config import Settings
    from app.device_runtime import DeviceRuntime
    from app.device_scheduler import DeviceScheduler
    calls, capacity_paths = [], []

    def forbidden(*args, **kwargs):
        calls.append("execution")
        pytest.fail("home metadata forbids runtime, grants, Pi and media execution")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(wave, "open", forbidden)
    for name in ("start", "run_once", "wake"):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, "pick_and_grant", forbidden)
    for name in ("_creation_runtime", "run_task_turn", "run_plan_turn", "start_assistant_continuation_driver"):
        monkeypatch.setattr(main_module, name, forbidden)
    monkeypatch.setattr(main_module.montage_renderer, "render", forbidden)
    monkeypatch.setattr(main_module.narration_generator, "generate_for_creation_task", forbidden)
    settings = Settings(database_path=str(tmp_path / "studio.sqlite3"),
        video_inbox_path=str(tmp_path / "library"), video_output_path=str(tmp_path / "output"),
        model_root_path=str(tmp_path / "models"), temporary_root_path=str(tmp_path / "temporary"),
        draft_output_path=str(tmp_path / "drafts"), jianying_draft_root_path=str(tmp_path / "jianying"),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "auth_context", SupabaseAuthContext(settings))
    monkeypatch.setattr(main_module, "device_runtime", None)
    actual_disk_usage = aggregation.psutil.disk_usage

    def temporary_capacity(path):
        resolved = Path(path).resolve()
        assert resolved == (tmp_path / "library").resolve()
        capacity_paths.append(resolved)
        return actual_disk_usage(path)  # Real filesystem metadata; no capacity mock.

    monkeypatch.setattr(aggregation.psutil, "disk_usage", temporary_capacity)

    def verify_boundaries():
        assert calls == []
        assert capacity_paths

    request.addfinalizer(verify_boundaries)

    def snapshot():
        databases = {}
        for path in sorted(tmp_path.rglob("*")):
            if not path.is_file() or path.suffix not in {".sqlite3", ".sqlite", ".db"}:
                continue
            with sqlite3.connect(path) as connection:
                tables = [row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                databases[str(path.relative_to(tmp_path))] = {
                    name: sorted((tuple(row) for row in connection.execute(
                        'SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr) for name in tables}
        tree = {str(item.relative_to(tmp_path)): item.read_bytes() if item.is_file() else None
                for item in tmp_path.rglob('*')}
        return databases, tree

    return snapshot


def _setup(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=library,
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [5.0, 17.0],
    )
    service = LocalMemberService(tmp_path / "agent.sqlite3")
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "source_repository", SourceRepository(tmp_path / "sources.sqlite3"))
    monkeypatch.setattr(main_module, "local_members_service", service)
    monkeypatch.setattr(
        main_module, "current_workspace_id", lambda request: request.headers.get("x-test-workspace", WORKSPACE)
    )
    owner, owner_token = service.join(service.create_invite_code(), "所有者")
    service.set_role(owner.member_id, "owner")
    member, member_token = service.join(service.create_invite_code(), "小王")
    return production, library, {
        "owner_token": owner_token,
        "member_token": member_token,
        "owner_id": owner.member_id,
        "member_id": member.member_id,
    }


def _save_asset(production, asset_id, file_name, owner_user_id, visibility, created_at=None):
    production._save_asset(MaterialAsset(
        id=asset_id, workspace_id=WORKSPACE, file_name=file_name, path=f"ws/{file_name}",
        size_bytes=10, modified_at=datetime(2026, 1, 1),
        visibility=visibility, owner_user_id=owner_user_id,
        **({"created_at": created_at} if created_at is not None else {}),
    ))


def _counts(body) -> dict[str, int]:
    return {item["key"]: item["value"] for item in body["counts"]}


def _save_task(production, task_id, name, owner_user_id, visibility, status="created", **overrides):
    from app.creation.plan_contracts import CreationPlan
    from app.creation.contracts import CreationTask

    plan = CreationPlan(
        id=f"plan-{task_id}", workspace_id=WORKSPACE, goal="做一条真实短视频", name=f"{name}的计划",
        target_count=1, language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
        knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="", model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint="fingerprint",
    )
    task = CreationTask(
        id=task_id, workspace_id=WORKSPACE, plan_id=plan.id, name=name,
        scenario="talking_head", status=status, visibility=visibility,
        **overrides,
    )
    # 归属只认服务端核过的身份：直接往 dataclass 塞 owner_user_id 不算数。
    production.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner_user_id)
    return task


def _home(token: str, days: int = 7):
    response = client.get(f"/api/production/home?days={days}", headers={"X-Local-Member-Token": token})
    assert response.status_code == 200, response.text
    return response


def test_home_counts_follow_the_existing_visibility_predicate(tmp_path, monkeypatch, home_metadata_guard):
    production, library, party = _setup(tmp_path, monkeypatch)
    _save_asset(production, "asset-owner-1", "所有者的私有素材.mp4", party["owner_id"], "private")
    _save_asset(production, "asset-owner-2", "所有者的另一条私有.mp4", party["owner_id"], "private")
    _save_asset(production, "asset-member", "小王的私有素材.mp4", party["member_id"], "private")
    _save_asset(production, "asset-shared", "共享素材.mp4", party["member_id"], "shared")

    before_reads = home_metadata_guard()
    assert set(before_reads[0]) == {"studio.sqlite3", "agent.sqlite3", "sources.sqlite3"}
    owner_view = _home(party["owner_token"]).json()
    member_view = _home(party["member_token"]).json()

    # 各自只读得到自己的私有加共享：owner 3 条（自己 2 + 共享 1），
    # 小王 2 条（自己 1 + 共享 1）。owner 的私有没有泄进小王那一侧。
    assert _counts(owner_view)["visible_assets"] == 3
    assert _counts(member_view)["visible_assets"] == 2

    # 别人的文件名和本机目录路径都不进响应。
    raw = str(member_view)
    assert "所有者的私有素材" not in raw
    assert str(library) not in raw

    # 无效令牌不被当成「没带令牌所以按 owner 看全部」。
    assert client.get(
        "/api/production/home", headers={"X-Local-Member-Token": "not-a-real-token"}
    ).status_code == 401
    assert home_metadata_guard() == before_reads


def test_home_trend_spans_the_requested_days_only(tmp_path, monkeypatch, home_metadata_guard):
    production, _library, party = _setup(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    _save_asset(production, "asset-today", "今天.mp4", party["owner_id"], "private", created_at=now)
    _save_asset(production, "asset-old", "三天前.mp4", party["owner_id"], "private", created_at=now - timedelta(days=3))

    before_reads = home_metadata_guard()
    assert set(before_reads[0]) == {"studio.sqlite3", "agent.sqlite3", "sources.sqlite3"}
    week = _home(party["owner_token"], days=7).json()
    assert week["days"] == 7
    assert len(week["trend"]["points"]) == 7
    # 趋势是当前身份可读素材的接收趋势，不能被当成整机匿名趋势。
    assert week["trend"]["meta"]["scope"] == "caller_visible"
    assert "device_anonymous" not in week["trend"]["meta"]["source"]
    assert [point["date"] for point in week["trend"]["points"]][-1] == now.date().isoformat()
    # 缺失的日子补 0：有数的那天有素材，没有的那天是 0 而不是断点。
    assert week["trend"]["points"][-1]["count"] == 1
    assert week["trend"]["points"][-4]["count"] == 1
    assert week["trend"]["points"][0]["count"] == 0

    month = _home(party["owner_token"], days=30).json()
    assert len(month["trend"]["points"]) == 30
    assert sum(point["count"] for point in month["trend"]["points"]) == 2
    # 跨度之外的口径不变：可见素材数与 7 天时一致。
    assert _counts(month)["visible_assets"] == _counts(week)["visible_assets"]

    assert client.get(
        "/api/production/home?days=14", headers={"X-Local-Member-Token": party["owner_token"]}
    ).status_code == 422
    assert home_metadata_guard() == before_reads


def test_device_counts_separate_running_and_queued_from_history(tmp_path, monkeypatch):
    production, library, party = _setup(tmp_path, monkeypatch)
    # 匿名聚合刻意不带 workspace：两个工作区的制作作业与接收状态一起算。
    with production._connect() as conn:
        conn.executemany(
            "INSERT INTO job_runs (id, workspace_id, task_id, kind, idempotency_key, input_digest, "
            "status, progress, attempt, output_refs_json, created_at, updated_at) "
            "VALUES (?, ?, ?, 'creation_task_execution', ?, 'digest', ?, 0, 0, '[]', '2026-01-01', '2026-01-01')",
            [
                ("job-running", WORKSPACE, "task-a", "key-a", "running"),
                ("job-queued", "ws-其他工作区", "task-b", "key-b", "queued"),
                ("job-done", WORKSPACE, "task-c", "key-c", "succeeded"),
            ],
        )
        conn.executemany(
            "INSERT INTO material_ingestion_files "
            "(workspace_id, file_path, size_bytes, mtime_ns, observed_at, state) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (WORKSPACE, "私密目录里的一个文件.mp4", 10, 1, 1.0, "ready"),
                ("ws-其他工作区", "别处的一个文件.mp4", 10, 1, 1.0, "observing"),
            ],
        )

    body = _home(party["owner_token"]).json()
    device = body["device_queue"]
    assert device["meta"]["scope"] == "device_anonymous"
    # 记录数含历史：已完成和已接收的都算数。
    assert device["records_total"] == 5
    # 在跑与排队只有真正未完成的那几条，已成功/已接收的不算队列长度。
    assert device["running"] == 1
    assert device["queued"] == 2
    raw = str(body)
    assert "私密目录里的一个文件" not in raw
    assert "别处的一个文件" not in raw

    # 本工作区可读这个目录时给真实容量，但不给目录路径。
    assert body["storage"]["capacity"]["total_bytes"] > 0
    assert str(library) not in raw

    unknown = {item["key"] for item in body["unknown"]}
    # 素材没有行业字段：如实声明未知，不编造分布。
    assert "materials.by_industry" in unknown
    # 分析没有运行/排队载体：如实声明未知。
    assert "device_queue.analysis" in unknown


def test_device_queue_is_unknown_not_zero_when_the_state_tables_are_unreadable(tmp_path, monkeypatch):
    production, _library, party = _setup(tmp_path, monkeypatch)
    # 状态表读不到时（老库没这张表、磁盘异常等），如实说未知。
    with production._connect() as conn:
        conn.execute("DROP TABLE job_runs")

    body = _home(party["owner_token"]).json()
    device = body["device_queue"]
    # 读不到就是未知，不能是全 0——0 会被页面读成「现在很空闲」。
    assert device["counts"] == {}
    assert device["running"] is None
    assert device["queued"] is None
    assert device["records_total"] is None
    assert "device_queue" in {item["key"] for item in body["unknown"]}


def test_home_entries_follow_task_identity_and_real_task_state(tmp_path, monkeypatch):
    production, _library, party = _setup(tmp_path, monkeypatch)
    _save_task(production, "task-mine-open", "我的进行中任务", party["owner_id"], "private", status="running")
    _save_task(production, "task-mine-done", "我的已完成任务", party["owner_id"], "private", status="completed")
    _save_task(production, "task-shared-open", "共享的进行中任务", party["member_id"], "shared", status="created")
    _save_task(
        production, "task-other-private", "别人的私有任务", party["member_id"], "private", status="running",
    )
    _save_task(
        production, "task-mine-todo", "我的待办任务", party["owner_id"], "private", status="failed",
        stage_status="failed", stage_reason="narration_generation_failed", stage_repair_action="压缩文案时长",
    )
    _save_task(
        production, "task-shared-work", "共享的成片任务", party["member_id"], "shared", status="completed",
        output_ready=True,
    )

    body = _home(party["owner_token"]).json()
    entries = body["entries"]

    # 队列只放还没做完的任务：已完成和已失败的都不算在跑或排队。
    queue_ids = [item["task_id"] for item in entries["queue"]]
    assert set(queue_ids) == {"task-mine-open", "task-shared-open"}
    assert "task-mine-done" not in queue_ids
    assert "task-mine-todo" not in queue_ids
    # 别人的私有任务既不在队列，也不在任何一块里出现。
    assert "task-other-private" not in str(entries)
    assert "别人的私有任务" not in str(entries)

    # 待办来自真实的 stage_reason，成果来自真实的 output_ready。
    assert [item["task_id"] for item in entries["todos"]] == ["task-mine-todo"]
    assert entries["todos"][0]["repair_action"] == "压缩文案时长"
    assert [item["task_id"] for item in entries["works"]] == ["task-shared-work"]

    # 每条都带真实 task_id 和真实标题，页面可以直接跳转。
    queued = next(item for item in entries["queue"] if item["task_id"] == "task-mine-open")
    assert queued["title"] == "我的进行中任务"
    assert queued["status"] == "running"


def test_storage_capacity_is_unknown_when_the_directory_is_another_workspaces(tmp_path, monkeypatch):
    production, library, party = _setup(tmp_path, monkeypatch)
    production.bind_material_ingestion("ws-别的工作区", expected_directory=str(library))

    body = _home(party["owner_token"]).json()
    storage = body["storage"]
    assert storage["bound_to_another_workspace"] is True
    assert storage["capacity"] is None
    assert "storage.capacity" in {item["key"] for item in body["unknown"]}
    assert str(library) not in str(body)
