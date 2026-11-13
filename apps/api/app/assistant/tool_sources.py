"""Check the deployed extension's actual relative TypeScript import closure.

The source imports own the file graph; packaging ships the TypeScript tools and
their trusted ``.mjs`` runtime sibling. This check reads no configuration and
neither initializes Pi nor calls HTTP tools.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_RELATIVE_IMPORT = re.compile(
    r"(?:\b(?:import|export)\s+(?:type\s+)?[^;]*?\bfrom\s*|\bimport\s*)"
    r"[\"'](\.[^\"']+)[\"']"
)
_SOURCE_SUFFIXES = {".ts", ".mjs"}


def _source_roots(extension: Path) -> set[Path]:
    """Return the two source roots shipped together with the extension.

    ``agent_tools`` is the extension root and the sibling ``agent_runtime``
    directory contains the trusted runtime modules imported by the tools. The
    sibling is explicit so a relative import cannot escape into arbitrary
    files elsewhere in the application or user's home directory.
    """
    root = extension.parent.resolve()
    roots = {root}
    if root.name in {"agent_tools", "agent-tools"}:
        roots.add((root.parent / "agent_runtime").resolve())
    return roots


def tool_sources_readable(extension: Path) -> bool:
    roots = _source_roots(extension)
    pending = [extension.resolve()]
    seen: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in seen:
            continue
        seen.add(source)
        if (not any(source.is_relative_to(root) for root in roots)
                or source.suffix not in _SOURCE_SUFFIXES
                or not source.is_file()
                or not os.access(source, os.R_OK)):
            return False
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return False
        pending.extend((source.parent / relative).resolve() for relative in _RELATIVE_IMPORT.findall(text))
    return True
