from __future__ import annotations

import importlib.util
import json
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CalledProcessError
from collections.abc import Callable
from tempfile import TemporaryDirectory

from PIL import Image

from app.asr import (
    ASRTranscriber,
    asr_transcriber_from_settings,
)
from app.config import Settings
from app.draft_artifacts import (
    script_to_srt,
    write_cover,
    write_script_artifacts,
)
from app.draft_index import (
    asset_from_path,
    episode_source_mode,
    episode_summary,
    package_asset_from_path,
    package_dirs,
)
from app.draft_models import (
    DraftAsset,
    DraftEnvironment,
    DraftImportResult,
    DraftPackage,
    DraftToolStatus,
)
from app.draft_templates import build_draft_template_manifest
from app.jianying_template_writer import (
    add_legacy_main_video_segments,
    add_legacy_template_tracks,
)
from app.media_commands import (
    read_media_duration_seconds,
    run_media_command,
)
from app.models import Episode, ScriptPackage
from app.video import SUPPORTED_VIDEO_EXTENSIONS


class DraftWorkspace:
    def __init__(
        self,
        settings: Settings,
        asr_transcriber: ASRTranscriber | None = None,
        runner: Callable[[list[str]], None] | None = None,
        legacy_draft_writer: Callable[[Path, Path, dict], Path] | None = None,
    ) -> None:
        self.video_output_path = Path(settings.video_output_path).expanduser()
        self.draft_output_path = Path(settings.draft_output_path).expanduser()
        self.jianying_root_path = Path(settings.jianying_draft_root_path).expanduser()
        self.asr_transcriber = asr_transcriber or asr_transcriber_from_settings(settings)
        self.runner = runner or run_media_command
        self.legacy_draft_writer = legacy_draft_writer or self._write_legacy_jianying_draft

    def environment(self) -> DraftEnvironment:
        packages = package_dirs(self.draft_output_path)
        draft_tool = self.draft_tool_status()
        jianying_10_7_detected = self._jianying_10_7_detected()
        root_exists = self.jianying_root_path.exists() and self.jianying_root_path.is_dir()
        editable_supported = draft_tool.available and root_exists
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
                draft_tool.available,
            ),
        )

    def processed_videos(self) -> list[DraftAsset]:
        if not self.video_output_path.exists() or not self.video_output_path.is_dir():
            return []
        videos = [
            asset_from_path(path)
            for path in self.video_output_path.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
        ]
        return sorted(videos, key=lambda asset: asset.modified_at, reverse=True)

    def packages(self) -> list[DraftAsset]:
        return [package_asset_from_path(path) for path in package_dirs(self.draft_output_path)]

    def create_package(
        self,
        video_file_name: str | None = None,
        episode: Episode | None = None,
    ) -> DraftPackage:
        video = self._resolve_video(video_file_name)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        package_name = f"AI视频日记_{timestamp}_{Path(video.file_name).stem}"
        package_dir = self.draft_output_path / package_name
        package_dir.mkdir(parents=True, exist_ok=False)
        artifacts = self._write_script_artifacts(package_dir, episode, video)
        template_effects = build_draft_template_manifest(
            episode=episode,
            artifacts=artifacts,
            video_path=video.path,
        )

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "package_name": package_name,
            "source_mode": episode_source_mode(episode),
            "video": {
                **asdict(video),
                "modified_at": video.modified_at.isoformat(),
            },
            "episode": episode_summary(episode),
            "artifacts": artifacts,
            "template_effects": template_effects,
            "draft_tool": asdict(self.draft_tool_status()),
            "jianying_import": {
                "root_path": str(self.jianying_root_path),
                "root_exists": self.jianying_root_path.exists()
                and self.jianying_root_path.is_dir(),
                "jianying_10_7_detected": self._jianying_10_7_detected(),
                "status": "not_attempted",
                "mode": (
                    "external_plaintext_bridge"
                    if self._jianying_10_7_detected()
                    else "legacy_plaintext_draft"
                ),
                "blocker": self._draft_import_blocker(
                    self.jianying_root_path.exists()
                    and self.jianying_root_path.is_dir(),
                    self.draft_tool_status().available,
                ),
            },
            "status": "草稿素材包已生成，尚未写入剪映草稿箱",
            "next_step": "当前包内已包含视频、字幕、封面和清单。点击导入后会生成剪映可登记的兼容草稿。",
        }
        manifest_path = package_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (package_dir / "README.md").write_text(
            "\n".join(
                [
                    "# AI视频日记 剪映草稿素材包",
                    "",
                    f"- 视频文件：`{video.path}`",
                    f"- 字幕文件：`{artifacts['subtitle_path'] or '未生成'}`",
                    f"- 封面图片：`{artifacts['cover_path'] or '未生成'}`",
                    "- 状态：已生成素材包，未写入剪映草稿箱。",
                    "- 说明：导入时会生成剪映 10.7 可登记的兼容明文草稿，并保留本素材包作为 fallback。",
                ]
            ),
            encoding="utf-8",
        )
        return DraftPackage(
            package_name=package_name,
            path=str(package_dir),
            manifest_path=str(manifest_path),
            video_path=video.path,
            subtitle_path=artifacts["subtitle_path"]
            if isinstance(artifacts["subtitle_path"], str)
            else None,
            cover_path=artifacts["cover_path"]
            if isinstance(artifacts["cover_path"], str)
            else None,
            status="草稿素材包已生成，尚未写入剪映草稿箱",
        )

    def create_package_and_import_to_jianying(
        self,
        video_file_name: str | None = None,
        episode: Episode | None = None,
    ) -> DraftPackage:
        package = self.create_package(video_file_name=video_file_name, episode=episode)
        import_result = self.import_package_to_jianying(package.package_name)
        return DraftPackage(
            package_name=package.package_name,
            path=package.path,
            manifest_path=package.manifest_path,
            video_path=package.video_path,
            subtitle_path=package.subtitle_path,
            cover_path=package.cover_path,
            status=f"{package.status}；剪映导入状态：{import_result.status}",
        )

    def draft_tool_status(self) -> DraftToolStatus:
        modules = ["pyJianYingDraft", "jianyingdraft", "capcut_mate"]
        for module in modules:
            if importlib.util.find_spec(module):
                return DraftToolStatus(True, module, modules)
        return DraftToolStatus(False, None, modules)

    def import_package_to_jianying(self, package_name: str | None = None) -> DraftImportResult:
        package_dir = self._resolve_package_dir(package_name)
        manifest_path = package_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        base_result = {
            "root_path": str(self.jianying_root_path),
            "root_exists": self.jianying_root_path.exists()
            and self.jianying_root_path.is_dir(),
            "jianying_10_7_detected": self._jianying_10_7_detected(),
        }

        if not self.jianying_root_path.exists() or not self.jianying_root_path.is_dir():
            result = {
                **base_result,
                "status": "missing_root",
                "draft_path": None,
                "message": "未发现剪映草稿根目录，请先在设置中配置 JIANYING_DRAFT_ROOT_PATH。",
            }
            self._update_manifest_import_result(manifest_path, manifest, result)
            return DraftImportResult(
                package_name=str(manifest["package_name"]),
                status="missing_root",
                message=str(result["message"]),
                draft_path=None,
                manifest_path=str(manifest_path),
            )

        if not self.draft_tool_status().available:
            result = {
                **base_result,
                "status": "missing_tool",
                "draft_path": None,
                "message": "未检测到 pyJianYingDraft/capcut_mate 等草稿工具，无法生成剪映兼容草稿。",
            }
            self._update_manifest_import_result(manifest_path, manifest, result)
            return DraftImportResult(
                package_name=str(manifest["package_name"]),
                status="missing_tool",
                message=str(result["message"]),
                draft_path=None,
                manifest_path=str(manifest_path),
            )

        try:
            draft_path = self.legacy_draft_writer(package_dir, self.jianying_root_path, manifest)
            self._sync_jianying_root_meta(draft_path, wait_seconds=3.0)
        except Exception as exc:
            result = {
                **base_result,
                "status": "failed",
                "draft_path": None,
                "message": f"旧式草稿写入失败：{exc}",
            }
            self._update_manifest_import_result(manifest_path, manifest, result)
            return DraftImportResult(
                package_name=str(manifest["package_name"]),
                status="failed",
                message=str(result["message"]),
                draft_path=None,
                manifest_path=str(manifest_path),
            )

        result = {
            **base_result,
            "status": "imported",
            "draft_path": str(draft_path),
            "message": self._import_success_message(self._jianying_10_7_detected()),
        }
        self._update_manifest_import_result(manifest_path, manifest, result)
        return DraftImportResult(
            package_name=str(manifest["package_name"]),
            status="imported",
            message=str(result["message"]),
            draft_path=str(draft_path),
            manifest_path=str(manifest_path),
        )

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

    def _draft_import_blocker(
        self,
        root_exists: bool,
        draft_tool_available: bool,
    ) -> str | None:
        if not root_exists:
            return "未发现剪映草稿根目录，请先在设置中配置 JIANYING_DRAFT_ROOT_PATH。"
        if not draft_tool_available:
            return "未检测到 pyJianYingDraft/capcut_mate 等草稿工具，暂时只能生成素材包。"
        return None

    def _import_success_message(self, jianying_10_7_detected: bool) -> str:
        if jianying_10_7_detected:
            return (
                "已生成 macOS 剪映兼容草稿并放入草稿目录。剪映 10.7 会通过 "
                "copy_draft_external 登记该草稿；如果剪映已打开，刷新草稿箱即可查看。"
            )
        return "已通过旧式草稿工具写入剪映草稿目录，请打开剪映验证是否可编辑。"

    def _resolve_package_dir(self, package_name: str | None) -> Path:
        packages = package_dirs(self.draft_output_path)
        if not packages:
            raise ValueError("no draft package available")
        if package_name is None:
            return packages[0]
        if Path(package_name).name != package_name:
            raise ValueError("invalid package name")
        for package_dir in packages:
            if package_dir.name == package_name:
                return package_dir
        raise ValueError("draft package not found")

    def _update_manifest_import_result(
        self,
        manifest_path: Path,
        manifest: dict,
        result: dict[str, object],
    ) -> None:
        manifest["jianying_import"] = result
        manifest["status"] = (
            "已写入剪映草稿目录，等待人工验证"
            if result.get("status") == "imported"
            else "草稿导入未完成，保留素材包 fallback"
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_legacy_jianying_draft(
        self,
        package_dir: Path,
        draft_root: Path,
        manifest: dict,
    ) -> Path:
        from pyJianYingDraft import (  # type: ignore
            DraftFolder,
            TrackType,
            VideoMaterial,
            VideoSegment,
            trange,
        )
        import pyJianYingDraft as draft_api  # type: ignore

        video_path = manifest["video"]["path"]
        draft_name = str(manifest["package_name"])
        draft_root.mkdir(parents=True, exist_ok=True)
        target_path = draft_root / draft_name
        if target_path.exists():
            raise FileExistsError(f"draft already exists: {target_path}")

        with TemporaryDirectory(prefix="ai_video_jianying_") as temp_dir:
            staging_root = Path(temp_dir)
            folder = DraftFolder(str(staging_root))
            script = folder.create_draft(
                draft_name,
                1080,
                1920,
                fps=30,
                allow_replace=False,
            )
            try:
                script.add_track(TrackType.video, "主视频")
            except NameError:
                pass
            material = VideoMaterial(video_path)
            duration = self._legacy_video_segment_duration_microseconds(
                Path(video_path),
                material,
            )
            add_legacy_main_video_segments(
                script,
                material,
                duration,
                manifest,
                draft_api,
                trange,
                VideoSegment,
            )
            subtitle_path = manifest.get("artifacts", {}).get("subtitle_path")
            if subtitle_path:
                script.import_srt(subtitle_path, "字幕")
            add_legacy_template_tracks(
                script,
                manifest,
                duration,
                draft_api,
                trange,
            )
            script.save()

            staging_path = staging_root / draft_name
            self._finalize_legacy_draft_files(staging_path, manifest)
            shutil.move(str(staging_path), str(target_path))
        return target_path

    def _legacy_video_segment_duration_microseconds(
        self,
        video_path: Path,
        material: object,
    ) -> int:
        probed_duration = self._video_duration_microseconds(video_path)
        material_duration = getattr(material, "duration", None)
        durations = [
            int(duration)
            for duration in (probed_duration, material_duration)
            if self._is_number(duration) and int(duration) > 0
        ]
        return min(durations) if durations else 0

    def _finalize_legacy_draft_files(self, draft_path: Path, manifest: dict) -> None:
        content_path = draft_path / "draft_content.json"
        if not content_path.exists():
            raise FileNotFoundError(f"draft_content.json not found: {content_path}")

        content = json.loads(content_path.read_text(encoding="utf-8"))
        content["name"] = str(manifest["package_name"])
        content["path"] = str(draft_path)
        timeline_duration = self._draft_timeline_duration(content)
        if timeline_duration is not None:
            content["duration"] = timeline_duration
        platform = content.setdefault("platform", {})
        platform["app_id"] = platform.get("app_id", 3704)
        platform["app_source"] = "lv"
        platform["app_version"] = str(platform.get("app_version") or "5.9.0")
        platform["os"] = "mac"
        content_text = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        content_path.write_text(content_text, encoding="utf-8")
        (draft_path / "draft_info.json").write_text(content_text, encoding="utf-8")

        cover_path = manifest.get("artifacts", {}).get("cover_path")
        if isinstance(cover_path, str) and Path(cover_path).exists():
            with Image.open(cover_path) as cover:
                cover.convert("RGB").save(
                    draft_path / "draft_cover.jpg",
                    "JPEG",
                    quality=92,
                )
        self._finalize_draft_meta_info(draft_path, content)

    def _finalize_draft_meta_info(self, draft_path: Path, draft_info: dict) -> None:
        meta_path = draft_path / "draft_meta_info.json"
        if not meta_path.exists():
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        duration = draft_info.get("duration")
        if self._is_number(duration):
            meta["tm_duration"] = int(duration)
        meta["draft_name"] = draft_path.name
        meta["draft_fold_path"] = str(draft_path)
        meta["draft_json_file"] = str(draft_path / "draft_info.json")
        meta["draft_root_path"] = str(self.jianying_root_path)
        meta["draft_cover"] = str(draft_path / "draft_cover.jpg") if (draft_path / "draft_cover.jpg").exists() else ""
        if draft_info.get("id"):
            meta["draft_id"] = str(draft_info["id"])
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    def _sync_jianying_root_meta(
        self,
        draft_path: Path,
        wait_seconds: float = 0,
    ) -> None:
        deadline = time.monotonic() + max(0, wait_seconds)
        while True:
            if self._try_sync_jianying_root_meta(draft_path):
                return
            if time.monotonic() >= deadline:
                return
            time.sleep(0.2)

    def _try_sync_jianying_root_meta(self, draft_path: Path) -> bool:
        root_meta_path = self.jianying_root_path / "root_meta_info.json"
        draft_info_path = draft_path / "draft_info.json"
        if not root_meta_path.exists() or not draft_info_path.exists():
            return False
        try:
            root_meta = json.loads(root_meta_path.read_text(encoding="utf-8"))
            draft_info = json.loads(draft_info_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        entries = root_meta.get("all_draft_store")
        if not isinstance(entries, list):
            return False
        duration = draft_info.get("duration")
        if not self._is_number(duration):
            duration = self._draft_timeline_duration(draft_info)
        if not self._is_number(duration):
            return False
        draft_name = draft_path.name
        changed = False
        matched = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("draft_fold_path") != str(draft_path) and entry.get("draft_name") != draft_name:
                continue
            matched = True
            entry["tm_duration"] = int(duration)
            entry["draft_json_file"] = str(draft_info_path)
            entry["draft_fold_path"] = str(draft_path)
            entry["draft_root_path"] = str(self.jianying_root_path)
            cover_path = draft_path / "draft_cover.jpg"
            if cover_path.exists():
                entry["draft_cover"] = str(cover_path)
            changed = True
        if not matched:
            entries.append(self._root_meta_entry(draft_path, draft_info, int(duration)))
            changed = True
        if changed:
            root_meta["draft_ids"] = len(entries)
            root_meta["root_path"] = str(self.jianying_root_path)
            root_meta_path.write_text(
                json.dumps(root_meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return changed

    def _root_meta_entry(
        self,
        draft_path: Path,
        draft_info: dict,
        duration: int,
    ) -> dict[str, object]:
        now_us = int(time.time() * 1_000_000)
        draft_id = str(draft_info.get("id") or draft_path.name)
        cover_path = draft_path / "draft_cover.jpg"
        return {
            "cloud_draft_cover": False,
            "cloud_draft_sync": False,
            "draft_cloud_last_action_download": False,
            "draft_cloud_purchase_info": "",
            "draft_cloud_template_id": "",
            "draft_cloud_tutorial_info": "",
            "draft_cloud_videocut_purchase_info": "",
            "draft_cover": str(cover_path) if cover_path.exists() else "",
            "draft_fold_path": str(draft_path),
            "draft_id": draft_id,
            "draft_is_ai_shorts": False,
            "draft_is_cloud_temp_draft": False,
            "draft_is_invisible": False,
            "draft_is_web_article_video": False,
            "draft_json_file": str(draft_path / "draft_info.json"),
            "draft_name": draft_path.name,
            "draft_new_version": str(draft_info.get("new_version") or ""),
            "draft_root_path": str(self.jianying_root_path),
            "draft_timeline_materials_size": self._draft_material_size(draft_info),
            "draft_type": "",
            "draft_web_article_video_enter_from": "",
            "streaming_edit_draft_ready": True,
            "tm_draft_cloud_completed": "",
            "tm_draft_cloud_entry_id": -1,
            "tm_draft_cloud_modified": 0,
            "tm_draft_cloud_parent_entry_id": -1,
            "tm_draft_cloud_space_id": -1,
            "tm_draft_cloud_user_id": -1,
            "tm_draft_create": now_us,
            "tm_draft_modified": now_us,
            "tm_draft_removed": 0,
            "tm_duration": duration,
        }

    def _draft_material_size(self, draft_info: dict) -> int:
        total = 0
        materials = draft_info.get("materials")
        if not isinstance(materials, dict):
            return total
        for group in materials.values():
            if not isinstance(group, list):
                continue
            for material in group:
                if not isinstance(material, dict):
                    continue
                path = material.get("path")
                if isinstance(path, str) and Path(path).exists():
                    total += Path(path).stat().st_size
        return total

    def _video_duration_microseconds(self, video_path: Path) -> int | None:
        try:
            seconds = read_media_duration_seconds(video_path)
        except (OSError, CalledProcessError, ValueError):
            return None
        if seconds <= 0:
            return None
        return int(seconds * 1_000_000)

    def _video_duration_milliseconds(self, video_path: Path) -> int | None:
        duration_us = self._video_duration_microseconds(video_path)
        if duration_us is None:
            return None
        return max(1, duration_us // 1_000)

    def _draft_timeline_duration(self, content: dict) -> int | None:
        max_end = 0
        tracks = content.get("tracks")
        if not isinstance(tracks, list):
            return None
        for track in tracks:
            if not isinstance(track, dict):
                continue
            segments = track.get("segments")
            if not isinstance(segments, list):
                continue
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                timerange = segment.get("target_timerange")
                if not isinstance(timerange, dict):
                    continue
                start = timerange.get("start", 0)
                duration = timerange.get("duration", 0)
                if self._is_number(start) and self._is_number(duration):
                    max_end = max(max_end, int(start + duration))
        return max_end or None

    def _is_number(self, value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def _resolve_video(self, video_file_name: str | None) -> DraftAsset:
        videos = self.processed_videos()
        if not videos:
            raise ValueError("no processed video available")
        if video_file_name is None:
            return videos[0]
        if Path(video_file_name).name != video_file_name:
            raise ValueError("invalid video file name")
        for video in videos:
            if video.file_name == video_file_name:
                return video
        raise ValueError("processed video not found")

    def _write_script_artifacts(
        self,
        package_dir: Path,
        episode: Episode | None,
        video: DraftAsset,
    ) -> dict[str, object]:
        return write_script_artifacts(
            package_dir,
            episode,
            video,
            asr_transcriber=self.asr_transcriber,
            runner=self.runner,
            video_duration_milliseconds=self._video_duration_milliseconds,
        )

    def _script_to_srt(
        self,
        script: ScriptPackage,
        max_duration_ms: int | None = None,
    ) -> str:
        return script_to_srt(script, max_duration_ms=max_duration_ms)

    def _write_cover(self, cover_path: Path, episode: Episode) -> None:
        write_cover(cover_path, episode)
