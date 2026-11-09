"""Plain-text historical segment metadata and pre-submit refusal only."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import subprocess
import wave

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.artifact_store import ArtifactStore
from app.auth import SupabaseAuthContext
from app.automation_runtime import AutomationRuntime
from app.automation_service import AutomationService
from app.config import Settings
from app.conversation_repository import ConversationRepository
from app.creation.contracts import CreationTask
from app.creation.plan_contracts import CreationPlan
from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
from app.creation.segment_production_use_case import SegmentProductionUseCase
from app.creation.narration_stage_use_case import NarrationStageUseCase
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.production import ProductionRepository
from app.source_repository import SourceRepository


def test_task_segments_read_text_metadata_and_reject_before_execution_without_writes(tmp_path, monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append('execution-or-artifact-read')
        pytest.fail('segment metadata forbids execution, grants, media and artifact reads')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('start', 'drive', 'resume'):
        monkeypatch.setattr(AutomationRuntime, name, forbidden)
    monkeypatch.setattr(AutomationService, 'tick', forbidden)
    monkeypatch.setattr(SegmentProductionUseCase, 'execute', forbidden)
    monkeypatch.setattr(NarrationStageUseCase, 'prepare_segment', forbidden)
    monkeypatch.setattr(ArtifactStore, 'describe', forbidden)
    monkeypatch.setattr(ArtifactStore, 'resolve_file', forbidden)
    monkeypatch.setattr(main, '_creation_runtime', forbidden)
    monkeypatch.setattr(main.montage_renderer, 'render', forbidden)
    monkeypatch.setattr(main.narration_generator, 'generate_for_creation_task', forbidden)
    database = tmp_path / 'segments.sqlite'
    settings = Settings(database_path=str(database), video_inbox_path=str(tmp_path / 'library'),
        video_output_path=str(tmp_path / 'output'), model_root_path=str(tmp_path / 'models'),
        temporary_root_path=str(tmp_path / 'temporary'), draft_output_path=str(tmp_path / 'drafts'),
        jianying_draft_root_path=str(tmp_path / 'jianying'), obsidian_vault_path=None,
        hermes_radar_path=None, local_whisper_python_path=None, local_whisper_cli_path=None,
        local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    repository = ProductionRepository(database, tmp_path / 'library')
    sources = SourceRepository(database)
    conversations = ConversationRepository(database)
    members = LocalMemberService(database)
    owner, token = members.whoami(None)
    _, other_token = members.join(members.create_invite_code(), 'Other member')
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'source_repository', sources)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'automation_runtime', None)
    plan = CreationPlan(id='plan', workspace_id='local', goal='Read a plain text section', name='Segment metadata',
        target_count=1, language='zh-CN', aspect_ratio='16:9', duration_target_seconds=2,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary='', model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint='metadata-only')
    task = CreationTask(id='task', workspace_id='local', plan_id=plan.id,
        name='Segment metadata', scenario='knowledge_explanation')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    task = repository.task_records.get_creation_task('local', task.id)
    now = datetime.now(timezone.utc)
    script = CreationTaskScript(id='script', workspace_id='local', task_id=task.id,
        run_id='declared-history', topic_id='declared-topic', title='Historical text', language='zh-CN',
        sections=[CreationTaskScriptSection(id='section', title='正文', content='纯文本段落，没有音视频。',
            visual_prompt='metadata only', evidence_reference_ids=[])], selection_status='confirmed',
        created_at=now, updated_at=now, confirmed_at=now)
    # Authorized fixture SQL declares historical text in the original row format, not a generation job.
    task.confirmed_script_id = script.id
    task.stage = 'narration'
    task.updated_at = now
    with repository._connect() as connection:
        connection.execute('BEGIN')
        payload = repository.script_records._script_payload(connection, script)
        connection.execute('INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at) VALUES (?,?,?,?,?,?)',
            (script.id, 'local', task.id, script.run_id, payload, now.isoformat()))
        connection.execute('UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?',
            (json.dumps(asdict(task), default=str), now.isoformat(), 'local', task.id))
    # Reopen actual readers and complete pure automation/segment composition in the seed interval.
    repository = ProductionRepository(database, tmp_path / 'library')
    monkeypatch.setattr(main, 'production_repo', repository)
    persisted = repository.script_records.get_creation_task_script('local', script.id)
    assert persisted.sections[0].id == 'section' and persisted.selection_status == 'confirmed'
    assert repository.task_records.get_creation_task('local', task.id).confirmed_script_id == script.id
    automation = main._automation_runtime()
    assert automation.production is repository
    case = main._segment_production_use_case()
    assert isinstance(case, SegmentProductionUseCase) and case.repository is repository
    assert isinstance(case.narration_stage, NarrationStageUseCase) and case.source_repository is sources

    def facts():
        with repository._connect() as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: sorted((tuple(row) for row in connection.execute(
                'SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr) for name in tables}

    def tree():
        return {str(item.relative_to(tmp_path)): item.read_bytes() if item.is_file() else None
                for item in tmp_path.rglob('*')}

    before_facts, before_tree = facts(), tree()
    client = TestClient(main.app)  # No lifespan, driver or worker.
    headers = {'X-Local-Member-Token': token}
    root = '/api/creation-tasks/task'
    listed = client.get(root + '/segments', headers=headers)
    assert listed.status_code == 200, listed.text
    data = listed.json()
    assert data['total_count'] == 1 and data['offset'] == 0 and data['truncated'] is False
    assert data['available_video_count'] == 0 and data['revision_requests'] == {'items': [], 'total_count': 0, 'truncated': False}
    segment = data['segments'][0]
    assert segment['section_id'] == 'section' and segment['text'] == script.sections[0].content
    assert segment['audio'] is None and segment['video'] is None and segment['historical_video'] is None
    context = client.get(root + '/segments/section/context', headers=headers)
    assert context.status_code == 200 and context.json() == segment
    evidence = client.get(root + '/segments/section/sources', headers=headers)
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()['items'] == [] and evidence.json()['total_count'] == 0
    assert client.get(root + '/segments', headers=headers, params={'limit': 0}).status_code == 422
    other = client.get(root + '/segments', headers={'X-Local-Member-Token': other_token})
    assert other.status_code == 403 and other.json()['detail'] == '当前成员不能访问此作品段落'
    missing_request = client.get(root + '/segment-requests/missing', headers=headers)
    assert missing_request.status_code == 404 and missing_request.json()['detail'] == '原修改请求不存在'
    missing_video = client.get(root + '/segments/section/versions/missing/video', headers=headers)
    assert missing_video.status_code == 404 and missing_video.json()['detail'] == '此段落版本的视频不可读取'
    revision = client.post(root + '/segments/section/revision', headers=headers, json={})
    assert revision.status_code == 409 and revision.json()['detail'] == '修改判断必须绑定正在运行的可信助手回合'
    resume = client.post(root + '/segment-requests/missing/resume', headers={'X-Local-Member-Token': 'invalid'})
    assert resume.status_code == 401 and resume.json()['detail'] == '成员凭据无效'
    assert facts() == before_facts
    assert tree() == before_tree
    assert repository.job_records.list_job_runs('local', task.id) == []
    assert calls == []
    client.close()
