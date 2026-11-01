"""Original voice receipts reconcile before the host may start a device runtime."""
import hashlib
import json
import sqlite3
import subprocess
import threading
import wave
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

import app.main as main
from app.assistant.task_business import TaskBusiness
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.conversation_repository import ConversationRepository
from app.creation.contracts import CreationTask, TaskEvent
from app.creation.execution_control import CreationExecutionControl
from app.creation.plan_contracts import CreationPlan
from app.creation.script_contracts import _assistant_reply_request_event_id
from app.creation_execution import CreationExecutionActor
from app.device_runtime import DeviceRuntime
from app.local_members import LocalMemberService
from app.member_permits import HostBudgetUncalibrated
from app.production import ProductionRepository
from app.schemas import CreationTaskNarrationVoiceSelectionResponse


def test_original_voice_receipt_precedes_lazy_runtime_and_preserves_instance_calls(tmp_path, monkeypatch):
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
    plan = CreationPlan(id='plan', workspace_id='local', goal='Original voice receipt', name='Receipt metadata',
        target_count=1, language='zh-CN', aspect_ratio='9:16', duration_target_seconds=4,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary='', model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint='declared-receipt')
    task = CreationTask('task', 'local', plan.id, 'Receipt metadata', 'knowledge_explanation',
                        stage='narration', stage_status='not_started')
    plan, task = repository.plan_confirmation.confirm_creation_plan(plan, task,
        verified_owner_user_id=member.member_id)
    request_id = str(uuid4())
    inputs = {'task_id': task.id, 'member_id': member.member_id, 'voice_profile_id': 'declared-voice',
        'voice_version': 'v1', 'expected_plan_updated_at': plan.updated_at.isoformat(),
        'expected_task_updated_at': task.updated_at.isoformat()}
    command = {'workspace_id': 'local', 'request_id': request_id, **inputs}
    receipt = {'request_id': request_id, 'workspace_id': 'local', 'member_id': member.member_id,
        'task_id': task.id, 'plan_id': plan.id, 'status': 'selected', 'recorded_at': task.updated_at.isoformat(),
        'expected_plan_updated_at': inputs['expected_plan_updated_at'],
        'expected_task_updated_at': inputs['expected_task_updated_at'],
        'plan_updated_at': plan.updated_at.isoformat(), 'task_updated_at': task.updated_at.isoformat(),
        'previous_voice': None, 'pronunciation_references': [], 'confirmed_script_id': None,
        'selected_voice': {'profile_id': 'declared-voice', 'version': 'v1', 'person_profile_id': '',
            'person_version': '', 'person_authorization_id': '', 'person_authorization_version': '',
            'authorization_id': 'declared-auth', 'authorization_version': 'v1', 'language': 'zh',
            'model_id': 'declared-model'}}
    CreationTaskNarrationVoiceSelectionResponse.model_validate(receipt)
    # Historical receipt declaration only: this does not prove an initial voice adoption.
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    event_id = _assistant_reply_request_event_id('task_narration_voice_selected', 'local', request_id)
    repository.execution_records.append_task_event(TaskEvent(event_id, 'local', task.id, None,
        'task_narration_voice_selected', {'fingerprint': fingerprint, 'member_id': member.member_id,
                                         'receipt': receipt}))
    reopened = ProductionRepository(repository.database_path, repository.video_library_path)
    assert reopened.narration_voice_selection.get_creation_task_narration_voice_selection('local', task.id, request_id,
        member_id=member.member_id) == receipt
    monkeypatch.setattr(main, 'production_repo', reopened)
    control = CreationExecutionControl(reopened, SimpleNamespace(latest=forbidden), None, forbidden)
    port_names = ('workspace_facts', 'script_context', 'script_case', 'script_facts', 'generate_topics',
        'topic_facts', 'edit_capabilities', 'script_capabilities', 'tool_capabilities', 'material_facts',
        'observation_facts', 'observe_output', 'request_result', 'media_result', 'record_adoption')
    business = TaskBusiness(reopened, conversations, None, runtime=main._creation_runtime,
        execution=control, **{name: forbidden for name in port_names})
    actor = CreationExecutionActor('local', member.member_id)
    payload = {key: value for key, value in command.items() if key not in {'workspace_id', 'task_id', 'member_id'}}

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

    before = snapshot()
    # This exercises execute, not invoke's separate goal/transport authorization contract.
    def execute(values):
        return business.execute('select_narration_voice', {'payload': values}, actor,
            task, {}, {}, None, forbidden)

    assert execute(payload) == receipt
    with pytest.raises(ValueError, match='不同输入'):
        execute({**payload, 'voice_profile_id': 'different-voice'})
    assert snapshot() == before
    for runtime in (None, SimpleNamespace(wake=forbidden)):
        assert control.select_narration_voice(runtime=runtime, actor=actor, **command) == receipt
    assert snapshot() == before

    builds = []
    budget_error = HostBudgetUncalibrated('controlled uncalibrated host')

    def unavailable_build(*, repository):
        assert repository is reopened
        builds.append(repository)
        raise budget_error

    monkeypatch.setattr(main, '_build_device_components', unavailable_build)
    with pytest.raises(HTTPException) as rejected:
        execute({**payload, 'request_id': str(uuid4())})
    assert rejected.value.status_code == 503
    assert rejected.value.detail == str(budget_error) and rejected.value.__cause__ is budget_error
    assert builds == [reopened] and main.device_runtime is None
    assert snapshot() == before
    assert violations == []
