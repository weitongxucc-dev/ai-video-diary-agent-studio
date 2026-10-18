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
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import jwt
from fastapi import Request
from jwt import PyJWKClient

from app.config import Settings
from app.repository import LEGACY_WORKSPACE_ID

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "Bearer "
_ASYMMETRIC_JWT_ALGORITHMS = ["ES256", "RS256"]
WORKSPACE_SELECTION_HEADER = "X-Workspace-Id"


class UnauthorizedError(Exception):
    """Missing, malformed, expired or unverifiable token -> HTTP 401."""


class ForbiddenError(Exception):
    """Valid token but no authorized workspace membership -> HTTP 403."""


class AuthDependencyUnavailableError(Exception):
    """The remote membership/onboarding fact could not be verified -> HTTP 503."""

    def __init__(self) -> None:
        super().__init__("暂时无法核实工作区访问权限或初始化状态，请稍后重试。")


class OnboardingStateError(Exception):
    """Trusted onboarding-state persistence failed."""


@dataclass(frozen=True)
class AuthPrincipal:
    """The authenticated identity resolved from a verified request."""

    user_id: str
    workspace_id: str | None
    authenticated: bool = True
    access_token: str | None = field(default=None, repr=False, compare=False)
    # Display only. User-editable metadata is never an authorization claim.
    name: str | None = None


