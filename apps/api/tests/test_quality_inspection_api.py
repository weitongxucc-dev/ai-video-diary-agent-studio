import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.quality_inspection as inspection
from app.artifact_store import ArtifactStore
from app.local_members import LocalMemberService
from app.montage import creation_task_montage_plan_to_payload
from app.creation.contracts import ArtifactRecord, CreationTask, JobRun, TimelineVersion
from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan
from test_quality_inspection import montage
from test_quality_resource_owner import (fixture_budget, licensed_native_port, metadata_result,
    build_metadata_runtime)
from app.creation.quality_inspection_use_case import QualityInspectionUseCase
from app.creation_execution import CreationExecutionActor
from app.quality_worker_runtime import source_receipt


@pytest.fixture
def system(tmp_path, monkeypatch, request):
    metadata_only = request.node.name == "test_authority_and_empty_body_reject_before_check_write"
    forbidden_calls = []
    if metadata_only:
        import wave
        from app.auth import SupabaseAuthContext
        from app.config import Settings
        from app.device_runtime import DeviceRuntime
        from app.device_scheduler import DeviceScheduler
        from app.creation.quality_execution import QualityCheckUnitExecutor

        def forbidden(*args, **kwargs):
            forbidden_calls.append("execution")
            pytest.fail("quality authority metadata forbids runtime/worker/media execution")

        monkeypatch.setattr(subprocess, "Popen", forbidden)
        monkeypatch.setattr(wave, "open", forbidden)
        for name in ("start", "run_once", "wake"):
            monkeypatch.setattr(DeviceRuntime, name, forbidden)
        monkeypatch.setattr(DeviceScheduler, "pick_and_grant", forbidden)
        monkeypatch.setattr(QualityCheckUnitExecutor, "execute", forbidden)
        monkeypatch.setattr(main, "_start_device_runtime", forbidden)
        settings = Settings(database_path=str(tmp_path / "studio.sqlite"),
            video_inbox_path=str(tmp_path / "inbox"), video_output_path=str(tmp_path / "output"),
            model_root_path=str(tmp_path / "models"), temporary_root_path=str(tmp_path / "temporary"),
            obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
            local_whisper_cli_path=None, local_whisper_model_path=None,
            supabase_url=None, supabase_publishable_key=None)
        monkeypatch.setattr(main, "settings", settings)
        monkeypatch.setattr(main, "auth_context", SupabaseAuthContext(settings))
    members = LocalMemberService(tmp_path / "studio.sqlite")
    owner, token = members.whoami(None)
    other, other_token = members.join(members.create_invite_code(), "另一个成员")
    repo = ProductionRepository(tmp_path / "studio.sqlite", tmp_path / "library")
    plan = CreationPlan(id="plan", workspace_id="local", goal="测试", name="检查", target_count=1,
                        language="zh-CN", aspect_ratio="16:9", duration_target_seconds=2,
                        knowledge_references=[], source_references=[], selected_voice=None,
                        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
                        material_rule_summary="", model_snapshot=[], confirmation_points=[],
                        blockers=[], preflight_fingerprint="isolated")
    task = CreationTask(id="task", workspace_id="local", plan_id="plan", name="检查",
                        scenario="knowledge_explanation", stage="review")
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    store = ArtifactStore(tmp_path / "artifacts")
    checks = inspection.QualityCheckRepository(tmp_path / "studio.sqlite")
    for name, value in [("production_repo", repo), ("artifact_store", store),
                        ("quality_check_repo", checks), ("local_members_service", members)]:
        monkeypatch.setattr(main, name, value)
    # No lifespan: no production, scheduler, recovery or model workers.
    client = TestClient(main.app)
    native = licensed_native_port(request) if request.node.name == "test_real_wil119_black_frame_persisted_and_locatable" else None
    actor = CreationExecutionActor("local", owner.member_id)
    service = QualityInspectionUseCase(repo, checks, main._output_observation_use_case(repo),
        budget=native.budget if native else fixture_budget(), verify_identity=main._verify_creation_identity,
        temporary_root=tmp_path / "quality-worker")
    runtime = build_metadata_runtime(tmp_path, service, native.worker if native else lambda *args: metadata_result(args[1]),
        resources=native.resources if native else None, processes=native.processes if native else None)
    monkeypatch.setattr(main, "_quality_inspection_use_case", lambda repository=None: service)
    monkeypatch.setattr(main, "device_runtime", runtime)
    if metadata_only:
        monkeypatch.setattr(runtime.registrations["quality_check"].executor, "worker", forbidden)
    client.quality_runtime, client.quality_service, client.quality_actor, client.quality_native = runtime, service, actor, native
    yield client, repo, store, checks, {"X-Local-Member-Token": token}, other_token, tmp_path
    assert runtime.stop(1)
    client.close()
    assert other.member_id != owner.member_id
    if metadata_only:
        assert forbidden_calls == []


