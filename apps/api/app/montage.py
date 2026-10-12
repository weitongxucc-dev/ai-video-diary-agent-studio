from __future__ import annotations

import json
import math
import re
import sqlite3
import subprocess
import threading
import time
from tempfile import TemporaryDirectory
from collections.abc import Callable
from dataclasses import replace, asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

from app.audio_timeline import AudioTimeline, FINITE_LOUDNESS_FILTER
from app.device_scheduler import DeviceScheduler, Grant
from app.host_resource_probes import (
    HostCapacity,
    ResourceAdmission,
    ResourceAdmissionRejected,
    ResourceSamplingUnavailable,
    admit_resource,
    sample_host_capacity,
)
from app.canvas import AspectRatio, canvas_size
from app.visual_effects import clip_video_filters
from app.transitions import extend_dissolve_sources, join_picture_filters
from app.graphic_overlays import validate_overlays, write_graphic_overlay
from app.subtitle_schedule import subtitle_schedule, schedule_srt, narration_schedule
from app.material_speech import speech_text_for_range
from app.models import Episode, EpisodeStatus, ScriptPackage, utc_now
from app.production import (
    automatic_segment_eligible,
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
    presenter_share_target: int = 0
    aspect_ratio: AspectRatio = "9:16"
    narration_audio_path: str | None = None
    output_path: str | None = None
    subtitle_path: str | None = None
    message: str = ""
    graphic_overlays: list[dict] = field(default_factory=list)
    audio_timeline: AudioTimeline | None = None
    # 人物镜头排序把原本没有音轨的方案第一次按来源分成多段；该方案此前的整片 loudnorm 是对
    # 整条配音源做的，标记表示这次响度要落在混音里的整源上，而不是分段后再在母线上补一次。
    # 已经有自定义音轨的方案本来就是省略 loudnorm 的，不受此标记影响。
    narration_linkage_created_audio_timeline: bool = False
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
            for segment in asset.segments
            if automatic_segment_eligible(asset, segment, workspace_id)
            and effective_segment_usage_role(asset, segment) == "b_roll"
            and segment.end_seconds - segment.start_seconds >= 0.75
        ]
        if not b_roll_candidates:
            raise ValueError("可用素材库中没有可自动选用的补充镜头")
        a_roll_candidates = [
            (asset, segment)
            for asset in assets
            if asset.workspace_id == workspace_id
            for segment in asset.segments
            if automatic_segment_eligible(asset, segment, workspace_id)
            and effective_segment_usage_role(asset, segment) == "a_roll"
            and segment.end_seconds - segment.start_seconds >= 0.75
        ]

        cues = _build_cues(
            episode.script,
            min_seconds=profile.target_duration_min_seconds,
            max_seconds=profile.target_duration_max_seconds,
            narration_duration_seconds=narration_duration_seconds,
        )
        cue_vectors, segment_vectors, semantic_note = self._semantic_vectors(
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
            if role == "b_roll" and visual_score is None:
                reasons.append(semantic_note or "该片段缺少可用的当前模型视觉向量，仅按素材文字与时长推荐")
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
        duration_target_seconds: int,
        assets: list[MaterialAsset],
        media_reference_for_asset: Callable[[str], str],
        narration_audio_path: str,
        narration_duration_seconds: float,
        narration_segment_ids_by_section: dict[str, str] | None = None,
        narration_segment_durations_by_section: dict[str, float] | None = None,
        person_render_by_section: dict[str, tuple[str, float]] | None = None,
        presenter_share_target: int = 0,
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
            for segment in asset.segments
            if automatic_segment_eligible(asset, segment, workspace_id)
            and effective_segment_usage_role(asset, segment) == "b_roll"
            and segment.end_seconds - segment.start_seconds >= 0.75
        ]
        cues = _build_creation_task_cues(
            script,
            min_seconds=profile.target_duration_min_seconds,
            max_seconds=duration_target_seconds,
            narration_duration_seconds=narration_duration_seconds,
            narration_segment_ids_by_section=narration_segment_ids_by_section,
            narration_segment_durations_by_section=narration_segment_durations_by_section,
        )
        person_render_by_section = person_render_by_section or {}
        if not b_roll_candidates and any(cue.source_section_id not in person_render_by_section for cue in cues):
            raise ValueError("可用素材库中没有可自动选用的补充画面，无法覆盖未露脸的配音段落")
        # Use the confirmed visual requirement for retrieval; narration remains
        # unchanged for audio and subtitles. These projections are never persisted.
        visual_requirements = {section.id: section.visual_prompt.strip() for section in script.sections}
        retrieval_cues = {
            cue.id: replace(cue, text=visual_requirements.get(cue.source_section_id) or cue.text)
            for cue in cues
        }
        cue_vectors, segment_vectors, semantic_note = self._semantic_vectors(
            workspace_id, list(retrieval_cues.values()), b_roll_candidates
        )
        clips: list[MontageClip] = []
        segment_usage: dict[str, int] = {}
        asset_usage: dict[str, int] = {}
        timeline_cursor = 0.0
        person_source_cursor: dict[str, float] = {}
        for cue in cues:
            clip_duration = round(cue.duration_seconds, 3)
            if cue.source_section_id in person_render_by_section:
                render_id, render_duration = person_render_by_section[cue.source_section_id]
                source_start = person_source_cursor.get(cue.source_section_id, 0.0)
                source_end = round(source_start + clip_duration, 3)
                if source_end > render_duration + 1 / 30:
                    raise ValueError("人物口型输出不足以覆盖露脸段落")
                clips.append(MontageClip(
                    id=f"clip_{uuid4().hex[:10]}", cue_id=cue.id,
                    asset_id=f"person:{render_id}", segment_id=cue.narration_segment_id,
                    source_path=f"person-render://{render_id}",
                    source_start_seconds=round(source_start, 3),
                    source_end_seconds=min(source_end, render_duration),
                    timeline_start_seconds=round(timeline_cursor, 3),
                    timeline_end_seconds=round(timeline_cursor + clip_duration, 3),
                    match_score=1.0,
                    match_reasons=["按当前配音生成口型", f"露脸目标 {presenter_share_target}%"],
                    needs_confirmation=False, role="a_roll", person_render_id=render_id,
                ))
                person_source_cursor[cue.source_section_id] = source_end
                timeline_cursor += clip_duration
                continue
            # Source talking-head footage is not aligned with generated narration.
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
            if role == "b_roll" and visual_score is None:
                reasons.append(semantic_note or "该片段缺少可用的当前模型视觉向量，仅按素材文字与时长推荐")
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
            presenter_share_target=presenter_share_target,
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
    ) -> tuple[dict[str, list[float]], dict[str, list[float]], str]:
        provider = self.semantic_provider
        loader = self.visual_feature_loader
        if provider is None or loader is None or not provider.ready:
            return {}, {}, "本地视觉语义未就绪，本次仅按素材文字与时长推荐"
        try:
            text_vectors = provider.embed_texts([cue.text for cue in cues])
        except (OSError, ValueError, VisualSemanticError):
            return {}, {}, "本地视觉语义计算失败，本次仅按素材文字与时长推荐"
        if len(text_vectors) != len(cues):
            return {}, {}, "本地视觉语义结果数量不符，本次仅按素材文字与时长推荐"
        cue_vectors = {
            cue.id: vector for cue, vector in zip(cues, text_vectors, strict=True)
        }
        segment_vectors: dict[str, list[float]] = {}
        for _asset, segment in candidates:
            vector = loader(workspace_id, segment.id, provider.model_id)
            if vector:
                segment_vectors[segment.id] = vector
        return cue_vectors, segment_vectors, ""


