"""Real isolated conversation persistence; model and current-principal seams only."""
import hashlib
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.assistant_goal_continuation import GoalContinuationService, GoalResultProjection
from app.conversation_repository import ConversationRepository


@pytest.fixture
def repo(tmp_path):
    return ConversationRepository(tmp_path / "continuation.sqlite3")


def test_goal_projection_keeps_successful_execution_stale_after_unrelated_version(tmp_path):
    """Real metadata owners; no main, model, render or permission replacement."""
    from dataclasses import asdict
    import sys
    from fastapi.encoders import jsonable_encoder
    from app.assistant_goal_continuation import GoalResultProjection
    from app.creation.contracts import JobRun, TaskEvent, TimelineVersion
    from app.production import CreationPlan, ProductionRepository
    from app.creation.contracts import CreationTask

    import subprocess
    cold=subprocess.run([sys.executable,"-B","-c",
        "import sys; import app.assistant_goal_continuation; assert 'app.main' not in sys.modules"],capture_output=True,text=True)
    assert cold.returncode == 0,cold.stderr
    library = tmp_path / "library"
    library.mkdir()
    production = ProductionRepository(tmp_path / "goal.sqlite3", library)
    conversations = ConversationRepository(production.database_path)
    plan = CreationPlan("plan", "workspace", "制作并检查输出", "goal", 1, "zh-CN", "9:16", 15,
        [], [], None, [], 0, 0, "declared metadata fixture", [], [], [], "fixture")
    task = CreationTask("task", "workspace", plan.id, plan.name, "declared-goal-fixture")
    production.confirm_creation_plan(plan, task, verified_owner_user_id="member")
    base = production.create_timeline_version(TimelineVersion(
        "version:one", "workspace", task.id, None, 1, {}, None))
    job = production.create_job_run(JobRun("execution:one", "workspace", task.id,
        "creation_task_execution", "execution:one", "declared-input"))
    production.claim_job_run("workspace", job.id)
    production.finish_job_run("workspace", job.id, status="succeeded")
    rendered = production.create_timeline_version(TimelineVersion("version:render", "workspace", task.id,
        base.id, 2, {"render_input_timeline_version_id": base.id, "creation_render_request_id": "render:one"}, None))
    production.create_timeline_version(TimelineVersion("version:other", "workspace", task.id, base.id, 3, {}, None))
    assert rendered.parent_version_id == base.id and rendered.snapshot["render_input_timeline_version_id"] == base.id

    chat = conversations.create("workspace", "member", "task", task.id)
    chat = conversations.save_draft(chat["id"], "workspace", "member", "制作并检查输出", chat["draft_version"])
    turn, inserted = conversations.create_turn_idempotent(chat["id"], "workspace", "member", chat["draft_text"],
        str(uuid4()), chat["draft_version"], request_context={"kind": "task", "task_id": task.id, "stage": "review",
            "target_snapshot": {"timeline_version_id": base.id, "timeline_sequence": 1}})
    assert inserted
    assert conversations.task_turn_source(turn["id"], "workspace", "member", task.id)["raw_command"] == "制作并检查输出"
    assert conversations.record_task_execution(turn["id"], "workspace", "member", task.id,
        {"task_id": task.id, "job": {"id": job.id, "status": "queued"}})
    conversations.finish_turn(turn["id"], "completed", "制作已提交。", result={"kind": "task_turn", "response": {
        "execution_status": {"task_id": task.id, "job": {"id": job.id, "status": "queued"}}}})
    assert conversations.register_task_goal(turn["id"])
    source = {"workspace_id": "workspace", "task_id": task.id, "member_id": "member",
        "conversation_id": chat["id"], "assistant_turn_id": turn["id"],
        "expected_timeline_version_id": base.id, "expected_timeline_sequence": 1}
    production.append_task_event(TaskEvent("located:one", "workspace", task.id, job.id,
        "assistant_execution_located", {"source": source, "job_id": job.id, "input_digest": job.input_digest}))
    owner = GoalResultProjection(production, conversations)
    rows = production.assistant_execution_projection_rows()
    assert len(rows) == 1
    context = owner.execution_candidate(rows[0])
    actual = jsonable_encoder({"task_id": task.id, "job": asdict(production.get_job_run("workspace", job.id)),
              "video_ready": production.get_creation_task("workspace", task.id).output_ready})
    event = owner.project_execution(context, actual)
    assert event["status"] == "stale" and event["actionable"] is False and event["result_version_id"] is None
    assert not production.assistant_execution_projection_rows()
    assert conversations.project_business_result(turn["id"], "workspace", "member", event,
        response_patch={"execution_status": actual}, message="duplicate is ignored")
    stored = conversations.get_turn(turn["id"])
    assert len([item for item in stored["events"] if item.get("event_id") == event["event_id"]]) == 1
    continuation, model_calls = service(conversations, response={})
    assert continuation.reconcile() == 0 and model_calls == []
    assert conversations.get_task_goal(turn["id"])["status"] == "invalidated"
    assert continuation.reconcile() == 0
    with production._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0] == 1


def original(repo, *, message="生成一张标题图片，放进当前镜头，采用后生成成片", register=True):
    chat = repo.create("workspace", "member", "task", "task")
    chat = repo.save_draft(chat["id"], "workspace", "member", message, chat["draft_version"])
    context = {"kind": "task", "task_id": "task", "stage": "montage", "selected_object_refs": ["clip:one"],
               "target_snapshot": {"timeline_version_id": "version:one", "timeline_sequence": 1}}
    request_id = str(uuid4())
    turn, inserted = repo.create_turn_idempotent(chat["id"], "workspace", "member", message,
        request_id, chat["draft_version"], request_context=context)
    assert inserted
    repo.finish_turn(turn["id"], "completed", "图片正在生成。", result={"kind": "task_turn",
        "response": {"media_job": {"job_id": "png:one", "status": "queued"}, "edit_request": None}})
    if register:
        assert repo.register_task_goal(turn["id"])
    return chat, turn, request_id, context


def emit(repo, turn, *, status="succeeded", phase="preview", result_kind="graphic_source", **fields):
    event = {"type": "business_result", "event_id": str(uuid4()), "task_id": "task", "status": status,
             "phase": phase, "result_kind": result_kind, **fields}
    assert repo.project_business_result(turn["id"], "workspace", "member", event,
        response_patch={}, message="实际业务结果已核验。")
    return event


def service(repo, *, response=None, readiness=None, max_rounds=6, action_refs=None):
    calls = []
    def verify(goal, event):
        return readiness or {"status": "ready", "task_version": {"timeline_version_id": "version:one", "timeline_sequence": 1},
                             "facts": {"current": True}, "no_side_effect": True}
    def run(claim, fresh):
        calls.append((claim, fresh))
        source = repo.task_turn_source(claim["turn_id"], "workspace", "member", "task")
        assert source["raw_command"] == claim["goal"]["original_instruction"]
        actual = response if response is not None else {"edit_preview": {"proposal": {"id": "proposal:one", "status": "proposed"}}}
        proofs = [{"kind": kind, "id": identifier} for kind, identifier in (
            ("edit_preview", ((actual.get("edit_preview") or {}).get("proposal") or {}).get("id")),
            ("media_job", (actual.get("media_job") or {}).get("job_id")),
            ("edit_request", (actual.get("edit_request") or {}).get("request_id")),
            ("execution_status", ((actual.get("execution_status") or {}).get("job") or {}).get("id"))) if identifier]
        repo.finish_turn(claim["turn_id"], "completed", "下一步已核验。", result={"kind": "task_turn",
            "response": actual, "goal_business_actions": proofs if action_refs is None else action_refs})
    return GoalContinuationService(repo, verify_current_goal=verify, run_goal_turn=run, max_rounds=max_rounds), calls


def test_bounded_goal_pages_rotate_past_eight_waiting_roots(repo):
    for _ in range(8):
        original(repo)
    _, ready, _, _ = original(repo)
    event = emit(repo, ready)
    svc, calls = service(repo)
    assert svc.reconcile() == 0 and calls == []
    assert svc.reconcile() == 1
    assert calls[0][0]["event"]["event_id"] == event["event_id"]
    assert repo.get_task_goal(ready["id"])["round_count"] == 1
    with repo._connect() as conn:
        plan = conn.execute("EXPLAIN QUERY PLAN SELECT id,created_at FROM assistant_conversation_turns WHERE "
            "json_extract(result_json,'$.goal_continuation.status') IN "
            "('running','waiting_result','waiting_adoption','waiting_identity','waiting_unknown') "
            "AND (created_at,id) > (?,?) ORDER BY created_at,id LIMIT 8", ("", "")).fetchall()
    assert any("idx_assistant_active_goals_created" in row["detail"] for row in plan)


