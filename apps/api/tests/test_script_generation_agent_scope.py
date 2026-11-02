"""The button's typed action can plan its own draft, never adopt or execute."""
from contextlib import nullcontext
from copy import deepcopy
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_script_generation_intent import generation  # noqa: F401
from test_creation_task_script_grounding import script_domain_state  # noqa: F401

from app.assistant import task_business as domain
from app.assistant.script_generation_policy import SCRIPT_GENERATION_TOOLS
from app.assistant.task_business_adapter import TaskBusinessAdapter
from app.assistant.task_business_errors import TaskBusinessRejected
import app.assistant_pi as pi
from app.creation_execution import CreationExecutionActor


def forbidden(*args, **kwargs):
    raise AssertionError('generation must not use a delegated goal, legacy start or native owner')


def fixture():
    actor=CreationExecutionActor('workspace','member')
    request,conversation,turn=str(uuid4()),str(uuid4()),str(uuid4())
    binding={'workspace_id':'workspace','member_id':'member','task_id':'task',
        'conversation_id':conversation,'turn_id':turn,'intent_kind':'script_generation','generation_request_id':request}
    source={'kind':'user_action','request_id':request,'member_id':'member',
        'conversation_id':conversation,'assistant_turn_id':turn}
    facts={'request_id':request,'task_id':'task','conversation_id':conversation,'turn_id':turn,
        'actor':actor.identity_payload(),'source':source,'run_id':'original-run','run_status':'running',
        'script_id':'script','script':{'id':'script'},'requested_effect':'draft','can_continue':True}
    state=SimpleNamespace(revoked=False,connections=[],facts=facts,ports=[])
    def read(conn,workspace,task,key,member):
        assert (workspace,task,key,member)==('workspace','task',request,'member')
        state.connections.append(conn)
        if state.revoked:
            raise PermissionError('original action stopped')
        return deepcopy(state.facts)
    task=SimpleNamespace(workspace_id='workspace',id='task')
    business=object.__new__(domain.TaskBusiness)
    business.production=SimpleNamespace(_connect=lambda:nullcontext(object()),task_records=SimpleNamespace(get_creation_task=lambda *args,**kwargs:task))
    business.conversations=SimpleNamespace(_goal_root=forbidden,_task_turn_source=forbidden)
    business.authorizations=SimpleNamespace(verdict=forbidden)
    business.generation_case=SimpleNamespace(read_generation_request=read)
    business.creator_memory=lambda workspace,member:{'revision':7,'member_id':member}
    business.workspace_facts=lambda workspace,current:{'task_id':current.id}
    def port(name):
        def call(*args,**kwargs):
            state.ports.append((name,args,kwargs))
            return {'task_id':'task','script_id':'script','run_id':'original-run'}
        return call
    business.script_case=lambda actual:SimpleNamespace(start_generation_window=port('start'),patch=port('patch'),
        window_request_result=port('query'),start_window=forbidden,confirm_saved=forbidden)
    business.native_case=SimpleNamespace(dispatch=forbidden)
    return business,state,binding,actor,task


def invoke(business,binding,operation,args=None,**options):
    return TaskBusinessAdapter(business)(operation,args or {},call_id='call',tool_call_id='tool',source_binding=binding,
        cancel_event=options.get('cancel_event',Event()),deadline_monotonic=float('inf'))


def section():
    return {'id':'section','title':'段','content':'已核资料内容','visual_prompt':'画面','evidence_reference_ids':[]}


def test_sdk_generation_tools_are_final_and_do_not_restrict_ordinary_task(monkeypatch):
    business,state,binding,_,_=fixture()
    calls=[]
    monkeypatch.setattr(pi,'_run_turn',lambda settings,**kwargs:(calls.append(kwargs) or ('done',None)))
    params=dict(workspace_id='workspace',task_id='task',conversation_id=binding['conversation_id'],turn_id=binding['turn_id'],
        caller_user_id='member',message='实际原目标',stage='script',selected_refs=[],playhead_ms=None,
        business_handler=TaskBusinessAdapter(business),native_handler=object(),input_handler=object())
    pi.run_task_turn(None,source_binding=binding,**params)
    generation=calls[-1]
    assert set(generation['tool_names'].split(','))==set(SCRIPT_GENERATION_TOOLS)
    assert generation['native_handler'] is None and generation['input_handler'] is None
    assert generation['message']=='实际原目标'
    pi.run_task_turn(None,**params)
    assert {'confirm_saved_script','preview_script_draft','start_creation_execution'} <= set(calls[-1]['tool_names'].split(','))
    assert calls[-1]['native_handler'] is params['native_handler']


