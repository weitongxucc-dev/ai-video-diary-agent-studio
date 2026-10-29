import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.model_installer import ModelInstaller, ModelInstallError
from app.model_runtime import CHINESE_CLIP_VIT_B16, ModelCatalog
from app.model_install_jobs import InstallJobStore
from app import model_install_execution as execution


def test_installer_requires_explicit_license_acceptance(tmp_path, monkeypatch):
    import app.main as main
    from fastapi.testclient import TestClient

    violations = []

    def forbidden_installation(*args, **kwargs):
        violations.append(True)
        pytest.fail("metadata/license refusal must not claim or execute installation, download or runtime")

    # Block installation entry points before constructing owners/seeding a job.
    # AnyIO's own TestClient worker threads are intentionally left available.
    for name in ("_claim_job", "_run_owned_job", "_download_model", "_ensure_runtime"):
        monkeypatch.setattr(ModelInstaller, name, forbidden_installation)
    monkeypatch.setattr(main, "_creation_runtime", forbidden_installation)
    monkeypatch.setattr(main, "_reload_runtime_dependencies", forbidden_installation)
    catalog = ModelCatalog(tmp_path / "models", tmp_path / "python", tmp_path / "worker.py")
    installer = ModelInstaller(catalog)

    with pytest.raises(ValueError, match="许可证"):
        installer.run_sync(CHINESE_CLIP_VIT_B16.id, license_accepted=False)

    monkeypatch.setattr(main, "model_catalog", catalog)
    monkeypatch.setattr(main, "model_installer", installer)
    local = TestClient(main.app, client=("127.0.0.1", 50000))
    models = local.get("/api/models")
    assert models.status_code == 200
    known = next(row for row in models.json()["models"] if row["model"]["id"] == CHINESE_CLIP_VIT_B16.id)
    assert known["state"] == "missing_runtime" and known["ready"] is False
    endpoint = f"/api/models/{CHINESE_CLIP_VIT_B16.id}/install"
    denied = local.post(endpoint, json={"license_accepted": False})
    assert denied.status_code == 400
    assert "许可证" in denied.json()["detail"]
    remote = TestClient(main.app, client=("203.0.113.1", 50000))
    assert remote.post(endpoint, json={"license_accepted": True}).status_code == 403
    assert not installer.jobs_path.exists()
    identity = "a" * 32
    timestamp = "2026-10-30T00:00:00Z"
    job = {
        "id": identity, "model_id": CHINESE_CLIP_VIT_B16.id,
        "display_name": CHINESE_CLIP_VIT_B16.display_name,
        "status": "interrupted", "phase": "interrupted", "message": "metadata fixture only",
        "bytes_downloaded": 0, "bytes_total": 100, "progress": 0.0,
        "license_name": CHINESE_CLIP_VIT_B16.license_name,
        "license_url": CHINESE_CLIP_VIT_B16.license_url,
        "license_accepted_at": timestamp, "created_at": timestamp, "updated_at": timestamp,
        "model_root_path": str(catalog.model_root_path), "external_volume": None,
        "free_bytes": 1000, "required_bytes": 100,
    }
    installer.jobs_path.mkdir(parents=True)
    path = installer.jobs_path / f"{identity}.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    before = path.read_bytes()
    result = local.get(f"/api/model-install-jobs/{identity}")
    assert result.status_code == 200
    assert result.json() == job
    assert installer.get_job(identity) == job
    assert path.read_bytes() == before
    assert set(installer.jobs_path.iterdir()) == {path}
    assert violations == []
    assert installer._active_thread is None and installer._writer is None


def test_installer_blocks_unmounted_external_volume_without_fallback(monkeypatch):
    catalog = ModelCatalog(
        "/Volumes/MissingDisk/AI视频日记/models",
        "/Volumes/MissingDisk/AI视频日记/model-runtime/bin/python",
        "/tmp/worker.py",
    )
    installer = ModelInstaller(catalog)
    monkeypatch.setattr("app.model_installer.os.path.ismount", lambda _path: False)

    with pytest.raises(ModelInstallError, match="不会回退到内置硬盘"):
        installer.storage_preflight(CHINESE_CLIP_VIT_B16)


def test_interrupted_job_is_marked_resumable(tmp_path):
    catalog = ModelCatalog(tmp_path / "models", tmp_path / "python", tmp_path / "worker.py")
    jobs = catalog.model_root_path / ".install-jobs"
    jobs.mkdir(parents=True)
    path = jobs / f"{'a' * 32}.json"
    path.write_text(
        json.dumps({"id": "a" * 32, "status": "running"}),
        encoding="utf-8",
    )

    installer = ModelInstaller(catalog)
    assert installer.get_job("a" * 32)["status"] == "running"
    assert installer.recover_interrupted_jobs() == ["a" * 32]
    recovered = installer.get_job("a" * 32)

    assert recovered["status"] == "interrupted"
    assert "断点" in recovered["message"]


