"""Topic preflight reads and missing-task commands only; no generation facts or models."""
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
from app.creation.script_draft_use_case import ScriptDraftUseCase
from app.creation.topic_records import TopicRecordStore
from app.automation_runtime import AutomationRuntime
from app.schemas import CreationTaskTopicConfirmRequest
from types import SimpleNamespace
from PIL import Image
from app.creation.plan_contracts import CreationPlan
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.media.picture_preview import PicturePreviewRenderer
from app.production import ProductionRepository
from app.source_repository import SourceRepository


def test_task_topic_preflight_and_missing_commands_do_not_write(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('topic-generation-or-media')
        pytest.fail('topic metadata forbids generation facts, automation execution, models and media')

    # Install before creating any fixture facts; the real factory may assemble only.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(ScriptDraftUseCase, 'generate_topics', forbidden)
    monkeypatch.setattr(TopicRecordStore, 'start_topic_generation', forbidden)
    for name in ('start', 'drive', 'resume'):
        monkeypatch.setattr(AutomationRuntime, name, forbidden)
    for name in ('_creation_task_topic_generator_for', '_creation_task_script_generator_for',
                 '_execute_script_generation', 'start_automation_due_driver'):
        monkeypatch.setattr(main, name, forbidden)
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
    monkeypatch.setattr(main, 'automation_runtime', None)
    monkeypatch.setattr(main, '_workspace_settings_for', lambda workspace_id: settings)
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
    plan = CreationPlan(id='topic-plan', workspace_id='local', goal='Read topic preflight',
        name='Topic metadata', target_count=1, language='zh-CN', aspect_ratio='16:9',
        duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary='', model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint='metadata-only')
    task = CreationTask(id='topic-task', workspace_id='local', plan_id=plan.id,
        name='Topic metadata', scenario='knowledge_explanation')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    repository = ProductionRepository(database, tmp_path / 'library')
    monkeypatch.setattr(main, 'production_repo', repository)
    automation = main._automation_runtime()  # Pure assembly/schema in the seed interval.
    assert automation.production is repository
    confirm = CreationTaskTopicConfirmRequest().model_dump(mode='json')

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
    base = '/api/creation-tasks/' + task.id + '/topics'
    read = client.get(base, headers=headers)
    assert read.status_code == 200, read.text
    body = read.json()
    assert body['candidates'] == [] and body['latest_run'] is None
    assert body['route_preflight']['availability'] == 'unconfigured'
    assert body['egress_preflight']['source_count'] == 0
    assert body['egress_preflight']['reason'] == 'content_sources_missing'
    assert client.get('/api/creation-tasks/missing/topics', headers=headers).status_code == 404
    missing = client.post(base + '/missing/confirm', json=confirm, headers=headers)
    assert missing.status_code == 404 and missing.json()['detail'] == 'creation task topic not found'
    assert client.post(base + '/missing/confirm', json={'title': ''}, headers=headers).status_code == 422
    # Never generate on the seeded task: even a preflight failure persists a run.
    generation = client.post('/api/creation-tasks/missing/topics/generate', json={}, headers=headers)
    assert generation.status_code == 404 and generation.json()['detail'] == 'creation task not found'
    assert repository.topic_records.latest_topic_generation_run('local', task.id) is None
    assert repository.job_records.list_job_runs('local', task.id) == []
    assert facts() == before_facts
    assert tree() == before_tree
    assert violations == []
    client.close()
