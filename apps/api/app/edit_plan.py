from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.models import Episode, VideoJobStatus, utc_now


VALID_SEGMENT_DECISIONS = {"keep", "remove", "review"}


def build_edit_plan_workspace(
    episode: Episode,
    video_output_path: str | Path,
) -> dict[str, object]:
    root = Path(video_output_path).expanduser().resolve()
    jianying = _jianying_state(episode)
    if _video_job_status(episode.video_job_status) == VideoJobStatus.PROCESSING:
        return _empty_workspace(
            episode,
            status="processing",
            message=episode.video_job_message or "视频正在处理中，剪辑计划尚未生成。",
            jianying=jianying,
        )
    if _video_job_status(episode.video_job_status) == VideoJobStatus.FAILED:
        return _empty_workspace(
            episode,
            status="failed",
            message=episode.video_job_error or "视频处理失败，暂时没有可用剪辑计划。",
            jianying=jianying,
        )
    if not episode.video_edit_plan_path:
        return _empty_workspace(
            episode,
            status="missing",
            message="还没有可视化剪辑计划，请先处理视频。",
            jianying=jianying,
        )

    try:
        _, payload = _read_episode_edit_plan(episode, root)
    except (OSError, RuntimeError, TypeError, ValueError):
        return _empty_workspace(
            episode,
            status="missing",
            message="剪辑计划文件不可读取，请重新处理视频。",
            jianying=jianying,
        )

    report = _read_optional_report(episode.video_dedup_report_path, root)
    segments = _segments_from_payload(payload, report)
    output_video = _video_response(
        _string_or_none(payload.get("confirmed_output_video"))
        or _string_or_none(payload.get("output_video"))
        or episode.video_processed_path,
        root,
    )
    source_video = _video_response(_string_or_none(payload.get("source_video")), root)
    duration_seconds = round(
        max((float(segment["end"]) for segment in segments), default=0.0),
        3,
    )
    return {
        "episode_id": episode.id,
        "available": True,
        "status": "ready",
        "message": _ready_message(payload, report, jianying),
        "strategy": _string_or_none(payload.get("strategy")),
        "duration_seconds": duration_seconds,
        "source_files": _source_files(episode, payload),
        "source_video": source_video,
        "output_video": output_video,
        "segments": segments,
        "decisions": _decisions_from_segments(segments),
        "markers": _markers_from_segments(segments),
        "jianying": jianying,
    }


def update_edit_plan_segment_decision(
    episode: Episode,
    video_output_path: str | Path,
    *,
    segment_id: str,
    action: str,
    reason: str | None = None,
) -> dict[str, object]:
    if action not in VALID_SEGMENT_DECISIONS:
        raise ValueError("unsupported edit plan segment decision")
    root = Path(video_output_path).expanduser().resolve()
    edit_plan_path, payload = _read_episode_edit_plan(episode, root)
    _ensure_segment_exists(payload, episode, root, segment_id)
    user_decisions = _user_decisions_payload(payload)
    user_decisions[segment_id] = {
        "action": action,
        "reason": reason,
        "created_at": utc_now().isoformat(),
    }
    payload["user_decisions"] = user_decisions
    _write_edit_plan_payload(edit_plan_path, payload)
    return build_edit_plan_workspace(episode, root)


def revert_edit_plan_segment_decision(
    episode: Episode,
    video_output_path: str | Path,
    *,
    segment_id: str,
) -> dict[str, object]:
    root = Path(video_output_path).expanduser().resolve()
    edit_plan_path, payload = _read_episode_edit_plan(episode, root)
    _ensure_segment_exists(payload, episode, root, segment_id)
    user_decisions = _user_decisions_payload(payload)
    user_decisions.pop(segment_id, None)
    payload["user_decisions"] = user_decisions
    _write_edit_plan_payload(edit_plan_path, payload)
    return build_edit_plan_workspace(episode, root)


