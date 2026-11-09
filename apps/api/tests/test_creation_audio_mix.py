"""Bounded synthetic checks for the existing render grant's audio consumer."""
import json
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.artifact_store import ArtifactStore
from app.audio_timeline import AudioTimeline, TimelineAudioClip, TimelineAudioTrack, render_audio_mix
from app.creation_execution import submit_creation_stage
from app.device_scheduler import RecoveryBlocked
from app.host_resource_probes import CpuAudioBudget, CpuRenderBudget, HostBudget, HostResourceProbe
from app.production import MaterialAsset
from app.linked_timeline import remove_time_ranges
from app.task_audio import TaskAudio, audio_mix_resources
from tests.host_memory_boundary import IsolatedMemoryMonitor, isolated_runtime_limits
from test_host_resource_probes import _render_budget
from test_creation_execution import _render_version_fixture
from _media_preview_test_fixtures import synthetic_wav_bytes


def audio_budget():
    # Isolated test envelope, never loaded into the product environment.
    return CpuAudioBudget(3, 8_000_000, 10, 10, 48000, 2, 4_000_000, 1, 30,
                          128 * 1024**2, 16, .05, 128 * 1024**2, 1_000_000)


def case(tmp_path, *, normalize=False, budget=True):
    repo, task_id, script_id, digest, actor, execution, owner, scheduler, base, members = _render_version_fixture(tmp_path)
    narration = repo.narration_artifacts.get_narration_artifact('local', 'cache_1')
    repo.narration_artifacts.save_narration_artifact(replace(narration, sample_rate=8000, duration_seconds=2))
    music = tmp_path / 'library/music.wav'
    music.write_bytes(synthetic_wav_bytes())
    repo._save_asset(MaterialAsset(id='music', workspace_id='local', file_name=music.name, path=str(music),
        size_bytes=music.stat().st_size, modified_at=datetime.now(timezone.utc), duration_seconds=2,
        usage_role='music', review_status='approved', analysis_status='ready', media_type='audio',
        owner_user_id=actor.member_id))
    reference = repo.workspace_media_reference_for_asset('local', 'music')
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id='local', reference=reference)
    graph = AudioTimeline(tracks=[
        TimelineAudioTrack(id='voice', name='voice', role='narration', clips=[TimelineAudioClip(
            id='voice-clip', name='voice', source_ref='narration://artifact/cache_1', source_start_ms=0, timeline_start_ms=0, source_end_ms=1000)]),
        TimelineAudioTrack(id='music', name='music', role='music', clips=[TimelineAudioClip(
            id='music-clip', name='music', source_ref=reference, source_start_ms=0, timeline_start_ms=0, source_end_ms=1000, gain_db=-6)])])
    payload = repo.get_creation_task_montage_plan('local', task_id)
    payload.update(audio_timeline=graph.model_dump(), narration_linkage_created_audio_timeline=normalize)
    repo.montage_plan_adoption.save_creation_task_montage_plan('local', task_id, 'montage_1', payload)
    repo.timeline_records.create_timeline_version(replace(base, id='timeline_audio', parent_version_id=base.id, sequence=2,
                                        snapshot={'montage': payload}))
    service = TaskAudio(repo, ArtifactStore(tmp_path/'artifacts'), tmp_path/'temp')
    cap = audio_budget() if budget else None
    owner.audio_mix_facts = lambda workspace, task, plan, *, actor: service.mix_facts(
        workspace, task, plan.audio_timeline, plan.duration_seconds, actor=actor,
        normalize_voice_sources=plan.narration_linkage_created_audio_timeline, budget=cap)
    render = CpuRenderBudget(10, 30_000_000, 1920, 1920, 8_000_000, 4, 4, 8_000_000, 1, 60, 1_000_000, 1_000_000, 16)  # Synthetic test profile.
    probe = HostResourceProbe(repo.database_path, disk_paths=(tmp_path,), budget=HostBudget(
        100, 1, 2**63, 0, 2**63, 8_000_000, sample_seconds=.01, cpu_render=replace(render,running_cpu_policy=_render_budget().running_cpu_policy), cpu_audio=cap),memory_monitor=IsolatedMemoryMonitor())
    scheduler.resources = probe
    return repo, task_id, script_id, digest, actor, execution, owner, scheduler, members, service, graph, music


