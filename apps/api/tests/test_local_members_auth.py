"""Member route guards (X-Local-Member-Token) and content visibility filters.

Covers the 第③步 contract: owner-only member management writes, join/whoami
token handout, and shared-only content lists for members under 方案 A —
anonymous callers keep the exact pre-feature behavior.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.database_migrations import (
    LOCAL_MEMBERS_TABLE_DDL,
    PRODUCTION_SCHEMA_VERSION,
    apply_production_migrations,
)
from app.local_members import LocalMemberService
from app.production import CreationTask, ProductionRepository

client = TestClient(main_module.app)


def _member_service(tmp_path, monkeypatch) -> LocalMemberService:
    service = LocalMemberService(tmp_path / "agent.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", service)
    return service


def _joined_member(service: LocalMemberService) -> tuple[str, str]:
    member, token = service.join(service.create_invite_code(), "小王")
    return member.member_id, token


def _member_headers(token: str) -> dict[str, str]:
    return {"X-Local-Member-Token": token}


def _test_repo(tmp_path, monkeypatch) -> ProductionRepository:
    repo = ProductionRepository(
        database_path=tmp_path / "production.sqlite3",
        video_library_path=tmp_path / "inbox",
    )
    monkeypatch.setattr(main_module, "production_repo", repo)
    return repo


def _insert_creation_task(
    database_path,
    workspace_id: str,
    task_id: str,
    visibility: str,
) -> None:
    task = CreationTask(
        id=task_id,
        workspace_id=workspace_id,
        plan_id=f"plan-{task_id}",
        name=task_id,
        scenario="vlog",
    )
    payload = json.dumps(asdict(task), default=str)
    with sqlite3.connect(str(database_path)) as conn:
        conn.execute(
            "INSERT INTO creation_tasks (id, workspace_id, plan_id, payload, "
            "updated_at, visibility) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, workspace_id, task.plan_id, payload, task.updated_at, visibility),
        )


def _insert_material_asset(
    database_path,
    workspace_id: str,
    asset_id: str,
    visibility: str,
    owner_user_id: str | None = None,
) -> None:
    """写入一条素材记录。

    `owner_user_id` 是这条素材的上传者：私有内容只对上传者本人可读，因此要断言
    「成员只看到共享」就必须给私有素材一个真实的他人归属。
    """
    payload = json.dumps(
        {
            "id": asset_id,
            "workspace_id": workspace_id,
            "owner_user_id": owner_user_id,
            "file_name": f"{asset_id}.mp4",
            "path": f"inbox/{asset_id}.mp4",
            "size_bytes": 12,
            "modified_at": "2026-09-28T00:00:00+00:00",
        }
    )
    with sqlite3.connect(str(database_path)) as conn:
        conn.execute(
            "INSERT INTO material_assets (id, workspace_id, file_path, payload, "
            "updated_at, visibility) VALUES (?, ?, ?, ?, ?, ?)",
            (
                asset_id,
                workspace_id,
                f"inbox/{asset_id}.mp4",
                payload,
                "2026-09-28T00:00:00+00:00",
                visibility,
            ),
        )


def test_join_response_contains_resolvable_member_token(tmp_path, monkeypatch):
    service = _member_service(tmp_path, monkeypatch)
    code = service.create_invite_code()

    response = client.post("/api/members/join", json={"code": code, "name": "小王"})

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "member"
    assert body["member_token"]
    resolved = service.resolve_member(body["member_token"])
    assert resolved is not None
    assert resolved.member_id == body["member_id"]


def test_member_token_reads_roster_without_exposing_tokens(tmp_path, monkeypatch):
    service = _member_service(tmp_path, monkeypatch)
    member_id, token = _joined_member(service)

    response = client.get("/api/members", headers=_member_headers(token))

    assert response.status_code == 200
    members = response.json()["members"]
    assert any(item["member_id"] == member_id for item in members)
    assert all("member_token" not in item for item in members)


def test_member_write_routes_are_forbidden(tmp_path, monkeypatch):
    service = _member_service(tmp_path, monkeypatch)
    member_id, token = _joined_member(service)
    headers = _member_headers(token)

    add_response = client.post("/api/members", json={"name": "新人"}, headers=headers)
    invite_response = client.post("/api/members/invite-code", headers=headers)
    patch_response = client.patch(
        f"/api/members/{member_id}", json={"role": "owner"}, headers=headers
    )
    other = service.add_member("小李", "member")
    delete_response = client.delete(
        f"/api/members/{other.member_id}", headers=headers
    )

    assert add_response.status_code == 403
    assert invite_response.status_code == 403
    assert patch_response.status_code == 403
    assert delete_response.status_code == 403
    assert add_response.json() == {
        "error": "forbidden",
        "detail": "仅所有者可执行此操作",
    }
    # None of the rejected writes took effect.
    assert {m.name for m in service.list_members()} == {"本机创作者", "小王", "小李"}


def test_owner_token_passes_member_write_routes(tmp_path, monkeypatch):
    service = _member_service(tmp_path, monkeypatch)
    _, owner_token = service.whoami(None)
    headers = _member_headers(owner_token)

    add_response = client.post("/api/members", json={"name": "新人"}, headers=headers)
    invite_response = client.post("/api/members/invite-code", headers=headers)

    assert add_response.status_code == 200
    assert invite_response.status_code == 200
    assert invite_response.json()["invite_code"]


def test_invalid_token_is_unauthorized(tmp_path, monkeypatch):
    _member_service(tmp_path, monkeypatch)
    headers = _member_headers("not-a-real-token")

    write_response = client.post("/api/members", json={"name": "新人"}, headers=headers)
    read_response = client.get("/api/creation-tasks", headers=headers)

    assert write_response.status_code == 401
    assert write_response.json()["error"] == "invalid"
    assert read_response.status_code == 401


def test_member_sees_only_shared_creation_tasks(tmp_path, monkeypatch):
    _member_service(tmp_path, monkeypatch)
    repo = _test_repo(tmp_path, monkeypatch)
    workspace_id = main_module.active_workspace_id()
    _insert_creation_task(repo.database_path, workspace_id, "task-shared", "shared")
    _insert_creation_task(repo.database_path, workspace_id, "task-private", "private")
    _, token = _joined_member(LocalMemberService(tmp_path / "agent.sqlite3"))

    member_view = client.get("/api/creation-tasks", headers=_member_headers(token))
    owner_view = client.get("/api/creation-tasks")

    assert member_view.status_code == 200
    assert {item["id"] for item in member_view.json()["items"]} == {"task-shared"}
    assert {item["id"] for item in owner_view.json()["items"]} == {
        "task-shared",
        "task-private",
    }
    # Single-task fetch keeps the same switch at the repository layer.
    with pytest.raises(KeyError):
        repo.get_creation_task(workspace_id, "task-private", include_private=False)
    assert repo.get_creation_task(workspace_id, "task-private").id == "task-private"


def test_member_sees_only_shared_materials(tmp_path, monkeypatch):
    service = _member_service(tmp_path, monkeypatch)
    owner_member, _owner_token = service.whoami(None)
    owner_member_id = owner_member.member_id
    repo = _test_repo(tmp_path, monkeypatch)
    workspace_id = main_module.active_workspace_id()
    _insert_material_asset(repo.database_path, workspace_id, "asset-shared", "shared")
    _insert_material_asset(
        repo.database_path, workspace_id, "asset-private", "private",
        owner_user_id=owner_member_id,
    )
    member_id, token = _joined_member(LocalMemberService(tmp_path / "agent.sqlite3"))

    member_view = client.get(
        "/api/production/materials", headers=_member_headers(token)
    )
    owner_view = client.get("/api/production/materials")

    assert member_view.status_code == 200
    assert member_view.json()["total_count"] == 1
    assert {asset["id"] for asset in member_view.json()["assets"]} == {"asset-shared"}
    assert owner_view.json()["total_count"] == 2
    # The page count query filters together with the rows.
    _, member_total = repo.list_assets_page(
        workspace_id, limit=24, offset=0,
        viewer_user_id=member_id,
    )
    assert member_total == 1


def test_zero_config_requests_without_header_keep_working(tmp_path, monkeypatch):
    _member_service(tmp_path, monkeypatch)

    assert client.get("/api/members").status_code == 200
    assert client.post("/api/members", json={"name": "直连用户"}).status_code == 200
    assert client.post("/api/members/invite-code").status_code == 200
    assert client.get("/api/creation-tasks").status_code == 200
    assert client.get("/api/production/materials").status_code == 200


def test_whoami_anonymous_returns_seed_owner_token(tmp_path, monkeypatch):
    service = _member_service(tmp_path, monkeypatch)

    response = client.get("/api/members/whoami")

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "owner"
    assert body["member_token"]
    resolved = service.resolve_member(body["member_token"])
    assert resolved is not None
    assert resolved.member_id == body["member_id"]


def test_whoami_with_member_token_echoes_that_member(tmp_path, monkeypatch):
    _member_service(tmp_path, monkeypatch)
    member_id, token = _joined_member(LocalMemberService(tmp_path / "agent.sqlite3"))

    response = client.get("/api/members/whoami", headers=_member_headers(token))

    assert response.status_code == 200
    body = response.json()
    assert body["member_id"] == member_id
    assert body["role"] == "member"
    assert body["member_token"] == token


def _chain_base_database(database_path) -> None:
    """Minimal pre-migration-13 database: the two content tables plus version
    rows 1..12, mirroring how real databases reach the new migration."""
    with sqlite3.connect(str(database_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE material_assets (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE creation_tasks (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE production_schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.executemany(
            "INSERT INTO production_schema_migrations (version) VALUES (?)",
            [(version,) for version in range(1, 13)],
        )


def test_migration_13_applies_twice_without_error(tmp_path):
    database_path = tmp_path / "chain.sqlite3"
    _chain_base_database(database_path)

    conn = sqlite3.connect(str(database_path))
    conn.row_factory = sqlite3.Row
    try:
        apply_production_migrations(conn)
        apply_production_migrations(conn)  # repeat apply must not raise

        versions = [
            int(row[0])
            for row in conn.execute(
                "SELECT version FROM production_schema_migrations ORDER BY version"
            )
        ]
    finally:
        conn.close()

    assert versions == list(range(1, PRODUCTION_SCHEMA_VERSION + 1))
    # The old database fixture carries migration markers but omits migration
    # 4's table. Reaching version 15 must restore and then really transform
    # that table, not merely stamp the version marker.
    with sqlite3.connect(database_path) as conn:
        narration_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(narration_segments)")
        }
    assert {"source_kind", "source_artifact_ref", "source_sha256"} <= narration_columns
    # Migration 13 is a version-only marker: it bumps the schema version without
    # touching creation_tasks/material_assets, so it stays safe on the bare
    # standalone chain (test_device_runtime). The visibility columns are added
    # by ProductionRepository._init_database's PRAGMA-guarded ensures instead.


def test_migration_14_adds_confirmation_columns_after_a_bare_upgrade(tmp_path):
    database_path = tmp_path / "intake-later.sqlite3"
    _chain_base_database(database_path)
    with sqlite3.connect(database_path) as conn:
        apply_production_migrations(conn)
        conn.execute("CREATE TABLE material_ingestion_files (file_path TEXT PRIMARY KEY, state TEXT)")
        conn.execute("INSERT INTO material_ingestion_files VALUES ('old.mp4', 'ready')")
        conn.commit()
        apply_production_migrations(conn)
        apply_production_migrations(conn)
        assert conn.execute(
            "SELECT file_path, state, confirmed_revision, confirmation_source, copy_confirmed_at FROM material_ingestion_files"
        ).fetchone() == ("old.mp4", "ready", None, None, None)
    with sqlite3.connect(database_path) as conn:
        apply_production_migrations(conn)
        assert len(conn.execute("PRAGMA table_info(material_ingestion_files)").fetchall()) == 5


def test_visibility_columns_are_provisioned_not_migrated(tmp_path, monkeypatch):
    # The content visibility columns come from ProductionRepository._init_database's
    # PRAGMA-guarded ensures, not from static migration SQL — SQLite has no
    # ADD COLUMN IF NOT EXISTS, and a plain ALTER would collide with the bare
    # standalone migration chain. A fresh repository must provision them.
    repo = _test_repo(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(repo.database_path))
    conn.row_factory = sqlite3.Row
    try:
        for table in ("creation_tasks", "material_assets"):
            columns = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA table_info({table})")
            }
            assert "visibility" in columns
    finally:
        conn.close()


def test_token_ensure_and_migration_chain_do_not_collide(tmp_path):
    # The members table is self-provisioned with its token column on every
    # service open; migration 13 must stay collision-free with that state.
    assert "token" not in LOCAL_MEMBERS_TABLE_DDL

    database_path = tmp_path / "mixed.sqlite3"
    service = LocalMemberService(database_path)
    service.add_member("小李", "member")
    _chain_base_database(database_path)

    conn = sqlite3.connect(str(database_path))
    conn.row_factory = sqlite3.Row
    try:
        apply_production_migrations(conn)
        apply_production_migrations(conn)  # repeat apply must not raise

        member_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(local_members)")
        }
        assert "token" in member_columns
    finally:
        conn.close()

    # The member's token survived the migration chain untouched.
    with sqlite3.connect(str(database_path)) as conn:
        stored = conn.execute(
            "SELECT token FROM local_members WHERE name = '小李'"
        ).fetchone()[0]
    resolved = LocalMemberService(database_path).resolve_member(stored)
    assert resolved is not None
    assert resolved.name == "小李"
