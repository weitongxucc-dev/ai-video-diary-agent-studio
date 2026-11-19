from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
import threading
from uuid import uuid4

import pytest

from app.creation.contracts import JobRun
from app.creation.execution_budget_scope import BudgetScopeConflict, BudgetScopeExhausted, ExecutionBudgetScopes
from app.creation.task_authorization import TaskAuthorizationUseCase
from app.creation.task_budget_use_case import TaskBudgetUseCase
from app.creation_execution import CreationExecutionActor, revision_digest, submit_creation_stage
from test_creation_execution import _narration_units_case, _seed


@pytest.fixture
def scope_case(tmp_path):
    repo, first, _, _ = _seed(tmp_path, workspace_id='local', task_suffix='_first')
    _, second, _, _ = _seed(tmp_path, workspace_id='local', task_suffix='_second')
    actor = CreationExecutionActor('local', 'scope-owner')
    with repo._connect() as conn:
        for task_id in (first, second):
            task = repo.task_records.get_creation_task('local', task_id)
            task.owner_user_id = actor.member_id
            conn.execute('UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?',
                (json.dumps(asdict(task), default=str), 'local', task_id))
    jobs = [repo.job_records.create_job_run(JobRun(id=f'job_scope_{i}', workspace_id='local', task_id=task_id,
        kind='creation_task_execution', idempotency_key=f'scope-test-{i}', input_digest=str(i) * 64))
        for i, task_id in enumerate((first, second), 1)]
    command = dict(scope_id='shared-goal', expected_revision=-1, request_id='approved-original',
        source_ref='isolated-confirmation:original', target_task_ids=[first, second],
        limits={'execution_seconds': 10, 'generated_bytes': 100}, policy_kind='explicit_total',
        authorization_guard=lambda conn: None)
    repo.authorize_execution_budget_scope('local', first, actor, **command)
    for job in jobs:
        repo.bind_execution_budget_scope('local', job.id, actor, scope_id='shared-goal', expected_revision=0)
    return repo, actor, first, jobs, command


def reserve(repo, actor, job, request, *, revision=0, maximum=None, fail=False):
    scopes = ExecutionBudgetScopes(repo)
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        started_id = f'event_{job.id}_narration_started_{request}'
        receipt_id = f'event_{job.id}_narration_settled_{request}'
        scopes._append(conn, started_id, 'local', job.task_id, job.id, 'creation_narration_unit_started',
            {'usage_id': f'{request}:1'})
        result = scopes.reserve(conn, 'local', job.id, actor, request_id=request, permit_generation=1,
            started_event_id=started_id, receipt_id=receipt_id, authorization_revision=revision,
            maximum=maximum or {'execution_seconds': 6, 'generated_bytes': 60})
        if fail:
            raise RuntimeError('isolated commit failure')
        return result


def settle(repo, job, request, seconds, size):
    scopes = ExecutionBudgetScopes(repo)
    with repo._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        receipt_id = f'event_{job.id}_narration_settled_{request}'
        scopes._append(conn, receipt_id, 'local', job.task_id, job.id, 'creation_narration_unit_settled',
            {'usage_id': f'{request}:1', 'elapsed_seconds': seconds, 'output_bytes': size})
        return scopes.settle(conn, 'local', job.id, request_id=request, permit_generation=1, receipt_id=receipt_id)


@pytest.fixture
def production_scope_start(tmp_path, request):
    import app.main as main

    case = _narration_units_case(tmp_path, start_job=False, **getattr(request, 'param', {}))
    repo = case.repo
    task = repo.task_records.get_creation_task('local', case.task_id)
    authority = TaskAuthorizationUseCase(repo, verify_identity=lambda _: 'ready')
    grant = authority.ensure(task, case.actor, interactive=True)
    assert authority.verdict(case.actor, task.id, 'narration') == 'ready'
    case.execution = main._new_creation_execution(repo)
    case.grant = grant
    try:
        yield case
    finally:
        case.scheduler.close()


