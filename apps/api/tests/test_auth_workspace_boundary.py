"""Focused tests for the Supabase auth + workspace partitioning boundary."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import app.main as main_module
from app.auth import (
    ForbiddenError,
    SupabaseAuthContext,
    UnauthorizedError,
    extract_bearer_token,
)
from app.config import Settings
from app.repository import (
    EpisodeRepository,
    WorkspaceContextRequiredError,
    active_workspace_id,
    load_preference_state_payload,
    reset_workspace_context,
    save_preference_state_payload,
    set_auth_enabled,
    set_workspace_context,
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
        supabase_secret_key="service-role-secret",
        supabase_publishable_key="anon-key",
        database_path=str(tmp_path / "db.sqlite"),
    )


def _fake_jwks_client(public_pem: bytes):
    class FakeClient:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=public_pem)

    return FakeClient()


@pytest.fixture
def auth_ctx(tmp_path):
    private_pem, public_pem = _make_rsa_keypair()
    settings = _auth_enabled_settings(tmp_path)
    ctx = SupabaseAuthContext(settings)
    ctx._jwks_client = lambda: _fake_jwks_client(public_pem)
    return ctx, settings, private_pem, public_pem


# ── Token extraction ────────────────────────────────────────────────────


def test_extract_bearer_token_variants():
    from fastapi import Request

    assert extract_bearer_token(Request({"type": "http", "headers": []})) is None

    scope = {
        "type": "http",
        "headers": [(b"authorization", b"Bearer abc.def.ghi")],
    }
    assert extract_bearer_token(Request(scope)) == "abc.def.ghi"

    scope = {
        "type": "http",
        "headers": [(b"authorization", b"Basic dXNlcjpwYXNz")],
    }
    assert extract_bearer_token(Request(scope)) is None


# ── JWT verification ────────────────────────────────────────────────────


def test_verify_token_returns_sub_for_valid_token(auth_ctx):
    ctx, _, private_pem, _ = auth_ctx
    token = _sign_token("user-123", key=private_pem)
    assert ctx.verify_token(token)["sub"] == "user-123"


def test_verify_token_rejects_wrong_audience(auth_ctx):
    ctx, _, private_pem, _ = auth_ctx
    token = _sign_token("user-123", audience="wrong-audience", key=private_pem)
    with pytest.raises(UnauthorizedError):
        ctx.verify_token(token)


def test_verify_token_rejects_expired_token(auth_ctx):
    ctx, _, private_pem, _ = auth_ctx
    token = _sign_token(
        "user-123",
        exp=datetime.now(UTC) - timedelta(seconds=1),
        key=private_pem,
    )
    with pytest.raises(UnauthorizedError):
        ctx.verify_token(token)


def test_verify_token_rejects_when_auth_disabled():
    ctx = SupabaseAuthContext(Settings(deepseek_api_key="sk-test"))
    with pytest.raises(UnauthorizedError):
        ctx.verify_token("anything")


# ── Workspace membership resolution ─────────────────────────────────────


def test_resolve_workspace_prefers_owner_role(auth_ctx):
    ctx, _, _, _ = auth_ctx

    class FakeResponse:
        status_code = 200
        content = json.dumps([
            {"workspace_id": "ws-member", "role": "member"},
            {"workspace_id": "ws-owner", "role": "owner"},
        ]).encode()

        def json(self):
            return json.loads(self.content)

    with mock.patch("app.auth.httpx.get", return_value=FakeResponse()):
        assert ctx.resolve_workspace_id("user-1") == "ws-owner"


def test_resolve_workspace_returns_none_when_no_membership(auth_ctx):
    ctx, _, _, _ = auth_ctx

    class FakeResponse:
        status_code = 200
        content = b"[]"

        def json(self):
            return json.loads(self.content)

    with mock.patch("app.auth.httpx.get", return_value=FakeResponse()):
        assert ctx.resolve_workspace_id("user-1") is None


def test_resolve_workspace_raises_forbidden_on_http_error(auth_ctx):
    ctx, _, _, _ = auth_ctx

    class FakeResponse:
        status_code = 500
        content = b"boom"

    with mock.patch("app.auth.httpx.get", return_value=FakeResponse()), pytest.raises(
        ForbiddenError
    ):
        ctx.resolve_workspace_id("user-1")


# ── Middleware: 401 vs 403 vs 200 ───────────────────────────────────────


def test_public_endpoints_skip_auth_when_enabled(auth_ctx, monkeypatch):
    ctx, settings, _, _ = auth_ctx
    monkeypatch.setattr(main_module, "auth_context", ctx)
    monkeypatch.setattr(main_module, "settings", settings)

    client = TestClient(main_module.app)
    assert client.get("/api/runtime/health").status_code == 200
    assert client.get("/api/setup/preflight").status_code == 200


def test_protected_endpoint_requires_token_when_enabled(auth_ctx, monkeypatch):
    ctx, settings, _, _ = auth_ctx
    monkeypatch.setattr(main_module, "auth_context", ctx)
    monkeypatch.setattr(main_module, "settings", settings)

    client = TestClient(main_module.app)
    response = client.get("/api/episodes/today")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_protected_endpoint_invalid_token_401(auth_ctx, monkeypatch):
    ctx, settings, _, _ = auth_ctx
    monkeypatch.setattr(main_module, "auth_context", ctx)
    monkeypatch.setattr(main_module, "settings", settings)

    client = TestClient(main_module.app)
    response = client.get(
        "/api/episodes/today",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_protected_endpoint_no_workspace_403(auth_ctx, monkeypatch):
    ctx, settings, private_pem, _ = auth_ctx
    ctx.resolve_workspace_id = lambda user_id: None
    monkeypatch.setattr(main_module, "auth_context", ctx)
    monkeypatch.setattr(main_module, "settings", settings)

    token = _sign_token("user-no-ws", key=private_pem)
    client = TestClient(main_module.app)
    response = client.get(
        "/api/episodes/today",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_protected_endpoint_authorized_workspace_200(auth_ctx, monkeypatch):
    ctx, settings, private_pem, _ = auth_ctx
    ctx.resolve_workspace_id = lambda user_id: "ws-a"
    monkeypatch.setattr(main_module, "auth_context", ctx)
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(
        main_module,
        "repo",
        EpisodeRepository(database_path=settings.database_path),
    )

    token = _sign_token("user-a", key=private_pem)
    client = TestClient(main_module.app)
    response = client.get(
        "/api/episodes/today",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


# ── Migration: legacy rows become 'local', partitioned queries ─────────


def test_migration_partitions_legacy_rows_as_local(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE episodes (
                id TEXT PRIMARY KEY,
                episode_date TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO episodes (id, episode_date, payload, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (
                "ep_legacy",
                "2026-01-01",
                json.dumps({
                    "id": "ep_legacy",
                    "day_number": 1,
                    "episode_date": "2026-01-01",
                    "status": "created",
                    "intake_mode": "planned_single",
                    "topics": [],
                }),
                "2026-01-01T00:00:00+00:00",
            ),
        )

    repo = EpisodeRepository(database_path=db_path)
    # Legacy row is assigned the reserved local workspace
    assert repo.get("ep_legacy").workspace_id == "local"

    # A fresh authenticated workspace cannot see legacy rows
    with pytest.raises(KeyError):
        repo.get("ep_legacy", workspace_id="ws-a")

    # The new schema has a per-workspace unique constraint
    with sqlite3.connect(db_path) as conn:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(episodes)")}
        assert "workspace_id" in columns


def test_repository_workspace_isolation(tmp_path):
    repo = EpisodeRepository(database_path=tmp_path / "db.sqlite")
    a = repo.create(workspace_id="ws-a")
    b = repo.create(workspace_id="ws-b")

    assert repo.get(a.id, workspace_id="ws-a") == a
    assert repo.get(b.id, workspace_id="ws-b") == b

    # Cross-workspace access must not leak existence
    with pytest.raises(KeyError):
        repo.get(a.id, workspace_id="ws-b")

    assert [e.id for e in repo.all(workspace_id="ws-a")] == [a.id]
    assert [e.id for e in repo.all(workspace_id="ws-b")] == [b.id]


def test_repository_uses_active_workspace_context(tmp_path):
    repo = EpisodeRepository(database_path=tmp_path / "db.sqlite")
    a = repo.create(workspace_id="ws-a")
    b = repo.create(workspace_id="ws-b")

    token = set_workspace_context("ws-a")
    try:
        assert active_workspace_id() == "ws-a"
        assert repo.get(a.id) == a
        with pytest.raises(KeyError):
            repo.get(b.id)
        assert [e.id for e in repo.all()] == [a.id]
    finally:
        reset_workspace_context(token)


# ── Global-state preference partitioning ────────────────────────────────


def test_preference_state_partitioned_by_workspace(tmp_path, monkeypatch):
    import app.repository as repo_mod

    monkeypatch.setattr(repo_mod, "_GLOBAL_STATE_FILE", tmp_path / "state.json")
    save_preference_state_payload({"disabled_signals": ["x"]}, workspace_id="ws-a")
    save_preference_state_payload({"disabled_signals": ["y"]}, workspace_id="ws-b")

    assert load_preference_state_payload(workspace_id="ws-a") == {
        "disabled_signals": ["x"]
    }
    assert load_preference_state_payload(workspace_id="ws-b") == {
        "disabled_signals": ["y"]
    }


def test_legacy_flat_state_only_visible_to_local(tmp_path, monkeypatch):
    import app.repository as repo_mod

    monkeypatch.setattr(repo_mod, "_GLOBAL_STATE_FILE", tmp_path / "legacy.json")
    repo_mod.save_global_state({"preference_state": {"disabled_signals": ["z"]}})

    assert load_preference_state_payload(workspace_id="local") == {
        "disabled_signals": ["z"]
    }
    assert load_preference_state_payload(workspace_id="ws-a") is None


# ── Request bodies can't forge user/workspace identity ──────────────────


def test_request_body_workspace_id_is_ignored(auth_ctx, monkeypatch):
    ctx, settings, private_pem, _ = auth_ctx
    ctx.resolve_workspace_id = lambda user_id: "ws-token"
    monkeypatch.setattr(main_module, "auth_context", ctx)
    monkeypatch.setattr(main_module, "settings", settings)
    repo = EpisodeRepository(database_path=settings.database_path)
    monkeypatch.setattr(main_module, "repo", repo)

    token = _sign_token("user-a", key=private_pem)
    client = TestClient(main_module.app)

    # A body claiming a different workspace has no effect: the episode lands
    # in the workspace derived from the verified token.
    response = client.post(
        "/api/episodes",
        json={"workspace_id": "ws-evil"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    created = response.json()

    assert repo.get(created["id"], workspace_id="ws-token").workspace_id == "ws-token"
    with pytest.raises(KeyError):
        repo.get(created["id"], workspace_id="ws-evil")


def test_cross_workspace_isolation_through_http(auth_ctx, monkeypatch):
    """An authenticated request can only read its own workspace's episode."""
    ctx, settings, private_pem, _ = auth_ctx
    monkeypatch.setattr(main_module, "auth_context", ctx)
    monkeypatch.setattr(main_module, "settings", settings)
    repo = EpisodeRepository(database_path=settings.database_path)
    monkeypatch.setattr(main_module, "repo", repo)

    # Create an episode belonging to ws-b directly.
    episode_b = repo.create(workspace_id="ws-b")

    # Authenticate as ws-a and try to read ws-b's episode -> 404 (no leak).
    ctx.resolve_workspace_id = lambda user_id: "ws-a"
    token = _sign_token("user-a", key=private_pem)
    client = TestClient(main_module.app)
    response = client.get(
        f"/api/episodes/{episode_b.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404

    # The list endpoint also never includes ws-b's episode.
    list_response = client.get(
        "/api/episodes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_response.status_code == 200
    ids = [e["id"] for e in list_response.json()["episodes"]]
    assert episode_b.id not in ids


# ── Creator profile scoping in the LLM prompt path ─────────────────────


def test_creator_profile_scoped_per_workspace(tmp_path):
    from app.creator_profile import (
        get_creator_profile,
        get_or_create_profile,
        upsert_creator_profile,
    )

    db = tmp_path / "profiles.sqlite"
    a = get_or_create_profile(db, workspace_id="ws-a")
    a.common_expressions = ["把“交付”挂嘴边"]
    upsert_creator_profile(a, db, workspace_id="ws-a")

    b = get_or_create_profile(db, workspace_id="ws-b")
    b.common_expressions = ["把“对齐”挂嘴边"]
    upsert_creator_profile(b, db, workspace_id="ws-b")

    assert get_creator_profile(db, workspace_id="ws-a").common_expressions == [
        "把“交付”挂嘴边"
    ]
    assert get_creator_profile(db, workspace_id="ws-b").common_expressions == [
        "把“对齐”挂嘴边"
    ]


def test_llm_prompt_profile_context_is_workspace_scoped(tmp_path):
    from app.creator_profile import get_or_create_profile, upsert_creator_profile
    from app.llm import DeepSeekScriptGenerator

    settings = Settings(
        deepseek_api_key="sk-test",
        database_path=str(tmp_path / "db.sqlite"),
    )
    a = get_or_create_profile(settings.database_path, workspace_id="ws-a")
    a.common_expressions = ["把“交付”挂嘴边"]
    upsert_creator_profile(a, settings.database_path, workspace_id="ws-a")

    b = get_or_create_profile(settings.database_path, workspace_id="ws-b")
    b.common_expressions = ["把“对齐”挂嘴边"]
    upsert_creator_profile(b, settings.database_path, workspace_id="ws-b")

    generator = DeepSeekScriptGenerator(settings)

    token = set_workspace_context("ws-a")
    try:
        prompt_a = generator._system_prompt()
    finally:
        reset_workspace_context(token)

    token = set_workspace_context("ws-b")
    try:
        prompt_b = generator._system_prompt()
    finally:
        reset_workspace_context(token)

    assert "把“交付”挂嘴边" in prompt_a
    assert "把“对齐”挂嘴边" not in prompt_a
    assert "把“对齐”挂嘴边" in prompt_b
    assert "把“交付”挂嘴边" not in prompt_b


# ── Fail-safe: auth enabled never silently reads the local workspace ───


def test_creator_profile_requires_context_when_auth_enabled(tmp_path):
    from app.creator_profile import get_creator_profile

    set_auth_enabled(True)
    try:
        with pytest.raises(WorkspaceContextRequiredError):
            get_creator_profile(str(tmp_path / "db.sqlite"))
    finally:
        set_auth_enabled(False)


def test_legacy_local_profile_only_visible_in_explicit_local_mode(tmp_path):
    from app.creator_profile import (
        get_creator_profile,
        get_or_create_profile,
        upsert_creator_profile,
    )
    from app.repository import LEGACY_WORKSPACE_ID

    db = tmp_path / "legacy.sqlite"
    local = get_or_create_profile(db, workspace_id=LEGACY_WORKSPACE_ID)
    local.common_expressions = ["本地画像"]
    upsert_creator_profile(local, db, workspace_id=LEGACY_WORKSPACE_ID)

    # Explicit local mode sees the legacy profile.
    assert get_creator_profile(db, workspace_id=LEGACY_WORKSPACE_ID).common_expressions == [
        "本地画像"
    ]

    # Another workspace never sees it.
    assert get_creator_profile(db, workspace_id="ws-a") is None

    # Auth-disabled local-compat mode (no context) still resolves local.
    set_auth_enabled(False)
    assert get_creator_profile(db).common_expressions == ["本地画像"]


def test_active_workspace_fails_safe_when_auth_enabled_and_no_context():
    set_auth_enabled(True)
    try:
        with pytest.raises(WorkspaceContextRequiredError):
            active_workspace_id()
    finally:
        set_auth_enabled(False)


def test_current_workspace_id_fails_safe_without_principal_when_auth_enabled(
    auth_ctx, monkeypatch
):
    from fastapi import Request

    _, settings, _, _ = auth_ctx
    monkeypatch.setattr(main_module, "settings", settings)
    set_auth_enabled(True)
    try:
        request = Request({"type": "http", "headers": []})
        with pytest.raises(WorkspaceContextRequiredError):
            main_module.current_workspace_id(request)
    finally:
        set_auth_enabled(False)


def test_endpoint_generation_fails_safe_without_context(auth_ctx, monkeypatch):
    """An authenticated request whose workspace context is lost must not
    silently generate against the reserved 'local' workspace."""
    ctx, settings, private_pem, _ = auth_ctx
    ctx.resolve_workspace_id = lambda user_id: "ws-a"
    monkeypatch.setattr(main_module, "auth_context", ctx)
    monkeypatch.setattr(main_module, "settings", settings)
    repo = EpisodeRepository(database_path=settings.database_path)
    monkeypatch.setattr(main_module, "repo", repo)

    token = _sign_token("user-a", key=private_pem)
    client = TestClient(main_module.app)

    # Normal authenticated path: the middleware sets the workspace context
    # and the endpoint succeeds.
    set_auth_enabled(True)
    try:
        ok = client.post(
            "/api/episodes",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert ok.status_code == 200

        # Simulate a lost context: no principal attached but auth is still
        # enabled. current_workspace_id must fail instead of resolving to
        # 'local'.
        from fastapi import Request

        bare_request = Request({"type": "http", "headers": []})
        with pytest.raises(WorkspaceContextRequiredError):
            main_module.current_workspace_id(bare_request)
    finally:
        set_auth_enabled(False)


def test_authenticated_creator_profile_never_reads_local(auth_ctx, monkeypatch):
    """An authenticated request's creator profile must not inherit the
    reserved 'local' workspace's profile."""
    from app.creator_profile import get_or_create_profile, upsert_creator_profile

    ctx, settings, private_pem, _ = auth_ctx
    ctx.resolve_workspace_id = lambda user_id: "ws-a"
    monkeypatch.setattr(main_module, "auth_context", ctx)
    monkeypatch.setattr(main_module, "settings", settings)
    repo = EpisodeRepository(database_path=settings.database_path)
    monkeypatch.setattr(main_module, "repo", repo)
    set_auth_enabled(True)

    # Seed a distinctive profile in the reserved local workspace.
    local = get_or_create_profile(settings.database_path, workspace_id="local")
    local.common_expressions = ["本地专属口头禅"]
    upsert_creator_profile(local, settings.database_path, workspace_id="local")

    token = _sign_token("user-a", key=private_pem)
    client = TestClient(main_module.app)
    try:
        response = client.get(
            "/api/creator-profile",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "本地专属口头禅" not in body["common_expressions"]
    finally:
        set_auth_enabled(False)
