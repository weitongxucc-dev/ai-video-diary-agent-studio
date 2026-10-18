"""Same-account pairing over signed-JWT HTTP and real SQLite receipts."""
from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3
from uuid import uuid4

import httpx
import jwt
import pytest

import app.main as main
from app.auth import AuthPrincipal
from app.companion.identity import CompanionIdentity
from app.companion.pairing import CompanionPairingService
from app.device_accounts import DeviceAccountError, DeviceAccountService, DeviceCredential
from test_companion_identity_http import linked, USER, OTHER, WORKSPACE, DEVICE, PROOF


def create(linked):
    response = linked.client.post('/api/companion/pairings')
    assert response.status_code == 201, response.text
    return response.json()


def test_pairing_ticket_only_saves_digest_and_acceptance_creates_no_cloud_objects(linked):
    ticket = create(linked)
    assert set(ticket) == {'token', 'expires_at', 'workspace_id', 'device_id'}
    assert ticket['workspace_id'] == WORKSPACE and ticket['device_id'] == DEVICE
    with sqlite3.connect(main.production_repo.database_path) as conn:
        row = conn.execute('SELECT token_sha256,member_id,expires_at FROM companion_pairing_tickets WHERE token_sha256=?',
            (hashlib.sha256(ticket['token'].encode()).hexdigest(),)).fetchone()
    assert row[0] != ticket['token'] and row[1] == USER
    assert 590 <= (datetime.fromisoformat(row[2]) - datetime.now(UTC)).total_seconds() <= 600
    linked.calls.clear()
    del linked.client.headers['X-Workspace-Id']
    first = linked.client.post('/api/companion/pairings/accept', json={'token': ticket['token']})
    assert first.status_code == 200, first.text
    assert first.json() == {'target_workspace_id': WORKSPACE, 'device_id': DEVICE, 'member_id': USER}
    assert linked.client.post('/api/companion/pairings/accept', json={'token': ticket['token']}).json() == first.json()
    assert all(call[0] == 'POST' and call[1].endswith('/device_member_context') for call in linked.calls)
    linked.permit = False
    assert linked.client.post('/api/companion/pairings/accept', json={'token': ticket['token']}).status_code == 403


def test_pairing_rejects_another_signed_account_before_cloud_permission_lookup(linked):
    ticket = create(linked)
    del linked.client.headers['X-Workspace-Id']
    other = jwt.encode({'sub': OTHER, 'iss': linked.settings.supabase_jwt_issuer_resolved,
        'aud': linked.settings.supabase_jwt_audience, 'exp': datetime.now(UTC) + timedelta(hours=1)},
        linked.private, algorithm='RS256', headers={'kid': 'k1'})
    linked.client.headers['Authorization'] = 'Bearer ' + other
    linked.calls.clear()
    assert linked.client.post('/api/companion/pairings/accept', json={'token': ticket['token']}).status_code == 403
    assert linked.calls == []


def test_pairing_rejects_wrong_native_device_and_missing_jwt(linked, monkeypatch):
    ticket = create(linked)
    del linked.client.headers['X-Workspace-Id']
    monkeypatch.setenv('DEVICE_ID', str(uuid4()))
    assert linked.client.post('/api/companion/pairings/accept', json={'token': ticket['token']}).status_code == 403
    del linked.client.headers['Authorization']
    assert linked.client.post('/api/companion/pairings/accept', json={'token': ticket['token']}).status_code == 401


def test_unknown_permission_does_not_consume_ticket_and_can_retry_original_request(linked):
    ticket = create(linked)
    del linked.client.headers['X-Workspace-Id']
    linked.permit = httpx.ConnectError('offline')
    assert linked.client.post('/api/companion/pairings/accept', json={'token': ticket['token']}).status_code == 503
    linked.permit = True
    assert linked.client.post('/api/companion/pairings/accept', json={'token': ticket['token']}).status_code == 200


def test_revoke_is_issuer_scoped_idempotent_and_token_stays_out_of_url(linked):
    ticket = create(linked)
    response = linked.client.request('DELETE', '/api/companion/pairings', json={'token': ticket['token']})
    assert response.status_code == 200 and response.json() == {'revoked': True}
    assert linked.client.request('DELETE', '/api/companion/pairings', json={'token': ticket['token']}).json() == {'revoked': False}
    del linked.client.headers['X-Workspace-Id']
    assert linked.client.post('/api/companion/pairings/accept', json={'token': ticket['token']}).status_code == 409


def test_expiry_and_restart_preserve_original_accepted_receipt_with_current_permission(linked, tmp_path):
    now = [datetime.now(UTC)]
    path = tmp_path / 'pairings.sqlite3'
    service = CompanionPairingService(path, clock=lambda: now[0])
    identity = CompanionIdentity(DEVICE, WORKSPACE, USER, 'actual-account', 'member')
    principal = AuthPrincipal(USER, None, access_token=linked.token)
    accounts = DeviceAccountService(linked.settings, DeviceCredential(DEVICE, PROOF))
    used, unused = service.create(identity), service.create(identity)
    receipt = service.accept(principal, used.token, accounts)
    now[0] += timedelta(minutes=11)
    reopened = CompanionPairingService(path, clock=lambda: now[0])
    with pytest.raises(DeviceAccountError, match='expired'):
        reopened.accept(principal, unused.token, accounts)
    assert reopened.accept(principal, used.token, accounts) == receipt
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM companion_pairing_tickets WHERE accepted_at IS NOT NULL').fetchone()[0] == 1
    assert used.token.encode() not in path.read_bytes() and unused.token.encode() not in path.read_bytes()


def test_concurrent_acceptance_returns_one_original_binding(linked):
    ticket = create(linked)
    del linked.client.headers['X-Workspace-Id']
    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(lambda _: linked.client.post('/api/companion/pairings/accept',
            json={'token': ticket['token']}), range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    with sqlite3.connect(main.production_repo.database_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM companion_pairing_tickets WHERE token_sha256=? AND accepted_at IS NOT NULL',
            (hashlib.sha256(ticket['token'].encode()).hexdigest(),)).fetchone()[0] == 1