def test_successful_install_refreshes_runtime_dependencies(tmp_path, monkeypatch):
    refreshed: list[bool] = []
    downloads: list[str] = []
    catalog = ModelCatalog(
        tmp_path / "models",
        tmp_path / "runtime" / "bin" / "python",
        tmp_path / "worker.py",
    )

    class SuccessfulInstaller(ModelInstaller):
        def _ensure_runtime(self, _spec):
            return None

        def _download_model(self, _job_id, _spec):
            downloads.append(_spec.id)
            return []

        def _publish_model(self, _spec, _manifest_files):
            return None

    installer = SuccessfulInstaller(catalog, on_complete=lambda: refreshed.append(True))
    # This tests installation state/callbacks with a tiny download substitute,
    # not this machine's free capacity or actual model/runtime installation.
    capacity = {"free": 32 * 1024**3}
    monkeypatch.setattr("app.model_installer.shutil.disk_usage", lambda _path: SimpleNamespace(
        total=32 * 1024**3, used=32 * 1024**3 - capacity["free"], free=capacity["free"]))
    required = installer.storage_preflight(CHINESE_CLIP_VIT_B16)["required_bytes"]
    capacity["free"] = required - 1
    with pytest.raises(ModelInstallError, match="可用空间不足"):
        installer.run_sync(CHINESE_CLIP_VIT_B16.id, license_accepted=True)
    assert downloads == refreshed == []
    capacity["free"] = required
    job = installer.run_sync(CHINESE_CLIP_VIT_B16.id, license_accepted=True)

    assert job["status"] == "ready"
    assert refreshed == [True]
    assert downloads == [CHINESE_CLIP_VIT_B16.id]


def test_constructor_never_creates_storage_or_recovers_existing_json(tmp_path):
    root = tmp_path / "models"
    catalog = ModelCatalog(root, None, None)
    installer = ModelInstaller(catalog)
    assert not root.exists()
    assert installer.recover_interrupted_jobs() == []
    assert not root.exists()
    jobs = root / ".install-jobs"
    jobs.mkdir(parents=True)
    path = jobs / ("b" * 32 + ".json")
    path.write_text(json.dumps({"id": "b" * 32, "status": "running", "original_field": "preserve"}))
    before = path.read_bytes()
    replacement = ModelInstaller(catalog)
    assert path.read_bytes() == before
    assert replacement.recover_interrupted_jobs() == ["b" * 32]
    assert replacement.get_job("b" * 32)["original_field"] == "preserve"
    assert replacement.recover_interrupted_jobs() == []


@pytest.fixture
def controlled_install(tmp_path):
    """Actual job lifecycle and file lock; execution has no model/network work."""
    entered, release = threading.Event(), threading.Event()
    catalog = ModelCatalog(tmp_path / "models", tmp_path / "python", tmp_path / "worker.py")

    class ControlledInstaller(ModelInstaller):
        def storage_preflight(self, spec):
            return {}

        def _ensure_runtime(self, spec):
            pass

        def _download_model(self, job_id, spec):
            entered.set()
            assert release.wait(3)
            return []

        def _publish_model(self, spec, manifest):
            pass

    installer = ControlledInstaller(catalog)
    job = installer.start(CHINESE_CLIP_VIT_B16.id, license_accepted=True)
    assert entered.wait(2)
    try:
        yield installer, ControlledInstaller, catalog, job, release
    finally:
        release.set()
        installer._active_thread.join(3)
        assert not installer._active_thread.is_alive()


def test_reconstructed_installer_cannot_recover_or_write_a_live_install(controlled_install):
    installer, kind, catalog, job, release = controlled_install
    path = installer.jobs_path / f"{job['id']}.json"
    before = path.read_bytes()
    replacement = kind(catalog)
    assert path.read_bytes() == before
    assert replacement.recover_interrupted_jobs() == []
    assert path.read_bytes() == before
    assert replacement.get_job(job["id"])["status"] == "running"
    for candidate in (installer, replacement):
        with pytest.raises(ValueError, match="已有模型安装任务在运行"):
            candidate.run_sync(CHINESE_CLIP_VIT_B16.id, license_accepted=True)
    release.set()
    installer._active_thread.join(3)
    assert replacement.get_job(job["id"])["status"] == "ready"


