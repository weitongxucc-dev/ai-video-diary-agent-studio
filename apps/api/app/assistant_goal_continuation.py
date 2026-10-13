"""Resume an explicitly bound original instruction from real business results.

The existing conversation turn/receipt owns both intent and checkpoints. This
helper has no job queue, model loop, identity issuance or proposal adoption.
"""
from __future__ import annotations

from collections.abc import Callable

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

    def register_turn(self, turn_id: str) -> dict | None:
        # Called only at the live, verified task-response boundary. Startup
        # never infers goals from pre-existing completed conversations.
        return self.repo.register_task_goal(turn_id)

    def observe_business_result(self, source_turn_id: str, event: dict) -> bool:
        goal = self.repo.get_task_goal(source_turn_id)
        return bool(goal and event.get("type") == "business_result" and event.get("event_id")
                    and event.get("task_id") == goal["task_id"])

    def invalidate(self, turn_id: str, reason: str) -> bool:
        return self.repo.invalidate_task_goal(turn_id, reason)

    def reconcile(self, *, limit: int = 8) -> int:
        advanced = 0
        for candidate in self.repo.goal_event_candidates(limit=limit):
            root, event = candidate["root"], candidate["event"]
            goal, status = root["goal"], event.get("status")
            if event.get("task_id") != goal["task_id"]:
                self.invalidate(root["id"], "business_event_target_changed")
                continue
            # A rejected user decision is final. A rejected/not-submitted
            # *request* is different: the host may prove no effect and permit
            # one of the finite new turns to correct its input.
            stale_observation_awaiting_adoption = (status == "stale" and event.get("phase") == "observation"
                                                    and goal["status"] == "waiting_adoption")
            if (status in {"stale", "cancelled", "stopped"} and not stale_observation_awaiting_adoption
                    or status == "rejected" and event.get("phase") != "request"):
                self.invalidate(root["id"], f"business_result_{status}")
                continue
            if goal["status"] == "waiting_adoption" and status != "confirmed":
                # Observation/program results can arrive out of order, even
                # after a human adoption changed the live version. Consume the
                # old result without treating it as adoption or making the
                # pending confirmed event stale before it can be verified.
                if self.repo.update_goal_checkpoint(root["id"], status="waiting_adoption",
                        waiting_reason="await_real_adoption", event_id=event["event_id"]):
                    advanced += 1
                continue
            if status == "proposed" or (event.get("phase") == "request" and event.get("actionable")):
                if self.repo.update_goal_checkpoint(root["id"], status="waiting_adoption",
                        waiting_reason="await_real_adoption", event_id=event["event_id"]):
                    advanced += 1
                continue
            if status in {"queued", "running", "pending", "result_unknown", "unknown"}:
                self.repo.update_goal_checkpoint(root["id"], status="waiting_unknown" if status in {"result_unknown", "unknown"} else "waiting_result",
                    waiting_reason="read_original_request_result", event_id=event["event_id"])
                continue
            fresh = self.verify_current_goal(goal, event)
            readiness = fresh["status"]
            if readiness == "invalidated":
                self.invalidate(root["id"], fresh.get("reason", "current_goal_invalidated"))
                continue
            if readiness in {"ready", "waiting_adoption"} and "completed_source_slots" in fresh:
                self.repo.record_goal_completed_sources(root["id"], fresh["completed_source_slots"])
            if readiness == "waiting_adoption":
                # A completed observation must never authorize execution while
                # another actual proposal is awaiting the real user's adoption.
                if self.repo.update_goal_checkpoint(root["id"], status="waiting_adoption",
                        waiting_reason=fresh.get("reason", "await_real_adoption"), event_id=event["event_id"]):
                    advanced += 1
                continue
            if readiness in {"waiting_identity", "waiting_result", "waiting_unknown"}:
                self.repo.update_goal_checkpoint(root["id"], status=readiness,
                    waiting_reason=fresh.get("reason", readiness))
                continue
            if readiness != "ready" or not isinstance(fresh.get("task_version"), dict):
                raise ValueError("接续必须使用当前已核验身份、目标和版本，不能默认授权")
            if event.get("phase") == "request" and status in {"rejected", "not_submitted"} and fresh.get("no_side_effect") is not True:
                self.repo.update_goal_checkpoint(root["id"], status="waiting_unknown", waiting_reason="request_effect_not_verified")
                continue
            checkpoint = ("continue_after_real_adoption" if status == "confirmed" else
                "read_completed_source_and_continue" if event.get("result_kind") == "graphic_source" else
                "read_observed_result_and_continue" if event.get("result_kind") == "observation" else
                "read_actual_output_and_continue" if event.get("phase") == "execution" else "evaluate_actual_result")
            self.repo.update_goal_checkpoint(root["id"], status="waiting_result", waiting_reason=checkpoint,
                next_checkpoint=checkpoint)
            claim = self.repo.claim_goal_event(candidate, fresh["task_version"], max_rounds=self.max_rounds)
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
