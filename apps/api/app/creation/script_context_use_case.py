"""Script-stage inputs and reader-authorized context shared by HTTP and agents.

Task/source reads remain repository-owned; writes and commit guards remain in
ScriptDraftUseCase. Owner source preparation never authorizes a context reader.
"""
from __future__ import annotations

from typing import Literal

from app.creation.script_draft_use_case import ScriptDraftRejected, script_failure
from app.creation.source_errors import SourcePreflightError
from app.creation.source_inputs_use_case import CreationSourceInputs
from app.production import CreationPlan, ProductionRepository
from app.source_policy import knowledge_item_readable as content_readable
from app.creation.topic_contracts import (
    TopicCandidate as CreationTaskTopicCandidate,
)
from app.creation.contracts import CreationTask


class ScriptContextUseCase:
    def __init__(self, repository: ProductionRepository, source_inputs: CreationSourceInputs):
        self.repository = repository
        self.source_inputs = source_inputs

    def _task(self, workspace_id, task_id):
        try:
            return self.repository.get_creation_task(workspace_id, task_id)
        except KeyError as exc:
            raise ScriptDraftRejected(status_code=404, detail="creation task not found") from exc

    def _plan(self, workspace_id, task):
        try:
            plan = self.repository.get_creation_plan(workspace_id, task.plan_id)
        except KeyError as exc:
            raise ScriptDraftRejected(status_code=404, detail="creation task not found") from exc
        if plan.creation_task_id != task.id:
            raise ScriptDraftRejected(status_code=404, detail="creation task not found")
        return plan

    def read_inputs(self, workspace_id: str, task_id: str):
        task = self._task(workspace_id, task_id)
        if task.confirmed_script_id and task.can_revise_script:
            confirmed = next((item for item in self.repository.active_creation_task_script_headers(workspace_id, task_id)
                              if item['id'] == task.confirmed_script_id), None)
            if confirmed is None:
                raise ScriptDraftRejected(status_code=409, detail="当前确认文案不存在，请重新读取任务")
            if confirmed.get('locked_body_digest'):
                raise ScriptDraftRejected(status_code=409, detail="当前定稿正文已锁定，不能自动改写；请明确采用新的定稿。")
        elif task.stage != "script" or task.confirmed_script_id:
            raise ScriptDraftRejected(status_code=409, detail="当前任务不在待确认文案阶段")
        plan = self._plan(workspace_id, task)
        topic = next((item for item in self.repository.topic_records.list_topic_candidates(workspace_id, task_id)
                      if item.id == task.selected_topic_id), None)
        if topic is None:
            raise ScriptDraftRejected(status_code=409, detail="请先确认本次选题")
        try:
            sources, input_references, _ = self.source_inputs.prepare(
                workspace_id, task, plan, cloud_egress=True,
            )
        except SourcePreflightError as exc:
            raise ScriptDraftRejected(status_code=409, detail={
                "reason": exc.reason, "impact": exc.impact, "repair_action": exc.repair_action,
            }) from exc
        return task, plan, topic, sources, input_references

    def topic_origin(self, workspace_id: str, plan: CreationPlan, topic: CreationTaskTopicCandidate) -> Literal["generated", "provided"]:
        intent = plan.creation_intent
        if not intent or not (intent.get("topic_title") or intent.get("final_script")):
            return "generated"
        run = self.repository.topic_records.get_topic_generation_run(workspace_id, topic.run_id)
        return "provided" if run.task_id == topic.task_id and run.route_snapshot.get("operation") == "provided_topic" else "generated"

    def goal(self, plan: CreationPlan, task: CreationTask, viewer_user_id: str | None) -> str:
        from app.creation.intent_use_case import CreationIntentUseCase

        try:
            return CreationIntentUseCase.script_goal(self.repository, plan, task, viewer_user_id=viewer_user_id)
        except (KeyError, ValueError) as exc:
            raise script_failure(
                status_code=409, error="script_goal_source_changed",
                reason="当前任务的原始制作要求不可读取，或与已采用方案的来源不一致。",
                impact="未调用文字模型，未保存或采用新文案。",
                repair_action="读取当前任务及原创作请求，核对采用来源和当前成员权限后再制作。",
                retryable=False,
            ) from exc

    def read_context(self, workspace_id, task_id, reader_id, *, local_diagnostic: bool):
        task = self._task(workspace_id, task_id)
        if not content_readable(user_id=reader_id, local_diagnostic=local_diagnostic,
                visibility=task.visibility, owner_user_id=task.owner_user_id):
            raise ScriptDraftRejected(status_code=404, detail="creation task not found")
        task, plan, topic, sources, refs = self.read_inputs(workspace_id, task_id)
        try:
            self.source_inputs.validate_current(workspace_id, plan, refs, reader_id, local_diagnostic)
        except SourcePreflightError as exc:
            raise ScriptDraftRejected(status_code=409, detail={
                "error": exc.reason, "message": exc.impact, "repair_action": exc.repair_action,
            }) from exc
        script_goal = self.goal(plan, task, reader_id)
        scripts = self.repository.active_creation_task_script_headers(workspace_id, task.id)
        draft = scripts[0] if scripts and scripts[0]['selection_status'] == "draft" else None
        if task.can_revise_script and task.confirmed_script_id:
            draft = next((item for item in scripts if item['id'] == task.confirmed_script_id), None)
        retained_draft = None
        if draft and not self.repository.script_knowledge_inputs_current(workspace_id, task.id, draft['id']):
            retained_draft, draft = draft, None
        reply_event = next((event for event in self.repository.execution_records.list_task_events(workspace_id, task.id)
                            if event.event_type == "assistant_reply_plan_confirmed"
                            and event.payload.get("plan_id") == plan.id), None)
        reply_reference = reply_event.payload.get("reply_reference") if reply_event else None
        reply_origin = ({
            "conversation_id": reply_reference["conversation_id"],
            "turn_id": reply_reference["turn_id"],
            "request_id": reply_event.payload["request_id"],
        } if isinstance(reply_reference, dict)
            and isinstance(reply_reference.get("conversation_id"), str)
            and isinstance(reply_reference.get("turn_id"), str)
            and isinstance(reply_event.payload.get("request_id"), str) else None)
        return {
            "task_id": task.id,
            "goal": script_goal,
            **({"creative_proposal": plan.goal,
                "creative_proposal_role": "可修订的Agent方案，不是用户已定稿正文；本条交付以language和duration_target_seconds等结构化要求为准。"}
               if script_goal != plan.goal else {}),
            "creation_intent": plan.creation_intent,
            "language": plan.language,
            "duration_target_seconds": plan.duration_target_seconds,
            "presenter_share_target": plan.presenter_share_target,
            "style_instruction": str(plan.template_settings.get("style_instruction", "")),
            "script_structure": list(plan.template_settings.get("script_structure", [])),
            "script_section_count": plan.template_settings.get("script_section_count"),
            "topic": {"title": topic.title, "angle": topic.angle, "audience": topic.audience,
                      "outline": topic.outline},
            "sources": [{"reference_id": source.reference_id, "title": source.title,
                         "text": source.text} for source in sources],
            "draft": ({"id": draft['id'], "title": draft['title'],
                       "updated_at": draft['updated_at'], "sections": None,
                       "draft_complete": draft.get('draft_complete', True),
                       "sections_loaded": False, "sections_total_count": draft['sections_total_count']}
                      if draft is not None else None),
            "reply_origin": reply_origin,
            "retained_draft": ({"id": retained_draft['id'], "title": retained_draft['title'],
                "updated_at": retained_draft['updated_at'], "reason": "资料已追加，旧稿保留；新一轮文案将使用新引用。"} if retained_draft else None),
        }
