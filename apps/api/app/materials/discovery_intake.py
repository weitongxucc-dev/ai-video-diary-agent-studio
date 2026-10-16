"""Complete discovery copies enter the existing material aggregate exactly once."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3

from app.materials.assets import MaterialAssetStore
from app.materials.contracts import MATERIAL_MEDIA_LABEL, MaterialAsset, material_media_type_for
from app.materials.source_files import MaterialSourceFileAccess
from app.models import utc_now


class DiscoveryIntake:
    def __init__(
        self, connection_factory: Callable[[], sqlite3.Connection], assets: MaterialAssetStore, *,
        target_for: Callable[[str], Path], confirm_copy: Callable[..., None],
    ):
        self._connect = connection_factory
        self._assets = assets
        self._target_for = target_for
        self._confirm_copy = confirm_copy

    def __call__(self, workspace_id: str, owner_user_id: str, path: Path, media_type: str, source_path: str, sha256: str) -> str:
        root = self._target_for(workspace_id).resolve(strict=True)
        resolved = path.resolve(strict=True)
        if path.is_symlink() or path != resolved or not resolved.is_relative_to(root):
            raise ValueError("接收素材不在原绑定目录内")
        if material_media_type_for(resolved) != media_type:
            raise ValueError("接收素材类型不匹配")
        info = resolved.stat()
        if not info.st_size:
            raise ValueError("接收文件为空，未登记")
        # The trusted discovery copier supplies its digest, never the renderer.
        identity = hashlib.sha256(f"{workspace_id}\0{owner_user_id}".encode()).hexdigest()[:16]
        if resolved.name != f"discovery-{identity}-{sha256}{resolved.suffix.lower()}":
            raise ValueError("接收文件与发现回执不匹配")
        self._confirm_copy(workspace_id, relative_path=str(resolved.relative_to(root)),
            expected_revision=MaterialSourceFileAccess.material_copy_revision(info),
            confirmation_source="app_import", verified_owner_user_id=owner_user_id)
        asset_id = "asset_" + hashlib.sha256(f"{workspace_id}\0{resolved}".encode()).hexdigest()[:16]
        asset = MaterialAsset(id=asset_id, workspace_id=workspace_id, owner_user_id=owner_user_id,
            file_name=Path(source_path).name, path=str(resolved), size_bytes=info.st_size,
            modified_at=datetime.fromtimestamp(info.st_mtime, tz=utc_now().tzinfo), media_type=media_type)
        if media_type != "video":
            asset.analysis_status = "blocked"
            asset.analysis_message = f"这条{MATERIAL_MEDIA_LABEL[media_type]}已入库；首批只支持视频的镜头与画面分析，内容分析尚未接入。"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            saved = self._assets.save_received_asset(conn, asset, workspace_id=workspace_id, owner_user_id=owner_user_id)
        return saved.id
