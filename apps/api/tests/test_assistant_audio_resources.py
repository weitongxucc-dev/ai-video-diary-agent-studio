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
    turn,_=conversations.create_turn_idempotent(chat['id'],actor.workspace_id,member,
        'import music',str(uuid4()),1,request_context={'kind':'task','task_id':'task'})
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
            **original,'conversation_id':'untrusted-conversation', 'request_id':'untrusted-request'},None
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
