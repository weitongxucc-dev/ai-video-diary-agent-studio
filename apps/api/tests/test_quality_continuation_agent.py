"""Original QC continuation through the real task host, isolated SQL/declared worker only."""
from dataclasses import replace
from threading import Event
from uuid import uuid4

import pytest
import app.main as main
from app.assistant.task_business_adapter import TaskBusinessAdapter
from app.conversation_repository import ConversationRepository
from app.creation.task_authorization import TaskAuthorizationUseCase
from app.creation_execution import CreationExecutionActor
from app.creation.contracts import TimelineVersion
from test_quality_inspection_api import system as system, seed_render  # noqa: F401
from test_quality_resource_owner import metadata_result


@pytest.fixture
def failed_quality(system, monkeypatch):
    client, repo, _, checks, _, _, _ = system
    # This metadata-only artifact has no material selection; do not invent
    # asset grants for the unrelated montage fixture's placeholder clips.
    import test_quality_inspection_api as fixture_module
    original_montage = fixture_module.montage
    monkeypatch.setattr(fixture_module, 'montage', lambda ref: replace(original_montage(ref), clips=[]))
    seed_render(system)
    runtime, service, actor = client.quality_runtime, client.quality_service, client.quality_actor
    def worker(context, request, root):
        if request['phase'] == 'black_frames':
            raise RuntimeError('declared phase failure')
        return metadata_result(request)
    runtime.registrations['quality_check'].executor.worker = worker
    check_id = service.start(runtime, actor, 'local', 'task', force=True)
    job_id = f'job_quality_{check_id}'
    assert runtime.run_once() and runtime.run_once()
    with repo._connect() as conn:
        failed = dict(conn.execute("SELECT * FROM device_work_requests WHERE owner_id=? AND state='failed'", (job_id,)).fetchone())
    service.current_policy = lambda: {'budget': service.budget, 'profile': {
        'profile_id': 'isolated', 'revision': 2, 'digest': 'a' * 64, 'source_kind': 'device_profile'}}
    starts = []
    monkeypatch.setattr(runtime, 'start', lambda: starts.append('start'))
    conversations = ConversationRepository(repo.database_path)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    member = actor.member_id
    chat = conversations.create('local', member, 'task', 'task')
    chat = conversations.save_draft(chat['id'], 'local', member, '继续原质检，保留失败前缀', 0)
    snapshot = {'timeline_version_id': 'v1', 'timeline_sequence': 1}
    context = {'kind': 'task', 'task_id': 'task', 'stage': 'review', 'selected_object_refs': [], 'target_snapshot': snapshot}
    turn, _ = conversations.create_turn_idempotent(chat['id'], 'local', member, chat['draft_text'], str(uuid4()),
        chat['draft_version'], request_context=context)
    conversations.finish_turn(turn['id'], 'completed', '原检查已有回执', result={'kind': 'task_turn',
        'response': {'media_job': {'job_id': job_id, 'status': 'queued'}}})
    goal = conversations.register_task_goal(turn['id'])
    authorizations = TaskAuthorizationUseCase(repo, lambda _: 'ready')
    conversations.task_goal_input_authorized = authorizations.adopted_goal_input_valid
    authorizations.bind_business_context(repo.task_records.get_creation_task('local', 'task'), actor, goal, memory={})
    conversations.project_business_result(turn['id'], 'local', member,
        {'type': 'business_result', 'event_id': str(uuid4()), 'task_id': 'task', 'status': 'succeeded',
         'phase': 'preview', 'result_kind': 'graphic_source'}, response_patch={}, message='声明式回合触发')
    claim = conversations.claim_goal_event(conversations.goal_event_candidates()[0], snapshot, max_rounds=6)
    goal = claim['goal']
    monkeypatch.setattr(main, '_task_authorizations_for_goal', lambda repository=None: authorizations)
    business = main._task_business()
    cancel = Event()
    options = dict(call_id='call', tool_call_id='tool', cancel_event=cancel, deadline_monotonic=float('inf'),
        source_binding={'workspace_id': 'local', 'member_id': member, 'task_id': 'task', 'conversation_id': chat['id'],
            'turn_id': claim['turn_id'], 'goal_revision': str(goal.get('revision', 0)),
            'input_id': goal.get('source_input_id'), 'input_turn_id': goal.get('source_input_turn_id'), 'task_version': snapshot})
    return client, repo, checks, service, actor, runtime, failed, starts, business, TaskBusinessAdapter(business), options


