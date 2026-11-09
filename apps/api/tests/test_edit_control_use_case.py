"""Shared proposal controls on isolated SQL and metadata-only compound drafts."""
from dataclasses import asdict, replace
import json
import subprocess
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.assistant.task_business import TaskBusiness
from app.assistant.task_business_adapter import TaskBusinessAdapter
from app.conversation_repository import ConversationRepository
from app.creation.editing.control_use_case import EditControlUseCase
from app.creation.editing.metadata_use_case import EditPreviewRejected
from app.creation_execution import CreationExecutionActor
from app.schemas import CompoundEditSelectionRequest, CreationTaskEditPreviewResponse, CreationTaskTimelineUndoRequest
from test_compound_application import compound_state, preview_command


@pytest.fixture
def state(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('proposal controls must not execute media or models')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    state = compound_state(tmp_path, workspace_id='local')
    state.actor = CreationExecutionActor('local', state.member.member_id)
    state.conversations = ConversationRepository(state.repo.database_path)
    state.wakes = []
    state.controls = EditControlUseCase(state.repo, conversations=state.conversations, compound=state.application,
        preview_response=lambda intent, proposal: CreationTaskEditPreviewResponse.model_validate(
            {'intent': asdict(intent), 'proposal': asdict(proposal)}), wake=lambda: state.wakes.append('discard'))
    command = replace(preview_command(state), workspace_id='local')
    _, state.proposal = state.application.preview(state.application.prepare_preview(command), draft_only=True, execution=None)
    return state


def facts(state):
    with state.repo._connect() as conn:
        return tuple(conn.iterdump())


def source(state, message):
    task = state.repo.task_records.get_creation_task('local', state.task.id)
    version = state.repo.timeline_records.get_timeline_version('local', task.current_timeline_version_id)
    chat = state.conversations.create('local', state.member.member_id, 'task', task.id)
    state.conversations.save_draft(chat['id'], 'local', state.member.member_id, message, 0)
    turn, _ = state.conversations.create_turn_idempotent(chat['id'], 'local', state.member.member_id, message,
        str(uuid4()), 1, request_context={'kind': 'task', 'task_id': task.id, 'stage': 'montage',
            'selected_object_refs': ['clip:clip'], 'target_snapshot': {'timeline_version_id': version.id,
                'timeline_sequence': version.sequence}})
    origin = {**state.conversations.task_turn_source(turn['id'], 'local', state.member.member_id, task.id),
        'assistant_turn_id': turn['id']}
    payload = CreationTaskTimelineUndoRequest(assistant_turn_id=turn['id'],
        expected_timeline_version_id=version.id, expected_timeline_sequence=version.sequence)
    return task, origin, payload


def test_control_read_and_partial_preview_keep_original_proposal_and_timeline(state):
    before = facts(state)
    jobs = state.repo.job_records.list_job_runs('local', state.task.id)
    read = state.controls.read(state.task, actor=state.actor)
    assert read['task_id'] == state.task.id and read['timeline_version_id'] == state.base.id
    assert [item['id'] for item in read['proposals']] == [state.proposal.id]
    assert facts(state) == before
    task, origin, _ = source(state, '只保留第1项和第3项')
    calls = []
    def guard(conn):
        assert conn.in_transaction
        calls.append(True)
    preview = state.controls.preview_selection(task, state.proposal.id,
        CompoundEditSelectionRequest(included_step_indices=[0, 2]), actor=state.actor, origin=origin, write_guard=guard)
    assert calls == [True]
    assert preview.proposal.id != state.proposal.id and preview.proposal.status == 'proposed'
    assert [step['enabled'] for step in preview.proposal.operation.payload['steps']] == [True, False, True]
    assert preview.intent.constraints['request_source']['raw_command'] == origin['raw_command']
    assert preview.intent.constraints['request_source']['member_id'] == state.actor.member_id
    assert state.repo.edit_records.get_edit_proposal('local', state.proposal.id) == state.proposal
    assert state.repo.task_records.get_creation_task('local', task.id).current_timeline_version_id == state.base.id
    assert not state.repo.edit_records.list_edit_operations('local', task.id)
    assert state.repo.job_records.list_job_runs('local', task.id) == jobs


def test_discard_is_source_bound_idempotent_and_never_changes_timeline(state):
    task, origin, payload = source(state, '取消这份修改方案')
    calls = []
    def guard(conn):
        assert conn.in_transaction
        calls.append(True)
    result = state.controls.discard(task, state.proposal.id, payload, actor=state.actor, origin=origin, write_guard=guard)
    assert result == {'task_id': task.id, 'proposal_id': state.proposal.id, 'status': 'rejected',
        'timeline_version_id': state.base.id}
    before = facts(state)
    assert state.controls.discard(task, state.proposal.id, payload,
        actor=state.actor, origin=origin, write_guard=guard) == result
    assert calls == [True, True] and facts(state) == before
    assert state.repo.task_records.get_creation_task('local', task.id).current_timeline_version_id == state.base.id
    assert not state.repo.edit_records.list_edit_operations('local', task.id)
    assert state.wakes == ['discard', 'discard']


def test_control_read_bounds_current_proposals_without_writing(state):
    for index in range(10):
        state.repo.edit_records.create_edit_proposal(replace(state.proposal, id=f'proposal-extra-{index}'))
    before = facts(state)
    read = state.controls.read(state.task, actor=state.actor)
    expected = state.repo.edit_records.list_edit_proposals('local', state.task.id,
        base_version_id=state.base.id, limit=8)
    assert len(read['proposals']) == 8
    assert [item['id'] for item in read['proposals']] == [item.id for item in expected]
    assert facts(state) == before


def test_selection_rechecks_actual_audio_source_in_saving_transaction(state):
    task, origin, _ = source(state, '只保留第1项和第3项')
    before = facts(state)
    calls = []
    def change_source(conn):
        assert conn.in_transaction
        calls.append(True)
        reference = state.plan.audio_timeline.tracks[0].clips[0].source_ref
        path = state.application.montage_edits.audio_service.source('local', task.id, reference, actor=state.actor)
        path.write_bytes(path.read_bytes() + b'changed-after-preparation')
    with pytest.raises(EditPreviewRejected):
        state.controls.preview_selection(task, state.proposal.id,
            CompoundEditSelectionRequest(included_step_indices=[0, 2]), actor=state.actor,
            origin=origin, write_guard=change_source)
    assert calls == [True] and facts(state) == before


@pytest.mark.parametrize('message', ['不要取消这份修改方案', '说明取消这份修改方案的效果', '取消制作作业', '只取消调色'])
def test_discard_rejects_non_authorizing_or_partial_source_before_writing(state, message):
    task, origin, payload = source(state, message)
    before = facts(state)
    with pytest.raises(EditPreviewRejected):
        state.controls.discard(task, state.proposal.id, payload, actor=state.actor, origin=origin)
    assert facts(state) == before and state.wakes == []


@pytest.mark.parametrize('operation', ['preview', 'discard'])
@pytest.mark.parametrize('change', ['stopped_source', 'owner', 'head', 'proposal'])
def test_control_writers_recheck_original_source_owner_and_head_and_rollback(state, operation, change):
    task, origin, payload = source(state, '取消这份修改方案' if operation == 'discard' else '只保留第1项和第3项')
    before = facts(state)
    calls = []
    def guard(conn):
        assert conn.in_transaction
        calls.append(True)
        if change == 'stopped_source':
            conn.execute("UPDATE assistant_conversation_turns SET status='stopped' WHERE id=?", (origin['assistant_turn_id'],))
        elif change == 'proposal':
            state.repo.edit_records.transition_edit_proposal('local', task.id, state.proposal.id,
                status='expired', connection=conn)
        else:
            current = state.repo.task_records.get_creation_task('local', task.id, connection=conn)
            if change == 'owner':
                current.owner_user_id = 'different-member'
            else:
                current.current_timeline_version_id = state.base.parent_version_id
            conn.execute('UPDATE creation_tasks SET payload=? WHERE id=?', (json.dumps(asdict(current), default=str), task.id))
    with pytest.raises(EditPreviewRejected):
        if operation == 'discard':
            state.controls.discard(task, state.proposal.id, payload, actor=state.actor, origin=origin, write_guard=guard)
        else:
            state.controls.preview_selection(task, state.proposal.id, CompoundEditSelectionRequest(included_step_indices=[0, 2]),
                actor=state.actor, origin=origin, write_guard=guard)
    assert calls == [True] and facts(state) == before


@pytest.mark.parametrize('operation', ['preview', 'discard'])
@pytest.mark.parametrize('changed', ['member', 'base'])
def test_original_proposal_member_and_base_are_not_rebound_by_the_control(state, operation, changed):
    task, origin, payload = source(state, '取消这份修改方案' if operation == 'discard' else '只保留第1项')
    original = state.repo.edit_records.get_edit_intent('local', state.proposal.intent_id)
    intent = replace(original, id='other-intent', constraints={**original.constraints,
        'request_source': {'member_id': 'another-member'}} if changed == 'member' else original.constraints)
    state.repo.edit_records.create_edit_intent(intent, source=None)
    data = state.proposal.operation['payload']
    proposal = replace(state.proposal, id='other-proposal', intent_id=intent.id,
        operation={**state.proposal.operation, 'payload': {**data,
            'base_version_id': state.base.parent_version_id if changed == 'base' else data['base_version_id']}})
    state.repo.edit_records.create_edit_proposal(proposal)
    before = facts(state)
    with pytest.raises(EditPreviewRejected) as denied:
        if operation == 'discard':
            state.controls.discard(task, proposal.id, payload, actor=state.actor, origin=origin)
        else:
            state.controls.preview_selection(task, proposal.id, CompoundEditSelectionRequest(included_step_indices=[0]),
                actor=state.actor, origin=origin)
    assert denied.value.status_code == (403 if changed == 'member' else 409)
    assert facts(state) == before


def test_shared_read_does_not_grant_discard_or_selection_to_another_member(state):
    reader = state.members.add_member('shared reader')
    state.repo.task_records.set_creation_task_visibility('local', state.task.id, 'shared')
    task, origin, payload = source(state, '取消这份修改方案')
    actor = CreationExecutionActor('local', reader.member_id)
    before = facts(state)
    assert state.controls.read(task, actor=actor)['proposals'][0]['id'] == state.proposal.id
    with pytest.raises(EditPreviewRejected) as denied:
        state.controls.discard(task, state.proposal.id, payload, actor=actor, origin=origin)
    assert denied.value.status_code == 403
    with pytest.raises(EditPreviewRejected) as denied:
        state.controls.preview_selection(task, state.proposal.id,
            CompoundEditSelectionRequest(included_step_indices=[0]), actor=actor)
    assert denied.value.status_code == 403 and facts(state) == before


def test_task_business_dispatches_actual_owner_and_preserves_actor_origin_and_commit_guard(state):
    task, origin, payload = source(state, '只保留第1项和第3项')
    business = object.__new__(TaskBusiness)
    business.edit_controls = state.controls
    checks = []
    # The existing goal/permit boundary is pinned here; the shared owner below
    # still resolves the real SQL turn, actor, proposal and saving transaction.
    def check(*args, connection=None, **kwargs):
        checks.append(connection is not None)
        return state.actor, task, {}, {key: value for key, value in origin.items() if key != 'assistant_turn_id'}, None
    business.check = check
    host = TaskBusinessAdapter(business)
    options = dict(call_id='call', tool_call_id='tool', source_binding={'turn_id': origin['assistant_turn_id']},
        cancel_event=Event(), deadline_monotonic=float('inf'))
    assert host('read_edit_control_context', {'task_id': task.id}, **options)['value'] == state.controls.read(task, actor=state.actor)
    rejected = host('preview_edit_selection', {'task_id': task.id, 'proposal_id': state.proposal.id,
        'payload': {'included_step_indices': [0, 2]}, 'draft_only': False}, **options)
    assert rejected['failure']['submission_effect'] == 'not_submitted'
    result = host('preview_edit_selection', {'task_id': task.id, 'proposal_id': state.proposal.id,
        'payload': {'included_step_indices': [0, 2]}, 'draft_only': True}, **options)
    assert result['ok'] and result['value']['proposal']['status'] == 'proposed'
    assert any(checks)
    foreign = host('discard_edit_proposal', {'task_id': 'other-task', 'proposal_id': state.proposal.id,
        'payload': payload.model_dump(mode='json')}, **options)
    assert foreign['failure']['status_code'] == 403
    task, origin, payload = source(state, '取消这份修改方案')
    options['source_binding'] = {'turn_id': origin['assistant_turn_id']}
    assert host('discard_edit_proposal', {'task_id': task.id, 'proposal_id': state.proposal.id,
        'payload': payload.model_dump(mode='json')}, **options)['value']['status'] == 'rejected'


def test_http_and_host_share_control_owner_and_keep_shared_read_and_write_access(state, monkeypatch):
    from app import main
    monkeypatch.setattr(main, 'production_repo', state.repo)
    monkeypatch.setattr(main, 'conversation_repo', state.conversations)
    monkeypatch.setattr(main, 'local_members_service', state.members)
    monkeypatch.setattr(main, '_edit_control_use_case', lambda: state.controls)
    client = TestClient(main.app, headers={'X-Local-Member-Token': state.token})
    root = f'/api/creation-tasks/{state.task.id}'
    assert client.get(root + '/edit-controls').json() == state.controls.read(state.task, actor=state.actor)
    selected = client.post(root + f'/edits/{state.proposal.id}/selection/preview?draft=true',
        json={'included_step_indices': [0, 2]})
    assert selected.status_code == 200, selected.text
    assert [step['enabled'] for step in selected.json()['proposal']['operation']['payload']['steps']] == [True, False, True]
    task, _origin, payload = source(state, '取消这份修改方案')
    result = client.post(root + f'/edits/{state.proposal.id}/reject', json=payload.model_dump(mode='json'))
    assert result.status_code == 200 and result.json()['status'] == 'rejected', result.text
    assert state.repo.task_records.get_creation_task('local', task.id).current_timeline_version_id == state.base.id
