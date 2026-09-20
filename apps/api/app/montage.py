from __future__ import annotations

import json
import math
import re
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from collections.abc import Callable
from dataclasses import replace, asdict, dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

from app.audio_timeline import AudioTimeline, FINITE_LOUDNESS_FILTER
from app.canvas import AspectRatio, canvas_size
from app.visual_effects import clip_video_filters
from app.transitions import extend_dissolve_sources, join_picture_filters
from app.graphic_overlays import validate_overlays, write_text_overlay
from app.subtitle_schedule import subtitle_schedule, schedule_srt, narration_schedule
from app.material_speech import speech_text_for_range
from app.models import Episode, EpisodeStatus, ScriptPackage, utc_now
from app.production import (
    CreationTaskScript,
    MaterialAsset,
    MaterialSegment,
    ProductionBatch,
    ProductionProfile,
    effective_segment_usage_role,
)
from app.video import SUPPORTED_VIDEO_EXTENSIONS
from app.visual_semantics import VisualSemanticError, VisualSemanticProvider

MontagePlanStatus = Literal[
    "waiting_for_user",
    "ready",
    "rendering",
    "rendered",
    "failed",
]


class MontageRenderError(OSError):
    """A failed ffmpeg/ffprobe boundary with actionable process evidence."""

    def __init__(self, *, tool: str, exit_code: int, stderr: str) -> None:
        self.tool = tool
        self.exit_code = exit_code
        self.stderr = stderr.strip() or "(no stderr produced)"
        super().__init__(
            f"{tool} exited with code {exit_code}: {self.stderr}"
        )


@dataclass
class NarrationCue:
    id: str
    source_section_id: str
    text: str
    duration_seconds: float
    narration_segment_id: str = ""
    subtitle_emphasis: list[str] = field(default_factory=list)
    subtitle_style: dict[str, object] = field(default_factory=dict)
    subtitle_emphasis_ranges: list[tuple[int, int]] | None = None


@dataclass
class MontageClip:
    id: str
    cue_id: str
    asset_id: str
    segment_id: str
    source_path: str
    source_start_seconds: float
    source_end_seconds: float
    timeline_start_seconds: float
    timeline_end_seconds: float
    match_score: float
    match_reasons: list[str]
    needs_confirmation: bool
    role: Literal["a_roll", "b_roll"] = "b_roll"
    locked: bool = False
    manually_selected: bool = False
    person_render_id: str | None = None
    fit_mode: Literal["fill", "fit"] = "fill"
    crop_x: float = 0.5
    crop_y: float = 0.5
    visual_effects: dict[str, float] = field(default_factory=dict)
    transition_in_seconds: float = 0
    fade_envelope_rate: float = 1
    fade_envelope_offset_frames: float = 0
    fade_envelope_duration_frames: float | None = None


@dataclass
class MontagePlan:
    id: str
    workspace_id: str
    batch_id: str
    episode_id: str
    script_title: str
    status: MontagePlanStatus
    cues: list[NarrationCue]
    clips: list[MontageClip]
    duration_seconds: float
    low_confidence_clip_count: int
    narration_audio_path: str | None = None
    output_path: str | None = None
    subtitle_path: str | None = None
    message: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class CreationTaskMontagePlan:
    id: str
    workspace_id: str
    task_id: str
    script_id: str
    script_title: str
    status: MontagePlanStatus
    cues: list[NarrationCue]
    clips: list[MontageClip]
    duration_seconds: float
    low_confidence_clip_count: int
    aspect_ratio: AspectRatio = "9:16"
    narration_audio_path: str | None = None
    output_path: str | None = None
    subtitle_path: str | None = None
    message: str = ""
    graphic_overlays: list[dict] = field(default_factory=list)
    audio_timeline: AudioTimeline | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


class MontageRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS production_montage_plans (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_montage_workspace_batch
                    ON production_montage_plans(workspace_id, batch_id, updated_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, workspace_id: str, plan_id: str) -> MontagePlan:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM production_montage_plans
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, plan_id),
            ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        return _plan_from_payload(json.loads(str(row["payload"])))

    def list_for_batch(self, workspace_id: str, batch_id: str) -> list[MontagePlan]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM production_montage_plans
                WHERE workspace_id = ? AND batch_id = ?
                ORDER BY updated_at DESC
                """,
                (workspace_id, batch_id),
            ).fetchall()
        return [_plan_from_payload(json.loads(str(row["payload"]))) for row in rows]

    def recent(self, workspace_id: str, limit: int = 12) -> list[MontagePlan]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM production_montage_plans
                WHERE workspace_id = ? ORDER BY updated_at DESC LIMIT ?
                """,
                (workspace_id, max(1, min(limit, 50))),
            ).fetchall()
        return [_plan_from_payload(json.loads(str(row["payload"]))) for row in rows]


