"""Focused tests for the JWT-only identity entry (device-invite groundwork).

``SupabaseAuthContext.authenticate_identity()`` verifies the bearer JWT and
returns a workspaceless principal for the narrow invite-acceptance endpoint.
A first-time invitee has no workspace membership yet, so this entry must
never query membership, never set a workspace context and never degrade to
the local principal. The existing ``authenticate()`` behavior must remain
unchanged for every other path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request

from app.auth import (
    ForbiddenError,
    SupabaseAuthContext,
    UnauthorizedError,
)
from app.config import Settings
from app.repository import (
    LEGACY_WORKSPACE_ID,
    WorkspaceContextRequiredError,
    active_workspace_id,
    set_auth_enabled,
)

ISSUER = "https://example.supabase.co/auth/v1"
AUDIENCE = "authenticated"


def _make_rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def _sign_token(
    user_id: str,
    *,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    exp: datetime | None = None,
    key: bytes | None = None,
) -> str:
    private_pem = key or _make_rsa_keypair()[0]
    payload = {
        "sub": user_id,
        "iss": issuer,
        "aud": audience,
        "exp": exp if exp is not None else datetime.now(UTC) + timedelta(hours=1),
        "role": "authenticated",
    }
    return pyjwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": "k1"})


def _auth_enabled_settings(tmp_path) -> Settings:
    return Settings(
        deepseek_api_key="sk-test",
        supabase_url="https://example.supabase.co",
        supabase_publishable_key="anon-key",
        database_path=str(tmp_path / "db.sqlite"),
    )


def _fake_jwks_client(public_pem: bytes):
    class FakeClient:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=public_pem)

    return FakeClient()


def _bearer_request(token: str | None) -> Request:
    headers = []
    if token is not None:
        headers = [(b"authorization", f"Bearer {token}".encode())]
    return Request({"type": "http", "headers": headers})


@pytest.fixture
def identity_ctx(tmp_path):
    private_pem, public_pem = _make_rsa_keypair()
    ctx = SupabaseAuthContext(_auth_enabled_settings(tmp_path))
    ctx._jwks_client = lambda: _fake_jwks_client(public_pem)
    return ctx, private_pem


# ── Identity entry: valid JWT → workspaceless principal ────────────────


def test_identity_entry_returns_workspaceless_principal(identity_ctx):
    ctx, private_pem = identity_ctx
    token = _sign_token("invitee-1", key=private_pem)

    with mock.patch("app.auth.httpx.get") as membership_get:
        principal = ctx.authenticate_identity(_bearer_request(token))

    assert principal.user_id == "invitee-1"
    assert principal.workspace_id is None
    assert principal.authenticated is True
    assert principal.access_token == token
    membership_get.assert_not_called()


def test_identity_entry_sets_no_workspace_context(identity_ctx):
    ctx, private_pem = identity_ctx
    token = _sign_token("invitee-1", key=private_pem)

    set_auth_enabled(True)
    try:
        ctx.authenticate_identity(_bearer_request(token))
        with pytest.raises(WorkspaceContextRequiredError):
            active_workspace_id()
    finally:
        set_auth_enabled(False)


# ── Identity entry: missing / bad / expired / unconfigured → 401 ────────


def test_identity_entry_requires_bearer_token(identity_ctx):
    ctx, _ = identity_ctx

    with pytest.raises(UnauthorizedError):
        ctx.authenticate_identity(_bearer_request(None))

    with pytest.raises(UnauthorizedError):
        ctx.authenticate_identity(
            Request(
                {
                    "type": "http",
                    "headers": [(b"authorization", b"Basic dXNlcjpwYXNz")],
                }
            )
        )


def test_identity_entry_rejects_malformed_token(identity_ctx):
    ctx, _ = identity_ctx

    with pytest.raises(UnauthorizedError):
        ctx.authenticate_identity(_bearer_request("not-a-jwt"))


def test_identity_entry_rejects_expired_token(identity_ctx):
    ctx, private_pem = identity_ctx
    token = _sign_token(
        "invitee-1",
        exp=datetime.now(UTC) - timedelta(seconds=1),
        key=private_pem,
    )

    with pytest.raises(UnauthorizedError):
        ctx.authenticate_identity(_bearer_request(token))


def test_identity_entry_rejects_forged_token(identity_ctx):
    ctx, _ = identity_ctx
    forged = _sign_token("invitee-1", key=_make_rsa_keypair()[0])

    with pytest.raises(UnauthorizedError):
        ctx.authenticate_identity(_bearer_request(forged))


def test_identity_entry_never_degrades_to_local_when_auth_disabled():
    disabled = SupabaseAuthContext(Settings(deepseek_api_key="sk-test"))
    assert disabled.enabled is False

    with pytest.raises(UnauthorizedError):
        disabled.authenticate_identity(_bearer_request("anything"))

    with pytest.raises(UnauthorizedError):
        disabled.authenticate_identity(_bearer_request(None))


# ── Regression: authenticate() behavior stays unchanged ─────────────────


def test_authenticate_still_degrades_to_local_when_auth_disabled():
    disabled = SupabaseAuthContext(Settings(deepseek_api_key="sk-test"))

    principal = disabled.authenticate(_bearer_request(None))

    assert principal.authenticated is False
    assert principal.workspace_id == LEGACY_WORKSPACE_ID


def test_authenticate_still_resolves_membership(identity_ctx):
    ctx, private_pem = identity_ctx
    token = _sign_token("member-1", key=private_pem)

    class FakeResponse:
        status_code = 200
        content = b'[{"workspace_id":"ws-a","role":"member"}]'

        def json(self):
            return [{"workspace_id": "ws-a", "role": "member"}]

    with mock.patch("app.auth.httpx.get", return_value=FakeResponse()) as membership_get:
        principal = ctx.authenticate(_bearer_request(token))

    assert principal.workspace_id == "ws-a"
    assert principal.authenticated is True
    assert principal.access_token == token
    membership_get.assert_called_once()


def test_authenticate_still_rejects_zero_membership_with_forbidden(identity_ctx):
    ctx, private_pem = identity_ctx
    token = _sign_token("invitee-1", key=private_pem)

    class FakeResponse:
        status_code = 200
        content = b"[]"

        def json(self):
            return []

    with mock.patch("app.auth.httpx.get", return_value=FakeResponse()) as membership_get:
        with pytest.raises(ForbiddenError):
            ctx.authenticate(_bearer_request(token))

    membership_get.assert_called_once()