def test_active_install_rebind_preserves_snapshot_and_idle_rebind_does_not_recover(controlled_install, tmp_path):
    installer, kind, catalog, job, release = controlled_install
    same = ModelCatalog(catalog.model_root_path, catalog.runtime_python_path, catalog.visual_worker_path)
    installer.rebind_catalog(same)
    assert installer.catalog is catalog
    changed = ModelCatalog(tmp_path / "another-root", catalog.runtime_python_path, catalog.visual_worker_path)
    with pytest.raises(ModelInstallError, match="保持原绑定"):
        installer.rebind_catalog(changed)
    changed_runtime = ModelCatalog(catalog.model_root_path, tmp_path / "different-python", catalog.visual_worker_path)
    with pytest.raises(ModelInstallError, match="保持原绑定"):
        installer.rebind_catalog(changed_runtime)
    assert not changed.model_root_path.exists()
    old_path = installer.jobs_path / f"{job['id']}.json"
    release.set()
    installer._active_thread.join(3)
    installer.rebind_catalog(changed)
    assert installer.catalog is changed
    assert not changed.model_root_path.exists()
    assert json.loads(old_path.read_text())["status"] == "ready"


def test_prospective_catalog_guard_rejects_before_env_write_and_never_recovers(controlled_install, tmp_path):
    installer, kind, catalog, job, release = controlled_install
    env_file = tmp_path / "prospective.env"
    changed = ModelCatalog(tmp_path / "other-models", catalog.runtime_python_path, catalog.visual_worker_path)
    before = (installer.jobs_path / f"{job['id']}.json").read_bytes()
    with pytest.raises(ModelInstallError, match="保持原绑定"):
        with installer.guard_catalog_update(changed):
            env_file.write_text("MODEL_ROOT_PATH=would-be-partial")
            installer.rebind_catalog(changed)
    assert not env_file.exists()
    same = ModelCatalog(catalog.model_root_path, catalog.runtime_python_path, catalog.visual_worker_path)
    installer.assert_catalog_rebindable(same)
    assert (installer.jobs_path / f"{job['id']}.json").read_bytes() == before


def test_catalog_guard_keeps_new_start_out_until_write_and_binding_finish(tmp_path):
    entered, returned = threading.Event(), threading.Event()

    class ControlledInstaller(ModelInstaller):
        def storage_preflight(self, spec):
            return {}

        def _ensure_runtime(self, spec):
            pass

        def _download_model(self, job, spec):
            entered.set()
            return []

        def _publish_model(self, spec, files):
            pass

    installer = ControlledInstaller(ModelCatalog(tmp_path / "old", None, None))
    candidate = ModelCatalog(tmp_path / "new", None, None)
    jobs = []

    def start():
        jobs.append(installer.start(CHINESE_CLIP_VIT_B16.id, license_accepted=True))
        returned.set()

    worker = threading.Thread(target=start)
    with installer.guard_catalog_update(candidate):
        worker.start()
        assert not returned.wait(.05)
        assert not (tmp_path / "old").exists() and not (tmp_path / "new").exists()
        (tmp_path / "prospective.env").write_text("MODEL_ROOT_PATH=new")
        installer.rebind_catalog(candidate)
    worker.join(2)
    installer._active_thread.join(2)
    assert returned.is_set() and entered.is_set()
    assert not (tmp_path / "old").exists()
    assert (candidate.model_root_path / ".install-jobs" / f"{jobs[0]['id']}.json").is_file()


def test_writer_lock_excludes_another_process_and_releases_with_owner(tmp_path):
    store = InstallJobStore(tmp_path / "models")
    writer = store.claim_writer()
    assert writer is not None
    code = ("from pathlib import Path;import sys;from app.model_install_jobs import InstallJobStore;"
        "store=InstallJobStore(Path(sys.argv[1]));owner=store.claim_writer();"
        "print(owner is not None);owner.close() if owner else None")

    def claimed():
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "models")],
            cwd=Path(__file__).parents[1], env={"PATH": os.environ["PATH"]},
            capture_output=True, text=True, timeout=5, check=True)
        return result.stdout.strip()

    try:
        assert claimed() == "False"
    finally:
        writer.close()
    assert claimed() == "True"
    with pytest.raises(ModelInstallError, match="写入责任已释放"):
        store.write({"id": "a" * 32, "status": "running"}, writer)
    assert not (store.jobs_path / ("a" * 32 + ".json")).exists()


def test_execution_failure_releases_writer_and_preserves_failed_job(tmp_path):
    class FailedInstaller(ModelInstaller):
        def storage_preflight(self, spec):
            return {}

        def _ensure_runtime(self, spec):
            raise ModelInstallError("synthetic runtime failure")

    installer = FailedInstaller(ModelCatalog(tmp_path / "models", None, None))
    job = installer.run_sync(CHINESE_CLIP_VIT_B16.id, license_accepted=True)
    assert job["status"] == "failed" and job["message"] == "synthetic runtime failure"
    other = ModelInstaller(installer.catalog)
    assert other.recover_interrupted_jobs() == []
    owner = other._jobs.claim_writer()
    assert owner is not None
    owner.close()


