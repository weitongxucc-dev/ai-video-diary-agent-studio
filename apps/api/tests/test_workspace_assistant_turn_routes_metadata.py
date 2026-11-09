"""Workspace planning/material JSON refusals through real admission, without creating a turn."""
import sqlite3
import subprocess
import wave
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main
from app.artifact_store import ArtifactStore
from app.assistant.home_plan_turn_use_case import HomePlanTurnUseCase
from app.assistant.stream_response import AssistantStreamTransport
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.conversation_repository import ConversationRepository
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.production import ProductionRepository
from app.schemas import MaterialAssistantTurnRequest, WorkspaceAssistantPlanTurnRequest
from app.source_repository import SourceRepository
from app.sources import ObsidianVault


def test_workspace_turn_json_refusals_do_not_create_conversations_or_turns(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('turn-stream-planning-or-media')
        pytest.fail('JSON refusal metadata forbids turn creation, stream workers, planning and media')

    # Install before all schema/member preparation; the actual plan/admission read chain stays live.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    monkeypatch.setattr(ConversationRepository, 'create_turn_idempotent', forbidden)
    monkeypatch.setattr(HomePlanTurnUseCase, 'execute', forbidden)
    monkeypatch.setattr(AssistantStreamTransport, 'response', forbidden)
    monkeypatch.setattr(ArtifactStore, 'resolve_file', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('_creation_runtime', '_build_device_components', '_start_device_runtime',
                 '_device_operation_budget', 'run_task_turn', 'run_plan_turn',
                 'start_assistant_continuation_driver'):
        monkeypatch.setattr(main, name, forbidden)
    monkeypatch.setattr(main, 'montage_renderer', SimpleNamespace(render=forbidden))
    monkeypatch.setattr(main, 'narration_generator', SimpleNamespace(generate_for_creation_task=forbidden))
    provider = SimpleNamespace(ready=False, model_id='metadata-only', message='isolated',
                               generate=forbidden, render=forbidden)
    monkeypatch.setattr(main, 'latentsync_provider', provider)
    monkeypatch.setattr(main, 'avatar_lipsync_provider', provider)
    monkeypatch.setattr(main, 'video_job_runner', SimpleNamespace(run=forbidden))
    database = tmp_path / 'workspace.sqlite'
    settings = Settings(database_path=str(database),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'outputs'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    # Configuration only: avoid the real workspace keychain; admission and selection owners are not mocked.
    monkeypatch.setattr(main, '_workspace_settings_for', lambda _workspace_id: settings)
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'automation_runtime', None)
    repository = ProductionRepository(database, tmp_path / 'library')
    conversations = ConversationRepository(database)
    members = LocalMemberService(database)
    member, token = members.whoami(None)
    assert member.member_id
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'source_repository', SourceRepository(database))
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
    monkeypatch.setattr(main, 'vault', ObsidianVault(None))
    missing_conversation = '911ab846-ad72-492e-a250-8beefc3e60b2'
    common = {'message': '只检查不存在的会话', 'conversation_id': missing_conversation,
              'client_request_id': '08ef639e-ae69-4d66-a2d6-a6e744424097', 'expected_draft_version': 0}
    plan_payload = WorkspaceAssistantPlanTurnRequest.model_validate(common).model_dump(mode='json')
    material_payload = MaterialAssistantTurnRequest.model_validate({**common, 'selection': None}).model_dump(mode='json')
    selected_payload = MaterialAssistantTurnRequest.model_validate({**common,
        'selection': {'scope': 'asset', 'asset_id': 'missing-asset'}}).model_dump(mode='json')
    assert UUID(plan_payload['conversation_id']) and UUID(plan_payload['client_request_id'])
    assert conversations.get(missing_conversation, 'local', member.member_id) is None

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

    before = snapshot()  # Only schema and member facts exist; no valid conversation is created.
    client = TestClient(main.app)  # No lifespan; JSON only, never request an SSE stream.
    headers = {'X-Local-Member-Token': token}
    for url, payload in (('/api/assistant/plan/turn', plan_payload),
                         ('/api/assistant/material/turn', material_payload)):
        missing = client.post(url, headers=headers, json=payload)
        assert missing.status_code == 404, missing.text
        assert missing.json()['detail'] == '对话不存在或当前成员无权访问。'
        invalid = client.post(url, headers={'X-Local-Member-Token': 'invalid-token'}, json=payload)
        assert invalid.status_code == 401
    missing_asset = client.post('/api/assistant/material/turn', headers=headers, json=selected_payload)
    assert missing_asset.status_code == 404 and missing_asset.json()['detail'] == 'material asset not found'
    assert snapshot() == before
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM assistant_conversations').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM assistant_conversation_turns').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM job_runs').fetchone()[0] == 0
    assert violations == []
    client.close()
