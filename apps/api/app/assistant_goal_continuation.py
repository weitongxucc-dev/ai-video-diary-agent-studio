"""Resume an explicitly bound original instruction from real business results.

The existing conversation turn/receipt owns both intent and checkpoints. This
helper has no job queue, model loop, identity issuance or proposal adoption.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import json
import math
import logging
import threading
import time
from typing import Any

from app.conversation_repository import ConversationRepository


class GoalContinuationService:
    def __init__(self, repo: ConversationRepository, *,
                 verify_current_goal: Callable[[dict, dict], dict],
                 run_goal_turn: Callable[[dict, dict], None], max_rounds: int = 6) -> None:
        if not 1 <= max_rounds <= 6:
            raise ValueError("自动接续每个原目标最多允许六个新模型回合")
        self.repo = repo
        self.verify_current_goal = verify_current_goal
        self.run_goal_turn = run_goal_turn
        self.max_rounds = max_rounds
        self.stop_event = threading.Event()
        self._wake = threading.Event()
        self._paused = threading.Event()
        self._reconcile_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._thread = None
        self._scan_limit = 8

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.stop_event.is_set():
                raise RuntimeError("接续服务已停止，不能恢复旧生命周期。")
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="assistant-goal-continuation", daemon=True)
            self._thread.start()

    def tick(self, *, limit: int = 8) -> None:
        if self.stop_event.is_set() or self._paused.is_set():
            return
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("接续发现必须绑定已启动的持久服务实例。")
        self._scan_limit = limit
        # Coalesce discoveries; durable events remain the only work source.
        self._wake.set()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self._wake.wait()
            self._wake.clear()
            if self.stop_event.is_set():
                break
            try:
                self.reconcile(limit=self._scan_limit)
            except Exception:
                logging.getLogger(__name__).exception("目标接续中断；原认领及业务回执保留，未重放操作")

    def pause(self, timeout_seconds: float) -> bool:
        self._paused.set()
        if not self._reconcile_lock.acquire(timeout=max(0, timeout_seconds)):
            self._paused.clear()
            return False
        self._reconcile_lock.release()
        return True

    def resume(self) -> None:
        if self.stop_event.is_set():
            raise RuntimeError("接续服务已停止，不能恢复旧生命周期。")
        self._paused.clear()
        self._wake.set()

    def request_stop(self) -> None:
        self.stop_event.set()
        self._wake.set()

    def stop(self, *, deadline_monotonic: float) -> bool:
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout=max(0, deadline_monotonic - time.monotonic()))
            return not self._thread.is_alive()
        return True

    def register_turn(self, turn_id: str) -> dict | None:
        # Called only at the live, verified task-response boundary. Startup
        # never infers goals from pre-existing completed conversations.
        return self.repo.register_task_goal(turn_id)

    def record_script_adoption(self, turn_id: str, workspace_id: str, member_id: str, task, plan, script, source: dict) -> bool:
        from app.creation.intent_use_case import CreationIntentUseCase

        if (task.confirmed_script_id != script.id or script.task_id != task.id
                or not CreationIntentUseCase.adopted_for_execution(plan, script)):
            return False
        try:
            CreationIntentUseCase.reject_discussion_only_input(source["raw_command"])
        except ValueError:
            return False
        return self.repo.record_task_script_adoption(turn_id, workspace_id, member_id, task.id,
            script_id=script.id, script_version=script.updated_at.isoformat(), source=source)

    def register_creation_request(self, turn_id: str, workspace_id: str, member_id: str, saved: dict) -> list[str]:
        """Bind only a host-read, actually adopted window to existing task goals."""
        intent, window = saved.get("intent") or {}, saved.get("current_window")
        if intent.get("kind") not in {"goal_delegation", "final_script"} or not window or window["status"] != "confirmed":
            return []
        source = intent["source"]
        if source.get("kind") != "conversation_input" or source.get("turn_id") != turn_id:
            return []
        entries = window["entries"]
        if not 1 <= len(entries) <= 8:
            raise ValueError("接续只绑定当前已采用窗口内的任务")
        tasks = []
        for entry in entries:
            task, plan = entry["task"], entry["plan"]
            adoption = plan.get("adoption") or {}
            if (task is None or entry["task_id"] != task["id"] or task["workspace_id"] != workspace_id
                    or entry["plan_id"] != task["plan_id"] or adoption.get("plan_id") != task["plan_id"]
                    or adoption.get("kind") != "delegated_system" or adoption.get("member_id") != member_id
                    or adoption.get("request_id") != saved["id"] or adoption.get("window_id") != window["id"]
                    or adoption.get("window_input_fingerprint") != window["input_fingerprint"]
                    or adoption.get("authorization_source") != source):
                raise ValueError("任务采用回执与原制作目标不一致")
            tasks.append({"kind": "task", "task_id": task["id"], "stage": task["stage"],
                "selected_object_refs": [], "playhead_ms": None,
                "target_snapshot": {"timeline_version_id": task["current_timeline_version_id"],
                    "timeline_sequence": task["timeline_sequence"]}})
        return self.repo.register_creation_task_goals(turn_id, workspace_id, member_id,
            {"request_id": saved["id"], "window_id": window["id"],
             "input_fingerprint": window["input_fingerprint"], "source": source}, tasks)

    def observe_business_result(self, source_turn_id: str, event: dict) -> bool:
        goal = self.repo.get_task_goal(source_turn_id)
        return bool(goal and event.get("type") == "business_result" and event.get("event_id")
                    and event.get("task_id") == goal["task_id"])

    def invalidate(self, turn_id: str, reason: str, *, expected_goal: dict | None = None) -> bool:
        return self.repo.invalidate_task_goal(turn_id, reason, expected_goal=expected_goal)

    def reconcile(self, *, limit: int = 8) -> int:
        with self._reconcile_lock:
            return self._reconcile(limit=limit)

    def _reconcile(self, *, limit: int) -> int:
        advanced = 0
        for candidate in self.repo.goal_event_candidates(limit=limit):
            if self.stop_event.is_set() or self._paused.is_set():
                break
            root, event = candidate["root"], candidate["event"]
            goal, status = root["goal"], event.get("status")
            if event.get("task_id") != goal["task_id"]:
                self.invalidate(root["id"], "business_event_target_changed", expected_goal=goal)
                continue
            # A rejected user decision is final. A rejected/not-submitted
            # *request* is different: the host may prove no effect and permit
            # one of the finite new turns to correct its input.
            stale_observation_awaiting_adoption = (status == "stale" and event.get("phase") == "observation"
                                                    and goal["status"] == "waiting_adoption")
            if (status in {"stale", "cancelled", "stopped"} and not stale_observation_awaiting_adoption
                    or status == "rejected" and event.get("phase") != "request"):
                self.invalidate(root["id"], f"business_result_{status}", expected_goal=goal)
                continue
            if goal["status"] == "waiting_adoption" and status != "confirmed":
                # Observation/program results can arrive out of order, even
                # after a human adoption changed the live version. Consume the
                # old result without treating it as adoption or making the
                # pending confirmed event stale before it can be verified.
                if self.repo.update_goal_checkpoint(root["id"], status="waiting_adoption",
                        waiting_reason="await_real_adoption", event_id=event["event_id"], expected_goal=goal):
                    advanced += 1
                continue
            if status == "proposed" or (event.get("phase") == "request" and event.get("actionable")):
                if self.repo.update_goal_checkpoint(root["id"], status="waiting_adoption",
                        waiting_reason="await_real_adoption", event_id=event["event_id"], expected_goal=goal):
                    advanced += 1
                continue
            if status in {"queued", "running", "pending", "result_unknown", "unknown"}:
                self.repo.update_goal_checkpoint(root["id"], status="waiting_unknown" if status in {"result_unknown", "unknown"} else "waiting_result",
                    waiting_reason="read_original_request_result", event_id=event["event_id"], expected_goal=goal)
                continue
            fresh = self.verify_current_goal(goal, event)
            readiness = fresh["status"]
            if readiness == "invalidated":
                self.invalidate(root["id"], fresh.get("reason", "current_goal_invalidated"), expected_goal=goal)
                continue
            if readiness in {"ready", "waiting_adoption"} and "completed_source_slots" in fresh:
                self.repo.record_goal_completed_sources(root["id"], fresh["completed_source_slots"], expected_goal=goal)
            if readiness == "waiting_adoption":
                # A completed observation must never authorize execution while
                # another actual proposal is awaiting the real user's adoption.
                if self.repo.update_goal_checkpoint(root["id"], status="waiting_adoption",
                        waiting_reason=fresh.get("reason", "await_real_adoption"), event_id=event["event_id"], expected_goal=goal):
                    advanced += 1
                continue
            if readiness == "ready":
                self.repo.record_goal_output_checkpoint(root["id"], fresh.get("completed_output_checkpoint"),
                    production_result=fresh.get("production_result"), expected_goal=goal)
            if readiness in {"waiting_identity", "waiting_result", "waiting_unknown"}:
                self.repo.update_goal_checkpoint(root["id"], status=readiness,
                    waiting_reason=fresh.get("reason", readiness), event_id=event["event_id"] if fresh.get("consume_event") else None, expected_goal=goal,
                    task_version=fresh.get("task_version"))
                continue
            if readiness != "ready" or not isinstance(fresh.get("task_version"), dict):
                raise ValueError("接续必须使用当前已核验身份、目标和版本，不能默认授权")
            if event.get("phase") == "request" and status in {"rejected", "not_submitted"} and fresh.get("no_side_effect") is not True:
                self.repo.update_goal_checkpoint(root["id"], status="waiting_unknown", waiting_reason="request_effect_not_verified", expected_goal=goal)
                continue
            checkpoint = ("continue_after_real_adoption" if status == "confirmed" else
                "read_completed_source_and_continue" if event.get("result_kind") == "graphic_source" else
                "read_observed_result_and_continue" if event.get("result_kind") == "observation" else
                "read_actual_output_and_continue" if event.get("phase") == "execution" else "evaluate_actual_result")
            self.repo.update_goal_checkpoint(root["id"], status="waiting_result", waiting_reason=checkpoint,
                next_checkpoint=checkpoint, expected_goal=goal)
            if self.stop_event.is_set() or self._paused.is_set():
                break
            claim = self.repo.claim_goal_event(candidate, fresh["task_version"], max_rounds=self.max_rounds,
                selection_context=fresh.get("selection_context"))
            if claim is None:
                continue
            try:
                self.run_goal_turn(claim, fresh)
            except Exception:
                # The event has already been consumed. Persist uncertainty,
                # never replay a potentially committed model/HTTP effect.
                self.repo.finish_turn(claim["turn_id"], "failed", "自动接续中断；先核对原操作结果。",
                    result={"reason": "goal_runner_interrupted"})
                self.repo.settle_goal_round(claim)
                raise
            self.repo.settle_goal_round(claim)
            advanced += 1
        return advanced


@dataclass
class GoalCandidate:
    candidate: dict
    source: dict
    binding: dict
    actor: Any
    task: Any
    require_owner: bool
    job: Any = None
    start: dict | None = None
    proposal: Any = None
    media_result: dict | None = None


@dataclass
class GoalProjection:
    event: dict
    response_patch: dict
    message: str
    preview: tuple | None = None
    media: dict | None = None


@dataclass
class GoalRequestResult:
    locator: dict
    preview: tuple | None = None
    job: Any = None
    applied: Any = None


@dataclass
class GoalExecutionCandidate:
    row: Any
    source: dict
    job_id: str
    goal: dict
    binding: dict


class GoalProjectionRejected(Exception):
    def __init__(self, *, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(str(detail))


class GoalResultProjection:
    """Original-goal provenance and projection policy over existing owner facts.

    Identity verification and canonical media/request readers stay at their
    boundaries. This owner accepts their finite facts, never Request objects,
    HTTP responses, route handlers or a collection of application callbacks.
    """

    def __init__(self, production, conversations: ConversationRepository):
        self.production, self.conversations = production, conversations

    @staticmethod
    def unavailable_fact(kind):
        status, reason = {
            "identity": ("waiting_identity", "interactive_identity_not_available"),
            "access": ("invalidated", "current_task_or_member_not_authorized"),
            "completed_sources": ("invalidated", "completed_source_no_longer_authorized_or_current"),
            "execution": ("invalidated", "original_execution_no_longer_current"),
            "media": ("invalidated", "original_media_job_no_longer_authorized"),
            "reference": ("invalidated", "original_reference_authorization_changed"),
        }[kind]
        return {"status": status, "reason": reason}

    def bound_execution(self, goal, base_version):
        matches = []
        for event in self.production.list_task_events(goal["workspace_id"], goal["task_id"]):
            if event.event_type != "assistant_execution_located":
                continue
            source = event.payload["source"]
            if ((source.get("workspace_id"), source.get("task_id"), source.get("member_id"), source.get("conversation_id"))
                    != (goal["workspace_id"], goal["task_id"], goal["member_id"], goal["conversation_id"])
                    or source.get("expected_timeline_version_id") != base_version or not self.source_belongs_to_goal(goal, source)):
                continue
            job = self.production.get_job_run(goal["workspace_id"], event.payload["job_id"])
            if job.task_id == goal["task_id"] and job.kind == "creation_task_execution":
                matches.append((source, job))
        return matches[0] if len(matches) == 1 else None

    def owned_render_version(self, goal, version):
        bound = self.bound_execution(goal, version.parent_version_id)
        if bound is None or version.snapshot.get("render_input_timeline_version_id") != version.parent_version_id:
            return False
        rendered = self.production.goal_render_request_row(goal["workspace_id"], version.snapshot.get("creation_render_request_id"))
        return bool(rendered and rendered["owner_id"] == bound[1].id and rendered["state"] == "done")

    def owned_edit_version(self, goal, version):
        for operation in self.production.list_edit_operations(goal["workspace_id"], goal["task_id"]):
            if operation.status != "applied" or operation.result_version_id != version.id or operation.base_version_id != version.parent_version_id:
                continue
            proposal = self.production.get_edit_proposal(goal["workspace_id"], operation.proposal_id)
            source = self.production.get_edit_intent(goal["workspace_id"], proposal.intent_id).constraints.get("request_source") or {}
            if ((source.get("workspace_id"), source.get("task_id"), source.get("member_id"), source.get("conversation_id"))
                    == (goal["workspace_id"], goal["task_id"], goal["member_id"], goal["conversation_id"])
                    and source.get("expected_timeline_version_id") == version.parent_version_id
                    and source.get("expected_timeline_sequence") == version.sequence - 1
                    and self.source_belongs_to_goal(goal, source)):
                return True
        return False

    def edit_result_current(self, source, task, result_version):
        if task.current_timeline_version_id == result_version:
            return True
        goal = self.conversations.get_task_goal(source["assistant_turn_id"])
        if goal is None or not self.source_belongs_to_goal(goal, source):
            return False
        version = self.production.get_timeline_version(task.workspace_id, task.current_timeline_version_id)
        return version.parent_version_id == result_version and self.owned_render_version(goal, version)

    def selection_version_chain(self, goal, version):
        original = goal["request_context"]["target_snapshot"].get("timeline_version_id")
        chain = {version.id} if version else {None}
        while version is not None and version.id != original:
            if not (self.owned_edit_version(goal, version) or self.owned_render_version(goal, version)):
                return None
            if version.parent_version_id in chain:
                return None
            version = self.production.get_timeline_version(goal["workspace_id"], version.parent_version_id)
            chain.add(version.id)
        return chain if original in chain else None

    def verify_version(self, goal, event, task):
        expected = goal["target_snapshot"].get("timeline_version_id")
        event_version = event.get("result_version_id") or event.get("base_version_id")
        if event.get("base_version_id") != expected:
            return {"status": "invalidated", "reason": "unrelated_timeline_change"}
        if task.current_timeline_version_id != event_version:
            version = self.production.get_timeline_version(goal["workspace_id"], task.current_timeline_version_id)
            if (event.get("phase") != "apply" or version.parent_version_id != event_version
                    or not self.owned_render_version(goal, version)):
                return {"status": "invalidated", "reason": "unrelated_timeline_change"}
        return None

    def deferred_edit_execution(self, goal, event):
        if event.get("phase") != "apply" or event.get("status") != "confirmed":
            return None
        result = event.get("result_version_id")
        if not self.bound_execution(goal, result):
            return None
        version = self.production.get_timeline_version(goal["workspace_id"], result)
        return {"status": "waiting_result", "reason": "read_original_request_result", "consume_event": True,
                "task_version": {"timeline_version_id": result, "timeline_sequence": version.sequence}}

    def request_verification(self, goal, event, actual):
        if event["status"] not in {"rejected", "not_submitted"}:
            return None, False
        code = actual.get("result_status_code")
        if code in {401, 403, 409}:
            return {"status": "invalidated", "reason": "original_request_authority_or_version_rejected"}, False
        jobs, proposals = self.production.goal_request_effect_rows(
            goal["workspace_id"], goal["task_id"], event["edit_request"]["request_id"])
        return None, code == 422 and jobs is None and proposals is None

    @staticmethod
    def request_locator(event):
        located = event.get("edit_request")
        if not isinstance(located, dict):
            return {"status": "waiting_unknown", "reason": "original_request_receipt_missing"}, None
        return None, located

    def request_claim(self, task, member_id, request_id, phase):
        from app.task_audio import media_digest

        event_id = "event_edit_request_" + media_digest([task.workspace_id, member_id, request_id, phase])
        try:
            event = self.production.get_task_event(task.workspace_id, event_id)
        except KeyError as exc:
            raise GoalProjectionRejected(status_code=404, detail="编辑请求不存在") from exc
        if event.task_id != task.id:
            raise GoalProjectionRejected(status_code=404, detail="编辑请求不存在")
        return event_id, event

    @staticmethod
    def require_media_task(task_id, job):
        if job.task_id != task_id:
            raise GoalProjectionRejected(status_code=404, detail="任务媒体作业不存在")

    @staticmethod
    def execution_verification(actual):
        if actual["job"]["status"] not in {"succeeded", "failed", "cancelled"}:
            return {"status": "waiting_result", "reason": "original_execution_not_settled"}
        return None

    @staticmethod
    def needs_media_receipt(event):
        return bool(event.get("job_id") and event.get("status") != "confirmed")

    @staticmethod
    def media_verification(actual):
        if actual["status"] not in {"succeeded", "failed"}:
            return {"status": "waiting_result", "reason": "original_media_job_not_settled"}
        return None

    @staticmethod
    def removed_references(goal):
        return list(goal["target_snapshot"].get("removed_selected_refs", []))

    def adopted_operation(self, goal, event, task, removed):
        if not event.get("proposal_id") or event.get("status") != "confirmed":
            return None, None, removed
        proposal = self.production.get_edit_proposal(goal["workspace_id"], event["proposal_id"])
        operation = next((item for item in self.production.list_edit_operations(goal["workspace_id"], goal["task_id"])
            if item.proposal_id == proposal.id and item.id == event.get("operation_id") and item.status == "applied"), None)
        current = task.current_timeline_version_id == (operation.result_version_id if operation else None)
        if not current and operation is not None:
            version = self.production.get_timeline_version(goal["workspace_id"], task.current_timeline_version_id)
            current = version.parent_version_id == operation.result_version_id and self.owned_render_version(goal, version)
        if proposal.status != "confirmed" or operation is None or not current:
            return {"status": "invalidated", "reason": "adoption_result_not_current"}, None, removed
        if operation.operation_type == "DeleteClip":
            ref = f"clip:{operation.payload['clip_id']}"
            if ref not in removed:
                removed.append(ref)
        return None, {"operation_id": operation.id, "result_version_id": operation.result_version_id}, removed

    @staticmethod
    def ready(task, version, actor, facts, no_side_effect, completed_sources, removed_refs, checkpoint):
        if checkpoint and "delivery_duration" in checkpoint:
            facts = {**facts, "delivery_duration": checkpoint["delivery_duration"]}
            if checkpoint["status"] != "completed":
                checkpoint = None
        execution = facts.get("execution_status") or {}
        job = execution.get("job") or {}
        production_result = None
        if job.get("status") == "failed" or job.get("status") == "succeeded" and execution.get("video_ready"):
            # These facts came from the bound execution reader, not the model's
            # reply. A playable result does not prove that output was checked.
            production_result = {"scope": "production", "status": job["status"], "job_id": job["id"],
                "timeline_version_id": task.current_timeline_version_id, "video_ready": bool(execution.get("video_ready"))}
        return {"status": "ready", "task_version": {"timeline_version_id": task.current_timeline_version_id,
            "timeline_sequence": version.sequence if version else 0,
            **({"removed_selected_refs": removed_refs} if removed_refs else {})}, "facts": facts,
            "actor": actor, "no_side_effect": no_side_effect, "completed_source_slots": completed_sources,
            **({"completed_output_checkpoint": checkpoint} if checkpoint else {}),
            **({"production_result": production_result} if production_result else {})}

    def source_belongs_to_goal(self, goal, source):
        bound = self.conversations.get_task_goal(source["assistant_turn_id"])
        return bool(bound and bound["original_turn_id"] == goal["original_turn_id"]
            and source.get("input_id") == goal.get("source_input_id")
            and source.get("input_turn_id") == goal.get("source_input_turn_id"))

    def script_source_slot(self, goal, job_id, actor, artifact_store, outputs):
        from app.creation_execution import CreationExecutionActor
        from app.graphic_sources import graphic_source_facts
        from app.task_audio import TASK_MEDIA_KIND, media_digest

        job = self.production.get_job_run(goal["workspace_id"], job_id)
        start = self.production.get_task_event(job.workspace_id, f"event_{job.id}_start").payload
        source = start["arguments"]["_typed_source"]
        if (job.task_id != goal["task_id"] or job.kind != TASK_MEDIA_KIND or job.status != "succeeded"
                or start["result_kind"] != "graphic_source" or CreationExecutionActor.from_event(job.workspace_id, start) != actor
                or (source["workspace_id"], source["task_id"], source["member_id"], source["conversation_id"]) != (
                    goal["workspace_id"], goal["task_id"], goal["member_id"], goal["conversation_id"])
                or not self.source_belongs_to_goal(goal, source)
                or media_digest({key: value for key, value in start.items() if key != "input_digest"}) != job.input_digest):
            raise ValueError("已完成程序来源不属于原目标的实际成功作业")
        payload = start["arguments"]["payload"]
        result = self.production.get_task_event(job.workspace_id, f"event_{job.id}_result").payload["result"]
        facts = graphic_source_facts(self.production, artifact_store, job.workspace_id, job.task_id, result["source_ref"], actor=actor)
        publication = artifact_store.describe(result["source_ref"])
        if (facts["producer_job_id"] != job.id or not outputs.published_outputs_current(job)
                or (publication.sha256, publication.size_bytes) != (result["sha256"], result["size_bytes"])
                or any(facts[key] != result[key] for key in ("artifact_id", "sha256", "size_bytes", "width", "height"))):
            raise ValueError("已完成PNG来源与当前真实登记文件不一致")
        if start["operation"] == "media_script_preview":
            requested = payload["requested_outputs"]
        elif start["operation"] == "media_script_tool_preview":
            definition = next(item for item in self.production.list_artifact_records(job.workspace_id, job.task_id)
                if item.producer_job_id == job.id and item.kind == "media_script_definition")
            published = artifact_store.describe(definition.logical_ref)
            if (published.sha256, published.size_bytes) != (definition.sha256, definition.size_bytes) or definition.size_bytes > 256 * 1024:
                raise ValueError("原成功配方定义不可核验")
            requested = json.loads(artifact_store.resolve_file(definition.logical_ref).read_text())["requested_outputs"]
        else:
            raise ValueError("已完成图片不是本目标的程序来源")
        output, = requested
        if output["name"] != result["name"]:
            raise ValueError("原成功图片与命名输出槽不一致")
        return {"job_id": job.id, **{key: result[key] for key in ("source_ref", "artifact_id", "sha256", "size_bytes", "name", "width", "height")},
            "requested_output": output, "input_refs": payload.get("input_refs", []), "base_version_id": start["input_facts"]["base_version_id"]}

    def completed_sources(self, goal, actor, artifact_store, outputs):
        rows = self.production.goal_completed_source_rows(goal["workspace_id"], goal["task_id"], goal["conversation_id"], goal["member_id"])
        slots = []
        for row in rows:
            source = json.loads(row["payload_json"])["arguments"]["_typed_source"]
            if self.source_belongs_to_goal(goal, source):
                slots.append(self.script_source_slot(goal, row["id"], actor, artifact_store, outputs))
        return slots

    def pending_preview(self, goal, timeline_version_id):
        proposals, jobs = self.production.goal_pending_preview_rows(goal["workspace_id"], goal["task_id"],
            timeline_version_id, goal["conversation_id"], goal["member_id"])
        for rows, status in ((proposals, "waiting_adoption"), (jobs, "waiting_result")):
            for row in rows:
                source = (json.loads(row["constraints_json"])["request_source"] if status == "waiting_adoption"
                    else json.loads(row["payload_json"])["arguments"]["_typed_source"])
                if self.source_belongs_to_goal(goal, source):
                    return {"status": status, "reason": "await_real_adoption" if status == "waiting_adoption"
                        else "await_actual_preview_result", "pending_id": row["id"]}
        return None

    def output_observation(self, goal, event, facts, task, version):
        if event.get("phase") != "observation" or event.get("status") != "succeeded" or version is None:
            return None
        receipt = facts.get("media_job", {})
        result = receipt.get("result") or {}
        if (receipt.get("status") != "succeeded" or result.get("target", {}).get("kind") != "final"
                or result.get("output_role") != "composed_output"
                or result.get("timeline_version_id") != version.id or result.get("timeline_sequence") != version.sequence
                or result.get("measurement_scope") != "actual_output_bounded_frames"):
            return None
        observed = self.production.get_task_event(task.workspace_id, f"event_{receipt['job_id']}_start").payload
        source = observed["arguments"].get("_typed_source", {})
        if not source.get("assistant_turn_id") or not self.source_belongs_to_goal(goal, source):
            return None
        return {"receipt": receipt, "result": result, "observed_start": observed}

    def output_sources(self, goal, task, version, observation, output):
        from app.task_audio import TASK_MEDIA_KIND

        result = observation["result"]
        if (result.get("output") != {key: value for key, value in output.items() if key not in {"logical_ref", "mtime_ns", "ctime_ns"}}
                or observation["observed_start"]["input_facts"]["output"] != output):
            return None
        rendered, located, pending, unknown = self.production.goal_output_provenance_rows(
            task.workspace_id, task.id, version.snapshot.get("creation_render_request_id"),
            goal["conversation_id"], goal["member_id"], TASK_MEDIA_KIND,
            input_id=goal.get("source_input_id"),input_turn_id=goal.get("source_input_turn_id"))
        for row in pending:
            source = json.loads(row["payload_json"])["arguments"]["_typed_source"]
            if self.source_belongs_to_goal(goal, source):
                return None
        return {"rendered": rendered, "located": located, "unknown_requests": unknown}

    def unknown_request_for_goal(self, goal, row):
        data = json.loads(row["payload_json"])
        if self.source_belongs_to_goal(goal, data["source"]):
            return data["locator"]
        return None

    @staticmethod
    def request_still_unknown(original):
        return original["status"] in {"unknown", "result_unknown"}

    def output_execution_source(self, goal, version, sources):
        rendered, located = sources["rendered"], sources["located"]
        if rendered is None or rendered["state"] != "done" or located is None:
            return None
        source = json.loads(located["payload_json"])
        base = source["source"]["expected_timeline_version_id"]
        if (not self.source_belongs_to_goal(goal, source["source"])
                or version.parent_version_id != base or version.snapshot.get("render_input_timeline_version_id") != base):
            return None
        return {"job_id": rendered["owner_id"], "input_digest": source.get("input_digest")}

    @staticmethod
    def completed_output(observation, output, version, source, actual, *, goal=None,
                         duration_target_seconds=None):
        if (actual["job"]["status"] != "succeeded" or not actual["video_ready"]
                or source["input_digest"] != actual["job"]["input_digest"]):
            return None
        checkpoint = {"status": "completed", "scope": "production_and_output_observation", "job_id": actual["job"]["id"],
            "observation_job_id": observation["receipt"]["job_id"], "timeline_version_id": version.id, "sha256": output["sha256"],
            "measurement_scope": observation["result"]["measurement_scope"], "unavailable": [
                {"scope": item["scope"], "reason": item["reason"][:500]} for item in observation["result"].get("unavailable", [])]}
        adoption = (goal or {}).get("creation_adoption")
        # Only the original adopted creation input owns this duration requirement.
        # Creation roots omit source_input_id even when adoption follows a home
        # input revision. Only a later, explicitly bound input replaces it.
        input_id = (goal or {}).get("source_input_id")
        if adoption and (input_id is None or input_id == adoption["source"].get("input_id")):
            def measured(value):
                return float(value) if type(value) in {int, float} and math.isfinite(value) and value > 0 else None

            target = measured(duration_target_seconds)
            actual_duration = measured(observation["result"].get("media", {}).get("duration_seconds"))
            tolerance = 1 / 30  # One frame on the current 30 fps output clock.
            delta = target - actual_duration if target is not None and actual_duration is not None else None
            status = ("unverified" if delta is None else "matched" if abs(delta) <= tolerance + 1e-9
                      else "short" if delta > 0 else "long")
            repair = {
                "unverified": "先核对原目标与当前成片的输出观察回执，取得真实视频时长；不能用时间线或配音估计值宣布完成。",
                "short": "调用read_current_task、read_task_edit_capabilities回读当前时间线、音频和实际可用编辑能力，并核对真实素材范围；依据差额自主修订，再渲染并观察。配音结束后的画面留白不延长字幕，素材不足须说明，不能循环、补帧或擅自变速。",
                "long": "调用read_current_task、read_task_edit_capabilities回读当前时间线、音频和实际可用编辑能力，核对超出目标的区间及字幕；依据差额自主修订，再渲染并观察。不能只改时长元数据或截断旁白。",
                "matched": "真实输出时长已符合原新建目标。",
            }[status]
            checkpoint["delivery_duration"] = {"scope": "original_creation_delivery", "status": status,
                "target_duration_seconds": target, "actual_duration_seconds": actual_duration,
                "delta_seconds": round(delta, 6) if delta is not None else None, "tolerance_seconds": tolerance,
                "repair_action": repair}
            if status != "matched":
                checkpoint["status"] = "incomplete"
        return checkpoint

    def execution_source_current(self, task, source, job_id, status):
        base = source["expected_timeline_version_id"]
        result = task.current_timeline_version_id
        if result == base:
            if result is None:
                return source["expected_timeline_sequence"] == 0
            version = self.production.get_timeline_version(task.workspace_id, result)
            return version.sequence == source["expected_timeline_sequence"]
        if status != "succeeded" or result is None:
            return False
        version = self.production.get_timeline_version(task.workspace_id, result)
        rendered = self.production.goal_render_request_row(task.workspace_id, version.snapshot.get("creation_render_request_id"))
        return (version.parent_version_id == base and version.snapshot.get("render_input_timeline_version_id") == base
            and rendered is not None and rendered["owner_id"] == job_id and rendered["state"] == "done")

    def execution_candidate(self, row):
        located = json.loads(row["payload_json"])
        source, job_id = located["source"], located["job_id"]
        turn_id = source["assistant_turn_id"]
        goal = self.conversations.get_task_goal(turn_id)
        binding = self.conversations.business_turn_context(turn_id, row["workspace_id"], source["member_id"], row["task_id"], source=source)
        if goal is None or binding is None or binding["status"] == "running":
            return None
        return GoalExecutionCandidate(row, source, job_id, goal, binding)

    def invalidate_execution(self, context):
        if not self.source_belongs_to_goal(context.goal, context.source):
            return False
        return self.conversations.invalidate_task_goal(context.source["assistant_turn_id"], "original_execution_authorization_changed", expected_goal=context.goal)

    def project_execution(self, context, actual):
        from app.creation.contracts import TaskEvent

        row, source, binding = context.row, context.source, context.binding
        workspace_id, task_id, turn_id = row["workspace_id"], row["task_id"], source["assistant_turn_id"]
        job_id = context.job_id
        if self.production.goal_unreleased_job_request(workspace_id, job_id) is not None:
            return None
        task = self.production.get_creation_task(workspace_id, task_id)
        current = self.execution_source_current(task, source, job_id, row["status"])
        event_id = f"event_assistant_execution_{row['id']}_{row['status']}"
        status = "stopped" if binding["status"] == "stopped" else row["status"] if current else "stale"
        event = {"type": "business_result", "event_id": event_id, "task_id": task_id, "phase": "execution",
            "job_id": job_id, "base_version_id": source["expected_timeline_version_id"],
            "result_version_id": task.current_timeline_version_id if current else None,
            "status": status, "actionable": False, "video_ready": actual["video_ready"],
            "waiting_reason": "原制作作业已实际结束，按原目标回读已登记输出。"}
        if source.get("input_id"):
            event["input_id"]=source["input_id"]
        if not self.conversations.project_business_result(turn_id, workspace_id, source["member_id"], event,
                response_patch={"execution_status": actual}, message="原制作作业的实际终态已回读，未重复启动制作。"):
            return None
        self.production.append_task_event(TaskEvent(id=event_id, workspace_id=workspace_id, task_id=task_id,
            job_id=job_id, event_type="assistant_business_projected", payload={"turn_id": turn_id,
                "source_kind": "execution", "source_id": row["id"]}))
        return event

    def mark(self, candidate):
        from app.creation.contracts import TaskEvent

        self.production.append_task_event(TaskEvent(id=candidate["event_id"], workspace_id=candidate["workspace_id"],
            task_id=candidate["task_id"], job_id=candidate["id"] if candidate["kind"] == "job" else None,
            event_type="assistant_business_projected", payload={"turn_id": candidate["source"]["assistant_turn_id"],
                "source_kind": candidate["kind"], "source_id": candidate["id"]},
            created_at=datetime.fromisoformat(candidate["created_at"])))

    def can_continue(self, binding, turn_id):
        if binding["status"] == "completed":
            return True
        if binding["status"] != "failed":
            return False
        turn = self.conversations.get_turn(turn_id)
        goal = self.conversations.get_task_goal(turn_id)
        return bool(turn and self.conversations.recoverable_business_failure(turn.get("result") or {})
                    and goal and goal["current_turn_id"] == turn_id
                    and goal["status"] in {"running", "waiting_result", "waiting_adoption", "waiting_unknown"})

    def authorized(self, context, identity_status):
        from app.production import content_readable

        task, member_id = context.task, context.source["member_id"]
        if (identity_status != "ready"
                or not content_readable(user_id=member_id, local_diagnostic=False, visibility=task.visibility, owner_user_id=task.owner_user_id)
                or (context.require_owner and task.owner_user_id is not None and task.owner_user_id != member_id)):
            self.mark(context.candidate)
            return False
        return True

    def commit(self, context, projection):
        source = context.source
        if source.get("input_id"):
            projection.event["input_id"]=source["input_id"]
        if self.conversations.project_business_result(source["assistant_turn_id"], context.candidate["workspace_id"],
                source["member_id"], projection.event, response_patch=projection.response_patch, message=projection.message):
            self.mark(context.candidate)
            return True
        return False

    def business_candidate(self, candidate, *, media_available):
        from app.creation_execution import CreationExecutionActor
        from app.task_audio import media_digest

        source = candidate["source"]
        workspace_id, task_id = candidate["workspace_id"], candidate["task_id"]
        member_id, turn_id = source["member_id"], source["assistant_turn_id"]
        binding = self.conversations.business_turn_context(turn_id, workspace_id, member_id, task_id, source=source)
        if binding is None or binding["conversation_id"] != source["conversation_id"]:
            self.mark(candidate)
            return True
        if binding["status"] == "running":
            return False
        snapshot = binding["context"].get("target_snapshot", {})
        if (source["workspace_id"], source["task_id"], source["expected_timeline_version_id"], source["expected_timeline_sequence"]) != (
                workspace_id, task_id, snapshot.get("timeline_version_id"), snapshot.get("timeline_sequence")):
            self.mark(candidate)
            return True
        proposal = job = start = media_result = None
        if candidate["kind"] == "job":
            if not media_available:
                return False
            job = self.production.get_job_run(workspace_id, candidate["id"])
            start = self.production.get_task_event(workspace_id, f"event_{job.id}_start").payload
            if media_digest({key: value for key, value in start.items() if key != "input_digest"}) != job.input_digest:
                self.mark(candidate)
                return True
            actor = CreationExecutionActor.from_event(workspace_id, start)
            if self.production.goal_unreleased_job_request(workspace_id, job.id) is not None:
                return False
            if job.status == "succeeded":
                media_result = self.production.get_task_event(workspace_id, f"event_{job.id}_result").payload["result"]
                if media_result.get("proposal"):
                    proposal = self.production.get_edit_proposal(workspace_id, media_result["proposal"]["id"])
        else:
            proposal = self.production.get_edit_proposal(workspace_id, candidate["id"])
            if proposal.status != candidate["status"]:
                self.mark(candidate)
                return True
            if "actor_identity" not in source:
                self.mark(candidate)
                return True
            actor = CreationExecutionActor.from_event(workspace_id, source["actor_identity"])
        task = self.production.get_creation_task(workspace_id, task_id)
        if (actor.user_id or actor.member_id) != member_id:
            self.mark(candidate)
            return True
        return GoalCandidate(candidate, source, binding, actor, task, True, job, start, proposal, media_result)

    def business_projection(self, context, *, media, outputs):
        candidate, source, binding = context.candidate, context.source, context.binding
        task, proposal, job, start = context.task, context.proposal, context.job, context.start
        workspace_id, task_id, turn_id = candidate["workspace_id"], candidate["task_id"], source["assistant_turn_id"]
        current_version, base_version = task.current_timeline_version_id, source["expected_timeline_version_id"]
        result_version = operation = None
        if proposal is not None:
            if proposal.task_id != task_id or proposal.workspace_id != workspace_id:
                self.mark(candidate)
                return True
            if proposal.status == "confirmed":
                operation = next((item for item in self.production.list_edit_operations(workspace_id, task_id)
                                  if item.proposal_id == proposal.id and item.status == "applied"), None)
                if operation is not None:
                    result_version = operation.result_version_id
        current = (self.edit_result_current(source, task, result_version) if result_version is not None
                   else current_version == base_version)
        inputs_current = True
        if job is not None and current_version == base_version:
            inputs_current = media.check(job, start, context.actor) == "ready"
        status = proposal.status if proposal is not None else job.status
        if job is not None and (job.status != "succeeded" or not outputs.published_outputs_current(job)):
            status = job.status if job.status != "succeeded" else "failed"
        if not current or not inputs_current:
            status = "stale"
        if binding["status"] == "stopped":
            status = "stopped"
        event = {"type": "business_result", "event_id": candidate["event_id"], "task_id": task_id,
            "phase": "observation" if job is not None and start["result_kind"] == "observation" else "preview" if candidate["kind"] == "job" or candidate["status"] == "proposed" else "apply" if candidate["status"] == "confirmed" else "decision",
            "job_id": job.id if job else source.get("media_job_id"), "proposal_id": proposal.id if proposal else None,
            "base_version_id": base_version, "result_version_id": result_version,
            "status": status, "actionable": status == "proposed" and self.can_continue(binding, turn_id)}
        patch = {"edit_preview": None} if proposal is not None and status != "proposed" else {}
        preview = None
        if event["actionable"]:
            intent = self.production.get_edit_intent(workspace_id, proposal.intent_id)
            preview = intent, proposal
            event["waiting_reason"] = "请查看实际修改预览，再采用或拒绝。"
            message = "修改预览已完成，可以查看并决定是否采用；当前草稿尚未改变。"
        elif status == "confirmed" and operation is not None:
            event["operation_id"] = operation.id
            event["waiting_reason"] = ("当前草稿已保存；这次修改尚未反映到成片。" if current_version == result_version
                                       else "修改已保存，后续制作沿同一输入的回执核验。")
            message = ("修改已应用并保存到草稿，可撤销；成片尚未重新生成。" if current_version == result_version
                       else "修改已保存，可撤销；后续制作已有独立回执。")
        elif status == "rejected":
            event["waiting_reason"] = "修改方案已放弃；等待新的明确要求。"
            message = "修改方案已放弃，当前草稿保持不变。"
        elif status == "succeeded" and job is not None and start["result_kind"] == "graphic_source":
            event["result_kind"], event["source_ref"] = "graphic_source", context.media_result["source_ref"]
            event["waiting_reason"] = "图片来源已生成；等待图层修改提案与采用，当前版本和成片保持不变。"
            message = "图片来源已生成，可用于图层修改；尚未应用到当前草稿或生成成片。"
        elif status == "succeeded" and job is not None and start["result_kind"] == "observation":
            event["result_kind"] = "observation"
            event["waiting_reason"] = "真实登记输出的本机观察已完成；按原目标回读事实后继续。"
            message = "输出观察已完成，已保存实际画面取样和技术事实；尚未检查的范围会明确标明。"
        elif status in {"stale", "stopped"}:
            event["waiting_reason"] = "仅保留原目标的历史结果，不沿旧选择继续。"
            message = "原修改结果已保留为历史；当前目标或对话已变化，没有继续应用。"
        else:
            event["waiting_reason"] = "媒体处理未完成；查看作业原因后再决定下一步。"
            message = "媒体处理未完成，当前草稿保持不变；请查看作业原因。"
        media_data = None
        if job is not None:
            media_data = {"job": job, "start": start, "actor": context.actor,
                          "result": context.media_result if event["actionable"] or status == "succeeded" else None,
                          "status": status,
                          "response_status": status if status in {"failed", "cancelled", "stale"} else job.status}
        return GoalProjection(event, patch, message, preview, media_data)

    def request_candidate(self, candidate):
        from app.creation_execution import CreationExecutionActor

        source, _located = candidate["source"], candidate["request"]
        workspace_id, task_id = candidate["workspace_id"], candidate["task_id"]
        member_id = source["member_id"]
        binding = self.conversations.business_turn_context(source["assistant_turn_id"], workspace_id, member_id, task_id, source=source)
        if binding is None or source.get("actor_identity") is None:
            self.mark(candidate)
            return True
        if binding["status"] == "running":
            return False
        actor = CreationExecutionActor.from_event(workspace_id, source["actor_identity"])
        task = self.production.get_creation_task(workspace_id, task_id)
        if (actor.user_id or actor.member_id) != member_id:
            self.mark(candidate)
            return True
        return GoalCandidate(candidate, source, binding, actor, task, False)

    @staticmethod
    def request_event_id(context):
        from app.task_audio import media_digest

        locator = context.candidate["request"]["locator"]
        return "event_edit_request_" + media_digest([context.candidate["workspace_id"], context.source["member_id"], locator["request_id"], locator["phase"]])

    def request_result(self, context, recorded, *, runtime_available):
        candidate, source, task = context.candidate, context.source, context.task
        workspace_id, task_id = candidate["workspace_id"], candidate["task_id"]
        locator = dict(candidate["request"]["locator"])
        if recorded is not None:
            claim = self.production.get_task_event(workspace_id, self.request_event_id(context))
            if claim.task_id != task_id or claim.payload["member_id"] != source["member_id"]:
                self.mark(candidate)
                return True
            locator.update(status="rejected" if recorded["status_code"] >= 400 else "recorded",
                result_status_code=recorded["status_code"], result=recorded["response"], input_digest=claim.payload["input_digest"])
        elif locator["status"] not in {"not_submitted", "rejected"}:
            return False
        result = GoalRequestResult(locator)
        current = task.current_timeline_version_id == source["expected_timeline_version_id"]
        if recorded is not None and recorded["status_code"] < 400:
            actual = recorded["response"]
            if locator["phase"] == "apply" and "operation_type" in claim.payload["inputs"] and actual.get("operation"):
                operation = self.production.get_edit_operation(workspace_id, actual["operation"]["id"])
                proposal = self.production.get_edit_proposal(workspace_id, operation.proposal_id)
                intent = self.production.get_edit_intent(workspace_id, proposal.intent_id)
                origin = intent.constraints.get("request_source") or {}
                if (operation.task_id != task_id or operation.status != "applied"
                        or not operation.result_version_id
                        or operation.base_version_id != source["expected_timeline_version_id"]
                        or origin.get("assistant_turn_id") != source["assistant_turn_id"]
                        or origin.get("request_id") != locator["request_id"]
                        or claim.payload["inputs"].get("assistant_turn_id") != source["assistant_turn_id"]
                        or claim.payload["inputs"].get("operation_type") != operation.operation_type):
                    self.mark(candidate)
                    return True
                result.applied = operation
            elif actual.get("proposal"):
                proposal = self.production.get_edit_proposal(workspace_id, actual["proposal"]["id"])
                if proposal.task_id != task_id:
                    self.mark(candidate)
                    return True
                if current and proposal.status == "proposed" and proposal.operation["payload"].get("base_version_id") == task.current_timeline_version_id:
                    result.preview = self.production.get_edit_intent(workspace_id, proposal.intent_id), proposal
            elif actual.get("job_id"):
                if not runtime_available:
                    return False
                job = self.production.get_job_run(workspace_id, actual["job_id"])
                if job.task_id != task_id:
                    self.mark(candidate)
                    return True
                result.job = job
        return result

    def request_projection(self, context, result, patch):
        source, binding = context.source, context.binding
        if result.applied is not None:
            operation=result.applied
            direct_current=context.task.current_timeline_version_id == operation.result_version_id
            current=self.edit_result_current(source, context.task, operation.result_version_id)
            status="stopped" if binding["status"] == "stopped" else "confirmed" if current else "stale"
            if current:
                patch={**patch,"timeline_action":"apply","edit_preview":None}
            event={"type":"business_result","event_id":context.candidate["event_id"],"phase":"apply",
                "task_id":context.candidate["task_id"],"request_id":result.locator["request_id"],
                "base_version_id":operation.base_version_id,"result_version_id":operation.result_version_id,
                "proposal_id":operation.proposal_id,"operation_id":operation.id,
                "status":status,"edit_request":result.locator,"actionable":False,
                "waiting_reason":"原直接编辑回执已核验；后续版本变化不改绑原请求。"}
            return GoalProjection(event,patch,("原直接编辑已保存并回读，可整项撤销；尚未重新生成视频。" if direct_current
                else "原编辑已保存，可撤销；对应成片制作结果请查看当前回执。") if current
                else "原直接编辑的保存结果已回读；当前已有后续版本，保留为历史回执。")
        status = result.locator["status"] if context.task.current_timeline_version_id == source["expected_timeline_version_id"] else "stale"
        event = {"type": "business_result", "event_id": context.candidate["event_id"], "phase": "request",
            "task_id": context.candidate["task_id"], "request_id": result.locator["request_id"],
            "base_version_id": source["expected_timeline_version_id"],
            "status": "stopped" if binding["status"] == "stopped" else status, "edit_request": result.locator,
            "actionable": bool(patch.get("edit_preview")) and self.can_continue(binding, source["assistant_turn_id"]),
            "waiting_reason": "只回读原请求；排队不是完成，尚未确认的修改不会自动应用。"}
        return GoalProjection(event, patch, "原编辑请求的实际结果已回读，没有重复提交或改绑版本。")

    def script_tool_candidate(self, candidate):
        from app.creation_execution import CreationExecutionActor

        source, _located = candidate["source"], candidate["located"]
        workspace_id, task_id = candidate["workspace_id"], candidate["task_id"]
        member_id = source["member_id"]
        binding = self.conversations.business_turn_context(source["assistant_turn_id"], workspace_id, member_id, task_id, source=source)
        if binding is None or binding["conversation_id"] != source["conversation_id"]:
            self.mark(candidate)
            return True
        if binding["status"] == "running":
            return False
        snapshot = binding["context"].get("target_snapshot", {})
        actor = CreationExecutionActor.from_event(workspace_id, source["actor_identity"])
        task = self.production.get_creation_task(workspace_id, task_id)
        if ((source["workspace_id"], source["task_id"], source["expected_timeline_version_id"], source["expected_timeline_sequence"]) != (
                workspace_id, task_id, snapshot.get("timeline_version_id"), snapshot.get("timeline_sequence"))
                or (actor.user_id or actor.member_id) != member_id):
            self.mark(candidate)
            return True
        return GoalCandidate(candidate, source, binding, actor, task, True)

    def script_tool_projection(self, context, receipt):
        source, task, binding = context.source, context.task, context.binding
        version = self.production.get_timeline_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        current = (task.current_timeline_version_id, version.sequence if version else 0) == (
            source["expected_timeline_version_id"], source["expected_timeline_sequence"])
        status = "stopped" if binding["status"] == "stopped" else "recorded" if current else "stale"
        event = {"type": "business_result", "event_id": context.candidate["event_id"], "phase": "script_tool",
            "task_id": task.id, "request_id": context.candidate["located"]["request_id"], "status": status, "actionable": False,
            "base_version_id": source["expected_timeline_version_id"], "script_tool": receipt,
            "waiting_reason": "只回读原工具登记；没有重复保存、生成图片或修改当前版本。"}
        message = ("原工具登记已回读为历史结果，原目标或对话已变化，没有继续执行。" if status in {"stale", "stopped"}
            else "原工具元数据登记已回读为实际v1；没有重复保存、生成图片或修改当前草稿、成片。")
        return GoalProjection(event, {"script_tool": receipt}, message)

    def script_tool_candidates(self):
        return [{"kind": "script_tool", "id": row["id"], "workspace_id": row["workspace_id"],
            "task_id": row["task_id"], "created_at": row["created_at"],
            "source": json.loads(row["payload_json"])["source"], "located": json.loads(row["payload_json"]),
            "event_id": f"event_assistant_script_tool_{row['id']}"} for row in self.production.assistant_script_tool_projection_rows()]
