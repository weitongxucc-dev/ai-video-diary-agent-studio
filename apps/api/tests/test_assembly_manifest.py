import array
import hashlib
import math
import wave

import pytest

from app.audio_timeline import AudioTimeline, TimelineAudioClip, TimelineAudioTrack, render_audio_mix
from app.creation.assembly_manifest import plan_audio_assembly


def timeline(refs):
    return AudioTimeline(tracks=[TimelineAudioTrack(id='voice', name='明确线性配音', role='narration',
        clips=[TimelineAudioClip(id=f'clip-{i}', name=f'配音{i}', source_ref=ref, source_start_ms=0,
            source_end_ms=1000, timeline_start_ms=0, gain_db=6, fade_in_ms=100, fade_out_ms=100)
            for i, ref in enumerate(refs)])])


def test_manifest_bounds_each_reduction_and_keeps_absolute_gain_envelopes():
    refs = [f'artifacts/source-{i}' for i in range(23)]
    graph = timeline(refs)
    before = graph.model_dump()
    plan = plan_audio_assembly(graph, 1000, workspace_id='local', task_id='task', producer_job_id='job', source_hashes={ref: 'a' * 64 for ref in refs},
        max_input_count=5, normalize_voice_sources=True)
    assert plan.final.master and all(not unit.master for unit in plan.units[:-1])
    assert all(len(unit.input_refs) <= 5 for unit in plan.units)
    leaves = [unit for unit in plan.units if not unit.dependencies]
    assert sum(len(unit.graph.tracks[0].clips) for unit in leaves) == 23
    assert all(sum(len(track.clips) for track in unit.graph.tracks) <= 5 for unit in leaves)
    assert [clip.model_dump() for unit in leaves for track in unit.graph.tracks for clip in track.clips] == before['tracks'][0]['clips']
    assert graph.model_dump() == before
    changed = plan_audio_assembly(graph, 1000, workspace_id='local', task_id='task', producer_job_id='job', source_hashes={ref: 'b' * 64 for ref in refs},
        max_input_count=5, normalize_voice_sources=True)
    assert changed.digest != plan.digest and changed.final.id != plan.final.id


def test_nonlinear_sidechain_is_not_silently_replaced_by_linear_reduction():
    graph = timeline(['artifacts/source'])
    graph.tracks[0].role = 'music'
    graph.tracks[0].duck_under_voice = True
    with pytest.raises(ValueError, match='侧链'):
        plan_audio_assembly(graph, 1000, workspace_id='local', task_id='task', producer_job_id='job', source_hashes={'artifacts/source': 'a' * 64}, max_input_count=5)


