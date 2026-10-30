"""Read declared legacy edit results through the real persistent owner, without execution."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import subprocess
import threading
import wave

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from anyio._backends._asyncio import WorkerThread

import app.main as main
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.creation.contracts import EditIntent, EditProposal, EditOperation, JobRun, TaskEvent, TimelineVersion
from app.device_runtime import DeviceRuntime
from app.local_members import LocalMemberService
from app.production import ProductionRepository
from test_metadata_edit_use_case import metadata_state


def test_original_edit_result_reads_persisted_metadata_without_writes(tmp_path, monkeypatch):
    violations = []

    def forbidden(*args, **kwargs):
        violations.append('unexpected-execution')
        pytest.fail('receipt metadata must not construct, recover or start execution')

    # Protect the boundary before any declared facts are seeded.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    original_thread_start = threading.Thread.start

    def start_transport_portal(thread, *args, **kwargs):
        target = thread._target
        if isinstance(thread, WorkerThread) or (target is not None and
                target.__module__ == 'anyio.from_thread' and target.__name__ == 'run_blocking_portal'):
            return original_thread_start(thread, *args, **kwargs)
        return forbidden(thread, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, 'start', start_transport_portal)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    for name in ('_build_device_components', 'recover_interrupted_quality_checks',
                 '_device_resource_profile', '_wake_assistant_continuations', '_creation_runtime'):
        monkeypatch.setattr(main, name, forbidden)
    settings = Settings(database_path=str(tmp_path / 'editor.sqlite3'),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'output'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'automation_runtime', None)
    state = metadata_state(tmp_path)
    monkeypatch.setattr(main, 'artifact_store', state.store)
    monkeypatch.setattr(main, 'local_members_service', state.members)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: state.task.workspace_id)
    client = TestClient(main.app, headers={'X-Local-Member-Token': state.token})
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def proposal(identity):
        state.repo.edit_records.create_edit_intent(EditIntent('intent-' + identity, 'ws', state.task.id,
            'declared crop', 'montage', ['clip://clip'], None, 'ChangeCrop', {}, {}, 1, 'ready'))
        return state.repo.edit_records.create_edit_proposal(EditProposal(identity, 'ws', state.task.id,
            'intent-' + identity, 'declared crop', ['clip://clip'], {'operation_type': 'ChangeCrop',
                'payload': {'base_version_id': state.base.id, 'clip_id': 'clip'}}, [], {}, []))

    def declared_job(identity, target, created_at):
        job = state.repo.job_records.create_job_run(JobRun(identity, 'ws', state.task.id,
            'declared_edit_job', identity, identity.encode().hex().ljust(64, '0'), created_at=created_at))
        state.repo.execution_records.append_task_event(TaskEvent('event-' + identity, 'ws', state.task.id,
            job.id, 'declared_edit_request', {'edit_proposal_id': target.id}))
        return job

    def reopen(target):
        reopened = ProductionRepository(state.repo.database_path, state.repo.video_library_path)
        monkeypatch.setattr(main, 'production_repo', reopened)
        monkeypatch.setattr(main, 'local_members_service', LocalMemberService(reopened.database_path))
        return reopened, reopened.task_records.get_creation_task('ws', state.task.id), reopened.edit_records.get_edit_proposal('ws', target.id)

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

    def get(target):
        response = client.get(f'/api/creation-tasks/{state.task.id}/edits/{target.id}/result')
        assert response.status_code == 200, response.text
        return response.json()['result']

    target = proposal('original-proposal')
    reopened, task, target = reopen(target)
    before = snapshot()
    assert main._original_edit_result(task, target) is None
    assert get(target) is None
    assert snapshot() == before

    # These are historical metadata declarations, never scheduler submission or execution.
    first = declared_job('z-earliest-job', target, when)
    declared_job('a-later-job', target, when + timedelta(seconds=1))
    with state.repo._connect() as conn:
        for identity, offset in (('z-earliest-request', 0), ('a-later-request', 1)):
            stamp = (when + timedelta(seconds=offset)).isoformat()
            conn.execute("""INSERT INTO device_work_requests
                (id, workspace_id, member_id, owner_type, owner_id, input_digest, unit_key,
                 source_refs_json, work_class, policy, first_ready_at_utc, state,
                 resources_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (identity, 'ws', state.member.member_id, 'creation_job', first.id, first.input_digest,
                 identity, '[]', 'normal_make', 'asap', stamp, 'waiting', '{}', stamp, stamp))
    reopened, task, target = reopen(target)
    before = snapshot()
    queued = main._original_edit_result(task, target)
    assert queued.status_code == 202
    queued_body = json.loads(queued.body)
    assert queued_body == {'status': 'queued', 'job_id': first.id,
        'request_id': 'z-earliest-request', 'failure_reason': None}
    assert get(target) == queued_body
    assert snapshot() == before

    # The existing queued -> cancelled owner transition is preparation, not worker acceptance.
    state.repo.job_records.finish_job_run('ws', first.id, status='cancelled',
        failure_reason='declared cancellation', impact='existing metadata retained', repair_action='inspect original receipt')
    no_request = proposal('no-request-proposal')
    no_request_job = declared_job('no-request-job', no_request, when)
    reopened, task, target = reopen(target)
    before = snapshot()
    terminal = main._original_edit_result(task, target)
    assert terminal.status_code == 200
    terminal_body = json.loads(terminal.body)
    assert terminal_body == {**queued_body, 'status': 'cancelled', 'failure_reason': 'declared cancellation'}
    assert get(target) == terminal_body
    missing_request = main._original_edit_result(task, no_request)
    assert missing_request.status_code == 202
    assert json.loads(missing_request.body)['job_id'] == no_request_job.id
    assert json.loads(missing_request.body)['request_id'] is None
    assert snapshot() == before

    # A declared applied operation outranks the old Job, using its own persisted version.
    result_version = TimelineVersion('result-version', 'ws', task.id, state.base.id, 2,
        dict(state.base.snapshot), 'original-operation')
    state.repo.timeline_records.create_timeline_version(result_version)
    state.repo.edit_records.transition_edit_proposal('ws', task.id, target.id, status='confirmed')
    operation = state.repo.edit_records.create_edit_operation(EditOperation('original-operation', 'ws', task.id,
        target.id, 'ChangeCrop', {'clip_id': 'clip'}, state.base.id, result_version.id, 'applied',
        created_at=when, applied_at=when))
    reopened, task, target = reopen(target)
    monkeypatch.setattr(reopened.edit_records, 'find_proposal_job_result', forbidden)
    before = snapshot()
    applied = main._original_edit_result(task, target)
    assert applied.operation.id == operation.id
    assert applied.operation.result_version_id == result_version.id
    assert applied.montage.id == state.plan.id and applied.narration is None
    assert get(target) == applied.model_dump(mode='json')
    assert snapshot() == before
    assert violations == []
