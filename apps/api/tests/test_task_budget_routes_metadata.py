"""Budget projection and invalid resume input only; no recovery or execution."""
import subprocess
import wave

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.auth import SupabaseAuthContext
from app.automation_runtime import AutomationRuntime
from app.automation_service import AutomationService
from app.config import Settings
from app.creation.contracts import CreationTask
from app.creation.plan_contracts import CreationPlan
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.production import ProductionRepository
from app.source_repository import SourceRepository


def test_task_budget_metadata_reads_and_rejects_invalid_resume_before_control_or_runtime(tmp_path, monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append('execution-or-resume')
        pytest.fail('budget metadata forbids control, runtime, automation drive, grant and media')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('start', 'drive', 'resume'):
        monkeypatch.setattr(AutomationRuntime, name, forbidden)
    monkeypatch.setattr(AutomationService, 'tick', forbidden)
    monkeypatch.setattr(main, '_creation_execution_control', forbidden)
    monkeypatch.setattr(main, '_creation_runtime', forbidden)
    monkeypatch.setattr(main.montage_renderer, 'render', forbidden)
    monkeypatch.setattr(main.narration_generator, 'generate_for_creation_task', forbidden)
    database = tmp_path / 'budget.sqlite'
    settings = Settings(database_path=str(database), video_inbox_path=str(tmp_path / 'library'),
        video_output_path=str(tmp_path / 'output'), model_root_path=str(tmp_path / 'models'),
        temporary_root_path=str(tmp_path / 'temporary'), draft_output_path=str(tmp_path / 'drafts'),
        jianying_draft_root_path=str(tmp_path / 'jianying'), obsidian_vault_path=None,
        hermes_radar_path=None, local_whisper_python_path=None, local_whisper_cli_path=None,
        local_whisper_model_path=None, model_runtime_python_path=None,
        supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    repository = ProductionRepository(database, tmp_path / 'library')
    sources = SourceRepository(database)
    members = LocalMemberService(database)
    owner, token = members.whoami(None)
    _, other_token = members.join(members.create_invite_code(), 'Other member')
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'source_repository', sources)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'automation_runtime', None)
    plan = CreationPlan(id='plan', workspace_id='local', goal='Read original resource facts', name='Budget metadata',
        target_count=1, language='zh-CN', aspect_ratio='16:9', duration_target_seconds=2,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary='', model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint='metadata-only')
    task = CreationTask(id='task', workspace_id='local', plan_id=plan.id,
        name='Budget metadata', scenario='knowledge_explanation')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    # Real migrations and pure automation/service composition belong to seed, before snapshots.
    automation = main._automation_runtime()
    assert isinstance(automation, AutomationRuntime)
    assert automation.production is repository and automation.sources is sources
    with repository._connect() as connection:
        assert connection.execute('SELECT count(*) FROM automation_runs').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM device_work_requests').fetchone()[0] == 0

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
    client = TestClient(main.app)  # No lifespan; existing guard checks no automation run.
    headers = {'X-Local-Member-Token': token}
    url = '/api/creation-tasks/task/budget'
    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['task_id'] == task.id and data['status'] == 'authorization_scope_unbound'
    assert data['scopes'] == [] and data['authorization_coverage'] == 'scope_unbound'
    assert data['resources']['receipt_count'] == 0 and data['resources']['coverage'] == 'complete'
    assert data['resources']['settled_execution_seconds'] == 0 and data['resources']['settled_generated_bytes'] == 0
    assert data['actual_cost']['status'] == 'pending_verification' and data['actual_cost']['amount'] is None
    assert data['in_flight'] == []
    missing = client.get('/api/creation-tasks/missing/budget', headers=headers)
    assert missing.status_code == 404 and missing.json()['detail'] == '任务预算不存在'
    assert client.get(url, headers={'X-Local-Member-Token': other_token}).status_code == 403
    rejected = client.post(url + '/resume', headers=headers, json={})
    assert rejected.status_code == 409, rejected.text
    assert 'request_id' in rejected.json()['detail'] and 'expected_job_id' in rejected.json()['detail']
    assert facts() == before_facts
    assert tree() == before_tree
    assert repository.job_records.list_job_runs('local', task.id) == []
    assert calls == []
    client.close()
