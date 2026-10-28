"""Real signed JWTs and HTTP adapters; cloud RPC transport is controlled."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.auth import SupabaseAuthContext
from app.creation.contracts import CreationTask
from app.production import ProductionRepository
from test_device_invite_accept_entry import _auth_enabled_settings, _fake_jwks_client, _make_rsa_keypair
from test_reply_script_transfer import make_plan
from test_voice_sampling import pcm

USER, OTHER, WORKSPACE, DEFAULT_SPACE, DEVICE, INVITE = [str(uuid4()) for _ in range(6)]
PROOF = 'ab' * 32


@pytest.fixture
def linked(tmp_path, monkeypatch):
    private, public = _make_rsa_keypair()
    settings = _auth_enabled_settings(tmp_path)
    auth = SupabaseAuthContext(settings)
    auth._jwks_client = lambda: _fake_jwks_client(public)
    state = SimpleNamespace(calls=[], member=True, permit=True, rpc_error=None, role='member', onboarded=True)

    def get(url, **kwargs):
        state.calls.append(('GET', url, kwargs))
        if url.endswith('/workspace_members'):
            selected = kwargs['params'].get('workspace_id')
            result = ([{'workspace_id': WORKSPACE, 'role': 'owner'}] if state.member else []) if selected else [
                {'workspace_id': DEFAULT_SPACE, 'role': 'owner'}, {'workspace_id': WORKSPACE, 'role': 'owner'}]
        elif url.endswith('/onboarding_states'):
            result = [{'completed_at': '2026-10-18T00:00:00Z'}] if state.onboarded else []
        else:
            pytest.fail('unexpected cloud GET ' + url)
        return httpx.Response(200, json=result)

    def post(url, **kwargs):
        state.calls.append(('POST', url, kwargs))
        if url.endswith('/device_member_context'):
            if isinstance(state.permit, Exception):
                raise state.permit
            if state.permit is False:
                return httpx.Response(403, json={'code': '42501', 'message': 'binding inactive'})
            result = ([{'device_id': DEVICE, 'workspace_id': WORKSPACE, 'member_id': USER, 'role': state.role}]
                if state.permit is True else state.permit)
            return httpx.Response(200, json=result)
        if state.rpc_error:
            return httpx.Response(403, json={'code': state.rpc_error, 'message': 'device owner required'})
        if url.endswith('/device_create_member_space'):
            result = WORKSPACE
        elif url.endswith('/device_create_invite'):
            result = [{'invite_id': INVITE, 'invite_token': 'cd' * 32, 'expires_at': '2026-10-18T12:00:00Z'}]
        elif url.endswith('/device_revoke_invite'):
            result = True
        elif url.endswith('/device_accept_invite'):
            result = [{'workspace_id': WORKSPACE, 'member_count': 2, 'already_accepted': False}]
        else:
            pytest.fail('unexpected cloud POST ' + url)
        return httpx.Response(200, json=result)

    monkeypatch.setattr(main, 'auth_context', auth)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(httpx, 'get', get)
    monkeypatch.setattr(httpx, 'post', post)
    monkeypatch.setenv('DEVICE_ID', DEVICE)
    monkeypatch.setenv('DEVICE_PROOF', PROOF)
    token = jwt.encode({'sub': USER, 'iss': settings.supabase_jwt_issuer_resolved,
        'aud': settings.supabase_jwt_audience, 'exp': datetime.now(UTC) + timedelta(hours=1),
        'user_metadata': {'account': 'actual-account', 'role': 'owner'}}, private, algorithm='RS256', headers={'kid': 'k1'})
    state.client = TestClient(main.app, headers={'Authorization': 'Bearer ' + token, 'X-Workspace-Id': WORKSPACE})
    state.settings, state.private, state.token = settings, private, token
    return state


def test_session_uses_explicit_membership_native_device_and_current_permission(linked):
    response = linked.client.get('/api/companion/session')
    assert response.status_code == 200, response.text
    assert response.json() == {'device_id': DEVICE, 'workspace_id': WORKSPACE, 'member_id': USER, 'name': 'actual-account', 'role': 'member'}
    membership = next(call for call in linked.calls if call[1].endswith('/workspace_members'))
    assert membership[2]['params']['workspace_id'] == 'eq.' + WORKSPACE
    permit = next(call for call in linked.calls if call[1].endswith('/device_member_context'))
    assert permit[2]['json'] == {'target_device_id': DEVICE, 'device_proof': PROOF,
        'target_workspace_id': WORKSPACE}
    assert PROOF not in response.text and linked.token not in response.text


@pytest.mark.parametrize('failure,status', [('membership', 403), ('device', 403), ('unknown', 503), ('transport', 503),
    ('missing_workspace', 400), ('bad_workspace', 403), ('wrong_workspace', 403),
    ('local_header', 403), ('missing_jwt', 401), ('bad_jwt', 401)])
def test_session_does_not_fallback_on_unverified_binding(linked, failure, status):
    if failure == 'membership': linked.member = False
    elif failure == 'device': linked.permit = False
    elif failure == 'unknown': linked.permit = {'ready': True}
    elif failure == 'transport': linked.permit = httpx.ConnectError('offline')
    elif failure == 'missing_workspace': del linked.client.headers['X-Workspace-Id']
    elif failure == 'bad_workspace': linked.client.headers['X-Workspace-Id'] = 'local'
    elif failure == 'wrong_workspace': linked.client.headers['X-Workspace-Id'] = str(uuid4())
    elif failure == 'local_header': linked.client.headers['X-Local-Member-Token'] = 'development-token'
    elif failure == 'missing_jwt': del linked.client.headers['Authorization']
    elif failure == 'bad_jwt': linked.client.headers['Authorization'] = 'Bearer invalid'
    response = linked.client.get('/api/companion/session')
    assert response.status_code == status, response.text
    assert 'local' != response.json().get('workspace_id')


def test_revocation_is_rechecked_on_next_request(linked):
    assert linked.client.get('/api/companion/session').status_code == 200
    linked.permit = False
    assert linked.client.get('/api/companion/session').status_code == 403


def test_new_invitee_can_read_binding_but_not_product_data_before_initialization(linked):
    linked.onboarded = False
    assert linked.client.get('/api/companion/session').status_code == 200
    assert linked.client.get('/api/creation-tasks').status_code == 403


@pytest.mark.parametrize('field', ['device_id', 'workspace_id', 'member_id', 'role'])
def test_session_refuses_a_cloud_result_bound_to_other_identity(linked, field):
    row = {'device_id': DEVICE, 'workspace_id': WORKSPACE, 'member_id': USER, 'role': 'member'}
    row[field] = 'unknown' if field == 'role' else str(uuid4())
    linked.permit = [row]
    assert linked.client.get('/api/companion/session').status_code == 503


def test_invite_management_calls_existing_owner_rpcs_with_native_proof(linked):
    space = linked.client.post('/api/device-member-spaces', json={'name': '成员空间'})
    assert space.status_code == 201 and space.json() == {'workspace_id': WORKSPACE}
    created = linked.client.post('/api/device-invites', json={'target_workspace_id': WORKSPACE, 'valid_for_seconds': 120})
    assert created.status_code == 201, created.text
    assert created.json()['id'] == INVITE and created.json()['token'] == 'cd' * 32
    assert created.json()['target_workspace_id'] == WORKSPACE
    assert PROOF not in created.text
    deleted = linked.client.delete('/api/device-invites/' + INVITE)
    assert deleted.status_code == 200 and deleted.json() == {'id': INVITE, 'revoked': True}
    writes = [call for call in linked.calls if call[0] == 'POST' and not call[1].endswith('/device_member_context')]
    assert len(writes) == 3
    assert all(call[2]['json']['device_proof'] == PROOF for call in writes)
    assert all(call[2]['headers']['Authorization'] == 'Bearer ' + linked.token for call in writes)


@pytest.mark.parametrize('method,path,body', [('POST', '/api/device-member-spaces', {'name': '成员空间'}),
    ('POST', '/api/device-invites', {'target_workspace_id': WORKSPACE}), ('DELETE', '/api/device-invites/' + INVITE, None)])
def test_device_owner_denial_is_not_overridden_by_workspace_owner_or_jwt_metadata(linked, method, path, body):
    linked.rpc_error = '42501'
    response = linked.client.request(method, path, json=body)
    assert response.status_code == 403, response.text


def test_invite_accept_is_identity_only_then_selects_returned_workspace(linked):
    del linked.client.headers['X-Workspace-Id']
    response = linked.client.post('/api/device-invites/accept', json={'invite_token': 'cd' * 32})
    assert response.status_code == 200, response.text
    assert linked.calls and all(not call[1].endswith(('/workspace_members', '/device_member_context')) for call in linked.calls)
    linked.client.headers['X-Workspace-Id'] = response.json()['target_workspace_id']
    assert linked.client.get('/api/companion/session').json()['workspace_id'] == WORKSPACE


def test_mobile_sample_uses_original_member_task_text_and_receipt(linked, tmp_path, monkeypatch):
    root = tmp_path / 'library'
    root.mkdir()
    repository = ProductionRepository(tmp_path / 'sample.sqlite3', root)
    monkeypatch.setattr(main, 'production_repo', repository)
    created = linked.client.post('/api/media/voice-sampling-tasks', json={
        'request_id': str(uuid4()), 'display_name': '手机声音', 'language': 'zh', 'purpose': 'voice_profile'})
    assert created.status_code == 201, created.text
    task = created.json()
    body = {'request_id': str(uuid4()), 'text_version': task['text_version'], 'purpose': 'voice_profile', 'source_device': 'mobile'}
    path = '/api/media/voice-sampling-tasks/' + task['id'] + '/sample'
    first = linked.client.post(path, data=body, files={'audio': ('sample.wav', pcm(), 'audio/wav')})
    assert first.status_code == 200, first.text
    receipt = first.json()
    assert receipt['source_device'] == 'mobile' and receipt['status'] == 'received'
    assert receipt['owner_user_id'] == USER and receipt['workspace_id'] == WORKSPACE
    assert linked.client.post(path, data=body, files={'audio': ('sample.wav', pcm(), 'audio/wav')}).json() == receipt
    body['text_version'] = 'stale'
    assert linked.client.post(path, data=body, files={'audio': ('sample.wav', pcm(), 'audio/wav')}).status_code == 409
    assert len(list(root.rglob('*.wav'))) == 1


def test_local_debug_roster_cannot_submit_as_mobile(tmp_path, monkeypatch):
    from app.local_members import LocalMemberService
    members = LocalMemberService(tmp_path / 'local.sqlite3')
    _, token = members.whoami(None)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(replace(main.settings, supabase_url=None, supabase_publishable_key=None)))
    monkeypatch.setattr(main, 'local_members_service', members)
    client = TestClient(main.app, headers={'X-Local-Member-Token': token})
    response = client.post('/api/media/voice-sampling-tasks/missing/sample', data={
        'request_id': str(uuid4()), 'text_version': 'version', 'purpose': 'voice_profile', 'source_device': 'mobile'},
        files={'audio': ('sample.wav', pcm(), 'audio/wav')})
    assert response.status_code == 401


@pytest.mark.parametrize('path', ['/api/creation-tasks/task', '/api/creation-tasks/task/job-runs',
    '/api/creation-tasks/task/execution', '/api/creation-tasks/task/execution/preview',
    '/api/creation-tasks/task/output', '/api/creation-tasks/task/output/subtitles',
    '/api/creation-tasks/task/narration/audio', '/api/creation-tasks/task/narration/segments/segment/audio',
    '/api/creation-tasks/task/scripts', '/api/creation-tasks/task/person-renders/render/media',
    '/api/creation-tasks/task/covers/cover/image', '/api/creation-tasks/task/output/selected-subtitles?expected_version_id=v&mode=zh'])
def test_same_workspace_does_not_expose_another_members_private_task(linked, tmp_path, monkeypatch, path):
    repository = ProductionRepository(tmp_path / 'task.sqlite3', tmp_path / 'library')
    plan = replace(make_plan(), workspace_id=WORKSPACE, id='plan', creation_task_id=None)
    task = CreationTask(id='task', workspace_id=WORKSPACE, plan_id='plan', name='私人作品', scenario='private')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=OTHER)
    monkeypatch.setattr(main, 'production_repo', repository)
    def forbidden_read(*args, **kwargs):
        pytest.fail('private output must be rejected before resolving a file')
    monkeypatch.setattr(main, '_creation_task_output_path', forbidden_read)
    response = linked.client.get(path)
    assert response.status_code == 404, response.text


@pytest.mark.parametrize('owner', [USER, OTHER])
def test_current_member_can_read_own_private_or_explicitly_shared_task(linked, tmp_path, monkeypatch, owner):
    repository = ProductionRepository(tmp_path / 'readable.sqlite3', tmp_path / 'library')
    plan = replace(make_plan(), workspace_id=WORKSPACE, id='plan', creation_task_id=None)
    task = CreationTask(id='readable', workspace_id=WORKSPACE, plan_id=plan.id, name='可读任务', scenario='read')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner)
    if owner == OTHER:
        repository.set_creation_task_visibility(WORKSPACE, task.id, 'shared')
    monkeypatch.setattr(main, 'production_repo', repository)
    response = linked.client.get('/api/creation-tasks/readable/job-runs')
    assert response.status_code == 200 and response.json()['task_id'] == task.id