def apply_confirmed_edit_plan(
    episode: Episode,
    video_output_path: str | Path,
    *,
    runner: Callable[[list[str]], None] | None = None,
) -> dict[str, str] | None:
    if not episode.video_edit_plan_path:
        return None
    root = Path(video_output_path).expanduser().resolve()
    edit_plan_path, payload = _read_episode_edit_plan(episode, root)
    user_decisions = _user_decisions_payload(payload)
    if not user_decisions:
        return None

    source_video = _string_or_none(payload.get("source_video"))
    if not source_video:
        raise ValueError("edit plan source video is missing")
    source_path = _safe_file(source_video, root)
    report = _read_optional_report(episode.video_dedup_report_path, root)
    segments = _segments_from_payload(payload, report)
    duration = _confirmed_plan_duration(payload, segments)
    removed_ranges = _effective_removed_ranges(segments)
    keep_ranges = _keep_ranges_after_removed_ranges(removed_ranges, duration)
    if not keep_ranges:
        raise ValueError("edit plan decisions would remove the entire video")

    output_path = root / f"{source_path.stem}_confirmed{source_path.suffix}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    (runner or _run_cut_command)(
        _build_cut_command(source_path, output_path, keep_ranges)
    )
    payload["confirmed_output_video"] = str(output_path)
    payload["confirmed_keep_ranges"] = [
        {"start": start, "end": end, "duration": round(end - start, 3)}
        for start, end in keep_ranges
    ]
    payload["confirmed_user_decisions"] = user_decisions
    payload["confirmed_at"] = utc_now().isoformat()
    _write_edit_plan_payload(edit_plan_path, payload)
    return {"file_name": output_path.name, "path": str(output_path)}


def _empty_workspace(
    episode: Episode,
    *,
    status: str,
    message: str,
    jianying: dict[str, object],
) -> dict[str, object]:
    return {
        "episode_id": episode.id,
        "available": False,
        "status": status,
        "message": message,
        "strategy": None,
        "duration_seconds": 0.0,
        "source_files": [*episode.video_source_file_names],
        "source_video": None,
        "output_video": None,
        "segments": [],
        "decisions": [],
        "markers": [],
        "jianying": jianying,
    }


def _segments_from_payload(
    payload: dict[str, Any],
    report: dict[str, Any] | None,
) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    user_decisions = _user_decisions_payload(payload)
    counters = {
        "keep": 0,
        "cut_silence": 0,
        "remove_retake": 0,
        "manual_review": 0,
    }

    for item in _range_list(payload.get("keep_ranges")):
        counters["keep"] += 1
        segment_id = f"keep-{counters['keep']}"
        segments.append(
            _segment(
                segment_id=segment_id,
                label=f"保留片段 {counters['keep']}",
                start=item["start"],
                end=item["end"],
                action="keep",
                status="kept",
                reason="剪辑计划保留的有效口播片段",
                confidence=None,
                source="edit_plan.keep_ranges",
                user_decision=user_decisions.get(segment_id),
            )
        )

    for item in _range_list(payload.get("removed_silences")):
        counters["cut_silence"] += 1
        segment_id = f"silence-{counters['cut_silence']}"
        segments.append(
            _segment(
                segment_id=segment_id,
                label=f"剪掉静音 {counters['cut_silence']}",
                start=item["start"],
                end=item["end"],
                action="cut_silence",
                status="auto_removed",
                reason="长静音保守剪辑",
                confidence=None,
                source="edit_plan.removed_silences",
                user_decision=user_decisions.get(segment_id),
            )
        )

    retake_items = _candidate_list(payload.get("removed_retakes"))
    if report is not None:
        retake_items = _candidate_list(report.get("auto_removed")) or retake_items
    for candidate in retake_items:
        counters["remove_retake"] += 1
        segment_id = f"retake-{counters['remove_retake']}"
        remove_range = candidate["remove"]
        segments.append(
            _segment(
                segment_id=segment_id,
                label=f"剪掉重录 {counters['remove_retake']}",
                start=remove_range["start"],
                end=remove_range["end"],
                action="remove_retake",
                status="auto_removed",
                reason=_retake_reason(str(candidate.get("reason") or "repeated_take")),
                confidence=_float_or_none(candidate.get("confidence")),
                source="retake_report.auto_removed",
                user_decision=user_decisions.get(segment_id),
            )
        )

    review_items = _candidate_list(report.get("review_only")) if report is not None else []
    for candidate in review_items:
        counters["manual_review"] += 1
        segment_id = f"review-{counters['manual_review']}"
        remove_range = candidate["remove"]
        segments.append(
            _segment(
                segment_id=segment_id,
                label=f"待复核 {counters['manual_review']}",
                start=remove_range["start"],
                end=remove_range["end"],
                action="manual_review",
                status="manual_review",
                reason=_retake_reason(str(candidate.get("reason") or "possible_retake")),
                confidence=_float_or_none(candidate.get("confidence")),
                source="retake_report.review_only",
                user_decision=user_decisions.get(segment_id),
            )
        )

    return sorted(segments, key=lambda segment: (float(segment["start"]), segment["id"]))


