"""Real isolated source tasks and render scopes; no media or model execution."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.artifact_store import ArtifactStore
from app.creation.contracts import ArtifactRecord, TaskEvent, TransformRun
from app.creation.render_generation import (
    current_render_generation, read_render_generation, render_generation_event, render_generation_fields, render_namespace,
)
from app.creation_execution import CreationExecution, revision_digest
from app.montage import creation_task_montage_plan_from_payload
from app.models import utc_now
from app.task_audio import TaskAudio
from test_pcm_output_clock import budget
from test_source_task_use_case import source_case as source_case


@pytest.fixture
def render_case(source_case, tmp_path):
    case = source_case
    receipt = case.use_case.create(case.command, case.actor)
    case.task = case.repo.task_records.get_creation_task('local', receipt['task_id'])
    case.plan = case.repo.plan_records.get_creation_plan('local', case.task.plan_id)
    def forbidden(*args, **kwargs):
        pytest.fail('generation metadata does not execute media or models')
    case.execution = CreationExecution(case.repo, run_montage_plan=forbidden, run_render=forbidden,
        render_is_valid=lambda *_:False)
    case.job = case.execution.start('local', case.task.id, None, revision_digest(None, case.plan), actor=case.actor)
    case.start = case.repo.execution_records.get_task_event('local', f'event_{case.job.id}_start')
    case.store = ArtifactStore(tmp_path/'artifacts')
    return case


def advance_head(case, name):
    task = case.repo.task_records.get_creation_task('local', case.task.id)
    before = case.repo.timeline_records.get_timeline_version('local', task.current_timeline_version_id)
    version = replace(before, id=name, parent_version_id=before.id, sequence=before.sequence+1)
    case.repo.timeline_records.create_timeline_version(version)
    case.task = case.repo.task_records.get_creation_task('local', task.id)
    return version


def append_generation(case):
    event = render_generation_event(case.job, case.task, case.start, case.actor)
    case.repo.execution_records.append_task_event(event)
    return event.payload


def test_generation_rollback_and_currentness_use_real_original_identity(render_case):
    case = render_case
    assert current_render_generation(case.repo, case.job) is None
    head = advance_head(case, 'edited-head')
    event = render_generation_event(case.job, case.task, case.start, case.actor)
    with case.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        case.repo.execution_records.append_task_event(event, connection=conn)
        assert read_render_generation(conn, case.job, case.repo.execution_records) == event.payload
        conn.rollback()
    assert current_render_generation(case.repo, case.job) is None
    generation = append_generation(case)
    assert generation['input_timeline_version_id'] == head.id
    assert render_namespace('legacy-manifest', None) == 'legacy-manifest'
    assert render_namespace('legacy-manifest', generation) != 'legacy-manifest'
    assert render_generation_fields(generation)['render_input_timeline_version_id'] == head.id
    advance_head(case, 'later-head')
    with pytest.raises(ValueError, match='范围已变化'):
        current_render_generation(case.repo, case.job)
    assert current_render_generation(case.repo, case.job, require_current_head=False) == generation
    next_generation = append_generation(case)
    assert next_generation['render_generation_id'] != generation['render_generation_id']
    with pytest.raises(PermissionError):
        render_generation_event(case.job, case.task, case.start, replace(case.actor, member_id='foreign'))


def test_generation_does_not_accept_another_tasks_start_or_input(render_case):
    case = render_case
    with pytest.raises(PermissionError):
        render_generation_event(case.job, case.task, replace(case.start, task_id='foreign'), case.actor)
    append_generation(case)
    with pytest.raises(ValueError, match='范围已变化'):
        current_render_generation(case.repo, replace(case.job, input_digest='a'*64))


def test_generation_pcm_clock_changes_get_a_new_manifest_and_old_unknown_cannot_be_replaced(render_case, tmp_path):
    from app.device_scheduler import RecoveryBlocked
    case = render_case
    audio = TaskAudio(case.repo, case.store, tmp_path/'audio')
    montage = creation_task_montage_plan_from_payload(case.repo.get_creation_task_montage_plan('local', case.task.id))
    original = audio.render_assembly(case.job, montage, actor=case.actor, budget=budget(), prepare=True,
        force_master=True, output_sample_count=312000)
    legacy_id = audio._assembly_plan_id(case.job, montage)
    legacy = case.repo.execution_records.get_task_event('local', legacy_id)
    advance_head(case, 'clock-edited')
    append_generation(case)
    # There are no original producer/settlement receipts. Matching graph and N
    # cannot turn an incomplete/unknown master into a new execution attempt.
    with pytest.raises(RecoveryBlocked, match='完整成功回执'):
        audio.render_assembly(case.job, montage, actor=case.actor, budget=budget(), prepare=True,
            force_master=True, output_sample_count=312000)
    changed = audio.render_assembly(case.job, montage, actor=case.actor, budget=budget(), prepare=True,
        force_master=True, output_sample_count=313600)
    assert changed.sample_count == 313600 and changed.digest != original.digest
    assert changed.final.id != original.final.id
    assert audio._assembly_plan_id(case.job, montage) != legacy_id
    assert case.repo.execution_records.get_task_event('local', legacy_id) == legacy
    assert audio.render_assembly(case.job, montage, actor=case.actor, budget=budget()) == changed
    with pytest.raises(ValueError, match='原清单和产物保持不变'):
        audio.render_assembly(case.job, montage, actor=case.actor, budget=budget(), prepare=True,
            force_master=True, output_sample_count=315200)


def test_generation_reuses_same_graph_clock_source_only_with_original_success_receipt(render_case, tmp_path, monkeypatch):
    from app.creation_execution import CreationJobOwnerVerifier, CpuRenderLimits
    from app.device_scheduler import DeviceScheduler, SubmissionIntent, SchedulePolicy
    from app.host_resource_probes import HostResourceDecision, HostProcessProbe
    case = render_case
    audio = TaskAudio(case.repo, case.store, tmp_path/'audio')
    montage = creation_task_montage_plan_from_payload(case.repo.get_creation_task_montage_plan('local', case.task.id))
    manifest = audio.render_assembly(case.job, montage, actor=case.actor, budget=budget(), prepare=True,
        force_master=True, output_sample_count=312000)
    original_id = audio._assembly_plan_id(case.job, montage)
    unit = manifest.final
    resources = {'kind':'task_audio', 'assembly_unit_id':unit.id, 'assembly_manifest_digest':manifest.digest,
        'max_output_bytes':1024**2, 'timeout_seconds':30, 'threads':1}
    owner = CreationJobOwnerVerifier(case.repo, cpu_render_limits=CpuRenderLimits(1024**2, 1, 30))
    key = f'render:audio-assembly:{unit.id}'
    monkeypatch.setattr(owner, 'render_facts', lambda *args, **kwargs:(key, resources))
    # Isolate admission mechanics only; this protocol test retains a real
    # scheduler Grant, owner verification and its persisted completed receipt.
    probe = SimpleNamespace(admit=lambda request:HostResourceDecision(True, dict(request.resources), {}, None),
        continue_unit=lambda _:True, released=lambda _:True)
    scheduler = DeviceScheduler(case.repo.database_path, tmp_path/'protocol.lock', owners=owner,
        resources=probe, processes=HostProcessProbe())
    try:
        scheduler.submit(SubmissionIntent('local', 'creation_job', case.job.id, case.job.input_digest, key, SchedulePolicy()), case.actor)
        grant = scheduler.pick_and_grant()
        assert grant is not None
        path = tmp_path/'declared-master.wav'
        path.write_bytes(b'declared successful PCM protocol fixture, not decoded media')
        published = case.store.publish_file(unit.output_ref(case.task.id), path)
        record = ArtifactRecord(f'artifact_audio_assembly_{unit.id}', 'local', case.task.id,
            'audio_assembly_fragment', published.logical_ref, published.sha256, published.size_bytes,
            case.job.id, list(unit.input_refs))
        transform = TransformRun(f'transform_audio_assembly_{unit.id}', 'local', case.task.id, case.job.id,
            'audio_assembly', unit.id, record.input_refs, [record.logical_ref], 'audio-mix/v2',
            {'manifest_digest':manifest.digest, 'master':True, 'duration_ms':manifest.duration_ms,
                'sample_rate':48000, 'channels':2, 'output_sample_count':manifest.output_sample_count},
            'succeeded', utc_now(), utc_now())
        case.repo.task_media_publication.record_task_media_publication(record, transform, [])
        def checkpoint(conn, current):
            case.repo.execution_records.append_task_event(TaskEvent('original-pcm-receipt', 'local', case.task.id,
                case.job.id, 'task_execution_unit_settled', {'usage_id':f'{current.request_id}:{current.generation}',
                    'request_id':current.request_id, 'permit_generation':current.generation,
                    'elapsed_seconds':0, 'output_bytes':record.size_bytes}), connection=conn)
            return record.logical_ref
        scheduler.complete_unit(grant, checkpoint_database_path=case.repo.database_path, checkpoint_write=checkpoint)
        advance_head(case, 'visual-only-edit')
        append_generation(case)
        reused = audio.render_assembly(case.job, montage, actor=case.actor, budget=budget(), prepare=True,
            force_master=True, output_sample_count=312000)
        assert reused == manifest and audio._assembly_plan_id(case.job, montage) == original_id
        facts = audio.prepared_master_facts(case.job, montage, actor=case.actor)
        assert facts['parent']['binding']['producer_request_id'] == grant.request_id
        assert facts['parent']['binding']['producer_generation'] == grant.generation
        with case.repo._connect() as conn:
            assert conn.execute('SELECT count(*) FROM device_work_requests WHERE owner_id=?', (case.job.id,)).fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM task_events WHERE job_id=? AND event_type='task_execution_unit_settled'", (case.job.id,)).fetchone()[0] == 1
    finally:
        scheduler.close()


@pytest.mark.parametrize('family', ['packet', 'audio_encode', 'final_mux'])
def test_resource_declarations_preserve_legacy_and_require_complete_scope(tmp_path, monkeypatch, family):
    from app.packet_copy_policy import packet_copy_refusal
    from app.audio_delivery_policy import audio_delivery_refusal
    from test_packet_copy_policy import fixture as packet_fixture
    from test_audio_delivery_policy import fixture as delivery_fixture
    if family == 'packet':
        policy, original, *_ = packet_fixture(tmp_path, monkeypatch)
        refusal = packet_copy_refusal
    else:
        policy, original, *_ = delivery_fixture(tmp_path, monkeypatch, operation=family)
        refusal = audio_delivery_refusal
    assert refusal(original, policy) is None
    scope = {'render_generation_id':'a'*64, 'render_input_timeline_version_id':'sealed-editorial-head'}
    declared = {**original, **scope}
    assert refusal(declared, policy) is None
    # Resource admission validates the complete declaration; it cannot grant
    # current-HEAD authority. The real owner tests reject foreign valid scopes.
    assert refusal({**declared, 'render_generation_id':'not-a-generation'}, policy).endswith('resources_invalid')
    incomplete = dict(declared)
    del incomplete['render_input_timeline_version_id']
    assert refusal(incomplete, policy).endswith('resources_invalid')
    assert not any(key in original for key in scope)