class MontagePlanner:
    """Build a traceable script-to-shot plan from approved material metadata.

    This planner is deliberately conservative: filename, transcript and human or
    model supplied visual descriptions are evidence. A duration-only fallback is
    retained for review, but never marked ready for unattended rendering.
    """

    def __init__(
        self,
        semantic_provider: VisualSemanticProvider | None = None,
        visual_feature_loader: Callable[[str, str, str], list[float] | None]
        | None = None,
        semantic_auto_confirm_threshold: float | None = None,
    ) -> None:
        self.semantic_provider = semantic_provider
        self.visual_feature_loader = visual_feature_loader
        # Automatic acceptance stays disabled until a real-material benchmark
        # establishes a product-specific threshold. Vectors may rank clips now,
        # but semantic-only matches still require human confirmation.
        self.semantic_auto_confirm_threshold = semantic_auto_confirm_threshold

    def build(
        self,
        *,
        workspace_id: str,
        batch: ProductionBatch,
        episode: Episode,
        profile: ProductionProfile,
        assets: list[MaterialAsset],
        narration_audio_path: str | None = None,
        narration_duration_seconds: float | None = None,
    ) -> MontagePlan:
        if batch.workspace_id != workspace_id or episode.workspace_id != workspace_id:
            raise ValueError("批次或 Episode 不属于当前工作区")
        if batch.status == "blocked":
            raise ValueError("生产批次仍被基础配置阻断")
        if episode.script is None or episode.status not in _SCRIPT_APPROVED_STATUSES:
            raise ValueError("只有已确认文案的 Episode 才能生成混剪计划")

        b_roll_candidates = [
            (asset, segment)
            for asset in assets
            if asset.workspace_id == workspace_id
            and asset.review_status == "approved"
            and asset.rights_confirmed
            and asset.brand_safe
            for segment in asset.segments
            if segment.review_status == "approved"
            and effective_segment_usage_role(asset, segment) == "b_roll"
            and segment.end_seconds - segment.start_seconds >= 0.75
        ]
        if not b_roll_candidates:
            raise ValueError("可用素材库中没有已审核的 B-roll 镜头")
        a_roll_candidates = [
            (asset, segment)
            for asset in assets
            if asset.workspace_id == workspace_id
            and asset.review_status == "approved"
            and asset.rights_confirmed
            and asset.brand_safe
            for segment in asset.segments
            if segment.review_status == "approved"
            and effective_segment_usage_role(asset, segment) == "a_roll"
            and segment.end_seconds - segment.start_seconds >= 0.75
        ]

        cues = _build_cues(
            episode.script,
            min_seconds=profile.target_duration_min_seconds,
            max_seconds=profile.target_duration_max_seconds,
            narration_duration_seconds=narration_duration_seconds,
        )
        cue_vectors, segment_vectors = self._semantic_vectors(
            workspace_id,
            cues,
            b_roll_candidates,
        )
        clips: list[MontageClip] = []
        segment_usage: dict[str, int] = {}
        asset_usage: dict[str, int] = {}
        timeline_cursor = 0.0

        for cue_index, cue in enumerate(cues):
            use_a_roll = bool(a_roll_candidates) and (
                cue_index == 0 or cue_index == len(cues) - 1
            )
            if use_a_roll:
                duration_candidates = [
                    item
                    for item in a_roll_candidates
                    if item[1].end_seconds - item[1].start_seconds >= cue.duration_seconds
                ]
                if not duration_candidates:
                    raise ValueError("A-roll 镜头时长不足，无法完整覆盖对应旁白")
                asset, segment = min(
                    duration_candidates,
                    key=lambda item: (
                        asset_usage.get(item[0].id, 0),
                        segment_usage.get(item[1].id, 0),
                        item[1].start_seconds,
                    ),
                )
                score = 1.0
                lexical_score = 1.0
                visual_score = None
                reasons = [
                    "人物主镜头作为叙事锚点",
                    "片头或片尾保留人物出镜",
                ]
                role: Literal["a_roll", "b_roll"] = "a_roll"
            else:
                duration_candidates = [
                    item
                    for item in b_roll_candidates
                    if item[1].end_seconds - item[1].start_seconds >= cue.duration_seconds
                ]
                if not duration_candidates:
                    raise ValueError("B-roll 镜头时长不足，无法完整覆盖对应旁白")
                ranked = sorted(
                    (
                        _score_candidate(
                            cue,
                            asset,
                            segment,
                            segment_usage.get(segment.id, 0),
                            asset_usage.get(asset.id, 0),
                            visual_score=_cosine_similarity(
                                cue_vectors.get(cue.id),
                                segment_vectors.get(segment.id),
                            ),
                        )
                        for asset, segment in duration_candidates
                    ),
                    key=lambda item: item[0],
                    reverse=True,
                )
                score, lexical_score, visual_score, asset, segment, reasons = ranked[0]
                role = "b_roll"
            clip_duration = round(cue.duration_seconds, 3)
            semantic_confirmed = (
                visual_score is not None
                and self.semantic_auto_confirm_threshold is not None
                and visual_score >= self.semantic_auto_confirm_threshold
            )
            needs_confirmation = (
                role == "b_roll"
                and lexical_score < 0.12
                and not semantic_confirmed
            )
            clips.append(
                MontageClip(
                    id=f"clip_{uuid4().hex[:10]}",
                    cue_id=cue.id,
                    asset_id=asset.id,
                    segment_id=segment.id,
                    source_path=asset.path,
                    source_start_seconds=segment.start_seconds,
                    source_end_seconds=round(segment.start_seconds + clip_duration, 3),
                    timeline_start_seconds=round(timeline_cursor, 3),
                    timeline_end_seconds=round(timeline_cursor + clip_duration, 3),
                    match_score=round(score, 3),
                    match_reasons=reasons,
                    needs_confirmation=needs_confirmation,
                    role=role,
                )
            )
            timeline_cursor += clip_duration
            segment_usage[segment.id] = segment_usage.get(segment.id, 0) + 1
            asset_usage[asset.id] = asset_usage.get(asset.id, 0) + 1

        low_confidence_count = sum(clip.needs_confirmation for clip in clips)
        status: MontagePlanStatus = "ready" if low_confidence_count == 0 else "waiting_for_user"
        return MontagePlan(
            id=f"montage_{uuid4().hex[:10]}",
            workspace_id=workspace_id,
            batch_id=batch.id,
            episode_id=episode.id,
            script_title=(episode.script.title_options[0] if episode.script.title_options else "未命名成片"),
            status=status,
            cues=cues,
            clips=clips,
            duration_seconds=round(timeline_cursor, 3),
            low_confidence_clip_count=low_confidence_count,
            narration_audio_path=narration_audio_path,
            message=(
                "镜头均有文案或画面语义依据，可以进入渲染。"
                if status == "ready"
                else f"有 {low_confidence_count} 个低置信镜头需要确认或补充画面描述。"
            ),
        )

    def build_for_creation_task(
        self,
        *,
        workspace_id: str,
        task_id: str,
        script: CreationTaskScript,
        profile: ProductionProfile,
        assets: list[MaterialAsset],
        media_reference_for_asset: Callable[[str], str],
        narration_audio_path: str,
        narration_duration_seconds: float,
        narration_segment_ids_by_section: dict[str, str] | None = None,
        narration_segment_durations_by_section: dict[str, float] | None = None,
    ) -> CreationTaskMontagePlan:
        if script.workspace_id != workspace_id or script.task_id != task_id:
            raise ValueError("文案不属于当前创作任务")
        if script.selection_status != "confirmed":
            raise ValueError("只有已确认文案才能生成剪辑计划")
        if not narration_audio_path.startswith("narration://artifact/"):
            raise ValueError("剪辑计划必须引用已持久化的创作任务配音")
        b_roll_candidates = [
            (asset, segment)
            for asset in assets
            if asset.workspace_id == workspace_id
            and asset.review_status == "approved"
            and asset.rights_confirmed
            and asset.brand_safe
            for segment in asset.segments
            if segment.review_status == "approved"
            and effective_segment_usage_role(asset, segment) == "b_roll"
            and segment.end_seconds - segment.start_seconds >= 0.75
        ]
        if not b_roll_candidates:
            raise ValueError("可用素材库中没有已审核的 B-roll 镜头")
        cues = _build_creation_task_cues(
            script,
            min_seconds=profile.target_duration_min_seconds,
            max_seconds=profile.target_duration_max_seconds,
            narration_duration_seconds=narration_duration_seconds,
            narration_segment_ids_by_section=narration_segment_ids_by_section,
            narration_segment_durations_by_section=narration_segment_durations_by_section,
        )
        # Use the confirmed visual requirement for retrieval; narration remains
        # unchanged for audio and subtitles. These projections are never persisted.
        visual_requirements = {section.id: section.visual_prompt.strip() for section in script.sections}
        retrieval_cues = {
            cue.id: replace(cue, text=visual_requirements.get(cue.source_section_id) or cue.text)
            for cue in cues
        }
        cue_vectors, segment_vectors = self._semantic_vectors(
            workspace_id, list(retrieval_cues.values()), b_roll_candidates
        )
        clips: list[MontageClip] = []
        segment_usage: dict[str, int] = {}
        asset_usage: dict[str, int] = {}
        timeline_cursor = 0.0
        for cue in cues:
            # Source talking-head footage is not aligned with this generated narration.
            # Person renders are inserted explicitly with their narration dependency.
            duration_candidates = [
                item for item in b_roll_candidates
                if item[1].end_seconds - item[1].start_seconds >= cue.duration_seconds
            ]
            if not duration_candidates:
                raise ValueError("B-roll 镜头时长不足，无法完整覆盖对应旁白")
            ranked = sorted(
                (_score_candidate(
                    retrieval_cues[cue.id], asset, segment,
                    segment_usage.get(segment.id, 0), asset_usage.get(asset.id, 0),
                    visual_score=_cosine_similarity(cue_vectors.get(cue.id), segment_vectors.get(segment.id)),
                    visual_only=True,
                ) for asset, segment in duration_candidates),
                key=lambda item: item[0], reverse=True,
            )
            score, lexical_score, visual_score, asset, segment, reasons = ranked[0]
            reasons.insert(0, f"画面要求：{retrieval_cues[cue.id].text}")
            observation = segment.understanding
            if observation and observation.status == "ready":
                actions = [fact for fact in observation.facts if fact["kind"] == "action"
                    and len({time for time in fact["sample_times"]
                        if segment.start_seconds <= time < segment.start_seconds + cue.duration_seconds}) >= 2]
                reasons.extend(
                    f"局部动作观察：{fact['text']}（原片 "
                    + "、".join(f"{time:.2f}秒" for time in fact["sample_times"]
                        if segment.start_seconds <= time < segment.start_seconds + cue.duration_seconds)
                    + "）" for fact in actions)
                if not actions:
                    reasons.append("选取区间缺少多帧动作证据；物体相似不代表动作符合")
                reasons.extend(f"画面未确认：{item}" for item in observation.unknowns)
            else:
                reasons.append("缺少本地多帧画面观察；尚未核验动作要求")
            role: Literal["a_roll", "b_roll"] = "b_roll"
            clip_duration = round(cue.duration_seconds, 3)
            semantic_confirmed = (
                visual_score is not None
                and self.semantic_auto_confirm_threshold is not None
                and visual_score >= self.semantic_auto_confirm_threshold
            )
            needs_confirmation = (
                role == "b_roll" and lexical_score < 0.12 and not semantic_confirmed
            )
            clips.append(
                MontageClip(
                    id=f"clip_{uuid4().hex[:10]}",
                    cue_id=cue.id,
                    asset_id=asset.id,
                    segment_id=segment.id,
                    source_path=media_reference_for_asset(asset.id),
                    source_start_seconds=segment.start_seconds,
                    source_end_seconds=round(segment.start_seconds + clip_duration, 3),
                    timeline_start_seconds=round(timeline_cursor, 3),
                    timeline_end_seconds=round(timeline_cursor + clip_duration, 3),
                    match_score=round(score, 3),
                    match_reasons=reasons,
                    needs_confirmation=needs_confirmation,
                    role=role,
                )
            )
            timeline_cursor += clip_duration
            segment_usage[segment.id] = segment_usage.get(segment.id, 0) + 1
            asset_usage[asset.id] = asset_usage.get(asset.id, 0) + 1
        low_confidence_count = sum(clip.needs_confirmation for clip in clips)
        status: MontagePlanStatus = (
            "ready" if low_confidence_count == 0 else "waiting_for_user"
        )
        return CreationTaskMontagePlan(
            id=f"task_montage_{uuid4().hex[:10]}",
            workspace_id=workspace_id,
            task_id=task_id,
            script_id=script.id,
            script_title=script.title,
            status=status,
            cues=cues,
            clips=clips,
            duration_seconds=round(timeline_cursor, 3),
            low_confidence_clip_count=low_confidence_count,
            narration_audio_path=narration_audio_path,
            message=(
                "镜头均有文案或画面语义依据，可以进入渲染。"
                if status == "ready"
                else f"有 {low_confidence_count} 个低置信镜头需要确认或替换。"
            ),
        )

    def _semantic_vectors(
        self,
        workspace_id: str,
        cues: list[NarrationCue],
        candidates: list[tuple[MaterialAsset, MaterialSegment]],
    ) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
        provider = self.semantic_provider
        loader = self.visual_feature_loader
        if provider is None or loader is None or not provider.ready:
            return {}, {}
        try:
            text_vectors = provider.embed_texts([cue.text for cue in cues])
        except (OSError, ValueError, VisualSemanticError):
            return {}, {}
        if len(text_vectors) != len(cues):
            return {}, {}
        cue_vectors = {
            cue.id: vector for cue, vector in zip(cues, text_vectors, strict=True)
        }
        segment_vectors: dict[str, list[float]] = {}
        for _asset, segment in candidates:
            vector = loader(workspace_id, segment.id, provider.model_id)
            if vector:
                segment_vectors[segment.id] = vector
        return cue_vectors, segment_vectors


