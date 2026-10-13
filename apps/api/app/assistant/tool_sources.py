"""Check the deployed extension's actual relative TypeScript import closure.

The source imports own the file graph; packaging already copies **/*.ts. This
check reads no configuration and neither initializes Pi nor calls HTTP tools.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_RELATIVE_IMPORT = re.compile(
    r"(?:\b(?:import|export)\s+(?:type\s+)?[^;]*?\bfrom\s*|\bimport\s*)"
    r"[\"'](\.[^\"']+)[\"']"
)


def tool_sources_readable(extension: Path) -> bool:
    root = extension.parent.resolve()
    pending = [extension.resolve()]
    seen: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in seen:
            continue
        seen.add(source)
        if not source.is_relative_to(root) or source.suffix != ".ts" or not source.is_file() or not os.access(source, os.R_OK):
            return False
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return False
        pending.extend((source.parent / relative).resolve() for relative in _RELATIVE_IMPORT.findall(text))
    return True
