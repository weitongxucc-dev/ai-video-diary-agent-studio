"""Thin formal session and owner invitation adapters over the existing RPC owner."""
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth import UnauthorizedError
from app.companion.identity import CompanionIdentity, require_account_principal
from app.companion.pairing import CompanionPairingService
from app.device_accounts import DeviceAccountError, DeviceAccountService


class CompanionSessionResponse(BaseModel):
    device_id: UUID
    workspace_id: UUID
    member_id: UUID
    name: str | None
    role: Literal["owner", "member"]


class MemberSpaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)


class MemberSpaceResponse(BaseModel):
    workspace_id: UUID


class DeviceInviteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_workspace_id: UUID
    valid_for_seconds: int = Field(default=3600, ge=60, le=86400)


class DeviceInviteResponse(BaseModel):
    id: UUID
    token: str
    target_workspace_id: UUID
    expires_at: datetime


class DeviceInviteRevokeResponse(BaseModel):
    id: UUID
    revoked: bool


class PairingTicketResponse(BaseModel):
    token: str
    expires_at: datetime
    workspace_id: UUID
    device_id: UUID


class PairingTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=256)


class PairingAcceptanceResponse(BaseModel):
    target_workspace_id: UUID
    member_id: UUID
    device_id: UUID


def companion_http_error(error: DeviceAccountError | UnauthorizedError) -> HTTPException:
    if isinstance(error, UnauthorizedError):
        return HTTPException(status_code=401, detail=str(error))
    return HTTPException(status_code={"forbidden": 403, "conflict": 409, "invalid": 400, "unavailable": 503}[error.kind], detail=str(error))


def build_companion_router(
    *, accounts_for: Callable[[], DeviceAccountService],
    identity_for: Callable[[Request], CompanionIdentity],
    pairings: CompanionPairingService,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/companion/session", response_model=CompanionSessionResponse)
    def session(request: Request):
        try:
            return asdict(identity_for(request))
        except (DeviceAccountError, UnauthorizedError) as exc:
            raise companion_http_error(exc) from exc

    @router.post("/api/companion/pairings", response_model=PairingTicketResponse, status_code=201)
    def create_pairing(request: Request):
        try:
            return asdict(pairings.create(identity_for(request)))
        except (DeviceAccountError, UnauthorizedError) as exc:
            raise companion_http_error(exc) from exc

    @router.post("/api/companion/pairings/accept", response_model=PairingAcceptanceResponse)
    def accept_pairing(request: Request, payload: PairingTokenRequest):
        try:
            return pairings.accept(require_account_principal(request), payload.token, accounts_for())
        except (DeviceAccountError, UnauthorizedError) as exc:
            raise companion_http_error(exc) from exc

    @router.delete("/api/companion/pairings")
    def revoke_pairing(request: Request, payload: PairingTokenRequest):
        try:
            return {"revoked": pairings.revoke(identity_for(request), payload.token)}
        except (DeviceAccountError, UnauthorizedError) as exc:
            raise companion_http_error(exc) from exc

    @router.post("/api/device-member-spaces", response_model=MemberSpaceResponse, status_code=201)
    def create_member_space(request: Request, payload: MemberSpaceCreateRequest):
        try:
            # The existing RPC verifies auth.uid(), native proof and device owner.
            workspace_id = accounts_for().create_member_space(require_account_principal(request), payload.name)
            return {"workspace_id": workspace_id}
        except (DeviceAccountError, UnauthorizedError) as exc:
            raise companion_http_error(exc) from exc

    @router.post("/api/device-invites", response_model=DeviceInviteResponse, status_code=201)
    def create_invite(request: Request, payload: DeviceInviteCreateRequest):
        try:
            return asdict(accounts_for().create_invite(require_account_principal(request),
                str(payload.target_workspace_id), valid_for_seconds=payload.valid_for_seconds))
        except (DeviceAccountError, UnauthorizedError) as exc:
            raise companion_http_error(exc) from exc

    @router.delete("/api/device-invites/{invite_id}", response_model=DeviceInviteRevokeResponse)
    def revoke_invite(invite_id: UUID, request: Request):
        try:
            revoked = accounts_for().revoke_invite(require_account_principal(request), str(invite_id))
            return {"id": invite_id, "revoked": revoked}
        except (DeviceAccountError, UnauthorizedError) as exc:
            raise companion_http_error(exc) from exc

    return router
