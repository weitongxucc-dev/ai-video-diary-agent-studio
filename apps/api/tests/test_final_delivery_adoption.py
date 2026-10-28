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
    # The fixture installs an approved storyboard directly after narration. Close
    # its superseded, never-run planning request before starting this delivery.
    with case.repo._connect() as conn:
        pending = conn.execute("SELECT id,unit_key,last_grant_generation FROM device_work_requests "
            "WHERE owner_id=? AND state IN ('waiting','blocked')", (case.job.id,)).fetchall()
    for request in pending:
        assert request['unit_key'].startswith('montage_plan:') and request['last_grant_generation'] is None
        case.scheduler.cancel(request['id'], case.actor)
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
    job = case.repo.job_records.get_job_run('local', case.job.id)
    original_audio_calls = list(case.generated)
    parent_head = case.repo.task_records.get_creation_task('local', case.task_id).current_timeline_version_id
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
            current = case.repo.task_records.get_creation_task('local', case.task_id)
            assert current.current_timeline_version_id == parent_head
            failed = case.repo.job_records.get_job_run('local', job.id)
            assert failed.status == 'failed' and not failed.output_refs
            assert 'after transactional subtitle' in failed.failure_reason
            with case.repo._connect() as conn:
                assert conn.execute("SELECT count(*) FROM timeline_versions WHERE id=?", (f'timeline_delivery_{job.id}',)).fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM subtitle_tracks WHERE timeline_version_id=?", (f'timeline_delivery_{job.id}',)).fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM transform_runs WHERE id=?", (f'transform_delivery_adoption_{job.id}',)).fetchone()[0] == 0
            preserved = [record for record in case.repo.execution_records.list_artifact_records('local', case.task_id) if record.kind == 'rendered_video']
            assert len(preserved) == 1 and store.resolve_file(preserved[0].logical_ref).is_file()
            usage = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
            assert usage['reserved_bytes'] == 0 and usage['unknown_unit_count'] == 0 and usage['generated_bytes'] > baseline
            assert not render.valid('local', case.task_id, verify_media=False)
            _resume_only_adoption(case, delivery, render, store, monkeypatch, create_track, grant, usage)
            case.scheduler.close()
            return
        assert case.scheduler._get(grant.request_id).state == 'done', case.repo.job_records.get_job_run('local', job.id).failure_reason
        if request.resources.get('subtitle_sidecar_bytes', 0):
            measured = case.repo.execution_records.get_task_event('local', leaf.measured_event_id(grant))
            sidecar_measurement = (request.resources, measured.payload)
    final_job = case.repo.job_records.get_job_run('local', job.id)
    assert final_job.status == 'succeeded', final_job.failure_reason
    assert families == ['picture-fragment', 'picture-fragment', 'packet-copy', 'audio-assembly',
        'audio-assembly', 'audio-encode', 'final-mux']
    assert case.generated == original_audio_calls
    current = case.repo.task_records.get_creation_task('local', case.task_id)
    assert current.current_timeline_version_id != parent_head
    version = case.repo.get_timeline_version('local', current.current_timeline_version_id)
    assert version.parent_version_id == parent_head and version.snapshot['audio_delivery']['job_id'] == job.id
    assert final_job.output_refs == version.snapshot['output_refs'] and len(final_job.output_refs) == 2
    subtitle = case.repo.execution_records.get_artifact_record('local', version.snapshot['audio_delivery']['subtitle_artifact_id'])
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


