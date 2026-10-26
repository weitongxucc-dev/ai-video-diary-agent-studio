"""Shared assistant draft validation and persistence, independent of transport."""
from __future__ import annotations
from dataclasses import asdict
from uuid import NAMESPACE_URL, uuid4, uuid5
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

    def confirm(self, workspace_id, task_id, script_id, payload, *, member_id, origin=None, write_guard=None, reread_origin=None):
        task=self.repository.get_creation_task(workspace_id,task_id)
        plan=self.repository.get_creation_plan(workspace_id,task.plan_id)
        script=self.repository.get_creation_task_script(workspace_id,script_id)
        if script.task_id!=task.id:
            raise ScriptDraftRejected(status_code=404,detail="文案不属于当前任务")
        run=None
        binding=None
        if script.run_id and not script.reused_from_entry_id:
            run=self.repository.get_script_generation_run(workspace_id,script.run_id)
            self.validate_inputs(workspace_id,plan,run.input_references)
        sections = [CreationTaskScriptSection(**section.model_dump()) for section in payload.sections]
        if not script.draft_complete:
            raise ScriptDraftRejected(status_code=409, detail="分窗草稿尚未完成全稿核查，请使用完整稿确认入口。")
        changed = payload.title != script.title or sections != script.sections
        if changed and not script.locked_body_digest:
            if getattr(payload, "edit_mode", None) != "replace_all":
                raise ScriptDraftRejected(status_code=422, detail="修改整稿须明确整稿替换意图；局部修改请使用稳定段落ID窗口，不能用部分正文覆盖全稿。")
            if payload.expected_updated_at != script.updated_at:
                raise ScriptDraftRejected(status_code=409, detail="文案版本已变化，请重新读取后再确认。")
            checked_task, checked_plan, topic, sources, refs, library_entry = self._confirmation_inputs(workspace_id, task_id, script)
            reader = self.validate_inputs(workspace_id, checked_plan, refs)
            binding = (asdict(checked_plan), asdict(topic) if topic else None, tuple(sources), refs, reader,
                       asdict(library_entry) if library_entry else None)
            self._review_candidate(workspace_id, checked_task, checked_plan, topic, sources, reader,
                                   payload.title, sections, origin, library_entry=library_entry)
            _, current_plan, current_topic, current_sources, current_refs, current_entry = self._confirmation_inputs(workspace_id, task_id, script)
            current_reader = self.validate_inputs(workspace_id, current_plan, current_refs)
            if binding != (asdict(current_plan), asdict(current_topic) if current_topic else None, tuple(current_sources),
                           current_refs, current_reader, asdict(current_entry) if current_entry else None):
                raise ScriptDraftRejected(status_code=409, detail="方案、选题或资料已变化，确认没有写入，请重新读取当前任务。")
        def commit_guard(conn):
            if write_guard is not None: write_guard(conn)
            if run is not None: self.validate_inputs(workspace_id,plan,run.input_references)
            if binding is not None:
                _,cp,ct,cs,cr,ce=self._confirmation_inputs(workspace_id,task_id,script)
                reader=self.validate_inputs(workspace_id,cp,cr)
                if binding!=(asdict(cp),asdict(ct) if ct else None,tuple(cs),cr,reader,asdict(ce) if ce else None):
                    raise ScriptDraftRejected(status_code=409,detail="提交时来源或要求已变化，原确认稿保持不变。")
            if origin is not None:
                if reread_origin is not None:
                    if origin!=reread_origin(False):
                        raise ScriptDraftRejected(status_code=409,detail="提交时原输入已变化，原确认稿保持不变。")
                elif write_guard is None:
                    raise ScriptDraftRejected(status_code=409,detail="采用缺少当前原输入的事务校验，没有沿旧输入确认文案。")
        task,script=self.repository.confirm_creation_task_script(workspace_id,task_id,script_id,title=payload.title,
            sections=sections,
            expected_updated_at=payload.expected_updated_at,adoption_kind=payload.adoption_kind,
            verified_owner_user_id=member_id,write_guard=commit_guard)
        if origin is not None:
            self.record_adoption(str(payload.assistant_turn_id),workspace_id,member_id,task,plan,script,origin)
        return task,script

    def _confirmation_inputs(self, workspace_id, task_id, script):
        if not script.reused_from_entry_id:
            return (*self.read_inputs(workspace_id, task_id), None)
        task = self.repository.get_creation_task(workspace_id, task_id)
        plan = self.repository.get_creation_plan(workspace_id, task.plan_id)
        entry = self.repository.get_script_library_entry(workspace_id, script.reused_from_entry_id)
        if entry.language != script.language or plan.language != entry.language:
            raise ScriptDraftRejected(status_code=409, detail="复用文案的来源语言与当前任务不一致，请重新读取文案库来源。")
        try:
            sources, refs, _ = self.topic_inputs(workspace_id, task, plan, cloud_egress=True)
        except self.preflight_error_type as exc:
            raise ScriptDraftRejected(status_code=409, detail={
                "reason": exc.reason, "impact": exc.impact, "repair_action": exc.repair_action}) from exc
        return task, plan, None, sources, refs, entry

    def _review_candidate(self, workspace_id, task, plan, topic, sources, reader, title, sections, instruction_origin,
                          *, library_entry=None, review_window=None, reuse_review=None):
        from app.topics import CreationTaskGeneratedScriptSection
        allowed = {source.reference_id for source in sources}
        if any(not set(section.evidence_reference_ids).issubset(allowed) for section in sections):
            raise ScriptDraftRejected(status_code=422, detail="文案引用了本次任务以外的资料")
        topic_origin = "provided" if library_entry is not None else self.topic_origin(workspace_id, plan, topic)
        script_input = CreationTaskScriptInput(
            goal=self.script_goal(plan, task, reader[0]),
            language=plan.language, presenter_share_target=plan.presenter_share_target,
            duration_target_seconds=plan.duration_target_seconds,
            topic_title=library_entry.title if library_entry is not None else topic.title,
            topic_angle=topic.angle if topic_origin == "generated" else "",
            topic_audience=topic.audience if topic is not None else "",
            topic_outline=tuple(topic.outline) if topic is not None else (),
            topic_origin=topic_origin,
            current_instruction=instruction_origin["raw_command"] if instruction_origin else "",
            sources=tuple(sources), style_instruction=str(plan.template_settings.get("style_instruction", "")),
            script_structure=tuple(plan.template_settings.get("script_structure", [])),
            script_section_count=plan.template_settings.get("script_section_count"), review_window=review_window,
        )
        candidate = CreationTaskGeneratedScript(title=title, language=plan.language, sections=tuple(
            CreationTaskGeneratedScriptSection(title=section.title, content=section.content,
                visual_prompt=section.visual_prompt, evidence_reference_ids=tuple(section.evidence_reference_ids))
            for section in sections))
        from app.creation.script_windows import SCRIPT_REVIEW_PROTOCOL_VERSION
        values = asdict(script_input)
        values.pop("review_window")
        values['review_protocol'] = SCRIPT_REVIEW_PROTOCOL_VERSION
        values["script_title"] = title
        values["requirements_phase"] = "complete" if review_window is not None and review_window.finalizing else "draft"
        binding_digest = hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if reuse_review is not None and reuse_review(binding_digest):
            return binding_digest
        try:
            self.check_draft(script_input, candidate)
        except CreationTaskScriptGenerationError as exc:
            prevalidation = exc.code in {"input_invalid", "language_unavailable", "route_configuration_invalid", "response_schema_invalid"}
            content_failure = exc.code in {"script_fact_unsupported", "script_goal_conflict"}
            if not prevalidation:
                reachable = content_failure or exc.code == "script_review_invalid"
                self.record_service( ok=reachable,
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
        self.record_service( ok=True, cause="verified", workspace_id=workspace_id)
        return binding_digest

    def _claimed_window_operation(self, operation, workspace_id, task_id, script_id, payload, member_id, callback, write_guard):
        request_id=str(payload.request_id)
        digest=hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        claimed=self.repository.claim_script_window_request(workspace_id,task_id,script_id,operation,request_id,member_id,digest,submitted_input=payload.model_dump(mode='json'),write_guard=write_guard)
        if not claimed:
            result=self.window_request_result(workspace_id,task_id,request_id,member_id=member_id,operation=operation,script_id=script_id)
            if result['status'] in {'saved','confirmed'}:
                return callback()
            return result
        try:
            return callback()
        except Exception as exc:
            # Persist only the bounded business error, never arbitrary provider/driver exceptions.
            detail=exc.detail if isinstance(exc,ScriptDraftRejected) else None
            self.repository.end_script_window_request_with_error(workspace_id,task_id,script_id,operation,request_id,member_id,error_detail=detail)
            raise

    def patch(self, workspace_id, task_id, script_id, payload, *, member_id, instruction_origin=None, reread_origin=None, write_guard=None):
        return self._claimed_window_operation('patch',workspace_id,task_id,script_id,payload,member_id,
            lambda:self._patch(workspace_id,task_id,script_id,payload,member_id=member_id,instruction_origin=instruction_origin,
                               reread_origin=reread_origin,write_guard=write_guard),write_guard)

    def start_window(self, workspace_id, task_id, payload, *, member_id, instruction_origin=None, reread_origin=None, write_guard=None):
        return self._claimed_window_operation('start',workspace_id,task_id,None,payload,member_id,
            lambda:self._start_window(workspace_id,task_id,payload,member_id=member_id,instruction_origin=instruction_origin,
                                      reread_origin=reread_origin,write_guard=write_guard),write_guard)

    def confirm_saved(self, workspace_id, task_id, script_id, payload, *, member_id, instruction_origin=None, reread_origin=None, write_guard=None):
        return self._claimed_window_operation('confirm',workspace_id,task_id,script_id,payload,member_id,
            lambda:self._confirm_saved(workspace_id,task_id,script_id,payload,member_id=member_id,instruction_origin=instruction_origin,
                                       reread_origin=reread_origin,write_guard=write_guard),write_guard)

    def _patch(self, workspace_id, task_id, script_id, payload, *, member_id, instruction_origin=None,
              reread_origin=None, write_guard=None):
        from app.creation.script_windows import script_patch_digest
        from app.topics import CreationTaskScriptReviewWindow
        sections = [CreationTaskScriptSection(**part.model_dump()) for part in payload.sections]
        request_id = str(payload.request_id)
        after = payload.insert_after_section_id
        digest = script_patch_digest([asdict(part) for part in sections], payload.expected_updated_at, after)
        previous = self.repository.script_patch_request_result(workspace_id,task_id,script_id,request_id,member_id)
        if previous is not None:
            if previous['request_digest'] != digest:
                raise ScriptDraftRejected(status_code=409, detail="同一文案请求不能更换输入；请读取原结果。")
            return previous['result']
        context = self.repository.script_section_patch_context(workspace_id,task_id,script_id,sections,
            payload.expected_updated_at, insert_after_section_id=after)
        metadata = context['metadata']
        # The current patch contract edits a generated draft; library copies keep
        # their source-aware same-operation confirmation until its window adapter is wired.
        if metadata.get('reused_from_entry_id'):
            raise ScriptDraftRejected(status_code=409, detail="复用稿窗口修改需要文案库来源读取接口，请使用当前文案库确认入口；没有修改原稿。")
        task,plan,topic,sources,refs = self.read_inputs(workspace_id,task_id)
        reader = self.validate_inputs(workspace_id,plan,refs)
        binding = (asdict(plan),asdict(topic),tuple(sources),refs,reader)
        window = CreationTaskScriptReviewWindow(script_id,metadata['updated_at'],tuple(part.id for part in sections),context['total_count'])
        review_binding = self._review_candidate(workspace_id,task,plan,topic,sources,reader,metadata['title'],sections,instruction_origin,
            review_window=window, reuse_review=lambda key:self.repository.script_sections_have_review(workspace_id,task_id,script_id,sections,
                binding_digest=key,layout_digest=context['layout_digest']))
        _,new_plan,new_topic,new_sources,new_refs = self.read_inputs(workspace_id,task_id)
        new_reader = self.validate_inputs(workspace_id,new_plan,new_refs)
        if binding != (asdict(new_plan),asdict(new_topic),tuple(new_sources),new_refs,new_reader):
            raise ScriptDraftRejected(status_code=409, detail="方案、资料或读取身份已变化，窗口未保存，请重新读取。")
        if instruction_origin is not None and (reread_origin is None or instruction_origin != reread_origin(True)):
            raise ScriptDraftRejected(status_code=409, detail="本轮要求已变化，窗口未保存。")
        commit_guard=self._source_commit_guard(workspace_id,task_id,binding,write_guard,instruction_origin,reread_origin,draft_only=True)
        return self.repository.patch_assistant_script_sections(workspace_id,task_id,script_id,sections=sections,
            expected_updated_at=payload.expected_updated_at,request_id=request_id,member_id=member_id,
            insert_after_section_id=after,write_guard=commit_guard,review_binding=review_binding)

    def window_request_result(self, workspace_id, task_id, request_id, *, member_id, operation, script_id=None, include_input=False):
        if operation == 'start':
            record=self.repository.script_window_start_result(workspace_id,task_id,str(request_id),member_id)
        elif operation == 'patch' and script_id is not None:
            record=self.repository.script_patch_request_result(workspace_id,task_id,script_id,str(request_id),member_id)
        elif operation == 'confirm' and script_id is not None:
            record=self.repository.script_confirm_saved_result(workspace_id,task_id,script_id,str(request_id),member_id)
        else:
            raise ValueError('文案请求类型或稿件定位无效')
        if record is None:
            pending=self.repository.pending_script_window_request(workspace_id,task_id,script_id,operation,str(request_id),member_id,include_input=include_input)
            if pending is None: raise KeyError(str(request_id))
            return pending
        return record['result']

    def _record_confirmed_origin(self, workspace_id, task_id, script_id, result, payload, member_id, instruction_origin):
        if instruction_origin is None: return
        actual_task=self.repository.get_creation_task(workspace_id,task_id)
        actual_plan=self.repository.get_creation_plan(workspace_id,actual_task.plan_id)
        facts=self.repository.confirmed_script_adoption_facts(workspace_id,task_id,script_id,result['updated_at'])
        self.record_adoption(str(payload.assistant_turn_id),workspace_id,member_id,actual_task,actual_plan,facts,instruction_origin,
                             verified_body_digest=facts.body_digest)

    def _confirm_saved(self, workspace_id, task_id, script_id, payload, *, member_id, instruction_origin=None,
                      reread_origin=None, write_guard=None):
        from app.creation.script_windows import script_confirmation_digest
        from app.topics import CreationTaskScriptReviewWindow
        request_id=str(payload.request_id)
        digest=script_confirmation_digest(payload.expected_updated_at,payload.adoption_kind)
        prior=self.repository.script_confirm_saved_result(workspace_id,task_id,script_id,request_id,member_id)
        if prior is not None:
            if prior['request_digest']!=digest:
                raise ScriptDraftRejected(status_code=409,detail="原确认请求不能更换输入，请读取原结果。")
            self._record_confirmed_origin(workspace_id,task_id,script_id,prior['result'],payload,member_id,instruction_origin)
            return prior['result']
        context=self.repository.saved_script_review_context(workspace_id,task_id,script_id,payload.expected_updated_at)
        metadata=context['metadata']
        if metadata.get('reused_from_entry_id'):
            raise ScriptDraftRejected(status_code=409,detail="复用稿完整确认需要文案库来源窗口，请沿现有来源确认入口，不伪造选题。")
        task,plan,topic,sources,refs=self.read_inputs(workspace_id,task_id)
        reader=self.validate_inputs(workspace_id,plan,refs)
        required=plan.template_settings.get('script_section_count')
        if context['total_count']==0 or (required is not None and context['total_count']!=required):
            raise ScriptDraftRejected(status_code=409,detail="真实全稿段数尚未满足采用要求，请补齐草稿后继续。")
        binding=(asdict(plan),asdict(topic),tuple(sources),refs,reader)
        def current_guard(conn):
            if write_guard is not None: write_guard(conn)
            _,new_plan,new_topic,new_sources,new_refs=self.read_inputs(workspace_id,task_id)
            new_reader=self.validate_inputs(workspace_id,new_plan,new_refs)
            if binding!=(asdict(new_plan),asdict(new_topic),tuple(new_sources),new_refs,new_reader):
                raise ScriptDraftRejected(status_code=409,detail="来源、要求或读取身份已变化，不能采用旧核查结果。")
            if instruction_origin is not None and (reread_origin is None or instruction_origin!=reread_origin(False)):
                raise ScriptDraftRejected(status_code=409,detail="原制作授权已变化，不能采用。")
        cursor,review_binding=0,None
        while cursor is not None:
            page=self.repository.read_creation_task_script_sections(workspace_id,task_id,script_id,
                expected_updated_at=payload.expected_updated_at,after_ordinal=cursor)
            parts=[CreationTaskScriptSection(**item['section']) for item in page['items']]
            window=CreationTaskScriptReviewWindow(script_id,metadata['updated_at'],tuple(part.id for part in parts),context['total_count'],finalizing=True)
            review_binding=self._review_candidate(workspace_id,task,plan,topic,sources,reader,metadata['title'],parts,instruction_origin,
                review_window=window,reuse_review=lambda key:self.repository.script_sections_have_review(workspace_id,task_id,script_id,parts,
                    binding_digest=key,layout_digest=context['layout_digest']))
            self.repository.record_saved_script_section_review(workspace_id,task_id,script_id,parts,payload.expected_updated_at,
                binding_digest=review_binding,layout_digest=context['layout_digest'],write_guard=current_guard)
            cursor=page['next_after_ordinal']
        result=self.repository.confirm_reviewed_script(workspace_id,task_id,script_id,expected_updated_at=payload.expected_updated_at,
            request_id=request_id,member_id=member_id,binding_digest=review_binding,layout_digest=context['layout_digest'],
            adoption_kind=payload.adoption_kind,write_guard=current_guard)
        self._record_confirmed_origin(workspace_id,task_id,script_id,result,payload,member_id,instruction_origin)
        return result

    def _start_window(self, workspace_id, task_id, payload, *, member_id, instruction_origin=None, reread_origin=None, write_guard=None):
        from app.topics import CreationTaskScriptReviewWindow
        request_id = str(payload.request_id)
        request_digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        prior = self.repository.script_window_start_result(workspace_id,task_id,request_id,member_id)
        if prior is not None:
            if prior['request_digest'] != request_digest:
                raise ScriptDraftRejected(status_code=409, detail="首窗原请求不能更换输入，请读取原结果。")
            return prior['result']
        if self.repository.active_creation_task_script_headers(workspace_id,task_id):
            raise ScriptDraftRejected(status_code=409, detail="当前已有稿件，请用稳定段落ID窗口修改或明确整稿替换，不重新创建稿件。")
        identity = uuid5(NAMESPACE_URL, json.dumps([workspace_id,task_id,member_id,request_id])).hex
        script_id, run_id = 'script_'+identity, 'script_run_'+identity
        try:
            self.repository.get_script_generation_run(workspace_id,run_id)
        except KeyError:
            pass
        else:
            raise ScriptDraftRejected(status_code=409, detail="首窗原请求已有运行记录，请查询原状态，不重复启动。")
        window = CreationTaskScriptReviewWindow(script_id,'new',tuple(part.id for part in payload.sections),len(payload.sections))
        self.save(workspace_id,task_id,payload,instruction_origin=instruction_origin,reread_origin=reread_origin,
            write_guard=write_guard,window_start={'script_id':script_id,'run_id':run_id,'request_id':request_id,
                'member_id':member_id,'request_digest':request_digest,'review_window':window})
        return self.repository.script_window_start_result(workspace_id,task_id,request_id,member_id)['result']

    def _source_commit_guard(self, workspace_id, task_id, snapshot, write_guard, instruction_origin, reread_origin, *, draft_only):
        def guard(conn):
            if write_guard is not None: write_guard(conn)
            _,plan,topic,sources,refs=self.read_inputs(workspace_id,task_id)
            reader=self.validate_inputs(workspace_id,plan,refs)
            if snapshot!=(asdict(plan),asdict(topic),tuple(sources),refs,reader):
                raise ScriptDraftRejected(status_code=409,detail="提交时资料、要求或读取身份已变化，核查结果未写入。")
            if instruction_origin is not None and (reread_origin is None or instruction_origin!=reread_origin(draft_only)):
                raise ScriptDraftRejected(status_code=409,detail="提交时原输入已变化，核查结果未写入。")
        return guard

    def save(self, workspace_id, task_id, payload, *, instruction_origin=None, reread_origin=None, write_guard=None, source_binding=None,
             window_start=None):
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
        _topic_route_snapshot=self.route_snapshot
        record_adoption=self.record_adoption
        task, plan, topic, sources, input_references = _assistant_script_inputs(workspace_id, task_id)
        reader = _require_current_script_inputs(workspace_id, plan, input_references)
        sections = [CreationTaskScriptSection(**section.model_dump()) for section in payload.sections]
        if len({part.id for part in sections}) != len(sections) or any(not part.id.strip() for part in sections):
            raise ScriptDraftRejected(status_code=422,detail="文案段落ID必须非空且唯一；没有启动生成记录。")
        allowed = {str(item["reference_id"]) for item in input_references}
        if any(not set(section.evidence_reference_ids).issubset(allowed) for section in sections):
            raise ScriptDraftRejected(status_code=422, detail="文案引用了本次任务以外的资料")
        from app.creation.script_windows import script_revision
        headers = self.repository.active_creation_task_script_headers(workspace_id,task_id)
        revising_confirmed = bool(task.confirmed_script_id and task.can_revise_script)
        latest = next((item for item in headers if item['id']==task.confirmed_script_id), None) if revising_confirmed else (headers[0] if headers else None)
        updating = bool(latest and latest['selection_status']=="draft" and task.stage_status=="waiting_for_user")
        if updating or revising_confirmed:
            if getattr(payload, "edit_mode", None) != "replace_all":
                raise ScriptDraftRejected(status_code=422, detail="已有稿件的完整替换须明确replace_all意图与当前版本；局部修改请使用段落窗口。")
            if payload.expected_updated_at is None:
                raise ScriptDraftRejected(status_code=422, detail="请先读取当前草稿，再提交修订。")
            if script_revision(payload.expected_updated_at) != script_revision(latest['updated_at']):
                raise ScriptDraftRejected(status_code=409, detail="草稿已被修改，请重新读取后再提交。")
        elif getattr(payload, "expected_updated_at", None) is not None:
            raise ScriptDraftRejected(status_code=409, detail="草稿版本已变化，请重新读取后再保存")
        binding = (asdict(plan), asdict(topic), tuple(sources), input_references)
        review_binding = self._review_candidate(workspace_id, task, plan, topic, sources, reader, payload.title, sections, instruction_origin,
            review_window=window_start['review_window'] if window_start else None)
        if window_start:
            result_write = lambda conn,script:self.repository.record_script_window_start(script,window_start['request_id'],
                window_start['member_id'],window_start['request_digest'],review_binding,connection=conn)
        # The model call can outlive source permissions or the current plan/topic.
        # Re-read those bindings; the repository still owns the final draft CAS.
        current_task, current_plan, current_topic, current_sources, current_refs = _assistant_script_inputs(workspace_id, task_id)
        current_reader = _require_current_script_inputs(workspace_id, current_plan, current_refs)
        if binding != (asdict(current_plan), asdict(current_topic), tuple(current_sources), current_refs) or reader != current_reader:
            raise ScriptDraftRejected(status_code=409, detail="方案、选题或资料已变化，修订没有写入，请重新读取当前任务。")
        if instruction_origin is not None and instruction_origin != reread_origin(True):
            raise ScriptDraftRejected(status_code=409, detail="本轮要求已变化，旧要求核查的文案没有保存，请读取当前输入。")
        commit_guard=self._source_commit_guard(workspace_id,task_id,(*binding,reader),write_guard,instruction_origin,reread_origin,draft_only=not revising_confirmed)
        if revising_confirmed:
            if instruction_origin is not None:
                # Revising an adopted body is itself adoption. A discussion-only
                # agent request must never be converted into a manual adoption.
                reread_origin(False)
            delegated = bool(instruction_origin and plan.creation_intent and plan.creation_intent["kind"] == "goal_delegation")
            try:
                task, draft = self.repository.confirm_creation_task_script(workspace_id, task_id, latest['id'],
                    title=payload.title, sections=sections, expected_updated_at=payload.expected_updated_at, write_guard=commit_guard,result_write=result_write,
                    adoption_kind="delegated_system" if delegated else "manual", verified_owner_user_id=reader[0])
            except (KeyError, ValueError) as exc:
                raise ScriptDraftRejected(status_code=409, detail=str(exc)) from exc
            if delegated:
                record_adoption(str(payload.assistant_turn_id), workspace_id, reader[0],
                    task, plan, draft, instruction_origin)
        elif updating:
            try:
                draft = self.repository.update_assistant_script_draft(
                    workspace_id, task_id, latest['id'], title=payload.title, sections=sections,
                    expected_updated_at=payload.expected_updated_at, write_guard=commit_guard,result_write=result_write,
                )
            except (KeyError, ValueError) as exc:
                raise ScriptDraftRejected(status_code=409, detail=str(exc)) from exc
        else:
            run = ScriptGenerationRun(
                id=window_start['run_id'] if window_start else f"script_run_{uuid4().hex[:12]}", workspace_id=workspace_id,
                task_id=task_id, plan_id=plan.id, topic_id=topic.id, status="running",
                route_snapshot=_topic_route_snapshot(workspace_id),
                prompt_schema_version="assistant-script-window/v1" if window_start else "assistant-script-draft/v2",
                input_references=input_references,
            )
            draft = CreationTaskScript(
                id=window_start['script_id'] if window_start else f"script_{uuid4().hex[:12]}", workspace_id=workspace_id,
                task_id=task_id, run_id=run.id, topic_id=topic.id,
                title=payload.title, language=plan.language, sections=sections, draft_complete=window_start is None,
            )
            try:
                _, _, draft = self.repository.save_reviewed_script_generation(run,draft,write_guard=commit_guard,result_write=result_write)
            except (KeyError,ValueError) as exc:
                raise ScriptDraftRejected(status_code=409,detail=str(exc)) from exc
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
