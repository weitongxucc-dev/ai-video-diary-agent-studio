"""Legacy montage control refusal metadata; no owner edit submission or media."""
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
from app.creation.editing.exact_preview_use_case import ExactPreviewUseCase
from app.schemas import CreationTaskMontageClipUpdateRequest, CreationTaskSubtitleCueUpdateRequest
from types import SimpleNamespace
from PIL import Image
from app.creation.plan_contracts import CreationPlan
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.media.picture_preview import PicturePreviewRenderer
from app.production import ProductionRepository
from app.source_repository import SourceRepository


def test_montage_control_rejections_use_real_access_without_writes(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('edit-submission-or-execution')
        pytest.fail('montage metadata forbids owner editing, runtime, grants and media')

    # Install before creating any fixture facts; the real factory may assemble only.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(ExactPreviewUseCase, 'apply', forbidden)
    monkeypatch.setattr(main, '_exact_preview_use_case', forbidden)
    monkeypatch.setattr(main, '_wake_assistant_continuations', forbidden)
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
    plan = CreationPlan(id='control-plan', workspace_id='local', goal='Read legacy montage controls',
        name='Montage control metadata', target_count=1, language='zh-CN', aspect_ratio='16:9',
        duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary='', model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint='metadata-only')
    task = CreationTask(id='control-task', workspace_id='local', plan_id=plan.id,
        name='Montage control metadata', scenario='knowledge_explanation')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    repository.task_records.set_creation_task_visibility('local', task.id, 'shared')
    repository = ProductionRepository(database, tmp_path / 'library')
    monkeypatch.setattr(main, 'production_repo', repository)
    clip_payload = CreationTaskMontageClipUpdateRequest(position=0).model_dump(mode='json', exclude_unset=True)
    cue_payload = CreationTaskSubtitleCueUpdateRequest(text='新字幕').model_dump(mode='json')

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
    base = '/api/creation-tasks/' + task.id + '/montage'
    missing = client.get(base + '/clips/missing/candidates', headers=headers)
    assert missing.status_code == 404 and missing.json()['detail'] == 'creation task montage clip not found'
    assert client.get('/api/creation-tasks/missing/montage/clips/missing/candidates', headers=headers).status_code == 404
    clip_url = base + '/clips/missing'
    for body in ({'fit_mode': 'fit'}, {'split_time_seconds': 0}, {'playback_rate': 1}):
        rejected = client.patch(clip_url, json=body, headers=headers)
        assert rejected.status_code == 409 and rejected.json()['detail'] == '请通过精确编辑预览确认修改。'
    empty = client.patch(clip_url, json={}, headers=headers)
    assert empty.status_code == 409 and empty.json()['detail'] == 'legacy montage PATCH accepts one typed edit at a time'
    assert client.patch(clip_url, json={'playback_rate': 0}, headers=headers).status_code == 422
    # The original save helper forwards to exact edit; these real identity gates
    # must reject before its use-case constructor or submit is reached.
    for suffix, body in (('/clips/missing', clip_payload), ('/cues/missing', cue_payload)):
        denied = client.patch(base + suffix, json=body, headers={'X-Local-Member-Token': reader_token})
        assert denied.status_code == 403 and denied.json()['detail'] == '当前成员不能修改此任务'
        assert client.patch(base + suffix, json=body,
            headers={'X-Local-Member-Token': 'invalid-token'}).status_code == 401
        assert client.patch('/api/creation-tasks/missing/montage' + suffix,
            json=body, headers=headers).status_code == 404
    # Never submit an owner command that passes the legacy admission gates.
    render = client.post(base + '/render', json={}, headers=headers)
    assert render.status_code == 409 and render.json()['detail'] == '请使用开始/继续制作，成片必须进入设备队列'
    assert facts() == before_facts
    assert tree() == before_tree
    assert repository.job_records.list_job_runs('local', task.id) == []
    assert violations == []
    client.close()