def test_slow_goal_tick_leaves_projection_free_and_stops_without_losing_later_result(repo):
    _, first, _, _ = original(repo)
    emit(repo, first)
    _, later, _, _ = original(repo)
    synchronous, calls = service(repo)
    entered, release = threading.Event(), threading.Event()
    tick_lock = threading.Lock()
    def slow(claim, fresh):
        entered.set()
        assert release.wait(2)
        synchronous.run_goal_turn(claim, fresh)
    svc = GoalContinuationService(repo, verify_current_goal=synchronous.verify_current_goal, run_goal_turn=slow)
    svc.start()
    try:
        with tick_lock:
            svc.tick()
        assert entered.wait(1)
        # The real business-event persistence remains available while a model
        # callback is blocked, under the driver's same projection tick lock.
        with tick_lock:
            later_event = emit(repo, later)
            svc.tick()
            svc.tick()
        svc.request_stop()
        assert svc.stop(deadline_monotonic=time.monotonic()) is False
        assert repo.get_task_goal(later["id"])["round_count"] == 0
        release.set()
        assert svc.stop(deadline_monotonic=time.monotonic() + 1)
        assert len(calls) == 1
    finally:
        release.set()
        svc.stop(deadline_monotonic=time.monotonic() + 1)
    # A fresh owner reads the existing durable event, without replaying the
    # first claim or creating another input/goal queue.
    restarted, resumed = service(ConversationRepository(repo.database_path))
    assert restarted.reconcile() == 1
    assert len(resumed) == 1 and resumed[0][0]["event"]["event_id"] == later_event["event_id"]
    assert repo.get_task_goal(first["id"])["round_count"] == 1


def test_real_result_starts_one_empty_human_turn_preserves_instruction_and_new_draft(repo):
    chat, turn, request_id, context = original(repo)
    event = emit(repo, turn)
    chat = repo.get(chat["id"], "workspace", "member")
    newer = repo.save_draft(chat["id"], "workspace", "member", "未发送的新草稿", chat["draft_version"])
    svc, calls = service(repo)
    assert svc.reconcile() == 1
    descendant = repo.list_turns(chat["id"])[1]
    assert descendant["user_message"] == ""
    assert descendant["events"][0] == {"type": "goal_continuation_started", "source_kind": "business_event",
        "original_turn_id": turn["id"], "source_turn_id": turn["id"], "event_id": event["event_id"]}
    assert calls[0][0]["goal"]["original_instruction"] == turn["user_message"]
    assert repo.get(chat["id"], "workspace", "member")["draft_text"] == newer["draft_text"]
    assert repo.get(chat["id"], "workspace", "member")["draft_version"] == newer["draft_version"]
    replay, created = repo.create_turn_idempotent(chat["id"], "workspace", "member", turn["user_message"],
        request_id, 1, request_context=context)
    assert not created and replay["id"] == turn["id"]
    assert svc.reconcile() == 0
    restarted, restart_calls = service(ConversationRepository(repo.database_path))
    assert restarted.reconcile() == 0 and restart_calls == []


def test_old_completed_history_and_media_events_do_not_infer_a_goal(repo):
    chat, turn, _, _ = original(repo, register=False)
    emit(repo, turn)
    svc, calls = service(ConversationRepository(repo.database_path))
    assert svc.reconcile() == 0 and calls == []
    assert len(repo.list_turns(chat["id"])) == 1


@pytest.mark.parametrize("status", ["failed", "queued"])
@pytest.mark.parametrize("proofs", [[], [{"kind":"execution_status", "id":"different-job"}]])
def test_pure_execution_read_or_other_job_proof_does_not_register_waiting_goal(repo, status, proofs):
    chat = repo.create("workspace", "member", "task", "task")
    chat = repo.save_draft(chat["id"], "workspace", "member", "查询原制作作业", chat["draft_version"])
    turn, _ = repo.create_turn_idempotent(chat["id"], "workspace", "member", chat["draft_text"], str(uuid4()),
        chat["draft_version"], request_context={"kind":"task", "task_id":"task", "stage":"review",
            "target_snapshot":{"timeline_version_id":"version:one", "timeline_sequence":1}})
    repo.finish_turn(turn["id"], "completed", "仅查询原作业。", result={"kind":"task_turn",
        "response":{"execution_status":{"task_id":"task", "job":{"id":"old-job", "status":status}}},
        "goal_business_actions":proofs})
    assert repo.register_task_goal(turn["id"]) is None
    assert repo.get_task_goal(turn["id"]) is None
    assert repo.get_turn(turn["id"])["result"]["response"]["execution_status"]["job"]["id"] == "old-job"


@pytest.mark.parametrize("terminal", ["failed", "restarted"])
def test_running_host_execution_survives_model_failure_and_restart(repo, terminal):
    chat = repo.create("workspace", "member", "task", "task")
    message = "制作后检查实际输出"
    chat = repo.save_draft(chat["id"], "workspace", "member", message, chat["draft_version"])
    turn, _ = repo.create_turn_idempotent(chat["id"], "workspace", "member", message, str(uuid4()), chat["draft_version"],
        request_context={"kind":"task", "task_id":"task", "stage":"montage",
            "target_snapshot":{"timeline_version_id":"version:one", "timeline_sequence":1}})
    status = {"task_id":"task", "job":{"id":"execution:one", "task_id":"task", "status":"queued"}}
    repo.record_task_execution(turn["id"], "workspace", "member", "task", status)
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_result"
    if terminal == "failed":
        repo.finish_turn(turn["id"], "failed", result={"kind":"failure", "message":"model interrupted"})
    else:
        assert ConversationRepository(repo.database_path).fail_interrupted_turns() == 1
    saved = repo.get_turn(turn["id"])
    assert saved["status"] == "failed"
    assert saved["result"]["response"]["execution_status"] == status
    assert saved["result"]["goal_business_actions"] == [{"kind":"execution_status", "id":"execution:one"}]
    emit(repo, turn, phase="execution", result_kind="execution", job_id="execution:one")
    svc, calls = service(ConversationRepository(repo.database_path), response={})
    assert svc.reconcile() == 1 and len(calls) == 1
    assert calls[0][0]["goal"]["original_instruction"] == message
    assert svc.reconcile() == 0


@pytest.mark.parametrize("interrupted_first", [False, True])
def test_verified_execution_receipt_and_goal_commit_atomically_even_if_model_already_failed(repo, monkeypatch, interrupted_first):
    chat = repo.create("workspace", "member", "task", "task")
    chat = repo.save_draft(chat["id"], "workspace", "member", "制作并检查输出", chat["draft_version"])
    turn, _ = repo.create_turn_idempotent(chat["id"], "workspace", "member", chat["draft_text"], str(uuid4()),
        chat["draft_version"], request_context={"kind":"task", "task_id":"task", "stage":"review",
            "target_snapshot":{"timeline_version_id":"version:one", "timeline_sequence":1}})
    # Authorization was read by the host before the accepted start could race model failure.
    assert repo.task_turn_source(turn["id"], "workspace", "member", "task")["raw_command"] == "制作并检查输出"
    if interrupted_first:
        repo.finish_turn(turn["id"], "failed", result={"kind":"failure", "message":"model interrupted"})
    register = repo._register_task_goal
    def while_registering(conn, turn_id, **kwargs):
        visible = ConversationRepository(repo.database_path).get_turn(turn_id)
        assert not (visible.get("result") or {}).get("goal_business_actions")
        assert repo.get_task_goal(turn_id) is None
        return register(conn, turn_id, **kwargs)
    monkeypatch.setattr(repo, "_register_task_goal", while_registering)
    receipt = {"task_id":"task", "job":{"id":"execution:race", "task_id":"task", "status":"queued"}}
    assert repo.record_task_execution(turn["id"], "workspace", "member", "task", receipt)
    if not interrupted_first:
        repo.finish_turn(turn["id"], "failed", result={"kind":"failure", "message":"model interrupted"})
    saved = ConversationRepository(repo.database_path).get_turn(turn["id"])
    assert saved["status"] == "failed"
    assert saved["result"]["response"]["execution_status"] == receipt
    assert saved["result"]["goal_continuation"]["status"] == "waiting_result"
    emit(repo, turn, phase="execution", job_id="execution:race")
    svc, calls = service(repo, response={})
    assert svc.reconcile() == 1 and len(calls) == 1


