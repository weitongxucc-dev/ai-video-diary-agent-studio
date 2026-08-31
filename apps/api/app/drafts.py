from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.video import SUPPORTED_VIDEO_EXTENSIONS


@dataclass(frozen=True)
class DraftAsset:
    file_name: str
    path: str
    size_bytes: int
    modified_at: datetime
    import_status: str | None = None
    import_message: str | None = None
    draft_path: str | None = None
    template_tracks: list[str] | None = None


def _safe_template_tracks_from_manifest(manifest: dict) -> list[str] | None:
    """Extract template_tracks from manifest, or None when absent.

    Returns a list of strings from jianying_import.template_tracks,
    or None when the key is missing/not a list — never fabricates.
    """
    jianying_import = manifest.get("jianying_import")
    if not isinstance(jianying_import, dict):
        return None
    raw = jianying_import.get("template_tracks")
    if not isinstance(raw, list):
        return None
    tracks: list[str] = []
    for item in raw:
        if isinstance(item, str):
            tracks.append(item)
    return tracks if tracks else None


@dataclass(frozen=True)
class DraftToolStatus:
    available: bool
    tool_name: str | None
    checked_modules: list[str]


@dataclass(frozen=True)
class DraftEnvironment:
    output_path: str
    output_exists: bool
    processed_video_count: int
    package_count: int
    draft_tool: DraftToolStatus
    jianying_root_path: str
    jianying_root_exists: bool
    jianying_10_7_detected: bool
    editable_draft_supported: bool
    jianying_blocker: str | None


class DraftWorkspace:
    def __init__(self, settings: Settings) -> None:
        self.video_output_path = Path(settings.video_output_path).expanduser()
        self.draft_output_path = Path(settings.draft_output_path).expanduser()
        self.jianying_root_path = Path(settings.jianying_draft_root_path).expanduser()

    def environment(self) -> DraftEnvironment:
        packages = self._package_dirs()
        draft_tool = self.draft_tool_status()
        jianying_10_7_detected = self._jianying_10_7_detected()
        root_exists = self.jianying_root_path.exists() and self.jianying_root_path.is_dir()
        editable_supported = False
        return DraftEnvironment(
            output_path=str(self.draft_output_path),
            output_exists=self.draft_output_path.exists() and self.draft_output_path.is_dir(),
            processed_video_count=len(self.processed_videos()),
            package_count=len(packages),
            draft_tool=draft_tool,
            jianying_root_path=str(self.jianying_root_path),
            jianying_root_exists=root_exists,
            jianying_10_7_detected=jianying_10_7_detected,
            editable_draft_supported=editable_supported,
            jianying_blocker=self._draft_import_blocker(
                root_exists,
            ),
        )

    def processed_videos(self) -> list[DraftAsset]:
        if not self.video_output_path.exists() or not self.video_output_path.is_dir():
            return []
        videos = [
            self._asset_from_path(path)
            for path in self.video_output_path.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
        ]
        return sorted(videos, key=lambda asset: asset.modified_at, reverse=True)

    def packages(self) -> list[DraftAsset]:
        return [self._package_asset_from_path(path) for path in self._package_dirs()]



    def draft_tool_status(self) -> DraftToolStatus:
        modules = ["pyJianYingDraft", "jianyingdraft", "capcut_mate"]
        for module in modules:
            if importlib.util.find_spec(module):
                return DraftToolStatus(True, module, modules)
        return DraftToolStatus(False, None, modules)


    def _jianying_10_7_detected(self) -> bool:
        if not self.jianying_root_path.exists() or not self.jianying_root_path.is_dir():
            return False
        for draft_dir in self.jianying_root_path.iterdir():
            if not draft_dir.is_dir():
                continue
            if (draft_dir / "crypto_key_store.dat").exists() and (
                draft_dir / "draft_info.json"
            ).exists():
                return True
        return False

    def _draft_import_blocker(self, root_exists: bool) -> str | None:
        if not root_exists:
            return "未发现剪映草稿根目录，请先在设置中配置 JIANYING_DRAFT_ROOT_PATH。"
        return "旧草稿写入口已退役；请通过创作任务成片与原生保存继续。"

    def _asset_from_path(self, path: Path) -> DraftAsset:
        stat = path.stat()
        return DraftAsset(
            file_name=path.name,
            path=str(path),
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        )

    def _package_asset_from_path(self, path: Path) -> DraftAsset:
        asset = self._asset_from_path(path)
        manifest_path = path / "manifest.json"
        if not manifest_path.exists():
            return asset
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return asset
        import_result = manifest.get("jianying_import", {})
        if not isinstance(import_result, dict):
            return asset
        return DraftAsset(
            file_name=asset.file_name,
            path=asset.path,
            size_bytes=asset.size_bytes,
            modified_at=asset.modified_at,
            import_status=str(import_result["status"])
            if import_result.get("status")
            else None,
            import_message=str(import_result["message"])
            if import_result.get("message")
            else str(import_result["blocker"])
            if import_result.get("blocker")
            else None,
            draft_path=str(import_result["draft_path"])
            if import_result.get("draft_path")
            else None,
            template_tracks=_safe_template_tracks_from_manifest(manifest),
        )

    def _package_dirs(self) -> list[Path]:
        if not self.draft_output_path.exists() or not self.draft_output_path.is_dir():
            return []
        return sorted(
            [
                path
                for path in self.draft_output_path.iterdir()
                if path.is_dir() and (path / "manifest.json").exists()
            ],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
