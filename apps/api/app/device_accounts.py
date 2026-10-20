"""Invitation commands for an already provisioned device.

The caller supplies an AuthPrincipal produced by the existing JWT verifier and
a DeviceCredential supplied by a trusted native runtime. This module neither
registers a device nor creates a device proof. Every database RPC independently
checks auth.uid(), the device proof, and its own object permissions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4
import hashlib
import json
import logging
import time

import httpx

from app.auth import AuthPrincipal
from app.config import Settings

logger = logging.getLogger(__name__)


class _UnavailableIdentity(str):
    """The existing unknown verdict with this call's diagnostic correlation."""
    def __new__(cls, verification_id):
        value = super().__new__(cls, "unknown")
        value.verification_id = verification_id
        return value


def report_creation_identity_unknown(identity, *, source, reason, started_monotonic=None,
                                     http_status=None, exception_type=None) -> str:
    """One failed identity call, without credentials, request body or raw errors."""
    verification_id = uuid4().hex
    logger.warning("Creation identity verification unavailable: %s", json.dumps({
        "verification_id": verification_id,
        "identity_fingerprint": hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest(),
        "source": source, "verdict": "unknown", "reason": reason,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": time.monotonic() - started_monotonic if started_monotonic is not None else None,
        "http_status": http_status, "exception_type": exception_type,
    }))
    return _UnavailableIdentity(verification_id)


class DeviceAccountError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class DeviceCredential:
    """Pre-provisioned native credential; never expose it to the renderer."""

    device_id: str
    proof: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            UUID(self.device_id)
        except ValueError as exc:
            raise ValueError("trusted device id must be a UUID") from exc
        if len(self.proof) != 64 or any(ch not in "0123456789abcdef" for ch in self.proof):
            raise ValueError("trusted device proof must be 32 lowercase-hex bytes")


@dataclass(frozen=True)
class DeviceInvite:
    id: str
    token: str = field(repr=False)
    target_workspace_id: str
    expires_at: datetime


@dataclass(frozen=True)
class InviteAcceptance:
    target_workspace_id: str
    member_count: int
    already_accepted: bool


@dataclass(frozen=True)
class DeviceMemberContext:
    device_id: str
    workspace_id: str
    member_id: str
    role: str