def start(case, *, execution=None, revised=False):
    repo = case.repo
    task = repo.task_records.get_creation_task('local', case.task_id)
    plan = repo.plan_records.get_creation_plan('local', task.plan_id)
    if revised:
        plan = replace(plan, name='Same authorized work, revised title')
        with repo._connect() as conn:
            repo.plan_records.write_plan_payload(conn, 'local', plan)
    script = repo.script_records.get_creation_task_script('local', case.script_id)
    return (execution or case.execution).start('local', task.id, script.id, revision_digest(script, plan), actor=case.actor)


def test_new_production_jobs_share_a_task_scope_and_record_actual_usage(production_scope_start):
    case = production_scope_start
    first = start(case)
    assert start(case).id == first.id
    reserve(case.repo, case.actor, first, 'first-production-leaf')
    settle(case.repo, first, 'first-production-leaf', 3, 25)
    next_job = start(case, revised=True)
    assert next_job.id != first.id
    with case.repo._connect() as conn:
        scopes = ExecutionBudgetScopes(case.repo)
        first_binding = scopes.binding(conn, 'local', first.id)
        next_binding = scopes.binding(conn, 'local', next_job.id)
    assert first_binding.payload['scope_id'] == next_binding.payload['scope_id']
    assert first_binding.payload['input_digest'] != next_binding.payload['input_digest']
    scope, = case.repo.execution_budget_scopes_for_task('local', case.task_id, case.actor)
    assert scope['authorization']['source_ref'] == f'event_{case.grant.id}_{case.grant.revision}'
    assert scope['usage']['execution_seconds'] == 3 and scope['usage']['generated_bytes'] == 25


def test_existing_explicit_cap_and_revocation_take_precedence_over_local_default(production_scope_start):
    case = production_scope_start
    repo = case.repo
    command = dict(scope_id='approved-total', source_ref='isolated-user-authorization',
        target_task_ids=[case.task_id], limits={'execution_seconds': 5, 'generated_bytes': 80},
        policy_kind='explicit_total', authorization_guard=lambda conn: None)
    repo.authorize_execution_budget_scope('local', case.task_id, case.actor, **command,
        expected_revision=-1, request_id='explicit-original')
    job = start(case)
    scoped, = repo.execution_budget_scopes_for_task('local', case.task_id, case.actor)
    assert scoped['authorization']['scope_id'] == 'approved-total'
    with pytest.raises(BudgetScopeExhausted):
        reserve(repo, case.actor, job, 'over-explicit-limit')
    repo.authorize_execution_budget_scope('local', case.task_id, case.actor, **command,
        expected_revision=0, request_id='explicit-revoked', revoked=True)
    assert start(case).id == job.id
    with pytest.raises(PermissionError, match='累计授权已撤销'):
        start(case, revised=True)
    assert [item.id for item in case.execution.jobs('local', case.task_id)] == [job.id]


def test_legacy_job_and_account_identity_do_not_get_local_default(production_scope_start):
    import app.main as main

    case = production_scope_start
    old_execution = main._new_creation_execution(case.repo)
    old_execution.budget_policy_for = None
    original = start(case, execution=old_execution)
    record = case.repo.execution_records.get_task_event('local', f'event_{original.id}_start')
    assert start(case).id == original.id
    assert case.repo.execution_records.get_task_event('local', record.id) == record
    new_input = start(case, revised=True)
    assert 'execution_budget_policy' not in case.repo.execution_records.get_task_event(
        'local', f'event_{new_input.id}_start').payload
    assert case.repo.execution_budget_scopes_for_task('local', case.task_id, case.actor) == []
    remote = CreationExecutionActor.account(*(str(uuid4()) for _ in range(3)))
    assert TaskBudgetUseCase(case.repo).local_start_policy(
        remote.workspace_id, case.task_id, remote, original.input_digest, operation='creation') is None


def test_scope_authorization_start_and_binding_roll_back_together(production_scope_start, monkeypatch):
    case = production_scope_start
    original = case.repo.execution_records.append_task_event

    def interrupted(event, *, connection=None):
        result = original(event, connection=connection)
        if event.event_type == 'execution_budget_scope_bound':
            raise RuntimeError('isolated transaction interruption')
        return result

    with monkeypatch.context() as patch:
        patch.setattr(case.repo.execution_records, 'append_task_event', interrupted)
        with pytest.raises(RuntimeError, match='isolated transaction interruption'):
            start(case)
    assert case.execution.jobs('local', case.task_id) == []
    assert not any(event.event_type.startswith('execution_budget_scope_') or event.event_type == 'creation_execution_started'
                   for event in case.repo.execution_records.list_task_events('local', case.task_id))
    assert start(case).id


