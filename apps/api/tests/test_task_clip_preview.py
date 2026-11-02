"""A long movie's selected-clip preview remains a silent five-second unit."""
from dataclasses import asdict, replace
from uuid import uuid4
from datetime import datetime, timezone
from types import SimpleNamespace
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.artifact_store import ArtifactStore
from app.host_resource_probes import CpuRenderBudget, HostProcessProbe, HostBudget, HostResourceProbe
from app.montage import CreationTaskMontagePlan, MontageClip, NarrationCue, creation_task_montage_plan_to_payload
from app.production import MaterialAsset, MaterialSegment
from app.creation.contracts import TimelineVersion
from test_task_media_grants import case


def test_preview_modules_import_without_app_or_http_dependencies():
    import ast
    from pathlib import Path
    files = [Path(main.__file__).parent / folder / 'clip_preview.py' for folder in ('creation', 'media')]
    for path in files:
        tree = ast.parse(path.read_text())
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(module and module.startswith(('app.main', 'fastapi', 'starlette')) for module in imports)
        assert not any(isinstance(node, ast.Attribute) and node.attr in {'_connect', '__wrapped__'} for node in ast.walk(tree))
    subprocess.run([__import__('sys').executable, '-c',
        "import sys; import app.creation.clip_preview; import app.media.clip_preview; assert 'app.main' not in sys.modules"],
        check=True, capture_output=True, timeout=10, cwd=Path(main.__file__).parents[1])


def test_exposure_preview_uses_explicit_grant_then_atomic_adoption_and_undo(tmp_path, monkeypatch):
    from app.task_audio import TASK_MEDIA_CONTEXT
    from app.production import EXACT_EDIT_SOURCE
    from test_task_media_grants import _visual_scope_case
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    try:
        payload = {'base_version_id':'timeline', 'clip_ids':['clip_1'], 'effects':{'exposure':.15}}
        queued = client.post('/api/creation-tasks/task/visual/preview', json=payload)
        assert queued.status_code == 202, queued.text
        def forbidden(*args, **kwargs):
            raise AssertionError('short preview must not reenter HTTP execution')
        assert not hasattr(media, "execute_operation")
        monkeypatch.setattr(main, 'Request', forbidden)
        execute = media.clip_preview.execute
        def explicit(job, event, original_actor, context):
            assert TASK_MEDIA_CONTEXT.get() is None and EXACT_EDIT_SOURCE.get() is None
            assert original_actor == actor and context.grant.owner_id == job.id
            assert event['arguments']['_clip_preview']['base_version_id'] == 'timeline'
            return execute(job, event, original_actor, context)
        monkeypatch.setattr(media.clip_preview, 'execute', explicit)
        assert runtime.run_once()
        done = client.get(queued.json()['result_url']).json()
        assert done['status'] == 'succeeded', done
        assert repo.get_creation_task_montage_plan('local', 'task') == original
        proposal = done['result']['proposal']
        adopted = client.post('/api/creation-tasks/task/edits/' + proposal['id'] + '/confirm')
        assert adopted.status_code == 200, adopted.text
        current = repo.get_creation_task_montage_plan('local', 'task')
        assert current['clips'][1]['visual_effects']['exposure'] == .15
        assert current['clips'][0] == original['clips'][0] and current['clips'][2:] == original['clips'][2:]
        assert current['audio_timeline'] == original['audio_timeline']
        current_id = repo.task_records.get_creation_task('local', 'task').current_timeline_version_id
        assert current_id != 'timeline'
        kept = repo.subtitle_records.list_subtitle_tracks('local', 'task', timeline_version_id=current_id)
        assert [(item.text, item.start_seconds, item.end_seconds) for item in kept[0].cues] == [
            (item.text, item.start_seconds, item.end_seconds) for item in track.cues]
        undo = client.post('/api/creation-tasks/task/timeline/undo', json={'request_id':str(uuid4())})
        assert undo.status_code == 200, undo.text
        restored = repo.get_creation_task_montage_plan('local', 'task')
        assert restored['clips'] == original['clips'] and restored['audio_timeline'] == original['audio_timeline']
    finally:
        runtime.stop(0)