@pytest.mark.parametrize('normalize', [False, True])
def test_composite_grant_seals_real_sources_and_runs_bounded_audio(tmp_path, normalize):
    repo, task_id, script_id, digest, actor, execution, owner, scheduler, _, service, graph, _ = case(tmp_path, normalize=normalize)
    try:
        job = execution.start('local', task_id, script_id, digest, actor=actor)
        request_id = submit_creation_stage(execution, scheduler, job, actor)
        grant = scheduler.pick_and_grant()
        assert grant is not None and grant.request_id == request_id
        declared = scheduler.resources._grant_resources(grant)['audio_mix']
        assert declared['input_count'] == 2 and len(declared['inputs']) == 2
        assert all(len(item['sha256']) == 64 for item in declared['inputs'])
        assert declared['normalize_voice_sources'] is normalize
        render_limits = scheduler.resources.scene_runtime_limits(grant)
        assert render_limits.max_process_rss_bytes == scheduler.resources.budget.cpu_render.additional_memory_bytes
        assert render_limits.max_process_threads == scheduler.resources.budget.cpu_render.max_process_threads
        assert scheduler.resources.scene_runtime_limits(grant, purpose='audio_mix').max_process_rss_bytes == audio_budget().max_process_rss_bytes
        if normalize:
            assert declared['output_duration_limit_seconds'] == 2  # Whole voice exceeds the one-second movie.
            assert declared['temporary_output_bytes'] == 3 * 192000 + 2 * 65536
        sources = {item['reference']: service.source('local', task_id, item['reference'], actor=actor) for item in declared['inputs']}
        output = render_audio_mix(graph, 1, sources.__getitem__, tmp_path/'mix.wav',
            scheduler=scheduler, grant=grant, deadline_monotonic=time.monotonic()+30,
            max_output_bytes=declared['max_output_bytes'], threads=declared['threads'], normalize_voice_sources=normalize)
        assert output.is_file() and output.stat().st_size <= declared['mix_output_bytes']
        scheduler.assert_grant(grant)
        scheduler.defer_unit(grant, reason='worker_interrupted',
            retry_not_before_utc=datetime.now(timezone.utc)+timedelta(seconds=30))
        scheduler.assert_released(grant)
    finally:
        scheduler.close()


def test_same_length_music_rewrite_invalidates_original_grant(tmp_path):
    repo, task_id, script_id, digest, actor, execution, _, scheduler, _, _, _, music = case(tmp_path)
    try:
        job = execution.start('local', task_id, script_id, digest, actor=actor)
        submit_creation_stage(execution, scheduler, job, actor)
        grant = scheduler.pick_and_grant()
        before = music.stat()
        data = bytearray(music.read_bytes())
        data[-2] ^= 1
        music.write_bytes(data)
        os.utime(music, ns=(before.st_atime_ns, before.st_mtime_ns))
        with pytest.raises(PermissionError):
            scheduler.assert_grant(grant)
        scheduler.defer_unit(grant, reason='input_stale')
        scheduler.assert_released(grant)
    finally:
        scheduler.close()


def test_missing_audio_budget_marks_registered_job_rejected_without_queue(tmp_path):
    repo, task_id, script_id, digest, actor, execution, _, scheduler, _, _, _, _ = case(tmp_path, budget=False)
    try:
        job = execution.start('local', task_id, script_id, digest, actor=actor)
        with pytest.raises(ValueError, match='音频处理预算尚未标定'):
            submit_creation_stage(execution, scheduler, job, actor)
        saved = repo.job_records.get_job_run('local', job.id)
        assert saved.status == 'failed' and '音频处理预算尚未标定' in saved.failure_reason
        assert saved.impact and saved.repair_action
        with repo._connect() as conn:
            assert conn.execute('SELECT count(*) FROM device_work_requests').fetchone()[0] == 0
        event = repo.execution_records.get_task_event('local', f'event_{job.id}_admission_failed')
        assert event.payload['status'] == 'failed'
    finally:
        scheduler.close()


