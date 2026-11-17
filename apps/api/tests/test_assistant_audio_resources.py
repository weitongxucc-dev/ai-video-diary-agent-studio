"""Synthetic audio input, real request/Job transactions; no provider or user media."""
from types import SimpleNamespace
from uuid import uuid4
from threading import Event

import pytest

from app import main
from app.creation.audio_ingress_use_case import AudioIngressRejected
from app.creation.resource_audio_use_case import ResourceAudioUseCase
from app.assistant.task_business import TaskBusiness
from app.assistant.task_business_adapter import TaskBusinessAdapter
from app.assistant.media_result_binding import AssistantMediaResultBinding
from app.creation.media_result_use_case import MediaResultRejected
from app.conversation_repository import ConversationRepository
from test_audio_ingress_application import audio_state, pcm_bytes


def setup_case(tmp_path, monkeypatch):
    repo,actor,media,runtime,*_=audio_state(tmp_path,monkeypatch)
    path=tmp_path/'resource.wav'
    path.write_bytes(pcm_bytes())
    item=SimpleNamespace(id='music',version=1,name='music',status='connected',
                         preview=SimpleNamespace(kind='audio'),provenance=[])
    def factory():
        return ResourceAudioUseCase(repo,media.audio_ingress,media,catalog_for=lambda:SimpleNamespace(resources=[item]),
            path_for=lambda _:path,temporary_root=main.settings.effective_temporary_root_path,
            cpu_budget=lambda:main._device_operation_budget('cpu_audio'),receipt_for=lambda *args:None,conversations=None)
    return repo,actor,media,runtime,item,factory


def test_fixed_import_restarts_query_original_job_and_preserves_catalog_source(tmp_path,monkeypatch):
    repo,actor,media,runtime,item,factory=setup_case(tmp_path,monkeypatch)
    request_id=str(uuid4())
    first=factory().submit(actor,'task',resource_id='music',version=1,request_id=request_id,runtime=runtime)
    assert first['status']=='recorded' and first['job_id']
    assert factory().submit(actor,'task',resource_id='music',version=1,request_id=request_id,runtime=runtime)==first
    assert factory().read(actor,'task',request_id)==first
    job,event=media.read(actor.workspace_id,first['job_id'])
    assert event['arguments']['catalog_resource']=={'id':'music','version':1,'provenance':[]}
    assert event['arguments']['_resource_request_id']==request_id
    with pytest.raises(ValueError,match='不同编辑输入'):
        factory().submit(actor,'task',resource_id='music',version=2,request_id=request_id,runtime=runtime)
    assert factory().read(actor,'task',request_id)['job_id']==job.id


def test_definite_failure_is_journaled_and_unknown_claim_never_resubmits(tmp_path,monkeypatch):
    repo,actor,media,runtime,item,factory=setup_case(tmp_path,monkeypatch)
    request_id=str(uuid4())
    item.status='catalog_only'
    with pytest.raises(AudioIngressRejected):
        factory().submit(actor,'task',resource_id='music',version=1,request_id=request_id,runtime=runtime)
    receipt=factory().read(actor,'task',request_id)
    assert receipt['status']=='rejected' and receipt['submission_effect']=='not_submitted'
    item.status='connected'
    assert factory().submit(actor,'task',resource_id='music',version=1,request_id=request_id,runtime=runtime)==receipt
    unknown=str(uuid4())
    task=repo.task_records.get_creation_task(actor.workspace_id,'task')
    repo.execution_records.claim_edit_request(task,actor.user_id or actor.member_id,unknown,'audio_resource',
        {'resource_id':'music','version':1,'source':None,'actor_identity':actor.identity_payload()})
    assert factory().submit(actor,'task',resource_id='music',version=1,request_id=unknown,runtime=runtime)['status']=='unknown'


def test_scheduler_reply_loss_retains_atomic_job_locator_without_resubmission(tmp_path,monkeypatch):
    repo,actor,media,runtime,item,factory=setup_case(tmp_path,monkeypatch)
    request_id=str(uuid4())
    def lost(*args):
        raise RuntimeError('synthetic reply lost after commit')
    monkeypatch.setattr(runtime,'wake',lost)
    with pytest.raises(RuntimeError,match='reply lost'):
        factory().submit(actor,'task',resource_id='music',version=1,request_id=request_id,runtime=runtime)
    receipt=factory().read(actor,'task',request_id)
    assert receipt['status']=='recorded' and receipt['job_id']
    # Recreated use case must return the committed original before staging or waking.
    assert factory().submit(actor,'task',resource_id='music',version=1,request_id=request_id,runtime=runtime)==receipt


