"""Request-scoped formal companion identity; transport never supplies ownership."""
from dataclasses import dataclass

from fastapi import Request

from app.auth import AuthPrincipal, UnauthorizedError, WORKSPACE_SELECTION_HEADER
from app.device_accounts import DeviceAccountError, DeviceAccountService


@dataclass(frozen=True)
class CompanionIdentity:
    device_id: str
    workspace_id: str
    member_id: str
    name: str | None
    role: str


def require_account_principal(request: Request) -> AuthPrincipal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, AuthPrincipal) or not principal.authenticated or not principal.access_token:
        raise UnauthorizedError("verified account identity is required")
    if "X-Local-Member-Token" in request.headers:
        raise DeviceAccountError("forbidden", "local development identity is not a companion account")
    return principal


def require_companion_identity(request: Request, accounts: DeviceAccountService) -> CompanionIdentity:
    """Use after JWT verification and server-side membership resolution in auth.py."""
    principal = require_account_principal(request)
    selected = request.headers.get(WORKSPACE_SELECTION_HEADER)
    if selected is None:
        raise DeviceAccountError("invalid", "explicit X-Workspace-Id selection is required")
    if not principal.workspace_id or selected.lower() != principal.workspace_id:
        raise DeviceAccountError("forbidden", "selected workspace does not match verified membership")
    binding = accounts.require_workspace_access(principal)
    return CompanionIdentity(binding.device_id, binding.workspace_id, binding.member_id, principal.name, binding.role)
