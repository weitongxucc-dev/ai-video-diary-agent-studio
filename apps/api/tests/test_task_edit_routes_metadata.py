"""Complete edit HTTP refusal/read metadata only; never begin a request or perform an edit."""
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
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.conversation_repository import ConversationRepository
from app.creation.contracts import CreationTask
from app.creation.editing.command_preview_use_case import CommandPreviewUseCase
from app.creation.editing.compound_use_case import CompoundEditUseCase
from app.creation.editing.exact_preview_use_case import ExactPreviewUseCase
from app.creation.editing.metadata_use_case import MetadataEditUseCase
from app.creation.plan_contracts import CreationPlan
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.media.picture_preview import PicturePreviewRenderer
from app.production import ProductionRepository
from app.creation.execution_records import ExecutionRecordStore
from app.schemas import CreationTaskExactEditRequest
from app.source_repository import SourceRepository
from app.sources import ObsidianVault


def test_task_edit_reads_and_pre_admission_refusals_never_claim_or_edit(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('edit-claim-save-preview-or-execution')
        pytest.fail('edit metadata forbids request claims, previews, edits, runtime and media')

    # Install before all temporary schemas/member/declared-plan preparation; read factories remain real.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    for name in ('apply', 'submit_preview', 'preview', 'claim_request'):
        monkeypatch.setattr(ExactPreviewUseCase, name, forbidden)
    for name in ('begin', 'save'):
        monkeypatch.setattr(MetadataEditUseCase, name, forbidden)
    monkeypatch.setattr(CommandPreviewUseCase, 'preview', forbidden)
    monkeypatch.setattr(CompoundEditUseCase, 'preview_selection', forbidden)
    monkeypatch.setattr(ExecutionRecordStore, 'claim_edit_request', forbidden)
    monkeypatch.setattr(ExecutionRecordStore, 'save_edit_request_result', forbidden)
    monkeypatch.setattr(ConversationRepository, 'create', forbidden)
    monkeypatch.setattr(ConversationRepository, 'create_turn_idempotent', forbidden)
    monkeypatch.setattr(ArtifactStore, 'resolve_file', forbidden)
    for name in ('render_montage', 'render_picture_input'):
        monkeypatch.setattr(PicturePreviewRenderer, name, forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('_creation_runtime', '_build_device_components', '_start_device_runtime',
                 '_device_operation_budget', '_workspace_settings_for', '_claim_edit_request',
                 '_save_edit_request_result', '_wake_assistant_continuations', 'run_task_turn',
                 'run_plan_turn', 'start_assistant_continuation_driver'):
        monkeypatch.setattr(main, name, forbidden)
    monkeypatch.setattr(main, 'montage_renderer', SimpleNamespace(render=forbidden))
    monkeypatch.setattr(main, 'narration_generator', SimpleNamespace(generate_for_creation_task=forbidden))
    provider = SimpleNamespace(ready=False, model_id='metadata-only', message='isolated',
                               generate=forbidden, render=forbidden)
    monkeypatch.setattr(main, 'latentsync_provider', provider)
    monkeypatch.setattr(main, 'avatar_lipsync_provider', provider)
    monkeypatch.setattr(main, 'video_job_runner', SimpleNamespace(run=forbidden))
    database = tmp_path / 'edits.sqlite'
    settings = Settings(database_path=str(database),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'outputs'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'automation_runtime', None)
    monkeypatch.setattr(main, 'creation_execution', None)
    repository = ProductionRepository(database, tmp_path / 'library')
    conversations = ConversationRepository(database)
    members = LocalMemberService(database)
    owner, owner_token = members.whoami(None)
    other_member, other_token = members.join(members.create_invite_code(), 'Other edit reader')
    assert owner.member_id != other_member.member_id
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'source_repository', SourceRepository(database))
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
    monkeypatch.setattr(main, 'vault', ObsidianVault(None))

    def seed_task(task_id, workspace='local'):
        plan = CreationPlan(id='plan-' + task_id, workspace_id=workspace, goal='Read edit metadata',
            name='Edit metadata', target_count=1, language='zh-CN', aspect_ratio='16:9',
            duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
            pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
            material_rule_summary='', model_snapshot=[], confirmation_points=[], blockers=[],
            preflight_fingerprint='metadata-only')
        task = CreationTask(id=task_id, workspace_id=workspace, plan_id=plan.id,
            name='Edit metadata', scenario='knowledge_explanation')
        repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
        return task

    task = seed_task('edit-task')
    other_task = seed_task('edit-other')
    outside_task = seed_task('edit-outside', 'outside')
    repository.task_records.set_creation_task_visibility('local', task.id, 'shared')
    repository = ProductionRepository(database, tmp_path / 'library')
    monkeypatch.setattr(main, 'production_repo', repository)
    current = repository.task_records.get_creation_task('local', task.id)
    assert current.owner_user_id == owner.member_id and current.visibility == 'shared'
    assert current.current_timeline_version_id is None and current.montage_plan_id is None
    # Pure actual factory assembly is allowed; receipt HTTP must still call its real reader.
    case = main._exact_preview_use_case()
    assert case.repository is repository and case.metadata.repository is repository
    assert case.conversations is conversations
    receipt_id = 'e1c15af1-3df7-4c3e-9b15-dedb2394213d'
    action = CreationTaskExactEditRequest(operation_type='SetClipLock', clip_id='declared-clip', locked=True,
        request_id=UUID(receipt_id), expected_timeline_version_id=None,
        expected_timeline_sequence=0).model_dump(mode='json', exclude_unset=True)

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

    before = snapshot()  # All schema/member/declared-task preparation is finished; no receipt was claimed.
    client = TestClient(main.app)  # No lifespan or runtime.
    headers = {'X-Local-Member-Token': owner_token}
    base = '/api/creation-tasks/' + task.id
    controls = client.get(base + '/edit-controls', headers=headers)
    assert controls.status_code == 200, controls.text
    assert controls.json() == {'task_id': task.id, 'timeline_version_id': None, 'timeline_sequence': 0,
                               'can_undo': False, 'can_redo': False, 'proposals': []}
    missing_proposal = client.get(base + '/edits/missing-proposal', headers=headers)
    assert missing_proposal.status_code == 404
    missing_result = client.get(base + '/edits/missing-proposal/result', headers=headers)
    assert missing_result.status_code == 404 and missing_result.json()['detail'] == '编辑提案不存在'
    receipt = client.get(base + '/assistant/edit-requests/' + receipt_id, headers=headers)
    assert receipt.status_code == 404 and receipt.json()['detail'] == '编辑请求不存在'
    for inaccessible in (other_task.id, outside_task.id):
        scoped = client.get('/api/creation-tasks/' + inaccessible + '/assistant/edit-requests/' + receipt_id,
                            headers=headers)
        assert scoped.status_code == 404
    assert client.get('/api/creation-tasks/missing/edit-controls', headers=headers).status_code == 404
    initialized = client.post(base + '/timeline/initialize', headers=headers)
    assert initialized.status_code == 409 and initialized.json()['detail'] == '当前任务尚无可编辑的故事板'
    confirm_missing = client.post(base + '/edits/missing-proposal/confirm', headers=headers)
    assert confirm_missing.status_code == 404
    # Never post an existing task as owner. A stale apply may claim before rejecting.
    # Denials below must precede even constructing the application factory.
    with monkeypatch.context() as gate:
        gate.setattr(main, '_exact_preview_use_case', forbidden)
        for suffix in ('/edits/exact/preview', '/edits/exact'):
            denied = client.post(base + suffix, json=action,
                                headers={'X-Local-Member-Token': other_token})
            assert denied.status_code == 403 and denied.json()['detail'] == '当前成员不能修改此任务'
            invalid = client.post(base + suffix, json=action,
                                 headers={'X-Local-Member-Token': 'invalid-token'})
            assert invalid.status_code == 401
            missing = client.post('/api/creation-tasks/missing' + suffix, json=action, headers=headers)
            assert missing.status_code == 404
    assert snapshot() == before
    assert repository.job_records.list_job_runs('local', task.id) == []
    assert repository.edit_records.list_edit_operations('local', task.id) == []
    assert repository.timeline_records.list_timeline_versions('local', task.id) == []
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM task_events WHERE event_type LIKE 'edit_request%'").fetchone()[0] == 0
    assert violations == []
    client.close()
