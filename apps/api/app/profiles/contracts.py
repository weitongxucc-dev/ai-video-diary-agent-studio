"""Person, voice and authorization values and their pure use/identity rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal


AuthorizationStatus = Literal["active", "pending", "expired", "revoked"]


@dataclass(frozen=True)
class Authorization:
    """An auditable grant for a person, voice, or avatar output.

    This is intentionally a domain contract rather than a second persistence
    store. The repository and API layer own how these values are stored.
    """

    id: str
    version: str
    rights_holder: str
    content_types: tuple[str, ...]
    usage_scopes: tuple[str, ...]
    effective_from: datetime
    expires_at: datetime | None
    evidence_reference: str
    status: AuthorizationStatus
    workspace_id: str = ""

    def failure_for(
        self,
        *,
        content_type: str,
        usage_scope: str,
        at: datetime | None = None,
    ) -> str | None:
        now = _as_utc(at or datetime.now(timezone.utc))
        if not self.id.strip() or not self.evidence_reference.strip():
            return "authorization_evidence_missing"
        if self.status != "active":
            return f"authorization_{self.status}"
        if _as_utc(self.effective_from) > now:
            return "authorization_not_effective"
        if self.expires_at is not None and _as_utc(self.expires_at) <= now:
            return "authorization_expired"
        if content_type not in self.content_types:
            return "authorization_content_type_denied"
        if usage_scope not in self.usage_scopes:
            return "authorization_scope_denied"
        return None


@dataclass(frozen=True)
class ProfileReference:
    """A registered source and its explicit range, independent of model output."""

    asset_id: str | None = None
    segment_id: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None


@dataclass(frozen=True)
class PersonProfile:
    id: str
    workspace_id: str
    display_name: str
    version: str
    avatar_reference: str
    background_capabilities: tuple[str, ...]
    shot_capabilities: tuple[str, ...]
    authorization: Authorization | None = None
    reference: ProfileReference | None = None
    use_state: str = "available"
    owner_user_id: str | None = None
    visibility: str = "private"
    reference_issue: str | None = None


@dataclass(frozen=True)
class VoiceProfile:
    id: str
    workspace_id: str
    version: str
    language: Literal["zh", "en", "zh-en"]
    reference_audio_path: str
    reference_text: str
    person_profile_id: str = ""
    person_version: str = ""
    model_id: str = ""
    authorization: Authorization | None = None
    display_name: str = ""
    reference: ProfileReference | None = None
    use_state: str = "available"
    owner_user_id: str | None = None
    visibility: str = "private"
    reference_issue: str | None = None


@dataclass(frozen=True)
class PronunciationEntry:
    id: str
    workspace_id: str
    term: str
    language: Literal["zh", "en", "zh-en"]
    target_pronunciation: str
    scope: Literal["workspace", "person", "task"]
    scope_reference: str | None
    version: str

    def applies_to(
        self,
        *,
        workspace_id: str,
        person_profile_id: str | None,
        task_id: str,
    ) -> bool:
        if self.workspace_id != workspace_id:
            return False
        if self.scope == "workspace":
            return self.scope_reference is None
        if self.scope == "person":
            return self.scope_reference == person_profile_id
        return self.scope_reference == task_id

    def task_input(self) -> dict[str, str]:
        return {
            "id": self.id,
            "version": self.version,
            "term": self.term,
            "language": self.language,
            "target_pronunciation": self.target_pronunciation,
            "scope": self.scope,
            "scope_reference": self.scope_reference or "",
        }


def profile_use_failure(profile, *, content_type: str, usage_scope: str) -> str | None:
    """Local references need no proof form; explicit restrictions remain binding."""
    if profile.reference_issue is not None:
        return profile.reference_issue
    if profile.use_state not in {"available", "not_for_publish"}:
        return f"profile_{profile.use_state}"
    authorization = profile.authorization
    if authorization is None:
        return None
    if authorization.workspace_id != profile.workspace_id:
        return "authorization_workspace_mismatch"
    now = datetime.now(timezone.utc)
    if authorization.status not in {"active", "pending"}:
        return f"authorization_{authorization.status}"
    if authorization.expires_at is not None and _as_utc(authorization.expires_at) <= now:
        return "authorization_expired"
    if _as_utc(authorization.effective_from) > now:
        return "authorization_not_effective"
    if authorization.content_types and content_type not in authorization.content_types:
        return "authorization_content_type_denied"
    if authorization.usage_scopes and usage_scope not in authorization.usage_scopes:
        return "authorization_scope_denied"
    return None


def profile_cache_identity(profile) -> dict[str, object]:
    return {
        "id": profile.id, "version": profile.version,
        "reference": asdict(profile.reference) if profile.reference else None,
        "use_state": profile.use_state,
        "authorization": _authorization_cache_identity(profile.authorization),
    }


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _authorization_cache_identity(authorization: Authorization | None) -> dict[str, str | None] | None:
    if authorization is None:
        return None
    return {
        "id": authorization.id,
        "version": authorization.version,
        "status": authorization.status,
        "expires_at": authorization.expires_at.isoformat() if authorization.expires_at else None,
    }