class MontageRenderer:
    def __init__(
        self,
        output_path: str | Path,
        runner: Callable[[list[str]], None] | None = None,
        duration_reader: Callable[[Path], float] | None = None,
    ) -> None:
        self.output_path = Path(output_path).expanduser()
        self.runner = runner or self._run
        self.duration_reader = duration_reader or _probe_duration

    def audio_duration(self, audio_path: str | Path) -> float:
        path = self._validated_audio_path(audio_path)
        duration = float(self.duration_reader(path))
        if duration <= 0:
            raise ValueError("旁白音频时长无效")
        return duration

    def render(
        self,
        plan: MontagePlan | CreationTaskMontagePlan,
        audio_path: str | Path,
        *,
        job_staging_path: str | Path | None = None,
        subtitle_tracks=None,
    ) -> MontagePlan | CreationTaskMontagePlan:
        if plan.status != "ready":
            raise ValueError("混剪计划仍有待确认镜头，不能渲染")
        if not plan.clips:
            raise ValueError("混剪计划没有镜头")
        narration_path = self._validated_audio_path(audio_path)
        if isinstance(plan, CreationTaskMontagePlan):
            narration_duration = self.audio_duration(narration_path)
            # Allow encoder padding, but never let -shortest silently truncate a changed voice track.
            if abs(narration_duration - plan.duration_seconds) > 0.25:
                raise ValueError("配音与剪辑时长不一致，请按当前配音重新生成故事板后渲染。")
            if job_staging_path is None:
                raise ValueError("CreationTask 渲染必须提供 job-scoped 暂存目录")
            render_root = Path(job_staging_path).expanduser().resolve()
            render_root.mkdir(parents=True, exist_ok=True)
            output_path = render_root / "preview.mp4"
            subtitle_path = render_root / "subtitles.srt"
            output_path.unlink(missing_ok=True)
            subtitle_path.unlink(missing_ok=True)
            plan.output_path = None
            plan.subtitle_path = None
        else:
            if job_staging_path is not None:
                raise ValueError("Legacy Batch/Episode 渲染不接受 CreationTask 暂存目录")
            self.output_path.mkdir(parents=True, exist_ok=True)
            output_path = self.output_path / f"{plan.batch_id}_{plan.episode_id}_preview.mp4"
            subtitle_path = self.output_path / f"{plan.batch_id}_{plan.episode_id}.srt"
        schedule = (subtitle_schedule(subtitle_tracks, plan.duration_seconds) if subtitle_tracks is not None
                    else narration_schedule(plan.cues, plan.duration_seconds) if isinstance(plan, CreationTaskMontagePlan)
                    else None)
        sidecar_schedule = schedule
        if subtitle_tracks and schedule == []:
            sidecar_schedule = subtitle_schedule([replace(track, style={**track.style, "enabled": True}) for track in subtitle_tracks], plan.duration_seconds)
        subtitle_path.write_text(schedule_srt(sidecar_schedule) if sidecar_schedule is not None else _plan_to_srt(plan), encoding="utf-8")

        plan.status = "rendering"
        plan.narration_audio_path = str(narration_path)
        try:
            with TemporaryDirectory(
                prefix=".subtitle-overlays-",
                dir=output_path.parent,
            ) as overlay_dir:
                if schedule is None:
                    subtitle_overlays = _write_subtitle_overlays(plan,Path(overlay_dir))
                else:
                    subtitle_overlays=[]
                    for index,cue in enumerate(schedule):
                        path=Path(overlay_dir)/f"subtitle-{index:03}.png"
                        _write_subtitle_image(cue,path, size=canvas_size(plan.aspect_ratio) if isinstance(plan, CreationTaskMontagePlan) else (1080,1920))
                        subtitle_overlays.append(path)
                intervals=([(cue.start_seconds,cue.end_seconds) for cue in schedule] if schedule is not None else
                    [(clip.timeline_start_seconds,clip.timeline_end_seconds) for clip in plan.clips])
                if isinstance(plan, CreationTaskMontagePlan):
                    graphics=validate_overlays(plan.graphic_overlays,plan.duration_seconds)
                    graphic_paths=[]
                    for index,graphic in enumerate(graphics):
                        path=Path(overlay_dir)/f"graphic-{index}.png"
                        write_text_overlay(graphic.model_dump(),path,canvas_size(plan.aspect_ratio))
                        graphic_paths.append(path)
                    subtitle_overlays=graphic_paths+subtitle_overlays
                    intervals=[(item.start_seconds,item.end_seconds) for item in graphics]+intervals
                self.runner(
                    self.build_command(
                        plan,
                        narration_path,
                        output_path,
                        subtitle_overlays,
                        subtitle_intervals=intervals,
                    )
                )
            _validate_rendered_media(output_path, expected_size=canvas_size(plan.aspect_ratio) if isinstance(plan, CreationTaskMontagePlan) else (1080,1920))
        except subprocess.CalledProcessError as exc:
            render_error = _render_error_from_process(exc)
            plan.status = "failed"
            plan.message = f"FFmpeg 渲染失败：{render_error}"
            raise render_error from exc
        except (OSError, subprocess.SubprocessError) as exc:
            plan.status = "failed"
            plan.message = f"FFmpeg 渲染失败：{exc}"
            raise
        plan.status = "rendered"
        if isinstance(plan, CreationTaskMontagePlan):
            plan.message = "MP4 与 SRT 已通过校验并写入任务暂存区，等待原子发布。"
        else:
            plan.output_path = str(output_path)
            plan.subtitle_path = str(subtitle_path)
            plan.message = "带中文字幕的 9:16 初版成片和独立 SRT 已生成，等待检查。"
        return plan

    def build_command(
        self,
        plan: MontagePlan | CreationTaskMontagePlan,
        audio_path: Path,
        output_path: Path,
        subtitle_overlays: list[Path],
        subtitle_intervals: list[tuple[float,float]] | None = None,
    ) -> list[str]:
        width,height = canvas_size(plan.aspect_ratio) if isinstance(plan, CreationTaskMontagePlan) else (1080,1920)
        command = ["ffmpeg", "-y"]
        filters: list[str] = []
        labels: list[str] = []
        render_clips, transitions = extend_dissolve_sources(plan.clips)
        for index, clip in enumerate(render_clips):
            source = Path(clip.source_path).expanduser().resolve()
            if not source.is_file() or source.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
                raise ValueError(f"混剪镜头文件不存在或格式不支持：{source.name}")
            duration = clip.source_end_seconds - clip.source_start_seconds
            command.extend(
                ["-ss", f"{clip.source_start_seconds:.3f}", "-t", f"{duration:.3f}", "-i", str(source)]
            )
            filters.append(f"[{index}:v]{clip_video_filters(clip, width=width, height=height)}[v{index}]")
            labels.append(f"[v{index}]")

        audio_index = len(plan.clips)
        command.extend(["-i", str(audio_path)])
        output_label = "[v0]"
        if any(transitions):
            output_label = "[vbase]"
            filters.extend(join_picture_filters(plan.clips, transitions))
        elif len(labels) > 1:
            output_label = "[vbase]"
            filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vbase]")
        for overlay_path in subtitle_overlays:
            command.extend(["-loop", "1", "-framerate", "30", "-i", str(overlay_path)])
        intervals=subtitle_intervals if subtitle_intervals is not None else [(clip.timeline_start_seconds,clip.timeline_end_seconds) for clip in plan.clips]
        for index, ((start,end), _overlay_path) in enumerate(zip(intervals,subtitle_overlays,strict=True)):
            overlay_input = audio_index + 1 + index
            next_label = f"[vsub{index}]"
            filters.append(
                f"{output_label}[{overlay_input}:v]overlay=0:0:shortest=1:"
                f"enable='gte(t,{start:.6f})*lt(t,{end:.6f})'{next_label}"
            )
            output_label = next_label
        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                output_label,
                "-map",
                f"{audio_index}:a:0",
                "-af", ",".join([
                    *([] if isinstance(plan, CreationTaskMontagePlan) and plan.audio_timeline is not None else ["loudnorm", FINITE_LOUDNESS_FILTER]),
                    f"apad=whole_dur={sum(round(clip.timeline_end_seconds*30)-round(clip.timeline_start_seconds*30) for clip in plan.clips)/30:.10f}",
                ]),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        return command

    def _validated_audio_path(self, raw_path: str | Path) -> Path:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in {".wav", ".mp3", ".m4a", ".aac", ".flac"}:
            raise ValueError("旁白音频不存在或格式不支持")
        return path

    @staticmethod
    def _run(command: list[str]) -> None:
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode:
            raise MontageRenderError(
                tool="ffmpeg",
                exit_code=completed.returncode,
                stderr=completed.stderr,
            )


