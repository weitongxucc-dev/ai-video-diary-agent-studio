"""Person-render storage through real public wiring; metadata fixtures, no media execution."""
from app.creation import plan_contracts
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json

import pytest

from app import production
from app.creation import person_renders
from app.creation.contracts import ArtifactRecord, CreationTask, JobRun, NarrationSegment
from app.profiles.contracts import PersonProfile, ProfileReference, VoiceProfile


def seed_task(repo, task_id):
    plan = plan_contracts.CreationPlan(
        id="plan-" + task_id, workspace_id="ws", goal="合成存储测试", name="人物片段夹具",
        target_count=1, language="zh-CN", aspect_ratio="9:16", duration_target_seconds=60,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary="合成登记",
        model_snapshot=[], confirmation_points=[], blockers=[], preflight_fingerprint="fixture-" + task_id,
    )
    task = CreationTask(task_id, "ws", plan.id, plan.name, "knowledge_explanation")
    return repo.plan_confirmation.confirm_creation_plan(plan, task)[1]


def case(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    repo = production.ProductionRepository(
        tmp_path / "db.sqlite3", library, duration_reader=lambda _: 2.0, scene_reader=lambda *_: [],
    )
    task = seed_task(repo, "task")
    visual = "workspace://approved/person.mp4"
    voice_reference = "workspace://approved/reference.wav"
    person = PersonProfile("person", "ws", "合成人物", "v1", visual, (), (), reference=ProfileReference())
    voice = VoiceProfile(
        "voice", "ws", "v1", "zh", voice_reference, "合成参考",
        person_profile_id="person", person_version="v1", display_name="合成声音", reference=ProfileReference(),
    )
    stamp = datetime(2026, 1, 2, tzinfo=timezone.utc)
    segment = NarrationSegment(
        "segment", "ws", task.id, "script-fixture", "section-fixture", "合成旁白",
        "artifacts/synthetic-narration.wav", 2.0, 16000, "synthetic-model", voice.id, voice.version,
        "zh-CN", "v1", "ready", created_at=stamp, updated_at=stamp,
    )
    # Source-record owners seed legal fixed metadata; no registration/generation qualification is mocked.
    with repo._connect() as conn:
        repo._profile_records._save_person_profile(conn, person)
        repo._profile_records._save_versioned_voice_profile(conn, voice)
        for reference in (visual, voice_reference):
            repo._workspace_media_references.claim_workspace_media_reference(conn, workspace_id="ws", reference=reference)
        values = asdict(segment)
        for name in ("created_at", "updated_at"):
            values[name] = values[name].isoformat()
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        conn.execute(f"INSERT INTO narration_segments ({columns}) VALUES ({placeholders})", tuple(values.values()))
    pending = person_renders.PersonRender(
        "render", "ws", task.id, person.id, person.version, voice.id, voice.version, segment.id,
        visual, "synthetic-model", "natural", "neutral", "a" * 64, "pending",
    )
    return repo, task, pending


def artifact(repo, task_id, identity, kind="person_render"):
    job = repo.job_records.create_job_run(JobRun(
        id="job-" + identity, workspace_id="ws", task_id=task_id, kind="person_render",
        idempotency_key="job-" + identity, input_digest="c" * 64,
    ))
    return repo.execution_records.create_artifact_record(ArtifactRecord(
        id=identity, workspace_id="ws", task_id=task_id, kind=kind,
        logical_ref="artifacts/" + identity + ".mp4", sha256="d" * 64, size_bytes=13,
        producer_job_id=job.id, input_refs=[],
    ))


def test_public_person_render_store_preserves_cache_state_artifact_and_restart(tmp_path):
    repo, task, pending = case(tmp_path)
    assert isinstance(repo.person_renders, person_renders.PersonRenderStore)
    retired = ('create_person_render', 'claim_person_render', 'finish_person_render',
        'bind_person_render_artifact', 'get_person_render', 'list_person_renders',
        'interrupt_running_person_renders')
    assert not any(hasattr(repo, name) for name in retired)
    assert not hasattr(repo, '_person_renders')
    assert not hasattr(production, 'PersonRender') and not hasattr(production, 'PersonRenderStatus')
    assert repo.person_renders._profiles is repo._profile_records
    assert repo.person_renders._workspace_references is repo._workspace_media_references
    saved = repo.person_renders.create_person_render(pending)
    assert type(saved) is person_renders.PersonRender
    assert repo.person_renders.create_person_render(replace(pending, id="another-request-id")) == saved
    with pytest.raises(ValueError, match="不同输入"):
        repo.person_renders.create_person_render(replace(pending, id="conflict", motion="different"))
    # Fixed-record qualification precedes even a matching cache lookup.
    with pytest.raises(KeyError):
        repo.person_renders.create_person_render(replace(pending, person_version="missing-version"))
    with pytest.raises(ValueError, match="声音版本必须与配音分段一致"):
        repo.person_renders.create_person_render(replace(pending, voice_version="v2"))
    good = artifact(repo, task.id, "good")
    with pytest.raises(ValueError, match="ready"):
        repo.person_renders.bind_person_render_artifact("ws", task.id, saved.id, good.logical_ref)
    assert repo.person_renders.claim_person_render("ws", task.id, saved.id).status == "running"
    with pytest.raises(ValueError, match="当前不可领取"):
        repo.person_renders.claim_person_render("ws", task.id, saved.id)
    failed = repo.person_renders.finish_person_render(
        "ws", task.id, saved.id, status="failed", failure_reason="synthetic_failure",
        impact="合成影响", repair_action="合成重试",
    )
    assert failed.status == "failed"
    claimed = repo.person_renders.claim_person_render("ws", task.id, saved.id)
    assert claimed.status == "running"
    assert all(getattr(claimed, name) is None for name in (
        "output_path", "duration_seconds", "alignment_evidence", "failure_reason", "impact", "repair_action",
    ))
    ready = repo.person_renders.finish_person_render(
        "ws", task.id, saved.id, status="ready", output_path=str(tmp_path / "synthetic-output.mp4"),
        duration_seconds=2.0, alignment_evidence='{"fixture":"metadata-only"}',
    )
    assert ready.status == "ready" and ready.artifact_ref is None
    with pytest.raises(ValueError, match="只能结束运行中"):
        repo.person_renders.finish_person_render(
            "ws", task.id, saved.id, status="ready", output_path=ready.output_path,
            duration_seconds=2.0, alignment_evidence=ready.alignment_evidence,
        )
    other_task = seed_task(repo, "other-task")
    foreign = artifact(repo, other_task.id, "foreign")
    wrong_kind = artifact(repo, task.id, "wrong-kind", kind="preview")
    with pytest.raises(ValueError, match="不属于当前工作区与创作任务"):
        repo.person_renders.bind_person_render_artifact("ws", task.id, saved.id, foreign.logical_ref)
    with pytest.raises(ValueError, match="类型必须为 person_render"):
        repo.person_renders.bind_person_render_artifact("ws", task.id, saved.id, wrong_kind.logical_ref)
    ready = repo.person_renders.bind_person_render_artifact("ws", task.id, saved.id, good.logical_ref)
    assert repo.person_renders.bind_person_render_artifact("ws", task.id, saved.id, good.logical_ref) == ready
    with pytest.raises(ValueError, match="已绑定其他"):
        repo.person_renders.bind_person_render_artifact("ws", task.id, saved.id, foreign.logical_ref)
    interrupted = repo.person_renders.create_person_render(replace(pending, id="interrupted", cache_key="b" * 64))
    repo.person_renders.claim_person_render("ws", task.id, interrupted.id)
    assert repo.person_renders.interrupt_running_person_renders("other-workspace") == 0
    assert repo.person_renders.interrupt_running_person_renders("ws") == 1
    assert repo.person_renders.interrupt_running_person_renders() == 0
    interrupted = repo.person_renders.get_person_render("ws", task.id, interrupted.id)
    assert (interrupted.status, interrupted.failure_reason) == ("failed", "process_interrupted")
    reopened = production.ProductionRepository(repo.database_path, repo.video_library_path)
    assert reopened.person_renders.get_person_render("ws", task.id, saved.id) == ready
    assert reopened.person_renders.list_person_renders("ws", task.id) == sorted([ready, interrupted], key=lambda r: (r.created_at, r.id))
    with pytest.raises(KeyError):
        reopened.person_renders.get_person_render("other-workspace", task.id, saved.id)
    assert not (tmp_path / "synthetic-output.mp4").exists()
    assert type(ready) is person_renders.PersonRender and type(interrupted) is person_renders.PersonRender
    (tmp_path / "person-store-observations.json").write_text(json.dumps({
        "actual_store": type(repo.person_renders).__module__ + "." + type(repo.person_renders).__name__,
        "profiles_owner_identity": repo.person_renders._profiles is repo._profile_records,
        "workspace_reference_owner_identity": repo.person_renders._workspace_references is repo._workspace_media_references,
        "canonical_return_types": [type(value).__module__ + "." + type(value).__name__ for value in (saved, ready, interrupted)],
        "root_delegates_absent": not any(hasattr(repo, name) for name in retired),
        "root_private_owner_absent": not hasattr(repo, '_person_renders'),
        "synthetic_output_file_exists": (tmp_path / "synthetic-output.mp4").exists(),
        "persisted_ready_id_and_artifact": [ready.id, ready.artifact_ref],
        "persisted_interrupted_id_and_reason": [interrupted.id, interrupted.failure_reason],
    }, ensure_ascii=False, indent=2) + "\n")
