from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AppState:
    manual_source_items: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class AppContext:
    settings: Any
    repo: Any | None = None
    preference_repo: Any | None = None
    workflow: Any | None = None
    manual_source_items: list[Any] = field(default_factory=list)
    hermes_source_client_factory: Callable[[str | Path | None], Any] | None = None
    external_source_client_factory: Callable[[list[str]], Any] | None = None
    runtime_env_path: str | Path | None = None
    reload_runtime_dependencies: Callable[[], None] | None = None