def test_task_authorization_is_rechecked_after_policy_read_before_binding(production_scope_start, monkeypatch):
    from datetime import datetime, timezone

    case = production_scope_start
    original = case.execution.budget_policy_for

    def revoke_after_selection(*args, **kwargs):
        policy = original(*args, **kwargs)
        assert policy['policy_kind'] == 'scope'
        case.repo.task_authorization_records.save_task_authorization(
            replace(case.grant, revision=case.grant.revision + 1, revoked_at=datetime.now(timezone.utc)),
            expected_revision=case.grant.revision)
        return policy

    monkeypatch.setattr(case.execution, 'budget_policy_for', revoke_after_selection)
    with pytest.raises(PermissionError, match='需要当前有效的原任务授权'):
        start(case)
    assert not case.execution.jobs('local', case.task_id)
    assert not case.repo.execution_budget_scopes_for_task('local', case.task_id, case.actor)


@pytest.mark.parametrize('production_scope_start', [
    {'section_count': 20, 'audio_input_count': 5, 'seconds': 2, 'duration_seconds': 40},
], indirect=True)
def test_production_scope_crosses_old_byte_cap_with_real_pcm_and_finite_units(production_scope_start):
    case = production_scope_start
    case.owner.narration_audio_budget = replace(case.owner.narration_audio_budget,
        max_source_duration_seconds=60, max_output_duration_seconds=60)
    job = start(case)
    submit_creation_stage(case.execution, case.scheduler, job, case.actor)
    for _ in range(40):
        if case.repo.task_records.get_creation_task('local', case.task_id).narration_cache_key:
            break
        grant = case.scheduler.pick_and_grant()
        assert grant is not None and grant.unit_key.startswith('narration:')
        unit = case.scheduler._get(grant.request_id).resources
        assert 0 < unit['timeout_seconds'] <= 300 and 0 < unit['max_output_bytes'] <= 3 * 1024**2
        assert unit['parent_timeout_seconds'] is None and unit['parent_max_output_bytes'] is None
        case.executor.execute(grant, case.scheduler)
    task = case.repo.task_records.get_creation_task('local', case.task_id)
    assert case.repo.narration_artifacts.get_narration_artifact('local', task.narration_cache_key).duration_seconds == 40
    scope, = case.repo.execution_budget_scopes_for_task('local', case.task_id, case.actor)
    assert scope['usage']['generated_bytes'] > 3 * 1024**2
    assert scope['usage']['reserved_bytes'] == 0 and scope['usage']['unknown_unit_count'] == 0
    assert len(case.generated) == 20


def test_two_jobs_atomically_reserve_one_shared_remainder(scope_case):
    repo, actor, _, jobs, _ = scope_case
    barrier = threading.Barrier(2)
    def claim(i):
        barrier.wait()
        try:
            return reserve(repo, actor, jobs[i], f'competing-{i}').id
        except BudgetScopeExhausted:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (0, 1)))
    assert sum(result is not None for result in results) == 1
    usage = repo.execution_budget_scope_usage('local', 'shared-goal')['usage']
    assert usage['reserved_seconds'] == 6 and usage['reserved_bytes'] == 60
    with repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE event_type='creation_narration_unit_started'").fetchone()[0] == 1


