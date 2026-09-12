from dataclasses import dataclass, field
from contextlib import nullcontext
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Literal

from app.model_runtime import ModelCatalog, QWEN3_VL_4B_4BIT

OCR_RETRIEVAL_MIN_CONFIDENCE = 0.8
_vlm_execution_lock = Lock()


@dataclass
class SegmentUnderstanding:
    status: Literal["ready", "blocked", "failed"]
    fingerprint: str = ""
    model_id: str = ""
    sample_times: list[float] = field(default_factory=list)
    summary: str = ""
    facts: list[dict] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    ocr_status: Literal["ready", "blocked", "failed"] = "blocked"
    ocr_model_id: str = ""
    ocr_frames: list[dict] = field(default_factory=list)
    message: str = ""


def searchable_ocr_text(understanding: SegmentUnderstanding | None) -> str:
    if not understanding or understanding.ocr_status != "ready":
        return ""
    return " ".join(item["text"] for frame in understanding.ocr_frames
                    for item in frame["observations"] if item["confidence"] >= OCR_RETRIEVAL_MIN_CONFIDENCE)


def understanding_schema(frame_count: int) -> dict:
    text = {"type": "string", "minLength": 1, "maxLength": 100}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["summary", "facts", "unknowns"],
        "properties": {
            "summary": text,
            "facts": {"type": "array", "maxItems": 5, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["kind", "text", "frames"],
                "properties": {
                    "kind": {"enum": ["object", "action", "scene"]},
                    "text": text,
                    "frames": {"type": "array", "minItems": 1, "maxItems": frame_count,
                               "items": {"type": "integer", "enum": list(range(frame_count))}},
                },
            }},
            "unknowns": {"type": "array", "maxItems": 3, "items": text},
        },
    }


