"""Real HTTP/auth/plan/queue composition, with the media driver held unstarted."""
from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest
import app.main as main
import app.device_resource_preparation as preparation
from test_typed_assistant_edits_api import metadata_http_state as metadata_http_state  # noqa: F401


@pytest.fixture(autouse=True)
def preparation_metadata_no_execution(monkeypatch):
    from test_production_foundation import edit_adoption_no_execution
    edit_adoption_no_execution(monkeypatch)


def test_original_member_can_cancel_an_orphaned_preparation(metadata_http_state, monkeypatch, tmp_path):
    from app import device_paths, device_resource_configuration
    from app.creation_execution import CreationExecutionActor
    from app.device_runtime import OwnerRouter, build_device_runtime
    from app.device_scheduler import DeviceScheduler, PriorDeviceWorkPending
    from app.member_permits import MemberDeviceAccess
    from test_device_scheduler import Owners, Processes, Resources, _complete, _db, _intent

    state = metadata_http_state
    monkeypatch.setattr(main, 'LEGACY_WORKSPACE_ID', state.task.workspace_id)
    monkeypatch.setattr(device_paths, 'application_data_root', lambda: tmp_path / 'device-lock')
    monkeypatch.setenv('DEVICE_ID', 'isolated-device')
    identity = device_resource_configuration.current_device_identity
    monkeypatch.setattr(device_resource_configuration, 'current_device_identity',
        lambda environment: replace(identity(environment), platform='darwin'))
    monkeypatch.setattr(main, 'settings', replace(main.settings, device_resource_mode='normal',
        device_resource_profile_path=str(tmp_path / 'device' / 'profile.json'),
        temporary_root_path=str(tmp_path / 'temporary')))
    monkeypatch.setattr(preparation, 'sample_host_memory', lambda: SimpleNamespace(
        pressure='normal', available_bytes=2 * 1024**3))
    monkeypatch.setattr(preparation.psutil, 'Process', lambda: SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=100 * 1024**2)))
    database = state.repo.database_path
    lock = device_paths.device_lock_path()
    history = DeviceScheduler(database, lock, owners=Owners(), resources=Resources(), processes=Processes())
    history.submit(_intent('previous-work'), 'member-a')
    _complete(history, history.pick_and_grant())

    service = main._device_preparation_service()
    actor = CreationExecutionActor(state.task.workspace_id, state.member.member_id)
    access = MemberDeviceAccess(state.members, identity_verifier=main._verify_creation_identity)
    shape = preparation.CpuPreparationShape(64, 64, 24, 1.0, 2, 2, 8000, 1)
    plan_id, _ = service.prepare(shape, actor, request_id=str(uuid4()))
    runtime = build_device_runtime(database_path=database, device_lock_path=lock, device_access=access,
        registrations={'device_preparation': service.registration()},
        resources=Resources(), processes=Processes())
    request = runtime.scheduler.submit(service.intent(plan_id, actor), actor)
    plan_path = service.root / 'plans' / f'{plan_id}.json'

    driver_calls = []

    def forbidden():
        pytest.fail('cancelling a missing plan cannot start or recover the normal runtime')

    def legacy_runtime():
        driver_calls.append(True)
        return runtime

    monkeypatch.setattr(main, '_creation_runtime', legacy_runtime)
    client = state.client
    intact_plan_id, _ = service.prepare(shape, actor, request_id=str(uuid4()))
    intact = runtime.scheduler.submit(service.intent(intact_plan_id, actor), actor)
    response = client.post(f'/api/device/queue/{intact.id}/cancel')
    assert response.status_code == 200 and response.json()['state'] == 'cancelled'
    assert len(driver_calls) == 1
    monkeypatch.setattr(main, '_creation_runtime', forbidden)

    plan_path.unlink()
    normal = DeviceScheduler(database, lock, owners=OwnerRouter({}, access),
        resources=Resources(), processes=Processes())
    assert normal.pick_and_grant() is None
    original = normal._get(request.id)
    assert (original.state, original.wait_reason, original.last_grant_generation) == ('blocked', 'owner_unknown', None)
    with state.repo._connect() as conn:
        row = conn.execute('SELECT * FROM device_work_requests WHERE id=?', (request.id,)).fetchone()
        DeviceScheduler._record_event(conn, row, 'deferred', 'dependency_unavailable', None, row['updated_at'])

    def snapshot():
        with state.repo._connect() as conn:
            row = dict(conn.execute('SELECT * FROM device_work_requests WHERE id=?', (request.id,)).fetchone())
            audit = [dict(event) for event in conn.execute(
                'SELECT * FROM device_request_events WHERE request_id=?', (request.id,))]
        return row, audit

    stored, audit = snapshot()
    ledger_bytes = history.ledger_path.read_bytes()

    next_database = tmp_path / 'next.sqlite'
    _db(next_database)
    successor = DeviceScheduler(next_database, lock, owners=Owners(), resources=Resources(), processes=Processes())
    successor.submit(_intent('next-work'), 'member-a')
    with pytest.raises(PriorDeviceWorkPending):
        successor.pick_and_grant()

    url = f'/api/device/queue/{request.id}/cancel'
    _, foreign = state.members.join(state.members.create_invite_code(), 'foreign member')
    assert client.post(url, headers={'X-Local-Member-Token': foreign}).status_code == 403
    assert client.post(url, headers={'X-Test-Workspace': 'different-workspace'}).status_code == 403
    with monkeypatch.context() as different_device:
        different_device.setattr(device_paths, 'application_data_root', lambda: tmp_path / 'different-device')
        assert client.post(url).status_code == 409
    copied_database = tmp_path / 'copied-original.sqlite'
    with state.repo._connect() as original_db, sqlite3.connect(copied_database) as copied_db:
        original_db.backup(copied_db)
    with monkeypatch.context() as other_database:
        other_database.setattr(main, 'production_repo', SimpleNamespace(database_path=copied_database))
        assert client.post(url).status_code == 409
    with sqlite3.connect(copied_database) as copied_db:
        copied = copied_db.execute('SELECT state,wait_reason FROM device_work_requests WHERE id=?', (request.id,)).fetchone()
    assert copied == ('blocked', 'owner_unknown')
    ledger = json.loads(ledger_bytes)
    history.ledger_path.write_text(json.dumps({**ledger, 'state': 'held'}))
    assert client.post(url).status_code == 409
    history.ledger_path.write_bytes(ledger_bytes)
    for state_name, generation in (('blocked', 1), ('granted', None), ('yield_requested', 1)):
        with state.repo._connect() as conn:
            conn.execute('UPDATE device_work_requests SET state=?, last_grant_generation=? WHERE id=?',
                         (state_name, generation, request.id))
        assert client.post(url).status_code == 403
    with state.repo._connect() as conn:
        conn.execute("UPDATE device_work_requests SET state='blocked', last_grant_generation=NULL WHERE id=?", (request.id,))
        conn.execute("UPDATE device_permit SET state='held' WHERE id=1")
    assert client.post(url).status_code == 409
    with state.repo._connect() as conn:
        conn.execute("UPDATE device_permit SET state='free' WHERE id=1")
    assert snapshot() == (stored, audit)

    response = client.post(url)
    assert response.status_code == 200, response.text
    assert response.json() == {'id': request.id, 'state': 'cancelled', 'wait_reason': 'user_cancelled'}
    cancelled, preserved_audit = snapshot()
    assert {**cancelled, 'updated_at': stored['updated_at']} == {
        **stored, 'state': 'cancelled', 'wait_reason': 'user_cancelled'}
    assert audit and preserved_audit == audit
    assert not plan_path.exists()
    assert history.ledger_path.read_bytes() == ledger_bytes
    grant = successor.pick_and_grant()
    assert grant.owner_id == 'next-work'
    _complete(successor, grant)