@pytest.mark.parametrize("invalidation", ["stopped", "new_instruction", "source_version"])
def test_late_verified_execution_receipt_preserves_history_without_reopening_invalid_goal(repo, invalidation):
    chat = repo.create("workspace", "member", "task", "task")
    chat = repo.save_draft(chat["id"], "workspace", "member", "开始制作", chat["draft_version"])
    turn, _ = repo.create_turn_idempotent(chat["id"], "workspace", "member", chat["draft_text"], str(uuid4()),
        chat["draft_version"], request_context={"kind":"task", "task_id":"task", "stage":"review",
            "target_snapshot":{"timeline_version_id":"version:one", "timeline_sequence":1}})
    repo.task_turn_source(turn["id"], "workspace", "member", "task")
    if invalidation == "stopped":
        repo.finish_turn(turn["id"], "stopped", result={"reason":"user_stopped"})
    elif invalidation == "new_instruction":
        repo.finish_turn(turn["id"], "failed", result={"kind":"failure", "message":"model interrupted"})
        chat = repo.get(chat["id"], "workspace", "member")
        chat = repo.save_draft(chat["id"], "workspace", "member", "只查看", chat["draft_version"])
        repo.create_turn_idempotent(chat["id"], "workspace", "member", chat["draft_text"], str(uuid4()),
            chat["draft_version"], request_context={"kind":"task", "task_id":"task"})
    else:
        repo.finish_turn(turn["id"], "failed", result={"kind":"failure"})
    receipt = {"task_id":"task", "job":{"id":"execution:old", "status":"queued"}}
    assert repo.record_task_execution(turn["id"], "workspace", "member", "task", receipt,
        current_source=invalidation != "source_version")
    assert repo.get_task_goal(turn["id"]) is None
    assert repo.get_turn(turn["id"])["result"]["response"]["execution_status"] == receipt


@pytest.mark.parametrize("change", ["adoption", "hash", "new_action"])
def test_output_checkpoint_is_not_reused_after_adoption_changed_facts_or_new_action(repo, change):
    _, turn, _, _ = original(repo)
    checkpoint = {"status":"completed", "scope":"production_and_output_observation",
        "job_id":"execution:one", "observation_job_id":"observation:one", "timeline_version_id":"version:one",
        "sha256":"a"*64, "measurement_scope":"actual_output_bounded_frames", "unavailable":[]}
    assert repo.record_goal_output_checkpoint(turn["id"], checkpoint)
    emit(repo, turn, phase="apply" if change=="adoption" else "observation",
        status="confirmed" if change=="adoption" else "succeeded", result_kind="observation")
    fresh = {"status":"ready", "task_version":{
        "timeline_version_id":"version:two" if change=="adoption" else "version:one", "timeline_sequence":2 if change=="adoption" else 1}}
    if change == "new_action":
        fresh["completed_output_checkpoint"] = checkpoint
    svc, calls = service(repo, response={"media_job":{"job_id":"observation:new", "status":"queued"}} if change=="new_action" else {}, readiness=fresh)
    assert svc.reconcile() == 1 and len(calls) == 1
    goal = repo.get_task_goal(turn["id"])
    assert "completed_output_checkpoint" not in goal
    assert goal["waiting_reason"] != "bounded_output_checkpoint_completed"
    assert goal["status"] == ("waiting_result" if change=="new_action" else "no_progress")


def test_bounded_output_checkpoint_does_not_certify_all_instruction_steps(repo):
    _, turn, _, _ = original(repo, message="生成成片检查技术输出，还需核对PNG中文与边框")
    emit(repo, turn, phase="observation", result_kind="observation")
    checkpoint = {"status":"completed", "scope":"production_and_output_observation",
        "job_id":"execution:one", "observation_job_id":"observation:one", "timeline_version_id":"version:one",
        "sha256":"a"*64, "measurement_scope":"actual_output_bounded_frames",
        "unavailable":[{"scope":"visual_semantics", "reason":"No semantic validator"}]}
    svc, calls = service(repo, response={}, readiness={"status":"ready",
        "task_version":{"timeline_version_id":"version:one", "timeline_sequence":1}, "completed_output_checkpoint":checkpoint})
    assert svc.reconcile() == 1 and len(calls) == 1
    goal = repo.get_task_goal(turn["id"])
    assert goal["status"] == "output_checked"
    assert goal["waiting_reason"] == "bounded_output_checkpoint_completed"
    assert goal["completed_output_checkpoint"] == checkpoint
    assert "complete_original_instruction" in goal["unfinished_steps"]
    assert "PNG中文与边框" in goal["original_instruction"]


@pytest.mark.parametrize("succeeded", [True, False])
def test_verified_production_settles_separately_from_output_check(repo, succeeded):
    _, turn, _, _ = original(repo)
    emit(repo, turn, phase="execution", status="succeeded" if succeeded else "failed", job_id="execution:one")
    production = {"scope": "production", "status": "succeeded" if succeeded else "failed",
        "job_id": "execution:one", "timeline_version_id": "version:one", "video_ready": succeeded}
    svc, calls = service(repo, response={}, readiness={"status": "ready",
        "task_version": {"timeline_version_id": "version:one", "timeline_sequence": 1},
        "production_result": production})
    assert svc.reconcile() == 1 and len(calls) == 1
    goal = repo.get_task_goal(turn["id"])
    assert goal["status"] == ("production_completed" if succeeded else "production_failed")
    assert goal["waiting_reason"] == ("output_not_checked" if succeeded else "production_failed")
    assert goal["production_result"] == production
    assert "completed_output_checkpoint" not in goal
    assert "complete_original_instruction" in goal["unfinished_steps"]
    assert svc.reconcile() == 0 and len(calls) == 1


def test_new_business_action_clears_old_production_result(repo):
    _, turn, _, _ = original(repo)
    emit(repo, turn, phase="execution", job_id="execution:one")
    svc, _ = service(repo, response={"media_job": {"job_id": "observation:new", "status": "queued"}},
        readiness={"status": "ready", "task_version": {"timeline_version_id": "version:one", "timeline_sequence": 1},
            "production_result": {"status": "succeeded", "job_id": "execution:one", "timeline_version_id": "version:one"}})
    assert svc.reconcile() == 1
    goal = repo.get_task_goal(turn["id"])
    assert goal["status"] == "waiting_result" and "production_result" not in goal


def test_host_projection_does_not_promote_success_without_playable_output():
    from types import SimpleNamespace
    from app.assistant_goal_continuation import GoalResultProjection

    task = SimpleNamespace(current_timeline_version_id="current")
    version = SimpleNamespace(sequence=2)
    for facts in ({}, {"execution_status": {"job": {"id": "render", "status": "succeeded"}, "video_ready": False}}):
        projected = GoalResultProjection.ready(task, version, None, facts, False, [], [], None)
        assert "production_result" not in projected and "completed_output_checkpoint" not in projected


@pytest.fixture
def observed_delivery():
    """Declared observation metadata; no model, media execution or ffprobe."""
    return {"observation": {"receipt": {"job_id": "observation:one"}, "result": {
                "measurement_scope": "actual_output_bounded_frames",
                "media": {"duration_seconds": 25.36}, "unavailable": []}},
            "output": {"sha256": "a" * 64}, "version": SimpleNamespace(id="version:one", sequence=1),
            "source": {"input_digest": "render-input"}, "actual": {
                "job": {"id": "execution:one", "status": "succeeded", "input_digest": "render-input"},
                "video_ready": True},
            "goal": {"creation_adoption": {"source": {}}}, "duration_target_seconds": 30}


@pytest.mark.parametrize("duration,status,delta", [
    (25.36, "short", 4.64), (32, "long", -2),
    (30, "matched", 0), (30 - 1 / 30, "matched", 1 / 30),
    (30 + 1 / 30, "matched", -1 / 30), (30 - 2 / 30, "short", 2 / 30),
])
def test_original_creation_output_must_meet_target_on_actual_output_clock(observed_delivery, duration, status, delta):
    observed_delivery["observation"]["result"]["media"]["duration_seconds"] = duration
    checkpoint = GoalResultProjection.completed_output(**observed_delivery)
    assert checkpoint["status"] == ("completed" if status == "matched" else "incomplete")
    assert checkpoint["sha256"] == "a" * 64 and checkpoint["job_id"] == "execution:one"
    fact = checkpoint["delivery_duration"]
    assert fact["status"] == status and fact["target_duration_seconds"] == 30
    assert fact["actual_duration_seconds"] == duration and fact["delta_seconds"] == pytest.approx(delta, abs=1e-6)
    if status != "matched":
        assert "read_task_edit_capabilities" in fact["repair_action"]
        assert "edit_operations" not in fact  # Availability is read from the actual capability owner.


@pytest.mark.parametrize("media", [{}, {"duration_seconds": None}, {"duration_seconds": "30"},
    {"duration_seconds": False}, {"duration_seconds": 0}, {"duration_seconds": -1},
    {"duration_seconds": float("nan")}, {"duration_seconds": float("inf")}])
def test_creation_cannot_substitute_plan_or_timeline_for_unmeasured_output(observed_delivery, media):
    observed_delivery["observation"]["result"]["media"] = media
    observed_delivery["version"].snapshot = {"montage": {"duration_seconds": 30}}
    checkpoint = GoalResultProjection.completed_output(**observed_delivery)
    assert checkpoint["status"] == "incomplete"
    assert checkpoint["delivery_duration"]["status"] == "unverified"
    assert checkpoint["delivery_duration"]["actual_duration_seconds"] is None
    assert checkpoint["delivery_duration"]["delta_seconds"] is None
    assert "真实视频时长" in checkpoint["delivery_duration"]["repair_action"]


