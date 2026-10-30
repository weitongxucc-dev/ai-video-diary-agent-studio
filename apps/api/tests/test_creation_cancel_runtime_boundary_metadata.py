"""Reject unrelated original-goal cancellation before starting a device runtime."""
import sqlite3
import subprocess
import threading
import wave
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

import app.main as main
from app.assistant.task_business import TaskBusiness, TaskBusinessRejected
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.conversation_repository import ConversationRepository
from app.creation.contracts import CreationTask, JobRun
from app.creation.execution_control import CreationExecutionControl
from app.creation.plan_contracts import CreationPlan
from app.creation_execution import CreationExecution, CreationExecutionActor
from app.device_runtime import DeviceRuntime
from app.local_members import LocalMemberService
from app.member_permits import HostBudgetUncalibrated
from app.production import ProductionRepository


def test_cancel_checks_original_goal_and_latest_job_before_runtime(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('unexpected-execution')
        pytest.fail('receipt metadata must not construct, recover or start execution')

    # Protect the boundary before any declared facts are seeded.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(threading.Thread, 'start', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    for name in ('_build_device_components', 'recover_interrupted_quality_checks',
                 '_device_resource_profile', '_wake_assistant_continuations'):
        monkeypatch.setattr(main, name, forbidden)
    settings = Settings(database_path=str(tmp_path / 'studio.sqlite3'),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'output'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'automation_runtime', None)
    repository = ProductionRepository(tmp_path / 'studio.sqlite3', tmp_path / 'library')
    members = LocalMemberService(repository.database_path)
    member, _token = members.whoami(None)
    conversations = ConversationRepository(repository.database_path)
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    plan = CreationPlan(id='plan', workspace_id='local', goal='Original cancellation source', name='Cancellation metadata',
        target_count=1, language='zh-CN', aspect_ratio='9:16', duration_target_seconds=4,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary='', model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint='declared-receipt')
    task = CreationTask('task', 'local', plan.id, 'Cancellation metadata', 'knowledge_explanation',
                        stage='narration', stage_status='not_started')
    plan, task = repository.plan_confirmation.confirm_creation_plan(plan, task,
        verified_owner_user_id=member.member_id)
    chat = conversations.create('local', member.member_id, 'task', task.id)
    chat = conversations.save_draft(chat['id'], 'local', member.member_id,
        '完成原视频并检查输出', chat['draft_version'])
    context = {'kind': 'task', 'task_id': task.id, 'stage': 'narration',
        'selected_object_refs': [], 'target_snapshot': {'timeline_version_id': None, 'timeline_sequence': 0}}
    turn, _ = conversations.create_turn_idempotent(chat['id'], 'local', member.member_id,
        chat['draft_text'], str(uuid4()), chat['draft_version'], request_context=context)
    conversations.finish_turn(turn['id'], 'completed', '已安排', result={'kind': 'task_turn',
        'response': {'media_job': {'job_id': 'declared-existing-source', 'status': 'queued'}}})
    goal = conversations.register_task_goal(turn['id'])
    assert goal is not None
    first = repository.job_records.create_job_run(JobRun('original-job', 'local', task.id,
        'creation_task_execution', 'original-job', 'a' * 64))
    builds = []
    budget_error = HostBudgetUncalibrated('controlled uncalibrated host')

    def unavailable_build(*, repository):
        assert repository is main.production_repo
        builds.append(repository)
        raise budget_error

    monkeypatch.setattr(main, '_build_device_components', unavailable_build)

    def reopen():
        reopened = ProductionRepository(repository.database_path, repository.video_library_path)
        current_conversations = ConversationRepository(repository.database_path)
        monkeypatch.setattr(main, 'production_repo', reopened)
        monkeypatch.setattr(main, 'conversation_repo', current_conversations)
        execution = CreationExecution(reopened, run_montage_plan=forbidden,
            run_render=forbidden, render_is_valid=forbidden)
        control = CreationExecutionControl(reopened, execution, None, forbidden)
        monkeypatch.setattr(control, 'cancel', forbidden)
        ports = ('workspace_facts', 'script_context', 'script_case', 'script_facts', 'generate_topics',
            'topic_facts', 'edit_capabilities', 'script_capabilities', 'tool_capabilities', 'material_facts',
            'observation_facts', 'observe_output', 'request_result', 'media_result', 'record_adoption')
        business = TaskBusiness(reopened, current_conversations, None, runtime=main._creation_runtime,
            execution=control, **{name: forbidden for name in ports})
        current_goal = current_conversations.get_task_goal(turn['id'])
        assert current_goal['original_turn_id'] == goal['original_turn_id']
        return reopened, execution, business, current_goal

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

    actor = CreationExecutionActor('local', member.member_id)
    origin = {'raw_command': '停止原视频制作'}

    def execute(business, current_goal):
        # Direct execute only; this does not claim invoke's separate authorization contract.
        return business.execute('cancel_creation_execution', {}, actor,
            task, current_goal, origin, None, forbidden)

    reopened, execution, business, current_goal = reopen()
    latest_calls = []
    original_latest = execution.latest

    def latest(*args):
        latest_calls.append(args)
        return original_latest(*args)

    monkeypatch.setattr(execution, 'latest', latest)
    before = snapshot()
    with pytest.raises(TaskBusinessRejected, match='当前可停止作业不是原目标绑定的作业'):
        execute(business, current_goal)
    assert latest_calls == [] and builds == []
    assert snapshot() == before

    # This declares a persisted locator, not a successful Agent execution.
    source = {'assistant_turn_id': turn['id'], 'conversation_id': chat['id'],
        'input_id': goal.get('source_input_id'), 'input_turn_id': goal.get('source_input_turn_id')}
    repository.bind_creation_execution_source(first, source)
    reopened, execution, business, current_goal = reopen()
    assert [event.job_id for event in business.execution_sources(current_goal)] == [first.id]
    assert execution.latest('local', task.id).id == first.id
    before = snapshot()
    with pytest.raises(HTTPException) as rejected:
        execute(business, current_goal)
    assert rejected.value.status_code == 503
    assert rejected.value.detail == str(budget_error) and rejected.value.__cause__ is budget_error
    assert builds == [reopened] and main.device_runtime is None
    assert snapshot() == before

    # A later original Job changes the real latest binding; no source is forged or deleted.
    second = repository.job_records.create_job_run(JobRun('later-job', 'local', task.id,
        'creation_task_execution', 'later-job', 'b' * 64))
    reopened, execution, business, current_goal = reopen()
    assert execution.latest('local', task.id).id == second.id
    assert [event.job_id for event in business.execution_sources(current_goal)] == [first.id]
    before = snapshot()
    with pytest.raises(TaskBusinessRejected, match='当前可停止作业不是原目标绑定的作业'):
        execute(business, current_goal)
    assert len(builds) == 1 and main.device_runtime is None
    assert snapshot() == before
    assert violations == []
