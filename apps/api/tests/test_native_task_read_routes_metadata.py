"""Native receipt metadata only; no command, claim, artifact bytes or MIME read."""
from functools import partial
import subprocess
import wave

import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.creation.native_task_operations as native
from app.artifact_store import ArtifactStore
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.creation.contracts import ArtifactRecord, CreationTask, JobRun, TaskEvent
from app.creation.plan_contracts import CreationPlan
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.production import ProductionRepository


def test_native_task_reads_metadata_and_rejects_unregistered_artifact_without_writes(tmp_path, monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append('execution-or-file-read')
        pytest.fail('native read metadata forbids execution, claim, artifact bytes and MIME reads')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('execute', 'submit', 'task_root'):
        monkeypatch.setattr(native.NativeTaskOperations, name, forbidden)
    monkeypatch.setattr(ArtifactStore, 'describe', forbidden)
    monkeypatch.setattr(ArtifactStore, 'resolve_file', forbidden)
    monkeypatch.setattr(native, 'actual_mime', forbidden)
    monkeypatch.setattr(main, '_creation_runtime', forbidden)
    database = tmp_path / 'native.sqlite'
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    monkeypatch.setenv('PATH', str(binaries))
    settings = Settings(database_path=str(database), video_inbox_path=str(tmp_path / 'library'),
        video_output_path=str(tmp_path / 'output'), model_root_path=str(tmp_path / 'models'),
        temporary_root_path=str(tmp_path / 'temporary'), draft_output_path=str(tmp_path / 'drafts'),
        jianying_draft_root_path=str(tmp_path / 'jianying'), obsidian_vault_path=None, hermes_radar_path=None,
        local_whisper_python_path=None, local_whisper_cli_path=None, local_whisper_model_path=None,
        model_runtime_python_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: 'local')
    monkeypatch.setattr(main, 'LEGACY_WORKSPACE_ID', 'local')
    monkeypatch.setattr(main, 'device_runtime', None)
    repository = ProductionRepository(database, tmp_path / 'library')
    members = LocalMemberService(database)
    owner, token = members.whoami(None)
    _, other_token = members.join(members.create_invite_code(), 'Other member')
    monkeypatch.setattr(main, 'local_members_service', members)
    plan = CreationPlan(id='plan', workspace_id='local', goal='Read original operation', name='Native history',
        target_count=1, language='zh-CN', aspect_ratio='16:9', duration_target_seconds=2,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary='', model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint='metadata-only')
    task = CreationTask(id='task', workspace_id='local', plan_id=plan.id,
        name='Native history', scenario='knowledge_explanation')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    # Declared queued facts only. No native submit/claim or command has occurred.
    job = repository.job_records.create_job_run(JobRun(id='native-job', workspace_id='local', task_id=task.id,
        kind='native_task_command', idempotency_key='native-metadata', input_digest='declared-metadata'))
    repository.execution_records.append_task_event(TaskEvent(id='event_' + job.id + '_start',
        workspace_id='local', task_id=task.id, job_id=job.id, event_type='native_operation_started',
        payload={'request_id': 'native-request', 'operation': 'native_task_command'}))
    repository.execution_records.create_artifact_record(ArtifactRecord(id='unfinished-artifact', workspace_id='local',
        task_id=task.id, kind='native_output', logical_ref='artifacts/not-published.txt', sha256='0' * 64,
        size_bytes=1, producer_job_id=job.id, input_refs=[]))
    repository = ProductionRepository(database, tmp_path / 'library')
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
    factory = main._native_task_operations_case
    monkeypatch.setattr(main, '_native_task_operations_case', partial(factory, budget=None))
    assert repository.job_records.get_job_run('local', job.id).status == 'queued'

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
    client = TestClient(main.app)  # No lifespan or runtime.
    headers = {'X-Local-Member-Token': token}
    url = '/api/creation-tasks/task/native-operations'
    listed = client.get(url, headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json() == {'task_id': task.id, 'items': [{
        'job_id': job.id, 'request_id': 'native-request', 'operation': 'native_task_command', 'status': 'queued',
        'process_state': 'not_started', 'release_state': 'not_acquired', 'artifacts': [],
        'failure_reason': None, 'repair_action': None, 'waiting_reason': None}],
        'offset': 0, 'total_count': 1, 'truncated': False}
    page = client.get(url, headers=headers, params={'offset': 1, 'limit': 1})
    assert page.status_code == 200 and page.json() == {
        'task_id': task.id, 'items': [], 'offset': 1, 'total_count': 1, 'truncated': False}
    assert client.get(url, headers=headers, params={'limit': 0}).status_code == 422
    other_headers = {'X-Local-Member-Token': other_token}
    assert client.get(url, headers=other_headers).status_code == 403
    content_url = '/api/creation-tasks/task/native-artifacts/'
    assert client.get(content_url + 'missing/content', headers=headers).status_code == 409
    unfinished = client.get(content_url + 'unfinished-artifact/content', headers=headers)
    assert unfinished.status_code == 409 and unfinished.json()['detail'] == '原产物尚未完成登记'
    assert client.get(content_url + 'unfinished-artifact/content', headers=other_headers).status_code == 403
    assert facts() == before_facts
    assert tree() == before_tree
    assert not (tmp_path / 'native-task-workspaces').exists()
    assert calls == []
    client.close()