def test_creation_cannot_claim_duration_verified_without_target(observed_delivery):
    observed_delivery["duration_target_seconds"] = None
    checkpoint = GoalResultProjection.completed_output(**observed_delivery)
    assert checkpoint["status"] == "incomplete"
    assert checkpoint["delivery_duration"]["actual_duration_seconds"] == 25.36
    assert checkpoint["delivery_duration"]["target_duration_seconds"] is None


@pytest.mark.parametrize("goal", [
    {"original_instruction": "把当前视频剪短到20秒"},
    {"creation_adoption": {"source": {"input_id": "adopted-home-input"}},
     "source_input_id": "later-task-edit", "original_instruction": "把当前视频剪短到20秒"},
])
def test_later_shortening_goal_does_not_inherit_creation_plan_duration(observed_delivery, goal):
    observed_delivery["goal"] = goal
    observed_delivery["observation"]["result"]["media"]["duration_seconds"] = 20
    checkpoint = GoalResultProjection.completed_output(**observed_delivery)
    assert checkpoint["status"] == "completed" and "delivery_duration" not in checkpoint


@pytest.mark.parametrize("changed", ["job_status", "video_ready", "digest"])
def test_duration_match_cannot_replace_original_output_provenance(observed_delivery, changed):
    observed_delivery["observation"]["result"]["media"]["duration_seconds"] = 30
    if changed == "job_status":
        observed_delivery["actual"]["job"]["status"] = "failed"
    elif changed == "video_ready":
        observed_delivery["actual"]["video_ready"] = False
    else:
        observed_delivery["source"]["input_digest"] = "different-render"
    assert GoalResultProjection.completed_output(**observed_delivery) is None


def adopted_creation_goal(repo, *, revised):
    message = "请自主制作30秒脱壳机介绍"
    chat = repo.create("workspace", "member", "home", "home")
    chat = repo.save_draft(chat["id"], "workspace", "member", message, chat["draft_version"])
    turn, _ = repo.create_turn_idempotent(chat["id"], "workspace", "member", message,
        str(uuid4()), chat["draft_version"], request_context={"kind": "home"})
    source = {"kind": "conversation_input", "turn_id": turn["id"], "conversation_id": chat["id"],
              "member_id": "member"}
    if revised:
        message = "按刚才讨论，自主制作30秒英文脱壳机介绍"
        chat = repo.get(chat["id"], "workspace", "member")
        chat = repo.save_draft(chat["id"], "workspace", "member", message, chat["draft_version"])
        input_id = str(uuid4())
        repo.enqueue_input(chat["id"], "workspace", "member", target_turn_id=turn["id"],
            client_request_id=input_id, message=message, mode="steer", expected_draft_version=chat["draft_version"],
            expected_reference_version=0, request_context={"kind": "home"})
        repo.transition_input(chat["id"], "workspace", "member", input_id, "consumed")
        repo.transition_input(chat["id"], "workspace", "member", input_id, "completed")
        source["input_id"] = input_id
    source["message_digest"] = hashlib.sha256(message.encode()).hexdigest()
    repo.finish_turn(turn["id"], "completed", "窗口已采用。")
    roots = repo.register_creation_task_goals(turn["id"], "workspace", "member",
        {"request_id": "creation-request", "window_id": "adopted-window", "source": source},
        [{"kind": "task", "task_id": "task", "stage": "montage",
          "target_snapshot": {"timeline_version_id": "version:one", "timeline_sequence": 1}}])
    assert len(roots) == 1
    root = repo.get_turn(roots[0])
    assert repo.update_goal_checkpoint(root["id"], status="waiting_result", waiting_reason="await_output",
        event_id=root["events"][0]["event_id"])
    return root, repo.get_task_goal(root["id"])


@pytest.mark.parametrize("revised", [False, True])
def test_duration_gap_reaches_existing_agent_continuation_without_completed_checkpoint(repo, observed_delivery, revised):
    root, goal = adopted_creation_goal(repo, revised=revised)
    assert "source_input_id" not in goal
    assert bool(goal["creation_adoption"]["source"].get("input_id")) == revised
    observed_delivery["goal"] = goal
    checkpoint = GoalResultProjection.completed_output(**observed_delivery)
    assert checkpoint["status"] == "incomplete"
    task = SimpleNamespace(current_timeline_version_id="version:one")
    fresh = GoalResultProjection.ready(task, observed_delivery["version"], None,
        {"execution_status": observed_delivery["actual"]}, True, [], [], checkpoint)
    assert fresh["status"] == "ready" and "completed_output_checkpoint" not in fresh
    assert fresh["production_result"]["video_ready"] is True
    emit(repo, root, phase="observation", result_kind="observation")
    svc, calls = service(repo, response={"edit_request": {"request_id": "duration-repair"}}, readiness=fresh)
    assert svc.reconcile() == 1 and len(calls) == 1
    claim, verified = calls[0]
    assert claim["goal"]["original_instruction"] == goal["original_instruction"]
    assert verified["facts"]["delivery_duration"]["delta_seconds"] == 4.64
    assert "read_current_task" in verified["facts"]["delivery_duration"]["repair_action"]
    persisted = ConversationRepository(repo.database_path).get_task_goal(root["id"])
    assert persisted["status"] == "waiting_result" and "completed_output_checkpoint" not in persisted
    assert svc.reconcile() == 0 and len(calls) == 1


def test_proposal_waits_real_adoption_then_new_version_drives_remaining_goal(repo):
    chat, turn, _, _ = original(repo)
    emit(repo, turn, status="proposed")
    svc, calls = service(repo)
    assert svc.reconcile() == 1 and calls == []
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_adoption"
    event = emit(repo, turn, status="confirmed", phase="apply", result_version_id="version:two")
    svc, calls = service(repo, response={"execution_status": {"job": {"id": "render:one", "status": "queued"}}},
        readiness={"status": "ready", "task_version": {"timeline_version_id": "version:two", "timeline_sequence": 2}})
    assert svc.reconcile() == 1
    assert calls[0][0]["event"] == event
    assert calls[0][0]["request_context"]["target_snapshot"]["timeline_version_id"] == "version:two"
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_result"
    assert len(repo.list_turns(chat["id"])) == 2


@pytest.mark.parametrize("status", ["rejected", "cancelled", "stopped", "stale"])
def test_actual_rejection_stop_cancel_or_stale_cannot_run_model(repo, status):
    _, turn, _, _ = original(repo)
    emit(repo, turn, status=status, phase="decision")
    svc, calls = service(repo)
    assert svc.reconcile() == 0 and calls == []
    assert repo.get_task_goal(turn["id"])["status"] == "invalidated"


def test_new_real_user_instruction_invalidates_prior_goal_and_late_registration(repo):
    chat, turn, _, _ = original(repo)
    emit(repo, turn)
    latest = repo.get(chat["id"], "workspace", "member")
    latest = repo.save_draft(chat["id"], "workspace", "member", "只读取当前结果", latest["draft_version"])
    new, _ = repo.create_turn_idempotent(chat["id"], "workspace", "member", latest["draft_text"],
        str(uuid4()), latest["draft_version"], request_context={"kind": "task", "task_id": "task"})
    repo.finish_turn(new["id"], "completed", "已读取。")
    svc, calls = service(repo)
    assert svc.reconcile() == 0 and calls == []
    assert repo.get_task_goal(turn["id"])["waiting_reason"] == "new_user_instruction"
    assert repo.register_task_goal(turn["id"]) is None


@pytest.mark.parametrize("ready", ["waiting_identity", "invalidated"])
def test_revoked_identity_or_unrelated_version_prevents_continuation(repo, ready):
    _, turn, _, _ = original(repo)
    emit(repo, turn)
    svc, calls = service(repo, readiness={"status": ready, "reason": "membership_revoked_or_version_changed"})
    assert svc.reconcile() == 0 and calls == []
    assert repo.get_task_goal(turn["id"])["status"] == ready


def test_unknown_submission_only_waits_and_known_no_effect_can_finitely_correct(repo):
    _, turn, _, _ = original(repo)
    event = emit(repo, turn, status="result_unknown", phase="request")
    svc, calls = service(repo)
    assert svc.reconcile() == 0 and calls == []
    assert event["event_id"] in repo.get_task_goal(turn["id"])["handled_event_ids"]
    # A later actual query result is a new persisted event, never a replay of
    # the original submission or a new user instruction.
    emit(repo, turn, status="not_submitted", phase="request")
    svc, calls = service(repo, readiness={"status": "ready", "task_version": {}, "no_side_effect": False})
    assert svc.reconcile() == 0 and calls == []
    svc, calls = service(repo)
    assert svc.reconcile() == 1 and len(calls) == 1


