"""Remaining shot controls use the same command and joint version transaction."""
from dataclasses import asdict, replace
import shutil
from pathlib import Path
import json

import pytest

from test_task_media_grants import _visual_scope_case


@pytest.mark.parametrize('operation', ['ReplaceClip', 'TrimClip', 'SplitClip', 'ReorderClip'])
def test_direct_shot_edit_preserves_other_tracks_and_roundtrips_without_routes(tmp_path, monkeypatch, operation):
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError('direct shot edit must not call a route or construct the media runtime')
    try:
        monkeypatch.setattr(main, 'preview_creation_task_exact_edit', forbidden)
        monkeypatch.setattr(main, 'confirm_creation_task_edit', forbidden)
        monkeypatch.setattr(main, '_creation_runtime', forbidden)
        values = {'operation_type':operation, 'clip_id':'clip_2'}
        if operation == 'ReplaceClip':
            original_asset = repo.get_asset('local', 'asset')
            target = tmp_path/'library'/'replacement.mp4'
            shutil.copyfile(original_asset.path, target)
            stat = target.stat()
            from datetime import datetime, timezone
            candidate = replace(original_asset, id='replacement', path=str(target), file_name=target.name,
                size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
                segments=[replace(original_asset.segments[0], id='replacement-segment', asset_id='replacement')])
            repo._save_asset(candidate)
            values.update(asset_id='replacement', segment_id='replacement-segment')
        elif operation == 'TrimClip':
            values['source_start_seconds'] = .5
        elif operation == 'SplitClip':
            values.update(clip_id='clip_1', split_time_seconds=3.)
        else:
            values.update(clip_id='clip_1', position=2)
        before_jobs = len(repo.job_records.list_job_runs('local', 'task'))
        saved = client.post('/api/creation-tasks/task/edits/exact', json=values)
        assert saved.status_code == 200, saved.text
        assert len(repo.job_records.list_job_runs('local', 'task')) == before_jobs
        changed = repo.get_creation_task_montage_plan('local', 'task')
        assert changed['audio_timeline'] == original['audio_timeline']
        assert changed['graphic_overlays'] == original['graphic_overlays']
        current_id = repo.task_records.get_creation_task('local', 'task').current_timeline_version_id
        current_tracks = repo.list_subtitle_tracks('local', 'task', timeline_version_id=current_id)
        assert [asdict(cue) for cue in current_tracks[0].cues] == [asdict(cue) for cue in track.cues]
        if operation == 'SplitClip':
            assert len(changed['clips']) == len(original['clips'])+1
            assert changed['clips'][1]['timeline_end_seconds'] == 3
            assert changed['clips'][2]['timeline_start_seconds'] == 3
        undo = client.post('/api/creation-tasks/task/timeline/undo', json={})
        assert undo.status_code == 200, undo.text
        restored = repo.get_creation_task_montage_plan('local', 'task')
        assert restored['clips'] == original['clips'] and restored['audio_timeline'] == original['audio_timeline']
        redo = client.post('/api/creation-tasks/task/timeline/redo', json={})
        assert redo.status_code == 200, redo.text
        assert repo.get_creation_task_montage_plan('local', 'task')['clips'] == changed['clips']
    finally:
        runtime.stop(0)