_SCRIPT_APPROVED_STATUSES = {
    EpisodeStatus.SCRIPT_APPROVED,
    EpisodeStatus.WAITING_VIDEO,
    EpisodeStatus.VIDEO_PROCESSING,
    EpisodeStatus.DRAFT_READY,
    EpisodeStatus.PUBLISHED,
}


def _build_cues(
    script: ScriptPackage,
    *,
    min_seconds: int,
    max_seconds: int,
    narration_duration_seconds: float | None,
) -> list[NarrationCue]:
    entries: list[tuple[str, str, float]] = []
    if script.hook.strip():
        entries.append(("hook", script.hook.strip(), 5.0))
    for section in script.sections:
        parts = _split_narration(section.content)
        total_chars = max(1, sum(len(part) for part in parts))
        for part in parts:
            weight = max(1.5, section.estimated_seconds * len(part) / total_chars)
            entries.append((section.id, part, weight))
    if not entries:
        raise ValueError("文案没有可用于时间线的旁白内容")

    raw_total = sum(weight for _, _, weight in entries)
    if narration_duration_seconds is not None and narration_duration_seconds > max_seconds:
        raise ValueError(
            "实际配音时长"
            f" {narration_duration_seconds:.1f} 秒，超过生产档案上限 {max_seconds} 秒"
        )
    requested_total = narration_duration_seconds or min(max(raw_total, min_seconds), max_seconds)
    if not math.isfinite(requested_total) or requested_total <= 0:
        raise ValueError("旁白时长无效")
    scale = requested_total / raw_total
    cues: list[NarrationCue] = []
    for section_id, text, weight in entries:
        scaled_duration = max(0.75, weight * scale)
        part_count = max(1, math.ceil(scaled_duration / 8.0))
        text_parts = _split_text_evenly(text, part_count)
        part_duration = scaled_duration / len(text_parts)
        for text_part in text_parts:
            cues.append(
                NarrationCue(
                    id=f"cue_{len(cues) + 1:03d}",
                    source_section_id=section_id,
                    text=text_part,
                    duration_seconds=round(part_duration, 3),
                )
            )
    return cues