def admit_creation_render(*, source_duration_s: float, input_count: int,
                          cpu_threads: int, wall_clock_s: float,
                          capacity: HostCapacity | None = None) -> ResourceAdmission:
    """Admit a bounded CPU render through the SCHED ``creation_render`` registry.

    The declared dimensions use the registry's field semantics: source duration,
    input clip count, requested encoder threads, and the wall-clock budget. An
    undeclared field, an over-limit value, a type mismatch, or a missing field
    raises :class:`ResourceAdmissionRejected` with a readable reason; a host
    sample that cannot complete raises :class:`ResourceSamplingUnavailable`
    instead of fabricating a budget.
    """
    if capacity is None:
        capacity = sample_host_capacity()
    return admit_resource("creation_render", {
        "source_duration_s": source_duration_s,
        "input_count": input_count,
        "cpu_threads": cpu_threads,
        "wall_clock_s": wall_clock_s,
    }, capacity=capacity)


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

    def audio_duration(self, audio_path: str | Path, *, scheduler: DeviceScheduler | None = None,
                       grant: Grant | None = None, cancel_event: threading.Event | None = None,
                       deadline_monotonic: float | None = None) -> float:
        path = self._validated_audio_path(audio_path)
        duration = float(_probe_duration(path, scheduler=scheduler, grant=grant, cancel_event=cancel_event,
                                         deadline_monotonic=deadline_monotonic)
                         if grant is not None else self.duration_reader(path))
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
        scheduler: DeviceScheduler | None = None,
        grant: Grant | None = None,
        cancel_event: threading.Event | None = None,
        max_output_bytes: int | None = None,
        threads: int | None = None,
        deadline_monotonic: float | None = None,
    ) -> MontagePlan | CreationTaskMontagePlan:
        if isinstance(plan, CreationTaskMontagePlan) and (scheduler is None or grant is None):
            raise PermissionError("CreationTask 媒体渲染必须取得设备许可")
        if (scheduler is None) != (grant is None):
            raise ValueError("渲染许可和调度器必须一起提供")
        if grant is not None and (max_output_bytes is None or max_output_bytes <= 0
                                  or threads is None or threads <= 0 or deadline_monotonic is None):
            raise ValueError("受许可渲染必须提供输出、线程和总时限")
        if plan.status != "ready":
            raise ValueError("混剪计划仍有待确认镜头，不能渲染")
        if not plan.clips:
            raise ValueError("混剪计划没有镜头")
        if grant is not None:
            try:
                admit_creation_render(
                    source_duration_s=plan.duration_seconds,
                    input_count=len(plan.clips),
                    cpu_threads=threads,
                    wall_clock_s=deadline_monotonic - time.monotonic(),
                )
            except (ResourceAdmissionRejected, ResourceSamplingUnavailable) as exc:
                plan.status = "failed"
                plan.message = f"CPU 成片资源准入未通过：{exc}"
                raise
        narration_path = self._validated_audio_path(audio_path)
        if isinstance(plan, CreationTaskMontagePlan):
            narration_duration = self.audio_duration(narration_path, scheduler=scheduler, grant=grant,
                                                     cancel_event=cancel_event,
                                                     deadline_monotonic=deadline_monotonic)
            # Allow encoder padding, but reject a changed voice before bounding the picture output.
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
                subtitle_sequence = None
                if isinstance(plan, CreationTaskMontagePlan):
                    if schedule:
                        subtitle_sequence = _write_timed_subtitle_input(
                            schedule, subtitle_overlays, Path(overlay_dir),
                            size=canvas_size(plan.aspect_ratio), duration_seconds=plan.duration_seconds,
                        )
                    graphics=validate_overlays(plan.graphic_overlays,plan.duration_seconds)
                    graphic_paths=[]
                    for index,graphic in enumerate(graphics):
                        path=Path(overlay_dir)/f"graphic-{index}.png"
                        write_graphic_overlay(graphic.model_dump(),path,canvas_size(plan.aspect_ratio))
                        graphic_paths.append(path)
                    subtitle_overlays=graphic_paths
                    intervals=[(item.start_seconds,item.end_seconds) for item in graphics]
                command = self.build_command(
                    plan, narration_path, output_path, subtitle_overlays,
                    subtitle_intervals=intervals,
                    subtitle_sequence=subtitle_sequence,
                )
                if grant is None:
                    self.runner(command)
                else:
                    bounded_command: list[str] = []
                    for argument in command:
                        if argument == "-i":
                            bounded_command.extend(["-threads", str(threads)])
                        bounded_command.append(argument)
                    command = bounded_command
                    command[2:2] = ["-filter_threads", str(threads), "-filter_complex_threads", str(threads)]
                    command[-1:-1] = ["-threads:v", str(threads), "-fs", str(max_output_bytes)]
                    _run_granted_media_command(scheduler, grant, command, timeout_seconds=None,
                                               cancel_event=cancel_event, deadline_monotonic=deadline_monotonic)
            if max_output_bytes is not None and output_path.stat().st_size > max_output_bytes:
                raise OSError("成片超出本次受许可输出上限")
            _validate_rendered_media(output_path, expected_size=canvas_size(plan.aspect_ratio) if isinstance(plan, CreationTaskMontagePlan) else (1080,1920),
                                     expected_duration_seconds=plan.duration_seconds,
                                     scheduler=scheduler, grant=grant, cancel_event=cancel_event,
                                     deadline_monotonic=deadline_monotonic)
        except subprocess.CalledProcessError as exc:
            render_error = _render_error_from_process(exc)
            plan.status = "failed"
            plan.message = f"FFmpeg 渲染失败：{render_error}"
            raise render_error from exc
        except (OSError, subprocess.SubprocessError) as exc:
            plan.status = "failed"
            plan.message = f"FFmpeg 渲染失败：{exc}"
            raise
        except RuntimeError as exc:
            if grant is not None:
                plan.status = "failed"
                plan.message = f"受许可渲染中断：{exc}"
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
        *,
        subtitle_sequence: tuple[Path, tuple[int, int]] | None = None,
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
        if subtitle_sequence is not None:
            subtitle_manifest, (subtitle_x, subtitle_y) = subtitle_sequence
            subtitle_input = audio_index + 1 + len(subtitle_overlays)
            # Only our job-local manifest is consumed. Its image options require
            # safe=0; the file-only protocol and generated relative names remain fixed.
            command.extend(["-f", "concat", "-safe", "0", "-protocol_whitelist", "file",
                            "-i", str(subtitle_manifest)])
            filters.append(
                f"[{subtitle_input}:v]setpts=PTS-STARTPTS,format=rgba[subtitleclock]"
            )
            filters.append(
                f"{output_label}[subtitleclock]overlay={subtitle_x}:{subtitle_y}:eof_action=pass:"
                "repeatlast=0:shortest=0[vsubtitles]"
            )
            output_label = "[vsubtitles]"
        picture_end_seconds = sum(
            round(clip.timeline_end_seconds * 30) - round(clip.timeline_start_seconds * 30)
            for clip in plan.clips
        ) / 30
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
                    f"apad=whole_dur={picture_end_seconds:.10f}",
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
                *(["-t", f"{picture_end_seconds:.10f}"] if isinstance(plan, CreationTaskMontagePlan) else ["-shortest"]),
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


