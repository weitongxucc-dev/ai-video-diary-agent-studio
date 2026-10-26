from dataclasses import replace
from pathlib import Path
import subprocess

from app.artifact_store import ArtifactStore
from app.creation.contracts import TimelineVersion
from app.creation.editing.metadata_use_case import MetadataEditUseCase
from app.creation.packet_copy_assembly import PacketCopyAssembly
from app.creation.picture_fragment_assembly import PictureFragmentAssembly
from app.creation_execution import CpuRenderGrantContext, CpuRenderLimits, submit_creation_stage
from app.device_scheduler import SubmissionIntent, SchedulePolicy
from app.host_resource_probes import CpuRenderBudget
from app.media.picture_preview import PicturePreviewRenderer
from app.models import utc_now
from app.montage import CreationTaskMontagePlan, MontageClip, MontageRenderer, NarrationCue, creation_task_montage_plan_to_payload
from app.packet_copy_policy import PacketCopyBudget
from app.production import MaterialAsset, MaterialSegment
from app.task_audio import TaskAudio
from test_creation_execution import _narration_units_case


def _leaf_case(tmp_path, monkeypatch):
    case = _narration_units_case(tmp_path, local_policy=True, duration_seconds=2)
    import app.creation_execution as execution_module
    original_next = execution_module.next_creation_intent
    def audio_setup(repository, scheduler, job, **kwargs):
        if repository.get_creation_task(job.workspace_id, job.task_id).narration_cache_key:
            return None
        return original_next(repository, scheduler, job, **kwargs)
    with monkeypatch.context() as setup:
        setup.setattr(execution_module, 'next_creation_intent', audio_setup)
        submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
        for _ in range(3):
            grant = case.scheduler.pick_and_grant()
            assert grant is not None
            case.executor.execute(grant, case.scheduler)
    source = tmp_path/'library'/'source.mp4'
    source.parent.mkdir(exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=24:duration=1',
        '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True)
    asset = case.repo._save_asset(MaterialAsset(id='source', workspace_id='local', file_name=source.name,
        path=str(source), size_bytes=source.stat().st_size, modified_at=utc_now(), duration_seconds=1,
        usage_role='b_roll', review_status='approved', analysis_status='ready', media_type='video', owner_user_id=case.actor.member_id,
        segments=[MaterialSegment(id='segment', asset_id='source', start_seconds=0, end_seconds=1,
            description='approved synthetic picture', review_status='approved')]))
    reference = case.repo.workspace_media_reference_for_asset('local', asset.id)
    with case.repo._connect() as conn:
        case.repo._claim_workspace_media_reference(conn, workspace_id='local', reference=reference)
    montage = CreationTaskMontagePlan(id='picture-window-plan', workspace_id='local', task_id=case.task_id,
        script_id=case.script_id, script_title='approved windows', status='ready',
        cues=[NarrationCue('cue', 'section_1', '窗口字幕', 2)], clips=[MontageClip(id='clip', cue_id='cue', asset_id=asset.id,
            segment_id='segment', source_path=reference, source_start_seconds=0, source_end_seconds=1,
            timeline_start_seconds=0, timeline_end_seconds=2, match_score=1, match_reasons=['explicit slow picture'],
            needs_confirmation=False)], duration_seconds=2, low_confidence_clip_count=0, aspect_ratio='16:9',
        narration_audio_path=case.repo.narration_artifact_reference(case.repo.get_creation_task('local', case.task_id).narration_cache_key))
    montage.graphic_overlays = [{'kind': 'text', 'id': 'title', 'text': '原图层', 'start_seconds': .4,
        'end_seconds': 1.8, 'x': .5, 'y': .15, 'width': .8}]
    case.repo.save_creation_task_montage_plan('local', case.task_id, montage.id, creation_task_montage_plan_to_payload(montage))
    case.repo.create_timeline_version(TimelineVersion(id='approved-picture-head', workspace_id='local', task_id=case.task_id,
        parent_version_id=None, sequence=1, created_by_operation_id=None, snapshot={'montage_plan': creation_task_montage_plan_to_payload(montage)}))
    store = ArtifactStore(tmp_path/'artifacts')
    audio = TaskAudio(case.repo, store, tmp_path/'audio')
    metadata = MetadataEditUseCase(case.repo, store, output_root=tmp_path/'outputs', audio_service=audio)
    renderer = PicturePreviewRenderer(case.repo, store, audio, metadata, montage_renderer=MontageRenderer(tmp_path/'output'),
        verify_identity=lambda actor: 'ready', presenter_model_ids={}, output_root=tmp_path/'outputs',
        portrait_worker_path=Path('unused'), temporary_root=tmp_path/'work')
    budget = CpuRenderBudget(1, 1920*1080, 1920, 1920, 32*1024**2, 1, 6, 32*1024**2,
        1, 30, 1024**3, 128*1024**2, 32)
    packet = PacketCopyAssembly(case.repo, store, tmp_path/'work',
        budget=PacketCopyBudget('a'*64, 1024**3, 1024**3, 32, 1, .1, 65536, 262144, 65536, 30), reserve_bytes=1024)
    service = PictureFragmentAssembly(case.repo, store, tmp_path/'work', metadata=metadata, picture_renderer=renderer,
        render_budget=budget, packet_copy_assembly=packet)
    metadata.picture_renderer = renderer
    return case, service, packet, store, metadata, renderer, budget, montage


def test_real_leaf_consumer_binds_captions_source_permissions_and_publication(tmp_path, monkeypatch):
    case, service, packet, store, metadata, renderer, budget, montage = _leaf_case(tmp_path, monkeypatch)
    source = Path(case.repo.get_asset('local', 'source').path)
    job = case.repo.get_job_run('local', case.job.id)
    manifest = service.prepare(job, actor=case.actor, write_guard=lambda conn: None)
    assert len(manifest['units']) == 2
    assert all(unit['layers'] and unit['decode_seconds'] == 1 for unit in manifest['units'])
    case.owner.cpu_render_limits = CpuRenderLimits(budget.max_output_bytes, budget.max_threads, budget.max_timeout_seconds)
    original_render = case.owner.render_facts
    def leaf_facts(conn, task, plan, **kwargs):
        result = service.next_unit(job, actor=case.actor, request=kwargs.get('request'), connection=conn)
        return result if result is not None else original_render(conn, task, plan, **kwargs)
    monkeypatch.setattr(case.owner, 'render_facts', leaf_facts)
    import time
    for index in range(2):
        pending = service.next_unit(job, actor=case.actor)
        assert pending is not None
        key, resources = pending
        assert resources['input_bytes'] == source.stat().st_size
        assert resources['duration_seconds'] == 1 and resources['clip_count'] == 1
        assert resources['picture_fragment_recipe']['layers']
        case.scheduler.submit(SubmissionIntent('local', 'creation_job', job.id, job.input_digest, key, SchedulePolicy()), case.actor)
        grant = case.scheduler.pick_and_grant()
        assert grant is not None
        context = CpuRenderGrantContext(case.scheduler, grant, time.monotonic()+30, budget.max_output_bytes, 1, None)
        def guard(conn):
            assert case.owner.check_current(case.scheduler._get(grant.request_id), conn) == 'ready'
        record = service.execute(job, actor=case.actor, context=context, publication_guard=guard)
        case.scheduler.complete_unit(grant, checkpoint_database_path=case.repo.database_path,
            checkpoint_write=lambda conn, current: record.logical_ref)
        transform = case.repo.get_transform_run('local', f'transform_{record.id}')
        assert transform.parameters['picture_fragment_recipe'] == resources['picture_fragment_recipe']
        assert packet._parent(job, record.id, packet.recipe(job))[0] == record
    ids = service.completed_artifact_ids(job, actor=case.actor)
    assert ids == [unit['artifact_id'] for unit in manifest['units']]
    copy_manifest = packet.prepare(job, ids, actor=case.actor, write_guard=lambda conn: None)
    assert copy_manifest['units'][-1]['packet_count'] == 60
    assert len(ids) == 2
    case.scheduler.close()
