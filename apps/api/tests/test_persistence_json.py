"""Persisted JSON compatibility without the production repository runtime."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from app.persistence_json import _json_default, _json_object, _json_payload, _with_datetime


def test_serialization_import_has_only_standard_library_dependencies(tmp_path):
    script = textwrap.dedent("""
        import importlib.abc
        import sqlite3
        import socket
        import subprocess
        import sys
        class OnlySerialization(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname.startswith('app.') and fullname != 'app.persistence_json':
                    raise AssertionError('unexpected application dependency: ' + fullname)
        def forbidden(*args, **kwargs):
            raise AssertionError('pure import attempted runtime work')
        sys.meta_path.insert(0, OnlySerialization())
        sqlite3.connect = forbidden
        socket.socket = forbidden
        subprocess.Popen = forbidden
        import app.persistence_json
        assert app.persistence_json._json_object({'name': '测试'}) == '{"name": "测试"}'
    """)
    script = f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n" + script
    completed = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_dataclass_and_object_payload_preserve_unicode_spacing_and_date_offsets():
    @dataclass
    class SavedValue:
        name: str
        created_at: datetime
        labels: list[str]

    when = datetime.fromisoformat("2026-10-14T08:09:10+08:00")
    expected = '{"name": "标签夹", "created_at": "2026-10-14T08:09:10+08:00", "labels": ["工作", "日记"]}'
    assert _json_payload(SavedValue("标签夹", when, ["工作", "日记"])) == expected
    assert _json_object({"name": "标签夹", "created_at": when, "labels": ["工作", "日记"]}) == expected
    assert _json_default(datetime(2026, 10, 14)) == "2026-10-14T00:00:00"


def test_date_decode_is_a_shallow_copy_and_keeps_missing_none_and_nonstring_fields():
    labels = ["资料"]
    original = {
        "created_at": "2026-10-14T00:00:00+00:00",
        "updated_at": "2026-10-14T08:09:10",
        "finished_at": None,
        "sequence": 2,
        "labels": labels,
    }
    decoded = _with_datetime(original, "created_at", "updated_at", "finished_at", "sequence", "missing")
    assert decoded is not original
    assert decoded["created_at"] == datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert decoded["updated_at"] == datetime(2026, 10, 14, 8, 9, 10)
    assert decoded["finished_at"] is None
    assert decoded["sequence"] == 2
    assert "missing" not in decoded
    assert decoded["labels"] is labels
    assert original["created_at"] == "2026-10-14T00:00:00+00:00"


def test_invalid_dates_and_unsupported_values_propagate_original_errors():
    with pytest.raises(ValueError, match="Invalid isoformat string"):
        _with_datetime({"created_at": "not-a-date"}, "created_at")
    with pytest.raises(TypeError, match="cannot serialize date"):
        _json_object({"created_at": date(2026, 10, 14)})
    with pytest.raises(TypeError, match="asdict"):
        _json_payload({"name": "标签夹"})
