"""Real HTTP/auth/plan/queue composition, with the media driver held unstarted."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import app.main as main
import app.device_resource_preparation as preparation
from test_typed_assistant_edits_api import metadata_http_state as metadata_http_state  # noqa: F401


def test_preparation_http_reuses_request_and_never_resumes_old_work(metadata_http_state, monkeypatch, tmp_path):
    state = metadata_http_state
    monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", "ws")
    monkeypatch.setenv("DEVICE_ID", "isolated-device")
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
    url = "/api/device/resource-preparations"
    first = state.client.post(url, json=payload)
    assert first.status_code == 200, first.text
    receipt = first.json()
    assert receipt["state"] == "waiting"
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