def test_profile_status_reads_current_profile_without_exposing_host_paths(metadata_http_state, monkeypatch, tmp_path):
    from test_device_resource_profile import DEVICE, BACKENDS, profile_document, write_profile
    import app.device_resource_configuration as configuration

    state = metadata_http_state
    monkeypatch.setattr(main, 'LEGACY_WORKSPACE_ID', 'ws')
    path = tmp_path / 'private-device-profile.json'
    monkeypatch.setenv('DEVICE_ID', DEVICE.device_id)
    monkeypatch.setattr(main, 'settings', replace(main.settings, device_resource_mode='normal',
        device_resource_profile_path=str(path)))
    monkeypatch.setattr(configuration, 'current_device_identity', lambda _: DEVICE)
    monkeypatch.setattr(configuration, 'backend_identity', lambda name, *_args, **_kwargs: BACKENDS.get(name))
    url = '/api/device/resource-preparations'
    missing = state.client.get(url)
    assert missing.status_code == 200 and missing.json()['profile_status'] == 'unprepared'
    assert missing.json()['preparation_configured'] and missing.json()['available_operations'] == []
    assert missing.json()['media_execution_ready'] is False
    assert missing.json()['status_endpoint'] == url
    assert missing.json()['prepare_endpoint'] == url
    assert '提交设备准备请求' in missing.json()['repair_action']
    path.write_text(write_profile(tmp_path, profile_document()).read_text())
    available = state.client.get(url).json()
    assert available['profile_status'] == 'available' and available['revision'] == 3
    assert available['available_operations'] == ['cpu_render', 'narration']
    assert available['media_execution_ready'] is True
    assert available['message'] == '设备资源档已就绪，可以提交媒体任务。'
    assert str(tmp_path) not in str(available) and 'budget' not in available and 'backends' not in available
    path.write_text('{"format_version": 99}')
    invalid = state.client.get(url).json()
    assert invalid['profile_status'] == 'invalid' and invalid['available_operations'] == []
    assert invalid['actor_scope'] == available['actor_scope']
    _, token = state.members.join(state.members.create_invite_code(), 'other setup member')
    other = state.client.get(url, headers={'X-Local-Member-Token': token}).json()
    assert other['actor_scope'] != available['actor_scope']


