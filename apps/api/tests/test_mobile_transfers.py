"""Real filesystem/SQLite byte intake; synthetic bytes, no media/model execution."""
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from starlette.requests import ClientDisconnect

from app.materials.assets import MaterialAssetStore
from app.materials.source_files import MaterialSourceFileAccess
from app.materials.workspace_references import WorkspaceMediaReferenceAccess
from app.mobile_transfer_routes import build_mobile_transfer_router
from app.mobile_transfers import MobileTransferActor, MobileTransferError, MobileTransfers
from app.production import ProductionRepository


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    repository = ProductionRepository(tmp_path / "app.sqlite3", root)
    repository.material_source_files.bind_material_ingestion("workspace", expected_directory=str(root))
    assets = MaterialAssetStore(repository._connect)
    references = WorkspaceMediaReferenceAccess(repository._connect,
        MaterialSourceFileAccess(repository._connect, root), assets)
    def confirm(conn, workspace_id, **values):
        repository.material_source_files.confirm_material_copy_complete(workspace_id, connection=conn, **values)
    def make():
        return MobileTransfers(repository._connect, assets=assets, workspace_references=references,
            target_for=repository.material_source_files.material_link_target, confirm_copy_in_transaction=confirm,
            min_free_disk_bytes=0, max_bytes=4 * 1024 * 1024)
    return SimpleNamespace(root=root, repository=repository, assets=assets,
        references=references, make=make, service=make(),
        actor=MobileTransferActor("workspace", "member", "user"))


DATA = b"synthetic mobile media bytes\0" * 200


def declaration(data=DATA, **changes):
    return {"filename": "phone.mp4", "mime_type": "video/mp4", "byte_length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "description": "手机拍摄的说明", **changes}


async def chunks(data=DATA, step=113):
    for offset in range(0, len(data), step):
        yield data[offset:offset + step]


def received(env, request_id=None, data=DATA):
    request_id = request_id or str(uuid4())
    env.service.declare(env.actor, request_id, **declaration(data))
    view = asyncio.run(env.service.receive_content(env.actor, request_id, chunks(data)))
    return request_id, view


def facts(env):
    with env.repository._connect() as conn:
        return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
            ("material_ingestion_files", "material_assets", "workspace_media_references"))


def test_whole_file_verification_then_atomic_receipt_and_canonical_reference(env):
    request_id = str(uuid4())
    view = env.service.declare(env.actor, request_id, **declaration())
    assert view["state"] == "declared" and view["received_bytes"] == 0 and facts(env) == (0, 0, 0)
    with pytest.raises(MobileTransferError, match="尚未校验"):
        env.service.complete(env.actor, request_id)
    request_id, view = received(env, request_id)
    assert view["state"] == "received" and view["received_bytes"] == len(DATA)
    assert view["asset_id"] is None and facts(env) == (0, 0, 0)
    completed = env.service.complete(env.actor, request_id)
    assert completed["state"] == "completed" and completed["asset_ref"].startswith("workspace://discovery-")
    assert facts(env) == (1, 1, 1)
    asset = env.assets.get_asset("workspace", completed["asset_id"])
    assert asset.owner_user_id == "user" and asset.visibility == "private"
    assert asset.file_name == "phone.mp4" and asset.user_description == "手机拍摄的说明"
    assert asset.analysis_status == "pending" and not asset.segments
    with Path(asset.path).open("rb") as file:
        assert hashlib.file_digest(file, "sha256").hexdigest() == declaration()["sha256"]
    assert not list((env.root / ".mobile-transfers").glob("*.part"))
    assert env.repository.material_source_files.pending_material_copies("workspace") == []  # Final ctime survives staging cleanup.
    assert env.make().query(env.actor, request_id) == completed
    assert env.make().complete(env.actor, request_id) == completed
    assert asyncio.run(env.service.receive_content(env.actor, request_id, chunks(b"ignored replay"))) == completed
    with pytest.raises(MobileTransferError, match="已入库"):
        env.service.cancel(env.actor, request_id)