class DeviceAccountService:
    """Call restricted Supabase RPCs with a verified user's bearer token."""

    def __init__(self, settings: Settings, trusted_device: DeviceCredential,
                 *, creation_permit_client: httpx.Client | None = None) -> None:
        if not settings.auth_enabled or not settings.supabase_url or not settings.supabase_publishable_key:
            raise DeviceAccountError("unavailable", "authenticated device service is not configured")
        self._base_url = settings.supabase_url.rstrip("/")
        self._publishable_key = settings.supabase_publishable_key
        self._device = trusted_device
        # Borrow only the transport; the application owns its lifetime.
        self._creation_permit_client = creation_permit_client

    @property
    def device_id(self) -> str:
        return self._device.device_id

    def require_workspace_access(self, principal: AuthPrincipal) -> DeviceMemberContext:
        """Recheck the actual account/device/workspace tuple, without a local fallback."""
        if not principal.authenticated or not principal.access_token or not principal.workspace_id:
            raise DeviceAccountError("forbidden", "verified account and workspace are required")
        result = self._call(principal, "device_member_context", {"target_workspace_id": principal.workspace_id})
        if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
            raise DeviceAccountError("unavailable", "invalid device membership response")
        row = result[0]
        if (row.get("device_id"), row.get("workspace_id"), row.get("member_id")) != (
            self.device_id, principal.workspace_id, principal.user_id,
        ) or row.get("role") not in {"owner", "member"}:
            raise DeviceAccountError("unavailable", "device membership response does not match the original binding")
        return DeviceMemberContext(row["device_id"], row["workspace_id"], row["member_id"], row["role"])

    def creation_permit(self, device_id: str, user_id: str, workspace_id: str) -> str:
        """Read only the original tuple's permission; no user session is retained."""
        if device_id != self._device.device_id:
            return "unauthorized"
        try:
            UUID(user_id)
            UUID(workspace_id)
        except (ValueError, TypeError, AttributeError):
            return "unauthorized"
        started = time.monotonic()
        http_status = None
        def unavailable(reason, exc=None):
            return report_creation_identity_unknown((device_id, user_id, workspace_id),
                source="device_creation_permit", reason=reason, started_monotonic=started,
                http_status=http_status, exception_type=type(exc).__name__ if exc is not None else None)
        try:
            post = self._creation_permit_client.post if self._creation_permit_client is not None else httpx.post
            response = post(
                f"{self._base_url}/rest/v1/rpc/device_creation_permit",
                headers={"apikey": self._publishable_key, "Content-Type": "application/json"},
                json={"target_device_id": device_id, "device_proof": self._device.proof,
                      "target_user_id": user_id, "target_workspace_id": workspace_id},
                timeout=5.0,
            )
            http_status = response.status_code
            if http_status != 200:
                return unavailable("http_status_not_ok")
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return unavailable("transport_error" if isinstance(exc, httpx.HTTPError) else "response_json_invalid", exc)
        if type(result) is not bool:
            return unavailable("response_type_invalid")
        return "ready" if result else "unauthorized"

    def _call(self, principal: AuthPrincipal, name: str, parameters: dict[str, object]) -> object:
        if not principal.authenticated or not principal.user_id or not principal.access_token:
            raise DeviceAccountError("forbidden", "verified account identity is required")
        try:
            response = httpx.post(
                f"{self._base_url}/rest/v1/rpc/{name}",
                headers={
                    "apikey": self._publishable_key,
                    "Authorization": f"Bearer {principal.access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "target_device_id": self._device.device_id,
                    "device_proof": self._device.proof,
                    **parameters,
                },
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            raise DeviceAccountError("unavailable", "device account service is unreachable") from exc
        if response.status_code != 200:
            try:
                error = response.json()
            except ValueError:
                error = {}
            code = error.get("code") if isinstance(error, dict) else None
            kind = {"42501": "forbidden", "23514": "conflict", "22023": "invalid"}.get(
                code, "unavailable"
            )
            message = error.get("message") if isinstance(error, dict) else None
            raise DeviceAccountError(kind, message or "device account command failed")
        try:
            return response.json()
        except ValueError as exc:
            raise DeviceAccountError("unavailable", "invalid device account response") from exc

    def create_member_space(self, principal: AuthPrincipal, name: str) -> str:
        result = self._call(
            principal, "device_create_member_space", {"space_name": name}
        )
        if not isinstance(result, str):
            raise DeviceAccountError("unavailable", "invalid workspace response")
        return result

    def create_invite(
        self,
        principal: AuthPrincipal,
        target_workspace_id: str,
        *,
        valid_for_seconds: int = 3600,
    ) -> DeviceInvite:
        result = self._call(
            principal,
            "device_create_invite",
            {
                "target_workspace_id": target_workspace_id,
                "valid_for_seconds": valid_for_seconds,
            },
        )
        if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
            raise DeviceAccountError("unavailable", "invalid invite response")
        row = result[0]
        try:
            return DeviceInvite(
                id=str(row["invite_id"]),
                token=str(row["invite_token"]),
                target_workspace_id=target_workspace_id,
                expires_at=datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DeviceAccountError("unavailable", "invalid invite response") from exc

    def revoke_invite(self, principal: AuthPrincipal, invite_id: str) -> bool:
        result = self._call(
            principal, "device_revoke_invite", {"invite_id": invite_id}
        )
        if not isinstance(result, bool):
            raise DeviceAccountError("unavailable", "invalid revoke response")
        return result

    def accept_invite(self, principal: AuthPrincipal, token: str) -> InviteAcceptance:
        result = self._call(
            principal, "device_accept_invite", {"invite_token": token}
        )
        if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
            raise DeviceAccountError("unavailable", "invalid acceptance response")
        row = result[0]
        try:
            workspace_id = str(row["workspace_id"])
            count = row["member_count"]
            already = row["already_accepted"]
            if not isinstance(count, int) or isinstance(count, bool) or not isinstance(already, bool):
                raise TypeError("invalid count or idempotency status")
            return InviteAcceptance(workspace_id, count, already)
        except (KeyError, TypeError, ValueError) as exc:
            raise DeviceAccountError("unavailable", "invalid acceptance response") from exc
