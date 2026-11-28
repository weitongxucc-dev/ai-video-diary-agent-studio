"""Source video uses real FFmpeg, the same queue and verified output publication."""
from array import array
from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace
from uuid import uuid4
import json
import subprocess
import time

import pytest

import app.main as main
from app.artifact_store import ArtifactStore
from app.creation.editing.metadata_use_case import default_subtitle_style
from app.creation.render_result_provenance import rendered_version_provenance
from app.creation.source_task_use_case import SourceTaskCommand, SourceTaskUseCase
from app.creation_execution import CreationJobUnitExecutor, submit_creation_stage
from app.host_resource_probes import CpuAudioBudget, CpuRenderBudget, HostBudget, HostResourceProbe
from app.materials.contracts import MaterialAsset, MaterialSegment
from app.montage import _resolve_subtitle_font
from app.schemas import CreationTaskExactEditRequest
from app.task_audio import TaskAudio
from app.video_jobs import VideoJobRunner
from tests.host_memory_boundary import IsolatedMemoryMonitor
from test_creation_execution import _render_version_fixture
from test_host_resource_probes import _render_budget


@pytest.mark.parametrize('edit,preserve_audio', [('', True), ('ripple', True), ('', False), ('replace', True), ('subtitle', True)])
def test_source_video_renders_requested_sound_without_script_or_narration(tmp_path, monkeypatch, edit, preserve_audio):
    if edit == 'subtitle':
        try:
            _resolve_subtitle_font(default_subtitle_style()['font_family'])
        except OSError as exc:
            pytest.skip(str(exc))
    repo, _, _, _, actor, execution, owner, scheduler, _, members = _render_version_fixture(tmp_path, source_seconds=4)
    store = ArtifactStore(tmp_path / 'source-artifacts')
    settings = SimpleNamespace(video_output_path=str(tmp_path/'source-output'),
        video_inbox_path=str(tmp_path/'library'), effective_temporary_root_path=tmp_path/'source-temp',
        native_portrait_worker_path=None, native_ocr_worker_path=None)
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'artifact_store', store)
    monkeypatch.setattr(main, 'video_job_runner', VideoJobRunner(repo.job_records))
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'local_members_service', members)
    asset = repo.get_asset('local', 'asset_1')
    receipt = SourceTaskUseCase(repo, lambda current: 'ready' if current == actor else 'unauthorized').create(
        SourceTaskCommand(str(uuid4()), asset.id, asset.updated_at, 0, 2, '原片真实音轨', '16:9', preserve_audio), actor)
    task_id = receipt['task_id']
    expected_duration = 2
    replacement = None
    if edit == 'ripple':
        from app.creation.contracts import EditCommand
        task = repo.task_records.get_creation_task('local', task_id)
        outcome = main._linked_timeline_use_case().apply(EditCommand('local', task_id,
            {'member_id': actor.member_id, 'local_diagnostic': False}, task.current_timeline_version_id,
            'RemoveTimelineRanges', {'range_edit': {'removed_ranges_ms': [[500, 1000]],
                'reason': '删除已定位停顿，成片须使用实际采用的精剪时钟'}}))
        expected_duration = 1.5
        assert outcome.montage.duration_seconds == expected_duration
        assert repo.plan_records.get_creation_plan('local', task.plan_id).duration_target_seconds == 2
    elif edit == 'replace':
        path = tmp_path / 'library' / 'replacement.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:size=160x90:rate=30',
            '-t', '4', '-pix_fmt', 'yuv420p', str(path)], check=True, capture_output=True)
        info = path.stat()
        replacement = repo._save_asset(MaterialAsset('replacement', 'local', path.name, str(path),
            info.st_size, datetime.fromtimestamp(info.st_mtime, timezone.utc), duration_seconds=4,
            analysis_status='ready', review_status='approved', usage_role='b_roll', owner_user_id=actor.member_id,
            segments=[MaterialSegment('replacement-range', 'replacement', .5, 3.5,
                description='QA 红色替换镜头', review_status='approved', usage_role='b_roll')]))
        replacement_ref = repo.workspace_media_reference_for_asset('local', replacement.id)
        with repo._connect() as conn:
            repo._claim_workspace_media_reference(conn, workspace_id='local', reference=replacement_ref)
        identity = {'member_id': actor.member_id, 'local_diagnostic': False}
        task = repo.task_records.get_creation_task('local', task_id)
        main._task_authorizations_for_goal().ensure(task, actor, interactive=True)
        command = CreationTaskExactEditRequest(operation_type='ReplaceClip', request_id=uuid4(),
            clip_id='clip-source', asset_id=replacement.id, segment_id='replacement-range',
            source_start_seconds=1, expected_replacement_asset_updated_at=replacement.updated_at,
            expected_timeline_version_id=receipt['timeline_version_id'], expected_timeline_sequence=1)
        case = main._exact_preview_use_case()
        result = case.apply(task, actor, command, identity=identity)
        assert result.status_code == 200, result.value
        assert case.apply(task, actor, command, identity=identity).value == result.value
        applied, = repo.edit_records.list_edit_operations('local', task_id)
        assert applied.operation_type == 'ReplaceClip' and applied.base_version_id == receipt['timeline_version_id']
        assert repo.task_records.get_creation_task('local', task_id).current_timeline_version_id == applied.result_version_id
    elif edit == 'subtitle':
        task = repo.task_records.get_creation_task('local', task_id)
        main._task_authorizations_for_goal().ensure(task, actor, interactive=True)
        command = CreationTaskExactEditRequest(operation_type='AddFirstSubtitleCue', request_id=uuid4(),
            text='Original audio', start_seconds=.25, end_seconds=1.25, language='en-US',
            expected_timeline_version_id=receipt['timeline_version_id'], expected_timeline_sequence=1)
        result = main._exact_preview_use_case().apply(task, actor, command,
            identity={'member_id': actor.member_id, 'local_diagnostic': False})
        assert result.status_code == 200, result.value
        applied, = repo.edit_records.list_edit_operations('local', task_id)
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
        assert montage['script_id'] is None and montage['cues'] == []
        if replacement is None:
            assert all(clip['segment_id'] is None for clip in montage['clips'])
        else:
            clip, = montage['clips']
            assert (clip['asset_id'], clip['segment_id']) == (replacement.id, 'replacement-range')
            assert (clip['source_start_seconds'], clip['source_end_seconds']) == (1, 3)
        output = store.resolve_file(montage['output_path'])
        if replacement is not None:
            rendered = repo.timeline_records.get_timeline_version('local', task.current_timeline_version_id)
            assert rendered.parent_version_id == applied.result_version_id
            assert rendered_version_provenance(repo, rendered)['job_id'] == job.id
            record, = [item for item in repo.execution_records.list_artifact_records('local', task_id)
                if item.kind == 'rendered_video']
            transform = repo.lineage_records.get_transform_run('local', rendered.snapshot['transform_run_id'])
            inner_job = repo.job_records.get_job_run('local', record.producer_job_id)
            assert inner_job.kind == 'creation_task_montage_render' and inner_job.status == 'succeeded'
            assert inner_job.id == transform.job_id and inner_job.task_id == task_id
            assert record.logical_ref == montage['output_path'] and record.logical_ref in transform.output_refs
            assert main._creation_task_output_path('local', task_id, subtitle=False) == output
            assert record.sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
            pixels = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output),
                '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                check=True, capture_output=True).stdout
            assert len(pixels) >= 3 and len(pixels) % 3 == 0
            assert all(red > 180 and max(green, blue) < 60
                       for red, green, blue in zip(pixels[::3], pixels[1::3], pixels[2::3]))
        probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(output)],
            check=True, capture_output=True, text=True).stdout)
        assert {stream['codec_type'] for stream in probe['streams']} >= {'video','audio'}
        assert abs(float(next(s for s in probe['streams'] if s['codec_type']=='video')['duration'])-expected_duration) < .04
        decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-t', '1', '-vn',
            '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], check=True, capture_output=True).stdout
        peak = max(abs(value) for value in array('h', decoded))
        assert peak > 500 if preserve_audio else peak == 0
        subtitle_text = store.resolve_file(montage['subtitle_path']).read_text().strip()
        if edit == 'subtitle':
            rendered = repo.timeline_records.get_timeline_version('local', task.current_timeline_version_id)
            assert rendered.parent_version_id == applied.result_version_id
            assert rendered_version_provenance(repo, rendered)['job_id'] == job.id
            assert subtitle_text == '1\n00:00:00,250 --> 00:00:01,250\nOriginal audio'
            brightness = []
            for seconds in ('0.10', '0.50', '1.50'):
                frame = subprocess.run(['ffmpeg', '-v', 'error', '-ss', seconds, '-i', str(output),
                    '-frames:v', '1', '-vf', 'crop=iw:ih/2:0:ih/2,format=gray', '-f', 'rawvideo', '-'],
                    check=True, capture_output=True).stdout
                assert frame
                brightness.append(sum(value > 210 for value in frame))
            assert brightness[0] == brightness[2] == 0 and brightness[1] > 100
        else:
            assert subtitle_text == ''
        assert not repo.script_records.list_creation_task_scripts('local', task_id)
        assert not repo.narration_records.list_narration_segments('local', task_id, None)
    finally:
        scheduler.close()
