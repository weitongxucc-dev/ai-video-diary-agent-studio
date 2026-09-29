import hashlib
import json

from app.model_runtime import (
    CHINESE_CLIP_VIT_B16,
    QWEN3_TTS_06B_BASE_8BIT,
    QWEN3_VL_4B_4BIT,
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


def test_readiness_lists_every_gap_instead_of_only_the_first(tmp_path):
    """An empty machine must name every outstanding prerequisite.

    ``status()`` reports 「本地模型运行环境尚未安装。」 and stops there, so a user
    never learns that a multi-gigabyte download is *also* outstanding. Readiness
    exists so the whole picture is visible before any worker is spawned.
    """
    catalog = ModelCatalog(tmp_path / "models", tmp_path / "python", tmp_path / "worker.py")

    readiness = catalog.readiness(QWEN3_VL_4B_4BIT)

    assert readiness.available is False
    assert readiness.label == "未安装"
    assert len(readiness.problems) == 3
    assert any("运行环境缺失" in problem for problem in readiness.problems)
    assert any("工作进程缺失" in problem for problem in readiness.problems)
    assert any("权重未下载" in problem for problem in readiness.problems)
    assert "Qwen3-VL 4B 4-bit 未安装" in readiness.message
    # The repair action must be actionable, not a shrug.
    assert QWEN3_VL_4B_4BIT.repository in readiness.repair_action
    assert "2.9 GiB" in readiness.repair_action
    # 状态机本身没有变：它仍然在第一个缺口处停下。
    assert catalog.status(QWEN3_VL_4B_4BIT).state == "missing_runtime"


def test_readiness_distinguishes_missing_weights_from_broken_install(tmp_path):
    runtime_python = tmp_path / "python"
    worker = tmp_path / "worker.py"
    runtime_python.write_text("python", encoding="utf-8")
    worker.write_text("worker", encoding="utf-8")
    catalog = ModelCatalog(tmp_path / "models", runtime_python, worker)
    model_path = catalog.model_path(CHINESE_CLIP_VIT_B16)
    model_path.mkdir(parents=True)

    # 权重目录存在但 manifest 没写完 —— 半路下载过。
    (model_path / "model.safetensors").write_bytes(b"partial")
    (model_path / "manifest.json").write_text("{", encoding="utf-8")

    readiness = catalog.readiness(CHINESE_CLIP_VIT_B16)

    assert readiness.available is False
    assert readiness.label == "安装不完整"
    assert len(readiness.problems) == 1
    assert readiness.problems[0].startswith("安装不完整：")
    assert "重新下载" in readiness.repair_action
    # 「跑一半才炸」的前置：状态机在同一个环境下也判 invalid，而不是 ready。
    assert catalog.status(CHINESE_CLIP_VIT_B16).state == "invalid"


def test_readiness_reports_weights_present_but_runtime_missing_as_environment_problem(tmp_path):
    """权重已经在硬盘上时，重装运行环境比重下几 GB 权重便宜得多。

    所以这里必须报「运行环境不完整」而不是「未安装」—— 标签决定了用户该做什么。
    """
    runtime_python = tmp_path / "runtime" / "python"
    worker = tmp_path / "worker.py"
    worker.write_text("worker", encoding="utf-8")
    catalog = ModelCatalog(tmp_path / "models", runtime_python, worker)
    model_path = catalog.model_path(CHINESE_CLIP_VIT_B16)
    model_path.mkdir(parents=True)
    weights = model_path / "model.safetensors"
    weights.write_bytes(b"weights")
    (model_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_id": CHINESE_CLIP_VIT_B16.id,
                "repository": CHINESE_CLIP_VIT_B16.repository,
                "revision": CHINESE_CLIP_VIT_B16.revision,
                "files": [{"path": weights.name, "size": weights.stat().st_size}],
            }
        ),
        encoding="utf-8",
    )

    readiness = catalog.readiness(CHINESE_CLIP_VIT_B16)

    assert readiness.available is False
    assert readiness.label == "运行环境不完整"
    assert not any("权重未下载" in problem for problem in readiness.problems)
    assert "运行环境" in readiness.repair_action


def test_ready_model_states_its_scale_quantization_and_resident_floor(tmp_path):
    """可用时必须报出可核对的量，并且把没实测的部分明说没实测。"""
    runtime_python = tmp_path / "python"
    worker = tmp_path / "worker.py"
    runtime_python.write_text("python", encoding="utf-8")
    worker.write_text("worker", encoding="utf-8")
    catalog = ModelCatalog(tmp_path / "models", runtime_python, worker)
    model_path = catalog.model_path(CHINESE_CLIP_VIT_B16)
    model_path.mkdir(parents=True)
    weights = model_path / "model.safetensors"
    weights.write_bytes(b"weights")
    (model_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_id": CHINESE_CLIP_VIT_B16.id,
                "repository": CHINESE_CLIP_VIT_B16.repository,
                "revision": CHINESE_CLIP_VIT_B16.revision,
                "files": [{"path": weights.name, "size": weights.stat().st_size}],
            }
        ),
        encoding="utf-8",
    )

    readiness = catalog.readiness(CHINESE_CLIP_VIT_B16)

    assert readiness.available is True
    assert readiness.label == "可用"
    assert readiness.problems == ()
    assert CHINESE_CLIP_VIT_B16.display_name in readiness.message
    assert "参数规模" in readiness.message
    assert "量化" in readiness.message
    assert "未实测" in readiness.message
    # 参数量不可得时必须明写「未实测」，不许编一个数字。
    assert "未实测" in CHINESE_CLIP_VIT_B16.parameter_scale
    # 状态机的就绪分支走的是同一条文案，两处不会各说各话。
    assert catalog.status(CHINESE_CLIP_VIT_B16).message == readiness.message
