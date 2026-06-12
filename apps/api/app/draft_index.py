from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.draft_models import DraftAsset
from app.models import Episode


def asset_from_path(path: Path) -> DraftAsset:
    stat = path.stat()
    return DraftAsset(
        file_name=path.name,
        path=str(path),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
    )


def package_asset_from_path(path: Path) -> DraftAsset:
    asset = asset_from_path(path)
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
        **template_summary(manifest.get("template_effects", {})),
    )


def template_summary(template_effects: object) -> dict[str, object]:
    if not isinstance(template_effects, dict):
        return {}
    tracks = template_effects.get("tracks")
    effects = template_effects.get("effects")
    effect_preview = [
        str(effect["label"])
        for effect in effects
        if isinstance(effect, dict) and effect.get("label")
    ][:10] if isinstance(effects, list) else []
    return {
        "template_id": str(template_effects["template_id"])
        if template_effects.get("template_id")
        else None,
        "template_name": str(template_effects["template_name"])
        if template_effects.get("template_name")
        else None,
        "track_count": len(tracks) if isinstance(tracks, list) else None,
        "effect_count": len(effects) if isinstance(effects, list) else None,
        "track_preview": track_preview(tracks),
        "effect_preview": effect_preview,
    }


def track_preview(tracks: object) -> list[str]:
    if not isinstance(tracks, list):
        return []
    preview: list[str] = []
    for track in tracks[:6]:
        if not isinstance(track, dict):
            continue
        number = track.get("number")
        label = track.get("label")
        if number and label:
            preview.append(f"轨道 {number}：{label}")
    return preview


def package_dirs(draft_output_path: Path) -> list[Path]:
    if not draft_output_path.exists() or not draft_output_path.is_dir():
        return []
    return sorted(
        [
            path
            for path in draft_output_path.iterdir()
            if path.is_dir() and (path / "manifest.json").exists()
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def episode_summary(episode: Episode | None) -> dict[str, object] | None:
    if episode is None:
        return None
    selected_topic = next(
        (topic for topic in episode.topics if topic.id == episode.selected_topic_id),
        None,
    )
    return {
        "id": episode.id,
        "day_number": episode.day_number,
        "episode_date": episode.episode_date.isoformat(),
        "intake_mode": episode_source_mode(episode),
        "selected_topic": selected_topic.title if selected_topic else None,
        "script_ready": episode.script is not None,
        "cover_copy": episode.script.cover_copy if episode.script else None,
        "video_source_file_names": episode.video_source_file_names,
    }


def episode_source_mode(episode: Episode | None) -> str:
    if episode is None:
        return "standalone_video"
    intake_mode = getattr(episode, "intake_mode", "planned")
    return getattr(intake_mode, "value", str(intake_mode))
