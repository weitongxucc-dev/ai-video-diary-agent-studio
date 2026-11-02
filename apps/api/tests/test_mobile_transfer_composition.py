"""Main router + public production composition + formal identity + real SQLite."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import jwt
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from app.production import ProductionRepository
from test_companion_identity_http import linked as linked, USER, OTHER, WORKSPACE
from test_mobile_transfers import DATA, declaration


def client_for(linked, tmp_path, monkeypatch):
    root = tmp_path / 'library'
    root.mkdir()
    repository = ProductionRepository(tmp_path / 'app.sqlite3', root)
    repository.material_source_files.bind_material_ingestion(WORKSPACE, expected_directory=str(root))
    owners = replace(main._current_database_owners(), production=repository)
    app = FastAPI()
    app.middleware('http')(main.auth_boundary_middleware)
    app.router.routes.extend(route for route in main._prepare_runtime_routes(owners, main.auth_context)
        if route.path.startswith('/api/mobile-transfers'))
    monkeypatch.setenv('DEVICE_HOST_MIN_FREE_DISK_BYTES', '1')  # Explicit synthetic storage policy.
    return TestClient(app, headers=dict(linked.client.headers)), repository


def test_mobile_wire_registers_bytes_once_with_actual_jwt_owner(linked, tmp_path, monkeypatch):
    client, repository = client_for(linked, tmp_path, monkeypatch)
    request_id = str(uuid4())
    path = '/api/mobile-transfers/' + request_id
    declared = client.put(path, json=declaration())
    assert declared.status_code == 200, declared.text
    assert declared.json()['member_id'] == USER and declared.json()['workspace_id'] == WORKSPACE
    received = client.put(path + '/content', content=DATA, headers={'Content-Type': 'application/octet-stream'})
    assert received.status_code == 200 and received.json()['state'] == 'received'
    completed = client.post(path + '/complete')
    assert completed.status_code == 200, completed.text
    receipt = completed.json()
    assert receipt['state'] == 'completed' and receipt['asset_ref'].startswith('workspace://')
    asset = repository.get_asset(WORKSPACE, receipt['asset_id'])
    assert asset.owner_user_id == USER and asset.visibility == 'private'
    assert client.get(path).json() == client.post(path + '/complete').json() == receipt
    with repository._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM material_assets').fetchone()[0] == 1
    linked.permit = False
    assert client.get(path).status_code == 403


def test_other_verified_member_cannot_read_original_receipt(linked, tmp_path, monkeypatch):
    client, _ = client_for(linked, tmp_path, monkeypatch)
    path = '/api/mobile-transfers/' + str(uuid4())
    assert client.put(path, json=declaration()).status_code == 200
    client.headers['Authorization'] = 'Bearer ' + jwt.encode({'sub': OTHER,
        'iss': linked.settings.supabase_jwt_issuer_resolved, 'aud': linked.settings.supabase_jwt_audience,
        'exp': datetime.now(UTC) + timedelta(hours=1)}, linked.private, algorithm='RS256', headers={'kid': 'k1'})
    from test_companion_identity_http import DEVICE
    linked.permit = [{'device_id': DEVICE, 'workspace_id': WORKSPACE, 'member_id': OTHER, 'role': 'member'}]
    assert client.get(path).status_code == 404


def test_missing_disk_policy_blocks_only_receiver_and_can_be_prepared(linked, tmp_path, monkeypatch):
    client, _ = client_for(linked, tmp_path, monkeypatch)
    monkeypatch.delenv('DEVICE_HOST_MIN_FREE_DISK_BYTES')
    path = '/api/mobile-transfers/' + str(uuid4())
    failed = client.put(path, json=declaration())
    assert failed.status_code == 503 and failed.json()['detail']['code'] == 'storage_budget_unavailable'
    monkeypatch.setenv('DEVICE_HOST_MIN_FREE_DISK_BYTES', '1')
    assert client.put(path, json=declaration()).status_code == 200
