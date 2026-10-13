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