def test_same_content_reuses_asset_without_overwriting_manual_facts(env):
    request_id, _ = received(env)
    first = env.service.complete(env.actor, request_id)
    asset = env.assets.get_asset("workspace", first["asset_id"])
    asset.user_description, asset.analysis_message = "人工修订保留", "existing analysis"
    env.assets.save_asset(asset)
    second_id, _ = received(env)
    second = env.service.complete(env.actor, second_id)
    assert second["asset_id"] == first["asset_id"] and facts(env) == (1, 1, 1)
    saved = env.assets.get_asset("workspace", first["asset_id"])
    assert (saved.user_description, saved.analysis_message) == ("人工修订保留", "existing analysis")


@pytest.mark.parametrize("changes,code", [
    ({"filename": "../phone.mp4"}, "invalid_filename"),
    ({"filename": "phone.exe"}, "unsupported_media"),
    ({"mime_type": "image/png"}, "unsupported_media"),
    ({"byte_length": True}, "invalid_length"),
    ({"byte_length": 0}, "invalid_length"),
    ({"byte_length": 4 * 1024 * 1024 + 1}, "invalid_length"),
    ({"sha256": "g" * 64}, "invalid_sha256"),
])
def test_invalid_declarations_never_receive_or_register(env, changes, code):
    with pytest.raises(MobileTransferError) as error:
        env.service.declare(env.actor, str(uuid4()), **declaration(**changes))
    assert error.value.code == code and facts(env) == (0, 0, 0)


def test_manifest_is_immutable_and_all_actions_are_member_and_workspace_scoped(env):
    request_id = str(uuid4())
    original = env.service.declare(env.actor, request_id, **declaration())
    assert env.service.declare(env.actor, request_id, **declaration()) == original
    with pytest.raises(MobileTransferError) as conflict:
        env.service.declare(env.actor, request_id, **declaration(description="changed"))
    assert conflict.value.code == "request_conflict"
    for actor in (MobileTransferActor("other", "member", "user"),
                  MobileTransferActor("workspace", "other", "user"),
                  MobileTransferActor("workspace", "member", "other")):
        for action in (env.service.query, env.service.complete, env.service.cancel):
            with pytest.raises(MobileTransferError) as denied:
                action(actor, request_id)
            assert denied.value.status == 404
    assert facts(env) == (0, 0, 0)


@pytest.mark.parametrize("data", [DATA[:-1], DATA + b"extra", b"x" * len(DATA)])
def test_partial_excess_and_wrong_digest_never_become_assets_and_retry_starts_zero(env, data):
    request_id = str(uuid4())
    env.service.declare(env.actor, request_id, **declaration())
    with pytest.raises(MobileTransferError):
        asyncio.run(env.service.receive_content(env.actor, request_id, chunks(data)))
    view = env.make().query(env.actor, request_id)
    assert view["state"] == "failed" and view["received_bytes"] == 0 and view["restart_from_zero"]
    assert view["error"]["retryable"] and facts(env) == (0, 0, 0)
    assert not list((env.root / ".mobile-transfers").glob("*.part"))
    asyncio.run(env.make().receive_content(env.actor, request_id, chunks()))
    assert env.make().complete(env.actor, request_id)["state"] == "completed"


def test_disconnect_persists_failure_and_same_request_can_restart(env):
    request_id = str(uuid4())
    env.service.declare(env.actor, request_id, **declaration())
    async def disconnected():
        yield DATA[:100]
        raise ClientDisconnect()
    with pytest.raises(ClientDisconnect):
        asyncio.run(env.service.receive_content(env.actor, request_id, disconnected()))
    view = env.make().query(env.actor, request_id)
    assert view["error"]["code"] == "transfer_interrupted" and facts(env) == (0, 0, 0)
    asyncio.run(env.make().receive_content(env.actor, request_id, chunks()))
    assert env.make().complete(env.actor, request_id)["state"] == "completed"


def test_dead_writer_recovers_but_unknown_writer_remains_protected(env, monkeypatch):
    request_id = str(uuid4())
    env.service.declare(env.actor, request_id, **declaration())
    with env.repository._connect() as conn:
        conn.execute("UPDATE mobile_transfers SET state='receiving',writer_json=?,temporary_name='orphan.part'",
            (json.dumps(env.service._writer()),))
    stage = env.root / ".mobile-transfers"
    stage.mkdir()
    (stage / "orphan.part").write_bytes(b"partial")
    monkeypatch.setattr(env.service, "_writer_alive", lambda marker: None)
    assert env.service.query(env.actor, request_id)["state"] == "receiving"
    with pytest.raises(MobileTransferError) as protected:
        asyncio.run(env.service.receive_content(env.actor, request_id, chunks()))
    assert protected.value.code == "transfer_busy" and (stage / "orphan.part").exists()
    monkeypatch.setattr(env.service, "_writer_alive", lambda marker: False)
    assert env.service.query(env.actor, request_id)["state"] == "failed"
    assert not (stage / "orphan.part").exists()
    asyncio.run(env.service.receive_content(env.actor, request_id, chunks()))
    assert env.service.complete(env.actor, request_id)["state"] == "completed"


