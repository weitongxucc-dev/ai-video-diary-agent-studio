"""Task narration refusal metadata; no voice selection mutation or media response."""

from app.creation.narration_artifacts import NarrationArtifactStore
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
from app.schemas import CreationTaskNarrationVoiceSelectionRequest
from uuid import UUID
from app.creation.plan_contracts import CreationPlan
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.media.picture_preview import PicturePreviewRenderer
from app.production import ProductionRepository
from app.creation.narration_voice_selection import NarrationVoiceSelectionTransaction
from app.source_repository import SourceRepository


def test_task_narration_rejections_and_missing_audio_do_not_write(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('voice-selection-or-media')
        pytest.fail('narration metadata forbids voice mutation, runtime, grants and media')

    # Install before creating any fixture facts; the real factory may assemble only.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(CreationExecutionControl, 'select_narration_voice', forbidden)
    monkeypatch.setattr(NarrationVoiceSelectionTransaction, 'select_creation_task_narration_voice', forbidden)
    monkeypatch.setattr(NarrationArtifactStore, 'resolve_authorized_narration_audio_reference', forbidden)
    monkeypatch.setattr(main, '_creation_execution_control', forbidden)
    monkeypatch.setattr(main, 'FileResponse', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('render_montage', 'render_picture_input'):
        monkeypatch.setattr(PicturePreviewRenderer, name, forbidden)
    for name in ('_creation_runtime', 'run_task_turn', 'run_plan_turn', 'start_assistant_continuation_driver'):
        monkeypatch.setattr(main, name, forbidden)
    monkeypatch.setattr(main.montage_renderer, 'render', forbidden)
    monkeypatch.setattr(main.narration_generator, 'generate_for_creation_task', forbidden)
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
    plan = CreationPlan(id='narration-plan', workspace_id='local', goal='Read original voice receipt',
        name='Narration metadata', target_count=1, language='zh-CN', aspect_ratio='16:9',
        duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary='', model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint='metadata-only')
    task = CreationTask(id='narration-task', workspace_id='local', plan_id=plan.id,
        name='Narration metadata', scenario='knowledge_explanation')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    # A visible shared task reaches the write ownership gate (private would be 404).
    repository.task_records.set_creation_task_visibility('local', task.id, 'shared')
    repository = ProductionRepository(database, tmp_path / 'library')
    monkeypatch.setattr(main, 'production_repo', repository)
    assert repository.task_records.get_creation_task('local', task.id).owner_user_id == owner.member_id
    current_plan = repository.plan_records.get_creation_plan('local', plan.id)
    current_task = repository.task_records.get_creation_task('local', task.id)
    request_id = '7a049d25-a8e9-46a6-a32d-e5b14c442c68'
    # Validate the complete current DTO before exercising ownership refusal.
    payload = CreationTaskNarrationVoiceSelectionRequest(
        request_id=UUID(request_id), voice_profile_id='not-registered', voice_version='v1',
        expected_plan_updated_at=current_plan.updated_at,
        expected_task_updated_at=current_task.updated_at).model_dump(mode='json')

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
    headers = {'X-Local-Member-Token': token}
    client = TestClient(main.app)  # No lifespan/runtime.
    base = '/api/creation-tasks/' + task.id + '/narration'
    denied = client.post(base + '/voice', json=payload,
        headers={'X-Local-Member-Token': reader_token})
    assert denied.status_code == 403 and denied.json()['detail'] == '当前成员不能修改此任务'
    assert client.post(base + '/voice', json=payload,
        headers={'X-Local-Member-Token': 'invalid-token'}).status_code == 401
    assert client.post('/api/creation-tasks/missing/narration/voice',
        json=payload, headers=headers).status_code == 404
    # Never submit the schema-valid owner selection: that could mutate its receipt.
    missing = client.get(base + '/voice/requests/' + request_id, headers=headers)
    assert missing.status_code == 404
    assert missing.json()['detail'] == '没有找到当前成员的原声音采用请求。'
    assert client.get(base + '/voice/requests/not-a-uuid', headers=headers).status_code == 422
    legacy = client.post(base + '/generate', headers=headers)
    assert legacy.status_code == 409
    assert legacy.json()['detail'] == '请确认文案和制作要求后使用开始/继续制作，配音必须进入设备队列'
    audio = client.get(base + '/audio', headers=headers)
    assert audio.status_code == 404 and audio.json()['detail'] == 'creation task narration not found'
    segment = client.get(base + '/segments/missing/audio', headers=headers)
    assert segment.status_code == 404 and segment.json()['detail'] == 'creation task narration segment not found'
    # Original shared ingress performs the missing-task check before runtime.
    assert client.get('/api/creation-tasks/missing/narration/waveform', headers=headers).status_code == 404
    assert facts() == before_facts
    assert tree() == before_tree
    assert repository.job_records.list_job_runs('local', task.id) == []
    assert violations == []
    client.close()
