"""Creation-permit transport only: local HTTP/1.1 and isolated ASGI lifecycle."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings
from app.creation_execution import CreationExecutionActor
from app.device_accounts import DeviceAccountService, DeviceCredential


PROOF = "ab" * 32


def test_continuation_pause_spends_one_deadline_and_restores_failed_pause(monkeypatch):
    from threading import Event
    driver=main.AssistantContinuationDriver.__new__(main.AssistantContinuationDriver)
    driver._paused=Event()
    now=[100.0];budgets=[]
    class TickLock:
        def acquire(self,timeout):
            budgets.append(timeout);now[0]+=0.4
            return True
        def release(self): pass
    driver._tick_lock=TickLock()
    service=SimpleNamespace(pause=lambda remaining:budgets.append(remaining) or False)
    monkeypatch.setattr(main.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(main,'_goal_service',lambda:service)
    assert driver.pause(1) is False
    assert budgets==pytest.approx([1,0.6]) and not driver._paused.is_set()


@pytest.fixture
def permit_server():
    state = SimpleNamespace(calls=[], status=200, result=True)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            parameters = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.calls.append((self.client_address, self.path, dict(self.headers), parameters))
            result = state.result(parameters) if callable(state.result) else state.result
            body = json.dumps(result).encode()
            self.send_response(state.status)
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


def test_real_keepalive_rechecks_false_unknown_and_current_tuple(permit_server):
    server = permit_server
    device, user, workspace, next_user, next_workspace = (str(uuid4()) for _ in range(5))
    with httpx.Client(trust_env=False) as client:
        service = DeviceAccountService(server.settings, DeviceCredential(device, PROOF), creation_permit_client=client)
        assert service.creation_permit(device, user, workspace) == "ready"
        server.result = False
        assert service.creation_permit(device, user, workspace) == "unauthorized"
        server.result = {"ready": True}
        assert service.creation_permit(device, next_user, next_workspace) == "unknown"
        server.status, server.result = 503, True
        assert service.creation_permit(device, user, workspace) == "unknown"
        assert service.creation_permit(device, "invalid-user", workspace) == "unauthorized"
        assert not client.is_closed
    assert len(server.calls) == 4
    assert len({call[0] for call in server.calls}) == 1  # One actual TCP peer for all four POSTs.
    assert all(call[1] == "/rest/v1/rpc/device_creation_permit" for call in server.calls)
    assert all("Authorization" not in call[2] for call in server.calls)
    expected = [(user, workspace), (user, workspace), (next_user, next_workspace), (user, workspace)]
    assert [call[3] for call in server.calls] == [
        {"target_device_id": device, "device_proof": PROOF, "target_user_id": actor, "target_workspace_id": scope}
        for actor, scope in expected]
    assert client.is_closed


def test_shared_client_concurrent_users_and_workspaces_do_not_exchange_results(permit_server):
    server = permit_server
    device = str(uuid4())
    bindings = [(str(uuid4()), str(uuid4())) for _ in range(8)]
    allowed = set(bindings[::2])
    server.result = lambda body: (body["target_user_id"], body["target_workspace_id"]) in allowed
    with httpx.Client(trust_env=False) as client:
        service = DeviceAccountService(server.settings, DeviceCredential(device, PROOF), creation_permit_client=client)
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(lambda pair: service.creation_permit(device, *pair), bindings))
        assert values == ["ready" if pair in allowed else "unauthorized" for pair in bindings]
    assert len(server.calls) == len(bindings)
    assert {(call[3]["target_user_id"], call[3]["target_workspace_id"]) for call in server.calls} == set(bindings)
    assert all(call[3]["device_proof"] == PROOF and "Authorization" not in call[2] for call in server.calls)


def test_injected_transport_keeps_five_second_deadline_and_unknown_on_network_failure():
    device, user, workspace = (str(uuid4()) for _ in range(3))
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("fixture unavailable", request=request)
    with httpx.Client(timeout=90, transport=httpx.MockTransport(handler)) as client:
        service = DeviceAccountService(Settings(supabase_url="https://permit.example",
            supabase_publishable_key="sb_publishable_fixture"), DeviceCredential(device, PROOF),
            creation_permit_client=client)
        assert service.creation_permit(device, user, workspace) == "unknown"
        assert service.creation_permit(device, user, workspace) == "unknown"
    assert len(calls) == 2
    assert all(request.extensions["timeout"] == httpx.Timeout(5).as_dict() for request in calls)


def test_application_opens_before_users_closes_after_users_and_recreates_on_second_start(monkeypatch):
    assert main.app.router.on_startup[0] is main.start_creation_permit_transport
    assert main.app.router.on_shutdown[-1] is main.stop_creation_permit_transport
    device, user, workspace = (str(uuid4()) for _ in range(3))
    actor = CreationExecutionActor.account(workspace, device, user)
    credential = DeviceCredential(device, PROOF)
    settings = Settings(supabase_url="https://permit.example", supabase_publishable_key="sb_publishable_fixture")
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "trusted_device_credential", lambda: credential)
    monkeypatch.setattr(main.app.state, "creation_permit_client", None)
    clients, requests, order = [], [], []
    verdict = True
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=verdict)
    original_client = httpx.Client
    def create_client(**kwargs):
        assert kwargs == {"timeout": 5.0}  # Keep TLS/default transport policy; no auth or user headers.
        client = original_client(**kwargs, transport=httpx.MockTransport(handler))
        clients.append(client)
        return client
    monkeypatch.setattr(main.httpx, "Client", create_client)
    def user_start():
        assert main.app.state.creation_permit_client is clients[-1] and not clients[-1].is_closed
        assert main._verify_creation_identity(actor) == "ready"
        order.append("user_start")
    def user_stop():
        assert not clients[-1].is_closed
        assert main._verify_creation_identity(actor) == "ready"
        order.append("user_stop")
    # Exercise the real app lifespan and transport hooks, replacing heavy users with boundary probes.
    monkeypatch.setattr(main.app.router, "on_startup", [main.start_creation_permit_transport, user_start])
    monkeypatch.setattr(main.app.router, "on_shutdown", [user_stop, main.stop_creation_permit_transport])
    assert main._verify_creation_identity(actor) == "unknown"
    for _ in range(2):
        verdict = True
        with TestClient(main.app):
            client = clients[-1]
            verdict = False
            assert main._verify_creation_identity(actor) == "unauthorized"
            verdict = True
        assert client.is_closed and main.app.state.creation_permit_client is None
        assert main._verify_creation_identity(actor) == "unknown"
    assert len(clients) == 2 and clients[0] is not clients[1]
    assert order == ["user_start", "user_stop", "user_start", "user_stop"]
    assert len(requests) == 6
    assert all("Authorization" not in request.headers for request in requests)
    assert all(json.loads(request.content) == {"target_device_id": device, "device_proof": PROOF,
        "target_user_id": user, "target_workspace_id": workspace} for request in requests)


def test_failed_user_shutdown_does_not_close_transport_under_an_active_user(monkeypatch):
    monkeypatch.setattr(main.app.state, "creation_permit_client", None)
    monkeypatch.setattr(main.app.router, "on_startup", [main.start_creation_permit_transport])
    def blocked_user():
        raise RuntimeError("fixture user still active")
    monkeypatch.setattr(main.app.router, "on_shutdown", [blocked_user, main.stop_creation_permit_transport])
    client = None
    try:
        with pytest.raises(RuntimeError, match="still active"):
            with TestClient(main.app):
                client = main.app.state.creation_permit_client
        assert main.app.state.creation_permit_client is client and not client.is_closed
    finally:
        # The fixture consumer has exited; the owner may now release its transport.
        main.stop_creation_permit_transport()
    assert client.is_closed and main.app.state.creation_permit_client is None



@pytest.fixture(autouse=True)
def desktop_shutdown_metadata_guard(request, monkeypatch, tmp_path):
    if request.node.originalname != 'test_desktop_shutdown_control_is_loopback_only_and_uses_one_deadline':
        return None
    import sqlite3
    import subprocess
    import wave
    from PIL import Image
    from app.auth import SupabaseAuthContext
    from app.creation.execution_control import DesktopShutdownUseCase
    from app.device_runtime import DeviceRuntime
    from app.device_scheduler import DeviceScheduler

    violations = []

    def forbidden(*args, **kwargs):
        violations.append('actual-shutdown-runtime-or-media')
        pytest.fail('controlled shutdown HTTP forbids real owner construction/execution/stopping and persistence')

    # Only the original test's preexisting controlled Shutdown object may execute.
    monkeypatch.setattr(DesktopShutdownUseCase, '__init__', forbidden)
    monkeypatch.setattr(DesktopShutdownUseCase, 'execute', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    for name in ('start', 'run_once', 'wake', 'stop', 'begin_shutdown'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('_creation_runtime', '_build_device_components', '_start_device_runtime',
                 '_stop_device_runtime', '_workspace_settings_for', 'run_task_turn', 'run_plan_turn',
                 'start_assistant_continuation_driver', 'stop_all_assistant_turns',
                 'stop_auth_transport', 'stop_creation_permit_transport'):
        monkeypatch.setattr(main, name, forbidden)
    monkeypatch.setattr(main, 'montage_renderer', SimpleNamespace(render=forbidden))
    monkeypatch.setattr(main, 'narration_generator', SimpleNamespace(generate_for_creation_task=forbidden))
    settings = Settings(database_path=str(tmp_path / 'unused.sqlite'),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'outputs'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    for name in ('device_runtime', 'automation_runtime', 'material_ingestion', 'assistant_continuation_driver',
                 'production_repo', 'conversation_repo', 'source_repository', 'artifact_store', 'local_members_service', 'vault'):
        monkeypatch.setattr(main, name, None)

    def snapshot():
        return {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob('*')}

    before = snapshot()
    assert before == {}  # The original test prepares only an in-memory controlled shutdown object.

    def assert_unchanged():
        assert snapshot() == before
        assert violations == []

    return SimpleNamespace(assert_unchanged=assert_unchanged)


def test_desktop_shutdown_control_is_loopback_only_and_uses_one_deadline(monkeypatch, desktop_shutdown_metadata_guard):
    from datetime import datetime, timedelta, timezone
    import time
    calls = []
    class Shutdown:
        def execute(self, request_id, deadline):
            calls.append((request_id, deadline-time.monotonic()))
            checks = dict.fromkeys(("dispatch_stopped", "executors_notified", "processes_released", "checkpoints_saved"), True)
            return {"exit_request_id": request_id, "status": "ready_to_exit", "checks": checks, "pending": []}
    monkeypatch.setattr(main.app.state, "desktop_control_token", "cd"*32)
    monkeypatch.setattr(main.app.state, "desktop_shutdown", Shutdown())
    request_id = str(uuid4())
    payload = {"exit_request_id": request_id, "deadline_at_utc": (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}
    client = TestClient(main.app, client=("127.0.0.1", 43210))
    assert client.post(main.DESKTOP_SHUTDOWN_PATH, json=payload).status_code == 403
    headers = {"X-Yaohe-Desktop-Control": "cd"*32}
    response = client.post(main.DESKTOP_SHUTDOWN_PATH, json=payload, headers=headers)
    assert response.status_code == 200 and response.json()["exit_request_id"] == request_id
    assert len(calls) == 1 and 29 < calls[0][1] <= 30
    expired = {**payload, "deadline_at_utc": (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}
    assert client.post(main.DESKTOP_SHUTDOWN_PATH, json=expired, headers=headers).status_code == 408
    remote = TestClient(main.app, client=("192.0.2.1", 43210))
    assert remote.post(main.DESKTOP_SHUTDOWN_PATH, json=payload, headers=headers).status_code == 403
    assert len(calls) == 1
    desktop_shutdown_metadata_guard.assert_unchanged()


def test_desktop_shutdown_repeats_receipt_without_restarting_or_resetting_budget():
    from app.creation.execution_control import DesktopShutdownUseCase
    import time
    calls = []
    class Driver:
        def request_stop(self): calls.append("notify")
        def stop(self, timeout_seconds, **kwargs):
            calls.append(timeout_seconds)
            time.sleep(.01)
            return True
    runtime = SimpleNamespace(begin_shutdown=lambda: calls.append("dispatch"),
        stop=lambda timeout_seconds: calls.append(timeout_seconds) or True,
        shutdown_checks=lambda: dict.fromkeys(("dispatch_stopped", "executors_notified", "processes_released", "checkpoints_saved"), True))
    service = DesktopShutdownUseCase(runtime=runtime, automation=Driver(), ingestion=Driver(), assistant=Driver(),
        stop_assistant=lambda **kwargs: [], conversations=None, close_clients=lambda: calls.append("close"))
    deadline = time.monotonic()+1
    first = service.execute("same-id", deadline)
    count = len(calls)
    assert first["status"] == "ready_to_exit"
    assert service.execute("same-id", time.monotonic()+30) == first and len(calls) == count
    budgets = [value for value in calls if isinstance(value, float)]
    assert 0 < budgets[-1] < budgets[0] <= 1
    assert calls[-1] == "close"
    with pytest.raises(ValueError):
        service.execute("different-id", deadline)
