"""Extract only explicitly labelled key/value/unit tables from Word and Excel."""
from __future__ import annotations

from dataclasses import dataclass

from app.knowledge_files import KnowledgeParameter


@dataclass(frozen=True)
class TableCell:
    text: str
    locator: str


_KEY_HEADERS = {"参数", "参数名称", "parameter", "name"}
_VALUE_HEADERS = {"值", "数值", "参数值", "value"}
_UNIT_HEADERS = {"单位", "unit"}


def explicit_parameters(rows: list[list[TableCell]]) -> list[KnowledgeParameter]:
    parameters: list[KnowledgeParameter] = []
    columns: tuple[int, int, int | None] | None = None
    recorded: set[str] = set()
    for row in rows:
        labels = [cell.text.strip().casefold() for cell in row]
        keys = [i for i, label in enumerate(labels) if label in _KEY_HEADERS]
        values = [i for i, label in enumerate(labels) if label in _VALUE_HEADERS]
        units = [i for i, label in enumerate(labels) if label in _UNIT_HEADERS]
        if len(keys) == len(values) == 1 and len(units) <= 1:
            columns = (keys[0], values[0], units[0] if units else None)
            continue
        if columns is None:
            continue
        key_column, value_column, unit_column = columns
        if max(key_column, value_column, unit_column or 0) >= len(row):
            continue
        if not any(row[index].text.strip() for index in columns if index is not None):
            columns = None
            continue
        key, value = row[key_column], row[value_column]
        if not key.text.strip() or not value.text.strip() or value.locator in recorded:
            continue
        unit = row[unit_column].text.strip() if unit_column is not None else ""
        parameters.append(KnowledgeParameter(key=key.text.strip(), value=value.text.strip(),
            unit=unit if unit not in {"", "-", "—"} else None, locator=value.locator))
        recorded.add(value.locator)
    return parameters