def test_no_progress_and_round_cap_end_automatic_model_calls(repo):
    _, turn, _, _ = original(repo)
    emit(repo, turn)
    svc, calls = service(repo, response={})
    assert svc.reconcile() == 1
    assert repo.get_task_goal(turn["id"])["status"] == "no_progress"
    assert svc.reconcile() == 0 and len(calls) == 1
    _, bounded, _, _ = original(repo)
    emit(repo, bounded)
    svc, calls = service(repo, response={"media_job": {"job_id": "observation:one", "status": "queued"}}, max_rounds=1)
    assert svc.reconcile() == 1
    child = {"id": calls[0][0]["turn_id"]}
    emit(repo, child, result_kind="output_observation")
    assert svc.reconcile() == 0 and len(calls) == 1
    assert repo.get_task_goal(bounded["id"])["status"] == "limit_reached"


def test_interrupted_claim_is_not_replayed_but_new_actual_result_can_continue(repo):
    _, turn, _, _ = original(repo)
    emit(repo, turn)
    candidate = repo.goal_event_candidates()[0]
    claimed = repo.claim_goal_event(candidate, {"timeline_version_id": "version:one", "timeline_sequence": 1}, max_rounds=6)
    assert repo.fail_interrupted_turns() == 1
    svc, calls = service(ConversationRepository(repo.database_path))
    assert svc.reconcile() == 0 and calls == []
    emit(repo, {"id": claimed["turn_id"]}, result_kind="output_observation")
    assert svc.reconcile() == 1 and len(calls) == 1


def test_cannot_claim_an_event_from_another_goal(repo):
    chat, turn, _, _ = original(repo)
    event = emit(repo, turn)
    other_chat, other, _, _ = original(repo)
    emit(repo, other)
    candidate = repo.goal_event_candidates()[0]
    candidate["source_turn_id"] = other["id"]
    candidate["event"] = event
    with pytest.raises(ValueError, match="持久化的业务事件"):
        repo.claim_goal_event(candidate, {"timeline_version_id": "version:one", "timeline_sequence": 1}, max_rounds=6)
    assert len(repo.list_turns(chat["id"])) == len(repo.list_turns(other_chat["id"])) == 1


@pytest.mark.parametrize("kind,receipt,event_fields", [
    ("media_job", {"job_id": "png:one", "status": "succeeded"}, {"job_id": "png:one"}),
    ("media_job", {"job_id": "unrelated:old", "status": "succeeded"}, {"job_id": "png:one"}),
    ("edit_preview", {"proposal": {"id": "proposal:old", "status": "confirmed"}}, {"proposal_id": "proposal:old"}),
    ("edit_request", {"request_id": "request:old", "status": "recorded"}, {"request_id": "request:old"}),
    ("execution_status", {"job": {"id": "render:old", "status": "succeeded"}}, {"job_id": "render:old"}),
])
def test_reading_settled_old_receipts_does_not_wait_for_an_event_that_will_never_arrive(repo, kind, receipt, event_fields):
    _, turn, _, _ = original(repo)
    event = emit(repo, turn, **event_fields)
    before = list(repo.get_turn(turn["id"])["events"])
    svc, calls = service(repo, response={kind: receipt}, action_refs=[])
    assert svc.reconcile() == 1 and len(calls) == 1
    goal = repo.get_task_goal(turn["id"])
    assert goal["status"] == "no_progress" and "complete_original_instruction" in goal["unfinished_steps"]
    assert event in repo.get_turn(turn["id"])["events"] == before
    assert svc.reconcile() == 0


def test_new_child_action_already_finished_still_waits_for_its_own_actual_event(repo):
    _, turn, _, _ = original(repo)
    emit(repo, turn, job_id="png:one")
    svc, calls = service(repo, response={"media_job": {"job_id": "observation:new", "status": "succeeded"}})
    assert svc.reconcile() == 1
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_result"
    child = {"id": calls[0][0]["turn_id"]}
    emit(repo, child, job_id="observation:new", result_kind="observation")
    readonly, reads = service(repo, response={"media_job": {"job_id": "observation:new", "status": "succeeded"}}, action_refs=[])
    assert readonly.reconcile() == 1 and len(reads) == 1
    assert repo.get_task_goal(turn["id"])["status"] == "no_progress"


@pytest.mark.parametrize("job_id,expected", [("png:one", "waiting_result"), ("unrelated:pending", "no_progress")])
def test_pending_work_must_belong_to_the_original_goal_or_a_new_child_action(repo, job_id, expected):
    _, turn, _, _ = original(repo)
    emit(repo, turn, job_id="png:one")
    svc, _ = service(repo, response={"media_job": {"job_id": job_id, "status": "running"}}, action_refs=[])
    assert svc.reconcile() == 1
    assert repo.get_task_goal(turn["id"])["status"] == expected


def shutdown_child(repo, *, status="failed", reason="api_shutdown"):
    chat, turn, request_id, context = original(repo)
    emit(repo, turn, job_id="png:one")
    claim = repo.claim_goal_event(repo.goal_event_candidates()[0], {"timeline_version_id": "version:one", "timeline_sequence": 1}, max_rounds=6)
    repo.finish_turn(claim["turn_id"], status, "接续中断。", result={"reason": reason})
    repo.settle_goal_round(claim)
    return chat, turn, request_id, context, claim


def test_actual_shutdown_result_can_be_adopted_without_rewriting_failed_turn_or_original_instruction(repo):
    chat, turn, request_id, context, claim = shutdown_child(repo)
    original_events = list(repo.get_turn(turn["id"])["events"])
    event = {"type": "business_result", "event_id": str(uuid4()), "task_id": "task",
             "phase": "preview", "status": "proposed", "proposal_id": "proposal:shutdown", "actionable": True}
    patch = {"edit_preview": {"proposal": {"id": "proposal:shutdown", "status": "proposed"}}}
    assert repo.project_business_result(claim["turn_id"], "workspace", "member", event,
        response_patch=patch, message="实际修改预览已完成，可以决定是否采用。")
    child = repo.get_turn(claim["turn_id"])
    assert child["status"] == "failed" and child["result"]["reason"] == "api_shutdown"
    assert child["result"]["response"]["edit_preview"] == patch["edit_preview"]
    assert child["user_message"] == "" and repo.get_turn(turn["id"])["user_message"] == turn["user_message"]
    assert repo.get_turn(turn["id"])["events"] == original_events
    assert repo.request_context(request_id, chat["id"], "workspace", "member") == context
    assert repo.project_business_result(claim["turn_id"], "workspace", "member", event,
        response_patch=patch, message="实际修改预览已完成，可以决定是否采用。")
    assert repo.get_turn(claim["turn_id"])["events"] == child["events"]
    assert repo.get_turn(claim["turn_id"])["assistant_text"] == child["assistant_text"]
    svc, calls = service(repo)
    assert svc.reconcile() == 1 and calls == []
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_adoption"


@pytest.mark.parametrize("status,reason,goal_status", [
    ("stopped", "api_shutdown", "waiting_unknown"),
    ("failed", "tool_failed", "waiting_unknown"),
    ("failed", "api_shutdown", "invalidated"),
    ("failed", "api_shutdown", "waiting_identity"),
    ("failed", "api_shutdown", "no_progress"),
    ("failed", "api_shutdown", "limit_reached"),
])
def test_shutdown_patch_does_not_reopen_user_stop_other_failure_or_an_inactive_goal(repo, status, reason, goal_status):
    _, turn, _, _, claim = shutdown_child(repo, status=status, reason=reason)
    if goal_status == "invalidated":
        repo.invalidate_task_goal(turn["id"], "user_stopped_or_membership_revoked")
    else:
        repo.update_goal_checkpoint(turn["id"], status=goal_status, waiting_reason="test_actual_lifecycle_state")
    event = {"type": "business_result", "event_id": str(uuid4()), "task_id": "task", "status": "proposed"}
    assert repo.project_business_result(claim["turn_id"], "workspace", "member", event,
        response_patch={"edit_preview": {"proposal": {"id": "proposal:shutdown", "status": "proposed"}}},
        message="实际修改已返回。")
    child = repo.get_turn(claim["turn_id"])
    assert child["status"] == status and child["result"]["reason"] == reason
    assert not child["result"].get("response", {}).get("edit_preview")


