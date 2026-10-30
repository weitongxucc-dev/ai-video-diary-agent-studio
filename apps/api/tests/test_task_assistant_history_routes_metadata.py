"""Declared historical text input, real SQLite history ordering/filtering, no Agent turn or media."""
from datetime import datetime, timedelta, timezone
import sqlite3
import subprocess
import wave
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.conversation_repository import ConversationRepository
from app.creation.contracts import CreationTask, TaskEvent
from app.creation.plan_contracts import CreationPlan
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.production import ProductionRepository


def test_task_assistant_history_reads_declared_events_in_scope_without_writes(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('conversation-execution-or-media')
        pytest.fail('history metadata forbids Agent turns, models, network, runtime and media')

    # Guard before all authorized temporary plan/task/member/text-event preparation.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    monkeypatch.setattr(ConversationRepository, 'create', forbidden)
    monkeypatch.setattr(ConversationRepository, 'create_turn_idempotent', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('_creation_runtime', '_build_device_components', '_start_device_runtime',
                 '_workspace_settings_for', 'run_task_turn', 'run_plan_turn',
                 'start_assistant_continuation_driver'):
        monkeypatch.setattr(main, name, forbidden)
    monkeypatch.setattr(main, 'montage_renderer', SimpleNamespace(render=forbidden))
    monkeypatch.setattr(main, 'narration_generator', SimpleNamespace(generate_for_creation_task=forbidden))
    database = tmp_path / 'history.sqlite'
    settings = Settings(database_path=str(database),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'outputs'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    for name in ('device_runtime', 'automation_runtime', 'conversation_repo', 'source_repository', 'artifact_store', 'vault'):
        monkeypatch.setattr(main, name, None)
    repository = ProductionRepository(database, tmp_path / 'library')
    members = LocalMemberService(database)
    owner, token = members.whoami(None)
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'local_members_service', members)

    def seed_task(task_id, workspace='local'):
        plan = CreationPlan(id='plan-' + task_id, workspace_id=workspace, goal='Read declared historical text',
            name='History metadata', target_count=1, language='zh-CN', aspect_ratio='16:9',
            duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
            pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
            material_rule_summary='', model_snapshot=[], confirmation_points=[], blockers=[],
            preflight_fingerprint='metadata-only')
        task = CreationTask(id=task_id, workspace_id=workspace, plan_id=plan.id,
            name='History metadata', scenario='knowledge_explanation')
        repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
        return task

    task = seed_task('history-main')
    empty = seed_task('history-empty')
    other = seed_task('history-other')
    foreign = seed_task('history-foreign', 'outside')
    at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    # These are declared preexisting input rows, not output from an Agent or model run.
    user = TaskEvent('history-a-user', 'local', task.id, None, 'assistant_turn',
        {'role': 'user', 'message': '声明的历史提问', 'stage': 'script'}, at)
    assistant = TaskEvent('history-b-assistant', 'local', task.id, None, 'assistant_turn',
        {'role': 'assistant', 'message': '声明的历史回复，不是模型执行结果', 'stage': 'script',
         'script_id': 'declared-script'}, at)
    filtered_role = TaskEvent('history-c-system', 'local', task.id, None, 'assistant_turn',
        {'role': 'system', 'message': '不展示的角色', 'stage': 'script'}, at - timedelta(seconds=1))
    filtered_type = TaskEvent('history-d-note', 'local', task.id, None, 'historical_note',
        {'role': 'user', 'message': '不是助手历史类型', 'stage': 'script'}, at - timedelta(seconds=2))
    other_event = TaskEvent('history-e-other', 'local', other.id, None, 'assistant_turn',
        {'role': 'user', 'message': '其他任务声明', 'stage': 'script'}, at)
    foreign_event = TaskEvent('history-f-foreign', 'outside', foreign.id, None, 'assistant_turn',
        {'role': 'user', 'message': '其他工作区声明', 'stage': 'script'}, at)
    # Reverse insertion at the same timestamp proves the owner's id tie-break is retained.
    for event in (assistant, user, filtered_role, filtered_type, other_event, foreign_event):
        repository.execution_records.append_task_event(event)
    reopened = ProductionRepository(database, tmp_path / 'library')
    monkeypatch.setattr(main, 'production_repo', reopened)
    persisted = reopened.execution_records.list_task_events('local', task.id)
    assert [event.id for event in persisted if event.event_type == 'assistant_turn'
            and event.payload.get('role') in {'user', 'assistant'}] == [user.id, assistant.id]
    assert reopened.execution_records.get_task_event('outside', foreign_event.id) == foreign_event
    assert reopened.execution_records.get_task_event('local', other_event.id) == other_event

    def snapshot():
        databases = {}
        for path in tmp_path.rglob('*'):
            if path.is_file() and path.suffix in {'.sqlite', '.sqlite3', '.db'}:
                with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as connection:
                    tables = [row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                    databases[str(path.relative_to(tmp_path))] = {
                        name: sorted(connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(), key=repr)
                        for name in tables}
        return databases, {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                           for path in tmp_path.rglob('*')}

    before = snapshot()  # All authorized schema/member/plan/task/event writes are preparation.
    client = TestClient(main.app)  # No lifespan or Agent execution.
    headers = {'X-Local-Member-Token': token}
    response = client.get('/api/creation-tasks/' + task.id + '/assistant/history', headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {'items': [
        {'role': 'user', 'message': user.payload['message'], 'stage': 'script', 'script_id': None,
         'at': '2026-10-01T00:00:00Z'},
        {'role': 'assistant', 'message': assistant.payload['message'], 'stage': 'script',
         'script_id': 'declared-script', 'at': '2026-10-01T00:00:00Z'},
    ]}
    empty_response = client.get('/api/creation-tasks/' + empty.id + '/assistant/history', headers=headers)
    assert empty_response.status_code == 200 and empty_response.json() == {'items': []}
    for inaccessible_id in ('missing', foreign.id):
        missing = client.get('/api/creation-tasks/' + inaccessible_id + '/assistant/history', headers=headers)
        assert missing.status_code == 404 and missing.json()['detail'] == 'creation task not found'
    assert snapshot() == before
    assert reopened.job_records.list_job_runs('local', task.id) == []
    assert violations == []
    client.close()
