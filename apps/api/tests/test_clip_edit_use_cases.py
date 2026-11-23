"""Remaining shot controls use the same command and joint version transaction."""
from app.content_identity_http import ContentIdentityHttpAdapter
from dataclasses import asdict, replace
import shutil

import pytest

from test_task_media_grants import _visual_scope_case


def test_opening_candidate_changes_only_opening_and_undo_restores_it_after_reopen(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import subprocess
    from uuid import uuid4
    from app.production import ProductionRepository

    main, repo, actor, _media, runtime, client, before, subtitle_track, _store = _visual_scope_case(
        tmp_path, monkeypatch, lock_first=False,
    )
    try:
        asset = repo.get_asset("local", "asset")
        path = repo.video_library_path / "red-opening.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x64:r=30:d=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
            check=True, capture_output=True, timeout=5)
        info = path.stat()
        candidate_asset = replace(asset, id="red-opening", path=str(path), file_name=path.name,
            size_bytes=info.st_size, modified_at=datetime.fromtimestamp(info.st_mtime, timezone.utc),
            duration_seconds=2, segments=[replace(asset.segments[0], id="red-segment", asset_id="red-opening",
                end_seconds=2, description="红色产品开场")])
        repo._save_asset(candidate_asset)
        base = "/api/creation-tasks/task"
        opening, *rest = before["clips"]
        assert opening["timeline_start_seconds"] == 0
        result = client.get(f"{base}/montage/clips/{opening['id']}/candidates?for_automation=true")
        assert result.status_code == 200, result.text
        [candidate] = result.json()
        assert candidate["description"] == "红色产品开场"

        def forbidden(*_args, **_kwargs):
            pytest.fail("保存或撤销单个镜头不需要重新制作视频")

        monkeypatch.setattr(main, "_creation_runtime", forbidden)
        jobs = repo.job_records.list_job_runs("local", "task")
        saved = client.post(f"{base}/edits/exact", json={"operation_type": "ReplaceClip",
            "clip_id": opening["id"], "asset_id": candidate["asset_id"], "segment_id": candidate["segment_id"],
            "request_id": str(uuid4()), "expected_timeline_version_id": "timeline", "expected_timeline_sequence": 1})
        assert saved.status_code == 200, saved.text

        def read_reopened(expected_asset, *, expected_rest=rest, expected_opening=None):
            reopened = ProductionRepository(repo.database_path, repo.video_library_path)
            task = reopened.task_records.get_creation_task("local", "task")
            current = reopened.get_creation_task_montage_plan("local", "task")
            kept = ("audio_timeline", "narration_audio_path", "cues", "graphic_overlays", "duration_seconds")
            assert {key: current[key] for key in kept} == {key: before[key] for key in kept}
            assert current["clips"][1:] == expected_rest
            clip = current["clips"][0]
            assert clip["asset_id"] == expected_asset
            assert (clip["id"], clip["timeline_start_seconds"], clip["timeline_end_seconds"]) == (
                opening["id"], opening["timeline_start_seconds"], opening["timeline_end_seconds"])
            if expected_opening is not None:
                assert clip == expected_opening
            [track] = reopened.subtitle_records.list_subtitle_tracks("local", "task",
                timeline_version_id=task.current_timeline_version_id)
            assert [asdict(cue) for cue in track.cues] == [asdict(cue) for cue in subtitle_track.cues]
            assert reopened.job_records.list_job_runs("local", "task") == jobs
            source = reopened.resolve_clip_media_reference("local", clip["asset_id"], clip["source_path"])
            frame = subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-frames:v", "1",
                "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                check=True, capture_output=True, timeout=5).stdout
            red, _green, blue = frame
            return task.current_timeline_version_id, clip, red > blue

        edited_version, edited_opening, is_red = read_reopened(candidate["asset_id"])
        assert edited_version != "timeline" and is_red

        # Make a real second edit on a distant shot. Undoing the opening
        # must not restore the entire old timeline and erase this later edit.
        tail = rest[-1]
        assert tail["locked"] is False
        locked = client.post(f"{base}/edits/exact", json={"operation_type": "SetClipLock",
            "clip_id": tail["id"], "locked": True, "request_id": str(uuid4()),
            "expected_timeline_version_id": edited_version, "expected_timeline_sequence": 2})
        assert locked.status_code == 200, locked.text
        locked_rest = [*rest[:-1], {**tail, "locked": True}]
        locked_version, _, _ = read_reopened(candidate["asset_id"],
            expected_rest=locked_rest, expected_opening=edited_opening)
        undo = client.post(f"{base}/timeline/undo", json={"request_id": str(uuid4()),
            "target_operation_id": saved.json()["operation"]["id"],
            "expected_timeline_version_id": locked_version, "expected_timeline_sequence": 3})
        assert undo.status_code == 200, undo.text
        restored_version, _, is_red = read_reopened(opening["asset_id"],
            expected_rest=locked_rest, expected_opening=opening)
        assert restored_version not in {"timeline", edited_version, locked_version} and not is_red
    finally:
        runtime.stop(0)