def account_display_name(payload: dict[str, Any]) -> str | None:
    metadata = payload.get("user_metadata")
    name = metadata.get("account") if isinstance(metadata, dict) else None
    return name if isinstance(name, str) and name.strip() else None


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

    def __init__(self, settings: Settings, *, http_client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http_client = http_client

    def bind_http_client(self, client: httpx.Client | None) -> None:
        """Borrow the application-owned transport; only its owner may close it."""
        self._http_client = client

    @property
    def enabled(self) -> bool:
        return self._settings.auth_enabled

    @property
    def jwks_url(self) -> str:
        return (
            f"{self._settings.supabase_url.rstrip('/')}/auth/v1/"
            ".well-known/jwks.json"
        )

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
                algorithms=_ASYMMETRIC_JWT_ALGORITHMS,
                audience=self._settings.supabase_jwt_audience,
                issuer=self.issuer,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError("invalid or expired token") from exc

    def _user_headers(self, access_token: str) -> dict[str, str]:
        return {
            "apikey": self._settings.supabase_publishable_key or "",
            "Authorization": f"Bearer {access_token}",
        }

    def resolve_workspace_id(
        self, user_id: str, access_token: str, *, selected_workspace_id: str | None = None,
    ) -> str | None:
        """Resolve the user's authorized workspace membership server-side.

        Uses the caller's verified token and the public key, so Supabase RLS
        remains the authority without shipping a service-role secret.
        """
        if not self.enabled:
            return None
        url = f"{self._settings.supabase_url.rstrip('/')}/rest/v1/workspace_members"
        headers = self._user_headers(access_token)
        params = {
            "user_id": f"eq.{user_id}",
            "select": "workspace_id,role",
            "order": "created_at.asc",
        }
        if selected_workspace_id is not None:
            try:
                selected_workspace_id = str(UUID(selected_workspace_id))
            except ValueError as exc:
                raise ForbiddenError("invalid selected workspace") from exc
            params["workspace_id"] = f"eq.{selected_workspace_id}"
        try:
            get = self._http_client.get if self._http_client is not None else httpx.get
            response = get(url, headers=headers, params=params, timeout=10.0)
        except httpx.HTTPError as exc:
            logger.warning("workspace membership lookup failed: %s", type(exc).__name__)
            raise AuthDependencyUnavailableError() from exc
        if response.status_code != 200:
            logger.warning(
                "workspace membership lookup returned HTTP %s",
                response.status_code,
            )
            if response.status_code in {401, 403}:
                raise ForbiddenError("unable to resolve workspace membership")
            raise AuthDependencyUnavailableError()
        try:
            rows = response.json()
        except ValueError as exc:
            raise AuthDependencyUnavailableError() from exc
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("workspace_id"), str) or not row["workspace_id"]
            for row in rows
        ):
            raise AuthDependencyUnavailableError()
        if not rows:
            return None
        if selected_workspace_id is not None:
            return selected_workspace_id if any(
                row["workspace_id"] == selected_workspace_id for row in rows
            ) else None
        owner = next(
            (
                row
                for row in rows
                if row.get("role") == "owner"
            ),
            None,
        )
        chosen = owner if owner is not None else rows[0]
        return chosen["workspace_id"]

    def complete_onboarding(
        self,
        user_id: str,
        workspace_id: str,
        access_token: str,
    ) -> None:
        """Persist initialization after the API verifies minimum workspace storage.

        The RPC verifies ``auth.uid()`` and the caller's workspace membership.
        The desktop never receives or stores a service-role secret.
        """
        if not self.enabled:
            raise OnboardingStateError("authentication is not enabled")

        url = (
            f"{self._settings.supabase_url.rstrip('/')}"
            "/rest/v1/rpc/complete_own_onboarding"
        )
        headers = {
            **self._user_headers(access_token),
            "Content-Type": "application/json",
        }
        payload = {"target_workspace_id": workspace_id}
        try:
            response = httpx.post(
                url,
                headers=headers,
                json=payload,
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            logger.warning("onboarding completion update failed: %s", exc)
            raise OnboardingStateError("unable to persist onboarding state") from exc

        completed = response.json() if response.content else False
        if response.status_code != 200 or completed is not True:
            logger.warning(
                "onboarding completion update returned HTTP %s",
                response.status_code,
            )
            raise OnboardingStateError("unable to persist onboarding state")

    def is_onboarding_complete(
        self,
        user_id: str,
        workspace_id: str,
        access_token: str,
    ) -> bool:
        """Return whether the caller's resolved workspace finished onboarding.

        The caller token is verified before this query and RLS only exposes the
        caller's own onboarding row.
        """
        if not self.enabled:
            return True

        url = f"{self._settings.supabase_url.rstrip('/')}/rest/v1/onboarding_states"
        headers = self._user_headers(access_token)
        params = {
            "workspace_id": f"eq.{workspace_id}",
            "user_id": f"eq.{user_id}",
            "select": "completed_at",
        }
        try:
            get = self._http_client.get if self._http_client is not None else httpx.get
            response = get(url, headers=headers, params=params, timeout=10.0)
        except httpx.HTTPError as exc:
            logger.warning("onboarding state lookup failed: %s", type(exc).__name__)
            raise AuthDependencyUnavailableError() from exc
        if response.status_code != 200:
            logger.warning(
                "onboarding state lookup returned HTTP %s",
                response.status_code,
            )
            if response.status_code in {401, 403}:
                raise ForbiddenError("unable to resolve onboarding state")
            raise AuthDependencyUnavailableError()
        try:
            rows = response.json()
        except ValueError as exc:
            raise AuthDependencyUnavailableError() from exc
        if not isinstance(rows, list) or len(rows) > 1 or (rows and (
            not isinstance(rows[0], dict) or "completed_at" not in rows[0]
            or (rows[0]["completed_at"] is not None and not isinstance(rows[0]["completed_at"], str))
        )):
            raise AuthDependencyUnavailableError()
        return bool(rows and rows[0]["completed_at"])

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
        selected = request.headers.get(WORKSPACE_SELECTION_HEADER)
        workspace_id = (self.resolve_workspace_id(user_id, token, selected_workspace_id=selected)
            if selected is not None else self.resolve_workspace_id(user_id, token))
        if workspace_id is None:
            raise ForbiddenError("no authorized workspace membership")
        return AuthPrincipal(
            user_id=user_id,
            workspace_id=workspace_id,
            authenticated=True,
            access_token=token,
            name=account_display_name(payload),
        )

    def authenticate_identity(self, request: Request) -> AuthPrincipal:
        """Authenticate a request's identity only, without workspace resolution.

        Verifies the bearer JWT and derives ``user_id`` from the verified
        ``sub`` claim, but leaves ``workspace_id`` unset. This is the identity
        entry for the narrow invite/pairing acceptance endpoints: a first-time
        invitee has no workspace membership yet, so membership-based
        ``authenticate()`` would reject them with 403 before they can reach
        the invite RPC. All product-data paths must keep using
        ``authenticate()``.

        Never returns a local principal: missing, malformed, expired or
        unverifiable tokens — and unconfigured Supabase auth — raise
        ``UnauthorizedError``.
        """
        token = extract_bearer_token(request)
        if token is None:
            raise UnauthorizedError("missing bearer token")
        payload = self.verify_token(token)
        user_id = str(payload.get("sub") or "")
        if not user_id:
            raise UnauthorizedError("token missing subject claim")
        return AuthPrincipal(
            user_id=user_id,
            workspace_id=None,
            authenticated=True,
            access_token=token,
            name=account_display_name(payload),
        )