def test_actual_twenty_source_float_reduction_matches_one_master_mix(tmp_path, granted_media):
    originals = {}
    values = [round(10000 * math.sin(2 * math.pi * 440 * n / 48000)) for n in range(48000)]
    for i in range(20):
        path = tmp_path / f'source-{i}.wav'
        with wave.open(str(path), 'wb') as output:
            output.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            output.writeframes(array.array('h', values if i < 11 else [-value for value in values]).tobytes())
        originals[f'artifacts/source-{i}'] = path
    hashes = {ref: hashlib.sha256(path.read_bytes()).hexdigest() for ref, path in originals.items()}
    graph = timeline(list(originals))
    plan = plan_audio_assembly(graph, 1000, workspace_id='local', task_id='task', producer_job_id='job', source_hashes=hashes, max_input_count=5)
    paths = dict(originals)
    observed_float_peak = 0
    for unit in plan.units:
        source_paths = [paths[ref] for ref in unit.input_refs]
        assert len(source_paths) <= 5
        with granted_media(source_paths) as context:
            assert context.scheduler._get(context.grant.request_id).resources['input_count'] <= 5
            output = tmp_path / f'{unit.id}.wav'
            render_audio_mix(unit.graph, 1, paths.__getitem__, output, apply_master=unit.master,
                scheduler=context.scheduler, grant=context.grant, deadline_monotonic=context.deadline_monotonic,
                max_output_bytes=context.max_output_bytes, threads=context.threads)
        paths[unit.output_ref('task')] = output
        if not unit.master:
            data = output.read_bytes()
            marker = data.index(b'data')
            raw = array.array('f', data[marker + 8:])
            observed_float_peak = max(observed_float_peak, max(map(abs, raw)))
    # Oversized partial sums must survive until positive and negative tracks cancel.
    assert observed_float_peak > 1
    reference = tmp_path / 'single-master.wav'
    with granted_media(list(originals.values())) as context:
        render_audio_mix(graph, 1, originals.__getitem__, reference,
            scheduler=context.scheduler, grant=context.grant, deadline_monotonic=context.deadline_monotonic,
            max_output_bytes=context.max_output_bytes, threads=context.threads)
    def pcm(path):
        with wave.open(str(path), 'rb') as source:
            assert source.getframerate() == 48000 and source.getnframes() == 48000
            return array.array('h', source.readframes(source.getnframes()))
    actual, expected = pcm(paths[plan.final.output_ref('task')]), pcm(reference)
    assert len(actual) == len(expected)
    assert max(abs(left - right) for left, right in zip(actual, expected)) <= 2
    assert {ref: hashlib.sha256(path.read_bytes()).hexdigest() for ref, path in originals.items()} == hashes


@pytest.mark.parametrize(('restart_after_first', 'preflight_attack'), [(False, None), (True, None),
    (False, 'wrong_manifest'), (False, 'revoke'), (False, 'normalization_failure'),
    (False, 'normalization_unknown'), (False, 'same_transaction_head')])