def _segment(
    *,
    segment_id: str,
    label: str,
    start: float,
    end: float,
    action: str,
    status: str,
    reason: str,
    confidence: float | None,
    source: str,
    user_decision: dict[str, object] | None,
) -> dict[str, object]:
    start = round(max(0.0, start), 3)
    end = round(max(start, end), 3)
    return {
        "id": segment_id,
        "label": label,
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
        "action": action,
        "status": status,
        "reason": reason,
        "confidence": confidence,
        "source": source,
        "user_decision": user_decision,
    }


def _decisions_from_segments(segments: list[dict[str, object]]) -> list[dict[str, object]]:
    priority = {"auto_removed": 0, "manual_review": 1, "kept": 2}
    decisions: list[dict[str, object]] = []
    for segment in sorted(
        segments,
        key=lambda item: (priority[str(item["status"])], float(item["start"])),
    ):
        command = (
            "keep_segment"
            if str(segment["action"]) == "keep"
            else str(segment["action"])
        )
        decisions.append(
            {
                "segment_id": str(segment["id"]),
                "title": _decision_title(segment),
                "command": command,
                "range": f"{_timecode(float(segment['start']))}-{_timecode(float(segment['end']))}",
                "confidence": segment["confidence"],
                "reason": str(segment["reason"]),
            }
        )
    return decisions


def _markers_from_segments(segments: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "segment_id": str(segment["id"]),
            "label": str(segment["label"]),
            "time": _timecode(float(segment["start"])),
            "memo": _marker_memo(segment),
        }
        for segment in sorted(segments, key=lambda item: float(item["start"]))
    ]


def _decision_title(segment: dict[str, object]) -> str:
    action = str(segment["action"])
    if action == "keep":
        return f"保留 {segment['label']}"
    if action == "cut_silence":
        return f"删除 {segment['label']}"
    if action == "remove_retake":
        return f"删除 {segment['label']}"
    return f"人工复核 {segment['label']}"


def _marker_memo(segment: dict[str, object]) -> str:
    status = str(segment["status"])
    if status == "kept":
        return "浏览有效片段"
    if status == "auto_removed":
        return "自动剪辑动作"
    return "需要人工试听"


