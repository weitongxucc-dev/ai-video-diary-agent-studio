"""Preview attempt identity, history and original device-request persistence."""
import json
from dataclasses import replace
from app.models import utc_now
from app.persistence_json import _json_payload, _with_datetime
from app.profiles.preview_contracts import MediaPreviewRunRecord, MediaPreviewType


class PreviewRecordStore:
    def __init__(self, connection_factory):
        self._connect = connection_factory

    def get_media_preview_run(
        self,
        workspace_id: str,
        preview_type: MediaPreviewType,
        identity: str,
    ) -> MediaPreviewRunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM media_preview_attempts
                WHERE workspace_id = ? AND preview_type = ? AND identity = ?
                ORDER BY attempt DESC
                LIMIT 1
                """,
                (workspace_id, preview_type, identity),
            ).fetchone()
        if row is None:
            return None
        return self._media_preview_run_from_payload(json.loads(str(row["payload"])))

    def save_media_preview_run(
        self,
        record: MediaPreviewRunRecord,
    ) -> MediaPreviewRunRecord:
        _validate_media_preview_run(record)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT payload FROM media_preview_attempts
                WHERE workspace_id = ? AND preview_type = ? AND identity = ? AND attempt = ?
                """,
                (record.workspace_id, record.preview_type, record.identity, record.attempt),
            ).fetchone()
            if row is not None:
                previous = self._media_preview_run_from_payload(
                    json.loads(str(row["payload"]))
                )
                if previous.id != record.id:
                    raise ValueError("预览尝试 ID 与现有记录不一致")
                if previous.device_request_id is not None:
                    if record.device_request_id not in {None, previous.device_request_id}:
                        raise ValueError("预览尝试已绑定其他设备请求")
                    record = replace(record, device_request_id=previous.device_request_id)
                if previous.status not in {"queued", "running"} and previous != record:
                    raise ValueError("已结束的预览尝试不可覆盖")
            conn.execute(
                """
                INSERT INTO media_preview_attempts
                    (id, workspace_id, preview_type, identity, attempt, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, preview_type, identity, attempt) DO UPDATE SET
                    id = excluded.id,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    record.id,
                    record.workspace_id,
                    record.preview_type,
                    record.identity,
                    record.attempt,
                    _json_payload(record),
                    utc_now().isoformat(),
                ),
            )
        return record

    def bind_preview_device_request(self, run, request_id, *, conn=None):
        """Bind this attempt, including a terminal checkpoint, to its exact request."""
        if conn is None:
            with self._connect() as writer:
                writer.execute("BEGIN IMMEDIATE")
                return self.bind_preview_device_request(run, request_id, conn=writer)
        row = conn.execute("SELECT payload FROM media_preview_attempts WHERE workspace_id=? AND id=?", (run.workspace_id, run.id)).fetchone()
        if row is None:
            raise KeyError(run.id)
        current = self._media_preview_run_from_payload(json.loads(row["payload"]))
        request = conn.execute("SELECT workspace_id,owner_type,owner_id,input_digest FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
        if (request is None or tuple(request) != (current.workspace_id, "media_preview", current.id, current.input_digest)
                or (run.identity, run.attempt, run.input_digest) != (current.identity, current.attempt, current.input_digest)):
            raise ValueError("设备请求与预览尝试或输入不一致")
        if current.device_request_id not in {None, request_id}:
            raise ValueError("预览尝试已绑定其他设备请求")
        updated = replace(current, device_request_id=request_id)
        conn.execute("UPDATE media_preview_attempts SET payload=? WHERE workspace_id=? AND id=?", (_json_payload(updated), run.workspace_id, run.id))
        return updated

    def claim_media_preview_run(
        self,
        record: MediaPreviewRunRecord,
        *,
        retry: bool,
        succeeded_reusable: bool = True,
    ) -> tuple[MediaPreviewRunRecord, bool]:
        """Atomically return the latest preview run or create one running attempt."""
        _validate_media_preview_run(record)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT payload FROM media_preview_attempts
                WHERE workspace_id = ? AND preview_type = ? AND identity = ?
                ORDER BY attempt DESC
                LIMIT 1
                """,
                (record.workspace_id, record.preview_type, record.identity),
            ).fetchone()
            latest = (
                self._media_preview_run_from_payload(json.loads(str(row["payload"])))
                if row is not None
                else None
            )
            if latest is not None:
                if latest.status == "queued" and record.status == "running":
                    running = replace(latest, status="running")
                    conn.execute("UPDATE media_preview_attempts SET payload=?,updated_at=? WHERE id=?",
                                 (_json_payload(running), utc_now().isoformat(), running.id))
                    return running, True
                if latest.status in {"queued", "running"}:
                    return latest, False
                if latest.status == "succeeded" and succeeded_reusable:
                    return latest, False
                if latest.status != "succeeded" and not retry:
                    return latest, False
                attempt = latest.attempt + 1
            else:
                attempt = 1
            claimed = replace(
                record,
                id=f"{record.preview_type}_preview_{record.identity}_{attempt}",
                attempt=attempt,
            )
            _validate_media_preview_run(claimed)
            conn.execute(
                """
                INSERT INTO media_preview_attempts
                    (id, workspace_id, preview_type, identity, attempt, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claimed.id,
                    claimed.workspace_id,
                    claimed.preview_type,
                    claimed.identity,
                    claimed.attempt,
                    _json_payload(claimed),
                    utc_now().isoformat(),
                ),
            )
        return claimed, True

    def list_media_preview_attempts(
        self,
        workspace_id: str,
        preview_type: MediaPreviewType,
        identity: str,
    ) -> list[MediaPreviewRunRecord]:
        if preview_type not in {"voice", "avatar_lipsync"}:
            raise ValueError("预览类型无效")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM media_preview_attempts
                WHERE workspace_id = ? AND preview_type = ? AND identity = ?
                ORDER BY attempt DESC
                """,
                (workspace_id, preview_type, identity),
            ).fetchall()
        return [
            self._media_preview_run_from_payload(json.loads(str(row["payload"])))
            for row in rows
        ]

    def list_media_preview_runs(
        self,
        workspace_id: str,
        preview_type: MediaPreviewType | None = None,
    ) -> list[MediaPreviewRunRecord]:
        if preview_type is not None and preview_type not in {"voice", "avatar_lipsync"}:
            raise ValueError("预览类型无效")
        with self._connect() as conn:
            if preview_type is None:
                rows = conn.execute(
                    """
                    SELECT payload FROM media_preview_attempts
                    WHERE workspace_id = ? ORDER BY updated_at DESC
                    """,
                    (workspace_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT payload FROM media_preview_attempts
                    WHERE workspace_id = ? AND preview_type = ? ORDER BY updated_at DESC
                    """,
                    (workspace_id, preview_type),
                ).fetchall()
        return [
            self._media_preview_run_from_payload(json.loads(str(row["payload"])))
            for row in rows
        ]

    @staticmethod
    def _media_preview_run_from_payload(
        data: dict[str, object],
    ) -> MediaPreviewRunRecord:
        data = _with_datetime(data, "started_at", "finished_at")
        return MediaPreviewRunRecord(**data)


    def read(self, workspace_id, run_id, *, connection):
        row = connection.execute("SELECT payload FROM media_preview_attempts WHERE workspace_id=? AND id=?",
            (workspace_id, run_id)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._media_preview_run_from_payload(json.loads(row['payload']))

    def local_revisions(self, workspace_id, run_id, *, upstream_identity, connection):
        own = connection.execute("SELECT updated_at FROM media_preview_attempts WHERE workspace_id=? AND id=?",
            (workspace_id, run_id)).fetchone()
        upstream = None
        if upstream_identity is not None:
            upstream = tuple(map(tuple, connection.execute(
                "SELECT id,updated_at FROM media_preview_attempts WHERE workspace_id=? AND preview_type='voice' AND identity=? ORDER BY attempt",
                (workspace_id, upstream_identity)).fetchall()))
        return tuple(own) if own else None, upstream

    def checkpoint(self, terminal, request_id, *, connection):
        current = self.bind_preview_device_request(terminal, request_id, conn=connection)
        terminal = replace(terminal, device_request_id=request_id)
        if current.status not in {'queued', 'running'} and current != terminal:
            raise ValueError("已结束的预览尝试不可覆盖")
        connection.execute("UPDATE media_preview_attempts SET payload=?, updated_at=? WHERE workspace_id=? AND id=?",
            (_json_payload(terminal), utc_now().isoformat(), terminal.workspace_id, terminal.id))
        return terminal.id


def _validate_media_preview_run(record: MediaPreviewRunRecord) -> None:
    if not record.workspace_id.strip() or not record.id.strip() or not record.identity.strip():
        raise ValueError("预览运行记录缺少工作区、ID 或幂等身份")
    if record.preview_type not in {"voice", "avatar_lipsync"}:
        raise ValueError("预览类型无效")
    if record.status not in {"queued", "running", "succeeded", "unavailable", "blocked", "failed", "cancelled"}:
        raise ValueError("预览运行状态无效")
    if not record.capability_id.strip() or not record.model_id.strip() or not record.language.strip():
        raise ValueError("预览运行记录缺少能力、模型或语言")
    if record.attempt < 1:
        raise ValueError("预览尝试序号必须从 1 开始")
    if record.status in {"queued", "running"}:
        if record.finished_at is not None:
            raise ValueError("运行中的预览不能包含结束时间")
    elif record.status == "succeeded":
        if not record.output_path or record.finished_at is None:
            raise ValueError("成功预览必须包含输出引用和结束时间")
    elif (
        record.finished_at is None
        or not record.reason
        or not record.impact
        or not record.repair_action
    ):
        raise ValueError("终态失败或不可用预览必须包含结束时间、原因、影响和修复动作")
