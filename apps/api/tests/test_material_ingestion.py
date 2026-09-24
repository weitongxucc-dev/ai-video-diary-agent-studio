from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import os
import shutil
import sqlite3
import subprocess
import threading
import time

import pytest

from app.material_ingestion import MaterialIngestionService
from app.production import (
    CreationPlan, CreationTask, CreationTaskScript, CreationTaskScriptSection,
    JobRun, ProductionRepository, ScriptGenerationRun, TaskEvent,
)


def make_repo(tmp_path: Path, duration_reader=None, scene_reader=None) -> tuple[ProductionRepository, Path]:
    library = tmp_path / "曜核AI IP一体机"
    library.mkdir(exist_ok=True)
    repo = ProductionRepository(
        tmp_path / "production.sqlite",
        library,
        duration_reader=duration_reader or (lambda path: 1.0),
        scene_reader=scene_reader or (lambda path, duration: []),
    )
    return repo, library


def seed_creation_task(repo: ProductionRepository, workspace_id: str, task_id: str, *, script_stage: bool = False) -> None:
    plan = CreationPlan(
        id=f"plan-{task_id}", workspace_id=workspace_id, goal="并发制作", name="测试任务",
        target_count=1, language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
        knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="测试", model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint=f"fingerprint-{task_id}",
    )
    task = CreationTask(
        id=task_id, workspace_id=workspace_id, plan_id=plan.id, name=plan.name,
        scenario="knowledge_explanation", stage="script" if script_stage else "topic",
        selected_topic_id="topic-one" if script_stage else None,
    )
    repo.confirm_creation_plan(plan, task)


def test_unbound_copy_stability_restart_and_changed_content(tmp_path: Path) -> None:
    duration = Mock(return_value=1.0)
    repo, library = make_repo(tmp_path, duration)
    source = library / "clip.mp4"
    source.write_bytes(b"copying")
    service = MaterialIngestionService(repo, stable_seconds=0)
    assert service.scan_once() == []
    assert repo.list_assets("one") == []
    repo.bind_material_ingestion("one")
    assert service.scan_once() == []
    assert repo.list_assets("one")[0].analysis_status == "blocked"
    source.write_bytes(b"finished")
    assert service.scan_once() == []
    analyzed = service.scan_once()
    assert len(analyzed) == 1 and analyzed[0].analysis_status == "ready"
    assert analyzed[0].review_status == "raw"
    assert repo.search_material_segments("one")
    assert duration.call_count == 1
    assert service.scan_once() == []

    restarted = ProductionRepository(tmp_path / "production.sqlite", library, duration_reader=duration, scene_reader=lambda path, seconds: [])
    assert MaterialIngestionService(restarted, stable_seconds=0).scan_once() == []
    assert duration.call_count == 1
    prior_stat = source.stat()
    time.sleep(0.01)
    source.write_bytes(b"changed!")  # Same size/name/mtime, but ctime exposes the rewrite.
    os.utime(source, ns=(prior_stat.st_atime_ns, prior_stat.st_mtime_ns))
    assert source.stat().st_ctime_ns != prior_stat.st_ctime_ns
    changed_service = MaterialIngestionService(restarted, stable_seconds=0)
    assert changed_service.scan_once() == []
    assert len(changed_service.scan_once()) == 1
    assert duration.call_count == 2
    assert restarted.list_assets("other") == []
    with pytest.raises(ValueError, match="已绑定另一工作区"):
        restarted.bind_material_ingestion("other")


def test_failure_is_isolated_and_retry_is_explicit(tmp_path: Path) -> None:
    def duration(path: Path) -> float:
        if path.name == "bad.mp4":
            raise ValueError("broken")
        return 1.0

    repo, library = make_repo(tmp_path, duration)
    (library / "good.mp4").write_bytes(b"good")
    (library / "bad.mp4").write_bytes(b"bad")
    repo.bind_material_ingestion("one")
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    service.scan_once()
    assets = {asset.file_name: asset for asset in repo.list_assets("one")}
    assert assets["good.mp4"].analysis_status == "ready"
    assert assets["bad.mp4"].analysis_status == "failed"
    assert service.scan_once() == []
    repo.retry_material_ingestion("one", assets["bad.mp4"].id)
    assert service.scan_once()[0].analysis_status == "failed"