def _range_list(value: object) -> list[dict[str, float]]:
    if not isinstance(value, list):
        return []
    ranges: list[dict[str, float]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        start = _float_or_none(item.get("start"))
        end = _float_or_none(item.get("end"))
        if start is None or end is None or end <= start:
            continue
        ranges.append({"start": start, "end": end})
    return ranges


def _candidate_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    candidates: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        remove = item.get("remove")
        if not isinstance(remove, dict):
            continue
        start = _float_or_none(remove.get("start"))
        end = _float_or_none(remove.get("end"))
        if start is None or end is None or end <= start:
            continue
        candidates.append(
            {
                **item,
                "remove": {"start": start, "end": end},
            }
        )
    return candidates


def _confirmed_plan_duration(
    payload: dict[str, Any],
    segments: list[dict[str, object]],
) -> float:
    duration = _float_or_none(payload.get("duration_seconds"))
    if duration is not None and duration > 0:
        return round(duration, 3)
    return round(max((float(segment["end"]) for segment in segments), default=0.0), 3)


def _effective_removed_ranges(
    segments: list[dict[str, object]],
) -> list[tuple[float, float]]:
    removed_ranges: list[tuple[float, float]] = []
    for segment in segments:
        decision = segment.get("user_decision")
        action = str(segment["action"])
        status = str(segment["status"])
        decision_action = (
            str(decision.get("action"))
            if isinstance(decision, dict) and decision.get("action")
            else None
        )
        should_remove = status == "auto_removed" and action in {
            "cut_silence",
            "remove_retake",
        }
        if decision_action == "remove":
            should_remove = True
        if decision_action in {"keep", "review"}:
            should_remove = False
        if should_remove:
            removed_ranges.append((float(segment["start"]), float(segment["end"])))
    return _merge_ranges(removed_ranges)


def _merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(ranges):
        start = round(max(0.0, start), 3)
        end = round(max(start, end), 3)
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, round(max(previous_end, end), 3))
    return merged


def _keep_ranges_after_removed_ranges(
    removed_ranges: list[tuple[float, float]],
    duration: float,
) -> list[tuple[float, float]]:
    if duration <= 0:
        raise ValueError("edit plan duration is missing")
    keep_ranges: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in removed_ranges:
        start = max(cursor, min(start, duration))
        end = max(start, min(end, duration))
        if start > cursor:
            keep_ranges.append((round(cursor, 3), round(start, 3)))
        cursor = max(cursor, end)
    if cursor < duration:
        keep_ranges.append((round(cursor, 3), round(duration, 3)))
    return [(start, end) for start, end in keep_ranges if end > start]


def _build_cut_command(
    source_path: Path,
    output_path: Path,
    keep_ranges: list[tuple[float, float]],
) -> list[str]:
    trim_parts: list[str] = []
    concat_inputs: list[str] = []
    for index, (start, end) in enumerate(keep_ranges):
        trim_parts.append(
            f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{index}]"
        )
        trim_parts.append(
            f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{index}]"
        )
        concat_inputs.append(f"[v{index}][a{index}]")
    filter_complex = (
        ";".join(trim_parts)
        + ";"
        + "".join(concat_inputs)
        + f"concat=n={len(keep_ranges)}:v=1:a=1[v][a]"
    )
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        str(output_path),
    ]


