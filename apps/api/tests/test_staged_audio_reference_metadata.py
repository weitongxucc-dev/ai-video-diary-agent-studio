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