def test_unknown_submission_does_not_invent_failed_state(tmp_path, monkeypatch):
    repo, task_id, script_id, digest, actor, execution, _, scheduler, _, _, _, _ = case(tmp_path)
    try:
        job = execution.start('local', task_id, script_id, digest, actor=actor)
        monkeypatch.setattr(scheduler, 'submit', lambda *args: (_ for _ in ()).throw(OSError('transport unknown')))
        with pytest.raises(OSError, match='transport unknown'):
            submit_creation_stage(execution, scheduler, job, actor)
        assert repo.job_records.get_job_run('local', job.id).status == 'queued'
        assert not any(event.id == f'event_{job.id}_admission_failed' for event in repo.execution_records.list_task_events('local', task_id))
    finally:
        scheduler.close()


def test_composite_missing_specs_or_purpose_cannot_borrow_render_budget(tmp_path, monkeypatch):
    repo, task_id, _, _, actor, _, owner, scheduler, _, _, _, _ = case(tmp_path)
    try:
        task = repo.task_records.get_creation_task('local', task_id)
        plan = repo.plan_records.get_creation_plan('local', task.plan_id)
        with repo._connect() as conn:
            _, resources = owner.render_facts(conn, task, plan, actor=actor)
        no_audio_budget = HostResourceProbe(repo.database_path, disk_paths=(tmp_path,),
            budget=replace(scheduler.resources.budget, cpu_audio=None))
        assert no_audio_budget._sample(resources).reason == 'audio_budget_or_execution_mode_unavailable'
        resources['audio_mix'].pop('source_sample_rate_limit')
        assert scheduler.resources._sample(resources).reason == 'audio_resource_fields_invalid'
        monkeypatch.setattr(scheduler.resources, '_grant_resources', lambda grant: resources)
        with pytest.raises(RecoveryBlocked):
            scheduler.resources.scene_runtime_limits(SimpleNamespace(), purpose='audio_mix')
        with pytest.raises(RecoveryBlocked):
            scheduler.resources.scene_runtime_limits(SimpleNamespace(), purpose='fake')
    finally:
        scheduler.close()


def test_linkage_bounds_reserve_whole_source_and_reject_missing_duration():
    graph = AudioTimeline(tracks=[TimelineAudioTrack(id='v', name='voice', role='narration', clips=[TimelineAudioClip(
        id='v1', name='voice', source_ref='workspace://voice.wav', source_start_ms=0, timeline_start_ms=0, source_end_ms=1000)])])
    source = {'reference':'workspace://voice.wav', 'path':'fixed.wav', 'uses':1, 'size_bytes':100,
              'duration_seconds':3, 'sample_rate':48000, 'channels':2}
    facts = audio_mix_resources([source], [graph], 1, normalize_voice_sources=True, budget=audio_budget())
    assert facts['output_duration_limit_seconds'] == 3
    assert facts['temporary_output_bytes'] == 4 * 192000 + 2 * 65536
    with pytest.raises(ValueError, match='缺少已保存的时长'):
        audio_mix_resources([dict(source, duration_seconds=None)], [graph], 1,
                            normalize_voice_sources=True, budget=audio_budget())


@pytest.mark.parametrize('exited', [True, False])
def test_short_registered_audio_child_requires_real_exit_receipt(monkeypatch, exited):
    import subprocess
    import psutil
    import app.granted_subprocess as runner
    from app.granted_subprocess import GrantedCommandResourceUnavailable
    calls = []
    def wait(timeout):
        calls.append(timeout)
        if not exited:
            raise subprocess.TimeoutExpired('registered child', timeout)
        return 0
    def disappeared():
        raise psutil.NoSuchProcess(42)
    process = SimpleNamespace(pid=42, poll=lambda: None, wait=wait)
    current = SimpleNamespace(pid=42, memory_info=disappeared, create_time=lambda: 1.)
    child = SimpleNamespace(pid=42, start_token='1.000000')
    monkeypatch.setattr(psutil, 'Process', lambda pid: current)
    monkeypatch.setattr(psutil, 'process_iter', lambda: [current])
    monkeypatch.setattr(runner.os, 'getpgid', lambda pid: 42)
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=1000))
    monkeypatch.setattr(psutil, 'swap_memory', lambda: SimpleNamespace(used=0))
    limits = isolated_runtime_limits(1000, 2, 100, 100, .05)
    if exited:
        runner._check_scene_runtime(process, child, limits)
    else:
        with pytest.raises(GrantedCommandResourceUnavailable, match='disappeared'):
            runner._check_scene_runtime(process, child, limits)
    assert calls == [.01]