def test_saved_profile_summary_uses_existing_member_guard_and_only_reads_the_file(metadata_http_state, monkeypatch, tmp_path):
    import subprocess

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import device_resource_configuration as configuration, device_resource_status as status
    from app.device_preparation_routes import build_device_preparation_router
    from test_device_resource_profile import DEVICE, PROFILE_FILENAME, profile_document, write_profile

    state = metadata_http_state
    monkeypatch.setattr(main, 'LEGACY_WORKSPACE_ID', state.task.workspace_id)
    path = tmp_path / PROFILE_FILENAME
    settings = replace(main.settings, device_resource_mode='normal', device_resource_profile_path=str(path))
    environment = {'DEVICE_ID': DEVICE.device_id}
    monkeypatch.setattr(configuration.platform, 'machine', lambda: DEVICE.architecture)
    monkeypatch.setattr(configuration.psutil, 'virtual_memory', lambda: SimpleNamespace(total=DEVICE.physical_memory_bytes))
    document = profile_document()
    document['device']['platform'] = configuration.sys.platform

    def forbidden(*_args, **_kwargs):
        pytest.fail('saved profile summary must not inspect live backends, launch a process or read the device ledger')

    monkeypatch.setattr(configuration, 'backend_identity', forbidden)
    monkeypatch.setattr(status, 'resolve_device_resources', forbidden)
    monkeypatch.setattr(status, 'production_device_blocker', forbidden)
    monkeypatch.setattr(status, 'device_lock_path', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)

    reads = []

    def read_summary():
        reads.append(True)
        return status.saved_resource_profile_summary(settings, environment)

    app = FastAPI()
    wiring = dict(authorized_actor=lambda request: main._require_permit_actor(request, main.current_workspace_id(request)),
        application=forbidden, profile_status=lambda actor: {'member': actor.member_id}, actor_scope=forbidden)
    app.include_router(build_device_preparation_router(**wiring, saved_profile_summary=read_summary))
    url = '/api/device/resource-preparations/profile/summary'
    with TestClient(app, headers={'X-Local-Member-Token': state.token}) as client:
        rejected = client.get(url, headers={'x-test-workspace': 'different-workspace'})
        assert rejected.status_code == 403
        assert not reads
        assert client.get('/api/device/resource-preparations').json() == {'member': state.member.member_id}
        assert client.get(url).json() == {'status': 'missing', 'revision': None}

        written = write_profile(tmp_path, document)
        assert written == path
        original = written.read_bytes()
        assert client.get(url).json() == {'status': 'saved', 'revision': 3}
        assert written.read_bytes() == original

        environment['DEVICE_ID'] = 'different-device'
        assert client.get(url).json() == {'status': 'device_mismatch', 'revision': None}
        environment.clear()
        assert client.get(url).json() == {'status': 'device_unbound', 'revision': None}

        environment['DEVICE_ID'] = DEVICE.device_id
        document['evidence'] = {'host': {'record_id': 'missing-host-evidence', 'sha256': '1' * 64}}
        write_profile(tmp_path, document)
        assert client.get(url).json() == {'status': 'unreadable', 'revision': None}
        written.write_text('{"format_version": 99}')
        assert client.get(url).json() == {'status': 'unreadable', 'revision': None}

    legacy = FastAPI()
    legacy.include_router(build_device_preparation_router(**wiring))
    with TestClient(legacy) as client:
        assert client.get(url).status_code == 404


