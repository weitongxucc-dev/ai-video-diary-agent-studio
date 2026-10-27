"""Scheduled hash observations and permit checkpoints; no file content I/O."""
from __future__ import annotations

import re
import sqlite3
from app.device_scheduler import Grant
from app.material_hash import MaterialHashResult
from app.models import utc_now


class MaterialHashRecordStore:
    def __init__(self, connection_factory):
        self._connect = connection_factory

    def require_observation_schema(self):
        with self._connect() as conn:
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(material_ingestion_files)")}
        if "provisional_revision" not in columns:
            raise RuntimeError("素材调度检查点迁移尚未就绪")

    def binding(self, connection):
        return connection.execute(
            "SELECT workspace_id, library_path FROM material_ingestion_binding WHERE id=1"
        ).fetchone()

    def asset_source(self, connection, workspace_id, asset_id):
        return connection.execute(
            "SELECT file_path FROM material_assets WHERE workspace_id=? AND id=?",
            (workspace_id, asset_id),
        ).fetchone()

    def observation(self, connection, workspace_id, file_path):
        return connection.execute(
            "SELECT provisional_revision, state, attempted_digest FROM material_ingestion_files "
            "WHERE workspace_id=? AND file_path=?", (workspace_id, file_path),
        ).fetchone()

    def request_identity(self, connection, request_id):
        return connection.execute(
            "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, attempt_number "
            "FROM device_work_requests WHERE id=?", (request_id,),
        ).fetchone()

    def observe_revision(self, workspace_id, resolved, stat, candidate, now, stable_seconds):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM material_ingestion_files WHERE workspace_id=? AND file_path=?",
                (workspace_id, str(resolved)),
            ).fetchone()
            changed = row is None or row["provisional_revision"] != candidate.provisional_revision
            if changed:
                conn.execute(
                    """INSERT INTO material_ingestion_files
                       (workspace_id, file_path, size_bytes, mtime_ns, ctime_ns, inode,
                        observed_at, state, provisional_revision)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'observing', ?)
                       ON CONFLICT(workspace_id, file_path) DO UPDATE SET
                         size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns,
                         ctime_ns=excluded.ctime_ns, inode=excluded.inode,
                         observed_at=excluded.observed_at, state='observing',
                         provisional_revision=excluded.provisional_revision,
                         attempted_digest=NULL""",
                    (workspace_id, str(resolved), stat.st_size, stat.st_mtime_ns,
                     stat.st_ctime_ns, stat.st_ino, now, candidate.provisional_revision),
                )
            elif row["state"] in {"ready", "failed"} or row["attempted_digest"]:
                return None
            elif now - float(row["observed_at"]) >= stable_seconds:
                conn.execute(
                    "UPDATE material_ingestion_files SET state='waiting' "
                    "WHERE workspace_id=? AND file_path=? AND provisional_revision=?",
                    (workspace_id, str(resolved), candidate.provisional_revision),
                )
                return False, True
            return changed, False

    def commit_material_hash(
        self, conn: sqlite3.Connection, grant: Grant, result: MaterialHashResult, *, source_verdict,
    ) -> str:
        """Write the hash fact on the scheduler's permit transaction connection."""
        candidate = result.candidate
        if not re.fullmatch(r"[0-9a-f]{64}", result.source_sha256):
            raise ValueError("素材哈希结果无效")
        request = conn.execute(
            "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, "
            "attempt_number, state FROM device_work_requests WHERE id=?",
            (grant.request_id,),
        ).fetchone()
        if (request is None or request["owner_type"] != "material_asset"
                or (request["owner_id"], request["workspace_id"], request["input_digest"],
                    request["unit_key"], request["attempt_number"])
                != (candidate.asset_id, grant.workspace_id, grant.input_digest,
                    grant.unit_key, grant.attempt_number)
                or request["state"] not in {"granted", "yield_requested"}
                or (candidate.workspace_id, candidate.provisional_revision, candidate.unit_key)
                != (grant.workspace_id, grant.input_digest, grant.unit_key)):
            raise PermissionError("素材哈希检查点不属于当前许可尝试")
        if source_verdict(
            candidate.workspace_id, candidate.asset_id, candidate.provisional_revision,
            candidate.unit_key, conn,
        ) != "ready":
            raise ValueError("素材来源已变化，未保存过期哈希")
        checkpoint = conn.execute(
            """SELECT stage_input_digest, result_ref FROM material_analysis_checkpoints
               WHERE workspace_id=? AND asset_id=? AND provisional_revision=?
                 AND source_sha256=? AND unit_key=?""",
            (candidate.workspace_id, candidate.asset_id, candidate.provisional_revision,
             result.source_sha256, candidate.unit_key),
        ).fetchone()
        result_ref = f"sha256:{result.source_sha256}"
        if checkpoint is not None and (
            checkpoint["stage_input_digest"] != candidate.stage_input_digest
            or checkpoint["result_ref"] != result_ref
        ):
            raise ValueError("既有哈希检查点结果不匹配")
        updated = conn.execute(
            """UPDATE material_ingestion_files
               SET attempted_digest=?, state='waiting'
               WHERE workspace_id=? AND file_path=? AND provisional_revision=?""",
            (result.source_sha256, candidate.workspace_id, candidate.file_path,
             candidate.provisional_revision),
        )
        if updated.rowcount != 1:
            raise ValueError("素材观察已失效，未写入哈希检查点")
        if checkpoint is None:
            conn.execute(
                """INSERT INTO material_analysis_checkpoints
                   (workspace_id, asset_id, source_sha256, provisional_revision,
                    unit_key, stage_input_digest, result_ref, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate.workspace_id, candidate.asset_id, result.source_sha256,
                 candidate.provisional_revision, candidate.unit_key,
                 candidate.stage_input_digest, result_ref, utc_now().isoformat()),
            )
        return f"material_hash:{candidate.asset_id}:{candidate.provisional_revision}"