def test_main_render_consumer_reads_current_actor_and_same_sealed_mix(tmp_path, monkeypatch):
    import app.main as main
    from app.creation_execution import CpuRenderGrantContext
    from app.montage import creation_task_montage_plan_from_payload
    from app.audio_timeline import probe_audio_metadata
    repo, task_id, script_id, digest, actor, execution, owner, scheduler, _, service, _, _ = case(tmp_path)
    try:
        job = execution.start('local', task_id, script_id, digest, actor=actor)
        submit_creation_stage(execution, scheduler, job, actor)
        grant = scheduler.pick_and_grant()
        context = CpuRenderGrantContext(scheduler, grant, time.monotonic()+30, 8_000_000, 1,
            started_monotonic=time.monotonic()-31)
        monkeypatch.setattr(main, 'production_repo', repo)
        monkeypatch.setattr(main, 'artifact_store', service.artifact_store)
        monkeypatch.setattr(main, 'settings', replace(main.settings,video_inbox_path=str(repo.video_library_path),
            video_output_path=str(tmp_path/'artifacts'),temporary_root_path=str(tmp_path/'temp')))
        monkeypatch.setattr(main, '_verify_creation_identity', owner.verify_identity)
        plan = creation_task_montage_plan_from_payload(repo.get_creation_task_montage_plan('local', task_id))
        facts, _ = main._granted_audio_mix_sources('local', plan, context)
        assert facts['resources'] == scheduler.resources._grant_resources(grant)['audio_mix']
        rendered = []
        def video_stub(plan, audio, **options):
            # Actual PCM consumer verification only; this stub is not video acceptance.
            measured = probe_audio_metadata(audio, scheduler=scheduler, grant=grant,
                deadline_monotonic=context.deadline_monotonic, output=True)
            assert measured == {'duration_seconds':1., 'sample_rate':48000, 'channels':2}
            assert options['grant'].request_id == grant.request_id
            rendered.append(audio)
            return plan
        monkeypatch.setattr(main, 'montage_renderer', SimpleNamespace(render=video_stub))
        staging = tmp_path/'render-stage'
        staging.mkdir()
        original_refs = [clip.source_path for clip in plan.clips]
        main._picture_preview_renderer().render_montage('local', plan, tmp_path/'library/audio.wav',
            job_staging_path=staging, render_context=context, subtitle_tracks=[])
        assert rendered == [staging/'timeline-mix.wav']
        assert [clip.source_path for clip in plan.clips] == original_refs
        scheduler.defer_unit(grant, reason='worker_interrupted',
            retry_not_before_utc=datetime.now(timezone.utc)+timedelta(seconds=30))
        scheduler.assert_released(grant)
    finally:
        scheduler.close()


