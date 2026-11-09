"""Material probe checkpoint facts, committed on the scheduler's original connection."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Literal

from app.device_scheduler import Grant
from app.material_probe import (
    MaterialProbeCandidate, MaterialProbeDependency, MaterialProbeFacts,
    material_probe_unit_key, probe_checkpoint_needs_rate_refresh,
)
from app.models import utc_now


class MaterialProbeRecordStore:
    def __init__(self, connection_factory, *, library_root, library_available,
                 read_binding, hash_candidate_from_stat, read_asset_source,
                 apply_probe_metadata, mark_probe_failed):
        self._connect = connection_factory
        self._library_root = library_root
        self._library_available = library_available
        self._read_binding = read_binding
        self._hash_candidate_from_stat = hash_candidate_from_stat
        self._read_asset_source = read_asset_source
        self._apply_probe_metadata = apply_probe_metadata
        self._mark_probe_failed = mark_probe_failed

    def pending_material_probe_dependencies(self) -> list[MaterialProbeDependency]:
        """Expose durable hash outputs for a future permitted probe submitter."""
        binding = self._read_binding()
        if binding is None or not self._library_available():
            return []
        workspace_id, bound_path = binding
        if str(self._library_root().resolve()) != bound_path:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT f.provisional_revision, f.attempted_digest, a.id AS asset_id
                   FROM material_ingestion_files f
                   JOIN material_assets a ON a.workspace_id=f.workspace_id
                                         AND a.file_path=f.file_path
                   WHERE f.workspace_id=? AND f.state='waiting'
                     AND f.attempted_digest IS NOT NULL""",
                (workspace_id,),
            ).fetchall()
            dependencies: list[MaterialProbeDependency] = []
            for row in rows:
                source_sha256 = str(row["attempted_digest"])
                try:
                    unit_key = material_probe_unit_key(source_sha256)
                except ValueError:
                    continue
                if self.material_probe_source_verdict(
                    workspace_id, str(row["asset_id"]), source_sha256, unit_key, conn,
                ) != "ready":
                    continue
                dependencies.append(MaterialProbeDependency(
                    workspace_id, str(row["asset_id"]), source_sha256,
                    str(row["provisional_revision"]),
                ))
        return dependencies

    def material_probe_key_for_dependency(self, dependency: MaterialProbeDependency) -> str:
        with self._connect() as conn:
            return self._preferred_material_probe_key(
                conn, dependency.workspace_id, dependency.asset_id,
                dependency.source_sha256, dependency.provisional_revision,
            )

    def _preferred_material_probe_key(
        self, conn: sqlite3.Connection, workspace_id: str, asset_id: str,
        source_sha256: str, provisional_revision: str,
    ) -> str:
        v1_key = material_probe_unit_key(source_sha256)
        row = conn.execute(
            """SELECT stage_input_digest, result_json, result_ref
               FROM material_analysis_checkpoints WHERE workspace_id=? AND asset_id=?
                 AND source_sha256=? AND provisional_revision=? AND unit_key=?""",
            (workspace_id, asset_id, source_sha256, provisional_revision, v1_key),
        ).fetchone()
        if row is None or not isinstance(row["result_json"], str):
            return v1_key
        result_json = row["result_json"]
        if (row["stage_input_digest"] != v1_key.rsplit(":", 1)[-1]
                or row["result_ref"] != "sha256:" + hashlib.sha256(result_json.encode()).hexdigest()):
            return v1_key
        try:
            legacy = probe_checkpoint_needs_rate_refresh(result_json)
        except ValueError:
            return v1_key
        return material_probe_unit_key(source_sha256, version=2) if legacy else v1_key

    def legacy_probe_request_for_replacement(
        self, dependency: MaterialProbeDependency,
    ) -> str | None:
        """Locate the old scene-named request without changing scheduler queue state."""
        with self._connect() as conn:
            current = conn.execute(
                """SELECT id FROM device_work_requests
                   WHERE workspace_id=? AND owner_type='material_asset' AND owner_id=?
                     AND input_digest=? AND unit_key=?""",
                (dependency.workspace_id, dependency.asset_id,
                 dependency.source_sha256, material_probe_unit_key(dependency.source_sha256)),
            ).fetchone()
            legacy = conn.execute(
                """SELECT id, state, wait_reason FROM device_work_requests
                   WHERE workspace_id=? AND owner_type='material_asset' AND owner_id=?
                     AND input_digest=? AND unit_key LIKE 'probe-scene:v1:%'""",
                (dependency.workspace_id, dependency.asset_id, dependency.source_sha256),
            ).fetchall()
        if len(legacy) > 1:
            raise ValueError("存在多个旧媒体探测请求，拒绝自动替换")
        if not legacy:
            return None
        old = legacy[0]
        if (old["state"] == "cancelled" and current is not None
                and old["wait_reason"] == f"replaced_by:{current['id']}"):
            return None
        if old["state"] not in {"waiting", "blocked"}:
            raise ValueError("旧素材探测请求已执行或终止，拒绝自动替换")
        return str(old["id"])

    def material_probe_source_verdict(
        self, workspace_id: str, asset_id: str, source_sha256: str,
        unit_key: str, conn: sqlite3.Connection,
    ) -> Literal["ready", "dependency", "stale", "unauthorized", "unknown"]:
        """Check the committed hash fact; no probe execution is authorized here."""
        binding = conn.execute(
            "SELECT workspace_id, library_path FROM material_ingestion_binding WHERE id=1"
        ).fetchone()
        if binding is None or binding["workspace_id"] != workspace_id:
            return "unauthorized"
        if not self._library_available():
            return "dependency"
        bound_path = str(self._library_root().resolve())
        if binding["library_path"] != bound_path:
            return "stale"
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            return "stale"
        if unit_key not in {material_probe_unit_key(source_sha256),
                            material_probe_unit_key(source_sha256, version=2)}:
            return "stale"
        asset = conn.execute(
            "SELECT file_path FROM material_assets WHERE workspace_id=? AND id=?",
            (workspace_id, asset_id),
        ).fetchone()
        if asset is None:
            return "stale"
        path = Path(str(asset["file_path"]))
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(Path(bound_path)):
                return "stale"
        except OSError:
            return "unknown"
        observation = conn.execute(
            "SELECT provisional_revision, state, attempted_digest FROM material_ingestion_files "
            "WHERE workspace_id=? AND file_path=?",
            (workspace_id, str(path)),
        ).fetchone()
        if (observation is None or observation["state"] != "waiting"
                or observation["attempted_digest"] != source_sha256):
            return "stale"
        try:
            stat = path.stat()
        except FileNotFoundError:
            return "dependency"
        except OSError:
            return "unknown"
        candidate = self._hash_candidate_from_stat(
            workspace_id, bound_path, path, stat, asset_id,
        )
        if candidate.provisional_revision != observation["provisional_revision"]:
            return "stale"
        checkpoint = conn.execute(
            """SELECT stage_input_digest, result_ref
               FROM material_analysis_checkpoints
               WHERE workspace_id=? AND asset_id=? AND provisional_revision=?
                 AND source_sha256=? AND unit_key=?""",
            (workspace_id, asset_id, candidate.provisional_revision,
             source_sha256, candidate.unit_key),
        ).fetchone()
        if (checkpoint is None
                or checkpoint["stage_input_digest"] != candidate.stage_input_digest
                or checkpoint["result_ref"] != f"sha256:{source_sha256}"):
            return "stale"
        return "ready"

    def material_probe_candidate_for_grant(self, grant: Grant) -> MaterialProbeCandidate:
        with self._connect() as conn:
            request = conn.execute(
                "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, attempt_number "
                "FROM device_work_requests WHERE id=?", (grant.request_id,),
            ).fetchone()
            if (request is None or request["owner_type"] != "material_asset"
                    or (request["workspace_id"], request["input_digest"], request["unit_key"],
                        request["attempt_number"])
                    != (grant.workspace_id, grant.input_digest, grant.unit_key, grant.attempt_number)):
                raise PermissionError("素材媒体探测许可与执行对象不匹配")
            asset_id = str(request["owner_id"])
            verdict = self.material_probe_source_verdict(
                grant.workspace_id, asset_id, grant.input_digest, grant.unit_key, conn,
            )
            if verdict != "ready":
                raise ValueError(f"素材媒体探测输入不可执行：{verdict}")
            asset = conn.execute(
                "SELECT file_path FROM material_assets WHERE workspace_id=? AND id=?",
                (grant.workspace_id, asset_id),
            ).fetchone()
            observation = conn.execute(
                "SELECT provisional_revision FROM material_ingestion_files "
                "WHERE workspace_id=? AND file_path=?",
                (grant.workspace_id, asset["file_path"]),
            ).fetchone()
        return MaterialProbeCandidate(
            grant.workspace_id, asset_id, str(asset["file_path"]), grant.input_digest,
            str(observation["provisional_revision"]), grant.unit_key,
        )

    def material_probe_verdict_for_grant(
        self, grant: Grant,
    ) -> Literal["ready", "dependency", "stale", "unauthorized", "unknown"]:
        with self._connect() as conn:
            request = conn.execute(
                "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, attempt_number "
                "FROM device_work_requests WHERE id=?", (grant.request_id,),
            ).fetchone()
            if (request is None or request["owner_type"] != "material_asset"
                    or (request["workspace_id"], request["input_digest"], request["unit_key"],
                        request["attempt_number"])
                    != (grant.workspace_id, grant.input_digest, grant.unit_key, grant.attempt_number)):
                return "unauthorized"
            return self.material_probe_source_verdict(
                grant.workspace_id, str(request["owner_id"]), grant.input_digest,
                grant.unit_key, conn,
            )

    def commit_material_probe(
        self, conn: sqlite3.Connection, grant: Grant,
        candidate: MaterialProbeCandidate, facts: MaterialProbeFacts,
    ) -> str:
        """Commit typed ffprobe facts and duration on the permit's SQLite transaction."""
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
                or (candidate.workspace_id, candidate.source_sha256, candidate.unit_key)
                != (grant.workspace_id, grant.input_digest, grant.unit_key)):
            raise PermissionError("素材媒体检查点不属于当前许可尝试")
        if self.material_probe_source_verdict(
            candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
            candidate.unit_key, conn,
        ) != "ready":
            raise ValueError("素材来源已变化，未保存过期媒体探测结果")
        if candidate.unit_key != self._preferred_material_probe_key(
            conn, candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
            candidate.provisional_revision,
        ):
            raise ValueError("素材媒体探测契约已更新，未保存旧检查点")
        asset_row = self._read_asset_source(
            conn, candidate.workspace_id, candidate.asset_id,
        )
        observation = conn.execute(
            "SELECT provisional_revision FROM material_ingestion_files "
            "WHERE workspace_id=? AND file_path=?",
            (candidate.workspace_id, candidate.file_path),
        ).fetchone()
        if (asset_row is None or asset_row["file_path"] != candidate.file_path
                or observation is None
                or observation["provisional_revision"] != candidate.provisional_revision):
            raise ValueError("素材媒体探测来源修订已变化")
        stage_input_digest = candidate.unit_key.rsplit(":", 1)[-1]
        result_json = facts.checkpoint_json()
        result_ref = "sha256:" + hashlib.sha256(result_json.encode("utf-8")).hexdigest()
        existing = conn.execute(
            """SELECT stage_input_digest, result_json, result_ref FROM material_analysis_checkpoints
               WHERE workspace_id=? AND asset_id=? AND provisional_revision=?
                 AND source_sha256=? AND unit_key=?""",
            (candidate.workspace_id, candidate.asset_id, candidate.provisional_revision,
             candidate.source_sha256, candidate.unit_key),
        ).fetchone()
        if existing is not None and (
            existing["stage_input_digest"] != stage_input_digest
            or existing["result_json"] != result_json
            or existing["result_ref"] != result_ref
        ):
            raise ValueError("既有媒体探测检查点结果不匹配")
        self._apply_probe_metadata(
            conn, candidate.workspace_id, candidate.asset_id,
            str(asset_row["payload"]), facts.duration_seconds,
        )
        if existing is None:
            conn.execute(
                """INSERT INTO material_analysis_checkpoints
                   (workspace_id, asset_id, source_sha256, provisional_revision,
                    unit_key, stage_input_digest, result_json, result_ref, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
                 candidate.provisional_revision, candidate.unit_key,
                 stage_input_digest, result_json, result_ref, utc_now().isoformat()),
            )
        return f"material_probe_meta:{candidate.asset_id}:{candidate.provisional_revision}"

    def fail_material_probe(
        self, conn: sqlite3.Connection, grant: Grant,
        candidate: MaterialProbeCandidate, reason: Literal[
            "probe_invalid_media", "probe_timeout", "probe_output_limit", "probe_failed",
        ],
    ) -> None:
        """Record a terminal metadata failure with the scheduler's failed request."""
        if self.material_probe_source_verdict(
            candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
            candidate.unit_key, conn,
        ) != "ready":
            raise ValueError("素材来源已变化，不能把旧媒体探测错误写给新素材")
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
                or request["state"] not in {"granted", "yield_requested"}):
            raise PermissionError("素材媒体失败不属于当前许可尝试")
        row = self._read_asset_source(
            conn, candidate.workspace_id, candidate.asset_id,
        )
        if row is None or row["file_path"] != candidate.file_path:
            raise ValueError("素材媒体失败来源已变化")
        self._mark_probe_failed(
            conn, candidate.workspace_id, candidate.asset_id,
            str(row["payload"]), reason,
        )
        updated = conn.execute(
            """UPDATE material_ingestion_files SET state='failed'
               WHERE workspace_id=? AND file_path=? AND provisional_revision=?
                 AND attempted_digest=?""",
            (candidate.workspace_id, candidate.file_path, candidate.provisional_revision,
             candidate.source_sha256),
        )
        if updated.rowcount != 1:
            raise ValueError("素材观察已变化，未记录旧媒体探测失败")
