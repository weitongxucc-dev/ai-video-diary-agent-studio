from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import subprocess
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from app.avatar_lipsync import (
    Authorization,
    AvatarAndLipSyncRequest,
    PersonProfile,
    PronunciationEntry,
    VoiceProfile as VersionedVoiceProfile,
)
from app.models import utc_now
from app.video import SUPPORTED_VIDEO_EXTENSIONS
from app.visual_semantics import (
    SegmentWindow,
    UnavailableVisualSemanticProvider,
    VisualSemanticError,
    VisualSemanticProvider,
)

AssetReviewStatus = Literal["raw", "approved", "rejected"]
AssetUsageRole = Literal["unassigned", "a_roll", "b_roll", "exclude"]
SuggestedUsageRole = Literal["unassigned", "a_roll", "b_roll"]
AnalysisStatus = Literal["pending", "ready", "blocked", "failed"]
BatchStatus = Literal[
    "created",
    "blocked",
    "running",
    "waiting_for_user",
    "interrupted",
    "ready",
    "reviewed",
    "failed",
]
BatchItemStatus = Literal[
    "queued",
    "running",
    "waiting_for_user",
    "rendered",
    "failed",
    "interrupted",
]
MediaPreviewType = Literal["voice", "avatar_lipsync"]
MediaPreviewStatus = Literal["running", "succeeded", "unavailable", "blocked", "failed"]

_A_ROLL_PROMPTS = (
    "一个人面对摄像头说话的自拍视频",
    "人物面部特写口播",
    "主播面对镜头讲话",
)
_B_ROLL_PROMPTS = (
    "没有人脸的风景环境补充镜头",
    "产品特写或软件界面操作演示",
    "城市街景、办公环境或物品的空镜头",
)
_ROLE_SUGGESTION_MARGIN = 0.02
DEFAULT_MATERIAL_TAXONOMY = (
    "场景:办公",
    "场景:户外",
    "物体:电脑",
    "动作:打字",
    "内容:软件界面",
    "主体:人物",
    "镜头:特写",
)


@dataclass
class MaterialSegment:
    id: str
    asset_id: str
    start_seconds: float
    end_seconds: float
    description: str = ""
    transcript: str = ""
    visual_status: AnalysisStatus = "pending"
    visual_model_id: str = ""
    visual_sample_count: int = 0
    usage_role: AssetUsageRole = "unassigned"
    suggested_usage_role: SuggestedUsageRole = "unassigned"
    usage_role_confidence: float = 0.0
    usage_role_reason: str = ""
    review_status: AssetReviewStatus = "raw"
    system_tags: list[str] = field(default_factory=list)
    user_tags: list[str] = field(default_factory=list)
    collection_ids: list[str] = field(default_factory=list)
    usage_count: int = 0
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class MaterialAsset:
    id: str
    workspace_id: str
    file_name: str
    path: str
    size_bytes: int
    modified_at: datetime
    duration_seconds: float | None = None
    usage_role: AssetUsageRole = "unassigned"
    suggested_usage_role: SuggestedUsageRole = "unassigned"
    usage_role_confidence: float = 0.0
    usage_role_reason: str = ""
    review_status: AssetReviewStatus = "raw"
    rights_confirmed: bool = False
    brand_safe: bool = False
    analysis_status: AnalysisStatus = "pending"
    analysis_message: str = "待分析镜头和画面内容。"
    segments: list[MaterialSegment] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class ProductionProfile:
    workspace_id: str
    workspace_name: str = "AI 视频日记"
    brand_name: str = ""
    primary_platform: str = "douyin"
    aspect_ratio: str = "9:16"
    target_duration_min_seconds: int = 30
    target_duration_max_seconds: int = 90
    brand_template_name: str = ""
    brand_colors: list[str] = field(default_factory=list)
    subtitle_style: str = ""
    intro_text: str = ""
    outro_text: str = ""
    background_music_path: str | None = None
    voice_name: str = ""
    voice_reference_path: str | None = None
    voice_reference_text: str = ""
    voice_consent_confirmed: bool = False
    voice_model_status: AnalysisStatus = "blocked"
    voice_model_message: str = "请先下载本地声音模型并确认声音授权。"
    source_urls: list[str] = field(default_factory=list)
    use_obsidian: bool = True
    use_hermes: bool = True
    factuality_gate_enabled: bool = True
    cloud_enhancement_enabled: bool = False
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class VoiceProfile:
    id: str
    workspace_id: str
    name: str
    reference_audio_path: str
    reference_text: str
    consent_confirmed: bool
    is_default: bool = False
    model_id: str = "qwen3-tts-12hz-0.6b-base-8bit"
    status: AnalysisStatus = "pending"
    message: str = "等待声音模型检查。"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class NarrationArtifactRecord:
    id: str
    workspace_id: str
    script_id: str
    voice_profile_id: str | None
    cache_key: str
    audio_path: str
    duration_seconds: float
    sample_rate: int
    model_id: str
    episode_id: str | None = None
    task_id: str | None = None
    language: str = "zh"
    pronunciation_version: str = ""
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class VersionedNarrationInputs:
    voice: VersionedVoiceProfile
    pronunciation_entries: tuple[PronunciationEntry, ...]


class MediaAuthorityError(ValueError):
    """A fail-closed media identity or authorization error for API callers."""

    def __init__(
        self,
        *,
        reason: str,
        impact: str,
        repair_action: str,
    ) -> None:
        self.reason = reason
        self.impact = impact
        self.repair_action = repair_action
        super().__init__(repair_action)


@dataclass
class MediaPreviewRunRecord:
    id: str
    workspace_id: str
    preview_type: MediaPreviewType
    identity: str
    status: MediaPreviewStatus
    capability_id: str
    model_id: str
    language: str
    person_profile_id: str | None
    person_version: str | None
    voice_profile_id: str | None
    voice_version: str | None
    authorization_id: str | None
    authorization_version: str | None
    attempt: int = 1
    output_path: str | None = None
    reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None


@dataclass
class MaterialCollection:
    id: str
    workspace_id: str
    name: str
    parent_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class ProductionBatchItem:
    episode_id: str
    status: BatchItemStatus = "queued"
    plan_id: str | None = None
    narration_audio_path: str | None = None
    output_path: str | None = None
    message: str = "等待串行处理。"
    attempts: int = 0
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class ProductionBatch:
    id: str
    workspace_id: str
    name: str
    target_count: int
    status: BatchStatus
    episode_ids: list[str] = field(default_factory=list)
    items: list[ProductionBatchItem] = field(default_factory=list)
    blocker_codes: list[str] = field(default_factory=list)
    message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def rendered_count(self) -> int:
        return sum(item.status == "rendered" for item in self.items)

    @property
    def waiting_review_count(self) -> int:
        return sum(item.status == "waiting_for_user" for item in self.items)

    @property
    def failed_count(self) -> int:
        return sum(item.status in {"failed", "interrupted"} for item in self.items)


@dataclass(frozen=True)
class CreationPlanKnowledgeReference:
    id: str
    version: str
    title: str
    authorization: str
    ownership: str


@dataclass(frozen=True)
class CreationPlanSourceReference:
    id: str
    connector_id: str
    source_label: str
    title: str
    license: str
    identity: str = ""


@dataclass(frozen=True)
class CreationPlanVoiceReference:
    profile_id: str
    version: str
    person_profile_id: str
    person_version: str
    person_authorization_id: str
    person_authorization_version: str
    authorization_id: str
    authorization_version: str
    language: str
    model_id: str


@dataclass(frozen=True)
class CreationPlanPronunciationReference:
    id: str
    version: str
    scope: str
    scope_reference: str | None
    language: str


@dataclass(frozen=True)
class CreationPlanBlocker:
    reason: str
    impact: str
    repair_action: str
    stage: str
    blocks_confirmation: bool


@dataclass
class CreationPlan:
    id: str
    workspace_id: str
    goal: str
    name: str
    target_count: int
    language: str
    aspect_ratio: str
    duration_target_seconds: int
    knowledge_references: list[CreationPlanKnowledgeReference]
    source_references: list[CreationPlanSourceReference]
    selected_voice: CreationPlanVoiceReference | None
    pronunciation_references: list[CreationPlanPronunciationReference]
    approved_asset_count: int
    approved_clip_count: int
    material_rule_summary: str
    model_snapshot: list[dict[str, object]]
    confirmation_points: list[str]
    blockers: list[CreationPlanBlocker]
    preflight_fingerprint: str
    status: Literal["confirmed"] = "confirmed"
    creation_task_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    confirmed_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class CreationTask:
    id: str
    workspace_id: str
    plan_id: str
    name: str
    scenario: str
    stage: Literal["topic", "script", "narration", "montage", "review"] = "topic"
    stage_status: Literal[
        "not_started", "running", "waiting_for_user", "completed", "failed"
    ] = "not_started"
    status: Literal["created", "running", "waiting_for_user", "completed", "failed"] = "created"
    progress: int = 0
    recovery_point: str = "topic"
    selected_topic_id: str | None = None
    confirmed_script_id: str | None = None
    narration_cache_key: str | None = None
    montage_plan_id: str | None = None
    output_ready: bool = False
    stage_reason: str | None = None
    stage_impact: str | None = None
    stage_repair_action: str | None = None
    stage_retryable: bool | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


TopicGenerationRunStatus = Literal["running", "succeeded", "failed"]
TopicSelectionStatus = Literal["unselected", "confirmed"]
TopicEvidenceKind = Literal["knowledge", "source"]


@dataclass
class TopicGenerationRun:
    id: str
    workspace_id: str
    task_id: str
    plan_id: str
    status: TopicGenerationRunStatus
    route_snapshot: dict[str, object]
    prompt_schema_version: str
    input_references: list[dict[str, object]]
    reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    retryable: bool | None = None
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None


@dataclass
class TopicCandidate:
    id: str
    workspace_id: str
    task_id: str
    run_id: str
    title: str
    angle: str
    audience: str
    outline: list[str]
    score: int
    selection_status: TopicSelectionStatus = "unselected"
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class TopicEvidenceReference:
    id: str
    workspace_id: str
    task_id: str
    candidate_id: str
    kind: TopicEvidenceKind
    reference_id: str
    version_or_identity: str
    title: str
    authorization_or_license: str
    source_url: str | None
    created_at: datetime = field(default_factory=utc_now)


ScriptGenerationRunStatus = Literal["running", "succeeded", "failed"]
ScriptSelectionStatus = Literal["draft", "confirmed"]


@dataclass
class ScriptGenerationRun:
    id: str
    workspace_id: str
    task_id: str
    plan_id: str
    topic_id: str
    status: ScriptGenerationRunStatus
    route_snapshot: dict[str, object]
    prompt_schema_version: str
    input_references: list[dict[str, object]]
    reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    retryable: bool | None = None
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None


@dataclass
class CreationTaskScriptSection:
    id: str
    title: str
    content: str
    visual_prompt: str
    evidence_reference_ids: list[str]


@dataclass
class CreationTaskScript:
    id: str
    workspace_id: str
    task_id: str
    run_id: str
    topic_id: str
    title: str
    language: Literal["zh-CN"]
    sections: list[CreationTaskScriptSection]
    selection_status: ScriptSelectionStatus = "draft"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    confirmed_at: datetime | None = None