def parse_understanding(payload: dict, sample_times: list[float]) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"summary", "facts", "unknowns"}:
        raise ValueError("画面理解字段不完整")
    def text(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
            raise ValueError("画面理解文字无效")
        return value.strip()
    summary = text(payload["summary"])
    if not isinstance(payload["facts"], list) or len(payload["facts"]) > 5:
        raise ValueError("画面观察数量无效")
    facts = []
    for item in payload["facts"]:
        if not isinstance(item, dict) or set(item) != {"kind", "text", "frames"} or not isinstance(item["kind"], str) or item["kind"] not in {"object", "action", "scene"}:
            raise ValueError("画面观察类型无效")
        indices = item["frames"]
        if not isinstance(indices, list) or not indices or len(indices) > len(sample_times) or any(type(index) is not int or not 0 <= index < len(sample_times) for index in indices):
            raise ValueError("画面观察缺少有效采样帧")
        if item["kind"] == "action" and len({sample_times[index] for index in indices}) < 2:
            raise ValueError("动作观察需要至少两个不同时间的采样帧")
        facts.append({"kind": item["kind"], "text": text(item["text"]), "sample_times": [sample_times[index] for index in dict.fromkeys(indices)]})
    if not isinstance(payload["unknowns"], list) or len(payload["unknowns"]) > 3:
        raise ValueError("不确定项无效")
    return {"summary": summary, "facts": facts, "unknowns": [text(item) for item in payload["unknowns"]]}


def validate_ocr_frames(payload: dict, requested: list[dict]) -> tuple[str, list[dict]]:
    if payload.get("coordinate_space") != "normalized_top_left" or not isinstance(payload.get("model_id"), str):
        raise ValueError("OCR 坐标或模型标识无效")
    frames = payload.get("frames")
    if not isinstance(frames, list) or len(frames) != len(requested):
        raise ValueError("OCR 帧数量不匹配")
    for frame, source in zip(frames, requested, strict=True):
        if not isinstance(frame, dict):
            raise ValueError("OCR 帧格式无效")
        if frame.get("id") != source["id"] or frame.get("timestamp_seconds") != source["timestamp_seconds"]:
            raise ValueError("OCR 帧来源不匹配")
        if any(type(frame.get(key)) is not int or frame[key] <= 0 for key in ("width", "height")):
            raise ValueError("OCR 图像尺寸无效")
        if not isinstance(frame.get("observations"), list):
            raise ValueError("OCR 文字记录无效")
        for item in frame["observations"]:
            if not isinstance(item, dict):
                raise ValueError("OCR 文字记录格式无效")
            box = item.get("box")
            if not isinstance(item.get("text"), str) or not isinstance(box, list) or len(box) != 4:
                raise ValueError("OCR 文字或位置无效")
            numbers = [*box, item.get("confidence")]
            if any(type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1 for value in numbers):
                raise ValueError("OCR 位置超出图像范围")
            if box[0] + box[2] > 1.000001 or box[1] + box[3] > 1.000001:
                raise ValueError("OCR 文字框超出图像范围")
    return payload["model_id"], frames


class LocalVisualUnderstanding:
    def __init__(self, catalog: ModelCatalog, temporary_root: Path, ocr_worker: str | None):
        self.catalog = catalog
        self.temporary_root = temporary_root
        self.ocr_worker = Path(ocr_worker).expanduser() if ocr_worker else None

    def analyze(self, path: Path, segments: list, source_fingerprint: str) -> dict[str, SegmentUnderstanding]:
        status = self.catalog.status(QWEN3_VL_4B_4BIT)
        ocr_ready = self.ocr_worker is not None and self.ocr_worker.is_file()
        tool_identity = []
        for tool in (Path(status.worker_path) if status.worker_path else None, self.ocr_worker):
            if tool and tool.is_file():
                tool_identity.append(hashlib.sha256(tool.read_bytes()).hexdigest())
        self.temporary_root.mkdir(exist_ok=True)
        results = {}
        pending = []
        for segment in segments:
            fingerprint = hashlib.sha256(json.dumps([source_fingerprint, segment.start_seconds, segment.end_seconds,
                QWEN3_VL_4B_4BIT.revision, tool_identity, "3-frames:448-vlm:1280-ocr:v1"]).encode()).hexdigest()
            previous = segment.understanding
            if previous and previous.fingerprint == fingerprint and previous.status == "ready" and (not ocr_ready or previous.ocr_status == "ready"):
                results[segment.id] = previous
                continue
            result = SegmentUnderstanding("blocked", fingerprint=fingerprint, model_id=QWEN3_VL_4B_4BIT.id, message=status.message)
            results[segment.id] = result
            if status.ready or ocr_ready:
                pending.append((segment, result))
        for offset in range(0, len(pending), 12):
            batch = pending[offset:offset + 12]
            with TemporaryDirectory(prefix="material-visual-", dir=self.temporary_root) as directory:
                root = Path(directory)
                jobs = []
                ocr_requests = []
                for segment, result in batch:
                    times = [round(segment.start_seconds + (segment.end_seconds - segment.start_seconds) * fraction, 3) for fraction in (.15, .5, .85)]
                    result.sample_times = times
                    frames = []
                    segment_ocr_requests = []
                    try:
                        for index, timestamp in enumerate(times):
                            frame_id = f"{segment.id}-{index}"
                            large = root / f"{frame_id}.png"
                            small = root / f"{frame_id}-vlm.png"
                            subprocess.run(["ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", str(path), "-frames:v", "1",
                                "-vf", "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease", str(large)],
                                capture_output=True, check=True, timeout=30)
                            from PIL import Image
                            with Image.open(large) as image:
                                image.thumbnail((448, 448))
                                image.save(small)
                            frames.append({"id": frame_id, "path": str(small), "timestamp_seconds": timestamp})
                            segment_ocr_requests.append({"id": frame_id, "path": str(large), "timestamp_seconds": timestamp})
                        jobs.append({"id": segment.id, "frames": frames})
                        ocr_requests.extend(segment_ocr_requests)
                    except (OSError, subprocess.SubprocessError):
                        result.status = "failed"
                        result.ocr_status = "failed"
                        result.message = "采样帧读取失败，请检查原片。"
                if ocr_ready and ocr_requests:
                    try:
                        payload = self._run([str(self.ocr_worker)], {"frames": ocr_requests}, root, "ocr", 120)
                        model_id, frames = validate_ocr_frames(payload, ocr_requests)
                        for segment, result in batch:
                            if not any(job["id"] == segment.id for job in jobs):
                                continue
                            result.ocr_status = "ready"
                            result.ocr_model_id = model_id
                            result.ocr_frames = [frame for frame in frames if frame["id"].startswith(f"{segment.id}-")]
                    except (OSError, subprocess.SubprocessError, ValueError):
                        for _, result in batch:
                            result.ocr_status = "failed"
                if status.ready and jobs:
                    try:
                        payload = self._run([status.runtime_python_path, status.worker_path], {"model_path": status.model_path, "segments": jobs}, root, "vlm", 600)
                        rows = payload.get("segments")
                        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) or [row.get("id") for row in rows] != [job["id"] for job in jobs]:
                            raise ValueError("画面结果与片段不匹配")
                        for row in rows:
                            result = results[row["id"]]
                            try:
                                parsed = parse_understanding(row["understanding"], result.sample_times)
                                result.summary, result.facts, result.unknowns = parsed["summary"], parsed["facts"], parsed["unknowns"]
                                result.status, result.message = "ready", "画面观察已生成，请结合采样时间核对。"
                            except (KeyError, ValueError):
                                result.status, result.message = "failed", "画面理解结果缺少可靠的结构或时间证据。"
                    except (OSError, subprocess.SubprocessError, ValueError):
                        for job in jobs:
                            results[job["id"]].status = "failed"
                            results[job["id"]].message = "本地画面理解失败，请检查模型运行环境。"
        return results

    def _run(self, command: list[str], request: dict, root: Path, name: str, timeout: int) -> dict:
        request_path, response_path = root / f"{name}-request.json", root / f"{name}-response.json"
        request_path.write_text(json.dumps(request))
        env = os.environ.copy()
        env.update({"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "TMPDIR": str(root),
                    "HF_HOME": str(self.catalog.model_root_path / ".cache" / "huggingface")})
        with _vlm_execution_lock if name == "vlm" else nullcontext():
            subprocess.run([*command, "--request", str(request_path), "--response", str(response_path)],
                           env=env, capture_output=True, check=True, timeout=timeout)
        result = json.loads(response_path.read_text())
        if not isinstance(result, dict):
            raise ValueError("模型响应不是对象")
        return result
