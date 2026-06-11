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
    creator_profile_repo: Any | None = None
    preference_repo: Any | None = None
    vault: Any | None = None
    service: Any | None = None
    workflow: Any | None = None
    manual_source_items: list[Any] = field(default_factory=list)
    hermes_source_client_factory: Callable[[str | Path | None], Any] | None = None
    external_source_client_factory: Callable[[list[str]], Any] | None = None
    runtime_env_path: str | Path | None = None
    reload_runtime_dependencies: Callable[[], None] | None = None
    video_inbox: Any | None = None
    video_pipeline: Any | None = None
    draft_workspace: Any | None = None
    video_job_runner: Any | None = None
    edit_plan_apply_runner: Callable[[list[str]], None] | None = None
    voice_transcriber: Any | None = None