def test_unknown_survives_revision_and_late_receipt_is_counted_once(scope_case):
    repo, actor, origin, jobs, command = scope_case
    first = reserve(repo, actor, jobs[0], 'unknown')
    pending = repo.execution_budget_scope_usage('local', 'shared-goal')['usage']
    assert pending['reserved_seconds'] == 6 and pending['reserved_bytes'] == 60
    measured = settle(repo, jobs[0], 'unknown', 2, None)
    assert measured['execution_seconds'] == 2 and measured['reserved_seconds'] == 0
    assert measured['generated_bytes'] == 0 and measured['reserved_bytes'] == 60
    updated = {**command, 'expected_revision': 0, 'request_id': 'more-authority',
        'limits': {'execution_seconds': 12, 'generated_bytes': 120}}
    event = repo.authorize_execution_budget_scope('local', origin, actor, **updated)
    assert repo.authorize_execution_budget_scope('local', origin, actor, **updated) == event
    assert reserve(repo, actor, jobs[0], 'unknown').id == first.id
    second = reserve(repo, actor, jobs[1], 'second', revision=1)
    revoked = {**updated, 'expected_revision': 1, 'request_id': 'revoke', 'revoked': True}
    repo.authorize_execution_budget_scope('local', origin, actor, **revoked)
    with pytest.raises(PermissionError):
        reserve(repo, actor, jobs[0], 'after-revoke', revision=2)
    measured = settle(repo, jobs[1], 'second', 3, 30)
    assert measured['execution_seconds'] == 5 and measured['generated_bytes'] == 30
    assert measured['reserved_seconds'] == 0 and measured['reserved_bytes'] == 60
    assert settle(repo, jobs[1], 'second', 3, 30) == measured
    assert repo.execution_records.get_task_event('local', second.id).payload['revision'] == 1
    with pytest.raises(BudgetScopeConflict):
        settle(repo, jobs[1], 'second', 4, 30)


def test_failed_transaction_has_neither_started_nor_reservation(scope_case):
    repo, actor, _, jobs, _ = scope_case
    with pytest.raises(RuntimeError, match='isolated commit failure'):
        reserve(repo, actor, jobs[0], 'rollback', fail=True)
    assert repo.execution_budget_scope_usage('local', 'shared-goal')['usage']['unit_count'] == 0
    with repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_events WHERE event_type='creation_narration_unit_started'").fetchone()[0] == 0


def test_scope_identity_input_revision_and_finite_unit_are_not_self_authorized(scope_case):
    repo, actor, origin, jobs, command = scope_case
    with pytest.raises(PermissionError):
        reserve(repo, CreationExecutionActor('local', 'other-member'), jobs[0], 'wrong-actor')
    with pytest.raises(BudgetScopeConflict):
        reserve(repo, actor, jobs[0], 'stale-revision', revision=3)
    with pytest.raises(ValueError, match='单元最大占用须有限且为正'):
        reserve(repo, actor, jobs[0], 'infinite', maximum={'execution_seconds': float('inf'), 'generated_bytes': 1})
    with pytest.raises(PermissionError):
        repo.authorize_execution_budget_scope('local', origin, actor, **{**command, 'authorization_guard': None})
    with pytest.raises(ValueError, match='至少需要一项'):
        repo.authorize_execution_budget_scope('local', origin, actor, **{**command,
            'limits': {'execution_seconds': None, 'generated_bytes': None}})
    with pytest.raises(ValueError, match='币种'):
        repo.authorize_execution_budget_scope('local', origin, actor, **{**command, 'limits': {'USD': 10}})
    with repo._connect() as conn:
        conn.execute('UPDATE job_runs SET input_digest=? WHERE id=?', ('new-input', jobs[0].id))
    with pytest.raises(PermissionError, match='输入已变化'):
        reserve(repo, actor, jobs[0], 'changed-input')


def test_no_total_limit_still_requires_finite_units_and_records_actuals(scope_case):
    repo, actor, origin, jobs, command = scope_case
    repo.authorize_execution_budget_scope('local', origin, actor, **{**command,
        'expected_revision': 0, 'request_id': 'local-goal', 'policy_kind': 'local_goal',
        'limits': {'execution_seconds': None, 'generated_bytes': None}})
    reserve(repo, actor, jobs[0], 'local-one', revision=1)
    measured = settle(repo, jobs[0], 'local-one', 8, 90)
    assert measured['execution_seconds'] == 8 and measured['generated_bytes'] == 90
    assert measured['reserved_seconds'] == 0 and measured['reserved_bytes'] == 0
    reserve(repo, actor, jobs[1], 'local-two', revision=1)
    status = repo.execution_budget_scope_usage('local', 'shared-goal')
    assert status['authorization']['limits'] == {'execution_seconds': None, 'generated_bytes': None}
    assert status['usage']['reserved_seconds'] == 6 and status['usage']['execution_seconds'] == 8


