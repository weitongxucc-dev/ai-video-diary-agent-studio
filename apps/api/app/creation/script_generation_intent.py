"""Own one draft-only button request across its Conversation turn and script Run."""
from __future__ import annotations

from contextlib import closing, nullcontext
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from app.creation.contracts import TaskEvent
from app.creation.intent_use_case import CreationIntentUseCase
from app.creation.script_contracts import ScriptGenerationRun
from app.creation_execution import CreationExecutionActor


class ScriptGenerationAgentStopped(RuntimeError):
    """The existing SDK runner reports that this turn was stopped."""


class ScriptGenerationAgentUnknown(RuntimeError):
    """The original runner or a write has no settled outcome; do not retry it."""


class ScriptGenerationAgentFailed(RuntimeError):
    def __init__(self, code, *, provider_attempted=False, provider_failed=True):
        super().__init__(code)
        self.code = code
        self.provider_attempted = provider_attempted
        self.provider_failed = provider_failed


def _canonical(value):
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                                 default=lambda item: item.isoformat()))


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


class ScriptGenerationIntent:
    def __init__(self, repository, conversations, *, source_inputs, route_snapshot, verify_identity):
        if Path(repository.database_path).resolve() != Path(conversations.database_path).resolve():
            raise ValueError("生成请求、对话和文案运行必须使用同一数据库")
        self.repository, self.conversations = repository, conversations
        self.source_inputs, self.verify_identity = source_inputs, verify_identity
        self.route_snapshot = dict(route_snapshot)

    @staticmethod
    def _event_id(workspace_id, task_id, request_id, suffix="intent"):
        identity = uuid5(NAMESPACE_URL, json.dumps([workspace_id, task_id, request_id])).hex
        return f"script_generation_{suffix}_{identity}"

    def _require_actor(self, conn, workspace_id, task_id, actor, *, verify=True):
        member_id = actor.user_id or actor.member_id
        task = self.repository.get_creation_task(workspace_id, task_id, connection=conn)
        if (actor.workspace_id != workspace_id or task.owner_user_id != member_id
                or verify and self.verify_identity(actor) != "ready"):
            raise PermissionError("生成请求的原成员或任务归属已失效")
        return task, member_id

    def _capture_input(self, conn, task, member_id, route_snapshot):
        plan = self.repository.get_creation_plan(task.workspace_id, task.plan_id)
        if plan.creation_task_id != task.id:
            raise ValueError("当前任务与制作要求不匹配")
        goal = CreationIntentUseCase.script_goal(self.repository, plan, task, viewer_user_id=member_id)
        topic = next((item for item in self.repository.list_topic_candidates(task.workspace_id, task.id)
                      if item.id == task.selected_topic_id), None)
        if topic is None:
            raise ValueError("当前任务还没有已确认选题")
        sources, references, _ = self.source_inputs(
            task.workspace_id, task, plan, cloud_egress=route_snapshot["execution_location"] == "cloud",
        )
        requirements = asdict(plan)
        fields = ("id", "creation_task_id", "goal", "language", "presenter_share_target",
                  "duration_target_seconds", "duration_mode", "duration_range_seconds", "template_settings",
                  "knowledge_references", "source_references", "creation_intent", "adoption")
        frozen = _canonical({
            "goal": goal, "plan_id": plan.id, "topic_id": topic.id,
            "requirements": {key: requirements[key] for key in fields if key in requirements},
            "topic": asdict(topic), "input_references": references,
            "sources_digest": _digest(_canonical([asdict(source) for source in sources])),
            "source_revisions": self.repository.task_source_revisions(
                task.workspace_id, [item.id for item in plan.knowledge_references],
                [item.id for item in plan.source_references], connection=conn),
            "goal_revision": self.repository.task_authorization_goal_revision(
                task.workspace_id, task.id, connection=conn),
        })
        return frozen

    def start(self, workspace_id, task_id, request_id, actor):
        """Accept once before any provider call; only the creator may dispatch."""
        request_id = str(UUID(str(request_id)))
        with closing(self.repository._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            task, member_id = self._require_actor(conn, workspace_id, task_id, actor)
            existing = self.conversations.read_user_action_request(
                conn, workspace_id, task_id, request_id, member_id,
            )
            if existing is not None:
                return self._read(conn, workspace_id, task_id, request_id, member_id, authorize=False), False
            frozen = self._capture_input(conn, task, member_id, self.route_snapshot)
            event_id = self._event_id(workspace_id, task_id, request_id)
            run_id = "script_run_" + uuid5(NAMESPACE_URL, event_id).hex
            conversation = self.conversations.current_or_create(
                workspace_id, member_id, "task", task_id, connection=conn,
            )
            context = {
                "kind": "script_generation", "source_kind": "user_action",
                "task_id": task_id, "stage": "script", "generation_request_id": request_id,
                "generation_event_id": event_id, "run_id": run_id,
                "requested_effect": "draft", "consumes_draft": False,
            }
            turn, created = self.conversations.create_user_action_turn_idempotent(
                conn, conversation_id=conversation["id"], workspace_id=workspace_id, member_id=member_id,
                task_id=task_id, client_request_id=request_id, request_context=context,
            )
            if not created:
                raise ValueError("原生成请求已存在，没有再次分配运行")
            run = ScriptGenerationRun(
                id=run_id, workspace_id=workspace_id, task_id=task_id, plan_id=task.plan_id,
                topic_id=task.selected_topic_id, status="running", route_snapshot=self.route_snapshot,
                prompt_schema_version="creation-task-script/v2", input_references=frozen["input_references"],
            )
            self.repository.start_script_generation(run, connection=conn)
            self.repository.append_task_event(TaskEvent(
                id=event_id, workspace_id=workspace_id, task_id=task_id, job_id=None,
                event_type="script_generation_requested", payload={
                    "request_id": request_id, "conversation_id": conversation["id"], "turn_id": turn["id"],
                    "run_id": run_id, "actor": actor.identity_payload(), "member_id": member_id,
                    "requested_effect": "draft", "frozen_input": frozen,
                    "input_digest": _digest(frozen), "route_snapshot": self.route_snapshot,
                }), connection=conn)
            return self._read(conn, workspace_id, task_id, request_id, member_id, authorize=False), True

    def read(self, workspace_id, task_id, request_id, member_id):
        """Reconcile the original request; reading a result grants no execution."""
        with closing(self.repository._connect()) as conn, conn:
            conn.execute("BEGIN")
            return self._read(conn, workspace_id, task_id, str(UUID(str(request_id))), member_id, authorize=False)

    def read_generation_request(self, conn, workspace_id, task_id, request_id, member_id):
        """Recheck draft-only authority on the caller's actual writer connection."""
        return self._read(conn, workspace_id, task_id, str(UUID(str(request_id))), member_id, authorize=True)

    def read_generation_result(self, workspace_id, task_id, request_id, member_id):
        return self.read(workspace_id, task_id, request_id, member_id)

    def _read(self, conn, workspace_id, task_id, request_id, member_id, *, authorize,
              allow_unbound_script=False, require_owner=True):
        if not conn.in_transaction:
            conn.execute("BEGIN")
        receipt = self.conversations.read_user_action_request(conn, workspace_id, task_id, request_id, member_id)
        if receipt is None:
            raise KeyError(request_id)
        context = receipt["request_context"]
        event = self.repository.get_task_event(
            workspace_id, self._event_id(workspace_id, task_id, request_id), connection=conn,
        )
        facts = event.payload
        if (event.task_id != task_id or event.event_type != "script_generation_requested"
                or facts["request_id"] != request_id or facts["member_id"] != member_id
                or facts["conversation_id"] != receipt["conversation_id"] or facts["turn_id"] != receipt["turn_id"]
                or context.get("generation_event_id") != event.id or context.get("run_id") != facts["run_id"]
                or context.get("kind") != "script_generation" or context.get("requested_effect") != "draft"
                or facts["requested_effect"] != "draft" or _digest(facts["frozen_input"]) != facts["input_digest"]):
            raise ValueError("原生成请求、回合与冻结输入不匹配")
        actor = CreationExecutionActor.from_event(workspace_id, facts["actor"])
        if require_owner:
            task, actual_member = self._require_actor(conn, workspace_id, task_id, actor, verify=authorize)
        else:
            task = self.repository.get_creation_task(workspace_id, task_id, connection=conn)
            actual_member = actor.user_id or actor.member_id
        if actual_member != member_id:
            raise PermissionError("原生成请求成员不匹配")
        state = self.repository.generation_run_state(conn, workspace_id, task_id, facts["run_id"])
        run, script = state["run"], state["script"]
        saved_result = context.get("generation_result")
        if (saved_result is not None and (saved_result["run_id"] != run.id or script is None
                                         or saved_result["script_id"] != script["id"])
                or script is not None and saved_result is None and not allow_unbound_script):
            raise ValueError("草稿未在原生成请求的写入事务中关联")
        if (run.plan_id != facts["frozen_input"]["plan_id"]
                or run.topic_id != facts["frozen_input"]["topic_id"]
                or run.input_references != facts["frozen_input"]["input_references"]):
            raise ValueError("原文案运行与按钮输入不匹配")
        if authorize:
            if run.status != "running":
                raise PermissionError("原文案运行已结束，没有再次授予写入")
            self._require_current_input(conn, task, member_id, receipt, facts)
        stopped = self._optional_event(conn, workspace_id, self._event_id(workspace_id, task_id, request_id, "stop"))
        unknown = self._optional_event(conn, workspace_id, self._event_id(workspace_id, task_id, request_id, "unknown"))
        status = ("draft_ready" if saved_result and saved_result["status"] == "draft_ready"
                  else "unknown" if unknown and run.status == "running"
                  else "stopping" if stopped and receipt["turn_status"] == "running"
                  else receipt["turn_status"] if receipt["turn_status"] in {"failed", "stopped"}
                  else "running" if receipt["turn_status"] == "running" and run.status == "running" else "incomplete")
        return {"request_id": request_id, "task_id": task_id, "conversation_id": receipt["conversation_id"],
                "turn_id": receipt["turn_id"], "turn_status": receipt["turn_status"],
                "run_id": run.id, "run_status": run.status, "script_id": script["id"] if script else None,
                "script": script, "frozen_input": facts["frozen_input"], "actor": facts["actor"],
                "source": {"kind": "user_action", "request_id": request_id, "member_id": member_id,
                           "conversation_id": receipt["conversation_id"], "assistant_turn_id": receipt["turn_id"]},
                "requested_effect": "draft", "can_continue": authorize, "status": status,
                "stop_requested": stopped is not None, "saved_result": saved_result,
                "result_stale": bool(saved_result and script and saved_result["updated_at"] != str(script["updated_at"]))}

    def _optional_event(self, conn, workspace_id, event_id):
        try:
            return self.repository.get_task_event(workspace_id, event_id, connection=conn)
        except KeyError:
            return None

    def _require_current_input(self, conn, task, member_id, receipt, facts):
        if receipt["turn_status"] != "running":
            raise PermissionError("原生成回合已结束，没有再次授予写入")
        latest = conn.execute("SELECT id FROM assistant_conversation_turns WHERE conversation_id=? "
                              "ORDER BY sequence DESC LIMIT 1", (receipt["conversation_id"],)).fetchone()
        if latest is None or latest["id"] != facts["turn_id"]:
            raise PermissionError("原生成动作已被新回合替代")
        if self._optional_event(conn, task.workspace_id, self._event_id(
                task.workspace_id, task.id, facts["request_id"], "stop")) is not None:
            raise PermissionError("原生成请求已要求停止，没有再次授予写入")
        if self._optional_event(conn, task.workspace_id, self._event_id(
                task.workspace_id, task.id, facts["request_id"], "unknown")) is not None:
            raise PermissionError("原生成请求结果未知，须先查询原回执")
        if self._capture_input(conn, task, member_id, facts["route_snapshot"]) != facts["frozen_input"]:
            raise PermissionError("原生成目标、选题或来源已经变化")

    def bind_generation_result(self, conn, workspace_id, task_id, request_id, member_id, *,
                               run_id, script_id, draft_ready=False):
        self.conversations._require_action_transaction(conn)
        # The caller's cancellation guard accompanies this same transaction.
        # Recheck the durable input even after the script owner marks success.
        facts = self._read(conn, workspace_id, task_id, request_id, member_id, authorize=False,
                           allow_unbound_script=True)
        actor = CreationExecutionActor.from_event(workspace_id, facts["actor"])
        self._require_actor(conn, workspace_id, task_id, actor)
        if facts["turn_status"] != "running" or facts["run_id"] != run_id or facts["script_id"] != script_id:
            raise PermissionError("文案结果不属于原生成请求的活动回合")
        receipt = self.conversations.read_user_action_request(conn, workspace_id, task_id, request_id, member_id)
        event = self.repository.get_task_event(workspace_id, self._event_id(workspace_id, task_id, request_id), connection=conn)
        task = self.repository.get_creation_task(workspace_id, task_id, connection=conn)
        self._require_current_input(conn, task, member_id, receipt, event.payload)
        script = facts["script"]
        if script["selection_status"] != "draft" or script.get("confirmed_at") or script.get("adoption"):
            raise PermissionError("生成按钮不能采用或执行文案")
        if draft_ready and (facts["run_status"] != "succeeded" or not script.get("draft_complete")):
            raise ValueError("草稿尚未完成完整核查")
        if not draft_ready and facts["run_status"] != "running":
            raise ValueError("原运行已结束，不能追加未完成草稿")
        result = {"run_id": run_id, "script_id": script_id, "updated_at": str(script["updated_at"]),
                  "status": "draft_ready" if draft_ready else "draft_partial", "requested_effect": "draft"}
        self.conversations.bind_user_action_result(conn, workspace_id, task_id, request_id, member_id, result)
        self.repository.append_task_event(TaskEvent(
            id=self._event_id(workspace_id, task_id, request_id, "ready" if draft_ready else "draft"),
            workspace_id=workspace_id, task_id=task_id, job_id=None,
            event_type="script_generation_draft_ready" if draft_ready else "script_generation_draft_bound",
            payload={"request_id": request_id, "run_id": run_id, "script_id": script_id,
                     "turn_id": facts["turn_id"], "requested_effect": "draft",
                     **({"updated_at": str(script["updated_at"])} if draft_ready else {})}), connection=conn)
        return self._read(conn, workspace_id, task_id, request_id, member_id, authorize=False)

    @staticmethod
    def public_projection(record):
        """Expose original result identity without actors, source excerpts or later edits."""
        saved = record["saved_result"]
        return {key: record[key] for key in (
            "request_id", "task_id", "conversation_id", "turn_id", "turn_status", "run_id", "run_status",
            "status", "requested_effect", "stop_requested", "result_stale",
        )} | {"script_id": saved["script_id"] if saved else None,
             "script_updated_at": saved["updated_at"] if saved else None}

    def request_stop(self, workspace_id, task_id, request_id, member_id):
        """Persist logical cancellation; this does not claim the SDK has exited."""
        request_id = str(UUID(str(request_id)))
        with closing(self.repository._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._read(conn, workspace_id, task_id, request_id, member_id, authorize=False)
            if record["stop_requested"]:
                return self.public_projection(record)
            if record["turn_status"] != "running" or record["status"] == "draft_ready":
                raise ValueError("原生成请求已结束，未追加停止动作")
            self.repository.append_task_event(TaskEvent(
                id=self._event_id(workspace_id, task_id, request_id, "stop"),
                workspace_id=workspace_id, task_id=task_id, job_id=None,
                event_type="script_generation_stop_requested",
                payload={"request_id": request_id, "turn_id": record["turn_id"],
                         "run_id": record["run_id"], "member_id": member_id}), connection=conn)
            return self.public_projection(self._read(conn, workspace_id, task_id, request_id, member_id, authorize=False))

    def fail(self, workspace_id, task_id, request_id, member_id, *, status, reason,
             impact, repair_action, retryable, provider_attempted=False, provider_failed=True):
        """Settle only the original failed/stopped run, retaining partial draft facts."""
        if status not in {"failed", "stopped"}:
            raise ValueError("生成异常只能收束为 failed 或 stopped")
        request_id = str(UUID(str(request_id)))
        with closing(self.repository._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._read(conn, workspace_id, task_id, request_id, member_id,
                                authorize=False, require_owner=False)
            if record["status"] == "draft_ready" and record["turn_status"] == "running":
                self.conversations.finish_turn(record["turn_id"], "completed", result={
                    "kind": "script_generation", "request_id": request_id,
                    "response": {**self.public_projection(record), "turn_status": "completed"},
                }, connection=conn)
                return self.public_projection(self._read(conn, workspace_id, task_id, request_id, member_id,
                                                         authorize=False, require_owner=False))
            if record["turn_status"] != "running":
                return self.public_projection(record)
            if record["run_status"] == "running":
                self.repository.fail_script_generation(record["run_id"], reason=reason, impact=impact,
                    repair_action=repair_action, retryable=retryable, provider_attempted=provider_attempted,
                    provider_failed=provider_failed, connection=conn)
            self.conversations.finish_turn(record["turn_id"], status, result={
                "kind": "script_generation", "request_id": request_id,
                "run_id": record["run_id"], "reason": reason,
            }, connection=conn)
            return self.public_projection(self._read(conn, workspace_id, task_id, request_id, member_id,
                                                     authorize=False, require_owner=False))

    def recover_interrupted(self, conn, workspace_id, task_id, request_id, member_id):
        """Fence a restarted turn and reconcile only its original durable writes."""
        self.conversations._require_action_transaction(conn)
        record = self._read(conn, workspace_id, task_id, request_id, member_id,
                            authorize=False, require_owner=False)
        if record["status"] == "draft_ready":
            if record["turn_status"] != "running":
                return  # Keep an already-terminal turn and its original ready locator.
            self.conversations.finish_turn(record["turn_id"], "completed", result={
                "kind": "script_generation", "request_id": request_id,
                "response": {**self.public_projection(record), "turn_status": "completed"},
            }, connection=conn)
            return
        unknown = self._optional_event(conn, workspace_id,
            self._event_id(workspace_id, task_id, request_id, "unknown"))
        if record["run_status"] == "running" and (unknown is not None or
                self.repository.script_records.generation_window_requests_pending(
                    workspace_id, task_id, request_id, member_id, connection=conn)):
            self._mark_unknown(workspace_id, task_id, request_id, member_id, connection=conn)
            return
        if record["run_status"] == "running":
            self.repository.fail_script_generation(record["run_id"], reason="api_restarted",
                impact="生成进程已中断，原请求与已保存草稿保留。",
                repair_action="查询原请求和草稿后，明确发起新的生成请求。", retryable=True,
                provider_attempted=False, connection=conn)
        self.conversations.finish_turn(record["turn_id"], "failed", result={
            "kind": "script_generation", "request_id": request_id,
            "run_id": record["run_id"], "reason": "api_restarted",
        }, connection=conn)

    def _mark_unknown(self, workspace_id, task_id, request_id, member_id, *, connection=None):
        with (closing(self.repository._connect()) if connection is None else nullcontext(connection)) as conn:
            if connection is None:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    return self._mark_unknown(workspace_id, task_id, request_id, member_id, connection=conn)
            record = self._read(conn, workspace_id, task_id, request_id, member_id,
                                authorize=False, require_owner=False)
            if self._optional_event(conn, workspace_id,
                    self._event_id(workspace_id, task_id, request_id, "unknown")) is not None:
                return self.public_projection(record)
            self.repository.append_task_event(TaskEvent(
                id=self._event_id(workspace_id, task_id, request_id, "unknown"),
                workspace_id=workspace_id, task_id=task_id, job_id=None,
                event_type="script_generation_outcome_unknown",
                payload={"request_id": request_id, "run_id": record["run_id"], "turn_id": record["turn_id"]}),
                connection=conn)
            return self.public_projection(self._read(conn, workspace_id, task_id, request_id, member_id,
                                                     authorize=False, require_owner=False))

    def execute(self, workspace_id, task_id, request_id, member_id, *, run_agent, script_case):
        """Run the existing Agent once, then finish actual reviewed rows as a draft.

        The injected runner owns its existing SDK/TurnControl lifecycle. Neither
        its reply nor its preview is evidence that any document was persisted.
        """
        from app.creation.script_draft_use_case import ScriptDraftRejected
        from app.schemas import CreationTaskScriptConfirmSavedRequest

        request_id = str(UUID(str(request_id)))
        agent_returned = False
        try:
            with closing(self.repository._connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                record = self._read(conn, workspace_id, task_id, request_id, member_id, authorize=False)
                dispatch_id = self._event_id(workspace_id, task_id, request_id, "dispatch")
                if record["turn_status"] != "running" or self._optional_event(conn, workspace_id, dispatch_id) is not None:
                    return self.public_projection(record)
                record = self.read_generation_request(conn, workspace_id, task_id, request_id, member_id)
                self.repository.append_task_event(TaskEvent(
                    id=dispatch_id, workspace_id=workspace_id, task_id=task_id, job_id=None,
                    event_type="script_generation_dispatched",
                    payload={"request_id": request_id, "run_id": record["run_id"], "turn_id": record["turn_id"]}), connection=conn)
            run_agent(record)
            agent_returned = True
            with closing(self.repository._connect()) as conn, conn:
                conn.execute("BEGIN")
                current = self.read_generation_request(conn, workspace_id, task_id, request_id, member_id)
            if current["script"] is None:
                raise ValueError("Agent 未保存原请求的实际草稿")
            if script_case.scripts.generation_window_requests_pending(workspace_id, task_id, request_id, member_id):
                raise ScriptGenerationAgentUnknown()
            # Reuse the versioned window command shape; the called operation is
            # finish_generation_draft, never confirmation or adoption.
            command = CreationTaskScriptConfirmSavedRequest(
                request_id=uuid5(NAMESPACE_URL, self._event_id(workspace_id, task_id, request_id, "finish")),
                expected_updated_at=current["script"]["updated_at"],
            )
            def guard(conn):
                return self.read_generation_request(conn, workspace_id, task_id, request_id, member_id)
            def draft_origin(value):
                return {**value["source"], "intent_kind": "script_generation", "generation_request_id": request_id,
                        "requested_effect": "draft", "raw_command": ""}
            def reread_origin(draft_only):
                if draft_only is not True:
                    raise PermissionError("生成按钮只允许保存草稿")
                with closing(self.repository._connect()) as conn, conn:
                    conn.execute("BEGIN")
                    return draft_origin(self.read_generation_request(conn, workspace_id, task_id, request_id, member_id))
            script_case.finish_generation_draft(workspace_id, task_id, current["script_id"], command,
                member_id=member_id, generation_request_id=request_id, write_guard=guard,
                instruction_origin=draft_origin(current), reread_origin=reread_origin)
            with closing(self.repository._connect()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                complete = self._read(conn, workspace_id, task_id, request_id, member_id, authorize=False)
                if complete["status"] != "draft_ready" or complete["result_stale"]:
                    raise ValueError("未取得原请求当前版本的完整草稿回执")
                self.conversations.finish_turn(complete["turn_id"], "completed", result={
                    "kind": "script_generation", "request_id": request_id,
                    "response": {**self.public_projection(complete), "turn_status": "completed"},
                }, connection=conn)
                return self.public_projection(self._read(conn, workspace_id, task_id, request_id, member_id, authorize=False))
        except ScriptGenerationAgentUnknown:
            return self._mark_unknown(workspace_id, task_id, request_id, member_id)
        except ScriptGenerationAgentStopped:
            return self.fail(workspace_id, task_id, request_id, member_id, status="stopped", reason="user_stopped",
                impact="原生成已停止，已有草稿保留。", repair_action="查询原请求结果后再明确新的生成请求。", retryable=True)
        except ScriptGenerationAgentFailed as exc:
            return self.fail(workspace_id, task_id, request_id, member_id, status="failed", reason=exc.code,
                impact="Agent 未完成本次草稿，已有结果保留。", repair_action="查看原请求与运行时状态后再处理。",
                retryable=True, provider_attempted=exc.provider_attempted, provider_failed=exc.provider_failed)
        except (PermissionError, ValueError, KeyError, ScriptDraftRejected):
            # Retain a finite failure category, not raw source/model/error text.
            with closing(self.repository._connect()) as conn, conn:
                conn.execute("BEGIN")
                stop = self._optional_event(conn, workspace_id, self._event_id(workspace_id, task_id, request_id, "stop"))
            return self.fail(workspace_id, task_id, request_id, member_id,
                status="stopped" if stop else "failed", reason="user_stopped" if stop else "generation_not_completed",
                impact="原请求未通过当前输入或完整草稿核查，已有结果保留。",
                repair_action="查询原请求与窗口回执，核对来源和权限后再处理。", retryable=True,
                provider_attempted=agent_returned, provider_failed=False)
        except Exception:
            # Unknown dispatch/write outcome is query-only, not a retry license.
            return self._mark_unknown(workspace_id, task_id, request_id, member_id)
