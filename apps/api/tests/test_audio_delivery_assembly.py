from dataclasses import replace
import hashlib
import json

import pytest

from app.audio_delivery_policy import AudioEncodeBudget, FinalMuxBudget
from app.creation.audio_delivery_assembly import AudioDeliveryAssembly
from app.creation_execution import CreationJobOwnerVerifier, CreationJobUnitExecutor
from app.device_scheduler import DeviceScheduler
from app.host_resource_probes import HostProcessProbe
from app.montage import creation_task_montage_plan_from_payload
from app.production import ProductionRepository
from app.task_audio import TaskAudio
from test_assembly_manifest import timeline
from test_packet_copy_assembly import _packet_case


def _delivery_case(tmp_path, monkeypatch):
    def add_audio(case, montage):
        segments = case.repo.list_narration_segments('local', case.task_id, case.script_id)
        graph = timeline([segments[0].audio_reference, segments[1].audio_reference, segments[0].audio_reference])
        for clip in graph.tracks[0].clips:
            clip.gain_db = -12
        montage.audio_timeline = graph
    case, picture, _, store, picture_manifest, original_next = _packet_case(tmp_path, monkeypatch, montage_adjust=add_audio)
    current = case.repo.get_job_run('local', case.job.id)
    intent = original_next(case.repo, case.scheduler, current)
    case.scheduler.submit(intent, case.actor)
    for _ in range(2):
        grant = case.scheduler.pick_and_grant()
        assert grant is not None
        case.executor.execute(grant, case.scheduler)
        assert case.scheduler._get(grant.request_id).state == 'done', case.repo.get_job_run('local', case.job.id).failure_reason
    audio = TaskAudio(case.repo, store, tmp_path/'audio')
    case.owner.audio_assembly = case.executor.audio_assembly = audio
    case.owner.narration_audio_budget = replace(case.owner.narration_audio_budget, max_input_count=2)
    montage = creation_task_montage_plan_from_payload(case.repo.get_creation_task_montage_plan('local', case.task_id))
    def audio_only(repository, scheduler, job, **kwargs):
        manifest = audio.render_assembly(job, montage, actor=case.actor, budget=case.owner.narration_audio_budget,
            prepare=True, connection=kwargs.get('connection'))
        try:
            repository.execution_records.get_artifact_record('local', f'artifact_audio_assembly_{manifest.final.id}')
        except KeyError:
            return original_next(repository, scheduler, job, **kwargs)
        return None
    with monkeypatch.context() as setup:
        setup.setattr('app.creation_execution.next_creation_intent', audio_only)
        intent = audio_only(case.repo, case.scheduler, case.repo.get_job_run('local', case.job.id))
        case.scheduler.submit(intent, case.actor)
        while True:
            grant = case.scheduler.pick_and_grant()
            if grant is None:
                break
            case.executor.execute(grant, case.scheduler)
            assert case.scheduler._get(grant.request_id).state == 'done', case.repo.get_job_run('local', case.job.id).failure_reason
    encoded_budget = AudioEncodeBudget('b'*64, 1024**3, 1024**3, 32, 1, .1, 24576, 49152, 65536, 30)
    mux_budget = FinalMuxBudget('c'*64, 1024**3, 1024**3, 32, 1, .1, 24576, 49152, 65536, 30)
    delivery = AudioDeliveryAssembly(case.repo, store, tmp_path/'work', audio=audio, packet_copy_assembly=picture,
        audio_budget=encoded_budget, mux_budget=mux_budget, reserve_bytes=1024)
    # Existing master selection must not re-enter whole-source admission or remix.
    case.owner.audio_assembly = case.executor.audio_assembly = None
    case.owner.audio_delivery = case.executor.audio_delivery = delivery
    def forbidden(*a, **kw):
        raise AssertionError('delivery must use original master facts, never remix/probe its original sources')
    monkeypatch.setattr(audio, 'master_mix_facts', forbidden)
    monkeypatch.setattr(audio, '_mix_sources', forbidden)
    monkeypatch.setattr(audio, 'render_assembly', forbidden)
    return case, picture, store, audio, delivery, original_next