def test_task_business_import_persists_verified_conversation_for_strict_media_binding(tmp_path,monkeypatch):
    repo,actor,media,runtime,item,factory=setup_case(tmp_path,monkeypatch)
    conversations=ConversationRepository(repo.database_path)
    member=actor.user_id or actor.member_id
    chat=conversations.create(actor.workspace_id,member,scope_kind='task',scope_id='task')
    conversations.save_draft(chat['id'],actor.workspace_id,member,'import music',0)
    task=repo.task_records.get_creation_task(actor.workspace_id,'task')
    current=repo.timeline_records.get_timeline_version(actor.workspace_id,task.current_timeline_version_id) if task.current_timeline_version_id else None
    snapshot={'timeline_version_id':current.id if current else None,'timeline_sequence':current.sequence if current else 0}
    turn,_=conversations.create_turn_idempotent(chat['id'],actor.workspace_id,member,
        'import music',str(uuid4()),1,request_context={'kind':'task','task_id':'task','target_snapshot':snapshot})
    resource=factory()
    resource.conversations=conversations
    business=object.__new__(TaskBusiness)
    business.resource_audio=resource
    business.runtime=lambda:runtime
    # The admission seam uses an actual consumed turn; submission, host
    # dispatch, original request journal and Job transaction are real.
    def check(binding,*args,**kwargs):
        original=conversations.task_turn_source(binding['turn_id'],actor.workspace_id,member,'task')
        return actor,repo.task_records.get_creation_task(actor.workspace_id,'task'),{}, {
            **original,'conversation_id':'untrusted-conversation', 'request_id':'untrusted-request',
            'expected_timeline_version_id':'untrusted-version','expected_timeline_sequence':999},None
    business.check=check
    request_id=str(uuid4())
    payload={'resource_id':'music','version':1,'request_id':request_id,'assistant_turn_id':turn['id']}
    host=TaskBusinessAdapter(business)
    options=dict(call_id='import',tool_call_id='resource',source_binding={'turn_id':turn['id']},
        cancel_event=Event(),deadline_monotonic=float('inf'))
    first=host('import_audio_resource',{'payload':payload},**options)
    assert first['ok'],first
    assert host('import_audio_resource',{'payload':payload},**options)==first
    job,event=media.read(actor.workspace_id,first['value']['job_id'])
    source=event['arguments']['_typed_source']
    assert source['conversation_id']==chat['id']
    assert source['assistant_turn_id']==turn['id']
    assert source['request_id']==request_id
    assert source['workspace_id']==actor.workspace_id and source['task_id']=='task'
    assert source['member_id']==member
    assert source['expected_timeline_version_id']==snapshot['timeline_version_id']
    assert source['expected_timeline_sequence']==snapshot['timeline_sequence']
    assert first['value']['submitted_input']['source']==source
    reported={'operation':'audio_import','input_digest':event['input_digest'],
        'created_at':job.created_at,'job_id':job.id,'status':job.status,
        'result_kind':'audio_source','result_url':f'/media-jobs/{job.id}'}
    binding=AssistantMediaResultBinding(repo,conversations,None,None,None)
    assert binding.related_job(media,actor.workspace_id,'task',turn['id'],chat['id'],reported)==job.id
    with pytest.raises(MediaResultRejected,match='不属于本轮真实目标'):
        binding.related_job(media,actor.workspace_id,'task',turn['id'],'another-conversation',reported)
    with pytest.raises(MediaResultRejected,match='不属于本轮真实目标'):
        binding.related_job(media,actor.workspace_id,'task','another-turn',chat['id'],reported)


def test_task_business_media_read_uses_public_response_and_rejects_foreign_task(tmp_path,monkeypatch):
    repo,actor,media,runtime,item,factory=setup_case(tmp_path,monkeypatch)
    receipt=factory().submit(actor,'task',resource_id='music',version=1,
        request_id=str(uuid4()),runtime=runtime)
    task=repo.task_records.get_creation_task(actor.workspace_id,'task')
    business=object.__new__(TaskBusiness)
    business.media_result=main._goal_media_metadata
    # The host admission seam fixes the current authorized task. The actual
    # production media read below must independently verify the stored Job.
    business.check=lambda *args,**kwargs:(actor,task,{}, {},None)
    host=TaskBusinessAdapter(business)
    options=dict(call_id='read',tool_call_id='resource',source_binding={'turn_id':'turn'},
        cancel_event=Event(),deadline_monotonic=float('inf'))
    result=host('read_task_media_job',{'task_id':'task','job_id':receipt['job_id']},**options)
    assert result['ok'],result
    assert result['value']['job_id']==receipt['job_id']
    assert result['value']['operation']=='audio_import'
    assert result['value']['result_kind']=='audio_source'
    assert 'task_id' not in result['value']
    from dataclasses import replace
    other=replace(task,id='other-task')
    business.check=lambda *args,**kwargs:(actor,other,{}, {},None)
    rejected=host('read_task_media_job',{'task_id':other.id,'job_id':receipt['job_id']},**options)
    assert rejected['ok'] is False and rejected['failure']['status_code']==404


