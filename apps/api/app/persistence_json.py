"""Existing JSON and date conversion rules for persisted application values."""

from dataclasses import asdict
from datetime import datetime
import json


def _json_payload(value: object) -> str:
    return json.dumps(asdict(value), ensure_ascii=False, default=_json_default)


def _json_object(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _with_datetime(data: dict[str, object], *keys: str) -> dict[str, object]:
    converted = dict(data)
    for key in keys:
        value = converted.get(key)
        if isinstance(value, str):
            converted[key] = datetime.fromisoformat(value)
    return converted


def _required_text(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"持久化媒体对象缺少 {key}")
    return value


def _optional_string_tuple(data: dict[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"持久化媒体对象的 {key} 无效")
    return tuple(value)


def _required_datetime(data: dict[str, object], key: str) -> datetime:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"持久化媒体对象缺少 {key}")
    return datetime.fromisoformat(value)


def _optional_datetime(data: dict[str, object], key: str) -> datetime | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"持久化媒体对象的 {key} 无效")
    return datetime.fromisoformat(value)