def _build_creation_task_cues(
    script: CreationTaskScript,
    *,
    min_seconds: int,
    max_seconds: int,
    narration_duration_seconds: float,
    narration_segment_ids_by_section: dict[str, str] | None = None,
    narration_segment_durations_by_section: dict[str, float] | None = None,
) -> list[NarrationCue]:
    if narration_duration_seconds > max_seconds:
        raise ValueError(
            f"实际配音时长 {narration_duration_seconds:.1f} 秒，超过创作方案上限 {max_seconds} 秒"
        )
    if not math.isfinite(narration_duration_seconds) or narration_duration_seconds <= 0:
        raise ValueError("旁白时长无效")
    entries: list[tuple[str, str, float]] = []
    for section in script.sections:
        parts = _split_narration(section.content)
        for part in parts:
            entries.append((section.id, part, max(1.0, float(len(part)))))
    if not entries:
        raise ValueError("文案没有可用于时间线的旁白内容")
    raw_total = sum(weight for _, _, weight in entries)
    requested_total = narration_duration_seconds
    scale = requested_total / raw_total
    weights_by_section: dict[str, float] = {}
    for section_id, _text, weight in entries:
        weights_by_section[section_id] = weights_by_section.get(section_id, 0.0) + weight
    if narration_segment_durations_by_section is not None:
        durations = [narration_segment_durations_by_section.get(section_id) for section_id in weights_by_section]
        if any(value is None or not math.isfinite(value) or value <= 0 for value in durations):
            raise ValueError("每个文案段落必须有有效的真实配音时长")
        if abs(sum(durations) - narration_duration_seconds) > 0.03:
            raise ValueError("分段配音时长与完整旁白不一致，请重新生成配音")
    cues: list[NarrationCue] = []
    cursor = 0.0
    rounded_cursor = 0.0
    for section_id, text, weight in entries:
        section_duration = (
            narration_segment_durations_by_section.get(section_id)
            if narration_segment_durations_by_section
            else None
        )
        scaled_duration = (
            section_duration * weight / weights_by_section[section_id]
            if section_duration is not None
            else weight * scale
        )
        part_count = max(1, math.ceil(scaled_duration / 8.0))
        text_parts = _split_text_evenly(text, part_count)
        part_duration = scaled_duration / len(text_parts)
        for text_part in text_parts:
            cursor += part_duration
            rounded_end = round(cursor, 3)
            cue_duration = round(rounded_end - rounded_cursor, 3)
            if cue_duration <= 0:
                raise ValueError("配音时长不足以为字幕分配有效时间区间")
            rounded_cursor = rounded_end
            cues.append(
                NarrationCue(
                    id=f"cue_{len(cues) + 1:03d}",
                    source_section_id=section_id,
                    text=text_part,
                    duration_seconds=cue_duration,
                    narration_segment_id=(
                        narration_segment_ids_by_section.get(section_id, "")
                        if narration_segment_ids_by_section
                        else ""
                    ),
                )
            )
    return cues


