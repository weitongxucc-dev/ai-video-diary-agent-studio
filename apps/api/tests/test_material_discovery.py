"""Synthetic bytes in a real temporary filesystem/SQLite; no media/model calls."""
import hashlib
from pathlib import Path
import sqlite3

import pytest

from app.materials.discovery import MaterialDiscoveryService
from app.materials.discovery_store import DISCOVERY_SCHEMA, DiscoveryStore
from app.materials.discovery_intake import DiscoveryIntake
from app.materials.assets import MaterialAssetStore
from app.materials.source_files import MaterialSourceFileAccess
from app.materials.workspace_references import WorkspaceMediaReferenceAccess


@pytest.fixture
def environment(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "inbox"
    source.mkdir()
    target.mkdir()
    database = tmp_path / "test.sqlite"
    def connect():
        conn = sqlite3.connect(database)
        conn.row_factory = sqlite3.Row
        return conn
    with connect() as conn:
        conn.executescript(DISCOVERY_SCHEMA + """
          CREATE TABLE material_assets (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
            file_path TEXT NOT NULL, payload TEXT NOT NULL, visibility TEXT NOT NULL,
            updated_at TEXT NOT NULL, UNIQUE(workspace_id,file_path));
          CREATE TABLE workspace_media_references (reference TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)
    assets = MaterialAssetStore(connect)
    confirmations = []
    wakeups = []
    def target_for(workspace):
        if workspace != "ws" or not target.is_dir():
            raise ValueError("原接收盘不可用或工作区不匹配")
        return target
    intake = DiscoveryIntake(connect, assets, target_for=target_for,
        workspace_references=WorkspaceMediaReferenceAccess(connect, MaterialSourceFileAccess(connect, target), assets),
        confirm_copy=lambda *args, **kwargs: confirmations.append((args, kwargs)))
    store = DiscoveryStore(connect)
    def make_service():
        return MaterialDiscoveryService(store, root_provider=lambda: [source], excluded_roots=lambda: [target], target_for=target_for, accept_copy=intake)
    service = make_service()
    return source, target, connect, store, assets, service, make_service, confirmations, wakeups


def scan(env, exclusions=None):
    service = env[5]
    session = service.start("ws", "alice", root_ids=[service.roots()[0]["id"]], exclusions=exclusions)
    while session["state"] == "scanning":
        session = service.advance(session["id"], "ws", "alice", budget=2)
    return session


def count(env):
    with env[2]() as conn:
        return conn.execute("SELECT COUNT(*) FROM material_assets").fetchone()[0]


def test_candidates_never_register_or_analyze_before_selection(environment):
    source, target, *_ = environment
    (source / "a.mp4").write_bytes(b"synthetic-video")
    (source / "b.jpg").write_bytes(b"synthetic-image")
    session = scan(environment)
    assert session["total_count"] == 2 and count(environment) == 0
    assert not list(target.iterdir()) and not environment[7] and not environment[8]
    chosen = next(c for c in session["candidates"] if c["file_name"] == "b.jpg")
    result = environment[5].receive(session["id"], "ws", "alice", [chosen["id"]])
    assert count(environment) == 1
    assert next(c for c in result["candidates"] if c["id"] == chosen["id"])["state"] == "received"
    assert (source / "b.jpg").read_bytes() == b"synthetic-image"
    assert len(list(target.iterdir())) == 1
    received = next(c for c in result["candidates"] if c["id"] == chosen["id"])
    existing = environment[4].get_asset("ws", received["asset_id"])
    assert existing.file_name == "b.jpg"
    with environment[2]() as conn:
        assert conn.execute("SELECT workspace_id FROM workspace_media_references").fetchone()[0] == "ws"
    copied = Path(existing.path)
    replay = environment[4].save_discovered_asset(existing, "ws", copied, copied.stat(), media_type=None,
        source_url="", owner_user_id="alice", copy_uploader_user_id="alice")
    assert replay.file_name == "b.jpg"
    assert environment[7][0][1]["confirmation_source"] == "app_import"


def test_exclusions_symlinks_and_software_outputs_are_not_candidates(environment):
    source = environment[0]
    excluded = source / "private"
    excluded.mkdir()
    (excluded / "secret.mp4").write_bytes(b"private")
    dependencies = source / "node_modules"
    dependencies.mkdir()
    (dependencies / "demo.mp4").write_bytes(b"demo")
    (source / "shortcut.mp4").symlink_to(excluded / "secret.mp4")
    (source / "ok.mp4").write_bytes(b"ok")
    session = scan(environment, [str(excluded)])
    assert [c["file_name"] for c in session["candidates"]] == ["ok.mp4"]
    candidate = session["candidates"][0]
    environment[5].control(session["id"], "ws", "alice", "exclude", exclusions=[str(source)])
    with pytest.raises(ValueError):
        environment[5].preview_path(session["id"], candidate["id"], "ws", "alice")
    result = environment[5].receive(session["id"], "ws", "alice", [candidate["id"]])
    assert result["candidates"][0]["state"] == "failed" and count(environment) == 0


def test_pause_restart_resume_cancel_are_durable(environment):
    for i in range(5):
        (environment[0] / f"{i}.mp4").write_bytes(b"x")
    service = environment[5]
    started = service.start("ws", "alice", root_ids=[service.roots()[0]["id"]])
    first = service.advance(started["id"], "ws", "alice", budget=1)
    assert first["total_count"] == 1
    service.control(first["id"], "ws", "alice", "pause")
    recovered = environment[6]()
    assert recovered.advance(first["id"], "ws", "alice")["total_count"] == 1
    recovered.control(first["id"], "ws", "alice", "resume")
    assert recovered.advance(first["id"], "ws", "alice")["total_count"] == 5
    recovered.control(first["id"], "ws", "alice", "cancel")
    assert recovered.latest("ws", "alice")["state"] == "cancelled"
    assert count(environment) == 0 and len(list(environment[0].iterdir())) == 5


def test_other_member_workspace_or_forged_candidate_cannot_receive(environment):
    (environment[0] / "a.mp4").write_bytes(b"x")
    session = scan(environment)
    for workspace, owner in [("other", "alice"), ("ws", "bob")]:
        with pytest.raises(KeyError):
            environment[5].receive(session["id"], workspace, owner, [session["candidates"][0]["id"]])
    with pytest.raises(KeyError):
        environment[5].receive(session["id"], "ws", "alice", [session["candidates"][0]["id"], "forged"])
    assert count(environment) == 0


def test_same_bytes_repeat_scan_and_receipt_gap_preserve_existing_asset(environment, monkeypatch):
    source, target, connect, store, assets, service, *_ = environment
    (source / "a.mp4").write_bytes(b"same-content")
    (source / "b.mp4").write_bytes(b"same-content")
    session = scan(environment)
    ids = [c["id"] for c in session["candidates"]]
    original_save = store.save_receipt
    def fail_after_asset(workspace, owner, digest, receipt):
        if receipt["asset_id"]:
            raise OSError("synthetic receipt write interruption")
        original_save(workspace, owner, digest, receipt)
    monkeypatch.setattr(store, "save_receipt", fail_after_asset)
    assert service.receive(session["id"], "ws", "alice", ids[:1])["candidates"][0]["state"] == "failed"
    assert count(environment) == 1
    digest = hashlib.sha256(b"same-content").hexdigest()
    receipt = store.receipt("ws", "alice", digest)
    current = assets.get_asset_by_path("ws", receipt["path"])
    current.user_tags = ["人工纠正"]
    current.analysis_status = "ready"
    assets.save_asset(current)
    monkeypatch.setattr(store, "save_receipt", original_save)
    service.receive(session["id"], "ws", "alice", ids)
    second = scan(environment)
    assert all(c["duplicate_asset_id"] == current.id for c in second["candidates"])
    monkeypatch.setattr("app.materials.discovery.tempfile.NamedTemporaryFile", lambda **kwargs: pytest.fail("receipt replay must not copy the source again"))
    service.receive(second["id"], "ws", "alice", [c["id"] for c in second["candidates"]])
    assert count(environment) == 1 and len(list(target.iterdir())) == 1
    restored = assets.get_asset("ws", current.id)
    assert restored.user_tags == ["人工纠正"] and restored.analysis_status == "ready"


def test_missing_target_and_source_change_cannot_register(environment):
    source = environment[0] / "clip.mp4"
    source.write_bytes(b"one")
    session = scan(environment)
    candidate_id = session["candidates"][0]["id"]
    source.write_bytes(b"different")
    result = environment[5].receive(session["id"], "ws", "alice", [candidate_id])
    assert result["candidates"][0]["state"] == "failed" and count(environment) == 0
    session = scan(environment)
    environment[1].rmdir()
    result = environment[5].receive(session["id"], "ws", "alice", [session["candidates"][0]["id"]])
    assert result["candidates"][0]["state"] == "failed" and not environment[1].exists()


def test_permission_failure_is_reported_not_whole_disk_success(environment):
    locked = environment[0] / "denied"
    locked.mkdir()
    locked.chmod(0)
    try:
        session = scan(environment)
        assert session["state"] == "completed" and session["skipped_count"] == 1
        assert session["issues"][0]["location"] == str(locked)
    finally:
        locked.chmod(0o700)


def test_cancel_during_copy_leaves_no_asset_or_partial(environment, monkeypatch):
    (environment[0] / "large.mp4").write_bytes(b"x" * (3 * 1024 * 1024))
    session = scan(environment)
    service = environment[5]
    original = service._copy_permitted
    calls = 0
    def cancel(*args):
        nonlocal calls
        calls += 1
        if calls == 3:
            service.control(session["id"], "ws", "alice", "cancel")
        original(*args)
    monkeypatch.setattr(service, "_copy_permitted", cancel)
    result = service.receive(session["id"], "ws", "alice", [session["candidates"][0]["id"]])
    assert result["candidates"][0]["state"] == "failed" and count(environment) == 0
    assert not list(environment[1].iterdir())


def test_new_exclusion_revokes_read_from_an_older_session(environment):
    (environment[0] / "clip.mp4").write_bytes(b"synthetic")
    first = scan(environment)
    second = scan(environment)
    environment[5].control(second["id"], "ws", "alice", "exclude", exclusions=[str(environment[0])])
    with pytest.raises(ValueError):
        environment[5].preview_path(first["id"], first["candidates"][0]["id"], "ws", "alice")
    assert count(environment) == 0
