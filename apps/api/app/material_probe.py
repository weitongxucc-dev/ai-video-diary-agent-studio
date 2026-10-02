"""Bounded ffprobe result parsing for one permitted material probe unit."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Literal


MAX_PROBE_OUTPUT_BYTES = 256 * 1024
MAX_PROBE_STREAMS = 32


@dataclass(frozen=True)
class MaterialMediaStream:
    index: int
    kind: Literal["video", "audio"]
    codec: str
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    avg_frame_rate_fps: float | None = None
    r_frame_rate_fps: float | None = None


@dataclass(frozen=True)
class MaterialProbeFacts:
    duration_seconds: float
    format_name: str
    streams: tuple[MaterialMediaStream, ...]

    def checkpoint_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_checkpoint_json(cls, value: str) -> MaterialProbeFacts:
        if len(value.encode("utf-8")) > MAX_PROBE_OUTPUT_BYTES:
            raise ValueError("媒体检查点超出上限")
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("媒体检查点 JSON 无效") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "duration_seconds", "format_name", "streams",
        }:
            raise ValueError("媒体检查点结构无效")
        duration = _positive_duration(payload["duration_seconds"])
        format_name = _bounded_text(payload["format_name"])
        raw_streams = payload["streams"]
        if (duration is None or not format_name or not isinstance(raw_streams, list)
                or len(raw_streams) > MAX_PROBE_STREAMS):
            raise ValueError("媒体检查点事实无效")
        streams: list[MaterialMediaStream] = []
        indices: set[int] = set()
        old_stream_fields = {"index", "kind", "codec", "width", "height", "sample_rate"}
        new_stream_fields = old_stream_fields | {"avg_frame_rate_fps", "r_frame_rate_fps"}
        legacy: bool | None = None
        for item in raw_streams:
            if not isinstance(item, dict) or frozenset(item) not in {
                frozenset(old_stream_fields), frozenset(new_stream_fields),
            }:
                raise ValueError("媒体检查点流结构无效")
            item_is_legacy = set(item) == old_stream_fields
            if legacy is not None and legacy != item_is_legacy:
                raise ValueError("媒体检查点流版本混用")
            legacy = item_is_legacy
            item = dict(item)
            if item_is_legacy:
                item["avg_frame_rate_fps"] = None
                item["r_frame_rate_fps"] = None
            index, kind = item["index"], item["kind"]
            if (isinstance(index, bool) or not isinstance(index, int) or index < 0
                    or index in indices or not isinstance(kind, str)
                    or kind not in {"video", "audio"}
                    or not _bounded_text(item["codec"])):
                raise ValueError("媒体检查点流事实无效")
            indices.add(index)
            if kind == "video":
                if (_positive_int(item["width"]) != item["width"]
                        or _positive_int(item["height"]) != item["height"]
                        or item["sample_rate"] is not None
                        or any(rate is not None and _frame_rate(rate) != rate for rate in (
                            item["avg_frame_rate_fps"], item["r_frame_rate_fps"]
                        ))):
                    raise ValueError("媒体检查点视频流无效")
            elif (item["width"] is not None or item["height"] is not None
                  or item["avg_frame_rate_fps"] is not None
                  or item["r_frame_rate_fps"] is not None
                  or (item["sample_rate"] is not None
                      and _positive_int(item["sample_rate"]) != item["sample_rate"])):
                raise ValueError("媒体检查点音频流无效")
            streams.append(MaterialMediaStream(**item))
        if not any(stream.kind == "video" for stream in streams):
            raise ValueError("媒体检查点缺少视频流")
        facts = cls(duration, format_name, tuple(streams))
        canonical = asdict(facts)
        if legacy:
            for stream in canonical["streams"]:
                stream.pop("avg_frame_rate_fps")
                stream.pop("r_frame_rate_fps")
        if json.dumps(canonical, sort_keys=True, separators=(",", ":")) != value:
            raise ValueError("媒体检查点不是规范编码")
        return facts


def probe_checkpoint_needs_rate_refresh(value: str) -> bool:
    """Only historical checkpoints missing rate fields need a new probe grant."""
    MaterialProbeFacts.from_checkpoint_json(value)
    payload = json.loads(value)
    return "avg_frame_rate_fps" not in payload["streams"][0]


def _positive_duration(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        duration = float(value)
    except ValueError:
        return None
    rounded = round(duration, 3)
    return rounded if math.isfinite(duration) and 0 < rounded <= 7 * 86400 else None


def _bounded_text(value: object) -> str:
    return value if isinstance(value, str) and 0 < len(value) <= 128 else ""


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 < number <= 1_000_000 else None


def _frame_rate(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            rate = float(numerator) / float(denominator)
        else:
            rate = float(value)
    except (ValueError, ZeroDivisionError):
        return None
    return round(rate, 3) if math.isfinite(rate) and 0 < rate <= 240 else None


def parse_ffprobe_output(output: bytes) -> MaterialProbeFacts:
    """Keep only bounded, typed facts needed before scene analysis."""
    if not output or len(output) > MAX_PROBE_OUTPUT_BYTES:
        raise ValueError("媒体探测输出为空或超出上限")
    try:
        payload = json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("媒体探测结果不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("媒体探测结果缺少对象")
    media_format = payload.get("format")
    raw_streams = payload.get("streams")
    if not isinstance(media_format, dict) or not isinstance(raw_streams, list):
        raise ValueError("媒体探测结果缺少格式或流")
    if len(raw_streams) > MAX_PROBE_STREAMS:
        raise ValueError("媒体流数量超出上限")
    format_name = _bounded_text(media_format.get("format_name"))
    if not format_name:
        raise ValueError("媒体容器格式未知")
    streams: list[MaterialMediaStream] = []
    seen_indices: set[int] = set()
    durations: list[float] = []
    for item in raw_streams:
        if not isinstance(item, dict):
            raise ValueError("媒体流结构无效")
        kind = item.get("codec_type")
        if not isinstance(kind, str) or kind not in {"video", "audio"}:
            continue
        index = item.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index in seen_indices:
            raise ValueError("媒体流索引无效")
        seen_indices.add(index)
        codec = _bounded_text(item.get("codec_name"))
        if not codec:
            raise ValueError("媒体流编码未知")
        duration = _positive_duration(item.get("duration"))
        if duration is not None:
            durations.append(duration)
        if kind == "video":
            disposition = item.get("disposition")
            if isinstance(disposition, dict) and disposition.get("attached_pic") == 1:
                continue
            width, height = _positive_int(item.get("width")), _positive_int(item.get("height"))
            if width is None or height is None:
                raise ValueError("视频流尺寸无效")
            streams.append(MaterialMediaStream(
                index, "video", codec, width=width, height=height,
                avg_frame_rate_fps=_frame_rate(item.get("avg_frame_rate")),
                r_frame_rate_fps=_frame_rate(item.get("r_frame_rate")),
            ))
        else:
            sample_rate = _positive_int(item.get("sample_rate"))
            streams.append(MaterialMediaStream(index, "audio", codec, sample_rate=sample_rate))
    if not any(stream.kind == "video" for stream in streams):
        raise ValueError("素材缺少可用视频流")
    duration = _positive_duration(media_format.get("duration")) or (max(durations) if durations else None)
    if duration is None:
        raise ValueError("媒体时长无效")
    return MaterialProbeFacts(duration, format_name, tuple(streams))