@pytest.mark.parametrize('operation,source_index', [('ReorderClip',2), ('SetClipTransition',1)])
@pytest.mark.parametrize('change', ['private', 'excluded', 'file'])
def test_direct_change_rechecks_displaced_or_transition_source_in_original_scope(tmp_path, monkeypatch, operation, source_index, change):
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    try:
        asset = repo.get_asset('local','asset')
        from datetime import datetime, timezone
        path = tmp_path/'library'/'neighbor.mp4'
        shutil.copyfile(asset.path,path)
        stat = path.stat()
        neighbor = replace(asset,id='neighbor',path=str(path),file_name=path.name,
            size_bytes=stat.st_size,modified_at=datetime.fromtimestamp(stat.st_mtime,timezone.utc),
            segments=[replace(asset.segments[0],id='neighbor-segment',asset_id='neighbor')])
        repo._save_asset(neighbor)
        from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
        from app.creation.contracts import TimelineVersion
        from app.creation.transactions import clone_subtitle_track
        plan = creation_task_montage_plan_from_payload(original)
        clip = plan.clips[source_index]
        clip.asset_id,clip.segment_id = 'neighbor','neighbor-segment'
        clip.source_path = repo.workspace_media_reference_for_asset('local','neighbor')
        seed = creation_task_montage_plan_to_payload(plan)
        repo.save_creation_task_montage_plan('local','task','montage',seed)
        copied = clone_subtitle_track(track,timeline_version_id='neighbor-base')
        repo.create_timeline_version(TimelineVersion('neighbor-base','local','task','timeline',2,
            {'montage':seed,'subtitle_track_ids':[copied.id]},None))
        repo.create_subtitle_track(copied)
        seed=repo.get_creation_task_montage_plan('local','task')
        monkeypatch.setattr(main,'_content_reader',lambda *args:(actor.member_id,False))
        commit = repo.confirm_direct_edit
        def revise(command,*args,**kwargs):
            assert f'clip_{source_index}' in command.affected_clip_ids
            assert 'clip_10' not in command.affected_clip_ids
            assert any(fact['asset_id']=='neighbor' for fact in command.affected_source_facts)
            if change == 'file':
                with path.open('ab') as output:output.write(b'neighbor revised')
            else:
                repo._save_asset(replace(repo.get_asset('local','neighbor'),
                    **({'owner_user_id':'other-member','visibility':'private'} if change=='private' else {'use_state':'excluded'})))
            return commit(command,*args,**kwargs)
        monkeypatch.setattr(repo,'confirm_direct_edit',revise)
        values = {'operation_type':operation,'clip_id':'clip_1','position':2} if operation=='ReorderClip' else {
            'operation_type':operation,'clip_id':'clip_2','transition_seconds':.1}
        response = client.post('/api/creation-tasks/task/edits/exact',json=values)
        assert response.status_code == (403 if change=='private' else 409),response.text
        assert repo.task_records.get_creation_task('local','task').current_timeline_version_id=='neighbor-base'
        assert repo.list_edit_intents('local','task')==[] and repo.list_edit_proposals('local','task')==[]
        assert repo.list_edit_operations('local','task')==[] and len(repo.list_timeline_versions('local','task'))==2
        assert repo.get_creation_task_montage_plan('local','task')==seed
    finally:
        runtime.stop(0)


def test_unaffected_distant_private_source_is_not_part_of_a_reorder(tmp_path,monkeypatch):
    main,repo,actor,media,runtime,client,original,track,store=_visual_scope_case(tmp_path,monkeypatch)
    try:
        from app.creation.editing.affected import affected_source_clip_ids
        from app.creation.editing.clips import reorder_picture
        from app.montage import creation_task_montage_plan_from_payload
        plan=creation_task_montage_plan_from_payload(original)
        order=[clip.id for clip in plan.clips]
        order.insert(2,order.pop(1))
        affected=affected_source_clip_ids(plan,reorder_picture(plan,order))
        assert affected==('clip_0','clip_1','clip_2','clip_3')
        assert 'clip_10' not in affected
        # Metadata-only changes with a stable hard cut do not pull in both neighbors.
        after=creation_task_montage_plan_from_payload(original)
        after.clips[10].crop_x=.2
        assert affected_source_clip_ids(plan,after)==('clip_10',)
        asset=repo.get_asset('local','asset')
        path=tmp_path/'library'/'distant-private.mp4'
        shutil.copyfile(asset.path,path)
        from datetime import datetime,timezone
        stat=path.stat()
        distant=replace(asset,id='distant',path=str(path),file_name=path.name,size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime,timezone.utc),visibility='private',owner_user_id='other-member',
            segments=[replace(asset.segments[0],id='distant-segment',asset_id='distant')])
        repo._save_asset(distant)
        plan.clips[10].asset_id,plan.clips[10].segment_id='distant','distant-segment'
        plan.clips[10].source_path=repo.workspace_media_reference_for_asset('local','distant')
        from app.montage import creation_task_montage_plan_to_payload
        from app.creation.contracts import TimelineVersion
        from app.creation.transactions import clone_subtitle_track
        seed=creation_task_montage_plan_to_payload(plan)
        repo.save_creation_task_montage_plan('local','task','montage',seed)
        copied=clone_subtitle_track(track,timeline_version_id='distant-base')
        repo.create_timeline_version(TimelineVersion('distant-base','local','task','timeline',2,
            {'montage':seed,'subtitle_track_ids':[copied.id]},None))
        repo.create_subtitle_track(copied)
        seed=repo.get_creation_task_montage_plan('local','task')
        monkeypatch.setattr(main,'_content_reader',lambda *args:(actor.member_id,False))
        response=client.post('/api/creation-tasks/task/edits/exact',json={
            'operation_type':'ReorderClip','clip_id':'clip_1','position':2})
        assert response.status_code==200,response.text
        saved=repo.get_creation_task_montage_plan('local','task')
        assert saved['clips'][10]==seed['clips'][10]
    finally:runtime.stop(0)


