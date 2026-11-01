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


def test_task_budget_resource_projection_deduplicates_conflicts_and_leaves_rollback_to_caller(tmp_path, monkeypatch):
    import socket
    import threading
    from PIL import Image
    from app.creation.contracts import JobRun, TaskEvent
    from app.creation.task_budget_use_case import TaskBudgetUseCase

    def forbidden(*args, **kwargs):
        pytest.fail('resource receipt metadata forbids media, network and execution')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(threading.Thread, 'start', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    database = tmp_path / 'receipts.sqlite'
    repository = ProductionRepository(database, tmp_path / 'library')
    members = LocalMemberService(database)
    owner, _token = members.whoami(None)
    plan = CreationPlan(id='plan', workspace_id='local', goal='Read declared resource receipts', name='Receipt metadata',
        target_count=1, language='zh-CN', aspect_ratio='16:9', duration_target_seconds=2,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary='', model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint='metadata-only')
    task = CreationTask(id='task', workspace_id='local', plan_id=plan.id,
        name='Receipt metadata', scenario='knowledge_explanation')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    for job_id in ('receipt-job-a', 'receipt-job-b'):
        repository.job_records.create_job_run(JobRun(job_id, 'local', task.id, 'creation_task_segment_revision',
            f'metadata-key-{job_id}', f'metadata-digest-{job_id}'))
    shared = {'usage_id': 'shared-declaration', 'elapsed_seconds': 2.5, 'output_bytes': 100}
    # Declared receipts exercise projection only, without measurements or device grants.
    for event_id, job_id, kind, payload in (
        ('equal-a', 'receipt-job-a', 'creation_narration_unit_settled', shared),
        ('equal-b', 'receipt-job-b', 'task_execution_unit_settled', shared),
        ('unique', 'receipt-job-a', 'task_execution_unit_settled',
            {'usage_id': 'unique-declaration', 'elapsed_seconds': 1.5, 'output_bytes': 20}),
    ):
        repository.execution_records.append_task_event(TaskEvent(event_id, 'local', task.id, job_id, kind, payload))
    budget = TaskBudgetUseCase(repository)
    rows = repository.execution_records.task_budget_evidence_rows('local', task.id)
    assert len(rows['settled']) == 3 and all(isinstance(row, dict) for row in rows['settled'])
    assert rows['grants'] == [] and rows['waiting'] is None
    original = budget.project_resources('local', task.id)
    resource = original['resources']
    assert resource['receipt_count'] == 2 and resource['coverage'] == 'complete'
    assert resource['settled_execution_seconds'] == resource['total_execution_seconds'] == 4.0
    assert resource['settled_generated_bytes'] == resource['total_generated_bytes'] == 120
    assert resource['conflicting_usage_ids'] == [] and original['in_flight'] == []
    assert original['actual_cost']['status'] == 'pending_verification' and original['actual_cost']['amount'] is None
    with repository._connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        repository.execution_records.append_task_event(TaskEvent('pending', 'local', task.id, 'receipt-job-b',
            'task_execution_unit_started', {'usage_id': 'pending-declaration'}), connection=connection)
        partial = budget.project_resources('local', task.id, connection=connection)
        assert partial['resources']['coverage'] == 'partial' and len(partial['in_flight']) == 1
        for event_id, payload in (
            ('conflicting', {'usage_id': 'shared-declaration', 'elapsed_seconds': 9.0, 'output_bytes': 900}),
            ('repeated-original', shared),
        ):
            repository.execution_records.append_task_event(TaskEvent(event_id, 'local', task.id, 'receipt-job-b',
                'task_execution_unit_settled', payload), connection=connection)
        result = budget.project_resources('local', task.id, connection=connection)
        resource = result['resources']
        assert resource['coverage'] == 'conflict' and len(result['in_flight']) == 1
        assert resource['receipt_count'] == 2 and resource['conflicting_usage_ids'] == ['shared-declaration']
        assert resource['settled_execution_seconds'] == 1.5 and resource['settled_generated_bytes'] == 20
        assert resource['total_execution_seconds'] is None and resource['total_generated_bytes'] is None
        assert result['actual_cost']['status'] == 'pending_verification' and result['actual_cost']['amount'] is None
        assert connection.in_transaction and connection.execute('SELECT 1').fetchone()[0] == 1
        assert budget.project_resources('local', task.id) == original
        connection.rollback()
        assert not connection.in_transaction and connection.execute('SELECT 1').fetchone()[0] == 1
        assert budget.project_resources('local', task.id, connection=connection) == original
        assert not connection.in_transaction
    assert budget.project_resources('local', task.id) == original