def test_unchanged_ready_and_failed_files_do_not_rehash_each_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def duration(path: Path) -> float:
        if path.name == "failed.mp4":
            raise ValueError("broken")
        return 1.0

    repo, library = make_repo(tmp_path, duration)
    (library / "ready.mp4").write_bytes(b"ready")
    (library / "failed.mp4").write_bytes(b"failed")
    repo.bind_material_ingestion("one")
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    service.scan_once()
    original_digest = hashlib.file_digest
    digests = Mock(wraps=original_digest)
    monkeypatch.setattr("app.production.hashlib.file_digest", digests)
    for _ in range(4):
        assert service.scan_once() == []
    assert digests.call_count == 0


def test_existing_ingestion_observations_gain_lightweight_stat_columns(tmp_path: Path) -> None:
    library = tmp_path / "曜核AI IP一体机"
    library.mkdir()
    database = tmp_path / "production.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE material_ingestion_files (workspace_id TEXT NOT NULL, file_path TEXT NOT NULL, "
            "size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, observed_at REAL NOT NULL, "
            "completed_digest TEXT, attempted_digest TEXT, state TEXT NOT NULL, "
            "PRIMARY KEY (workspace_id, file_path))"
        )
        conn.execute(
            "INSERT INTO material_ingestion_files (workspace_id, file_path, size_bytes, mtime_ns, observed_at, state) VALUES ('one', 'clip.mp4', 5, 10, 20, 'ready')"
        )
    ProductionRepository(database, library)
    with sqlite3.connect(database) as conn:
        row = conn.execute("SELECT size_bytes, ctime_ns, inode FROM material_ingestion_files").fetchone()
    assert row == (5, 0, 0)


def test_copy_waits_for_stable_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    duration = Mock(return_value=1.0)
    repo, library = make_repo(tmp_path, duration)
    source = library / "copy.mp4"
    source.write_bytes(b"part")
    repo.bind_material_ingestion("one")
    service = MaterialIngestionService(repo, stable_seconds=30)
    service.scan_once()
    clock[0] = 1020
    assert service.scan_once() == []
    source.write_bytes(b"complete")
    clock[0] = 1030
    assert service.scan_once() == []
    clock[0] = 1050
    assert service.scan_once() == []
    assert duration.call_count == 0
    clock[0] = 1061
    assert service.scan_once()[0].analysis_status == "ready"


def test_binding_wakes_background_scan_without_waiting_for_interval(tmp_path: Path) -> None:
    analyzed = threading.Event()

    def duration(path: Path) -> float:
        analyzed.set()
        return 1.0

    repo, library = make_repo(tmp_path, duration)
    (library / "new.mp4").write_bytes(b"video")
    service = MaterialIngestionService(repo, scan_interval_seconds=3600, stable_seconds=0)
    service.start()
    try:
        repo.bind_material_ingestion("one")
        service.wake()
        deadline = time.monotonic() + 2
        while not repo.list_assets("one") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert repo.list_assets("one")
        service.wake()
        assert analyzed.wait(2)
    finally:
        service.stop()
    assert repo.list_assets("one")[0].analysis_status == "ready"


