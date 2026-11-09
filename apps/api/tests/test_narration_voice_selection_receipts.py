"""A voice adoption and its original receipt commit once, without model execution."""
from script_seed_fixtures import script_seed_payload
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.materials.contracts import MediaAuthorityError
from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan, CreationPlanBlocker
from app.creation.contracts import CreationTask, JobRun
from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
from app.schemas import CreationTaskNarrationVoiceSelectionRequest, CreationTaskNarrationVoiceSelectionResponse
from _media_preview_test_fixtures import register_synthetic_profiles


@pytest.fixture
def state(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    repo = ProductionRepository(database_path=tmp_path / "studio.sqlite3", video_library_path=library)
    plan = CreationPlan(id="plan", workspace_id="workspace", goal="中文介绍", name="原任务", target_count=1,
        language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30, knowledge_references=[], source_references=[],
        selected_voice=None, pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="保留素材", model_snapshot=[], confirmation_points=[], preflight_fingerprint="f"*64,
        blockers=[CreationPlanBlocker("voice_identity_missing", "缺声音", "选择声音", "narration", False)])
    task = CreationTask(id="task", workspace_id="workspace", plan_id="plan", name="原任务", scenario="中文介绍",
        stage="narration", stage_status="failed", status="failed", confirmed_script_id="confirmed-script",
        selected_topic_id="original-topic", stage_reason="voice_identity_missing")
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id="owner")
    voices = [register_synthetic_profiles(repo, workspace_id="workspace", member_id="owner",
        person_id=f"person-{i}", voice_id=f"voice-{i}", voice_version="v1", language="zh")[1] for i in (1, 2)]
    return SimpleNamespace(repo=repo, voices=voices)


def command(state, **changes):
    plan = state.repo.plan_records.get_creation_plan("workspace", "plan")
    task = state.repo.task_records.get_creation_task("workspace", "task")
    return dict(workspace_id="workspace", task_id="task", member_id="owner", request_id=str(uuid4()),
        voice_profile_id="voice-1", voice_version="v1", expected_plan_updated_at=plan.updated_at,
        expected_task_updated_at=task.updated_at) | changes


def snapshot(repo):
    with repo._connect() as conn:
        return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in ("creation_tasks", "creation_plans", "creation_task_scripts", "task_events")}


def test_selection_replay_and_query_preserve_the_original_receipt_after_another_selection(state):
    body = command(state)
    first = state.repo.narration_voice_selection.select_creation_task_narration_voice(**body)
    CreationTaskNarrationVoiceSelectionResponse.model_validate(first)
    assert first["selected_voice"]["profile_id"] == "voice-1" and first["previous_voice"] is None
    assert first["member_id"] == "owner" and first["confirmed_script_id"] == "confirmed-script"
    current = state.repo.task_records.get_creation_task("workspace", "task")
    assert current.stage == "narration" and current.stage_status == "not_started"
    assert current.selected_topic_id == "original-topic" and current.confirmed_script_id == "confirmed-script"
    assert not state.repo.plan_records.get_creation_plan("workspace", "plan").blockers
    second = state.repo.narration_voice_selection.select_creation_task_narration_voice(**command(state, voice_profile_id="voice-2"))
    assert second["previous_voice"] == first["selected_voice"]
    before = snapshot(state.repo)
    reloaded = ProductionRepository(database_path=state.repo.database_path, video_library_path=state.repo.video_library_path)
    assert reloaded.narration_voice_selection.select_creation_task_narration_voice(**body) == first
    assert reloaded.narration_voice_selection.get_creation_task_narration_voice_selection("workspace", "task", body["request_id"], member_id="owner") == first
    assert snapshot(reloaded) == before
    assert reloaded.plan_records.get_creation_plan("workspace", "plan").selected_voice.profile_id == "voice-2"


@pytest.mark.parametrize("field,value", [("voice_profile_id", "voice-2"), ("voice_version", "v2"),
    ("expected_plan_updated_at", "2020-01-01T00:00:00+00:00"), ("expected_task_updated_at", "2020-01-01T00:00:00+00:00")])
def test_same_request_rejects_different_input_without_writes(state, field, value):
    body = command(state)
    state.repo.narration_voice_selection.select_creation_task_narration_voice(**body)
    before = snapshot(state.repo)
    with pytest.raises(ValueError, match="不同输入"):
        state.repo.narration_voice_selection.select_creation_task_narration_voice(**(body | {field: value}))
    assert snapshot(state.repo) == before


