"""Original-goal business calls. The host owns identity; tools never carry credentials."""
from __future__ import annotations

from dataclasses import asdict
import json
import time
import hashlib

from app.creation.editing.metadata_use_case import EditPreviewRejected
from app.assistant.task_business_errors import TaskBusinessRejected, TaskOperationRejected, TaskInvocation
from app.assistant.script_generation_policy import SCRIPT_GENERATION_OPERATIONS, is_script_generation
from app.creation.intent_use_case import CreationIntentUseCase
from app.creation.output_observation_use_case import read_original_observation_request
from app.creation_execution import CreationExecutionActor, ExecutionStartRequest
from app.schemas import CreationTaskScriptDraftRequest, CreationTaskScriptConfirmRequest, CreationTaskTopicConfirmRequest


from contextlib import nullcontext as _Borrow

def record_narration_semantic_review(production, task, origin, payload, *, write_guard):
    """Store a model comparison, never a deterministic factual verdict."""
    from app.creation.contracts import TaskEvent
    from app.schemas import NarrationSemanticReviewRequest
    trusted_input_id=origin.get('input_id') or origin.get('assistant_turn_id')
    if payload.get('input_id') is not None and payload['input_id']!=trusted_input_id:
        raise ValueError('语义复核不能切换原输入')
    payload=NarrationSemanticReviewRequest.model_validate({**payload,'input_id':trusted_input_id}).model_dump(mode='json')
    if not payload['findings']:
        raise ValueError('语义复核须记录具体前后对照依据')
    for finding in payload['findings']:
        if (set(finding)!={'rule_id','before','after','assessment','reason'}
                or finding['assessment'] not in {'preserved','uncertain','conflict'}
                or not all(isinstance(finding[k],str) and finding[k].strip() for k in finding)):
            raise ValueError('每项语义对照须包含原句、改写、结论和依据')
    if payload['conclusion']=='preserved' and any(f['assessment']!='preserved' for f in payload['findings']):
        raise ValueError('存疑或冲突的语义依据不能记为通过')
    if payload['authorized_locked_section_ids']:
        raise ValueError('模型复核不能授予定稿解锁权限')
    source={'conversation_id':origin['conversation_id'],'input_id':trusted_input_id,
            **{k:origin[k] for k in ('assistant_turn_id','input_turn_id') if k in origin}}
    if not source.get('assistant_turn_id'):
        raise ValueError('语义复核缺少可信原回合')
    identity=hashlib.sha256(json.dumps([task.workspace_id,task.id,source,payload['request_id']],
        sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    event=TaskEvent(id='narration_semantic_review_'+identity,workspace_id=task.workspace_id,task_id=task.id,
        job_id=None,event_type='narration_semantic_review',payload={**payload,'source':source,
        'evidence_kind':'pi_semantic_comparison'})
    event=production.save_semantic_review(event,write_guard=write_guard)
    return {'semantic_review_ref':event.id,**event.payload}


class TaskBusiness:
    """Finite operations on one persisted goal, with the same check at owner commit."""
    def __init__(self, production, conversations, authorizations, *, runtime, execution,
                 workspace_facts, script_context, script_case, script_facts, generate_topics, topic_facts,
                 edit_capabilities, script_capabilities, tool_capabilities, material_facts,
                 observation_facts, observe_output, request_result, media_result, record_adoption, segments=None,native_case=None,timeline_restores=None,exact_case_for=None,media_jobs_list=None,execution_capabilities=None,read_script_sections=None,device_preparation=None,generation_case=None,creator_memory=None,execution_status=None,quality=None,media_binding=None,edit_controls=None):
        self.production,self.conversations,self.authorizations=production,conversations,authorizations
        self.runtime,self.execution=runtime,execution
        self.workspace_facts,self.script_context,self.script_case=workspace_facts,script_context,script_case
        self.script_facts=script_facts
        self.generate_topics,self.topic_facts=generate_topics,topic_facts
        self.edit_capabilities,self.script_capabilities,self.tool_capabilities=edit_capabilities,script_capabilities,tool_capabilities
        self.material_facts=material_facts
        self.observation_facts,self.observe_output=observation_facts,observe_output
        self.request_result,self.media_result=request_result,media_result
        self.record_adoption=record_adoption
        self.segments=segments
        self.native_case=native_case
        self.timeline_restores=timeline_restores
        self.exact_case_for=exact_case_for
        self.media_jobs_list=media_jobs_list
        self.execution_capabilities=execution_capabilities
        self.read_script_sections=read_script_sections
        self.quality=quality
        self.device_preparation=device_preparation
        self.generation_case,self.creator_memory=generation_case,creator_memory
        self.execution_status=execution_status
        self.media_binding=media_binding
        self.edit_controls=edit_controls

    def invoke_native(self, operation, arguments, *, call_id,tool_call_id,source_binding,cancel_event,deadline_monotonic, invocation: TaskInvocation):
        """Same task authorization, with the native owner as sole execution writer."""
        if is_script_generation(source_binding):
            raise TaskBusinessRejected('生成按钮不授权原生执行或采用操作')
        actor,task,goal,origin,_scope=self.check(source_binding,cancel_event,deadline_monotonic,check_version=False)
        if self.native_case is None:
            raise TaskBusinessRejected('原生执行用例尚未装配')
        native_origin={**origin,'task_id':task.id,'assistant_turn_id':source_binding['turn_id'],
            'input_id':origin.get('input_id') or source_binding['turn_id'],
            'session_id':source_binding['session_id'],'sdk_round':source_binding.get('sdk_round',0)}
        def guard(conn):
            return self.check(source_binding,cancel_event,deadline_monotonic,connection=conn,check_version=False)
        invocation.owner_entered=True
        value=self.native_case.dispatch(operation,arguments,actor=actor,origin=native_origin,
            write_guard=guard,runtime=self.runtime() if operation in {'submit_task_command','cancel_task_command','register_task_artifacts'} else None,
            call_id=call_id,cancel_event=cancel_event,
            deadline_monotonic=deadline_monotonic)
        return value

    def original(self, goal):
        ws,task_id=goal['workspace_id'],goal['task_id']
        authorization=self.production.task_authorization_records.get_task_authorization(ws,task_id)
        if authorization is None:
            raise TaskBusinessRejected('原目标没有持久任务授权；须从可信采用入口核实')
        actor=CreationExecutionActor.from_event(ws,authorization.identity)
        if (actor.user_id or actor.member_id)!=goal['member_id']:
            raise TaskBusinessRejected('原目标成员与任务授权不一致')
        if self.authorizations.verdict(actor,task_id,'task_goal',check_scope=False)!='ready':
            raise TaskBusinessRejected('原任务授权已撤销、到期或目标已变化')
        scope=self.production.execution_records.get_task_event(ws,self.authorizations.business_context_id(goal)).payload
        if scope['authorization_id']!=authorization.id or scope['identity']!=authorization.identity:
            raise TaskBusinessRejected('原目标封存范围与任务授权不一致')
        task=self.production.task_records.get_creation_task(ws,task_id)
        return actor,task,scope

    def check(self, binding, cancelled, deadline, *, connection=None, check_version=True):
        if cancelled.is_set() or time.monotonic()>=deadline:
            raise TaskBusinessRejected('本次原目标调用已停止或超过截止',kind='conflict')
        if is_script_generation(binding):
            return self._check_generation(binding,connection=connection)
        ws,task_id=binding['workspace_id'],binding['task_id']
        with (self.production._connect() if connection is None else _Borrow(connection)) as conn:
            origin=self.conversations._task_turn_source(conn,binding['turn_id'],ws,binding['member_id'],task_id)
            root=self.conversations._goal_root(conn,binding['turn_id'])
            if (root is None or str(root['goal'].get('revision',0))!=str(binding['goal_revision'])
                    or root['goal'].get('source_input_id')!=binding.get('input_id')
                    or origin.get('input_id')!=binding.get('input_id')
                    or origin['conversation_id']!=binding['conversation_id']
                    or not self.conversations._goal_origin_current(conn,root)):
                raise TaskBusinessRejected('原目标修订或已消费输入已变化',kind='conflict')
            authorization=self.production.task_authorization_records.get_task_authorization(ws,task_id,connection=conn,metadata_only=True)
            if authorization is None:
                raise TaskBusinessRejected('原任务授权不存在')
            actor=CreationExecutionActor.from_event(ws,authorization.identity)
            if ((actor.user_id or actor.member_id)!=binding['member_id']
                    or self.authorizations.verdict(actor,task_id,'task_goal',connection=conn,check_scope=False)!='ready'):
                raise TaskBusinessRejected('原任务授权已到期或撤销')
            task=self.production.task_records.get_creation_task(ws,task_id,connection=conn)
            version=conn.execute('SELECT sequence FROM timeline_versions WHERE workspace_id=? AND task_id=? AND id=?',
                (ws,task_id,task.current_timeline_version_id)).fetchone() if task.current_timeline_version_id else None
            snapshot=binding.get('task_version')
            if check_version and snapshot is not None and (snapshot.get('timeline_version_id'),snapshot.get('timeline_sequence',0)) != (task.current_timeline_version_id,version[0] if version else 0):
                raise TaskBusinessRejected('原目标的预期作品版本已变化',kind='conflict')
            sealed=self.production.execution_records.get_task_event(ws,self.authorizations.business_context_id(root['goal']),connection=conn).payload
            if sealed['authorization_id']!=authorization.id or sealed['identity']!=authorization.identity:
                raise TaskBusinessRejected('原目标封存范围不匹配')
            row=conn.execute('SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?',(ws,task.plan_id)).fetchone()
            plan=json.loads(row[0])
            if (sealed['knowledge']!=plan['knowledge_references'] or sealed['sources']!=plan['source_references']):
                raise TaskBusinessRejected('原目标引用范围已变化，未读取新增私人资料',kind='conflict')
            if self.production.task_authorization_records.task_source_revisions(ws,[item['id'] for item in sealed['knowledge']],
                    [item['id'] for item in sealed['sources']],connection=conn)!=sealed['source_revisions']:
                raise TaskBusinessRejected('原目标固定资料或本地读取权限已变化',kind='conflict')
        return actor,task,root['goal'],origin,sealed

    def _check_generation(self, binding, *, connection):
        if (binding.get('intent_kind') != 'script_generation' or not binding.get('generation_request_id')
                or binding.get('input_id') is not None or binding.get('input_turn_id') is not None):
            raise TaskBusinessRejected('生成动作没有原始按钮请求来源')
        if self.generation_case is None:
            raise TaskBusinessRejected('原生成请求业务尚未装配',kind='unavailable')
        ws,task_id,member=binding['workspace_id'],binding['task_id'],binding['member_id']
        with (self.production._connect() if connection is None else _Borrow(connection)) as conn:
            try:
                facts=self.generation_case.read_generation_request(conn,ws,task_id,binding['generation_request_id'],member)
            except PermissionError as exc:
                raise TaskBusinessRejected(str(exc)) from exc
            source=facts['source']
            if (facts['request_id']!=binding['generation_request_id'] or facts['task_id']!=task_id
                    or facts['conversation_id']!=binding['conversation_id'] or facts['turn_id']!=binding['turn_id']
                    or facts['requested_effect']!='draft' or facts['can_continue'] is not True
                    or source!={'kind':'user_action','request_id':facts['request_id'],'member_id':member,
                        'conversation_id':facts['conversation_id'],'assistant_turn_id':facts['turn_id']}):
                raise TaskBusinessRejected('原生成请求、回合或写稿范围已变化',kind='conflict')
            actor=CreationExecutionActor.from_event(ws,facts['actor'])
            if (actor.user_id or actor.member_id)!=member:
                raise TaskBusinessRejected('原生成动作成员与真实身份不一致')
            task=self.production.task_records.get_creation_task(ws,task_id,connection=conn)
        origin={**source,'intent_kind':'script_generation','generation_request_id':facts['request_id'],
                'requested_effect':'draft','raw_command':''}
        return actor,task,facts,origin,None

    def visual_job_belongs_to_goal(self, goal, actor, job_id):
        from app.assistant_goal_continuation import GoalResultProjection
        job=self.production.job_records.get_job_run(goal['workspace_id'],job_id)
        if job.task_id!=goal['task_id']:
            return False
        if job.kind=='creation_task_execution':
            return any(event.job_id==job_id for event in self.execution_sources(goal))
        if job.kind!='creation_task_segment_revision':
            return False
        start=self.production.execution_records.get_task_event(goal['workspace_id'],f'event_{job_id}_start')
        if CreationExecutionActor.from_event(job.workspace_id,start.payload)!=actor:
            return False
        command=start.payload['command']
        review=self.production.execution_records.get_task_event(job.workspace_id,command['semantic_review_ref'])
        if review.task_id!=goal['task_id'] or review.event_type!='narration_semantic_review':
            return False
        source=review.payload['source']
        if source['input_id']!=command['input_id']:
            return False
        if source['input_id']==source['assistant_turn_id'] and not source.get('input_turn_id'):
            source={key:value for key,value in source.items() if key!='input_id'}
        return GoalResultProjection(self.production,self.conversations).source_belongs_to_goal(goal,source)

    def read_execution(self, goal, actor, *, job_id=None):
        ws,task_id=goal['workspace_id'],goal['task_id']
        located=self.execution_sources(goal)
        if job_id is None:
            candidates={event.job_id for event in located}
            with self.production._connect() as conn:
                revisions=conn.execute("SELECT j.id FROM job_runs j JOIN task_events e ON e.workspace_id=j.workspace_id "
                    "AND e.id='event_' || j.id || '_start' WHERE j.workspace_id=? AND j.task_id=? "
                    "AND j.kind='creation_task_segment_revision' AND json_extract(e.payload_json,'$.member_id')=? "
                    "ORDER BY j.created_at DESC,j.id DESC LIMIT 24",(ws,task_id,actor.member_id)).fetchall()
            candidates.update(row['id'] for row in revisions if self.visual_job_belongs_to_goal(goal,actor,row['id']))
            if candidates:
                job_id=max((self.production.job_records.get_job_run(ws,key) for key in candidates),key=lambda job:(job.created_at,job.id)).id
            else:
                return {'task_id':task_id,'job':None,'stages':[],'video_ready':False,
                    'waiting_reason':'原目标没有可核验的制作作业定位，请按原回执查询。',
                    'visual_decisions':{'items':[],'total_count':0,'truncated':False}}
        if not self.visual_job_belongs_to_goal(goal,actor,job_id):
            raise TaskBusinessRejected('制作作业不属于原目标来源')
        return self.execution.read_bound(ws,task_id,job_id,actor,verify_media=False)

    def execution_sources(self,goal):
        from app.assistant_goal_continuation import GoalResultProjection
        projection=GoalResultProjection(self.production,self.conversations)
        return [event for event in self.production.execution_records.list_task_events(goal['workspace_id'],goal['task_id'])
            if event.event_type=='assistant_execution_located' and projection.source_belongs_to_goal(goal,event.payload['source'])]

    def invoke(self, operation, arguments, *, call_id, tool_call_id, source_binding, cancel_event, deadline_monotonic, invocation: TaskInvocation):
        binding=dict(source_binding)
        if is_script_generation(binding) and operation not in SCRIPT_GENERATION_OPERATIONS:
            raise TaskBusinessRejected('本次生成动作仅授权读取与保存原请求草稿')
        reconcile_original=(operation in {'read_task_workspace','read_task_quality_check','read_task_quality_continuation','continue_task_quality_check','read_script_window_request','read_timeline_history','read_timeline_restore_request','read_task_observations','read_task_observation_request','read_task_media_job','read_creation_execution','read_narration_revision','read_task_edit_request','read_narration_voice_request','read_task_media_jobs','read_segment_visual_decision','apply_task_edit','preview_task_edit','select_narration_voice'}
            or operation=='observe_task_output' and (arguments.get('payload') or {}).get('request_id') is not None)
        # Section owners validate the frozen target and dependency digest at
        # commit. An unrelated timeline publication may legitimately move HEAD.
        section_scoped=operation in {'read_narration_segment_context','read_narration_segments',
            'read_narration_segment_sources','record_narration_semantic_review','revise_narration_segment','resume_narration_revision'}
        targeted_restore=operation=='restore_timeline' and bool((arguments.get('payload') or {}).get('target_operation_id'))
        check_version=not (reconcile_original or section_scoped or targeted_restore)
        actor,task,goal,origin,sealed=self.check(binding,cancel_event,deadline_monotonic,check_version=check_version)
        if arguments.get('task_id',task.id)!=task.id:
            raise TaskBusinessRejected('工具参数不能切换原任务')
        if operation=='observe_task_output' and (arguments.get('payload') or {}).get('request_id') is not None:
            from app.schemas import CreationTaskObservationRequest
            previous=read_original_observation_request(self.production,task,actor,
                CreationTaskObservationRequest.model_validate(arguments['payload']))
            if previous is not None:
                return previous
        declared=arguments.get('assistant_turn_id') or (arguments.get('payload') or {}).get('assistant_turn_id')
        if declared is not None and declared!=binding['turn_id']:
            raise TaskBusinessRejected('工具参数不能切换原回合')
        if operation in {'record_input_understanding','record_narration_semantic_review','revise_narration_segment'}:
            if arguments['payload'].get('input_id')!=(origin.get('input_id') or binding['turn_id']):
                raise TaskBusinessRejected('局部修改不能切换原输入')
        if operation in {'save_assistant_script_draft','start_script_window','patch_script_sections','confirm_saved_script','confirm_creation_task_script','confirm_creation_task_topic','observe_task_output'}:
            arguments={**arguments,'payload':{**arguments['payload'],'assistant_turn_id':binding['turn_id']}}
        def guard(conn):
            return self.check(binding,cancel_event,deadline_monotonic,connection=conn,
            check_version=check_version or operation=='continue_task_quality_check')
        invocation.owner_entered=True
        value=self.execute(operation,arguments,actor,task,goal,{**origin,'assistant_turn_id':binding['turn_id']},sealed,guard)
        if is_script_generation(binding):
            self.check(binding,cancel_event,deadline_monotonic,check_version=False)
        return value

    def input_delivery(self, source_binding):
        from app.assistant.input_delivery import AssistantInputDelivery
        binding=dict(source_binding)
        def consume(item, *, cancel_event, deadline_monotonic):
            def validate(conn, current, old_goal):
                actor,task,_goal,_origin,sealed=self.check(binding,cancel_event,deadline_monotonic,connection=conn,check_version=False)
                return self._input_context(conn,current['request_context'],task,actor,old_goal,sealed)
            def seal(conn, old_goal, goal, context):
                if cancel_event.is_set() or time.monotonic()>=deadline_monotonic:
                    raise TaskBusinessRejected('追加输入消费已停止或超过截止',kind='conflict')
                return self.authorizations.inherit_business_context(old_goal,goal,context,connection=conn)
            consumed=self.conversations.consume_task_goal_input(binding['conversation_id'],binding['workspace_id'],
                binding['member_id'],item['input_id'],expected_binding=binding,validate=validate,seal=seal)
            goal,context=consumed['goal'],consumed['context']
            snapshot=context['target_snapshot']
            version={key:snapshot[key] for key in ('timeline_version_id','timeline_sequence')}
            trusted={'goal_revision':str(goal['revision']),'input_id':goal['source_input_id'],
                'input_turn_id':goal['source_input_turn_id'],'task_version':version}
            binding.update(trusted)
            delta={'INPUT_ID':item['input_id'],'INPUT_TURN_ID':binding['turn_id'],
                'USER_INSTRUCTION':item['message'],'SELECTED_REFS':json.dumps(context['selected_object_refs']),
                'SELECTED_REFERENCES':json.dumps(snapshot['selected_references'],ensure_ascii=False),
                'TASK_VERSION':json.dumps(version),'PLAYHEAD_MS':'',
                **{key.upper():json.dumps(snapshot[key],ensure_ascii=False) if snapshot.get(key) else ''
                   for key in ('material_selection','profile_reference','knowledge_reference')},
                **{key:'' for key in ('TASK_READ_READY','SCRIPT_DRAFT_UPDATED_AT','EDIT_CAPABILITIES',
                    'MEDIA_SCRIPT_CAPABILITIES','SCRIPT_TOOL_CAPABILITIES')}}
            return {'environment':{'AI_VIDEO_DIARY_AGENT_'+key:value for key,value in delta.items()},
                'frozen_context':{'kind':'task','task_id':binding['task_id'],
                    'selected_object_refs':context['selected_object_refs'],'target_snapshot':snapshot},
                '_business_binding':trusted}
        return AssistantInputDelivery(self.conversations,conversation_id=binding['conversation_id'],
            workspace_id=binding['workspace_id'],member_id=binding['member_id'],turn_id=binding['turn_id'],consume=consume)

    def claim_pending_input(self, candidate):
        def validate(conn,item,goal):
            actor,task,sealed=self.original(goal)
            return self._input_context(conn,item['request_context'],task,actor,goal,sealed)
        return self.conversations.claim_pending_goal_input(candidate,validate=validate,
            seal=lambda conn,old,new,context:self.authorizations.inherit_business_context(old,new,context,connection=conn))

    def read_input_submission(self, candidate):
        """Read the preserved original request without running or resubmitting Pi."""
        goal,item=candidate['root']['goal'],candidate['input']
        actor,task,_scope=self.original(goal)
        original=item.get('submission') or item['business_effect']
        try:
            receipt=self.segments.read_request(task.workspace_id,task.id,original['request_id'],actor)
        except KeyError:
            return {'task_id':task.id,'input_id':item['input_id'],'request_id':original['request_id'],'status':'result_unknown'}
        if (receipt['request_id']!=original['request_id'] or receipt['input_id']!=item['input_id']
                or receipt['task_id']!=task.id or original.get('section_id',receipt['section_id'])!=receipt['section_id']):
            raise ValueError('回读修改不属于原输入或稳定段落')
        return receipt

    def _input_context(self, conn, frozen, task, actor, old_goal, sealed):
        from app.assistant.references import normalize_selection, validate_timeline_reference, timeline_object_refs
        from app.source_policy import knowledge_item_readable as content_readable
        from datetime import datetime
        if frozen.get('kind')!='task' or frozen.get('task_id')!=task.id:
            raise TaskBusinessRejected('追加输入切换了任务，需要从交互入口重新授权')
        snapshot=frozen['target_snapshot']
        timeline=(self.production.timeline_records.get_timeline_version(task.workspace_id,task.current_timeline_version_id,connection=conn)
            if task.current_timeline_version_id else None)
        version={'timeline_version_id':task.current_timeline_version_id,'timeline_sequence':timeline.sequence if timeline else 0}
        if any(snapshot.get(key)!=value for key,value in version.items()):
            raise ValueError('追加输入接收后作品版本已变化，请查看当前作品后重新发送')
        reference=normalize_selection(frozen.get('selected_reference'))
        items=reference['items'] if reference and reference['kind']=='selection' else [reference] if reference else []
        tracks=self.production.subtitle_records.list_subtitle_tracks(task.workspace_id,task.id,connection=conn)
        authorization=self.production.task_authorization_records.get_task_authorization(task.workspace_id,task.id,connection=conn)
        if self.authorizations.status(task.workspace_id,task.id,identity=authorization.identity,
                operation='task_goal',connection=conn)!='valid':
            raise TaskBusinessRejected('原任务有限资源范围已变化，追加输入需要重新授权')
        previous=old_goal['request_context']
        private=sealed.get('selected_references',[])
        def material(selection):
            if selection['asset_id'] not in authorization.resource_scope['assets']:
                raise TaskBusinessRejected('追加输入引用了原范围外素材，需要重新授权')
            asset=self.production.get_asset(task.workspace_id,selection['asset_id'],connection=conn)
            if not content_readable(user_id=actor.user_id or actor.member_id,local_diagnostic=False,
                    visibility=asset.visibility,owner_user_id=asset.owner_user_id):
                raise TaskBusinessRejected('追加输入素材已不可读取，需要重新授权')
            if datetime.fromisoformat(selection['expected_updated_at'].replace('Z','+00:00'))!=asset.updated_at:
                raise ValueError('追加输入所选素材版本已变化，请重新选择')
            if selection['scope']=='segment':
                segment=next((part for part in asset.segments if part.id==selection['segment_id']),None)
                if segment is None or datetime.fromisoformat(selection['expected_segment_updated_at'].replace('Z','+00:00'))!=segment.updated_at:
                    raise ValueError('追加输入所选片段已变化，请重新选择')
        for item in items:
            if item['kind'] in {'person','voice','knowledge'}:
                if item not in private:
                    raise TaskBusinessRejected('追加输入引入新的私人资料或版本，需要从交互入口重新授权')
            elif item['kind']=='material':
                material(item['selection'])
            else:
                if timeline is None:
                    raise ValueError('追加输入引用的作品版本不可用')
                validate_timeline_reference(item,task=task,timeline=timeline,subtitle_tracks=tracks)
        for key in ('profile_reference','knowledge_reference'):
            if frozen.get(key) is not None and frozen[key]!=previous.get(key):
                raise TaskBusinessRejected('追加输入引入新的私人资料，需要从交互入口重新授权')
        if snapshot.get('material_selection'):
            material(snapshot['material_selection'])
        snapshot={**snapshot,'selected_references':items,**version,
            **{key:sealed.get(key) if frozen.get(key) else None for key in ('profile_reference','knowledge_reference')}}
        return {**frozen,'target_snapshot':snapshot,'selected_object_refs':timeline_object_refs(items,task.id,tracks)}

    def finish_goal(self, claim, reply, preview):
        if isinstance(preview, dict) and preview.get('kind') == 'script_window_request':
            return self.finish(claim, reply, script_window_request=preview)
        if isinstance(preview, dict) and preview.get('kind') == 'quality_continuation':
            return self.finish(claim, reply, quality_continuation=preview)
        if isinstance(preview, dict) and preview.get('kind') == 'media_job':
            return self.finish(claim, reply, reported_media=preview['media_job'])
        return self.finish(claim, reply)

    def finish(self, claim, reply, *, script_window_request=None, quality_continuation=None, reported_media=None):
        """Project real host receipts; a model's proposed result is never adopted here."""
        goal=self.conversations.get_task_goal(claim['turn_id'])
        if (goal is None or goal['current_turn_id']!=claim['turn_id'] or goal['status']!='running'
                or any(goal[key]!=claim['goal'][key] for key in ('original_turn_id','task_id','workspace_id','member_id','conversation_id'))):
            raise TaskBusinessRejected('后台回合已不属于当前目标',kind='conflict')
        claim.update(goal=goal,request_context=goal['request_context'])
        ws,task_id=goal['workspace_id'],goal['task_id']
        actor,task,_sealed=self.original(goal)
        events=self.production.execution_records.list_task_events(ws,task_id)
        response={'reply':reply,'turn_id':claim['turn_id'],'draft_version':1}
        proofs=[]
        if quality_continuation is not None:
            from app.assistant.result_projection import resolve_quality_continuation, quality_continuation_message
            try:
                actual = resolve_quality_continuation(quality_continuation, task_id=task_id,
                    invocation_input_id=goal.get('source_input_id') or claim['turn_id'],
                    read_original=lambda job_id, failed_request_id: self.quality.read_continuation(
                        ws, task_id, job_id, failed_request_id, actor))
            except ValueError as exc:
                raise TaskBusinessRejected(str(exc), kind='conflict') from exc
            reply = quality_continuation_message(actual)
            response.update(reply=reply, quality_continuation=actual)
        if script_window_request is not None:
            from app.assistant.script_window_receipts import resolve_script_window_request, script_window_message
            if script_window_request.get('input_id')!=goal.get('source_input_id'):
                raise TaskBusinessRejected('文案窗口回执不属于当前输入',kind='conflict')
            window=resolve_script_window_request(script_window_request,task_id=task_id,
                read_original=lambda request_id,operation,script_id:self.script_case(actor).window_request_result(
                    ws,task_id,request_id,member_id=actor.user_id or actor.member_id,
                    operation=operation,script_id=script_id))
            reply=script_window_message(window)
            response.update(reply=reply,script_window_request=window)
            proofs.append({'kind':'script_window_request','id':window['request_id'],
                'operation':window['operation'],'status':window['status'],
                **({'input_id':goal['source_input_id']} if goal.get('source_input_id') else {})})
        located=[event for event in events if event.event_type=='assistant_execution_located'
            and event.payload['source']['assistant_turn_id']==claim['turn_id']
            and event.payload['source'].get('input_id')==goal.get('source_input_id')]
        if located:
            actual=self.read_execution(goal,actor,job_id=located[-1].job_id)
            response.update(execution_status=actual,execution_action=located[-1].payload['source']['action'])
            proofs.append({'kind':'execution_status','id':located[-1].job_id,
                **({'input_id':goal['source_input_id']} if goal.get('source_input_id') else {})})
        drafts=[event for event in events if event.event_type=='assistant_script_saved'
            and event.payload['source']['turn_id']==claim['turn_id']
            and event.payload['source'].get('input_id')==goal.get('source_input_id')]
        if drafts and script_window_request is None:
            response['script_draft']=asdict(self.production.script_records.get_creation_task_script(ws,drafts[-1].payload['script_id']))
        jobs=[event for event in events if event.event_type=='task_media_submitted'
            and event.payload['arguments'].get('_typed_source',{}).get('assistant_turn_id')==claim['turn_id']
            and event.payload['arguments'].get('_typed_source',{}).get('input_id')==goal.get('source_input_id')]
        if jobs:
            response['media_job']=self.media_result(task,actor,jobs[-1].job_id)
            proofs.append({'kind':'media_job','id':jobs[-1].job_id,
                **({'input_id':goal['source_input_id']} if goal.get('source_input_id') else {})})
        if reported_media is not None:
            if self.media_binding is None:
                raise TaskBusinessRejected('媒体来源核验尚未装配', kind='unavailable')
            selection = self.media_binding.select_finish(claim['turn_id'], reported_media)
            if selection.completed_slot is not None:
                slot = selection.completed_slot
                response['media_job'] = self.media_binding.completed_receipt(task, actor, claim['turn_id'], slot,
                    contract=slot['requested_output'], input_refs=slot['input_refs'])
        proposals, _ = self.production.execution_records.goal_pending_preview_rows(ws, task_id,
            task.current_timeline_version_id, goal['conversation_id'], goal['member_id'])
        for row in proposals:
            source = json.loads(row['constraints_json'])['request_source']
            if (source.get('assistant_turn_id') != claim['turn_id']
                    or source.get('input_id') != goal.get('source_input_id')):
                continue
            proposal = self.production.edit_records.get_edit_proposal(ws, row['id'])
            intent = self.production.edit_records.get_edit_intent(ws, proposal.intent_id)
            response['edit_preview'] = {'intent': asdict(intent), 'proposal': asdict(proposal)}
            proofs.append({'kind': 'edit_preview', 'id': proposal.id,
                **({'input_id': goal['source_input_id']} if goal.get('source_input_id') else {})})
        from threading import Event
        binding={**{key:goal[key] for key in ('workspace_id','member_id','task_id','conversation_id')},
            'turn_id':claim['turn_id'],'goal_revision':str(goal.get('revision',0)),
            'input_id':goal.get('source_input_id'),'input_turn_id':goal.get('source_input_turn_id')}
        from fastapi.encoders import jsonable_encoder
        self.conversations.finish_turn(claim['turn_id'],'completed',reply,
            jsonable_encoder({'kind':'task_turn','response':response,'goal_business_actions':proofs}),
            write_guard=lambda conn:self.check(binding,Event(),float('inf'),connection=conn,check_version=False),
            completed_input_id=goal.get('source_input_id') if goal.get('source_input_turn_id')==claim['turn_id'] else None)
        return response

    def execute(self, operation, args, actor, task, goal, origin, sealed, guard):
        ws,task_id=task.workspace_id,task.id
        generation = is_script_generation(origin)
        if generation and (origin.get('requested_effect')!='draft' or operation not in SCRIPT_GENERATION_OPERATIONS):
            raise TaskBusinessRejected('本次生成动作不授权采用、制作或其他写入')
        if generation and operation in {'patch_script_sections','read_script_sections'} and args['script_id']!=goal['script_id']:
            raise TaskBusinessRejected('生成窗口不能切换原请求的稿件')
        if operation=='read_segment_visual_decision':
            if not self.visual_job_belongs_to_goal(goal,actor,args['job_id']):
                raise TaskBusinessRejected('画面决定不属于原目标来源')
            return self.execution.read_visual_decision(ws,task_id,args['job_id'],args['section_id'],actor,
                offset=args.get('offset',0),limit=args.get('limit',8))
        if operation in {'apply_task_edit','preview_task_edit'}:
            from app.schemas import CreationTaskExactEditRequest
            payload=CreationTaskExactEditRequest.model_validate({**args['payload'], 'assistant_turn_id':origin['assistant_turn_id']})
            case=self.exact_case_for(write_guard=guard)
            identity={'member_id':actor.user_id or actor.member_id,'local_diagnostic':False}
            if operation=='apply_task_edit':
                result=case.apply(task,actor,payload,identity=identity,origin=origin)
            else:
                result=case.submit_preview(self.runtime,actor,task,payload,identity=identity,
                    origin=origin,draft_only=args.get('draft_only',False),project_media=lambda media,job,actor,runtime:self.media_result(task,actor,job.id))
            if result.status_code>=400:
                raise EditPreviewRejected(result.status_code,result.value.get('detail',result.value))
            return result.value
        if operation=='read_task_edit_request':
            from app.assistant_goal_continuation import GoalResultProjection
            projection=GoalResultProjection(self.production,self.conversations)
            event_id,event=projection.request_claim(task,actor.user_id or actor.member_id,args['request_id'],args.get('phase','preview'))
            source=self.exact_case_for().read_request_source(task,event_id,actor)
            if (not source.get('assistant_turn_id') or not projection.source_belongs_to_goal(goal,source)):
                raise TaskBusinessRejected('原请求不属于当前目标输入')
            return self.request_result(task,actor,args['request_id'],args.get('phase','preview'))
        if operation=='read_task_media_jobs':
            return self.media_jobs_list(task,actor,cursor=args.get('cursor'),limit=args.get('limit',20))
        if operation in {'select_narration_voice','read_narration_voice_request'}:
            member=actor.user_id or actor.member_id
            if operation=='read_narration_voice_request':
                return self.production.narration_voice_selection.get_creation_task_narration_voice_selection(ws,task_id,args['request_id'],member_id=member)
            from app.schemas import CreationTaskNarrationVoiceSelectionRequest
            payload=CreationTaskNarrationVoiceSelectionRequest.model_validate(args['payload'])
            return self.execution.select_narration_voice(runtime_for=self.runtime,actor=actor,write_guard=guard,
                workspace_id=ws,task_id=task_id,member_id=member,
                **{**payload.model_dump(exclude={'assistant_turn_id'}),'request_id':str(payload.request_id)})
        if operation in {'read_edit_control_context', 'preview_edit_selection', 'discard_edit_proposal'}:
            if self.edit_controls is None:
                raise TaskBusinessRejected('编辑控制业务用例尚未装配', kind='unavailable')
            if operation == 'read_edit_control_context':
                return self.edit_controls.read(task, actor=actor)
            from pydantic import ValidationError
            from app.schemas import CompoundEditSelectionRequest, CreationTaskTimelineUndoRequest
            try:
                payload = (CompoundEditSelectionRequest.model_validate(args['payload']) if operation == 'preview_edit_selection'
                    else CreationTaskTimelineUndoRequest.model_validate({**args['payload'],
                        'assistant_turn_id': origin['assistant_turn_id']}))
            except ValidationError as exc:
                raise TaskOperationRejected(str(exc), kind='invalid_input') from exc
            if operation == 'preview_edit_selection':
                if args.get('draft_only') is not True:
                    raise TaskOperationRejected({'error': 'edit_selection_draft_required',
                        'reason': '后台修改选择仅准备草稿参数；媒体预览须另行取得执行许可。',
                        'submission_effect': 'not_submitted'}, kind='conflict')
                return self.edit_controls.preview_selection(task, args['proposal_id'], payload, actor=actor,
                    origin=origin, write_guard=guard, draft_only=True)
            return self.edit_controls.discard(task, args['proposal_id'], payload, actor=actor,
                origin=origin, write_guard=guard)
        if operation in {'read_timeline_history','read_timeline_restore_request','restore_timeline'}:
            if self.timeline_restores is None:
                raise TaskBusinessRejected('原修改恢复用例尚未装配')
            if operation=='read_timeline_history':
                return self.timeline_restores.history(ws,task_id,actor,
                limit=args.get('limit',20),before_sequence=args.get('before_sequence'))
            if operation=='read_timeline_restore_request':
                return self.timeline_restores.read_request(ws,task_id,args['request_id'],actor)
            from app.creation.editing.timeline_restore_use_case import TimelineRestoreCommand
            from app.schemas import CreationTaskTimelineRestoreRequest
            payload=CreationTaskTimelineRestoreRequest.model_validate(args['payload'])
            if args['action'] not in {'undo','redo'}:
                raise TaskBusinessRejected('恢复动作不可用')
            result=self.timeline_restores.execute(TimelineRestoreCommand(ws,task,args['action']=='redo'),actor=actor,
                request_id=payload.request_id,target_operation_id=payload.target_operation_id,
                expected_timeline_version_id=payload.expected_timeline_version_id,
                expected_timeline_sequence=payload.expected_timeline_sequence,write_guard=guard,
                source={'assistant_turn_id':goal['current_turn_id'],'input_id':origin.get('input_id') or goal['current_turn_id']})
            return {'operation':result.operation,'montage':result.montage,'narration':result.narration,
                'restore_request':result.restore_request,'execution_readiness':result.execution_readiness}
        if operation in {'read_narration_segment_context','read_narration_segment_sources','read_narration_segments','read_narration_revision','revise_narration_segment','resume_narration_revision'}:
            if self.segments is None:
                raise TaskBusinessRejected('局部修改业务用例尚未装配')
            if operation=='read_narration_segment_context':
                return self.segments.context(ws,task_id,args['segment_id'],actor)
            if operation=='read_narration_segment_sources':
                return self.segments.sources(ws,task_id,args['segment_id'],actor,
                offset=args.get('offset',0),limit=args.get('limit',8),text_offset=args.get('text_offset',0),max_chars=args.get('max_chars',3000))
            if operation=='read_narration_segments':
                return self.segments.list_segments(ws,task_id,actor,
                offset=args.get('offset',0),limit=args.get('limit',24))
            if operation=='read_narration_revision':
                return self.segments.read_request(ws,task_id,args['request_id'],actor)
            if operation=='resume_narration_revision':
                return self.segments.resume_request(ws,task_id,args['request_id'],actor,
                runtime=self.runtime(),write_guard=guard)
            from app.schemas import NarrationRevisionRequest
            payload=NarrationRevisionRequest.model_validate({k:v for k,v in args['payload'].items() if k!='assistant_turn_id'})
            self.conversations.record_input_submission(goal['current_turn_id'],ws,goal['member_id'],
                input_id=payload.input_id,request_id=payload.request_id,section_id=payload.section_id)
            def commit_guard(conn):
                guard(conn)
                self.conversations.record_input_business_effect(goal['current_turn_id'],ws,goal['member_id'],
                    input_id=payload.input_id,request_id=payload.request_id,status='queued',
                    adoption_mode=payload.adoption_mode,connection=conn)
            return self.segments.submit_revision(ws,task_id,actor,payload,runtime=self.runtime(),write_guard=commit_guard)
        if operation=='record_input_understanding':
            return self.conversations.record_input_understanding(origin['conversation_id'],ws,goal['member_id'],
                goal['current_turn_id'],origin.get('input_id'),
                **{k:v for k,v in args['payload'].items() if k not in {'assistant_turn_id','input_id'}})
        if operation=='record_narration_semantic_review':
            return record_narration_semantic_review(self.production,task,
                {**origin,'assistant_turn_id':goal['current_turn_id']},
                {k:v for k,v in args['payload'].items() if k not in {'assistant_turn_id','input_id'}},write_guard=guard)
        if operation=='read_task_workspace':
            return self.workspace_facts(ws,task)
        if operation=='read_creator_memory':
            if generation:
                if self.creator_memory is None:
                    raise TaskBusinessRejected('当前成员偏好读取尚未装配',kind='unavailable')
                return self.creator_memory(ws,actor.user_id or actor.member_id)
            return sealed['memory']
        if operation=='read_assistant_script_context':
            return self.script_context(ws,task_id,actor.user_id or actor.member_id)
        if operation in {'prepare_device_resources','read_device_preparation','finalize_device_preparation'}:
            from pydantic import ValidationError
            from app.schemas import DevicePreparationRequest
            from app.device_resource_profile import DeviceProfileUnavailable
            from app.device_preparation_application import DevicePreparationConflict
            from app.device_scheduler import RecoveryBlocked
            try:
                if operation=='prepare_device_resources':
                    payload=DevicePreparationRequest.model_validate(args['payload'])
                    from app.creation.execution_control import task_execution_requirements
                    plan=self.production.plan_records.get_creation_plan(task.workspace_id,task.plan_id)
                    requirements=task_execution_requirements(task,plan)
                    if payload.kind in requirements['not_applicable_preparation_kinds']:
                        raise TaskOperationRejected(kind='conflict',detail={
                            'error':'device_preparation_not_applicable','stage':'device_preparation_preflight',
                            'task_id':task.id,'submission_effect':'not_submitted',
                            'reason':'当前是原视频剪辑任务，配音和故事板准备不适用；未提交设备准备或改变原任务。',
                            'repair_action':'读取当前任务的task_execution与实际执行能力，按next_required_stage和原作业回执选择下一步；不要准备不适用的种类。'})
                    shape=payload.shape.model_dump()
                    if payload.kind=='narration':
                        shape['task_id']=task.id
                    if payload.kind=='montage' and shape['task_id'] != task.id:
                        raise TaskBusinessRejected('故事板准备不能切换当前任务', kind='conflict')
                if self.device_preparation is None:
                    raise TaskBusinessRejected('设备准备业务用例尚未装配',kind='unavailable')
                if operation=='prepare_device_resources':
                    result=self.device_preparation('prepare',kind=payload.kind,shape=shape,request_id=str(payload.request_id),actor=actor)
                elif operation=='read_device_preparation':
                    result=self.device_preparation('status',request_id=args['request_id'],actor=actor)
                else:
                    result=self.device_preparation('finalize',request_id=args['request_id'],actor=actor)
            except ValidationError as exc:
                raise TaskOperationRejected(kind='invalid_input',detail=str(exc)) from exc
            except KeyError as exc:
                raise TaskOperationRejected(kind='not_found',detail='设备准备请求不存在') from exc
            except (DevicePreparationConflict,DeviceProfileUnavailable,RecoveryBlocked,ValueError) as exc:
                raise TaskOperationRejected(kind='conflict',detail=str(exc)) from exc
            except RuntimeError as exc:
                raise TaskOperationRejected(kind='unavailable',detail=str(exc)) from exc
            return {key:result[key] for key in ('id','plan_id','state','wait_reason','profile_published',
                'profile_id','revision','profile_digest','normal_work_started') if key in result}
        if operation=='read_script_sections':
            from app.creation.script_windows import SCRIPT_SECTION_WINDOW_LIMIT, ScriptWindowConflict
            if self.read_script_sections is None:
                raise TaskBusinessRejected('文案窗口读面尚未装配',kind='unavailable')
            try:
                return self.read_script_sections(task,args['script_id'],
                    after_ordinal=args.get('after_ordinal',0),limit=args.get('limit',SCRIPT_SECTION_WINDOW_LIMIT),
                    expected_updated_at=args.get('expected_updated_at'))
            except ScriptWindowConflict as exc:
                raise TaskOperationRejected(kind='conflict',detail=str(exc)) from exc
            except KeyError as exc:
                raise TaskOperationRejected(kind='not_found',detail='文案不存在') from exc
            except ValueError as exc:
                raise TaskOperationRejected(kind='invalid_input',detail=str(exc)) from exc
        if operation=='read_task_edit_capabilities':
            return self.edit_capabilities(task)
        if operation=='read_execution_capabilities':
            if self.execution_capabilities is None:
                raise TaskBusinessRejected('执行能力读面尚未装配',kind='unavailable')
            return self.execution_capabilities(task)
        if operation=='read_media_script_capabilities':
            return self.script_capabilities(task,actor.user_id or actor.member_id)
        if operation=='read_media_script_tool_capabilities':
            return self.tool_capabilities()
        if operation=='read_creation_execution_preview':
            return self.execution.preview(ws,task_id)
        if operation=='read_creation_execution':
            if generation:
                if args.get('job_id') is not None:
                    raise TaskBusinessRejected('生成动作只读取当前任务的制作摘要')
                if self.execution_status is None:
                    raise TaskBusinessRejected('任务制作摘要读取尚未装配',kind='unavailable')
                return self.execution_status(ws,task_id,actor=actor)
            return self.read_execution(goal,actor,job_id=args.get('job_id'))
        if operation in {'search_task_materials','read_task_material'}:
            allowed=self.authorizations.material_asset_ids(ws,task_id)
            if operation=='read_task_material':
                if args['asset_id'] not in allowed:
                    raise TaskBusinessRejected('素材不在原目标固定范围内')
                return self.material_facts(self.production.get_asset(ws,args['asset_id']))
            query=str(args.get('query','')).casefold()
            limit=int(args.get('limit',8))
            if not 1<=limit<=20:
                raise ValueError('素材搜索范围须为1至20')
            if args.get('include_eligibility'):
                purpose=args.get('purpose','manual')
                if purpose not in {'manual','automatic'}:
                    raise ValueError('镜头检索用途无效')
                return self.production.material_search.search_material_segments(ws,query=query,asset_ids=sorted(allowed),
                    usage_roles=['a_roll','b_roll'],limit=limit,viewer_user_id=actor.user_id or actor.member_id,
                    purpose=purpose,include_eligibility=True)
            assets=[asset for asset in self.production.list_assets(ws,viewer_user_id=actor.user_id or actor.member_id)
                    if asset.id in allowed and (not query or query in (asset.file_name+' '+asset.user_description+' '+' '.join(asset.user_tags)).casefold())]
            return {"assets":[self.material_facts(asset) for asset in assets[:limit]],"total_count":len(assets),"has_more":len(assets)>limit}
        if operation in {'start_script_window','patch_script_sections','confirm_saved_script','read_script_window_request'}:
            from app.schemas import (CreationTaskScriptWindowStartRequest, CreationTaskScriptSectionsPatchRequest,
                                     CreationTaskScriptConfirmSavedRequest)
            from app.creation.script_windows import ScriptWindowConflict
            from uuid import UUID
            case=self.script_case(actor)
            common={'member_id':actor.user_id or actor.member_id}
            if generation:
                common['generation_request_id']=origin['generation_request_id']
            try:
                if operation=='read_script_window_request':
                    if type(args.get('include_input',False)) is not bool:
                        raise ValueError('include_input须为布尔值')
                    return case.window_request_result(ws,task_id,str(UUID(args['request_id'])),operation=args['operation'],
                        script_id=args.get('script_id'),include_input=args.get('include_input',False),**common)
                common.update(instruction_origin=origin,reread_origin=lambda draft_only:self._adoption_origin(origin,draft_only),write_guard=guard)
                if operation=='start_script_window':
                    payload=CreationTaskScriptWindowStartRequest.model_validate(args['payload'])
                    if generation:
                        return case.start_generation_window(ws,task_id,payload,**common)
                    return case.start_window(ws,task_id,payload,**common)
                if operation=='patch_script_sections':
                    payload=CreationTaskScriptSectionsPatchRequest.model_validate(args['payload'])
                    return case.patch(ws,task_id,args['script_id'],payload,**common)
                self._adoption_origin(origin,False)
                payload=CreationTaskScriptConfirmSavedRequest.model_validate({**args['payload'],'adoption_kind':'delegated_system'})
                return case.confirm_saved(ws,task_id,args['script_id'],payload,**common)
            except ScriptWindowConflict as exc:
                raise TaskOperationRejected(kind='conflict',detail=str(exc)) from exc
            except KeyError as exc:
                raise TaskOperationRejected(kind='not_found',detail='文案或原请求不存在') from exc
            except ValueError as exc:
                raise TaskOperationRejected(kind='invalid_input',detail=str(exc)) from exc
        if operation=='save_assistant_script_draft':
            payload=CreationTaskScriptDraftRequest.model_validate(args['payload'])
            return self.script_case(actor).save(ws,task_id,payload,instruction_origin=origin,
                reread_origin=lambda draft_only:self._adoption_origin(origin,draft_only),write_guard=guard,
                source_binding={"turn_id":goal["current_turn_id"],"input_id":goal.get("source_input_id"),"input_turn_id":goal.get("source_input_turn_id")})
        if operation=='confirm_creation_task_script':
            payload=CreationTaskScriptConfirmRequest.model_validate(args['payload'])
            self._adoption_origin(origin,False)
            payload=payload.model_copy(update={'adoption_kind':'delegated_system'})
            adopted,_script=self.script_case(actor).confirm(ws,task_id,args['script_id'],payload,member_id=goal['member_id'],
                origin=origin,write_guard=guard)
            return self.script_facts(ws,adopted)
        if operation=='generate_creation_task_topics':
            return self.generate_topics(ws,task,guard)
        if operation=='confirm_creation_task_topic':
            payload=CreationTaskTopicConfirmRequest.model_validate(args['payload'])
            self._adoption_origin(origin,False)
            task,_=self.production.topic_records.confirm_topic_candidate(ws,task_id,args['candidate_id'],title=payload.title,
                adoption_kind='delegated_system',verified_owner_user_id=goal['member_id'],write_guard=guard)
            return self.topic_facts(ws,task)
        if operation in {'start_creation_execution','resume_creation_execution','cancel_creation_execution'}:
            return self.execute_creation(operation,args,actor,task,goal,origin,guard)
        if operation in {'read_task_quality_check','read_task_quality_continuation','continue_task_quality_check'}:
            from app.schemas import QualityContinuationRequest
            from app.device_scheduler import RecoveryBlocked
            from app.device_runtime import RuntimeUnavailable
            if self.quality is None:
                raise TaskBusinessRejected('原质检应用尚未装配',kind='unavailable')
            try:
                if operation=='read_task_quality_check':
                    return self.quality.read(ws,task_id,actor.user_id or actor.member_id,actor=actor)
                payload=QualityContinuationRequest.model_validate(args['payload'] if operation=='continue_task_quality_check'
                    else {key:value for key,value in args.items() if key!='task_id'})
                if operation=='read_task_quality_continuation':
                    return self.quality.read_continuation(ws,task_id,payload.job_id,payload.failed_request_id,actor)
                return self.quality.continue_failed(ws,task_id,payload.job_id,payload.failed_request_id,actor,
                    write_guard=guard)
            except RecoveryBlocked as exc:
                raise TaskBusinessRejected(str(exc),kind='conflict') from exc
            except RuntimeUnavailable as exc:
                raise TaskBusinessRejected(str(exc),kind='unavailable') from exc
        if operation=='read_task_observations':
            return self.observation_facts(task,actor)
        if operation=='observe_task_output':
            return self.observe_output(task,actor,args['payload'],origin,guard)
        if operation=='read_task_observation_request':
            return self.request_result(task,actor,args['request_id'],'observation')
        if operation=='read_task_media_job':
            return self.media_result(task,actor,args['job_id'])
        raise TaskBusinessRejected('该操作未被授权用于原目标后台接续')

    @staticmethod
    def _adoption_origin(origin,draft_only):
        if is_script_generation(origin) and not draft_only:
            raise TaskBusinessRejected('生成按钮不能升级为采用授权')
        if not draft_only:
            CreationIntentUseCase.reject_discussion_only_input(origin['raw_command'])
        return origin

    def execute_creation(self, operation,args,actor,task,goal,origin,guard):
        ws,task_id=task.workspace_id,task.id
        self._adoption_origin(origin,False)
        if operation=='cancel_creation_execution':
            sources=self.execution_sources(goal)
            if not sources or self.execution.execution.latest(ws,task_id).id!=sources[-1].job_id:
                raise TaskBusinessRejected('当前可停止作业不是原目标绑定的作业')
            runtime=self.runtime()
            return self.execution.cancel(ws,task_id,actor,runtime,verify_media=False,write_guard=guard)
        runtime=self.runtime()
        if args.get('assistant_turn_id') not in {None,goal.get('current_turn_id')}:
            raise ValueError('制作请求不能绑定其他助手回合')
        source={"workspace_id":ws,"task_id":task_id,"member_id":goal['member_id'],
            "assistant_turn_id":goal.get('current_turn_id'),"conversation_id":goal['conversation_id'],
            "expected_timeline_version_id":task.current_timeline_version_id,
            "expected_timeline_sequence":goal['target_snapshot'].get('timeline_sequence',0),
            "actor_identity":actor.identity_payload(),"action":"start" if operation.startswith('start_') else 'resume',
            **{key:origin[key] for key in ('input_id','input_turn_id') if key in origin}}
        try:
            if operation=='start_creation_execution':
                payload=ExecutionStartRequest.model_validate(args['payload'])
                if not payload.requirements_confirmed:
                    raise ValueError('制作要求尚未确认')
                result=self.execution.start(ws,task_id,payload.script_id,payload.input_digest,actor,runtime,
                    verify_media=False,write_guard=guard,execution_source=source)
            else:
                result=self.execution.resume(ws,task_id,actor,runtime,verify_media=False,write_guard=guard,execution_source=source)
            from fastapi.encoders import jsonable_encoder
            result=jsonable_encoder(result)
            recorded=self.conversations.record_task_execution(source['assistant_turn_id'],ws,goal['member_id'],task_id,result,
                current_source=True,input_id=source.get('input_id'))
            if not recorded:
                raise ValueError('原制作结果尚未回填到当前输入')
        except Exception as exc:
            # The original owner journal, not the transport call, decides if a
            # business submission exists. Never replay a committed job after a
            # failed conversation projection or queue acknowledgement.
            event_id=f"event_assistant_execution_locator_{source['assistant_turn_id']}"+(f"_{source['input_id']}" if source.get('input_id') else '')
            try:
                located=self.production.execution_records.get_task_event(ws,event_id)
            except KeyError:
                raise exc
            if located.payload['source']!=source:
                raise ValueError('原回合已绑定其他制作来源') from exc
            failure=TaskBusinessRejected('原制作作业已持久化，后续安排或回填未确认；请查询原作业',kind='conflict')
            failure.detail.update(submission_effect='unknown',task_id=task_id,job_id=located.job_id,
                input_digest=located.payload['input_digest'],query_tool='read_creation_execution',
                query_url=f'/api/creation-tasks/{task_id}/execution')
            raise failure from exc
        return result