def test_agent_continuation_keeps_original_job_and_replays_before_new_head_or_runtime(failed_quality, monkeypatch):
    client, repo, checks, service, actor, runtime, failed, starts, business, host, options = failed_quality
    pair = {'job_id': failed['owner_id'], 'failed_request_id': failed['id']}
    original = checks.get('local', 'task')
    read = host('read_task_quality_check', {}, **options)
    assert read['ok'] and read['value']['continuation']['can_request']
    unknown = host('read_task_quality_continuation', pair, **options)
    assert unknown['ok'] and unknown['value']['status'] == 'unknown' and starts == []
    result = host('continue_task_quality_check', {'payload': pair}, **options)
    assert result['ok'], result
    receipt = result['value']
    assert receipt['job_id'] == pair['job_id'] and receipt['check_id'] == original['check_id']
    assert receipt['request_id'] != failed['id']
    assert checks.get('local', 'task')['evidence']['_phases'] == original['evidence']['_phases']
    repo.timeline_records.create_timeline_version(TimelineVersion('v2', 'local', 'task', 'v1', 2, {}, None))
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, '_quality_inspection_use_case', lambda **kwargs: service)
    monkeypatch.setattr(main, '_bind_quality_runtime', lambda: pytest.fail('original receipt cannot bind runtime'))
    count = len(starts)
    assert host('read_task_quality_continuation', pair, **options)['value']['receipt'] == receipt
    assert host('continue_task_quality_check', {'payload': pair}, **options) == {'ok': True, 'value': receipt}
    assert len(starts) == count
    with repo._connect() as conn:
        assert dict(conn.execute('SELECT * FROM device_work_requests WHERE id=?', (failed['id'],)).fetchone()) == failed
        assert conn.execute("SELECT count(*) FROM job_runs WHERE kind='quality_check'").fetchone()[0] == 1


def test_agent_current_guard_rolls_back_original_continuation_in_owner_transaction(failed_quality, monkeypatch):
    _, repo, checks, service, actor, runtime, failed, starts, business, host, options = failed_quality
    pair = {'job_id': failed['owner_id'], 'failed_request_id': failed['id']}
    before_job = repo.job_records.get_job_run('local', pair['job_id'])
    before_check = checks.get('local', 'task')
    reconcile = runtime.scheduler.reconcile_released
    def released():
        result = reconcile()
        options['cancel_event'].set()
        return result
    monkeypatch.setattr(runtime.scheduler, 'reconcile_released', released)
    result = host('continue_task_quality_check', {'payload': pair}, **options)
    assert not result['ok'] and result['failure']['status_code'] == 409
    assert repo.job_records.get_job_run('local', pair['job_id']) == before_job
    assert checks.get('local', 'task') == before_check and starts == []
    with repo._connect() as conn:
        assert conn.execute('SELECT count(*) FROM device_work_requests WHERE owner_id=?', (pair['job_id'],)).fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM device_request_events WHERE request_id=? AND event_type='retried'", (failed['id'],)).fetchone()[0] == 0


def test_agent_new_continuation_rejects_stale_head_and_foreign_pair(failed_quality):
    _, repo, checks, service, actor, runtime, failed, starts, business, host, options = failed_quality
    pair = {'job_id': failed['owner_id'], 'failed_request_id': failed['id']}
    wrong = host('continue_task_quality_check', {'payload': {**pair, 'failed_request_id': 'unknown-predecessor'}}, **options)
    assert not wrong['ok'] and wrong['failure']['status_code'] == 409
    assert not host('read_task_quality_continuation', {**pair, 'task_id': 'other'}, **options)['ok']
    with pytest.raises((KeyError, PermissionError)):
        business.quality.read_continuation('local', 'task', pair['job_id'], pair['failed_request_id'], CreationExecutionActor('local', 'other'))
    repo.timeline_records.create_timeline_version(TimelineVersion('v2', 'local', 'task', 'v1', 2, {}, None))
    denied = host('continue_task_quality_check', {'payload': pair}, **options)
    assert not denied['ok'] and denied['failure']['status_code'] == 409 and starts == []
    assert repo.job_records.get_job_run('local', pair['job_id']).status == 'failed'