@pytest.mark.parametrize('invalid',['missing_intent','other_task','no_host','appended_input'])
def test_sdk_rejects_unbound_generation_before_any_runtime(monkeypatch,invalid):
    business,_,binding,_,_=fixture()
    handler=TaskBusinessAdapter(business)
    if invalid=='missing_intent':
        del binding['intent_kind']
    if invalid=='other_task':
        binding['task_id']='other'
    if invalid=='no_host':
        handler=None
    if invalid=='appended_input':
        binding['input_id']='new-message'
    monkeypatch.setattr(pi,'_run_turn',forbidden)
    with pytest.raises(ValueError):
        pi.run_task_turn(None,workspace_id='workspace',task_id='task',conversation_id=binding['conversation_id'],turn_id=binding['turn_id'],
            caller_user_id='member',message='实际原目标',stage='script',selected_refs=[],playhead_ms=None,
            source_binding=binding,business_handler=handler)


def test_generation_preflight_reads_real_actor_memory_and_workspace_without_a_goal_root():
    business,_,binding,_,_=fixture()
    memory=invoke(business,binding,'read_creator_memory')
    workspace=invoke(business,binding,'read_task_workspace')
    assert memory=={'ok':True,'value':{'revision':7,'member_id':'member'}}
    assert workspace=={'ok':True,'value':{'task_id':'task'}}


def test_generation_window_ports_receive_original_host_request_and_not_model_run_or_scope():
    business,state,binding,_,_=fixture()
    payload={'request_id':str(uuid4()),'title':'稿件','sections':[section()]}
    started=invoke(business,binding,'start_script_window',{'payload':payload,'generation_request_id':'forged'})
    assert started['ok'] and started['value']['run_id']=='original-run',started
    patched=invoke(business,binding,'patch_script_sections',{'script_id':'script','payload':{
        'request_id':str(uuid4()),'expected_updated_at':'2026-10-27T00:00:00Z','sections':[section()]}})
    assert patched['ok']
    queried=invoke(business,binding,'read_script_window_request',{'request_id':str(uuid4()),'operation':'patch',
        'script_id':'script','include_input':True,'generation_request_id':'forged'})
    assert queried['ok'] and [name for name,_,_ in state.ports]==['start','patch','query']
    for name,args,kwargs in state.ports:
        assert kwargs['generation_request_id']==binding['generation_request_id'] and kwargs['member_id']=='member'
        if name!='query':
            assert kwargs['instruction_origin']['raw_command']==''
            assert kwargs['reread_origin'](True)==kwargs['instruction_origin']
            with pytest.raises(TaskBusinessRejected):
                kwargs['reread_origin'](False)
    assert str(state.ports[0][1][2].assistant_turn_id)==binding['turn_id']
    assert state.ports[2][2]['include_input'] is True


def test_generation_denies_adoption_legacy_and_native_bypasses_before_owner():
    business,state,binding,actor,task=fixture()
    for operation in ('confirm_saved_script','save_assistant_script_draft','start_creation_execution','update_preference_memory'):
        rejected=invoke(business,binding,operation,{'payload':{}})
        assert not rejected['ok'] and rejected['failure']['status_code']==403
    native=TaskBusinessAdapter(business).native_call('submit_task_command',{},call_id='call',tool_call_id='tool',
        source_binding=binding,cancel_event=Event(),deadline_monotonic=float('inf'))
    assert not native['ok'] and native['failure']['submission_effect']=='not_submitted'
    with pytest.raises(TaskBusinessRejected):
        business.execute('confirm_saved_script',{},actor,task,state.facts,
            {'intent_kind':'script_generation','generation_request_id':binding['generation_request_id'],'requested_effect':'draft'},None,forbidden)
    forged=invoke(business,binding,'start_script_window',{'payload':{'assistant_turn_id':'forged'}})
    assert not forged['ok'] and not state.ports


@pytest.mark.parametrize('changed',['turn','effect','actor'])
def test_generation_rejects_cross_scope_reader_facts(changed):
    business,state,binding,_,_=fixture()
    if changed=='turn':
        state.facts['turn_id']='other'
    if changed=='effect':
        state.facts['requested_effect']='adopt'
    if changed=='actor':
        state.facts['actor']['member_id']='other'
    result=invoke(business,binding,'read_task_workspace')
    assert not result['ok'] and not state.ports


def test_generation_commit_guard_rechecks_original_request_in_the_same_transaction():
    business,state,binding,_,_=fixture()
    committed=[]
    transaction=object()
    def start(*args,write_guard,**kwargs):
        state.revoked=True
        write_guard(transaction)
        committed.append('must not write')
    business.script_case=lambda actor:SimpleNamespace(start_generation_window=start)
    result=invoke(business,binding,'start_script_window',{'payload':{'request_id':str(uuid4()),'title':'稿件','sections':[section()]}})
    assert not result['ok'] and state.connections[-1] is transaction and not committed
    assert result['failure'].get('submission_effect')!='not_submitted'


def test_generation_cannot_patch_a_different_script_or_write_after_cancel():
    business,state,binding,_,_=fixture()
    denied=invoke(business,binding,'patch_script_sections',{'script_id':'other','payload':{}})
    assert not denied['ok'] and not state.ports
    cancelled=Event()
    cancelled.set()
    denied=invoke(business,binding,'read_task_workspace',cancel_event=cancelled)
    assert not denied['ok'] and denied['failure']['submission_effect']=='not_submitted'