def test_missing_disk_and_symlink_escape(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    (library / "clip.mp4").write_bytes(b"video")
    outside = tmp_path / "private.mp4"
    outside.write_bytes(b"outside")
    (library / "escape.mp4").symlink_to(outside)
    repo.bind_material_ingestion("one")
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    service.scan_once()
    assert [asset.file_name for asset in repo.list_assets("one")] == ["clip.mp4"]
    library.rename(tmp_path / "disconnected")
    assert service.scan_once() == []
    assert "外接盘" in repo.list_assets("one")[0].analysis_message
    (tmp_path / "disconnected").rename(library)
    assert service.scan_once() == []
    assert repo.list_assets("one")[0].analysis_status == "ready"


def test_reanalysis_preserves_unmatched_manual_correction(tmp_path: Path) -> None:
    boundaries = [2.0]
    repo, library = make_repo(tmp_path, duration_reader=lambda path: 4.0, scene_reader=lambda path, duration: boundaries)
    source = library / "clip.mp4"
    source.write_bytes(b"video")
    repo.bind_material_ingestion("one")
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    asset = service.scan_once()[0]
    first = asset.segments[0]
    repo.update_segment("one", asset.id, first.id, description="人工事实", transcript="人工文字", review_status="raw", user_tags=["手工标签"])
    boundaries[:] = [1.5, 2.5]
    repo.analyze_asset("one", asset.id)
    persisted = repo.get_asset("one", asset.id)
    assert all(segment.id != first.id for segment in persisted.segments)
    assert "待核对" in persisted.analysis_message
    assert repo.list_unmatched_material_corrections("one", asset.id)[0]["description"] == "人工事实"


def test_asset_tags_are_atomic_workspace_scoped_and_searchable(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    (library / "one.mp4").write_bytes(b"one")
    (library / "two.mp4").write_bytes(b"two")
    repo.bind_material_ingestion("one")
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    service.scan_once()
    ids = [asset.id for asset in repo.list_assets("one")]
    repo.update_asset_tags("one", ids[0], ["客户现场"])
    assert repo.search_material_segments("one", tags=["客户现场"])
    assert repo.list_assets_page("one", limit=10, offset=0, query="客户现场")[1] == 1
    with pytest.raises(KeyError):
        repo.update_assets_tags_batch("one", [ids[0], "foreign"], add=["新品"], remove=[])
    assert repo.get_asset("one", ids[0]).user_tags == ["客户现场"]
    updated = repo.update_assets_tags_batch("one", ids, add=["新品"], remove=["客户现场"])
    assert all(asset.user_tags == ["新品"] for asset in updated)
    with pytest.raises(KeyError):
        repo.update_asset_tags("other", ids[0], ["越界"])


def test_manual_edit_during_analysis_survives_merge(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    (library / "clip.mp4").write_bytes(b"video")
    asset = repo.scan_library("one")[0]
    repo.analyze_asset("one", asset.id)
    segment_id = repo.get_asset("one", asset.id).segments[0].id

    def simultaneous_edit(path: Path) -> float:
        repo.update_segment("one", asset.id, segment_id, description="人工保留", transcript="", review_status="raw", user_tags=["保留"])
        return 1.0

    repo.duration_reader = simultaneous_edit
    repo.analyze_asset("one", asset.id)
    segment = repo.get_asset("one", asset.id).segments[0]
    assert segment.description == "人工保留"
    assert segment.user_tags == ["保留"]


def test_generated_video_uses_real_probe_and_scene_tools(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg tools unavailable")
    repo, library = make_repo(tmp_path)
    source = library / "generated.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:d=1.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        check=True,
    )
    real_repo = ProductionRepository(tmp_path / "real.sqlite", library)
    real_repo.bind_material_ingestion("one")
    service = MaterialIngestionService(real_repo, stable_seconds=0)
    service.scan_once()
    analyzed = service.scan_once()
    assert len(analyzed) == 1
    assert analyzed[0].analysis_status == "ready"
    assert analyzed[0].duration_seconds == pytest.approx(1.2, abs=0.2)
    assert analyzed[0].segments


def test_concurrent_job_creation_reuses_same_persisted_idempotency_key(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    seed_creation_task(repo, "one", "task-one")
    gate = threading.Barrier(12)

    def create(index: int) -> JobRun:
        gate.wait()
        return repo.create_job_run(JobRun(
            id=f"job-{index}", workspace_id="one", task_id="task-one",
            kind="creation_task_execution", idempotency_key="same-request", input_digest="same-input",
        ))

    with ThreadPoolExecutor(max_workers=12) as pool:
        created = list(pool.map(create, range(12)))
    assert len({job.id for job in created}) == 1
    assert len(repo.list_job_runs("one", "task-one")) == 1
    with pytest.raises(ValueError, match="不同的任务输入"):
        repo.create_job_run(JobRun(
            id="different-input", workspace_id="one", task_id="task-one",
            kind="creation_task_execution", idempotency_key="same-request", input_digest="changed",
        ))
    with pytest.raises(ValueError, match="ID 已存在"):
        repo.create_job_run(JobRun(
            id=created[0].id, workspace_id="one", task_id="task-one",
            kind="creation_task_execution", idempotency_key="different-request", input_digest="different",
        ))
    seed_creation_task(repo, "two", "task-two")
    other = repo.create_job_run(JobRun(
        id="other-workspace-job", workspace_id="two", task_id="task-two",
        kind="creation_task_execution", idempotency_key="same-request", input_digest="same-input",
    ))
    assert other.id != created[0].id


def test_concurrent_identical_task_event_returns_one_record(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    seed_creation_task(repo, "one", "task-one")
    job = repo.create_job_run(JobRun(
        id="job-one", workspace_id="one", task_id="task-one", kind="creation_task_execution",
        idempotency_key="request-one", input_digest="input-one",
    ))
    event = TaskEvent(
        id="start-one", workspace_id="one", task_id="task-one", job_id=job.id,
        event_type="creation_started", payload={"job_id": job.id}, created_at=job.created_at,
    )
    gate = threading.Barrier(12)

    def append(_: int) -> TaskEvent:
        gate.wait()
        return repo.append_task_event(event)

    with ThreadPoolExecutor(max_workers=12) as pool:
        events = list(pool.map(append, range(12)))
    assert all(item == event for item in events)
    assert repo.list_task_events("one", "task-one") == [event]
    with pytest.raises(ValueError, match="领域字段不一致"):
        repo.append_task_event(replace(event, payload={"job_id": "different"}))
    seed_creation_task(repo, "two", "task-two")
    with pytest.raises(ValueError, match="领域字段不一致"):
        repo.append_task_event(replace(event, workspace_id="two", task_id="task-two", job_id=None))


def test_assistant_draft_update_uses_current_run_and_keeps_confirmation_boundary(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    seed_creation_task(repo, "one", "task-one", script_stage=True)
    run = ScriptGenerationRun(
        id="run-one", workspace_id="one", task_id="task-one", plan_id="plan-task-one",
        topic_id="topic-one", status="running", route_snapshot={"source": "pi"},
        prompt_schema_version="assistant-v1", input_references=[{"reference_id": "ref-one"}],
    )
    repo.start_script_generation(run)
    initial = CreationTaskScript(
        id="draft-one", workspace_id="one", task_id="task-one", run_id=run.id,
        topic_id="topic-one", title="初稿", language="zh-CN",
        sections=[CreationTaskScriptSection("section-one", "开头", "初稿内容", "真实画面", ["ref-one"])],
    )
    repo.complete_script_generation(run.id, initial)
    revised_sections = [CreationTaskScriptSection("section-one", "开头", "用户改过的内容", "产品画面", ["ref-one"])]
    revised = repo.update_assistant_script_draft("one", "task-one", initial.id, title="新版草稿", sections=revised_sections)
    assert (revised.id, revised.run_id, revised.topic_id, revised.selection_status) == (initial.id, run.id, "topic-one", "draft")
    assert revised.sections == revised_sections
    assert repo.get_creation_task("one", "task-one").confirmed_script_id is None
    assert repo.get_creation_task("one", "task-one").stage_status == "waiting_for_user"
    restarted = ProductionRepository(tmp_path / "production.sqlite", repo.video_library_path)
    assert restarted.get_creation_task_script("one", initial.id).title == "新版草稿"
    with pytest.raises(KeyError):
        restarted.update_assistant_script_draft("other", "task-one", initial.id, title="越界", sections=revised_sections)
    with pytest.raises(ValueError, match="模型输入之外"):
        restarted.update_assistant_script_draft(
            "one", "task-one", initial.id, title="伪来源",
            sections=[replace(revised_sections[0], evidence_reference_ids=["unknown"])],
        )
    restarted.confirm_creation_task_script("one", "task-one", initial.id, title="用户确认", sections=revised_sections)
    with pytest.raises(ValueError, match="不允许修改"):
        restarted.update_assistant_script_draft("one", "task-one", initial.id, title="越过确认", sections=revised_sections)