def seed_render(system, content=b"fake video", *, register=True, ref="artifacts/video.mp4", version="v1"):
    _, repo, store, _, _, _, _ = system
    publication = store.publish_bytes(ref, content)
    if register:
        repo.job_records.create_job_run(JobRun(id="fixture-job-" + version, workspace_id="local", task_id="task",
                                   kind="creation_task_montage_render", idempotency_key=version,
                                   input_digest=version))
        repo.execution_records.create_artifact_record(ArtifactRecord("artifact-" + version, "local", "task", "rendered_video",
                                                   ref, publication.sha256, publication.size_bytes,
                                                   "fixture-job-" + version, []))
    payload = creation_task_montage_plan_to_payload(montage(ref))
    repo.montage_plan_adoption.save_creation_task_montage_plan("local", "task", "montage", payload)
    current = repo.task_records.get_creation_task("local", "task").current_timeline_version_id
    repo.timeline_records.create_timeline_version(TimelineVersion(id=version, workspace_id="local", task_id="task",
                                                 parent_version_id=current, sequence=1 if current is None else 2,
                                                 snapshot={"montage": payload, "output_refs": [ref]},
                                                 created_by_operation_id=None))
    return publication


def wait_result(client, headers):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        client.quality_runtime.run_once()
        response = client.get("/api/creation-tasks/task/quality-check", headers=headers)
        assert response.status_code == 200
        result = response.json()
        if result["check_state"] != "checking":
            return result
        time.sleep(.02)
    pytest.fail("isolated quality worker did not finish")


def test_real_wil119_black_frame_persisted_and_locatable(system):
    client, repo, store, checks, headers, _, tmp_path = system
    video = tmp_path / "black.mp4"
    client.quality_native.command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=160x120:r=25:d=0.8", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:duration=0.8", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
                   check=True, capture_output=True, timeout=15)
    publication = seed_render(system, video.read_bytes())
    # Registered synthetic output through the real HTTP trigger, no detector mock.
    response = client.post("/api/creation-tasks/task/quality-check", headers=headers, json={})
    assert response.status_code == 200
    result = wait_result(client, headers)
    assert result["check_state"] == "done"
    assert result["result_status"] == "issues_found"
    black = next(item for item in result["findings"] if item["kind"] == "black_frame")
    assert black["start_seconds"] == 0
    assert black["end_seconds"] > .6
    assert black["clip_ids"] == ["clip-1"]
    assert result["render_ref"] == publication.sha256
    assert checks.get("local", "task")["evidence"]["engine"] == "ffmpeg/ffprobe present"
    main._start_creation_task_quality_check("local", "task", actor=client.quality_actor)
    assert checks.get("local", "task")["check_id"] == result["check_id"]
    monkey_repo = inspection.QualityCheckRepository(repo.database_path)
    assert monkey_repo.get("local", "task")["evidence"]["status"] == "issues_found"
    root = os.environ.get("WIL108_EVIDENCE_ROOT")
    if root:
        evidence = Path(root)
        (evidence / "black.mp4").write_bytes(video.read_bytes())
        (evidence / "real-check.json").write_text(json.dumps({"api": result, "persisted": monkey_repo.get("local", "task")},
                                                           ensure_ascii=False, indent=2))
    # New timeline, same immutable video bytes: old conclusion still invalidates.
    repo.timeline_records.create_timeline_version(TimelineVersion(id="v2", workspace_id="local", task_id="task",
                                                 parent_version_id="v1", sequence=2, snapshot={},
                                                 created_by_operation_id=None))
    stale = client.get("/api/creation-tasks/task/quality-check", headers=headers).json()
    assert stale["stale"] is True and stale["result_status"] is None
    assert stale["findings"] == []
    assert store.resolve_file("artifacts/video.mp4").is_file()