@pytest.mark.parametrize('background', [False, True], ids=['chat-response', 'goal-finish'])
@pytest.mark.parametrize('recorded', [False, True], ids=['unknown', 'recorded'])
def test_quality_turn_persists_owner_receipt_for_original_input(failed_quality, system, monkeypatch, background, recorded):
    from types import SimpleNamespace
    from fastapi import Request, HTTPException
    from app.assistant.task_business_errors import TaskBusinessRejected
    _, repo, checks, service, actor, runtime, failed, starts, business, host, options = failed_quality
    pair = {'job_id': failed['owner_id'], 'failed_request_id': failed['id']}
    if recorded:
        assert host('continue_task_quality_check', {'payload': pair}, **options)['ok']
    expected = host('read_task_quality_continuation', pair, **options)['value']
    binding = options['source_binding']
    turn_id = binding['turn_id']
    goal = business.conversations.get_task_goal(turn_id)
    preview = {'kind': 'quality_continuation', 'task_id': 'task', **pair,
        'invocation_input_id': goal.get('source_input_id') or turn_id,
        'status': 'recorded', 'receipt': {'check_id': 'invented', 'job_id': pair['job_id'], 'request_id': 'invented'}}
    before_starts = len(starts)
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: None)
    monkeypatch.setattr(main, '_goal_service', lambda: SimpleNamespace(register_turn=lambda _: None))
    if background:
        claim = {'turn_id': turn_id, 'goal': goal}
        finish = lambda details: main._task_turn_http_adapter().finish_goal(business, claim, '质检通过，视频完成', details)
    else:
        request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [
            (key.lower().encode(), value.encode()) for key, value in system[4].items()]})
        payload = SimpleNamespace(conversation_id=binding['conversation_id'], expected_draft_version=1)
        finish = lambda details: main._task_turn_http_adapter().finish('task', request, payload,
            turn_id, '质检通过，视频完成', details, 'review').model_dump(mode='json')
    with pytest.raises((HTTPException, TaskBusinessRejected)):
        finish({**preview, 'invocation_input_id': 'another-input'})
    assert business.conversations.get_turn(turn_id)['status'] == 'running'
    response = finish(preview)
    assert response['quality_continuation'] == expected
    assert response.get('edit_preview') is None
    assert '质检通过，视频完成' not in response['reply']
    restored = ConversationRepository(repo.database_path).get_turn(turn_id)
    assert restored['status'] == 'completed'
    assert restored['result']['response']['quality_continuation'] == expected
    assert restored['result']['response']['reply'] == response['reply']
    assert len(starts) == before_starts
    with repo._connect() as conn:
        assert conn.execute("SELECT count(*) FROM job_runs WHERE kind='quality_check'").fetchone()[0] == 1
        assert dict(conn.execute('SELECT * FROM device_work_requests WHERE id=?', (failed['id'],)).fetchone()) == failed


def test_completed_quality_response_replays_and_new_turn_rejects_old_input(failed_quality, system, monkeypatch):
    from types import SimpleNamespace
    from fastapi import Request, HTTPException
    from app.schemas import CreationTaskAssistantTurnResponse
    _, _, _, _, _, _, failed, starts, business, host, options = failed_quality
    binding = options['source_binding']
    conversations = business.conversations
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [
        (key.lower().encode(), value.encode()) for key, value in system[4].items()]})
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: None)
    monkeypatch.setattr(main, '_goal_service', lambda: SimpleNamespace(register_turn=lambda _: None))
    details = {'kind': 'quality_continuation', 'task_id': 'task', 'job_id': failed['owner_id'],
        'failed_request_id': failed['id'], 'invocation_input_id': binding['input_id'] or binding['turn_id']}
    result = main._task_turn_http_adapter().finish('task', request, SimpleNamespace(
        conversation_id=binding['conversation_id'], expected_draft_version=1),
        binding['turn_id'], '查询原质检', details, 'review')
    replay = main._task_turn_http_adapter().replay(binding['turn_id'], CreationTaskAssistantTurnResponse,
        provider='unused', model='unused', draft_version=99)
    assert replay == result and replay.quality_continuation.status == 'unknown'
    chat = conversations.get(binding['conversation_id'], 'local', binding['member_id'])
    chat = conversations.save_draft(chat['id'], 'local', binding['member_id'], '新的问题', chat['draft_version'])
    turn, _ = conversations.create_turn_idempotent(chat['id'], 'local', binding['member_id'],
        chat['draft_text'], str(uuid4()), chat['draft_version'],
        request_context={'kind': 'task', 'task_id': 'task', 'stage': 'review'})
    with pytest.raises(HTTPException) as invalid:
        main._task_turn_http_adapter().finish('task', request, SimpleNamespace(
            conversation_id=chat['id'], expected_draft_version=chat['draft_version']),
            turn['id'], '旧结果', details, 'review')
    assert invalid.value.status_code == 502
    fresh = conversations.get_turn(turn['id'])
    assert fresh['status'] == 'running' and fresh['result'] is None
    assert starts == []