@pytest.mark.parametrize('operation', ['ReplaceClip', 'TrimClip', 'SplitClip', 'ReorderClip'])
def test_direct_shot_edit_preserves_other_tracks_and_roundtrips_without_routes(tmp_path, monkeypatch, operation):
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError('direct shot edit must not call a route or construct the media runtime')
    try:
        canonical_guard_routes = [route for route in main.app.routes if route.name == 'preview_creation_task_exact_edit' and route.path == '/api/creation-tasks/{task_id}/edits/exact/preview' and ('POST' in route.methods)]
        assert len(canonical_guard_routes) == 1
        monkeypatch.setattr(canonical_guard_routes[0], 'endpoint', forbidden)
        monkeypatch.setattr(canonical_guard_routes[0].dependant, 'call', forbidden)
        canonical_guard_routes = [route for route in main.app.routes if route.name == 'confirm_creation_task_edit' and route.path == '/api/creation-tasks/{task_id}/edits/{proposal_id}/confirm' and ('POST' in route.methods)]
        assert len(canonical_guard_routes) == 1
        monkeypatch.setattr(canonical_guard_routes[0], 'endpoint', forbidden)
        monkeypatch.setattr(canonical_guard_routes[0].dependant, 'call', forbidden)
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
        current_tracks = repo.subtitle_records.list_subtitle_tracks('local', 'task', timeline_version_id=current_id)
        assert [asdict(cue) for cue in current_tracks[0].cues] == [asdict(cue) for cue in track.cues]
        if operation == 'SplitClip':
            assert len(changed['clips']) == len(original['clips'])+1
            assert changed['clips'][1]['timeline_end_seconds'] == 3
            assert changed['clips'][2]['timeline_start_seconds'] == 3
        undo = client.post('/api/creation-tasks/task/timeline/undo', json={'request_id': 'test_direct_shot_edit_preserves_other_tracks_and_roundtrips_without_routes:1'})
        assert undo.status_code == 200, undo.text
        restored = repo.get_creation_task_montage_plan('local', 'task')
        assert restored['clips'] == original['clips'] and restored['audio_timeline'] == original['audio_timeline']
        redo = client.post('/api/creation-tasks/task/timeline/redo', json={'request_id': 'test_direct_shot_edit_preserves_other_tracks_and_roundtrips_without_routes:2'})
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
        repo.montage_plan_adoption.save_creation_task_montage_plan('local','task','montage',seed)
        copied = clone_subtitle_track(track,timeline_version_id='neighbor-base')
        repo.timeline_records.create_timeline_version(TimelineVersion('neighbor-base','local','task','timeline',2,
            {'montage':seed,'subtitle_track_ids':[copied.id]},None))
        repo.subtitle_records.create_subtitle_track(copied)
        seed=repo.get_creation_task_montage_plan('local','task')
        monkeypatch.setattr(ContentIdentityHttpAdapter,'reader',lambda adapter, *args:(actor.member_id,False))
        commit = repo.edit_adoption.confirm_direct
        def revise(command,*args,**kwargs):
            if kwargs.get('connection') is not None:
                return commit(command,*args,**kwargs)
            assert f'clip_{source_index}' in command.affected_clip_ids
            assert 'clip_10' not in command.affected_clip_ids
            assert any(fact['asset_id']=='neighbor' for fact in command.affected_source_facts)
            if change == 'file':
                with path.open('ab') as output:
                    output.write(b'neighbor revised')
            else:
                repo._save_asset(replace(repo.get_asset('local','neighbor'),
                    **({'owner_user_id':'other-member','visibility':'private'} if change=='private' else {'use_state':'excluded'})))
            return commit(command,*args,**kwargs)
        monkeypatch.setattr(repo.edit_adoption,'confirm_direct',revise)
        values = {'operation_type':operation,'clip_id':'clip_1','position':2} if operation=='ReorderClip' else {
            'operation_type':operation,'clip_id':'clip_2','transition_seconds':.1}
        response = client.post('/api/creation-tasks/task/edits/exact',json=values)
        assert response.status_code == (403 if change=='private' else 409),response.text
        assert repo.task_records.get_creation_task('local','task').current_timeline_version_id=='neighbor-base'
        assert repo.edit_records.list_edit_intents('local','task')==[] and repo.edit_records.list_edit_proposals('local','task')==[]
        assert repo.edit_records.list_edit_operations('local','task')==[] and len(repo.timeline_records.list_timeline_versions('local','task'))==2
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
        repo.montage_plan_adoption.save_creation_task_montage_plan('local','task','montage',seed)
        copied=clone_subtitle_track(track,timeline_version_id='distant-base')
        repo.timeline_records.create_timeline_version(TimelineVersion('distant-base','local','task','timeline',2,
            {'montage':seed,'subtitle_track_ids':[copied.id]},None))
        repo.subtitle_records.create_subtitle_track(copied)
        seed=repo.get_creation_task_montage_plan('local','task')
        monkeypatch.setattr(ContentIdentityHttpAdapter,'reader',lambda adapter, *args:(actor.member_id,False))
        response=client.post('/api/creation-tasks/task/edits/exact',json={
            'operation_type':'ReorderClip','clip_id':'clip_1','position':2})
        assert response.status_code==200,response.text
        saved=repo.get_creation_task_montage_plan('local','task')
        assert saved['clips'][10]==seed['clips'][10]
    finally:
        runtime.stop(0)


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
        repo.montage_plan_adoption.save_creation_task_montage_plan('local','task','montage',seed)
        copied=clone_subtitle_track(track,timeline_version_id='transition-base')
        repo.timeline_records.create_timeline_version(TimelineVersion('transition-base','local','task','timeline',2,
            {'montage':seed,'subtitle_track_ids':[copied.id]},None))
        repo.subtitle_records.create_subtitle_track(copied)
        monkeypatch.setattr(ContentIdentityHttpAdapter,'reader',lambda adapter, *args:(actor.member_id,False))
        prepared=client.post('/api/creation-tasks/task/edits/exact/preview?draft=true',json={
            'operation_type':'SetClipTransition','clip_id':'clip_2','transition_seconds':.1})
        assert prepared.status_code==200,prepared.text
        proposal=prepared.json()['proposal']
        bound=repo.edit_records.get_edit_intent('local',proposal['intent_id']).constraints['affected_source_scope']
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
        assert len(repo.edit_records.list_edit_intents('local','task'))==1 and len(repo.edit_records.list_edit_proposals('local','task'))==1
        assert repo.edit_records.list_edit_operations('local','task')==[] and len(repo.timeline_records.list_timeline_versions('local','task'))==2
    finally:
        runtime.stop(0)


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
