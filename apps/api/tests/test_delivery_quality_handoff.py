"""Two-second synthetic delivery through the real independent QC worker.

Declared admission budgets are test ports, not host calibration. No TTS/model,
user database, or replacement render job is used.
"""
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from app.audio_delivery_policy import AudioEncodeBudget, FinalMuxBudget
from app.creation.audio_delivery_assembly import AudioDeliveryAssembly
from app.creation.output_observation_use_case import OutputObservationUseCase
from app.creation.quality_execution import quality_check_registration
from app.creation.quality_inspection_use_case import QUALITY_KIND, QualityInspectionUseCase
from app.creation_execution import CpuRenderLimits, next_creation_intent
from app.device_runtime import OwnerRegistration, OwnerRouter
from app.device_resource_preparation import CpuPreparationPolicy
from app.host_memory import MemoryPolicy
from app.host_resource_probes import HostBudget, HostResourceProbe
from app.quality_inspection import QualityCheckRepository
from app.quality_worker import worker_identity
from app.quality_worker_runtime import QUALITY_PHASES, source_receipt
from app.task_audio import TaskAudio
from test_picture_fragment_assembly import _leaf_case
from test_quality_resource_owner import MetadataAccess, fixture_budget


def _native_quality_budget():
    binaries = {name: shutil.which(name) for name in ('ffmpeg', 'ffprobe')}
    if not all(binaries.values()):
        pytest.skip('synthetic delivery inspection requires ffmpeg/ffprobe')
    identities = {}
    for name, path in binaries.items():
        with open(path, 'rb') as stream:
            identities[f'{name}_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return replace(fixture_budget(), max_input_bytes=32 * 1024**2, max_pcm_bytes=1024**2,
        max_frame_json_bytes=1024**2, max_report_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
        max_timeout_seconds=30, max_process_rss_bytes=512 * 1024**2, max_process_threads=32,
        additional_memory_bytes=512 * 1024**2, additional_disk_bytes=1024**2,
        max_tool_bytes=max(Path(path).stat().st_size for path in binaries.values()),
        ffmpeg_path=binaries['ffmpeg'], ffprobe_path=binaries['ffprobe'],
        worker_sha256=worker_identity(), **identities)


