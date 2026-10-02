import hashlib
import json
from dataclasses import replace

from app import model_runtime
from app.model_runtime import (
    CHINESE_CLIP_VIT_B16,
    QWEN3_TTS_06B_BASE_8BIT,
    QWEN3_VL_4B_4BIT,
    DownloadFile,
    ModelCatalog,
    inspect_weights_directory,
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
    assert readiness.label == "未找到"
    assert readiness.severity == "missing"
    assert len(readiness.problems) == 3
    assert any("运行环境缺失" in problem for problem in readiness.problems)
    assert any("工作进程缺失" in problem for problem in readiness.problems)
    assert any("权重未下载" in problem for problem in readiness.problems)
    assert "Qwen3-VL 4B 4-bit 未找到" in readiness.message
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


def test_inspect_weights_separates_absent_incomplete_and_complete(tmp_path):
    """三态判据：目录不存在 / 找到了但不完整 / 齐全，各说各话。

    判据只看目录里真实有什么。``manifest.json`` 是本项目自己写的安装记录，
    拿它当门槛会把「上游权重俱全的目录」和「空目录」判成同一件事。
    """
    expected = (
        DownloadFile("config.json", 12),
        DownloadFile("model.safetensors", 34),
    )

    absent = inspect_weights_directory(tmp_path / "no-such-model", expected)
    assert absent.state == "not_found"
    assert absent.exists is False
    assert [item.path for item in absent.missing] == ["config.json", "model.safetensors"]
    assert "不存在" in absent.describe()

    half = tmp_path / "half"
    half.mkdir()
    (half / "config.json").write_bytes(b"012345678901")
    inspection = inspect_weights_directory(half, expected)
    assert inspection.state == "incomplete"
    assert [item.path for item in inspection.present] == ["config.json"]
    assert [item.path for item in inspection.missing] == ["model.safetensors"]
    assert "model.safetensors" in inspection.describe()

    (half / "model.safetensors").write_bytes(b"x" * 34)
    complete = inspect_weights_directory(half, expected)
    assert complete.state == "complete"
    assert complete.wrong_size == ()

    # 大小对不上是另一种「不完整」，也必须被算成不完整而不是放过去。
    (half / "model.safetensors").write_bytes(b"x" * 33)
    assert inspect_weights_directory(half, expected).state == "incomplete"


def test_inspect_weights_refuses_to_call_an_unlisted_directory_complete(tmp_path):
    """没有期望文件清单就没有「齐全」这个结论可说。

    LatentSync-1.6 就是这种目录：里面躺着两个 unet，缺 vae 和 pose，
    光看目录存在就报「完整」正是本模块要消灭的那种谎。
    """
    directory = tmp_path / "LatentSync-1.6"
    directory.mkdir()
    (directory / "latentsync_unet.pt").write_bytes(b"unet")

    inspection = inspect_weights_directory(directory)

    assert inspection.state == "unknown"
    assert "没有它的期望文件清单" in inspection.describe()
    # 目录里实际有什么照样如实报出来，不因为「无法判定」就装作没看见。
    assert inspection.unlisted == ("latentsync_unet.pt",)


def test_readiness_does_not_ask_for_a_redownload_when_the_weights_are_there(
    tmp_path, monkeypatch,
):
    """权重齐全、只缺安装记录时，劝用户重下几 GB 是错的。"""
    runtime_python = tmp_path / "python"
    worker = tmp_path / "worker.py"
    runtime_python.write_text("python", encoding="utf-8")
    worker.write_text("worker", encoding="utf-8")
    expected = (
        DownloadFile("config.json", 12),
        DownloadFile("model.safetensors", 34),
    )
    # 真实清单里 Chinese-CLIP 有一个 753 MB 的 pytorch_model.bin，测试里写不出来，
    # 所以换一条同构的假 spec 来覆盖这条分支。分支逻辑与清单内容无关。
    spec = replace(CHINESE_CLIP_VIT_B16, id="probe-tiny")
    monkeypatch.setattr(model_runtime, "MODEL_FILES", {spec.id: expected})
    catalog = ModelCatalog(tmp_path / "models", runtime_python, worker)
    model_path = catalog.model_path(spec)
    model_path.mkdir(parents=True)
    for item in expected:
        (model_path / item.path).write_bytes(b"x" * item.size)
    assert not (model_path / "manifest.json").exists()

    readiness = catalog.readiness(spec)

    assert readiness.available is False
    assert readiness.severity == "degraded"
    assert readiness.label == "权重齐全，缺安装记录"
    assert "已有 2/2 个上游文件" in readiness.message
    assert "不必重新下载权重" in readiness.repair_action


def test_readiness_names_the_files_that_are_actually_missing(tmp_path):
    """找到了但不完整时，要报出缺的是哪几个文件，不是一句「未安装」。"""
    runtime_python = tmp_path / "python"
    worker = tmp_path / "worker.py"
    runtime_python.write_text("python", encoding="utf-8")
    worker.write_text("worker", encoding="utf-8")
    catalog = ModelCatalog(tmp_path / "models", runtime_python, worker)
    spec = CHINESE_CLIP_VIT_B16
    model_path = catalog.model_path(spec)
    model_path.mkdir(parents=True)
    (model_path / "config.json").write_bytes(b"0" * 3_008)
    (model_path / "vocab.txt").write_bytes(b"0" * 109_540)
    # pytorch_model.bin（753 MB）没下下来。

    readiness = catalog.readiness(spec)

    assert readiness.available is False
    assert readiness.severity == "invalid"
    assert readiness.label == "找到了但不完整"
    assert any("pytorch_model.bin" in problem for problem in readiness.problems)
    assert "已有 2/4 个上游文件" in readiness.message
    assert "重新下载" in readiness.repair_action
