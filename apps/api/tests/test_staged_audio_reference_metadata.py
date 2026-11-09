"""Persisted staged-input references protect any task in the same workspace."""
import sqlite3
import subprocess
import threading
import wave

import httpx
import pytest
from PIL import Image

from app.creation.contracts import CreationTask, TaskEvent
from app.creation.plan_contracts import CreationPlan
from app.local_members import LocalMemberService
from app.production import ProductionRepository


def test_staged_audio_reference_is_exact_workspace_wide_and_read_only(tmp_path, monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append('execution')
        pytest.fail('reference metadata must not upload, decode, start a worker or access the network')

    for owner, name in ((subprocess, 'Popen'), (threading.Thread, 'start'), (wave, 'open'),
            (Image, 'open'), (Image, 'new'), (Image.Image, 'save'),
            (httpx.HTTPTransport, 'handle_request'), (httpx.AsyncHTTPTransport, 'handle_async_request')):
        monkeypatch.setattr(owner, name, forbidden)
    repository = ProductionRepository(tmp_path / 'studio.sqlite3', tmp_path / 'library')
    member, _ = LocalMemberService(repository.database_path).whoami(None)
    tasks = {}
    for workspace_id, identity in (('workspace-a', 'first'), ('workspace-a', 'second'), ('workspace-b', 'foreign')):
        plan = CreationPlan('plan-' + identity, workspace_id, 'reference metadata', 'reference metadata',
            1, 'zh-CN', '9:16', 4, [], [], None, [], 0, 0, '', [], [], [], 'declared-reference')
        task = CreationTask('task-' + identity, workspace_id, plan.id, 'reference metadata', 'knowledge_explanation')
        _, tasks[identity] = repository.plan_confirmation.confirm_creation_plan(plan, task,
            verified_owner_user_id=member.member_id)
    relative = 'task-media-inputs/declared-owner/fixed.wav'

    def event(identity, task, path, event_type='task_media_submitted'):
        # A historical reference declaration, not an upload or successful media submission.
        repository.execution_records.append_task_event(TaskEvent(identity, task.workspace_id, task.id,
            None, event_type, {'arguments': {'audio': {'staged_input': path}}}))

    def reopen():
        return ProductionRepository(repository.database_path, repository.video_library_path)

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

    reader = reopen().execution_records
    before = snapshot()
    assert reader.staged_audio_input_referenced('workspace-a', relative) is False
    assert snapshot() == before

    event('foreign-reference', tasks['foreign'], relative)
    event('different-relative', tasks['first'], relative + '.other')
    event('different-event-type', tasks['first'], relative, 'declared_unsubmitted_input')
    reader = reopen().execution_records
    before = snapshot()
    assert reader.staged_audio_input_referenced('workspace-a', relative) is False
    assert reader.staged_audio_input_referenced('workspace-b', relative) is True
    assert reader.staged_audio_input_referenced('workspace-a', relative + '.other') is True
    assert snapshot() == before

    # The original query intentionally protects a same-workspace reference from another task.
    event('same-workspace-other-task', tasks['second'], relative)
    reader = reopen().execution_records
    before = snapshot()
    assert repository.execution_records.get_task_event('workspace-a', 'same-workspace-other-task').task_id != tasks['first'].id
    assert reader.staged_audio_input_referenced('workspace-a', relative) is True
    assert reader.staged_audio_input_referenced('workspace-a', relative + '.missing') is False
    assert reader.staged_audio_input_referenced('unrelated-workspace', relative) is False
    assert snapshot() == before
    assert calls == []


def test_staged_audio_submission_cleans_only_new_unreferenced_files(tmp_path, monkeypatch):
    import hashlib
    from io import BytesIO
    from types import SimpleNamespace
    from fastapi import HTTPException
    import app.main as main
    from app.creation_execution import CreationExecutionActor
    from app.device_runtime import DeviceRuntime

    def forbidden(*args, **kwargs):
        pytest.fail('staged metadata forbids media, runtime execution and external transport')

    for owner, name in ((subprocess, 'Popen'), (threading.Thread, 'start'), (wave, 'open'),
            (Image, 'open'), (Image, 'new'), (Image.Image, 'save'),
            (httpx.HTTPTransport, 'handle_request'), (httpx.AsyncHTTPTransport, 'handle_async_request'),
            (DeviceRuntime, 'start'), (DeviceRuntime, 'run_once'), (DeviceRuntime, 'wake')):
        monkeypatch.setattr(owner, name, forbidden)
    repository = ProductionRepository(tmp_path / 'studio.sqlite3', tmp_path / 'library')
    member, _ = LocalMemberService(repository.database_path).whoami(None)
    plan = CreationPlan('submission-plan', 'workspace', 'metadata', 'metadata', 1, 'zh-CN', '9:16',
        4, [], [], None, [], 0, 0, '', [], [], [], 'declared-submission')
    _, task = repository.plan_confirmation.confirm_creation_plan(plan,
        CreationTask('submission-task', 'workspace', plan.id, 'metadata', 'knowledge_explanation'),
        verified_owner_user_id=member.member_id)
    actor = CreationExecutionActor('workspace', member.member_id)
    temporary = tmp_path / 'inputs'
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'artifact_store', main.ArtifactStore(tmp_path / 'artifacts'))
    monkeypatch.setattr(main, 'settings', SimpleNamespace(effective_temporary_root_path=temporary))
    monkeypatch.setattr(main, 'current_workspace_id', lambda _: 'workspace')
    monkeypatch.setattr(main, '_creation_task_or_404', repository.task_records.get_creation_task)
    monkeypatch.setattr(main, '_require_permit_actor', lambda *_: actor)
    budgets, captured, streams = [], [], []
    monkeypatch.setattr(main, '_device_operation_budget', lambda kind: budgets.append(kind) or object())
    mode = 'unreferenced'

    def submit(runtime, submitted_actor, task_id, operation, values, result_kind):
        staged = temporary / values['audio']['staged_input']
        captured.append((staged, values))
        assert staged.read_bytes() == streams[-1].getvalue()
        assert staged.name == hashlib.sha256(staged.read_bytes()).hexdigest() + '.wav'
        assert values['audio']['filename'] == 'declared.wav'
        assert values['audio']['size_bytes'] == len(staged.read_bytes())
        assert values['_draft'] is False and values['_media_kind'] == 'declared-kind'
        assert not list(temporary.rglob('*.part'))
        if mode == 'referenced':
            repository.execution_records.append_task_event(TaskEvent('declared-submitted', 'workspace', task.id,
                None, 'task_media_submitted', {'arguments': values}))
            return SimpleNamespace(id='declared-job')
        raise RuntimeError('declared submit failure')

    media = SimpleNamespace(submit=submit)
    runtime = SimpleNamespace(registrations={'creation_job': SimpleNamespace(owner=SimpleNamespace(task_media=media))})
    monkeypatch.setattr(main, '_creation_runtime', lambda: runtime)
    monkeypatch.setattr(main, '_media_result_receipt', lambda *_: {'job_id': 'declared-job', 'unencodable': object()})
    request = SimpleNamespace(query_params={'draft': 'true'}, url=SimpleNamespace(path='/declared-audio'))

    def receive(payload):
        stream = BytesIO(payload)
        streams.append(stream)
        return main._submit_audio_ingress(task.id, request, 'audio_import', 'audio_source',
            audio=SimpleNamespace(filename='../declared.wav', file=stream),
            arguments={'_draft': False, '_media_kind': 'declared-kind'})

    with monkeypatch.context() as denied:
        denied.setattr(main, '_require_permit_actor', lambda *_: CreationExecutionActor('workspace', 'foreign-member'))
        with pytest.raises(HTTPException) as rejected:
            receive(b'denied metadata')
        assert rejected.value.status_code == 403 and budgets == [] and not temporary.exists()
    with pytest.raises(RuntimeError, match='declared submit failure'):
        receive(b'unreferenced metadata')
    assert not captured[-1][0].exists()
    mode = 'referenced'
    with pytest.raises(TypeError, match='JSON serializable'):
        receive(b'referenced metadata')
    retained = captured[-1][0]
    assert retained.exists()
    assert repository.execution_records.staged_audio_input_referenced('workspace', captured[-1][1]['audio']['staged_input'])
    mode = 'unreferenced'
    real_query = repository.execution_records.staged_audio_input_referenced
    reference_error = ValueError('declared reference observation failure')
    reads = []

    def unreadable_reference(workspace_id, relative):
        reads.append((workspace_id, relative))
        assert real_query(workspace_id, relative) is False
        raise reference_error

    with monkeypatch.context() as unavailable:
        unavailable.setattr(repository.execution_records, 'staged_audio_input_referenced', unreadable_reference)
        with pytest.raises(ValueError) as failed:
            receive(b'unknown reference metadata')
        assert failed.value is reference_error
        existing = captured[-1][0]
        assert existing.exists() and len(reads) == 1
        with pytest.raises(RuntimeError, match='declared submit failure'):
            receive(b'unknown reference metadata')
        assert captured[-1][0] == existing and existing.exists() and len(reads) == 1
    assert all(not stream.closed for stream in streams)
    assert not list(temporary.rglob('*.part'))
    assert budgets == ['cpu_audio'] * 4
    with repository._connect() as conn:
        assert conn.execute('SELECT count(*) FROM job_runs').fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM task_events WHERE event_type='task_media_submitted'").fetchone()[0] == 1