def test_late_shutdown_proposal_does_not_patch_an_old_source_after_goal_moved_to_another_child(repo):
    _, turn, _, _, old = shutdown_child(repo)
    emit(repo, {"id": old["turn_id"]}, job_id="observation:old", result_kind="observation")
    current = repo.claim_goal_event(repo.goal_event_candidates()[0], {"timeline_version_id": "version:one", "timeline_sequence": 1}, max_rounds=6)
    repo.finish_turn(current["turn_id"], "completed", "新检查已排队。", result={"response": {
        "media_job": {"job_id": "observation:new", "status": "queued"}},
        "goal_business_actions": [{"kind": "media_job", "id": "observation:new"}]})
    repo.settle_goal_round(current)
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_result"
    event = {"type": "business_result", "event_id": str(uuid4()), "task_id": "task", "status": "proposed"}
    assert repo.project_business_result(old["turn_id"], "workspace", "member", event,
        response_patch={"edit_preview": {"proposal": {"id": "proposal:old", "status": "proposed"}}}, message="旧结果已保留。")
    assert not repo.get_turn(old["turn_id"])["result"].get("response", {}).get("edit_preview")


def completed_source(*, name="title.png", inputs=None, job_id="png:one", sha256="a" * 64):
    return {"name": name, "requested_output": {"name": name, "width": 48, "height": 48, "max_bytes": 65536},
        "input_refs": inputs or [], "job_id": job_id, "source_ref": f"artifacts/{job_id}/{name}",
        "artifact_id": f"artifact:{job_id}", "sha256": sha256, "size_bytes": 174,
        "width": 48, "height": 48, "base_version_id": "version:one"}


def test_completed_source_slot_is_durable_and_scoped_to_the_active_automatic_child(repo):
    _, turn, _, _ = original(repo)
    emit(repo, turn, job_id="png:one")
    slot = completed_source()
    duplicate = completed_source(job_id="png:duplicate", sha256="b" * 64)
    assert repo.record_goal_completed_sources(turn["id"], [slot, duplicate])
    claim = repo.claim_goal_event(repo.goal_event_candidates()[0], {"timeline_version_id": "version:one", "timeline_sequence": 1}, max_rounds=6)
    matched = repo.completed_goal_source(claim["turn_id"], requested_outputs=[slot["requested_output"]], input_refs=[])
    assert matched["job_id"] == "png:one" and matched["sha256"] == "a" * 64
    assert len(repo.get_task_goal(turn["id"])["completed_source_slots"]) == 1
    restarted = ConversationRepository(repo.database_path)
    assert restarted.completed_goal_source(claim["turn_id"], requested_outputs=[slot["requested_output"]], input_refs=[]) == matched
    assert repo.completed_goal_source(turn["id"], requested_outputs=[slot["requested_output"]], input_refs=[]) is None
    assert repo.completed_goal_source(claim["turn_id"], requested_outputs=[{"name": "second-source.png"}], input_refs=[]) is None
    assert repo.completed_goal_source(claim["turn_id"], requested_outputs=[slot["requested_output"]],
        input_refs=[{"name": "input.png", "source_ref": "other", "expected_artifact_id": "input:one", "expected_sha256": "c" * 64}]) is None
    repo.invalidate_task_goal(turn["id"], "new_user_instruction_or_revoked")
    assert not repo.record_goal_completed_sources(turn["id"], [slot])
    assert repo.completed_goal_source(claim["turn_id"], requested_outputs=[slot["requested_output"]], input_refs=[]) is None


def test_real_source_facts_and_event_checkpoint_are_given_to_the_new_turn(repo):
    _, turn, _, _ = original(repo)
    emit(repo, turn, job_id="png:one")
    slot = completed_source()
    svc, calls = service(repo, readiness={"status": "ready", "task_version": {"timeline_version_id": "version:one", "timeline_sequence": 1}, "facts": {},
                                        "completed_source_slots": [slot]})
    assert svc.reconcile() == 1
    assert calls[0][0]["goal"]["completed_source_slots"][0]["artifact_id"] == slot["artifact_id"]
    assert calls[0][0]["goal"]["next_checkpoint"] == "read_completed_source_and_continue"
    assert calls[0][0]["goal"]["unfinished_steps"][0] == "read_completed_source_and_continue"


@pytest.mark.parametrize("readiness", ["ready", "waiting_adoption"])
@pytest.mark.parametrize("observed_status", ["succeeded", "stale"])
def test_observation_event_cannot_start_execution_while_actual_proposal_waits_for_adoption(repo, readiness, observed_status):
    _, turn, _, _ = original(repo)
    emit(repo, turn, job_id="png:one")
    svc, initial = service(repo)
    assert svc.reconcile() == 1
    child = {"id": initial[0][0]["turn_id"]}
    observed = emit(repo, child, status=observed_status, phase="observation", job_id="observation:one", result_kind="observation")
    blocked, calls = service(repo, readiness={"status": readiness, "task_version": {}, "reason": "actual_proposal_is_proposed",
                                             "completed_source_slots": [completed_source()]})
    assert blocked.reconcile() == 1 and calls == []
    goal = repo.get_task_goal(turn["id"])
    assert goal["status"] == "waiting_adoption" and observed["event_id"] in goal["handled_event_ids"]
    assert goal["next_checkpoint"] == "await_real_adoption"
    assert blocked.reconcile() == 0
    emit(repo, child, status="confirmed", phase="apply", proposal_id="proposal:one", result_version_id="version:two")
    adopted, after = service(repo, response={}, readiness={"status": "ready",
        "task_version": {"timeline_version_id": "version:two", "timeline_sequence": 2},
        "facts": {"adopted_operation": {"operation_id": "actual:one", "result_version_id": "version:two"}}})
    assert adopted.reconcile() == 1 and len(after) == 1
    assert after[0][0]["event"]["status"] == "confirmed"
    assert after[0][0]["goal"]["next_checkpoint"] == "continue_after_real_adoption"


def test_host_pending_proposal_gate_can_stop_a_goal_still_waiting_for_preview_result(repo):
    _, turn, _, _ = original(repo)
    emit(repo, turn, job_id="png:one")
    preview, first = service(repo, response={"media_job": {"job_id": "preview:one", "status": "queued", "result_kind": "edit_preview"}})
    assert preview.reconcile() == 1
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_result"
    observed = emit(repo, {"id": first[0][0]["turn_id"]}, phase="observation", result_kind="observation", job_id="observation:one")
    blocked, calls = service(repo, readiness={"status": "waiting_adoption", "reason": "actual_pending_preview_or_proposal",
                                            "completed_source_slots": [completed_source()]})
    assert blocked.reconcile() == 1 and calls == []
    goal = repo.get_task_goal(turn["id"])
    assert goal["status"] == "waiting_adoption" and observed["event_id"] in goal["handled_event_ids"]
    assert goal["completed_source_slots"][0]["job_id"] == "png:one"


def _adopted_groups_for_resume(tmp_path, *, groups=1):
    from app.production import CreationPlan, ProductionRepository
    from app.creation.contracts import CreationTask
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.creation_execution import CreationExecutionActor
    production=ProductionRepository(tmp_path/'resume.sqlite3',tmp_path/'library')
    authorizations=TaskAuthorizationUseCase(production,lambda _: 'ready')
    conversations=ConversationRepository(production.database_path,task_goal_input_authorized=authorizations.adopted_goal_input_valid)
    chat=conversations.create('workspace','member','home','home')
    def human(message,context=None):
        current=conversations.get(chat['id'],'workspace','member')
        current=conversations.save_draft(chat['id'],'workspace','member',message,current['draft_version'])
        turn,_=conversations.create_turn_idempotent(chat['id'],'workspace','member',message,str(uuid4()),
            current['draft_version'],request_context=context or {'kind':'home'})
        return conversations.get_turn(turn['id'])
    continuation=GoalContinuationService(conversations,verify_current_goal=lambda *_:None,run_goal_turn=lambda *_:None)
    adopted=[]
    for group in range(groups):
        turn=human('自主制作中英文各一条介绍')
        source={'kind':'conversation_input','turn_id':turn['id'],'conversation_id':chat['id'],'member_id':'member',
            'message_digest':hashlib.sha256(turn['user_message'].encode()).hexdigest()}
        intent={'kind':'goal_delegation','source':source}
        saved=production.create_creation_request('workspace',request_key=turn['id'],input_fingerprint=source['message_digest'],
            goal=turn['user_message'],target_count=2,output_relation='variants',requirements={'creation_intent':intent},
            material_scope={'kind':'workspace_library'},clarification_questions=[],verified_owner_user_id='member')
        entries=[]
        for language in ('zh-CN','en'):
            identity=f'{group}-{language}'
            plan=CreationPlan('plan-'+identity,'workspace',saved['goal'],language,1,language,'9:16',30,
                [],[],None,[],0,0,'metadata fixture',[],[],[],'fingerprint-'+identity)
            task=CreationTask('task-'+identity,'workspace',plan.id,plan.name,'knowledge_explanation')
            entries.append((plan,task))
        saved=production.save_creation_request_window('workspace',saved['id'],expected_revision=saved['revision'],
            input_fingerprint='window-'+str(group),entries=entries,verified_owner_user_id='member')
        window=saved['current_window']
        saved=production.confirm_creation_request_window('workspace',saved['id'],window['id'],expected_revision=saved['revision'],
            input_fingerprint=window['input_fingerprint'],verified_owner_user_id='member',adoption_kind='delegated_system')
        for item in saved['current_window']['entries']:
            item['task']['timeline_sequence']=0  # No timeline exists in this metadata-only case.
        conversations.finish_turn(turn['id'],'completed','已采用原任务组')
        roots=continuation.register_creation_request(turn['id'],'workspace','member',{**saved,'intent':intent})
        assert len(roots)==2
        for root_id in roots:
            goal=conversations.get_task_goal(root_id)
            authorizations.bind_business_context(production.get_creation_task('workspace',goal['task_id']),
                CreationExecutionActor('workspace','member'),goal,memory={'revision':1})
        adopted.append((saved,roots))
    return SimpleNamespace(production=production,conversations=conversations,authorizations=authorizations,
        service=continuation,human=human,adopted=adopted,chat=chat)


