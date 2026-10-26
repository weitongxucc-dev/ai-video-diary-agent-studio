import pytest
import json
from types import SimpleNamespace

from app.audio_delivery_policy import AudioEncodeBudget, FinalMuxBudget
from app.creation.audio_delivery_assembly import AudioDeliveryAssembly
from app.creation.render_stage_use_case import RenderStageUseCase
from app.creation_execution import CpuRenderLimits, next_creation_intent
from app.task_audio import TaskAudio
from test_picture_fragment_assembly import _leaf_case


@pytest.mark.parametrize('rollback', [False, True])
def test_automatic_original_job_leaf_to_srt_aac_mux_and_timeline(tmp_path, monkeypatch, rollback):
    case, leaf, packet, store, metadata, renderer, budget, montage = _leaf_case(tmp_path, monkeypatch)
    audio = TaskAudio(case.repo, store, tmp_path/'audio')
    delivery = AudioDeliveryAssembly(case.repo, store, tmp_path/'work', audio=audio, packet_copy_assembly=packet,
        audio_budget=AudioEncodeBudget('b'*64, 1024**3, 1024**3, 32, 1, .1, 24576, 49152, 65536, 30),
        mux_budget=FinalMuxBudget('c'*64, 1024**3, 1024**3, 32, 1, .1, 24576, 49152, 65536, 30), reserve_bytes=1024)
    for port, value in [('picture_fragments', leaf), ('packet_copy_assembly', packet), ('audio_assembly', audio), ('audio_delivery', delivery)]:
        setattr(case.owner, port, value)
        setattr(case.executor, port, value)
    case.owner.cpu_render_limits = CpuRenderLimits(budget.max_output_bytes, 1, 30)
    def forbidden(*args, **kwargs):
        raise AssertionError('automatic S6 delivery cannot rerun the old full render/mix path')
    case.executor.render_action = forbidden
    case.executor.render_is_valid = forbidden
    metadata.picture_renderer = renderer
    render = RenderStageUseCase(case.repo, metadata, None, None, SimpleNamespace())
    job = case.repo.get_job_run('local', case.job.id)
    original_audio_calls = list(case.generated)
    parent_head = case.repo.get_creation_task('local', case.task_id).current_timeline_version_id
    baseline = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']['generated_bytes']
    if rollback:
        create_track = case.repo.create_subtitle_track
        def fail_after_child_write(track, **kwargs):
            result = create_track(track, **kwargs)
            if track.timeline_version_id.startswith('timeline_delivery_'):
                assert kwargs['connection'].in_transaction
                raise ValueError('controlled failure after transactional subtitle child insert')
            return result
        monkeypatch.setattr(case.repo, 'create_subtitle_track', fail_after_child_write)
    intent = next_creation_intent(case.repo, case.scheduler, job)
    assert intent.unit_key.startswith('render:picture-fragment:v1:')
    case.scheduler.submit(intent, case.actor)
    families, sidecar_measurement = [], None
    for _ in range(12):
        grant = case.scheduler.pick_and_grant()
        if grant is None:
            break
        request = case.scheduler._get(grant.request_id)
        families.append(request.unit_key.split(':')[1])
        case.executor.execute(grant, case.scheduler)
        if rollback and request.unit_key.startswith('render:final-mux:v1:'):
            assert case.scheduler._get(grant.request_id).state == 'failed'
            current = case.repo.get_creation_task('local', case.task_id)
            assert current.current_timeline_version_id == parent_head
            failed = case.repo.get_job_run('local', job.id)
            assert failed.status == 'failed' and not failed.output_refs
            assert 'after transactional subtitle' in failed.failure_reason
            with case.repo._connect() as conn:
                assert conn.execute("SELECT count(*) FROM timeline_versions WHERE id=?", (f'timeline_delivery_{job.id}',)).fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM subtitle_tracks WHERE timeline_version_id=?", (f'timeline_delivery_{job.id}',)).fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM transform_runs WHERE id=?", (f'transform_delivery_adoption_{job.id}',)).fetchone()[0] == 0
            preserved = [record for record in case.repo.list_artifact_records('local', case.task_id) if record.kind == 'rendered_video']
            assert len(preserved) == 1 and store.resolve_file(preserved[0].logical_ref).is_file()
            usage = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
            assert usage['reserved_bytes'] == 0 and usage['unknown_unit_count'] == 0 and usage['generated_bytes'] > baseline
            assert not render.valid('local', case.task_id, verify_media=False)
            case.scheduler.close()
            return
        assert case.scheduler._get(grant.request_id).state == 'done', case.repo.get_job_run('local', job.id).failure_reason
        if request.resources.get('subtitle_sidecar_bytes', 0):
            measured = case.repo.get_task_event('local', leaf.measured_event_id(grant))
            sidecar_measurement = (request.resources, measured.payload)
    final_job = case.repo.get_job_run('local', job.id)
    assert final_job.status == 'succeeded', final_job.failure_reason
    assert families == ['picture-fragment', 'picture-fragment', 'packet-copy', 'audio-assembly',
        'audio-assembly', 'audio-encode', 'final-mux']
    assert case.generated == original_audio_calls
    current = case.repo.get_creation_task('local', case.task_id)
    assert current.current_timeline_version_id != parent_head
    version = case.repo.get_timeline_version('local', current.current_timeline_version_id)
    assert version.parent_version_id == parent_head and version.snapshot['audio_delivery']['job_id'] == job.id
    assert final_job.output_refs == version.snapshot['output_refs'] and len(final_job.output_refs) == 2
    subtitle = case.repo.get_artifact_record('local', version.snapshot['audio_delivery']['subtitle_artifact_id'])
    text = store.resolve_file(subtitle.logical_ref).read_text()
    assert '窗口字幕' in text and '-->' in text
    assert sidecar_measurement is not None
    resources, measured = sidecar_measurement
    assert resources['picture_video_output_bytes'] + resources['subtitle_sidecar_bytes'] == resources['max_output_bytes']
    assert resources['subtitle_sidecar_bytes'] == subtitle.size_bytes
    assert measured['consumption_known'] and measured['output_bytes'] > 2*subtitle.size_bytes
    def no_full_sha_outside_grant(*args, **kwargs):
        raise AssertionError('delivery validity read must not scan media outside a Grant')
    monkeypatch.setattr('app.device_resource_configuration._hash_file', no_full_sha_outside_grant)
    assert render.valid('local', case.task_id, verify_media=False)
    assert render.valid('local', case.task_id)
    assert case.repo.list_subtitle_tracks('local', case.task_id, timeline_version_id=version.id)
    with case.repo._connect() as conn:
        receipts = [json.loads(row[0]) for row in conn.execute("SELECT payload_json FROM task_events WHERE job_id=? AND event_type='task_execution_unit_settled'", (job.id,))]
    usage = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
    assert usage['generated_bytes'] - baseline == sum(row['output_bytes'] for row in receipts)
    assert usage['reserved_bytes'] == 0 and usage['unknown_unit_count'] == 0
    video_ref = version.snapshot['montage']['output_path']
    with store.resolve_file(video_ref).open('r+b') as output:
        output.write(b'changed')
    assert not render.valid('local', case.task_id, verify_media=False)
    case.scheduler.close()