def test_native_durable_aac_then_copy_restarts_and_keeps_priming_and_original_master(tmp_path, monkeypatch):
    case, picture, store, audio, delivery, original_next = _delivery_case(tmp_path, monkeypatch)
    import app.granted_subprocess as commands
    original_run = commands.run_granted_command
    commands_seen = []
    def capture(scheduler, grant, command, **kwargs):
        resources = scheduler._get(grant.request_id).resources
        if resources.get('kind') in {'creation_audio_encode', 'creation_final_mux'}:
            assert kwargs['max_stdout_bytes'] == resources['max_stdout_bytes'] == 24576
            assert kwargs['max_stderr_bytes'] == resources['max_stderr_bytes'] == 49152
            assert 0 < kwargs['timeout_seconds'] <= resources['timeout_seconds']
            if command[0] == 'ffmpeg':
                commands_seen.append((resources['kind'], list(command)))
        return original_run(scheduler, grant, command, **kwargs)
    monkeypatch.setattr(commands, 'run_granted_command', capture)
    original_job = case.repo.get_job_run('local', case.job.id)
    intent = original_next(case.repo, case.scheduler, original_job)
    assert intent.unit_key.startswith('render:audio-encode:v1:')
    case.scheduler.submit(intent, case.actor)
    encode = case.scheduler.pick_and_grant()
    assert encode is not None
    baseline = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']['generated_bytes']
    case.executor.execute(encode, case.scheduler)
    assert case.scheduler._get(encode.request_id).state == 'done', case.repo.get_job_run('local', case.job.id).failure_reason
    manifest = delivery.manifest(original_job, actor=case.actor)
    encoded = delivery._artifact(original_job, manifest, 'audio_encode')
    assert encoded['parent']['clock']['sample_count'] == 48000
    assert encoded['parent']['clock']['first_pts'] < 0
    assert encoded['parent']['clock']['initial_skip_samples'] == 1024
    assert encoded['parent']['clock']['trailing_padding_samples'] is None
    from pathlib import Path
    path = Path(encoded['path'])
    mtime = path.stat().st_mtime_ns
    case.scheduler.close()
    repo = ProductionRepository(case.repo.database_path, video_library_path=tmp_path/'library')
    fresh_audio = TaskAudio(repo, store, tmp_path/'audio')
    picture = type(picture)(repo, store, tmp_path/'work', budget=picture.budget, reserve_bytes=1024)
    delivery = AudioDeliveryAssembly(repo, store, tmp_path/'work', audio=fresh_audio, packet_copy_assembly=picture,
        audio_budget=delivery.audio_budget, mux_budget=delivery.mux_budget, reserve_bytes=1024)
    owner = CreationJobOwnerVerifier(repo, cpu_render_limits=case.owner.cpu_render_limits,
        verify_identity=lambda actor: 'ready', packet_copy_assembly=picture, audio_delivery=delivery)
    scheduler = DeviceScheduler(repo.database_path, tmp_path/'units.lock', owners=owner,
        resources=case.resources, processes=HostProcessProbe())
    executor = CreationJobUnitExecutor(repo, render_action=lambda *_: pytest.fail('copy cannot enter whole render'),
        render_is_valid=lambda *_: False, packet_copy_assembly=picture, audio_delivery=delivery)
    mux = scheduler.pick_and_grant()
    assert mux is not None and mux.unit_key.startswith('render:final-mux:v1:')
    executor.execute(mux, scheduler)
    assert scheduler._get(mux.request_id).state == 'done', repo.get_job_run('local', case.job.id).failure_reason
    completed = delivery.completed(repo.get_job_run('local', case.job.id), actor=case.actor)
    assert completed is not None and completed['parent']['clock']['audio']['sample_count'] == 48000
    assert path.stat().st_mtime_ns == mtime and hashlib.sha256(path.read_bytes()).hexdigest() == encoded['parent']['binding']['sha256']
    assert [kind for kind, _ in commands_seen] == ['creation_audio_encode', 'creation_final_mux']
    assert commands_seen[1][1][commands_seen[1][1].index('-c:v')+1] == 'copy'
    assert commands_seen[1][1][commands_seen[1][1].index('-c:a')+1] == 'copy'
    with repo._connect() as conn:
        receipts = [json.loads(row[0]) for row in conn.execute("SELECT payload_json FROM task_events WHERE job_id=? AND event_type='audio_delivery_output_measured'", (case.job.id,))]
    usage = repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
    assert len(receipts) == 2 and all(row['consumption_known'] and row['output_bytes'] > 0 for row in receipts)
    assert usage['generated_bytes'] - baseline == sum(row['output_bytes'] for row in receipts)
    scheduler.close()


