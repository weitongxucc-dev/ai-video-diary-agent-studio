"""Canonical values for independent voice and avatar preview attempts."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from app.models import utc_now


MediaPreviewType = Literal["voice", "avatar_lipsync"]
MediaPreviewStatus = Literal["queued", "running", "succeeded", "unavailable", "blocked", "failed", "cancelled"]


@dataclass
class MediaPreviewRunRecord:
    id: str
    workspace_id: str
    preview_type: MediaPreviewType
    identity: str
    status: MediaPreviewStatus
    capability_id: str
    model_id: str
    language: str
    person_profile_id: str | None
    person_version: str | None
    voice_profile_id: str | None
    voice_version: str | None
    authorization_id: str | None
    authorization_version: str | None
    attempt: int = 1
    output_path: str | None = None
    reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    member_identity: dict = field(default_factory=dict)
    preview_input: dict = field(default_factory=dict)
    input_digest: str = ""
    device_request_id: str | None = None
