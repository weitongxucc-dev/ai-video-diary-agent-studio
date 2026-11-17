"""Synthetic original production records, no media/model/runtime execution."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.materials.candidate_bundle import build_candidate_bundle
from app.materials.profile_feedback import ProfileCandidateFeedbackReader
from app.profiles.contracts import Authorization, PersonProfile, VoiceProfile
from tests.test_creation_execution import _seed


def seed(tmp_path):
    repo, task, script, _ = _seed(tmp_path)
    grant = Authorization('grant', 'a1', 'owner', ('voice', 'person_likeness'),
        ('narration', 'avatar_lipsync'), datetime.now(timezone.utc) - timedelta(days=1),
        None, 'evidence:owned', 'active', 'workspace_a')
    repo.save_authorization(grant)
    person = PersonProfile('person_1', 'workspace_a', 'Person', 'v1', '/private/avatar', (), (),
        authorization=grant, owner_user_id='member')
    voice = VoiceProfile('voice_1', 'workspace_a', 'v1', 'zh', '/private/voice', 'private transcript',
        authorization=grant, owner_user_id='member')
    with repo._connect() as conn:
        repo.profile_records._save_person_profile(conn, person)
        repo.profile_records.save_received_voice(conn, voice)
        conn.execute("UPDATE creation_tasks SET payload=json_set(payload,'$.owner_user_id','member') WHERE id=?", (task,))
    return repo, task, script, grant, voice


def read(repo, task, kind='voice', version='v1', member='member', workspace='workspace_a'):
    return ProfileCandidateFeedbackReader(repo)(workspace, kind, kind + '_1', version, member, task_id=task)


def production_facts(repo, task, script):
    ws = 'workspace_a'
    with repo._connect() as conn:
        conn.execute('''INSERT INTO narration_segments
            (id,workspace_id,task_id,script_id,source_section_id,text,audio_reference,duration_seconds,
             sample_rate,model_id,voice_profile_id,voice_version,language,pronunciation_version,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('segment',ws,task,script,'section_1','private text','narration://artifact/audio',1,16000,
             'fixture','voice_1','v1','zh','','ready','time','time'))
        for identity, status in [('person-ready','ready'), ('person-failed','failed')]:
            conn.execute('''INSERT INTO person_renders
                (id,workspace_id,task_id,person_profile_id,person_version,voice_profile_id,voice_version,
                 narration_segment_id,visual_reference,model_id,motion,expression,cache_key,status,
                 failure_reason,created_at,updated_at,artifact_ref)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (identity,ws,task,'person_1','v1','voice_1','v1','segment','workspace://private/avatar',
                 'fixture','natural','neutral',identity,status,'provider_failed' if status=='failed' else None,
                 identity,identity,'artifacts/person.mp4' if status=='ready' else None))
        for identity, status, kind in [('person-ready','succeeded','person_render'),
                ('person-failed','failed','person_render'),('render','succeeded','creation_execution'),
                ('observe','succeeded','output_observation'),('unrelated','failed','creation_execution')]:
            conn.execute('''INSERT INTO job_runs(id,workspace_id,task_id,kind,idempotency_key,input_digest,status,
                progress,attempt,output_refs_json,failure_reason,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (identity,ws,task,kind,identity,identity,status,0,1,'[]',
                 'provider_failed' if status=='failed' else None,identity,identity))
        for identity, ref, producer, kind, inputs in [
                ('person-artifact','artifacts/person.mp4','person-ready','person_render',[]),
                ('output','artifacts/output.mp4','render','rendered_video',['artifacts/person.mp4'])]:
            conn.execute('INSERT INTO artifact_records VALUES (?,?,?,?,?,?,?,?,?,?)',
                (identity,ws,task,kind,ref,'a'*64,1,producer,json.dumps(inputs),identity))
        for seq, identity, parent, montage, outputs in [
                (1,'v1',None,{'clips':[{'person_render_id':'person-ready'}]},[]),
                (2,'v2','v1',{'clips':[{'person_render_id':'person-ready'}]},['artifacts/output.mp4']),
                (3,'v3','v2',{'clips':[]},[])]:
            conn.execute('INSERT INTO timeline_versions VALUES (?,?,?,?,?,?,?,?)',
                (identity,ws,task,parent,seq,json.dumps({'montage':montage,'output_refs':outputs}),None,identity))
        result={'result':{'observation_id':'observe','timeline_version_id':'v2','output':{'artifact_id':'output'}}}
        conn.execute('INSERT INTO task_events VALUES (?,?,?,?,?,?,?)',
            ('event-observe',ws,task,'observe','task_media_result',json.dumps(result),'time'))
        conn.execute("UPDATE creation_tasks SET payload=json_set(payload,'$.current_timeline_version_id','v3') WHERE id=?", (task,))


def test_unused_candidate_is_not_generation_or_adoption(tmp_path):
    repo, task, _, _, _ = seed(tmp_path)
    person = read(repo, task, 'person')
    assert person['status'] == 'candidate'
    assert person['feedback']['status'] == 'not_used'
    assert person['feedback']['stages']['generation'] == ['not_recorded']
    voice = read(repo, task)
    assert voice['feedback']['task_reference'] is True
    assert voice['feedback']['generations'] == []
    assert voice['feedback']['observations'] == []
    assert ProfileCandidateFeedbackReader(repo)('workspace_a','voice','voice_1','v1','member')['feedback'] == {'status':'not_requested'}


@pytest.mark.parametrize('kind', ['person', 'voice'])
def test_success_failure_timeline_removal_and_observation_from_original_facts(tmp_path, kind):
    repo, task, script, _, _ = seed(tmp_path)
    production_facts(repo, task, script)
    def readonly():
        conn = original()
        conn.execute('PRAGMA query_only=ON')
        return conn
    original = repo._connect
    repo._connect = readonly
    candidate = read(repo, task, kind)
    feedback = candidate['feedback']
    assert feedback == read(repo, task, kind)['feedback']
    assert {'ready','failed'} <= set(feedback['stages']['generation'])
    assert [(item['timeline_version_id'],item['transition']) for item in feedback['timeline_history']] == [
        ('v1','added'),('v2','retained'),('v3','removed')]
    assert feedback['current_profile_present'] is False
    assert {j['job_id']: j['status'] for j in feedback['jobs']} == {
        'person-ready':'succeeded','person-failed':'failed','render':'succeeded','observe':'succeeded'}
    assert feedback['observations'][0]['job_status'] == 'succeeded'
    assert feedback['observations'][0]['artifact_id'] == 'output'
    assert feedback['media_validation'] == 'unknown'
    bundle = build_candidate_bundle(profile_candidates=[candidate])
    assert bundle['candidates'][0]['status'] == 'candidate'
    assert '/private/' not in json.dumps(bundle) and 'private transcript' not in json.dumps(bundle)


def test_fixed_historical_version_and_current_revocation_do_not_rewrite_history(tmp_path):
    repo, task, script, grant, voice = seed(tmp_path)
    production_facts(repo, task, script)
    with repo._connect() as conn:
        repo.profile_records.save_received_voice(conn, replace(voice,version='v2'))
    assert read(repo,task,version='v2')['feedback']['status'] == 'not_used'
    before = read(repo,task)['feedback']
    repo.save_authorization(replace(grant,version='a2',status='revoked'))
    after = read(repo,task)
    assert after['authorization']['status'] == 'unavailable'
    assert after['feedback'] == before
    assert after['source_revision'] == {'profile_version':'v1','current_profile_version':'v2'}


def test_profile_and_task_workspace_member_isolation(tmp_path):
    repo, task, _, _, _ = seed(tmp_path)
    for options in ({'workspace':'other'},{'member':'other'},{'version':'missing'}):
        with pytest.raises(KeyError):
            read(repo,task,**options)
    with repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET visibility='private',payload=json_set(payload,'$.owner_user_id','other') WHERE id=?",(task,))
    with pytest.raises(KeyError):
        read(repo,task)


def test_frozen_voice_snapshot_survives_mutable_segment_revision_without_borrowing_old_jobs(tmp_path):
    repo, task, script, _, voice = seed(tmp_path)
    production_facts(repo,task,script)
    with repo._connect() as conn:
        repo.profile_records.save_received_voice(conn,replace(voice,version='v2'))
        conn.execute("UPDATE narration_segments SET voice_version='v2' WHERE id='segment'")
        snapshot={'narration_segments':[{'id':'segment','voice_profile_id':'voice_1','voice_version':'v1',
            'audio_reference':'narration://artifact/old-audio','status':'ready'}]}
        conn.execute('INSERT INTO timeline_versions VALUES (?,?,?,?,?,?,?,?)',
            ('historical', 'workspace_a',task,'v3',4,json.dumps(snapshot),None,'time'))
        conn.execute('INSERT INTO task_events VALUES (?,?,?,?,?,?,?)',
            ('old-job-segment','workspace_a',task,'unrelated','stage_failed',
             json.dumps({'narration_segment_id':'segment'}),'time'))
    history=read(repo,task)['feedback']
    assert any(g['kind']=='narration_segment_snapshot' and g['timeline_version_id']=='historical'
               for g in history['generations'])
    assert history['timeline_history'][-1]['profile_present'] is True
    assert not any(j['job_id']=='unrelated' for j in read(repo,task,version='v2')['feedback']['jobs'])


def test_observation_event_is_not_success_when_original_job_failed(tmp_path):
    repo,task,script,_,_=seed(tmp_path)
    production_facts(repo,task,script)
    with repo._connect() as conn:
        conn.execute("UPDATE job_runs SET status='failed',failure_reason='observation_failed' WHERE id='observe'")
    feedback=read(repo,task,'person')['feedback']
    assert feedback['observations'][0]['job_status']=='failed'
    assert next(j for j in feedback['jobs'] if j['job_id']=='observe')['failure_reason']=='observation_failed'


def test_profile_relation_in_voice_selection_is_not_person_generation(tmp_path):
    repo,task,_,_,_=seed(tmp_path)
    with repo._connect() as conn:
        payload={'selected_voice':{'profile_id':'voice_1','version':'v1','person_profile_id':'person_1','person_version':'v1'}}
        conn.execute('INSERT INTO task_events VALUES (?,?,?,?,?,?,?)',
            ('voice-selection','workspace_a',task,'voice-job','stage_started',json.dumps(payload),'time'))
    assert read(repo,task,'person')['feedback']['status']=='not_used'


def test_candidate_http_forwards_task_feedback_and_rejects_foreign_task(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from app.materials.library_routes import MaterialLibraryHTTPAdapter, build_material_library_router
    repo,task,_,_,_=seed(tmp_path)
    adapter=MaterialLibraryHTTPAdapter(records_for=lambda:SimpleNamespace(get_asset=lambda *_:SimpleNamespace(
        id='asset',visibility='shared',owner_user_id=None,
        updated_at=datetime.now(timezone.utc),modified_at=datetime.now(timezone.utc))),
        search_for=lambda:SimpleNamespace(search_material_segments=lambda *a,**kw:[]),
        workspace_for=lambda _:'workspace_a',reader_for=lambda *_:('member',False),visible=lambda *_:True,
        write_guard=lambda _:None,library_root=lambda:'.',asset_response=lambda a:a,
        profile_candidate_for=ProfileCandidateFeedbackReader(repo))
    app=FastAPI()
    app.include_router(build_material_library_router(lambda:adapter,('read_material_candidate_bundle',)))
    client=TestClient(app)
    params={'person_profile_id':'person_1','person_version':'v1','task_id':task}
    response=client.get('/api/production/materials/asset/candidate-bundle',params=params)
    assert response.status_code==200
    assert response.json()['candidates'][0]['feedback']['task_id']==task
    params['task_id']='other-task'
    assert client.get('/api/production/materials/asset/candidate-bundle',params=params).status_code==404


def test_inherited_unrelated_output_and_foreign_observation_version_cannot_supply_feedback(tmp_path):
    repo,task,script,_,_=seed(tmp_path)
    production_facts(repo,task,script)
    with repo._connect() as conn:
        conn.execute('INSERT INTO artifact_records VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('unrelated-output','workspace_a',task,'rendered_video','artifacts/unrelated.mp4','b'*64,1,
             'unrelated','[]','time'))
        # A copied output ref alone cannot prove this profile produced the output.
        snapshot={'montage':{'clips':[{'person_render_id':'person-ready'}]},'output_refs':['artifacts/unrelated.mp4']}
        conn.execute('INSERT INTO timeline_versions VALUES (?,?,?,?,?,?,?,?)',
            ('copy','workspace_a',task,'v3',4,json.dumps(snapshot),None,'time'))
        for identity,version,artifact in [('inherited','copy','unrelated-output'),('foreign','foreign-version','output')]:
            result={'result':{'observation_id':identity,'timeline_version_id':version,'output':{'artifact_id':artifact}}}
            conn.execute('INSERT INTO task_events VALUES (?,?,?,?,?,?,?)',
                (identity,'workspace_a',task,'observe','task_media_result',json.dumps(result),'time'))
    feedback=read(repo,task,'person')['feedback']
    assert {row['artifact_id'] for row in feedback['artifacts']}=={'person-artifact','output'}
    assert [row['observation_id'] for row in feedback['observations']]==['observe']


def test_bounded_history_does_not_claim_unused_when_truncated(tmp_path):
    repo,task,_,_,_=seed(tmp_path)
    with repo._connect() as conn:
        conn.executemany('INSERT INTO task_events VALUES (?,?,?,?,?,?,?)',
            ((f'e{i}','workspace_a',task,None,'fixture','{}',str(i)) for i in range(2001)))
    feedback=read(repo,task,'person')['feedback']
    assert feedback['completeness']=='partial'
    assert feedback['status']=='unknown'