@pytest.mark.skipif(sys.platform != 'darwin', reason='uses the native macOS CPU preparation memory policy')
def test_original_delivery_runs_complete_quality_worker(tmp_path, monkeypatch):
    budget = _native_quality_budget()
    case, leaf, packet, store, metadata, _renderer, render_budget, _montage = _leaf_case(tmp_path, monkeypatch)
    audio = TaskAudio(case.repo, store, tmp_path/'audio')
    delivery = AudioDeliveryAssembly(case.repo, store, tmp_path/'work', audio=audio, packet_copy_assembly=packet,
        audio_budget=AudioEncodeBudget('b'*64, 1024**3, 1024**3, 32, 1, .1, 24576, 49152, 65536, 30),
        mux_budget=FinalMuxBudget('c'*64, 1024**3, 1024**3, 32, 1, .1, 24576, 49152, 65536, 30), reserve_bytes=1024)
    for name, port in [('picture_fragments', leaf), ('packet_copy_assembly', packet),
                       ('audio_assembly', audio), ('audio_delivery', delivery)]:
        setattr(case.owner, name, port)
        setattr(case.executor, name, port)
    case.owner.cpu_render_limits = CpuRenderLimits(render_budget.max_output_bytes, 1, 30)
    def no_old_render(*args, **kwargs):
        raise AssertionError('formal delivery must use the new assembly chain')
    case.executor.render_action = case.executor.render_is_valid = no_old_render
    outputs = OutputObservationUseCase(case.repo, store, audio, tmp_path/'observation', None)
    quality = QualityInspectionUseCase(case.repo, QualityCheckRepository(case.repo.database_path), outputs,
        verify_identity=lambda actor: 'ready' if actor == case.actor else 'unauthorized',
        budget=budget, temporary_root=tmp_path/'quality')
    registration = quality_check_registration(quality)
    case.scheduler.owners = OwnerRouter({'creation_job': OwnerRegistration(case.owner, case.executor),
        QUALITY_KIND: registration}, MetadataAccess())
    runtime = SimpleNamespace(scheduler=case.scheduler, wake=lambda: None)
    handoff = []
    def completed(workspace_id, task_id, job_id):
        assert job_id == case.job.id
        assert case.repo.job_records.get_job_run(workspace_id, job_id).status == 'succeeded'
        task = case.repo.task_records.get_creation_task(workspace_id, task_id)
        version = case.repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        assert version.snapshot['audio_delivery']['job_id'] == job_id
        downloaded = outputs.download(workspace_id, task_id, expected_version_id=version.id, subtitle=False)
        _, _, path, artifact = outputs.quality_source_metadata(workspace_id, task_id)
        assert downloaded.path == path and artifact.producer_job_id == job_id
        check_id = quality.start_for_render(runtime, workspace_id, task_id, job_id)
        assert check_id is not None
        handoff.append((check_id, version.id, artifact))
    case.executor.on_render_completed = completed
    original_tts = list(case.generated)
    job = case.repo.job_records.get_job_run('local', case.job.id)
    case.scheduler.submit(next_creation_intent(case.repo, case.scheduler, job), case.actor)
    qc_probe = None
    try:
        for _ in range(7):
            grant = case.scheduler.pick_and_grant()
            assert grant is not None
            assert grant.owner_id == job.id and grant.owner_type == 'creation_job'
            case.executor.execute(grant, case.scheduler)
            assert case.scheduler._get(grant.request_id).state == 'done', case.repo.job_records.get_job_run('local', job.id)
            if case.repo.job_records.get_job_run('local', job.id).status == 'succeeded':
                break
        assert len(handoff) == 1
        case.scheduler.assert_released(grant)
        # Render setup uses declared admission ports. QC uses the real host
        # probe and the exact finite budget sealed into its worker request.
        quality.temporary_root.mkdir(exist_ok=True)
        policy = CpuPreparationPolicy()
        cadence = policy.poll_seconds + policy.sample_seconds
        memory_policy = MemoryPolicy('darwin', cadence * 3, cadence * 2, policy.id)
        qc_probe = HostResourceProbe(case.repo.database_path,
            disk_paths={'database': case.repo.database_path.parent, 'output': tmp_path/'artifacts',
                'temporary': quality.temporary_root},
            budget=HostBudget(max_cpu_percent=100,
                min_available_memory_bytes=math.ceil(psutil.Process().memory_info().rss * policy.recovery_rss_multiplier),
                max_swap_used_bytes=None, min_free_disk_bytes=1024**2,
                max_disk_io_bytes_per_second=None, max_unit_input_bytes=budget.max_input_bytes,
                sample_seconds=policy.sample_seconds, quality=budget, memory_policy=memory_policy))
        case.scheduler.resources = qc_probe
        qc_probe.start_observing(interval_seconds=policy.poll_seconds)
        # Match the production preparation cadence; observe real health for a
        # finite window, without replacing native pressure or recovery state.
        deadline = time.monotonic() + policy.timeout_seconds
        while True:
            memory = qc_probe.memory_monitor.check(
                system_reserve_bytes=qc_probe.budget.min_available_memory_bytes,
                additional_memory_bytes=budget.additional_memory_bytes)
            if memory.verdict is True:
                break
            assert memory.reason == 'memory_recovery_stabilizing' and time.monotonic() < deadline, memory
            time.sleep(policy.poll_seconds)
        check_id, version_id, artifact = handoff[0]
        quality_job_id = None
        phases = []
        for expected_phase in QUALITY_PHASES:
            grant = case.scheduler.pick_and_grant()
            assert grant is not None
            assert grant.owner_type == QUALITY_KIND
            quality_job_id = quality_job_id or grant.owner_id
            assert grant.owner_id == quality_job_id
            assert grant.unit_key == f'quality:{check_id}:{expected_phase}'
            registration.executor.execute(grant, case.scheduler)
            qc_job, _, record, sealed = quality.read('local', quality_job_id)
            assert case.scheduler._get(grant.request_id).state == 'done', (qc_job, record)
            phases.append(expected_phase)
        assert qc_job.status == 'succeeded' and qc_job.task_id == case.task_id
        assert record['check_state'] == 'done' and record['check_id'] == check_id
        assert sealed['source']['timeline_version_id'] == version_id
        assert sealed['source']['artifact_id'] == artifact.id
        assert sealed['source']['render_ref'] == artifact.sha256
        evidence = record['evidence']
        assert evidence['_source_receipt'] == source_receipt(sealed['source'], check_id)
        assert evidence['unavailable'] == [], evidence
        assert set(evidence['kinds_checked']) == {'black_frame', 'truncation', 'clipping',
            'timestamp_offset', 'delivery_duration'}
        assert evidence['duration_assessment']['status'] == 'met'
        assert evidence['status'] in {'clean', 'issues_found'}
        assert evidence['is_clean'] == (not evidence['findings'])
        measurements = quality.read_measurements('local', quality_job_id, case.actor)
        assert [item['phase'] for item in measurements] == phases == list(QUALITY_PHASES)
        measured = {item['phase']: item for item in measurements}
        assert measured['metadata']['workload']['full_sha_bytes'] == artifact.size_bytes
        assert measured['finalize']['workload']['full_sha_bytes'] == artifact.size_bytes
        assert measured['black_frames']['workload']['full_black_scan'] is True
        decoded = measured['truncation']['workload']['decoded_streams']
        assert decoded['video']['complete'] and decoded['video']['frame_count'] == 60
        assert decoded['audio']['complete'] and decoded['audio']['sample_count'] >= 96000
        pcm = measured['clipping']['workload']['pcm_passes']
        assert len(pcm) == 2 and all(item['complete'] for item in pcm)
        assert all(item['sample_count'] == decoded['audio']['sample_count'] for item in pcm)
        assert evidence['_phases']['clipping']['decoded_pcm']['frame_sample_mapping_matches'] is True
        assert all(item['execution']['process_released'] and item['complete'] for item in measurements)
        assert case.generated == original_tts
        assert case.repo.task_records.get_creation_task('local', case.task_id).current_timeline_version_id == version_id
        assert case.scheduler.pick_and_grant() is None
        print(json.dumps({'render_job_id': job.id, 'quality_job_id': quality_job_id, 'check_id': check_id,
            'artifact_id': artifact.id, 'sha256': artifact.sha256, 'phases': phases,
            'result_status': evidence['status'], 'findings': evidence['findings'],
            'decoded_streams': decoded, 'pcm_passes': pcm}, ensure_ascii=False))
    finally:
        case.scheduler.close()
        if qc_probe is not None:
            assert qc_probe.stop_observing(2)
