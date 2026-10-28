import json
from uuid import uuid4

import pytest

import app.main as main
from app.conversation_repository import ConversationRepository
from app.assistant_goal_continuation import GoalContinuationService
from test_creation_task_script_grounding import (
    api_state, agent_payload, draft, reviewed, rejected, set_agent_reviewer, private_task_rows,
    saved_agent_draft,
)


def start_turn(state, monkeypatch, message):
    conversations = ConversationRepository(state.repository.database_path)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    chat = conversations.create('local', state.owner.member_id, 'task', 'task', '文案')
    saved = conversations.save_draft(chat['id'], 'local', state.owner.member_id, message, 0)
    turn, _ = conversations.create_turn_idempotent(chat['id'], 'local', state.owner.member_id, message,
        str(uuid4()), saved['draft_version'], request_context={'kind': 'task', 'task_id': 'task', 'stage': 'script',
            'target_snapshot': {'timeline_version_id': None, 'timeline_sequence': 0}})
    return conversations, chat, turn


def test_checker_receives_actual_current_instruction_and_keeps_fixed_plan(api_state, monkeypatch):
    state = api_state
    instruction = '只写实际朗读正文，片头署名不写入旁白；不改变原方案语言和人物比例。'
    _, _, turn = start_turn(state, monkeypatch, instruction)
    calls = []
    set_agent_reviewer(monkeypatch, reviewed(draft()), calls)
    response = state.client.post('/api/creation-tasks/task/assistant/script-draft',
        json={**agent_payload(state), 'assistant_turn_id': turn['id']})
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    inputs = json.loads(calls[0]['messages'][1]['content'])
    assert inputs['current_instruction'] == instruction
    assert inputs['goal'] == state.plan.goal
    assert inputs['requirements']['language'] == state.plan.language
    assert inputs['requirements']['presenter_share_target'] == state.plan.presenter_share_target
    prompt = calls[0]['messages'][0]['content']
    assert '全部文字都会实际朗读' in prompt and '不能暗改固定的language' in prompt


def test_visual_only_instructions_in_spoken_content_are_reviewed_as_goal_conflict(api_state, monkeypatch):
    state = api_state
    _, _, turn = start_turn(state, monkeypatch, '片尾署名只放画面，不写入旁白。')
    content = '片尾署名卡显示来源，画面文字，不读旁白。'
    document = draft(content)
    calls = []
    set_agent_reviewer(monkeypatch, rejected(document, kind='goal_conflict', field='content', quote=content,
        detail='content全部会朗读，片尾画面文字不能作为旁白。'), calls)
    before = private_task_rows(state)
    response = state.client.post('/api/creation-tasks/task/assistant/script-draft',
        json={**agent_payload(state, content=content), 'assistant_turn_id': turn['id']})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['reason_code'] == 'goal_conflict'
    assert response.json()['detail']['section_index'] == 1
    assert before == private_task_rows(state) and len(calls) == 1


def test_instruction_changed_during_check_does_not_save_the_old_candidate(api_state, monkeypatch):
    state = api_state
    conversations, chat, turn = start_turn(state, monkeypatch, '先按这句要求写旁白。')
    def steer():
        current = conversations.get(chat['id'], 'local', state.owner.member_id)
        message, input_id = '新要求：先讨论，不要保存文案。', str(uuid4())
        saved = conversations.save_draft(chat['id'], 'local', state.owner.member_id, message, current['draft_version'])
        conversations.enqueue_input(chat['id'], 'local', state.owner.member_id, target_turn_id=turn['id'],
            client_request_id=input_id, message=message, mode='steer', expected_draft_version=saved['draft_version'],
            expected_reference_version=0, request_context={'kind': 'task', 'task_id': 'task', 'stage': 'script',
                'target_snapshot': {'timeline_version_id': None, 'timeline_sequence': 0}})
        conversations.transition_input(chat['id'], 'local', state.owner.member_id, input_id, 'consumed')
    calls = []
    set_agent_reviewer(monkeypatch, reviewed(draft()), calls, during_check=steer)
    before = private_task_rows(state)
    response = state.client.post('/api/creation-tasks/task/assistant/script-draft',
        json={**agent_payload(state), 'assistant_turn_id': turn['id']})
    assert response.status_code == 409, response.text
    assert before == private_task_rows(state) and len(calls) == 1