def test_long_fade_preview_seals_scoped_audio_and_whole_source_linkage(tmp_path, monkeypatch):
    import app.main as main
    from _media_preview_test_fixtures import configure_synthetic_media_budgets
    repo, task_id, _, _, actor, _, _, scheduler, _, service, _, _ = case(tmp_path, normalize=True)
    try:
        monkeypatch.setattr(main, 'production_repo', repo)
        monkeypatch.setattr(main, 'artifact_store', service.artifact_store)
        monkeypatch.setattr(main, 'settings', replace(main.settings,video_inbox_path=str(repo.video_library_path),
            video_output_path=str(tmp_path/'artifacts'),temporary_root_path=str(tmp_path/'temp')))
        configure_synthetic_media_budgets(monkeypatch, main)
        selected_budget = main._device_host_budget()
        monkeypatch.setattr(main, '_device_host_budget', lambda: replace(selected_budget, cpu_audio=audio_budget()))
        payload = repo.get_creation_task_montage_plan('local', task_id)
        original = payload['clips'][0]
        payload['clips'] = [
            {**original, 'id':'before', 'locked':True},
            {**original, 'timeline_start_seconds':1, 'timeline_end_seconds':7,
                'source_end_seconds':6, 'visual_effects':{'fade_out_seconds':.4}},
            {**original, 'id':'outside', 'timeline_start_seconds':7, 'timeline_end_seconds':27, 'locked':True}]
        payload['duration_seconds'] = 27
        graph = service.repository.get_creation_task_montage_plan('local', task_id)['audio_timeline']
        for track in graph['tracks']:
            track['locked'] = True
            track['clips'][0].update(source_end_ms=2000, playback_rate=.25, locked=True)
        payload['audio_timeline'] = graph
        repo.montage_plan_adoption.save_creation_task_montage_plan('local', task_id, 'montage_1', payload)
        payload = repo.get_creation_task_montage_plan('local', task_id)
        task = repo.task_records.get_creation_task('local', task_id)
        facts, resources = main._task_media_facts('local', task_id, 'exact_edit_preview',
            {'payload':{'operation_type':'AdjustVisualEffects', 'clip_ids':['clip_1'], 'effects':{'fade_out_seconds':0},
                'expected_timeline_version_id':task.current_timeline_version_id, 'expected_timeline_sequence':2},
             '_media_kind':'picture_preview'}, actor)
        assert resources['kind'] == 'creation_render'
        assert resources['duration_seconds'] == 6 and resources['clip_count'] == 1
        assert facts['visual_preview']['representative_clip_id'] == 'clip_1'
        assert resources['audio_mix']['input_count'] == 2
        assert resources['audio_mix']['normalize_voice_sources'] is True
        assert resources['audio_mix']['temporary_output_bytes'] > resources['audio_mix']['mix_output_bytes']
        assert facts['audio_mix_candidates'] == resources['audio_mix_candidates']
        assert all(item['resources']['normalize_voice_sources'] for item in resources['audio_mix_candidates'])
        assert all(item['resources']['inputs'][0]['duration_seconds'] == 2
                   for item in resources['audio_mix_candidates'])
        assert repo.get_creation_task_montage_plan('local', task_id) == payload
    finally:
        scheduler.close()


def test_existing_blocked_request_is_not_invented_as_never_submitted_failure(tmp_path):
    repo, task_id, script_id, digest, actor, execution, owner, scheduler, _, _, _, _ = case(tmp_path)
    try:
        job = execution.start('local', task_id, script_id, digest, actor=actor)
        request_id = submit_creation_stage(execution, scheduler, job, actor)
        grant = scheduler.pick_and_grant()
        scheduler.defer_unit(grant, reason='worker_interrupted',
            retry_not_before_utc=datetime.now(timezone.utc)+timedelta(seconds=30))
        owner.audio_mix_facts = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('audio input changed'))
        with pytest.raises(ValueError, match='audio input changed'):
            submit_creation_stage(execution, scheduler, job, actor)
        assert repo.job_records.get_job_run('local', job.id).status == 'queued'
        assert not any(event.id == f'event_{job.id}_admission_failed' for event in repo.execution_records.list_task_events('local', task_id))
        with repo._connect() as conn:
            pending = conn.execute('SELECT state,wait_reason FROM device_work_requests WHERE id=?',(request_id,)).fetchone()
        assert dict(pending) == {'state':'blocked', 'wait_reason':'worker_interrupted'}
    finally:
        scheduler.close()