def _split_narration(text: str) -> list[str]:
    sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;])\s*", text) if item.strip()]
    chunks: list[str] = []
    for sentence in sentences or [text.strip()]:
        while len(sentence) > 48:
            split_at = max(sentence.rfind("，", 0, 49), sentence.rfind(",", 0, 49), 24)
            chunks.append(sentence[: split_at + 1].strip())
            sentence = sentence[split_at + 1 :].strip()
        if sentence:
            chunks.append(sentence)
    return chunks


def _split_text_evenly(text: str, part_count: int) -> list[str]:
    if part_count <= 1 or len(text) <= 1:
        return [text]
    parts = [part.strip() for part in re.findall(r"[^，,]+[，,]?", text) if part.strip()]
    while len(parts) < part_count:
        index = max(range(len(parts)), key=lambda item: len(parts[item]))
        value = parts[index]
        if len(value) <= 2:
            break
        split_at = max(1, len(value) // 2)
        parts[index : index + 1] = [
            value[:split_at].strip(),
            value[split_at:].strip(),
        ]
    while len(parts) > part_count:
        index = min(
            range(len(parts) - 1),
            key=lambda item: len(parts[item]) + len(parts[item + 1]),
        )
        parts[index : index + 2] = [f"{parts[index]}{parts[index + 1]}"]
    return [part for part in parts if part]


def _score_candidate(
    cue,
    asset,
    segment,
    segment_usage: int,
    asset_usage: int,
    *,
    visual_score: float | None = None,
    visual_only: bool = False,
):
    automatic_description = segment.understanding.summary if segment.understanding and segment.understanding.status == "ready" else ""
    metadata = " ".join(item for item in (
        segment.description or automatic_description or Path(asset.file_name).stem,
        (segment.transcript or speech_text_for_range(asset.speech_analysis, segment.start_seconds, segment.end_seconds))
        if not visual_only else "",
    ) if item)
    cue_tokens = _tokens(cue.text)
    metadata_tokens = _tokens(metadata)
    lexical_score = (
        len(cue_tokens & metadata_tokens) / max(1, len(cue_tokens))
        if metadata_tokens
        else 0.0
    )
    reviewed_tokens = _tokens(segment.description)
    reviewed_lexical_score = len(cue_tokens & reviewed_tokens) / max(1, len(cue_tokens))
    available = segment.end_seconds - segment.start_seconds
    # A long source can be trimmed without degrading duration coverage.
    duration_score = (
        min(1.0, available / max(cue.duration_seconds, 0.001))
        if visual_only else
        max(0.0, 1.0 - abs(available - cue.duration_seconds) / max(cue.duration_seconds, 1.0))
    )
    if visual_score is None:
        score = lexical_score * 0.72 + duration_score * 0.2
    else:
        score = lexical_score * 0.37 + max(0.0, visual_score) * 0.43 + duration_score * 0.12
    if segment_usage == 0:
        score += 0.08
    score -= min(0.36, segment_usage * 0.22 + asset_usage * 0.04)
    score -= min(0.3, segment.usage_count * 0.05)
    reasons = [
        f"文字词重合 {round(lexical_score * 100)}%",
        f"镜头时长适配 {round(duration_score * 100)}%",
    ]
    if lexical_score > reviewed_lexical_score:
        reasons.append(
            "自动画面观察命中相关内容；仍需核对原片"
            if visual_only else
            "自动观察命中相关内容；仍需核对画面，语音证据不能确认画面匹配"
        )
    if visual_score is not None:
        reasons.insert(1, f"画面向量相似度 {round(visual_score * 100)}%")
    if segment_usage == 0:
        reasons.append("优先使用未重复镜头")
    else:
        reasons.append("素材不足，存在重复使用")
    if segment.usage_count:
        reasons.append(f"历史成片已使用 {segment.usage_count} 次，降低重复优先级")
    return score, reviewed_lexical_score, visual_score, asset, segment, reasons


def _cosine_similarity(
    left: list[float] | None,
    right: list[float] | None,
) -> float | None:
    if left is None or right is None or len(left) != len(right) or not left:
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        return None
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def _tokens(value: str) -> set[str]:
    normalized = value.lower()
    ascii_tokens = set(re.findall(r"[a-z0-9]{2,}", normalized))
    chinese_tokens = {
        phrase[index : index + 2]
        for phrase in re.findall(r"[\u4e00-\u9fff]+", normalized)
        for index in range(len(phrase) - 1)
    }
    return ascii_tokens | chinese_tokens


def _plan_to_srt(plan: MontagePlan | CreationTaskMontagePlan) -> str:
    cue_by_id = {cue.id: cue for cue in plan.cues}
    lines: list[str] = []
    for index, clip in enumerate(plan.clips, start=1):
        cue = cue_by_id[clip.cue_id]
        lines.extend(
            [
                str(index),
                f"{_srt_time(clip.timeline_start_seconds)} --> {_srt_time(clip.timeline_end_seconds)}",
                "\n".join(_wrap_subtitle_text(cue.text, max_chars=24)),
                "",
            ]
        )
    return "\n".join(lines)


def _write_subtitle_overlays(
    plan: MontagePlan | CreationTaskMontagePlan,
    output_dir: Path,
) -> list[Path]:
    cue_by_id = {cue.id: cue for cue in plan.cues}
    overlays: list[Path] = []
    for index, clip in enumerate(plan.clips, start=1):
        cue = cue_by_id[clip.cue_id]
        path = output_dir / f"subtitle-{index:03}.png"
        _write_subtitle_image(cue,path)
        overlays.append(path)
    return overlays


def _write_subtitle_image(cue, path: Path, *, size=(1080,1920)):
    width,height = size
    margin_y = round(height * 250 / 1920)
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font, lines = _subtitle_font_and_lines(cue.text, cue.subtitle_style, size=size)
    stroke_width = cue.subtitle_style.get("stroke_width", 3)
    spacing = 10
    line_boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width) for line in lines]
    line_heights = [box[3] - box[1] for box in line_boxes]
    block_height = sum(line_heights) + spacing * max(0, len(lines) - 1)
    widest = max(box[2] - box[0] for box in line_boxes)
    left = (width - widest) / 2 - 26
    panel_height = block_height + 40
    position = cue.subtitle_style.get("position", "bottom")
    if position == "top":
        top = margin_y
    elif position == "middle":
        top = (height - panel_height) / 2
    elif position == "bottom":
        top = height - margin_y - panel_height
    else:
        raise OSError(f"不支持的字幕位置：{position}；请选择 top、middle 或 bottom")
    right = (width + widest) / 2 + 26
    bottom = top + block_height + 40
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=18,
        fill=(*_subtitle_color(cue.subtitle_style.get("background_color"), fallback=(0, 0, 0, 255))[:3],
              round(cue.subtitle_style.get("background_opacity", 158 / 255) * 255)),
    )
    compact_text = re.sub(r"\s+", "", "".join(lines))
    terms = sorted({re.sub(r"\s+", "", term) for term in cue.subtitle_emphasis} - {""}, key=len, reverse=True)
    emphasized_indices: set[int] = set()
    if cue.subtitle_emphasis_ranges is not None:
        for start, end in cue.subtitle_emphasis_ranges:
            emphasized_indices.update(range(start, end))
    elif terms:
        for match in re.finditer("|".join(re.escape(term) for term in terms), compact_text):
            emphasized_indices.update(range(match.start(), match.end()))
    text_offset = 0
    cursor_y = top + 20
    for line, box, line_height in zip(lines, line_boxes, line_heights, strict=True):
        emphasis_mask = []
        for character in line:
            emphasis_mask.append(not character.isspace() and text_offset in emphasized_indices)
            if not character.isspace():
                text_offset += 1
        line_width = box[2] - box[0]
        _draw_subtitle_line(
            draw,
            line=line,
            x=(width - line_width) / 2 - box[0],
            y=cursor_y - box[1],
            font=font,
            primary_color=_subtitle_color(
                cue.subtitle_style.get("primary_color"),
                fallback=(255, 255, 255, 255),
            ),
            emphasis_mask=emphasis_mask,
            emphasis_color=_subtitle_color(
                cue.subtitle_style.get("emphasis_color"),
                fallback=(255, 213, 79, 255),
            ),
            stroke_width=stroke_width,
            stroke_color=_subtitle_color(cue.subtitle_style.get("stroke_color"), fallback=(0, 0, 0, 230)),
        )
        cursor_y += line_height + spacing
    overlay.save(path)



