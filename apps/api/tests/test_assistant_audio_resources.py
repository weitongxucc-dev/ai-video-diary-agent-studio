"""Synthetic audio input, real request/Job transactions; no provider or user media."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import main
from app.creation.audio_ingress_use_case import AudioIngressRejected
from app.creation.resource_audio_use_case import ResourceAudioUseCase
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
