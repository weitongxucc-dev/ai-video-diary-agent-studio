"""Device-session HTTP metadata only: real SQLite/scheduler, no device execution."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.creation.execution_control import CreationExecutionControl
from app.creation_execution import CreationJobUnitExecutor
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from test_task_media_api import _result_read_guards
from test_task_media_grants import case


def test_device_session_queue_cancel_metadata(tmp_path, monkeypatch):
    _result_read_guards(monkeypatch)
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append('execution')
        pytest.fail('device-session metadata forbids grants and worker execution')

    # Before SQLite/owner/runtime seed; construction and metadata submission are allowed.
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    monkeypatch.setattr(CreationJobUnitExecutor, 'execute', forbidden)
    repo, owner_actor, state, _, build, media = case(tmp_path)
    members = LocalMemberService(repo.database_path)
    member = members.add_member('session metadata member')
    actor = replace(owner_actor, member_id=member.member_id)
    runtime = build(media)
    control = CreationExecutionControl(repo, None, None, None)
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: 'local')
    monkeypatch.setattr(main, '_require_permit_actor', lambda *args: actor)
    monkeypatch.setattr(main, '_request_permit_actor', lambda *args: actor)
    monkeypatch.setattr(main, '_creation_runtime', lambda: runtime)
    monkeypatch.setattr(main, '_creation_execution_control', lambda: control)
    client = TestClient(main.app)  # No lifespan/startup.

    def dump():
        with repo._connect() as conn:
            return list(conn.iterdump())

    try:
        job = media.submit(runtime, actor, 'task', 'audio_listen', {'position': 1}, 'audio_listen')
        foreign_job = media.submit(runtime, owner_actor, 'task', 'audio_listen', {'position': 2}, 'audio_listen')
        with repo._connect() as conn:
            request_id = conn.execute('SELECT id FROM device_work_requests WHERE owner_id=?', (job.id,)).fetchone()[0]
            foreign_request = conn.execute('SELECT id FROM device_work_requests WHERE owner_id=?', (foreign_job.id,)).fetchone()[0]
        heartbeat = client.post('/api/device/session/heartbeat', json={
            'session_id': 'metadata-session', 'playing': True, 'interaction': True})
        assert heartbeat.status_code == 200, heartbeat.text
        assert heartbeat.json() == {'session_id': 'metadata-session', 'workspace_id': 'local', 'member_id': actor.member_id}
        with repo._connect() as conn:
            saved = dict(conn.execute('SELECT * FROM device_session_activity WHERE session_id=?', ('metadata-session',)).fetchone())
        assert (saved['workspace_id'], saved['member_id'], saved['playing'], saved['recording'], saved['editing']) == (
            'local', actor.member_id, 1, 0, 0)
        assert saved['last_heartbeat_at_utc'] and saved['last_interaction_at_utc']
        before_read = dump()
        queue = client.get('/api/device/queue')
        assert queue.status_code == 200, queue.text
        assert len(queue.json()['mine']) == 1 and queue.json()['mine'][0]['id'] == request_id
        assert queue.json()['device'] == {'running': 0, 'waiting': 2}
        assert foreign_request not in queue.text and foreign_job.id not in queue.text
        assert dump() == before_read
        for target, status in [('missing-request', 404), (foreign_request, 403)]:
            before_rejected = dump()
            response = client.post(f'/api/device/queue/{target}/cancel')
            assert response.status_code == status, response.text
            assert dump() == before_rejected
        with monkeypatch.context() as missing_actor:
            missing_actor.setattr(main, '_request_permit_actor', lambda *args: None)
            missing_actor.setattr(main, '_creation_runtime', forbidden)
            before_rejected = dump()
            assert client.post(f'/api/device/queue/{request_id}/cancel').status_code == 403
            assert dump() == before_rejected

        # The scheduler rejects reads/heartbeat after roster revocation, but exact-owner cancellation remains allowed.
        members.remove_member(actor.member_id)
        before_revoked = dump()
        assert client.get('/api/device/queue').status_code == 403
        assert client.post('/api/device/session/heartbeat', json={'session_id': 'metadata-session'}).status_code == 403
        assert dump() == before_revoked
        cancelled = client.post(f'/api/device/queue/{request_id}/cancel')
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json() == {'id': request_id, 'state': 'cancelled', 'wait_reason': 'user_cancelled'}
        stored = repo.job_records.get_job_run('local', job.id)
        assert stored.status == 'cancelled' and stored.failure_reason == 'user_cancelled'
        with repo._connect() as conn:
            row = conn.execute('SELECT state,wait_reason FROM device_work_requests WHERE id=?', (request_id,)).fetchone()
        assert tuple(row) == ('cancelled', 'user_cancelled')
        before_repeat = dump()
        repeated = client.post(f'/api/device/queue/{request_id}/cancel')
        assert repeated.status_code == 200 and repeated.json() == cancelled.json()
        assert dump() == before_repeat
        assert repo.job_records.get_job_run('local', foreign_job.id).status == 'queued'
        assert state['calls'] == 0 and forbidden_calls == []
    finally:
        runtime.stop(0)