@pytest.mark.parametrize('snapshot', [{}, {'timeline_version_id':None}, {'timeline_sequence':0},
    {'timeline_version_id':None,'timeline_sequence':True}, {'timeline_version_id':None,'timeline_sequence':2}])
def test_agent_resource_import_without_verified_frozen_snapshot_never_submits(tmp_path,monkeypatch,snapshot):
    repo,actor,media,runtime,item,factory=setup_case(tmp_path,monkeypatch)
    conversations=ConversationRepository(repo.database_path)
    member=actor.user_id or actor.member_id
    chat=conversations.create(actor.workspace_id,member,'task','task')
    conversations.save_draft(chat['id'],actor.workspace_id,member,'import music',0)
    turn,_=conversations.create_turn_idempotent(chat['id'],actor.workspace_id,member,'import music',str(uuid4()),1,
        request_context={'kind':'task','task_id':'task','target_snapshot':snapshot})
    resource=factory()
    resource.conversations=conversations
    before=repo.job_records.list_job_runs(actor.workspace_id,'task')
    with pytest.raises(AudioIngressRejected,match='冻结时间线版本'):
        resource.submit(actor,'task',resource_id='music',version=1,request_id=str(uuid4()),
            source={'assistant_turn_id':turn['id'],'expected_timeline_version_id':'invented','expected_timeline_sequence':1},runtime=runtime)
    assert repo.job_records.list_job_runs(actor.workspace_id,'task')==before


@pytest.mark.parametrize('fields', [{}, {'expected_timeline_version_id':None}, {'expected_timeline_sequence':0}])
def test_persisted_legacy_import_source_is_marked_without_rewriting_or_projection(tmp_path,monkeypatch,fields):
    from copy import deepcopy
    from dataclasses import replace
    from app.assistant_goal_continuation import GoalResultProjection
    from app.creation.contracts import TaskEvent
    repo,actor,media,runtime,item,factory=setup_case(tmp_path,monkeypatch)
    receipt=factory().submit(actor,'task',resource_id='music',version=1,request_id=str(uuid4()),runtime=runtime)
    job,start=media.read(actor.workspace_id,receipt['job_id'])
    conversations=ConversationRepository(repo.database_path)
    member=actor.user_id or actor.member_id
    chat=conversations.create(actor.workspace_id,member,'task','task')
    conversations.save_draft(chat['id'],actor.workspace_id,member,'old import',0)
    turn,_=conversations.create_turn_idempotent(chat['id'],actor.workspace_id,member,'old import',str(uuid4()),1,
        request_context={'kind':'task','task_id':'task','target_snapshot':{'timeline_version_id':None,'timeline_sequence':0}})
    conversations.finish_turn(turn['id'],'failed')
    legacy=repo.job_records.create_job_run(replace(job,id='legacy-import',idempotency_key='legacy-import'))
    source={'workspace_id':actor.workspace_id,'task_id':'task','member_id':member,
        'conversation_id':chat['id'],'assistant_turn_id':turn['id'],**fields}
    payload=deepcopy(start)
    payload['arguments']['_typed_source']=source
    repo.execution_records.append_task_event(TaskEvent('event_legacy-import_start',actor.workspace_id,'task',legacy.id,
        'task_media_submitted',payload))
    projection=GoalResultProjection(repo,ConversationRepository(repo.database_path))
    candidate={'kind':'job','id':legacy.id,'workspace_id':actor.workspace_id,'task_id':'task','source':source,
        'event_id':'event_legacy-import_projection','created_at':legacy.created_at.isoformat()}
    assert projection.business_candidate(candidate,media_available=True) is True
    marker=repo.execution_records.get_task_event(actor.workspace_id,candidate['event_id'])
    assert marker.payload['reason']=='source_timeline_snapshot_unverifiable'
    assert repo.execution_records.get_task_event(actor.workspace_id,'event_legacy-import_start').payload==payload
    assert not projection.execution_source_current(SimpleNamespace(current_timeline_version_id=None),source,legacy.id,'succeeded')
    saved=conversations.get_turn(turn['id'])
    assert saved['status']=='failed' and not (saved['result'] or {}).get('continuation')
