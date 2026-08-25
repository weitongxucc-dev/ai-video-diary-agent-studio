import hashlib
import json

from app.model_runtime import (
    CHINESE_CLIP_VIT_B16,
    QWEN3_TTS_06B_BASE_8BIT,
    ModelCatalog,
)


def test_model_catalog_requires_runtime_worker_and_complete_manifest(tmp_path):
    model_root = tmp_path / "models"
    runtime_python = tmp_path / "runtime" / "bin" / "python"
    worker = tmp_path / "worker.py"
    catalog = ModelCatalog(model_root, runtime_python, worker)

    assert catalog.status(CHINESE_CLIP_VIT_B16).state == "missing_runtime"

    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("python", encoding="utf-8")
    assert catalog.status(CHINESE_CLIP_VIT_B16).state == "missing_worker"

    worker.write_text("worker", encoding="utf-8")
    assert catalog.status(CHINESE_CLIP_VIT_B16).state == "not_installed"

    model_path = catalog.model_path(CHINESE_CLIP_VIT_B16)
    model_path.mkdir(parents=True)
    weights = model_path / "model.safetensors"
    weights.write_bytes(b"model weights")
    (model_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_id": CHINESE_CLIP_VIT_B16.id,
                "repository": CHINESE_CLIP_VIT_B16.repository,
                "revision": CHINESE_CLIP_VIT_B16.revision,
                "files": [
                    {
                        "path": weights.name,
                        "size": weights.stat().st_size,
                        "sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert catalog.status(CHINESE_CLIP_VIT_B16).state == "ready"
    assert catalog.verify(CHINESE_CLIP_VIT_B16).state == "ready"

    weights.write_bytes(b"tampered data")
    assert catalog.status(CHINESE_CLIP_VIT_B16).state == "ready"
    assert catalog.verify(CHINESE_CLIP_VIT_B16).state == "invalid"


def test_model_manifest_cannot_escape_model_directory(tmp_path):
    runtime_python = tmp_path / "python"
    worker = tmp_path / "worker.py"
    runtime_python.write_text("python", encoding="utf-8")
    worker.write_text("worker", encoding="utf-8")
    catalog = ModelCatalog(tmp_path / "models", runtime_python, worker)
    model_path = catalog.model_path(CHINESE_CLIP_VIT_B16)
    model_path.mkdir(parents=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    (model_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_id": CHINESE_CLIP_VIT_B16.id,
                "repository": CHINESE_CLIP_VIT_B16.repository,
                "revision": CHINESE_CLIP_VIT_B16.revision,
                "files": [
                    {
                        "path": "../../outside.bin",
                        "size": outside.stat().st_size,
                        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    status = catalog.status(CHINESE_CLIP_VIT_B16)

    assert status.state == "invalid"
    assert "路径越界" in status.message


def test_voice_model_uses_its_own_worker(tmp_path):
    runtime_python = tmp_path / "python"
    visual_worker = tmp_path / "visual.py"
    runtime_python.write_text("python", encoding="utf-8")
    visual_worker.write_text("worker", encoding="utf-8")
    catalog = ModelCatalog(
        tmp_path / "models",
        runtime_python,
        visual_worker,
        voice_worker_path=None,
    )

    status = catalog.status(QWEN3_TTS_06B_BASE_8BIT)

    assert status.state == "missing_worker"