def test_actual_script_adoption_triggers_one_existing_continuation(api_state, monkeypatch):
    state = api_state
    conversations, _, turn = start_turn(state, monkeypatch, '文案和剪辑由你完成，采用后继续制作。')
    with state.repository._connect() as db:
        plan = json.loads(db.execute("SELECT payload FROM creation_plans WHERE id='plan'").fetchone()[0])
        plan['goal'] = '制作本机工作区介绍视频。'
        plan['creation_intent'] = {'kind': 'goal_delegation', 'source': {'kind': 'user_action',
            'member_id': state.owner.member_id, 'request_id': 'original-delegation'}}
        db.execute("UPDATE creation_plans SET payload=? WHERE id='plan'", (json.dumps(plan),))
    calls = []
    set_agent_reviewer(monkeypatch, reviewed(draft()), calls)
    saved = state.client.post('/api/creation-tasks/task/assistant/script-draft',
        json={**agent_payload(state), 'assistant_turn_id': turn['id']})
    assert saved.status_code == 200, saved.text
    script = saved.json()
    state.client.app.add_api_route('/api/creation-tasks/{task_id}/scripts/{script_id}/confirm',
        main.confirm_creation_task_script, methods=['POST'])
    body = {key: script[key] for key in ('title', 'sections')}
    body.update(expected_updated_at=script['updated_at'], adoption_kind='delegated_system', assistant_turn_id=turn['id'])
    response = state.client.post(f"/api/creation-tasks/task/scripts/{script['id']}/confirm", json=body)
    assert response.status_code == 200, response.text
    actual = state.repository.get_creation_task_script('local', script['id'])
    source = conversations.task_turn_source(turn['id'], 'local', state.owner.member_id, 'task')
    current = state.repository.task_records.get_creation_task('local', 'task')
    plan = state.repository.plan_records.get_creation_plan('local', current.plan_id)
    service = main._goal_service()
    # Same real receipt is replayed; no second event or adoption is invented.
    assert service.record_script_adoption(turn['id'], 'local', state.owner.member_id, current, plan, actual, source)
    conversations.finish_turn(turn['id'], 'completed', '采用完成。', result={'kind': 'task_turn',
        'response': {'script_draft': response.json()['scripts'][0]}})
    assert service.register_turn(turn['id'])
    events = [item for item in conversations.get_turn(turn['id'])['events'] if item.get('phase') == 'script_adoption']
    assert len(events) == 1 and events[0]['script_id'] == script['id']
    started = []
    def run(claim, fresh):
        started.append(claim)
        conversations.finish_turn(claim['turn_id'], 'completed', '接续边界测试，无模型媒体。', result={})
    continuation = GoalContinuationService(conversations, verify_current_goal=lambda *_: {
        'status': 'ready', 'task_version': {'timeline_version_id': None, 'timeline_sequence': 0}}, run_goal_turn=run)
    assert continuation.reconcile() == 1
    assert continuation.reconcile() == 0 and len(started) == 1
    assert len(calls) == 1


@pytest.mark.parametrize('kind,message', [('discussion', '只讨论旁白。'), ('goal_delegation', '只写文案，不要制作。')])
def test_discussion_and_draft_only_never_record_an_execution_wakeup(api_state, monkeypatch, kind, message):
    from types import SimpleNamespace
    state = api_state
    conversations, _, turn = start_turn(state, monkeypatch, message)
    source = conversations.task_turn_source(turn['id'], 'local', state.owner.member_id, 'task')
    service = main._goal_service()
    # The actual adoption predicate is false for discussion. Draft-only input
    # also remains a stop even if an older plan carried production delegation.
    import hashlib
    body = '实际正文'
    authorization = {'member_id': state.owner.member_id}
    plan = SimpleNamespace(creation_intent={'kind': kind, 'source': authorization})
    script = SimpleNamespace(id='script', task_id='task', selection_status='confirmed',
        sections=[SimpleNamespace(content=body)], updated_at=state.task.updated_at,
        adoption={'kind': 'delegated_system', 'authorization_source': authorization,
            'body_digest': hashlib.sha256(body.encode()).hexdigest()})
    task = SimpleNamespace(id='task', confirmed_script_id='script')
    assert not service.record_script_adoption(turn['id'], 'local', state.owner.member_id, task, plan, script, source)
    conversations.finish_turn(turn['id'], 'completed', '仅文案。', result={})
    assert service.register_turn(turn['id']) is None
    assert conversations.goal_event_candidates() == []


def adopted_before_narration(state):
    old = saved_agent_draft(state)
    task, script = state.repository.confirm_creation_task_script('local', 'task', old.id,
        title=old.title, sections=old.sections, expected_updated_at=old.updated_at,
        verified_owner_user_id=state.owner.member_id)
    assert task.stage == 'narration' and task.stage_status == 'not_started' and task.can_revise_script
    return script