def test_short_preview_replay_cancel_and_changed_base_do_not_execute(tmp_path, monkeypatch):
    from uuid import uuid4
    from test_task_media_grants import _visual_scope_case
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    try:
        request_id = str(uuid4())
        payload = {'operation_type':'AdjustVisualEffects', 'clip_ids':['clip_1'], 'effects':{'exposure':.15},
            'request_id':request_id, 'expected_timeline_version_id':'timeline', 'expected_timeline_sequence':1}
        count = len(repo.job_records.list_job_runs('local', 'task'))
        queued = client.post('/api/creation-tasks/task/edits/exact/preview', json=payload)
        assert queued.status_code == 202, queued.text
        receipt = queued.json()
        job, event = media.read('local', receipt['job_id'])
        from copy import deepcopy
        wrong_task = deepcopy(event['arguments'])
        wrong_task['_clip_preview']['task_id'] = 'other'
        with pytest.raises(PermissionError, match='当前成员和任务'):
            main._task_media_facts('local', 'task', event['operation'], wrong_task, actor)
        wrong_actor = deepcopy(event['arguments'])
        wrong_actor['_clip_preview']['source']['actor_identity']['member_id'] = 'other'
        with pytest.raises(PermissionError, match='操作者身份'):
            main._task_media_facts('local', 'task', event['operation'], wrong_actor, actor)
        replay = client.post('/api/creation-tasks/task/edits/exact/preview', json=payload)
        assert replay.json()['job_id'] == receipt['job_id'] and len(repo.job_records.list_job_runs('local', 'task')) == count + 1
        cancelled = client.post(receipt['result_url'] + '/cancel')
        assert cancelled.json()['status'] == 'cancelled' and not runtime.run_once()
        payload['request_id'] = str(uuid4())
        second = client.post('/api/creation-tasks/task/edits/exact/preview', json=payload)
        assert second.status_code == 202, second.text
        version = TimelineVersion('next', 'local', 'task', 'timeline', 2, {'montage':original}, None)
        repo.timeline_records.create_timeline_version(version)
        replay = client.post('/api/creation-tasks/task/edits/exact/preview', json=payload)
        assert replay.status_code == 202 and replay.json()['job_id'] == second.json()['job_id']
        assert client.get(second.json()['result_url']).json()['status'] == 'stale'
        assert not runtime.run_once()
        assert not [item for item in repo.execution_records.list_artifact_records('local', 'task') if item.kind == 'color_preview']
    finally:
        runtime.stop(0)


def test_missing_profile_or_base_never_changes_draft_or_submits_work(tmp_path, monkeypatch):
    from test_task_media_grants import _visual_scope_case
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    try:
        count = len(repo.job_records.list_job_runs('local', 'task'))
        selected_budget_for = main._device_operation_budget
        monkeypatch.setattr(main, '_device_operation_budget',
            lambda name: None if name == 'clip_preview' else selected_budget_for(name))
        payload = {'base_version_id':'timeline', 'clip_ids':['clip_1'], 'effects':{'exposure':.15}}
        refused = client.post('/api/creation-tasks/task/visual/preview', json=payload)
        assert refused.status_code == 503 and '尚未标定' in refused.text
        assert len(repo.job_records.list_job_runs('local', 'task')) == count
        assert repo.get_creation_task_montage_plan('local', 'task') == original
        task = repo.task_records.get_creation_task('local', 'task')
        task.current_timeline_version_id = None
        with repo._connect() as conn:
            conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id='local' AND id='task'",
                (json.dumps(asdict(task), default=str),))
        payload['base_version_id'] = None
        missing = client.post('/api/creation-tasks/task/visual/preview', json=payload)
        assert missing.status_code == 409 and '基础版本' in missing.text
        assert repo.task_records.get_creation_task('local', 'task').current_timeline_version_id is None
        assert len(repo.timeline_records.list_timeline_versions('local', 'task')) == 1
    finally:
        runtime.stop(0)


