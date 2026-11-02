"""Fixed-source adoption uses real public source reads and one SQLite transaction."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4

import pytest

import app.main as main
from app.creation.script_contracts import CreationTaskScriptSection, ScriptGenerationRun
from app.creation.contracts import JobRun
from app.source_types import KnowledgeItem
from test_creation_task_script_grounding import (
    api_state, draft, reviewed, saved_agent_draft, set_api_provider,
)
from test_knowledge_original_files import uploaded, word_file


@pytest.fixture(autouse=True)
def knowledge_adoption_metadata_guard(request, monkeypatch, tmp_path):
    if request.node.name not in {
        'test_stale_plan_or_task_revision_rejects_without_writes[expected_plan_updated_at]',
        'test_unavailable_source_is_rejected_before_plan_or_task_write[missing]',
    }:
        return None
    import sqlite3
    import subprocess
    import wave
    from types import SimpleNamespace
    from PIL import Image
    from app.artifact_store import ArtifactStore
    from app.auth import SupabaseAuthContext
    from app.config import Settings
    from app.device_runtime import DeviceRuntime
    from app.device_scheduler import DeviceScheduler

    violations = []

    def forbidden(*args, **kwargs):
        violations.append('knowledge-adoption-media-or-execution')
        pytest.fail('knowledge refusal metadata forbids models, script execution, runtime, grants and media')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('_creation_runtime', '_start_device_runtime', 'run_task_turn', 'run_plan_turn',
                 '_execute_script_generation', '_creation_task_script_generator_for',
                 '_creation_task_topic_generator_for', 'start_assistant_continuation_driver'):
        monkeypatch.setattr(main, name, forbidden)
    monkeypatch.setattr(main, 'montage_renderer', SimpleNamespace(render=forbidden))
    monkeypatch.setattr(main, 'narration_generator', SimpleNamespace(generate_for_creation_task=forbidden))
    provider = SimpleNamespace(ready=False, model_id='metadata-only', render=forbidden, generate=forbidden)
    monkeypatch.setattr(main, 'latentsync_provider', provider)
    monkeypatch.setattr(main, 'avatar_lipsync_provider', provider)
    monkeypatch.setattr(main, 'video_job_runner', SimpleNamespace(run=forbidden))
    settings = Settings(database_path=str(tmp_path / 'isolated.sqlite'),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'output'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
    # Load the unchanged text-only fixture only after all execution guards exist.
    target = request.getfixturevalue('state')

    def snapshot():
        databases = {}
        for path in tmp_path.rglob('*'):
            if path.is_file() and path.suffix in {'.sqlite', '.sqlite3', '.db'}:
                with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as conn:
                    tables = [row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                    databases[str(path.relative_to(tmp_path))] = {
                        table: sorted((tuple(row) for row in conn.execute(
                            'SELECT * FROM "' + table.replace('"', '""') + '"')), key=repr) for table in tables}
        tree = {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob('*')}
        return databases, tree

    def assert_reads_unchanged(body, before):
        url = '/api/creation-tasks/task/knowledge-adoptions/'
        missing = target.client.get(url + body['request_id'])
        assert missing.status_code == 404
        assert missing.json()['detail'] == '没有找到此成员在当前任务中的原资料采用回执。'
        assert target.client.get(url + 'not-a-uuid').status_code == 422
        assert snapshot() == before
        assert target.repository.job_records.list_job_runs('local', 'task') == []
        assert violations == []

    target._adoption_http_metadata = SimpleNamespace(snapshot=snapshot, assert_reads_unchanged=assert_reads_unchanged)

    def check_no_execution():
        assert violations == []

    request.addfinalizer(check_no_execution)


@pytest.fixture
def state(api_state):
    for route in main.app.routes:
        if getattr(route, 'path', '').startswith('/api/creation-tasks/{task_id}/knowledge-adoptions'):
            api_state.client.app.routes.append(route)
    return api_state


def knowledge(state, identity='additional', *, owner=None, workspace='local'):
    return state.sources.create_knowledge_item_owned(KnowledgeItem(
        id=identity, workspace_id=workspace, content_type='note', title='补充资料',
        body='新增资料：资料可按固定版本引用。', file_reference=None,
        authorization='authorized', ownership='user_owned', source_item_id=None, version='1'),
        verified_user_id=owner or state.owner.member_id)


def command(state, item, *, request_id=None):
    plan = state.repository.plan_records.get_creation_plan('local', 'plan')
    task = state.repository.task_records.get_creation_task('local', 'task')
    return {'request_id': request_id or str(uuid4()),
        'expected_plan_updated_at': plan.updated_at.isoformat(),
        'expected_task_updated_at': task.updated_at.isoformat(),
        'knowledge_references': [{'knowledge_item_id': item.id, 'version': item.version}]}


def adopt(state, body):
    return state.client.post('/api/creation-tasks/task/knowledge-adoptions', json=body)


def rows(state, tables=('creation_plans', 'creation_tasks', 'creation_task_scripts', 'script_generation_runs', 'task_events')):
    with state.repository._connect() as conn:
        return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY id')]
            for table in tables}


def test_append_is_atomic_replayable_and_preserves_old_script_and_run(state, monkeypatch):
    old = saved_agent_draft(state)
    historical = rows(state, ('creation_task_scripts', 'script_generation_runs'))
    item = knowledge(state)
    body = command(state, item)
    response = adopt(state, body)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt['status'] == 'adopted' and receipt['retained_script_count'] == 1
    assert receipt['next_step'] == 'generate_new_script'
    assert receipt['added_references'][0]['id'] == item.id
    assert receipt['previous_references'][0]['id'] == 'fixture'
    task = state.repository.task_records.get_creation_task('local', 'task')
    assert task.id == old.task_id and task.selected_topic_id == old.topic_id
    assert task.stage == 'script' and task.stage_status == 'not_started'
    assert rows(state, historical) == historical
    assert main._creation_task_response_adapter().creation_plan_preview_response(state.repository.plan_records.get_creation_plan('local', 'plan')).updated_at == task.updated_at
    events = [event for event in state.repository.execution_records.list_task_events('local', 'task') if event.event_type == 'task_knowledge_adopted']
    assert len(events) == 1
    assert datetime.fromisoformat(events[0].payload['receipt']['task_updated_at']) == datetime.fromisoformat(receipt['task_updated_at'])
    committed = rows(state)
    # Receipt replay is a read of the original outcome; it does not re-adopt
    # revoked content or claim that the current source grant is still valid.
    state.sources.set_knowledge_item_use_state('local', item.id, 'excluded')
    monkeypatch.setattr(state.sources, 'get_knowledge_reference_readable_by', lambda *a, **kw: pytest.fail('replay must read the receipt'))
    assert adopt(state, body).json() == receipt
    query = state.client.get('/api/creation-tasks/task/knowledge-adoptions/' + body['request_id'])
    assert query.status_code == 200 and query.json() == receipt
    assert rows(state) == committed


def test_new_script_consumes_new_fixed_inputs_and_old_draft_cannot_be_adopted(state, monkeypatch):
    old = saved_agent_draft(state)
    historical = rows(state, ('creation_task_scripts', 'script_generation_runs'))
    item = knowledge(state)
    assert adopt(state, command(state, item)).status_code == 200
    context = state.client.get('/api/creation-tasks/task/assistant/script-context')
    assert context.status_code == 200, context.text
    assert context.json()['draft'] is None and context.json()['retained_draft']['id'] == old.id
    with pytest.raises(ValueError, match='追加资料'):
        state.repository.script_records.confirm_creation_task_script('local', 'task', old.id,
            title=old.title, sections=old.sections, expected_updated_at=old.updated_at)
    with pytest.raises(ValueError, match='旧稿仅保留'):
        state.repository.script_records.update_assistant_script_draft('local', 'task', old.id,
            title=old.title, sections=old.sections, expected_updated_at=old.updated_at)
    assert rows(state, historical) == historical
    document, calls = draft(), []
    set_api_provider(monkeypatch, document, reviewed(document), calls)
    response = state.client.post('/api/creation-tasks/task/scripts/generate', json={})
    assert response.status_code == 200, response.text
    model_input = json.loads(calls[0][1]['messages'][1]['content'])
    assert any(source['text'] == item.body for source in model_input['sources'])
    run = state.repository.script_records.latest_script_generation_run('local', 'task')
    assert any(ref['object_id'] == item.id and ref['version_or_identity'] == item.version for ref in run.input_references)
    scripts = state.repository.script_records.list_creation_task_scripts('local', 'task')
    assert len(scripts) == 2 and scripts[0].id != old.id
    assert state.repository.script_records.get_creation_task_script('local', old.id) == old
    # Even after the task returns to waiting_for_user, the old draft stays stale.
    with pytest.raises(ValueError, match='不能采用旧资料'):
        state.repository.script_records.confirm_creation_task_script('local', 'task', old.id,
            title=old.title, sections=old.sections, expected_updated_at=old.updated_at)
    current = scripts[0]
    task, confirmed = state.repository.script_records.confirm_creation_task_script('local', 'task', current.id,
        title=current.title, sections=current.sections, expected_updated_at=current.updated_at)
    assert task.confirmed_script_id == confirmed.id == current.id


def test_in_flight_old_input_snapshot_cannot_start_after_adoption(state):
    delayed = ScriptGenerationRun(id='delayed', workspace_id='local', task_id='task', plan_id='plan',
        topic_id='topic', status='running', route_snapshot={}, prompt_schema_version='test', input_references=state.refs)
    assert adopt(state, command(state, knowledge(state))).status_code == 200
    before = rows(state)
    with pytest.raises(ValueError, match='全部固定引用'):
        state.repository.script_records.start_script_generation(delayed)
    assert rows(state) == before

    from dataclasses import replace
    task = state.repository.task_records.get_creation_task('local', 'task')
    plan = state.repository.plan_records.get_creation_plan('local', task.plan_id)
    _, current_refs, _ = main._source_inputs_use_case().prepare('local', task, plan, cloud_egress=True)
    current = replace(delayed, id='current-inputs', input_references=current_refs)
    task, run = state.repository.script_records.start_script_generation(current)
    planned = {(item.id, item.version) for item in plan.knowledge_references}
    bound = {(item['object_id'], item['version_or_identity']) for item in run.input_references
             if item.get('kind') == 'knowledge'}
    assert planned.issubset(bound) and len(planned) == 2
    assert task.stage_status == 'running' and run.id == current.id


@pytest.mark.parametrize('field', ['expected_plan_updated_at', 'expected_task_updated_at'])
def test_stale_plan_or_task_revision_rejects_without_writes(state, field):
    body = command(state, knowledge(state))
    body[field] = '2000-01-01T00:00:00Z'
    before = rows(state)
    before_metadata = state._adoption_http_metadata.snapshot() if field == 'expected_plan_updated_at' else None
    response = adopt(state, body)
    assert response.status_code == 409 and '版本已变化' in response.text
    assert rows(state) == before
    if before_metadata is not None:
        state._adoption_http_metadata.assert_reads_unchanged(body, before_metadata)


@pytest.mark.parametrize('case', ['missing', 'wrong_version', 'private', 'other_workspace', 'excluded', 'not_for_automation'])
def test_unavailable_source_is_rejected_before_plan_or_task_write(state, case):
    item = knowledge(state, owner='someone-else' if case == 'private' else None,
        workspace='elsewhere' if case == 'other_workspace' else 'local')
    body = command(state, item)
    if case == 'missing':
        body['knowledge_references'][0]['knowledge_item_id'] = 'missing'
    elif case == 'wrong_version':
        body['knowledge_references'][0]['version'] = 'missing-version'
    elif case in {'excluded', 'not_for_automation'}:
        state.sources.set_knowledge_item_use_state('local', item.id, case)
    before = rows(state)
    before_metadata = state._adoption_http_metadata.snapshot() if case == 'missing' else None
    response = adopt(state, body)
    assert response.status_code in {404, 409}, response.text
    assert rows(state) == before
    if before_metadata is not None:
        state._adoption_http_metadata.assert_reads_unchanged(body, before_metadata)


def test_receipt_request_identity_cannot_be_reused_for_different_sources(state):
    first, second = knowledge(state, 'first'), knowledge(state, 'second')
    body = command(state, first)
    assert adopt(state, body).status_code == 200
    before = rows(state)
    body['knowledge_references'][0]['knowledge_item_id'] = second.id
    response = adopt(state, body)
    assert response.status_code == 409 and '不同任务或资料' in response.text
    assert rows(state) == before


def test_concurrent_replay_returns_one_committed_receipt(state):
    body = command(state, knowledge(state))
    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(lambda _: adopt(state, body), range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert len([event for event in state.repository.execution_records.list_task_events('local', 'task')
        if event.event_type == 'task_knowledge_adopted']) == 1


def test_multi_source_failure_does_not_partially_adopt_available_reference(state):
    body = command(state, knowledge(state))
    body['knowledge_references'].append({'knowledge_item_id': 'missing', 'version': '1'})
    before = rows(state)
    assert adopt(state, body).status_code == 404
    assert rows(state) == before


def test_unchanged_reference_is_explicit_noop_and_does_not_invalidate_draft(state):
    old = saved_agent_draft(state)
    item = state.sources.get_knowledge_item('local', 'fixture')
    before = rows(state, ('creation_plans', 'creation_tasks', 'creation_task_scripts', 'script_generation_runs'))
    response = adopt(state, command(state, item))
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'unchanged' and response.json()['next_step'] == 'unchanged'
    assert response.json()['added_references'] == [] and rows(state, before) == before
    assert state.repository.script_records.script_knowledge_inputs_current('local', 'task', old.id)


@pytest.mark.parametrize('busy', ['script_running', 'queued', 'running', 'interrupted', 'confirmed'])
def test_busy_or_downstream_task_rejects_addition_without_silent_invalidation(state, busy):
    if busy == 'script_running':
        state.repository.script_records.start_script_generation(ScriptGenerationRun(id='busy', workspace_id='local', task_id='task',
            plan_id='plan', topic_id='topic', status='running', route_snapshot={}, prompt_schema_version='test', input_references=state.refs))
    elif busy == 'confirmed':
        old = saved_agent_draft(state)
        state.repository.script_records.confirm_creation_task_script('local', 'task', old.id,
            title=old.title, sections=old.sections, expected_updated_at=old.updated_at)
    else:
        state.repository.job_records.create_job_run(JobRun(id='busy', workspace_id='local', task_id='task', kind='script_generation',
            idempotency_key='busy', input_digest='digest'))
        if busy == 'running':
            state.repository.job_records.claim_job_run('local', 'busy')
        elif busy == 'interrupted':
            state.repository.job_records.claim_job_run('local', 'busy')
            state.repository.job_records.interrupt_running_job_runs('local')
    body = command(state, knowledge(state))
    before = rows(state, ('creation_plans', 'creation_tasks', 'creation_task_scripts', 'script_generation_runs', 'task_events', 'job_runs'))
    response = adopt(state, body)
    assert response.status_code == 409, response.text
    assert ('下游结果' if busy == 'confirmed' else '未结束') in response.text
    assert rows(state, before) == before


def test_failed_event_insert_rolls_back_both_plan_and_task(state):
    saved_agent_draft(state)
    body = command(state, knowledge(state))
    before = rows(state)
    with state.repository._connect() as conn:
        conn.execute("CREATE TRIGGER fail_adoption BEFORE INSERT ON task_events WHEN NEW.event_type='task_knowledge_adopted' "
            "BEGIN SELECT RAISE(ABORT, 'adoption-event-failure'); END")
    assert adopt(state, body).status_code == 409
    assert rows(state) == before


def test_exact_historical_version_survives_head_update_and_does_not_replace_existing_reference(state):
    _, first = uploaded(state.sources, word_file('1200'), owner=state.owner.member_id, workspace='local')
    _, current = uploaded(state.sources, word_file('1600'), owner=state.owner.member_id, workspace='local')
    assert current.id == first.id and current.version == '2'
    response = adopt(state, command(state, first))
    assert response.status_code == 200, response.text
    assert response.json()['added_references'][0]['version'] == '1'
    plan = state.repository.plan_records.get_creation_plan('local', 'plan')
    task = state.repository.task_records.get_creation_task('local', 'task')
    sources, refs, _ = main._source_inputs_use_case().prepare('local', task, plan, cloud_egress=True)
    assert any(ref['object_id'] == first.id and ref['version_or_identity'] == '1' for ref in refs)
    assert any('1200' in source.text and '1600' not in source.text for source in sources)
    before = rows(state)
    conflict = adopt(state, command(state, current))
    assert conflict.status_code == 409 and '只追加' in conflict.text
    assert rows(state) == before


def test_current_revocation_of_shared_source_prevents_new_adoption(state):
    item = knowledge(state, owner='another-owner')
    state.sources.set_knowledge_item_visibility('local', item.id, 'shared')
    body = command(state, item)
    state.sources.set_knowledge_item_visibility('local', item.id, 'private')
    before = rows(state)
    assert adopt(state, body).status_code == 404
    assert rows(state) == before


def test_private_task_and_receipt_cannot_be_read_or_changed_by_other_member(state):
    body = command(state, knowledge(state))
    assert adopt(state, body).status_code == 200
    _, other_token = main.local_members_service.join(main.local_members_service.create_invite_code(), '另一成员')
    before = rows(state)
    state.client.headers['X-Local-Member-Token'] = other_token
    assert adopt(state, body).status_code == 404
    assert state.client.get('/api/creation-tasks/task/knowledge-adoptions/' + body['request_id']).status_code == 404
    assert rows(state) == before


@pytest.mark.parametrize('replace', [False, True])
def test_reply_transfer_after_adoption_keeps_historical_script_immutable(state, replace):
    old = saved_agent_draft(state)
    assert adopt(state, command(state, knowledge(state))).status_code == 200
    plan = state.repository.plan_records.get_creation_plan('local', 'plan')
    task = state.repository.task_records.get_creation_task('local', 'task')
    _, refs, _ = main._source_inputs_use_case().prepare('local', task, plan, cloud_egress=True)
    text = '基于新增资料的明确回复。'
    kwargs = dict(request_id=str(uuid4()), reply_text=text,
        reply_reference={'conversation_id': 'conversation', 'turn_id': 'turn',
            'response_digest': hashlib.sha256(text.encode()).hexdigest()},
        expected_plan_id=plan.id, expected_plan_updated_at=plan.updated_at,
        expected_topic_id=task.selected_topic_id, expected_task_updated_at=task.updated_at,
        input_references=refs, script_id=old.id if replace else 'new-reply-script',
        run_id='new-reply-run', title='补充资料后的回复', language='zh-CN',
        sections=[CreationTaskScriptSection(id='new-section', title='新回复', content=text,
            visual_prompt='', evidence_reference_ids=[])], verified_owner_user_id=state.owner.member_id)
    historical = rows(state, ('creation_task_scripts', 'script_generation_runs'))
    if replace:
        with pytest.raises(ValueError, match='旧稿仅保留为历史'):
            state.repository.script_records.transfer_completed_assistant_reply_to_script('local', 'task',
                **kwargs, replace_existing_script_id=old.id, expected_script_updated_at=old.updated_at)
        assert rows(state, historical) == historical
    else:
        _, run, script = state.repository.script_records.transfer_completed_assistant_reply_to_script('local', 'task', **kwargs)
        assert script.id != old.id and run.input_references == refs
        assert len(state.repository.script_records.list_creation_task_scripts('local', 'task')) == 2
    assert state.repository.script_records.get_creation_task_script('local', old.id) == old