@pytest.mark.parametrize('through_agent', [False, True])
def test_same_task_adopted_text_changes_only_after_review_and_old_revision_is_rejected(api_state, monkeypatch, through_agent):
    state = api_state
    old = adopted_before_narration(state)
    turn = None
    if through_agent:
        with state.repository._connect() as db:
            plan = json.loads(db.execute("SELECT payload FROM creation_plans WHERE id='plan'").fetchone()[0])
            plan['goal'] = '制作本机工作区介绍视频。'
            plan['creation_intent'] = {'kind': 'goal_delegation', 'source': {'kind': 'user_action',
                'member_id': state.owner.member_id, 'request_id': 'original-delegation'}}
            db.execute("UPDATE creation_plans SET payload=? WHERE id='plan'", (json.dumps(plan),))
        _, _, turn = start_turn(state, monkeypatch, '请修正并采用当前旁白，继续原来的制作。')
    context = state.client.get('/api/creation-tasks/task/assistant/script-context')
    assert context.status_code == 200 and context.json()['draft']['id'] == old.id
    revised_content = '用户可为素材设置用途。'
    document = draft(revised_content)
    calls = []
    def while_checking():
        actual = state.repository.get_creation_task_script('local', old.id)
        assert actual.sections == old.sections and actual.updated_at == old.updated_at
    set_agent_reviewer(monkeypatch, reviewed(document), calls, during_check=while_checking)
    body = agent_payload(state, old, content=revised_content)
    if turn is not None:
        body['assistant_turn_id'] = turn['id']
    response = state.client.post('/api/creation-tasks/task/assistant/script-draft', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['id'] == old.id and response.json()['selection_status'] == 'confirmed'
    assert response.json()['adoption']['kind'] == ('delegated_system' if through_agent else 'manual')
    actual = state.repository.get_creation_task_script('local', old.id)
    assert actual.sections[0].content == revised_content and actual.updated_at != old.updated_at
    assert state.repository.task_records.get_creation_task('local', 'task').stage == 'narration'
    stale = state.client.post('/api/creation-tasks/task/assistant/script-draft', json=body)
    assert stale.status_code == 409 and len(calls) == 1


def test_failed_review_keeps_the_same_task_adopted_body_untouched(api_state, monkeypatch):
    state = api_state
    old = adopted_before_narration(state)
    content = '片尾署名卡，非旁白。'
    calls = []
    set_agent_reviewer(monkeypatch, rejected(draft(content), kind='goal_conflict', field='content', quote=content,
        detail='制作说明不得朗读。'), calls)
    before = private_task_rows(state)
    response = state.client.post('/api/creation-tasks/task/assistant/script-draft', json=agent_payload(state, old, content=content))
    assert response.status_code == 422 and len(calls) == 1
    assert before == private_task_rows(state)


@pytest.mark.parametrize('instruction', ['只讨论这句怎么修。', '先看看方案，不要采用。', '修订但不采用，不替换原稿。'])
def test_draft_only_revision_never_becomes_manual_adoption(api_state, monkeypatch, instruction):
    state = api_state
    old = adopted_before_narration(state)
    with state.repository._connect() as db:
        plan = json.loads(db.execute("SELECT payload FROM creation_plans WHERE id='plan'").fetchone()[0])
        plan['creation_intent'] = {'kind': 'goal_delegation', 'source': {'kind': 'user_action',
            'member_id': state.owner.member_id, 'request_id': 'original-delegation'}}
        db.execute("UPDATE creation_plans SET payload=? WHERE id='plan'", (json.dumps(plan),))
    conversations, _, turn = start_turn(state, monkeypatch, instruction)
    revised_content = '用户可为素材设置用途。'
    calls = []
    set_agent_reviewer(monkeypatch, reviewed(draft(revised_content)), calls)
    before = private_task_rows(state)
    response = state.client.post('/api/creation-tasks/task/assistant/script-draft',
        json={**agent_payload(state, old, content=revised_content), 'assistant_turn_id': turn['id']})
    assert response.status_code == 409, response.text
    assert private_task_rows(state) == before
    actual = state.repository.get_creation_task_script('local', old.id)
    assert actual.sections == old.sections and actual.updated_at == old.updated_at and actual.adoption == old.adoption
    assert conversations.get_task_goal(turn['id']) is None
    assert not any(event.get('phase') == 'script_adoption' for event in conversations.get_turn(turn['id'])['events'])
    assert conversations.goal_event_candidates() == []


def test_locked_or_downstream_script_is_not_opened_for_automatic_revision(api_state, monkeypatch):
    state = api_state
    old = adopted_before_narration(state)
    with state.repository._connect() as db:
        data = json.loads(db.execute('SELECT payload FROM creation_task_scripts WHERE id=?', (old.id,)).fetchone()[0])
        data['locked_body_digest'] = 'locked-exact-copy'
        db.execute('UPDATE creation_task_scripts SET payload=? WHERE id=?', (json.dumps(data), old.id))
    response = state.client.post('/api/creation-tasks/task/assistant/script-draft', json=agent_payload(state, old))
    assert response.status_code == 409 and '锁定' in response.text


@pytest.mark.parametrize('field', ['narration_cache_key', 'montage_plan_id', 'current_timeline_version_id'])
def test_existing_downstream_facts_keep_revision_closed(api_state, field):
    state = api_state
    old = adopted_before_narration(state)
    with state.repository._connect() as db:
        data = json.loads(db.execute("SELECT payload FROM creation_tasks WHERE id='task'").fetchone()[0])
        data[field] = 'existing-result'
        db.execute("UPDATE creation_tasks SET payload=? WHERE id='task'", (json.dumps(data),))
    response = state.client.post('/api/creation-tasks/task/assistant/script-draft', json=agent_payload(state, old))
    assert response.status_code == 409
