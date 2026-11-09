"""Borrowed auth GET transport: real keep-alive, isolated assembly and no models/media."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.auth import AuthDependencyUnavailableError, SupabaseAuthContext
from app.config import Settings
from test_host_model_installation import isolated_host as isolated_host


@pytest.fixture
def auth_server():
    state = SimpleNamespace(calls=[], members={}, completed=set())
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            url = urlsplit(self.path)
            params = {key: values[0] for key, values in parse_qs(url.query).items()}
            state.calls.append((self.client_address, url.path, dict(self.headers), params))
            user = params["user_id"].removeprefix("eq.")
            if url.path.endswith("/workspace_members"):
                workspace = state.members.get(user)
                selected = params.get("workspace_id", "eq." + (workspace or "")).removeprefix("eq.")
                result = [{"workspace_id": workspace, "role": "owner"}] if workspace and workspace == selected else []
            elif url.path.endswith("/onboarding_states"):
                workspace = params["workspace_id"].removeprefix("eq.")
                result = [{"completed_at": "2026-10-18T00:00:00Z" if (user, workspace) in state.completed else None}]
            else:
                raise AssertionError("unexpected auth path")
            body = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    state.settings = Settings(supabase_url=f"http://127.0.0.1:{server.server_port}",
        supabase_publishable_key="sb_publishable_fixture")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_real_keepalive_rereads_membership_and_onboarding_after_revocation(auth_server):
    state = auth_server
    state.members = {"user-a": "ws-a", "user-b": "ws-b"}
    state.completed.add(("user-a", "ws-a"))
    with httpx.Client(trust_env=False) as client:
        ctx = SupabaseAuthContext(state.settings, http_client=client)
        assert ctx.resolve_workspace_id("user-a", "token-a") == "ws-a"
        assert ctx.is_onboarding_complete("user-a", "ws-a", "token-a")
        state.completed.clear()
        assert not ctx.is_onboarding_complete("user-a", "ws-a", "token-a")
        del state.members["user-a"]
        assert ctx.resolve_workspace_id("user-a", "token-a") is None
        assert ctx.resolve_workspace_id("user-b", "token-b") == "ws-b"
        assert "Authorization" not in client.headers and "apikey" not in client.headers
        assert not client.is_closed
    assert len(state.calls) == 5 and len({call[0] for call in state.calls}) == 1
    assert [call[2]["Authorization"] for call in state.calls] == ["Bearer token-a"] * 4 + ["Bearer token-b"]
    assert all(call[2]["apikey"] == "sb_publishable_fixture" for call in state.calls)
    assert [call[3] for call in state.calls] == [
        {"user_id": "eq.user-a", "select": "workspace_id,role", "order": "created_at.asc"},
        {"workspace_id": "eq.ws-a", "user_id": "eq.user-a", "select": "completed_at"},
        {"workspace_id": "eq.ws-a", "user_id": "eq.user-a", "select": "completed_at"},
        {"user_id": "eq.user-a", "select": "workspace_id,role", "order": "created_at.asc"},
        {"user_id": "eq.user-b", "select": "workspace_id,role", "order": "created_at.asc"},
    ]
    assert client.is_closed


def test_shared_transport_keeps_concurrent_identity_and_selected_workspace_separate(auth_server):
    state = auth_server
    bindings = [(str(uuid4()), str(uuid4())) for _ in range(8)]
    state.members = dict(bindings)
    state.completed = set(bindings[::2])
    with httpx.Client(trust_env=False) as client:
        ctx = SupabaseAuthContext(state.settings, http_client=client)
        def read(binding):
            user, workspace = binding
            assert ctx.resolve_workspace_id(user, "token-" + user, selected_workspace_id=workspace) == workspace
            return ctx.is_onboarding_complete(user, workspace, "token-" + user)
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(read, bindings)) == [pair in state.completed for pair in bindings]
        assert "Authorization" not in client.headers and "apikey" not in client.headers
    assert len(state.calls) == 16
    for _, _, headers, params in state.calls:
        user = params["user_id"].removeprefix("eq.")
        assert headers["Authorization"] == "Bearer token-" + user
        assert params["workspace_id"] == "eq." + state.members[user]


def test_both_gets_keep_ten_second_timeout_and_do_not_retry_network_failure():
    calls = []
    def fail(request):
        calls.append(request)
        raise httpx.ConnectTimeout("fixture unavailable", request=request)
    with httpx.Client(timeout=90, transport=httpx.MockTransport(fail)) as client:
        ctx = SupabaseAuthContext(Settings(supabase_url="https://auth.example",
            supabase_publishable_key="sb_publishable_fixture"), http_client=client)
        with pytest.raises(AuthDependencyUnavailableError):
            ctx.resolve_workspace_id("user", "token")
        with pytest.raises(AuthDependencyUnavailableError):
            ctx.is_onboarding_complete("user", "ws", "token")
        assert not client.is_closed
    assert len(calls) == 2
    assert all(request.extensions["timeout"] == httpx.Timeout(10).as_dict() for request in calls)


def test_application_binds_before_users_closes_after_users_and_restarts_with_new_client(monkeypatch):
    assert main.app.router.on_startup.index(main.start_auth_transport) < main.app.router.on_startup.index(main.recover_interrupted_assistant_turns)
    assert main.app.router.on_startup.index(main.start_auth_transport) < main.app.router.on_startup.index(main.resume_persisted_creation_executions)
    shutdown = main.app.router.on_shutdown
    assert all(shutdown.index(user) < shutdown.index(main.stop_auth_transport) for user in (
        main.stop_assistant_continuation_driver, main.stop_active_assistant_turns, main.stop_creation_execution_worker))
    assert shutdown.index(main.stop_auth_transport) < shutdown.index(main.stop_creation_permit_transport)
    ctx = SupabaseAuthContext(Settings(supabase_url="https://auth.example", supabase_publishable_key="sb_publishable_fixture"))
    monkeypatch.setattr(main, "auth_context", ctx)
    monkeypatch.setattr(main.app.state, "auth_http_client", None)
    clients, order = [], []
    original_client = httpx.Client
    def create_client(**kwargs):
        assert kwargs == {"timeout": 10.0}  # TLS/default transport policy, no user auth/headers or retries.
        client = original_client(**kwargs, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[{"workspace_id": "ws", "role": "owner"}])))
        clients.append(client)
        return client
    monkeypatch.setattr(main.httpx, "Client", create_client)
    def user_start():
        assert ctx._http_client is clients[-1] and not clients[-1].is_closed
        assert ctx.resolve_workspace_id("user", "token") == "ws"
        main.start_auth_transport()
        assert len(clients) == order.count("stop") + 1
        order.append("start")
    def user_stop():
        assert not clients[-1].is_closed
        assert ctx.resolve_workspace_id("user", "token") == "ws"
        order.append("stop")
    monkeypatch.setattr(main.app.router, "on_startup", [main.start_auth_transport, user_start])
    monkeypatch.setattr(main.app.router, "on_shutdown", [user_stop, main.stop_auth_transport])
    for _ in range(2):
        with TestClient(main.app):
            assert not clients[-1].is_closed
        assert clients[-1].is_closed and main.app.state.auth_http_client is None and ctx._http_client is None
    assert len(clients) == 2 and clients[0] is not clients[1]
    assert order == ["start", "stop", "start", "stop"]


def test_reload_binds_new_auth_to_same_application_transport(isolated_host, monkeypatch):
    monkeypatch.setattr(main.app.state, "auth_http_client", None)
    main.start_auth_transport()
    client, previous = main.app.state.auth_http_client, main.auth_context
    try:
        main._reload_runtime_dependencies()
        assert main.auth_context is not previous
        assert main.auth_context._http_client is previous._http_client is client
        assert main.app.state.auth_http_client is client and not client.is_closed
    finally:
        # Stop actual isolated users before releasing the shared transport.
        main._stop_device_runtime()
        main._stop_material_ingestion()
        main._stop_device_runtime()
        main.stop_auth_transport()
    assert client.is_closed and main.auth_context._http_client is None


def test_failed_user_shutdown_keeps_auth_transport_open(monkeypatch):
    monkeypatch.setattr(main.app.state, "auth_http_client", None)
    monkeypatch.setattr(main, "auth_context", SupabaseAuthContext(main.settings))
    def blocked_user():
        raise RuntimeError("fixture auth user still active")
    monkeypatch.setattr(main.app.router, "on_startup", [main.start_auth_transport])
    monkeypatch.setattr(main.app.router, "on_shutdown", [blocked_user, main.stop_auth_transport])
    try:
        with pytest.raises(RuntimeError, match="still active"):
            with TestClient(main.app):
                client = main.app.state.auth_http_client
        assert not client.is_closed and main.auth_context._http_client is client
    finally:
        main.stop_auth_transport()
    assert client.is_closed


def test_task_goal_authority_unknown_can_reverify_but_actual_revocation_still_blocks(tmp_path):
    from dataclasses import replace
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.creation_execution import CreationExecutionActor
    from app.device_accounts import DeviceAccountService, DeviceCredential
    from test_metadata_edit_use_case import metadata_state

    state = metadata_state(tmp_path)
    workspace, device = str(uuid4()), str(uuid4())
    user = state.member.member_id
    plan = replace(state.repo.plan_records.get_creation_plan('ws', 'plan'), id='account-plan', workspace_id=workspace)
    task = replace(state.task, id='account-task', workspace_id=workspace, plan_id=plan.id,
        current_timeline_version_id=None, montage_plan_id=None, narration_cache_key=None)
    task = state.repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=user)[1]
    calls = []
    outcome = 'unknown'
    def read(request):
        calls.append(request)
        assert request.url.path == '/rest/v1/rpc/device_creation_permit'
        assert json.loads(request.content) == {'target_device_id': device, 'device_proof': 'a' * 64,
            'target_user_id': user, 'target_workspace_id': workspace}
        assert 'authorization' not in request.headers
        if outcome == 'unknown':
            raise httpx.ConnectTimeout('fixture unavailable', request=request)
        return httpx.Response(200, json=outcome == 'ready')
    with httpx.Client(transport=httpx.MockTransport(read)) as client:
        service = DeviceAccountService(Settings(supabase_url='https://auth.example',
            supabase_publishable_key='sb_publishable_fixture'), DeviceCredential(device, 'a' * 64),
            creation_permit_client=client)
        authority = TaskAuthorizationUseCase(state.repo, lambda identity: service.creation_permit(
            identity['device_id'], identity['user_id'], identity['workspace_id']))
        actor = CreationExecutionActor.account(workspace, device, user)
        saved = authority.ensure(task, actor, interactive=True)
        assert saved.revoked_at is None and saved.last_verdict == 'unknown'
        assert authority.status(workspace, task.id, operation='task_goal') == 'unverified'
        outcome = 'ready'
        authority.refresh(saved)
        saved = state.repo.task_authorization_records.get_task_authorization(workspace, task.id)
        assert authority.status(workspace, task.id, operation='task_goal') == 'valid'
        outcome = 'revoked'
        authority.refresh(saved)
        saved = state.repo.task_authorization_records.get_task_authorization(workspace, task.id)
        assert saved.revoked_at is not None
        assert authority.status(workspace, task.id, operation='task_goal') == 'revoked'
        outcome = 'ready'
        authority.refresh(saved)
        assert authority.status(workspace, task.id, operation='task_goal') == 'revoked'
    assert len(calls) == 3
