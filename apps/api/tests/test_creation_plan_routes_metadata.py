"""Creation plan metadata; two explicit temporary visibility writes and segmented read-only checks."""
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
from app.creation.plan_confirmation_use_case import CreationPlanConfirmationUseCase
from app.creation.plan_preflight_use_case import CreationPlanPreflightUseCase
import json
from datetime import datetime
from app.schemas import CreationPlanPreviewRequest, CreationPlanConfirmRequest
from types import SimpleNamespace
from PIL import Image
from app.creation.plan_contracts import CreationPlan
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.media.picture_preview import PicturePreviewRenderer
from app.production import ProductionRepository
from app.source_repository import SourceRepository


def test_creation_plan_visibility_persists_and_other_requests_do_not_write(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('plan-confirmation-or-media')
        pytest.fail('plan metadata forbids new confirmation, model, runtime, grants and media')

    # Install before creating any fixture facts; the real factory may assemble only.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    confirm_seed = CreationPlanConfirmationUseCase.confirm_creation_plan
    monkeypatch.setattr(CreationPlanConfirmationUseCase, 'confirm_creation_plan', forbidden)
    monkeypatch.setattr(CreationPlanConfirmationUseCase, 'confirm_creation_plan_from_reply', forbidden)
    monkeypatch.setattr(CreationPlanPreflightUseCase, 'prepare', forbidden)
    monkeypatch.setattr(main, '_video_template_http_adapter', forbidden)
    monkeypatch.setattr(main, '_lipsync_provider', forbidden)
    monkeypatch.setattr(main, 'model_catalog', SimpleNamespace(list_statuses=forbidden, status=forbidden))
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
    plan = CreationPlan(id='plan-metadata', workspace_id='local', goal='Read confirmed plan metadata',
        name='Plan metadata', target_count=1, language='zh-CN', aspect_ratio='16:9',
        duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary='', model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint='metadata-only')
    task = CreationTask(id='plan-task', workspace_id='local', plan_id=plan.id,
        name='Plan metadata', scenario='knowledge_explanation')
    # The captured real method is used once for authorized seed, never by HTTP.
    confirm_seed(repository.plan_confirmation, plan, task, verified_owner_user_id=owner.member_id)
    repository = ProductionRepository(database, tmp_path / 'library')
    monkeypatch.setattr(main, 'production_repo', repository)
    preview = CreationPlanPreviewRequest(goal='元数据预检', template_id='missing-version').model_dump(mode='json')
    confirm = CreationPlanConfirmRequest(goal='两条目标', target_count=2,
        preflight_fingerprint='a' * 64).model_dump(mode='json')

    def facts():
        with repository._connect() as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: sorted((tuple(row) for row in connection.execute(
                'SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr) for name in tables}

    def tree():
        return {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob('*')}

    client = TestClient(main.app)  # No lifespan/runtime.
    headers = {'X-Local-Member-Token': token}
    url = '/api/creation-plans/' + plan.id

    def readonly_segment(visibility):
        before_facts, before_tree = facts(), tree()
        current = client.get(url, headers=headers)
        assert current.status_code == 200, current.text
        assert current.json()['id'] == plan.id and current.json()['visibility'] == visibility
        assert current.json()['creation_task']['id'] == task.id
        assert client.get('/api/creation-plans/missing', headers=headers).status_code == 404
        rejected = client.post('/api/creation-plans/preview', json=preview, headers=headers)
        assert rejected.status_code == 422 and rejected.json()['detail'] == '请选择明确的模板版本。'
        multiple = client.post('/api/creation-plans/confirm', json=confirm, headers=headers)
        assert multiple.status_code == 409
        assert multiple.json()['detail'] == '多条视频需要分别准备方案，请从新建视频的总目标入口分组创建；没有创建单条任务冒充多条。'
        assert client.post('/api/creation-plans/confirm', json={}, headers=headers).status_code == 422
        assert facts() == before_facts
        assert tree() == before_tree

    readonly_segment('private')
    with repository._connect() as connection:
        columns = [row[1] for row in connection.execute('PRAGMA table_info(creation_plans)')]
    for visibility in ('shared', 'private'):
        before = facts()
        saved = client.put(url + '/visibility', json={'visibility': visibility}, headers=headers)
        assert saved.status_code == 200 and saved.json()['visibility'] == visibility, saved.text
        after = facts()
        assert {k: v for k, v in before.items() if k != 'creation_plans'} == {
            k: v for k, v in after.items() if k != 'creation_plans'}
        assert len(before['creation_plans']) == len(after['creation_plans']) == 1
        old_row, new_row = dict(zip(columns, before['creation_plans'][0])), dict(zip(columns, after['creation_plans'][0]))
        assert new_row['visibility'] == visibility
        assert {k: v for k, v in old_row.items() if k not in {'payload', 'visibility', 'updated_at'}} == {
            k: v for k, v in new_row.items() if k not in {'payload', 'visibility', 'updated_at'}}
        old_payload, new_payload = json.loads(old_row['payload']), json.loads(new_row['payload'])
        assert new_payload['visibility'] == visibility
        assert {k: v for k, v in old_payload.items() if k not in {'visibility', 'updated_at'}} == {
            k: v for k, v in new_payload.items() if k not in {'visibility', 'updated_at'}}
        reopened = ProductionRepository(database, tmp_path / 'library')
        readback = reopened.plan_records.get_creation_plan('local', plan.id)
        assert readback.visibility == visibility and readback.updated_at == datetime.fromisoformat(saved.json()['updated_at'])
        assert facts() == after  # Reopening/readback introduces no additional changes.
        # Each authorized write ends here; start a fresh no-write interval.
        readonly_segment(visibility)
    assert repository.job_records.list_job_runs('local', task.id) == []
    assert violations == []
    client.close()
