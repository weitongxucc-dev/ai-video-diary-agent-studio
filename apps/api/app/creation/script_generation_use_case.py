"""Generate and record a script draft independently of its HTTP entry point."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from app.creation.intent_use_case import CreationIntentUseCase
from app.creation.script_contracts import (
    CreationTaskScript,
    CreationTaskScriptSection,
    ScriptGenerationRun,
)
from app.creation.source_errors import SourcePreflightError
from app.topics import (
    CreationTaskGeneratedScript,
    CreationTaskScriptGenerationError,
    CreationTaskScriptInput,
    CreationTaskTopicSource,
)

if TYPE_CHECKING:
    from app.creation.contracts import CreationTask
    from app.production import ProductionRepository


RejectionKind = Literal[
    "not_found", "goal_source_changed", "topic_missing", "stage_conflict",
    "preflight_failed", "content_rejected", "provider_failed", "output_invalid",
]


class ScriptGenerationRejected(Exception):
    """A business rejection; the transport owns status and response encoding."""

    def __init__(self, *, kind: RejectionKind, **details: object) -> None:
        self.kind = kind
        self.details = {key: value for key, value in details.items() if value is not None}
        super().__init__(str(self.details.get("reason", kind)))


class ScriptGenerationUseCase:
    def __init__(
        self,
        *,
        repository: ProductionRepository,
        source_inputs: Callable,
        route_snapshot: Mapping[str, object],
        generate: Callable[[CreationTaskScriptInput], CreationTaskGeneratedScript],
        record_service: Callable,
    ) -> None:
        self.repository = repository
        self.source_inputs = source_inputs
        self.route_snapshot = dict(route_snapshot)
        self.generate = generate
        self.record_service = record_service

    def generate_script(
        self, workspace_id: str, task_id: str, *, viewer_user_id: str | None,
    ) -> CreationTask:
        try:
            task = self.repository.get_creation_task(workspace_id, task_id)
            plan = self.repository.get_creation_plan(workspace_id, task.plan_id)
        except KeyError as exc:
            raise ScriptGenerationRejected(
                kind="not_found", reason="creation task not found",
            ) from exc
        if plan.creation_task_id != task.id:
            raise ScriptGenerationRejected(kind="not_found", reason="creation task not found")
        try:
            script_goal = CreationIntentUseCase.script_goal(
                self.repository, plan, task, viewer_user_id=viewer_user_id,
            )
        except (KeyError, ValueError) as exc:
            raise ScriptGenerationRejected(
                kind="goal_source_changed", error="script_goal_source_changed",
                reason="当前任务的原始制作要求不可读取，或与已采用方案的来源不一致。",
                impact="未调用文字模型，未保存或采用新文案。",
                repair_action="读取当前任务及原创作请求，核对采用来源和当前成员权限后再制作。",
                retryable=False,
            ) from exc
        selected_topic = next(
            (candidate for candidate in self.repository.topic_records.list_topic_candidates(workspace_id, task.id)
             if candidate.id == task.selected_topic_id), None,
        )
        if selected_topic is None:
            raise ScriptGenerationRejected(
                kind="topic_missing", error="confirmed_topic_missing",
                reason="当前创作任务还没有已确认选题。",
                impact="未调用文字模型，未生成文案。",
                repair_action="先生成并确认一个选题。", retryable=False,
            )
        route_snapshot = dict(self.route_snapshot)
        provider_sources: tuple[CreationTaskTopicSource, ...] = ()
        input_references: list[dict[str, object]] = []
        preflight_error: SourcePreflightError | None = None
        try:
            provider_sources, input_references, _ = self.source_inputs(
                workspace_id, task, plan,
                cloud_egress=route_snapshot["execution_location"] == "cloud",
            )
            if route_snapshot["availability"] == "unconfigured":
                raise SourcePreflightError(
                    reason="capability_unavailable",
                    impact="当前没有可调用的文字模型连接，未生成文案。",
                    repair_action="在本机运行时设置中配置 OpenAI-compatible 文字模型后重试。",
                    retryable=True,
                )
        except SourcePreflightError as exc:
            preflight_error = exc
        run = ScriptGenerationRun(
            id=f"script_run_{uuid4().hex[:12]}", workspace_id=workspace_id,
            task_id=task.id, plan_id=plan.id, topic_id=selected_topic.id,
            status="running", route_snapshot=route_snapshot,
            prompt_schema_version="creation-task-script/v2", input_references=input_references,
        )
        try:
            task, run = self.repository.start_script_generation(run)
        except (KeyError, ValueError) as exc:
            raise ScriptGenerationRejected(
                kind="stage_conflict", error="creation_task_script_stage_conflict",
                reason="当前创作任务不能开始生成文案。",
                impact="没有创建重复文案或重复模型调用。",
                repair_action=str(exc), retryable=task.stage_status == "failed",
            ) from exc
        # A preflight rejection still belongs to an actual failed generation run.
        if preflight_error is not None:
            failure = dict(
                reason=preflight_error.reason, impact=preflight_error.impact,
                repair_action=preflight_error.repair_action, retryable=preflight_error.retryable,
            )
            self.repository.fail_script_generation(run.id, **failure)
            raise ScriptGenerationRejected(
                kind="preflight_failed", error="capability_unavailable", **failure,
            )
        try:
            generated = self.generate(CreationTaskScriptInput(
                goal=script_goal, language=plan.language,
                presenter_share_target=plan.presenter_share_target,
                duration_target_seconds=plan.duration_target_seconds,
                topic_title=selected_topic.title,
                topic_origin=self._topic_origin(workspace_id, plan, selected_topic),
                topic_angle=selected_topic.angle, topic_audience=selected_topic.audience,
                topic_outline=tuple(selected_topic.outline), sources=provider_sources,
                style_instruction=str(plan.template_settings.get("style_instruction", "")),
                script_structure=tuple(plan.template_settings.get("script_structure", [])),
                script_section_count=plan.template_settings.get("script_section_count"),
            ))
        except CreationTaskScriptGenerationError as exc:
            content_failure = exc.code in {"script_fact_unsupported", "script_goal_conflict"}
            provider_not_attempted = exc.code in {
                "input_invalid", "route_configuration_invalid", "language_unavailable", "window_required",
            }
            if content_failure or exc.code == "script_review_invalid":
                # Successful provider calls can yield rejected content or review
                # structure; neither demonstrates a broken service connection.
                self.record_service(ok=True, cause="verified")
            elif not provider_not_attempted:
                self.record_service(
                    ok=False, cause=exc.code, detail=exc.reason, repair_action=exc.repair_action,
                )
            self.repository.fail_script_generation(
                run.id, reason=exc.reason, impact=exc.impact,
                repair_action=exc.repair_action, retryable=exc.retryable,
                provider_attempted=not provider_not_attempted,
                provider_failed=not (
                    provider_not_attempted or content_failure or exc.code == "script_review_invalid"
                ),
            )
            raise ScriptGenerationRejected(
                kind="content_rejected" if content_failure or exc.code == "window_required" else "provider_failed",
                error=exc.code, reason=exc.reason, impact=exc.impact,
                repair_action=exc.repair_action, retryable=exc.retryable,
                reason_code=exc.reason_code, section_index=exc.section_index,
                candidate_section_index=exc.candidate_section_index,
                candidate_field=exc.candidate_field,
                candidate_fragment_index=exc.candidate_fragment_index,
                candidate_field_digest=exc.candidate_field_digest, review_issues=exc.review_issues,
            ) from exc
        self.record_service(ok=True, cause="verified")
        script = CreationTaskScript(
            id=f"script_{uuid4().hex[:12]}", workspace_id=workspace_id,
            task_id=task.id, run_id=run.id, topic_id=selected_topic.id,
            title=generated.title, language=generated.language,
            sections=[
                CreationTaskScriptSection(
                    id=f"section_{index:02d}_{uuid4().hex[:6]}", title=section.title,
                    content=section.content, visual_prompt=section.visual_prompt,
                    evidence_reference_ids=list(section.evidence_reference_ids),
                )
                for index, section in enumerate(generated.sections, start=1)
            ],
        )
        try:
            task, _, _ = self.repository.complete_script_generation(run.id, script)
        except ValueError as exc:
            self.repository.fail_script_generation(
                run.id, reason="provider_output_invalid",
                impact="文字模型输出未通过文案与证据持久化校验，未保存文案。",
                repair_action="检查模型 JSON 输出与来源引用后重试。",
                retryable=True, provider_attempted=True,
            )
            raise ScriptGenerationRejected(
                kind="output_invalid", error="provider_output_invalid",
                reason="文案输出未通过持久化校验。", impact="未保存无效文案。",
                repair_action="检查模型 JSON 输出与来源引用后重试。", retryable=True,
            ) from exc
        return task

    def _topic_origin(self, workspace_id, plan, topic) -> Literal["generated", "provided"]:
        intent = plan.creation_intent
        if not intent or not (intent.get("topic_title") or intent.get("final_script")):
            return "generated"
        run = self.repository.topic_records.get_topic_generation_run(workspace_id, topic.run_id)
        return "provided" if run.task_id == topic.task_id and run.route_snapshot.get("operation") == "provided_topic" else "generated"