def test_generation_rechecks_scope_before_returning_private_read_results():
    business,state,binding,_,_=fixture()
    def read(*args):
        state.revoked=True
        return {'private':'must not reach the SDK'}
    business.execute=read
    result=invoke(business,binding,'read_assistant_script_context')
    assert not result['ok'] and 'must not reach the SDK' not in str(result)
    assert result['failure'].get('submission_effect')!='not_submitted'


def test_real_node_task_preflight_reads_its_required_host_dependencies():
    import json
    from pathlib import Path
    import selectors
    import shutil
    import subprocess
    node=shutil.which('node')
    if node is None:
        pytest.skip('node unavailable')
    business,state,binding,_,_=fixture()
    business.workspace_facts=lambda *args:{'task':{'id':'task','stage':'script','confirmed_script_id':None},
        'plan':{'id':'plan','knowledge_references':[],'source_references':[]}}
    business.script_context=lambda *args:{'draft':None,'sources':[]}
    business.execution_status=lambda ws,task,*,actor:{'task_id':task,'job':None,'stages':[],'video_ready':False}
    module=Path(__file__).resolve().parents[1]/'agent_tools/diary/task-context.ts'
    code="""
import {createInterface} from 'node:readline';
const {createTaskContext}=await import(process.argv[1]);
const {createBusinessRequest}=await import(new URL('./transport.ts',process.argv[1]));
const input=createInterface({input:process.stdin});
const request=createBusinessRequest({taskId:'task',selectedTaskVersion:null,verifyTaskVersion:()=>{},
 readCredentials:()=>{throw Error('no credentials');},getSignal:()=>undefined,
 fetchRequest:()=>{throw Error('no HTTP');},
 businessCall:async(operation,args)=>{
   const response=new Promise(resolve=>input.once('line',line=>resolve(JSON.parse(line))));
   console.log(JSON.stringify({operation,args}));return await response;
 }});
await request('/api/assistant/memory');
const context=createTaskContext({request,taskId:'task',stage:'script',selectedRefs:[],playheadMs:null,
 verifyTaskVersion:()=>{},initialTaskRead:false,initialScriptDraftUpdatedAt:''});
console.log(JSON.stringify({done:await context.readCurrentTaskFacts()}));
input.close();
"""
    child=subprocess.Popen([node,'--experimental-strip-types','--input-type=module','-e',code,module.as_uri()],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    operations=[]
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout,selectors.EVENT_READ)
            while True:
                assert selector.select(timeout=10),'Node host dependency request timed out'
                line=child.stdout.readline()
                assert line,'Node exited before task facts returned'
                request=json.loads(line)
                if 'done' in request:
                    assert request['done']['task_id']=='task'
                    break
                operations.append(request['operation'])
                result=invoke(business,binding,request['operation'],request['args'])
                child.stdin.write(json.dumps(result)+'\n')
                child.stdin.flush()
            _,stderr=child.communicate(timeout=5)
            assert child.returncode==0,stderr
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
    assert operations==['read_creator_memory','read_task_workspace','read_creation_execution','read_assistant_script_context']
    assert not state.ports


def test_real_generation_receipt_authorizes_only_its_original_action_and_durable_stop(generation,monkeypatch):
    state,conversations,owner,actor=generation
    chat=conversations.current_or_create('local',actor.member_id,'task','task')
    draft=conversations.save_draft(chat['id'],'local',actor.member_id,'尚未发送的修改要求',0)
    record,created=owner.start('local','task',str(uuid4()),actor)
    assert created
    business=object.__new__(domain.TaskBusiness)
    business.production=state.repository
    business.conversations=conversations
    business.generation_case=owner
    business.creator_memory=lambda ws,member:{'revision':3,'member_id':member}
    monkeypatch.setattr(conversations,'_goal_root',forbidden)
    binding={'workspace_id':'local','member_id':actor.member_id,'task_id':'task',
        'conversation_id':record['conversation_id'],'turn_id':record['turn_id'],
        'intent_kind':'script_generation','generation_request_id':record['request_id']}
    result=invoke(business,binding,'read_creator_memory')
    assert result=={'ok':True,'value':{'revision':3,'member_id':actor.member_id}}
    turn=conversations.get_turn(record['turn_id'])
    assert turn['user_message']=='' and turn['status']=='running'
    assert not invoke(business,binding,'confirm_saved_script',{'payload':{}})['ok']
    owner.request_stop('local','task',record['request_id'],actor.member_id)
    stopped=invoke(business,binding,'read_creator_memory')
    assert not stopped['ok'] and stopped['failure']['submission_effect']=='not_submitted'
    assert state.repository.script_records.get_script_generation_run('local',record['run_id']).status=='running'
    current=conversations.get(chat['id'],'local',actor.member_id)
    assert current['draft_text']=='尚未发送的修改要求' and current['draft_version']==draft['draft_version']