def select_presenter_sections(
    section_durations: list[tuple[str, float]], target_percent: int
) -> list[str]:
    """Choose complete narration sections so only visible speech needs lip sync."""
    if target_percent not in {0, 20, 40, 60, 80, 100}:
        raise ValueError("露脸占比必须选择 0%、20%、40%、60%、80% 或 100%")
    if not section_durations or target_percent == 0:
        return []
    if target_percent == 100:
        return [section_id for section_id, _ in section_durations]
    total = sum(duration for _, duration in section_durations)
    if total <= 0:
        raise ValueError("配音段落时长无效")
    target = total * target_percent / 100
    # Intro and closing speech establish the speaker; retain script order in output.
    priority = [section_durations[0], *section_durations[-1:], *section_durations[1:-1]]
    chosen: set[str] = set()
    covered = 0.0
    while len(chosen) < len(section_durations):
        remaining = [(section_id, duration) for section_id, duration in priority if section_id not in chosen]
        section_id, duration = min(remaining, key=lambda item: abs(target - covered - item[1]))
        if chosen and abs(target - (covered + duration)) >= abs(target - covered):
            break
        chosen.add(section_id)
        covered += duration
    return [section_id for section_id, _ in section_durations if section_id in chosen]


def _split_narration(text: str) -> list[str]:
    sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;])\s*|(?<=\.)\s+(?=[A-Z])", text) if item.strip()]
    chunks: list[str] = []
    for sentence in sentences or [text.strip()]:
        while len(sentence) > 48:
            boundaries = _narration_text_boundaries(sentence)
            if not boundaries:
                break
            before_limit = [position for position in boundaries if position <= 48]
            comma_ends = [position for position in before_limit if sentence[position - 1] in "，,"]
            split_at = max(comma_ends or before_limit) if before_limit else min(boundaries)
            chunks.append(sentence[:split_at].strip())
            sentence = sentence[split_at:].strip()
        if sentence:
            chunks.append(sentence)
    return chunks


