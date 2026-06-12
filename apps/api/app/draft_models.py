from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class DraftAsset:
    file_name: str
    path: str
    size_bytes: int
    modified_at: datetime
    import_status: str | None = None
    import_message: str | None = None
    draft_path: str | None = None
    template_id: str | None = None
    template_name: str | None = None
    track_count: int | None = None
    effect_count: int | None = None
    track_preview: list[str] = field(default_factory=list)
    effect_preview: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DraftToolStatus:
    available: bool
    tool_name: str | None
    checked_modules: list[str]


@dataclass(frozen=True)
class DraftEnvironment:
    output_path: str
    output_exists: bool
    processed_video_count: int
    package_count: int
    draft_tool: DraftToolStatus
    jianying_root_path: str
    jianying_root_exists: bool
    jianying_10_7_detected: bool
    editable_draft_supported: bool
    jianying_blocker: str | None


@dataclass(frozen=True)
class DraftPackage:
    package_name: str
    path: str
    manifest_path: str
    video_path: str
    subtitle_path: str | None
    cover_path: str | None
    status: str


@dataclass(frozen=True)
class DraftImportResult:
    package_name: str
    status: str
    message: str
    draft_path: str | None
    manifest_path: str