def test_real_receiver_process_exit_recovers_original_request_from_zero(env):
    request_id = str(uuid4())
    env.service.declare(env.actor, request_id, **declaration())
    script = '''
import asyncio, os, sqlite3, sys
from pathlib import Path
from app.mobile_transfers import MobileTransfers, MobileTransferActor
database, root, request_id = sys.argv[1:]
def connect():
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    return conn
service = MobileTransfers(connect, assets=None, workspace_references=None,
    target_for=lambda workspace: Path(root), confirm_copy_in_transaction=None,
    min_free_disk_bytes=0)
async def interrupted():
    yield b"partial bytes"
    os._exit(77)
asyncio.run(service.receive_content(MobileTransferActor("workspace", "member", "user"), request_id, interrupted()))
'''
    result = subprocess.run([sys.executable, "-c", script, str(env.repository.database_path),
        str(env.root), request_id], timeout=15, capture_output=True, text=True)
    assert result.returncode == 77, result.stderr
    with env.repository._connect() as conn:
        assert conn.execute("SELECT state FROM mobile_transfers").fetchone()[0] == "receiving"
    view = env.make().query(env.actor, request_id)
    assert view["state"] == "failed" and view["error"]["code"] == "transfer_interrupted"
    assert not list((env.root / ".mobile-transfers").glob("*.part")) and facts(env) == (0, 0, 0)
    asyncio.run(env.make().receive_content(env.actor, request_id, chunks()))
    assert env.make().complete(env.actor, request_id)["state"] == "completed"


def test_cancelling_a_live_stream_fences_publication_and_cleans_only_temporary_file(env):
    request_id = str(uuid4())
    env.service.declare(env.actor, request_id, **declaration())
    unrelated = env.root / "keep.mp4"
    unrelated.write_bytes(b"user original")
    async def cancelled():
        yield DATA[:100]
        env.service.cancel(env.actor, request_id)
        yield DATA[100:]
    with pytest.raises(MobileTransferError) as error:
        asyncio.run(env.service.receive_content(env.actor, request_id, cancelled()))
    assert error.value.code == "cancelled"
    assert env.make().query(env.actor, request_id)["state"] == "cancelled"
    assert facts(env) == (0, 0, 0) and unrelated.read_bytes() == b"user original"
    assert not list((env.root / ".mobile-transfers").glob("*.part"))


def test_owner_write_failure_rolls_back_every_fact_and_reconciles_published_bytes(env, monkeypatch):
    request_id, _ = received(env)
    original = env.references.claim_asset_reference
    def fail_after_reference(conn, workspace_id, asset_id):
        original(conn, workspace_id, asset_id)
        raise OSError("injected owner transaction fault")
    monkeypatch.setattr(env.references, "claim_asset_reference", fail_after_reference)
    with pytest.raises(OSError, match="injected"):
        env.service.complete(env.actor, request_id)
    assert facts(env) == (0, 0, 0)
    assert env.make().query(env.actor, request_id)["state"] == "received"
    assert len(list(env.root.glob("discovery-*"))) == 1  # Complete bytes await reconciliation.
    assert env.repository.material_intake.scan_library("workspace") == []  # Unconfirmed publication cannot bypass completion.
    monkeypatch.setattr(env.references, "claim_asset_reference", original)
    assert env.make().complete(env.actor, request_id)["state"] == "completed"
    assert facts(env) == (1, 1, 1) and env.repository.material_source_files.pending_material_copies("workspace") == []


def test_changed_bytes_or_mount_never_register_or_fall_back(env):
    request_id, _ = received(env)
    part = next((env.root / ".mobile-transfers").glob("*.part"))
    part.write_bytes(b"x" * len(DATA))
    with pytest.raises(MobileTransferError) as changed:
        env.service.complete(env.actor, request_id)
    assert changed.value.code == "content_changed" and facts(env) == (0, 0, 0)
    env.root.rename(env.root.with_name("detached"))
    env.root.mkdir()
    with pytest.raises(MobileTransferError) as mount:
        env.service.complete(env.actor, request_id)
    assert mount.value.code in {"storage_changed", "storage_unavailable"} and facts(env) == (0, 0, 0)


