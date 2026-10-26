import pytest
from app.conversation_repository import ConversationRepository
from app.assistant.inputs import AssistantInputConflict


def test_section_business_defers_to_original_dependencies_after_unrelated_head_changes():
    from types import SimpleNamespace
    from threading import Event
    from app.assistant.task_business import TaskBusiness, TaskBusinessRejected
    from app.assistant.task_business_adapter import TaskBusinessAdapter
    case = object.__new__(TaskBusiness)
    binding = {'turn_id':'turn', 'input_id':'input', 'task_version':{'timeline_version_id':'read-version'}}
    original = {'input_id':'input', 'base_version_id':'read-version', 'dependency_digest':'read-target-digest'}
    revoked, target_changed = False, False
    def authorize(source, cancelled, deadline, *, connection=None, check_version=True):
        if revoked:
            raise TaskBusinessRejected('原授权已撤销')
        if check_version:
            raise TaskBusinessRejected('整片HEAD已由另一段发布推进', kind='conflict')
        return None, SimpleNamespace(id='task'), None, {'input_id':'input'}, None
    def owner(operation, args, actor, task, goal, origin, sealed, guard):
        guard(None)
        assert args['payload'] == original  # Preserve the actual read basis.
        if target_changed:
            raise ValueError('目标真实依赖已变化')
        return {'request_id':'original-request', 'status':'queued'}
    case.check, case.execute = authorize, owner
    def invoke(operation):
        return TaskBusinessAdapter(case)(operation, {'payload':dict(original)}, call_id='call', tool_call_id='tool',
            source_binding=binding, cancel_event=Event(), deadline_monotonic=float('inf'))
    assert invoke('revise_narration_segment')['value']['request_id'] == 'original-request'
    assert not invoke('save_assistant_script_draft')['ok']
    target_changed = True
    assert not invoke('revise_narration_segment')['ok']
    revoked = True
    assert not invoke('revise_narration_segment')['ok']


def test_input_receipt_consumes_draft_once_keeps_original_target_and_can_be_withdrawn(tmp_path):
    repo = ConversationRepository(tmp_path / "inputs.sqlite")
    chat = repo.create("w", "m", "home", "home")
    repo.save_draft(chat["id"], "w", "m", "第一轮要求", 0)
    turn, _ = repo.create_turn_idempotent(chat["id"], "w", "m", "第一轮要求", "first", 1,
                                         {"kind": "task", "task_id": "t"})
    repo.record_task_execution(turn["id"],"w","m","t",{"task_id":"t","job":{"id":"existing-job","status":"queued"}})
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_result"
    repo.save_draft(chat["id"], "w", "m", "改成第二种字幕", 2)
    values = dict(target_turn_id=turn["id"], client_request_id="second", message="改成第二种字幕",
                  mode="steer", expected_draft_version=3, expected_reference_version=0,
                  request_context={"kind": "task", "task_id": "t", "selected_reference": None})
    receipt, created = repo.enqueue_input(chat["id"], "w", "m", **values)
    assert created and receipt["status"] == "accepted"
    assert repo.get(chat["id"], "w", "m")["draft_version"] == 4
    assert repo.enqueue_input(chat["id"], "w", "m", **values) == (receipt, False)
    with pytest.raises(AssistantInputConflict):
        repo.enqueue_input(chat["id"], "w", "m", **{**values, "message": "不同要求"})
    assert repo.task_turn_source(turn["id"], "w", "m", "t")["raw_command"] == "第一轮要求"
    repo.transition_input(chat["id"], "w", "m", "second", "consumed")
    assert repo.get_task_goal(turn["id"])["status"] == "waiting_result"
    assert repo.get_task_goal(turn["id"])["original_instruction"] == "第一轮要求"
    source = repo.task_turn_source(turn["id"], "w", "m", "t")
    assert source["raw_command"] == "改成第二种字幕" and source["input_id"] == "second"
    assert repo.business_turn_context(turn["id"], "w", "m", "t")["context"] == values["request_context"]
    assert repo.consumed_turn_request_context(turn["id"], chat["id"], "w", "m") == values["request_context"]
    with pytest.raises(AssistantInputConflict):
        repo.transition_input(chat["id"], "w", "m", "second", "withdrawn")
    repo.transition_input(chat["id"], "w", "m", "second", "completed")
    assert ConversationRepository(repo.database_path).list_inputs(chat["id"], "w", "m")[0]["status"] == "completed"
    assert repo.list_inputs(chat["id"], "w", "other") == []
    repo.save_draft(chat["id"], "w", "m", "第三种字幕", 4)
    next_context={"kind":"task", "task_id":"t", "selected_reference":None, "selected_object_refs":["cue:third"]}
    repo.enqueue_input(chat["id"], "w", "m", **{**values, "client_request_id":"third", "message":"第三种字幕",
                       "expected_draft_version":5, "request_context":next_context})
    assert repo.consumed_turn_request_context(turn["id"], chat["id"], "w", "m") == values["request_context"]
    repo.transition_input(chat["id"], "w", "m", "third", "consumed")
    assert repo.business_turn_context(turn["id"], "w", "m", "t")["context"] == next_context
    restarted=ConversationRepository(repo.database_path)
    assert restarted.fail_interrupted_turns() == 1
    assert restarted.list_inputs(chat["id"], "w", "m")[-1]["status"] == "interrupted"


