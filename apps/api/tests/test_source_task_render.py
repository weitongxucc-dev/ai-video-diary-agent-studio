"""Source video uses real FFmpeg, the same queue and verified output publication."""
from array import array
from types import SimpleNamespace
from uuid import uuid4
import json
import subprocess
import time

import pytest

import app.main as main
from app.artifact_store import ArtifactStore
from app.creation.source_task_use_case import SourceTaskCommand, SourceTaskUseCase
from app.creation_execution import CreationJobUnitExecutor, submit_creation_stage
from app.host_resource_probes import CpuAudioBudget, CpuRenderBudget, HostBudget, HostResourceProbe
from app.task_audio import TaskAudio
from app.video_jobs import VideoJobRunner
from tests.host_memory_boundary import IsolatedMemoryMonitor
from test_creation_execution import _render_version_fixture
from test_host_resource_probes import _render_budget


@pytest.mark.parametrize('ripple_cut', [False, True])
def test_source_video_renders_original_sound_without_script_or_narration(tmp_path, monkeypatch, ripple_cut):
    repo, _, _, _, actor, execution, owner, scheduler, _, members = _render_version_fixture(tmp_path)
    store = ArtifactStore(tmp_path / 'source-artifacts')
    settings = SimpleNamespace(video_output_path=str(tmp_path/'source-output'),
        video_inbox_path=str(tmp_path/'library'), effective_temporary_root_path=tmp_path/'source-temp',
        native_portrait_worker_path=None)
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'artifact_store', store)
    monkeypatch.setattr(main, 'video_job_runner', VideoJobRunner(repo.job_records))
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'local_members_service', members)
    asset = repo.get_asset('local', 'asset_1')
    receipt = SourceTaskUseCase(repo, lambda current: 'ready' if current == actor else 'unauthorized').create(
        SourceTaskCommand(str(uuid4()), asset.id, asset.updated_at, 0, 2, '原片真实音轨', '16:9', True), actor)
    task_id = receipt['task_id']
    expected_duration = 2
    if ripple_cut:
        from app.creation.contracts import EditCommand
        task = repo.task_records.get_creation_task('local', task_id)
        outcome = main._linked_timeline_use_case().apply(EditCommand('local', task_id,
            {'member_id': actor.member_id, 'local_diagnostic': False}, task.current_timeline_version_id,
            'RemoveTimelineRanges', {'range_edit': {'removed_ranges_ms': [[500, 1000]],
                'reason': '删除已定位停顿，成片须使用实际采用的精剪时钟'}}))
        expected_duration = 1.5
        assert outcome.montage.duration_seconds == expected_duration
        assert repo.plan_records.get_creation_plan('local', task.plan_id).duration_target_seconds == 2
    audio = TaskAudio(repo, store, settings.effective_temporary_root_path)
    cap = CpuAudioBudget(3, 8_000_000, 60, 10, 48000, 2, 4_000_000, 1, 60,
        512*1024**2, 16, .05, 1024**3, 1_000_000)
    owner.audio_mix_facts = lambda ws, task, montage, *, actor, connection=None: audio.mix_facts(ws, task,
        montage.audio_timeline, montage.duration_seconds, actor=actor,
        normalize_voice_sources=montage.narration_linkage_created_audio_timeline, budget=cap, connection=connection)
    render_cap = CpuRenderBudget(10, 30_000_000, 1920, 1920, 8_000_000, 4, 0, 8_000_000, 1, 60,
        512*1024**2, 64*1024**2, 16, running_cpu_policy=_render_budget().running_cpu_policy)
    scheduler.resources = HostResourceProbe(repo.database_path, disk_paths=(tmp_path,),
        budget=HostBudget(100, 1, 2**63, 0, 2**63, 8_000_000, sample_seconds=.01,
            cpu_render=render_cap, cpu_audio=cap), memory_monitor=IsolatedMemoryMonitor())
    try:
        snapshot = repo.execution_inputs.read_creation_execution_snapshot('local', task_id)
        assert snapshot.script_header is None
        job = execution.start('local', task_id, None, snapshot.input_digest, actor=actor)
        request_id = submit_creation_stage(execution, scheduler, job, actor)
        deadline = time.monotonic()+scheduler.resources.io_window_seconds+2
        grant = scheduler.pick_and_grant()
        while grant is None and time.monotonic() < deadline:
            time.sleep(.1)
            grant = scheduler.pick_and_grant()
        assert grant is not None and grant.request_id == request_id
        executor = CreationJobUnitExecutor(repo,
            render_action=lambda w,t,context: main._render_creation_task_montage_for_workspace(w,t,render_context=context),
            render_is_valid=lambda w,t,context: main._creation_render_is_valid(w,t,render_context=context))
        executor.execute(grant, scheduler)
        scheduler.assert_released(grant)
        finished = repo.job_records.get_job_run('local', job.id)
        assert finished.status == 'succeeded', finished.failure_reason
        task = repo.task_records.get_creation_task('local', task_id)
        assert task.confirmed_script_id is None and task.narration_cache_key is None and task.output_ready
        montage = repo.get_creation_task_montage_plan('local', task_id)
        assert montage['script_id'] is None and montage['cues'] == [] and montage['clips'][0]['segment_id'] is None
        output = store.resolve_file(montage['output_path'])
        probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(output)],
            check=True, capture_output=True, text=True).stdout)
        assert {stream['codec_type'] for stream in probe['streams']} >= {'video','audio'}
        assert abs(float(next(s for s in probe['streams'] if s['codec_type']=='video')['duration'])-expected_duration) < .04
        decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-t', '1', '-vn',
            '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], check=True, capture_output=True).stdout
        assert max(abs(value) for value in array('h', decoded)) > 500
        assert store.resolve_file(montage['subtitle_path']).read_text().strip() == ''
        assert not repo.script_records.list_creation_task_scripts('local', task_id)
        assert not repo.narration_records.list_narration_segments('local', task_id, None)
    finally:
        scheduler.close()
