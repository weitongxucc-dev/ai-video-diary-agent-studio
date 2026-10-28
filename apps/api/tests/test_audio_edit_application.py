"""Two finite audio editor journeys on real isolated stores and synthetic original grants."""
from dataclasses import asdict,replace
from datetime import datetime,timezone
import hashlib,io,json,wave
from pathlib import Path
from uuid import uuid4

import pytest

from app import main
from app.audio_timeline import TimelineAudioTrack
from app.creation.contracts import TimelineVersion
from app.montage import MontageClip,creation_task_montage_plan_to_payload
from app.production import MaterialAsset,MaterialSegment
from app.task_audio import TASK_MEDIA_CONTEXT
from test_audio_ingress_application import audio_state,seed_narration,settled,pcm_bytes


def edit_state(tmp_path,monkeypatch):
    repo,actor,media,runtime,client,errors,executions=audio_state(tmp_path,monkeypatch)
    monkeypatch.setattr(main,'creation_execution_mode','controlled')
    media.audio_edit=main._audio_edit_use_case()
    with repo._connect() as conn:
        for table in ('creation_tasks','creation_plans'):
            row=conn.execute('SELECT id,payload FROM '+table).fetchone()
            payload=json.loads(row['payload']);payload['owner_user_id']=actor.member_id
            conn.execute('UPDATE '+table+' SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    version,graph,path=seed_narration(repo,media,tmp_path)
    graph=graph.model_copy(update={'tracks':[track.model_copy(update={'locked':False,
        'clips':[clip.model_copy(update={'locked':False}) for clip in track.clips]}) for track in graph.tracks]})
    payload=repo.get_creation_task_montage_plan('local','task');payload['audio_timeline']=graph.model_dump()
    repo.save_creation_task_montage_plan('local','task','montage',payload)
    version=repo.create_timeline_version(replace(version,id='editable-base',parent_version_id=version.id,sequence=2,
        snapshot={**version.snapshot,'montage':payload}))
    calls=[];execute=media.audio_edit.execute
    def observed(job,event,who,context):
        assert TASK_MEDIA_CONTEXT.get() is None and who==actor and context.grant.owner_id==job.id
        calls.append((event['operation'],job.id,context.grant.request_id))
        return execute(job,event,who,context)
    monkeypatch.setattr(media.audio_edit,'execute',observed)
    return repo,actor,media,runtime,client,version,graph,path,calls


def test_direct_draft_native_preview_confirm_and_original_result(tmp_path,monkeypatch):
    repo,actor,media,runtime,client,base,graph,path,calls=edit_state(tmp_path,monkeypatch)
    url='/api/creation-tasks/task/audio/preview'
    edit={'base_version_id':base.id,'action':'update','target_id':graph.tracks[0].clips[0].id,'values':{'gain_db':-3}}
    before_jobs=repo.job_records.list_job_runs('local','task');before_artifacts=repo.execution_records.list_artifact_records('local','task')
    draft=client.post(url+'?draft=true',json=edit)
    assert draft.status_code==200,draft.text
    assert repo.job_records.list_job_runs('local','task')==before_jobs and repo.execution_records.list_artifact_records('local','task')==before_artifacts
    assert repo.task_records.get_creation_task('local','task').current_timeline_version_id==base.id
    submitted=client.post(url,json=edit)
    assert submitted.status_code==202,submitted.text
    receipt=submitted.json();result=settled(client,runtime,receipt)
    assert result['status']=='succeeded',result
    proposal=result['result']['proposal'];intent=repo.get_edit_intent('local',proposal['intent_id'])
    assert intent.constraints['audio_source_facts']
    for side in ('before','after'):
        audio=client.get(proposal['preview'][side]['audio_url']);assert audio.status_code==200
        with wave.open(io.BytesIO(audio.content)) as stream:assert stream.getframerate()==48000
    assert repo.task_records.get_creation_task('local','task').current_timeline_version_id==base.id
    confirms=[];original=repo.edit_adoption.confirm
    def observed_confirm(*args,**kwargs):
        if kwargs.get('operation').operation_type=='EditAudioTimeline':
            assert kwargs.get('verify_sources') is not None
            confirms.append(kwargs['operation'].id)
        return original(*args,**kwargs)
    monkeypatch.setattr(repo.edit_adoption, 'confirm',observed_confirm)
    request={'request_id':str(uuid4())}
    endpoint='/api/creation-tasks/task/edits/'+proposal['id']+'/confirm'
    applied=client.post(endpoint,json=request);assert applied.status_code==200,applied.text
    first=applied.json();assert len(confirms)==1
    current=repo.task_records.get_creation_task('local','task');assert current.current_timeline_version_id!=base.id
    replay=client.post(endpoint,json=request);assert replay.status_code==200 and replay.json()['operation']==first['operation']
    assert len(confirms)==1 and len(repo.list_edit_operations('local','task'))==1
    fixed=client.get('/api/creation-tasks/task/edits/'+proposal['id']+'/result');assert fixed.status_code==200
    assert fixed.json()['result']['operation']==first['operation']
    assert len(calls)==1 and calls[0][0]=='audio_edit_preview' and calls[0][1]!=calls[0][2]
    assert TASK_MEDIA_CONTEXT.get() is None and runtime.stop(1)


def test_exact_command_and_original_audio_share_original_job_and_recording_block(tmp_path,monkeypatch,granted_media):
    repo,actor,media,runtime,client,base,graph,path,calls=edit_state(tmp_path,monkeypatch)
    payload={'operation_type':'EditAudioTimeline','audio_edit':{'action':'update','target_id':graph.tracks[0].clips[0].id,
        'values':{'gain_db':-2}},'request_id':str(uuid4()),'expected_timeline_version_id':base.id,'expected_timeline_sequence':base.sequence}
    exact=client.post('/api/creation-tasks/task/edits/exact/preview',json=payload)
    assert exact.status_code==202,exact.text
    exact_result=settled(client,runtime,exact.json());assert exact_result['status']=='succeeded',exact_result
    command={'raw_command':'音量降低3分贝','context_stage':'montage',
        'selected_object_refs':['audio:'+graph.tracks[0].clips[0].id],'playhead_ms':0}
    audio=client.post('/api/creation-tasks/task/edits/preview',json=command)
    assert audio.status_code==202,audio.text
    audio_result=settled(client,runtime,audio.json());assert audio_result['status']=='succeeded',audio_result
    repo.video_library_path.mkdir(parents=True,exist_ok=True)
    video=repo.video_library_path/'owned-original.mp4'
    with granted_media([path]) as context:
        encoded=context.run(['ffmpeg','-v','error','-filter_threads','1','-threads','1','-f','lavfi','-i','color=s=32x32:r=10:d=0.2',
            '-i',str(path),'-c:v','mpeg4','-threads:v','1','-c:a','aac','-threads:a','1','-shortest',str(video)],output_paths=(video,))
        assert encoded.returncode==0,encoded.stderr
    stat=video.stat();asset=MaterialAsset(id='original-video',workspace_id='local',file_name=video.name,path=str(video),
        size_bytes=stat.st_size,modified_at=datetime.fromtimestamp(stat.st_mtime,timezone.utc),duration_seconds=.2,
        owner_user_id=actor.member_id,visibility='private',analysis_status='ready',usage_role='b_roll',
        segments=[MaterialSegment('source-segment','original-video',0,.2,usage_role='b_roll')])
    repo._save_asset(asset)
    ref=repo.workspace_media_reference_for_asset('local',asset.id)
    with repo._connect() as conn:repo._claim_workspace_media_reference(conn,workspace_id='local',reference=ref)
    plan=repo.get_creation_task_montage_plan('local','task')
    assert len([track for track in graph.tracks if track.role=='original'])==1
    plan['audio_timeline']=graph.model_dump();plan['clips']=[asdict(MontageClip('video-clip','cue',asset.id,'source-segment',ref,
        0,.2,0,.2,1,[],False))]
    repo.save_creation_task_montage_plan('local','task','montage',plan)
    next_base=repo.create_timeline_version(replace(base,id='original-base',parent_version_id=base.id,sequence=3,
        snapshot={**base.snapshot,'montage':plan}))
    raw=client.post('/api/creation-tasks/task/edits/preview',json={'raw_command':'提取原声','context_stage':'montage',
        'selected_object_refs':['clip:video-clip'],'playhead_ms':0})
    assert raw.status_code==202,raw.text
    raw_result=settled(client,runtime,raw.json());assert raw_result['status']=='succeeded',raw_result
    proposal=raw_result['result']['proposal'];new_ref=proposal['operation']['payload']['edit']['values']['source_ref']
    published=[row for row in repo.execution_records.list_artifact_records('local','task') if row.producer_job_id==raw.json()['job_id']]
    assert len(published)==3 and next(row for row in published if row.logical_ref==new_ref).kind=='audio_source'
    assert calls[-1][1]==raw.json()['job_id'] and len(calls)==3
    assert repo.task_records.get_creation_task('local','task').current_timeline_version_id==next_base.id
    monkeypatch.setattr(main,'creation_execution_mode','scheduled')
    blocked=client.post('/api/creation-tasks/task/audio/preview',json={'base_version_id':next_base.id,'action':'replace_narration',
        'target_id':graph.tracks[0].clips[0].id,'values':{'source_ref':'artifacts/task/fixture-voice.wav'}})
    assert blocked.status_code==503 and blocked.json()['detail']=='电脑录音校验尚未接入整机许可'
    assert TASK_MEDIA_CONTEXT.get() is None and runtime.stop(1)
