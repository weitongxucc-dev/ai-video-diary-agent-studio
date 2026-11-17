"""Complete sidechain through the production owner/executor and real host probe.

All stores, grants, audio and budgets are isolated test inputs; no provider or
production calibration is changed.
"""
from array import array
from dataclasses import replace
from datetime import datetime, timezone
import math
import time
import wave

import pytest

from app.artifact_store import ArtifactStore
from app.audio_timeline import AudioTimeline, TimelineAudioClip, TimelineAudioTrack
from app.creation.contracts import TimelineVersion
from app.creation_execution import CpuRenderLimits, submit_creation_stage
from app.host_resource_probes import HostBudget, HostResourceProbe
from app.materials.contracts import MaterialAsset
from app.montage import CreationTaskMontagePlan, creation_task_montage_plan_to_payload
from app.task_audio import TaskAudio
from tests.host_memory_boundary import IsolatedMemoryMonitor
from test_creation_execution import _narration_units_case


@pytest.mark.parametrize('normalize', [False, True])
def test_sidechain_master_executes_with_original_sources_and_real_probe(tmp_path, monkeypatch, normalize):
    import app.creation_execution as execution_module
    case = _narration_units_case(tmp_path, audio_input_count=2, local_policy=True, duration_seconds=2)
    original_next = execution_module.next_creation_intent

    def narration_only(repository, scheduler, job, **kwargs):
        if repository.task_records.get_creation_task(job.workspace_id, job.task_id).narration_cache_key is not None:
            return None
        return original_next(repository, scheduler, job, **kwargs)

    try:
        with monkeypatch.context() as setup:
            setup.setattr(execution_module, 'next_creation_intent', narration_only)
            submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
            for _ in range(3):
                grant = case.scheduler.pick_and_grant()
                assert grant is not None, case.repo.job_records.get_job_run('local', case.job.id).failure_reason
                case.executor.execute(grant, case.scheduler)
        job = case.repo.job_records.get_job_run('local', case.job.id)
        tracks = []
        for role, frequency, amplitude in [('narration', 330, 6000), ('music', 550, 3000)]:
            path = case.repo.video_library_path / f'{role}.wav'
            with wave.open(str(path), 'wb') as output:
                output.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                output.writeframes(array('h', [round(amplitude * math.sin(2 * math.pi * frequency * n / 48000))
                    if n < 24000 else 0 for n in range(48000)]).tobytes())
            info = path.stat()
            asset = case.repo._save_asset(MaterialAsset(role, 'local', path.name, str(path), info.st_size,
                datetime.fromtimestamp(info.st_mtime, timezone.utc), media_type='audio', duration_seconds=1,
                analysis_status='ready', owner_user_id=case.actor.member_id))
            reference = case.repo.workspace_media_reference_for_asset('local', asset.id)
            with case.repo._connect() as conn:
                case.repo._claim_workspace_media_reference(conn, workspace_id='local', reference=reference)
            tracks.append(TimelineAudioTrack(id=role, name=role, role=role, duck_under_voice=role == 'music',
                clips=[TimelineAudioClip(id=f'{role}-clip', name=role, source_ref=reference,
                    source_start_ms=0, source_end_ms=1000, timeline_start_ms=0)]))
        graph = AudioTimeline(tracks=tracks)
        montage = CreationTaskMontagePlan(id='sidechain-montage', workspace_id='local', task_id=case.task_id,
            script_id=case.script_id, script_title='隔离音频消费者', status='ready', cues=[], clips=[],
            duration_seconds=1, low_confidence_clip_count=0, audio_timeline=graph,
            narration_linkage_created_audio_timeline=normalize)
        payload = creation_task_montage_plan_to_payload(montage)
        case.repo.montage_plan_adoption.save_creation_task_montage_plan('local', case.task_id, montage.id, payload)
        case.repo.timeline_records.create_timeline_version(TimelineVersion('sidechain-base', 'local', case.task_id,
            None, 1, {'montage': payload}, None), expected_current_version_id=None)
        store = ArtifactStore(tmp_path / 'artifacts')
        audio = TaskAudio(case.repo, store, tmp_path / 'temporary')
        case.owner.audio_assembly = case.executor.audio_assembly = audio
        case.owner.cpu_render_limits = CpuRenderLimits(8_000_000, 1, 30)
        budget = replace(case.owner.narration_audio_budget, max_process_rss_bytes=512*1024**2,
            additional_memory_bytes=512*1024**2, additional_disk_bytes=64*1024**2,
            max_process_threads=16, runtime_sample_seconds=.05)
        case.owner.narration_audio_budget = budget
        case.scheduler.resources = HostResourceProbe(case.repo.database_path, disk_paths=(tmp_path,),
            budget=HostBudget(100, 1, 2**63, 0, 2**63, 8_000_000, sample_seconds=.01, cpu_audio=budget),
            memory_monitor=IsolatedMemoryMonitor())
        # The delivery path explicitly requests a master, even below the input cap.
        manifest = audio.render_assembly(job, montage, actor=case.actor, budget=budget,
            prepare=True, force_master=True, output_sample_count=48000)
        unit, = manifest.units
        assert unit.master and not unit.dependencies and unit.normalize_voice_sources is normalize
        assert set(unit.input_refs) == {track.clips[0].source_ref for track in tracks}

        def stop_after_master(repository, scheduler, current, **kwargs):
            try:
                repository.execution_records.get_artifact_record('local', f'artifact_audio_assembly_{unit.id}')
            except KeyError:
                return original_next(repository, scheduler, current, **kwargs)
            return None

        with monkeypatch.context() as execution:
            execution.setattr(execution_module, 'next_creation_intent', stop_after_master)
            submit_creation_stage(case.execution, case.scheduler, job, case.actor)
            deadline = time.monotonic() + 5
            grant = case.scheduler.pick_and_grant()
            while grant is None and time.monotonic() < deadline:
                time.sleep(.05)
                grant = case.scheduler.pick_and_grant()
            assert grant is not None
            request = case.scheduler._get(grant.request_id)
            assert request.unit_key.startswith('render:audio-assembly:')
            assert request.resources['input_count'] == 2
            assert request.resources['normalize_voice_sources'] is normalize
            assert request.resources['apply_master']
            case.executor.execute(grant, case.scheduler)
        assert case.scheduler._get(grant.request_id).state == 'done', case.repo.job_records.get_job_run('local', job.id).failure_reason
        record = case.repo.execution_records.get_artifact_record('local', f'artifact_audio_assembly_{unit.id}')
        assert set(record.input_refs) == set(unit.input_refs)
        saved_manifest, saved_record = audio.prepared_master(job, montage, actor=case.actor, budget=budget)
        assert saved_manifest == manifest and saved_record == record
        output = store.resolve_file(record.logical_ref)
        with wave.open(str(output), 'rb') as source:
            assert (source.getframerate(), source.getnchannels(), source.getnframes()) == (48000, 2, 48000)
            assert max(map(abs, array('h', source.readframes(source.getnframes())))) > 500
        audio.prepared_master_facts(job, montage, actor=case.actor)
        settlement = next(event for event in case.repo.execution_records.list_task_events('local', case.task_id)
            if event.event_type == 'task_execution_unit_settled'
            and event.payload['usage_id'] == f'{request.id}:{grant.generation}')
        assert settlement.payload['output_bytes'] >= record.size_bytes
        if normalize:
            assert settlement.payload['output_bytes'] > record.size_bytes
        assert not case.repo.narration_units.creation_narration_usage('local', job.id)['unsettled']
    finally:
        case.scheduler.close()
