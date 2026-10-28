"""Qualification metadata only; the render result and output port are declared facts."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3

from app import main
from app.artifact_store import ArtifactStore
from app.creation.contracts import ArtifactRecord, CreationTask, JobRun, TimelineVersion
from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan


def test_metadata_authorized_task_and_output_qualification(tmp_path, monkeypatch):
    workspace, owner = "publish-metadata", "declared-owner"
    database = tmp_path / "qualification.sqlite3"
    repository = ProductionRepository(database, tmp_path / "unused-library")
    plan = CreationPlan(id="plan-metadata", workspace_id=workspace, goal="声明的资格元数据", name="元数据任务",
        target_count=1, language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary="metadata only",
        model_snapshot=[], confirmation_points=[], blockers=[], preflight_fingerprint="0" * 64)
    declared_task = CreationTask(id="task-metadata", workspace_id=workspace, plan_id=plan.id,
        name="元数据任务", scenario="metadata", output_ready=True)
    _, task = repository.plan_confirmation.confirm_creation_plan(plan, declared_task, verified_owner_user_id=owner)
    store = ArtifactStore(tmp_path / "controlled-artifacts")
    logical_ref = "fixtures/output.NONMEDIA"
    content = b"NONMEDIA qualification fixture; no video or render"
    publication = store.publish_bytes(logical_ref, content)
    version = repository.timeline_records.create_timeline_version(TimelineVersion(id="timeline-metadata", workspace_id=workspace,
        task_id=task.id, parent_version_id=None, sequence=1,
        snapshot={"montage": {"output_path": logical_ref}, "output_refs": [logical_ref]},
        created_by_operation_id=None))
    # A queued metadata producer row supplies the public artifact ownership link.
    # It has never executed and makes no render/worker success claim.
    producer = repository.job_records.create_job_run(JobRun(id="producer-metadata", workspace_id=workspace,
        task_id=task.id, kind="metadata_fixture", idempotency_key="producer-metadata", input_digest="metadata-fixture"))
    artifact = repository.execution_records.create_artifact_record(ArtifactRecord(id="artifact-metadata", workspace_id=workspace,
        task_id=task.id, kind="rendered_video", logical_ref=logical_ref, sha256=publication.sha256,
        size_bytes=publication.size_bytes, producer_job_id=producer.id, input_refs=[]))
    source_task = repository.task_records.get_creation_task(workspace, task.id)
    assert source_task.owner_user_id == owner and source_task.visibility == "private"
    assert source_task.output_ready and source_task.current_timeline_version_id == version.id
    source_snapshot = asdict(source_task)

    def counts():
        with sqlite3.connect(database) as conn:
            result = {table: conn.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in
                ("creation_plans", "creation_tasks", "timeline_versions", "job_runs", "artifact_records", "task_events")}
            result["platform_publish_jobs"] = conn.execute("SELECT count(*) FROM job_runs WHERE kind='platform_publish'").fetchone()[0]
        return result

    before = counts()
    trace, task_reads, version_reads = [], [], []
    get_task, get_version, list_artifacts = repository.task_records.get_creation_task, repository.timeline_records.get_timeline_version, repository.execution_records.list_artifact_records

    def observe_task(ws, identifier):
        trace.append("task")
        assert (ws, identifier) == (workspace, task.id)
        result = get_task(ws, identifier)
        task_reads.append(result)
        return result

    def observe_version(ws, identifier):
        trace.append("version")
        assert (ws, identifier) == (workspace, version.id)
        result = get_version(ws, identifier)
        version_reads.append(result)
        return result

    def observe_artifacts(ws, identifier):
        trace.append("artifacts")
        assert (ws, identifier) == (workspace, task.id)
        return list_artifacts(ws, identifier)

    def declared_render(ws, identifier, *, verify_media, timeline_version):
        trace.append("render:declared-true")
        assert (ws, identifier, verify_media) == (workspace, task.id, False)
        assert timeline_version is version_reads[-1] and timeline_version == version
        return True

    class ObservedPath(type(tmp_path)):
        def is_file(self):
            trace.append("file.is_file")
            return super().is_file()

        def stat(self, *args, **kwargs):
            trace.append("file.stat")
            return super().stat(*args, **kwargs)

    def declared_output(ws, identifier, *, subtitle):
        trace.append("output:declared-qualified")
        assert (ws, identifier, subtitle) == (workspace, task.id, False)
        return ObservedPath(store.resolve_file(logical_ref))

    monkeypatch.setattr(main, "production_repo", repository)
    monkeypatch.setattr(repository.task_records, "get_creation_task", observe_task)
    monkeypatch.setattr(repository.timeline_records, "get_timeline_version", observe_version)
    monkeypatch.setattr(repository.execution_records, "list_artifact_records", observe_artifacts)
    monkeypatch.setattr(main, "_creation_render_is_valid", declared_render)
    monkeypatch.setattr(main, "_creation_task_output_path", declared_output)
    visible = main._platform_publish_task_for_user(workspace, task.id, owner)
    assert visible is task_reads[0] and asdict(visible) == source_snapshot
    assert trace == ["task"] and version_reads == []
    task_trace = list(trace)
    trace.clear()
    output = main._platform_publish_output_for(workspace, task.id, owner)
    assert output["task"] is task_reads[1]
    assert output == {"task": task_reads[1], "timeline_version_id": version.id, "logical_ref": logical_ref,
        "path": str(store.resolve_file(logical_ref)), "sha256": artifact.sha256, "size_bytes": len(content)}
    assert trace == ["task", "version", "render:declared-true", "output:declared-qualified", "artifacts",
        "file.is_file", "file.stat", "file.stat"]
    assert len(task_reads) == 2 and len(version_reads) == 1
    fresh = ProductionRepository(database, tmp_path / "unused-library")
    assert asdict(fresh.task_records.get_creation_task(workspace, task.id)) == source_snapshot
    assert fresh.timeline_records.get_timeline_version(workspace, version.id) == version
    assert fresh.execution_records.get_artifact_record(workspace, artifact.id) == artifact
    fresh_producer = fresh.job_records.get_job_run(workspace, producer.id)
    assert (fresh_producer.status, fresh_producer.progress, fresh_producer.attempt) == ("queued", 0, 0)
    assert fresh_producer.started_at is None and fresh_producer.finished_at is None and fresh_producer.output_refs == []
    assert counts() == before == {"creation_plans": 1, "creation_tasks": 1, "timeline_versions": 1,
        "job_runs": 1, "artifact_records": 1, "task_events": 0, "platform_publish_jobs": 0}
    evidence = os.environ.get("PUBLISH_OUTPUT_EVIDENCE_DIR")
    if evidence:
        (Path(evidence) / "NORMAL_FACTS.json").write_text(json.dumps({"database": str(database),
            "nonmedia_file": str(store.resolve_file(logical_ref)), "nonmedia_bytes": len(content),
            "invocations": [{"kind": "authorized_task", "trace": task_trace},
                {"kind": "authorized_output", "trace": trace}],
            "counts_before": before, "counts_after": counts(),
            "result": {key: (value.id if key == "task" else value) for key, value in output.items()},
            "producer_state": [fresh_producer.status, fresh_producer.progress, fresh_producer.attempt],
            "public_metadata_persistence_and_fresh_reads": True, "render_and_output_ports": "declared fixtures",
            "native_video_worker_model_platform_auth_acceptance": False}, ensure_ascii=False, indent=2) + "\n")