def _draw_subtitle_line(
    draw: ImageDraw.ImageDraw,
    *,
    line: str,
    x: float,
    y: float,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    primary_color: tuple[int, int, int, int],
    emphasis_mask: list[bool],
    emphasis_color: tuple[int, int, int, int],
    stroke_width: int,
    stroke_color: tuple[int, int, int, int] = (0, 0, 0, 230),
) -> None:
    start = 0
    while start < len(line):
        end = start + 1
        while end < len(line) and emphasis_mask[end] == emphasis_mask[start]:
            end += 1
        draw.text(
            (x + draw.textlength(line[:start], font=font), y), line[start:end], font=font,
            fill=emphasis_color if emphasis_mask[start] else primary_color,
            stroke_width=stroke_width, stroke_fill=stroke_color,
        )
        start = end


def _subtitle_color(
    value: object,
    *,
    fallback: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return (
            int(value[1:3], 16),
            int(value[3:5], 16),
            int(value[5:7], 16),
            255,
        )
    return fallback


def _subtitle_font_and_lines(
    text: str,
    style: dict[str, object],
    *, size=(1080,1920),
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
    canvas_width, canvas_height = size
    safe_width = canvas_width - 200
    safe_height = canvas_height - 2 * round(canvas_height * 250 / 1920) - 40
    size = style.get("font_size", 40)
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 1920:
        raise OSError("字幕字号必须是 1–1920 的整数；请调整字号后重试")
    family = style.get("font_family")
    font = _subtitle_font(size, family=family)
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    while size > 0:
        lines: list[str] = []
        for explicit_line in _wrap_subtitle_text(text, max_chars=max(1, len(text))):
            remaining = explicit_line
            while remaining:
                low, high = 1, len(remaining)
                while low < high:
                    end = (low + high + 1) // 2
                    box = draw.textbbox((0, 0), remaining[:end], font=font, stroke_width=style.get("stroke_width", 3))
                    if box[2] - box[0] <= safe_width:
                        low = end
                    else:
                        high = end - 1
                line = _wrap_subtitle_text(remaining, max_chars=low)[0]
                if len(line) > low:
                    line = remaining[:low].rstrip()
                lines.append(line)
                remaining = remaining[len(line):].lstrip()
            if not explicit_line:
                lines.append("")
        boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=style.get("stroke_width", 3)) for line in lines]
        height = sum(box[3] - box[1] for box in boxes) + 10 * max(0, len(lines) - 1)
        if height <= safe_height and all(box[2] - box[0] <= safe_width for box in boxes):
            return font, lines
        size -= 1
        font = _subtitle_font(size, family=family) if size else font
    raise OSError("字幕无法放入画面安全区；请缩短字幕后重试")


