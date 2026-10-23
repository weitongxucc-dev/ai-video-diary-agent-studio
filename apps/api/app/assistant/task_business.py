"""Original-goal business calls. The host owns identity; tools never carry credentials."""
from __future__ import annotations

from dataclasses import asdict
import json
import time
import hashlib

from pydantic import ValidationError
from fastapi import HTTPException
from app.assistant.projection import _safe_business_failure
from app.creation.intent_use_case import CreationIntentUseCase
from app.creation.script_draft_use_case import ScriptDraftRejected
from app.creation.execution_control import ExecutionControlRejected
from app.creation.output_observation_use_case import OutputObservationRejected, read_original_observation_request
from app.creation_execution import CreationExecutionActor, ExecutionStartRequest
from app.schemas import CreationTaskScriptDraftRequest, CreationTaskScriptConfirmRequest, CreationTaskTopicConfirmRequest


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


class TaskBusinessRejected(PermissionError):
    def __init__(self, reason, *, status_code=403):
        self.status_code=status_code
        self.detail={"error":"task_goal_authority_unavailable","reason":reason}
        super().__init__(reason)


class TaskBusiness:
    """Finite operations on one persisted goal, with the same check at owner commit."""
    def __init__(self, production, conversations, authorizations, *, runtime, execution,
                 workspace_facts, script_context, script_case, script_facts, generate_topics, topic_facts,
                 edit_capabilities, script_capabilities, tool_capabilities, material_facts,
                 observation_facts, observe_output, request_result, media_result, record_adoption, segments=None,native_case=None,timeline_restores=None):
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

    def native_call(self, operation, arguments, *, call_id,tool_call_id,source_binding,cancel_event,deadline_monotonic):
        """Same task authorization, with the native owner as sole execution writer."""
        entered=False
        try:
            actor,task,goal,origin,_scope=self.check(source_binding,cancel_event,deadline_monotonic,check_version=False)
            if self.native_case is None: raise TaskBusinessRejected('原生执行用例尚未装配')
            native_origin={**origin,'task_id':task.id,'assistant_turn_id':source_binding['turn_id'],
                'input_id':origin.get('input_id') or source_binding['turn_id'],
                'session_id':source_binding['session_id'],'sdk_round':source_binding.get('sdk_round',0)}
            guard=lambda conn:self.check(source_binding,cancel_event,deadline_monotonic,connection=conn,check_version=False)
            entered=True
            value=self.native_case.dispatch(operation,arguments,actor=actor,origin=native_origin,
                write_guard=guard,runtime=self.runtime() if operation in {'submit_task_command','cancel_task_command','register_task_artifacts'} else None,
                call_id=call_id,cancel_event=cancel_event,
                deadline_monotonic=deadline_monotonic)
            from fastapi.encoders import jsonable_encoder
            return {'ok':True,'value':jsonable_encoder(value)}
        except (TaskBusinessRejected,HTTPException) as exc:
            return {'ok':False,'failure':{'status_code':exc.status_code,'reason':str(exc.detail),
                'submission_effect':'unknown' if entered else 'not_submitted'}}
        except (KeyError,ValueError,PermissionError) as exc:
            return {'ok':False,'failure':{'status_code':403 if isinstance(exc,PermissionError) else 409,
                'reason':str(exc),'submission_effect':'unknown' if entered else 'not_submitted'}}

    def original(self, goal):
        ws,task_id=goal['workspace_id'],goal['task_id']
        authorization=self.production.get_task_authorization(ws,task_id)
        if authorization is None:
            raise TaskBusinessRejected('原目标没有持久任务授权；须从可信采用入口核实')
        actor=CreationExecutionActor.from_event(ws,authorization.identity)
        if (actor.user_id or actor.member_id)!=goal['member_id']:
            raise TaskBusinessRejected('原目标成员与任务授权不一致')
        if self.authorizations.verdict(actor,task_id,'task_goal',check_scope=False)!='ready':
            raise TaskBusinessRejected('原任务授权已撤销、到期或目标已变化')
        scope=self.production.get_task_event(ws,self.authorizations.business_context_id(goal)).payload
        if scope['authorization_id']!=authorization.id or scope['identity']!=authorization.identity:
            raise TaskBusinessRejected('原目标封存范围与任务授权不一致')
        task=self.production.get_creation_task(ws,task_id)
        return actor,task,scope

    def check(self, binding, cancelled, deadline, *, connection=None, check_version=True):
        if cancelled.is_set() or time.monotonic()>=deadline:
            raise TaskBusinessRejected('本次原目标调用已停止或超过截止',status_code=409)
        ws,task_id=binding['workspace_id'],binding['task_id']
        with (self.production._connect() if connection is None else _Borrow(connection)) as conn:
            origin=self.conversations._task_turn_source(conn,binding['turn_id'],ws,binding['member_id'],task_id)
            root=self.conversations._goal_root(conn,binding['turn_id'])
            if (root is None or str(root['goal'].get('revision',0))!=str(binding['goal_revision'])
                    or root['goal'].get('source_input_id')!=binding.get('input_id')
                    or origin.get('input_id')!=binding.get('input_id')
                    or origin['conversation_id']!=binding['conversation_id']
                    or not self.conversations._goal_origin_current(conn,root)):
                raise TaskBusinessRejected('原目标修订或已消费输入已变化',status_code=409)
            authorization=self.production.get_task_authorization(ws,task_id,connection=conn,metadata_only=True)
            if authorization is None:
                raise TaskBusinessRejected('原任务授权不存在')
            actor=CreationExecutionActor.from_event(ws,authorization.identity)
            if ((actor.user_id or actor.member_id)!=binding['member_id']
                    or self.authorizations.verdict(actor,task_id,'task_goal',connection=conn,check_scope=False)!='ready'):
                raise TaskBusinessRejected('原任务授权已到期或撤销')
            task=self.production.get_creation_task(ws,task_id,connection=conn)
            version=conn.execute('SELECT sequence FROM timeline_versions WHERE workspace_id=? AND task_id=? AND id=?',
                (ws,task_id,task.current_timeline_version_id)).fetchone() if task.current_timeline_version_id else None
            snapshot=binding.get('task_version')
            if check_version and snapshot is not None and (snapshot.get('timeline_version_id'),snapshot.get('timeline_sequence',0)) != (task.current_timeline_version_id,version[0] if version else 0):
                raise TaskBusinessRejected('原目标的预期作品版本已变化',status_code=409)
            sealed=self.production.get_task_event(ws,self.authorizations.business_context_id(root['goal']),connection=conn).payload
            if sealed['authorization_id']!=authorization.id or sealed['identity']!=authorization.identity:
                raise TaskBusinessRejected('原目标封存范围不匹配')
            row=conn.execute('SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?',(ws,task.plan_id)).fetchone()
            plan=json.loads(row[0])
            if (sealed['knowledge']!=plan['knowledge_references'] or sealed['sources']!=plan['source_references']):
                raise TaskBusinessRejected('原目标引用范围已变化，未读取新增私人资料',status_code=409)
            if self.production.task_source_revisions(ws,[item['id'] for item in sealed['knowledge']],
                    [item['id'] for item in sealed['sources']],connection=conn)!=sealed['source_revisions']:
                raise TaskBusinessRejected('原目标固定资料或本地读取权限已变化',status_code=409)
        return actor,task,root['goal'],origin,sealed

    def read_execution(self, goal, actor, *, job_id=None):
        ws,task_id=goal['workspace_id'],goal['task_id']
        located=self.execution_sources(goal)
        if job_id is None:
            if not located:
                return {"task_id":task_id,"job":None,"stages":[],"video_ready":False,"waiting_reason":"原目标尚未提交制作"}
            job_id=located[-1].job_id
        if not any(event.job_id==job_id for event in located):
            raise TaskBusinessRejected('制作作业不属于原目标来源')
        return self.execution.read_bound(ws,task_id,job_id,actor,verify_media=False)

    def execution_sources(self,goal):
        from app.assistant_goal_continuation import GoalResultProjection
        projection=GoalResultProjection(self.production,self.conversations)
        return [event for event in self.production.list_task_events(goal['workspace_id'],goal['task_id'])
            if event.event_type=='assistant_execution_located' and projection.source_belongs_to_goal(goal,event.payload['source'])]

    def __call__(self, operation, arguments, *, call_id, tool_call_id, source_binding, cancel_event, deadline_monotonic):
        entered=False
        try:
            binding=dict(source_binding)
            reconcile_original=(operation in {'read_timeline_history','read_timeline_restore_request','read_task_observations','read_task_observation_request','read_task_media_job','read_creation_execution','read_narration_revision'}
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
                    return {'ok':True,'value':previous}
            declared=arguments.get('assistant_turn_id') or (arguments.get('payload') or {}).get('assistant_turn_id')
            if declared is not None and declared!=binding['turn_id']:
                raise TaskBusinessRejected('工具参数不能切换原回合')
            if operation in {'record_input_understanding','record_narration_semantic_review','revise_narration_segment'}:
                if arguments['payload'].get('input_id')!=(origin.get('input_id') or binding['turn_id']):
                    raise TaskBusinessRejected('局部修改不能切换原输入')
            if operation in {'save_assistant_script_draft','confirm_creation_task_script','confirm_creation_task_topic','observe_task_output'}:
                arguments={**arguments,'payload':{**arguments['payload'],'assistant_turn_id':binding['turn_id']}}
            guard=lambda conn:self.check(binding,cancel_event,deadline_monotonic,connection=conn,check_version=check_version)
            entered=True
            value=self.execute(operation,arguments,actor,task,goal,origin,sealed,guard)
            from fastapi.encoders import jsonable_encoder
            value=jsonable_encoder(value)
            return {"ok":True,"value":value}
        except (TaskBusinessRejected,ScriptDraftRejected,ExecutionControlRejected,OutputObservationRejected,HTTPException) as exc:
            failure={"status_code":exc.status_code,**(_safe_business_failure(exc.detail) if isinstance(exc.detail,dict) else {"reason":str(exc.detail)})}
            if not entered and 'submission_effect' not in failure:
                failure['submission_effect']='not_submitted'
            return {"ok":False,"failure":failure}
        except (KeyError,ValueError,PermissionError,ValidationError) as exc:
            return {"ok":False,"failure":{"status_code":403 if isinstance(exc,PermissionError) else 409,
                "reason":str(exc)}}

    def input_delivery(self, source_binding):
        from app.assistant.input_delivery import AssistantInputDelivery
        binding=dict(source_binding)
        def consume(item, *, cancel_event, deadline_monotonic):
            def validate(conn, current, old_goal):
                actor,task,_goal,_origin,sealed=self.check(binding,cancel_event,deadline_monotonic,connection=conn,check_version=False)
                return self._input_context(conn,current['request_context'],task,actor,old_goal,sealed)
            def seal(conn, old_goal, goal, context):
                if cancel_event.is_set() or time.monotonic()>=deadline_monotonic:
                    raise TaskBusinessRejected('追加输入消费已停止或超过截止',status_code=409)
                return self.authorizations.inherit_business_context(old_goal,goal,context,connection=conn)
            consumed=self.conversations.consume_task_goal_input(binding['conversation_id'],binding['workspace_id'],
                binding['member_id'],item['input_id'],expected_binding=binding,validate=validate,seal=seal)
            goal,context=consumed['goal'],consumed['context'];snapshot=context['target_snapshot']
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
        from app.production import content_readable
        from datetime import datetime
        if frozen.get('kind')!='task' or frozen.get('task_id')!=task.id:
            raise TaskBusinessRejected('追加输入切换了任务，需要从交互入口重新授权')
        snapshot=frozen['target_snapshot']
        timeline=(self.production.get_timeline_version(task.workspace_id,task.current_timeline_version_id,connection=conn)
            if task.current_timeline_version_id else None)
        version={'timeline_version_id':task.current_timeline_version_id,'timeline_sequence':timeline.sequence if timeline else 0}
        if any(snapshot.get(key)!=value for key,value in version.items()):
            raise ValueError('追加输入接收后作品版本已变化，请查看当前作品后重新发送')
        reference=normalize_selection(frozen.get('selected_reference'))
        items=reference['items'] if reference and reference['kind']=='selection' else [reference] if reference else []
        tracks=self.production.list_subtitle_tracks(task.workspace_id,task.id,connection=conn)
        authorization=self.production.get_task_authorization(task.workspace_id,task.id,connection=conn)
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

    def finish(self, claim, reply):
        """Project real host receipts; a model's proposed result is never adopted here."""
        goal=self.conversations.get_task_goal(claim['turn_id'])
        if (goal is None or goal['current_turn_id']!=claim['turn_id'] or goal['status']!='running'
                or any(goal[key]!=claim['goal'][key] for key in ('original_turn_id','task_id','workspace_id','member_id','conversation_id'))):
            raise TaskBusinessRejected('后台回合已不属于当前目标',status_code=409)
        claim.update(goal=goal,request_context=goal['request_context'])
        ws,task_id=goal['workspace_id'],goal['task_id']
        actor,task,_sealed=self.original(goal)
        events=self.production.list_task_events(ws,task_id)
        response={'reply':reply,'turn_id':claim['turn_id'],'draft_version':1}
        proofs=[]
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
        if drafts:
            response['script_draft']=asdict(self.production.get_creation_task_script(ws,drafts[-1].payload['script_id']))
        jobs=[event for event in events if event.event_type=='task_media_submitted'
            and event.payload['arguments'].get('_typed_source',{}).get('assistant_turn_id')==claim['turn_id']
            and event.payload['arguments'].get('_typed_source',{}).get('input_id')==goal.get('source_input_id')]
        if jobs:
            response['media_job']=self.media_result(task,actor,jobs[-1].job_id)
            proofs.append({'kind':'media_job','id':jobs[-1].job_id,
                **({'input_id':goal['source_input_id']} if goal.get('source_input_id') else {})})
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
        if operation in {'read_timeline_history','read_timeline_restore_request','restore_timeline'}:
            if self.timeline_restores is None: raise TaskBusinessRejected('原修改恢复用例尚未装配')
            if operation=='read_timeline_history': return self.timeline_restores.history(ws,task_id,actor,
                limit=args.get('limit',20),before_sequence=args.get('before_sequence'))
            if operation=='read_timeline_restore_request': return self.timeline_restores.read_request(ws,task_id,args['request_id'],actor)
            from app.creation.editing.timeline_restore_use_case import TimelineRestoreCommand
            from app.schemas import CreationTaskTimelineRestoreRequest
            payload=CreationTaskTimelineRestoreRequest.model_validate(args['payload'])
            if args['action'] not in {'undo','redo'}: raise TaskBusinessRejected('恢复动作不可用')
            result=self.timeline_restores.execute(TimelineRestoreCommand(ws,task,args['action']=='redo'),actor=actor,
                request_id=payload.request_id,target_operation_id=payload.target_operation_id,
                expected_timeline_version_id=payload.expected_timeline_version_id,
                expected_timeline_sequence=payload.expected_timeline_sequence,write_guard=guard,
                source={'assistant_turn_id':goal['current_turn_id'],'input_id':origin.get('input_id') or goal['current_turn_id']})
            return {'operation':result.operation,'montage':result.montage,'narration':result.narration,
                'restore_request':result.restore_request,'execution_readiness':result.execution_readiness}
        if operation in {'read_narration_segment_context','read_narration_segment_sources','read_narration_segments','read_narration_revision','revise_narration_segment','resume_narration_revision'}:
            if self.segments is None: raise TaskBusinessRejected('局部修改业务用例尚未装配')
            if operation=='read_narration_segment_context': return self.segments.context(ws,task_id,args['segment_id'],actor)
            if operation=='read_narration_segment_sources': return self.segments.sources(ws,task_id,args['segment_id'],actor,
                offset=args.get('offset',0),limit=args.get('limit',8),text_offset=args.get('text_offset',0),max_chars=args.get('max_chars',3000))
            if operation=='read_narration_segments': return self.segments.list_segments(ws,task_id,actor,
                offset=args.get('offset',0),limit=args.get('limit',24))
            if operation=='read_narration_revision': return self.segments.read_request(ws,task_id,args['request_id'],actor)
            if operation=='resume_narration_revision': return self.segments.resume_request(ws,task_id,args['request_id'],actor,
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
        if operation=='read_task_workspace': return self.workspace_facts(ws,task)
        if operation=='read_creator_memory': return sealed['memory']
        if operation=='read_assistant_script_context': return self.script_context(ws,task_id,actor.user_id or actor.member_id)
        if operation=='read_task_edit_capabilities': return self.edit_capabilities(task)
        if operation=='read_media_script_capabilities': return self.script_capabilities(task,actor.user_id or actor.member_id)
        if operation=='read_media_script_tool_capabilities': return self.tool_capabilities()
        if operation=='read_creation_execution_preview': return self.execution.preview(ws,task_id)
        if operation=='read_creation_execution': return self.read_execution(goal,actor,job_id=args.get('job_id'))
        if operation in {'search_task_materials','read_task_material'}:
            allowed=self.authorizations.material_asset_ids(ws,task_id)
            if operation=='read_task_material':
                if args['asset_id'] not in allowed: raise TaskBusinessRejected('素材不在原目标固定范围内')
                return self.material_facts(self.production.get_asset(ws,args['asset_id']))
            query=str(args.get('query','')).casefold();limit=int(args.get('limit',8))
            if not 1<=limit<=20: raise ValueError('素材搜索范围须为1至20')
            if args.get('include_eligibility'):
                purpose=args.get('purpose','manual')
                if purpose not in {'manual','automatic'}: raise ValueError('镜头检索用途无效')
                return self.production.search_material_segments(ws,query=query,asset_ids=sorted(allowed),
                    usage_roles=['a_roll','b_roll'],limit=limit,viewer_user_id=actor.user_id or actor.member_id,
                    purpose=purpose,include_eligibility=True)
            assets=[asset for asset in self.production.list_assets(ws,viewer_user_id=actor.user_id or actor.member_id)
                    if asset.id in allowed and (not query or query in (asset.file_name+' '+asset.user_description+' '+' '.join(asset.user_tags)).casefold())]
            return {"assets":[self.material_facts(asset) for asset in assets[:limit]],"total_count":len(assets),"has_more":len(assets)>limit}
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
        if operation=='generate_creation_task_topics': return self.generate_topics(ws,task,guard)
        if operation=='confirm_creation_task_topic':
            payload=CreationTaskTopicConfirmRequest.model_validate(args['payload']);self._adoption_origin(origin,False)
            task,_=self.production.confirm_topic_candidate(ws,task_id,args['candidate_id'],title=payload.title,
                adoption_kind='delegated_system',verified_owner_user_id=goal['member_id'],write_guard=guard)
            return self.topic_facts(ws,task)
        if operation in {'start_creation_execution','resume_creation_execution','cancel_creation_execution'}:
            return self.execute_creation(operation,args,actor,task,goal,origin,guard)
        if operation=='read_task_observations': return self.observation_facts(task,actor)
        if operation=='observe_task_output': return self.observe_output(task,actor,args['payload'],origin,guard)
        if operation=='read_task_observation_request': return self.request_result(task,actor,args['request_id'],'observation')
        if operation=='read_task_media_job': return self.media_result(task,actor,args['job_id'])
        raise TaskBusinessRejected('该操作未被授权用于原目标后台接续')

    @staticmethod
    def _adoption_origin(origin,draft_only):
        if not draft_only: CreationIntentUseCase.reject_discussion_only_input(origin['raw_command'])
        return origin

    def execute_creation(self, operation,args,actor,task,goal,origin,guard):
        ws,task_id=task.workspace_id,task.id
        self._adoption_origin(origin,False)
        runtime=self.runtime()
        if operation=='cancel_creation_execution':
            sources=self.execution_sources(goal)
            if not sources or self.execution.execution.latest(ws,task_id).id!=sources[-1].job_id:
                raise TaskBusinessRejected('当前可停止作业不是原目标绑定的作业')
            return self.execution.cancel(ws,task_id,actor,runtime,verify_media=False,write_guard=guard)
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
                if not payload.requirements_confirmed: raise ValueError('制作要求尚未确认')
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
                located=self.production.get_task_event(ws,event_id)
            except KeyError:
                raise exc
            if located.payload['source']!=source:
                raise ValueError('原回合已绑定其他制作来源') from exc
            failure=TaskBusinessRejected('原制作作业已持久化，后续安排或回填未确认；请查询原作业',status_code=409)
            failure.detail.update(submission_effect='unknown',task_id=task_id,job_id=located.job_id,
                input_digest=located.payload['input_digest'],query_tool='read_creation_execution',
                query_url=f'/api/creation-tasks/{task_id}/execution')
            raise failure from exc
        return result


from contextlib import nullcontext as _Borrow
