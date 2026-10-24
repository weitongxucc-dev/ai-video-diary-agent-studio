"""Shared assistant draft validation and persistence, independent of transport."""
from __future__ import annotations
from dataclasses import asdict
from uuid import uuid4
import hashlib
import json
from app.production import CreationTaskScriptSection, CreationTaskScript, ScriptGenerationRun, TopicGenerationRun, TopicEvidenceReference, TopicCandidate as CreationTaskTopicCandidate
from app.topics import CreationTaskScriptInput, CreationTaskGeneratedScript, CreationTaskScriptGenerationError, CreationTaskTopicInput, CreationTaskTopicSource, CreationTaskTopicGenerationError


class ScriptDraftRejected(Exception):
    def __init__(self, *, status_code, detail):
        self.status_code,self.detail=status_code,detail
        super().__init__(str(detail))


def script_failure(*, status_code, **detail):
    return ScriptDraftRejected(status_code=status_code,detail=detail)


class ScriptDraftUseCase:
    def __init__(self, repository, *, read_inputs, validate_inputs, script_goal, topic_origin,
                 check_draft, record_service, route_snapshot, record_adoption):
        self.repository=repository
        self.read_inputs,self.validate_inputs=read_inputs,validate_inputs
        self.script_goal,self.topic_origin=script_goal,topic_origin
        self.check_draft,self.record_service=check_draft,record_service
        self.route_snapshot,self.record_adoption=route_snapshot,record_adoption

    def confirm(self, workspace_id, task_id, script_id, payload, *, member_id, origin=None, write_guard=None):
        task=self.repository.get_creation_task(workspace_id,task_id)
        plan=self.repository.get_creation_plan(workspace_id,task.plan_id)
        script=self.repository.get_creation_task_script(workspace_id,script_id)
        if script.task_id!=task.id:
            raise ScriptDraftRejected(status_code=404,detail="文案不属于当前任务")
        if script.run_id and not script.reused_from_entry_id:
            run=self.repository.get_script_generation_run(workspace_id,script.run_id)
            self.validate_inputs(workspace_id,plan,run.input_references)
        task,script=self.repository.confirm_creation_task_script(workspace_id,task_id,script_id,title=payload.title,
            sections=[CreationTaskScriptSection(**section.model_dump()) for section in payload.sections],
            expected_updated_at=payload.expected_updated_at,adoption_kind=payload.adoption_kind,
            verified_owner_user_id=member_id,write_guard=write_guard)
        if origin is not None:
            self.record_adoption(str(payload.assistant_turn_id),workspace_id,member_id,task,plan,script,origin)
        return task,script

    def save(self, workspace_id, task_id, payload, *, instruction_origin=None, reread_origin=None, write_guard=None, source_binding=None):
        request_digest=hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        result_write=None
        if source_binding is not None:
            previous=[event for event in self.repository.list_task_events(workspace_id,task_id)
                if event.event_type=='assistant_script_saved' and event.payload['source']==source_binding
                and event.payload['request_digest']==request_digest]
            if previous:
                return self.repository.get_creation_task_script(workspace_id,previous[-1].payload['script_id'])
            result_write=lambda conn,script:self.repository.record_assistant_script_save(script,source_binding,request_digest,connection=conn)
        _assistant_script_inputs=self.read_inputs
        _require_current_script_inputs=self.validate_inputs
        _creation_task_script_goal=self.script_goal
        _script_topic_origin=self.topic_origin
        check_draft=self.check_draft
        record_service=self.record_service
        _topic_route_snapshot=self.route_snapshot
        record_adoption=self.record_adoption
        from app.topics import CreationTaskGeneratedScriptSection

        task, plan, topic, sources, input_references = _assistant_script_inputs(workspace_id, task_id)
        reader = _require_current_script_inputs(workspace_id, plan, input_references)
        sections = [CreationTaskScriptSection(**section.model_dump()) for section in payload.sections]
        allowed = {str(item["reference_id"]) for item in input_references}
        if any(not set(section.evidence_reference_ids).issubset(allowed) for section in sections):
            raise ScriptDraftRejected(status_code=422, detail="文案引用了本次任务以外的资料")
        scripts = self.repository.list_creation_task_scripts(workspace_id, task_id)
        revising_confirmed = bool(task.confirmed_script_id and task.can_revise_script)
        if revising_confirmed:
            scripts = [self.repository.get_creation_task_script(workspace_id, task.confirmed_script_id)]
        updating = bool(scripts and scripts[0].selection_status == "draft" and task.stage_status == "waiting_for_user")
        if updating or revising_confirmed:
            if payload.expected_updated_at is None:
                raise ScriptDraftRejected(status_code=422, detail="请先读取当前草稿，再提交修订。")
            if payload.expected_updated_at != scripts[0].updated_at:
                raise ScriptDraftRejected(status_code=409, detail="草稿已被修改，请重新读取后再提交。")
        elif payload.expected_updated_at is not None:
            raise ScriptDraftRejected(status_code=409, detail="草稿版本已变化，请重新读取后再保存")
        binding = (asdict(plan), asdict(topic), tuple(sources), input_references)
        topic_origin = _script_topic_origin(workspace_id, plan, topic)
        script_input = CreationTaskScriptInput(
            goal=_creation_task_script_goal(plan, task, reader[0]),
            language=plan.language, presenter_share_target=plan.presenter_share_target,
            duration_target_seconds=plan.duration_target_seconds, topic_title=topic.title,
            topic_angle=topic.angle if topic_origin == "generated" else "", topic_audience=topic.audience, topic_outline=tuple(topic.outline),
            topic_origin=topic_origin,
            current_instruction=instruction_origin["raw_command"] if instruction_origin else "",
            sources=tuple(sources), style_instruction=str(plan.template_settings.get("style_instruction", "")),
            script_structure=tuple(plan.template_settings.get("script_structure", [])),
            script_section_count=plan.template_settings.get("script_section_count"),
        )
        candidate = CreationTaskGeneratedScript(title=payload.title, language=plan.language, sections=tuple(
            CreationTaskGeneratedScriptSection(title=section.title, content=section.content,
                visual_prompt=section.visual_prompt, evidence_reference_ids=tuple(section.evidence_reference_ids))
            for section in sections))
        try:
            check_draft(script_input, candidate)
        except CreationTaskScriptGenerationError as exc:
            prevalidation = exc.code in {"input_invalid", "language_unavailable", "route_configuration_invalid", "response_schema_invalid"}
            content_failure = exc.code in {"script_fact_unsupported", "script_goal_conflict"}
            if not prevalidation:
                reachable = content_failure or exc.code == "script_review_invalid"
                record_service( ok=reachable,
                    cause="verified" if reachable else exc.code,
                    detail="" if reachable else exc.reason,
                    repair_action="" if reachable else exc.repair_action, workspace_id=workspace_id)
            raise script_failure(status_code=422 if content_failure or prevalidation else 502,
                error=exc.code, reason=exc.reason, impact="本轮修订未保存，原有草稿和任务保持原样。",
                repair_action=("请核对文案段落、资料引用和方案时长，修改后再提交。" if prevalidation else exc.repair_action),
                retryable=exc.retryable, reason_code=exc.reason_code, section_index=exc.section_index,
                candidate_section_index=exc.candidate_section_index, candidate_field=exc.candidate_field,
                candidate_fragment_index=exc.candidate_fragment_index,
                candidate_field_digest=exc.candidate_field_digest, review_issues=exc.review_issues) from exc
        record_service( ok=True, cause="verified", workspace_id=workspace_id)
        # The model call can outlive source permissions or the current plan/topic.
        # Re-read those bindings; the repository still owns the final draft CAS.
        current_task, current_plan, current_topic, current_sources, current_refs = _assistant_script_inputs(workspace_id, task_id)
        current_reader = _require_current_script_inputs(workspace_id, current_plan, current_refs)
        if binding != (asdict(current_plan), asdict(current_topic), tuple(current_sources), current_refs) or reader != current_reader:
            raise ScriptDraftRejected(status_code=409, detail="方案、选题或资料已变化，修订没有写入，请重新读取当前任务。")
        if instruction_origin is not None and instruction_origin != reread_origin(True):
            raise ScriptDraftRejected(status_code=409, detail="本轮要求已变化，旧要求核查的文案没有保存，请读取当前输入。")
        if revising_confirmed:
            if instruction_origin is not None:
                # Revising an adopted body is itself adoption. A discussion-only
                # agent request must never be converted into a manual adoption.
                reread_origin(False)
            delegated = bool(instruction_origin and plan.creation_intent and plan.creation_intent["kind"] == "goal_delegation")
            try:
                task, draft = self.repository.confirm_creation_task_script(workspace_id, task_id, scripts[0].id,
                    title=payload.title, sections=sections, expected_updated_at=payload.expected_updated_at, write_guard=write_guard,result_write=result_write,
                    adoption_kind="delegated_system" if delegated else "manual", verified_owner_user_id=reader[0])
            except (KeyError, ValueError) as exc:
                raise ScriptDraftRejected(status_code=409, detail=str(exc)) from exc
            if delegated:
                record_adoption(str(payload.assistant_turn_id), workspace_id, reader[0],
                    task, plan, draft, instruction_origin)
        elif updating:
            try:
                draft = self.repository.update_assistant_script_draft(
                    workspace_id, task_id, scripts[0].id, title=payload.title, sections=sections,
                    expected_updated_at=payload.expected_updated_at, write_guard=write_guard,result_write=result_write,
                )
            except (KeyError, ValueError) as exc:
                raise ScriptDraftRejected(status_code=409, detail=str(exc)) from exc
        else:
            run = ScriptGenerationRun(
                id=f"script_run_{uuid4().hex[:12]}", workspace_id=workspace_id,
                task_id=task_id, plan_id=plan.id, topic_id=topic.id, status="running",
                route_snapshot=_topic_route_snapshot(workspace_id),
                prompt_schema_version="assistant-script-draft/v2",
                input_references=input_references,
            )
            try:
                self.repository.start_script_generation(run,write_guard=write_guard)
            except (KeyError, ValueError) as exc:
                raise ScriptDraftRejected(status_code=409, detail=str(exc)) from exc
            draft = CreationTaskScript(
                id=f"script_{uuid4().hex[:12]}", workspace_id=workspace_id,
                task_id=task_id, run_id=run.id, topic_id=topic.id,
                title=payload.title, language=plan.language, sections=sections,
            )
            try:
                _, _, draft = self.repository.complete_script_generation(run.id, draft,write_guard=write_guard,result_write=result_write)
            except ValueError as exc:
                self.repository.fail_script_generation(
                    run.id, reason="assistant_script_invalid",
                    impact="本轮文案未保存，原有文案不变。",
                    repair_action="检查文案内容和来源后重试。",
                    retryable=True, provider_attempted=True,
                )
                raise ScriptDraftRejected(status_code=422, detail=str(exc)) from exc
        return draft

    def generate_topics(self, workspace_id, task, *, write_guard=None):
        _creation_plan_for_task_or_404=self.plan_for_task
        _topic_route_snapshot=self.route_snapshot
        _creation_task_topic_inputs=self.topic_inputs
        _CreationTaskTopicPreflightError=self.preflight_error_type
        record_service=self.record_service
        task_id=task.id
        plan = _creation_plan_for_task_or_404(workspace_id, task)
        route_snapshot = _topic_route_snapshot(workspace_id)
        provider_sources: tuple[CreationTaskTopicSource, ...] = ()
        input_references: list[dict[str, object]] = []
        evidence_by_reference_id: dict[str, dict[str, object]] = {}
        preflight_error: _CreationTaskTopicPreflightError | None = None
        try:
            (
                provider_sources,
                input_references,
                evidence_by_reference_id,
            ) = _creation_task_topic_inputs(
                workspace_id,
                task,
                plan,
                cloud_egress=route_snapshot["execution_location"] == "cloud",
            )
            if route_snapshot["availability"] == "unconfigured":
                raise _CreationTaskTopicPreflightError(
                    reason="capability_unavailable",
                    impact="当前没有可调用的文字模型连接，未生成选题候选。",
                    repair_action="在本机运行时设置中配置 OpenAI-compatible 文字模型后重试。",
                    retryable=True,
                )
        except _CreationTaskTopicPreflightError as exc:
            preflight_error = exc

        run = TopicGenerationRun(
            id=f"topic_run_{uuid4().hex[:12]}",
            workspace_id=workspace_id,
            task_id=task.id,
            plan_id=plan.id,
            status="running",
            route_snapshot=route_snapshot,
            prompt_schema_version="creation-task-topic/v1",
            input_references=input_references,
        )
        try:
            task, run = self.repository.start_topic_generation(run,write_guard=write_guard)
        except KeyError as exc:
            raise ScriptDraftRejected(status_code=404, detail="creation task not found") from exc
        except ValueError as exc:
            raise script_failure(
                status_code=409,
                error="creation_task_topic_stage_conflict",
                reason="当前创作任务不能重复开始选题。",
                impact="没有创建新的选题候选或重复运行记录。",
                repair_action=str(exc),
                retryable=task.stage_status == "failed",
            ) from exc

        if preflight_error is not None:
            task, _ = self.repository.fail_topic_generation(
                run.id,
                reason=preflight_error.reason,
                impact=preflight_error.impact,
                repair_action=preflight_error.repair_action,
                retryable=preflight_error.retryable,
            )
            raise script_failure(
                status_code=503,
                error="capability_unavailable",
                reason=preflight_error.reason,
                impact=preflight_error.impact,
                repair_action=preflight_error.repair_action,
                retryable=preflight_error.retryable,
            )

        try:
            generated_topics = self.topic_generator(
                CreationTaskTopicInput(
                    goal=plan.goal,
                    language=plan.language,
                    sources=provider_sources,
                    style_instruction=str(plan.template_settings.get("style_instruction", "")),
                    script_structure=tuple(plan.template_settings.get("script_structure", [])),
                )
            )
        except CreationTaskTopicGenerationError as exc:
            if exc.code not in {"input_invalid", "route_configuration_invalid"}:
                record_service( ok=False, cause=exc.code,
                    detail=exc.reason, repair_action=exc.repair_action,
                    workspace_id=workspace_id,
                )
            self.repository.fail_topic_generation(
                run.id,
                reason=exc.reason,
                impact=exc.impact,
                repair_action=exc.repair_action,
                retryable=exc.retryable,
                provider_attempted=True,
            )
            raise script_failure(
                status_code=502,
                error=exc.code,
                reason=exc.reason,
                impact=exc.impact,
                repair_action=exc.repair_action,
                retryable=exc.retryable,
            ) from exc

        record_service( ok=True, cause="verified",
            workspace_id=workspace_id,
        )
        candidates: list[CreationTaskTopicCandidate] = []
        evidence_references: list[TopicEvidenceReference] = []
        try:
            for generated in generated_topics:
                candidate = CreationTaskTopicCandidate(
                    id=f"topic_{uuid4().hex[:12]}",
                    workspace_id=workspace_id,
                    task_id=task.id,
                    run_id=run.id,
                    title=generated.title,
                    angle=generated.angle,
                    audience=generated.audience,
                    outline=list(generated.outline),
                    score=round(generated.score),
                )
                candidates.append(candidate)
                for reference_id in generated.evidence_reference_ids:
                    source = evidence_by_reference_id.get(reference_id)
                    if source is None:
                        raise ValueError("文字模型引用了未授权的证据")
                    evidence_references.append(
                        TopicEvidenceReference(
                            id=f"topic_evidence_{uuid4().hex[:12]}",
                            workspace_id=workspace_id,
                            task_id=task.id,
                            candidate_id=candidate.id,
                            kind=str(source["kind"]),  # type: ignore[arg-type]
                            reference_id=str(source["reference_id"]),
                            version_or_identity=str(source["version_or_identity"]),
                            title=str(source["title"]),
                            authorization_or_license=str(source["authorization_or_license"]),
                            source_url=(
                                str(source["source_url"])
                                if source["source_url"] is not None
                                else None
                            ),
                        )
                    )
            task, run, _, _ = self.repository.complete_topic_generation(
                run.id,
                candidates,
                evidence_references,write_guard=write_guard,
            )
        except ValueError as exc:
            self.repository.fail_topic_generation(
                run.id,
                reason="provider_output_invalid",
                impact="文字模型输出未通过选题和证据持久化校验，未生成候选。",
                repair_action="检查模型 JSON 输出与来源引用后重试。",
                retryable=True,
                provider_attempted=True,
            )
            raise script_failure(
                status_code=502,
                error="provider_output_invalid",
                reason="provider_output_invalid",
                impact="文字模型输出未通过选题和证据持久化校验，未生成候选。",
                repair_action="检查模型 JSON 输出与来源引用后重试。",
                retryable=True,
            ) from exc
        return self.topic_response(
            workspace_id,
            task,
            route_snapshot=route_snapshot,
            preflight_input_count=len(provider_sources),
        )
