from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Protocol, Sequence

from app.model_runtime import CHINESE_CLIP_VIT_B16, ModelCatalog


class VisualSemanticError(RuntimeError):
    pass


@dataclass(frozen=True)
class SegmentWindow:
    segment_id: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class VisualEmbedding:
    segment_id: str
    vector: list[float]
    sample_count: int


class VisualSemanticProvider(Protocol):
    model_id: str

    @property
    def ready(self) -> bool: ...

    @property
    def message(self) -> str: ...

    def embed_segments(
        self,
        video_path: str | Path,
        segments: Sequence[SegmentWindow],
    ) -> list[VisualEmbedding]: ...

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...


class UnavailableVisualSemanticProvider:
    model_id = CHINESE_CLIP_VIT_B16.id

    def __init__(self, message: str = "中文画面语义模型尚未安装。") -> None:
        self._message = message

    @property
    def ready(self) -> bool:
        return False

    @property
    def message(self) -> str:
        return self._message

    def embed_segments(
        self,
        video_path: str | Path,
        segments: Sequence[SegmentWindow],
    ) -> list[VisualEmbedding]:
        raise VisualSemanticError(self.message)

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        raise VisualSemanticError(self.message)


class ChineseClipSubprocessProvider:
    model_id = CHINESE_CLIP_VIT_B16.id

    def __init__(
        self,
        *,
        runtime_python_path: str | Path,
        worker_path: str | Path,
        model_path: str | Path,
        runner: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.runtime_python_path = Path(runtime_python_path).expanduser()
        self.worker_path = Path(worker_path).expanduser()
        self.model_path = Path(model_path).expanduser()
        self._runner = runner or self._run_command

    @property
    def ready(self) -> bool:
        return (
            self.runtime_python_path.is_file()
            and self.worker_path.is_file()
            and (self.model_path / "manifest.json").is_file()
        )

    @property
    def message(self) -> str:
        return (
            "中文画面语义模型已就绪。"
            if self.ready
            else "中文画面语义模型运行环境不完整。"
        )

    def embed_segments(
        self,
        video_path: str | Path,
        segments: Sequence[SegmentWindow],
    ) -> list[VisualEmbedding]:
        path = Path(video_path).expanduser()
        if not self.ready:
            raise VisualSemanticError(self.message)
        if not path.is_file():
            raise VisualSemanticError("待分析视频不存在")
        if not segments:
            return []
        payload = self._call_worker(
            {
                "mode": "image",
                "video_path": str(path),
                "segments": [
                    {
                        "segment_id": item.segment_id,
                        "start_seconds": item.start_seconds,
                        "end_seconds": item.end_seconds,
                    }
                    for item in segments
                ],
            }
        )
        try:
            return _parse_embeddings(payload, segments)
        except (ValueError, TypeError) as exc:
            raise VisualSemanticError(f"画面语义结果无效：{exc}") from exc

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.ready:
            raise VisualSemanticError(self.message)
        clean = [text.strip() for text in texts]
        if not clean or any(not text for text in clean):
            raise VisualSemanticError("待匹配文案不能为空")
        payload = self._call_worker({"mode": "text", "texts": clean})
        try:
            return _parse_text_embeddings(payload, len(clean))
        except (ValueError, TypeError) as exc:
            raise VisualSemanticError(f"文案语义结果无效：{exc}") from exc

    def _call_worker(self, request_payload: dict[str, object]) -> object:
        with TemporaryDirectory(prefix="ai_video_visual_") as temporary_dir:
            request_path = Path(temporary_dir) / "request.json"
            response_path = Path(temporary_dir) / "response.json"
            request_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model_id": self.model_id,
                        "model_path": str(self.model_path),
                        **request_payload,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            command = [
                str(self.runtime_python_path),
                str(self.worker_path),
                "--request",
                str(request_path),
                "--response",
                str(response_path),
            ]
            try:
                self._runner(command)
            except (OSError, subprocess.SubprocessError) as exc:
                raise VisualSemanticError(f"画面语义工作进程失败：{exc}") from exc
            if not response_path.is_file():
                raise VisualSemanticError("画面语义工作进程没有返回结果")
            try:
                payload = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise VisualSemanticError(f"画面语义结果无效：{exc}") from exc
        return payload

    def _run_command(self, command: list[str]) -> None:
        cache_root = self.model_path.parent / ".cache"
        temporary_root = cache_root / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        environment.update(
            {
                "HF_HOME": str(cache_root / "huggingface"),
                "TORCH_HOME": str(cache_root / "torch"),
                "XDG_CACHE_HOME": str(cache_root),
                "TMPDIR": str(temporary_root),
            }
        )
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30 * 60,
            env=environment,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            if len(detail) > 4000:
                detail = detail[-4000:]
            raise VisualSemanticError(
                f"画面语义工作进程失败：{detail or f'exit {completed.returncode}'}"
            )


def visual_semantic_provider_from_catalog(
    catalog: ModelCatalog,
) -> VisualSemanticProvider:
    status = catalog.status(CHINESE_CLIP_VIT_B16)
    if not status.ready or not status.runtime_python_path or not status.worker_path:
        return UnavailableVisualSemanticProvider(status.message)
    return ChineseClipSubprocessProvider(
        runtime_python_path=status.runtime_python_path,
        worker_path=status.worker_path,
        model_path=status.model_path,
    )


def _parse_embeddings(
    payload: object,
    requested_segments: Sequence[SegmentWindow],
) -> list[VisualEmbedding]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("响应协议版本错误")
    if payload.get("model_id") != CHINESE_CLIP_VIT_B16.id:
        raise ValueError("响应模型不匹配")
    raw_items = payload.get("embeddings")
    if not isinstance(raw_items, list):
        raise ValueError("响应缺少向量")
    expected_ids = {item.segment_id for item in requested_segments}
    results: list[VisualEmbedding] = []
    dimensions: set[int] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("向量记录格式错误")
        segment_id = item.get("segment_id")
        raw_vector = item.get("vector")
        sample_count = item.get("sample_count")
        if not isinstance(segment_id, str) or segment_id not in expected_ids:
            raise ValueError("响应包含未知分段")
        if not isinstance(raw_vector, list) or not raw_vector:
            raise ValueError("向量为空")
        vector = [float(value) for value in raw_vector]
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("向量包含非有限值")
        if not isinstance(sample_count, int) or not 1 <= sample_count <= 3:
            raise ValueError("抽帧数量无效")
        dimensions.add(len(vector))
        results.append(
            VisualEmbedding(
                segment_id=segment_id,
                vector=vector,
                sample_count=sample_count,
            )
        )
    if {item.segment_id for item in results} != expected_ids:
        raise ValueError("响应分段不完整")
    if len(dimensions) != 1:
        raise ValueError("向量维度不一致")
    return results


def _parse_text_embeddings(payload: object, expected_count: int) -> list[list[float]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("响应协议版本错误")
    if payload.get("model_id") != CHINESE_CLIP_VIT_B16.id:
        raise ValueError("响应模型不匹配")
    raw_items = payload.get("text_embeddings")
    if not isinstance(raw_items, list) or len(raw_items) != expected_count:
        raise ValueError("文案向量数量不匹配")
    results: list[list[float]] = []
    dimensions: set[int] = set()
    for raw_vector in raw_items:
        if not isinstance(raw_vector, list) or not raw_vector:
            raise ValueError("文案向量为空")
        vector = [float(value) for value in raw_vector]
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("文案向量包含非有限值")
        results.append(vector)
        dimensions.add(len(vector))
    if len(dimensions) != 1:
        raise ValueError("文案向量维度不一致")
    return results
