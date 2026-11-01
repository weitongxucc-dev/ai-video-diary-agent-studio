"""Metadata-only real SQLite use cases; no HTTP, renderer or model calls."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.artifact_store import ArtifactStore
from app.creation.contracts import TimelineVersion, SubtitleTrack, SubtitleCue
from app.creation.editing.metadata_use_case import (
    MetadataEditUseCase, metadata_edit_command, default_subtitle_style,
)
from app.creation.editing.picture import EditConflict
from app.creation.editing.sources import EditSourceVerifier
from app.local_members import LocalMemberService
from app.montage import (
    CreationTaskMontagePlan, MontageClip, NarrationCue,
    creation_task_montage_plan_to_payload,
)
from app.creation.narration_artifacts import NarrationArtifactRecord
from app.production import ProductionRepository, MaterialAsset, MaterialSegment
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import CreationTask
from app.task_audio import TaskAudio


def metadata_state(tmp_path):
    """Seed fixed editorial facts and opaque source bytes; never decode media."""
    library = tmp_path / 'library'
    library.mkdir()
    repo = ProductionRepository(tmp_path / 'editor.sqlite3', library,
        duration_reader=lambda _: 4, scene_reader=lambda *_: [])
    members = LocalMemberService(repo.database_path)
    member, token = members.whoami(None)
    owner = member.member_id
    plan = CreationPlan('plan', 'ws', 'metadata', 'metadata', 1, 'zh-CN', '9:16', 4,
        [], [], None, [], 0, 0, 'fixed source', [], [], [], 'fixture')
    task = CreationTask('task', 'ws', plan.id, 'metadata', 'knowledge_explanation',
        stage='montage', owner_user_id=owner, visibility='private', narration_cache_key='fixed-narration')
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner)
    # The fixture begins at an already-produced editorial state. This is input
    # seeding, not evidence of a script/model/production workflow completing.
    with repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',
            (json.dumps(asdict(task), default=str), task.id))
    source = library / 'source.mp4'
    source.write_bytes(b'opaque metadata source; only stat identity is inspected')
    stat = source.stat()
    asset = MaterialAsset(id='asset', workspace_id='ws', file_name=source.name, path=str(source),
        size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        duration_seconds=4, owner_user_id=owner, visibility='private', analysis_status='ready', usage_role='b_roll',
        segments=[MaterialSegment('segment', 'asset', 0, 4, usage_role='b_roll')])
    repo._save_asset(asset)
    reference = repo.workspace_media_reference_for_asset('ws', asset.id)
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id='ws', reference=reference)
    montage = CreationTaskMontagePlan('montage', 'ws', task.id, 'fixed-script', 'fixed', 'ready',
        [NarrationCue('cue', 'section', '原字幕', 4, narration_segment_id='fixed-segment')],
        [MontageClip('clip', 'cue', asset.id, 'segment', reference, 0, 4, 0, 4, 1, [], False)],
        4, 0, narration_audio_path='artifacts/task/fixed-narration.wav')
    payload = creation_task_montage_plan_to_payload(montage)
    repo.montage_plan_adoption.save_creation_task_montage_plan('ws', task.id, montage.id, payload)
    narration = NarrationArtifactRecord('narration', 'ws', 'fixed-script', None, 'fixed-narration',
        montage.narration_audio_path, 4, 16000, 'fixture-input', task_id=task.id)
    repo.narration_artifacts.save_narration_artifact(narration)
    base = TimelineVersion('base', 'ws', task.id, None, 1,
        {'montage': payload, 'subtitle_track_ids': ['base-zh'], 'narration_cache_key': narration.cache_key}, None)
    repo.timeline_records.create_timeline_version(base)
    subtitle_ref = 'artifacts/task/fixed-subtitles.srt'
    subtitle_file = tmp_path / 'outputs' / subtitle_ref
    subtitle_file.parent.mkdir(parents=True)
    subtitle_file.write_text('1\n00:00:00,000 --> 00:00:04,000\n原字幕\n', encoding='utf-8')
    track = SubtitleTrack('base-zh', 'ws', task.id, base.id, 'zh-CN', None, default_subtitle_style(),
        [SubtitleCue('cue', None, '原字幕', 0, 4)], 'timeline', None, subtitle_ref, 'fixed-track-input', 'valid')
    repo.subtitle_records.create_subtitle_track(track)
    store = ArtifactStore(tmp_path / 'outputs')
    audio = TaskAudio(repo, store, tmp_path / 'temporary')
    case = MetadataEditUseCase(repo, store, output_root=tmp_path / 'outputs', audio_service=audio)
    return SimpleNamespace(repo=repo, task=repo.task_records.get_creation_task('ws', task.id), plan=montage, base=base,
        asset=asset, source=source, case=case, store=store, members=members, member=member, token=token,
        identity={'member_id': owner, 'local_diagnostic': False}, narration=narration, track=track)


@pytest.fixture
def state(tmp_path):
    return metadata_state(tmp_path)


def crop_command(state, **values):
    return metadata_edit_command(state.task, state.identity, 'ChangeCrop',
        {'base_version_id': state.base.id, 'clip_id': 'clip', 'fit_mode': 'fit', 'crop_x': .3, 'crop_y': .7, **values})


@pytest.mark.parametrize('authorization_state',['valid','missing','expired','revoked_during_prepare'])
def test_replace_material_adoption_keeps_original_authority_or_reports_not_ready(tmp_path,authorization_state):
    from datetime import timedelta
    from app.creation.contracts import TaskAuthorization
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.creation_execution import CreationExecutionActor
    s = metadata_state(tmp_path)
    path = tmp_path/'library'/'replacement.mp4'
    path.write_bytes(b'different opaque source with fixed metadata')
    info = path.stat()
    asset = replace(s.asset,id='replacement',file_name=path.name,path=str(path),size_bytes=info.st_size,
        modified_at=datetime.fromtimestamp(info.st_mtime,timezone.utc),
        segments=[replace(s.asset.segments[0],id='replacement-segment',asset_id='replacement')])
    s.repo._save_asset(asset)
    reference = s.repo.workspace_media_reference_for_asset('ws',asset.id)
    now = datetime.now(timezone.utc)
    actor = CreationExecutionActor('ws',s.member.member_id)
    def verify(_identity):
        if authorization_state=='revoked_during_prepare':
            current = s.repo.task_authorization_records.get_task_authorization('ws',s.task.id)
            s.repo.task_authorization_records.save_task_authorization(replace(current,revision=current.revision+1,revoked_at=now),
                expected_revision=current.revision)
        return 'ready'
    service = TaskAuthorizationUseCase(s.repo,verify,clock=lambda:now)
    _,required = service._requirements('ws',s.task.id)
    original = TaskAuthorization('original','ws',s.task.id,service.identity(actor),
        s.repo.task_authorization_records.task_authorization_goal_revision('ws',s.task.id),('render','narration'),
        {**required,'voices':['keep:voice'],'persons':['keep:person']},now-timedelta(hours=1),
        now-timedelta(hours=1),now+timedelta(hours=1) if authorization_state!='expired' else now-timedelta(seconds=1))
    if authorization_state!='missing':
        s.repo.task_authorization_records.save_task_authorization(original,expected_revision=None)
    s.case.task_authorizations = service
    command = metadata_edit_command(s.task,s.identity,'ReplaceClip',{'base_version_id':s.base.id,
        'clip_id':'clip','asset_id':asset.id,'segment_id':'replacement-segment'})
    if authorization_state=='revoked_during_prepare':
        with pytest.raises(EditConflict,match='授权已变化'):
            s.case.save(s.task,command)
        assert s.repo.task_records.get_creation_task('ws',s.task.id).current_timeline_version_id==s.base.id
        assert s.repo.edit_records.list_edit_operations('ws',s.task.id)==[]
        assert reference not in s.repo.task_authorization_records.get_task_authorization('ws',s.task.id).resource_scope['references']
        return
    result = s.case.save(s.task,command)
    assert result.montage.clips[0].asset_id==asset.id
    saved = s.repo.task_authorization_records.get_task_authorization('ws',s.task.id)
    assert result.execution_readiness['device_admission']=='not_checked'
    if authorization_state=='valid':
        assert result.execution_readiness['status']=='authorization_ready'
        assert saved.resource_scope['assets']==['asset','replacement']
        assert reference in saved.resource_scope['references']
        assert saved.resource_scope['voices']==original.resource_scope['voices']
        assert saved.resource_scope['persons']==original.resource_scope['persons']
        assert (saved.identity,saved.goal_revision,saved.issued_at,saved.verified_at,saved.expires_at)==(
            original.identity,original.goal_revision,original.issued_at,original.verified_at,original.expires_at)
    else:
        assert result.execution_readiness['status']=='not_ready'
        assert saved==(None if authorization_state=='missing' else original)


def editorial_rows(repo):
    with repo._connect() as conn:
        return {name: tuple(conn.execute(f'SELECT * FROM {name} ORDER BY rowid')) for name in
            ('creation_tasks', 'timeline_versions', 'edit_intents', 'edit_proposals', 'edit_operations',
             'subtitle_tracks', 'task_events', 'creation_task_montage_plans')}


def test_restore_material_uses_current_adoption_baseline_before_historical_subtitles(state):
    from copy import deepcopy
    from datetime import timedelta
    from app.creation.contracts import TaskAuthorization,EditIntent,EditProposal
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.creation_execution import CreationExecutionActor
    removed=deepcopy(state.plan);removed.clips=[]
    payload=creation_task_montage_plan_to_payload(removed)
    state.repo.montage_plan_adoption.save_creation_task_montage_plan('ws',state.task.id,removed.id,payload)
    base=TimelineVersion('without-material','ws',state.task.id,state.base.id,2,
        {**state.base.snapshot,'montage':payload,'subtitle_track_ids':[]},None)
    state.repo.timeline_records.create_timeline_version(base)
    task=state.repo.task_records.get_creation_task('ws',state.task.id)
    now=datetime.now(timezone.utc)
    actor=CreationExecutionActor('ws',state.member.member_id)
    service=TaskAuthorizationUseCase(state.repo,lambda _:'ready',clock=lambda:now)
    _,required=service._requirements('ws',task.id)
    assert required['assets']==[]
    original=TaskAuthorization('narrow-current','ws',task.id,service.identity(actor),
        state.repo.task_authorization_records.task_authorization_goal_revision('ws',task.id),('render',),required,now,now,now+timedelta(hours=1))
    state.repo.task_authorization_records.save_task_authorization(original,expected_revision=None)
    state.case.task_authorizations=service
    intent=state.repo.edit_records.create_edit_intent(EditIntent('restore-intent','ws',task.id,'撤销删除','montage',[],None,
        'undo_timeline',{}, {},1,'interpreted'))
    proposal=state.repo.edit_records.create_edit_proposal(EditProposal('restore-proposal','ws',task.id,intent.id,'恢复原素材',
        ['clip:clip'],{'operation_type':'UndoTimelineVersion','payload':{'base_version_id':base.id}},[],{},[]))
    result=state.case.persist_montage_edit(workspace_id='ws',task=task,proposal=proposal,base_version=base,
        updated_plan=deepcopy(state.plan),operation_type='UndoTimelineVersion',operation_payload=proposal.operation['payload'],
        subtitle_source_version=state.base,actor_identity=state.identity)
    saved=state.repo.task_authorization_records.get_task_authorization('ws',task.id)
    assert result.montage.clips[0].asset_id==state.asset.id
    assert result.execution_readiness['status']=='authorization_ready'
    assert saved.resource_scope['assets']==[state.asset.id]
    assert state.plan.clips[0].source_path in saved.resource_scope['references']
    assert saved.expires_at==original.expires_at and service.status('ws',task.id,operation='render')=='valid'


def test_insert_undo_redo_reauthorizes_restored_material_and_replays_http_readiness(state,tmp_path,monkeypatch):
    from datetime import timedelta
    from starlette.requests import Request
    from app.creation.contracts import TaskAuthorization
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.creation_execution import CreationExecutionActor
    from app.creation.editing.linked_timeline_use_case import LinkedTimelineEditUseCase
    from app.creation.editing.timeline_restore_use_case import TimelineRestoreUseCase,TimelineRestoreCommand
    from app.schemas import CreationTaskTimelineRestoreRequest
    import app.main as main
    s=state
    import wave
    audio_path=tmp_path/'original-narration.wav'
    with wave.open(str(audio_path),'wb') as stream:
        stream.setparams((1,2,16000,0,'NONE','not compressed'))
        stream.writeframes(b'\x01\x00'*64000)
    s.repo.narration_artifacts.save_narration_artifact(replace(s.narration,audio_path=str(audio_path),voice_profile_id='fixture-voice'))
    from app.creation.contracts import NarrationSegment
    now=datetime.now(timezone.utc)
    s.task.confirmed_script_id='fixed-script'
    script={'id':'fixed-script','workspace_id':'ws','task_id':s.task.id,'run_id':'fixture-run','topic_id':'fixture-topic',
        'title':'固定文案','language':'zh-CN','sections':[{'id':'section','title':'正文','content':'原字幕',
            'visual_prompt':'fixture','evidence_reference_ids':[]}],'selection_status':'confirmed',
        'created_at':now.isoformat(),'updated_at':now.isoformat(),'confirmed_at':now.isoformat()}
    segment=NarrationSegment(id='fixed-segment',workspace_id='ws',task_id=s.task.id,script_id='fixed-script',
        source_section_id='section',text='原字幕',audio_reference='narration://artifact/fixed-narration',
        duration_seconds=4,sample_rate=16000,model_id=s.narration.model_id,voice_profile_id='fixture-voice',voice_version='v1',
        language='zh',pronunciation_version=s.narration.pronunciation_version,status='ready')
    s.base=TimelineVersion('complete-narration-base','ws',s.task.id,s.base.id,s.base.sequence+1,
        {**s.base.snapshot,'narration_segments':[asdict(segment)],'subtitle_track_ids':['complete-zh']},None)
    with s.repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',(json.dumps(asdict(s.task),default=str),s.task.id))
        conn.execute('INSERT INTO creation_task_scripts(id,workspace_id,task_id,run_id,payload,updated_at) VALUES(?,?,?,?,?,?)',
            ('fixed-script','ws',s.task.id,'fixture-run',json.dumps(script),now.isoformat()))
    s.repo.timeline_records.create_timeline_version(s.base)
    s.repo.subtitle_records.create_subtitle_track(replace(s.track,id='complete-zh',timeline_version_id=s.base.id))
    s.repo.narration_records.save_narration_segments('ws',s.task.id,'fixed-script',[segment])
    path=tmp_path/'library'/'inserted.mp4';path.write_bytes(b'opaque inserted source')
    info=path.stat()
    asset=replace(s.asset,id='inserted-material',path=str(path),file_name=path.name,size_bytes=info.st_size,
        modified_at=datetime.fromtimestamp(info.st_mtime,timezone.utc),duration_seconds=1,
        segments=[replace(s.asset.segments[0],id='inserted-segment',asset_id='inserted-material',end_seconds=1)])
    s.repo._save_asset(asset)
    now=datetime.now(timezone.utc)
    actor=CreationExecutionActor('ws',s.member.member_id)
    service=TaskAuthorizationUseCase(s.repo,lambda _:'ready',clock=lambda:now)
    _,scope=service._requirements('ws',s.task.id)
    original=TaskAuthorization('insert-authority','ws',s.task.id,service.identity(actor),
        s.repo.task_authorization_records.task_authorization_goal_revision('ws',s.task.id),('render',),scope,now,now,now+timedelta(hours=1))
    s.repo.task_authorization_records.save_task_authorization(original,expected_revision=None)
    s.case.task_authorizations=service
    identity={**s.identity,'material_reader_identity':s.identity}
    inserted=LinkedTimelineEditUseCase(s.repo,s.case).apply(metadata_edit_command(s.task,identity,'InsertClip',
        {'base_version_id':s.base.id,'clip_id':'clip','asset_id':asset.id,'segment_id':'inserted-segment'}))
    restore=TimelineRestoreUseCase(s.repo,s.case)
    seen=s.repo.timeline_records.get_timeline_version('ws',inserted.task.current_timeline_version_id)
    undone=restore.execute(TimelineRestoreCommand('ws',inserted.task,False),actor=actor,request_id='undo-insert',
        target_operation_id=inserted.operation.id,expected_timeline_version_id=seen.id,expected_timeline_sequence=seen.sequence)
    current=s.repo.task_authorization_records.get_task_authorization('ws',s.task.id)
    _,remaining=service._requirements('ws',s.task.id)
    assert asset.id not in remaining['assets']
    s.repo.task_authorization_records.save_task_authorization(replace(current,revision=current.revision+1,resource_scope=remaining),expected_revision=current.revision)
    assert service.status('ws',s.task.id,operation='render')=='valid'
    monkeypatch.setattr(main,'current_workspace_id',lambda _:'ws')
    monkeypatch.setattr(main,'production_repo',s.repo)
    monkeypatch.setattr(main,'_exact_edit_access',lambda *_args,**_kwargs:(s.repo.task_records.get_creation_task('ws',s.task.id),actor.member_id))
    monkeypatch.setattr(main,'_timeline_restore_use_case',lambda:restore)
    request=Request({'type':'http','method':'POST','path':'/','headers':[]})
    seen=s.repo.timeline_records.get_timeline_version('ws',undone.task.current_timeline_version_id)
    payload=CreationTaskTimelineRestoreRequest(request_id='redo-insert',target_operation_id=inserted.operation.id,
        expected_timeline_version_id=seen.id,expected_timeline_sequence=seen.sequence)
    posted=next(route.endpoint for route in main.app.routes if route.name == "redo_creation_task_timeline")(s.task.id,request,payload)
    queried=next(route.endpoint for route in main.app.routes if route.name == "get_creation_task_timeline_restore_request")(s.task.id,'redo-insert',request)
    replayed=next(route.endpoint for route in main.app.routes if route.name == "redo_creation_task_timeline")(s.task.id,request,payload)
    assert posted.execution_readiness==queried['execution_readiness']==replayed.execution_readiness
    assert posted.restore_request['execution_readiness']==posted.execution_readiness
    assert posted.execution_readiness['status']=='authorization_ready'
    assert posted.execution_readiness['device_admission']=='not_checked'
    assert posted.operation.id==replayed.operation.id
    assert asset.id in s.repo.task_authorization_records.get_task_authorization('ws',s.task.id).resource_scope['assets']
    assert service.status('ws',s.task.id,operation='render')=='valid'


def test_use_case_cold_import_has_no_main_or_http_dependency():
    code = "import sys; import app.creation.editing.metadata_use_case; assert 'app.main' not in sys.modules; assert 'fastapi' not in sys.modules; assert 'starlette.requests' not in sys.modules"
    result = subprocess.run([sys.executable, '-B', '-c', code], env=dict(os.environ),
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_named_subtitle_restore_keeps_later_user_crop_and_rejects_changed_text(state):
    from app.creation_execution import CreationExecutionActor
    from app.creation.editing.timeline_restore_use_case import TimelineRestoreUseCase, TimelineRestoreCommand
    actor = CreationExecutionActor('ws', state.member.member_id)
    a = state.case.save(state.task, metadata_edit_command(state.task,state.identity,'UpdateSubtitleCue',
        {'base_version_id':state.base.id,'cue_id':'cue','text':'更新后的字幕'}))
    b = state.case.save(a.task, metadata_edit_command(a.task,state.identity,'ChangeCrop',
        {'base_version_id':a.task.current_timeline_version_id,'clip_id':'clip','fit_mode':'fit','crop_x':.3,'crop_y':.7}))
    case = TimelineRestoreUseCase(state.repo,state.case)
    seen = state.repo.timeline_records.get_timeline_version('ws',a.task.current_timeline_version_id)
    versions_before = len(state.repo.timeline_records.list_timeline_versions('ws',state.task.id))
    with pytest.raises(EditConflict,match='普通撤销'):
        case.execute(TimelineRestoreCommand('ws',b.task,False),actor=actor,request_id='stale-default-undo',
            expected_timeline_version_id=seen.id,expected_timeline_sequence=seen.sequence)
    assert state.repo.task_records.get_creation_task('ws',state.task.id).current_timeline_version_id==b.task.current_timeline_version_id
    assert len(state.repo.timeline_records.list_timeline_versions('ws',state.task.id))==versions_before
    for redo,text in ((False,'原字幕'),(True,'更新后的字幕')):
        task = state.repo.task_records.get_creation_task('ws',state.task.id)
        version = state.repo.timeline_records.get_timeline_version('ws',task.current_timeline_version_id)
        outcome = case.execute(TimelineRestoreCommand('ws',task,redo),actor=actor,
            request_id='subtitle-redo' if redo else 'subtitle-undo',target_operation_id=a.operation.id,
            expected_timeline_version_id=version.id,expected_timeline_sequence=version.sequence)
        assert outcome.montage.clips[0].crop_x==.3 and outcome.montage.clips[0].crop_y==.7
        tracks = state.repo.subtitle_records.list_subtitle_tracks('ws',task.id,timeline_version_id=outcome.task.current_timeline_version_id)
        assert tracks[0].cues[0].text==text
        assert state.store.resolve_file(tracks[0].artifact_ref).is_file()
        assert outcome.execution_readiness==outcome.restore_request['execution_readiness']
        original_readiness=dict(outcome.execution_readiness)
        assert case.read_request('ws',task.id,outcome.restore_request['request_id'],actor)['execution_readiness']==original_readiness
        replay=case.execute(TimelineRestoreCommand('ws',task,redo),actor=actor,
            request_id=outcome.restore_request['request_id'],target_operation_id=a.operation.id,
            expected_timeline_version_id=version.id,expected_timeline_sequence=version.sequence)
        assert replay.execution_readiness==original_readiness and replay.operation.id==outcome.operation.id
    changed = state.case.save(outcome.task, metadata_edit_command(outcome.task,state.identity,'UpdateSubtitleCue',
        {'base_version_id':outcome.task.current_timeline_version_id,'cue_id':'cue','text':'另一次字幕修改'}))
    version = state.repo.timeline_records.get_timeline_version('ws',changed.task.current_timeline_version_id)
    with pytest.raises(EditConflict,match='修改字段|变化'):
        case.execute(TimelineRestoreCommand('ws',changed.task,False),actor=actor,request_id='conflicting-subtitle-undo',
            target_operation_id=a.operation.id,expected_timeline_version_id=version.id,expected_timeline_sequence=version.sequence)
    assert state.repo.task_records.get_creation_task('ws',state.task.id).current_timeline_version_id==version.id


def test_metadata_save_and_staged_request_replay_recover_original_version(state):
    request_id = uuid4()
    inputs = {'operation_type': 'ChangeCrop', 'crop_x': .3}
    event_id, replay = state.case.begin(state.task, state.member.member_id, request_id, inputs)
    assert replay is None
    command = crop_command(state)
    source = {'workspace_id': state.task.workspace_id, 'task_id': state.task.id,
        'member_id': state.member.member_id, 'request_id': str(request_id),
        'expected_timeline_version_id': state.base.id, 'expected_timeline_sequence': state.base.sequence,
        'action': {'operation_type': command.operation_type,
            **{key: value for key, value in command.values.items() if key != 'base_version_id'}},
        'source_facts': EditSourceVerifier(state.repo, identity=state.identity).freeze(command)}
    result = state.case.save(state.task, replace(command, source=source))
    projection = json.loads(json.dumps({'operation': asdict(result.operation), 'montage_id': result.montage.id},
        default=lambda value: value.isoformat()))
    state.case.record_result(state.task, event_id, status_code=200, response=projection)
    current = state.repo.task_records.get_creation_task('ws', state.task.id)
    _, replay = state.case.begin(current, state.member.member_id, request_id, inputs)
    assert replay.recorded_projection['response'] == projection
    original_claim = state.repo.execution_records.get_task_event(current.workspace_id, event_id)
    original_identity = original_claim.payload
    assert original_claim.task_id == current.id
    assert original_identity['member_id'] == state.member.member_id
    assert original_identity['request_id'] == str(request_id)
    guarded_replays = []
    def reject_known_request(connection):
        assert connection.in_transaction
        row = connection.execute('SELECT id,payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND id=?',
            (current.workspace_id, current.id, original_claim.id)).fetchone()
        assert row is not None and row['id'] == event_id
        sealed = json.loads(row['payload_json'])
        assert (sealed['member_id'], sealed['request_id'], sealed['phase']) == (
            original_identity['member_id'], original_identity['request_id'], original_identity['phase'])
        guarded_replays.append(row['id'])
        raise PermissionError('original request guard refused replay')
    before_guard = editorial_rows(state.repo)
    with pytest.raises(PermissionError, match='guard refused replay'):
        state.repo.execution_records.claim_edit_request(current, original_identity['member_id'],
            original_identity['request_id'], original_identity['phase'], inputs, write_guard=reject_known_request)
    assert guarded_replays == [event_id]
    assert editorial_rows(state.repo) == before_guard
    with pytest.raises(ValueError, match='不同编辑输入'):
        state.case.begin(current, state.member.member_id, request_id, {**inputs, 'crop_x': .9})
    with state.repo._connect() as conn:
        conn.execute('DELETE FROM task_events WHERE id=?', (event_id + '_result',))
    _, recovered = state.case.begin(current, state.member.member_id, request_id, inputs)
    assert recovered.recorded_projection is None
    assert recovered.result.operation == result.operation
    assert recovered.result.montage.clips[0].crop_x == .3
    assert state.repo.timeline_records.get_timeline_version('ws', state.base.id).snapshot == state.base.snapshot
    assert current.narration_cache_key == state.narration.cache_key
    assert result.montage.narration_audio_path == state.plan.narration_audio_path
    assert len(state.repo.edit_records.list_edit_operations('ws', state.task.id)) == 1


@pytest.mark.parametrize('field', ['member', 'workspace', 'task'])
def test_metadata_command_refuses_foreign_identity_without_any_writes(state, field):
    command = crop_command(state)
    if field == 'member':
        command = replace(command, actor_identity={'member_id': 'other', 'local_diagnostic': False})
    else:
        command = replace(command, **{field + '_id': 'other'})
    before = editorial_rows(state.repo)
    with pytest.raises(PermissionError):
        state.case.save(state.task, command)
    assert editorial_rows(state.repo) == before


@pytest.mark.parametrize('mutation', ['source', 'version'])
def test_metadata_commit_rechecks_real_source_and_version(state, monkeypatch, mutation):
    original = state.repo.edit_adoption.confirm_direct
    def mutate(command, intent, proposal, change, **kwargs):
        if kwargs.get('connection') is None:
            if mutation == 'source':
                state.source.write_bytes(b'changed source identity')
            else:
                state.repo.timeline_records.create_timeline_version(replace(state.base, id='concurrent', sequence=2,
                    parent_version_id=state.base.id))
        return original(command, intent, proposal, change, **kwargs)
    monkeypatch.setattr(state.repo.edit_adoption, 'confirm_direct', mutate)
    with pytest.raises(ValueError):
        state.case.save(state.task, crop_command(state))
    assert state.repo.edit_records.list_edit_operations('ws', state.task.id) == []
    assert state.repo.edit_records.list_edit_proposals('ws', state.task.id) == []
    assert state.repo.task_records.get_creation_task('ws', state.task.id).current_timeline_version_id == (
        'concurrent' if mutation == 'version' else state.base.id)


def test_metadata_supplied_connection_does_not_commit_or_open_a_second_writer(state):
    with state.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        operation = state.case.save(state.task, crop_command(state), connection=conn)
        assert conn.execute('SELECT id FROM edit_operations').fetchone()[0] == operation.id
        assert state.repo.task_records.get_creation_task('ws', state.task.id).current_timeline_version_id == state.base.id
        conn.rollback()
    assert state.repo.edit_records.list_edit_operations('ws', state.task.id) == []
    assert state.repo.edit_records.list_edit_proposals('ws', state.task.id) == []
    assert state.repo.task_records.get_creation_task('ws', state.task.id).current_timeline_version_id == state.base.id


def test_metadata_late_write_failure_rolls_back_change_but_keeps_prior_claim(state):
    event_id, _ = state.case.begin(state.task, state.member.member_id, uuid4(), {'operation_type': 'ChangeCrop'})
    with state.repo._connect() as conn:
        conn.execute("CREATE TRIGGER reject_operation BEFORE INSERT ON edit_operations BEGIN SELECT RAISE(ABORT, 'second write'); END")
    before = editorial_rows(state.repo)
    with pytest.raises(sqlite3.IntegrityError, match='second write'):
        state.case.save(state.task, crop_command(state))
    assert editorial_rows(state.repo) == before
    assert state.repo.execution_records.get_task_event('ws', event_id).event_type == 'edit_request_claimed'
    assert state.repo.execution_records.read_edit_request_result(state.task, event_id) is None


def test_public_reorder_proposal_confirms_once_through_metadata_owner(tmp_path):
    from app.creation_execution import CreationExecutionActor
    from app.schemas import CreationTaskEditPreviewRequest

    state = metadata_state(tmp_path)
    first = replace(state.plan.clips[0], source_end_seconds=2, timeline_end_seconds=2)
    second = replace(state.plan.clips[0], id='clip-second', source_start_seconds=2,
        timeline_start_seconds=2)
    state.plan.clips = [first, second]
    payload = creation_task_montage_plan_to_payload(state.plan)
    state.repo.montage_plan_adoption.save_creation_task_montage_plan('ws', state.task.id, state.plan.id, payload)
    base = TimelineVersion('two-clips-base', 'ws', state.task.id, state.base.id, 2,
        {**state.base.snapshot, 'montage': payload, 'subtitle_track_ids': ['two-clips-zh']}, None)
    state.repo.timeline_records.create_timeline_version(base)
    state.repo.subtitle_records.create_subtitle_track(replace(state.track, id='two-clips-zh', timeline_version_id=base.id))
    task = state.repo.task_records.get_creation_task('ws', state.task.id)
    preview = CreationTaskEditPreviewRequest(raw_command='交换第1和第2个镜头', context_stage='montage',
        selected_object_refs=['clip:clip', 'clip:clip-second'])
    _, proposal = state.case.preview_reorder(task, preview, ('swap', 0, 1), identity=state.identity,
        request_source=None, draft_only=True, execution=None,
        actor=CreationExecutionActor('ws', state.member.member_id))
    assert proposal.operation['operation_type'] == 'ReorderTimelineItems'
    before = len(state.repo.timeline_records.list_timeline_versions('ws', task.id))
    result = state.case.confirm(task, proposal, identity=state.identity, reader_identity=state.identity, source=None)
    reopened = ProductionRepository(state.repo.database_path, tmp_path / 'library')
    versions = reopened.timeline_records.list_timeline_versions('ws', task.id)
    operations = reopened.edit_records.list_edit_operations('ws', task.id)
    current = reopened.task_records.get_creation_task('ws', task.id)
    assert len(versions) == before + 1 and len(operations) == 1
    assert operations[0].proposal_id == proposal.id and operations[0].result_version_id == current.current_timeline_version_id
    assert reopened.edit_records.get_edit_proposal('ws', proposal.id).status == 'confirmed'
    assert [clip.id for clip in result.montage.clips] == ['clip-second', 'clip']
    assert result.montage.duration_seconds == 4
    assert result.montage.narration_audio_path == state.plan.narration_audio_path
    version = reopened.timeline_records.get_timeline_version('ws', current.current_timeline_version_id)
    assert version.parent_version_id == base.id and version.sequence == 3
    assert [clip['id'] for clip in version.snapshot['montage']['clips']] == ['clip-second', 'clip']
    assert reopened.timeline_records.get_timeline_version('ws', base.id).snapshot == base.snapshot
    assert reopened.get_creation_task_montage_plan('ws', task.id)['clips'][0]['id'] == 'clip-second'
    assert len(reopened.edit_records.list_edit_operations('ws', task.id)) == 1
    evidence = os.environ.get('METADATA_CONFIRM_EVIDENCE_DIR')
    if evidence:
        Path(evidence, 'NORMAL_FACTS.json').write_text(json.dumps({'proposal': proposal.id,
            'operation_type': proposal.operation['operation_type'], 'operation': operations[0].id,
            'base_version': base.id, 'result_version': current.current_timeline_version_id,
            'versions_added': 1, 'operations': 1, 'fresh_readback': True, 'native_calls': 0}, indent=2))


@pytest.mark.parametrize('reuse_output',[False,True])
def test_task_authorization_render_outputs_are_not_input_grants(state,reuse_output):
    from datetime import timedelta
    from app.audio_timeline import AudioTimeline,TimelineAudioTrack,TimelineAudioClip
    from app.creation.contracts import TaskAuthorization
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.creation_execution import CreationExecutionActor
    now=datetime.now(timezone.utc)
    actor=CreationExecutionActor('ws',state.member.member_id)
    service=TaskAuthorizationUseCase(state.repo,lambda _:'ready',clock=lambda:now)
    _,scope=service._requirements('ws',state.task.id)
    authorization=TaskAuthorization('render-input-authority','ws',state.task.id,service.identity(actor),
        state.repo.task_authorization_records.task_authorization_goal_revision('ws',state.task.id),('task_media',),scope,now,now,now+timedelta(hours=1))
    state.repo.task_authorization_records.save_task_authorization(authorization,expected_revision=None)
    montage=dict(state.repo.get_creation_task_montage_plan('ws',state.task.id))
    output='artifacts/task/final-render/preview.mp4'
    sidecar='artifacts/task/final-render/subtitles.srt'
    montage.update(output_path=output,subtitle_path=sidecar)
    if reuse_output:
        montage['audio_timeline']=AudioTimeline(tracks=[TimelineAudioTrack(id='reused-output',name='原声',role='original',
            clips=[TimelineAudioClip(id='reused-output-audio',name='产物原声',source_ref=output,
                source_start_ms=0,source_end_ms=1000,timeline_start_ms=0)])]).model_dump(mode='json')
    state.repo.montage_plan_adoption.save_creation_task_montage_plan('ws',state.task.id,montage['id'],montage)
    _,required=service._requirements('ws',state.task.id)
    assert (output in required['references']) is reuse_output
    assert sidecar not in required['references']
    assert service.status('ws',state.task.id,identity=service.identity(actor),operation='task_media')==(
        'outside_scope' if reuse_output else 'valid')
    assert state.repo.task_authorization_records.get_task_authorization('ws',state.task.id)==authorization


def test_queued_audio_preview_prepares_sibling_base_after_identity(tmp_path, monkeypatch):
    import threading
    import wave
    import httpx
    from PIL import Image
    from app.audio_timeline import AudioTimeline
    from app.creation.editing.audio_edit_use_case import AudioEditUseCase
    from app.creation_execution import CreationExecutionActor
    from app.device_runtime import DeviceRuntime

    def forbidden(*args, **kwargs):
        pytest.fail('queued preview metadata forbids audio, runtime, media and external transport')

    for owner, name in ((subprocess, 'Popen'), (threading.Thread, 'start'), (wave, 'open'),
            (Image, 'open'), (Image, 'new'), (Image.Image, 'save'),
            (httpx.HTTPTransport, 'handle_request'), (httpx.AsyncHTTPTransport, 'handle_async_request'),
            (DeviceRuntime, 'start'), (DeviceRuntime, 'run_once'), (DeviceRuntime, 'wake'),
            (TaskAudio, 'source'), (TaskAudio, 'publish'), (TaskAudio, 'probe')):
        monkeypatch.setattr(owner, name, forbidden)
    s = metadata_state(tmp_path)
    original_task = s.repo.task_records.get_creation_task('ws', s.task.id)
    original_base = s.repo.timeline_records.get_timeline_version('ws', s.base.id)
    original_montage = s.repo.get_creation_task_montage_plan('ws', s.task.id)
    sibling_plan = replace(s.repo.plan_records.get_creation_plan('ws', s.task.plan_id), id='queued-audio-plan')
    sibling = CreationTask('queued-audio-task', 'ws', sibling_plan.id, 'metadata', 'knowledge_explanation',
        stage='montage', owner_user_id=s.member.member_id, visibility='private')
    s.repo.plan_confirmation.confirm_creation_plan(sibling_plan, sibling,
        verified_owner_user_id=s.member.member_id)
    with s.repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?',
            (json.dumps(asdict(sibling), default=str), sibling.id))
    montage = replace(s.plan, id='queued-audio-montage', task_id=sibling.id,
        cues=[replace(cue, narration_segment_id=None) for cue in s.plan.cues],
        narration_audio_path=None, audio_timeline=AudioTimeline(tracks=[]))
    s.repo.montage_plan_adoption.save_creation_task_montage_plan('ws', sibling.id, montage.id,
        creation_task_montage_plan_to_payload(montage))
    sibling = s.repo.task_records.get_creation_task('ws', sibling.id)
    assert sibling.current_timeline_version_id is None and sibling.narration_cache_key is None
    assert s.repo.timeline_records.list_timeline_versions('ws', sibling.id) == []
    owner = AudioEditUseCase(s.repo, TaskAudio(s.repo, s.store, tmp_path / 'queued-temporary'), s.case, 'interactive')
    actor = CreationExecutionActor('ws', s.member.member_id)
    values = {'payload': {'declared': 'unchanged'}, '_media_kind': 'declared-required-kind', '_draft': True}
    order = []
    verdict = 'unauthorized'

    def facts(workspace_id, task_id, operation, actual, identity):
        assert (workspace_id, task_id, operation) == ('ws', sibling.id, 'audio_edit_preview')
        assert actual is values and identity is actor
        order.append('facts')

    def verify_identity(identity):
        assert identity is actor
        order.append('identity')
        return verdict

    real_base = s.case.ensure_montage_base

    def observe_base(*args, **kwargs):
        order.append('base')
        assert kwargs == {}
        return real_base(*args, **kwargs)

    monkeypatch.setattr(s.case, 'ensure_montage_base', observe_base)
    before = s.repo.task_records.get_creation_task('ws', sibling.id)
    with pytest.raises(PermissionError, match='媒体处理身份失效或无法复核'):
        owner.prepare_queued_preview(sibling, actor, values, facts=facts, verify_identity=verify_identity)
    assert order == ['facts', 'identity'] and values['payload'] == {'declared': 'unchanged'}
    assert s.repo.task_records.get_creation_task('ws', sibling.id) == before
    assert s.repo.timeline_records.list_timeline_versions('ws', sibling.id) == []
    verdict = 'ready'
    prepared = owner.prepare_queued_preview(sibling, actor, values, facts=facts, verify_identity=verify_identity)
    assert prepared is values and order == ['facts', 'identity', 'facts', 'identity', 'base']
    versions = s.repo.timeline_records.list_timeline_versions('ws', sibling.id)
    assert len(versions) == 1
    version = versions[0]
    assert version.sequence == 1 and version.parent_version_id is None
    assert s.repo.task_records.get_creation_task('ws', sibling.id).current_timeline_version_id == version.id
    assert values['payload'] == {'declared': 'unchanged', 'base_version_id': version.id}
    assert values['_media_kind'] == 'declared-required-kind' and values['_draft'] is True
    assert version.snapshot['montage']['task_id'] == sibling.id
    assert [clip['id'] for clip in version.snapshot['montage']['clips']] == ['clip']
    assert version.snapshot['montage']['audio_timeline']['tracks'] == []
    assert s.repo.task_records.get_creation_task('ws', s.task.id) == original_task
    assert s.repo.timeline_records.get_timeline_version('ws', s.base.id) == original_base
    assert s.repo.get_creation_task_montage_plan('ws', s.task.id) == original_montage
    with s.repo._connect() as conn:
        assert conn.execute('SELECT count(*) FROM job_runs').fetchone()[0] == 0


def test_montage_plan_adoption_metadata_preserves_cas_and_transaction_ownership(tmp_path, monkeypatch):
    import sqlite3
    import threading
    import wave
    import httpx
    from PIL import Image
    from app.device_runtime import DeviceRuntime

    violations = []

    def forbidden(*args, **kwargs):
        violations.append('execution')
        pytest.fail('montage adoption metadata forbids runtime, media and external transport')

    for owner, name in ((subprocess, 'Popen'), (threading.Thread, 'start'), (wave, 'open'),
            (Image, 'open'), (Image, 'new'), (Image.Image, 'save'),
            (httpx.HTTPTransport, 'handle_request'), (httpx.AsyncHTTPTransport, 'handle_async_request'),
            (DeviceRuntime, 'start'), (DeviceRuntime, 'run_once'), (DeviceRuntime, 'wake'),
            (TaskAudio, 'source'), (TaskAudio, 'publish'), (TaskAudio, 'probe')):
        monkeypatch.setattr(owner, name, forbidden)
    s = metadata_state(tmp_path)
    owner = s.repo.montage_plan_adoption
    initial_task = s.repo.task_records.get_creation_task('ws', s.task.id)
    assert initial_task.current_timeline_version_id == s.base.id
    payload = {**s.repo.get_creation_task_montage_plan('ws', s.task.id),
        'id': 'transaction-plan', 'status': 'rendering'}

    def snapshot(conn):
        return {table: sorted([tuple(row) for row in conn.execute('SELECT * FROM ' + table)], key=repr)
            for table in ('creation_tasks', 'creation_task_montage_plans')}

    def outside():
        with sqlite3.connect(f'file:{s.repo.database_path}?mode=ro', uri=True) as conn:
            return snapshot(conn)

    before = outside()
    verified = []
    verifier_error = PermissionError('declared verifier rejection')

    def verify_inputs(conn, task):
        verified.append((conn, task.id, task.current_timeline_version_id))
        raise verifier_error

    with monkeypatch.context() as provided:
        provided.setattr(owner, '_connect', forbidden)
        provided.setattr(owner, '_read_task', forbidden)
        provided.setattr(owner, '_read_montage', forbidden)
        with pytest.raises(ValueError, match='混剪计划状态无效'):
            owner.save_creation_task_montage_plan('ws', s.task.id, 'transaction-plan',
                {**payload, 'status': 'declared-invalid'})
        with s.repo._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            with pytest.raises(PermissionError) as rejected:
                owner.save_creation_task_montage_plan('ws', s.task.id, 'transaction-plan', payload,
                    connection=conn, expected_current_version_id=None, verify_inputs=verify_inputs)
            assert rejected.value is verifier_error
            assert verified == [(conn, s.task.id, s.base.id)]
            assert conn.in_transaction and snapshot(conn) == before
            with pytest.raises(ValueError, match='当前时间线已变化'):
                owner.save_creation_task_montage_plan('ws', s.task.id, 'transaction-plan', payload,
                    connection=conn, expected_current_version_id=None)
            assert snapshot(conn) == before
            task, adopted = owner.save_creation_task_montage_plan('ws', s.task.id, 'transaction-plan',
                payload, connection=conn, expected_current_version_id=False)
            assert task.montage_plan_id == 'transaction-plan' and task.stage == 'montage'
            assert task.stage_status == 'running' and task.status == 'running' and task.output_ready is False
            assert adopted['status'] == 'rendering' and conn.in_transaction
            assert snapshot(conn) != before and outside() == before
            conn.rollback()
            assert snapshot(conn) == before
    assert outside() == before
    with s.repo._connect() as conn:
        conn.executescript("""CREATE TRIGGER reject_montage_adoption_tail BEFORE UPDATE OF payload ON creation_tasks
            WHEN NEW.id='task' AND json_extract(NEW.payload,'$.montage_plan_id')='transaction-plan'
            AND EXISTS(SELECT 1 FROM creation_task_montage_plans p
                WHERE p.workspace_id=NEW.workspace_id AND p.task_id=NEW.id AND p.id='transaction-plan'
                AND json_extract(p.payload,'$.status')='rendering')
            BEGIN SELECT RAISE(ABORT,'declared montage task-update failure'); END;""")
    with pytest.raises(sqlite3.IntegrityError, match='declared montage task-update failure'):
        owner.save_creation_task_montage_plan('ws', s.task.id, 'transaction-plan', payload)
    assert outside() == before
    assert s.repo.task_records.get_creation_task('ws', s.task.id) == initial_task
    assert s.repo.timeline_records.get_timeline_version('ws', s.base.id) == s.base
    assert violations == []


def test_render_publication_metadata_is_atomic_and_preserves_head_guard(tmp_path, monkeypatch):
    import sqlite3
    import threading
    import wave
    import httpx
    from PIL import Image
    from app.device_runtime import DeviceRuntime

    violations = []

    def forbidden(*args, **kwargs):
        violations.append('execution')
        pytest.fail('render publication metadata forbids runtime, media and external transport')

    for target, name in ((subprocess, 'Popen'), (threading.Thread, 'start'), (wave, 'open'),
            (Image, 'open'), (Image, 'new'), (Image.Image, 'save'),
            (httpx.HTTPTransport, 'handle_request'), (httpx.AsyncHTTPTransport, 'handle_async_request'),
            (DeviceRuntime, 'start'), (DeviceRuntime, 'run_once'), (DeviceRuntime, 'wake'),
            (TaskAudio, 'source'), (TaskAudio, 'publish'), (TaskAudio, 'probe')):
        monkeypatch.setattr(target, name, forbidden)
    s = metadata_state(tmp_path)
    owner = s.repo.montage_plan_adoption
    payload = {**s.repo.get_creation_task_montage_plan('ws', s.task.id),
        'id': 'published-plan', 'status': 'rendered'}
    version = TimelineVersion('published-version', 'ws', s.task.id, s.base.id, s.base.sequence + 1,
        {'montage': payload, 'render_input_timeline_version_id': s.base.id}, None)
    assert owner.publish_creation_task_render('ws', s.task.id, 'published-plan', payload, version,
        expected_current_version_id=s.base.id) is None
    saved = s.repo.get_creation_task_montage_plan('ws', s.task.id)
    task = s.repo.task_records.get_creation_task('ws', s.task.id)
    stored = s.repo.timeline_records.get_timeline_version('ws', version.id)
    assert saved['id'] == task.montage_plan_id == stored.snapshot['montage']['id'] == 'published-plan'
    assert saved['status'] == stored.snapshot['montage']['status'] == 'rendered'
    assert task.current_timeline_version_id == stored.id and task.output_ready is True
    assert task.stage == 'review' and task.stage_status == 'waiting_for_user'
    assert stored.parent_version_id == s.base.id and stored.sequence == s.base.sequence + 1

    def outside():
        with sqlite3.connect(f'file:{s.repo.database_path}?mode=ro', uri=True) as conn:
            return {table: sorted(conn.execute('SELECT * FROM ' + table).fetchall(), key=repr)
                for table in ('creation_tasks', 'creation_task_montage_plans', 'timeline_versions')}

    before = outside()
    with s.repo._connect() as conn:
        conn.executescript("""CREATE TRIGGER reject_render_version BEFORE INSERT ON timeline_versions
            WHEN NEW.id='blocked-version'
            AND EXISTS(SELECT 1 FROM creation_task_montage_plans p
                WHERE p.workspace_id=NEW.workspace_id AND p.task_id=NEW.task_id AND p.id='blocked-plan'
                AND json_extract(p.payload,'$.status')='rendered')
            AND EXISTS(SELECT 1 FROM creation_tasks t WHERE t.workspace_id=NEW.workspace_id AND t.id=NEW.task_id
                AND json_extract(t.payload,'$.montage_plan_id')='blocked-plan'
                AND json_extract(t.payload,'$.output_ready')=1)
            BEGIN SELECT RAISE(ABORT,'declared render version-insert failure'); END;""")
    blocked_payload = {**payload, 'id': 'blocked-plan'}
    blocked_version = TimelineVersion('blocked-version', 'ws', s.task.id, version.id, version.sequence + 1,
        {'montage': blocked_payload, 'render_input_timeline_version_id': version.id}, None)
    with pytest.raises(ValueError, match='时间线版本已存在，不能覆盖') as rejected:
        owner.publish_creation_task_render('ws', s.task.id, 'blocked-plan', blocked_payload, blocked_version,
            expected_current_version_id=version.id)
    assert isinstance(rejected.value.__cause__, sqlite3.IntegrityError)
    assert str(rejected.value.__cause__) == 'declared render version-insert failure'
    assert outside() == before
    assert owner.save_failed_creation_task_render_if_current('ws', s.task.id, 'ignored-plan',
        {'status': 'declared-invalid'}, expected_current_version_id=s.base.id) is None
    assert outside() == before
    failed = {**payload, 'status': 'failed', 'message': 'declared current-head failure'}
    assert owner.save_failed_creation_task_render_if_current('ws', s.task.id, 'published-plan', failed,
        expected_current_version_id=version.id) is None
    task = s.repo.task_records.get_creation_task('ws', s.task.id)
    assert s.repo.get_creation_task_montage_plan('ws', s.task.id)['status'] == 'failed'
    assert task.current_timeline_version_id == version.id and task.output_ready is False
    assert task.stage == 'montage' and task.stage_status == 'failed' and task.stage_reason == 'render_failed'
    assert len(s.repo.timeline_records.list_timeline_versions('ws', s.task.id)) == 2
    assert violations == []


def test_workspace_response_http_read_and_agent_port_share_real_metadata(tmp_path, monkeypatch):
    import sqlite3
    import threading
    import wave
    import httpx
    from PIL import Image
    from starlette.requests import Request
    import app.main as main
    from app.assistant.task_business import TaskBusiness
    from app.device_runtime import DeviceRuntime
    from app.schemas import CreationTaskWorkspaceResponse

    violations = []

    def forbidden(*args, **kwargs):
        violations.append('execution')
        pytest.fail('workspace metadata forbids runtime, media and external transport')

    for target, name in ((subprocess, 'Popen'), (threading.Thread, 'start'), (wave, 'open'),
            (Image, 'open'), (Image, 'new'), (Image.Image, 'save'),
            (httpx.HTTPTransport, 'handle_request'), (httpx.AsyncHTTPTransport, 'handle_async_request'),
            (DeviceRuntime, 'start'), (DeviceRuntime, 'run_once'), (DeviceRuntime, 'wake'),
            (TaskAudio, 'source'), (TaskAudio, 'publish'), (TaskAudio, 'probe')):
        monkeypatch.setattr(target, name, forbidden)
    s = metadata_state(tmp_path)
    monkeypatch.setattr(main, 'production_repo', s.repo)
    monkeypatch.setattr(main, 'artifact_store', s.store)
    monkeypatch.setattr(main, 'current_workspace_id', lambda _: 'ws')
    monkeypatch.setattr(main, '_content_reader', lambda *_: (s.member.member_id, False))
    monkeypatch.setattr(main, '_metadata_edit_use_case', lambda: s.case)
    monkeypatch.setattr(main, '_lipsync_provider', lambda *_: None)
    monkeypatch.setattr(main, 'subtitle_translation_provider_from_settings', lambda _: None)
    monkeypatch.setattr(main, '_portrait_processor', lambda _: SimpleNamespace(available=False))
    for name in ('_creation_runtime', '_start_device_runtime', '_creation_execution_control',
                 '_segment_production_use_case', '_native_task_operations_case', '_timeline_restore_use_case'):
        monkeypatch.setattr(main, name, forbidden)
    business = main._task_business(generation_case=SimpleNamespace())
    assert isinstance(business, TaskBusiness)
    real_reader = main._task_workspace_read_use_case
    readers = []

    def current_reader():
        reader = real_reader()
        readers.append(reader)
        return reader

    monkeypatch.setattr(main, '_task_workspace_read_use_case', current_reader)
    route = next(route for route in main._task_workspace_http_router(('get_creation_task_workspace',)).routes
        if route.name == 'get_creation_task_workspace')
    request = Request({'type': 'http', 'method': 'GET', 'path': f'/api/creation-tasks/{s.task.id}',
        'headers': [], 'query_string': b'', 'state': {}})

    def snapshot():
        with sqlite3.connect(f'file:{s.repo.database_path}?mode=ro', uri=True) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {table: sorted(conn.execute('SELECT * FROM "' + table.replace('"', '""') + '"').fetchall(), key=repr)
                for table in tables}

    before = snapshot()
    http_response = route.endpoint(s.task.id, request)
    task = s.repo.task_records.get_creation_task('ws', s.task.id)
    agent_response = business.workspace_facts('ws', task)
    assert isinstance(http_response, CreationTaskWorkspaceResponse)
    assert isinstance(agent_response, CreationTaskWorkspaceResponse)
    http_values = http_response.model_dump(mode='json')
    assert CreationTaskWorkspaceResponse.model_validate(http_values).model_dump(mode='json') == agent_response.model_dump(mode='json')
    assert http_values['task']['id'] == s.task.id and http_values['montage']['id'] == s.plan.id
    assert http_values['subtitle_tracks'][0]['id'] == s.track.id
    assert http_values['portrait_available'] is False
    assert len(readers) == 2 and readers[0] is not readers[1]
    assert snapshot() == before and violations == []


def _section_adoption_metadata_state(tmp_path):
    from app.creation.contracts import ArtifactRecord, JobRun, NarrationSegment, TaskEvent, TransformRun
    from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
    from app.creation.section_change_use_case import SectionTimelineChange
    from app.creation_execution import CreationExecutionActor
    from app.creation.editing.metadata_use_case import execution_subtitle_track
    from app.subtitle_tracks import render_srt

    s = metadata_state(tmp_path)
    repo, task = s.repo, s.task
    script = CreationTaskScript(id='fixed-script', workspace_id='ws', task_id=task.id,
        run_id='metadata-script-run', topic_id='metadata-topic', title='声明段落', language='zh-CN',
        sections=[CreationTaskScriptSection(id='section', title='目标段', content='目标原文',
            visual_prompt='声明来源', evidence_reference_ids=[]),
            CreationTaskScriptSection(id='later-section', title='另一段', content='后段原文',
                visual_prompt='声明来源', evidence_reference_ids=[])], selection_status='confirmed',
        confirmed_at=task.created_at)
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        payload = repo.script_records._script_payload(conn, script)
        conn.execute('INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at) VALUES (?,?,?,?,?,?)',
            (script.id, 'ws', task.id, script.run_id, payload, script.updated_at.isoformat()))
        task.confirmed_script_id = script.id
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(asdict(task), default=str), task.id))
    job = repo.job_records.create_job_run(JobRun('metadata-section-job', 'ws', task.id, 'narration',
        'metadata-section-job', 'declared-section-input'))
    repo.job_records.claim_job_run('ws', job.id)
    rows = [NarrationSegment('section-audio', 'ws', task.id, script.id, 'section', '目标原文',
        'narration://artifact/section-original', 2, 24000, 'metadata', 'metadata-voice', 'v1', 'zh', 'original', 'ready'),
        NarrationSegment('later-audio', 'ws', task.id, script.id, 'later-section', '后段原文',
        'narration://artifact/later-original', 2, 24000, 'metadata', 'metadata-voice', 'v1', 'zh', 'original', 'ready')]
    repo.narration_records.save_narration_segments('ws', task.id, script.id, rows)
    montage = replace(s.plan, cues=[NarrationCue('cue', 'section', '目标原文', 2, narration_segment_id='section-audio'),
        NarrationCue('later-cue', 'later-section', '后段原文', 2, narration_segment_id='later-audio')],
        clips=[replace(s.plan.clips[0], source_end_seconds=2, timeline_end_seconds=2),
            replace(s.plan.clips[0], id='later-clip', cue_id='later-cue', source_start_seconds=2,
                timeline_start_seconds=2)], narration_audio_path='narration://artifact/declared-composition')
    full = creation_task_montage_plan_to_payload(montage)
    local, _ = SectionTimelineChange.local_state(full, [], 'section')
    repo.execution_records.create_artifact_record(ArtifactRecord('section-video', 'ws', task.id, 'video',
        'video://artifact/declared-section', 'a'*64, 1, job.id, []))
    repo.execution_records.append_task_event(TaskEvent('section-original', 'ws', task.id, job.id, 'segment_publication',
        {'section_id': 'section', 'script_id': script.id, 'text': rows[0].text, 'adopted': True,
         'video_artifact_id': 'section-video', 'duration_seconds': 2, 'narration_reference': rows[0].audio_reference,
         'montage': local}))
    snapshot = {**s.base.snapshot, 'montage': full, 'stage_segments': {'section': 'section-original'}}
    base = TimelineVersion('section-base', 'ws', task.id, s.base.id, 2, snapshot, None)
    repo.timeline_records.create_timeline_version(base, expected_current_version_id=s.base.id)
    repo.montage_plan_adoption.save_creation_task_montage_plan('ws', task.id, montage.id, full)
    track = replace(s.track, id='section-base-zh', timeline_version_id=base.id,
        cues=[SubtitleCue('cue', None, '目标原文', 0, 2), SubtitleCue('later-cue', None, '后段原文', 2, 4)])
    reference, transform = s.case.publish_subtitle_text(workspace_id='ws', task_id=task.id, proposal_id='section-before',
        kind='subtitle_cue_update', input_refs=['timeline:section-base'],
        content=render_srt(execution_subtitle_track(track)), engine='subtitle-srt/v1', parameters={})
    repo.subtitle_records.create_subtitle_track(replace(track, artifact_ref=reference, transform_id=transform))
    replacement = replace(rows[0], text='目标修改后的正文', audio_reference='narration://artifact/section-replacement',
        duration_seconds=3, updated_at=datetime.now(timezone.utc))
    local = json.loads(json.dumps(local))
    local.update(duration_seconds=3, narration_audio_path=replacement.audio_reference, audio_timeline=None)
    local['cues'][0].update(text=replacement.text, duration_seconds=3)
    local['clips'][0].update(source_end_seconds=3, timeline_end_seconds=3)
    publication = TaskEvent('section-published', 'ws', task.id, job.id, 'segment_publication',
        {'section_id': 'section', 'script_id': script.id, 'text': replacement.text,
         'narration_reference': replacement.audio_reference, 'duration_seconds': 3, 'montage': local,
         'video_artifact_id': 'section-video', 'request_id': 'declared-revision', 'input_id': 'declared-input', 'adopted': True})
    now = datetime.now(timezone.utc)
    transform = TransformRun('section-transform', 'ws', task.id, job.id, 'segment_render', 'declared-input', [],
        ['video://artifact/declared-section'], 'metadata', {}, 'succeeded', now, now)
    return SimpleNamespace(**vars(s), section_base=base, original_segments=rows, before=rows[0], replacement=replacement,
        local=local, publication=publication, publication_transform=transform, job=job,
        change=SectionTimelineChange(repo, s.store), actor=CreationExecutionActor('ws', s.member.member_id))


def _publish_section_metadata(s, conn):
    return s.repo.section_adoption.publish_segment_version(s.publication, [], s.publication_transform, [],
        adopted=True, expected_version_id=s.section_base.id, expected_segment_version='section-original',
        segment=s.replacement, timeline_change=s.change, connection=conn)


def test_section_adoption_metadata_borrows_transaction_and_keeps_dynamic_late_guard(tmp_path, monkeypatch):
    s = _section_adoption_metadata_state(tmp_path)
    s.change.prepare_current('ws', s.task.id, 'section', s.local, s.replacement, s.job)
    with s.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='发布前'):
            s.repo.section_adoption.publish_segment_version(s.publication, [], s.publication_transform, [],
                adopted=True, expected_version_id=s.section_base.id, expected_segment_version='changed-target',
                segment=s.replacement, timeline_change=s.change, connection=conn)
        assert conn.in_transaction
        conn.rollback()
    with s.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert _publish_section_metadata(s, conn) == s.publication.id
        assert conn.in_transaction
        conn.rollback()
    assert s.repo.task_records.get_creation_task('ws', s.task.id).current_timeline_version_id == s.section_base.id
    assert s.repo.narration_records.get_narration_segment('ws', s.task.id, s.before.id) == s.before
    with pytest.raises(KeyError):
        s.repo.execution_records.get_task_event('ws', s.publication.id)
    with s.repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM edit_operations WHERE operation_type='ReviseScriptSection'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM transform_runs WHERE id='section-transform'").fetchone()[0] == 0
    original = s.repo.replace_adopted_narration_segment
    called = []
    def revoked(*args, **kwargs):
        original(*args, **kwargs)
        called.append(kwargs['connection'].in_transaction)
        raise PermissionError('metadata authorization revoked at the real replace port')
    with monkeypatch.context() as guard:
        guard.setattr(s.repo, 'replace_adopted_narration_segment', revoked)
        with pytest.raises(PermissionError, match='real replace port'):
            with s.repo._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                _publish_section_metadata(s, conn)
    assert called == [True]
    assert s.repo.task_records.get_creation_task('ws', s.task.id).current_timeline_version_id == s.section_base.id
    assert s.repo.narration_records.get_narration_segment('ws', s.task.id, s.before.id) == s.before
    with pytest.raises(KeyError):
        s.repo.execution_records.get_task_event('ws', s.publication.id)


def test_section_adoption_metadata_restore_tail_rollback_and_original_request_replay(tmp_path, monkeypatch):
    from app.creation.contracts import JobRun
    from app.creation.section_change_use_case import SectionTimelineChange
    from app.creation.editing.timeline_restore_use_case import TimelineRestoreUseCase, TimelineRestoreCommand
    from app.device_scheduler import RecoveryBlocked

    s = _section_adoption_metadata_state(tmp_path)
    repo = s.repo
    s.change.prepare_current('ws', s.task.id, 'section', s.local, s.replacement, s.job)
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _publish_section_metadata(s, conn)
    task = repo.task_records.get_creation_task('ws', s.task.id)
    published = repo.timeline_records.get_timeline_version('ws', task.current_timeline_version_id)
    operation = repo.edit_records.get_edit_operation('ws', published.created_by_operation_id)
    assert len(published.snapshot['narration_segments']) == 2
    assert repo.narration_records.get_narration_segment('ws', task.id, s.original_segments[1].id) == s.original_segments[1]
    snapshot = json.loads(json.dumps(published.snapshot))
    snapshot['montage']['clips'][-1]['crop_y'] = .31
    later = TimelineVersion('section-later', 'ws', task.id, published.id, published.sequence+1, snapshot, None)
    repo.timeline_records.create_timeline_version(later, expected_current_version_id=published.id)
    for track in repo.subtitle_records.list_subtitle_tracks('ws', task.id, timeline_version_id=published.id):
        repo.subtitle_records.create_subtitle_track(replace(track, id=track.id+'_later', timeline_version_id=later.id))
    job = repo.job_records.create_job_run(JobRun('metadata-tail-job', 'ws', task.id, 'section_timeline_restore',
        'metadata-tail-job', 'declared-restore-input'))
    repo.job_records.claim_job_run('ws', job.id)
    change = SectionTimelineChange(repo, s.store)
    change.prepare_current('ws', task.id, 'section', s.local, s.before, job, desired_version=s.section_base)
    dependencies = repo.section_inputs.section_dependency_snapshot('ws', task.id, 'section')
    with pytest.raises(ValueError, match='实际音画、字幕或来源依赖已变化'):
        repo.section_adoption.restore_script_section('ws', task.id, history=operation.payload,
            expected_dependencies={}, redo=False, changed_version_id=published.id,
            restore_version_id=s.section_base.id, timeline_change=change, job=job)
    assert repo.task_records.get_creation_task('ws', task.id).current_timeline_version_id == later.id
    finish = repo.job_records.finish_job_run
    reached = []
    def tail_failure(*args, **kwargs):
        finish(*args, **kwargs)
        reached.append(kwargs['connection'].in_transaction)
        raise RuntimeError('metadata restore failed after the real job success write')
    with monkeypatch.context() as guard:
        guard.setattr(repo.job_records, 'finish_job_run', tail_failure)
        with pytest.raises(RuntimeError, match='real job success'):
            repo.section_adoption.restore_script_section('ws', task.id, history=operation.payload,
                expected_dependencies=dependencies, redo=False, changed_version_id=published.id,
                restore_version_id=s.section_base.id, timeline_change=change, job=job)
    assert reached == [True]
    assert repo.task_records.get_creation_task('ws', task.id).current_timeline_version_id == later.id
    assert repo.narration_records.get_narration_segment('ws', task.id, s.before.id) == s.replacement
    assert repo.job_records.get_job_run('ws', job.id).status == 'running'
    with repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM edit_operations WHERE operation_type='UndoTimelineVersion'").fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM task_events WHERE job_id=?', (job.id,)).fetchone()[0] == 0
    restore = TimelineRestoreUseCase(repo, s.case)
    for redo, expected in [(False, s.before), (True, s.replacement)]:
        current_task = repo.task_records.get_creation_task('ws', task.id)
        current = repo.timeline_records.get_timeline_version('ws', current_task.current_timeline_version_id)
        request_id = 'metadata-redo' if redo else 'metadata-undo'
        args = dict(actor=s.actor, request_id=request_id, target_operation_id=operation.id,
            expected_timeline_version_id=current.id, expected_timeline_sequence=current.sequence)
        command = TimelineRestoreCommand('ws', current_task, redo)
        result = restore.execute(command, **args)
        replay = restore.execute(command, **args)
        assert result.operation.id == replay.operation.id
        assert result.montage.clips[-1].crop_y == .31
        assert repo.narration_records.get_narration_segment('ws', task.id, s.before.id).text == expected.text
        assert repo.narration_records.get_narration_segment('ws', task.id, s.original_segments[1].id) == s.original_segments[1]
        assert result.task.output_ready is False and result.task.narration_cache_key is None
    current_task = repo.task_records.get_creation_task('ws', task.id)
    inputs = {'action': 'undo', 'target_operation_id': None, 'expected_timeline_version_id': None,
        'expected_timeline_sequence': None, 'source': None, 'actor_identity': s.actor.identity_payload()}
    repo.restore_requests.claim(current_task, s.actor.member_id, 'metadata-unknown', inputs)
    with repo._connect() as conn:
        before_jobs = conn.execute('SELECT COUNT(*) FROM job_runs').fetchone()[0]
    with pytest.raises(RecoveryBlocked, match='未知'):
        restore.execute(TimelineRestoreCommand('ws', current_task, False), actor=s.actor, request_id='metadata-unknown')
    assert restore.read_request('ws', task.id, 'metadata-unknown', s.actor)['status'] == 'result_unknown'
    with repo._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM job_runs').fetchone()[0] == before_jobs


def test_section_input_reader_keeps_snapshot_stream_and_caller_lifetimes(tmp_path):
    from app.creation.contracts import TaskEvent
    from app.creation.section_inputs import SectionInputReader

    s = _section_adoption_metadata_state(tmp_path)
    repo, task_id = s.repo, s.task.id
    reader = repo.section_inputs
    assert isinstance(reader, SectionInputReader)
    with repo._connect() as idle:
        statements = []
        idle.set_trace_callback(statements.append)
        assert reader.stage_segment_state('ws', task_id, 'section', connection=idle)['segment_version'] == 'section-original'
        assert reader.stage_section_version('ws', task_id, 'section', connection=idle) == 'section-original'
        assert not idle.in_transaction and statements == []
        with pytest.raises(ValueError, match='已开启的事务'):
            reader.iter_creation_narration_sections(s.task, SimpleNamespace(), connection=idle).__next__()
    with repo._connect() as conn:
        before = reader.section_dependency_snapshot('ws', task_id, 'section', connection=conn)
        assert conn.in_transaction
        task = repo.task_records.get_creation_task('ws', task_id, connection=conn)
        header, _ = repo.script_records.read_execution_script_metadata(conn, 'ws', task.confirmed_script_id)
        original_digest = repo.read_creation_execution_snapshot('ws', task_id, connection=conn).input_digest
        head = s.section_base
        def advance_snapshot(stages, invalidations=None):
            nonlocal head
            snapshot = json.loads(json.dumps(head.snapshot))
            snapshot['stage_segments'] = stages
            if invalidations is not None:
                snapshot['stage_segment_invalidations'] = invalidations
            new = TimelineVersion(f'input-read-{head.sequence+1}', 'ws', task_id, head.id,
                head.sequence+1, snapshot, None)
            repo.timeline_records.create_timeline_version(new, connection=conn, expected_current_version_id=head.id)
            for track in repo.subtitle_records.list_subtitle_tracks('ws', task_id,
                    timeline_version_id=head.id, connection=conn):
                repo.subtitle_records.create_subtitle_track(replace(track, id=track.id+'_'+new.id,
                    timeline_version_id=new.id), connection=conn)
            head = new
        assert reader.creation_section_ids(task, header, offset=-2, limit=1, connection=conn) == ['section']
        assert [row.id for row in reader.creation_section_neighbors(task, header, 'section', connection=conn)] == ['later-section']
        assert reader.creation_section_titles('ws', task_id, ['section'], connection=conn) == {'section': '目标段'}
        from contextlib import closing
        with closing(reader.iter_creation_section_planning_inputs(task, header, connection=conn)) as stream:
            first, measured, window = next(stream)
            assert (first.id, first.content, measured, window) == ('section', '目标原文', 2.0, None)
            original_event = repo.execution_records.get_task_event('ws', 'section-original', connection=conn)
            repo.execution_records.append_task_event(TaskEvent('read-later-publication', 'ws', task_id, s.job.id,
                'segment_publication', {**original_event.payload, 'section_id': 'later-section', 'text': '后段采用正文',
                    'narration_reference': s.original_segments[1].audio_reference}), connection=conn)
            stages = {'section': 'section-original', 'later-section': 'read-later-publication'}
            advance_snapshot(stages)
            conn.execute('UPDATE narration_segments SET text=? WHERE workspace_id=? AND task_id=? AND id=?',
                ('后段采用正文', 'ws', task_id, s.original_segments[1].id))
            second, measured, _ = next(stream)
            assert (second.id, second.content, measured) == ('later-section', '后段采用正文', 2.0)
            with pytest.raises(StopIteration):
                next(stream)
        assert reader.section_dependency_snapshot('ws', task_id, 'section', connection=conn) == before
        repo.execution_records.append_task_event(TaskEvent('read-target-publication', 'ws', task_id, s.job.id,
            'segment_publication', {**original_event.payload, 'text': '目标采用正文'}), connection=conn)
        stages['section'] = 'read-target-publication'
        advance_snapshot(stages, {'section': {'publication_id': 'read-target-publication', 'reason': 'changed_source'}})
        conn.execute('UPDATE narration_segments SET text=? WHERE workspace_id=? AND task_id=? AND id=?',
            ('目标采用正文', 'ws', task_id, s.before.id))
        current = reader.read_creation_section_input(task, header, 'section', connection=conn)
        assert current['section'].content == '目标采用正文'
        assert current['adopted']['current_validity'] == 'invalidated'
        assert current['adopted']['invalidation_reason'] == 'changed_source'
        assert [section.content for section, audio in reader.iter_creation_narration_sections(task, header, connection=conn)] == [
            '目标采用正文', '后段采用正文']
        assert repo.read_creation_execution_snapshot('ws', task_id, connection=conn).input_digest == original_digest
        conn.execute('SAVEPOINT orphan_audio')
        conn.execute('DELETE FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? AND section_id=?',
            ('ws', header.id, 'later-section'))
        with pytest.raises(ValueError, match='现有配音片段集'):
            next(reader.iter_creation_narration_sections(task, header, connection=conn))
        conn.execute('ROLLBACK TO orphan_audio')
        conn.execute('RELEASE orphan_audio')
        advance_snapshot({'later-section': 'read-later-publication'})
        assert reader.stage_segment_state('ws', task_id, 'section', connection=conn) is None
        assert len(reader.stage_section_version('ws', task_id, 'section', connection=conn)) == 64
        advance_snapshot(stages)
        conn.execute('PRAGMA query_only=ON')
        effective = reader.effective_creation_script('ws', task_id, connection=conn)
        assert [section.content for section in effective.sections] == ['目标采用正文', '后段采用正文']
        assert reader.section_dependency_snapshot('ws', task_id, 'section', connection=conn) != before
        assert conn.in_transaction
        conn.rollback()
        assert not conn.in_transaction
    assert reader.stage_section_version('ws', task_id, 'section') == 'section-original'
    assert [section.content for section in reader.effective_creation_script('ws', task_id).sections] == ['目标原文', '后段原文']
    with repo._connect() as idle:
        idle.execute('PRAGMA query_only=ON')
        reader.effective_creation_script('ws', task_id, connection=idle)
        assert idle.in_transaction
        idle.rollback()


def test_narration_unit_transactions_keep_real_scope_receipts_and_original_job(tmp_path, monkeypatch):
    from app.creation.contracts import JobRun, NarrationSegment, TaskEvent
    from app.creation.execution_budget import narration_limits
    from app.creation.execution_budget_scope import BudgetScopeExhausted
    from app.creation_execution import CreationExecutionActor
    from app.device_scheduler import DeviceScheduler, Grant, RecoveryBlocked
    from test_creation_execution import _seed

    repo, task_id, script_id, _ = _seed(tmp_path, workspace_id='local', voice=False)
    actor = CreationExecutionActor('local', 'metadata-unit-owner')
    with repo._connect() as conn:
        task = repo.task_records.get_creation_task('local', task_id, connection=conn)
        task.owner_user_id = actor.member_id
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(asdict(task), default=str), task_id))
    digest = repo.read_creation_execution_snapshot('local', task_id).input_digest
    job = repo.job_records.create_job_run(JobRun('metadata-unit-job', 'local', task_id,
        'creation_task_execution', 'metadata-unit-job', digest))
    repo.execution_records.append_task_event(TaskEvent(f'event_{job.id}_start', 'local', task_id, job.id,
        'creation_execution_started', {'input_digest': digest, 'script_id': script_id, **actor.identity_payload()}))
    repo.authorize_execution_budget_scope('local', task_id, actor, scope_id='metadata-unit-scope', expected_revision=-1,
        request_id='metadata-unit-approval', source_ref='isolated-confirmation:metadata', target_task_ids=[task_id],
        limits={'execution_seconds': 10, 'generated_bytes': 100}, policy_kind='explicit_total', authorization_guard=lambda conn: None)
    repo.bind_execution_budget_scope('local', job.id, actor, scope_id='metadata-unit-scope', expected_revision=0)
    units = repo.narration_units
    def declare_grant(number, seconds, size):
        limits = narration_limits(units.creation_narration_usage('local', job.id),
            SimpleNamespace(max_timeout_seconds=seconds, max_output_bytes=size))
        resources = {**limits, 'script_id': script_id, 'input_contract': 'isolated-narration-metadata/v1',
            'timeout_seconds': seconds, 'max_output_bytes': size, 'narration_phase': 'segments',
            'section_id': 'section_1', 'segment_id': 'metadata-pending', 'ordinal': 0}
        request_id = f'metadata-unit-request-{number}'
        unit_key = f'narration:segment:v1:metadata-{number}'
        now = datetime.now(timezone.utc).isoformat()
        with repo._connect() as conn:
            conn.execute('INSERT INTO device_work_requests '
                '(id,workspace_id,member_id,owner_type,owner_id,input_digest,unit_key,source_refs_json,work_class,policy,'
                'first_ready_at_utc,state,resources_json,created_at,updated_at,last_grant_generation) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (request_id, 'local', actor.member_id, 'creation_job', job.id, digest, unit_key, '[]', 'normal_make',
                    'asap', now, 'granted', json.dumps(resources), now, now, number))
            row = conn.execute('SELECT * FROM device_work_requests WHERE id=?', (request_id,)).fetchone()
            DeviceScheduler._record_event(conn, row, 'deferred', 'metadata-prior-generation-declaration', actor.member_id, now)
        grant = Grant(request_id, 'local', 'creation_job', job.id, unit_key, digest, 1, number,
            'metadata-declaration-not-a-host-permit', repo.database_path)
        return grant, resources
    def pending(task, header, section, resources):
        return NarrationSegment('metadata-pending', 'local', task_id, script_id, section.id,
            section.content, None, None, None, 'metadata-model', 'metadata-voice', 'v1', 'zh', '', 'pending')
    first, resources = declare_grant(1, 6, 60)
    started, seconds, size = units.start_creation_narration_unit(first, resources, [], pending_segment_for=pending)
    assert (started.id, seconds, size) == (job.id, 6, 60)
    active = units.creation_narration_usage('local', job.id)
    assert active['consumption_known'] is False and active['used_seconds'] == 0
    assert (active['scope']['usage']['reserved_seconds'], active['scope']['usage']['reserved_bytes']) == (6, 60)
    with pytest.raises(RecoveryBlocked, match='尚未结算'):
        units.start_creation_narration_unit(first, resources, [], pending_segment_for=pending)
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='尚不可核实'):
            units.resume_creation_narration_job('local', job.id, connection=conn)
        conn.rollback()
    output = tmp_path / 'opaque-unit-output'
    output.write_bytes(b'x'*60)
    import hashlib
    output_file = {'path': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'size_bytes': 60}
    scope_settle = units.budget_scopes.settle
    reached = []
    def fail_after_scope(*args, **kwargs):
        scope_settle(*args, **kwargs)
        reached.append(args[0].in_transaction)
        raise RuntimeError('metadata failure after real scope settlement')
    with monkeypatch.context() as guard:
        guard.setattr(units.budget_scopes, 'settle', fail_after_scope)
        with repo._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            with pytest.raises(RuntimeError, match='real scope settlement'):
                units.settle_creation_narration_unit(first, elapsed_seconds=6, output_bytes=60, connection=conn, output_file=output_file)
            assert conn.in_transaction
            conn.rollback()
    assert reached == [True]
    assert units.creation_narration_usage('local', job.id)['used_seconds'] == 0
    with pytest.raises(KeyError):
        repo.execution_records.get_task_event('local', f'event_{job.id}_narration_settled_1')
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        receipt = units.settle_creation_narration_unit(first, elapsed_seconds=6, output_bytes=60, connection=conn, output_file=output_file)
        assert conn.in_transaction
        assert units.settle_creation_narration_unit(first, elapsed_seconds=6, output_bytes=60, connection=conn, output_file=output_file) == receipt
        with pytest.raises(ValueError, match='不能重复结算不同消耗'):
            units.settle_creation_narration_unit(first, elapsed_seconds=5, output_bytes=60, connection=conn, output_file=output_file)
    settled = units.creation_narration_usage('local', job.id)
    assert settled['consumption_known'] and (settled['used_seconds'], settled['used_output_bytes']) == (6, 60)
    assert (settled['scope']['usage']['execution_seconds'], settled['scope']['usage']['reserved_seconds']) == (6, 0)
    assert units.previous_creation_narration_output(replace(first, generation=2)) == output_file
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('SAVEPOINT missing_receipt')
        conn.execute("UPDATE task_events SET payload_json=json_set(payload_json,'$.settled_event_id','missing-original-receipt') "
            'WHERE workspace_id=? AND id=?', ('local', f'event_{job.id}_narration_budget'))
        with pytest.raises(RecoveryBlocked, match='不能按零恢复'):
            units.creation_narration_usage('local', job.id, connection=conn)
        conn.execute('ROLLBACK TO missing_receipt')
        conn.execute('RELEASE missing_receipt')
        repo.job_records.finish_job_run('local', job.id, status='failed', failure_reason='declared interruption',
            impact='metadata unit retains its original receipt', repair_action='resume the original known job', connection=conn)
        units.resume_creation_narration_job('local', job.id, connection=conn)
    assert repo.job_records.get_job_run('local', job.id).status == 'interrupted'
    second, resources = declare_grant(2, 4, 40)
    units.start_creation_narration_unit(second, resources, [], pending_segment_for=pending)
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        units.settle_creation_narration_unit(second, elapsed_seconds=4, output_bytes=40, connection=conn)
        conn.execute("UPDATE job_runs SET status='queued' WHERE workspace_id=? AND id=?", ('local', job.id))
    assert units.previous_creation_narration_output(replace(second, generation=3)) is None
    final = units.creation_narration_usage('local', job.id)
    assert (final['used_seconds'], final['used_output_bytes']) == (10, 100)
    assert (final['scope']['usage']['execution_seconds'], final['scope']['usage']['generated_bytes']) == (10, 100)
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        units.exhaust_creation_narration_budget(second, task_id, connection=conn)
        assert repo.job_records.get_job_run('local', job.id, connection=conn).status == 'failed'
        conn.rollback()
    assert repo.job_records.get_job_run('local', job.id).status == 'queued'
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        units.exhaust_creation_narration_budget(second, task_id, connection=conn)
    assert repo.task_records.get_creation_task('local', task_id).stage_status == 'failed'
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        units.resume_creation_narration_job('local', job.id, connection=conn)
        count_before = conn.execute('SELECT COUNT(*) FROM job_runs').fetchone()[0]
    third, resources = declare_grant(3, 1, 1)
    with pytest.raises(BudgetScopeExhausted):
        units.start_creation_narration_unit(third, resources, [], pending_segment_for=pending)
    assert repo.job_records.get_job_run('local', job.id).status == 'interrupted'
    assert units.creation_narration_usage('local', job.id)['used_seconds'] == 10
    with repo._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM job_runs').fetchone()[0] == count_before
    with pytest.raises(KeyError):
        repo.execution_records.get_task_event('local', f'event_{job.id}_narration_started_3')


def test_narration_checkpoints_keep_caller_atomicity_candidates_and_intermediate_pcm(tmp_path, monkeypatch):
    from app.creation.contracts import JobRun

    s = _section_adoption_metadata_state(tmp_path)
    repo, checkpoints = s.repo, s.repo.narration_checkpoints
    opaque = tmp_path / 'declared-narration-artifact'
    opaque.write_bytes(b'opaque checkpoint source, never decoded')
    artifact = replace(s.narration, id='checkpoint-artifact', voice_profile_id=s.before.voice_profile_id,
        cache_key='checkpoint-ready', audio_path=str(opaque), duration_seconds=2, sample_rate=24000,
        model_id=s.before.model_id)
    ready = replace(s.before, audio_reference=repo.narration_artifacts.narration_artifact_reference(artifact.cache_key))
    grant = SimpleNamespace(workspace_id='ws', owner_id=s.job.id, request_id='declared-segment-request',
        generation=1, unit_key='narration:segment:declared')
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        checkpoints.commit_creation_narration_segment(artifact, ready, connection=conn)
        assert conn.in_transaction
        assert repo.narration_artifacts.get_narration_artifact('ws', artifact.cache_key, connection=conn) == artifact
        assert repo.narration_artifacts.get_narration_artifact('ws', artifact.cache_key) is None
        conn.rollback()
    assert repo.narration_artifacts.get_narration_artifact('ws', artifact.cache_key) is None
    assert repo.narration_records.get_narration_segment('ws', s.task.id, s.before.id) == s.before
    original_commit = checkpoints.commit_creation_narration_segment
    committed = []
    def refused(*args, **kwargs):
        original_commit(*args, **kwargs)
        committed.append(kwargs['connection'].in_transaction)
        raise RuntimeError('metadata failure after real commit port')
    with monkeypatch.context() as guard:
        guard.setattr(checkpoints, 'commit_creation_narration_segment', refused)
        with pytest.raises(RuntimeError, match='real commit port'):
            with repo._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                checkpoints.checkpoint_creation_narration_segment(grant, artifact, ready, 0, connection=conn)
    assert committed == [True]
    assert repo.narration_artifacts.get_narration_artifact('ws', artifact.cache_key) is None
    assert repo.narration_records.get_narration_segment('ws', s.task.id, s.before.id) == s.before
    assert repo.job_records.get_job_run('ws', s.job.id).status == 'running'
    append = repo.execution_records.append_task_event
    event_id = f'event_{s.job.id}_narration_segment_1'
    tail = []
    def tail_failure(event, **kwargs):
        result = append(event, **kwargs)
        if event.id == event_id:
            tail.append(kwargs['connection'].in_transaction)
            raise RuntimeError('metadata failure after real checkpoint event')
        return result
    with monkeypatch.context() as guard:
        guard.setattr(repo.execution_records, 'append_task_event', tail_failure)
        with pytest.raises(RuntimeError, match='real checkpoint event'):
            with repo._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                checkpoints.checkpoint_creation_narration_segment(grant, artifact, ready, 0, connection=conn)
    assert tail == [True]
    assert repo.narration_artifacts.get_narration_artifact('ws', artifact.cache_key) is None
    assert repo.narration_records.get_narration_segment('ws', s.task.id, s.before.id) == s.before
    assert repo.job_records.get_job_run('ws', s.job.id).status == 'running'
    with pytest.raises(KeyError):
        repo.execution_records.get_task_event('ws', event_id)
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert checkpoints.checkpoint_creation_narration_segment(grant, artifact, ready, 0, connection=conn) == event_id
        assert conn.in_transaction
        assert repo.job_records.get_job_run('ws', s.job.id, connection=conn).status == 'queued'
    assert repo.narration_records.get_narration_segment('ws', s.task.id, s.before.id) == ready
    event = repo.execution_records.get_task_event('ws', event_id)
    assert event.payload['request_id'] == grant.request_id and event.payload['output'] == ready.audio_reference
    edit_job = repo.job_records.create_job_run(JobRun('checkpoint-edit-job', 'ws', s.task.id,
        'creation_task_narration_edit', 'checkpoint-edit-job', 'declared-edit-input'))
    repo.job_records.claim_job_run('ws', edit_job.id)
    candidate = replace(artifact, id='candidate-artifact', cache_key='checkpoint-candidate')
    edit_grant = SimpleNamespace(workspace_id='ws', owner_id=edit_job.id, request_id='declared-edit-request',
        generation=2, unit_key='narration:edit:declared')
    assert checkpoints.narration_edit_generated('ws', edit_job.id) is None
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        candidate_event = checkpoints.checkpoint_narration_edit_generated(edit_grant, candidate,
            replace(ready, audio_reference=repo.narration_artifacts.narration_artifact_reference(candidate.cache_key)), 'declared-source-key', connection=conn)
        candidate_payload = checkpoints.narration_edit_generated('ws', edit_job.id, connection=conn)
        assert candidate_payload == {'source_key': 'declared-source-key', 'segment_id': ready.id,
            'cache_key': candidate.cache_key, 'request_id': edit_grant.request_id, 'permit_generation': 2}
        assert checkpoints.narration_edit_generated('ws', edit_job.id) is None
        assert conn.in_transaction
    assert checkpoints.narration_edit_generated('ws', edit_job.id) == candidate_payload
    assert repo.execution_records.get_task_event('ws', candidate_event).job_id == edit_job.id
    assert repo.narration_records.get_narration_segment('ws', s.task.id, s.before.id) == ready
    repo.job_records.claim_job_run('ws', s.job.id)
    with repo._connect() as conn:
        task = repo.task_records.get_creation_task('ws', s.task.id, connection=conn)
        task.stage = 'narration'
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(asdict(task), default=str), task.id))
        artifacts_before = conn.execute('SELECT COUNT(*) FROM narration_artifacts').fetchone()[0]
    merge = SimpleNamespace(**{**vars(grant), 'generation': 3, 'request_id': 'declared-pcm-request', 'unit_key': 'narration:merge:declared'})
    pcm = {'job_id': s.job.id, 'unit_id': f'{merge.request_id}:{merge.generation}',
        'source_digest': 'declared-pcm-source', 'finalized': False}
    values = dict(source_digest=pcm['source_digest'], section_ids=['section', 'later-section'],
        input_references=[ready.audio_reference, s.original_segments[1].audio_reference], pcm_receipt=pcm)
    assert checkpoints.creation_narration_merge_checkpoint('ws', s.job.id) is None
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='中间PCM检查点'):
            checkpoints.checkpoint_creation_narration_merge(merge, **{**values, 'pcm_receipt': {**pcm, 'finalized': True}}, connection=conn)
        assert repo.job_records.get_job_run('ws', s.job.id, connection=conn).status == 'running'
        merge_event = checkpoints.checkpoint_creation_narration_merge(merge, **values, connection=conn)
        assert checkpoints.creation_narration_merge_checkpoint('ws', s.job.id, connection=conn)['pcm'] == pcm
        assert checkpoints.creation_narration_merge_checkpoint('ws', s.job.id) is None
        assert conn.in_transaction
        conn.rollback()
    assert checkpoints.creation_narration_merge_checkpoint('ws', s.job.id) is None
    assert repo.job_records.get_job_run('ws', s.job.id).status == 'running'
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert checkpoints.checkpoint_creation_narration_merge(merge, **values, connection=conn) == merge_event
    payload = checkpoints.creation_narration_merge_checkpoint('ws', s.job.id)
    assert (payload['request_id'], payload['source_digest'], payload['pcm']) == (merge.request_id, pcm['source_digest'], pcm)
    assert repo.job_records.get_job_run('ws', s.job.id).status == 'queued'
    assert repo.task_records.get_creation_task('ws', s.task.id).stage == 'narration'
    with repo._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM narration_artifacts').fetchone()[0] == artifacts_before
    from app.creation.narration_artifacts import narration_artifact_from_payload
    encoded = json.loads(json.dumps(asdict(artifact), default=str))
    assert narration_artifact_from_payload(encoded) == artifact
    legacy = {key: value for key, value in encoded.items() if key not in {
        'source_kind', 'source_artifact_ref', 'source_sha256', 'source_script_digest'}}
    assert narration_artifact_from_payload(legacy).source_kind == 'generated'
    updated = replace(artifact, id='same-cache-new-record', duration_seconds=3)
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert repo.narration_artifacts.save_narration_artifact(updated, connection=conn) is updated
        assert repo.narration_artifacts.get_narration_artifact('ws', artifact.cache_key, connection=conn) == updated
        assert repo.narration_artifacts.get_narration_artifact('ws', artifact.cache_key) == artifact
        assert conn.in_transaction
    assert repo.narration_artifacts.get_narration_artifact('ws', artifact.cache_key) == updated
    recorded = replace(artifact, id='missing-recorded-source', cache_key='missing-recorded-source',
        voice_profile_id=None, model_id=None, source_kind='recorded',
        source_artifact_ref=f'artifacts/{s.task.id}/missing-source.wav', source_sha256='0'*64,
        source_script_digest='1'*64)
    repo.narration_artifacts.save_narration_artifact(recorded)
    assert repo.narration_artifacts.get_narration_artifact('ws', recorded.cache_key) is None


def test_task_authorization_records_keep_cas_atomicity_and_fixed_goal_facts(tmp_path, monkeypatch):
    from datetime import timedelta
    from app.creation.contracts import TaskAuthorization, TaskAuthorizationConflict, JobRun

    s = metadata_state(tmp_path)
    records = s.repo.task_authorization_records
    now = datetime.now(timezone.utc)
    goal = records.task_authorization_goal_revision('ws', s.task.id)
    authorization = TaskAuthorization('metadata-authorization', 'ws', s.task.id,
        {'member_id': s.member.member_id}, goal, ('task_media',), {'assets': [s.asset.id]},
        now, now, now + timedelta(hours=1), last_verdict='ready')
    with s.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert records.save_task_authorization(authorization, expected_revision=None, connection=conn) is authorization
        assert records.get_task_authorization('ws', s.task.id, connection=conn) == authorization
        assert records.get_task_authorization('ws', s.task.id) is None
        assert conn.in_transaction
        conn.rollback()
    assert records.get_task_authorization('ws', s.task.id) is None
    append = s.repo.execution_records.append_task_event
    def append_then_fail(*args, **kwargs):
        append(*args, **kwargs)
        raise RuntimeError('authorization event tail')
    with monkeypatch.context() as patch:
        patch.setattr(s.repo.execution_records, 'append_task_event', append_then_fail)
        with pytest.raises(RuntimeError, match='authorization event tail'):
            records.save_task_authorization(authorization, expected_revision=None)
    assert records.get_task_authorization('ws', s.task.id) is None
    with s.repo._connect() as conn:
        assert conn.execute('SELECT id FROM task_events WHERE id=?',
            ('event_metadata-authorization_1',)).fetchone() is None
    records.save_task_authorization(authorization, expected_revision=None)
    assert records.get_task_authorization('ws', s.task.id, metadata_only=True).resource_scope == {}
    with pytest.raises(TaskAuthorizationConflict):
        records.save_task_authorization(replace(authorization, revision=2), expected_revision=None)
    revoked = replace(authorization, revision=2, revoked_at=now, last_verdict='unauthorized')
    records.save_task_authorization(revoked, expected_revision=1)
    with pytest.raises(PermissionError, match='不能由迟到核验恢复'):
        records.save_task_authorization(replace(authorization, revision=3), expected_revision=2)
    assert records.get_task_authorization('ws', s.task.id) == revoked
    with s.repo._connect() as conn:
        event = json.loads(conn.execute('SELECT payload_json FROM task_events WHERE id=?',
            ('event_metadata-authorization_2',)).fetchone()[0])
        assert event['revoked'] and 'resource_scope' not in event and 'identity' not in event
    from app.source_repository import SourceRepository
    from app.source_types import KnowledgeItem
    sources = SourceRepository(s.repo.database_path)
    knowledge = sources.create_knowledge_item_owned(KnowledgeItem(
        'fixed-knowledge', 'ws', 'text', 'metadata fact', 'fixed content', None,
        'owned', 'original', None, 'v1', content_hash='f' * 64), verified_user_id=s.member.member_id)
    with s.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("UPDATE knowledge_items SET use_state='excluded' WHERE id='fixed-knowledge'")
        facts = records.task_source_revisions('ws', ['fixed-knowledge', 'fixed-knowledge'], [], connection=conn)
        assert facts['knowledge'] == [['fixed-knowledge', 'v1', knowledge.content_hash, 'excluded', 'private', s.member.member_id]]
        assert records.task_source_revisions('ws', ['fixed-knowledge'], [])['knowledge'][0][3] == 'available'
        assert conn.in_transaction
        conn.rollback()
    with pytest.raises(PermissionError, match='固定资料已不可用'):
        records.task_source_revisions('other-workspace', ['fixed-knowledge'], [])
    with pytest.raises(PermissionError, match='固定资料已不可用'):
        records.task_source_revisions('ws', [], ['missing-source'])
    request = s.repo.creation_requests.create_creation_request('ws', request_key='fixed-goal-request',
        input_fingerprint='request-facts', goal='fixed goal', target_count=2, output_relation='series',
        requirements={}, material_scope={'assets': [s.asset.id]}, clarification_questions=[],
        verified_owner_user_id=s.member.member_id)
    plan = replace(s.repo.plan_records.get_creation_plan('ws', s.task.plan_id), id='origin-plan')
    task = CreationTask('origin-task', 'ws', plan.id, 'fixed facts', 'knowledge_explanation')
    window = s.repo.creation_requests.save_creation_request_window('ws', request['id'],
        expected_revision=request['revision'], input_fingerprint='window-facts', entries=[(plan, task)],
        verified_owner_user_id=s.member.member_id)
    s.repo.creation_request_adoption.confirm_creation_request_window('ws', request['id'],
        window['current_window']['id'], expected_revision=window['revision'], input_fingerprint='window-facts',
        verified_owner_user_id=s.member.member_id)
    fixed = records.task_authorization_goal_revision('ws', task.id)
    assert records.task_authorization_material_scope('ws', task.id) == {'assets': [s.asset.id]}
    assert records.task_authorization_material_scope('ws', s.task.id) is None
    with s.repo._connect() as conn:
        conn.execute('UPDATE creation_requests SET revision=revision+1 WHERE id=?', (request['id'],))
    assert records.task_authorization_goal_revision('ws', task.id) == fixed
    with s.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('UPDATE creation_requests SET goal=? WHERE id=?', ('changed fixed goal', request['id']))
        assert records.task_authorization_goal_revision('ws', task.id, connection=conn) != fixed
        assert records.task_authorization_goal_revision('ws', task.id) == fixed
        conn.rollback()
    s.repo.job_records.create_job_run(JobRun('authorization-due-job', 'ws', task.id,
        'creation_task_execution', 'authorization-due', 'a' * 64))
    active = replace(authorization, id='active-authorization', task_id=task.id, goal_revision=fixed)
    records.save_task_authorization(active, expected_revision=None)
    assert records.list_task_authorizations(due_before=now) == [active]
    with pytest.raises(KeyError):
        records.task_authorization_goal_revision('ws', 'missing-task')