def _run_cut_command(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _video_response(path_value: str | None, root: Path) -> dict[str, str] | None:
    if not path_value:
        return None
    try:
        path = _safe_file(path_value, root)
    except (OSError, RuntimeError, ValueError):
        return None
    return {
        "file_name": path.name,
        "path": str(path),
        "url": f"/api/videos/processed/{path.name}",
    }


def _read_episode_edit_plan(
    episode: Episode,
    root: Path,
) -> tuple[Path, dict[str, Any]]:
    if not episode.video_edit_plan_path:
        raise ValueError("episode has no edit plan")
    edit_plan_path = _safe_file(episode.video_edit_plan_path, root)
    payload = json.loads(edit_plan_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("edit plan payload must be an object")
    return edit_plan_path, payload


def _write_edit_plan_payload(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _ensure_segment_exists(
    payload: dict[str, Any],
    episode: Episode,
    root: Path,
    segment_id: str,
) -> None:
    report = _read_optional_report(episode.video_dedup_report_path, root)
    segment_ids = {
        str(segment["id"])
        for segment in _segments_from_payload(payload, report)
    }
    if segment_id not in segment_ids:
        raise KeyError("edit plan segment not found")


def _user_decisions_payload(payload: dict[str, Any]) -> dict[str, dict[str, object]]:
    raw_decisions = payload.get("user_decisions")
    if not isinstance(raw_decisions, dict):
        return {}
    decisions: dict[str, dict[str, object]] = {}
    for segment_id, value in raw_decisions.items():
        if not isinstance(segment_id, str) or not isinstance(value, dict):
            continue
        action = value.get("action")
        created_at = value.get("created_at")
        if action not in VALID_SEGMENT_DECISIONS or not isinstance(created_at, str):
            continue
        reason = value.get("reason")
        decisions[segment_id] = {
            "action": action,
            "reason": reason if isinstance(reason, str) else None,
            "created_at": created_at,
        }
    return decisions


def _safe_file(path_value: str | Path, root: Path) -> Path:
    path = Path(path_value).expanduser().resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("file is outside the video output directory")
    return path


def safe_processed_video_path(file_name: str, video_output_path: str | Path) -> Path:
    if Path(file_name).name != file_name:
        raise ValueError("invalid processed video file name")
    root = Path(video_output_path).expanduser().resolve()
    return _safe_file(root / file_name, root)


def _read_optional_report(path_value: str | None, root: Path) -> dict[str, Any] | None:
    if not path_value:
        return None
    try:
        path = _safe_file(path_value, root)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _ready_message(
    payload: dict[str, Any],
    report: dict[str, Any] | None,
    jianying: dict[str, object],
) -> str:
    if jianying["status"] == "draft_ready":
        return "剪辑计划已生成，剪映草稿导入流程已完成。"
    if report and report.get("status") == "applied":
        return "剪辑计划已生成，重复录制已自动去除，等待确认写入剪映草稿。"
    if payload.get("removed_silences"):
        return "剪辑计划已生成，长静音已按保守策略处理，等待确认写入剪映草稿。"
    return "剪辑计划已生成，等待确认写入剪映草稿。"


def _source_files(episode: Episode, payload: dict[str, Any]) -> list[str]:
    source_files = payload.get("source_files")
    if isinstance(source_files, list):
        return [str(item) for item in source_files if str(item)]
    if episode.video_source_file_names:
        return [*episode.video_source_file_names]
    if episode.video_source_file_name:
        return [episode.video_source_file_name]
    return []


def _jianying_state(episode: Episode) -> dict[str, object]:
    if episode.draft_package_name:
        return {
            "status": "draft_ready",
            "package_name": episode.draft_package_name,
            "package_path": episode.draft_package_path,
            "auto_imported": True,
            "message": "已生成剪映素材包，并已自动尝试导入剪映草稿箱。",
        }
    if episode.video_processed_file_name:
        return {
            "status": "waiting_confirmation",
            "package_name": None,
            "package_path": None,
            "auto_imported": False,
            "message": "剪辑计划可复核，确认后会生成并导入剪映草稿。",
        }
    return {
        "status": "not_ready",
        "package_name": None,
        "package_path": None,
        "auto_imported": False,
        "message": "还没有处理后视频，无法写入剪映草稿。",
    }


def _retake_reason(reason: str) -> str:
    labels = {
        "interrupted_take": "中断后重录，保留更完整表达",
        "possible_retake": "疑似重复口播，请人工确认",
        "repeated_take": "重复口播，保留后一次表达",
    }
    return labels.get(reason, reason)


def _timecode(seconds: float) -> str:
    safe_seconds = max(0.0, seconds)
    minutes = int(safe_seconds // 60)
    remaining = safe_seconds % 60
    return f"{minutes:02d}:{remaining:04.1f}"


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _video_job_status(value: object) -> VideoJobStatus:
    try:
        return value if isinstance(value, VideoJobStatus) else VideoJobStatus(str(value))
    except ValueError:
        return VideoJobStatus.IDLE
