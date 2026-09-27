"""Invitation commands for an already provisioned device.

The caller supplies an AuthPrincipal produced by the existing JWT verifier and
a DeviceCredential supplied by a trusted native runtime. This module neither
registers a device nor creates a device proof. Every database RPC independently
checks auth.uid(), the device proof, and its own object permissions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import httpx

from app.auth import AuthPrincipal
from app.config import Settings


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


class DeviceAccountService:
    """Call restricted Supabase RPCs with a verified user's bearer token."""

    def __init__(self, settings: Settings, trusted_device: DeviceCredential) -> None:
        if not settings.auth_enabled or not settings.supabase_url or not settings.supabase_publishable_key:
            raise DeviceAccountError("unavailable", "authenticated device service is not configured")
        self._base_url = settings.supabase_url.rstrip("/")
        self._publishable_key = settings.supabase_publishable_key
        self._device = trusted_device

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