def test_withdrawn_input_does_not_change_tool_authorization(tmp_path):
    repo = ConversationRepository(tmp_path / "withdrawn.sqlite")
    chat = repo.create("w", "m", "home", "home")
    repo.save_draft(chat["id"], "w", "m", "第一轮要求", 0)
    turn, _ = repo.create_turn_idempotent(chat["id"], "w", "m", "第一轮要求", "first", 1, {"kind": "task", "task_id": "t"})
    repo.save_draft(chat["id"], "w", "m", "完成后再改", 2)
    repo.enqueue_input(chat["id"], "w", "m", target_turn_id=turn["id"], client_request_id="later", message="完成后再改",
                       mode="follow_up", expected_draft_version=3, expected_reference_version=0,
                       request_context={"kind": "task", "task_id": "t", "selected_reference": None})
    repo.transition_input(chat["id"], "w", "m", "later", "withdrawn")
    with pytest.raises(AssistantInputConflict):
        repo.transition_input(chat["id"], "w", "m", "later", "consumed")
    assert repo.task_turn_source(turn["id"], "w", "m", "t")["raw_command"] == "第一轮要求"


def test_pending_input_survives_finished_turn_and_query_preserves_output_checkpoint(tmp_path):
    repo = ConversationRepository(tmp_path / "waiting.sqlite")
    chat = repo.create("w", "m", "task", "t")
    repo.save_draft(chat["id"], "w", "m", "制作视频", 0)
    turn, _ = repo.create_turn_idempotent(chat["id"], "w", "m", "制作视频", "first", 1,
        {"kind":"task","task_id":"t"})
    repo.record_task_execution(turn["id"],"w","m","t",{"task_id":"t","job":{"id":"job","status":"queued"}})
    repo.record_goal_output_checkpoint(turn["id"], {"timeline_version_id":"v1"})
    repo.save_draft(chat["id"], "w", "m", "现在做到哪里了", 2)
    repo.enqueue_input(chat["id"], "w", "m", target_turn_id=turn["id"], client_request_id="query",
        message="现在做到哪里了", mode="steer", expected_draft_version=3, expected_reference_version=0,
        request_context={"kind":"task","task_id":"t","selected_reference":None})
    repo.transition_input(chat["id"], "w", "m", "query", "consumed")
    repo.record_input_understanding(chat["id"],"w","m",turn["id"],"query",kind="query",
        summary="只读取当前制作进度",target_refs=[])
    repo.transition_input(chat["id"], "w", "m", "query", "completed")
    repo.save_draft(chat["id"], "w", "m", "第1段紧凑一点", 4)
    repo.enqueue_input(chat["id"], "w", "m", target_turn_id=turn["id"], client_request_id="later",
        message="第1段紧凑一点", mode="follow_up", expected_draft_version=5, expected_reference_version=0,
        request_context={"kind":"task","task_id":"t","selected_reference":None})
    repo.finish_turn(turn["id"], "completed")
    restarted=ConversationRepository(repo.database_path)
    receipts=restarted.list_inputs(chat["id"],"w","m")
    assert receipts[-1]["status"]=="accepted" and receipts[-1]["reason"]=="waiting_agent"
    goal=restarted.get_task_goal(turn["id"])
    assert goal["original_instruction"]=="制作视频"
    assert goal["completed_output_checkpoint"]=={"timeline_version_id":"v1"}
    assert restarted.transition_input(chat["id"],"w","m","later","withdrawn")["status"]=="withdrawn"