def test_assistant_short_preview_freezes_original_turn_and_survives_runtime_restart(tmp_path, monkeypatch):
    from app.conversation_repository import ConversationRepository
    from app.device_runtime import build_device_runtime
    from app.local_members import LocalMemberService
    from app.member_permits import MemberDeviceAccess
    from test_task_media_grants import _visual_scope_case
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    conversations = ConversationRepository(repo.database_path)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    conversation = conversations.create('local', actor.member_id, 'task', 'task')
    text = '曝光设为0.15'
    draft = conversations.save_draft(conversation['id'], 'local', actor.member_id, text, 0)
    turn, _ = conversations.create_turn_idempotent(conversation['id'], 'local', actor.member_id, text,
        'short-preview-turn', draft['draft_version'], {'kind':'task', 'task_id':'task', 'stage':'montage',
            'selected_object_refs':['clip:clip_1'], 'playhead_ms':None,
            'target_snapshot':{'timeline_version_id':'timeline', 'timeline_sequence':1}})
    try:
        queued = client.post('/api/creation-tasks/task/edits/preview', json={'raw_command':'model rewrite',
            'assistant_turn_id':turn['id'], 'context_stage':'montage', 'selected_object_refs':['clip:clip_1']})
        assert queued.status_code == 202, queued.text
        receipt = queued.json()
        assert receipt['operation'] == 'command_edit_preview'
        job, event = media.read('local', receipt['job_id'])
        assert event['arguments']['_clip_preview']['source']['raw_command'] == text
        assert event['arguments']['_clip_preview']['source']['assistant_turn_id'] == turn['id']
        conversations.finish_turn(turn['id'], 'completed', result={})
        assert runtime.stop(0)
        from app.task_audio import TaskMediaJobs
        from app.device_runtime import OwnerRegistration
        from app.creation_execution import CreationJobOwnerVerifier,CreationJobUnitExecutor
        media = TaskMediaJobs(repo,facts=media.facts,verify_identity=media.verify_identity,clip_preview=media.clip_preview)
        registration=OwnerRegistration(CreationJobOwnerVerifier(repo,verify_identity=media.verify_identity,task_media=media),
            CreationJobUnitExecutor(repo,render_action=None,render_is_valid=None,task_media=media))
        runtime = build_device_runtime(database_path=repo.database_path, device_lock_path=tmp_path/'device.lock',
            registrations={'creation_job':registration}, device_access=MemberDeviceAccess(LocalMemberService(repo.database_path)),
            resources=runtime.scheduler.resources, processes=HostProcessProbe(), admission_recorder=media.record_admission)
        monkeypatch.setattr(main, '_creation_runtime', lambda:runtime)
        assert runtime.run_once()
        done = client.get(receipt['result_url']).json()
        assert done['status'] == 'succeeded', done
        source = done['result']['intent']['constraints']['request_source']
        assert source['assistant_turn_id'] == turn['id'] and source['raw_command'] == text
        assert source['media_job_id'] == job.id and source['actor_identity'] == actor.identity_payload()
        assert repo.get_creation_task_montage_plan('local', 'task') == original
    finally:
        runtime.stop(0)


def test_retired_declarations_preserve_successful_history_but_never_execute(tmp_path, monkeypatch):
    from copy import deepcopy
    from dataclasses import replace
    from app.task_audio import media_digest
    from test_task_media_grants import _visual_scope_case
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    try:
        queued = client.post('/api/creation-tasks/task/visual/preview', json={
            'base_version_id':'timeline', 'clip_ids':['clip_1'], 'effects':{'exposure':.15}})
        assert queued.status_code == 202
        assert runtime.run_once()
        job, event = media.read('local', queued.json()['job_id'])
        legacy = deepcopy(event)
        del legacy['arguments']['_clip_preview']
        legacy['input_facts'] = {key:legacy['input_facts'][key] for key in ('base_version_id', 'clip', 'aspect_ratio', 'source')}
        digest = media_digest({key:value for key,value in legacy.items() if key != 'input_digest'})
        legacy['input_digest'] = digest
        old_job = replace(job, input_digest=digest)
        assert media.check(old_job, legacy, actor) == 'ready'
        assert media.check(replace(old_job, status='queued'), legacy, actor) == 'stale'
        with monkeypatch.context() as scoped:
            scoped.setattr(media, 'read', lambda *args:(old_job, legacy))
            with pytest.raises(PermissionError, match='仅供回读'):
                media.verify_retry(SimpleNamespace(workspace_id='local', owner_id=job.id), actor, 'result_invalid', None)
        source = repo.resolve_clip_media_reference('local', 'asset', original['clips'][1]['source_path'])
        source.touch()
        assert media.check(old_job, legacy, actor) == 'stale'
    finally:
        runtime.stop(0)


