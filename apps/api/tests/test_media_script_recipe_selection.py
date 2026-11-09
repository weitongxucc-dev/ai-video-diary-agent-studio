"""Explicit recipe selection grants code provenance, never the producer's inputs."""
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.creation.contracts import TaskAuthorizationConflict, TimelineVersion
from app.creation.task_authorization import TaskAuthorizationUseCase
from app.creation_execution import CreationExecutionActor
from app.media_script_tools import MediaScriptTools, ScriptToolConflict
from test_media_script_tools import case as recipe_case, create as create_recipe  # noqa: F401


@pytest.fixture
def selection(recipe_case):  # noqa: F811 (shared metadata-only fixture)
    repo, store, members, actor, tools, producer = recipe_case
    recipe = create_recipe(recipe_case)
    original = repo.task_records.get_creation_task('local', 'task')
    plan = repo.plan_records.get_creation_plan('local', original.plan_id)
    task = replace(original, id='new-task', plan_id='new-plan')
    repo.plan_confirmation.confirm_creation_plan(replace(plan, id=task.plan_id, creation_task_id=task.id),
        task, verified_owner_user_id=actor.member_id)
    authorization = TaskAuthorizationUseCase(repo, lambda identity: tools().verify_identity(
        CreationExecutionActor.from_event(identity['workspace_id'], identity)))
    authorization.ensure(task, actor)
    inputs = {'request_id': str(uuid4()), 'tool_id': recipe['tool_id'], 'tool_version': recipe['version'],
        'expected_timeline_version_id': None, 'expected_timeline_sequence': 0,
        'parameters': {'label': 'selected'}, 'input_refs': [{'name': 'frame.png', 'source_ref': 'new-task.png',
            'expected_artifact_id': 'new-input', 'expected_sha256': 'a' * 64}]}
    event_id, fresh = repo.execution_records.claim_edit_request(task, actor.member_id,
        inputs['request_id'], 'script', inputs)
    assert fresh
    source = {key: inputs[key] for key in ('request_id', 'expected_timeline_version_id', 'expected_timeline_sequence')}
    source.update(workspace_id='local', task_id=task.id, member_id=actor.member_id,
        actor_identity=actor.identity_payload())
    reference = {key: recipe[key] for key in ('tool_id', 'version', 'definition_ref', 'code_sha256')}
    scoped = MediaScriptTools(repo, store, verify_identity=tools().verify_identity,
        authorize_task_write=tools().authorize_task_write, task_id=task.id, task_authorizations=authorization)
    return SimpleNamespace(repo=repo, store=store, members=members, actor=actor, tools=tools,
        producer=producer, recipe=recipe, task=task, authorization=authorization, inputs=inputs,
        source=source, reference=reference, scoped=scoped, event_id=event_id)


def prepare(state):
    definition, current = state.tools().prepare_task_selection(state.actor, state.recipe['tool_id'],
        version=state.recipe['version'], expected_reference=state.reference)
    return state.authorization.prepare_recipe_selection(state.task, state.actor, definition,
        source=state.source, request_inputs=state.inputs, recipe_current=current)


