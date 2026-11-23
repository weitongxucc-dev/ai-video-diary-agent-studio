"""Material-root rebind is an explicit, content-checked metadata transaction."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from app.materials.contracts import MaterialAsset
from app.database_migrations import apply_production_migrations
from app.models import utc_now
from app.persistence_json import _json_payload
from app.production import ProductionRepository


def _fixture(tmp_path: Path):
    old = tmp_path / "old-library"
    new = tmp_path / "new-library"
    old.mkdir()
    new.mkdir()
    source = old / "nested" / "clip.mp4"
    target = new / "nested" / "clip.mp4"
    source.parent.mkdir()
    target.parent.mkdir()
    source.write_bytes(b"same source bytes")
    target.write_bytes(source.read_bytes())
    repository = ProductionRepository(tmp_path / "agent.sqlite3", old)
    now = utc_now()
    asset = MaterialAsset(
        id="asset-rebind",
        workspace_id="workspace-rebind",
        file_name=source.name,
        path=str(source.resolve()),
        size_bytes=source.stat().st_size,
        modified_at=datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc),
        duration_seconds=1.0,
        created_at=now,
        updated_at=now,
    )
    with repository._connect() as connection:
        connection.execute(
            "INSERT INTO material_ingestion_binding(id,workspace_id,library_path) VALUES(1,?,?)",
            (asset.workspace_id, str(old.resolve())),
        )
        connection.execute(
            "INSERT INTO material_assets(id,workspace_id,file_path,payload,updated_at) VALUES(?,?,?,?,?)",
            (asset.id, asset.workspace_id, asset.path, _json_payload(asset), asset.updated_at.isoformat()),
        )
        stat = source.stat()
        connection.execute(
            """INSERT INTO material_ingestion_files
               (workspace_id,file_path,size_bytes,mtime_ns,ctime_ns,inode,observed_at,state,confirmed_revision)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (asset.workspace_id, asset.path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
             stat.st_ino, 0.0, "ready", json.dumps(repository.material_source_files.material_copy_revision(stat), sort_keys=True)),
        )
        connection.execute(
            "INSERT INTO creation_plans(id,workspace_id,payload,updated_at) VALUES(?,?,?,?)",
            ("plan-rebind", asset.workspace_id, json.dumps({
                "source_media": {
                    "asset_id": asset.id,
                    "file_signature": repository.material_source_files.material_copy_revision(stat),
                },
            }), now.isoformat()),
        )
    return repository, old, new, source, target, asset.workspace_id


def test_material_rebind_prepare_commit_updates_registered_paths_and_is_idempotent(tmp_path):
    repository, old, new, source, target, workspace_id = _fixture(tmp_path)

    prepared = repository.material_source_files.prepare_material_rebind(
        workspace_id, old_directory=str(old), new_directory=str(new), operation_id="move-1",
    )
    assert prepared["status"] == "prepared"
    assert prepared["entries"][0]["relative_path"] == "nested/clip.mp4"
    result = repository.material_source_files.commit_material_rebind(
        workspace_id, operation_id="move-1", manifest_sha256=prepared["manifest_sha256"],
    )
    assert result["status"] == "applied"
    assert result["entry_count"] == 1

    with repository._connect() as connection:
        binding = connection.execute("SELECT library_path FROM material_ingestion_binding WHERE id=1").fetchone()
        asset = connection.execute("SELECT file_path,payload FROM material_assets WHERE id='asset-rebind'").fetchone()
        intake = connection.execute("SELECT file_path,mtime_ns,inode FROM material_ingestion_files WHERE workspace_id=?", (workspace_id,)).fetchone()
        plan = connection.execute("SELECT payload FROM creation_plans WHERE id='plan-rebind'").fetchone()
    assert binding["library_path"] == str(new.resolve())
    assert asset["file_path"] == str(target.resolve())
    assert json.loads(asset["payload"])["path"] == str(target.resolve())
    assert intake["file_path"] == str(target.resolve())
    assert intake["mtime_ns"] == target.stat().st_mtime_ns
    assert json.loads(plan["payload"])["source_media"]["file_signature"] == repository.material_source_files.material_copy_revision(target.stat())
    assert repository.material_source_files.commit_material_rebind(
        workspace_id, operation_id="move-1", manifest_sha256=prepared["manifest_sha256"],
    )["status"] == "applied"


def test_material_rebind_receipts_are_installed_by_versioned_migration(tmp_path):
    repository, _old, _new, _source, _target, _workspace_id = _fixture(tmp_path)
    with repository._connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM production_schema_migrations WHERE version=30"
        ).fetchone() is not None
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='material_rebind_receipts'"
        ).fetchone() is not None
        connection.execute("DROP TABLE material_rebind_receipts")
        connection.execute("DELETE FROM production_schema_migrations WHERE version=30")
        apply_production_migrations(connection)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='material_rebind_receipts'"
        ).fetchone() is not None


def test_material_rebind_rejects_changed_target_without_touching_binding(tmp_path):
    repository, old, new, source, target, workspace_id = _fixture(tmp_path)
    prepared = repository.material_source_files.prepare_material_rebind(
        workspace_id, old_directory=str(old), new_directory=str(new), operation_id="move-2",
    )
    target.write_bytes(b"different bytes")
    with pytest.raises(ValueError, match="提交前已变化|摘要不一致"):
        repository.material_source_files.commit_material_rebind(
            workspace_id, operation_id="move-2", manifest_sha256=prepared["manifest_sha256"],
        )
    with repository._connect() as connection:
        assert connection.execute("SELECT library_path FROM material_ingestion_binding WHERE id=1").fetchone()[0] == str(old.resolve())
        assert connection.execute("SELECT file_path FROM material_assets WHERE id='asset-rebind'").fetchone()[0] == str(source.resolve())


def test_material_rebind_rejects_missing_file_and_foreign_workspace(tmp_path):
    repository, old, new, source, target, workspace_id = _fixture(tmp_path)
    target.unlink()
    with pytest.raises(ValueError, match="新素材文件缺失"):
        repository.material_source_files.prepare_material_rebind(
            workspace_id, old_directory=str(old), new_directory=str(new), operation_id="move-3",
        )
    target.write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match="未绑定到当前工作区"):
        repository.material_source_files.prepare_material_rebind(
            "foreign-workspace", old_directory=str(old), new_directory=str(new), operation_id="move-4",
        )
