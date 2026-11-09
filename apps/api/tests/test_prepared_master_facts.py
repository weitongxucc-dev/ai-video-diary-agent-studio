from pathlib import Path
import wave

from app.artifact_store import ArtifactStore
from app.creation.assembly_manifest import plan_audio_assembly
from app.creation.contracts import ArtifactRecord, TaskEvent, TransformRun
from app.creation_execution import CpuRenderLimits
from app.device_scheduler import SubmissionIntent, SchedulePolicy
from app.models import utc_now
from app.task_audio import TaskAudio
from test_assembly_manifest import timeline
from test_creation_execution import _narration_units_case


def test_prepared_master_reads_original_receipt_and_clock_without_remixing(tmp_path, monkeypatch):
    case = _narration_units_case(tmp_path, local_policy=True)
    store = ArtifactStore(tmp_path/'artifacts')
    audio = TaskAudio(case.repo, store, tmp_path/'audio')
    graph = timeline(['artifacts/source-one', 'artifacts/source-two', 'artifacts/source-three'])
    montage = type('Montage', (), {'audio_timeline': graph, 'duration_seconds': 1,
        'narration_linkage_created_audio_timeline': False})()
    manifest = plan_audio_assembly(graph, 1000, workspace_id='local', task_id=case.task_id,
        producer_job_id=case.job.id, source_hashes={ref: 'a'*64 for ref in ['artifacts/source-one', 'artifacts/source-two', 'artifacts/source-three']},
        max_input_count=2)
    case.repo.execution_records.append_task_event(TaskEvent(audio._assembly_plan_id(case.job, montage), 'local', case.task_id,
        case.job.id, 'creation_audio_assembly_plan', {'input_digest': case.job.input_digest, 'manifest': manifest.payload()}))
    unit = manifest.final
    resources = {'kind': 'task_audio', 'assembly_unit_id': unit.id, 'assembly_manifest_digest': manifest.digest,
        'max_output_bytes': 1024**2, 'timeout_seconds': 30, 'threads': 1}
    key = f'render:audio-assembly:{unit.id}'
    case.owner.cpu_render_limits = CpuRenderLimits(1024**2, 1, 30)
    monkeypatch.setattr(case.owner, 'render_facts', lambda *a, **kw: (key, resources))
    case.scheduler.submit(SubmissionIntent('local', 'creation_job', case.job.id, case.job.input_digest,
        key, SchedulePolicy()), case.actor)
    grant = case.scheduler.pick_and_grant()
    assert grant is not None
    case.scheduler.assert_grant(grant)
    path = tmp_path/'master.wav'
    with wave.open(str(path), 'wb') as output:
        output.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        output.writeframes(b'\0'*48000*4)
    published = store.publish_file(unit.output_ref(case.task_id), path)
    record = ArtifactRecord(f'artifact_audio_assembly_{unit.id}', 'local', case.task_id,
        'audio_assembly_fragment', published.logical_ref, published.sha256, published.size_bytes,
        case.job.id, list(unit.input_refs))
    transform = TransformRun(id=f'transform_audio_assembly_{unit.id}', workspace_id='local', task_id=case.task_id,
        job_id=case.job.id, transform_type='audio_assembly', input_digest=unit.id, input_refs=record.input_refs,
        output_refs=[record.logical_ref], engine='audio-mix/v2', parameters={'manifest_digest': manifest.digest,
            'master': True, 'duration_ms': 1000, 'sample_rate': 48000, 'channels': 2},
        status='succeeded', started_at=utc_now(), finished_at=utc_now())
    case.repo.task_media_publication.record_task_media_publication(record, transform, [])
    def commit(conn, current):
        case.repo.execution_records.append_task_event(TaskEvent('fixture-original-master-receipt', 'local', case.task_id, case.job.id,
            'task_execution_unit_settled', {'usage_id': f'{current.request_id}:{current.generation}',
                'request_id': current.request_id, 'permit_generation': current.generation,
                'elapsed_seconds': 0, 'output_bytes': published.size_bytes}), connection=conn)
        return record.logical_ref
    case.scheduler.complete_unit(grant, checkpoint_database_path=case.repo.database_path, checkpoint_write=commit)
    calls = []
    monkeypatch.setattr(audio, 'validate_source_access', lambda ws, task, ref, actor, *, connection=None: calls.append(('access', ref)))
    monkeypatch.setattr(audio, 'validate_material_use', lambda ws, task, ref, *, connection=None: calls.append(('rights', ref)))
    def forbidden(*a, **kw):
        raise AssertionError('master read must not mix, hash or probe the original sources')
    monkeypatch.setattr(audio, '_mix_sources', forbidden)
    monkeypatch.setattr(audio, 'master_mix_facts', forbidden)
    monkeypatch.setattr(audio, 'render_assembly', forbidden)
    monkeypatch.setattr(audio, 'probe', forbidden)
    result = audio.prepared_master_facts(case.job, montage, actor=case.actor)
    assert result['parent']['binding']['artifact_id'] == record.id
    assert result['parent']['binding']['producer_request_id'] == grant.request_id
    assert result['parent']['binding']['producer_generation'] == grant.generation
    assert result['parent']['clock'] == {'sample_count': 48000, 'sample_rate': 48000,
        'channels': 2, 'sample_format': 's16le'}
    assert Path(result['path']) == store.resolve_file(record.logical_ref)
    assert calls == [('access', record.logical_ref), ('rights', record.logical_ref)]
    case.scheduler.close()
