"""Direct-link material reuse, restoration and provenance receipts."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.materials.assets import MaterialAssetStore
from app.materials.contracts import MaterialAsset, MaterialMediaType, MATERIAL_MEDIA_LABEL
from app.materials.source_files import MaterialSourceFileAccess
from app.media_link_import import RegisteredLink
from app.models import utc_now
from app.source_policy import knowledge_item_readable as _content_readable


class LinkMaterialIntakeUseCase:
    def __init__(self, *, assets: MaterialAssetStore, source_files: MaterialSourceFileAccess,
                 accept_discovered_asset: Callable[..., MaterialAsset]) -> None:
        self._assets = assets
        self._source_files = source_files
        self._accept_discovered_asset = accept_discovered_asset

    def existing_link_material(
        self, workspace_id: str, source_url: str, *,
        viewer_user_id: str | None = None, local_diagnostic: bool = False,
    ) -> MaterialAsset | None:
        """The material already registered for one original direct link.

        Returns it only while its own file is still there at the recorded size, so
        a repeat submission of the same URL can reuse both instead of downloading
        again over the top of it.
        """
        asset = self._assets.find_asset_by_source_url(workspace_id, source_url)
        if asset is not None and not _content_readable(
            user_id=viewer_user_id, local_diagnostic=local_diagnostic,
            visibility=asset.visibility, owner_user_id=asset.owner_user_id,
        ):
            raise KeyError("material asset not found")
        if asset is None or not self._source_files.material_file_intact(asset):
            return None
        return asset

    def _discard_redundant_link_file(self, path: Path) -> None:
        """Drop a link-import file that no material references.

        The video whitelist scans the intake directory, so a leftover copy would
        later be registered as a second material for the same source. Only files
        this feature names after their URL are ever removed.
        """
        resolved = path.resolve()
        try:
            resolved.relative_to(self._source_files.video_library_path.resolve())
        except ValueError as exc:
            raise ValueError("素材文件不在配置的素材库中") from exc
        if resolved.name.startswith("link-") and resolved.is_file():
            resolved.unlink(missing_ok=True)

    def register_link_material(
        self,
        workspace_id: str,
        path: Path,
        *,
        media_type: MaterialMediaType,
        source_url: str,
        verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> MaterialAsset:
        """Register one fully downloaded direct link as a workspace material asset.

        This is the only path that admits non-video media. The original URL is the
        identity: re-submitting it reuses the record made on first import. This
        ticket has no "refresh this source" scope, so an intact original file is
        never overwritten by whatever the link returns later; the later download
        is discarded. Only a missing or unreadable original file is restored, and
        then onto the same record, keeping the original URL as its provenance.
        """
        if not source_url:
            raise ValueError("链接素材必须记录来源地址")
        resolved = Path(path).expanduser()
        if not resolved.is_file() or resolved.stat().st_size == 0:
            raise ValueError("素材文件不存在或为空，未登记。")
        recorded = self._assets.find_asset_by_source_url(workspace_id, source_url)
        if recorded is not None:
            if not _content_readable(
                user_id=verified_owner_user_id, local_diagnostic=local_diagnostic,
                visibility=recorded.visibility, owner_user_id=recorded.owner_user_id,
            ):
                raise KeyError("material asset not found")
            if self._source_files.material_file_intact(recorded):
                # 原记录和原文件都还在：复用它们，这次下载的那份不再需要。
                if Path(recorded.path).resolve() != resolved.resolve():
                    self._discard_redundant_link_file(resolved)
                return recorded
            # 原文件已缺失或读不出来：按既有导入契约把同一条记录恢复回来。
            if Path(recorded.path).resolve() != resolved.resolve():
                self._discard_redundant_link_file(Path(recorded.path))
            stat = resolved.stat()
            recorded.file_name = resolved.name
            recorded.path = str(resolved.resolve())
            recorded.size_bytes = stat.st_size
            recorded.modified_at = datetime.fromtimestamp(stat.st_mtime, tz=utc_now().tzinfo)
            recorded.media_type = media_type
            recorded.updated_at = utc_now()
            self._assets.repoint_asset(recorded)
            return self._apply_media_analysis_status(recorded)
        asset = self._accept_discovered_asset(
            workspace_id, resolved, media_type=media_type, source_url=source_url,
            owner_user_id=verified_owner_user_id,
        )
        return self._apply_media_analysis_status(asset)

    def _apply_media_analysis_status(self, asset: MaterialAsset) -> MaterialAsset:
        """Non-video has no shot/content analysis yet. Say so instead of reporting
        ready or running video analysis on it."""
        if asset.media_type == "video":
            return asset
        asset.analysis_status = "blocked"
        asset.analysis_message = (
            f"这条{MATERIAL_MEDIA_LABEL[asset.media_type]}已入库；"
            "首批只支持视频的镜头与画面分析，内容分析尚未接入。"
        )
        return self._assets.save_asset(asset)


class MaterialLinkProvenanceSink:
    """Record a completed direct-link download on the existing material fact source.

    The imported file becomes a `material_assets` row of the current workspace —
    the same fact source, key and lookup the directory scan already uses. No
    parallel store is introduced.
    """

    def __init__(
        self,
        links: LinkMaterialIntakeUseCase,
        workspace_id: str,
        verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> None:
        self._links = links
        self._workspace_id = workspace_id
        self._owner_user_id = verified_owner_user_id
        self._local_diagnostic = local_diagnostic

    def existing_link_import(self, url: str):
        asset = self._links.existing_link_material(
            self._workspace_id, url, viewer_user_id=self._owner_user_id,
            local_diagnostic=self._local_diagnostic,
        )
        if asset is None:
            return None
        return RegisteredLink(
            url=asset.source_url,
            file_path=asset.path,
            file_name=asset.file_name,
            media_type=asset.media_type,
            size_bytes=asset.size_bytes,
            asset_id=asset.id,
        )

    def record_link_import(self, record) -> str:
        asset = self._links.register_link_material(
            self._workspace_id,
            Path(record.file_path),
            media_type=record.media_type,
            source_url=record.url,
            verified_owner_user_id=self._owner_user_id,
            local_diagnostic=self._local_diagnostic,
        )
        return asset.id