def test_transition_adoption_rechecks_frozen_predecessor_inside_the_version_transaction(tmp_path,monkeypatch):
    main,repo,actor,media,runtime,client,original,track,store=_visual_scope_case(tmp_path,monkeypatch)
    try:
        asset=repo.get_asset('local','asset')
        path=tmp_path/'library'/'predecessor.mp4'
        shutil.copyfile(asset.path,path)
        from datetime import datetime,timezone
        from app.montage import creation_task_montage_plan_from_payload,creation_task_montage_plan_to_payload
        from app.creation.contracts import TimelineVersion
        from app.creation.transactions import clone_subtitle_track
        stat=path.stat()
        predecessor=replace(asset,id='predecessor',path=str(path),file_name=path.name,size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime,timezone.utc),
            segments=[replace(asset.segments[0],id='predecessor-segment',asset_id='predecessor')])
        repo._save_asset(predecessor)
        plan=creation_task_montage_plan_from_payload(original)
        plan.clips[1].asset_id,plan.clips[1].segment_id='predecessor','predecessor-segment'
        plan.clips[1].source_path=repo.workspace_media_reference_for_asset('local','predecessor')
        seed=creation_task_montage_plan_to_payload(plan)
        repo.save_creation_task_montage_plan('local','task','montage',seed)
        copied=clone_subtitle_track(track,timeline_version_id='transition-base')
        repo.create_timeline_version(TimelineVersion('transition-base','local','task','timeline',2,
            {'montage':seed,'subtitle_track_ids':[copied.id]},None))
        repo.create_subtitle_track(copied)
        monkeypatch.setattr(main,'_content_reader',lambda *args:(actor.member_id,False))
        prepared=client.post('/api/creation-tasks/task/edits/exact/preview?draft=true',json={
            'operation_type':'SetClipTransition','clip_id':'clip_2','transition_seconds':.1})
        assert prepared.status_code==200,prepared.text
        proposal=prepared.json()['proposal']
        bound=repo.get_edit_intent('local',proposal['intent_id']).constraints['affected_source_scope']
        assert bound['clip_ids']==['clip_1','clip_2']
        commit=repo.edit_adoption.confirm
        def revoke_before_commit(*args,**kwargs):
            if kwargs.get('verify_sources') is not None:
                repo._save_asset(replace(repo.get_asset('local','predecessor'),owner_user_id='other-member',visibility='private'))
            return commit(*args,**kwargs)
        monkeypatch.setattr(repo.edit_adoption, 'confirm',revoke_before_commit)
        response=client.post('/api/creation-tasks/task/edits/'+proposal['id']+'/confirm')
        assert response.status_code==403,response.text
        assert repo.task_records.get_creation_task('local','task').current_timeline_version_id=='transition-base'
        assert len(repo.list_edit_intents('local','task'))==1 and len(repo.list_edit_proposals('local','task'))==1
        assert repo.list_edit_operations('local','task')==[] and len(repo.list_timeline_versions('local','task'))==2
    finally:runtime.stop(0)


def test_split_does_not_include_distant_stable_sources_due_to_ordinal_shift():
    from app.creation.editing.clips import split_clip_picture, reorder_picture
    from app.creation.editing.affected import affected_source_clip_ids
    from app.montage import CreationTaskMontagePlan, MontageClip
    clips=[MontageClip(name,name,'asset',name,'workspace://source.mp4',start,end,start,end,1,[],False)
        for name,start,end in [('A',0,2),('B',2,4),('distant',4,6)]]
    plan=CreationTaskMontagePlan('plan','local','task','script','scope','ready',[],clips,6,0)
    split=split_clip_picture(plan,'A',1)
    assert affected_source_clip_ids(plan,split)==('A','B')
    assert split.clips[-1]==plan.clips[-1]
    moved=reorder_picture(plan,['B','A','distant'])
    assert affected_source_clip_ids(plan,moved)==('A','B','distant')
    transitioned=replace(plan,clips=[clips[0],replace(clips[1],transition_in_seconds=.1),clips[2]])
    assert affected_source_clip_ids(plan,transitioned)==('A','B')