@pytest.mark.parametrize('additional_input',[False,True])
def test_home_resume_reuses_adopted_cn_en_roots_and_is_atomic_idempotent(tmp_path,monkeypatch,additional_input):
    from app import main
    case=_adopted_groups_for_resume(tmp_path)
    saved,roots=case.adopted[0]
    original=[case.conversations.get_task_goal(root) for root in roots]
    expires={goal['task_id']:case.production.get_task_authorization('workspace',goal['task_id']).expires_at for goal in original}
    turn=case.human('继续刚才已经采用的中英文两条')
    input_id=None
    if additional_input:
        input_id=str(uuid4())
        chat=case.conversations.get(case.chat['id'],'workspace','member')
        chat=case.conversations.save_draft(chat['id'],'workspace','member','继续原来的两条，先查原请求',chat['draft_version'])
        case.conversations.enqueue_input(chat['id'],'workspace','member',target_turn_id=turn['id'],client_request_id=input_id,
            message=chat['draft_text'],mode='steer',expected_draft_version=chat['draft_version'],expected_reference_version=0,
            request_context={'kind':'home'})
        case.conversations.transition_input(chat['id'],'workspace','member',input_id,'consumed')
    assert all(case.conversations.get_task_goal(root)['status']=='invalidated' for root in roots)
    monkeypatch.setattr(main,'conversation_repo',case.conversations)
    monkeypatch.setattr(main,'_goal_service',lambda:case.service)
    monkeypatch.setattr(main,'_task_authorizations_for_goal',lambda:case.authorizations)
    monkeypatch.setattr(main,'_assistant_conversation_for_request',lambda *_:('workspace','member',case.chat))
    monkeypatch.setattr(main,'_wake_assistant_continuations',lambda:None)
    request=SimpleNamespace(headers={'x-assistant-input-id':input_id} if input_id else {})
    response=main.resume_assistant_creation_request(request,turn['id'],None)
    assert response['status']=='resumed' and response['request_id']==saved['id']
    assert {item['task_id'] for item in response['tasks']}=={goal['task_id'] for goal in original}
    current=[case.conversations.get_task_goal(root) for root in roots]
    assert all(goal['revision']==1 and goal['creation_resume']['source']['turn_id']==turn['id'] for goal in current)
    assert all(goal['creation_adoption']==before['creation_adoption'] for goal,before in zip(current,original))
    duplicate=main.resume_assistant_creation_request(request,turn['id'],saved['id'])
    assert duplicate['status']=='already_running'
    assert all(case.conversations.get_task_goal(root)['revision']==1 for root in roots)
    assert case.conversations.get_turn(turn['id'])['user_message']=='继续刚才已经采用的中英文两条'
    with pytest.raises(ValueError):
        case.conversations.task_turn_source(turn['id'],'workspace','member',original[0]['task_id'])
    case.conversations.finish_turn(turn['id'],'failed' if additional_input else 'completed','已安排核对原任务',
        result={'reason':'api_shutdown'} if additional_input else None)
    claims=[]
    for candidate in case.conversations.goal_event_candidates():
        claim=case.conversations.claim_goal_event(candidate,{'timeline_version_id':None,'timeline_sequence':0},max_rounds=6)
        if claim:
            claims.append(claim)
            case.conversations.finish_turn(claim['turn_id'],'completed','已读取当前任务')
    assert {claim['goal']['task_id'] for claim in claims}=={goal['task_id'] for goal in original}
    assert all(case.production.get_task_authorization('workspace',task).expires_at==expiry for task,expiry in expires.items())
    assert len(case.production.list_creation_tasks('workspace',viewer_user_id='member'))==2


def test_home_resume_selection_and_original_authorization_are_required(tmp_path):
    from dataclasses import replace
    from datetime import datetime,timezone
    case=_adopted_groups_for_resume(tmp_path,groups=2)
    turn=case.human('继续原来采用的任务组')
    response=case.service.resume_creation_request('workspace','member',turn['id'],None,None,case.authorizations)
    assert response['status']=='needs_selection' and len(response['candidates'])==2
    absent=case.service.resume_creation_request('workspace','member',turn['id'],None,'another-conversation-request',case.authorizations)
    assert absent['status']=='blocked' and absent['request_id'] is None
    with pytest.raises(ValueError):
        case.service.resume_creation_request('workspace','another-member',turn['id'],None,case.adopted[0][0]['id'],case.authorizations)
    with pytest.raises(ValueError):
        case.service.resume_creation_request('workspace','member',turn['id'],'stale-input',case.adopted[0][0]['id'],case.authorizations)
    saved,roots=case.adopted[0]
    for root in roots:
        goal=case.conversations.get_task_goal(root)
        authority=case.production.get_task_authorization('workspace',goal['task_id'])
        case.production.save_task_authorization(replace(authority,revision=authority.revision+1,
            revoked_at=datetime.now(timezone.utc)),expected_revision=authority.revision)
    denied=case.service.resume_creation_request('workspace','member',turn['id'],None,saved['id'],case.authorizations)
    assert denied['status']=='blocked' and all(item['status']=='blocked' for item in denied['tasks'])
    assert all(case.conversations.get_task_goal(root)['revision']==0 for root in roots)
    case.conversations.finish_turn(turn['id'],'completed','未恢复被撤权的任务组')
    new_source=case.human('继续并使用刚选中的资料',{'kind':'home',
        'knowledge_reference':{'knowledge_item_id':'new-private-source','version':'v1'}})
    other,other_roots=case.adopted[1]
    outside=case.service.resume_creation_request('workspace','member',new_source['id'],None,other['id'],case.authorizations)
    assert outside['status']=='blocked' and all('范围外' in item['reason'] for item in outside['tasks'])
    assert all(case.conversations.get_task_goal(root)['revision']==0 for root in other_roots)


def test_home_resume_rolls_back_all_roots_when_scope_inheritance_fails(tmp_path,monkeypatch):
    case=_adopted_groups_for_resume(tmp_path)
    saved,roots=case.adopted[0];turn=case.human('继续原任务')
    inherit=case.authorizations.inherit_business_context;calls=[]
    def fail_second(*args,**kwargs):
        calls.append(True)
        inherit(*args,**kwargs)
        if len(calls)==2:
            raise PermissionError('scope no longer authorized')
    monkeypatch.setattr(case.authorizations,'inherit_business_context',fail_second)
    with pytest.raises(PermissionError):
        case.service.resume_creation_request('workspace','member',turn['id'],None,saved['id'],case.authorizations)
    assert all(case.conversations.get_task_goal(root)['revision']==0 for root in roots)
    assert not any(event['type']=='creation_request_resumed' for event in case.conversations.get_turn(turn['id'])['events'])
    for root in roots:
        goal=case.conversations.get_task_goal(root)
        with pytest.raises(KeyError):
            case.production.get_task_event('workspace',case.authorizations.business_context_id({**goal,'revision':1}))