def test_finished_turn_input_claim_is_once_only_and_does_not_require_a_new_media_job(tmp_path):
    repo = ConversationRepository(tmp_path / 'queued.sqlite')
    chat=repo.create('w','m','task','t')
    repo.save_draft(chat['id'],'w','m','制作视频',0)
    context={'kind':'task','task_id':'t','selected_reference':None,
        'target_snapshot':{'timeline_version_id':'v1','timeline_sequence':1}}
    turn,_=repo.create_turn_idempotent(chat['id'],'w','m','制作视频','first',1,context)
    repo.record_task_execution(turn['id'],'w','m','t',{'task_id':'t','job':{'id':'original','status':'queued'}})
    repo.finish_turn(turn['id'],'completed')
    repo.save_draft(chat['id'],'w','m','只查进度',2)
    repo.enqueue_input(chat['id'],'w','m',target_turn_id=turn['id'],client_request_id='query',message='只查进度',
        mode='steer',expected_draft_version=3,expected_reference_version=0,request_context=context)
    candidate=repo.pending_input_candidates()[0]
    seals=[]
    claim=repo.claim_pending_goal_input(candidate,validate=lambda conn,item,goal:item['request_context'],
        seal=lambda conn,old,new,ctx:seals.append(new['source_input_id']))
    assert claim['event']['phase']=='input' and claim['event']['message']=='只查进度'
    assert seals==['query'] and repo.pending_input_candidates()==[]
    assert repo.claim_pending_goal_input(candidate,validate=lambda *args:None,seal=lambda *args:None) is None
    assert repo.task_turn_source(claim['turn_id'],'w','m','t')['raw_command']=='只查进度'
    repo.record_input_understanding(chat['id'],'w','m',claim['turn_id'],'query',kind='query',summary='只读取进度',target_refs=[])
    repo.finish_turn(claim['turn_id'],'completed')
    receipt=repo.list_inputs(chat['id'],'w','m')[0]
    assert receipt['target_turn_id']==turn['id'] and receipt['delivery_turn_id']==claim['turn_id']
    assert receipt['status']=='completed' and receipt['understanding']['kind']=='query'
    assert repo.get_task_goal(claim['turn_id'])['original_instruction']=='制作视频'
    assert repo.get_task_goal(claim['turn_id'])['retained_business_sources']==[{'input_id':None,'input_turn_id':None}]


@pytest.mark.parametrize('kind,mode,changed',[('candidate','candidate',False),('effective_modification','apply',True)])
def test_only_committed_effect_changes_latest_requirement_checkpoint(tmp_path,kind,mode,changed):
    repo=ConversationRepository(tmp_path/'effects.sqlite')
    chat=repo.create('w','m','task','t')
    repo.save_draft(chat['id'],'w','m','制作原视频',0)
    turn,_=repo.create_turn_idempotent(chat['id'],'w','m','制作原视频','first',1,{'kind':'task','task_id':'t'})
    repo.record_task_execution(turn['id'],'w','m','t',{'task_id':'t','job':{'id':'job','status':'queued'}})
    repo.record_goal_output_checkpoint(turn['id'],{'timeline_version_id':'v1'})
    repo.save_draft(chat['id'],'w','m','第1段紧凑一点',2)
    repo.enqueue_input(chat['id'],'w','m',target_turn_id=turn['id'],client_request_id='edit',message='第1段紧凑一点',
        mode='steer',expected_draft_version=3,expected_reference_version=0,request_context={'kind':'task','task_id':'t','selected_reference':None})
    repo.transition_input(chat['id'],'w','m','edit','consumed')
    repo.record_input_understanding(chat['id'],'w','m',turn['id'],'edit',kind=kind,summary='按当前原文处理本段',target_refs=['section:first'])
    with pytest.raises(RuntimeError):
        with repo._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            repo.record_input_business_effect(turn['id'],'w','m',input_id='edit',request_id='revision',status='queued',adoption_mode=mode,connection=conn)
            raise RuntimeError('owner commit rejected')
    assert repo.get_task_goal(turn['id'])['completed_output_checkpoint']=={'timeline_version_id':'v1'}
    assert repo.list_inputs(chat['id'],'w','m')[0]['business_effect'] is None
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        repo.record_input_business_effect(turn['id'],'w','m',input_id='edit',request_id='revision',status='queued',adoption_mode=mode,connection=conn)
    assert ('completed_output_checkpoint' not in repo.get_task_goal(turn['id'])) is changed
    assert repo.list_inputs(chat['id'],'w','m')[0]['business_effect']=={'request_id':'revision','status':'queued'}


