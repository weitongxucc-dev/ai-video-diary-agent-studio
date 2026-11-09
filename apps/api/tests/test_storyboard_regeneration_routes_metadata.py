"""Storyboard regeneration refusal and original receipt reads; no submission or runtime."""
import subprocess
import wave

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.artifact_store import ArtifactStore
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.conversation_repository import ConversationRepository
from app.creation.contracts import CreationTask
from app.creation.execution_control import CreationExecutionControl
from app.creation.storyboard_regeneration import StoryboardRegeneration
from app.creation_execution import CreationExecution
from app.schemas import StoryboardRegenerationRequest
from uuid import UUID
from types import SimpleNamespace
from PIL import Image
from app.creation.plan_contracts import CreationPlan
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.media.picture_preview import PicturePreviewRenderer
from app.production import ProductionRepository
from app.source_repository import SourceRepository


def test_storyboard_refusals_and_missing_original_receipt_do_not_write(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('storyboard-submission-or-execution')
        pytest.fail('storyboard metadata forbids submission, runtime, grants and media')

    # Install before creating any fixture facts; the real factory may assemble only.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(CreationExecutionControl, 'regenerate_storyboard', forbidden)
    for name in ('enqueue', 'prepare', 'commit'):
        monkeypatch.setattr(StoryboardRegeneration, name, forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(ArtifactStore, 'resolve_file', forbidden)
    monkeypatch.setattr(main, 'FileResponse', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('render_montage', 'render_picture_input'):
        monkeypatch.setattr(PicturePreviewRenderer, name, forbidden)
    for name in ('_creation_runtime', 'run_task_turn', 'run_plan_turn', 'start_assistant_continuation_driver'):
        monkeypatch.setattr(main, name, forbidden)
    monkeypatch.setattr(main, 'montage_renderer', SimpleNamespace(render=forbidden))
    monkeypatch.setattr(main, 'narration_generator', SimpleNamespace(generate_for_creation_task=forbidden))
    provider = SimpleNamespace(ready=False, model_id='metadata-only', message='isolated', render=forbidden, generate=forbidden)
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
    database = tmp_path / 'studio.sqlite'
    repository = ProductionRepository(database, tmp_path / 'library')
    members = LocalMemberService(database)
    owner, token = members.whoami(None)
    _, reader_token = members.join(members.create_invite_code(), 'Reader')
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'conversation_repo', ConversationRepository(database))
    monkeypatch.setattr(main, 'source_repository', SourceRepository(database))
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
    plan = CreationPlan(id='storyboard-plan', workspace_id='local', goal='Read original storyboard request',
        name='Storyboard metadata', target_count=1, language='zh-CN', aspect_ratio='16:9',
        duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary='', model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint='metadata-only')
    task = CreationTask(id='storyboard-task', workspace_id='local', plan_id=plan.id,
        name='Storyboard metadata', scenario='knowledge_explanation')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    repository.task_records.set_creation_task_visibility('local', task.id, 'shared')
    repository = ProductionRepository(database, tmp_path / 'library')
    monkeypatch.setattr(main, 'production_repo', repository)
    execution = CreationExecution(repository, run_montage_plan=forbidden,
        run_render=forbidden, render_is_valid=forbidden)
    monkeypatch.setattr(main, 'creation_execution', execution)
    case = main._creation_execution_control()
    assert case.repository is repository and case.execution is execution
    assert case.render_stage.repository is repository and case.segments.repository is repository
    request_id = 'c198873c-18df-405b-9e48-1e694f00b507'
    payload = StoryboardRegenerationRequest(request_id=UUID(request_id), script_id='not-produced',
        input_digest='a' * 64, expected_timeline_version_id='not-produced',
        expected_timeline_sequence=1).model_dump(mode='json')

    def facts():
        with repository._connect() as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: sorted((tuple(row) for row in connection.execute(
                'SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr) for name in tables}

    def tree():
        return {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob('*')}

    before_facts, before_tree = facts(), tree()
    client = TestClient(main.app)  # No lifespan/runtime.
    headers = {'X-Local-Member-Token': token}
    base = '/api/creation-tasks/' + task.id + '/montage/plan'
    denied = client.post(base, json=payload, headers={'X-Local-Member-Token': reader_token})
    assert denied.status_code == 403 and denied.json()['detail'] == '当前成员不能修改此任务'
    assert client.post(base, json=payload,
        headers={'X-Local-Member-Token': 'invalid-token'}).status_code == 401
    assert client.post('/api/creation-tasks/missing/montage/plan',
        json=payload, headers=headers).status_code == 404
    assert client.post(base, json={}, headers=headers).status_code == 422
    # No schema-valid owner POST: runtime is intentionally before the business call.
    missing = client.get(base + '/requests/' + request_id, headers=headers)
    assert missing.status_code == 404 and missing.json()['detail'] == '故事板原请求不存在'
    assert client.get(base + '/requests/not-a-uuid', headers=headers).status_code == 422
    assert facts() == before_facts
    assert tree() == before_tree
    assert repository.job_records.list_job_runs('local', task.id) == []
    assert violations == []
    client.close()
