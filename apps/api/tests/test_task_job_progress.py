"""任务作业进度：只读已落库的作业行，估时无样本就写未知。

只用隔离临时库，不启调度、不跑模型。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import CreationPlanVoiceReference, CreationTask, JobRun

WORKSPACE = "workspace-job"


def make_repo(tmp_path: Path) -> ProductionRepository:
    library = tmp_path / "曜核AI IP一体机"
    library.mkdir(exist_ok=True)
    return ProductionRepository(
        tmp_path / "production.sqlite",
        library,
        duration_reader=lambda path: 1.0,
        scene_reader=lambda path, duration: [],
    )


def seed_task(repo: ProductionRepository, workspace_id: str, task_id: str, *, owner_user_id: str) -> None:
    plan = CreationPlan(
        id=f"plan-{task_id}", workspace_id=workspace_id, goal="测试进度", name="进度任务",
        target_count=1, language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
        knowledge_references=[], source_references=[],
        selected_voice=CreationPlanVoiceReference(
            profile_id="voice_1", version="v1", person_profile_id="person_1", person_version="v1",
            person_authorization_id="auth_1", person_authorization_version="v1",
            authorization_id="voice_auth_1", authorization_version="v1",
            language="zh-CN", model_id="test"),
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="测试", model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint=f"fingerprint-{task_id}",
    )
    task = CreationTask(
        id=task_id, workspace_id=workspace_id, plan_id=plan.id, name=plan.name,
        scenario="knowledge_explanation", stage="montage", owner_user_id=owner_user_id,
    )
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner_user_id)
    assert repo.get_creation_task(workspace_id, task_id).owner_user_id == owner_user_id


@pytest.fixture
def client(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    from app.local_members import LocalMemberService
    members = LocalMemberService(repo.database_path)
    member, token = members.join(members.create_invite_code(), '进度HTTP隔离成员')
    monkeypatch.setattr(main_module, 'auth_context', SimpleNamespace(enabled=False))
    monkeypatch.setattr(main_module, 'local_members_service', members)
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", WORKSPACE),
    )
    monkeypatch.setattr(main_module, '_creation_runtime', lambda: pytest.fail('job progress must not start runtime'))
    client = TestClient(main_module.app, headers={'X-Local-Member-Token':token})
    client.member_id = member.member_id
    client.repo = repo  # type: ignore[attr-defined]
    return client


def test_a_running_job_reports_real_progress_elapsed_and_updated_time(client):
    repo = client.repo
    seed_task(repo, WORKSPACE, "task-job-1", owner_user_id=client.member_id)
    repo.create_job_run(JobRun(
        id="job-running", workspace_id=WORKSPACE, task_id="task-job-1",
        kind="creation_task_montage_render", idempotency_key="render:1", input_digest="d1",
    ))
    repo.claim_job_run(WORKSPACE, "job-running")
    repo.update_job_run_progress(WORKSPACE, "job-running", 40)

    response = client.get("/api/creation-tasks/task-job-1/job-runs")

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    current = body["current"]
    assert (current["id"], current["status"], current["progress"]) == ("job-running", "running", 40)
    assert current["started_at"] is not None
    assert current["updated_at"] is not None
    # 运行中的已耗时按服务端此刻实时算，不会停在最近一次更新时间上。
    assert current["elapsed_seconds"] is not None and current["elapsed_seconds"] >= 0
    # 本次已耗时不是对下一次的可信预测样本：两项估时都明确未知。
    assert current["processing_estimate_seconds"] is None
    assert current["estimate_basis"] is None
    # 排队等待没有落库依据时不编造。
    assert current["wait_estimate_seconds"] is None


def test_a_finished_failed_job_keeps_its_failure_reason_across_reopen(client):
    repo = client.repo
    seed_task(repo, WORKSPACE, "task-job-2", owner_user_id=client.member_id)
    repo.create_job_run(JobRun(
        id="job-failed", workspace_id=WORKSPACE, task_id="task-job-2",
        kind="creation_task_montage_render", idempotency_key="render:2", input_digest="d2",
    ))
    repo.claim_job_run(WORKSPACE, "job-failed")
    repo.finish_job_run(
        WORKSPACE, "job-failed", status="failed",
        failure_reason="输出磁盘写入失败", impact="本次没有成片", repair_action="检查磁盘空间后重新生成",
    )

    first = client.get("/api/creation-tasks/task-job-2/job-runs").json()
    assert first["current"]["status"] == "failed"
    assert first["current"]["failure_reason"] == "输出磁盘写入失败"
    assert first["current"]["impact"] == "本次没有成片"
    assert first["current"]["repair_action"] == "检查磁盘空间后重新生成"
    assert first["current"]["finished_at"] is not None
    # 终态已耗时用结束时间减开始时间。
    assert first["current"]["elapsed_seconds"] is not None and first["current"]["elapsed_seconds"] >= 0
    # 这一次的耗时同样不是可信预测样本，估时仍为未知。
    assert first["current"]["processing_estimate_seconds"] is None
    assert first["current"]["estimate_basis"] is None

    # 重开应用：重新打开同一份库读到的是同一批作业，失败原因和耗时仍在。
    reopened = make_repo(Path(repo.database_path).parent)
    second = client.get("/api/creation-tasks/task-job-2/job-runs")
    assert second.status_code == 200
    again = second.json()["current"]
    assert again["id"] == "job-failed"
    assert again["failure_reason"] == "输出磁盘写入失败"
    assert again["elapsed_seconds"] == first["current"]["elapsed_seconds"]
    assert reopened.list_job_runs(WORKSPACE, "task-job-2")[0].failure_reason == "输出磁盘写入失败"


def test_job_progress_does_not_cross_workspaces(client):
    repo = client.repo
    seed_task(repo, WORKSPACE, "task-job-3", owner_user_id=client.member_id)
    repo.create_job_run(JobRun(
        id="job-private", workspace_id=WORKSPACE, task_id="task-job-3",
        kind="creation_task_montage_render", idempotency_key="render:3", input_digest="d3",
    ))

    mine = client.get("/api/creation-tasks/task-job-3/job-runs").json()
    theirs = client.get(
        "/api/creation-tasks/task-job-3/job-runs",
        headers={"x-test-workspace": "workspace-other"},
    )

    assert mine["total_count"] == 1
    assert theirs.status_code == 404


def test_a_task_without_jobs_reports_unknown_rather_than_a_fake_progress(client):
    client.repo  # 任务存在但还没有任何作业
    seed_task(client.repo, WORKSPACE, "task-job-4")

    response = client.get("/api/creation-tasks/task-job-4/job-runs")

    assert response.status_code == 200
    body = response.json()
    assert body["current"] is None
    assert body["items"] == []
    assert body["total_count"] == 0

def test_an_old_interrupted_job_does_not_hide_the_later_result(client):
    repo = client.repo
    seed_task(repo, WORKSPACE, "task-job-5", owner_user_id=client.member_id)
    # 一次被中断的旧作业，后面重试成功。
    repo.create_job_run(JobRun(
        id="job-old", workspace_id=WORKSPACE, task_id="task-job-5",
        kind="creation_task_montage_render", idempotency_key="render:old", input_digest="d-old",
    ))
    repo.claim_job_run(WORKSPACE, "job-old")
    repo.finish_job_run(WORKSPACE, "job-old", status="interrupted",
                        failure_reason="应用退出时中断", impact="本次没有成片", repair_action="重新生成")
    repo.create_job_run(JobRun(
        id="job-new", workspace_id=WORKSPACE, task_id="task-job-5",
        kind="creation_task_montage_render", idempotency_key="render:new", input_digest="d-new",
    ))
    repo.claim_job_run(WORKSPACE, "job-new")
    repo.finish_job_run(WORKSPACE, "job-new", status="succeeded")

    body = client.get("/api/creation-tasks/task-job-5/job-runs").json()

    # 旧中断不算活跃：当前读数是后来的成功结果，不是那条中断记录。
    assert body["total_count"] == 2
    assert body["current"]["id"] == "job-new"
    assert body["current"]["status"] == "succeeded"
