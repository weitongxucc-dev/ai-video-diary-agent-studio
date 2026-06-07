from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AppContext:
    settings: Any
    repo: Any | None = None
    workflow: Any | None = None
    runtime_env_path: str | Path | None = None
    reload_runtime_dependencies: Callable[[], None] | None = None
