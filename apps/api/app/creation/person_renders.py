"""Task-bound person-render records, cache identity and original status transitions."""
from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
import math
from pathlib import Path
import re
import sqlite3
from typing import Literal
from app.models import utc_now
from app.creation.contracts import validate_logical_reference as _validate_logical_reference
from app.creation.task_input_reads import require_creation_task, narration_segment_from_row
from app.profiles.records import ProfileRecordStore
from app.materials.workspace_references import WorkspaceMediaReferenceAccess


PersonRenderStatus = Literal["pending", "running", "ready", "failed"]


@dataclass(frozen=True)
class PersonRender:
    id: str
    workspace_id: str
    task_id: str
    person_profile_id: str
    person_version: str
    voice_profile_id: str
    voice_version: str
    narration_segment_id: str
    visual_reference: str
    model_id: str
    motion: str
    expression: str
    cache_key: str
    status: PersonRenderStatus
    artifact_ref: str | None = None
    output_path: str | None = None
    duration_seconds: float | None = None
    alignment_evidence: str | None = None
    failure_reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


class PersonRenderStore:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection],
                 profiles: ProfileRecordStore, workspace_references: WorkspaceMediaReferenceAccess) -> None:
        self._connect = connection_factory
        self._profiles = profiles
        self._workspace_references = workspace_references

    def create_person_render(self, render: PersonRender) -> PersonRender:
        _validate_person_render(render)
        if render.status != "pending":
            raise ValueError("新人物片段必须从 pending 状态开始")
        with self._connect() as conn:
            require_creation_task(conn, render.workspace_id, render.task_id)
            narration_row = conn.execute(
                """
                SELECT * FROM narration_segments
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (render.workspace_id, render.task_id, render.narration_segment_id),
            ).fetchone()
            if narration_row is None:
                raise ValueError("人物片段关联的配音分段不属于当前任务")
            narration = narration_segment_from_row(narration_row)
            if narration.status != "ready" or narration.audio_reference is None:
                raise ValueError("人物片段只能使用 ready 配音分段")
            if (
                narration.voice_profile_id != render.voice_profile_id
                or narration.voice_version != render.voice_version
            ):
                raise ValueError("人物片段声音版本必须与配音分段一致")
            self._profiles._get_person_profile(
                conn,
                render.workspace_id,
                render.person_profile_id,
                render.person_version,
            )
            self._profiles._get_versioned_voice_profile(
                conn,
                render.workspace_id,
                render.voice_profile_id,
                render.voice_version,
            )
            self._workspace_references.resolve_workspace_media_reference(
                render.workspace_id,
                render.visual_reference,
                require_registered=True,
            )
            existing = conn.execute(
                """
                SELECT * FROM person_renders
                WHERE workspace_id = ? AND task_id = ? AND cache_key = ?
                """,
                (render.workspace_id, render.task_id, render.cache_key),
            ).fetchone()
            if existing is not None:
                persisted = self._person_render_from_row(existing)
                if _person_render_identity(persisted) != _person_render_identity(render):
                    raise ValueError("人物片段缓存键已绑定到不同输入")
                return persisted
            conn.execute(
                """
                INSERT INTO person_renders
                    (id, workspace_id, task_id, person_profile_id, person_version,
                     voice_profile_id, voice_version, narration_segment_id,
                     visual_reference, model_id, motion, expression, cache_key,
                     status, artifact_ref, output_path, duration_seconds, alignment_evidence,
                     failure_reason, impact, repair_action, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _person_render_values(render),
            )
        return self.get_person_render(render.workspace_id, render.task_id, render.id)

    def claim_person_render(
        self, workspace_id: str, task_id: str, render_id: str
    ) -> PersonRender:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status FROM person_renders
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, render_id),
            ).fetchone()
            if row is None:
                raise KeyError(render_id)
            current = str(row["status"])
            if current not in {"pending", "failed"}:
                raise ValueError("人物片段当前不可领取")
            now = utc_now().isoformat()
            changed = conn.execute(
                """
                UPDATE person_renders
                SET status = 'running', output_path = NULL,
                    duration_seconds = NULL, alignment_evidence = NULL,
                    failure_reason = NULL, impact = NULL, repair_action = NULL,
                    updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ? AND status = ?
                """,
                (now, workspace_id, task_id, render_id, current),
            ).rowcount
            if changed != 1:
                raise ValueError("人物片段已被其他执行者领取")
        return self.get_person_render(workspace_id, task_id, render_id)

    def finish_person_render(
        self,
        workspace_id: str,
        task_id: str,
        render_id: str,
        *,
        status: Literal["ready", "failed"],
        output_path: str | None = None,
        duration_seconds: float | None = None,
        alignment_evidence: str | None = None,
        failure_reason: str | None = None,
        impact: str | None = None,
        repair_action: str | None = None,
    ) -> PersonRender:
        candidate = replace(
            self.get_person_render(workspace_id, task_id, render_id),
            status=status,
            output_path=output_path,
            duration_seconds=duration_seconds,
            alignment_evidence=alignment_evidence,
            failure_reason=failure_reason,
            impact=impact,
            repair_action=repair_action,
            updated_at=utc_now(),
        )
        _validate_person_render(candidate)
        with self._connect() as conn:
            changed = conn.execute(
                """
                UPDATE person_renders
                SET status = ?, output_path = ?, duration_seconds = ?,
                    alignment_evidence = ?, failure_reason = ?, impact = ?,
                    repair_action = ?, updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                  AND status = 'running'
                """,
                (
                    candidate.status,
                    candidate.output_path,
                    candidate.duration_seconds,
                    candidate.alignment_evidence,
                    candidate.failure_reason,
                    candidate.impact,
                    candidate.repair_action,
                    candidate.updated_at.isoformat(),
                    workspace_id,
                    task_id,
                    render_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("只能结束运行中的人物片段")
        return self.get_person_render(workspace_id, task_id, render_id)

    def bind_person_render_artifact(
        self,
        workspace_id: str,
        task_id: str,
        render_id: str,
        artifact_ref: str,
    ) -> PersonRender:
        _validate_logical_reference(artifact_ref, "人物片段产物引用")
        with self._connect() as conn:
            require_creation_task(conn, workspace_id, task_id)
            render_row = conn.execute(
                """
                SELECT * FROM person_renders
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, render_id),
            ).fetchone()
            if render_row is None:
                raise KeyError(render_id)
            render = self._person_render_from_row(render_row)
            if render.status != "ready":
                raise ValueError("只能为 ready 人物片段绑定产物")
            if render.artifact_ref is not None:
                if render.artifact_ref != artifact_ref:
                    raise ValueError("人物片段已绑定其他产物引用")
                return render
            artifact_row = conn.execute(
                """
                SELECT task_id, kind FROM artifact_records
                WHERE workspace_id = ? AND logical_ref = ?
                """,
                (workspace_id, artifact_ref),
            ).fetchone()
            if artifact_row is None or str(artifact_row["task_id"]) != task_id:
                raise ValueError("人物片段产物不属于当前工作区与创作任务")
            if str(artifact_row["kind"]) != "person_render":
                raise ValueError("人物片段产物类型必须为 person_render")
            now = utc_now().isoformat()
            changed = conn.execute(
                """
                UPDATE person_renders
                SET artifact_ref = ?, updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                  AND status = 'ready' AND artifact_ref IS NULL
                """,
                (artifact_ref, now, workspace_id, task_id, render_id),
            ).rowcount
            if changed != 1:
                raise ValueError("人物片段产物引用未能绑定")
        return self.get_person_render(workspace_id, task_id, render_id)

    def get_person_render(
        self, workspace_id: str, task_id: str, render_id: str
    ) -> PersonRender:
        with self._connect() as conn:
            require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                """
                SELECT * FROM person_renders
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, render_id),
            ).fetchone()
        if row is None:
            raise KeyError(render_id)
        return self._person_render_from_row(row)

    def list_person_renders(
        self, workspace_id: str, task_id: str
    ) -> list[PersonRender]:
        with self._connect() as conn:
            require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM person_renders
                WHERE workspace_id = ? AND task_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self._person_render_from_row(row) for row in rows]

    def interrupt_running_person_renders(self, workspace_id: str | None = None) -> int:
        now = utc_now().isoformat()
        workspace_clause = "" if workspace_id is None else " AND workspace_id = ?"
        parameters: tuple[object, ...] = (
            (now,) if workspace_id is None else (now, workspace_id)
        )
        with self._connect() as conn:
            return conn.execute(
                """
                UPDATE person_renders
                SET status = 'failed', failure_reason = 'process_interrupted',
                    impact = '本次人物片段执行未产生可信输出。',
                    repair_action = '重新生成该人物片段。', updated_at = ?
                WHERE status = 'running'
                """
                + workspace_clause,
                parameters,
            ).rowcount

    @staticmethod
    def _person_render_from_row(row: sqlite3.Row) -> PersonRender:
        return PersonRender(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            person_profile_id=str(row["person_profile_id"]),
            person_version=str(row["person_version"]),
            voice_profile_id=str(row["voice_profile_id"]),
            voice_version=str(row["voice_version"]),
            narration_segment_id=str(row["narration_segment_id"]),
            visual_reference=str(row["visual_reference"]),
            model_id=str(row["model_id"]),
            motion=str(row["motion"]),
            expression=str(row["expression"]),
            cache_key=str(row["cache_key"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            artifact_ref=(
                str(row["artifact_ref"])
                if row["artifact_ref"] is not None
                else None
            ),
            output_path=(
                str(row["output_path"]) if row["output_path"] is not None else None
            ),
            duration_seconds=(
                float(row["duration_seconds"])
                if row["duration_seconds"] is not None
                else None
            ),
            alignment_evidence=(
                str(row["alignment_evidence"])
                if row["alignment_evidence"] is not None
                else None
            ),
            failure_reason=(
                str(row["failure_reason"])
                if row["failure_reason"] is not None
                else None
            ),
            impact=str(row["impact"]) if row["impact"] is not None else None,
            repair_action=(
                str(row["repair_action"])
                if row["repair_action"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )


def _person_render_values(render: PersonRender) -> tuple[object, ...]:
    return (
        render.id,
        render.workspace_id,
        render.task_id,
        render.person_profile_id,
        render.person_version,
        render.voice_profile_id,
        render.voice_version,
        render.narration_segment_id,
        render.visual_reference,
        render.model_id,
        render.motion,
        render.expression,
        render.cache_key,
        render.status,
        render.artifact_ref,
        render.output_path,
        render.duration_seconds,
        render.alignment_evidence,
        render.failure_reason,
        render.impact,
        render.repair_action,
        render.created_at.isoformat(),
        render.updated_at.isoformat(),
    )


def _person_render_identity(render: PersonRender) -> tuple[str, ...]:
    return (
        render.workspace_id,
        render.task_id,
        render.person_profile_id,
        render.person_version,
        render.voice_profile_id,
        render.voice_version,
        render.narration_segment_id,
        render.visual_reference,
        render.model_id,
        render.motion,
        render.expression,
        render.cache_key,
    )


def _validate_person_render(render: PersonRender) -> None:
    if not all(
        value.strip()
        for value in (
            render.id,
            render.workspace_id,
            render.task_id,
            render.person_profile_id,
            render.person_version,
            render.voice_profile_id,
            render.voice_version,
            render.narration_segment_id,
            render.visual_reference,
            render.model_id,
            render.motion,
            render.expression,
            render.cache_key,
        )
    ):
        raise ValueError("人物片段缺少必要谱系、输入版本或模型参数")
    _validate_logical_reference(render.visual_reference, "人物片段画面引用")
    if render.artifact_ref is not None:
        _validate_logical_reference(render.artifact_ref, "人物片段产物引用")
    if not re.fullmatch(r"[0-9a-f]{64}", render.cache_key):
        raise ValueError("人物片段缓存键必须是小写 SHA-256")
    if render.updated_at < render.created_at:
        raise ValueError("人物片段更新时间不能早于创建时间")
    if render.status in {"pending", "running"}:
        if any(
            value is not None
            for value in (
                render.output_path,
                render.artifact_ref,
                render.duration_seconds,
                render.alignment_evidence,
                render.failure_reason,
                render.impact,
                render.repair_action,
            )
        ):
            raise ValueError("未完成的人物片段不能包含输出或失败终态")
    elif render.status == "ready":
        if (
            not (render.output_path or "").strip()
            or Path(render.output_path or "").is_absolute() is False
            or render.duration_seconds is None
            or not math.isfinite(render.duration_seconds)
            or render.duration_seconds <= 0
            or not (render.alignment_evidence or "").strip()
        ):
            raise ValueError("ready 人物片段必须有有效输出、时长和对齐证据")
        if any(
            value is not None
            for value in (render.failure_reason, render.impact, render.repair_action)
        ):
            raise ValueError("ready 人物片段不能包含失败终态")
    elif render.status == "failed":
        if not all(
            (value or "").strip()
            for value in (render.failure_reason, render.impact, render.repair_action)
        ):
            raise ValueError("失败人物片段必须记录原因、影响和修复动作")
        if any(
            value is not None
            for value in (
                render.output_path,
                render.artifact_ref,
                render.duration_seconds,
                render.alignment_evidence,
            )
        ):
            raise ValueError("失败人物片段不能登记成功输出")
    else:
        raise ValueError("人物片段状态无效")
