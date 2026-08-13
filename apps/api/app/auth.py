"""Supabase bearer-token authentication and server-side workspace resolution.

This module is the single trust boundary for the beta auth model:

* JWTs are verified locally against the configured project JWKS endpoint,
  and ``user_id`` is derived exclusively from the verified ``sub`` claim.
* Authorized workspace membership is resolved server-side via the Supabase
  Data API (RLS-protected) — never from request bodies.
* When Supabase auth is *not* configured, the backend remains a local-first
  single-user app and every record lives in the reserved ``local`` workspace
  so legacy data can never leak into an authenticated account later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import Request
from jwt import PyJWKClient

from app.config import Settings
from app.repository import LEGACY_WORKSPACE_ID

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "Bearer "


class UnauthorizedError(Exception):
    """Missing, malformed, expired or unverifiable token -> HTTP 401."""


class ForbiddenError(Exception):
    """Valid token but no authorized workspace membership -> HTTP 403."""


@dataclass(frozen=True)
class AuthPrincipal:
    """The authenticated identity resolved from a verified request."""

    user_id: str
    workspace_id: str | None
    authenticated: bool = True


def extract_bearer_token(request: Request) -> str | None:
    """Return the raw bearer token from the Authorization header, if any."""
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


class SupabaseAuthContext:
    """Verifies Supabase JWTs and resolves workspace membership server-side."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return self._settings.auth_enabled

    @property
    def jwks_url(self) -> str:
        return f"{self._settings.supabase_url.rstrip('/')}/auth/v1/jwt/jwks"

    @property
    def issuer(self) -> str | None:
        return self._settings.supabase_jwt_issuer_resolved

    def _jwks_client(self) -> PyJWKClient:
        return PyJWKClient(self.jwks_url, cache_keys=True)

    def verify_token(self, token: str) -> dict[str, Any]:
        """Verify a bearer JWT and return its claims.

        Raises UnauthorizedError when the token is unverifiable.
        """
        if not self.enabled:
            raise UnauthorizedError("authentication is not enabled")
        try:
            signing_key = self._jwks_client().get_signing_key_from_jwt(token)
        except Exception as exc:
            raise UnauthorizedError("unable to resolve token signing key") from exc
        try:
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.supabase_jwt_audience,
                issuer=self.issuer,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError("invalid or expired token") from exc

    def resolve_workspace_id(self, user_id: str) -> str | None:
        """Resolve the user's authorized workspace membership server-side.

        Uses the service-role key so membership is authoritative and cannot
        be forged by a client. Returns None when the user has no workspace.
        """
        if not self.enabled:
            return None
        url = f"{self._settings.supabase_url.rstrip('/')}/rest/v1/workspace_members"
        headers = {
            "apikey": self._settings.supabase_secret_key,
            "Authorization": f"Bearer {self._settings.supabase_secret_key}",
        }
        params = {
            "user_id": f"eq.{user_id}",
            "select": "workspace_id,role",
            "order": "created_at.asc",
        }
        try:
            response = httpx.get(url, headers=headers, params=params, timeout=10.0)
        except httpx.HTTPError as exc:
            logger.warning("workspace membership lookup failed: %s", exc)
            raise ForbiddenError("unable to resolve workspace membership") from exc
        if response.status_code != 200:
            logger.warning(
                "workspace membership lookup returned HTTP %s",
                response.status_code,
            )
            raise ForbiddenError("unable to resolve workspace membership")
        rows = response.json() if response.content else []
        if not isinstance(rows, list) or not rows:
            return None
        owner = next(
            (
                row
                for row in rows
                if isinstance(row, dict) and row.get("role") == "owner"
            ),
            None,
        )
        chosen = owner if owner is not None else rows[0]
        if not isinstance(chosen, dict):
            return None
        workspace_id = chosen.get("workspace_id")
        return str(workspace_id) if workspace_id else None

    def authenticate(self, request: Request) -> AuthPrincipal:
        """Authenticate a request and resolve its authorized workspace.

        Returns a local (unauthenticated) principal when auth is disabled so
        the local-first legacy path keeps working without a token.
        """
        if not self.enabled:
            return AuthPrincipal(
                user_id=LEGACY_WORKSPACE_ID,
                workspace_id=LEGACY_WORKSPACE_ID,
                authenticated=False,
            )
        token = extract_bearer_token(request)
        if token is None:
            raise UnauthorizedError("missing bearer token")
        payload = self.verify_token(token)
        user_id = str(payload.get("sub") or "")
        if not user_id:
            raise UnauthorizedError("token missing subject claim")
        workspace_id = self.resolve_workspace_id(user_id)
        if workspace_id is None:
            raise ForbiddenError("no authorized workspace membership")
        return AuthPrincipal(
            user_id=user_id,
            workspace_id=workspace_id,
            authenticated=True,
        )