def test_task_budget_read_discovers_bound_scope_and_distinguishes_unset_total(scope_case):
    from app.creation.task_budget_use_case import TaskBudgetUseCase
    repo, actor, origin, jobs, command = scope_case
    service = TaskBudgetUseCase(repo)
    reserve(repo, actor, jobs[1], 'other-job-pending')
    response = service.read('local', origin, actor)
    assert response['resources']['receipt_count'] == 0
    assert response['authorization_coverage'] == 'tts_pcm_only'
    assert len(response['scopes']) == 1
    scope = response['scopes'][0]
    assert scope['remaining'] == {'execution_seconds': 4, 'generated_bytes': 40}
    assert scope['usage']['unknown_unit_count'] == 1 and scope['status'] == 'usage_unknown'
    assert response['status'] == 'authorization_scope_usage_unknown'
    repo.authorize_execution_budget_scope('local', origin, actor, **{**command,
        'expected_revision': 0, 'request_id': 'explicit-local-goal', 'policy_kind': 'local_goal',
        'limits': {'execution_seconds': None, 'generated_bytes': None}})
    scope = service.read('local', origin, actor)['scopes'][0]
    assert scope['remaining'] == {'execution_seconds': None, 'generated_bytes': None}
    assert scope['usage']['unknown_unit_count'] == 1
    with pytest.raises(PermissionError):
        service.read('local', origin, CreationExecutionActor('local', 'different-reader'))
    repo.authorize_execution_budget_scope('local', origin, actor, **{**command,
        'expected_revision': 1, 'request_id': 'remove-second-target', 'target_task_ids': [origin]})
    with pytest.raises(PermissionError, match='目标范围'):
        service.read('local', jobs[1].task_id, actor)


def test_task_budget_without_scope_is_not_reported_as_goal_authorized(scope_case):
    from app.creation.task_budget_use_case import TaskBudgetUseCase
    repo, actor, origin, jobs, _ = scope_case
    with repo._connect() as conn:
        conn.execute("DELETE FROM task_events WHERE event_type='execution_budget_scope_bound'")
    response = TaskBudgetUseCase(repo).read('local', origin, actor)
    assert response['scopes'] == []
    assert response['authorization_coverage'] == 'scope_unbound'
    assert response['status'] == 'authorization_scope_unbound'
    assert 'resources' in response and 'actual_cost' in response and 'in_flight' in response


def test_task_budget_mixed_scopes_do_not_claim_whole_task_exhausted(scope_case):
    from app.creation.task_budget_use_case import TaskBudgetUseCase
    repo, actor, origin, jobs, command = scope_case
    reserve(repo, actor, jobs[0], 'old-scope-work')
    settle(repo, jobs[0], 'old-scope-work', 10, 100)
    repo.authorize_execution_budget_scope('local', origin, actor, **{**command,
        'scope_id': 'additional-goal', 'request_id': 'additional-authorization'})
    job = repo.job_records.create_job_run(JobRun(id='job_new_scope', workspace_id='local', task_id=origin,
        kind='creation_task_execution', idempotency_key='new-scope-job', input_digest='c' * 64))
    repo.bind_execution_budget_scope('local', job.id, actor, scope_id='additional-goal', expected_revision=0)
    response = TaskBudgetUseCase(repo).read('local', origin, actor)
    assert response['status'] == 'authorization_scope_mixed'
    scopes = {item['authorization']['scope_id']: item for item in response['scopes']}
    assert scopes['shared-goal']['status'] == 'exhausted'
    assert scopes['shared-goal']['remaining'] == {'execution_seconds': 0, 'generated_bytes': 0}
    assert scopes['additional-goal']['status'] == 'available'
    assert scopes['additional-goal']['remaining'] == {'execution_seconds': 10, 'generated_bytes': 100}
    assert scopes['shared-goal']['usage']['generated_bytes'] == 100