def test_authority_and_empty_body_reject_before_check_write(system, monkeypatch):
    client, repo, _, checks, headers, other_token, _ = system
    seed_render(system)

    def database_facts():
        with repo._connect() as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: sorted((tuple(row) for row in connection.execute(
                'SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr) for name in tables}

    before = database_facts()
    url = "/api/creation-tasks/task/quality-check"
    for method in (client.get, client.post):
        args = {"json": {}} if method == client.post else {}
        assert method(url, **args).status_code == 401
        assert method(url, headers={"X-Local-Member-Token": "invalid"}, **args).status_code == 401
        assert method(url, headers={"X-Local-Member-Token": other_token}, **args).status_code == 404
        assert method("/api/creation-tasks/unknown/quality-check", headers=headers, **args).status_code == 404
    assert client.post(url, headers=headers, json={"path": "/etc/passwd"}).status_code == 422
    readable = client.get(url, headers=headers)
    assert readable.status_code == 200, readable.text
    assert readable.json()["check_state"] == "none"
    assert readable.json()["timeline_version_id"] == "v1"
    assert readable.json()["check_id"] is None and readable.json()["findings"] == []
    missing = {"job_id": "missing-job", "failed_request_id": "missing-request"}
    for response in (client.get(url + "/continuation", headers=headers, params=missing),
                     client.post(url + "/continue", headers=headers, json=missing)):
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "原质检作业不存在或不属于当前任务"
    assert client.post(url + "/continue", headers=headers,
                       json={**missing, "max_timeout_seconds": 99}).status_code == 422
    monkeypatch.setattr(main, "current_workspace_id", lambda _request: "another-workspace")
    assert client.get(url, headers=headers).status_code == 403
    assert client.post(url, headers=headers, json={}).status_code == 403
    assert checks.get("local", "task") is None
    assert database_facts() == before


@pytest.mark.parametrize("registered", [True, False])
def test_unreadable_or_unregistered_render_is_failed_not_clean(system, registered):
    client, _, store, _, headers, _, _ = system
    seed_render(system, register=registered)
    if registered:
        store.resolve_file("artifacts/video.mp4").unlink()
    response = client.post("/api/creation-tasks/task/quality-check", headers=headers, json={})
    assert response.status_code == 200
    result = response.json()
    assert result["check_state"] == "failed"
    assert result["result_status"] is None
    assert result["error"]


def test_polling_and_retry_reuse_active_check(system, monkeypatch):
    client, _, _, checks, headers, _, _ = system
    publication = seed_render(system)
    check_id = client.quality_service.start(client.quality_runtime, client.quality_actor, "local", "task", force=True)
    def forbidden(*args):
        pytest.fail("active check must not start twice")
    monkeypatch.setattr(client.quality_runtime.registrations["quality_check"].executor, "worker", forbidden)
    for method in (client.get, client.post):
        args = {"json": {}} if method == client.post else {}
        result = method("/api/creation-tasks/task/quality-check", headers=headers, **args).json()
        assert result["check_state"] == "checking"
        assert result["check_id"] == check_id
        assert result["result_status"] is None


@pytest.mark.parametrize("change", ["delete", "replace", "invalidate", "regenerate"])
def test_persisted_clean_never_survives_output_changes(system, change):
    client, repo, store, checks, headers, _, _ = system
    publication = seed_render(system)
    check_id = checks.begin("local", "task", "v1", "artifacts/video.mp4", publication.sha256)
    _, _, source = client.quality_service.source_metadata("local", "task")
    checks.finish("local", "task", check_id, evidence={"status": "clean", "findings": [], "unavailable": [],
        "_source_receipt": source_receipt(source, check_id)})
    url = "/api/creation-tasks/task/quality-check"
    assert client.get(url, headers=headers).json()["result_status"] == "clean"
    if change == "delete":
        store.resolve_file("artifacts/video.mp4").unlink()
    elif change == "replace":
        store.resolve_file("artifacts/video.mp4").write_bytes(b"tampered render")
    elif change == "invalidate":
        payload = creation_task_montage_plan_to_payload(montage(None))
        payload["status"] = "ready"
        repo.montage_plan_adoption.save_creation_task_montage_plan("local", "task", "montage", payload)
    else:
        seed_render(system, b"new render", ref="artifacts/new.mp4", version="v2")
    result = client.get(url, headers=headers).json()
    assert result["stale"] is True and result["result_status"] is None
    assert result["findings"] == result["unavailable"] == []


def test_delivery_judgment_uses_video_not_container_duration_and_is_idempotent(system):
    from dataclasses import asdict
    client, repo, store, checks, headers, other_token, tmp_path = system
    seed_render(system)
    service = client.quality_service
    _, _, source = service.source_metadata('local', 'task')
    request = {'budget': asdict(service.budget), 'source': source, 'check_id': 'declared-duration-check'}
    result = metadata_result(request)
    result['evidence'].update(video_duration_seconds=1.92, video_frame_rate='25/1', duration_seconds=2)
    service.validate_result(request, result)
    assert result['evidence']['duration_assessment']['status'] == 'mismatch'
    assert result['evidence']['status'] == 'issues_found' and not result['evidence']['is_clean']
    service.validate_result(request, result)
    assert len([f for f in result['evidence']['findings'] if f['kind'] == 'delivery_duration']) == 1
