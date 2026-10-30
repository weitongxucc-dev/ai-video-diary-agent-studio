"""Real missing-toolchain capability reads and WriteAccess refusals, without a request claim."""
import sqlite3
import subprocess
import sys
import wave
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main
from app import media_script, media_script_jobs
from app.artifact_store import ArtifactStore
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.conversation_repository import ConversationRepository
from app.creation.contracts import CreationTask
from app.creation.editing.exact_preview_use_case import ExactPreviewUseCase
from app.creation.plan_contracts import CreationPlan
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.production import ProductionRepository
from app.schemas import MediaScriptPreviewRequest
from app.source_repository import SourceRepository


def test_media_script_capabilities_and_write_access_refusals_do_not_claim(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('claim-budget-runtime-or-media')
        pytest.fail('metadata forbids receipt claims, budget reads, execution and media')

    # Protect before any schema/member/task seed, allowing the actual submission FSM.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(ArtifactStore, 'resolve_file', forbidden)
    monkeypatch.setattr(ExactPreviewUseCase, 'claim_request', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('_creation_runtime', '_exact_preview_use_case', '_device_operation_budget',
                 '_media_script_use_case', 'run_task_turn', 'run_plan_turn',
                 'start_assistant_continuation_driver', '_workspace_settings_for'):
        monkeypatch.setattr(main, name, forbidden)
    for module in (media_script, media_script_jobs):
        for name in ('prepare_media_script', 'run_media_script', 'seal_media_script'):
            monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(media_script_jobs, 'execute_script', forbidden)
    for name in ('prepare_source', 'execute'):
        monkeypatch.setattr(media_script_jobs.MediaScriptPreviewUseCase, name, forbidden)
    monkeypatch.setattr(main, 'montage_renderer', SimpleNamespace(render=forbidden))
    monkeypatch.setattr(main, 'narration_generator', SimpleNamespace(generate_for_creation_task=forbidden))
    provider = SimpleNamespace(ready=False, model_id='metadata-only', message='isolated',
                               generate=forbidden, render=forbidden)
    monkeypatch.setattr(main, 'latentsync_provider', provider)
    monkeypatch.setattr(main, 'avatar_lipsync_provider', provider)
    monkeypatch.setattr(main, 'video_job_runner', SimpleNamespace(run=forbidden))
    settings = Settings(database_path=str(tmp_path / 'studio.sqlite'),
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
    database = tmp_path / 'studio.sqlite'
    repository = ProductionRepository(database, tmp_path / 'library')
    members = LocalMemberService(database)
    owner, owner_token = members.whoami(None)
    reader, reader_token = members.join(members.create_invite_code(), 'Reader')
    assert reader.member_id != owner.member_id
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'conversation_repo', ConversationRepository(database))
    monkeypatch.setattr(main, 'source_repository', SourceRepository(database))
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
    plan = CreationPlan(id='script-plan', workspace_id='local', goal='Read script capabilities',
        name='Script metadata', target_count=1, language='zh-CN', aspect_ratio='16:9',
        duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary='', model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint='metadata-only')
    task = CreationTask(id='script-task', workspace_id='local', plan_id=plan.id,
        name='Script metadata', scenario='knowledge_explanation')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    repository.task_records.set_creation_task_visibility('local', task.id, 'shared')
    repository = ProductionRepository(database, tmp_path / 'library')
    monkeypatch.setattr(main, 'production_repo', repository)
    current = repository.task_records.get_creation_task('local', task.id)
    assert current.owner_user_id == owner.member_id and current.visibility == 'shared'
    payload = MediaScriptPreviewRequest.model_validate({
        'request_id': 'ecce0791-d0bb-4a69-a8d9-96a2c02004fd',
        'expected_timeline_version_id': None, 'expected_timeline_sequence': 0,
        'script_source': '# metadata declaration; never execute',
        'requested_outputs': [{'name': 'output.png', 'max_bytes': 1024, 'width': 16, 'height': 16}],
    }).model_dump(mode='json')

    def facts():
        databases = {}
        for path in tmp_path.rglob('*'):
            if path.is_file() and path.suffix in {'.sqlite', '.sqlite3', '.db'}:
                with sqlite3.connect(path) as connection:
                    tables = [row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                    databases[str(path.relative_to(tmp_path))] = {
                        name: sorted(connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(), key=repr)
                        for name in tables}
        return databases

    def tree():
        return {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob('*')}

    before_facts, before_tree = facts(), tree()
    client = TestClient(main.app)  # No lifespan or runtime.
    owner_headers = {'X-Local-Member-Token': owner_token}
    base = '/api/creation-tasks/' + task.id
    capabilities = client.get(base + '/assistant/media-script-capabilities', headers=owner_headers)
    assert capabilities.status_code == 200, capabilities.text
    result = capabilities.json()
    assert result['task_id'] == task.id and result['available'] is False
    assert result['reason'] == ('受校准的脚本运行时描述不可用' if sys.platform == 'darwin'
                                else '仅当前macOS隔离运行时支持媒体脚本')
    assert result['runtime'] is None and result['limits'] is None
    assert result['schema'] == MediaScriptPreviewRequest.model_json_schema()
    assert result['purpose'] == 'preview' and result['output_count'] == 1
    assert not (tmp_path / 'models' / 'media-script-runtime.json').exists()
    assert client.get('/api/creation-tasks/missing/assistant/media-script-capabilities',
                      headers=owner_headers).status_code == 404
    # Never submit the existing task as its owner: even missing-toolchain failure would claim a receipt.
    denied = client.post(base + '/media-scripts/preview', json=payload,
                         headers={'X-Local-Member-Token': reader_token})
    assert denied.status_code == 403 and denied.json()['detail'] == '当前成员不能修改此任务'
    invalid = client.post(base + '/media-scripts/preview', json=payload,
                          headers={'X-Local-Member-Token': 'invalid-token'})
    assert invalid.status_code == 401
    missing = client.post('/api/creation-tasks/missing/media-scripts/preview',
                          json=payload, headers=owner_headers)
    assert missing.status_code == 404 and missing.json()['detail'] == 'creation task not found'
    assert facts() == before_facts
    assert tree() == before_tree
    assert repository.job_records.list_job_runs('local', task.id) == []
    assert violations == []
    client.close()