def test_explicit_selection_keeps_only_code_scope_and_commit_uses_sql(selection, monkeypatch):
    state = selection
    records = state.repo.task_authorization_records
    before = records.get_task_authorization('local', state.task.id)
    jobs = state.repo.job_records.list_job_runs('local', state.task.id)
    with pytest.raises(PermissionError, match='配方来源'):
        state.scoped.get(state.actor, state.recipe['tool_id'], version=1)
    commit = prepare(state)

    def forbidden(*args, **kwargs):
        raise AssertionError('selection commit must not open connections, files or remote identity')

    with state.repo._connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        with monkeypatch.context() as patch:
            patch.setattr(state.repo, '_connect', forbidden)
            patch.setattr(MediaScriptTools, '_blob', forbidden)
            patch.setattr(state.authorization, 'verify_identity', forbidden)
            commit(connection)
    after = records.get_task_authorization('local', state.task.id)
    assert after.resource_scope == {**before.resource_scope,
        'references': sorted(set(before.resource_scope['references']) | {state.recipe['definition_ref']})}
    assert after.revision == before.revision + 1
    assert state.scoped.get(state.actor, state.recipe['tool_id'], version=1)['code_sha256'] == state.recipe['code_sha256']
    with pytest.raises(PermissionError, match='浏览'):
        state.scoped.list(state.actor)
    with pytest.raises(PermissionError, match='扩大'):
        state.scoped.prepare_task_selection(state.actor, state.recipe['tool_id'], version=1,
            expected_reference=state.reference)
    assert state.authorization.verdict(state.actor, state.task.id, 'task_media',
        references=['outputs/1.png']) == 'unauthorized'
    assert state.repo.task_records.get_creation_task('local', state.task.id).current_timeline_version_id is None
    assert state.repo.job_records.list_job_runs('local', state.task.id) == jobs
    assert state.repo.execution_records.read_edit_request_result(state.task, state.event_id) is None
    # Rebinding the same explicit selection does not manufacture another grant.
    repeat = prepare(state)
    with state.repo._connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        repeat(connection)
    assert records.get_task_authorization('local', state.task.id) == after


@pytest.mark.parametrize('mutation', ['recipe_revoke', 'definition', 'request', 'timeline', 'scope', 'goal', 'expiry'])
def test_selection_refuses_changes_before_scope_commit(selection, mutation):
    state = selection
    commit = prepare(state)
    records = state.repo.task_authorization_records
    before = records.get_task_authorization('local', state.task.id)
    if mutation == 'recipe_revoke':
        state.tools().revoke(state.actor, state.recipe['tool_id'], expected_version=1)
    elif mutation == 'definition':
        with state.repo._connect() as connection:
            connection.execute('UPDATE artifact_records SET sha256=? WHERE id=?', ('b' * 64, state.recipe['definition_artifact_id']))
    elif mutation == 'request':
        with state.repo._connect() as connection:
            connection.execute("UPDATE task_events SET payload_json=json_set(payload_json,'$.inputs.tool_version',2) WHERE id=?",
                (state.event_id,))
    elif mutation == 'timeline':
        state.repo.timeline_records.create_timeline_version(TimelineVersion('new-version', 'local', state.task.id, None, 1, {}, None))
    elif mutation == 'scope':
        records.save_task_authorization(replace(before, revision=before.revision + 1), expected_revision=before.revision)
    elif mutation == 'goal':
        plan = state.repo.plan_records.get_creation_plan('local', state.task.plan_id)
        with state.repo._connect() as connection:
            connection.execute('UPDATE creation_plans SET payload=? WHERE id=?',
                (json.dumps({**vars(plan), 'duration_target_seconds': 20}, default=str), plan.id))
    else:
        state.authorization.clock = lambda: before.expires_at + timedelta(seconds=1)
    with pytest.raises((PermissionError, TaskAuthorizationConflict)):
        with state.repo._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            commit(connection)
    assert state.recipe['definition_ref'] not in records.get_task_authorization('local', state.task.id).resource_scope['references']


def test_selection_refuses_a_different_recipe_version_or_origin(selection):
    state = selection
    with pytest.raises(ScriptToolConflict):
        state.tools().prepare_task_selection(state.actor, state.recipe['tool_id'], version=1,
            expected_reference={**state.reference, 'code_sha256': 'b' * 64})
    definition, current = state.tools().prepare_task_selection(state.actor, state.recipe['tool_id'],
        version=1, expected_reference=state.reference)
    with pytest.raises(PermissionError, match='操作者'):
        state.authorization.prepare_recipe_selection(state.task, state.actor, definition,
            source={**state.source, 'actor_identity': {'member_id': 'another-member'}},
            request_inputs=state.inputs, recipe_current=current)