def test_compound_music_and_subtitle_previews_seal_before_and_after_audio(tmp_path, monkeypatch):
    import app.main as main
    from app.montage import creation_task_montage_plan_from_payload
    from _media_preview_test_fixtures import configure_synthetic_media_budgets
    repo, task_id, _, _, actor, _, _, scheduler, _, service, _, _ = case(tmp_path)
    try:
        monkeypatch.setattr(main, 'production_repo', repo)
        monkeypatch.setattr(main, 'artifact_store', service.artifact_store)
        monkeypatch.setattr(main, 'settings', replace(main.settings,video_inbox_path=str(repo.video_library_path),
            video_output_path=str(tmp_path/'artifacts'),temporary_root_path=str(tmp_path/'temp')))
        configure_synthetic_media_budgets(monkeypatch, main)
        selected_budget = main._device_host_budget()
        monkeypatch.setattr(main, '_device_host_budget', lambda: replace(selected_budget, cpu_audio=audio_budget()))
        task = repo.task_records.get_creation_task('local', task_id)
        command = '音乐小一点，同时字幕字号改成64'
        args = {'payload':{'raw_command':command, 'context_stage':'montage', 'selected_object_refs':[],
                         'playhead_ms':None}, '_media_kind':'picture_preview'}
        facts, resources = main._task_media_facts('local', task_id, 'command_edit_preview', args, actor)
        plan = creation_task_montage_plan_from_payload(repo.get_creation_task_montage_plan('local', task_id))
        after, _ = main.compile_compound_edit(command, plan, plan.audio_timeline, [], task.current_timeline_version_id, None)
        expected_keys = {main.media_digest([candidate.audio_timeline.model_dump(), candidate.duration_seconds,
            candidate.narration_linkage_created_audio_timeline]) for candidate in [plan, after]}
        assert len(expected_keys) == 2
        assert {item['key'] for item in resources['audio_mix_candidates']} == expected_keys
        assert facts['audio_mix_candidates'] == resources['audio_mix_candidates']
        assert resources['audio_mix']['input_count'] == 2
    finally:
        scheduler.close()


