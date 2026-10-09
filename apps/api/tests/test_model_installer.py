import json
from types import SimpleNamespace

import pytest

from app.model_installer import ModelInstaller, ModelInstallError
from app.model_runtime import CHINESE_CLIP_VIT_B16, ModelCatalog


def test_installer_requires_explicit_license_acceptance(tmp_path):
    installer = ModelInstaller(
        ModelCatalog(tmp_path / "models", tmp_path / "python", tmp_path / "worker.py")
    )

    with pytest.raises(ValueError, match="许可证"):
        installer.run_sync(CHINESE_CLIP_VIT_B16.id, license_accepted=False)


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