def _narration_text_boundaries(text: str) -> list[int]:
    # Chinese can wrap between characters; English words, contractions and
    # decimal/version numbers must remain intact across generated cues.
    protected = set()
    for word in re.finditer(r"[A-Za-z0-9]+(?:['’.-][A-Za-z0-9]+)*", text):
        protected.update(range(word.start() + 1, word.end()))
    return [position for position in range(1, len(text))
            if position not in protected and text[:position].strip() and text[position:].strip()
            and text[position] not in "，,。.!?！？；;：:"]


def _split_text_evenly(text: str, part_count: int) -> list[str]:
    if part_count <= 1 or len(text) <= 1:
        return [text]
    # Retain whitespace until all joins are finished, so merging does not
    # concatenate neighbouring English words.
    parts = [part for part in re.findall(r"[^，,]*[，,]|[^，,]+$", text) if part]
    while len(parts) < part_count:
        candidates = [(index, _narration_text_boundaries(value)) for index, value in enumerate(parts)]
        candidates = [(index, boundaries) for index, boundaries in candidates if boundaries]
        if not candidates:
            break
        index, boundaries = max(candidates, key=lambda item: len(parts[item[0]]))
        value = parts[index]
        split_at = min(boundaries, key=lambda position: abs(position - len(value) / 2))
        parts[index : index + 1] = [
            value[:split_at],
            value[split_at:],
        ]
    while len(parts) > part_count:
        index = min(
            range(len(parts) - 1),
            key=lambda item: len(parts[item]) + len(parts[item + 1]),
        )
        parts[index : index + 2] = [f"{parts[index]}{parts[index + 1]}"]
    return [part.strip() for part in parts if part.strip()]


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
    # Manual description is a correction: stale model observations must not
    # override it. User tags remain searchable regardless of that correction.
    visual_facts = segment.understanding.facts if not segment.description and segment.understanding and segment.understanding.status == "ready" else []
    related_tags = [tag for tag in (*segment.system_tags, *segment.user_tags, *asset.user_tags)
                    if not tag.startswith("用途:")]
    observation_text = " ".join(fact["text"] for fact in visual_facts)
    metadata = " ".join(item for item in (
        segment.description or automatic_description,
        observation_text,
        " ".join(related_tags),
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
    # Actual visual/label overlap is the first ranking tier. Duration,
    # repetition and vector similarity cannot promote an unrelated shot above it.
    if lexical_score > 0:
        score += 1.0
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
    matching_tags = [tag for tag in related_tags if _tokens(tag.split(":", 1)[-1]) & cue_tokens]
    if matching_tags:
        reasons.append("标签依据：" + "、".join(matching_tags))
    if observation_text and _tokens(observation_text) & cue_tokens:
        reasons.append("片段观察中的主体、动作或场景命中画面要求")
    if visual_score is not None:
        reasons.insert(1, f"画面语义排序分 {visual_score:.3f}（不是准确概率）")
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


def _subtitle_frame_boundary(seconds: float) -> int:
    # The existing overlay enables use six decimal places on a 30fps picture clock.
    return int((Decimal(f"{seconds:.6f}") * 30).to_integral_value(rounding=ROUND_CEILING))


def _write_timed_subtitle_input(schedule, images: list[Path], output_dir: Path, *,
                                size: tuple[int, int], duration_seconds: float) -> tuple[Path, tuple[int, int]]:
    """One bounded-cadence PNG stream with the same frame visibility/layer order."""
    bounds = None
    for path in images:
        with Image.open(path) as layer:
            # Alpha content is always retained. A zero-opacity colored panel
            # also stores RGB with A=0; retain those bytes for exact RGBA recovery.
            visible = layer.getbbox(alpha_only=False)
        if visible is not None:
            bounds = visible if bounds is None else (
                min(bounds[0], visible[0]), min(bounds[1], visible[1]),
                max(bounds[2], visible[2]), max(bounds[3], visible[3]),
            )
    if bounds is None:
        bounds = (0, 0, *size)
    # Every frame shares this box. Even offsets preserve the original positions
    # when overlay negotiates yuv420 chroma coordinates; never shrink alpha edges.
    crop = (bounds[0] // 2 * 2, bounds[1] // 2 * 2,
            min(size[0], (bounds[2] + 1) // 2 * 2),
            min(size[1], (bounds[3] + 1) // 2 * 2))
    cropped_size = (crop[2] - crop[0], crop[3] - crop[1])
    for path in dict.fromkeys(images):
        with Image.open(path) as layer:
            cropped = layer.crop(crop)
        cropped.save(path)
    # Match clip_video_filters' absolute picture endpoint, rather than rounding
    # the six-decimal cue enable expression into an extra terminal frame.
    frame_count = round(duration_seconds * 30)
    spans = [(min(frame_count, _subtitle_frame_boundary(cue.start_seconds)),
              min(frame_count, _subtitle_frame_boundary(cue.end_seconds))) for cue in schedule]
    boundaries = sorted({0, frame_count, *(frame for span in spans for frame in span)})
    transparent = output_dir / "subtitle-transparent.png"
    Image.new("RGBA", cropped_size, (0, 0, 0, 0)).save(transparent)
    frames: dict[tuple[int, ...], Path] = {(): transparent}
    lines = ["ffconcat version 1.0"]
    for start, end in zip(boundaries, boundaries[1:]):
        active = tuple(index for index, (first, last) in enumerate(spans) if first <= start < last)
        if active not in frames:
            if len(active) == 1:
                frames[active] = images[active[0]]
            else:
                composite = Image.new("RGBA", cropped_size, (0, 0, 0, 0))
                for index in active:
                    with Image.open(images[index]) as layer:
                        composite.alpha_composite(layer)
                frames[active] = output_dir / f"subtitle-composite-{len(frames):03}.png"
                composite.save(frames[active])
        # Concat stores durations in microseconds. Differences of absolute
        # boundaries avoid accumulating rounding drift across many captions.
        start_us = (start * 1_000_000 + 15) // 30
        end_us = (end * 1_000_000 + 15) // 30
        # A sparse PNG followed by fps needs the NEXT cue before it can emit;
        # framesync also looks ahead. Emit current-image packets every 1/30s
        # inside this single demuxer instead. The exclusive outpoint lies halfway
        # after the last wanted packet, avoiding microsecond boundary rounding.
        lines.extend([f"file {frames[active].name}", "option framerate 30", "option loop 1",
                      f"outpoint {(end - start - .5) / 30:.6f}",
                      f"duration {(end_us - start_us) / 1_000_000:.6f}"])
    # One non-looped transparent frame at the exact end supplies EOF after the
    # last picture frame; overlay passes the main picture after EOF, never text.
    lines.extend([f"file {transparent.name}", "option framerate 30"])
    manifest = output_dir / "subtitles.ffconcat"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest, (crop[0], crop[1])


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


def _run_granted_media_command(scheduler: DeviceScheduler, grant: Grant, command: list[str], *,
                               timeout_seconds: float | None, cancel_event: threading.Event | None,
                               deadline_monotonic: float | None):
    from app.granted_subprocess import GrantedCommandTimeout, run_granted_command

    if deadline_monotonic is None:
        raise ValueError("受许可媒体命令缺少阶段总时限")
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise GrantedCommandTimeout("渲染阶段已超出总时限")
    timeout_seconds = min(timeout_seconds, remaining) if timeout_seconds is not None else remaining

    result = run_granted_command(scheduler, grant, command, timeout_seconds=timeout_seconds,
                                 max_stdout_bytes=64 * 1024, max_stderr_bytes=256 * 1024,
                                 cancel_event=cancel_event)
    if result.returncode:
        raise MontageRenderError(tool=Path(command[0]).name, exit_code=result.returncode,
                                 stderr=result.stderr.decode("utf-8", errors="replace"))
    return result


def _probe_duration(path: Path, *, scheduler: DeviceScheduler | None = None,
                    grant: Grant | None = None, cancel_event: threading.Event | None = None,
                    deadline_monotonic: float | None = None) -> float:
    command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    if grant is not None:
        if scheduler is None:
            raise ValueError("音频探测缺少调度器")
        return float(_run_granted_media_command(scheduler, grant, command, timeout_seconds=30,
                                                cancel_event=cancel_event,
                                                deadline_monotonic=deadline_monotonic).stdout.strip())
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return float(completed.stdout.strip())


def _validate_rendered_media(output_path: Path, *, expected_size=(1080,1920),
                             expected_duration_seconds: float | None = None,
                             scheduler: DeviceScheduler | None = None, grant: Grant | None = None,
                             cancel_event: threading.Event | None = None,
                             deadline_monotonic: float | None = None) -> None:
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise OSError("FFmpeg 未生成有效输出文件")
    command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,duration",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(output_path),
        ]
    if grant is not None:
        if scheduler is None:
            raise ValueError("成片探测缺少调度器")
        result = _run_granted_media_command(scheduler, grant, command, timeout_seconds=30,
                                            cancel_event=cancel_event,
                                            deadline_monotonic=deadline_monotonic)
        completed = subprocess.CompletedProcess(command, result.returncode,
                                                result.stdout.decode("utf-8", errors="replace"),
                                                result.stderr.decode("utf-8", errors="replace"))
    else:
        completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        raise MontageRenderError(
            tool="ffprobe",
            exit_code=completed.returncode,
            stderr=completed.stderr,
        )
    try:
        metadata = json.loads(completed.stdout)
        streams = metadata.get("streams", [])
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
    if expected_duration_seconds is not None:
        if not math.isfinite(expected_duration_seconds) or expected_duration_seconds <= 0:
            raise ValueError("成片计划时长无效")
        for stream in (video_stream, audio_stream):
            try:
                duration = float(stream["duration"])
            except (KeyError, TypeError, ValueError) as exc:
                raise OSError("渲染输出缺少可核对的音视频时长") from exc
            if not math.isfinite(duration) or duration + 0.1 < expected_duration_seconds:
                raise OSError("渲染输出音视频时长不足，未完成整段计划")


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


def probe_preview_video(path: Path, context, *, expected_size, expected_duration_seconds, silent=False):
    """Check a granted preview as video; silent clips require no audio stream."""
    context.check()
    command = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,duration:format=duration", "-of", "json", str(path)]
    completed = _run_granted_media_command(context.scheduler, context.grant, command, timeout_seconds=30,
        cancel_event=context.cancel_event, deadline_monotonic=context.deadline_monotonic)
    data = json.loads(completed.stdout)
    video = next((item for item in data.get("streams", []) if item.get("codec_type") == "video"), None)
    audio = next((item for item in data.get("streams", []) if item.get("codec_type") == "audio"), None)
    if (video is None or video.get("codec_name") != "h264"
            or (video.get("width"), video.get("height")) != expected_size
            or (silent and audio is not None) or (not silent and audio is None)):
        raise ValueError("视频预览的画幅、编码或声音范围不符合请求")
    duration = float(video.get("duration", data.get("format", {}).get("duration", 0)))
    if not math.isfinite(duration) or abs(duration - expected_duration_seconds) > .1:
        raise ValueError("视频预览未覆盖请求窗口或超出请求时长")
    if not path.is_file() or path.stat().st_size <= 0 or path.stat().st_size > context.max_output_bytes:
        raise ValueError("视频预览文件不符合许可输出上限")
    return {"duration_seconds": duration, "sample_rate": None, "channels": None,
            "width_px": video["width"], "height_px": video["height"], "silent": silent}


def render_granted_clip_preview(clip, source: Path, output: Path, context, *, aspect_ratio, max_seconds=5):
    from app.canvas import preview_size
    from app.visual_effects import clip_video_filters, clip_playback_rate, VisualEffects
    context.check()
    if not 0 < max_seconds <= 5:
        raise ValueError("镜头对比窗口必须在五秒以内")
    effects = VisualEffects.model_validate(clip.visual_effects)
    if effects.beauty_strength or effects.skin_exposure:
        raise ValueError("本机人像效果尚无受许可执行路径")
    width, height = preview_size(aspect_ratio)
    rate = clip_playback_rate(clip)
    source_duration = min(max_seconds * rate, clip.source_end_seconds - clip.source_start_seconds)
    expected = min(max_seconds, clip.timeline_end_seconds - clip.timeline_start_seconds)
    command = ["ffmpeg", "-v", "error", "-y", "-protocol_whitelist", "file,pipe",
        "-filter_threads", str(context.threads), "-filter_complex_threads", str(context.threads),
        "-ss", str(clip.source_start_seconds), "-t", str(source_duration), "-threads", str(context.threads), "-i", str(source),
        "-vf", clip_video_filters(clip, width=width, height=height, max_seconds=max_seconds),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-threads:v", str(context.threads), "-fs", str(context.max_output_bytes), "-movflags", "+faststart", str(output)]
    _run_granted_media_command(context.scheduler, context.grant, command, timeout_seconds=None,
        cancel_event=context.cancel_event, deadline_monotonic=context.deadline_monotonic)
    probe_preview_video(output, context, expected_size=(width,height), expected_duration_seconds=expected, silent=True)
    return output
