import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.visual_semantics import (
    ChineseClipSubprocessProvider,
    SegmentWindow,
    VisualSemanticError,
)
from model_workers.chinese_clip_worker import _feature_tensor


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