def test_preparation_expected_actor_scope_rejects_before_read_or_submission(metadata_http_state, monkeypatch):
    state = metadata_http_state
    monkeypatch.setattr(main, 'LEGACY_WORKSPACE_ID', 'ws')
    url = '/api/device/resource-preparations'
    scope = state.client.get(url).json()['actor_scope']
    _, token = state.members.join(state.members.create_invite_code(), 'new setup member')
    headers = {'X-Local-Member-Token': token, 'X-Device-Preparation-Scope': scope}
    monkeypatch.setattr(main, '_device_preparation_application',
        lambda **_kwargs: pytest.fail('a stale actor scope must not read or submit work'))
    payload = {'request_id': str(uuid4()), 'shape': {'width_px': 64, 'height_px': 64,
        'frame_rate': 24, 'duration_seconds': 1.0, 'clip_count': 2,
        'audio_input_count': 2, 'sample_rate': 8000, 'channels': 1}}
    for response in (state.client.post(url, json=payload, headers=headers),
                     state.client.get(url + '/original', headers=headers),
                     state.client.post(url + '/original/finalize', headers=headers)):
        assert response.status_code == 409 and '身份已变化' in response.text


@pytest.mark.parametrize('overlay_count', [0, 2])
def test_preparation_http_reuses_request_and_never_resumes_old_work(metadata_http_state, monkeypatch, tmp_path, overlay_count):
    state = metadata_http_state
    monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", "ws")
    monkeypatch.setenv("DEVICE_ID", "isolated-device")
    import app.device_resource_configuration as resource_configuration
    current_identity = resource_configuration.current_device_identity
    monkeypatch.setattr(resource_configuration, "current_device_identity",
        lambda environment: replace(current_identity(environment), platform="darwin"))
    monkeypatch.setattr(main, "settings", replace(main.settings, device_resource_mode="normal",
        device_resource_profile_path=str(tmp_path / "device" / "profile.json"),
        temporary_root_path=str(tmp_path / "temporary")))
    monkeypatch.setattr(preparation, "sample_host_memory", lambda: SimpleNamespace(
        pressure="normal", available_bytes=2 * 1024**3))
    monkeypatch.setattr(preparation.psutil, "Process", lambda: SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=100 * 1024**2)))
    actions = []
    class OldRuntime:
        registrations = {"creation_job": object()}
        def pause(self, timeout):
            actions.append("old_pause")
            return True
        def stop(self, timeout_seconds):
            actions.append("old_stop")
            return True
    monkeypatch.setattr(main, "device_runtime", OldRuntime())
    original_bind = main.bind_device_runtime
    def bind(**kwargs):
        assert set(kwargs["registrations"]) == {"device_preparation"}
        assert kwargs["dispatch_owner"][0] == "device_preparation"
        real, _ = original_bind(**kwargs)
        class HeldDriver:
            scheduler = real.scheduler
            registrations = real.registrations
            running = False
            last_error = None
            def start(self):
                actions.append("preparation_start")
                self.running = True  # Deliberately no run_once, worker or media.
            def stop(self, timeout_seconds):
                actions.append("preparation_stop")
                self.running = False
                real.scheduler.close()
                return True
        return HeldDriver(), None
    monkeypatch.setattr(main, "bind_device_runtime", bind)
    payload = {"request_id": str(uuid4()), "shape": {"width_px": 64, "height_px": 64,
        "frame_rate": 24, "duration_seconds": 1.0, "clip_count": 2,
        "audio_input_count": 2, "sample_rate": 8000, "channels": 1}}
    if overlay_count:
        payload['shape']['overlay_count'] = overlay_count
    url = "/api/device/resource-preparations"
    first = state.client.post(url, json=payload)
    assert first.status_code == 200, first.text
    receipt = first.json()
    assert receipt["state"] == "waiting"
    assert receipt["mp4_cut_measurement"] == "not_requested"
    assert actions == ["old_pause", "old_stop", "preparation_start"]
    repeated = state.client.post(url, json=payload)
    assert repeated.status_code == 200 and repeated.json() == receipt
    assert len(actions) == 3
    changed = {**payload, "shape": {**payload["shape"], "clip_count": 3}}
    assert state.client.post(url, json=changed).status_code == 409
    read_url = url + "/" + receipt["id"]
    assert state.client.get(read_url).json() == receipt
    assert state.client.get(read_url, headers={"x-test-workspace": "outside"}).status_code == 403
    assert state.client.get(url + "/missing").status_code == 404
    assert state.client.post(read_url + "/finalize").status_code == 409
    assert len(actions) == 3  # Waiting/unknown cannot publish or release an active driver.
    with state.repo._connect() as connection:
        assert connection.execute("SELECT count(*) FROM device_work_requests").fetchone()[0] == 1
        resources = json.loads(connection.execute('SELECT resources_json FROM device_work_requests WHERE id=?',
            (receipt['id'],)).fetchone()[0])
        assert resources['overlay_count'] == overlay_count
        connection.execute("UPDATE device_work_requests SET state='failed' WHERE id=?", (receipt["id"],))
    failure_replay = state.client.post(url, json=payload)
    assert failure_replay.status_code == 200 and failure_replay.json()["state"] == "failed"
    assert len(actions) == 3
    closed = state.client.post(read_url + "/finalize")
    assert closed.status_code == 200, closed.text
    assert closed.json()["profile_published"] is False
    assert closed.json()["normal_work_started"] is False
    assert actions[-1] == "preparation_stop" and main.device_runtime is None
    assert not (tmp_path / "device" / "profile.json").exists()