def test_disk_shortage_fails_without_asset_or_partial_bytes(env, monkeypatch):
    request_id = str(uuid4())
    env.service.declare(env.actor, request_id, **declaration())
    monkeypatch.setattr("app.mobile_transfers.shutil.disk_usage", lambda path: SimpleNamespace(free=len(DATA) - 1))
    with pytest.raises(MobileTransferError) as error:
        asyncio.run(env.service.receive_content(env.actor, request_id, chunks()))
    assert error.value.code == "disk_space_insufficient"
    assert env.service.query(env.actor, request_id)["state"] == "failed" and facts(env) == (0, 0, 0)
    assert not list((env.root / ".mobile-transfers").glob("*.part"))


def test_copy_confirmation_refusal_rolls_back_and_reports_repairable_error(env, monkeypatch):
    request_id, _ = received(env)
    def refused(*args, **kwargs):
        raise ValueError("binding changed")
    monkeypatch.setattr(env.service, "confirm_copy", refused)
    with pytest.raises(MobileTransferError) as error:
        env.service.complete(env.actor, request_id)
    assert error.value.code == "copy_confirmation_refused" and error.value.retryable
    assert facts(env) == (0, 0, 0) and env.service.query(env.actor, request_id)["state"] == "received"


def test_equal_bytes_from_different_uploaders_remain_private_separate_assets(env):
    request_id, _ = received(env)
    first = env.service.complete(env.actor, request_id)
    env.actor = MobileTransferActor("workspace", "other-member", "other-user")
    request_id, _ = received(env)
    second = env.service.complete(env.actor, request_id)
    assert first["asset_id"] != second["asset_id"] and facts(env) == (2, 2, 2)
    asset = env.assets.get_asset("workspace", second["asset_id"])
    assert (asset.owner_user_id, asset.visibility) == ("other-user", "private")


def test_http_stream_protocol_and_fresh_formal_identity_on_every_action(env):
    app = FastAPI()
    checks, authorized = [], [True]
    def actor_for(request):
        checks.append(request.method)
        if not authorized[0]:
            raise HTTPException(status_code=403, detail="membership revoked")
        return env.actor
    app.include_router(build_mobile_transfer_router(transfers_for=lambda: env.service, actor_for=actor_for))
    request_id = str(uuid4())
    url = f"/api/mobile-transfers/{request_id}"
    with TestClient(app) as client:
        assert client.put(url, json={**declaration(), "owner_user_id": "spoofed"}).status_code == 422
        assert client.put(url, json=declaration()).json()["state"] == "declared"
        assert client.put(url + "/content", content=DATA[:-1]).status_code == 422
        assert client.put(url + "/content", content=DATA).json()["state"] == "received"
        authorized[0] = False
        assert client.post(url + "/complete").status_code == 403 and facts(env) == (0, 0, 0)
        authorized[0] = True
        completed = client.post(url + "/complete").json()
        assert completed["state"] == "completed" and completed["error"] is None
        assert client.get(url).json() == completed
        cancelled = client.post(url + "/cancel")
        assert cancelled.status_code == 409 and cancelled.json()["detail"]["code"] == "already_completed"
    assert checks == ["PUT", "PUT", "PUT", "POST", "POST", "GET", "POST"]


@pytest.mark.parametrize("method,suffix", [("GET", ""), ("PUT", "/content")])
def test_http_persistence_failure_is_explicit_and_never_success(env, monkeypatch, method, suffix):
    app = FastAPI()
    app.include_router(build_mobile_transfer_router(transfers_for=lambda: env.service, actor_for=lambda request: env.actor))
    def unavailable():
        raise sqlite3.OperationalError("injected unavailable database")
    monkeypatch.setattr(env.service, "_connect", unavailable)
    with TestClient(app) as client:
        response = client.request(method, f"/api/mobile-transfers/{uuid4()}{suffix}", content=b"x" if suffix else None)
    assert response.status_code == 503 and response.json()["detail"]["code"] == "persistence_unavailable"