@lru_cache(maxsize=32)
def _resolve_subtitle_font(family: str) -> tuple[str, int]:
    # Resolve the actual family, including TTC faces; never substitute a different
    # installed font for a family the user explicitly confirmed.
    roots = (
        Path("/System/Library/Fonts"), Path("/Library/Fonts"),
        Path.home() / "Library/Fonts", Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"), Path.home() / ".local/share/fonts",
        Path("C:/Windows/Fonts"),
    )
    candidates = sorted({
        path for root in roots if root.is_dir() for path in root.rglob("*")
        if path.suffix.lower() in {".ttf", ".ttc", ".otf"}
    })
    candidates.extend(sorted(Path("/System/Library/AssetsV2").glob(
        "com_apple_MobileAsset_Font*/*.asset/AssetData/*.[to]t[fc]"
    )))
    matched: tuple[str, int] | None = None
    for path in candidates:
        index = 0
        while True:
            try:
                font = ImageFont.truetype(str(path), size=40, index=index)
            except OSError:
                break  # End of a collection or a font Pillow cannot load.
            actual_family, face = font.getname()
            if actual_family.casefold() == family.casefold():
                matched = (str(path), index)
                if face.casefold() in {"regular", "normal", "book"}:
                    return matched
            if path.suffix.lower() != ".ttc":
                break
            index += 1
    if matched is not None:
        return matched
    raise OSError(f"字幕字体“{family}”未安装或无法读取；请安装该字体或选择已安装字体后重试")


def _subtitle_font(
    size: int, *, family: object = None,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if family is not None:
        if not isinstance(family, str) or not family.strip():
            raise OSError("字幕字体名称无效；请选择已安装字体后重试")
        path, index = _resolve_subtitle_font(family.strip())
        return ImageFont.truetype(path, size=size, index=index)
    for candidate in (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "C:/Windows/Fonts/msyh.ttc",
    ):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _wrap_subtitle_text(text: str, *, max_chars: int) -> list[str]:
    explicit_lines = [
        re.sub(r"[\t\f\v ]+", " ", line.strip())
        for line in re.split(r"\r?\n", text.strip())
        if line.strip()
    ]
    if not explicit_lines:
        return [""]
    lines: list[str] = []
    for explicit_line in explicit_lines:
        remaining = explicit_line
        while remaining:
            if len(remaining) <= max_chars:
                lines.append(remaining)
                break
            split_at = max(
                remaining.rfind("，", 0, max_chars + 1),
                remaining.rfind("。", 0, max_chars + 1),
                remaining.rfind("？", 0, max_chars + 1),
                remaining.rfind("！", 0, max_chars + 1),
                remaining.rfind(" ", 0, max_chars + 1),
            )
            if split_at < max_chars // 2:
                split_at = max_chars
            else:
                split_at += 1
            lines.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
    return lines


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _probe_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(completed.stdout.strip())


def _validate_rendered_media(output_path: Path, *, expected_size=(1080,1920)) -> None:
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise OSError("FFmpeg 未生成有效输出文件")
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise MontageRenderError(
            tool="ffprobe",
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
    try:
        streams = json.loads(completed.stdout).get("streams", [])
    except json.JSONDecodeError as exc:
        raise OSError("ffprobe 返回了无效的媒体元数据") from exc
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )
    if video_stream is None or video_stream.get("codec_name") != "h264":
        raise OSError("渲染输出缺少 H.264 视频轨")
    if audio_stream is None or audio_stream.get("codec_name") != "aac":
        raise OSError("渲染输出缺少 AAC 音频轨")
    if (video_stream.get("width"), video_stream.get("height")) != expected_size:
        raise OSError(f"渲染输出画幅不符合 {expected_size[0]}×{expected_size[1]}")


def _render_error_from_process(exc: subprocess.CalledProcessError) -> MontageRenderError:
    command = exc.cmd
    if isinstance(command, list | tuple) and command:
        tool = Path(str(command[0])).name
    else:
        tool = "ffmpeg"
    stderr = exc.stderr if isinstance(exc.stderr, str) else ""
    return MontageRenderError(
        tool=tool,
        exit_code=exc.returncode,
        stderr=stderr,
    )


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def creation_task_montage_plan_to_payload(
    plan: CreationTaskMontagePlan,
) -> dict[str, object]:
    payload = asdict(plan)
    payload["audio_timeline"] = plan.audio_timeline.model_dump() if plan.audio_timeline else None
    return json.loads(json.dumps(payload, ensure_ascii=False, default=_json_default))


def creation_task_montage_plan_from_payload(
    data: dict[str, object],
) -> CreationTaskMontagePlan:
    payload = dict(data)
    if payload.get("audio_timeline") is not None:
        payload["audio_timeline"] = AudioTimeline.model_validate(payload["audio_timeline"])
    cue_payloads = payload.pop("cues", [])
    clip_payloads = payload.pop("clips", [])
    for key in ("created_at", "updated_at"):
        if isinstance(payload.get(key), str):
            payload[key] = datetime.fromisoformat(str(payload[key]))
    return CreationTaskMontagePlan(
        **payload,
        cues=[NarrationCue(**item) for item in cue_payloads if isinstance(item, dict)],
        clips=[MontageClip(**item) for item in clip_payloads if isinstance(item, dict)],
    )


def _plan_from_payload(data: dict[str, object]) -> MontagePlan:
    cue_payloads = data.pop("cues", [])
    clip_payloads = data.pop("clips", [])
    for key in ("created_at", "updated_at"):
        if isinstance(data.get(key), str):
            data[key] = datetime.fromisoformat(str(data[key]))
    return MontagePlan(
        **data,
        cues=[NarrationCue(**item) for item in cue_payloads if isinstance(item, dict)],
        clips=[MontageClip(**item) for item in clip_payloads if isinstance(item, dict)],
    )