@pytest.mark.parametrize('mutation', ['none', 'gain', 'missing_analysis', 'uses', 'video_uses'])
def test_dynamic_silence_after_is_exact_measured_derivative_with_finite_uses(tmp_path, monkeypatch, mutation):
    import app.main as main
    from app.creation_execution import CpuRenderGrantContext
    from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
    from app.creation.contracts import TaskEvent
    from app.task_audio import TaskMediaJobs, TASK_MEDIA_CONTEXT
    from _media_preview_test_fixtures import configure_synthetic_media_budgets
    repo, task_id, _, _, actor, _, owner, scheduler, _, service, _, _ = case(tmp_path, normalize=True)
    token = None
    try:
        # A single audible voice splits into two uses. Music remains muted.
        payload = repo.get_creation_task_montage_plan('local', task_id)
        payload['audio_timeline']['tracks'][1]['muted'] = True
        repo.montage_plan_adoption.save_creation_task_montage_plan('local', task_id, 'montage_1', payload)
        current = repo.timeline_records.get_timeline_version('local', 'timeline_audio')
        repo.timeline_records.create_timeline_version(replace(current, id='timeline_silence', parent_version_id=current.id, sequence=3, snapshot={'montage':payload}))
        monkeypatch.setattr(main, 'production_repo', repo)
        monkeypatch.setattr(main, 'artifact_store', service.artifact_store)
        monkeypatch.setattr(main, 'settings', replace(main.settings,video_inbox_path=str(repo.video_library_path),
            video_output_path=str(tmp_path/'artifacts'),temporary_root_path=str(tmp_path/'temp')))
        monkeypatch.setattr(main, '_verify_creation_identity', owner.verify_identity)
        configure_synthetic_media_budgets(monkeypatch, main)
        selected_budget = main._device_host_budget()
        monkeypatch.setattr(main, '_device_host_budget', lambda: replace(selected_budget,
            cpu_audio=audio_budget(), cpu_render=scheduler.resources.budget.cpu_render))
        args = {'payload':{'raw_command':'剪掉当前镜头停顿', 'context_stage':'montage',
                          'selected_object_refs':['clip:clip_1'], 'playhead_ms':None}, '_media_kind':'picture_preview'}
        media = TaskMediaJobs(repo, facts=main._task_media_facts,
                             command_preview=main._command_preview_use_case(repo),verify_identity=owner.verify_identity)
        owner.task_media = media
        job = media.submit(SimpleNamespace(scheduler=scheduler, wake=lambda:None), actor, task_id, 'command_edit_preview', args, 'edit_preview')
        grant = scheduler.pick_and_grant()
        assert grant is not None
        repo.job_records.claim_job_run('local', job.id)
        context = CpuRenderGrantContext(scheduler, grant, time.monotonic()+30, 8_000_000, 1)
        token = TASK_MEDIA_CONTEXT.set((context, job, media.read('local',job.id)[1], actor))
        before = creation_task_montage_plan_from_payload(repo.get_creation_task_montage_plan('local', task_id))
        graph = before.audio_timeline
        analysis = service.publish('local', task_id, kind='audio_preview',
            parameters={'action':'silence_analysis', 'clip_id':'clip_1'},
            input_refs=['narration://artifact/cache_1'], operation='audio_mix',
            media_input={'audio_timeline':graph.model_dump(), 'duration_seconds':1,
                        'normalize_voice_sources':True, 'start_seconds':0, 'end_seconds':1, 'solo_track_ids':['voice']})
        transform = service._transform_for('local', task_id, analysis)
        cuts = [[300,600]]
        after, tracks = remove_time_ranges(before, [], graph, cuts)
        repo.execution_records.append_task_event(TaskEvent(id=f'event_{job.id}_{analysis.id}_silence_detected', workspace_id='local',
            task_id=task_id, job_id=job.id, event_type='silence_analysis_result', payload={
                'base_version_id':'timeline_silence', 'clip_id':'clip_1', 'analysis_ref':analysis.logical_ref,
                'analysis_sha256':analysis.sha256, 'transform_id':transform.id, 'removed_ranges_ms':cuts}))
        if mutation == 'gain':
            after.audio_timeline.tracks[0].gain_db = -12
        actual = {'operation':'picture_preview', 'parameters':{'side':'after','removed_ranges_ms':cuts,
                  'analysis_ref':analysis.logical_ref if mutation != 'missing_analysis' else 'artifacts/absent.wav'},
                  'media_input':{'plan':creation_task_montage_plan_to_payload(after), 'subtitle_tracks':[]}}
        repo.execution_records.append_task_event(TaskEvent(id='actual-after-input', workspace_id='local', task_id=task_id, job_id=job.id,
                                        event_type='task_media_input', payload=actual))
        if mutation in {'uses', 'video_uses'}:
            # The existing calibrated profile remains three; a smaller sealed
            # declaration cannot be bypassed by the derived renderer either.
            original = scheduler.resources._grant_resources
            monkeypatch.setattr(scheduler.resources, '_grant_resources', lambda grant:
                {**original(grant), 'audio_mix':{**original(grant)['audio_mix'], 'dynamic_input_count_limit':1}})
            with repo._connect() as conn:
                resources = json.loads(conn.execute('SELECT resources_json FROM device_work_requests WHERE id=?',
                    (grant.request_id,)).fetchone()[0])
            facts = service.mix_facts('local', task_id, after.audio_timeline, after.duration_seconds, actor=actor,
                                     normalize_voice_sources=True, budget=audio_budget())
            if mutation == 'uses':
                resources['audio_mix']['dynamic_input_count_limit'] = 1
            else:
                resources['dynamic_clip_count_limit'] = 1
            with pytest.raises(ValueError, match='音频上界|成片上界'):
                main._verify_silence_audio_candidate('local', after, context, resources, facts, actor)
        elif mutation != 'none':
            with pytest.raises(ValueError):
                main._granted_audio_mix_sources('local', after, context)
        else:
            facts, sources = main._granted_audio_mix_sources('local', after, context)
            assert facts['resources']['input_count'] == 2
            assert set(sources) == {'narration://artifact/cache_1', graph.tracks[1].clips[0].source_ref}
            output = render_audio_mix(after.audio_timeline, after.duration_seconds, sources.__getitem__, tmp_path/'after.wav',
                scheduler=scheduler, grant=grant, deadline_monotonic=context.deadline_monotonic,
                max_output_bytes=facts['resources']['max_output_bytes'], threads=1, normalize_voice_sources=True)
            assert output.is_file()
        scheduler.defer_unit(grant, reason='worker_interrupted',
            retry_not_before_utc=datetime.now(timezone.utc)+timedelta(seconds=30))
        scheduler.assert_released(grant)
    finally:
        if token is not None:
            TASK_MEDIA_CONTEXT.reset(token)
        scheduler.close()


