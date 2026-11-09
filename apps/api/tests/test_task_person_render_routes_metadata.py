"""Task person render metadata; no grant, generation or file response."""
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
from app.creation.person_stage_use_case import PersonStageUseCase
from app.schemas import CreationTaskPersonRenderRequest
from types import SimpleNamespace
from PIL import Image
from app.creation.plan_contracts import CreationPlan
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.media.picture_preview import PicturePreviewRenderer
from app.production import ProductionRepository
from app.source_repository import SourceRepository


def test_person_render_metadata_reads_and_rejects_generation_without_grant(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('person-render-execution-or-file')
        pytest.fail('person render metadata forbids runtime, grants, media and file responses')

    # Install before creating any fixture facts; the real factory may assemble only.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(PersonStageUseCase, 'task_or_missing', forbidden)
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
    plan = CreationPlan(id='person-plan', workspace_id='local', goal='Read person render metadata',
        name='Person metadata', target_count=1, language='zh-CN', aspect_ratio='16:9',
        duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary='', model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint='metadata-only')
    task = CreationTask(id='person-task', workspace_id='local', plan_id=plan.id,
        name='Person metadata', scenario='knowledge_explanation')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    repository = ProductionRepository(database, tmp_path / 'library')
    monkeypatch.setattr(main, 'production_repo', repository)
    current = repository.task_records.get_creation_task('local', task.id)
    assert current.owner_user_id == owner.member_id and current.visibility == 'private'
    case = main._person_stage_use_case()
    assert case.repository is repository and case.metadata.repository is repository
    payload = CreationTaskPersonRenderRequest(narration_segment_id='missing-segment',
        person_profile_id='missing-person', person_version='v1').model_dump(mode='json')

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
    base = '/api/creation-tasks/' + task.id + '/person-renders'
    listed = client.get(base, headers=headers)
    assert listed.status_code == 200 and listed.json() == {'items': [], 'total_count': 0}
    assert client.get('/api/creation-tasks/missing/person-renders', headers=headers).status_code == 404
    generated = client.post(base + '/generate', json=payload, headers=headers)
    assert generated.status_code == 409
    assert generated.json()['detail'] == '人物制作必须通过已确认任务的继续制作入口取得专属设备许可'
    missing = client.get(base + '/missing/media', headers=headers)
    assert missing.status_code == 404 and missing.json()['detail'] == 'person render not found'
    private = client.get(base + '/missing/media', headers={'X-Local-Member-Token': reader_token})
    assert private.status_code == 404 and private.json()['detail'] == 'creation task not found'
    assert facts() == before_facts
    assert tree() == before_tree
    assert repository.job_records.list_job_runs('local', task.id) == []
    assert violations == []
    client.close()