@pytest.mark.parametrize('route', ['visual', 'exact', 'command'])
@pytest.mark.parametrize('race_at', ['adapter', 'job_commit', 'proposal_commit'])
def test_preview_version_race_rejects_original_base_without_job_or_proposal(tmp_path, monkeypatch, route, race_at):
    from test_task_media_grants import _visual_scope_case
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    def commit_other_edit():
        repo.timeline_records.create_timeline_version(TimelineVersion('next', 'local', 'task', 'timeline', 2, {'montage':original}, None))
    try:
        job_count = len(repo.job_records.list_job_runs('local', 'task'))
        intent_count = len(repo.edit_records.list_edit_intents('local', 'task'))
        proposal_count = len(repo.edit_records.list_edit_proposals('local', 'task'))
        if race_at == 'adapter':
            from app.creation.clip_preview import ClipPreviewUseCase
            prepare = ClipPreviewUseCase.prepare
            def after_initial_validation(self, original_actor, command):
                prepared = prepare(self, original_actor, command)
                assert command.base_version_id == 'timeline'
                commit_other_edit()
                return prepared
            monkeypatch.setattr(ClipPreviewUseCase, 'prepare', after_initial_validation)
        elif race_at == 'job_commit':
            facts = media.facts
            def after_sealing(*args):
                sealed = facts(*args)
                assert sealed[0]['base_version_id'] == 'timeline'
                commit_other_edit()
                return sealed
            monkeypatch.setattr(media, 'facts', after_sealing)
        else:
            save = repo.edit_adoption.save_clip_preview_proposal
            def before_proposal_write(*args, **kwargs):
                assert kwargs['expected_version_id'] == 'timeline'
                commit_other_edit()
                return save(*args, **kwargs)
            monkeypatch.setattr(repo.edit_adoption, 'save_clip_preview_proposal', before_proposal_write)
        if route == 'visual':
            path = '/api/creation-tasks/task/visual/preview'
            payload = {'base_version_id':'timeline', 'clip_ids':['clip_1'], 'effects':{'exposure':.15}}
        elif route == 'exact':
            path = '/api/creation-tasks/task/edits/exact/preview'
            payload = {'operation_type':'AdjustVisualEffects', 'clip_ids':['clip_1'], 'effects':{'exposure':.15},
                'expected_timeline_version_id':'timeline', 'expected_timeline_sequence':1}
        else:
            path = '/api/creation-tasks/task/edits/preview'
            payload = {'raw_command':'曝光设为0.15', 'context_stage':'montage', 'selected_object_refs':['clip:clip_1']}
        if race_at == 'proposal_commit':
            path += '?draft=true'
        response = client.post(path, json=payload)
        assert response.status_code == 409, response.text
        assert repo.task_records.get_creation_task('local', 'task').current_timeline_version_id == 'next'
        assert len(repo.job_records.list_job_runs('local', 'task')) == job_count
        assert len(repo.edit_records.list_edit_intents('local', 'task')) == intent_count
        assert len(repo.edit_records.list_edit_proposals('local', 'task')) == proposal_count
        assert repo.get_creation_task_montage_plan('local', 'task') == original
        assert not runtime.run_once()
    finally:
        runtime.stop(0)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),reason='FFmpeg required for bounded clip verification')