def test_creation_job_uses_durable_audio_units_before_actual_hardcut_render(tmp_path, monkeypatch, restart_after_first, preflight_attack):
    import json
    import subprocess
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.artifact_store import ArtifactStore
    from app.creation.editing.metadata_use_case import MetadataEditUseCase
    from app.creation.render_stage_use_case import RenderStageUseCase
    from app.creation_execution import CpuRenderLimits, CreationJobOwnerVerifier, CreationJobUnitExecutor, submit_creation_stage
    from app.device_scheduler import DeviceScheduler
    from app.host_resource_probes import HostProcessProbe
    from app.production import ProductionRepository
    from app.media.picture_preview import PicturePreviewRenderer
    from app.montage import CreationTaskMontagePlan, MontageClip, MontageRenderer, NarrationCue, creation_task_montage_plan_to_payload
    from app.production import MaterialAsset, MaterialSegment
    from app.task_audio import TaskAudio
    from app.video_jobs import VideoJobRunner
    from test_creation_execution import _narration_units_case
    case = _narration_units_case(tmp_path, audio_input_count=5, local_policy=True, duration_seconds=2)
    submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
    while case.repo.get_creation_task('local', case.task_id).narration_cache_key is None:
        case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    task = case.repo.get_creation_task('local', case.task_id)
    source = tmp_path / 'library' / 'dynamic.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=30:duration=2',
        '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(source)], check=True)
    asset = case.repo._save_asset(MaterialAsset(id='dynamic', workspace_id='local', file_name=source.name,
        path=str(source), size_bytes=source.stat().st_size, modified_at=datetime.now(timezone.utc),
        duration_seconds=2, usage_role='b_roll', review_status='approved', analysis_status='ready',
        media_type='video', owner_user_id=case.actor.member_id, segments=[MaterialSegment(id='dynamic-segment',
            asset_id='dynamic', start_seconds=0, end_seconds=2, description='隔离动态素材', review_status='approved')]))
    source_ref = case.repo.workspace_media_reference_for_asset('local', asset.id)
    with case.repo._connect() as conn:
        case.repo._claim_workspace_media_reference(conn, workspace_id='local', reference=source_ref)
    segments = case.repo.list_narration_segments('local', case.task_id, case.script_id)
    graph = timeline([segments[i % 2].audio_reference for i in range(20)])
    for clip in graph.tracks[0].clips:
        clip.gain_db = -30
    script = case.repo.get_creation_task_script('local', case.script_id)
    cues = [NarrationCue(id=f'cue-{i}', source_section_id=section.id, text=section.content, duration_seconds=1,
        narration_segment_id=segments[i].id, timeline_start_seconds=i) for i, section in enumerate(script.sections)]
    montage = CreationTaskMontagePlan(id='montage-linear-assembly', workspace_id='local', task_id=case.task_id,
        script_id=case.script_id, script_title='隔离归约', status='ready', cues=cues,
        clips=[MontageClip(id='clip', cue_id='cue-0', asset_id=asset.id, segment_id='dynamic-segment', source_path=source_ref,
            source_start_seconds=0, source_end_seconds=2, timeline_start_seconds=0, timeline_end_seconds=2,
            match_score=1, match_reasons=['隔离许可素材'], needs_confirmation=False)],
        duration_seconds=2, low_confidence_clip_count=0,
        narration_audio_path=case.repo.narration_artifact_reference(task.narration_cache_key), audio_timeline=graph)
    if preflight_attack in {'normalization_failure', 'normalization_unknown'}:
        montage.narration_linkage_created_audio_timeline = True
    case.repo.save_creation_task_montage_plan('local', case.task_id, montage.id, creation_task_montage_plan_to_payload(montage))
    from app.creation.contracts import TimelineVersion
    case.repo.create_timeline_version(TimelineVersion(id='adopted-input', workspace_id='local', task_id=case.task_id,
        parent_version_id=None, sequence=1, snapshot={'montage': creation_task_montage_plan_to_payload(montage)},
        created_by_operation_id=None), expected_current_version_id=None)
    def configure():
        store = ArtifactStore(tmp_path / 'artifacts')
        audio = TaskAudio(case.repo, store, tmp_path / 'temporary')
        metadata = MetadataEditUseCase(case.repo, store, output_root=tmp_path / 'outputs', audio_service=audio)
        renderer = PicturePreviewRenderer(case.repo, store, audio, metadata, MontageRenderer(tmp_path / 'outputs'),
            verify_identity=lambda actor: 'ready' if actor == case.actor else 'unauthorized', presenter_model_ids=lambda: {},
            output_root=tmp_path / 'outputs', portrait_worker_path=None, temporary_root=tmp_path / 'temporary')
        metadata.picture_renderer = renderer
        settings = SimpleNamespace(video_output_path=str(tmp_path / 'outputs'), effective_temporary_root_path=tmp_path / 'temporary')
        render = RenderStageUseCase(case.repo, metadata, None, VideoJobRunner(case.repo), settings)
        case.owner.audio_assembly = case.executor.audio_assembly = audio
        case.owner.cpu_render_limits = CpuRenderLimits(max_output_bytes=32*1024**2, threads=1, timeout_seconds=60)
        render_errors = []
        def actual_render(ws, tid, context):
            try:
                return render.render(ws, tid, render_context=context)
            except Exception:
                import traceback
                render_errors.append(traceback.format_exc())
                raise
        case.executor.render_action = actual_render
        case.executor.render_is_valid = lambda ws, tid, context: render.valid_with_grant(ws, tid, context)
        case.scheduler.resources.budget = SimpleNamespace(cpu_audio=case.owner.narration_audio_budget,
            cpu_render=SimpleNamespace(additional_disk_bytes=64*1024**2), presenter_cpu_render=None)
        case.scheduler.resources._grant_resources = lambda grant: case.scheduler._get(grant.request_id).resources
        return store, render_errors
    store, render_errors = configure()
    first_fragment = None
    scope_before_restart = None
    submit_creation_stage(case.execution, case.scheduler, case.repo.get_job_run('local', case.job.id), case.actor)
    if preflight_attack is not None:
        from dataclasses import replace
        import time
        from app.creation_execution import CpuRenderGrantContext
        grant = case.scheduler.pick_and_grant()
        request = case.scheduler._get(grant.request_id)
        assert request.unit_key.startswith('render:audio-assembly:')
        before = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
        before_cache = case.repo.get_creation_task('local', case.task_id).narration_cache_key
        if preflight_attack == 'same_transaction_head':
            class RolledBack(Exception):
                pass
            with pytest.raises(RolledBack):
                with case.repo._connect() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    case.repo.create_timeline_version(TimelineVersion(id='uncommitted-adoption', workspace_id='local',
                        task_id=case.task_id, parent_version_id='adopted-input', sequence=2,
                        snapshot={'montage': creation_task_montage_plan_to_payload(montage)},
                        created_by_operation_id=None), connection=conn, expected_current_version_id='adopted-input')
                    actual_task = case.repo.get_creation_task('local', case.task_id, connection=conn)
                    actual_plan = case.repo.plan_records.get_creation_plan('local', actual_task.plan_id)
                    _, current_resources = case.owner.render_facts(conn, actual_task, actual_plan,
                        actor=case.actor, job=case.job, prepare_assembly=True)
                    assert current_resources['input_timeline_version_id'] == 'uncommitted-adoption'
                    raise RolledBack()
            assert case.repo.get_creation_task('local', case.task_id).current_timeline_version_id == 'adopted-input'
            case.scheduler.fail_unit(grant, reason='isolated_same_transaction_check',
                failure_write=lambda conn, current: 'isolated_same_transaction_check')
            case.scheduler.assert_released(grant)
            case.scheduler.close()
            return
        if preflight_attack in {'normalization_failure', 'normalization_unknown'}:
            import app.audio_timeline as audio_module
            from app.device_scheduler import RecoveryBlocked
            from pathlib import Path
            written = []
            def partial_normalizer(directory, _graph, resolve, *_args, **_kwargs):
                data = resolve(_graph.tracks[0].clips[0].source_ref).read_bytes()
                first, second = Path(directory) / 'narration-0.wav', Path(directory) / 'narration-1.wav'
                first.write_bytes(data)
                second.write_bytes(data[:127])
                written.append(len(data) + 127)
                raise RuntimeError('synthetic normalization failure after partial output')
            monkeypatch.setattr(audio_module, '_render_narration_normalizations', partial_normalizer)
            if preflight_attack == 'normalization_unknown':
                original = Path.lstat
                def unreadable(path):
                    if path.name == 'narration-1.wav':
                        raise PermissionError('isolated output measurement unavailable')
                    return original(path)
                monkeypatch.setattr(Path, 'lstat', unreadable)
                with pytest.raises(RecoveryBlocked):
                    case.executor.execute(grant, case.scheduler)
                after = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
                assert after['generated_bytes'] == before['generated_bytes']
                assert after['unknown_unit_count'] == before['unknown_unit_count'] + 1
                assert after['reserved_bytes'] == request.resources['max_output_bytes']
                case.scheduler.fail_unit(grant, reason='isolated_output_measurement_unknown',
                    failure_write=lambda conn, current: 'isolated_output_measurement_unknown')
            else:
                case.executor.execute(grant, case.scheduler)
                after = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
                assert after['generated_bytes'] - before['generated_bytes'] == written[0] > 0
                assert after['unknown_unit_count'] == after['reserved_bytes'] == 0
                assert case.repo.get_job_run('local', case.job.id).status == 'failed'
            assert len(case.generated) == 2
            assert case.repo.get_creation_task('local', case.task_id).narration_cache_key == before_cache
            assert not [record for record in case.repo.execution_records.list_artifact_records('local', case.task_id)
                if record.kind == 'audio_assembly_fragment']
            case.scheduler.assert_released(grant)
            case.scheduler.close()
            return
        if preflight_attack == 'revoke':
            case.repo.authorize_execution_budget_scope('local', case.task_id, case.actor,
                scope_id='isolated-local-goal', expected_revision=0, request_id='revoke-before-audio-start',
                source_ref='isolated-test:explicit-revocation', target_task_ids=[case.task_id],
                limits={'execution_seconds': None, 'generated_bytes': None}, policy_kind='local_goal',
                authorization_guard=lambda conn: None, revoked=True)
            with pytest.raises(PermissionError):
                case.executor.execute(grant, case.scheduler)
        else:
            from app.montage import creation_task_montage_plan_from_payload
            adopted = creation_task_montage_plan_from_payload(case.repo.get_creation_task_montage_plan('local', case.task_id))
            manifest = case.owner.audio_assembly.render_assembly(case.job, adopted, actor=case.actor,
                budget=case.owner.narration_audio_budget)
            unit = manifest.units[0]
            graph = unit.graph
            graph.tracks[0].gain_db = -12
            forged = replace(manifest, units=(replace(unit, graph_json=graph.model_dump_json()), *manifest.units[1:]))
            context = CpuRenderGrantContext(case.scheduler, grant, time.monotonic() + request.resources['timeout_seconds'],
                request.resources['max_output_bytes'], request.resources['threads'])
            with pytest.raises(ValueError, match='封存摘要'):
                case.owner.audio_assembly.execute_assembly_unit('local', case.task_id, forged, unit.id,
                    actor=case.actor, budget=case.owner.narration_audio_budget, context=context)
        assert case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage'] == before
        assert case.repo.get_creation_task('local', case.task_id).narration_cache_key == before_cache
        assert not [record for record in case.repo.execution_records.list_artifact_records('local', case.task_id)
            if record.kind == 'audio_assembly_fragment']
        assert len(case.generated) == 2
        assert case.repo.get_job_run('local', case.job.id).status == 'queued'
        case.scheduler.fail_unit(grant, reason='isolated_preflight_rejection',
            failure_write=lambda conn, current: 'isolated_preflight_rejection')
        case.scheduler.assert_released(grant)
        case.scheduler.close()
        return
    admitted = []
    restarted = False
    for _ in range(12):
        grant = case.scheduler.pick_and_grant()
        assert grant is not None
        request = case.scheduler._get(grant.request_id)
        if request.unit_key.startswith('render:audio-assembly:'):
            assert request.resources['input_count'] <= 5
            admitted.append(request.resources['assembly_unit_id'])
        case.executor.execute(grant, case.scheduler)
        current = case.repo.get_job_run('local', case.job.id)
        assert current.status != 'failed', (current.failure_reason, render_errors[-1] if render_errors else None)
        if current.status == 'succeeded':
            break
        if restart_after_first and len(admitted) == 1 and not restarted:
            record = case.repo.execution_records.get_artifact_record('local', f'artifact_audio_assembly_{admitted[0]}')
            first_fragment = (record.logical_ref, record.sha256, store.resolve_file(record.logical_ref).stat().st_mtime_ns)
            scope_before_restart = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
            previous_owner = case.owner
            resources = case.scheduler.resources
            case.scheduler.close()
            case.repo = ProductionRepository(case.repo.database_path, tmp_path / 'library')
            case.owner = CreationJobOwnerVerifier(case.repo, narration_budget=previous_owner.narration_budget,
                narration_audio_budget=previous_owner.narration_audio_budget, montage_budget=previous_owner.montage_budget,
                verify_identity=lambda actor: 'ready' if actor == case.actor else 'unauthorized')
            case.executor = CreationJobUnitExecutor(case.repo, render_action=None, render_is_valid=None,
                narration_stage=case.stage)
            case.scheduler = DeviceScheduler(case.repo.database_path, tmp_path / 'units.lock', owners=case.owner,
                resources=resources, processes=HostProcessProbe())
            store, render_errors = configure()
            restarted = True
    assert current.status == 'succeeded' and len(admitted) == 6
    assert len(case.generated) == 2
    if restart_after_first:
        assert restarted and scope_before_restart['generated_bytes'] > 0
        ref, sha, modified = first_fragment
        assert store.describe(ref).sha256 == sha and store.resolve_file(ref).stat().st_mtime_ns == modified
        assert case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']['generated_bytes'] > scope_before_restart['generated_bytes']
    assert len(case.execution.jobs('local', case.task_id)) == 1
    task = case.repo.get_creation_task('local', case.task_id)
    assert task.output_ready
    output = case.repo.get_creation_task_montage_plan('local', case.task_id)['output_path']
    actual = store.describe(output)
    assert actual.sha256 == hashlib.sha256(store.resolve_file(output).read_bytes()).hexdigest()
    events = case.repo.execution_records.list_task_events('local', case.task_id)
    settlements = [event for event in events if event.event_type == 'task_execution_unit_settled']
    assert len(settlements) == 6 and all(event.payload['output_bytes'] > 0 for event in settlements)
    assert not case.repo.creation_narration_usage('local', case.job.id)['unsettled']
    case.scheduler.close()


