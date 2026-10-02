"""Focused tests for the device-invite acceptance entry (identity-only).

``POST /api/device-invites/accept`` is the single identity-level entry: it
authenticates the request's identity via ``authenticate_identity()`` without
resolving workspace membership, and never sets a workspace context. A
first-time invitee holds no workspace yet, so the ordinary ``authenticate()``
boundary would reject them with 403 on every product-data path. These tests
prove the narrow exemption — the same no-membership identity that is 403 on
product data is admitted on this POST path alone — while token failures still
401, the trusted device credential is not forgeable from the request body, and
``DeviceAccountError`` kinds map to their documented HTTP statuses.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import app.main as main_module
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.device_accounts import DeviceAccountError, InviteAcceptance

ISSUER = "https://example.supabase.co/auth/v1"
AUDIENCE = "authenticated"
PROOF = "ab" * 32


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


def _sign_token(user_id: str, *, key: bytes) -> str:
    payload = {
        "sub": user_id,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "role": "authenticated",
    }
    return pyjwt.encode(payload, key, algorithm="RS256", headers={"kid": "k1"})


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


@pytest.fixture
def entry(monkeypatch, tmp_path):
    """App whose auth boundary holds no workspace membership for any identity.

    The context verifies tokens via a fake JWKS, resolves *no* membership
    (so ``authenticate()`` would 403 every product path), and a trusted device
    credential is injected through the environment.
    """
    private_pem, public_pem = _make_rsa_keypair()
    settings = _auth_enabled_settings(tmp_path)
    ctx = SupabaseAuthContext(settings)
    ctx._jwks_client = lambda: _fake_jwks_client(public_pem)
    ctx.resolve_workspace_id = lambda user_id, access_token: None
    ctx.is_onboarding_complete = lambda user_id, workspace_id, access_token: True
    monkeypatch.setattr(main_module, "auth_context", ctx)
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setenv("DEVICE_ID", str(uuid4()))
    monkeypatch.setenv("DEVICE_PROOF", PROOF)
    return TestClient(main_module.app), private_pem, ctx


def _acceptance_service(acceptance=None, error=None):
    """Return a DeviceAccountService stand-in recording its constructor/args."""

    class FakeService:
        def __init__(self, settings, device):
            self.settings = settings
            self.device = device

        def accept_invite(self, principal, token):
            self.principal = principal
            self.token = token
            if error is not None:
                raise error
            return acceptance or InviteAcceptance("ws-target", 3, False)

    return FakeService


# ── The narrow exemption: identity-only entry, no workspace resolution ──


def test_device_accept_success_with_workspaceless_identity(entry, monkeypatch):
    client, private_pem, ctx = entry
    fake = _acceptance_service()
    monkeypatch.setattr(main_module, "DeviceAccountService", fake)

    token = _sign_token("invitee-1", key=private_pem)
    resp = client.post(
        "/api/device-invites/accept",
        json={"invite_token": "invite-token-123"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "target_workspace_id": "ws-target",
        "member_count": 3,
        "already_accepted": False,
    }


def test_device_accept_injects_trusted_credential_not_request(entry, monkeypatch):
    client, private_pem, ctx = entry
    captured = {}

    class FakeService:
        def __init__(self, settings, device):
            captured["device"] = device

        def accept_invite(self, principal, token):
            captured["principal"] = principal
            captured["token"] = token
            return InviteAcceptance("ws-target", 3, False)

    monkeypatch.setattr(main_module, "DeviceAccountService", FakeService)

    token = _sign_token("invitee-1", key=private_pem)
    resp = client.post(
        "/api/device-invites/accept",
        json={
            "invite_token": "invite-token-123",
            "device_id": "forged-device-id",
            "device_proof": "forged-proof",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    # The device credential came from the trusted environment, never the body.
    assert captured["device"].device_id == os.environ["DEVICE_ID"]
    assert captured["device"].proof == PROOF
    # The principal is identity-only: authenticated, but workspaceless.
    assert captured["principal"].user_id == "invitee-1"
    assert captured["principal"].workspace_id is None
    assert captured["principal"].authenticated is True
    assert captured["token"] == "invite-token-123"


def test_device_accept_uses_identity_entry_not_workspace_auth(entry, monkeypatch):
    client, private_pem, ctx = entry
    monkeypatch.setattr(main_module, "DeviceAccountService", _acceptance_service())
    token = _sign_token("invitee-1", key=private_pem)

    with mock.patch.object(ctx, "authenticate", wraps=ctx.authenticate) as auth:
        with mock.patch.object(
            ctx, "authenticate_identity", wraps=ctx.authenticate_identity
        ) as identity:
            resp = client.post(
                "/api/device-invites/accept",
                json={"invite_token": "tok"},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    identity.assert_called_once()
    auth.assert_not_called()


def test_device_accept_never_sets_workspace_context(entry, monkeypatch):
    client, private_pem, ctx = entry
    monkeypatch.setattr(main_module, "DeviceAccountService", _acceptance_service())
    token = _sign_token("invitee-1", key=private_pem)

    with mock.patch.object(
        main_module, "set_workspace_context", wraps=main_module.set_workspace_context
    ) as set_ctx:
        with mock.patch.object(
            main_module,
            "reset_workspace_context",
            wraps=main_module.reset_workspace_context,
        ) as reset_ctx:
            resp = client.post(
                "/api/device-invites/accept",
                json={"invite_token": "tok"},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    set_ctx.assert_not_called()
    reset_ctx.assert_not_called()


# ── Token failures are 401, never a local-principal fallback ───────────


def test_device_accept_requires_bearer_token(entry):
    client, _, _ = entry

    resp = client.post("/api/device-invites/accept", json={"invite_token": "tok"})

    assert resp.status_code == 401


def test_device_accept_rejects_malformed_token(entry):
    client, _, _ = entry

    resp = client.post(
        "/api/device-invites/accept",
        json={"invite_token": "tok"},
        headers={"Authorization": "Bearer not-a-jwt"},
    )

    assert resp.status_code == 401


# ── The same no-membership identity stays 403 on product data ──────────


def test_no_membership_identity_forbidden_on_product_data(entry):
    client, private_pem, _ = entry
    token = _sign_token("invitee-1", key=private_pem)

    resp = client.get(
        "/api/creator-profile",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403


# ── DeviceAccountError kinds map to their documented HTTP statuses ──────


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        ("forbidden", 403),
        ("conflict", 409),
        ("invalid", 400),
        ("unavailable", 503),
    ],
)
def test_device_accept_error_mapping(entry, monkeypatch, kind, status):
    client, private_pem, _ = entry
    monkeypatch.setattr(
        main_module,
        "DeviceAccountService",
        _acceptance_service(error=DeviceAccountError(kind, f"kind-{kind}")),
    )
    token = _sign_token("invitee-1", key=private_pem)

    resp = client.post(
        "/api/device-invites/accept",
        json={"invite_token": "tok"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == status
    assert resp.json() == {"error": kind, "detail": f"kind-{kind}"}


# ── Trusted device credential provisioning failures are 503 ────────────


def test_device_accept_missing_device_credential_503(entry, monkeypatch):
    client, private_pem, _ = entry
    monkeypatch.delenv("DEVICE_ID")
    monkeypatch.delenv("DEVICE_PROOF")
    token = _sign_token("invitee-1", key=private_pem)

    resp = client.post(
        "/api/device-invites/accept",
        json={"invite_token": "tok"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 503
    assert resp.json()["error"] == "unavailable"


def test_device_accept_malformed_device_credential_503(entry, monkeypatch):
    client, private_pem, _ = entry
    monkeypatch.setenv("DEVICE_PROOF", "not-hex")
    token = _sign_token("invitee-1", key=private_pem)

    resp = client.post(
        "/api/device-invites/accept",
        json={"invite_token": "tok"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 503
    assert resp.json()["error"] == "unavailable"