def test_thread_launch_failure_has_failed_receipt_and_releases_owner(tmp_path, monkeypatch):
    installer = ModelInstaller(ModelCatalog(tmp_path / "models", None, None))
    monkeypatch.setattr(installer, "storage_preflight", lambda spec: {})
    monkeypatch.setattr("app.model_installer.threading.Thread.start", lambda _thread: (_ for _ in ()).throw(RuntimeError("synthetic launch failure")))
    with pytest.raises(RuntimeError, match="synthetic launch failure"):
        installer.start(CHINESE_CLIP_VIT_B16.id, license_accepted=True)
    record = json.loads(next(installer.jobs_path.glob("*.json")).read_text())
    assert record["status"] == "failed" and "线程未能启动" in record["message"]
    owner = installer._jobs.claim_writer()
    assert owner is not None
    owner.close()


def test_initial_record_write_failure_releases_claim_without_launch(tmp_path, monkeypatch):
    installer = ModelInstaller(ModelCatalog(tmp_path / "models", None, None))
    monkeypatch.setattr(installer, "storage_preflight", lambda spec: {})
    with monkeypatch.context() as patch:
        patch.setattr("app.model_install_jobs._atomic_json", lambda *_args: (_ for _ in ()).throw(PermissionError("synthetic write denial")))
        patch.setattr("app.model_installer.threading.Thread.start", lambda _thread: pytest.fail("failed record write launched execution"))
        with pytest.raises(PermissionError, match="synthetic write denial"):
            installer.start(CHINESE_CLIP_VIT_B16.id, license_accepted=True)
    owner = installer._jobs.claim_writer()
    assert owner is not None
    owner.close()


def test_runtime_execution_keeps_pinned_requirements_and_existing_marker_format(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime/bin/python"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("synthetic executable; never run")
    catalog = ModelCatalog(tmp_path / "models", runtime, None)
    calls = []
    monkeypatch.setattr(execution.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)) or subprocess.CompletedProcess(command, 0))
    execution.ensure_runtime(catalog, CHINESE_CLIP_VIT_B16)
    expected = sorted(execution.MODEL_RUNTIME_REQUIREMENTS[CHINESE_CLIP_VIT_B16.id])
    assert calls[0][0] == [str(runtime), "-m", "pip", "install", *expected]
    assert calls[0][1]["env"]["PIP_CACHE_DIR"] == str(catalog.model_root_path / ".cache/pip")
    marker = json.loads((runtime.parent.parent / "installed-requirements.json").read_text())
    assert marker == {"schema_version": 1, "requirements": expected}
    execution.ensure_runtime(catalog, CHINESE_CLIP_VIT_B16)
    assert len(calls) == 1


def test_tiny_resumed_artifact_preserves_hash_manifest_and_publish_without_network(tmp_path, monkeypatch):
    from app.model_runtime import DownloadFile
    import hashlib
    spec = CHINESE_CLIP_VIT_B16
    staging = tmp_path / ".staging"
    part = staging / spec.id / "tiny.bin.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"ab")
    expected = DownloadFile("tiny.bin", 6)
    monkeypatch.setattr(execution, "MODEL_FILES", {spec.id: (expected,)})
    requests, updates = [], []

    class TinyResponse:
        status = 206
        remaining = b"cdef"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, size):
            value, self.remaining = self.remaining, b""
            return value

    def receive(request, timeout):
        requests.append((request.full_url, request.get_header("Range"), timeout))
        return TinyResponse()

    monkeypatch.setattr(execution.urllib.request, "urlopen", receive)
    report = lambda job, **values: updates.append((job, values))
    download = lambda *args: execution.download_file(report, *args)
    files = execution.download_model(staging, "synthetic-job", spec, download, report)
    assert requests == [(f"https://huggingface.co/{spec.repository}/resolve/{spec.revision}/tiny.bin", "bytes=2-", 60)]
    assert files == [{"path": "tiny.bin", "size": 6, "sha256": hashlib.sha256(b"abcdef").hexdigest()}]
    assert updates[-1][1]["bytes_downloaded"] == 6
    catalog = ModelCatalog(tmp_path / "models", None, None)
    execution.publish_model(catalog, staging, spec, files)
    assert (catalog.model_path(spec) / "tiny.bin").read_bytes() == b"abcdef"
    manifest = json.loads((catalog.model_path(spec) / "manifest.json").read_text())
    assert manifest == {"schema_version": 1, "model_id": spec.id, "repository": spec.repository,
        "revision": spec.revision, "files": files}


def test_unsupported_writer_lock_platform_refuses_before_storage_creation(tmp_path, monkeypatch):
    root = tmp_path / "models"
    store = InstallJobStore(root)
    with monkeypatch.context() as patch:
        patch.setattr("app.model_install_jobs.os.name", "nt")
        with pytest.raises(ModelInstallError, match="未开始安装或恢复"):
            store.claim_writer()
    assert not root.exists()