def test_home_resume_reconciles_unknown_original_request_before_any_new_business(tmp_path):
    from app.creation_execution import CreationExecutionActor
    case=_adopted_groups_for_resume(tmp_path)
    saved,roots=case.adopted[0];root=roots[0]
    goal=case.conversations.get_task_goal(root);task=case.production.get_creation_task('workspace',goal['task_id'])
    request_id=str(uuid4())
    case.production.claim_edit_request(task,'member',request_id,'observation',{'original':'fixed'})
    candidate=next(item for item in case.conversations.goal_event_candidates() if item['root']['id']==root)
    claim=case.conversations.claim_goal_event(candidate,{'timeline_version_id':None,'timeline_sequence':0},max_rounds=6)
    case.conversations.append_event(claim['turn_id'],{'type':'tool_end','failure':{'submission_effect':'unknown',
        'request_id':request_id,'query_tool':'read_task_observation_request'}})
    case.conversations.finish_turn(claim['turn_id'],'failed',result={'reason':'goal_runner_interrupted'})
    case.conversations.settle_goal_round(claim)
    turn=case.human('继续原任务，先核对原结果')
    assert case.service.resume_creation_request('workspace','member',turn['id'],None,saved['id'],case.authorizations)['status']=='resumed'
    current=case.conversations.get_task_goal(root)
    def read_request(task,actor,identity,phase):
        event_id,event=GoalResultProjection(case.production,case.conversations).request_claim(task,'member',identity,phase)
        original=case.production.read_edit_request_result(task,event_id)
        return {'status':'unknown' if original is None else 'recorded','result':original}
    def forbidden(*args,**kwargs): raise AssertionError('must not submit or select new work before reconciliation')
    outcome=case.service.verify_resume_business(current,task,CreationExecutionActor('workspace','member'),
        SimpleNamespace(execution_sources=forbidden),GoalResultProjection(case.production,case.conversations),
        read_request=read_request,read_media=forbidden)
    assert outcome=={'status':'waiting_unknown','reason':'read_original_request_result'}


def test_home_resume_explicit_stop_does_not_change_goals_events_or_authority(tmp_path):
    case=_adopted_groups_for_resume(tmp_path)
    saved,roots=case.adopted[0]
    turn=case.human('不要继续，先停下')
    before=[case.conversations.get_turn(root) for root in roots]
    before_turn=case.conversations.get_turn(turn['id'])
    authorities=[case.production.get_task_authorization('workspace',case.conversations.get_task_goal(root)['task_id']) for root in roots]
    with pytest.raises(ValueError,match='停止'):
        case.service.resume_creation_request('workspace','member',turn['id'],None,saved['id'],case.authorizations)
    assert [case.conversations.get_turn(root) for root in roots]==before
    assert case.conversations.get_turn(turn['id'])==before_turn
    assert [case.production.get_task_authorization('workspace',case.conversations.get_task_goal(root)['task_id']) for root in roots]==authorities
    case.conversations.finish_turn(turn['id'],'completed','已保留停止要求')
    continued=case.human('继续刚才暂停的任务')
    resumed=case.service.resume_creation_request('workspace','member',continued['id'],None,saved['id'],case.authorizations)
    assert resumed['status']=='resumed'
    assert all(case.conversations.get_task_goal(root)['revision']==1 for root in roots)


@pytest.mark.parametrize('recorded_state',['queued','running','missing_job','rejected'])
def test_home_resume_recorded_request_reconciles_its_job_before_ready(tmp_path,recorded_state):
    from app.creation.contracts import JobRun,TaskEvent
    from app.creation_execution import CreationExecutionActor
    from app.task_audio import TASK_MEDIA_KIND
    case=_adopted_groups_for_resume(tmp_path)
    saved,roots=case.adopted[0];root=roots[0]
    goal=case.conversations.get_task_goal(root);task=case.production.get_creation_task('workspace',goal['task_id'])
    actor=CreationExecutionActor('workspace','member');owner=GoalResultProjection(case.production,case.conversations)
    request_id=str(uuid4())
    event_id,_=case.production.claim_edit_request(task,'member',request_id,'observation',{'original':'fixed'})
    candidate=next(item for item in case.conversations.goal_event_candidates() if item['root']['id']==root)
    claim=case.conversations.claim_goal_event(candidate,{'timeline_version_id':None,'timeline_sequence':0},max_rounds=6)
    case.conversations.append_event(claim['turn_id'],{'type':'tool_end','failure':{'submission_effect':'unknown',
        'request_id':request_id,'query_tool':'read_task_observation_request'}})
    case.conversations.finish_turn(claim['turn_id'],'failed',result={'reason':'goal_runner_interrupted'})
    case.conversations.settle_goal_round(claim)
    receipt={};code=202;job=None
    if recorded_state in {'queued','running'}:
        job=case.production.create_job_run(JobRun('original-request-job','workspace',task.id,TASK_MEDIA_KIND,
            'original-request-job','a'*64))
        case.production.append_task_event(TaskEvent('event_'+job.id+'_start','workspace',task.id,job.id,
            'task_media_submitted',{**actor.identity_payload(),'arguments':{'_typed_source':{
                'assistant_turn_id':claim['turn_id'],'request_id':request_id,'conversation_id':case.chat['id']}}}))
        receipt={'job_id':job.id,'status':'queued','input_digest':job.input_digest,'result_kind':'observation'}
        if recorded_state=='running':
            case.production.claim_job_run('workspace',job.id)
    elif recorded_state=='rejected':
        code=409;receipt={'detail':{'error':'original_refusal','reason':'原观察输入被拒绝'}}
    case.production.save_edit_request_result(task,event_id,status_code=code,response=receipt)
    assert not (case.conversations.get_turn(claim['turn_id'])['result'].get('response') or {}).get('media_job')
    turn=case.human('继续原任务，先核对原回执')
    assert case.service.resume_creation_request('workspace','member',turn['id'],None,saved['id'],case.authorizations)['status']=='resumed'
    reads=[]
    def read_request(task,actor,identity,phase):
        key,event=owner.request_claim(task,'member',identity,phase)
        recorded=case.production.read_edit_request_result(task,key)
        return {'status':'recorded','result_status_code':recorded['status_code'],'result':recorded['response']}
    def read_media(task,actor,job_id):
        reads.append(job_id)
        current=case.production.get_job_run('workspace',job_id)
        source=case.production.get_task_event('workspace','event_'+job_id+'_start')
        owner.require_media_task(task.id,current)
        assert CreationExecutionActor.from_event('workspace',source.payload)==actor
        return {'job_id':current.id,'status':current.status}
    def forbidden(*args,**kwargs): raise AssertionError('original request must settle before any other work')
    outcome=case.service.verify_resume_business(case.conversations.get_task_goal(root),task,actor,
        SimpleNamespace(execution_sources=forbidden),owner,read_request=read_request,read_media=read_media)
    if job:
        assert outcome['status']=='waiting_result' and outcome['original_job']['status']==recorded_state
        assert reads==[job.id] and len(case.production.list_job_runs('workspace',task.id))==1
    elif recorded_state=='missing_job':
        assert outcome['status']=='waiting_unknown' and outcome['reason']=='original_request_job_not_located' and reads==[]
    else:
        assert outcome['status']=='waiting_unknown' and outcome['reason']=='原观察输入被拒绝' and reads==[]
        assert outcome['original_request']['result']==receipt


def test_slow_pending_input_does_not_block_other_goal_receipt_reconciliation(repo):
    chat, turn, _, context = original(repo)
    current=repo.get(chat['id'],'workspace','member')
    draft=repo.save_draft(chat['id'],'workspace','member','只查进度',current['draft_version'])
    repo.enqueue_input(chat['id'],'workspace','member',target_turn_id=turn['id'],client_request_id='query',
        message='只查进度',mode='steer',expected_draft_version=draft['draft_version'],expected_reference_version=0,
        request_context={**context,'selected_reference':None})
    _,later,_,_=original(repo)
    entered,release=threading.Event(),threading.Event()
    business=SimpleNamespace(claim_pending_input=lambda candidate:repo.claim_pending_goal_input(candidate,
        validate=lambda conn,item,goal:item['request_context'],seal=lambda *args:None))
    def run(claim,fresh):
        entered.set()
        assert release.wait(2)
        repo.finish_turn(claim['turn_id'],'completed')
    svc=GoalContinuationService(repo,verify_current_goal=lambda *_:{'status':'ready','business':business},run_goal_turn=run)
    svc.start()
    try:
        svc.tick();assert entered.wait(1)
        same_goal_event=emit(repo,turn,status='queued',result_kind='edit_preview')
        event=emit(repo,later,status='queued',result_kind='edit_preview')
        svc.tick()
        deadline=time.monotonic()+1
        while event['event_id'] not in repo.get_task_goal(later['id'])['handled_event_ids'] and time.monotonic()<deadline:
            time.sleep(.005)
        assert event['event_id'] in repo.get_task_goal(later['id'])['handled_event_ids']
        assert repo.get_task_goal(later['id'])['round_count']==0
        current_goal=repo.get_task_goal(turn['id'])
        assert current_goal['status']=='running'
        assert same_goal_event['event_id'] not in current_goal['handled_event_ids']
        assert repo.task_turn_source(current_goal['current_turn_id'],'workspace','member','task')['raw_command']=='只查进度'
        assert svc.stop(deadline_monotonic=time.monotonic()) is False
        release.set();assert svc.stop(deadline_monotonic=time.monotonic()+1)
        assert repo.list_inputs(chat['id'],'workspace','member')[0]['status']=='completed'
        assert repo.get_task_goal(turn['id'])['original_instruction']==turn['user_message']
    finally:
        release.set();svc.stop(deadline_monotonic=time.monotonic()+1)