@pytest.mark.parametrize('problem', ['master_hash', 'quota_shrink', 'publication_unknown'])
def test_audio_encode_original_parent_quota_and_unknown_boundaries(tmp_path, monkeypatch, problem):
    from pathlib import Path
    from app.creation.execution_budget_scope import BudgetScopeExhausted
    from app.device_scheduler import RecoveryBlocked
    case, picture, store, audio, delivery, original_next = _delivery_case(tmp_path, monkeypatch)
    job = case.repo.get_job_run('local', case.job.id)
    if problem == 'master_hash':
        facts = delivery._master(job, case.actor)
        with Path(facts['path']).open('r+b') as output:
            output.seek(44)
            output.write(b'changed')
    intent = original_next(case.repo, case.scheduler, job)
    case.scheduler.submit(intent, case.actor)
    grant = case.scheduler.pick_and_grant()
    assert grant is not None
    baseline = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
    if problem == 'quota_shrink':
        case.repo.authorize_execution_budget_scope('local', case.task_id, case.actor,
            scope_id='isolated-local-goal', expected_revision=0, request_id='shrink-aac-before-start',
            source_ref='isolated-test:aac-quota', target_task_ids=[case.task_id],
            limits={'execution_seconds': 1, 'generated_bytes': None}, policy_kind='explicit_total',
            authorization_guard=lambda conn: None)
        with pytest.raises(BudgetScopeExhausted):
            case.executor.execute(grant, case.scheduler)
        usage = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
        assert usage['unit_count'] == baseline['unit_count'] and usage['generated_bytes'] == baseline['generated_bytes']
    elif problem == 'master_hash':
        import app.granted_subprocess as commands
        def no_media(*a, **kw):
            raise AssertionError('corrupt original master must be refused before AAC/media work')
        monkeypatch.setattr(commands, 'run_granted_command', no_media)
        case.executor.execute(grant, case.scheduler)
        assert case.scheduler._get(grant.request_id).state == 'failed'
        assert '完整SHA' in case.repo.get_job_run('local', case.job.id).failure_reason
        measured = case.repo.execution_records.get_task_event('local', delivery.measured_event_id(grant))
        assert measured.payload['consumption_known'] and measured.payload['output_bytes'] == 0
    else:
        def unknown(*a, **kw):
            raise OSError('controlled publication fail before temporary identity was returned')
        monkeypatch.setattr(store, 'publish_file', unknown)
        with pytest.raises(RecoveryBlocked, match='真实新文件字节未知'):
            case.executor.execute(grant, case.scheduler)
        measured = case.repo.execution_records.get_task_event('local', delivery.measured_event_id(grant))
        assert measured.payload['consumption_known'] is False and measured.payload['output_bytes'] is None
        usage = case.repo.execution_budget_scope_usage('local', 'isolated-local-goal')['usage']
        assert usage['unknown_unit_count'] == 1 and usage['reserved_bytes'] > 0
    case.scheduler.close()