class ProductionRepository:
    """Workspace-scoped persistence for the batch-production foundation.

    The production domain deliberately uses separate tables from legacy Episode
    payloads. That keeps the migration reversible while allowing the existing
    single-Episode flow to continue during the product transition.
    """

    def __init__(
        self,
        database_path: str | Path,
        video_library_path: str | Path,
        duration_reader: Callable[[Path], float] | None = None,
        scene_reader: Callable[[Path, float], list[float]] | None = None,
        visual_provider: VisualSemanticProvider | None = None,
    ) -> None:
        self.database_path = Path(database_path).expanduser()
        self.video_library_path = Path(video_library_path).expanduser()
        self.duration_reader = duration_reader or self._probe_duration
        self.scene_reader = scene_reader or self._detect_scene_boundaries
        self.visual_provider = visual_provider or UnavailableVisualSemanticProvider()
        self._usage_role_prompt_vectors: list[list[float]] | None = None
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

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
        root = self.video_library_path.resolve()
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

    def import_voice_reference_wav(
        self,
        workspace_id: str,
        content: bytes,
    ) -> tuple[str, int, float]:
        """Persist a validated WAV under the workspace media root.

        The caller supplies bytes, never a client-controlled source path. The
        returned value is the only reference accepted by voice profile writes.
        """
        if not workspace_id.strip():
            raise ValueError("工作区不能为空")
        if not content:
            raise ValueError("声音参考 WAV 不能为空")
        if len(content) > 50 * 1024 * 1024:
            raise ValueError("声音参考 WAV 不能超过 50 MB")
        try:
            with wave.open(io.BytesIO(content), "rb") as audio:
                frame_rate = audio.getframerate()
                frame_count = audio.getnframes()
                channels = audio.getnchannels()
                sample_width = audio.getsampwidth()
        except (EOFError, wave.Error) as exc:
            raise ValueError("文件不是可读取的 PCM WAV") from exc
        if frame_rate <= 0 or frame_count <= 0 or channels not in {1, 2}:
            raise ValueError("WAV 必须包含单声道或双声道有效音频")
        if sample_width not in {1, 2, 3, 4}:
            raise ValueError("WAV 采样位宽不受支持")
        duration_seconds = frame_count / frame_rate
        if duration_seconds < 1 or duration_seconds > 120:
            raise ValueError("声音参考 WAV 时长必须在 1–120 秒之间")

        relative = Path("approved") / "voices" / f"voice_{uuid4().hex}.wav"
        target = self.video_library_path.resolve() / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".partial")
        try:
            temporary.write_bytes(content)
            temporary.replace(target)
            reference = f"workspace://{relative.as_posix()}"
            with self._connect() as conn:
                self._claim_workspace_media_reference(
                    conn,
                    workspace_id=workspace_id,
                    reference=reference,
                )
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        return reference, len(content), duration_seconds

    @staticmethod
    def narration_artifact_reference(cache_key: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", cache_key):
            raise ValueError("旁白产物标识无效")
        return f"narration://artifact/{cache_key}"

    def resolve_authorized_narration_audio_reference(
        self,
        workspace_id: str,
        reference: str,
    ) -> Path:
        value = reference.strip()
        if value.startswith("workspace://"):
            return self.resolve_workspace_media_reference(
                workspace_id,
                value,
                require_registered=True,
            )
        parsed = urlsplit(value)
        if (
            parsed.scheme != "narration"
            or parsed.netloc != "artifact"
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(r"/[a-zA-Z0-9_-]{1,128}", parsed.path)
        ):
            raise MediaAuthorityError(
                reason="logical_media_reference_required",
                impact="任意本机路径不能作为旁白或渲染输入。",
                repair_action="从当前工作区选择 workspace:// 媒体，或使用已有 narration://artifact 资源。",
            )
        artifact = self.get_narration_artifact(
            workspace_id,
            parsed.path.removeprefix("/"),
        )
        if artifact is None:
            raise MediaAuthorityError(
                reason="narration_audio_unavailable",
                impact="当前工作区不存在可用的旁白产物，未调用渲染器。",
                repair_action="重新生成旁白，或选择当前工作区已授权的媒体引用。",
            )
        return Path(artifact.audio_path)

    def workspace_media_reference_for_asset(
        self,
        workspace_id: str,
        asset_id: str,
    ) -> str:
        asset = self.get_asset(workspace_id, asset_id)
        root = self.video_library_path.resolve()
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

    def resolve_clip_media_reference(
        self,
        workspace_id: str,
        asset_id: str,
        reference: str,
    ) -> Path:
        expected_reference = self.workspace_media_reference_for_asset(
            workspace_id,
            asset_id,
        )
        if reference != expected_reference:
            raise MediaAuthorityError(
                reason="clip_media_reference_mismatch",
                impact="剪辑计划中的素材引用与当前工作区资产不匹配，未调用渲染器。",
                repair_action="重新从当前工作区素材库生成剪辑计划。",
            )
        return self.resolve_workspace_media_reference(workspace_id, reference)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS production_profiles (
                    workspace_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS material_assets (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, file_path)
                );
                CREATE INDEX IF NOT EXISTS idx_material_assets_workspace
                    ON material_assets(workspace_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS material_segment_features (
                    workspace_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    sample_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, segment_id, model_id)
                );
                CREATE INDEX IF NOT EXISTS idx_material_features_asset
                    ON material_segment_features(workspace_id, asset_id);
                CREATE TABLE IF NOT EXISTS production_batches (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_production_batches_workspace
                    ON production_batches(workspace_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS creation_plans (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_creation_plans_workspace
                    ON creation_plans(workspace_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS creation_tasks (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, plan_id)
                );
                CREATE INDEX IF NOT EXISTS idx_creation_tasks_workspace
                    ON creation_tasks(workspace_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS topic_generation_runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_topic_generation_runs_task
                    ON topic_generation_runs(workspace_id, task_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS topic_candidates (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_topic_candidates_task
                    ON topic_candidates(workspace_id, task_id, created_at ASC);
                CREATE TABLE IF NOT EXISTS topic_evidence_references (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_topic_evidence_candidate
                    ON topic_evidence_references(workspace_id, candidate_id, created_at ASC);
                CREATE TABLE IF NOT EXISTS script_generation_runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_script_generation_runs_task
                    ON script_generation_runs(workspace_id, task_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS creation_task_scripts (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_creation_task_scripts_task
                    ON creation_task_scripts(workspace_id, task_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS creation_task_montage_plans (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_creation_task_montage_task
                    ON creation_task_montage_plans(workspace_id, task_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS voice_profiles (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_voice_profiles_workspace
                    ON voice_profiles(workspace_id, is_default DESC, updated_at DESC);
                CREATE TABLE IF NOT EXISTS episode_voice_selections (
                    workspace_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    voice_profile_id TEXT NOT NULL,
                    voice_version TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, episode_id)
                );
                CREATE TABLE IF NOT EXISTS narration_artifacts (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id, cache_key)
                );
                CREATE INDEX IF NOT EXISTS idx_narration_artifacts_episode
                    ON narration_artifacts(workspace_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS media_preview_runs (
                    id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    preview_type TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, preview_type, identity)
                );
                CREATE INDEX IF NOT EXISTS idx_media_preview_runs_workspace
                    ON media_preview_runs(workspace_id, preview_type, updated_at DESC);
                CREATE TABLE IF NOT EXISTS media_preview_attempts (
                    id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    preview_type TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, preview_type, identity, attempt)
                );
                CREATE INDEX IF NOT EXISTS idx_media_preview_attempts_workspace
                    ON media_preview_attempts(workspace_id, preview_type, identity, attempt DESC);
                CREATE TABLE IF NOT EXISTS media_authorizations (
                    workspace_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_media_authorizations_current
                    ON media_authorizations(workspace_id, id, created_at DESC);
                CREATE TABLE IF NOT EXISTS person_profiles (
                    workspace_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_person_profiles_current
                    ON person_profiles(workspace_id, id, created_at DESC);
                CREATE TABLE IF NOT EXISTS versioned_voice_profiles (
                    workspace_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_versioned_voice_profiles_current
                    ON versioned_voice_profiles(workspace_id, id, created_at DESC);
                CREATE TABLE IF NOT EXISTS workspace_media_references (
                    reference TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workspace_media_references_workspace
                    ON workspace_media_references(workspace_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS pronunciation_entries (
                    workspace_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_pronunciation_entries_workspace
                    ON pronunciation_entries(workspace_id, id, created_at DESC);
                CREATE TABLE IF NOT EXISTS material_collections (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_material_collections_workspace
                    ON material_collections(workspace_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS material_segment_usage (
                    workspace_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    clip_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    used_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, plan_id, clip_id)
                );
                CREATE INDEX IF NOT EXISTS idx_material_usage_segment
                    ON material_segment_usage(workspace_id, segment_id, used_at DESC);
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(episode_voice_selections)")
            }
            if "voice_version" not in columns:
                conn.execute(
                    "ALTER TABLE episode_voice_selections ADD COLUMN voice_version TEXT"
                )
            conn.execute(
                """
                INSERT OR IGNORE INTO media_preview_attempts
                    (id, workspace_id, preview_type, identity, attempt, payload, updated_at)
                SELECT id, workspace_id, preview_type, identity, 1, payload, updated_at
                FROM media_preview_runs
                """
            )

    def save_authorization(self, authorization: Authorization) -> Authorization:
        _validate_authorization(authorization)
        with self._connect() as conn:
            self._save_versioned_payload(
                conn,
                table="media_authorizations",
                workspace_id=authorization.workspace_id,
                record_id=authorization.id,
                version=authorization.version,
                payload=_authorization_payload(authorization),
            )
        return authorization

    def get_authorization(
        self,
        workspace_id: str,
        authorization_id: str,
        version: str | None = None,
    ) -> Authorization:
        with self._connect() as conn:
            return self._get_authorization(conn, workspace_id, authorization_id, version)

    def list_authorizations(self, workspace_id: str) -> list[Authorization]:
        with self._connect() as conn:
            payloads = self._list_versioned_payloads(
                conn,
                table="media_authorizations",
                workspace_id=workspace_id,
                current_only=True,
            )
        return [self._authorization_from_payload(payload) for payload in payloads]

    def list_authorization_versions(
        self,
        workspace_id: str,
        authorization_id: str,
    ) -> list[Authorization]:
        with self._connect() as conn:
            payloads = self._list_versioned_payloads(
                conn,
                table="media_authorizations",
                workspace_id=workspace_id,
                record_id=authorization_id,
            )
        return [self._authorization_from_payload(payload) for payload in payloads]

    def save_person_profile(self, person: PersonProfile) -> PersonProfile:
        _validate_person_profile(person)
        with self._connect() as conn:
            self._save_authorization(conn, person.workspace_id, person.authorization)
            self._save_versioned_payload(
                conn,
                table="person_profiles",
                workspace_id=person.workspace_id,
                record_id=person.id,
                version=person.version,
                payload=_person_profile_payload(person),
            )
        return person

    def get_person_profile(
        self,
        workspace_id: str,
        person_id: str,
        version: str | None = None,
    ) -> PersonProfile:
        with self._connect() as conn:
            return self._get_person_profile(conn, workspace_id, person_id, version)

    def list_person_profiles(self, workspace_id: str) -> list[PersonProfile]:
        with self._connect() as conn:
            payloads = self._list_versioned_payloads(
                conn,
                table="person_profiles",
                workspace_id=workspace_id,
                current_only=True,
            )
            return [
                self._person_profile_from_payload(conn, workspace_id, payload)
                for payload in payloads
            ]

    def list_person_profile_versions(
        self,
        workspace_id: str,
        person_id: str,
    ) -> list[PersonProfile]:
        with self._connect() as conn:
            payloads = self._list_versioned_payloads(
                conn,
                table="person_profiles",
                workspace_id=workspace_id,
                record_id=person_id,
            )
            return [
                self._person_profile_from_payload(conn, workspace_id, payload)
                for payload in payloads
            ]

    def save_versioned_voice_profile(
        self,
        voice: VersionedVoiceProfile,
    ) -> VersionedVoiceProfile:
        _validate_versioned_voice_profile(voice)
        with self._connect() as conn:
            self.resolve_workspace_media_reference(
                voice.workspace_id,
                voice.reference_audio_path,
            )
            self._get_person_profile(
                conn,
                voice.workspace_id,
                voice.person_profile_id,
                voice.person_version,
            )
            self._save_authorization(conn, voice.workspace_id, voice.authorization)
            self._claim_workspace_media_reference(
                conn,
                workspace_id=voice.workspace_id,
                reference=voice.reference_audio_path,
            )
            self._save_versioned_payload(
                conn,
                table="versioned_voice_profiles",
                workspace_id=voice.workspace_id,
                record_id=voice.id,
                version=voice.version,
                payload=_versioned_voice_profile_payload(voice),
            )
        return voice

    def get_versioned_voice_profile(
        self,
        workspace_id: str,
        voice_profile_id: str,
        version: str | None = None,
    ) -> VersionedVoiceProfile:
        with self._connect() as conn:
            return self._get_versioned_voice_profile(
                conn,
                workspace_id,
                voice_profile_id,
                version,
            )

    def list_versioned_voice_profiles(
        self,
        workspace_id: str,
    ) -> list[VersionedVoiceProfile]:
        with self._connect() as conn:
            payloads = self._list_versioned_payloads(
                conn,
                table="versioned_voice_profiles",
                workspace_id=workspace_id,
                current_only=True,
            )
            return [
                self._versioned_voice_profile_from_payload(conn, workspace_id, payload)
                for payload in payloads
            ]

    def list_versioned_voice_profile_versions(
        self,
        workspace_id: str,
        voice_profile_id: str,
    ) -> list[VersionedVoiceProfile]:
        with self._connect() as conn:
            payloads = self._list_versioned_payloads(
                conn,
                table="versioned_voice_profiles",
                workspace_id=workspace_id,
                record_id=voice_profile_id,
            )
            return [
                self._versioned_voice_profile_from_payload(conn, workspace_id, payload)
                for payload in payloads
            ]

    def save_pronunciation_entry(
        self,
        entry: PronunciationEntry,
    ) -> PronunciationEntry:
        _validate_pronunciation_entry(entry)
        with self._connect() as conn:
            if entry.scope == "person":
                self._get_person_profile(conn, entry.workspace_id, entry.scope_reference or "")
            self._save_versioned_payload(
                conn,
                table="pronunciation_entries",
                workspace_id=entry.workspace_id,
                record_id=entry.id,
                version=entry.version,
                payload=_pronunciation_entry_payload(entry),
            )
        return entry

    def get_pronunciation_entry(
        self,
        workspace_id: str,
        entry_id: str,
        version: str | None = None,
    ) -> PronunciationEntry:
        with self._connect() as conn:
            row = self._versioned_row(
                conn,
                table="pronunciation_entries",
                workspace_id=workspace_id,
                record_id=entry_id,
                version=version,
            )
        return self._pronunciation_entry_from_payload(json.loads(str(row["payload"])))

    def list_pronunciation_entries(self, workspace_id: str) -> list[PronunciationEntry]:
        with self._connect() as conn:
            payloads = self._list_versioned_payloads(
                conn,
                table="pronunciation_entries",
                workspace_id=workspace_id,
                current_only=True,
            )
        return [self._pronunciation_entry_from_payload(payload) for payload in payloads]

    def list_pronunciation_entry_versions(
        self,
        workspace_id: str,
        entry_id: str,
    ) -> list[PronunciationEntry]:
        with self._connect() as conn:
            payloads = self._list_versioned_payloads(
                conn,
                table="pronunciation_entries",
                workspace_id=workspace_id,
                record_id=entry_id,
            )
        return [self._pronunciation_entry_from_payload(payload) for payload in payloads]

    def resolve_narration_inputs(
        self,
        *,
        workspace_id: str,
        episode_id: str,
        voice_profile_id: str,
        voice_version: str | None = None,
    ) -> VersionedNarrationInputs:
        with self._connect() as conn:
            historical_voice = self._get_versioned_voice_profile(
                conn,
                workspace_id,
                voice_profile_id,
                voice_version,
            )
            voice = replace(
                historical_voice,
                authorization=self._current_authorization(
                    conn,
                    workspace_id,
                    historical_voice.authorization,
                ),
            )
        return VersionedNarrationInputs(
            voice=voice,
            pronunciation_entries=tuple(
                self.list_applicable_pronunciation_entries(
                    workspace_id=workspace_id,
                    person_profile_id=voice.person_profile_id,
                    episode_id=episode_id,
                )
            ),
        )

    def resolve_avatar_lipsync_request(
        self,
        *,
        workspace_id: str,
        person_id: str,
        person_version: str,
        voice_profile_id: str,
        voice_version: str,
        audio_path: str,
        audio_cache_key: str,
        language: str,
        motion: str,
        expression: str,
        authorization_id: str,
        authorization_version: str,
        model_id: str,
    ) -> AvatarAndLipSyncRequest:
        if language not in {"zh", "en", "zh-en"}:
            raise ValueError("人物口型语言必须是 zh、en 或 zh-en")
        with self._connect() as conn:
            historical_person = self._get_person_profile(
                conn,
                workspace_id,
                person_id,
                person_version,
            )
            historical_voice = self._get_versioned_voice_profile(
                conn,
                workspace_id,
                voice_profile_id,
                voice_version,
            )
            person = replace(
                historical_person,
                authorization=self._current_authorization(
                    conn,
                    workspace_id,
                    historical_person.authorization,
                ),
            )
            voice = replace(
                historical_voice,
                authorization=self._current_authorization(
                    conn,
                    workspace_id,
                    historical_voice.authorization,
                ),
            )
            requested_authorization = self._get_authorization(
                conn,
                workspace_id,
                authorization_id,
                authorization_version,
            )
            authorization = self._current_authorization(
                conn,
                workspace_id,
                requested_authorization,
            )
        return AvatarAndLipSyncRequest(
            workspace_id=workspace_id,
            person=person,
            voice=voice,
            audio_path=audio_path,
            audio_cache_key=audio_cache_key,
            language=language,
            motion=motion,
            expression=expression,
            authorization=authorization,
            model_id=model_id,
        )

    def migrate_legacy_voice_profile(
        self,
        *,
        workspace_id: str,
        legacy_voice_profile_id: str,
        person_profile_id: str,
        person_version: str,
        version: str,
        language: str,
        authorization: Authorization,
    ) -> VersionedVoiceProfile:
        """Explicitly map a legacy voice row; no automatic migration is performed."""
        if language not in {"zh", "en", "zh-en"}:
            raise ValueError("配音语言必须是 zh、en 或 zh-en")
        legacy = self.get_voice_profile(workspace_id, legacy_voice_profile_id)
        versioned = VersionedVoiceProfile(
            id=legacy.id,
            workspace_id=workspace_id,
            person_profile_id=person_profile_id,
            person_version=person_version,
            version=version,
            language=language,
            reference_audio_path=legacy.reference_audio_path,
            reference_text=legacy.reference_text,
            model_id=legacy.model_id,
            authorization=authorization,
        )
        return self.save_versioned_voice_profile(versioned)

    def list_applicable_pronunciation_entries(
        self,
        *,
        workspace_id: str,
        person_profile_id: str | None,
        episode_id: str,
    ) -> list[PronunciationEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM pronunciation_entries
                WHERE workspace_id = ?
                ORDER BY created_at DESC, rowid DESC
                """,
                (workspace_id,),
            ).fetchall()
        entries = [self._pronunciation_entry_from_payload(json.loads(str(row["payload"]))) for row in rows]
        latest_by_id: dict[str, PronunciationEntry] = {}
        for entry in entries:
            latest_by_id.setdefault(entry.id, entry)
        return [
            entry
            for entry in latest_by_id.values()
            if entry.applies_to(
                workspace_id=workspace_id,
                person_profile_id=person_profile_id,
                task_id=episode_id,
            )
        ]

    def _save_authorization(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        authorization: Authorization,
    ) -> None:
        _validate_authorization(authorization)
        if authorization.workspace_id != workspace_id:
            raise ValueError("授权不属于当前工作区")
        self._save_versioned_payload(
            conn,
            table="media_authorizations",
            workspace_id=workspace_id,
            record_id=authorization.id,
            version=authorization.version,
            payload=_authorization_payload(authorization),
        )

    def _claim_workspace_media_reference(
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

    def _save_versioned_payload(
        self,
        conn: sqlite3.Connection,
        *,
        table: str,
        workspace_id: str,
        record_id: str,
        version: str,
        payload: dict[str, object],
    ) -> None:
        if not workspace_id.strip() or not record_id.strip() or not version.strip():
            raise ValueError("版本化媒体对象必须包含工作区、ID 和版本")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        row = conn.execute(
            f"SELECT payload FROM {table} WHERE workspace_id = ? AND id = ? AND version = ?",
            (workspace_id, record_id, version),
        ).fetchone()
        if row is not None:
            if str(row["payload"]) != encoded:
                raise ValueError("语义变更必须使用新版本")
            return
        conn.execute(
            f"INSERT INTO {table} (workspace_id, id, version, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (workspace_id, record_id, version, encoded, utc_now().isoformat()),
        )

    @staticmethod
    def _list_versioned_payloads(
        conn: sqlite3.Connection,
        *,
        table: str,
        workspace_id: str,
        record_id: str | None = None,
        current_only: bool = False,
    ) -> list[dict[str, object]]:
        if record_id is None:
            rows = conn.execute(
                f"SELECT id, payload FROM {table} WHERE workspace_id = ? ORDER BY id, created_at DESC, rowid DESC",
                (workspace_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT id, payload FROM {table} WHERE workspace_id = ? AND id = ? ORDER BY created_at DESC, rowid DESC",
                (workspace_id, record_id),
            ).fetchall()
        seen: set[str] = set()
        payloads: list[dict[str, object]] = []
        for row in rows:
            item_id = str(row["id"])
            if current_only and item_id in seen:
                continue
            seen.add(item_id)
            payloads.append(json.loads(str(row["payload"])))
        return payloads

    @staticmethod
    def _versioned_row(
        conn: sqlite3.Connection,
        *,
        table: str,
        workspace_id: str,
        record_id: str,
        version: str | None,
    ) -> sqlite3.Row:
        if version is None:
            row = conn.execute(
                f"SELECT payload FROM {table} WHERE workspace_id = ? AND id = ? ORDER BY created_at DESC LIMIT 1",
                (workspace_id, record_id),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT payload FROM {table} WHERE workspace_id = ? AND id = ? AND version = ?",
                (workspace_id, record_id, version),
            ).fetchone()
        if row is None:
            raise KeyError(record_id)
        return row

    def _get_authorization(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        authorization_id: str,
        version: str | None,
    ) -> Authorization:
        row = self._versioned_row(
            conn,
            table="media_authorizations",
            workspace_id=workspace_id,
            record_id=authorization_id,
            version=version,
        )
        return self._authorization_from_payload(json.loads(str(row["payload"])))

    def _current_authorization(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        authorization: Authorization,
    ) -> Authorization:
        return self._get_authorization(conn, workspace_id, authorization.id, None)

    def _get_person_profile(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        person_id: str,
        version: str | None = None,
    ) -> PersonProfile:
        row = self._versioned_row(
            conn,
            table="person_profiles",
            workspace_id=workspace_id,
            record_id=person_id,
            version=version,
        )
        return self._person_profile_from_payload(conn, workspace_id, json.loads(str(row["payload"])))

    def _get_versioned_voice_profile(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        voice_profile_id: str,
        version: str | None = None,
    ) -> VersionedVoiceProfile:
        row = self._versioned_row(
            conn,
            table="versioned_voice_profiles",
            workspace_id=workspace_id,
            record_id=voice_profile_id,
            version=version,
        )
        return self._versioned_voice_profile_from_payload(
            conn,
            workspace_id,
            json.loads(str(row["payload"])),
        )

    def get_profile(self, workspace_id: str) -> ProductionProfile:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM production_profiles WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            return ProductionProfile(workspace_id=workspace_id)
        return self._profile_from_payload(json.loads(str(row["payload"])))

    def save_profile(self, profile: ProductionProfile) -> ProductionProfile:
        profile.updated_at = utc_now()
        payload = _json_payload(profile)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO production_profiles (workspace_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (profile.workspace_id, payload, profile.updated_at.isoformat()),
            )
        return profile

    def list_voice_profiles(self, workspace_id: str) -> list[VoiceProfile]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload, is_default FROM voice_profiles
                WHERE workspace_id = ? ORDER BY is_default DESC, updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        profiles: list[VoiceProfile] = []
        for row in rows:
            data = json.loads(str(row["payload"]))
            data["is_default"] = bool(row["is_default"])
            profiles.append(self._voice_profile_from_payload(data))
        return profiles

    def get_voice_profile(self, workspace_id: str, voice_profile_id: str) -> VoiceProfile:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload, is_default FROM voice_profiles
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, voice_profile_id),
            ).fetchone()
        if row is None:
            raise KeyError(voice_profile_id)
        data = json.loads(str(row["payload"]))
        data["is_default"] = bool(row["is_default"])
        return self._voice_profile_from_payload(data)

    def save_voice_profile(self, voice: VoiceProfile) -> VoiceProfile:
        voice.updated_at = utc_now()
        with self._connect() as conn:
            if voice.is_default:
                conn.execute(
                    "UPDATE voice_profiles SET is_default = 0 WHERE workspace_id = ?",
                    (voice.workspace_id,),
                )
            conn.execute(
                """
                INSERT INTO voice_profiles (id, workspace_id, is_default, payload, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    is_default = excluded.is_default,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    voice.id,
                    voice.workspace_id,
                    int(voice.is_default),
                    _json_payload(voice),
                    voice.updated_at.isoformat(),
                ),
            )
        return voice

    def selected_episode_versioned_voice_identity(
        self,
        workspace_id: str,
        episode_id: str,
    ) -> tuple[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT voice_profile_id, voice_version FROM episode_voice_selections
                WHERE workspace_id = ? AND episode_id = ?
                """,
                (workspace_id, episode_id),
            ).fetchone()
        if row is None:
            return None
        version = row["voice_version"]
        if not isinstance(version, str) or not version.strip():
            raise MediaAuthorityError(
                reason="legacy_voice_selection_requires_migration",
                impact="历史未版本化声音选择不会用于新的配音或渲染。",
                repair_action="在人物与声音中选择已授权的声音版本后重新开始该步骤。",
            )
        return str(row["voice_profile_id"]), version

    def resolve_episode_narration_inputs(
        self,
        workspace_id: str,
        episode_id: str,
    ) -> VersionedNarrationInputs:
        identity = self.selected_episode_versioned_voice_identity(
            workspace_id,
            episode_id,
        )
        if identity is None:
            raise MediaAuthorityError(
                reason="voice_selection_required",
                impact="当前创作任务未生成配音，未调用声音模型。",
                repair_action="为该创作任务选择已授权的声音版本后重试。",
            )
        voice_profile_id, voice_version = identity
        inputs = self.resolve_narration_inputs(
            workspace_id=workspace_id,
            episode_id=episode_id,
            voice_profile_id=voice_profile_id,
            voice_version=voice_version,
        )
        authorization_failure = inputs.voice.authorization.failure_for(
            content_type="voice",
            usage_scope="narration",
        )
        if authorization_failure is not None:
            raise MediaAuthorityError(
                reason=f"voice_authorization_{authorization_failure}",
                impact="声音授权当前不可用，未调用声音模型或复用旧配音。",
                repair_action="恢复或更新该声音的 narration 授权后重试。",
            )
        return inputs

    def get_narration_artifact(
        self,
        workspace_id: str,
        cache_key: str,
    ) -> NarrationArtifactRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM narration_artifacts
                WHERE workspace_id = ? AND cache_key = ?
                """,
                (workspace_id, cache_key),
            ).fetchone()
        if row is None:
            return None
        record = self._narration_artifact_from_payload(json.loads(str(row["payload"])))
        if not Path(record.audio_path).is_file():
            return None
        return record

    def save_narration_artifact(
        self,
        artifact: NarrationArtifactRecord,
    ) -> NarrationArtifactRecord:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO narration_artifacts (id, workspace_id, cache_key, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, cache_key) DO UPDATE SET
                    id = excluded.id,
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    artifact.id,
                    artifact.workspace_id,
                    artifact.cache_key,
                    _json_payload(artifact),
                    artifact.created_at.isoformat(),
                ),
            )
        return artifact

    def get_media_preview_run(
        self,
        workspace_id: str,
        preview_type: MediaPreviewType,
        identity: str,
    ) -> MediaPreviewRunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM media_preview_attempts
                WHERE workspace_id = ? AND preview_type = ? AND identity = ?
                ORDER BY attempt DESC
                LIMIT 1
                """,
                (workspace_id, preview_type, identity),
            ).fetchone()
        if row is None:
            return None
        return self._media_preview_run_from_payload(json.loads(str(row["payload"])))

    def save_media_preview_run(
        self,
        record: MediaPreviewRunRecord,
    ) -> MediaPreviewRunRecord:
        _validate_media_preview_run(record)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM media_preview_attempts
                WHERE workspace_id = ? AND preview_type = ? AND identity = ? AND attempt = ?
                """,
                (record.workspace_id, record.preview_type, record.identity, record.attempt),
            ).fetchone()
            if row is not None:
                previous = self._media_preview_run_from_payload(
                    json.loads(str(row["payload"]))
                )
                if previous.id != record.id:
                    raise ValueError("预览尝试 ID 与现有记录不一致")
                if previous.status != "running" and previous != record:
                    raise ValueError("已结束的预览尝试不可覆盖")
            conn.execute(
                """
                INSERT INTO media_preview_attempts
                    (id, workspace_id, preview_type, identity, attempt, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, preview_type, identity, attempt) DO UPDATE SET
                    id = excluded.id,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    record.id,
                    record.workspace_id,
                    record.preview_type,
                    record.identity,
                    record.attempt,
                    _json_payload(record),
                    utc_now().isoformat(),
                ),
            )
        return record

    def claim_media_preview_run(
        self,
        record: MediaPreviewRunRecord,
        *,
        retry: bool,
        succeeded_reusable: bool = True,
    ) -> tuple[MediaPreviewRunRecord, bool]:
        """Atomically return the latest preview run or create one running attempt."""
        _validate_media_preview_run(record)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT payload FROM media_preview_attempts
                WHERE workspace_id = ? AND preview_type = ? AND identity = ?
                ORDER BY attempt DESC
                LIMIT 1
                """,
                (record.workspace_id, record.preview_type, record.identity),
            ).fetchone()
            latest = (
                self._media_preview_run_from_payload(json.loads(str(row["payload"])))
                if row is not None
                else None
            )
            if latest is not None:
                if latest.status == "running":
                    return latest, False
                if latest.status == "succeeded" and succeeded_reusable:
                    return latest, False
                if latest.status != "succeeded" and not retry:
                    return latest, False
                attempt = latest.attempt + 1
            else:
                attempt = 1
            claimed = replace(
                record,
                id=f"{record.preview_type}_preview_{record.identity}_{attempt}",
                attempt=attempt,
            )
            _validate_media_preview_run(claimed)
            conn.execute(
                """
                INSERT INTO media_preview_attempts
                    (id, workspace_id, preview_type, identity, attempt, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claimed.id,
                    claimed.workspace_id,
                    claimed.preview_type,
                    claimed.identity,
                    claimed.attempt,
                    _json_payload(claimed),
                    utc_now().isoformat(),
                ),
            )
        return claimed, True

    def list_media_preview_attempts(
        self,
        workspace_id: str,
        preview_type: MediaPreviewType,
        identity: str,
    ) -> list[MediaPreviewRunRecord]:
        if preview_type not in {"voice", "avatar_lipsync"}:
            raise ValueError("预览类型无效")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM media_preview_attempts
                WHERE workspace_id = ? AND preview_type = ? AND identity = ?
                ORDER BY attempt DESC
                """,
                (workspace_id, preview_type, identity),
            ).fetchall()
        return [
            self._media_preview_run_from_payload(json.loads(str(row["payload"])))
            for row in rows
        ]

    def list_media_preview_runs(
        self,
        workspace_id: str,
        preview_type: MediaPreviewType | None = None,
    ) -> list[MediaPreviewRunRecord]:
        if preview_type is not None and preview_type not in {"voice", "avatar_lipsync"}:
            raise ValueError("预览类型无效")
        with self._connect() as conn:
            if preview_type is None:
                rows = conn.execute(
                    """
                    SELECT payload FROM media_preview_attempts
                    WHERE workspace_id = ? ORDER BY updated_at DESC
                    """,
                    (workspace_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT payload FROM media_preview_attempts
                    WHERE workspace_id = ? AND preview_type = ? ORDER BY updated_at DESC
                    """,
                    (workspace_id, preview_type),
                ).fetchall()
        return [
            self._media_preview_run_from_payload(json.loads(str(row["payload"])))
            for row in rows
        ]

    def list_collections(self, workspace_id: str) -> list[MaterialCollection]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM material_collections
                WHERE workspace_id = ? ORDER BY updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        return [
            self._collection_from_payload(json.loads(str(row["payload"])))
            for row in rows
        ]

    def save_collection(self, collection: MaterialCollection) -> MaterialCollection:
        if collection.parent_id is not None:
            parent = next(
                (
                    item
                    for item in self.list_collections(collection.workspace_id)
                    if item.id == collection.parent_id
                ),
                None,
            )
            if parent is None:
                raise KeyError(collection.parent_id)
        collection.updated_at = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO material_collections (id, workspace_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    collection.id,
                    collection.workspace_id,
                    _json_payload(collection),
                    collection.updated_at.isoformat(),
                ),
            )
        return collection

    def scan_library(self, workspace_id: str) -> list[MaterialAsset]:
        if not self.video_library_path.exists() or not self.video_library_path.is_dir():
            return []
        discovered: list[MaterialAsset] = []
        for path in sorted(self.video_library_path.rglob("*")):
            if (
                not path.is_file()
                or path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS
                or "_已处理" in path.parts
            ):
                continue
            discovered.append(self._upsert_discovered_asset(workspace_id, path))
        return discovered

    def list_assets(self, workspace_id: str) -> list[MaterialAsset]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM material_assets
                WHERE workspace_id = ? ORDER BY updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        return [self._asset_from_payload(json.loads(str(row["payload"]))) for row in rows]

    def list_assets_page(
        self,
        workspace_id: str,
        *,
        limit: int,
        offset: int,
        query: str | None = None,
        review_status: str | None = None,
    ) -> tuple[list[MaterialAsset], int]:
        clauses = ["workspace_id = ?"]
        parameters: list[object] = [workspace_id]
        if query:
            clauses.append("LOWER(payload) LIKE ?")
            parameters.append(f"%{query.strip().lower()}%")
        if review_status:
            clauses.append("json_extract(payload, '$.review_status') = ?")
            parameters.append(review_status)
        where_clause = " AND ".join(clauses)
        with self._connect() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM material_assets WHERE {where_clause}",
                    parameters,
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT payload FROM material_assets
                WHERE {where_clause} ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            ).fetchall()
        assets = [
            self._asset_from_payload(json.loads(str(row["payload"]))) for row in rows
        ]
        return assets, total

    def get_asset(self, workspace_id: str, asset_id: str) -> MaterialAsset:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM material_assets
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, asset_id),
            ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return self._asset_from_payload(json.loads(str(row["payload"])))

    def analyze_asset(self, workspace_id: str, asset_id: str) -> MaterialAsset:
        asset = self.get_asset(workspace_id, asset_id)
        path = self._validated_library_file(asset.path)
        try:
            duration = float(self.duration_reader(path))
            if duration <= 0:
                raise ValueError("视频时长无效")
            boundaries = self.scene_reader(path, duration)
            ranges = _scene_ranges(boundaries, duration)
            old_segments = {segment.id: segment for segment in asset.segments}
            segments: list[MaterialSegment] = []
            for start, end in ranges:
                segment_id = _segment_id(asset.id, start, end)
                previous = old_segments.get(segment_id)
                segments.append(
                    previous
                    or MaterialSegment(
                        id=segment_id,
                        asset_id=asset.id,
                        start_seconds=start,
                        end_seconds=end,
                    )
                )
            asset.duration_seconds = round(duration, 3)
            asset.segments = segments
            asset.analysis_status = "ready"
            visual_message = self._analyze_visual_semantics(asset, path)
            role_message = self._suggest_usage_roles(asset)
            for segment in asset.segments:
                segment.system_tags = _infer_system_tags(asset, segment)
            asset.analysis_message = (
                f"已拆分 {len(segments)} 个镜头；{visual_message} {role_message}"
            ).strip()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            asset.analysis_status = "failed"
            asset.analysis_message = f"镜头分析失败：{exc}"
        return self._save_asset(asset)

    def get_visual_feature(
        self,
        workspace_id: str,
        segment_id: str,
        model_id: str,
    ) -> list[float] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT vector_json FROM material_segment_features
                WHERE workspace_id = ? AND segment_id = ? AND model_id = ?
                """,
                (workspace_id, segment_id, model_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["vector_json"]))
        if not isinstance(payload, list):
            return None
        return [float(value) for value in payload]

    def update_asset_review(
        self,
        workspace_id: str,
        asset_id: str,
        *,
        review_status: AssetReviewStatus,
        rights_confirmed: bool,
        brand_safe: bool,
        usage_role: AssetUsageRole = "unassigned",
    ) -> MaterialAsset:
        asset = self.get_asset(workspace_id, asset_id)
        if usage_role == "exclude":
            review_status = "rejected"
        if review_status == "approved":
            if usage_role not in {"a_roll", "b_roll"}:
                raise ValueError("先选择人物主镜头或 B-roll，再加入可用库")
            if not rights_confirmed:
                raise ValueError("确认版权后才能将素材加入可用库")
            if not brand_safe:
                raise ValueError("确认品牌安全后才能将素材加入可用库")
            if asset.analysis_status != "ready" or not asset.segments:
                raise ValueError("请先完成镜头分析再审核素材")
        asset.review_status = review_status
        asset.usage_role = usage_role
        asset.rights_confirmed = rights_confirmed
        asset.brand_safe = brand_safe
        if review_status == "approved":
            for segment in asset.segments:
                if segment.usage_role == "unassigned":
                    segment.usage_role = usage_role
                if segment.review_status == "raw":
                    segment.review_status = "approved"
                    segment.updated_at = utc_now()
        elif review_status == "rejected":
            for segment in asset.segments:
                if usage_role == "exclude":
                    segment.usage_role = "exclude"
                segment.review_status = "rejected"
                segment.updated_at = utc_now()
        return self._save_asset(asset)

    def update_segment(
        self,
        workspace_id: str,
        asset_id: str,
        segment_id: str,
        *,
        description: str,
        transcript: str,
        review_status: AssetReviewStatus,
        usage_role: AssetUsageRole | None = None,
        user_tags: list[str] | None = None,
        collection_ids: list[str] | None = None,
    ) -> MaterialAsset:
        asset = self.get_asset(workspace_id, asset_id)
        segment = next((item for item in asset.segments if item.id == segment_id), None)
        if segment is None:
            raise KeyError(segment_id)
        if review_status == "approved" and asset.review_status != "approved":
            raise ValueError("素材文件尚未进入可用库")
        segment.description = description.strip()
        segment.transcript = transcript.strip()
        segment.system_tags = _infer_system_tags(asset, segment)
        if usage_role is not None:
            segment.usage_role = usage_role
            if usage_role == "exclude":
                review_status = "rejected"
        if user_tags is not None:
            segment.user_tags = _normalized_labels(user_tags)
        if collection_ids is not None:
            known_collections = {
                item.id for item in self.list_collections(workspace_id)
            }
            if not set(collection_ids) <= known_collections:
                raise ValueError("标签夹不存在或不属于当前工作区")
            segment.collection_ids = list(dict.fromkeys(collection_ids))
        segment.review_status = review_status
        segment.updated_at = utc_now()
        return self._save_asset(asset)

    def record_segment_usage(
        self,
        workspace_id: str,
        asset_id: str,
        segment_id: str,
        *,
        plan_id: str | None = None,
        clip_id: str | None = None,
    ) -> MaterialAsset:
        asset = self.get_asset(workspace_id, asset_id)
        segment = next((item for item in asset.segments if item.id == segment_id), None)
        if segment is None:
            raise KeyError(segment_id)
        usage_plan_id = plan_id or f"manual_{uuid4().hex}"
        usage_clip_id = clip_id or f"clip_{uuid4().hex}"
        used_at = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO material_segment_usage
                    (workspace_id, plan_id, clip_id, asset_id, segment_id, used_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    usage_plan_id,
                    usage_clip_id,
                    asset_id,
                    segment_id,
                    used_at.isoformat(),
                ),
            )
        if cursor.rowcount == 0:
            return asset
        segment.usage_count += 1
        segment.last_used_at = used_at
        segment.updated_at = used_at
        return self._save_asset(asset)

    def library_insights(self, workspace_id: str) -> dict[str, object]:
        assets = self.list_assets(workspace_id)
        segments = [segment for asset in assets for segment in asset.segments]
        stats: dict[str, dict[str, object]] = {}
        for segment in segments:
            duration = max(0.0, segment.end_seconds - segment.start_seconds)
            for tag in {*segment.system_tags, *segment.user_tags}:
                item = stats.setdefault(
                    tag,
                    {"tag": tag, "segment_count": 0, "duration_seconds": 0.0, "usage_count": 0},
                )
                item["segment_count"] = int(item["segment_count"]) + 1
                item["duration_seconds"] = float(item["duration_seconds"]) + duration
                item["usage_count"] = int(item["usage_count"]) + segment.usage_count
        for tag in DEFAULT_MATERIAL_TAXONOMY:
            stats.setdefault(
                tag,
                {"tag": tag, "segment_count": 0, "duration_seconds": 0.0, "usage_count": 0},
            )
        ordered = sorted(
            stats.values(),
            key=lambda item: (-int(item["segment_count"]), str(item["tag"])),
        )
        return {
            "asset_count": len(assets),
            "segment_count": len(segments),
            "tags": [
                {**item, "duration_seconds": round(float(item["duration_seconds"]), 3)}
                for item in ordered
            ],
            "recommended_gaps": [
                str(item["tag"])
                for item in ordered
                if int(item["segment_count"]) == 0
                and str(item["tag"]) in DEFAULT_MATERIAL_TAXONOMY
            ],
        }

    def list_batches(self, workspace_id: str) -> list[ProductionBatch]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM production_batches
                WHERE workspace_id = ? ORDER BY updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        return [self._batch_from_payload(json.loads(str(row["payload"]))) for row in rows]

    def get_batch(self, workspace_id: str, batch_id: str) -> ProductionBatch:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM production_batches
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, batch_id),
            ).fetchone()
        if row is None:
            raise KeyError(batch_id)
        return self._batch_from_payload(json.loads(str(row["payload"])))

    def confirm_creation_plan(
        self,
        plan: CreationPlan,
        task: CreationTask,
    ) -> tuple[CreationPlan, CreationTask]:
        if not plan.workspace_id.strip() or not task.workspace_id.strip():
            raise ValueError("创作方案和任务必须属于工作区")
        if plan.workspace_id != task.workspace_id or plan.id != task.plan_id:
            raise ValueError("创作任务必须关联同一工作区的创作方案")
        if not plan.goal.strip() or not plan.name.strip():
            raise ValueError("创作方案必须包含目标和名称")
        if not 1 <= plan.target_count <= 50:
            raise ValueError("创作方案成片数必须在 1 到 50 之间")
        if any(blocker.blocks_confirmation for blocker in plan.blockers):
            raise ValueError("当前预检存在阻断选题阶段的事实缺口")

        now = utc_now()
        plan.creation_task_id = task.id
        plan.updated_at = now
        task.updated_at = now
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO creation_plans (id, workspace_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    plan.id,
                    plan.workspace_id,
                    _json_payload(plan),
                    plan.updated_at.isoformat(),
                ),
            )
            conn.execute(
                """
                INSERT INTO creation_tasks (id, workspace_id, plan_id, payload, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.workspace_id,
                    task.plan_id,
                    _json_payload(task),
                    task.updated_at.isoformat(),
                ),
            )
        return (
            self.get_creation_plan(plan.workspace_id, plan.id),
            self.get_creation_task(task.workspace_id, task.id),
        )

    def get_creation_plan(self, workspace_id: str, plan_id: str) -> CreationPlan:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM creation_plans
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, plan_id),
            ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        return self._creation_plan_from_payload(json.loads(str(row["payload"])))

    def get_creation_task(self, workspace_id: str, task_id: str) -> CreationTask:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM creation_tasks
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, task_id),
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._creation_task_from_payload(json.loads(str(row["payload"])))

    def list_creation_tasks(self, workspace_id: str) -> list[CreationTask]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM creation_tasks
                WHERE workspace_id = ? ORDER BY updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        return [
            self._creation_task_from_payload(json.loads(str(row["payload"])))
            for row in rows
        ]

    def start_topic_generation(
        self,
        run: TopicGenerationRun,
    ) -> tuple[CreationTask, TopicGenerationRun]:
        """Persist the running attempt before the provider boundary is crossed."""
        if run.status != "running":
            raise ValueError("选题运行必须以 running 状态开始")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM creation_tasks
                WHERE workspace_id = ? AND id = ?
                """,
                (run.workspace_id, run.task_id),
            ).fetchone()
            if row is None:
                raise KeyError(run.task_id)
            task = self._creation_task_from_payload(json.loads(str(row["payload"])))
            if task.plan_id != run.plan_id:
                raise ValueError("选题运行关联的方案与创作任务不一致")
            if task.stage != "topic" or task.stage_status not in {"not_started", "failed"}:
                raise ValueError("当前创作任务不允许再次开始选题")
            now = utc_now()
            task.stage_status = "running"
            task.status = "running"
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
            task.updated_at = now
            conn.execute(
                """
                INSERT INTO topic_generation_runs
                    (id, workspace_id, task_id, plan_id, payload, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.workspace_id,
                    run.task_id,
                    run.plan_id,
                    _json_payload(run),
                    run.started_at.isoformat(),
                    now.isoformat(),
                ),
            )
            conn.execute(
                """
                UPDATE creation_tasks SET payload = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ?
                """,
                (
                    _json_payload(task),
                    task.updated_at.isoformat(),
                    task.workspace_id,
                    task.id,
                ),
            )
        return (
            self.get_creation_task(run.workspace_id, run.task_id),
            self.get_topic_generation_run(run.workspace_id, run.id),
        )

    def complete_topic_generation(
        self,
        run_id: str,
        candidates: list[TopicCandidate],
        evidence_references: list[TopicEvidenceReference],
    ) -> tuple[CreationTask, TopicGenerationRun, list[TopicCandidate], list[TopicEvidenceReference]]:
        """Commit validated candidates, their evidence, and the stage transition together."""
        if not candidates:
            raise ValueError("选题生成没有返回候选")
        candidate_ids = {candidate.id for candidate in candidates}
        if len(candidate_ids) != len(candidates):
            raise ValueError("选题候选标识重复")
        if any(reference.candidate_id not in candidate_ids for reference in evidence_references):
            raise ValueError("选题证据关联了不存在的候选")
        if any(
            not any(reference.candidate_id == candidate.id for reference in evidence_references)
            for candidate in candidates
        ):
            raise ValueError("每个选题候选都必须保留来源证据")

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM topic_generation_runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            run = self._topic_generation_run_from_payload(json.loads(str(row["payload"])))
            if run.status != "running":
                raise ValueError("选题运行不是可完成状态")
            task_row = conn.execute(
                """
                SELECT payload FROM creation_tasks
                WHERE workspace_id = ? AND id = ?
                """,
                (run.workspace_id, run.task_id),
            ).fetchone()
            if task_row is None:
                raise KeyError(run.task_id)
            task = self._creation_task_from_payload(json.loads(str(task_row["payload"])))
            if task.stage_status != "running":
                raise ValueError("创作任务的选题阶段不是运行中")
            for candidate in candidates:
                if (
                    candidate.workspace_id != run.workspace_id
                    or candidate.task_id != run.task_id
                    or candidate.run_id != run.id
                    or not candidate.title.strip()
                    or not candidate.angle.strip()
                    or not candidate.audience.strip()
                    or not candidate.outline
                    or not 0 <= candidate.score <= 100
                ):
                    raise ValueError("选题候选不符合当前任务的持久化契约")
            for reference in evidence_references:
                if (
                    reference.workspace_id != run.workspace_id
                    or reference.task_id != run.task_id
                    or not reference.reference_id.strip()
                    or not reference.version_or_identity.strip()
                    or not reference.authorization_or_license.strip()
                ):
                    raise ValueError("选题证据不符合当前任务的持久化契约")

            now = utc_now()
            run.status = "succeeded"
            run.route_snapshot = {
                **run.route_snapshot,
                "availability": "succeeded",
                "checked_at": now,
            }
            run.finished_at = now
            task.stage_status = "waiting_for_user"
            task.status = "waiting_for_user"
            task.progress = max(task.progress, 15)
            task.updated_at = now
            conn.execute(
                """
                UPDATE topic_generation_runs SET payload = ?, updated_at = ? WHERE id = ?
                """,
                (_json_payload(run), now.isoformat(), run.id),
            )
            conn.executemany(
                """
                INSERT INTO topic_candidates
                    (id, workspace_id, task_id, run_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        candidate.id,
                        candidate.workspace_id,
                        candidate.task_id,
                        candidate.run_id,
                        _json_payload(candidate),
                        candidate.created_at.isoformat(),
                    )
                    for candidate in candidates
                ],
            )
            conn.executemany(
                """
                INSERT INTO topic_evidence_references
                    (id, workspace_id, task_id, candidate_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        reference.id,
                        reference.workspace_id,
                        reference.task_id,
                        reference.candidate_id,
                        _json_payload(reference),
                        reference.created_at.isoformat(),
                    )
                    for reference in evidence_references
                ],
            )
            conn.execute(
                """
                UPDATE creation_tasks SET payload = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ?
                """,
                (
                    _json_payload(task),
                    task.updated_at.isoformat(),
                    task.workspace_id,
                    task.id,
                ),
            )
        return (
            self.get_creation_task(run.workspace_id, run.task_id),
            self.get_topic_generation_run(run.workspace_id, run.id),
            self.list_topic_candidates(run.workspace_id, run.task_id),
            self.list_topic_evidence_references(run.workspace_id, run.task_id),
        )

    def fail_topic_generation(
        self,
        run_id: str,
        *,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
        provider_attempted: bool = False,
    ) -> tuple[CreationTask, TopicGenerationRun]:
        if not all(value.strip() for value in (reason, impact, repair_action)):
            raise ValueError("选题失败必须包含原因、影响和修复动作")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM topic_generation_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            run = self._topic_generation_run_from_payload(json.loads(str(row["payload"])))
            if run.status != "running":
                raise ValueError("选题运行不是可失败状态")
            task_row = conn.execute(
                """
                SELECT payload FROM creation_tasks
                WHERE workspace_id = ? AND id = ?
                """,
                (run.workspace_id, run.task_id),
            ).fetchone()
            if task_row is None:
                raise KeyError(run.task_id)
            task = self._creation_task_from_payload(json.loads(str(task_row["payload"])))
            now = utc_now()
            run.status = "failed"
            run.reason = reason
            run.impact = impact
            run.repair_action = repair_action
            run.retryable = retryable
            if provider_attempted:
                run.route_snapshot = {
                    **run.route_snapshot,
                    "availability": "failed",
                    "checked_at": now,
                }
            run.finished_at = now
            task.stage_status = "failed"
            task.status = "failed"
            task.recovery_point = "topic"
            task.stage_reason = reason
            task.stage_impact = impact
            task.stage_repair_action = repair_action
            task.stage_retryable = retryable
            task.updated_at = now
            conn.execute(
                "UPDATE topic_generation_runs SET payload = ?, updated_at = ? WHERE id = ?",
                (_json_payload(run), now.isoformat(), run.id),
            )
            conn.execute(
                """
                UPDATE creation_tasks SET payload = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ?
                """,
                (
                    _json_payload(task),
                    task.updated_at.isoformat(),
                    task.workspace_id,
                    task.id,
                ),
            )
        return (
            self.get_creation_task(run.workspace_id, run.task_id),
            self.get_topic_generation_run(run.workspace_id, run.id),
        )

    def get_topic_generation_run(
        self, workspace_id: str, run_id: str
    ) -> TopicGenerationRun:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM topic_generation_runs
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._topic_generation_run_from_payload(json.loads(str(row["payload"])))

    def latest_topic_generation_run(
        self, workspace_id: str, task_id: str
    ) -> TopicGenerationRun | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM topic_generation_runs
                WHERE workspace_id = ? AND task_id = ?
                ORDER BY started_at DESC LIMIT 1
                """,
                (workspace_id, task_id),
            ).fetchone()
        return (
            self._topic_generation_run_from_payload(json.loads(str(row["payload"])))
            if row is not None
            else None
        )

    def list_topic_candidates(
        self, workspace_id: str, task_id: str
    ) -> list[TopicCandidate]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM topic_candidates
                WHERE workspace_id = ? AND task_id = ?
                ORDER BY created_at ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [
            self._topic_candidate_from_payload(json.loads(str(row["payload"])))
            for row in rows
        ]

    def list_topic_evidence_references(
        self, workspace_id: str, task_id: str
    ) -> list[TopicEvidenceReference]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM topic_evidence_references
                WHERE workspace_id = ? AND task_id = ?
                ORDER BY created_at ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [
            self._topic_evidence_reference_from_payload(json.loads(str(row["payload"])))
            for row in rows
        ]

    def confirm_topic_candidate(
        self, workspace_id: str, task_id: str, candidate_id: str
    ) -> tuple[CreationTask, TopicCandidate]:
        with self._connect() as conn:
            task_row = conn.execute(
                """
                SELECT payload FROM creation_tasks
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, task_id),
            ).fetchone()
            if task_row is None:
                raise KeyError(task_id)
            task = self._creation_task_from_payload(json.loads(str(task_row["payload"])))
            candidate_row = conn.execute(
                """
                SELECT payload FROM topic_candidates
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, candidate_id),
            ).fetchone()
            if candidate_row is None:
                raise KeyError(candidate_id)
            candidate = self._topic_candidate_from_payload(
                json.loads(str(candidate_row["payload"]))
            )
            if (
                task.selected_topic_id == candidate_id
                and candidate.selection_status == "confirmed"
            ):
                return task, candidate
            if task.selected_topic_id is not None:
                raise ValueError("当前创作任务已经确认了另一个选题")
            if task.stage != "topic" or task.stage_status != "waiting_for_user":
                raise ValueError("当前创作任务不在等待确认选题状态")
            now = utc_now()
            candidate.selection_status = "confirmed"
            task.selected_topic_id = candidate.id
            task.stage = "script"
            task.stage_status = "not_started"
            task.status = "running"
            task.progress = max(task.progress, 20)
            task.recovery_point = "script"
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
            task.updated_at = now
            conn.execute(
                "UPDATE topic_candidates SET payload = ? WHERE id = ?",
                (_json_payload(candidate), candidate.id),
            )
            conn.execute(
                """
                UPDATE creation_tasks SET payload = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ?
                """,
                (
                    _json_payload(task),
                    task.updated_at.isoformat(),
                    task.workspace_id,
                    task.id,
                ),
            )
        return (
            self.get_creation_task(workspace_id, task_id),
            next(
                candidate
                for candidate in self.list_topic_candidates(workspace_id, task_id)
                if candidate.id == candidate_id
            ),
        )

    def start_script_generation(
        self,
        run: ScriptGenerationRun,
    ) -> tuple[CreationTask, ScriptGenerationRun]:
        if run.status != "running":
            raise ValueError("文案运行必须以 running 状态开始")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (run.workspace_id, run.task_id),
            ).fetchone()
            if row is None:
                raise KeyError(run.task_id)
            task = self._creation_task_from_payload(json.loads(str(row["payload"])))
            if task.plan_id != run.plan_id or task.selected_topic_id != run.topic_id:
                raise ValueError("文案运行与已确认选题不一致")
            duration_repair = (
                task.stage == "narration"
                and task.stage_status == "failed"
                and task.stage_reason == "narration_generation_failed"
                and "压缩并重新确认文案" in (task.stage_repair_action or "")
            )
            if not duration_repair and (
                task.stage != "script"
                or task.stage_status not in {"not_started", "failed"}
            ):
                raise ValueError("当前创作任务不允许再次开始生成文案")
            now = utc_now()
            if duration_repair:
                task.stage = "script"
            task.stage_status = "running"
            task.status = "running"
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
            task.updated_at = now
            conn.execute(
                """
                INSERT INTO script_generation_runs
                    (id, workspace_id, task_id, plan_id, topic_id, payload, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.workspace_id,
                    run.task_id,
                    run.plan_id,
                    run.topic_id,
                    _json_payload(run),
                    run.started_at.isoformat(),
                    now.isoformat(),
                ),
            )
            conn.execute(
                "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(task), now.isoformat(), task.workspace_id, task.id),
            )
        return (
            self.get_creation_task(run.workspace_id, run.task_id),
            self.get_script_generation_run(run.workspace_id, run.id),
        )

    def complete_script_generation(
        self,
        run_id: str,
        script: CreationTaskScript,
    ) -> tuple[CreationTask, ScriptGenerationRun, CreationTaskScript]:
        if not script.title.strip() or not script.sections:
            raise ValueError("文案必须包含标题和正文")
        if any(
            not section.id.strip()
            or not section.title.strip()
            or not section.content.strip()
            or not section.visual_prompt.strip()
            or not section.evidence_reference_ids
            for section in script.sections
        ):
            raise ValueError("文案段落必须包含画面建议和来源证据")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM script_generation_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            run = self._script_generation_run_from_payload(json.loads(str(row["payload"])))
            if run.status != "running":
                raise ValueError("文案运行不是可完成状态")
            task_row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (run.workspace_id, run.task_id),
            ).fetchone()
            if task_row is None:
                raise KeyError(run.task_id)
            task = self._creation_task_from_payload(json.loads(str(task_row["payload"])))
            if (
                script.workspace_id != run.workspace_id
                or script.task_id != run.task_id
                or script.run_id != run.id
                or script.topic_id != run.topic_id
                or task.stage != "script"
                or task.stage_status != "running"
            ):
                raise ValueError("文案与当前创作任务不一致")
            allowed_references = {
                str(item["reference_id"])
                for item in run.input_references
                if isinstance(item, dict) and item.get("reference_id")
            }
            used_references = {
                reference_id
                for section in script.sections
                for reference_id in section.evidence_reference_ids
            }
            if not used_references.issubset(allowed_references):
                raise ValueError("文案引用了本次模型输入之外的资料")
            now = utc_now()
            run.status = "succeeded"
            run.route_snapshot = {
                **run.route_snapshot,
                "availability": "succeeded",
                "checked_at": now,
            }
            run.finished_at = now
            script.updated_at = now
            task.stage_status = "waiting_for_user"
            task.status = "waiting_for_user"
            task.progress = max(task.progress, 35)
            task.updated_at = now
            conn.execute(
                "UPDATE script_generation_runs SET payload = ?, updated_at = ? WHERE id = ?",
                (_json_payload(run), now.isoformat(), run.id),
            )
            conn.execute(
                """
                INSERT INTO creation_task_scripts
                    (id, workspace_id, task_id, run_id, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    script.id,
                    script.workspace_id,
                    script.task_id,
                    script.run_id,
                    _json_payload(script),
                    now.isoformat(),
                ),
            )
            conn.execute(
                "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(task), now.isoformat(), task.workspace_id, task.id),
            )
        return (
            self.get_creation_task(run.workspace_id, run.task_id),
            self.get_script_generation_run(run.workspace_id, run.id),
            self.get_creation_task_script(run.workspace_id, script.id),
        )

    def fail_script_generation(
        self,
        run_id: str,
        *,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
        provider_attempted: bool = False,
    ) -> tuple[CreationTask, ScriptGenerationRun]:
        if not all(value.strip() for value in (reason, impact, repair_action)):
            raise ValueError("文案失败必须包含原因、影响和修复动作")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM script_generation_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            run = self._script_generation_run_from_payload(json.loads(str(row["payload"])))
            if run.status != "running":
                raise ValueError("文案运行不是可失败状态")
            task_row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (run.workspace_id, run.task_id),
            ).fetchone()
            if task_row is None:
                raise KeyError(run.task_id)
            task = self._creation_task_from_payload(json.loads(str(task_row["payload"])))
            now = utc_now()
            run.status = "failed"
            run.reason = reason
            run.impact = impact
            run.repair_action = repair_action
            run.retryable = retryable
            run.finished_at = now
            if provider_attempted:
                run.route_snapshot = {
                    **run.route_snapshot,
                    "availability": "failed",
                    "checked_at": now,
                }
            task.stage_status = "failed"
            task.status = "failed"
            task.recovery_point = "script"
            task.stage_reason = reason
            task.stage_impact = impact
            task.stage_repair_action = repair_action
            task.stage_retryable = retryable
            task.updated_at = now
            conn.execute(
                "UPDATE script_generation_runs SET payload = ?, updated_at = ? WHERE id = ?",
                (_json_payload(run), now.isoformat(), run.id),
            )
            conn.execute(
                "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(task), now.isoformat(), task.workspace_id, task.id),
            )
        return (
            self.get_creation_task(run.workspace_id, run.task_id),
            self.get_script_generation_run(run.workspace_id, run.id),
        )

    def get_script_generation_run(
        self, workspace_id: str, run_id: str
    ) -> ScriptGenerationRun:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM script_generation_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._script_generation_run_from_payload(json.loads(str(row["payload"])))

    def latest_script_generation_run(
        self, workspace_id: str, task_id: str
    ) -> ScriptGenerationRun | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM script_generation_runs
                WHERE workspace_id = ? AND task_id = ?
                ORDER BY started_at DESC LIMIT 1
                """,
                (workspace_id, task_id),
            ).fetchone()
        return (
            self._script_generation_run_from_payload(json.loads(str(row["payload"])))
            if row is not None
            else None
        )

    def list_creation_task_scripts(
        self, workspace_id: str, task_id: str
    ) -> list[CreationTaskScript]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM creation_task_scripts
                WHERE workspace_id = ? AND task_id = ? ORDER BY updated_at DESC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [
            self._creation_task_script_from_payload(json.loads(str(row["payload"])))
            for row in rows
        ]

    def get_creation_task_script(
        self, workspace_id: str, script_id: str
    ) -> CreationTaskScript:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM creation_task_scripts WHERE workspace_id = ? AND id = ?",
                (workspace_id, script_id),
            ).fetchone()
        if row is None:
            raise KeyError(script_id)
        return self._creation_task_script_from_payload(json.loads(str(row["payload"])))

    def confirm_creation_task_script(
        self,
        workspace_id: str,
        task_id: str,
        script_id: str,
        *,
        title: str,
        sections: list[CreationTaskScriptSection],
    ) -> tuple[CreationTask, CreationTaskScript]:
        if not title.strip() or not sections or any(
            not section.content.strip() or not section.evidence_reference_ids
            for section in sections
        ):
            raise ValueError("确认文案必须包含正文和来源证据")
        with self._connect() as conn:
            task_row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (workspace_id, task_id),
            ).fetchone()
            script_row = conn.execute(
                "SELECT payload FROM creation_task_scripts WHERE workspace_id = ? AND task_id = ? AND id = ?",
                (workspace_id, task_id, script_id),
            ).fetchone()
            if task_row is None or script_row is None:
                raise KeyError(script_id)
            task = self._creation_task_from_payload(json.loads(str(task_row["payload"])))
            script = self._creation_task_script_from_payload(json.loads(str(script_row["payload"])))
            confirmed_script_repair = (
                task.confirmed_script_id == script_id
                and script.selection_status == "confirmed"
                and task.stage == "script"
                and task.stage_status == "failed"
            )
            if (
                task.confirmed_script_id == script_id
                and script.selection_status == "confirmed"
                and not confirmed_script_repair
            ):
                return task, script
            if not confirmed_script_repair and (
                task.stage != "script" or task.stage_status != "waiting_for_user"
            ):
                raise ValueError("当前创作任务不在等待确认文案状态")
            allowed_references = {
                reference_id
                for original in script.sections
                for reference_id in original.evidence_reference_ids
            }
            used_references = {
                reference_id for section in sections for reference_id in section.evidence_reference_ids
            }
            if not used_references.issubset(allowed_references):
                raise ValueError("编辑后的文案引用了生成时未使用的资料")
            now = utc_now()
            script.title = title.strip()
            script.sections = sections
            script.selection_status = "confirmed"
            script.confirmed_at = now
            script.updated_at = now
            task.confirmed_script_id = script.id
            task.stage = "narration"
            task.stage_status = "not_started"
            task.status = "running"
            task.progress = max(task.progress, 40)
            task.recovery_point = "narration"
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
            task.updated_at = now
            conn.execute(
                "UPDATE creation_task_scripts SET payload = ?, updated_at = ? WHERE id = ?",
                (_json_payload(script), now.isoformat(), script.id),
            )
            conn.execute(
                "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(task), now.isoformat(), task.workspace_id, task.id),
            )
        return self.get_creation_task(workspace_id, task_id), self.get_creation_task_script(
            workspace_id, script_id
        )

    def start_creation_task_narration(
        self, workspace_id: str, task_id: str
    ) -> CreationTask:
        return self._start_creation_task_stage(workspace_id, task_id, "narration")

    def start_creation_task_montage(
        self, workspace_id: str, task_id: str
    ) -> CreationTask:
        return self._start_creation_task_stage(workspace_id, task_id, "montage")

    def resolve_creation_task_narration_inputs(
        self,
        *,
        workspace_id: str,
        task_id: str,
        voice_profile_id: str,
        voice_version: str | None = None,
    ) -> VersionedNarrationInputs:
        return self.resolve_narration_inputs(
            workspace_id=workspace_id,
            episode_id=task_id,
            voice_profile_id=voice_profile_id,
            voice_version=voice_version,
        )

    def select_creation_task_narration_voice(
        self,
        *,
        workspace_id: str,
        task_id: str,
        selected_voice: CreationPlanVoiceReference,
        pronunciation_references: list[CreationPlanPronunciationReference],
    ) -> tuple[CreationPlan, CreationTask]:
        """Bind an explicitly confirmed voice to a blocked narration stage."""
        with self._connect() as conn:
            task_row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (workspace_id, task_id),
            ).fetchone()
            if task_row is None:
                raise KeyError(task_id)
            task = self._creation_task_from_payload(json.loads(str(task_row["payload"])))
            plan_row = conn.execute(
                "SELECT payload FROM creation_plans WHERE workspace_id = ? AND id = ?",
                (workspace_id, task.plan_id),
            ).fetchone()
            if plan_row is None:
                raise KeyError(task.plan_id)
            plan = self._creation_plan_from_payload(json.loads(str(plan_row["payload"])))
            if plan.creation_task_id != task.id:
                raise ValueError("创作方案与任务不一致")
            if task.stage != "narration" or task.stage_status not in {
                "not_started",
                "failed",
            }:
                raise ValueError("当前任务不能选择配音声音")

            now = utc_now()
            plan.selected_voice = selected_voice
            plan.pronunciation_references = pronunciation_references
            plan.blockers = [
                blocker
                for blocker in plan.blockers
                if not (
                    blocker.stage == "narration"
                    and blocker.reason
                    in {
                        "voice_authorization_missing",
                        "voice_identity_missing",
                        "voice_language_unavailable",
                        "voice_model_unavailable",
                        "voice_reference_wav_unavailable",
                    }
                )
            ]
            plan.updated_at = now
            task.stage_status = "not_started"
            task.status = "running"
            task.recovery_point = "narration"
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
            task.updated_at = now
            conn.execute(
                "UPDATE creation_plans SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(plan), now.isoformat(), workspace_id, plan.id),
            )
            conn.execute(
                "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(task), now.isoformat(), workspace_id, task.id),
            )
        return (
            self.get_creation_plan(workspace_id, task.plan_id),
            self.get_creation_task(workspace_id, task.id),
        )

    def complete_creation_task_narration(
        self, artifact: NarrationArtifactRecord
    ) -> tuple[CreationTask, NarrationArtifactRecord]:
        if artifact.task_id is None:
            raise ValueError("创作任务配音必须关联 task_id")
        if not Path(artifact.audio_path).is_file() or Path(artifact.audio_path).stat().st_size == 0:
            raise ValueError("声音克隆没有生成有效 WAV")
        with self._connect() as conn:
            task_row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (artifact.workspace_id, artifact.task_id),
            ).fetchone()
            if task_row is None:
                raise KeyError(artifact.task_id)
            task = self._creation_task_from_payload(json.loads(str(task_row["payload"])))
            if (
                task.stage != "narration"
                or task.stage_status != "running"
                or task.confirmed_script_id != artifact.script_id
            ):
                raise ValueError("配音产物与当前创作任务不一致")
            now = utc_now()
            conn.execute(
                """
                INSERT INTO narration_artifacts (id, workspace_id, cache_key, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, cache_key) DO UPDATE SET
                    id = excluded.id, payload = excluded.payload, created_at = excluded.created_at
                """,
                (
                    artifact.id,
                    artifact.workspace_id,
                    artifact.cache_key,
                    _json_payload(artifact),
                    artifact.created_at.isoformat(),
                ),
            )
            task.narration_cache_key = artifact.cache_key
            task.stage = "montage"
            task.stage_status = "not_started"
            task.status = "running"
            task.progress = max(task.progress, 60)
            task.recovery_point = "montage"
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
            task.updated_at = now
            conn.execute(
                "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(task), now.isoformat(), task.workspace_id, task.id),
            )
        saved = self.get_narration_artifact(artifact.workspace_id, artifact.cache_key)
        if saved is None:
            raise ValueError("配音产物持久化失败")
        return self.get_creation_task(artifact.workspace_id, artifact.task_id), saved

    def save_creation_task_montage_plan(
        self,
        workspace_id: str,
        task_id: str,
        plan_id: str,
        payload: dict[str, object],
    ) -> tuple[CreationTask, dict[str, object]]:
        status = str(payload.get("status", ""))
        if status not in {"waiting_for_user", "ready", "rendering", "rendered", "failed"}:
            raise ValueError("混剪计划状态无效")
        with self._connect() as conn:
            task_row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (workspace_id, task_id),
            ).fetchone()
            if task_row is None:
                raise KeyError(task_id)
            task = self._creation_task_from_payload(json.loads(str(task_row["payload"])))
            if task.stage not in {"montage", "review"}:
                raise ValueError("当前创作任务不在剪辑或成片检查阶段")
            if str(payload.get("task_id", "")) != task.id:
                raise ValueError("混剪计划与当前创作任务不一致")
            now = utc_now()
            payload = {**payload, "updated_at": now.isoformat()}
            conn.execute(
                """
                INSERT INTO creation_task_montage_plans
                    (id, workspace_id, task_id, payload, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, task_id) DO UPDATE SET
                    id = excluded.id, payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (plan_id, workspace_id, task_id, json.dumps(payload, ensure_ascii=False), now.isoformat()),
            )
            task.montage_plan_id = plan_id
            task.output_ready = status == "rendered"
            if status != "failed":
                task.stage_reason = None
                task.stage_impact = None
                task.stage_repair_action = None
                task.stage_retryable = None
            if status == "rendering":
                task.stage = "montage"
                task.stage_status = "running"
                task.status = "running"
            elif status == "rendered":
                task.stage = "review"
                task.stage_status = "waiting_for_user"
                task.status = "waiting_for_user"
                task.progress = max(task.progress, 90)
                task.recovery_point = "review"
                task.output_ready = True
            elif status == "failed":
                task.stage = "montage"
                task.stage_status = "failed"
                task.status = "failed"
                task.recovery_point = "montage"
                task.stage_reason = "render_failed"
                task.stage_impact = "未生成可检查的成片。"
                task.stage_repair_action = str(payload.get("message") or "检查 FFmpeg 日志后重试。")
                task.stage_retryable = True
            else:
                task.stage = "montage"
                task.stage_status = "waiting_for_user"
                task.status = "waiting_for_user"
                task.progress = 70 if status == "waiting_for_user" else 75
            task.updated_at = now
            conn.execute(
                "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(task), now.isoformat(), task.workspace_id, task.id),
            )
        return self.get_creation_task(workspace_id, task_id), self.get_creation_task_montage_plan(
            workspace_id, task_id
        )

    def get_creation_task_montage_plan(
        self, workspace_id: str, task_id: str
    ) -> dict[str, object]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM creation_task_montage_plans WHERE workspace_id = ? AND task_id = ?",
                (workspace_id, task_id),
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        payload = json.loads(str(row["payload"]))
        if not isinstance(payload, dict):
            raise ValueError("混剪计划持久化内容无效")
        return payload

    def fail_creation_task_stage(
        self,
        workspace_id: str,
        task_id: str,
        *,
        stage: Literal["narration", "montage"],
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
    ) -> CreationTask:
        if not all(value.strip() for value in (reason, impact, repair_action)):
            raise ValueError("阶段失败必须包含原因、影响和修复动作")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (workspace_id, task_id),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            task = self._creation_task_from_payload(json.loads(str(row["payload"])))
            if task.stage != stage:
                raise ValueError("任务当前阶段与失败阶段不一致")
            task.stage_status = "failed"
            task.status = "failed"
            task.recovery_point = stage
            task.stage_reason = reason
            task.stage_impact = impact
            task.stage_repair_action = repair_action
            task.stage_retryable = retryable
            task.updated_at = utc_now()
            conn.execute(
                "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(task), task.updated_at.isoformat(), workspace_id, task_id),
            )
        return self.get_creation_task(workspace_id, task_id)

    def _start_creation_task_stage(
        self,
        workspace_id: str,
        task_id: str,
        stage: Literal["narration", "montage"],
    ) -> CreationTask:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (workspace_id, task_id),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            task = self._creation_task_from_payload(json.loads(str(row["payload"])))
            if task.stage != stage or task.stage_status not in {"not_started", "failed"}:
                raise ValueError("当前创作任务不能开始该阶段")
            task.stage_status = "running"
            task.status = "running"
            task.stage_reason = None
            task.stage_impact = None
            task.stage_repair_action = None
            task.stage_retryable = None
            task.updated_at = utc_now()
            conn.execute(
                "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                (_json_payload(task), task.updated_at.isoformat(), workspace_id, task_id),
            )
        return self.get_creation_task(workspace_id, task_id)

    def readiness(
        self,
        workspace_id: str,
        *,
        profile: ProductionProfile | None = None,
    ) -> dict[str, object]:
        profile = profile or self.get_profile(workspace_id)
        assets = self.list_assets(workspace_id)
        approved_assets = [
            asset
            for asset in assets
            if asset.review_status == "approved"
            and asset.usage_role in {"a_roll", "b_roll"}
        ]
        approved_segments = [
            segment
            for asset in approved_assets
            for segment in asset.segments
            if segment.review_status == "approved"
            and effective_segment_usage_role(asset, segment) == "b_roll"
        ]
        blockers: list[dict[str, str]] = []
        versioned_voices = self.list_versioned_voice_profiles(workspace_id)
        usable_voice = next(
            (
                voice
                for voice in versioned_voices
                if voice.authorization.failure_for(
                    content_type="voice",
                    usage_scope="narration",
                )
                is None
            ),
            None,
        )
        if not profile.brand_name or not profile.brand_template_name:
            blockers.append(
                {
                    "code": "brand_profile_missing",
                    "message": "尚未配置品牌名称和固定视觉模板。",
                }
            )
        if usable_voice is None:
            blockers.append(
                {
                    "code": "versioned_voice_authorization_missing",
                    "message": "尚未登记可用于配音的版本化授权声音。",
                }
            )
        elif not usable_voice.reference_audio_path or not usable_voice.reference_text.strip():
            blockers.append(
                {
                "code": "voice_reference_missing",
                    "message": "授权声音缺少工作区参考音频或对应文字。",
                }
            )
        if profile.voice_model_status != "ready":
            blockers.append(
                {
                    "code": "voice_model_not_ready",
                    "message": profile.voice_model_message,
                }
            )
        if not (profile.source_urls or profile.use_obsidian or profile.use_hermes):
            blockers.append(
                {
                    "code": "source_pool_empty",
                    "message": "自动选题来源池为空。",
                }
            )
        if not approved_segments:
            blockers.append(
                {
                    "code": "approved_materials_empty",
                    "message": "可用素材库中还没有已审核的 B-roll 镜头。",
                }
            )
        return {
            "ready": not blockers,
            "blockers": blockers,
            "asset_count": len(assets),
            "approved_asset_count": len(approved_assets),
            "approved_segment_count": len(approved_segments),
            "total_duration_seconds": round(
                sum(asset.duration_seconds or 0 for asset in assets), 3
            ),
        }

    def _upsert_discovered_asset(
        self,
        workspace_id: str,
        path: Path,
    ) -> MaterialAsset:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.video_library_path.resolve())
        except ValueError as exc:
            raise ValueError("素材文件不在配置的素材库中") from exc
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload
                FROM material_assets
                WHERE workspace_id = ? AND file_path = ?
                """,
                (workspace_id, str(resolved)),
            ).fetchone()
        existing = (
            self._asset_from_payload(json.loads(str(row["payload"])))
            if row is not None
            else None
        )
        stat = resolved.stat()
        if existing is None:
            digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
            existing = MaterialAsset(
                id=f"asset_{digest}",
                workspace_id=workspace_id,
                file_name=resolved.name,
                path=str(resolved),
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=utc_now().tzinfo),
            )
        else:
            existing.file_name = resolved.name
            existing.size_bytes = stat.st_size
            existing.modified_at = datetime.fromtimestamp(stat.st_mtime, tz=utc_now().tzinfo)
        return self._save_asset(existing)

    def _save_asset(self, asset: MaterialAsset) -> MaterialAsset:
        asset.updated_at = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO material_assets
                    (id, workspace_id, file_path, payload, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, file_path) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    asset.id,
                    asset.workspace_id,
                    asset.path,
                    _json_payload(asset),
                    asset.updated_at.isoformat(),
                ),
            )
        return asset

    def _analyze_visual_semantics(self, asset: MaterialAsset, path: Path) -> str:
        provider = self.visual_provider
        if not provider.ready:
            for segment in asset.segments:
                if not segment.visual_model_id:
                    segment.visual_status = "blocked"
            return f"{provider.message} 可先人工补充画面描述并审核。"

        fingerprints = {
            segment.id: _visual_fingerprint(path, segment, provider.model_id)
            for segment in asset.segments
        }
        cached_ids = self._cached_visual_segment_ids(
            asset.workspace_id,
            provider.model_id,
            fingerprints,
        )
        pending = [segment for segment in asset.segments if segment.id not in cached_ids]
        for segment in asset.segments:
            if segment.id in cached_ids:
                segment.visual_status = "ready"
                segment.visual_model_id = provider.model_id
        if not pending:
            return f"已复用 {len(cached_ids)} 个镜头的画面语义缓存，等待审核。"

        try:
            embeddings = provider.embed_segments(
                path,
                [
                    SegmentWindow(
                        segment_id=segment.id,
                        start_seconds=segment.start_seconds,
                        end_seconds=segment.end_seconds,
                    )
                    for segment in pending
                ],
            )
            embedding_by_id = {item.segment_id: item for item in embeddings}
            with self._connect() as conn:
                for segment in pending:
                    embedding = embedding_by_id[segment.id]
                    conn.execute(
                        """
                        INSERT INTO material_segment_features
                            (workspace_id, asset_id, segment_id, model_id, fingerprint,
                             vector_json, sample_count, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(workspace_id, segment_id, model_id) DO UPDATE SET
                            asset_id = excluded.asset_id,
                            fingerprint = excluded.fingerprint,
                            vector_json = excluded.vector_json,
                            sample_count = excluded.sample_count,
                            updated_at = excluded.updated_at
                        """,
                        (
                            asset.workspace_id,
                            asset.id,
                            segment.id,
                            provider.model_id,
                            fingerprints[segment.id],
                            json.dumps(embedding.vector),
                            embedding.sample_count,
                            utc_now().isoformat(),
                        ),
                    )
                    segment.visual_status = "ready"
                    segment.visual_model_id = provider.model_id
                    segment.visual_sample_count = embedding.sample_count
        except (KeyError, OSError, ValueError, VisualSemanticError) as exc:
            for segment in pending:
                segment.visual_status = "failed"
                segment.visual_model_id = provider.model_id
                segment.visual_sample_count = 0
            return f"画面语义分析失败：{exc}；可重试或人工补充描述。"
        return (
            f"已生成 {len(pending)} 个镜头的画面语义向量"
            f"并复用 {len(cached_ids)} 个缓存，等待审核。"
        )

    def _suggest_usage_roles(self, asset: MaterialAsset) -> str:
        asset.suggested_usage_role = "unassigned"
        asset.usage_role_confidence = 0.0
        asset.usage_role_reason = ""
        for segment in asset.segments:
            segment.suggested_usage_role = "unassigned"
            segment.usage_role_confidence = 0.0
            segment.usage_role_reason = ""

        provider = self.visual_provider
        if not provider.ready:
            return "画面模型未就绪，未生成素材用途建议。"
        try:
            if self._usage_role_prompt_vectors is None:
                self._usage_role_prompt_vectors = provider.embed_texts(
                    [*_A_ROLL_PROMPTS, *_B_ROLL_PROMPTS]
                )
            prompt_vectors = self._usage_role_prompt_vectors
            if len(prompt_vectors) != len(_A_ROLL_PROMPTS) + len(_B_ROLL_PROMPTS):
                raise ValueError("素材用途提示向量数量不正确")

            scored_segments: list[MaterialSegment] = []
            for segment in asset.segments:
                vector = self.get_visual_feature(
                    asset.workspace_id,
                    segment.id,
                    provider.model_id,
                )
                if not vector:
                    continue
                scores = [
                    _cosine_similarity(vector, prompt_vector)
                    for prompt_vector in prompt_vectors
                ]
                a_roll_score = max(scores[: len(_A_ROLL_PROMPTS)])
                b_roll_score = max(scores[len(_A_ROLL_PROMPTS) :])
                margin = abs(a_roll_score - b_roll_score)
                if margin < _ROLE_SUGGESTION_MARGIN:
                    segment.usage_role_reason = (
                        "人物口播与补充画面得分接近，保留人工判断"
                        f"（A {a_roll_score:.2f} / B {b_roll_score:.2f}）"
                    )
                else:
                    segment.suggested_usage_role = (
                        "a_roll" if a_roll_score > b_roll_score else "b_roll"
                    )
                    segment.usage_role_confidence = round(
                        min(0.99, 0.5 + margin * 5),
                        3,
                    )
                    label = (
                        "人物面对镜头口播"
                        if segment.suggested_usage_role == "a_roll"
                        else "补充画面或环境空镜"
                    )
                    segment.usage_role_reason = (
                        f"画面更接近{label}"
                        f"（A {a_roll_score:.2f} / B {b_roll_score:.2f}）"
                    )
                scored_segments.append(segment)
        except (OSError, ValueError, VisualSemanticError) as exc:
            return f"素材用途建议失败：{exc}；请人工判断。"

        if not scored_segments:
            return "没有可用于判断素材用途的画面向量。"
        coverage = len(scored_segments) / max(1, len(asset.segments))
        if coverage < 0.75:
            asset.usage_role_reason = (
                f"仅完成 {len(scored_segments)}/{len(asset.segments)} 个镜头的用途判断"
            )
            return "画面分析覆盖不足，请重试后再确认素材用途。"
        counts = {
            role: sum(segment.suggested_usage_role == role for segment in scored_segments)
            for role in ("a_roll", "b_roll")
        }
        dominant_role = max(counts, key=counts.get)
        dominant_segments = [
            segment
            for segment in scored_segments
            if segment.suggested_usage_role == dominant_role
        ]
        dominant_ratio = len(dominant_segments) / len(scored_segments)
        if dominant_ratio >= 0.75 and dominant_segments:
            asset.suggested_usage_role = dominant_role
            asset.usage_role_confidence = round(
                dominant_ratio
                * sum(segment.usage_role_confidence for segment in dominant_segments)
                / len(dominant_segments),
                3,
            )
            label = "人物主镜头" if dominant_role == "a_roll" else "B-roll"
            asset.usage_role_reason = (
                f"{len(dominant_segments)}/{len(scored_segments)} 个镜头建议为{label}"
            )
            return (
                f"用途建议：{label}"
                f"（置信度 {round(asset.usage_role_confidence * 100)}%）。"
            )

        asset.usage_role_reason = (
            f"镜头用途混合或不确定：人物主镜头 {counts['a_roll']} 个，"
            f"B-roll {counts['b_roll']} 个"
        )
        return "素材包含混合或不确定镜头，请逐段确认用途。"

    def _cached_visual_segment_ids(
        self,
        workspace_id: str,
        model_id: str,
        fingerprints: dict[str, str],
    ) -> set[str]:
        if not fingerprints:
            return set()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT segment_id, fingerprint FROM material_segment_features
                WHERE workspace_id = ? AND model_id = ?
                """,
                (workspace_id, model_id),
            ).fetchall()
        return {
            str(row["segment_id"])
            for row in rows
            if fingerprints.get(str(row["segment_id"])) == str(row["fingerprint"])
        }

    def _validated_library_file(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser().resolve()
        try:
            path.relative_to(self.video_library_path.resolve())
        except ValueError as exc:
            raise ValueError("素材文件不在配置的素材库中") from exc
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            raise ValueError("素材文件不存在或格式不支持")
        return path

    def _probe_duration(self, path: Path) -> float:
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

    def _detect_scene_boundaries(self, path: Path, duration: float) -> list[float]:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(path),
                "-vf",
                "select='gt(scene,0.35)',showinfo",
                "-an",
                "-f",
                "null",
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise subprocess.CalledProcessError(
                completed.returncode,
                completed.args,
                output=completed.stdout,
                stderr=completed.stderr,
            )
        boundaries = [
            float(match.group(1))
            for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", completed.stderr)
        ]
        return [value for value in boundaries if 0 < value < duration]

    @staticmethod
    def _profile_from_payload(data: dict[str, object]) -> ProductionProfile:
        data = _with_datetime(data, "updated_at")
        return ProductionProfile(**data)

    @staticmethod
    def _voice_profile_from_payload(data: dict[str, object]) -> VoiceProfile:
        data = _with_datetime(data, "created_at", "updated_at")
        return VoiceProfile(**data)

    @staticmethod
    def _authorization_from_payload(data: dict[str, object]) -> Authorization:
        return Authorization(
            id=_required_text(data, "id"),
            version=_required_text(data, "version"),
            rights_holder=_required_text(data, "rights_holder"),
            content_types=_string_tuple(data, "content_types"),
            usage_scopes=_string_tuple(data, "usage_scopes"),
            effective_from=_required_datetime(data, "effective_from"),
            expires_at=_optional_datetime(data, "expires_at"),
            evidence_reference=_required_text(data, "evidence_reference"),
            status=_required_text(data, "status"),  # type: ignore[arg-type]
            workspace_id=_required_text(data, "workspace_id"),
        )

    def _person_profile_from_payload(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        data: dict[str, object],
    ) -> PersonProfile:
        authorization = self._get_authorization(
            conn,
            workspace_id,
            _required_text(data, "authorization_id"),
            _required_text(data, "authorization_version"),
        )
        return PersonProfile(
            id=_required_text(data, "id"),
            workspace_id=_required_text(data, "workspace_id"),
            display_name=_required_text(data, "display_name"),
            version=_required_text(data, "version"),
            avatar_reference=_required_text(data, "avatar_reference"),
            background_capabilities=_optional_string_tuple(
                data, "background_capabilities"
            ),
            shot_capabilities=_optional_string_tuple(data, "shot_capabilities"),
            authorization=authorization,
        )

    def _versioned_voice_profile_from_payload(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        data: dict[str, object],
    ) -> VersionedVoiceProfile:
        authorization = self._get_authorization(
            conn,
            workspace_id,
            _required_text(data, "authorization_id"),
            _required_text(data, "authorization_version"),
        )
        return VersionedVoiceProfile(
            id=_required_text(data, "id"),
            workspace_id=_required_text(data, "workspace_id"),
            person_profile_id=_required_text(data, "person_profile_id"),
            person_version=_required_text(data, "person_version"),
            version=_required_text(data, "version"),
            language=_required_text(data, "language"),  # type: ignore[arg-type]
            reference_audio_path=_required_text(data, "reference_audio_path"),
            reference_text=_required_text(data, "reference_text"),
            model_id=_required_text(data, "model_id"),
            authorization=authorization,
        )

    @staticmethod
    def _pronunciation_entry_from_payload(data: dict[str, object]) -> PronunciationEntry:
        scope_reference = data.get("scope_reference")
        if scope_reference is not None and not isinstance(scope_reference, str):
            raise ValueError("发音词典作用域引用无效")
        return PronunciationEntry(
            id=_required_text(data, "id"),
            workspace_id=_required_text(data, "workspace_id"),
            term=_required_text(data, "term"),
            language=_required_text(data, "language"),  # type: ignore[arg-type]
            target_pronunciation=_required_text(data, "target_pronunciation"),
            scope=_required_text(data, "scope"),  # type: ignore[arg-type]
            scope_reference=scope_reference,
            version=_required_text(data, "version"),
        )

    @staticmethod
    def _narration_artifact_from_payload(
        data: dict[str, object],
    ) -> NarrationArtifactRecord:
        data = _with_datetime(data, "created_at")
        return NarrationArtifactRecord(**data)

    @staticmethod
    def _media_preview_run_from_payload(
        data: dict[str, object],
    ) -> MediaPreviewRunRecord:
        data = _with_datetime(data, "started_at", "finished_at")
        return MediaPreviewRunRecord(**data)

    @staticmethod
    def _collection_from_payload(data: dict[str, object]) -> MaterialCollection:
        data = _with_datetime(data, "created_at", "updated_at")
        return MaterialCollection(**data)

    @staticmethod
    def _asset_from_payload(data: dict[str, object]) -> MaterialAsset:
        segment_payloads = data.pop("segments", [])
        data = _with_datetime(data, "modified_at", "created_at", "updated_at")
        segments = [
            MaterialSegment(
                **_with_datetime(segment, "last_used_at", "created_at", "updated_at")
            )
            for segment in segment_payloads
            if isinstance(segment, dict)
        ]
        asset = MaterialAsset(**data, segments=segments)
        for segment in asset.segments:
            if not segment.system_tags:
                segment.system_tags = _infer_system_tags(asset, segment)
        return asset

    @staticmethod
    def _batch_from_payload(data: dict[str, object]) -> ProductionBatch:
        item_payloads = data.pop("items", [])
        data = _with_datetime(
            data,
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        )
        items = [
            ProductionBatchItem(**_with_datetime(item, "updated_at"))
            for item in item_payloads
            if isinstance(item, dict)
        ]
        return ProductionBatch(**data, items=items)

    @staticmethod
    def _creation_plan_from_payload(data: dict[str, object]) -> CreationPlan:
        knowledge_payloads = data.pop("knowledge_references", [])
        source_payloads = data.pop("source_references", [])
        voice_payload = data.pop("selected_voice", None)
        pronunciation_payloads = data.pop("pronunciation_references", [])
        blocker_payloads = data.pop("blockers", [])
        data = _with_datetime(data, "created_at", "confirmed_at", "updated_at")
        return CreationPlan(
            **data,
            knowledge_references=[
                CreationPlanKnowledgeReference(**item)
                for item in knowledge_payloads
                if isinstance(item, dict)
            ],
            source_references=[
                CreationPlanSourceReference(**item)
                for item in source_payloads
                if isinstance(item, dict)
            ],
            selected_voice=(
                CreationPlanVoiceReference(**voice_payload)
                if isinstance(voice_payload, dict)
                else None
            ),
            pronunciation_references=[
                CreationPlanPronunciationReference(**item)
                for item in pronunciation_payloads
                if isinstance(item, dict)
            ],
            blockers=[
                CreationPlanBlocker(**item)
                for item in blocker_payloads
                if isinstance(item, dict)
            ],
        )

    @staticmethod
    def _creation_task_from_payload(data: dict[str, object]) -> CreationTask:
        return CreationTask(**_with_datetime(data, "created_at", "updated_at"))

    @staticmethod
    def _topic_generation_run_from_payload(data: dict[str, object]) -> TopicGenerationRun:
        return TopicGenerationRun(
            **_with_datetime(data, "started_at", "finished_at")
        )

    @staticmethod
    def _topic_candidate_from_payload(data: dict[str, object]) -> TopicCandidate:
        return TopicCandidate(**_with_datetime(data, "created_at"))

    @staticmethod
    def _topic_evidence_reference_from_payload(
        data: dict[str, object],
    ) -> TopicEvidenceReference:
        return TopicEvidenceReference(**_with_datetime(data, "created_at"))

    @staticmethod
    def _script_generation_run_from_payload(
        data: dict[str, object],
    ) -> ScriptGenerationRun:
        return ScriptGenerationRun(
            **_with_datetime(data, "started_at", "finished_at")
        )

    @staticmethod
    def _creation_task_script_from_payload(
        data: dict[str, object],
    ) -> CreationTaskScript:
        section_payloads = data.pop("sections", [])
        return CreationTaskScript(
            **_with_datetime(data, "created_at", "updated_at", "confirmed_at"),
            sections=[
                CreationTaskScriptSection(**section)
                for section in section_payloads
                if isinstance(section, dict)
            ],
        )


def _json_payload(value: object) -> str:
    return json.dumps(asdict(value), ensure_ascii=False, default=_json_default)


def _authorization_payload(authorization: Authorization) -> dict[str, object]:
    return {
        "id": authorization.id,
        "version": authorization.version,
        "rights_holder": authorization.rights_holder,
        "content_types": list(authorization.content_types),
        "usage_scopes": list(authorization.usage_scopes),
        "effective_from": authorization.effective_from.isoformat(),
        "expires_at": authorization.expires_at.isoformat() if authorization.expires_at else None,
        "evidence_reference": authorization.evidence_reference,
        "status": authorization.status,
        "workspace_id": authorization.workspace_id,
    }


def _person_profile_payload(person: PersonProfile) -> dict[str, object]:
    return {
        "id": person.id,
        "workspace_id": person.workspace_id,
        "display_name": person.display_name,
        "version": person.version,
        "avatar_reference": person.avatar_reference,
        "background_capabilities": list(person.background_capabilities),
        "shot_capabilities": list(person.shot_capabilities),
        "authorization_id": person.authorization.id,
        "authorization_version": person.authorization.version,
    }


def _versioned_voice_profile_payload(voice: VersionedVoiceProfile) -> dict[str, object]:
    return {
        "id": voice.id,
        "workspace_id": voice.workspace_id,
        "person_profile_id": voice.person_profile_id,
        "person_version": voice.person_version,
        "version": voice.version,
        "language": voice.language,
        "reference_audio_path": voice.reference_audio_path,
        "reference_text": voice.reference_text,
        "model_id": voice.model_id,
        "authorization_id": voice.authorization.id,
        "authorization_version": voice.authorization.version,
    }


def _pronunciation_entry_payload(entry: PronunciationEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "workspace_id": entry.workspace_id,
        "term": entry.term,
        "language": entry.language,
        "target_pronunciation": entry.target_pronunciation,
        "scope": entry.scope,
        "scope_reference": entry.scope_reference,
        "version": entry.version,
    }


def _validate_authorization(authorization: Authorization) -> None:
    if not authorization.workspace_id.strip():
        raise ValueError("授权必须属于一个工作区")
    if not all(
        value.strip()
        for value in (
            authorization.id,
            authorization.version,
            authorization.rights_holder,
            authorization.evidence_reference,
        )
    ):
        raise ValueError("授权缺少必要标识或证据")
    if not authorization.content_types or not authorization.usage_scopes:
        raise ValueError("授权必须声明内容类型和用途范围")
    if authorization.status not in {"active", "pending", "expired", "revoked"}:
        raise ValueError("授权状态无效")


def _validate_media_preview_run(record: MediaPreviewRunRecord) -> None:
    if not record.workspace_id.strip() or not record.id.strip() or not record.identity.strip():
        raise ValueError("预览运行记录缺少工作区、ID 或幂等身份")
    if record.preview_type not in {"voice", "avatar_lipsync"}:
        raise ValueError("预览类型无效")
    if record.status not in {"running", "succeeded", "unavailable", "blocked", "failed"}:
        raise ValueError("预览运行状态无效")
    if not record.capability_id.strip() or not record.model_id.strip() or not record.language.strip():
        raise ValueError("预览运行记录缺少能力、模型或语言")
    if record.attempt < 1:
        raise ValueError("预览尝试序号必须从 1 开始")
    if record.status == "running":
        if record.finished_at is not None:
            raise ValueError("运行中的预览不能包含结束时间")
    elif record.status == "succeeded":
        if not record.output_path or record.finished_at is None:
            raise ValueError("成功预览必须包含输出引用和结束时间")
    elif (
        record.finished_at is None
        or not record.reason
        or not record.impact
        or not record.repair_action
    ):
        raise ValueError("终态失败或不可用预览必须包含结束时间、原因、影响和修复动作")


def _validate_person_profile(person: PersonProfile) -> None:
    if not all(
        value.strip()
        for value in (person.id, person.workspace_id, person.display_name, person.version, person.avatar_reference)
    ):
        raise ValueError("人物档案缺少必要标识或形象引用")
    if person.authorization.workspace_id != person.workspace_id:
        raise ValueError("人物授权不属于当前工作区")


def _validate_versioned_voice_profile(voice: VersionedVoiceProfile) -> None:
    if not all(
        value.strip()
        for value in (
            voice.id,
            voice.workspace_id,
            voice.person_profile_id,
            voice.person_version,
            voice.version,
            voice.reference_audio_path,
            voice.reference_text,
            voice.model_id,
        )
    ):
        raise ValueError("声音档案缺少必要标识或参考资料")
    if voice.language not in {"zh", "en", "zh-en"}:
        raise ValueError("声音语言必须是 zh、en 或 zh-en")
    if voice.authorization.workspace_id != voice.workspace_id:
        raise ValueError("声音授权不属于当前工作区")


def _validate_pronunciation_entry(entry: PronunciationEntry) -> None:
    if not all(
        value.strip()
        for value in (entry.id, entry.workspace_id, entry.term, entry.target_pronunciation, entry.version)
    ):
        raise ValueError("发音词典缺少必要标识或发音")
    if entry.language not in {"zh", "en", "zh-en"}:
        raise ValueError("发音词典语言必须是 zh、en 或 zh-en")
    if entry.scope not in {"workspace", "person", "task"}:
        raise ValueError("发音词典作用域无效")
    if entry.scope == "workspace" and entry.scope_reference is not None:
        raise ValueError("工作区发音词典不能绑定对象")
    if entry.scope != "workspace" and not (entry.scope_reference or "").strip():
        raise ValueError("对象或任务发音词典必须绑定作用域引用")


def _required_text(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"持久化媒体对象缺少 {key}")
    return value


def _string_tuple(data: dict[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"持久化媒体对象的 {key} 无效")
    return tuple(value)


def _optional_string_tuple(data: dict[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"持久化媒体对象的 {key} 无效")
    return tuple(value)


def _required_datetime(data: dict[str, object], key: str) -> datetime:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"持久化媒体对象缺少 {key}")
    return datetime.fromisoformat(value)


def _optional_datetime(data: dict[str, object], key: str) -> datetime | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"持久化媒体对象的 {key} 无效")
    return datetime.fromisoformat(value)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("画面与提示向量维度不一致")
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        raise ValueError("素材用途向量无效")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def effective_segment_usage_role(
    asset: MaterialAsset,
    segment: MaterialSegment,
) -> AssetUsageRole:
    return segment.usage_role if segment.usage_role != "unassigned" else asset.usage_role


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _with_datetime(data: dict[str, object], *keys: str) -> dict[str, object]:
    converted = dict(data)
    for key in keys:
        value = converted.get(key)
        if isinstance(value, str):
            converted[key] = datetime.fromisoformat(value)
    return converted


def _normalized_labels(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            value.strip()[:80]
            for value in values
            if value.strip()
        )
    )


def _infer_system_tags(
    asset: MaterialAsset,
    segment: MaterialSegment,
) -> list[str]:
    text = f"{asset.file_name} {segment.description} {segment.transcript}".lower()
    role = effective_segment_usage_role(asset, segment)
    if role == "unassigned":
        role = segment.suggested_usage_role
    tags = [f"用途:{role}"] if role != "unassigned" else []
    rules = {
        "场景:办公": ("办公", "office", "workspace", "desk"),
        "物体:电脑": ("电脑", "笔记本", "laptop", "computer", "keyboard"),
        "动作:打字": ("打字", "键盘", "typing", "keyboard"),
        "内容:软件界面": ("软件", "界面", "screen", "app", "website"),
        "场景:户外": ("户外", "街道", "风景", "outdoor", "street", "nature"),
        "主体:人物": ("人物", "人脸", "口播", "person", "face", "talking"),
        "镜头:特写": ("特写", "close-up", "closeup"),
    }
    for tag, keywords in rules.items():
        if any(keyword in text for keyword in keywords):
            tags.append(tag)
    return _normalized_labels(tags)


def _segment_id(asset_id: str, start: float, end: float) -> str:
    raw = f"{asset_id}:{start:.3f}:{end:.3f}"
    return f"seg_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _visual_fingerprint(
    path: Path,
    segment: MaterialSegment,
    model_id: str,
) -> str:
    stat = path.stat()
    raw = (
        f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:"
        f"{segment.start_seconds:.3f}:{segment.end_seconds:.3f}:{model_id}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _scene_ranges(boundaries: list[float], duration: float) -> list[tuple[float, float]]:
    clean = sorted({round(value, 3) for value in boundaries if 0 < value < duration})
    points = [0.0, *clean, round(duration, 3)]
    ranges: list[tuple[float, float]] = []
    for start, end in pairwise(points):
        cursor = start
        while end - cursor > 12:
            ranges.append((round(cursor, 3), round(cursor + 12, 3)))
            cursor += 12
        if end - cursor >= 0.75:
            ranges.append((round(cursor, 3), round(end, 3)))
        elif ranges:
            previous_start, _ = ranges[-1]
            ranges[-1] = (previous_start, round(end, 3))
    return ranges or [(0.0, round(duration, 3))]
