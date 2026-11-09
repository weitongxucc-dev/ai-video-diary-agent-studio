"""Transform/dependency records; publication remains in the caller's transaction."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
import json
import sqlite3

from app.creation.contracts import (
    TransformRun as _TransformRun,
    DependencyEdge as _DependencyEdge,
    validate_logical_reference as _validate_logical_reference,
)
from app.persistence_json import _json_object


def _validate_transform_run(run: _TransformRun) -> None:
    if not all(
        value.strip()
        for value in (
            run.id,
            run.workspace_id,
            run.task_id,
            run.job_id,
            run.transform_type,
            run.input_digest,
            run.engine,
        )
    ):
        raise ValueError("变换运行缺少必要标识、输入摘要或引擎")
    for reference in (*run.input_refs, *run.output_refs):
        _validate_logical_reference(reference, "变换输入输出引用")
    if len(set(run.input_refs)) != len(run.input_refs) or len(
        set(run.output_refs)
    ) != len(run.output_refs):
        raise ValueError("变换输入输出引用不能重复")
    _json_object(run.parameters)
    if run.finished_at is not None and run.finished_at < run.started_at:
        raise ValueError("变换结束时间不能早于开始时间")
    if run.status == "running":
        if (
            run.finished_at is not None
            or run.output_refs
            or run.failure_reason is not None
            or run.remediation is not None
        ):
            raise ValueError("运行中变换不能已有输出或终态信息")
    elif run.status == "succeeded":
        if run.finished_at is None or not run.output_refs:
            raise ValueError("成功变换必须包含结束时间和输出引用")
        if run.failure_reason is not None or run.remediation is not None:
            raise ValueError("成功变换不能包含失败信息")
    elif run.status == "failed":
        if (
            run.finished_at is None
            or not (run.failure_reason or "").strip()
            or not (run.remediation or "").strip()
        ):
            raise ValueError("失败变换必须包含结束时间、原因和修复动作")
        if run.output_refs:
            raise ValueError("失败变换不能登记成功输出")
    else:
        raise ValueError("变换运行状态无效")


def _validate_dependency_edge(edge: _DependencyEdge) -> None:
    if not all(
        value.strip()
        for value in (
            edge.id,
            edge.workspace_id,
            edge.task_id,
            edge.created_by_transform_id,
        )
    ):
        raise ValueError("依赖边缺少必要标识或变换引用")
    _validate_logical_reference(edge.upstream_ref, "依赖边上游引用")
    _validate_logical_reference(edge.downstream_ref, "依赖边下游引用")
    if edge.upstream_ref == edge.downstream_ref:
        raise ValueError("依赖边不能指向自身")


class LineageRecordStore:
    def __init__(self, connection_factory, *, require_task, job_belongs, output_producers,
                 decode_reference_list, decode_parameter_object):
        self._connect = connection_factory
        self._require_creation_task = require_task
        self._job_belongs = job_belongs
        self._output_producers = output_producers
        self._decode_reference_list = decode_reference_list
        self._decode_parameter_object = decode_parameter_object

    def create_transform_run(self, run: _TransformRun, *, connection: sqlite3.Connection | None = None) -> _TransformRun:
        _validate_transform_run(run)
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            self._require_creation_task(conn, run.workspace_id, run.task_id)
            if not self._job_belongs(conn, run.workspace_id, run.task_id, run.job_id):
                raise ValueError("变换运行关联的 JobRun 不属于当前工作区与任务")
            if run.status == "succeeded":
                artifacts = self._output_producers(
                    conn, run.workspace_id, run.task_id, run.output_refs,
                )
                if any(
                    artifacts.get(reference) != run.job_id
                    for reference in run.output_refs
                ):
                    raise ValueError("成功变换的输出必须是该 JobRun 已登记的产物")
            existing_row = conn.execute(
                "SELECT * FROM transform_runs WHERE id = ?",
                (run.id,),
            ).fetchone()
            if existing_row is not None:
                persisted = self._transform_run_from_row(existing_row)
                if persisted != run:
                    raise ValueError("变换运行记录 ID 已存在，且领域字段不一致")
                return persisted
            try:
                conn.execute(
                    """
                    INSERT INTO transform_runs
                        (id, workspace_id, task_id, job_id, transform_type,
                         input_digest, input_refs_json, output_refs_json, engine,
                         parameters_json, status, started_at, finished_at,
                         failure_reason, remediation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id,
                        run.workspace_id,
                        run.task_id,
                        run.job_id,
                        run.transform_type,
                        run.input_digest,
                        json.dumps(run.input_refs, ensure_ascii=False),
                        json.dumps(run.output_refs, ensure_ascii=False),
                        run.engine,
                        _json_object(run.parameters),
                        run.status,
                        run.started_at.isoformat(),
                        run.finished_at.isoformat() if run.finished_at else None,
                        run.failure_reason,
                        run.remediation,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("变换运行记录 ID 已存在") from exc
            row = conn.execute("SELECT * FROM transform_runs WHERE workspace_id=? AND id=?", (run.workspace_id, run.id)).fetchone()
            return self._transform_run_from_row(row)

    def get_transform_run(self, workspace_id: str, transform_id: str, *, connection=None) -> _TransformRun:
        with self._connect() if connection is None else nullcontext(connection) as conn:
            row = conn.execute(
                "SELECT * FROM transform_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, transform_id),
            ).fetchone()
        if row is None:
            raise KeyError(transform_id)
        return self._transform_run_from_row(row)

    def list_transform_runs(
        self, workspace_id: str, task_id: str, *, connection: sqlite3.Connection | None = None
    ) -> list[_TransformRun]:
        with self._connect() if connection is None else nullcontext(connection) as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM transform_runs
                WHERE workspace_id = ? AND task_id = ? ORDER BY started_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self._transform_run_from_row(row) for row in rows]

    def create_dependency_edge(self, edge: _DependencyEdge, *, connection: sqlite3.Connection | None = None) -> _DependencyEdge:
        _validate_dependency_edge(edge)
        if connection is None:
            with self._connect() as conn:
                self.create_dependency_edge(edge, connection=conn)
            return self.get_dependency_edge(edge.workspace_id, edge.id)
        conn = connection
        self._require_creation_task(conn, edge.workspace_id, edge.task_id)
        transform_row = conn.execute(
            """
            SELECT * FROM transform_runs
            WHERE workspace_id = ? AND task_id = ? AND id = ?
            """,
            (
                edge.workspace_id,
                edge.task_id,
                edge.created_by_transform_id,
            ),
        ).fetchone()
        if transform_row is None:
            raise ValueError("依赖边关联的变换不属于当前工作区与任务")
        transform = self._transform_run_from_row(transform_row)
        if transform.status != "succeeded":
            raise ValueError("只能为成功变换登记依赖边")
        if edge.upstream_ref not in transform.input_refs:
            raise ValueError("依赖边上游不在变换输入引用中")
        if edge.downstream_ref not in transform.output_refs:
            raise ValueError("依赖边下游不在变换输出引用中")
        existing_row = conn.execute(
            "SELECT * FROM dependency_edges WHERE id = ?",
            (edge.id,),
        ).fetchone()
        if existing_row is None:
            existing_row = conn.execute(
                """
                SELECT * FROM dependency_edges
                WHERE workspace_id = ? AND task_id = ?
                  AND upstream_ref = ? AND downstream_ref = ?
                  AND created_by_transform_id = ?
                """,
                (
                    edge.workspace_id,
                    edge.task_id,
                    edge.upstream_ref,
                    edge.downstream_ref,
                    edge.created_by_transform_id,
                ),
            ).fetchone()
        if existing_row is not None:
            persisted = self._dependency_edge_from_row(existing_row)
            if persisted != edge:
                raise ValueError("依赖边 ID 或逻辑唯一键已存在，且领域字段不一致")
            return persisted
        try:
            conn.execute(
                """
                INSERT INTO dependency_edges
                    (id, workspace_id, task_id, upstream_ref, downstream_ref,
                     created_by_transform_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.id,
                    edge.workspace_id,
                    edge.task_id,
                    edge.upstream_ref,
                    edge.downstream_ref,
                    edge.created_by_transform_id,
                    edge.created_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("依赖边 ID 或同一变换中的边已存在") from exc
        return edge

    def get_dependency_edge(
        self, workspace_id: str, edge_id: str
    ) -> _DependencyEdge:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM dependency_edges WHERE workspace_id = ? AND id = ?",
                (workspace_id, edge_id),
            ).fetchone()
        if row is None:
            raise KeyError(edge_id)
        return self._dependency_edge_from_row(row)

    def list_dependency_edges(
        self, workspace_id: str, task_id: str
    ) -> list[_DependencyEdge]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM dependency_edges
                WHERE workspace_id = ? AND task_id = ? ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self._dependency_edge_from_row(row) for row in rows]

    def _transform_run_from_row(self, row: sqlite3.Row) -> _TransformRun:
        return _TransformRun(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            job_id=str(row["job_id"]),
            transform_type=str(row["transform_type"]),
            input_digest=str(row["input_digest"]),
            input_refs=self._decode_reference_list(str(row["input_refs_json"])),
            output_refs=self._decode_reference_list(str(row["output_refs_json"])),
            engine=str(row["engine"]),
            parameters=self._decode_parameter_object(str(row["parameters_json"])),
            status=str(row["status"]),  # type: ignore[arg-type]
            started_at=datetime.fromisoformat(str(row["started_at"])),
            finished_at=(
                datetime.fromisoformat(str(row["finished_at"]))
                if row["finished_at"] is not None
                else None
            ),
            failure_reason=(
                str(row["failure_reason"])
                if row["failure_reason"] is not None
                else None
            ),
            remediation=(
                str(row["remediation"])
                if row["remediation"] is not None
                else None
            ),
        )

    @staticmethod
    def _dependency_edge_from_row(row: sqlite3.Row) -> _DependencyEdge:
        return _DependencyEdge(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            upstream_ref=str(row["upstream_ref"]),
            downstream_ref=str(row["downstream_ref"]),
            created_by_transform_id=str(row["created_by_transform_id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def list_recipe_runs(self, connection, workspace_id, owner_member_id, tool_id=None):
        rows = connection.execute("""SELECT * FROM transform_runs
            WHERE workspace_id=? AND transform_type='media_script_tool_recipe'
              AND json_extract(parameters_json,'$.owner_member_id')=?
              AND (? IS NULL OR json_extract(parameters_json,'$.tool_id')=?)""",
            (workspace_id, owner_member_id, tool_id, tool_id)).fetchall()
        return [self._transform_run_from_row(row) for row in rows]

    def deactivate_recipe_run(self, connection, workspace_id, run):
        parameters_json = json.dumps({**run.parameters, "active": False},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        connection.execute("UPDATE transform_runs SET parameters_json=? WHERE workspace_id=? AND id=?",
            (parameters_json, workspace_id, run.id))
