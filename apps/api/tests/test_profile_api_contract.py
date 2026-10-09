"""Synthetic/isolated API and queue consumers; not model or GUI acceptance."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import io
import os
import wave
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
import app.main as main
from app.production import ProductionRepository, MaterialAsset, MaterialSegment, automatic_segment_eligible
from app.avatar_lipsync import PersonProfile, ProfileReference, VoiceProfile, AvatarPreviewService, UnavailableAvatarAndLipSyncProvider
from app.narration import VoicePreviewService
from app.creation_execution import CreationExecutionActor
from app.profile_preview_execution import profile_preview_registration, submit_profile_preview
from app.device_runtime import build_device_runtime
from app.device_scheduler import ProcessIdentity
from app.host_resource_probes import NarrationBudget
from app.voice_clone import VoiceCloneResult


def wav_bytes():
    buf=io.BytesIO()
    with wave.open(buf,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(b'\x01\x00'*16000)
    return buf.getvalue()

@pytest.fixture
def repo(tmp_path):
    return ProductionRepository(database_path=tmp_path/'db.sqlite3',video_library_path=tmp_path/'library',duration_reader=lambda _:2,scene_reader=lambda *_:[])

@pytest.fixture
def client(repo,monkeypatch):
    monkeypatch.setattr(main,'production_repo',repo)
    monkeypatch.setattr(main,'current_workspace_id',lambda request:request.headers.get('x-test-workspace','ws'))
    monkeypatch.setattr(main,'_content_reader',lambda request,ws:(request.headers.get('x-test-member','member'),False))
    return TestClient(main.app)


def test_independent_registration_default_fixed_reopen_and_reader(client,repo):
    imported=client.post('/api/media/voice-references/import',files={'audio':('synthetic.wav',wav_bytes(),'audio/wav')})
    assert imported.status_code==201, imported.text
    payload={'id':'voice','display_name':'独立声音','language':'zh','reference_audio_path':imported.json()['reference']}
    saved=client.post('/api/media/voice-profiles',json=payload)
    assert saved.status_code==200,saved.text
    body=saved.json();assert body['authorization'] is None and body['person_profile_id']=='' and body['model_id']==''
    assert body['reference']['asset_id'] and body['reference_url']
    assert client.get('/api/media/voice-profiles').json()['items'][0]==body
    assert client.get(body['reference_url']).content==wav_bytes()
    assert client.put('/api/media/default-voice',json={'profile_id':'voice','version':body['version']}).status_code==200
    updated=client.post('/api/media/voice-profiles',json={**payload,'display_name':'新名称'})
    assert updated.status_code==200 and updated.json()['version']!=body['version']
    assert client.get('/api/media/default-voice').json()['version']==body['version']
    assert client.get('/api/media/voice-profiles',headers={'x-test-member':'other'}).json()['items']==[]
    assert client.get(body['reference_url'],headers={'x-test-member':'other'}).status_code==404
    assert client.get('/api/media/voice-profiles',headers={'x-test-workspace':'other'}).json()['items']==[]


def test_person_registration_no_model_and_member_write_boundary(client,repo):
    from PIL import Image
    path=repo.video_library_path/'portrait.png';path.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(2,2)).save(path)
    asset=repo._save_asset(MaterialAsset(id='portrait',workspace_id='ws',file_name=path.name,path=str(path),size_bytes=path.stat().st_size,modified_at=datetime.fromtimestamp(path.stat().st_mtime),media_type='image',owner_user_id='member'))
    payload={'id':'person','display_name':'自制图','avatar_reference':repo.workspace_media_reference_for_asset('ws',asset.id),'reference':{'asset_id':asset.id}}
    saved=client.post('/api/media/person-profiles',json=payload)
    assert saved.status_code==200,saved.text
    assert saved.json()['authorization'] is None and saved.json()['body_reference_available']
    assert client.post('/api/media/person-profiles',json=payload,headers={'x-test-member':'other'}).status_code==403
    path.unlink()
    again=client.get('/api/media/person-profiles')
    assert again.status_code==200 and not again.json()['items'][0]['body_reference_available']


def test_automation_and_coverage_keep_observation(repo,tmp_path):
    path=repo.video_library_path/'synthetic.mp4';path.parent.mkdir(exist_ok=True);path.write_bytes(b'isolated metadata fixture, not claimed decoded video')
    segment=MaterialSegment(id='s',asset_id='a',start_seconds=0,end_seconds=2,description='办公画面',system_tags=['戴眼镜女性','笔记本','办公室'],usage_role='a_roll')
    asset=repo._save_asset(MaterialAsset(id='a',workspace_id='ws',file_name=path.name,path=str(path),size_bytes=path.stat().st_size,modified_at=datetime.fromtimestamp(path.stat().st_mtime),duration_seconds=2,analysis_status='ready',segments=[segment],owner_user_id='member'))
    with repo._connect() as conn: repo._claim_workspace_media_reference(conn,workspace_id='ws',reference=repo.workspace_media_reference_for_asset('ws',asset.id))
    assert automatic_segment_eligible(asset,segment,'ws')
    assert len(repo.automatic_material_segments('ws',viewer_user_id='member'))==1
    for value in ['reference_only','excluded','not_for_automation']:
        assert not automatic_segment_eligible(replace(asset,use_state=value),segment,'ws')
    assert not automatic_segment_eligible(asset,replace(segment,review_status='rejected'),'ws')
    assert not automatic_segment_eligible(asset,replace(segment,end_seconds=3),'ws')
    assert repo.automatic_material_segments('ws',viewer_user_id='other')==[]
    insights=repo.library_insights('ws',viewer_user_id='member')
    assert not {'主体:人物','物体:电脑','场景:办公'} & set(insights['recommended_gaps'])
    assert {'戴眼镜女性','笔记本','办公室'} <= {i['tag'] for i in insights['tags']}

class VoiceProvider:
    ready=True;model_id='fixture-model';message='test';supported_languages=('zh',)
    calls=0
    def generate(self,**kwargs):
        self.calls+=1
        assert kwargs['grant_context'].grant.owner_type=='media_preview'
        out=Path(kwargs['output_path']);out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(wav_bytes())
        return VoiceCloneResult(audio_path=str(out),sample_rate=8000,duration_seconds=2)

class Resources:
    admit_value=False
    def admit(self,request):return self.admit_value
    def released(self,grant):return True
    def continue_unit(self,grant):return True
class Processes:
    def current(self):return ProcessIdentity(os.getpid(), "fixture-start", "fixture-boot")
    def children_released(self,children):return True
    def recovery_state(self,holder,children):return "released"
class Access:
    def can_view_device_summary(self,actor):return True


def setup_preview(repo,tmp_path,provider=None):
    ref,_,_=repo.import_voice_reference_wav('ws',wav_bytes(),verified_owner_user_id='member')
    voice=repo.save_versioned_voice_profile(VoiceProfile(id='voice',workspace_id='ws',version='v1',language='zh',display_name='声音',reference_audio_path=ref,reference_text='合成文字',model_id='fixture-model',reference=ProfileReference()),verified_owner_user_id='member')
    provider=provider or VoiceProvider();service=VoicePreviewService(tmp_path/'output',provider)
    budget=NarrationBudget(100,1,10**6,5,10**6,1,10,10**6,2,0.1,10**6,10**6)
    reg=profile_preview_registration(repo,voice_service=service,avatar_service=AvatarPreviewService(tmp_path/'output',UnavailableAvatarAndLipSyncProvider()),narration_budget=budget,presenter_budget=None,verify_identity=lambda actor:'ready')
    resources=Resources()
    runtime=build_device_runtime(database_path=repo.database_path,device_lock_path=tmp_path/'device.lock',device_access=Access(),registrations={'media_preview':reg},resources=resources,processes=Processes())
    actor=CreationExecutionActor('ws','member');params={'voice_profile_id':voice.id,'voice_version':voice.version,'preview_text':'简短合成测试'}
    prepared=service.request(repository=repo,workspace_id='ws',viewer_user_id='member',prepare_only=True,**params)
    queued=submit_profile_preview(runtime,prepared,actor,params)
    return runtime,resources,provider,queued


def test_preview_queue_zero_provider_until_grant_and_atomic_success(repo,tmp_path):
    runtime,resources,provider,queued=setup_preview(repo,tmp_path)
    try:
        assert queued.status=='queued' and provider.calls==0
        assert runtime.run_once() is False and provider.calls==0
        resources.admit_value=True
        assert runtime.run_once()
        final=repo.get_media_preview_run('ws','voice',queued.identity)
        assert final.status=='succeeded', final
        assert Path(final.output_path).read_bytes()==wav_bytes() and provider.calls==1
        assert not runtime.run_once() and provider.calls==1
    finally:assert runtime.stop(1)


def test_preview_stale_and_cancel_do_not_generate(repo,tmp_path):
    runtime,resources,provider,queued=setup_preview(repo,tmp_path)
    try:
        voice=repo.get_versioned_voice_profile('ws','voice','v1')
        asset=repo.get_asset('ws',voice.reference.asset_id)
        repo.update_asset_use_state('ws',asset.id,'excluded')
        resources.admit_value=True
        assert not runtime.run_once() and provider.calls==0
        result=runtime.scheduler.cancel(queued.device_request_id,CreationExecutionActor('ws','member'))
        assert result['state']=='cancelled' and provider.calls==0
    finally:assert runtime.stop(1)


def test_preview_failure_is_not_success_and_preserves_input(repo,tmp_path):
    class Failing(VoiceProvider):
        def generate(self,**kwargs):
            self.calls+=1
            raise OSError('synthetic provider failure')
    runtime,resources,provider,queued=setup_preview(repo,tmp_path,Failing())
    try:
        resources.admit_value=True
        assert runtime.run_once()
        final=repo.get_media_preview_run('ws','voice',queued.identity)
        assert final.status=='failed' and final.output_path is None and provider.calls==1
        assert final.preview_input['preview_text']=='简短合成测试'
        assert not runtime.run_once() and provider.calls==1
    finally:assert runtime.stop(1)


def test_preview_submission_failure_is_persisted_without_provider(repo,tmp_path,monkeypatch):
    runtime,resources,provider,queued=setup_preview(repo,tmp_path)
    try:
        runtime.scheduler.cancel(queued.device_request_id,CreationExecutionActor('ws','member'))
        repo.save_media_preview_run(replace(queued,status='cancelled',finished_at=main.utc_now(),reason='cancelled',impact='Fixture cancelled',repair_action='Explicit retry'))
        service=runtime.registrations['media_preview'].owner.services['voice']
        params={**queued.preview_input,'retry':True}
        prepared=service.request(repository=repo,workspace_id='ws',viewer_user_id='member',prepare_only=True,**params)
        def rejected(*args):raise PermissionError('synthetic ledger refusal')
        monkeypatch.setattr(runtime.scheduler,'submit',rejected)
        with pytest.raises(PermissionError,match='ledger refusal'):
            submit_profile_preview(runtime,prepared,CreationExecutionActor('ws','member'),params)
        last=repo.get_media_preview_run('ws','voice',prepared.identity)
        assert last.status=='failed' and last.reason=='device_submission_failed' and provider.calls==0
    finally:assert runtime.stop(1)


def test_default_and_profile_usage_are_not_upgraded_on_reopen(client,repo):
    ref,_,_=repo.import_voice_reference_wav('ws',wav_bytes(),verified_owner_user_id='member')
    payload={'id':'limited','language':'zh','display_name':'仅参考','reference_audio_path':ref,'use_state':'reference_only'}
    first=client.post('/api/media/voice-profiles',json=payload)
    assert first.status_code==200 and not first.json()['runnable']
    del payload['use_state']
    second=client.post('/api/media/voice-profiles',json=payload)
    assert second.status_code==200 and second.json()['use_state']=='reference_only'
    assert client.put('/api/media/default-voice',json={'profile_id':'limited','version':first.json()['version']}).status_code==200
    assert client.get('/api/media/default-voice',headers={'x-test-member':'other'}).json() is None
    assert client.put('/api/media/default-voice',json={}).status_code==422


def test_plan_duration_pin_and_zero_person_do_not_query_lipsync(client,repo,monkeypatch):
    from app.production import VideoTemplate
    from app.schemas import CreationPlanPreviewRequest
    from starlette.requests import Request
    monkeypatch.setattr(main,'source_repository',SimpleNamespace(list_knowledge_items_readable_by=lambda *a,**kw:[],list_source_items=lambda *a:[]))
    monkeypatch.setattr(main,'model_catalog',SimpleNamespace(list_statuses=lambda:[]))
    def no_lipsync(*args):raise AssertionError('0 percent must not inspect lipsync provider')
    monkeypatch.setattr(main,'_lipsync_provider',no_lipsync)
    template=repo.save_video_template(VideoTemplate(id='t',workspace_id='ws',version=1,name='模板',origin='user',settings={'duration_target_seconds':90,'language':'zh-CN','aspect_ratio':'9:16'}),verified_owner_user_id='member')
    request=Request({'type':'http','headers':[]})
    base={'goal':'合成目标','template_id':template.id,'template_version':1,'presenter_share_target':0}
    inherited=main._creation_plan_preflight(request,'ws',CreationPlanPreviewRequest(**base))
    explicit=main._creation_plan_preflight(request,'ws',CreationPlanPreviewRequest(**base,duration_target_seconds=30))
    confirm=main._creation_plan_preflight(request,'ws',main.CreationPlanConfirmRequest(**base,duration_target_seconds=30,preflight_fingerprint=explicit.preflight_fingerprint))
    assert inherited.duration_target_seconds==90 and explicit.duration_target_seconds==30
    assert confirm.preflight_fingerprint==explicit.preflight_fingerprint!=inherited.preflight_fingerprint
    ref,_,_=repo.import_voice_reference_wav('ws',wav_bytes(),verified_owner_user_id='member')
    voice=repo.save_versioned_voice_profile(VoiceProfile(id='independent',workspace_id='ws',version='v1',language='zh',display_name='声音',reference_text='',reference_audio_path=ref,reference=ProfileReference()),verified_owner_user_id='member')
    person_path=repo.video_library_path/'portrait.png'
    from PIL import Image
    Image.new('RGB',(2,2)).save(person_path)
    asset=repo._save_asset(MaterialAsset(id='p',workspace_id='ws',file_name=person_path.name,path=str(person_path),size_bytes=person_path.stat().st_size,modified_at=datetime.fromtimestamp(person_path.stat().st_mtime),media_type='image',owner_user_id='member'))
    person=repo.save_person_profile(PersonProfile(id='p',workspace_id='ws',version='v2',display_name='人物',background_capabilities=(),shot_capabilities=(),avatar_reference=repo.workspace_media_reference_for_asset('ws',asset.id),reference=ProfileReference(asset_id=asset.id)),verified_owner_user_id='member')
    pinned=main._creation_plan_preflight(request,'ws',CreationPlanPreviewRequest(**base,selected_voice={'profile_id':voice.id,'version':voice.version},selected_person={'profile_id':person.id,'version':person.version}))
    assert pinned.selected_voice.profile_id==voice.id and pinned.selected_voice.person_profile_id==''
    assert pinned.selected_person.profile_id==person.id and pinned.selected_person.version==person.version


def test_health_uses_workspace_effective_settings_and_current_call_only(client,monkeypatch):
    from app import workspace_settings as store
    from app.config import Settings,record_llm_service_verification
    from app.runtime_health import _deepseek_item
    from app.models import utc_now
    base=Settings(llm_provider='minimax',llm_api_key='synthetic-old-env',llm_api_key_provider='minimax')
    for ws in ('health-a','health-b'):
        store.save_workspace_config(ws,{'llm_provider':'minimax'})
        store.save_workspace_secret(ws,'llm_api_key','synthetic-test-secret')
    effective={ws:store.workspace_settings(ws,base).settings for ws in ('health-a','health-b')}
    record_llm_service_verification(effective['health-a'],workspace_id='health-a',ok=True,cause='verified')
    assert _deepseek_item(effective['health-a'],utc_now(),'health-a')['status']=='ready'
    assert _deepseek_item(effective['health-b'],utc_now(),'health-b')['status']=='degraded'
    record_llm_service_verification(effective['health-a'],workspace_id='health-a',ok=False,cause='key_rejected')
    assert '密钥' in _deepseek_item(effective['health-a'],utc_now(),'health-a')['reason']
    store.delete_workspace_secret('health-a','llm_api_key')
    assert _deepseek_item(store.workspace_settings('health-a',base).settings,utc_now(),'health-a')['status']=='missing'
    captured=[]
    monkeypatch.setattr(main,'_workspace_settings_for',lambda ws:effective[ws])
    monkeypatch.setattr(main,'runtime_health',lambda settings,workspace_id:captured.append((settings,workspace_id)) or {'summary':{'total':0,'ready':0,'missing':0,'degraded':0,'invalid':0},'items':[],'feature_gates':[]})
    response=client.get('/api/runtime/health',headers={'x-test-workspace':'health-b'})
    assert response.status_code==200,response.text
    assert captured==[(effective['health-b'],'health-b')]


def test_preview_unknown_admission_recovery_and_cache_stay_same_request(repo,tmp_path):
    runtime,resources,provider,queued=setup_preview(repo,tmp_path)
    try:
        resources.admit_value=None
        assert not runtime.run_once() and provider.calls==0
        # Interrupted executor state; the existing queue grants after its process
        # recovery verifier declares the prior group released, not a second queue.
        repo.save_media_preview_run(replace(queued,status='running'))
        resources.admit_value=True
        assert runtime.run_once() and provider.calls==1
        final=repo.get_media_preview_run('ws','voice',queued.identity)
        assert final.id==queued.id and final.device_request_id==queued.device_request_id and final.status=='succeeded'
        service=runtime.registrations['media_preview'].owner.services['voice']
        reused=service.request(repository=repo,workspace_id='ws',viewer_user_id='member',prepare_only=True,
            member_identity=queued.member_identity,**queued.preview_input)
        assert reused.id==queued.id and reused.status=='succeeded' and provider.calls==1
    finally:assert runtime.stop(1)


def test_changed_pronunciation_before_admission_does_not_use_old_queue(repo,tmp_path):
    from app.avatar_lipsync import PronunciationEntry
    runtime,resources,provider,queued=setup_preview(repo,tmp_path)
    try:
        repo.save_pronunciation_entry(PronunciationEntry('rule','ws','AI','zh','诶艾','workspace',None,'v1'))
        resources.admit_value=True
        assert not runtime.run_once() and provider.calls==0
        view=runtime.scheduler.view_for(queued.device_request_id,CreationExecutionActor('ws','member'))
        assert view['wait_reason']=='input_stale'
    finally:assert runtime.stop(1)


def test_api_preview_posts_queue_and_cancel_with_member_filtered_read(client,repo,tmp_path,monkeypatch):
    runtime,resources,provider,queued=setup_preview(repo,tmp_path)
    monkeypatch.setattr(main,'_creation_runtime',lambda:runtime)
    monkeypatch.setattr(main,'voice_preview_service',runtime.registrations['media_preview'].owner.services['voice'])
    monkeypatch.setattr(main,'_require_permit_actor',lambda request,ws:CreationExecutionActor(ws,request.headers.get('x-test-member','member')))
    try:
        response=client.post('/api/media/previews/voice',json=queued.preview_input)
        assert response.status_code==200,response.text
        assert response.json()['id']==queued.id and response.json()['status']=='queued' and response.json()['output_url'] is None
        assert provider.calls==0
        assert client.get('/api/media/previews',headers={'x-test-member':'other'}).json()['items']==[]
        denied=client.post(f'/api/media/previews/voice/{queued.identity}/cancel',headers={'x-test-member':'other'})
        assert denied.status_code==404
        cancelled=client.post(f'/api/media/previews/voice/{queued.identity}/cancel')
        assert cancelled.status_code==200 and cancelled.json()['status']=='cancelled'
        resources.admit_value=True
        assert not runtime.run_once() and provider.calls==0
        assert repo.get_media_preview_run('ws','voice',queued.identity).preview_input==queued.preview_input
    finally:assert runtime.stop(1)


def test_avatar_preview_uses_own_grant_and_independent_person_version(repo,tmp_path,monkeypatch):
    from app.host_resource_probes import PresenterBudget
    from PIL import Image
    class AvatarProvider:
        ready=True;model_id='avatar-fixture';supported_languages=('zh',);message='fixture'
        calls=0
        def render(self,**kwargs):
            self.calls+=1
            assert kwargs['grant_context'].grant.owner_type=='media_preview'
            assert kwargs['grant_context'].grant.unit_key.startswith('avatar_lipsync:')
            raise OSError('intentional synthetic avatar failure')
    runtime,resources,provider,queued=setup_preview(repo,tmp_path)
    try:
        resources.admit_value=True
        assert runtime.run_once()
        audio=repo.get_media_preview_run('ws','voice',queued.identity)
        path=repo.video_library_path/'avatar.png';Image.new('RGB',(2,2)).save(path)
        asset=repo._save_asset(MaterialAsset('avatar','ws',path.name,str(path),path.stat().st_size,datetime.fromtimestamp(path.stat().st_mtime),media_type='image',owner_user_id='member'))
        person=repo.save_person_profile(PersonProfile('independent-person','ws','人物','p1',repo.workspace_media_reference_for_asset('ws',asset.id),(),(),reference=ProfileReference(asset_id=asset.id)),verified_owner_user_id='member')
        avatar_provider=AvatarProvider()
        owner=runtime.registrations['media_preview'].owner
        owner.services['avatar_lipsync']=AvatarPreviewService(tmp_path/'output',avatar_provider)
        owner.presenter_budget=PresenterBudget(1,10**6,5,10**6,1,10,10**6,2,.1,10**6,10**6)
        params=dict(person_id=person.id,person_version=person.version,voice_profile_id='voice',voice_version='v1',
                    audio_path=audio.output_path,audio_cache_key=audio.identity,language='zh',motion='natural',expression='neutral',authorization_id=None,authorization_version=None,model_id=avatar_provider.model_id)
        service=owner.services['avatar_lipsync']
        prepared=service.request(repository=repo,workspace_id='ws',viewer_user_id='member',prepare_only=True,member_identity=queued.member_identity,**params)
        assert prepared.status=='queued'
        submitted=submit_profile_preview(runtime,prepared,CreationExecutionActor('ws','member'),params)
        assert avatar_provider.calls==0
        assert runtime.run_once()
        result=repo.get_media_preview_run('ws','avatar_lipsync',submitted.identity)
        assert result.status=='failed' and result.output_path is None and result.person_version=='p1'
        assert avatar_provider.calls==1
        assert repo.get_person_profile('ws',person.id,person.version)==person
        assert repo.get_media_preview_run('ws','voice',queued.identity)==audio
    finally:assert runtime.stop(1)


def test_person_cache_validation_uses_actual_task_reader(repo,tmp_path,monkeypatch):
    monkeypatch.setattr(main,'production_repo',repo)
    render=SimpleNamespace(workspace_id='ws',task_id='task',person_profile_id='person',person_version='p1',cache_key='render-key',artifact_ref='artifact',motion='natural',expression='neutral',model_id='fixture')
    segment=SimpleNamespace(audio_reference='audio',voice_profile_id='voice',voice_version='v1',language='zh')
    facts={'source':'exact-source-revision'}
    transform=SimpleNamespace(transform_type='person_render',status='succeeded',input_digest='render-key',output_refs=['artifact'],input_refs=['audio'],parameters={'language':'zh','presenter_input_facts':facts})
    monkeypatch.setattr(repo,'get_creation_task',lambda ws,task:SimpleNamespace(owner_user_id='member'))
    monkeypatch.setattr(repo,'get_person_profile',lambda *args:SimpleNamespace(id='person',version='p1',authorization=None))
    monkeypatch.setattr(repo,'resolve_authorized_narration_audio_reference',lambda *args:tmp_path/'synthetic.wav')
    captured=[]
    monkeypatch.setattr(repo,'resolve_avatar_lipsync_request',lambda **kw:captured.append(kw) or SimpleNamespace())
    monkeypatch.setattr(main,'presenter_input_facts',lambda request:facts)
    monkeypatch.setattr('app.avatar_lipsync._authorization_failure',lambda request:None)
    assert main._person_render_matches_narration(render,segment,[transform])
    assert captured[0]['viewer_user_id']=='member' and captured[0]['person_version']=='p1'
    def denied(**kw):raise PermissionError('source sharing revoked')
    monkeypatch.setattr(repo,'resolve_avatar_lipsync_request',denied)
    assert not main._person_render_matches_narration(render,segment,[transform])