def test_audio_sections_share_active_allowance_but_exclude_video_time(monkeypatch):
    import app.audio_timeline as audio_module
    import app.creation_execution as execution_module
    from app.audio_timeline import bounded_audio_context
    from app.creation_execution import CpuRenderGrantContext
    from app.granted_subprocess import GrantedCommandTimeout
    clock = [100.]
    timer = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(audio_module, 'time', timer)
    monkeypatch.setattr(execution_module, 'time', timer)
    monkeypatch.setattr(audio_module, '_audio_grant_resources', lambda *_: {
        'kind':'creation_render', 'audio_mix':{'timeout_seconds':15., 'threads':1, 'max_output_bytes':899072}})
    checked = []
    grant = object()
    scheduler = SimpleNamespace(assert_grant=lambda value: checked.append(value))
    context = CpuRenderGrantContext(scheduler, grant, 160., 8_000_000, 2, started_monotonic=100.)
    with bounded_audio_context(context) as audio:
        assert (audio.deadline_monotonic, audio.max_output_bytes, audio.threads, audio.resource_purpose) == (
            115., 899072, 1, 'audio_mix')
        # Probe, normalization and mix use this section's fixed clock.
        for stage_time in (101., 102., 104.):
            clock[0] = stage_time
            audio.check()
            assert audio.deadline_monotonic == 115.
    clock[0] = 124.  # Video took 20 seconds, consuming only the outer allowance.
    with bounded_audio_context(replace(context, max_output_bytes=4_000_000)) as audio:
        assert audio.deadline_monotonic == 135.  # 15 - 4 active seconds remain.
        clock[0] = 130.
    clock[0] = 140.
    with pytest.raises(GrantedCommandTimeout):
        with bounded_audio_context(context) as audio:
            assert audio.deadline_monotonic == 145.
            clock[0] = 145.
            audio.check()
    with pytest.raises(GrantedCommandTimeout):
        with bounded_audio_context(context):
            pytest.fail('exhausted audio allowance must not start another section')
    context.check()  # Audio exhaustion does not masquerade as the outer/video deadline.
    assert checked and all(value is grant for value in checked)


@pytest.mark.parametrize('failure', ['error', 'cancelled'])
def test_failed_audio_section_preserves_consumed_time(monkeypatch, failure):
    import threading
    import app.audio_timeline as audio_module
    import app.creation_execution as execution_module
    from app.audio_timeline import bounded_audio_context
    from app.creation_execution import CpuRenderGrantContext
    from app.granted_subprocess import GrantedCommandCancelled, GrantedCommandTimeout
    clock = [100.]
    timer = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(audio_module, 'time', timer)
    monkeypatch.setattr(execution_module, 'time', timer)
    monkeypatch.setattr(audio_module, '_audio_grant_resources', lambda *_: {
        'kind':'creation_render', 'audio_mix':{'timeout_seconds':15., 'threads':1, 'max_output_bytes':899072}})
    cancelled = threading.Event()
    context = CpuRenderGrantContext(SimpleNamespace(assert_grant=lambda _: None), object(), 125.,
        8_000_000, 1, cancelled, started_monotonic=100.)
    with pytest.raises(ValueError if failure == 'error' else GrantedCommandCancelled):
        with bounded_audio_context(context):
            clock[0] = 105.
            if failure == 'error':
                raise ValueError('measured audio failure')
            cancelled.set()
    cancelled.clear()
    clock[0] = 108.
    with bounded_audio_context(context) as audio:
        assert audio.deadline_monotonic == 118.
        clock[0] = 110.
    clock[0] = 122.
    # Remaining audio would allow eight seconds, but the original outer deadline wins.
    with pytest.raises(GrantedCommandTimeout):
        with bounded_audio_context(context) as audio:
            assert audio.deadline_monotonic == 125.
            clock[0] = 125.
