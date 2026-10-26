"""One new size representative: complete 22-cut delivery and formal output/QC acceptance."""
from dataclasses import replace
from fractions import Fraction
import json
from types import SimpleNamespace

from app.audio_delivery_policy import AudioEncodeBudget, FinalMuxBudget
from app.creation.audio_delivery_assembly import AudioDeliveryAssembly
from app.creation.contracts import TimelineVersion
from app.creation.output_observation_use_case import OutputObservationUseCase
from app.creation.quality_execution import quality_check_registration
from app.creation.quality_inspection_use_case import QUALITY_KIND, QualityInspectionUseCase
from app.creation.render_stage_use_case import RenderStageUseCase
from app.creation_execution import CpuRenderLimits, next_creation_intent
from app.device_runtime import OwnerRegistration, OwnerRouter
from app.montage import NarrationCue, creation_task_montage_plan_to_payload
from app.quality_inspection import QualityCheckRepository
from app.task_audio import TaskAudio
from test_picture_fragment_assembly import _leaf_case
from test_quality_resource_owner import fixture_budget, MetadataAccess


def test_twenty_two_short_cuts_finish_original_job_and_reach_download_and_qc(tmp_path, monkeypatch):
    case, leaf, packet, store, metadata, renderer, render_budget, montage = _leaf_case(tmp_path, monkeypatch)
    original = montage.clips[0]
    offset, clips, cues = 0, [], []
    frames_per_cut = [3]*16 + [2]*6
    for index, frames in enumerate(frames_per_cut):
        start, end = Fraction(offset, 30), Fraction(offset+frames, 30)
        cue_id = 'scale-speech-0' if offset < 30 else 'scale-speech-1'
        clips.append(replace(original, id=f'cut-{index}', cue_id=cue_id,
            source_start_seconds=0, source_end_seconds=float(Fraction(frames, 30)),
            timeline_start_seconds=float(start), timeline_end_seconds=float(end)))
        offset += frames
    assert len(clips) == 22 and offset == 60
    # Narration remains on its independent exact two-second clock. The 22
    # picture cuts do not manufacture 1/15-second floating speech intervals.
    cues = [NarrationCue('scale-speech-0', 'section_1', '镜头1', 1, timeline_start_seconds=0),
        NarrationCue('scale-speech-1', 'section_2', '镜头22', 1, timeline_start_seconds=1)]
    montage.clips, montage.cues, montage.duration_seconds = clips, cues, 2
    payload = creation_task_montage_plan_to_payload(montage)
    case.repo.save_creation_task_montage_plan('local', case.task_id, montage.id, payload)
    previous = case.repo.get_creation_task('local', case.task_id).current_timeline_version_id
    with case.repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        case.repo.create_timeline_version(TimelineVersion(id='approved-twenty-two-cuts', workspace_id='local',
            task_id=case.task_id, parent_version_id=previous,
            sequence=case.repo.next_timeline_sequence('local', case.task_id, connection=conn),
            snapshot={'montage': payload}, created_by_operation_id=None), connection=conn,
            expected_current_version_id=previous)
    audio = TaskAudio(case.repo, store, tmp_path/'audio')
    delivery = AudioDeliveryAssembly(case.repo, store, tmp_path/'work', audio=audio, packet_copy_assembly=packet,
        audio_budget=AudioEncodeBudget('b'*64, 1024**3, 1024**3, 32, 1, .1, 24576, 49152, 65536, 30),
        mux_budget=FinalMuxBudget('c'*64, 1024**3, 1024**3, 32, 1, .1, 24576, 49152, 65536, 30), reserve_bytes=1024)
    for name, port in [('picture_fragments', leaf), ('packet_copy_assembly', packet), ('audio_assembly', audio), ('audio_delivery', delivery)]:
        setattr(case.owner, name, port)
        setattr(case.executor, name, port)
    case.owner.cpu_render_limits = CpuRenderLimits(render_budget.max_output_bytes, 1, 30)
    def no_old_render(*args, **kwargs):
        raise AssertionError('new delivery cannot fall through to the old whole-work renderer')
    case.executor.render_action = case.executor.render_is_valid = no_old_render
    metadata.picture_renderer = renderer
    render = RenderStageUseCase(case.repo, metadata, None, None, SimpleNamespace())
    outputs = OutputObservationUseCase(case.repo, store, audio, tmp_path/'observation', None)
    checks = QualityCheckRepository(case.repo.database_path)
    quality = QualityInspectionUseCase(case.repo, checks, outputs, verify_identity=lambda actor: 'ready',
        budget=fixture_budget(), temporary_root=tmp_path/'quality')
    quality_registration = quality_check_registration(quality)
    case.scheduler.owners = OwnerRouter({'creation_job': OwnerRegistration(case.owner, case.executor),
        QUALITY_KIND: quality_registration}, MetadataAccess())
    runtime = SimpleNamespace(scheduler=case.scheduler, wake=lambda: None)
    callback_receipts = []
    def on_render_completed(workspace_id, task_id, job_id):
        # Independent reads must see the finished original Job, adopted HEAD and
        # released device permit before the original QC entry is invoked.
        with case.repo._connect() as reader:
            row = reader.execute('SELECT status,output_refs_json FROM job_runs WHERE id=?', (job_id,)).fetchone()
            permit = reader.execute('SELECT state,request_id FROM device_permit WHERE id=1').fetchone()
            assert row['status'] == 'succeeded' and len(json.loads(row['output_refs_json'])) == 2
            assert permit['state'] == 'free' and permit['request_id'] is None
        task = case.repo.get_creation_task(workspace_id, task_id)
        version = case.repo.get_timeline_version(workspace_id, task.current_timeline_version_id)
        assert version.snapshot['audio_delivery']['job_id'] == job_id
        downloaded = outputs.download(workspace_id, task_id, expected_version_id=version.id, subtitle=False)
        sidecar = outputs.download(workspace_id, task_id, expected_version_id=version.id, subtitle=True)
        assert downloaded.path == outputs.output_path(workspace_id, task_id, subtitle=False)
        assert sidecar.path == outputs.output_path(workspace_id, task_id, subtitle=True)
        assert downloaded.version_id == sidecar.version_id == version.id
        assert downloaded.path.is_file() and sidecar.path.is_file()
        source_task, plan, source_path, registered = outputs.quality_source_metadata(workspace_id, task_id)
        assert source_task.current_timeline_version_id == version.id and source_path == downloaded.path
        assert registered.kind == 'rendered_video' and registered.producer_job_id == job_id
        final_transform = case.repo.get_transform_run(workspace_id, f'transform_{registered.id}')
        assert final_transform.transform_type == 'final_mux' and final_transform.job_id == job_id
        assert final_transform.parameters['clock']['picture']['packet_count'] == 60
        assert final_transform.parameters['clock']['audio']['sample_count'] == 96000
        check_id = quality.start_for_render(runtime, workspace_id, task_id, job_id)
        assert check_id is not None
        with case.repo._connect() as reader:
            queued = reader.execute("SELECT * FROM device_work_requests WHERE owner_type=? ORDER BY created_at DESC LIMIT 1", (QUALITY_KIND,)).fetchone()
            assert queued is not None and queued['state'] == 'waiting'
        quality_job, _event, record, sealed = quality.read(workspace_id, queued['owner_id'])
        quality.verify_source(sealed, case.actor)
        assert record['check_state'] == 'checking' and quality_job.status == 'queued'
        assert sealed['source']['artifact_id'] == registered.id and sealed['source']['source_ref'] == registered.logical_ref
        with case.repo._connect() as reader:
            assert quality_registration.owner.check_current(case.scheduler._get(queued['id']), reader) == 'ready'
        callback_receipts.append((job_id, version.id, check_id, queued['id'], sidecar.path))
    case.executor.on_render_completed = on_render_completed
    job = case.repo.get_job_run('local', case.job.id)
    original_tts = list(case.generated)
    baseline = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
    first = next_creation_intent(case.repo, case.scheduler, job)
    case.scheduler.submit(first, case.actor)
    counts = {}
    for _ in range(55):
        grant = case.scheduler.pick_and_grant()
        assert grant is not None, case.repo.get_job_run('local', job.id).failure_reason
        request = case.scheduler._get(grant.request_id)
        assert request.owner_type == 'creation_job'
        family = request.unit_key.split(':')[1]
        counts[family] = counts.get(family, 0)+1
        case.executor.execute(grant, case.scheduler)
        assert case.scheduler._get(grant.request_id).state == 'done', case.repo.get_job_run('local', job.id).failure_reason
        if case.repo.get_job_run('local', job.id).status == 'succeeded':
            break
    assert counts == {'picture-fragment': 22, 'packet-copy': 21, 'audio-assembly': 2, 'audio-encode': 1, 'final-mux': 1}
    assert len(callback_receipts) == 1
    assert case.generated == original_tts
    assert render.valid('local', case.task_id, verify_media=False)
    assert render.valid('local', case.task_id)
    sidecar_text = callback_receipts[0][-1].read_text()
    assert '镜头1\n' in sidecar_text and '镜头22\n' in sidecar_text
    usage = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
    with case.repo._connect() as reader:
        receipts = [json.loads(row[0]) for row in reader.execute("SELECT payload_json FROM task_events WHERE job_id=? AND event_type='task_execution_unit_settled'", (job.id,))]
        leaf_count = reader.execute("SELECT count(*) FROM artifact_records WHERE producer_job_id=? AND kind='picture_fragment'", (job.id,)).fetchone()[0]
    assert leaf_count == 22 and len(receipts) == 47
    assert usage['generated_bytes']-baseline['generated_bytes'] == sum(row['output_bytes'] for row in receipts)
    assert usage['unit_count']-baseline['unit_count'] == 47
    assert usage['reserved_bytes'] == 0 and usage['unknown_unit_count'] == 0
    # The original QC request is accepted; this representative deliberately does
    # not run the independent QC worker or claim a native quality conclusion.
    qc_request = case.scheduler._get(callback_receipts[0][3])
    assert qc_request.owner_type == QUALITY_KIND and qc_request.state == 'waiting'
    case.scheduler.close()