def test_change_crop_on_long_task_only_renders_selected_silent_window(tmp_path,monkeypatch):
    repo,actor,state,factory,build,media=case(tmp_path)
    library=tmp_path/'library'
    library.mkdir(exist_ok=True)
    source=library/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=64x64:r=30:d=6',
        '-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(source)],check=True,capture_output=True,timeout=5)
    asset=MaterialAsset(id='asset',workspace_id='local',file_name=source.name,path=str(source),
        size_bytes=source.stat().st_size,modified_at=datetime.fromtimestamp(source.stat().st_mtime,timezone.utc),
        duration_seconds=6,usage_role='b_roll',review_status='approved',analysis_status='ready',media_type='video',
        owner_user_id=actor.member_id,segments=[MaterialSegment(id='segment',asset_id='asset',start_seconds=0,end_seconds=6,
            description='synthetic frame',review_status='approved',usage_role='b_roll')])
    repo._save_asset(asset)
    reference=repo.workspace_media_reference_for_asset('local','asset')
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn,workspace_id='local',reference=reference)
    clips=[MontageClip('clip','cue','asset','segment',reference,0,6,0,6,1,[],False),
           MontageClip('other','othercue','asset','segment',reference,0,6,6,27,1,[],False)]
    plan=CreationTaskMontagePlan(id='montage',workspace_id='local',task_id='task',script_id='script',script_title='long fixture',
        status='ready',cues=[NarrationCue('cue','section','first',6),NarrationCue('othercue','section2','other',21)],
        clips=clips,duration_seconds=27,low_confidence_clip_count=0)
    with repo._connect() as conn:
        task=repo.task_records.get_creation_task('local','task')
        task.montage_plan_id='montage'
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id='local' AND id='task'",(json.dumps(asdict(task),default=str),))
        conn.execute("INSERT INTO creation_task_montage_plans (id,workspace_id,task_id,payload,updated_at) VALUES (?,?,?,?,?)",
            ('montage','local','task',json.dumps(creation_task_montage_plan_to_payload(plan)),task.updated_at.isoformat()))
    repo.timeline_records.create_timeline_version(TimelineVersion('timeline','local','task',None,1,{'montage':creation_task_montage_plan_to_payload(plan)},None))
    budget=CpuRenderBudget(max_duration_seconds=20.401,max_pixel_seconds=1920*1920*20.401,max_width_px=1920,max_height_px=1920,
        max_input_bytes=10000000,max_clip_count=64,max_overlay_count=16,max_output_bytes=2000000,max_threads=1,max_timeout_seconds=30,
        additional_memory_bytes=1,additional_disk_bytes=1,max_process_threads=16)  # Synthetic test profile.
    selected_budget_for = main._device_operation_budget
    monkeypatch.setattr(main, '_device_operation_budget',
        lambda name: budget if name in {'clip_preview', 'cpu_render'} else selected_budget_for(name))
    monkeypatch.setattr(main,'production_repo',repo)
    monkeypatch.setattr(main,'artifact_store',ArtifactStore(tmp_path/'output'))
    monkeypatch.setattr(main,'settings',replace(main.settings,temporary_root_path=str(tmp_path/'temp'),video_output_path=str(tmp_path/'output')))
    monkeypatch.setattr(main,'_require_permit_actor',lambda *args:actor)
    monkeypatch.setattr(main,'_assistant_member_id',lambda *args:actor.member_id)
    monkeypatch.setattr(main,'current_workspace_id',lambda request:'local')
    media.facts=main._task_media_facts
    from app.media.clip_preview import ClipPreviewExecutor
    from app.task_audio import TaskAudio
    media.clip_preview=ClipPreviewExecutor(main._clip_preview_use_case(),
        TaskAudio(repo,main.artifact_store,main.settings.effective_temporary_root_path))
    runtime=build(media)
    runtime.scheduler.processes=HostProcessProbe()
    monkeypatch.setattr(main,'_creation_runtime',lambda:runtime)
    client=TestClient(main.app)
    payload={'operation_type':'ChangeCrop','clip_id':'clip','fit_mode':'fit','crop_x':.5,'crop_y':.5}
    queued=client.post('/api/creation-tasks/task/edits/exact/preview',json=payload)
    assert queued.status_code==202,queued.text
    _,event=media.read('local',queued.json()['job_id'])
    assert event['resources']['duration_seconds']==5 and event['resources']['clip_count']==1
    assert event['resources']['overlay_count']==0 and event['resources']['silent'] is True
    assert len(event['input_facts']['source'])==4 and 'montage' not in event['input_facts']
    assert queued.json()['target_refs']==['clip:clip']
    assert runtime.run_once()
    done=client.get(queued.json()['result_url']).json()
    assert done['status']=='succeeded',done
    records=[record for record in repo.execution_records.list_artifact_records('local','task') if record.producer_job_id==queued.json()['job_id']]
    assert len(records)==2
    for record in records:
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type,width,height:format=duration',
            '-of','json',str(main.artifact_store.resolve_file(record.logical_ref))],capture_output=True,text=True,check=True,timeout=5)
        metadata=json.loads(probe.stdout)
        assert float(metadata['format']['duration'])<=5.1
        assert [stream['codec_type'] for stream in metadata['streams']]==['video']
    assert client.post('/api/creation-tasks/task/edits/exact/preview',json=payload).json()['job_id']==queued.json()['job_id']
    assert not runtime.run_once()
    count_before=len(repo.job_records.list_job_runs('local','task'))
    def no_runtime():
        raise AssertionError('metadata proposal must not construct the device runtime')
    monkeypatch.setattr(main,'_creation_runtime',no_runtime)
    metadata=client.post('/api/creation-tasks/task/edits/exact/preview',json={
        'operation_type':'SetClipLock','clip_id':'clip','locked':True})
    assert metadata.status_code==200,metadata.text
    assert len(repo.job_records.list_job_runs('local','task'))==count_before
    # Budget refusal concerns this declared window, independently of the 27s parent.
    host=HostResourceProbe(tmp_path/'budget.sqlite',disk_paths=(tmp_path,),budget=HostBudget(max_cpu_percent=100,
        min_available_memory_bytes=0,max_swap_used_bytes=10**12,min_free_disk_bytes=0,max_disk_io_bytes_per_second=10**12,
        max_unit_input_bytes=10**12,clip_preview=CpuRenderBudget(**{**asdict(budget),'max_duration_seconds':4})))
    assert host._sample(event['resources']).verdict is False
    runtime.stop(0)