@pytest.mark.parametrize("field", ["expected_plan_updated_at", "expected_task_updated_at"])
def test_stale_task_or_plan_rejects_without_a_receipt(state, field):
    before = snapshot(state.repo)
    with pytest.raises(ValueError, match="版本已变化"):
        state.repo.narration_voice_selection.select_creation_task_narration_voice(**command(state, **{field: "2020-01-01T00:00:00+00:00"}))
    assert snapshot(state.repo) == before


@pytest.mark.parametrize("member_id,workspace_id", [("other", "workspace"), ("", "workspace"), ("owner", "other")])
def test_task_and_receipt_remain_bound_to_workspace_and_member(state, member_id, workspace_id):
    body = command(state)
    state.repo.narration_voice_selection.select_creation_task_narration_voice(**body)
    before = snapshot(state.repo)
    with pytest.raises((KeyError, PermissionError)):
        state.repo.narration_voice_selection.select_creation_task_narration_voice(**command(state, member_id=member_id, workspace_id=workspace_id))
    with pytest.raises((KeyError, PermissionError)):
        state.repo.narration_voice_selection.get_creation_task_narration_voice_selection(workspace_id, "task", body["request_id"], member_id=member_id)
    assert snapshot(state.repo) == before


@pytest.mark.parametrize("stage,status", [("script", "not_started"), ("narration", "running"), ("montage", "not_started")])
def test_selection_keeps_the_existing_stage_gate(state, stage, status):
    with state.repo._connect() as conn:
        row = conn.execute("SELECT payload FROM creation_tasks WHERE id='task'").fetchone()
        payload = json.loads(row["payload"])
        payload.update(stage=stage, stage_status=status)
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id='task'", (json.dumps(payload),))
    before = snapshot(state.repo)
    with pytest.raises(ValueError, match="不能选择"):
        state.repo.narration_voice_selection.select_creation_task_narration_voice(**command(state))
    assert snapshot(state.repo) == before


def test_queued_production_cannot_race_a_voice_change(state):
    state.repo.job_records.create_job_run(JobRun("job", "workspace", "task", "creation_execution", "job-key", "f"*64))
    before = snapshot(state.repo)
    with pytest.raises(ValueError, match="未结束"):
        state.repo.narration_voice_selection.select_creation_task_narration_voice(**command(state))
    assert snapshot(state.repo) == before


@pytest.mark.parametrize("change", ["missing_version", "revoked", "other_member", "language"])
def test_resolves_the_actual_fixed_profile_and_current_permission(state, change):
    body = command(state)
    voice = state.voices[0]
    if change == "missing_version":
        body["voice_version"] = "missing"
    elif change == "revoked":
        state.repo.save_authorization(replace(voice.authorization, status="revoked", version="revoked"))
    else:
        _, replacement, *_ = register_synthetic_profiles(state.repo, workspace_id="workspace",
            member_id="other" if change == "other_member" else "owner", person_id="new-person", voice_id="new-voice",
            language="en" if change == "language" else "zh")
        body.update(voice_profile_id=replacement.id, voice_version=replacement.version)
    before = snapshot(state.repo)
    with pytest.raises((MediaAuthorityError, KeyError, PermissionError)):
        state.repo.narration_voice_selection.select_creation_task_narration_voice(**body)
    assert snapshot(state.repo) == before


def test_failure_to_record_receipt_rolls_back_both_task_and_plan(state):
    with state.repo._connect() as conn:
        conn.execute("CREATE TRIGGER fail_voice_receipt BEFORE INSERT ON task_events "
            "WHEN NEW.event_type='task_narration_voice_selected' BEGIN SELECT RAISE(ABORT,'receipt failure'); END")
    before = snapshot(state.repo)
    with pytest.raises(ValueError, match="任务事件 ID 已存在"):
        state.repo.narration_voice_selection.select_creation_task_narration_voice(**command(state))
    assert snapshot(state.repo) == before


def test_concurrent_replay_writes_one_receipt(state):
    body = command(state)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: state.repo.narration_voice_selection.select_creation_task_narration_voice(**body), range(2)))
    assert results[0] == results[1]
    assert len([event for event in state.repo.execution_records.list_task_events("workspace", "task")
        if event.event_type == "task_narration_voice_selected"]) == 1


def test_concurrent_different_requests_cannot_both_adopt_from_one_revision(state):
    body = command(state)
    def select(request_id):
        try:
            return state.repo.narration_voice_selection.select_creation_task_narration_voice(**(body | {"request_id": request_id}))
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(select, [body["request_id"], str(uuid4())]))
    assert sum(isinstance(item, dict) for item in results) == 1
    assert any(isinstance(item, str) and "版本已变化" in item for item in results)


