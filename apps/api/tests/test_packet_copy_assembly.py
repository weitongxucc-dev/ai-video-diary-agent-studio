import hashlib
import json
import subprocess
import pytest

from app.artifact_store import ArtifactStore
from app.creation.contracts import ArtifactRecord, TransformRun
from app.creation.packet_copy_assembly import PacketCopyAssembly
from app.creation_execution import CpuRenderGrantContext, CpuRenderLimits, CreationJobOwnerVerifier, CreationJobUnitExecutor
from app.device_scheduler import DeviceScheduler, SchedulePolicy, SubmissionIntent
from app.host_resource_probes import HostProcessProbe
from app.models import utc_now
from app.montage import (CreationTaskMontagePlan, MontageClip, NarrationCue,
    creation_task_montage_plan_to_payload, render_granted_picture_fragment)
from app.creation.assembly_manifest import iter_picture_windows
from app.packet_copy_policy import PacketCopyBudget
from app.production import ProductionRepository, MaterialAsset, MaterialSegment
from test_creation_execution import _narration_units_case


def _packet_case(tmp_path, monkeypatch, *, before_prepare=None, montage_adjust=None):
    case = _narration_units_case(tmp_path, local_policy=True, duration_seconds=2)
    import app.creation_execution as execution_module
    from app.creation_execution import submit_creation_stage
    original_next = execution_module.next_creation_intent
    def stop_after_audio(repository, scheduler, job, **kwargs):
        if repository.get_creation_task(job.workspace_id, job.task_id).narration_cache_key is not None:
            return None
        return original_next(repository, scheduler, job, **kwargs)
    with monkeypatch.context() as setup:
        setup.setattr(execution_module, 'next_creation_intent', stop_after_audio)
        submit_creation_stage(case.execution, case.scheduler, case.job, case.actor)
        for _ in range(3):
            grant = case.scheduler.pick_and_grant()
            assert grant is not None, (case.repo.get_job_run('local', case.job.id).failure_reason, case.repo.list_creation_job_request_states('local', case.job.id))
            case.executor.execute(grant, case.scheduler)
    case.job = case.repo.get_job_run('local', case.job.id)
    source = tmp_path / 'library' / 'source.mp4'
    source.parent.mkdir(exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=24:duration=1',
        '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True)
    asset = case.repo._save_asset(MaterialAsset(id='asset', workspace_id='local', file_name=source.name,
        path=str(source), size_bytes=source.stat().st_size, modified_at=utc_now(),
        usage_role='b_roll', review_status='approved', analysis_status='ready', media_type='video',
        owner_user_id=case.actor.member_id, duration_seconds=1,
        segments=[MaterialSegment(id='segment', asset_id='asset', start_seconds=0, end_seconds=1,
            description='synthetic source', review_status='approved')]))
    source_ref = case.repo.workspace_media_reference_for_asset('local', asset.id)
    with case.repo._connect() as conn:
        case.repo._claim_workspace_media_reference(conn, workspace_id='local', reference=source_ref)
    cut = MontageClip(id='cut', cue_id='cue', asset_id='asset', segment_id='segment', source_path=source_ref,
        source_start_seconds=0, source_end_seconds=1, timeline_start_seconds=0, timeline_end_seconds=1,
        match_score=1, match_reasons=['synthetic approved source'], needs_confirmation=False)
    now = utc_now()
    montage = CreationTaskMontagePlan(id='picture-plan', workspace_id='local', task_id=case.task_id,
        script_id=case.script_id, script_title='synthetic packet tree', status='ready',
        cues=[NarrationCue('cue', 'section_1', '原文', 1)], clips=[cut], duration_seconds=1,
        low_confidence_clip_count=0, created_at=now, updated_at=now, aspect_ratio='16:9')
    if montage_adjust is not None:
        montage_adjust(case, montage)
    case.repo.save_creation_task_montage_plan('local', case.task_id, montage.id, creation_task_montage_plan_to_payload(montage))
    budget = PacketCopyBudget('a'*64, 1024**3, 1024**3, 32, 1, .1, 16384, 32768, 65536, 30)
    store = ArtifactStore(tmp_path / 'artifacts')
    service = PacketCopyAssembly(case.repo, store, tmp_path / 'work', budget=budget, reserve_bytes=1024)
    case.owner.cpu_render_limits = CpuRenderLimits(32*1024**2, 1, 30)
    original_render_facts = case.owner.render_facts
    recipe = service.recipe(case.job)
    windows = list(iter_picture_windows(montage, source_identities={source_ref: {
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}}, max_frames_per_unit=10))
    identities = []
    for index, window in enumerate(windows):
        fragment = {'first_frame': window.timeline_start_frame, 'packet_count': window.frame_count,
            'contract': 'h264-crf20-b0-fmp4/v1'}
        key = f'render:picture-fragment:v1:{window.id}'
        resources = {'kind': 'creation_render', 'max_output_bytes': 32*1024**2, 'threads': 1, 'timeout_seconds': 30,
            'picture_recipe_digest': recipe, 'picture_fragment': fragment}
        monkeypatch.setattr(case.owner, 'render_facts', lambda *args, _key=key, _resources=resources, **kwargs: (_key, _resources))
        case.scheduler.submit(SubmissionIntent('local', 'creation_job', case.job.id, case.job.input_digest, key, SchedulePolicy()), case.actor)
        grant = case.scheduler.pick_and_grant()
        assert grant is not None
        import time
        context = CpuRenderGrantContext(case.scheduler, grant, time.monotonic()+30, 32*1024**2, 1, None)
        path = tmp_path / f'leaf-{index}.mp4'
        render_granted_picture_fragment(window, source, path, context)
        published = store.publish_file(f'leaves/{index}.mp4', path)
        identity = f'leaf-{index}'
        record = ArtifactRecord(identity, 'local', case.task_id, 'picture_fragment', published.logical_ref,
            published.sha256, published.size_bytes, case.job.id, [source_ref])
        transform = TransformRun(id=f'transform_{identity}', workspace_id='local', task_id=case.task_id,
            job_id=case.job.id, transform_type='picture_fragment', input_digest=window.id,
            input_refs=record.input_refs, output_refs=[record.logical_ref], engine=fragment['contract'],
            parameters={'picture_fragment': {**fragment, 'recipe_digest': recipe,
                'producer_request_id': grant.request_id, 'producer_generation': grant.generation}},
            status='succeeded', started_at=now, finished_at=utc_now())
        case.repo.record_task_media_publication(record, transform, [])
        case.scheduler.complete_unit(grant, checkpoint_database_path=case.repo.database_path,
            checkpoint_write=lambda conn, current: 'fixture-leaf-completed')
        identities.append(identity)
    monkeypatch.setattr(case.owner, 'render_facts', original_render_facts)
    if before_prepare is not None:
        before_prepare(case, service, store, identities)
    plan = service.prepare(case.job, identities, actor=case.actor, write_guard=lambda conn: None)
    case.owner.packet_copy_assembly = case.executor.packet_copy_assembly = service
    return case, service, budget, store, plan, original_next


def test_original_job_packet_tree_restarts_with_original_parents_and_real_limits(tmp_path, monkeypatch):
    case, service, budget, store, plan, original_next = _packet_case(tmp_path, monkeypatch)
    import app.granted_subprocess as commands
    original = commands.run_granted_command
    observed = []
    def capture(scheduler, grant, command, **kwargs):
        if scheduler._get(grant.request_id).resources.get('kind') == 'creation_packet_copy':
            observed.append((kwargs['max_stdout_bytes'], kwargs['max_stderr_bytes'], kwargs['timeout_seconds']))
        return original(scheduler, grant, command, **kwargs)
    monkeypatch.setattr(commands, 'run_granted_command', capture)
    intent = original_next(case.repo, case.scheduler, case.job)
    case.scheduler.submit(intent, case.actor)
    first = case.scheduler.pick_and_grant()
    assert first is not None
    case.executor.execute(first, case.scheduler)
    assert case.scheduler._get(first.request_id).state == 'done', case.repo.get_job_run('local', case.job.id).failure_reason
    completed = case.repo.get_artifact_record('local', plan['units'][0]['artifact_id'])
    old_path = store.resolve_file(completed.logical_ref)
    old_sha, old_mtime = completed.sha256, old_path.stat().st_mtime_ns
    case.scheduler.close()
    repo = ProductionRepository(case.repo.database_path, video_library_path=tmp_path/'library')
    service = PacketCopyAssembly(repo, store, tmp_path/'work', budget=budget, reserve_bytes=1024)
    owner = CreationJobOwnerVerifier(repo, cpu_render_limits=case.owner.cpu_render_limits,
        verify_identity=lambda actor: 'ready', packet_copy_assembly=service)
    scheduler = DeviceScheduler(repo.database_path, tmp_path/'units.lock', owners=owner,
        resources=case.resources, processes=HostProcessProbe())
    executor = CreationJobUnitExecutor(repo, render_action=lambda *_: None, render_is_valid=lambda *_: False,
        packet_copy_assembly=service)
    following = scheduler.pick_and_grant()
    assert following is not None and following.unit_key != first.unit_key
    executor.execute(following, scheduler)
    assert scheduler._get(following.request_id).state == 'done', repo.get_job_run('local', case.job.id).failure_reason
    master = service.master(repo.get_job_run('local', case.job.id))
    assert master is not None and master.id == plan['master_artifact_id']
    assert master.producer_job_id == case.job.id
    assert old_path.stat().st_mtime_ns == old_mtime and hashlib.sha256(old_path.read_bytes()).hexdigest() == old_sha
    assert observed and all(stdout == budget.max_stdout_bytes and stderr == budget.max_stderr_bytes
        and 0 < timeout <= budget.preparation_timeout_seconds for stdout, stderr, timeout in observed)
    with repo._connect() as conn:
        receipts = [json.loads(row[0]) for row in conn.execute("SELECT payload_json FROM task_events WHERE job_id=? AND event_type='picture_packet_output_measured'", (case.job.id,))]
    assert len(receipts) == 2 and all(row['consumption_known'] and row['output_bytes'] > 0 for row in receipts)
    usage = repo.creation_narration_usage('local', case.job.id)['scope']['usage']
    assert usage['generated_bytes'] >= sum(row['output_bytes'] for row in receipts)
    scheduler.close()


@pytest.mark.parametrize('attack', ['parent_bytes', 'scope_shrink'])
def test_packet_copy_before_start_rechecks_original_parent_and_quota(tmp_path, monkeypatch, attack):
    from app.creation.execution_budget_scope import BudgetScopeExhausted
    case, service, budget, store, plan, original_next = _packet_case(tmp_path, monkeypatch)
    intent = original_next(case.repo, case.scheduler, case.job)
    case.scheduler.submit(intent, case.actor)
    grant = case.scheduler.pick_and_grant()
    assert grant is not None
    baseline = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
    if attack == 'parent_bytes':
        record = case.repo.get_artifact_record('local', plan['leaf_artifact_ids'][0])
        with store.resolve_file(record.logical_ref).open('r+b') as file:
            file.write(b'changed')
        error = PermissionError
    else:
        case.repo.authorize_execution_budget_scope('local', case.task_id, case.actor,
            scope_id='isolated-local-goal', expected_revision=0, request_id='shrink-before-copy-start',
            source_ref='isolated-test:copy-quota', target_task_ids=[case.task_id],
            limits={'execution_seconds': budget.preparation_timeout_seconds - 1, 'generated_bytes': None},
            policy_kind='explicit_total', authorization_guard=lambda conn: None)
        error = BudgetScopeExhausted
    with pytest.raises(error):
        case.executor.execute(grant, case.scheduler)
    current = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
    assert current['unit_count'] == baseline['unit_count']
    assert current['generated_bytes'] == baseline['generated_bytes']
    with case.repo._connect() as conn:
        assert conn.execute("SELECT count(*) FROM task_events WHERE job_id=? AND event_type='picture_packet_output_measured'",
            (case.job.id,)).fetchone()[0] == 0
    case.scheduler.close()


def test_packet_publication_unknown_keeps_original_reservation_and_receipt(tmp_path, monkeypatch):
    from app.device_scheduler import RecoveryBlocked
    case, service, budget, store, plan, original_next = _packet_case(tmp_path, monkeypatch)
    intent = original_next(case.repo, case.scheduler, case.job)
    case.scheduler.submit(intent, case.actor)
    grant = case.scheduler.pick_and_grant()
    publish = store.publish_file
    def interrupted(logical_ref, source, **kwargs):
        def stop():
            raise OSError('controlled publication interrupted before its cleanup')
        return publish(logical_ref, source, **{**kwargs, 'check': stop})
    monkeypatch.setattr(store, 'publish_file', interrupted)
    with pytest.raises(RecoveryBlocked, match='真实新文件字节未知'):
        case.executor.execute(grant, case.scheduler)
    usage = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
    assert usage['reserved_bytes'] > 0 and usage['unknown_unit_count'] == 1
    event = case.repo.get_task_event('local', f'event_{grant.request_id}_{grant.generation}_picture_output')
    assert event.payload['consumption_known'] is False and event.payload['output_bytes'] is None
    with pytest.raises(KeyError):
        case.repo.get_artifact_record('local', plan['units'][0]['artifact_id'])
    case.scheduler.close()


@pytest.mark.parametrize('corrupt', [False, True])
def test_packet_parent_admission_reads_only_stats_and_sha_runs_in_original_grant(tmp_path, monkeypatch, corrupt):
    import builtins
    import io
    import os
    from pathlib import Path
    import app.device_resource_configuration as identities
    import app.montage as media
    allowed = [False]
    verified_grants, copied = [], []
    def guard_before_prepare(case, service, store, artifact_ids):
        parent_paths = {store.resolve_file(case.repo.get_artifact_record('local', identity).logical_ref)
            for identity in artifact_ids}
        if corrupt:
            with next(iter(parent_paths)).open('r+b') as file:
                file.write(b'changed')
        def guarded(original):
            def call(path, *args, **kwargs):
                if isinstance(path, (str, bytes, os.PathLike)) and Path(os.fsdecode(path)).resolve() in parent_paths:
                    assert allowed[0], 'admission/master/prepare must not read parent media bytes'
                return original(path, *args, **kwargs)
            return call
        monkeypatch.setattr(builtins, 'open', guarded(builtins.open))
        monkeypatch.setattr(io, 'open', guarded(io.open))
        monkeypatch.setattr(os, 'open', guarded(os.open))
        hash_file = identities._hash_file
        def no_admission_hash(path):
            assert Path(path).resolve() not in parent_paths, 'parent SHA belongs to original Grant, not admission/cache'
            return hash_file(path)
        monkeypatch.setattr(identities, '_hash_file', no_admission_hash)
        verify = service._verify_parent_bytes
        def in_grant(parents, context):
            case.scheduler.assert_grant(context.grant)
            verified_grants.append((context.grant.request_id, context.grant.generation))
            allowed[0] = True
            try:
                return verify(parents, context)
            finally:
                allowed[0] = False
        monkeypatch.setattr(service, '_verify_parent_bytes', in_grant)
        concatenate = media.concatenate_granted_picture_fragments
        def copy_only_after_sha(*args, **kwargs):
            assert not corrupt, 'bad original parent SHA must be rejected before packet copy/publication'
            assert verified_grants
            copied.append(True)
            return concatenate(*args, **kwargs)
        monkeypatch.setattr(media, 'concatenate_granted_picture_fragments', copy_only_after_sha)
    case, service, budget, store, plan, original_next = _packet_case(tmp_path, monkeypatch,
        before_prepare=guard_before_prepare)
    assert service.master(case.job) is None
    intent = original_next(case.repo, case.scheduler, case.job)
    case.scheduler.submit(intent, case.actor)
    grant = case.scheduler.pick_and_grant()
    assert grant is not None and not verified_grants
    case.executor.execute(grant, case.scheduler)
    assert verified_grants == [(grant.request_id, grant.generation)]
    request = case.scheduler._get(grant.request_id)
    if corrupt:
        assert not copied and request.state == 'failed'
        assert '完整SHA' in case.repo.get_job_run('local', case.job.id).failure_reason
        measured = case.repo.get_task_event('local', f'event_{grant.request_id}_{grant.generation}_picture_output')
        assert measured.payload['consumption_known'] is True and measured.payload['output_bytes'] == 0
        with pytest.raises(KeyError):
            case.repo.get_artifact_record('local', plan['units'][0]['artifact_id'])
    else:
        assert copied == [True] and request.state == 'done'
        checkpoint = case.repo.get_task_event('local', f'event_{grant.request_id}_{grant.generation}_picture_parents_verified')
        assert len(checkpoint.payload['parents']) == 2
        assert service.master(case.repo.get_job_run('local', case.job.id)) is None
    case.scheduler.close()
