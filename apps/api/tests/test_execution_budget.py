from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import threading

import pytest

from app.creation.contracts import JobRun, TaskEvent
from app.creation.execution_budget_scope import BudgetScopeConflict, BudgetScopeExhausted, ExecutionBudgetScopes
from app.creation_execution import CreationExecutionActor
from test_creation_execution import _seed


@pytest.fixture
def scope_case(tmp_path):
    repo, first, _, _ = _seed(tmp_path, workspace_id='local', task_suffix='_first')
    _, second, _, _ = _seed(tmp_path, workspace_id='local', task_suffix='_second')
    actor = CreationExecutionActor('local', 'scope-owner')
    with repo._connect() as conn:
        for task_id in (first, second):
            task = repo.get_creation_task('local', task_id)
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
