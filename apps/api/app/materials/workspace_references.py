"""Canonical workspace media paths and original joined reference claims."""
from __future__ import annotations
from collections.abc import Callable
from pathlib import Path
import sqlite3
from urllib.parse import unquote
from app.models import utc_now
from app.materials.assets import MaterialAssetStore
from app.materials.contracts import MediaAuthorityError
from app.materials.source_files import MaterialSourceFileAccess


class WorkspaceMediaReferenceAccess:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection],
                 source_files: MaterialSourceFileAccess, assets: MaterialAssetStore) -> None:
        self._connect = connection_factory
        self._source_files = source_files
        self._assets = assets

    def resolve_workspace_media_reference(
        self,
        workspace_id: str,
        reference: str,
        *,
        require_registered: bool = False,
    ) -> Path:
        """Resolve an approved workspace media reference without widening its root."""
        if not workspace_id.strip():
            raise MediaAuthorityError(
                reason="workspace_required",
                impact="不能解析或使用没有工作区归属的媒体。",
                repair_action="从当前已认证工作区重新提交媒体引用。",
            )
        value = reference.strip()
        root = self._source_files.video_library_path.resolve()
        if value.startswith("workspace://"):
            encoded_path = value.removeprefix("workspace://")
            decoded_path = unquote(encoded_path)
            parts = decoded_path.split("/")
            if (
                not decoded_path
                or "\\" in decoded_path
                or "\x00" in decoded_path
                or decoded_path.startswith("/")
                or any(part in {"", ".", ".."} for part in parts)
            ):
                raise MediaAuthorityError(
                    reason="invalid_media_reference",
                    impact="媒体引用未被解析，未向模型或渲染器传递文件路径。",
                    repair_action="使用规范的 workspace://<relative-posix-path> 引用。",
                )
            candidate = root.joinpath(*parts)
        else:
            raise MediaAuthorityError(
                reason="logical_media_reference_required",
                impact="任意本机路径不能作为已授权媒体使用。",
                repair_action="从当前工作区已批准素材选择 workspace:// 逻辑引用。",
            )
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise MediaAuthorityError(
                reason="media_reference_outside_authorized_root",
                impact="越界媒体不会被读取或传给模型、ffmpeg。",
                repair_action="选择当前工作区已授权媒体根目录内的素材。",
            ) from exc
        if require_registered:
            self._require_workspace_media_reference(workspace_id, value)
        return resolved

    def workspace_media_reference_for_asset(
        self,
        workspace_id: str,
        asset_id: str,
        *, connection: sqlite3.Connection | None = None,
    ) -> str:
        asset = self._assets.get_asset(workspace_id, asset_id, connection=connection)
        root = self._source_files.video_library_path.resolve()
        resolved = Path(asset.path).resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            raise MediaAuthorityError(
                reason="asset_outside_authorized_root",
                impact="素材不会被写入剪辑计划或传给渲染器。",
                repair_action="重新扫描当前工作区已授权素材根内的文件。",
            ) from exc
        return f"workspace://{relative.as_posix()}"

    def claim_workspace_media_reference(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        reference: str,
    ) -> None:
        row = conn.execute(
            "SELECT workspace_id FROM workspace_media_references WHERE reference = ?",
            (reference,),
        ).fetchone()
        if row is not None and str(row["workspace_id"]) != workspace_id:
            raise MediaAuthorityError(
                reason="media_reference_owned_by_another_workspace",
                impact="其他工作区登记的媒体不会被当前声音档案或创作任务使用。",
                repair_action="从当前工作区选择已授权的媒体引用，或由原工作区重新授权。",
            )
        conn.execute(
            """
            INSERT INTO workspace_media_references (reference, workspace_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(reference) DO UPDATE SET
                workspace_id = excluded.workspace_id,
                updated_at = excluded.updated_at
            """,
            (reference, workspace_id, utc_now().isoformat()),
        )

    def _require_workspace_media_reference(
        self,
        workspace_id: str,
        reference: str,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT workspace_id FROM workspace_media_references WHERE reference = ?",
                (reference,),
            ).fetchone()
        if row is None or str(row["workspace_id"]) != workspace_id:
            raise MediaAuthorityError(
                reason="media_reference_not_authorized_for_workspace",
                impact="未登记给当前工作区的媒体不会被读取或传给模型、ffmpeg。",
                repair_action="先将该媒体作为当前工作区已授权声音引用登记，或选择该工作区已有的逻辑引用。",
            )
