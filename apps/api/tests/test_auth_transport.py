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
        main._stop_execution_worker()
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


def test_goal_unknown_can_reverify_but_actual_revocation_still_blocks(monkeypatch):
    calls, verified = [], []
    outcome = "unknown"
    def read(request):
        calls.append(request)
        if outcome == "unknown":
            raise httpx.ConnectTimeout("fixture unavailable", request=request)
        return httpx.Response(200, json=[{"workspace_id": "ws", "role": "owner"}] if outcome == "ready" else [])
    with httpx.Client(transport=httpx.MockTransport(read)) as client:
        ctx = SupabaseAuthContext(Settings(supabase_url="https://auth.example",
            supabase_publishable_key="sb_publishable_fixture"), http_client=client)
        def verify(token):
            verified.append(token)
            return {"sub": "member"}
        monkeypatch.setattr(ctx, "verify_token", verify)
        monkeypatch.setattr(main, "auth_context", ctx)
        saved = {"authorization": "Bearer fixture-token", "cookie": None, "blocked": False}
        monkeypatch.setattr(main, "_assistant_goal_transports", {("ws", "member"): saved})
        goal = {"workspace_id": "ws", "member_id": "member", "task_id": "task"}
        assert main._goal_readiness_use_case().verify(goal, {}) == {"status": "waiting_identity", "reason": "interactive_identity_not_available"}
        assert saved["blocked"] is False
        outcome = "ready"
        request = main._goal_request(goal)
        assert request.state.principal.user_id == "member" and request.state.principal.workspace_id == "ws"
        outcome = "revoked"
        assert main._goal_request(goal) is None and saved["blocked"] is True
        assert main._goal_request(goal) is None
    assert len(calls) == 3 and verified == ["fixture-token"] * 3
