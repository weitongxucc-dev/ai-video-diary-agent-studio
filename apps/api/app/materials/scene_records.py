"""Scene window checkpoints and final range assembly on the original permit transaction."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3

from app.device_scheduler import Grant
from app.material_probe import MaterialProbeDependency, MaterialProbeFacts, material_probe_unit_key
from app.material_scene import (
    MaterialSceneCandidate, SceneWindow, SceneWindowFacts,
    SCENE_LOGICAL_WINDOW_SECONDS, scene_unit_key, scene_window_for_key, scene_windows,
)
from app.models import utc_now


class MaterialSceneRecordStore:
    def __init__(self, connection_factory, *, library_root, library_available, read_binding,
                 preferred_probe_key, probe_source_verdict, decode_asset, read_asset_payload,
                 assemble_scene_segments, mark_scene_failed):
        self._connect = connection_factory
        self._library_root = library_root
        self._library_available = library_available
        self._read_binding = read_binding
        self._preferred_probe_key = preferred_probe_key
        self._probe_source_verdict = probe_source_verdict
        self._decode_asset = decode_asset
        self._read_asset_payload = read_asset_payload
        self._assemble_scene_segments = assemble_scene_segments
        self._mark_scene_failed = mark_scene_failed

    def pending_material_scene_dependencies(self) -> list[MaterialProbeDependency]:
        """Expose verified metadata checkpoints needing a future scene contract."""
        binding = self._read_binding()
        if binding is None or not self._library_available():
            return []
        workspace_id, bound_path = binding
        if str(self._library_root().resolve()) != bound_path:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT c.asset_id, c.source_sha256, c.provisional_revision,
                          c.unit_key, c.stage_input_digest, c.result_json, c.result_ref,
                          a.payload
                   FROM material_analysis_checkpoints c
                   JOIN material_assets a ON a.workspace_id=c.workspace_id AND a.id=c.asset_id
                   WHERE c.workspace_id=? AND c.unit_key LIKE 'probe-meta:v%:%'""",
                (workspace_id,),
            ).fetchall()
            pending: list[MaterialProbeDependency] = []
            for row in rows:
                source_sha256 = str(row["source_sha256"])
                unit_key = str(row["unit_key"])
                if (unit_key != self._preferred_probe_key(
                            conn, workspace_id, str(row["asset_id"]), source_sha256,
                            str(row["provisional_revision"]),
                        )
                        or row["stage_input_digest"] != unit_key.rsplit(":", 1)[-1]
                        or self._probe_source_verdict(
                            workspace_id, str(row["asset_id"]), source_sha256,
                            unit_key, conn,
                        ) != "ready"):
                    continue
                result_json = row["result_json"]
                if (not isinstance(result_json, str)
                        or row["result_ref"] != "sha256:" + hashlib.sha256(
                            result_json.encode("utf-8")
                        ).hexdigest()):
                    continue
                try:
                    facts = MaterialProbeFacts.from_checkpoint_json(result_json)
                    asset = self._decode_asset(json.loads(str(row["payload"])))
                except (ValueError, TypeError, KeyError):
                    continue
                if asset.duration_seconds != facts.duration_seconds or asset.analysis_status != "blocked":
                    continue
                pending.append(MaterialProbeDependency(
                    workspace_id, str(row["asset_id"]), source_sha256,
                    str(row["provisional_revision"]),
                ))
        return pending

    def _material_scene_source(
        self, conn: sqlite3.Connection, workspace_id: str, asset_id: str,
        source_sha256: str,
    ) -> tuple[sqlite3.Row, MaterialProbeFacts, str] | None:
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            return None
        if self._probe_source_verdict(
            workspace_id, asset_id, source_sha256,
            material_probe_unit_key(source_sha256), conn,
        ) != "ready":
            return None
        rows = conn.execute(
            """SELECT c.provisional_revision, c.unit_key, c.stage_input_digest,
                      c.result_json, c.result_ref,
                      a.file_path, a.payload, f.size_bytes
               FROM material_analysis_checkpoints c
               JOIN material_assets a ON a.workspace_id=c.workspace_id AND a.id=c.asset_id
               JOIN material_ingestion_files f ON f.workspace_id=a.workspace_id
                    AND f.file_path=a.file_path
               WHERE c.workspace_id=? AND c.asset_id=? AND c.source_sha256=?
                 AND c.unit_key IN (?, ?) AND c.provisional_revision=f.provisional_revision""",
            (workspace_id, asset_id, source_sha256,
             material_probe_unit_key(source_sha256),
             material_probe_unit_key(source_sha256, version=2)),
        ).fetchall()
        if not rows:
            return None
        probe_key = self._preferred_probe_key(
            conn, workspace_id, asset_id, source_sha256,
            str(rows[0]["provisional_revision"]),
        )
        row = next((item for item in rows if item["unit_key"] == probe_key), None)
        if (row is None or row["stage_input_digest"] != probe_key.rsplit(":", 1)[-1]
                or not isinstance(row["result_json"], str)):
            return None
        result_ref = "sha256:" + hashlib.sha256(row["result_json"].encode()).hexdigest()
        if row["result_ref"] != result_ref:
            return None
        try:
            facts = MaterialProbeFacts.from_checkpoint_json(row["result_json"])
            asset = self._decode_asset(json.loads(str(row["payload"])))
        except (ValueError, TypeError, KeyError):
            return None
        if asset.duration_seconds != facts.duration_seconds or asset.analysis_status != "blocked":
            return None
        return row, facts, result_ref

    def material_scene_candidate(
        self, workspace_id: str, asset_id: str, source_sha256: str,
        unit_key: str, conn: sqlite3.Connection,
    ) -> MaterialSceneCandidate | None:
        source = self._material_scene_source(conn, workspace_id, asset_id, source_sha256)
        if source is None:
            return None
        row, facts, result_ref = source
        video = next(stream for stream in facts.streams if stream.kind == "video")
        window = scene_window_for_key(
            facts.duration_seconds, source_sha256, result_ref, video.index, unit_key,
        )
        if window is None:
            return None
        rate = (video.avg_frame_rate_fps
                if video.avg_frame_rate_fps is not None
                and video.avg_frame_rate_fps == video.r_frame_rate_fps else None)
        return MaterialSceneCandidate(
            workspace_id, asset_id, str(row["file_path"]), source_sha256,
            str(row["provisional_revision"]), result_ref, video.index, video.codec,
            int(video.width), int(video.height), rate, facts.duration_seconds,
            int(row["size_bytes"]), window, unit_key,
        )

    def pending_material_scene_windows(self) -> list[MaterialSceneCandidate]:
        """Replay missing windows from the metadata fact; completed windows stay durable."""
        pending: list[MaterialSceneCandidate] = []
        for dependency in self.pending_material_scene_dependencies():
            with self._connect() as conn:
                source = self._material_scene_source(
                    conn, dependency.workspace_id, dependency.asset_id,
                    dependency.source_sha256,
                )
                if source is None:
                    continue
                _, facts, result_ref = source
                video = next(stream for stream in facts.streams if stream.kind == "video")
                for window in scene_windows(facts.duration_seconds):
                    unit_key = scene_unit_key(
                        dependency.source_sha256, result_ref, video.index, window,
                    )
                    checkpoint = conn.execute(
                        """SELECT stage_input_digest, result_json, result_ref
                           FROM material_analysis_checkpoints WHERE workspace_id=?
                             AND asset_id=? AND source_sha256=? AND provisional_revision=?
                             AND unit_key=?""",
                        (dependency.workspace_id, dependency.asset_id,
                         dependency.source_sha256, dependency.provisional_revision, unit_key),
                    ).fetchone()
                    if checkpoint is not None and self._valid_scene_checkpoint(checkpoint, unit_key, window):
                        continue
                    candidate = self.material_scene_candidate(
                        dependency.workspace_id, dependency.asset_id,
                        dependency.source_sha256, unit_key, conn,
                    )
                    if candidate is not None:
                        pending.append(candidate)
                    if len(pending) >= 8:
                        return pending
        return pending

    @staticmethod
    def _valid_scene_checkpoint(row: sqlite3.Row, unit_key: str, window: SceneWindow) -> bool:
        result_json = row["result_json"]
        if (row["stage_input_digest"] != unit_key.rsplit(":", 1)[-1]
                or not isinstance(result_json, str)
                or row["result_ref"] != "sha256:" + hashlib.sha256(result_json.encode()).hexdigest()):
            return False
        try:
            return SceneWindowFacts.from_checkpoint_json(result_json).window == window
        except ValueError:
            return False

    def material_scene_verdict_for_grant(self, grant: Grant) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, "
                "attempt_number FROM device_work_requests WHERE id=?", (grant.request_id,),
            ).fetchone()
            if (row is None or row["owner_type"] != "material_asset"
                    or (row["workspace_id"], row["input_digest"], row["unit_key"],
                        row["attempt_number"])
                    != (grant.workspace_id, grant.input_digest, grant.unit_key,
                        grant.attempt_number)):
                return "unauthorized"
            if not re.fullmatch(r"[0-9a-f]{64}", grant.input_digest):
                return "stale"
            source_verdict = self._probe_source_verdict(
                grant.workspace_id, str(row["owner_id"]), grant.input_digest,
                material_probe_unit_key(grant.input_digest), conn,
            )
            if source_verdict != "ready":
                return source_verdict
            return ("ready" if self.material_scene_candidate(
                grant.workspace_id, str(row["owner_id"]), grant.input_digest,
                grant.unit_key, conn,
            ) is not None else "stale")

    def material_scene_candidate_for_grant(self, grant: Grant) -> MaterialSceneCandidate:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, "
                "attempt_number FROM device_work_requests WHERE id=?", (grant.request_id,),
            ).fetchone()
            if (row is None or row["owner_type"] != "material_asset"
                    or (row["workspace_id"], row["input_digest"], row["unit_key"],
                        row["attempt_number"])
                    != (grant.workspace_id, grant.input_digest, grant.unit_key,
                        grant.attempt_number)):
                raise PermissionError("镜头许可与执行对象不匹配")
            candidate = self.material_scene_candidate(
                grant.workspace_id, str(row["owner_id"]), grant.input_digest,
                grant.unit_key, conn,
            )
            if candidate is None:
                raise ValueError("镜头窗口来源已变化")
            return candidate

    def commit_material_scene(
        self, conn: sqlite3.Connection, grant: Grant,
        candidate: MaterialSceneCandidate, facts: SceneWindowFacts,
    ) -> str:
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
                or facts.window != candidate.window
                or self.material_scene_candidate(
                    candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
                    candidate.unit_key, conn,
                ) != candidate):
            raise ValueError("镜头检查点不属于当前许可或来源已变化")
        result_json = facts.checkpoint_json()
        if SceneWindowFacts.from_checkpoint_json(result_json) != facts:
            raise ValueError("镜头切点事实无效")
        result_ref = "sha256:" + hashlib.sha256(result_json.encode()).hexdigest()
        existing = conn.execute(
            """SELECT stage_input_digest, result_json, result_ref
               FROM material_analysis_checkpoints WHERE workspace_id=? AND asset_id=?
                 AND source_sha256=? AND provisional_revision=? AND unit_key=?""",
            (candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
             candidate.provisional_revision, candidate.unit_key),
        ).fetchone()
        if existing is not None and (
            existing["stage_input_digest"] != candidate.unit_key.rsplit(":", 1)[-1]
            or existing["result_json"] != result_json or existing["result_ref"] != result_ref
        ):
            raise ValueError("既有镜头检查点结果不匹配")
        if existing is None:
            conn.execute(
                """INSERT INTO material_analysis_checkpoints
                   (workspace_id, asset_id, source_sha256, provisional_revision,
                    unit_key, stage_input_digest, result_json, result_ref, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
                 candidate.provisional_revision, candidate.unit_key,
                 candidate.unit_key.rsplit(":", 1)[-1], result_json, result_ref,
                 utc_now().isoformat()),
            )
        self._assemble_material_scene_if_complete(conn, candidate)
        return f"material_scene:{candidate.asset_id}:{candidate.window.start_seconds:.3f}"

    def _assemble_material_scene_if_complete(
        self, conn: sqlite3.Connection, candidate: MaterialSceneCandidate,
    ) -> None:
        expected_windows = math.ceil(candidate.duration_seconds / SCENE_LOGICAL_WINDOW_SECONDS)
        completed_windows = conn.execute(
            """SELECT COUNT(*) FROM material_analysis_checkpoints
               WHERE workspace_id=? AND asset_id=? AND source_sha256=?
                 AND provisional_revision=? AND unit_key LIKE 'scene-window:v1:%'""",
            (candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
             candidate.provisional_revision),
        ).fetchone()[0]
        if completed_windows < expected_windows:
            return
        boundaries: list[float] = []
        for window in scene_windows(candidate.duration_seconds):
            unit_key = scene_unit_key(
                candidate.source_sha256, candidate.probe_result_ref,
                candidate.video_stream_index, window,
            )
            row = conn.execute(
                """SELECT stage_input_digest, result_json, result_ref
                   FROM material_analysis_checkpoints WHERE workspace_id=? AND asset_id=?
                     AND source_sha256=? AND provisional_revision=? AND unit_key=?""",
                (candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
                 candidate.provisional_revision, unit_key),
            ).fetchone()
            if row is None:
                return
            if not self._valid_scene_checkpoint(row, unit_key, window):
                raise ValueError("镜头检查点损坏，不能装配片段")
            boundaries.extend(SceneWindowFacts.from_checkpoint_json(row["result_json"]).boundaries_seconds)
        row = self._read_asset_payload(
            conn, candidate.workspace_id, candidate.asset_id,
        )
        if row is None:
            raise ValueError("镜头装配时素材已不存在")
        self._assemble_scene_segments(conn, str(row["payload"]), boundaries, candidate.duration_seconds)

    def fail_material_scene(
        self, conn: sqlite3.Connection, grant: Grant,
        candidate: MaterialSceneCandidate, reason: str,
    ) -> None:
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
                or self.material_scene_candidate(
            candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
            candidate.unit_key, conn,
        ) != candidate):
            raise ValueError("镜头失败来源已变化")
        row = self._read_asset_payload(
            conn, candidate.workspace_id, candidate.asset_id,
        )
        self._mark_scene_failed(conn, str(row["payload"]), reason)
