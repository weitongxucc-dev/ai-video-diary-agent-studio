import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.model_runtime import CHINESE_CLIP_VIT_B16, ModelCatalog
from app.visual_semantics import (
    ChineseClipSubprocessProvider,
    SegmentWindow,
    VisualSemanticError,
    evidence_timestamps,
    visual_semantic_provider_from_catalog,
)
from model_workers.chinese_clip_worker import _feature_tensor, _sample_timestamps

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


def _provider(tmp_path: Path, responder):
    runtime_python = tmp_path / "python"
    worker = tmp_path / "worker.py"
    model = tmp_path / "model"
    runtime_python.write_text("python", encoding="utf-8")
    worker.write_text("worker", encoding="utf-8")
    model.mkdir()
    (model / "manifest.json").write_text("{}", encoding="utf-8")

    def runner(command):
        request_path = Path(command[command.index("--request") + 1])
        response_path = Path(command[command.index("--response") + 1])
        responder(json.loads(request_path.read_text(encoding="utf-8")), response_path)

    return ChineseClipSubprocessProvider(
        runtime_python_path=runtime_python,
        worker_path=worker,
        model_path=model,
        runner=runner,
    )


def test_subprocess_provider_validates_image_and_text_protocols(tmp_path):
    def responder(request, response_path):
        if request["mode"] == "image":
            payload = {
                "schema_version": 1,
                "model_id": "chinese-clip-vit-base-patch16",
                "embeddings": [
                    {
                        "segment_id": item["segment_id"],
                        "vector": [0.6, 0.8],
                        "sample_count": 3,
                    }
                    for item in request["segments"]
                ],
            }
        else:
            payload = {
                "schema_version": 1,
                "model_id": "chinese-clip-vit-base-patch16",
                "text_embeddings": [[1.0, 0.0] for _text in request["texts"]],
            }
        response_path.write_text(json.dumps(payload), encoding="utf-8")

    provider = _provider(tmp_path, responder)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")

    images = provider.embed_segments(
        video,
        [SegmentWindow("segment_1", 0.0, 4.0)],
    )
    texts = provider.embed_texts(["城市夜景", "团队办公"])

    assert images[0].vector == [0.6, 0.8]
    assert images[0].sample_count == 3
    assert texts == [[1.0, 0.0], [1.0, 0.0]]


def test_subprocess_provider_rejects_partial_segment_response(tmp_path):
    def responder(_request, response_path):
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "model_id": "chinese-clip-vit-base-patch16",
                    "embeddings": [],
                }
            ),
            encoding="utf-8",
        )

    provider = _provider(tmp_path, responder)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")

    with pytest.raises(VisualSemanticError, match="响应分段不完整"):
        provider.embed_segments(
            video,
            [SegmentWindow("segment_1", 0.0, 4.0)],
        )


def test_worker_accepts_transformers_4_tensor_and_transformers_5_output():
    tensor = object()

    assert _feature_tensor(tensor) is tensor
    assert _feature_tensor(SimpleNamespace(pooler_output=tensor)) is tensor


def test_subprocess_provider_preserves_worker_stderr(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import sys\nsys.stderr.write('dependency mismatch')\nraise SystemExit(1)\n",
        encoding="utf-8",
    )
    model = tmp_path / "model"
    model.mkdir()
    (model / "manifest.json").write_text("{}", encoding="utf-8")
    provider = ChineseClipSubprocessProvider(
        runtime_python_path=sys.executable,
        worker_path=worker,
        model_path=model,
    )

    with pytest.raises(VisualSemanticError, match="dependency mismatch"):
        provider.embed_texts(["测试文案"])


def test_evidence_timestamps_never_diverge_from_the_worker(tmp_path):
    """证据时间戳必须和 worker 真正抽帧的秒数逐个相等。

    worker 只回报抽帧数量，不回报秒数，所以 ``visual_semantics`` 复刻了那条规则。
    一旦两边任一侧漂移，这个测试立刻失败，而不是让证据静默指向错误的秒。
    """
    windows = [
        (0.0, 3.0),
        (0.0, 1.4999),
        (0.0, 1.5),
        (0.0, 1.0),
        (0.0, 0.0),
        (12.345, 12.345 + 7.891),
        (100.0, 100.4),
        (0.0, 3600.0),
    ]

    for start, end in windows:
        assert evidence_timestamps(start, end) == _sample_timestamps(start, end), (
            f"采样规则与 worker 不一致：{start}~{end}"
        )

    # 规则本身的形状也要钉住：短片段取中点，长片段取 25%/50%/75%。
    assert evidence_timestamps(0.0, 1.0) == [0.5]
    assert evidence_timestamps(0.0, 3.0) == [0.75, 1.5, 2.25]


def test_missing_model_never_degrades_into_empty_or_fake_labels(tmp_path):
    """空环境必须说「未找到」，不许返回空标签或假向量。

    这是验收判据 (a)：整条链路的降级都要可见、可测，不许假装成功。
    """
    catalog = ModelCatalog(tmp_path / "models", tmp_path / "python", tmp_path / "worker.py")

    provider = visual_semantic_provider_from_catalog(catalog)

    assert provider.ready is False
    assert "未找到" in provider.message
    assert "权重未下载" in provider.message
    assert "处置：" in provider.message

    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    segments = [SegmentWindow("segment_1", 0.0, 4.0)]

    # 两个入口都必须抛错，而不是悄悄返回空结果。
    with pytest.raises(VisualSemanticError, match="未找到"):
        provider.embed_segments(video, segments)
    with pytest.raises(VisualSemanticError, match="未找到"):
        provider.embed_texts(["城市夜景"])


