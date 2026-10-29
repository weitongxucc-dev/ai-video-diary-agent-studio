"""Task graphic/visual HTTP reads and early refusals; no decoding or rendering."""
import subprocess
import wave

from PIL import Image
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.artifact_store import ArtifactStore
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.conversation_repository import ConversationRepository
from app.local_members import LocalMemberService
from app.media.picture_preview import PicturePreviewRenderer
from test_task_media_api import _result_read_guards
from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import CreationTask


def test_task_visual_metadata_reads_and_refusals_preserve_state(tmp_path, monkeypatch):
    _result_read_guards(monkeypatch)
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append('media-or-execution')
        pytest.fail('task visual metadata forbids image decoding/creation, rendering and runtime')

    # Before task/store/owner seed; actual use-case and renderer construction is permitted.
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(PicturePreviewRenderer, 'render_picture_input', forbidden)
    monkeypatch.setattr(PicturePreviewRenderer, 'render_montage', forbidden)
    monkeypatch.setattr(main, '_creation_runtime', forbidden)
    monkeypatch.setattr(main, 'transcribe_audio_bytes', forbidden)
    monkeypatch.setattr(main.narration_generator, 'generate_for_creation_task', forbidden)
    repo = ProductionRepository(tmp_path/'visual.sqlite3', tmp_path/'library')
    members = LocalMemberService(repo.database_path)
    member, token = members.whoami(None)
    plan = CreationPlan(id='visual-plan', workspace_id='local', goal='metadata', name='metadata', target_count=1,
        language='zh-CN', aspect_ratio='9:16', duration_target_seconds=1,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary='', model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint='declared metadata')
    task = CreationTask('task', 'local', plan.id, 'metadata visual task', 'knowledge_explanation',
                        owner_user_id=member.member_id)
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=member.member_id)
    temporary = tmp_path / 'temporary'
    temporary.mkdir()
    settings = Settings(database_path=str(repo.database_path), video_inbox_path=str(repo.video_library_path),
        video_output_path=str(tmp_path/'output'), temporary_root_path=str(temporary), model_root_path=str(tmp_path/'models'),
        obsidian_vault_path=None, hermes_radar_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path/'blobs'))
    monkeypatch.setattr(main, 'conversation_repo', ConversationRepository(repo.database_path))
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main, 'active_workspace_id', lambda: task.workspace_id)
    monkeypatch.setattr(main, 'device_runtime', None)
    client = TestClient(main.app, headers={'X-Local-Member-Token': token})  # No lifespan.
    base = '/api/creation-tasks/task'

    def database():
        with repo._connect() as conn:
            return list(conn.iterdump())

    def tree():
        return {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob('*')}

    before_db, before_tree = database(), tree()
    sources = client.get(base+'/graphics/sources')
    assert sources.status_code == 200 and sources.json() == [], sources.text
    missing_source = client.get(base+'/graphics/source/missing-artifact')
    assert missing_source.status_code == 404 and missing_source.json()['detail'] == '当前任务图片不存在'
    missing_visual = client.get(base+'/visual/artifacts/missing-artifact')
    assert missing_visual.status_code == 404 and missing_visual.json()['detail'] == '当前任务的画面预览不存在'
    empty_upload = client.post(base+'/graphics/import', files={'image': ('empty.png', b'', 'image/png')})
    assert empty_upload.status_code == 422 and empty_upload.json()['detail'] == '图片文件为空'
    # The actual importer stages then cleans an empty temporary file; only no-residue is claimed.
    assert list(temporary.iterdir()) == []
    stale = client.post(base+'/visual/preview', json={
        'base_version_id': 'stale-version', 'clip_ids': ['clip'], 'effects': {}})
    assert stale.status_code == 409 and stale.json()['detail'] == '时间线已经变化，请刷新后重新预览'
    assert database() == before_db
    assert tree() == before_tree
    assert forbidden_calls == []