def test_model_shape_and_original_request_select_the_same_application(metadata_http_state, monkeypatch):
    """Adapter coverage; owner/grant behavior is tested by the preparation owner."""
    from app.device_preparation_application import DevicePreparationApplication
    from app.narration_resource_preparation import NarrationPreparationShape
    state = metadata_http_state
    monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", "ws")
    actions = []
    def selected_service(kind="cpu"):
        actions.append(("service", kind))
        return SimpleNamespace(kind=kind)
    monkeypatch.setattr(main, "_device_preparation_service", selected_service)
    def original_kind(*, database_path, request_id, actor):
        assert str(database_path) == str(state.repo.database_path)
        assert actor.workspace_id == "ws"
        actions.append(("lookup", request_id))
        return "narration"
    monkeypatch.setattr(preparation, "request_kind", original_kind)
    def prepare(self, shape, request_id, actor):
        assert isinstance(shape, NarrationPreparationShape)
        assert self.service.kind == "narration"
        actions.append(("prepare", shape, request_id))
        return {"id": "original-model-request", "state": "waiting"}
    def status(self, request_id, actor):
        assert self.service.kind == "narration"
        return {"id": request_id, "state": "done"}
    def finalize(self, request_id, actor):
        assert self.service.kind == "narration"
        actions.append(("finalize", request_id))
        return {"id": request_id, "state": "done", "normal_work_started": False}
    monkeypatch.setattr(DevicePreparationApplication, "prepare", prepare)
    monkeypatch.setattr(DevicePreparationApplication, "status", status)
    monkeypatch.setattr(DevicePreparationApplication, "finalize", finalize)
    payload = {"request_id": str(uuid4()), "kind": "narration", "shape": {
        "voice_profile_id": "voice", "voice_version": "version", "text": "准备样本",
        "language": "zh", "output_duration_seconds": 2.0, "task_id": "task",
        "pronunciation_references": [{"id": "entry", "version": "v1"}]}}
    url = "/api/device/resource-preparations"
    submitted = state.client.post(url, json=payload)
    assert submitted.status_code == 200, submitted.text
    assert actions[0] == ("service", "narration")
    assert actions[1][1].text == "准备样本"
    assert actions[1][1].pronunciation_references == ({"id": "entry", "version": "v1"},)
    assert actions[1][1].task_id == "task"
    for bad in ({**payload, "kind": "cpu"}, {**payload, "kind": "other"},
                {**payload, "shape": {**payload["shape"], "memory_bytes": 100}},
                {**payload, "shape": {**payload["shape"], "pronunciation_references": [
                    {"id": "entry", "version": "v1", "term": "caller-supplied"}]}}):
        assert state.client.post(url, json=bad).status_code == 422
    assert len(actions) == 2
    assert state.client.get(url + "/original-model-request").json()["state"] == "done"
    closed = state.client.post(url + "/original-model-request/finalize")
    assert closed.status_code == 200 and closed.json()["normal_work_started"] is False
    assert [entry for entry in actions if entry[0] == "lookup"] == [
        ("lookup", "original-model-request"), ("lookup", "original-model-request")]