def test_same_audio_recipe_has_distinct_persistent_task_and_producer_instances(tmp_path):
    import json
    from datetime import datetime, timezone
    from app.artifact_store import ArtifactStore
    from app.creation.assembly_manifest import AudioAssemblyManifest
    from app.creation.contracts import ArtifactRecord, JobRun, TransformRun
    from test_creation_execution import _seed
    repo, first_task, _, _ = _seed(tmp_path, workspace_id='local', task_suffix='_first')
    _, second_task, _, _ = _seed(tmp_path, workspace_id='local', task_suffix='_second')
    shared = tmp_path / 'shared.wav'
    with wave.open(str(shared), 'wb') as audio:
        audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        audio.writeframes(bytes(192000))
    sha = hashlib.sha256(shared.read_bytes()).hexdigest()
    graph = timeline(['workspace://shared-audio'])
    plans, records = [], []
    store = ArtifactStore(tmp_path / 'artifacts')
    for index, task_id in enumerate((first_task, second_task, first_task)):
        job = repo.create_job_run(JobRun(id=f'identity-job-{index}', workspace_id='local', task_id=task_id,
            kind='creation_task_execution', idempotency_key=f'identity-{index}', input_digest='a'*64))
        plan = plan_audio_assembly(graph, 1000, workspace_id='local', task_id=task_id, producer_job_id=job.id,
            source_hashes={'workspace://shared-audio': sha}, max_input_count=5)
        unit = plan.units[0]
        published = store.publish_file(unit.output_ref(task_id), shared)
        record = ArtifactRecord(id=f'artifact_audio_assembly_{unit.id}', workspace_id='local', task_id=task_id,
            kind='audio_assembly_fragment', logical_ref=published.logical_ref, sha256=published.sha256,
            size_bytes=published.size_bytes, producer_job_id=job.id, input_refs=list(unit.input_refs))
        transform = TransformRun(id=f'transform_audio_assembly_{unit.id}', workspace_id='local', task_id=task_id,
            job_id=job.id, transform_type='audio_assembly', input_digest=unit.id, input_refs=list(unit.input_refs),
            output_refs=[record.logical_ref], engine='isolated-instance-identity-fixture',
            parameters={'manifest_digest': plan.digest}, status='succeeded', started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc))
        repo.record_task_media_publication(record, transform, [])
        restored = AudioAssemblyManifest.from_payload(json.loads(json.dumps(plan.payload())))
        assert restored == plan
        restored.validate_scope('local', task_id, job.id)
        with pytest.raises(PermissionError):
            restored.validate_scope('local', task_id, 'another-producer')
        plans.append(plan)
        records.append(repo.execution_records.get_artifact_record('local', record.id))
    assert len({plan.units[0].id for plan in plans}) == 3
    assert len({record.id for record in records}) == 3
    assert len({record.logical_ref for record in records}) == 3
    assert {record.sha256 for record in records} == {sha}
    assert [record.task_id for record in records] == [first_task, second_task, first_task]
