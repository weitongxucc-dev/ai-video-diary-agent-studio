"""Atomic current-timeline reopens and original failed-attempt boundaries, without media."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.creation.contracts import TaskEvent
from app.creation.editing.metadata_use_case import MetadataEditUseCase
from app.creation.packet_copy_assembly import PacketCopyAssembly
from app.creation.picture_fragment_assembly import PictureFragmentAssembly
from app.creation.render_generation import current_render_generation
from app.creation_execution import CreationJobOwnerVerifier, next_creation_intent
from app.device_scheduler import RecoveryBlocked
from app.media.picture_preview import PicturePreviewRenderer
from app.task_audio import TaskAudio
from test_host_resource_probes import _render_budget
from test_render_generation import advance_head, render_case as render_case
from test_source_task_use_case import source_case as source_case


@pytest.fixture
def reopened(render_case, tmp_path):
    case = render_case
    audio = TaskAudio(case.repo, case.store, tmp_path/'audio')
    metadata = MetadataEditUseCase(case.repo, case.store, output_root=tmp_path/'artifacts', audio_service=audio)
    renderer = PicturePreviewRenderer(case.repo, case.store, audio, metadata, None,
        verify_identity=lambda _: 'ready', presenter_model_ids={}, output_root=tmp_path/'artifacts',
        portrait_worker_path=tmp_path/'unused', temporary_root=tmp_path/'work')
    packet = PacketCopyAssembly(case.repo, case.store, tmp_path/'work', budget=None, reserve_bytes=1)
    budget = replace(_render_budget(), max_duration_seconds=30, max_pixel_seconds=1920*1080*30,
        max_width_px=1920, max_height_px=1080)
    case.leaf = PictureFragmentAssembly(case.repo, case.store, tmp_path/'work', metadata=metadata,
        picture_renderer=renderer, render_budget=budget, packet_copy_assembly=packet)
    case.owner = CreationJobOwnerVerifier(case.repo, picture_fragments=case.leaf, packet_copy_assembly=packet)
    case.scheduler = SimpleNamespace(owners=case.owner)
    case.original_manifest = case.leaf.prepare(case.job, actor=case.actor, write_guard=lambda _: None)
    # An already completed original production is an input to this metadata
    # test, not a claim that the opaque source bytes were rendered here.
    with case.repo._connect() as conn:
        conn.execute("UPDATE job_runs SET status='succeeded' WHERE id=?", (case.job.id,))
    case.job = case.repo.job_records.get_job_run('local', case.job.id)
    advance_head(case, 'user-edit')
    return case


def database(case):
    with case.repo._connect() as conn:
        return tuple(conn.iterdump())


@pytest.mark.parametrize('denied', [False, True])
def test_progressive_reopen_commits_scope_and_real_first_unit_atomically(reopened, monkeypatch, denied):
    case = reopened
    before = database(case)
    if denied:
        def reject(*args, **kwargs):
            assert kwargs['connection'].in_transaction
            assert current_render_generation(case.repo, case.job, connection=kwargs['connection'])
            raise PermissionError('controlled source revocation before submission')
        monkeypatch.setattr(case.owner, 'verify_submission', reject)
        with pytest.raises(PermissionError, match='revocation'):
            case.execution.rerender_current(case.job, case.actor, case.scheduler)
        assert database(case) == before
        return
    assert case.execution.rerender_current(case.job, case.actor, case.scheduler)
    queued = case.repo.job_records.get_job_run('local', case.job.id)
    assert queued.status == 'queued' and queued.input_digest == case.job.input_digest
    generation = current_render_generation(case.repo, queued)
    assert generation['input_timeline_version_id'] == case.task.current_timeline_version_id
    manifest = case.leaf.manifest(queued, actor=case.actor)
    assert manifest['head'] == case.task.current_timeline_version_id
    assert manifest['digest'] != case.original_manifest['digest']
    preserved = case.repo.execution_records.get_task_event('local', f'event_{case.job.id}_picture_fragment_manifest')
    assert preserved.payload == case.original_manifest
    intent = next_creation_intent(case.repo, case.scheduler, queued)
    assert intent.unit_key.startswith('render:picture-fragment:v1:')
    verified = case.owner.verify_submission(intent, case.actor)
    assert verified.resources['render_generation_id'] == generation['render_generation_id']
    request = SimpleNamespace(**{**verified.__dict__, 'state':'waiting'})
    with case.repo.execution_inputs.creation_execution_read() as conn:
        assert case.owner.check_current(request, conn) == 'ready'
        foreign = SimpleNamespace(**{**request.__dict__, 'resources':{
            **request.resources, 'render_generation_id':'f'*64}})
        assert case.owner.check_current(foreign, conn) == 'stale'
    assert len(case.execution.jobs('local', case.task.id)) == 1
    advance_head(case, 'later-edit')
    with case.repo.execution_inputs.creation_execution_read() as conn:
        assert case.owner.check_current(request, conn) == 'stale'


@pytest.mark.parametrize('unknown', [False, True])
def test_known_failed_scope_retries_without_transferring_scope_but_unknown_stays_bound(reopened, unknown):
    case = reopened
    assert case.execution.rerender_current(case.job, case.actor, case.scheduler)
    generation = current_render_generation(case.repo, case.job)
    with case.repo._connect() as conn:
        conn.execute("UPDATE job_runs SET status='failed' WHERE id=?", (case.job.id,))
        if unknown:
            case.repo.execution_records.append_task_event(TaskEvent('unsettled-start', 'local', case.task.id,
                case.job.id, 'task_execution_unit_started', {'usage_id':'original-request:17'}), connection=conn)
    before = database(case)
    if unknown:
        with pytest.raises(RecoveryBlocked, match='未结清'):
            case.execution.retry('local', case.task.id, member_id=case.actor.member_id)
        assert database(case) == before
    else:
        retried = case.execution.retry('local', case.task.id, member_id=case.actor.member_id)
        assert retried.id != case.job.id and retried.input_digest == case.job.input_digest
        start = case.repo.creation_execution_jobs.creation_execution_input('local', retried.id)
        assert not any(key in start for key in ('operation','render_generation_id','render_input_timeline_version_id',
            'render_request_id','render_unit_key'))
        assert current_render_generation(case.repo, retried) is None
        assert case.repo.job_records.get_job_run('local', case.job.id).status == 'failed'
        assert current_render_generation(case.repo, case.job) == generation
