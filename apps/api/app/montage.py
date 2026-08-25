from __future__ import annotations

import json
import math
import re
import sqlite3
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

from app.models import Episode, EpisodeStatus, ScriptPackage, utc_now
from app.production import (
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


@dataclass
class NarrationCue:
    id: str
    source_section_id: str
    text: str
    duration_seconds: float


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

    def save(self, plan: MontagePlan) -> MontagePlan:
        plan.updated_at = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO production_montage_plans
                    (id, workspace_id, batch_id, episode_id, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    plan.id,
                    plan.workspace_id,
                    plan.batch_id,
                    plan.episode_id,
                    json.dumps(asdict(plan), ensure_ascii=False, default=_json_default),
                    plan.updated_at.isoformat(),
                ),
            )
        return plan

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
                asset, segment = min(
                    a_roll_candidates,
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
                        for asset, segment in b_roll_candidates
                    ),
                    key=lambda item: item[0],
                    reverse=True,
                )
                score, lexical_score, visual_score, asset, segment, reasons = ranked[0]
                role = "b_roll"
            available = segment.end_seconds - segment.start_seconds
            clip_duration = round(min(cue.duration_seconds, available), 3)
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

    def render(self, plan: MontagePlan, audio_path: str | Path) -> MontagePlan:
        if plan.status != "ready":
            raise ValueError("混剪计划仍有待确认镜头，不能渲染")
        if not plan.clips:
            raise ValueError("混剪计划没有镜头")
        narration_path = self._validated_audio_path(audio_path)
        self.output_path.mkdir(parents=True, exist_ok=True)
        output_path = self.output_path / f"{plan.batch_id}_{plan.episode_id}_preview.mp4"
        subtitle_path = self.output_path / f"{plan.batch_id}_{plan.episode_id}.srt"
        subtitle_path.write_text(_plan_to_srt(plan), encoding="utf-8")

        plan.status = "rendering"
        plan.narration_audio_path = str(narration_path)
        try:
            self.runner(self.build_command(plan, narration_path, output_path))
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise OSError("FFmpeg 未生成有效输出文件")
        except (OSError, subprocess.SubprocessError) as exc:
            plan.status = "failed"
            plan.message = f"FFmpeg 渲染失败：{exc}"
            raise
        plan.status = "rendered"
        plan.output_path = str(output_path)
        plan.subtitle_path = str(subtitle_path)
        plan.message = "9:16 初版成片和 SRT 字幕已生成，等待最终审核。"
        return plan

    def build_command(
        self,
        plan: MontagePlan,
        audio_path: Path,
        output_path: Path,
    ) -> list[str]:
        command = ["ffmpeg", "-y"]
        filters: list[str] = []
        labels: list[str] = []
        for index, clip in enumerate(plan.clips):
            source = Path(clip.source_path).expanduser().resolve()
            if not source.is_file() or source.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
                raise ValueError(f"混剪镜头文件不存在或格式不支持：{source.name}")
            duration = clip.source_end_seconds - clip.source_start_seconds
            command.extend(
                ["-ss", f"{clip.source_start_seconds:.3f}", "-t", f"{duration:.3f}", "-i", str(source)]
            )
            filters.append(
                f"[{index}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,fps=30,setpts=PTS-STARTPTS[v{index}]"
            )
            labels.append(f"[v{index}]")

        audio_index = len(plan.clips)
        command.extend(["-i", str(audio_path)])
        output_label = "[v0]"
        if len(labels) > 1:
            output_label = "[vout]"
            filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vout]")
        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                output_label,
                "-map",
                f"{audio_index}:a:0",
                "-af",
                "loudnorm",
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
        subprocess.run(command, check=True)


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
):
    metadata = " ".join(
        item for item in (segment.description, segment.transcript, Path(asset.file_name).stem) if item
    )
    cue_tokens = _tokens(cue.text)
    metadata_tokens = _tokens(metadata)
    lexical_score = (
        len(cue_tokens & metadata_tokens) / max(1, len(cue_tokens))
        if metadata_tokens
        else 0.0
    )
    available = segment.end_seconds - segment.start_seconds
    duration_score = max(0.0, 1.0 - abs(available - cue.duration_seconds) / max(cue.duration_seconds, 1.0))
    if visual_score is None:
        score = lexical_score * 0.72 + duration_score * 0.2
    else:
        score = lexical_score * 0.37 + max(0.0, visual_score) * 0.43 + duration_score * 0.12
    if segment_usage == 0:
        score += 0.08
    score -= min(0.36, segment_usage * 0.22 + asset_usage * 0.04)
    reasons = [
        f"语义词重合 {round(lexical_score * 100)}%",
        f"镜头时长适配 {round(duration_score * 100)}%",
    ]
    if visual_score is not None:
        reasons.insert(1, f"画面向量相似度 {round(visual_score * 100)}%")
    if segment_usage == 0:
        reasons.append("优先使用未重复镜头")
    else:
        reasons.append("素材不足，存在重复使用")
    return score, lexical_score, visual_score, asset, segment, reasons


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
    normalized = re.sub(r"\s+", "", value.lower())
    ascii_tokens = set(re.findall(r"[a-z0-9]{2,}", normalized))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    chinese_tokens = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    return ascii_tokens | chinese_tokens


def _plan_to_srt(plan: MontagePlan) -> str:
    cue_by_id = {cue.id: cue for cue in plan.cues}
    lines: list[str] = []
    for index, clip in enumerate(plan.clips, start=1):
        cue = cue_by_id[clip.cue_id]
        lines.extend(
            [
                str(index),
                f"{_srt_time(clip.timeline_start_seconds)} --> {_srt_time(clip.timeline_end_seconds)}",
                cue.text,
                "",
            ]
        )
    return "\n".join(lines)


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


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


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