def test_request_and_receipt_cannot_be_reused_for_another_owned_task(state):
    body = command(state)
    state.repo.narration_voice_selection.select_creation_task_narration_voice(**body)
    plan = state.repo.plan_records.get_creation_plan("workspace", "plan")
    task = state.repo.task_records.get_creation_task("workspace", "task")
    second_plan, second_task = state.repo.plan_confirmation.confirm_creation_plan(
        replace(plan, id="plan-2", creation_task_id=None), replace(task, id="task-2", plan_id="plan-2"),
        verified_owner_user_id="owner")
    before = snapshot(state.repo)
    with pytest.raises(ValueError, match="不同输入"):
        state.repo.narration_voice_selection.select_creation_task_narration_voice(**(body | {"task_id":"task-2",
            "expected_plan_updated_at":second_plan.updated_at, "expected_task_updated_at":second_task.updated_at}))
    with pytest.raises(KeyError):
        state.repo.narration_voice_selection.get_creation_task_narration_voice_selection("workspace", "task-2", body["request_id"], member_id="owner")
    assert snapshot(state.repo) == before


@pytest.mark.parametrize("field", ["request_id", "expected_plan_updated_at", "expected_task_updated_at"])
def test_dto_does_not_keep_the_unversioned_write_entry(field):
    body = {"request_id": str(uuid4()), "voice_profile_id": "voice-1", "voice_version": "v1",
        "expected_plan_updated_at": "2026-10-18T00:00:00Z", "expected_task_updated_at": "2026-10-18T00:00:00Z"}
    body.pop(field)
    with pytest.raises(ValidationError):
        CreationTaskNarrationVoiceSelectionRequest.model_validate(body)


def script_candidate(state, *, locked=False):
    script = CreationTaskScript(id="confirmed-script", workspace_id="workspace", task_id="task", run_id="run",
        topic_id="original-topic", title="原文案", language="zh-CN", selection_status="confirmed",
        sections=[CreationTaskScriptSection("section", "正文", "原正文", "原画面", ["source"])])
    if locked:
        script.locked_body_digest = hashlib.sha256("原正文".encode()).hexdigest()
    with state.repo._connect() as conn:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at) VALUES (?,?,?,?,?,?)",
            (script.id, script.workspace_id, script.task_id, script.run_id,
             script_seed_payload(state.repo, conn, script), script.updated_at.isoformat()))
    return script


def confirm(state, script, **changes):
    return state.repo.script_records.confirm_creation_task_script("workspace", "task", script.id,
        title=script.title, sections=changes.pop("sections", script.sections),
        expected_updated_at=changes.pop("expected_updated_at", script.updated_at), verified_owner_user_id="owner", **changes)


@pytest.mark.parametrize("status", ["queued", "running", "interrupted"])
def test_script_confirmation_refuses_active_production_in_the_same_transaction(state, status):
    script = script_candidate(state)
    state.repo.job_records.create_job_run(JobRun("job", "workspace", "task", "creation_execution", "job-key", "f"*64))
    if status != "queued":
        state.repo.job_records.claim_job_run("workspace", "job")
    if status == "interrupted":
        state.repo.job_records.finish_job_run("workspace", "job", status="interrupted", failure_reason="interrupted",
            impact="保留原任务", repair_action="回读原请求")
    before = snapshot(state.repo)
    with pytest.raises(ValueError, match="未结束"):
        confirm(state, script)
    assert snapshot(state.repo) == before


@pytest.mark.parametrize("field,value", [("narration_cache_key", "audio"), ("montage_plan_id", "montage"),
    ("current_timeline_version_id", "timeline"), ("output_ready", True)])
def test_script_confirmation_preserves_existing_downstream_results(state, field, value):
    script = script_candidate(state)
    with state.repo._connect() as conn:
        row = conn.execute("SELECT payload FROM creation_tasks WHERE id='task'").fetchone()
        payload = json.loads(row["payload"])
        payload[field] = value
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id='task'", (json.dumps(payload),))
    before = snapshot(state.repo)
    with pytest.raises(ValueError, match="已有配音或剪辑结果"):
        confirm(state, script)
    assert snapshot(state.repo) == before


def test_script_confirmation_keeps_the_existing_revision_and_locked_body_checks(state):
    script = script_candidate(state, locked=True)
    before = snapshot(state.repo)
    with pytest.raises(ValueError, match="修订已变化"):
        confirm(state, script, expected_updated_at="2020-01-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="定稿锁定"):
        confirm(state, script, sections=[replace(script.sections[0], content="未授权改写")])
    assert snapshot(state.repo) == before
