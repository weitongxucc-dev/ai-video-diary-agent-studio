"""Source execution ownership/queue with real SQL; audio facts are declared, not decoded."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.creation.execution_control import CreationExecutionControl
from app.creation.task_authorization import TaskAuthorizationUseCase
from app.creation_execution import (CreationExecution, CreationExecutionActor, CreationJobOwnerVerifier,
    CpuRenderLimits, ExecutionStartRequest, next_creation_intent, revision_digest, submit_creation_stage)
from app.device_runtime import OwnerRegistration, build_device_runtime
from test_device_runtime import Access
from test_device_scheduler import Resources, Processes
from test_source_task_use_case import source_case as source_case  # noqa: F401


@pytest.fixture
def execution_case(source_case, monkeypatch):  # noqa: F811 (shared source metadata fixture)
    case = source_case
    receipt = case.use_case.create(case.command, case.actor)
    case.task = case.repo.task_records.get_creation_task('local', receipt['task_id'])
    case.plan = case.repo.plan_records.get_creation_plan('local', case.task.plan_id)

    def forbidden(*args, **kwargs):
        pytest.fail('source mode cannot read a Script or dispatch narration/segment work')

    monkeypatch.setattr(case.repo.script_records, 'read_execution_script_metadata', forbidden)
    monkeypatch.setattr(case.repo.script_records, 'get_creation_task_script', forbidden)
    monkeypatch.setattr(case.repo.narration_artifacts, 'get_narration_artifact', forbidden)
    segments = SimpleNamespace(next_initial=forbidden, waiting_for_visual_decision=forbidden,
        visual_decision_locators=forbidden)
    authorizations = TaskAuthorizationUseCase(case.repo, lambda identity: case.verify(
        CreationExecutionActor.from_event(identity['workspace_id'], identity)))
    # This finite audio declaration isolates dispatch from decoder/model work.
    # The source ownership, file stat binding and the real render projection run.
    def audio_facts(workspace_id, task_id, montage, *, actor, connection=None):
        assert (workspace_id, task_id, actor) == ('local', case.task.id, case.actor)
        assert montage.script_id is None and montage.narration_audio_path is None
        return {'sources': [{'path': str(case.path)}], 'resources': {'declared_fixture_audio': True}}
    owner = CreationJobOwnerVerifier(case.repo, cpu_render_limits=CpuRenderLimits(1024 * 1024, 1, 10),
        verify_identity=case.verify, task_authorizations=authorizations, segments=segments,
        narration_budget=SimpleNamespace(input_scope='single_call_prepared_v1'), audio_mix_facts=audio_facts)
    runtime = build_device_runtime(database_path=case.repo.database_path,
        device_lock_path=case.repo.database_path.parent / 'source-execution.lock', device_access=Access(),
        registrations={'creation_job': OwnerRegistration(owner, SimpleNamespace(execute=forbidden))},
        resources=Resources(), processes=Processes())
    runtime.task_authorizations = authorizations
    execution = CreationExecution(case.repo, run_montage_plan=forbidden, run_render=forbidden,
        render_is_valid=lambda *args: False)
    control = CreationExecutionControl(case.repo, execution,
        SimpleNamespace(valid=lambda *args, **kwargs: False), case.verify, segments=segments)
    case.owner, case.runtime, case.execution, case.control = owner, runtime, execution, control
    yield case
    runtime.scheduler.close()


def start(case):
    snapshot = case.repo.execution_inputs.read_creation_execution_snapshot('local', case.task.id)
    case.owner.task_authorizations.ensure(snapshot.task, case.actor)
    return case.execution.start('local', case.task.id, None, snapshot.input_digest, actor=case.actor)


def test_source_snapshot_and_preview_keep_script_genuinely_absent(execution_case):
    case = execution_case
    snapshot = case.repo.execution_inputs.read_creation_execution_snapshot('local', case.task.id)
    assert snapshot.script_header is None
    assert snapshot.input_digest == revision_digest(None, snapshot.plan)
    preview = case.control.preview('local', case.task.id)
    assert preview['script_id'] is preview['script_title'] is preview['script_updated_at'] is None
    assert preview['script_sections_total_count'] == 0 and preview['narration_source'] is None
    assert preview['requirements']['can_continue'] and preview['requirements']['continuation_blocker'] is None
    request = ExecutionStartRequest(script_id=None, input_digest=snapshot.input_digest, requirements_confirmed=True)
    assert request.script_id is None
    with pytest.raises(ValidationError):
        ExecutionStartRequest(input_digest=snapshot.input_digest, requirements_confirmed=True)
    with pytest.raises(ValidationError):
        ExecutionStartRequest(script_id='', input_digest=snapshot.input_digest, requirements_confirmed=True)
    with case.repo._connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        case.repo.plan_records.write_knowledge_adopted_plan(connection, 'local',
            replace(snapshot.plan, source_media={**snapshot.plan.source_media, 'source_end_seconds': 8}))
        borrowed = case.repo.execution_inputs.read_creation_execution_snapshot('local', case.task.id, connection=connection)
        assert borrowed.script_header is None and borrowed.input_digest != snapshot.input_digest
        connection.rollback()


@pytest.mark.parametrize('mutation', ['normal_missing_script', 'script', 'narration', 'presenter', 'unconfirmed'])
def test_absent_script_is_valid_only_for_explicit_confirmed_source_mode(execution_case, mutation):
    case = execution_case
    with case.repo._connect() as connection:
        if mutation == 'normal_missing_script':
            connection.execute("UPDATE creation_plans SET payload=json_remove(payload,'$.source_media') WHERE id=?", (case.plan.id,))
        elif mutation in {'script', 'narration'}:
            field = 'confirmed_script_id' if mutation == 'script' else 'narration_cache_key'
            connection.execute(f"UPDATE creation_tasks SET payload=json_set(payload,'$.{field}','unexpected') WHERE id=?", (case.task.id,))
        elif mutation == 'presenter':
            connection.execute("UPDATE creation_plans SET payload=json_set(payload,'$.presenter_share_target',20) WHERE id=?", (case.plan.id,))
        else:
            connection.execute("UPDATE creation_plans SET payload=json_set(payload,'$.status','draft') WHERE id=?", (case.plan.id,))
    with pytest.raises(ValueError):
        case.repo.execution_inputs.read_creation_execution_snapshot('local', case.task.id)


def test_source_start_queues_and_grants_only_render_on_the_original_job(execution_case):
    case = execution_case
    job = start(case)
    original = case.repo.execution_records.get_task_event('local', f'event_{job.id}_start')
    assert original.payload['script_id'] is None and original.payload['member_id'] == case.actor.member_id
    same = start(case)
    assert same.id == job.id
    intent = next_creation_intent(case.repo, case.runtime.scheduler, job)
    assert intent.owner_id == job.id and intent.input_digest == job.input_digest and intent.unit_key.startswith('render:')
    started = case.control.start('local', case.task.id, None, job.input_digest,
        case.actor, case.runtime, verify_media=False)
    assert started['job']['id'] == job.id
    request_id = submit_creation_stage(case.execution, case.runtime.scheduler, job, case.actor)
    assert submit_creation_stage(case.execution, case.runtime.scheduler, job, case.actor) == request_id
    status = case.control.status('local', case.task.id, verify_media=False, actor=case.actor)
    assert status['job']['id'] == job.id and status['visual_decisions']['items'] == []
    resumed = case.control.resume('local', case.task.id, case.actor, case.runtime, verify_media=False)
    assert resumed['job']['id'] == job.id
    grant = case.runtime.scheduler.pick_and_grant()
    assert grant is not None and grant.owner_id == job.id and grant.request_id == request_id
    assert grant.unit_key.startswith('render:') and grant.input_digest == job.input_digest
    case.runtime.scheduler.assert_grant(grant)
    case.runtime.scheduler.fail_unit(grant, reason='metadata_fixture_completed', failure_write=lambda *args: None)
    case.runtime.scheduler.assert_released(grant)
    with case.repo._connect() as connection:
        assert connection.execute('SELECT count(*) FROM creation_task_scripts').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM narration_artifacts').fetchone()[0] == 0
    assert len(case.execution.jobs('local', case.task.id)) == 1
    with pytest.raises(PermissionError, match='设备队列'):
        case.execution.execute('local', job.id)


@pytest.mark.parametrize('mutation', ['source_revision', 'member_removed', 'plan_scope'])
def test_source_commit_check_refuses_changed_dependencies_without_new_io(execution_case, monkeypatch, mutation):
    case = execution_case
    job = start(case)
    request_id = submit_creation_stage(case.execution, case.runtime.scheduler, job, case.actor)
    request = case.runtime.scheduler._get(request_id)
    with case.repo._connect() as connection:
        observed = case.owner.capture_local(request, connection)
    if mutation == 'source_revision':
        with case.repo._connect() as connection:
            connection.execute("UPDATE material_assets SET updated_at='changed' WHERE id=?", (case.asset.id,))
    elif mutation == 'member_removed':
        case.members.remove_member(case.actor.member_id)
    else:
        with case.repo._connect() as connection:
            connection.execute("UPDATE creation_plans SET payload=json_set(payload,'$.source_media.source_end_seconds',8) WHERE id=?", (case.plan.id,))
    def forbidden(*args, **kwargs):
        pytest.fail('source commit check must only reuse the caller SQL connection')
    with case.repo._connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        with monkeypatch.context() as patch:
            patch.setattr(case.repo, '_connect', forbidden)
            patch.setattr(case.owner, 'verify_identity', forbidden)
            verdict = case.owner.check_local(request, connection, observed)
    assert verdict == ('unauthorized' if mutation == 'member_removed' else 'stale')
    assert case.repo.task_records.get_creation_task('local', case.task.id).confirmed_script_id is None
    assert case.repo.task_records.get_creation_task('local', case.task.id).narration_cache_key is None