def _resume_only_adoption(case, delivery, render, store, monkeypatch, create_track, original_grant, before):
    """Exercise the public resume entry on the preserved real final-mux failure."""
    from app.creation.execution_control import CreationExecutionControl, ExecutionControlRejected
    from dataclasses import replace
    control = CreationExecutionControl(case.repo, case.execution, render, lambda _: 'ready')
    runtime = SimpleNamespace(scheduler=case.scheduler, registrations={
        'creation_job': SimpleNamespace(owner=case.owner, executor=case.executor)}, wake=lambda: None)
    original = case.scheduler._get(original_grant.request_id)
    with case.repo._connect() as conn:
        receipt = conn.execute("SELECT id,payload_json FROM task_events WHERE job_id=? "
            "AND event_type='task_execution_unit_settled' AND json_extract(payload_json,'$.usage_id')=?",
            (case.job.id, f'{original.id}:{original.last_grant_generation}')).fetchone()
        task_payload = conn.execute('SELECT payload FROM creation_tasks WHERE id=?', (case.task_id,)).fetchone()[0]
        request_count = conn.execute('SELECT count(*) FROM device_work_requests WHERE owner_id=?', (case.job.id,)).fetchone()[0]
    def refused(actor, code):
        with pytest.raises(ExecutionControlRejected) as rejection:
            control.resume('local', case.task_id, actor, runtime, verify_media=False)
        assert rejection.value.status_code == code
        with case.repo._connect() as conn:
            assert conn.execute('SELECT count(*) FROM device_work_requests WHERE owner_id=?', (case.job.id,)).fetchone()[0] == request_count
        assert case.scheduler._get(original.id) == original
        assert case.repo.job_records.get_job_run('local', case.job.id).status == 'failed'
    refused(replace(case.actor, member_id='another-member'), 403)
    # Unknown consumption is a durable fault, not permission to create a retry.
    unknown = {**json.loads(receipt['payload_json']), 'output_bytes': None}
    with case.repo._connect() as conn:
        conn.execute('UPDATE task_events SET payload_json=? WHERE id=?', (json.dumps(unknown), receipt['id']))
    refused(case.actor, 409)
    with case.repo._connect() as conn:
        conn.execute('UPDATE task_events SET payload_json=? WHERE id=?', (receipt['payload_json'], receipt['id']))
    measurement_id = f'event_{original.id}_{original.last_grant_generation}_delivery_output'
    with case.repo._connect() as conn:
        conn.execute('UPDATE task_events SET id=? WHERE id=?', ('withheld-measurement', measurement_id))
    refused(case.actor, 409)
    with case.repo._connect() as conn:
        conn.execute('UPDATE task_events SET id=? WHERE id=?', (measurement_id, 'withheld-measurement'))
    original_record_read = case.repo.execution_records.get_artifact_record
    def missing_final_registration(workspace_id, artifact_id):
        if artifact_id.startswith('artifact_final_mux_'):
            raise KeyError(artifact_id)
        return original_record_read(workspace_id, artifact_id)
    with monkeypatch.context() as registration_failure:
        registration_failure.setattr(case.repo.execution_records, 'get_artifact_record', missing_final_registration)
        refused(case.actor, 409)
    # A changed current HEAD must not adopt the previous final media into it.
    changed = {**json.loads(task_payload), 'current_timeline_version_id': None}
    with case.repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(changed), case.task_id))
    refused(case.actor, 409)
    with case.repo._connect() as conn:
        conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (task_payload, case.task_id))
    original_calls = list(case.generated)
    artifacts = {item.id: (item.logical_ref, item.sha256, item.size_bytes)
        for item in case.repo.execution_records.list_artifact_records('local', case.task_id)}
    def forbidden(*args, **kwargs):
        raise AssertionError('adoption recovery must not regenerate any media or create a replacement Job')
    monkeypatch.setattr(delivery, 'execute', forbidden)
    monkeypatch.setattr(case.owner.picture_fragments, 'execute', forbidden)
    monkeypatch.setattr(case.execution, 'retry', forbidden)
    callbacks = []
    def completed(workspace_id, task_id, job_id):
        with case.repo._connect() as conn:
            assert conn.execute('SELECT state FROM device_permit WHERE id=1').fetchone()[0] == 'free'
        assert case.repo.job_records.get_job_run(workspace_id, job_id).status == 'succeeded'
        assert render.valid(workspace_id, task_id, verify_media=False)
        callbacks.append(job_id)
    case.executor.on_render_completed = completed
    recovery_ids = []
    for attempt in range(2):
        with case.repo._connect() as conn:
            active = [dict(row) for row in conn.execute("SELECT id,unit_key,state FROM device_work_requests "
                "WHERE owner_id=? AND state IN ('waiting','blocked','granted','yield_requested')", (case.job.id,))]
        assert not active, (attempt, active)
        result = control.resume('local', case.task_id, case.actor, runtime, verify_media=False)
        assert result['job']['id'] == case.job.id
        # Repeated clicks read the same waiting request, without reopening/recounting.
        assert control.resume('local', case.task_id, case.actor, runtime, verify_media=False)['job']['id'] == case.job.id
        grant = case.scheduler.pick_and_grant()
        assert grant is not None and grant.unit_key.startswith('render:delivery-adopt:v1:')
        request = case.scheduler._get(grant.request_id)
        assert request.resources['max_output_bytes'] == request.resources['generated_bytes_limit'] == 0
        assert request.resources['write_allocations'] == {'database': request.resources['metadata_bytes']}
        recovery_ids.append(grant.request_id)
        if attempt:
            monkeypatch.setattr(case.repo, 'create_subtitle_track', create_track)
        case.executor.execute(grant, case.scheduler)
        current = case.repo.job_records.get_job_run('local', case.job.id)
        if not attempt:
            assert current.status == 'failed' and 'after transactional subtitle' in current.failure_reason
            assert callbacks == []
        else:
            assert current.status == 'succeeded', current.failure_reason
        usage = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
        assert usage['generated_bytes'] == before['generated_bytes']
        assert usage['reserved_bytes'] == usage['unknown_unit_count'] == 0
    assert callbacks == [case.job.id]
    assert len(set(recovery_ids)) == 2 and case.scheduler._get(recovery_ids[0]).state == 'failed'
    assert case.scheduler._get(original.id) == original
    assert case.generated == original_calls
    assert {item.id: (item.logical_ref, item.sha256, item.size_bytes)
        for item in case.repo.execution_records.list_artifact_records('local', case.task_id)} == artifacts
    assert render.valid('local', case.task_id, verify_media=False)
    # A replay after completion also preserves the original completed request.
    assert control.resume('local', case.task_id, case.actor, runtime, verify_media=False)['job']['id'] == case.job.id
    assert case.scheduler.pick_and_grant() is None