@pytest.mark.parametrize('submission_state',['not_submitted','submitted','result_unknown'])
def test_restart_reconciles_original_input_request_without_replaying_it(tmp_path,submission_state):
    from types import SimpleNamespace
    from app.assistant_goal_continuation import GoalContinuationService
    from app.assistant.task_business import TaskBusiness
    repo=ConversationRepository(tmp_path/'input-recovery.sqlite')
    chat=repo.create('w','m','task','t')
    repo.save_draft(chat['id'],'w','m','制作视频',0)
    context={'kind':'task','task_id':'t','selected_reference':None,
        'target_snapshot':{'timeline_version_id':'v1','timeline_sequence':1}}
    turn,_=repo.create_turn_idempotent(chat['id'],'w','m','制作视频','first',1,context)
    repo.record_task_execution(turn['id'],'w','m','t',{'task_id':'t','job':{'id':'original','status':'queued'}})
    repo.finish_turn(turn['id'],'completed')
    repo.save_draft(chat['id'],'w','m','第1段紧凑一点',2)
    repo.enqueue_input(chat['id'],'w','m',target_turn_id=turn['id'],client_request_id='edit',message='第1段紧凑一点',
        mode='steer',expected_draft_version=3,expected_reference_version=0,request_context=context)
    claim=repo.claim_pending_goal_input(repo.pending_input_candidates()[0],
        validate=lambda conn,item,goal:item['request_context'],seal=lambda *args:None)
    repo.record_input_understanding(chat['id'],'w','m',claim['turn_id'],'edit',
        kind='effective_modification',summary='按真实原文改写本段',target_refs=['section:first'])
    if submission_state!='not_submitted':
        repo.record_input_submission(claim['turn_id'],'w','m',input_id='edit',request_id='original-revision',section_id='first')
    if submission_state=='submitted':
        with repo._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            repo.record_input_business_effect(claim['turn_id'],'w','m',input_id='edit',
                request_id='original-revision',status='queued',adoption_mode='apply',connection=conn)
    restarted=ConversationRepository(repo.database_path)
    assert restarted.fail_interrupted_turns()==1
    restarted.settle_input_round(claim)
    item=restarted.list_inputs(chat['id'],'w','m')[0]
    if submission_state=='not_submitted':
        assert item['status']=='interrupted' and restarted.pending_input_candidates()==[]
        return
    assert item['status']=='consumed' and item['reason']=='read_original_input_request'
    with restarted._connect() as conn:
        assert restarted._goal_origin_current(conn,restarted._goal_root(conn,claim['turn_id']))
    reads=[]; observed={'available':submission_state!='result_unknown','status':'queued'}
    def read_request(ws,task,request_id,actor):
        reads.append(request_id)
        if not observed['available']: raise KeyError(request_id)
        return {'task_id':task,'request_id':request_id,'input_id':'edit','section_id':'first',
            'job_id':'original-revision-job','status':observed['status']}
    bound=SimpleNamespace(original=lambda goal:(object(),SimpleNamespace(workspace_id='w',id='t'),{}),
        segments=SimpleNamespace(read_request=read_request))
    business=SimpleNamespace(read_input_submission=lambda candidate:TaskBusiness.read_input_submission(bound,candidate))
    def no_model(*args): raise AssertionError('must query the original request instead of replaying input')
    service=GoalContinuationService(restarted,verify_current_goal=lambda *_:{'status':'ready','business':business},run_goal_turn=no_model)
    service.reconcile()
    assert reads==['original-revision']
    assert len(restarted.list_turns(chat['id']))==2
    assert restarted.list_inputs(chat['id'],'w','m')[0]['status']=='consumed'
    if submission_state=='submitted':
        assert restarted.get_task_goal(turn['id'])['status']=='waiting_result'
    else:
        assert restarted.get_task_goal(turn['id'])['status']=='waiting_unknown'
    observed.update(available=True,status='succeeded')
    candidate=restarted.pending_input_candidates()[0]
    assert candidate['reconcile_only']
    actual=business.read_input_submission(candidate)
    assert restarted.reconcile_input_submission(candidate,actual)
    assert reads==['original-revision','original-revision']
    assert len(restarted.list_turns(chat['id']))==2
    assert restarted.list_inputs(chat['id'],'w','m')[0]['status']=='completed'
    assert restarted.pending_input_candidates()==[]
    result=restarted.get_turn(claim['turn_id'])['events'][-1]
    assert result['phase']=='segment_revision' and result['job_id']=='original-revision-job'
    assert result['status']=='succeeded' and result['request_id']=='original-revision'