def test_broken_model_install_fails_before_any_worker_starts(tmp_path, monkeypatch):
    """权重校验不过时要当场报，不许跑到一半炸。"""
    runtime_python = tmp_path / "python"
    worker = tmp_path / "worker.py"
    runtime_python.write_text("python", encoding="utf-8")
    worker.write_text("worker", encoding="utf-8")
    catalog = ModelCatalog(tmp_path / "models", runtime_python, worker)
    model_path = catalog.model_path(CHINESE_CLIP_VIT_B16)
    model_path.mkdir(parents=True)
    # 目录在、manifest 也在，但内容写坏了 —— 半路下载 / 半路安装的典型样子。
    (model_path / "manifest.json").write_text("{", encoding="utf-8")

    def never_run(*args, **kwargs):
        raise AssertionError("校验未过就不该启动工作进程")

    monkeypatch.setattr(subprocess, "run", never_run)

    provider = visual_semantic_provider_from_catalog(catalog)

    assert provider.ready is False
    assert "安装不完整" in provider.message
    with pytest.raises(VisualSemanticError, match="安装不完整"):
        provider.embed_segments(tmp_path / "video.mp4", [SegmentWindow("segment_1", 0.0, 3.0)])


def _dominant_channel(red: int, green: int, blue: int) -> str:
    return "RGB"[(red, green, blue).index(max(red, green, blue))]


def _frame_average_rgb(video: Path, timestamp: float, side: int = 64) -> tuple[int, int, int]:
    """用 worker 同一条命令取出该秒的帧，返回整帧平均 RGB。"""
    completed = subprocess.run(
        [
            FFMPEG,
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    pixels = completed.stdout
    assert len(pixels) == side * side * 3, "抽出的帧尺寸不对"
    totals = [0, 0, 0]
    for offset in range(0, len(pixels), 3):
        for channel in range(3):
            totals[channel] += pixels[offset + channel]
    return tuple(total // (side * side) for total in totals)


def _make_colour_card(path: Path) -> None:
    """红 0~1s、绿 1~2s、蓝 2~3s 的合成素材，每秒内容确定且互不相同。"""
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:d=1:r=10",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=64x64:d=1:r=10",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:d=1:r=10",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@needs_ffmpeg
def test_evidence_frame_actually_contains_the_content_it_points_at(tmp_path):
    """验收判据 (b)：标签回指的帧，帧里确实有对应内容。

    素材是内容已知的合成色卡，所以「这一秒是什么颜色」是可直接核对的真值；
    证据时间戳指到哪一帧，哪一帧就必须是那个颜色。
    """
    video = tmp_path / "colour_card.mp4"
    _make_colour_card(video)
    windows = [SegmentWindow("segment_1", 0.0, 3.0)]
    expected = ["R", "G", "B"]

    def responder(request, response_path):
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "model_id": "chinese-clip-vit-base-patch16",
                    "embeddings": [
                        {
                            "segment_id": item["segment_id"],
                            "vector": [0.6, 0.8],
                            "sample_count": len(evidence_timestamps(item["start_seconds"], item["end_seconds"])),
                        }
                        for item in request["segments"]
                    ],
                }
            ),
            encoding="utf-8",
        )

    provider = _provider(tmp_path, responder)
    embeddings = provider.embed_segments(video, windows)
    evidence = embeddings[0].evidence

    assert [item.timestamp_seconds for item in evidence] == [0.75, 1.5, 2.25]
    for item, expected_channel in zip(evidence, expected):
        red, green, blue = _frame_average_rgb(video, item.timestamp_seconds)
        assert _dominant_channel(red, green, blue) == expected_channel, (
            f"{item.timestamp_seconds}s 的帧不是{expected_channel}色：rgb=({red},{green},{blue})"
        )
    # 置信度不是模型给的分数，而是该帧在均值池化里的实际占比。
    assert [round(item.confidence, 3) for item in evidence] == [0.333, 0.333, 0.333]
    assert all("ffmpeg -ss" in item.source for item in evidence)


@needs_ffmpeg
def test_short_segment_evidence_points_at_its_single_frame(tmp_path):
    video = tmp_path / "colour_card.mp4"
    _make_colour_card(video)

    def responder(request, response_path):
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "model_id": "chinese-clip-vit-base-patch16",
                    "embeddings": [
                        {"segment_id": "segment_1", "vector": [0.1, 0.2], "sample_count": 1}
                    ],
                }
            ),
            encoding="utf-8",
        )

    provider = _provider(tmp_path, responder)

    embeddings = provider.embed_segments(video, [SegmentWindow("segment_1", 1.0, 2.0)])

    evidence = embeddings[0].evidence
    assert [item.timestamp_seconds for item in evidence] == [1.5]
    assert evidence[0].confidence == 1.0
    red, green, blue = _frame_average_rgb(video, evidence[0].timestamp_seconds)
    assert _dominant_channel(red, green, blue) == "G"


def test_sample_count_disagreeing_with_the_sampling_rule_is_rejected(tmp_path):
    """worker 报告的抽帧数与规则不符时必须拒绝，不许把错的证据附上去。"""

    def responder(_request, response_path):
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "model_id": "chinese-clip-vit-base-patch16",
                    "embeddings": [
                        {"segment_id": "segment_1", "vector": [0.1, 0.2], "sample_count": 2}
                    ],
                }
            ),
            encoding="utf-8",
        )

    provider = _provider(tmp_path, responder)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")

    with pytest.raises(VisualSemanticError, match="抽帧数量与采样规则不符"):
        provider.embed_segments(video, [SegmentWindow("segment_1", 0.0, 3.0)])
