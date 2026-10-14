from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import sqlite3
import subprocess
import wave
from contextlib import closing, nullcontext
from contextvars import ContextVar
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from app.avatar_lipsync import (AvatarAndLipSyncRequest)
from app.creation.contracts import ClipSourceSelection
from app.profiles.contracts import (Authorization, PersonProfile, ProfileReference, profile_use_failure, _as_utc, PronunciationEntry, VoiceProfile as VersionedVoiceProfile)
from app.profiles.authorizations import MediaAuthorizationStore, _authorization_payload, _authorization_from_payload, _validate_authorization
from app.profiles.versioned_payloads import _save_versioned_payload, _list_versioned_payloads, _versioned_row
from app.profiles.records import (
    ProfileRecordStore, _person_profile_payload, _versioned_voice_profile_payload,
    _profile_reference_payload, _profile_reference_fields,
)
from app.database_migrations import (
    CONTENT_VISIBILITY_DEFAULT,
    LOCAL_ADOPTION_RECEIPTS_DDL,
    apply_production_migrations,
    ensure_content_visibility_columns,
)
from app.visual_understanding import LocalVisualUnderstanding, SegmentUnderstanding, OCR_RETRIEVAL_MIN_CONFIDENCE, searchable_ocr_text
from app.models import utc_now
from app.materials.business_objects import MaterialBusinessObjectStore
from app.materials.collections import MaterialCollectionStore
from app.materials.assets import MaterialAssetStore, MaterialVersionConflict, _normalized_labels, _infer_system_tags, _scene_ranges, _segment_id
from app.persistence_json import (
    _json_payload, _json_object, _json_default, _with_datetime,
    _required_text, _optional_string_tuple, _required_datetime, _optional_datetime,
)
from app.materials.contracts import (
    AssetReviewStatus,
    ContentVisibility,
    AssetUsageRole,
    SuggestedUsageRole,
    AnalysisStatus,
    MaterialMediaType,
    MATERIAL_MEDIA_LABEL,
    MATERIAL_MEDIA_BY_SUFFIX,
    BUSINESS_OBJECT_KINDS,
    ContentUseState,
    MaterialSegment,
    MaterialAsset,
    MaterialAssetNote,
    MaterialBusinessObject,
    MaterialCollection,
    MaterialSegmentMatch,
    material_media_type_for,
    material_media_type_label,
    material_allows_footage,
    material_allows_automation,
    effective_segment_usage_role,
    selectable_segment_usage_role,
)
from app.asr import SubtitleSegment as SpeechObservation
from app.material_speech import LocalMaterialSpeechAnalyzer, MaterialSpeechAnalysis, speech_text_for_range
# Only direct-link import admits non-video media. The directory scan whitelist
# (SUPPORTED_VIDEO_EXTENSIONS) is unchanged: nothing else starts watching these.
from app.video import SUPPORTED_VIDEO_EXTENSIONS
from app.visual_semantics import (
    SegmentWindow,
    UnavailableVisualSemanticProvider,
    VisualSemanticError,
    VisualSemanticProvider,
)
from app.material_probe import MaterialProbeFacts, probe_checkpoint_needs_rate_refresh
from app.media_link_import import RegisteredLink, _probe_file_media_type
from app.material_scene import (
    SCENE_LOGICAL_WINDOW_SECONDS, SceneWindow, SceneWindowFacts, scene_unit_key,
    scene_window_for_key, scene_windows,
)

if TYPE_CHECKING:
    from app.device_scheduler import Grant
    from app.profile_reference_media import ProfileReferenceInput, ProfileReferenceArtifact

logger = logging.getLogger(__name__)
EXACT_EDIT_SOURCE = ContextVar("exact_edit_source", default=None)


def content_owner(verified_user_id: str | None) -> str | None:
    """把一次已验证身份归一成内容的上传者标识。

    来源是 Supabase 已验证的 AuthPrincipal.user_id 或本地成员令牌解析出的
    member_id——两种既有身份在此归一，不新造第二套规则。dataclass 里已有的
    owner_user_id 不作数（那是调用方能自填的字段）。没有可解析身份时留空：
    这条内容不会被算成任何成员的私有资产。
    """
    return str(verified_user_id or "").strip() or None


def content_readable(
    *,
    user_id: str | None,
    local_diagnostic: bool,
    visibility: str,
    owner_user_id: str | None,
) -> bool:
    """内容可读判据。与知识域共用同一套规则（转发到 app.source_policy）。

    私有只对上传者本人可读，共享对成员可读；认证态缺可信身份不可读，不降级
    local；保留的 local workspace 受控诊断下本机私有照常可读（非成员隔离）。
    """
    from app.source_policy import knowledge_item_readable

    return knowledge_item_readable(
        user_id=user_id,
        local_diagnostic=local_diagnostic,
        visibility=visibility,
        owner_user_id=owner_user_id,
    )


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
MediaPreviewStatus = Literal["queued", "running", "succeeded", "unavailable", "blocked", "failed", "cancelled"]

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


def directory_accepts_writes(root: Path) -> bool:
    """Prove the directory accepts writes, because existence alone never promised that.

    A read-only mount still lists as a normal directory, so binding to one
    registers an intake that can never accept a file and hides the cause until
    a later import fails, and a first-run preflight would report the local video
    pipeline as ready. Creating and removing a hidden uniquely named file is the
    same proof the desktop import path already relies on, and every scanner that
    walks these directories skips anything without a supported media suffix.
    """
    probe = root / f".chatcut_write_probe_{uuid4().hex}"
    try:
        probe.touch(exist_ok=False)
        probe.unlink()
    except OSError:
        return False
    return True


@dataclass(frozen=True)
class MaterialHashCandidate:
    workspace_id: str
    asset_id: str
    file_path: str
    provisional_revision: str
    stage_input_digest: str
    unit_key: str


@dataclass(frozen=True)
class MaterialHashResult:
    candidate: MaterialHashCandidate
    source_sha256: str


@dataclass(frozen=True)
class MaterialProbeDependency:
    workspace_id: str
    asset_id: str
    source_sha256: str
    provisional_revision: str


@dataclass(frozen=True)
class MaterialProbeCandidate:
    workspace_id: str
    asset_id: str
    file_path: str
    source_sha256: str
    provisional_revision: str
    unit_key: str


@dataclass(frozen=True)
class MaterialSceneCandidate:
    workspace_id: str
    asset_id: str
    file_path: str
    source_sha256: str
    provisional_revision: str
    probe_result_ref: str
    video_stream_index: int
    video_codec: str
    video_width_px: int
    video_height_px: int
    source_frame_rate_fps: float | None
    duration_seconds: float
    size_bytes: int
    window: SceneWindow
    unit_key: str


def material_probe_unit_key(source_sha256: str, *, version: int = 1) -> str:
    """Identity for bounded ffprobe metadata only; scene analysis is separate."""
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("素材源哈希无效")
    if version not in {1, 2}:
        raise ValueError("素材探测契约版本无效")
    stage_input = hashlib.sha256(json.dumps(
        {"stage": f"probe-meta:v{version}", "source_sha256": source_sha256,
         "probe_contract": ("ffprobe-metadata:v1" if version == 1
                            else "ffprobe-metadata-rates:v2")},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return f"probe-meta:v{version}:{stage_input}"


def _material_hash_candidate(
    workspace_id: str, bound_path: str, path: Path, stat: os.stat_result, asset_id: str
) -> MaterialHashCandidate:
    revision_input = {
        "workspace_id": workspace_id,
        "bound_path": bound_path,
        "file_path": str(path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "inode": stat.st_ino,
    }
    provisional = hashlib.sha256(
        json.dumps(revision_input, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    stage_input = hashlib.sha256(
        json.dumps(
            {"stage": "hash:v1", "algorithm": "sha256", "provisional_revision": provisional},
            sort_keys=True, separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return MaterialHashCandidate(
        workspace_id, asset_id, str(path), provisional, stage_input,
        f"hash:v1:{stage_input}",
    )


_MATERIAL_COVERAGE_TERMS = {
    "主体:人物": ("主体:人物", "人物", "人像", "女性", "女人", "男子", "男人", "女孩", "男孩", "女士", "男士", "person", "people"),
    "场景:办公": ("场景:办公", "办公室", "办公环境", "office"),
    "场景:户外": ("场景:户外", "室外", "户外", "街道", "outdoor"),
    "物体:电脑": ("物体:电脑", "电脑", "笔记本", "计算机", "computer", "laptop"),
    "动作:打字": ("动作:打字", "打字", "typing"),
    "内容:软件界面": ("内容:软件界面", "软件界面", "应用界面", "app interface"),
    "镜头:特写": ("镜头:特写", "特写", "close-up"),
}

def material_segment_eligible(asset, segment, workspace_id, *, automation_only=False):
    """Metadata/file qualification; deep decoding remains under the executing Grant."""
    if (asset.workspace_id != workspace_id or asset.review_status == "rejected"
            or segment.asset_id != asset.id or segment.review_status == "rejected"
            or not (material_allows_automation(asset) if automation_only else material_allows_footage(asset))
            or asset.analysis_status != "ready" or asset.media_type != "video"
            or effective_segment_usage_role(asset, segment) not in {"a_roll", "b_roll"}
            or asset.duration_seconds is None or not math.isfinite(asset.duration_seconds)
            or not (math.isfinite(segment.start_seconds) and math.isfinite(segment.end_seconds)
                    and 0 <= segment.start_seconds < segment.end_seconds <= asset.duration_seconds)):
        return False
    path = Path(asset.path)
    try:
        stat = path.stat()
        return (path.is_file() and stat.st_size > 0 and stat.st_size == asset.size_bytes
                and abs(stat.st_mtime - asset.modified_at.timestamp()) < 0.001)
    except OSError:
        return False


def automatic_segment_eligible(asset, segment, workspace_id):
    return material_segment_eligible(asset, segment, workspace_id, automation_only=True)


def _material_analysis_public_error(error: Exception) -> str:
    if isinstance(error, PermissionError):
        return "素材文件没有读取权限"
    if isinstance(error, FileNotFoundError):
        return "素材文件已移动或外接盘暂不可用"
    if isinstance(error, OSError):
        return "素材文件无法读取"
    if isinstance(error, subprocess.SubprocessError):
        return "媒体探测或解码失败，请检查文件格式"
    if isinstance(error, VisualSemanticError):
        return "画面分析模型不可用或执行失败"
    if isinstance(error, ValueError):
        return "媒体格式或内容无效"
    return "分析服务未能完成，请查看本机日志"


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
    default_voice_profile_id: str | None = None
    default_voice_version: str | None = None
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
    sample_rate: int | None
    model_id: str | None
    episode_id: str | None = None
    task_id: str | None = None
    language: str = "zh"
    pronunciation_version: str = ""
    source_kind: Literal["generated", "recorded"] = "generated"
    source_artifact_ref: str | None = None
    source_sha256: str | None = None
    source_script_digest: str | None = None
    created_at: datetime = field(default_factory=utc_now)


NarrationSegmentStatus = Literal["pending", "generating", "ready", "failed"]


@dataclass(frozen=True)
class NarrationSegment:
    id: str
    workspace_id: str
    task_id: str
    script_id: str
    source_section_id: str
    text: str
    audio_reference: str | None
    duration_seconds: float | None
    sample_rate: int | None
    model_id: str | None
    voice_profile_id: str | None
    voice_version: str | None
    language: str
    pronunciation_version: str
    status: NarrationSegmentStatus
    source_kind: Literal["generated", "recorded"] = "generated"
    source_artifact_ref: str | None = None
    source_sha256: str | None = None
    source_script_digest: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


PersonRenderStatus = Literal["pending", "running", "ready", "failed"]


@dataclass(frozen=True)
class PersonRender:
    id: str
    workspace_id: str
    task_id: str
    person_profile_id: str
    person_version: str
    voice_profile_id: str
    voice_version: str
    narration_segment_id: str
    visual_reference: str
    model_id: str
    motion: str
    expression: str
    cache_key: str
    status: PersonRenderStatus
    artifact_ref: str | None = None
    output_path: str | None = None
    duration_seconds: float | None = None
    alignment_evidence: str | None = None
    failure_reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


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
    member_identity: dict = field(default_factory=dict)
    preview_input: dict = field(default_factory=dict)
    input_digest: str = ""
    device_request_id: str | None = None



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
class CreationPlanPersonReference:
    profile_id: str
    version: str

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or not self.version.strip():
            raise ValueError("人物选择必须固定档案ID与版本")


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
    selected_person: CreationPlanPersonReference | None = None
    presenter_share_target: int = 0
    lipsync_quality: Literal["high", "fast"] = "fast"
    template_id: str | None = None
    template_version: int | None = None
    template_settings: dict[str, object] = field(default_factory=dict)
    material_matches: list[MaterialSegmentMatch] = field(default_factory=list)
    status: Literal["confirmed"] = "confirmed"
    creation_task_id: str | None = None
    visibility: ContentVisibility = CONTENT_VISIBILITY_DEFAULT
    owner_user_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    confirmed_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class VideoTemplate:
    id: str
    workspace_id: str
    version: int
    name: str
    origin: Literal["built_in", "user"]
    settings: dict[str, object]
    source_task_id: str | None = None
    source_timeline_version_id: str | None = None
    visibility: ContentVisibility = CONTENT_VISIBILITY_DEFAULT
    owner_user_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)


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
    current_timeline_version_id: str | None = None
    selected_topic_id: str | None = None
    confirmed_script_id: str | None = None
    narration_cache_key: str | None = None
    montage_plan_id: str | None = None
    output_ready: bool = False
    stage_reason: str | None = None
    stage_impact: str | None = None
    stage_repair_action: str | None = None
    stage_retryable: bool | None = None
    visibility: ContentVisibility = CONTENT_VISIBILITY_DEFAULT
    owner_user_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def needs_script_duration_repair(self) -> bool:
        return (
            self.stage == "narration" and self.stage_status == "failed"
            and self.stage_reason == "narration_generation_failed"
            and "压缩" in (self.stage_repair_action or "")
            and "文案" in (self.stage_repair_action or "")
        )

    @property
    def can_revise_script(self) -> bool:
        return bool(
            self.confirmed_script_id and not self.narration_cache_key
            and not self.montage_plan_id and not self.current_timeline_version_id
            and ((self.stage == "script" and self.stage_status == "failed")
                 or self.needs_script_duration_repair)
        )


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
    run_kind: Literal["generation", "assistant_reply_transfer"] = "generation"
    source_reply_reference: dict[str, str] | None = None


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
    language: Literal["zh-CN", "en-US", "zh-CN/en-US"]
    sections: list[CreationTaskScriptSection]
    selection_status: ScriptSelectionStatus = "draft"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    confirmed_at: datetime | None = None
    # 复用稿没有自己的生成记录，这里记下它来自文案库的哪一条，供界面如实标注。
    reused_from_entry_id: str | None = None
    source_reply_reference: dict[str, str] | None = None


@dataclass
class ScriptLibrarySection:
    """库条目里冻结的一段文案。

    只保存用户自己写的正文与画面建议。资料标题、来源地址、授权字段一律不落库：
    那些是授权校验之后才可读的内容，存进库里就等于给快照开了一条绕过当前
    workspace 权限的路。出处只留 reference_ids，用来回查时定位。
    """

    id: str
    title: str
    content: str
    visual_prompt: str
    reference_ids: list[str]


@dataclass
class ScriptLibraryEntry:
    """一条命名保存的文案：可搜索、可复用，关联原创作任务。

    每次保存都追加一行，version 递增并指向被取代的那一条，旧版本原样留着。
    不静默合并，也就不会把用户已经保存过的文案悄悄改掉。
    """

    id: str
    workspace_id: str
    name: str
    category: str
    source_script_id: str
    source_task_id: str
    source_run_id: str
    source_topic_id: str
    version: int
    supersedes_entry_id: str | None
    title: str
    language: Literal["zh-CN", "en-US", "zh-CN/en-US"]
    sections: list[ScriptLibrarySection]
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


# Public imports retained while downstream creation consumers migrate.
from app.creation.contracts import (
    JobRunStatus,
    TimelineVersion,
    SubtitleTrackStatus,
    SubtitleCue,
    SubtitleTrack,
    CoverAsset,
    JobRun,
    ArtifactRecord,
    TaskEvent,
    TransformRunStatus,
    TransformRun,
    DependencyEdge,
    EditIntent,
    EditProposalStatus,
    EditProposal,
    EditOperationStatus,
    EditOperationType,
    SUPPORTED_EDIT_OPERATION_TYPES,
    EditOperation
)


class TimelineVersionConflict(ValueError):
    """The original timeline changed before an atomic edit or preview submission."""


class CreationRequestConflict(ValueError):
    """A request/window identity or revision no longer matches its frozen input."""


class ProductionRepository:
    """Workspace-scoped persistence for the CreationTask production foundation.

    Legacy tables remain migration inventory only; TimelineVersion, JobRun,
    ArtifactRecord and TaskEvent are authoritative for the new task runtime.
    """

    def __init__(
        self,
        database_path: str | Path,
        video_library_path: str | Path,
        duration_reader: Callable[[Path], float] | None = None,
        scene_reader: Callable[[Path, float], list[float]] | None = None,
        visual_provider: VisualSemanticProvider | None = None,
        speech_analyzer: LocalMaterialSpeechAnalyzer | None = None,
        visual_understanding: LocalVisualUnderstanding | None = None,
    ) -> None:
        self.database_path = Path(database_path).expanduser()
        self.video_library_path = Path(video_library_path).expanduser()
        self.duration_reader = duration_reader or self._probe_duration
        self.scene_reader = scene_reader or self._detect_scene_boundaries
        self.visual_provider = visual_provider or UnavailableVisualSemanticProvider()
        self.speech_analyzer = speech_analyzer
        self.visual_understanding = visual_understanding
        self._usage_role_prompt_vectors: list[list[float]] | None = None
        self._material_business_objects = MaterialBusinessObjectStore(self._connect)
        self._material_collections = MaterialCollectionStore(self._connect)
        self._material_assets = MaterialAssetStore(self._connect)
        self._initialize_profile_stores()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def _initialize_profile_stores(self) -> None:
        """Bind only profile owners; do not open connections or provision schema."""
        self._media_authorizations = MediaAuthorizationStore(self._connect)
        self._profile_records = ProfileRecordStore(self._connect, self._media_authorizations)

    @classmethod
    def plan_local_adoption(
        cls, database_path: str | Path, video_library_path: str | Path, *,
        member_id: str, operation_id: str, asset_ids: list[str],
        voice_id: str, voice_version: str, new_voice_version: str,
        authorization_basis: str,
    ) -> dict:
        """Explicit read-only maintenance plan, with no startup provisioning."""
        repository = _local_adoption_repository(cls, database_path, video_library_path)
        with closing(sqlite3.connect(repository.database_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            manifest, _ = _local_adoption_plan(repository, conn,
                member_id=member_id, operation_id=operation_id, asset_ids=asset_ids,
                voice_id=voice_id, voice_version=voice_version,
                new_voice_version=new_voice_version, authorization_basis=authorization_basis)
            return manifest

    @classmethod
    def apply_local_adoption(
        cls, database_path: str | Path, video_library_path: str | Path, *,
        member_id: str, manifest: dict, manifest_sha256: str,
    ) -> dict:
        """CAS all selected inputs; receipt and content commit as one transaction."""
        if _local_adoption_digest(manifest) != manifest_sha256:
            raise ValueError("local_adoption_manifest_digest_mismatch")
        if manifest.get("workspace_id") != "local" or manifest.get("member_id") != member_id:
            raise ValueError("local_adoption_target_mismatch")
        repository = _local_adoption_repository(cls, database_path, video_library_path)
        with closing(sqlite3.connect(repository.database_path.resolve().as_uri() + "?mode=rw", uri=True)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            _local_adoption_target(repository, conn, member_id)
            if manifest.get("database_identity") != _local_adoption_database_identity(repository):
                raise ValueError("local_adoption_database_changed")
            if manifest.get("media_root") != str(repository.video_library_path.resolve()):
                raise ValueError("local_adoption_root_changed")
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_adoption_receipts'").fetchone()
            if exists:
                receipt = conn.execute("SELECT manifest_sha256, result_json FROM local_adoption_receipts WHERE operation_id=?",
                                       (manifest["operation_id"],)).fetchone()
                if receipt is not None:
                    if receipt["manifest_sha256"] != manifest_sha256:
                        raise ValueError("local_adoption_operation_conflict")
                    return json.loads(receipt["result_json"])
            current, originals = _local_adoption_plan(repository, conn,
                member_id=member_id, operation_id=manifest["operation_id"],
                asset_ids=[item["id"] for item in manifest["assets"]],
                voice_id=manifest["voice"]["id"], voice_version=manifest["voice"]["version"],
                new_voice_version=manifest["voice"]["new_version"],
                authorization_basis=manifest["authorization_basis"])
            if current != manifest:
                raise ValueError("local_adoption_inputs_changed")
            now = utc_now().isoformat()
            conn.execute(LOCAL_ADOPTION_RECEIPTS_DDL)
            after = []
            for row in originals["assets"]:
                payload = json.loads(row["payload"])
                payload.update(owner_user_id=member_id, visibility="private")
                serialized = json.dumps(payload, ensure_ascii=False)
                repository._material_assets.privatize_local_adoption_asset(conn, row["id"], serialized)
                after.append({**row, "payload": serialized, "visibility": "private"})
            for reference in current["references"]:
                if reference["before_sha256"] is None:
                    conn.execute("INSERT INTO workspace_media_references(reference,workspace_id,updated_at) VALUES (?,'local',?)",
                                 (reference["reference"], now))
            source = current["voice"]["source"]
            # The new source is generated from the verified WAV facts, never an external body.
            source_asset = MaterialAsset(id=source["id"], workspace_id="local",
                file_name=Path(originals["voice_path"]).name, path=originals["voice_path"],
                size_bytes=source["file_revision"]["size_bytes"],
                modified_at=datetime.fromtimestamp(source["file_revision"]["mtime_ns"] / 1e9),
                media_type="audio", duration_seconds=source["duration_seconds"],
                owner_user_id=member_id, visibility="private", created_at=utc_now(), updated_at=utc_now())
            source_payload = _json_payload(source_asset)
            repository._material_assets.insert_local_voice_source_asset(conn, source_asset, source_payload, now)
            after.append({"id": source_asset.id, "workspace_id": "local", "file_path": source_asset.path,
                          "payload": source_payload, "visibility": "private", "updated_at": now})
            # Preserve every historical field; v1 stays byte-for-byte in its original row.
            voice_payload = json.loads(originals["voice"]["payload"])
            voice_payload.update(version=current["voice"]["new_version"], owner_user_id=member_id,
                                 visibility="private", reference=asdict(ProfileReference(asset_id=source_asset.id)))
            serialized_voice = json.dumps(voice_payload, ensure_ascii=False)
            conn.execute("INSERT INTO versioned_voice_profiles(workspace_id,id,version,payload,created_at) VALUES ('local',?,?,?,?)",
                         (current["voice"]["id"], current["voice"]["new_version"], serialized_voice, now))
            # Files are outside SQLite. Recheck after all writes, immediately before the commit.
            for item in [*current["assets"], source]:
                path = repository.resolve_workspace_media_reference("local", item["reference"])
                if _local_adoption_file_revision(path) != item["file_revision"]:
                    raise ValueError("local_adoption_file_changed")
            result = {"operation_id": current["operation_id"], "workspace_id": "local", "member_id": member_id,
                      "manifest_sha256": manifest_sha256, "asset_ids": [item["id"] for item in current["assets"]],
                      "voice_id": current["voice"]["id"], "voice_version": current["voice"]["new_version"],
                      "voice_source_asset_id": source_asset.id, "applied_at": now}
            private_snapshot = {"before": originals, "after_assets": after,
                                "after_voice_payload": serialized_voice, "approved_manifest": current}
            conn.execute("INSERT INTO local_adoption_receipts VALUES (?,'local',?,?,?,?,?)",
                         (current["operation_id"], member_id, manifest_sha256,
                          json.dumps(private_snapshot, ensure_ascii=False), json.dumps(result, ensure_ascii=False), now))
            return result

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

    def validate_material_reference_use(self, workspace_id: str, reference: str) -> None:
        """Check current material restrictions for a source retained in artifact lineage."""
        path = self.resolve_workspace_media_reference(workspace_id, reference)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM material_assets WHERE workspace_id = ? AND file_path = ?",
                (workspace_id, str(path)),
            ).fetchone()
        if row is not None and not material_allows_footage(self._asset_from_payload(json.loads(row["payload"]))):
            raise ValueError("来源素材使用状态不允许进入成片，请移除对应音频或在素材详情修改使用状态")

    def import_voice_reference_wav(
        self,
        workspace_id: str,
        content: bytes,
        *, verified_owner_user_id: str | None = None,
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
            # Use the existing material ownership/source record, not an unowned
            # logical-path claim as permission to another member's reference.
            stat = target.stat()
            self._save_asset(MaterialAsset(
                id=f"voice_source_{uuid4().hex}", workspace_id=workspace_id,
                file_name=target.name, path=str(target), size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime), media_type="audio",
                duration_seconds=duration_seconds,
                owner_user_id=content_owner(verified_owner_user_id),
            ))
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
        *, connection: sqlite3.Connection | None = None,
    ) -> str:
        asset = self.get_asset(workspace_id, asset_id, connection=connection)
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
        *, connection: sqlite3.Connection | None = None,
    ) -> Path:
        asset = self.get_asset(workspace_id, asset_id, connection=connection)
        if not material_allows_footage(asset):
            raise MediaAuthorityError(reason="material_use_restricted",
                impact="素材已设为仅参考或不使用，未传给渲染器。",
                repair_action="替换这个镜头，或在素材详情明确修改使用状态。")
        expected_reference = self.workspace_media_reference_for_asset(
            workspace_id,
            asset_id, connection=connection,
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
                CREATE TABLE IF NOT EXISTS material_ingestion_binding (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    workspace_id TEXT NOT NULL,
                    library_path TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS material_ingestion_files (
                    workspace_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    ctime_ns INTEGER NOT NULL DEFAULT 0,
                    inode INTEGER NOT NULL DEFAULT 0,
                    observed_at REAL NOT NULL,
                    completed_digest TEXT,
                    attempted_digest TEXT,
                    state TEXT NOT NULL,
                    copy_uploader_user_id TEXT,
                    PRIMARY KEY (workspace_id, file_path)
                );
                CREATE TABLE IF NOT EXISTS material_unmatched_corrections (
                    workspace_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    preserved_at TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, asset_id, segment_id)
                );
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
                CREATE TABLE IF NOT EXISTS business_objects (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('product', 'dish', 'person')),
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_business_objects_workspace
                    ON business_objects(workspace_id, created_at);
                CREATE TABLE IF NOT EXISTS material_business_objects (
                    workspace_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    business_object_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, asset_id, business_object_id)
                );
                CREATE INDEX IF NOT EXISTS idx_material_business_objects_object
                    ON material_business_objects(workspace_id, business_object_id);
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
                CREATE TABLE IF NOT EXISTS video_templates (
                    workspace_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, id, version)
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
                CREATE TABLE IF NOT EXISTS script_library_entries (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    source_task_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_script_library_workspace
                    ON script_library_entries(workspace_id, source_task_id, name, created_at);
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
            apply_production_migrations(conn)
            ingestion_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(material_ingestion_files)")
            }
            if "ctime_ns" not in ingestion_columns:
                conn.execute("ALTER TABLE material_ingestion_files ADD COLUMN ctime_ns INTEGER NOT NULL DEFAULT 0")
            if "inode" not in ingestion_columns:
                conn.execute("ALTER TABLE material_ingestion_files ADD COLUMN inode INTEGER NOT NULL DEFAULT 0")
            if "copy_uploader_user_id" not in ingestion_columns:
                conn.execute("ALTER TABLE material_ingestion_files ADD COLUMN copy_uploader_user_id TEXT")
            # 保留历史绑定者记录；目录绑定不决定逐文件上传者。
            binding_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(material_ingestion_binding)")
            }
            if "intake_owner_user_id" not in binding_columns:
                conn.execute("ALTER TABLE material_ingestion_binding ADD COLUMN intake_owner_user_id TEXT")
            # 内容可见性列：与知识资料共用同一个 PRAGMA 守卫 ensure，新增行由仓储层
            # 显式写入，新建默认 private，存量行回填 shared 不改原语义。
            ensure_content_visibility_columns(conn)

    def save_authorization(self, authorization: Authorization) -> Authorization:
        return self._media_authorizations.save_authorization(authorization)

    def get_authorization(
        self,
        workspace_id: str,
        authorization_id: str,
        version: str | None = None,
    ) -> Authorization:
        return self._media_authorizations.get_authorization(workspace_id, authorization_id, version)

    def list_authorizations(self, workspace_id: str) -> list[Authorization]:
        return self._media_authorizations.list_authorizations(workspace_id)

    def list_authorization_versions(
        self,
        workspace_id: str,
        authorization_id: str,
    ) -> list[Authorization]:
        return self._media_authorizations.list_authorization_versions(workspace_id, authorization_id)

    def resolve_profile_reference(
        self, profile: PersonProfile | VersionedVoiceProfile, *,
        viewer_user_id: str | None = None, local_diagnostic: bool = False,
        for_generation: bool = True,
    ) -> tuple[Path, float | None, float | None]:
        path, start, end = self._profile_reference_source(profile, viewer_user_id=viewer_user_id,
            local_diagnostic=local_diagnostic, for_generation=for_generation)
        if for_generation and start is not None:
            if not profile.reference.asset_id:
                raise ValueError("精确派生参考需要稳定来源素材")
            asset = self.get_asset(profile.workspace_id, profile.reference.asset_id)
            if start != 0 or end != asset.duration_seconds:
                raise ValueError("参考范围需要精确派生输入，不能用整片替代所选范围")
        return path, start, end

    def _profile_reference_source(
        self, profile: PersonProfile | VersionedVoiceProfile, *,
        viewer_user_id: str | None, local_diagnostic: bool, for_generation: bool,
        probe_media: bool = True,
    ) -> tuple[Path, float | None, float | None]:
        """Re-read the source, membership and range; registration never runs a model."""
        with self._connect() as conn:
            row = self._profile_records._latest_profile_payload_row(
                conn, profile.workspace_id, profile.id, person=isinstance(profile, PersonProfile))
            current = ((self._person_profile_from_payload if isinstance(profile, PersonProfile)
                        else self._versioned_voice_profile_from_payload)(conn, profile.workspace_id, json.loads(row["payload"]))
                       if row is not None else profile)
            if row is not None:
                current = replace(current, authorization=self._current_authorization(conn, profile.workspace_id, current.authorization))
        if not content_readable(user_id=viewer_user_id, local_diagnostic=local_diagnostic,
                                visibility=current.visibility, owner_user_id=current.owner_user_id):
            raise PermissionError("档案不属于当前成员或已取消共享")
        if not content_readable(user_id=viewer_user_id, local_diagnostic=local_diagnostic,
                                visibility=profile.visibility, owner_user_id=profile.owner_user_id):
            raise PermissionError("档案不属于当前成员或已取消共享")
        if profile.reference_issue is not None:
            raise ValueError(f"历史参考不能可靠映射，请重新定位：{profile.reference_issue}")
        reference = profile.reference
        if reference is not None:
            _validate_profile_reference_fields(profile)
        logical = (profile.avatar_reference if isinstance(profile, PersonProfile)
                   else profile.reference_audio_path)
        path = self.resolve_workspace_media_reference(profile.workspace_id, logical,
                                                       require_registered=reference is None or not reference.asset_id)
        asset = None
        if reference is not None and reference.asset_id:
            asset = self.get_asset(profile.workspace_id, reference.asset_id)
            if self.workspace_media_reference_for_asset(profile.workspace_id, asset.id) != logical:
                raise ValueError("参考文件与来源素材不一致")
        else:
            with self._connect() as conn:
                row = self._material_assets._asset_payload_row_by_path(conn, profile.workspace_id, str(path))
            if row is not None:
                asset = self._asset_from_row(row)
        if asset is not None and not content_readable(user_id=viewer_user_id, local_diagnostic=local_diagnostic,
                                                      visibility=asset.visibility, owner_user_id=asset.owner_user_id):
            raise PermissionError("来源素材不属于当前成员或已取消共享")
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("参考文件失联或为空，请重新定位原参考")
        if asset is not None:
            if abs(path.stat().st_mtime - asset.modified_at.timestamp()) > .00001:
                raise ValueError("原参考已变化，请重新定位并登记新版本")
            if asset.media_type == "audio" and path.suffix.lower() == ".wav":
                intact = path.stat().st_size == asset.size_bytes
                _profile_wav_duration(path)
            else:
                intact = (self._material_file_intact(asset) if probe_media else
                          not path.is_symlink() and path.stat().st_size == asset.size_bytes)
            if not intact:
                raise ValueError("原参考已变化，请重新定位并登记新版本")
            if isinstance(profile, PersonProfile) and asset.media_type not in {"video", "image"}:
                raise ValueError("人物参考需要已登记的视频或图片")
            if isinstance(profile, VersionedVoiceProfile) and asset.media_type not in {"audio", "video"}:
                raise ValueError("声音参考需要已登记音频或视频")
            if asset.use_state == "excluded" or (for_generation and asset.use_state not in {"available", "not_for_publish"}):
                raise MediaAuthorityError(reason=f"source_{asset.use_state}",
                    impact="来源素材的使用限制阻断本次预览或生成，未采用该参考。",
                    repair_action=f"来源素材使用状态限制：{asset.use_state}；请在来源详情确认允许的用途或选择其他参考。")
            duration = asset.duration_seconds
            if asset.media_type != "image" and (duration is None or not math.isfinite(duration) or duration <= 0):
                raise ValueError("来源时长未知，请重新定位有效参考范围")
            if reference is not None and reference.segment_id:
                segment = next((item for item in asset.segments if item.id == reference.segment_id), None)
                if segment is None or segment.asset_id != asset.id:
                    raise ValueError("参考片段已失联，请重新选择")
                if reference.start_seconds is None or reference.end_seconds is None:
                    raise ValueError("片段参考需要明确起止时间")
                if reference.start_seconds < segment.start_seconds or reference.end_seconds > segment.end_seconds:
                    raise ValueError("参考范围超出来源片段")
                if segment.usage_role == "exclude":
                    raise ValueError("来源片段已排除使用")
        else:
            if isinstance(profile, PersonProfile):
                raise ValueError("人物参考缺少稳定素材登记，请重新定位")
            duration = _profile_wav_duration(path)
            if not local_diagnostic:
                raise PermissionError("历史声音来源缺少成员归属，请重新导入参考")
        if isinstance(profile, VersionedVoiceProfile) and path.suffix.lower() == ".wav":
            duration = _profile_wav_duration(path)
        start = reference.start_seconds if reference else None
        end = reference.end_seconds if reference else None
        if start is not None and (duration is None or end is None or end > duration):
            raise ValueError("参考范围超出有效来源时长")
        if for_generation:
            for version in (profile, current):
                failure = profile_use_failure(version, content_type=("person_likeness" if isinstance(profile, PersonProfile) else "voice"),
                                              usage_scope=("avatar_lipsync" if isinstance(profile, PersonProfile) else "narration"))
                if failure is not None:
                    raise ValueError(f"参考使用受限：{failure}")
        return path, start, end

    def profile_reference_input(self, profile: PersonProfile | VersionedVoiceProfile, *,
                                viewer_user_id: str | None = None, local_diagnostic: bool = False,
                                for_generation: bool = True) -> ProfileReferenceInput:
        """Qualified source facts only; never decode/derive during device admission."""
        from app.profile_reference_media import ProfileReferenceInput
        path, start, end = self._profile_reference_source(profile, viewer_user_id=viewer_user_id,
            local_diagnostic=local_diagnostic, for_generation=for_generation, probe_media=False)
        reference = profile.reference
        asset = self.get_asset(profile.workspace_id, reference.asset_id) if reference and reference.asset_id else None
        kind = "voice_audio" if isinstance(profile, VersionedVoiceProfile) else "person_visual"
        if kind == "voice_audio" and asset and asset.media_type not in {"audio", "video"}:
            raise ValueError("声音参考需要有效音频或视频的声音范围")
        duration = asset.duration_seconds if asset else _profile_wav_duration(path)
        return ProfileReferenceInput.from_source(
            workspace_id=profile.workspace_id, profile_id=profile.id, profile_version=profile.version,
            asset_id=asset.id if asset else None, segment_id=reference.segment_id if reference else None,
            source_path=path, source_type=asset.media_type if asset else "audio", kind=kind,
            start_seconds=start, end_seconds=end, source_duration_seconds=duration)

    def materialize_profile_reference(self, profile: PersonProfile | VersionedVoiceProfile, *,
                                      grant_context, viewer_user_id: str | None = None,
                                      local_diagnostic: bool = False) -> ProfileReferenceArtifact:
        from app.profile_reference_media import materialize_reference
        if grant_context is None:
            raise PermissionError("精确参考派生需要现有设备执行许可")
        grant_context.check()
        if grant_context.grant.workspace_id != profile.workspace_id:
            raise PermissionError("参考派生许可工作区不匹配")
        expected_stages = (("person_render:", "person_edit:") if isinstance(profile, PersonProfile)
                           else ("narration:", "narration_edit:"))
        if (not ((grant_context.grant.owner_type == "creation_job"
                      and grant_context.grant.unit_key.startswith(expected_stages))
                     or (grant_context.grant.owner_type == "media_preview"
                         and grant_context.grant.unit_key.startswith("avatar_lipsync:" if isinstance(profile, PersonProfile) else "voice:")))):
            raise PermissionError("参考派生需要对应声音或人物阶段的专属许可")
        source = self.profile_reference_input(profile, viewer_user_id=viewer_user_id,
                                              local_diagnostic=local_diagnostic)
        def recheck():
            grant_context.check()
            if self.profile_reference_input(profile, viewer_user_id=viewer_user_id,
                                             local_diagnostic=local_diagnostic) != source:
                raise ValueError("参考源修订已变化，请重新排队")
        return materialize_reference(source, output_root=self.video_library_path / ".profile_references",
                                     grant_context=grant_context, recheck=recheck)

    def _require_profile_write_owner(self, conn, table: str, profile, verified_owner_user_id, local_diagnostic):
        return self._profile_records._require_profile_write_owner(conn, table, profile, verified_owner_user_id, local_diagnostic)

    def resolve_voice_reference(self, voice: VersionedVoiceProfile, *,
                                viewer_user_id: str | None = None,
                                local_diagnostic: bool = False, grant_context=None) -> Path:
        if grant_context is not None:
            return self.materialize_profile_reference(voice, grant_context=grant_context,
                viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic).path
        path, _, _ = self.resolve_profile_reference(voice, viewer_user_id=viewer_user_id,
                                                   local_diagnostic=local_diagnostic)
        _profile_wav_duration(path)
        return path

    @staticmethod
    def profile_readable(profile, user_id, local_diagnostic=False):
        return content_readable(user_id=user_id, local_diagnostic=local_diagnostic, visibility=profile.visibility, owner_user_id=profile.owner_user_id)

    def current_profile_authorization(self, profile):
        return self.get_authorization(profile.workspace_id, profile.authorization.id) if profile.authorization else None

    def set_default_versioned_voice(self, workspace_id: str, voice_profile_id: str, version: str, *,
                                    viewer_user_id: str | None = None, local_diagnostic: bool = False) -> VersionedVoiceProfile:
        if not voice_profile_id.strip() or not version.strip():
            raise ValueError("默认声音必须指定准确档案与版本")
        voice = self.get_versioned_voice_profile(workspace_id, voice_profile_id, version)
        # A default can be stored without a model, but must point at a readable real reference.
        self.resolve_profile_reference(voice, viewer_user_id=viewer_user_id,
                                       local_diagnostic=local_diagnostic, for_generation=False)
        profile = self.get_profile(workspace_id)
        profile.default_voice_profile_id, profile.default_voice_version = voice.id, voice.version
        self.save_profile(profile)
        return voice

    def get_default_versioned_voice(self, workspace_id: str) -> VersionedVoiceProfile | None:
        profile = self.get_profile(workspace_id)
        if profile.default_voice_profile_id is None and profile.default_voice_version is None:
            return None
        if not profile.default_voice_profile_id or not profile.default_voice_version:
            raise ValueError("默认声音引用不完整，请重新选择准确版本")
        return self.get_versioned_voice_profile(workspace_id, profile.default_voice_profile_id,
                                                profile.default_voice_version)

    def creation_plan_voice_reference(self, workspace_id, profile_id, version, *, viewer_user_id=None, local_diagnostic=False):
        voice = self.get_versioned_voice_profile(workspace_id, profile_id, version)
        self.profile_reference_input(voice, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
        auth = self.current_profile_authorization(voice)
        return CreationPlanVoiceReference(profile_id=voice.id, version=voice.version,
            person_profile_id=voice.person_profile_id, person_version=voice.person_version,
            person_authorization_id="", person_authorization_version="",
            authorization_id=auth.id if auth else "", authorization_version=auth.version if auth else "",
            language=voice.language, model_id=voice.model_id)

    def automatic_material_segments(self, workspace_id, *, viewer_user_id=None, local_diagnostic=False):
        result = []
        for asset in self.list_assets(workspace_id, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic):
            try:
                self.resolve_workspace_media_reference(workspace_id, self.workspace_media_reference_for_asset(workspace_id, asset.id), require_registered=True)
            except (ValueError, KeyError, OSError):
                continue
            result.extend((asset, segment) for segment in asset.segments if automatic_segment_eligible(asset, segment, workspace_id))
        return result

    def creation_plan_person_reference(self, workspace_id: str, profile_id: str, version: str, *,
                                       viewer_user_id: str | None = None, local_diagnostic: bool = False) -> CreationPlanPersonReference:
        selected = CreationPlanPersonReference(profile_id, version)
        person = self.get_person_profile(workspace_id, selected.profile_id, selected.version)
        self.profile_reference_input(person, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
        return selected

    def _retain_profile_authorization(self, table, profile):
        with self._connect() as conn:
            return self._profile_records._retain_profile_authorization(conn, table, profile)

    def save_person_profile(
        self, person: PersonProfile, *, verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> PersonProfile:
        person = self._retain_profile_authorization("person_profiles", person)
        _validate_person_profile(person)
        if person.reference is not None:
            person = replace(person, owner_user_id=content_owner(verified_owner_user_id))
            self.resolve_profile_reference(person, viewer_user_id=verified_owner_user_id,
                                           local_diagnostic=local_diagnostic, for_generation=False)
            if person.reference.asset_id is None:
                path = self.resolve_workspace_media_reference(person.workspace_id, person.avatar_reference)
                asset = self._asset_by_path(person.workspace_id, str(path))
                person = replace(person, reference=replace(person.reference, asset_id=asset.id))
        with self._connect() as conn:
            if person.reference is not None:
                self._require_profile_write_owner(conn, "person_profiles", person, verified_owner_user_id, local_diagnostic)
            if person.avatar_reference.startswith("workspace://"):
                self.resolve_workspace_media_reference(
                    person.workspace_id,
                    person.avatar_reference,
                )
                self._claim_workspace_media_reference(
                    conn,
                    workspace_id=person.workspace_id,
                    reference=person.avatar_reference,
                )
            if person.authorization is not None:
                self._save_authorization(conn, person.workspace_id, person.authorization)
            self._profile_records._save_person_profile(conn, person)
        return person

    def get_person_profile(
        self,
        workspace_id: str,
        person_id: str,
        version: str | None = None,
    ) -> PersonProfile:
        return self._profile_records.get_person_profile(workspace_id, person_id, version)

    def list_person_profiles(self, workspace_id: str) -> list[PersonProfile]:
        return self._profile_records.list_person_profiles(workspace_id)

    def list_person_profile_versions(
        self,
        workspace_id: str,
        person_id: str,
    ) -> list[PersonProfile]:
        return self._profile_records.list_person_profile_versions(workspace_id, person_id)

    def save_versioned_voice_profile(
        self,
        voice: VersionedVoiceProfile, *, verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> VersionedVoiceProfile:
        voice = self._retain_profile_authorization("versioned_voice_profiles", voice)
        _validate_versioned_voice_profile(voice)
        if voice.reference is not None:
            voice = replace(voice, owner_user_id=content_owner(verified_owner_user_id))
            self.resolve_profile_reference(voice, viewer_user_id=verified_owner_user_id,
                                          local_diagnostic=local_diagnostic, for_generation=False)
            if voice.reference.asset_id is None:
                path = self.resolve_workspace_media_reference(voice.workspace_id, voice.reference_audio_path)
                with self._connect() as conn:
                    row = conn.execute("SELECT id FROM material_assets WHERE workspace_id=? AND file_path=?",
                                       (voice.workspace_id, str(path))).fetchone()
                if row is not None:
                    voice = replace(voice, reference=replace(voice.reference, asset_id=str(row["id"])))
        with self._connect() as conn:
            if voice.reference is not None:
                self._require_profile_write_owner(conn, "versioned_voice_profiles", voice, verified_owner_user_id, local_diagnostic)
            self.resolve_workspace_media_reference(
                voice.workspace_id,
                voice.reference_audio_path,
            )
            if voice.person_profile_id:
                self._get_person_profile(conn, voice.workspace_id, voice.person_profile_id, voice.person_version)
            if voice.authorization is not None:
                self._save_authorization(conn, voice.workspace_id, voice.authorization)
            self._claim_workspace_media_reference(
                conn,
                workspace_id=voice.workspace_id,
                reference=voice.reference_audio_path,
            )
            self._profile_records._save_versioned_voice_profile(conn, voice)
        return voice

    def get_versioned_voice_profile(
        self,
        workspace_id: str,
        voice_profile_id: str,
        version: str | None = None,
    ) -> VersionedVoiceProfile:
        return self._profile_records.get_versioned_voice_profile(workspace_id, voice_profile_id, version)

    def list_versioned_voice_profiles(
        self,
        workspace_id: str,
    ) -> list[VersionedVoiceProfile]:
        return self._profile_records.list_versioned_voice_profiles(workspace_id)

    def list_versioned_voice_profile_versions(
        self,
        workspace_id: str,
        voice_profile_id: str,
    ) -> list[VersionedVoiceProfile]:
        return self._profile_records.list_versioned_voice_profile_versions(workspace_id, voice_profile_id)

    def save_pronunciation_entry(
        self,
        entry: PronunciationEntry,
    ) -> PronunciationEntry:
        _validate_pronunciation_entry(entry)
        with self._connect() as conn:
            if entry.scope == "person":
                self._get_person_profile(conn, entry.workspace_id, entry.scope_reference or "")
            _save_versioned_payload(
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
            row = _versioned_row(
                conn,
                table="pronunciation_entries",
                workspace_id=workspace_id,
                record_id=entry_id,
                version=version,
            )
        return self._pronunciation_entry_from_payload(json.loads(str(row["payload"])))

    def list_pronunciation_entries(self, workspace_id: str) -> list[PronunciationEntry]:
        with self._connect() as conn:
            payloads = _list_versioned_payloads(
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
            payloads = _list_versioned_payloads(
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
            current_voice = self._get_versioned_voice_profile(conn, workspace_id, voice_profile_id)
            voice = replace(
                historical_voice,
                use_state=(historical_voice.use_state if historical_voice.use_state != "available"
                           else current_voice.use_state),
                visibility=current_voice.visibility,
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
        viewer_user_id: str | None = None, local_diagnostic: bool = False,
        for_generation: bool = True,
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
            current_person = self._get_person_profile(conn, workspace_id, person_id)
            current_voice = self._get_versioned_voice_profile(conn, workspace_id, voice_profile_id)
            person = replace(
                historical_person,
                use_state=(historical_person.use_state if historical_person.use_state != "available" else current_person.use_state),
                visibility=current_person.visibility,
                authorization=self._current_authorization(
                    conn,
                    workspace_id,
                    historical_person.authorization,
                ),
            )
            voice = replace(
                historical_voice,
                use_state=(historical_voice.use_state if historical_voice.use_state != "available" else current_voice.use_state),
                visibility=current_voice.visibility,
                authorization=self._current_authorization(
                    conn,
                    workspace_id,
                    historical_voice.authorization,
                ),
            )
            if bool(authorization_id) != bool(authorization_version):
                raise ValueError("授权引用必须同时指定ID和版本")
            requested_authorization = (self._get_authorization(
                conn, workspace_id, authorization_id, authorization_version,
            ) if authorization_id else None)
            authorization = self._current_authorization(
                conn,
                workspace_id,
                requested_authorization,
            )
        if not self.profile_readable(person, viewer_user_id, local_diagnostic) or not self.profile_readable(voice, viewer_user_id, local_diagnostic):
            raise PermissionError("档案不属于当前成员或已取消共享")
        visual_input = None
        if for_generation:
            self.profile_reference_input(voice, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
            visual_input = self.profile_reference_input(person, viewer_user_id=viewer_user_id,
                                                        local_diagnostic=local_diagnostic)
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
            visual_path=visual_input.source_path if visual_input is not None else "",
            visual_reference_input=visual_input,
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
        verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
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
            reference=ProfileReference(),
            display_name=legacy.name,
        )
        return self.save_versioned_voice_profile(versioned, verified_owner_user_id=verified_owner_user_id,
                                                 local_diagnostic=local_diagnostic)

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
        return self._media_authorizations._save_authorization(conn, workspace_id, authorization)

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




    def _get_authorization(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        authorization_id: str,
        version: str | None,
    ) -> Authorization:
        return self._media_authorizations._get_authorization(conn, workspace_id, authorization_id, version)

    def _current_authorization(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        authorization: Authorization | None,
    ) -> Authorization | None:
        return self._media_authorizations._current_authorization(conn, workspace_id, authorization)

    def _get_person_profile(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        person_id: str,
        version: str | None = None,
    ) -> PersonProfile:
        return self._profile_records._get_person_profile(conn, workspace_id, person_id, version)

    def _get_versioned_voice_profile(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        voice_profile_id: str,
        version: str | None = None,
    ) -> VersionedVoiceProfile:
        return self._profile_records._get_versioned_voice_profile(conn, workspace_id, voice_profile_id, version)

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
        authorization_failure = profile_use_failure(inputs.voice,
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
        if record.source_kind == "recorded" and not self._recorded_narration_source_is_current(record):
            return None
        return record

    def _recorded_narration_source_is_current(self, artifact: NarrationArtifactRecord) -> bool:
        if (artifact.source_kind != "recorded" or not artifact.task_id
                or not artifact.source_artifact_ref or not artifact.source_sha256
                or not artifact.source_script_digest
                or artifact.voice_profile_id is not None or artifact.model_id is not None):
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifact_records WHERE workspace_id=? AND task_id=? AND logical_ref=?",
                (artifact.workspace_id, artifact.task_id, artifact.source_artifact_ref),
            ).fetchone()
            if row is None:
                return False
            source = self._artifact_record_from_row(row)
            if source.kind != "task_recording" or source.sha256 != artifact.source_sha256:
                return False
            try:
                job = self.get_job_run(artifact.workspace_id, source.producer_job_id)
                transform = next((item for item in self.list_transform_runs(artifact.workspace_id, artifact.task_id)
                    if item.job_id == source.producer_job_id and item.output_refs == [source.logical_ref]), None)
            except KeyError:
                return False
        if transform is None:
            return False
        if (job.task_id != artifact.task_id or job.status != "succeeded"
                or transform.task_id != artifact.task_id or transform.status != "succeeded"
                or transform.job_id != job.id or transform.output_refs != [source.logical_ref]
                or transform.parameters.get("script_id") != artifact.script_id
                or transform.parameters.get("script_digest") != artifact.source_script_digest
                or self.confirmed_creation_task_script_digest(
                    artifact.workspace_id, artifact.task_id, artifact.script_id
                ) != artifact.source_script_digest):
            return False
        path = Path(artifact.audio_path)
        if not path.is_file() or path.stat().st_size != source.size_bytes:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as audio:
            for chunk in iter(lambda: audio.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == source.sha256

    def save_narration_artifact(
        self,
        artifact: NarrationArtifactRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> NarrationArtifactRecord:
        if connection is None:
            with self._connect() as conn:
                return self.save_narration_artifact(artifact, connection=conn)
        connection.execute(
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
            conn.execute("BEGIN IMMEDIATE")
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
                if previous.device_request_id is not None:
                    if record.device_request_id not in {None, previous.device_request_id}:
                        raise ValueError("预览尝试已绑定其他设备请求")
                    record = replace(record, device_request_id=previous.device_request_id)
                if previous.status not in {"queued", "running"} and previous != record:
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

    def bind_preview_device_request(self, run, request_id, *, conn=None):
        """Bind this attempt, including a terminal checkpoint, to its exact request."""
        if conn is None:
            with self._connect() as writer:
                writer.execute("BEGIN IMMEDIATE")
                return self.bind_preview_device_request(run, request_id, conn=writer)
        row = conn.execute("SELECT payload FROM media_preview_attempts WHERE workspace_id=? AND id=?", (run.workspace_id, run.id)).fetchone()
        if row is None:
            raise KeyError(run.id)
        current = self._media_preview_run_from_payload(json.loads(row["payload"]))
        request = conn.execute("SELECT workspace_id,owner_type,owner_id,input_digest FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
        if (request is None or tuple(request) != (current.workspace_id, "media_preview", current.id, current.input_digest)
                or (run.identity, run.attempt, run.input_digest) != (current.identity, current.attempt, current.input_digest)):
            raise ValueError("设备请求与预览尝试或输入不一致")
        if current.device_request_id not in {None, request_id}:
            raise ValueError("预览尝试已绑定其他设备请求")
        updated = replace(current, device_request_id=request_id)
        conn.execute("UPDATE media_preview_attempts SET payload=? WHERE workspace_id=? AND id=?", (_json_payload(updated), run.workspace_id, run.id))
        return updated

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
                if latest.status == "queued" and record.status == "running":
                    running = replace(latest, status="running")
                    conn.execute("UPDATE media_preview_attempts SET payload=?,updated_at=? WHERE id=?",
                                 (_json_payload(running), utc_now().isoformat(), running.id))
                    return running, True
                if latest.status in {"queued", "running"}:
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
        return self._material_collections.list_collections(workspace_id)

    def save_collection(self, collection: MaterialCollection) -> MaterialCollection:
        return self._material_collections.save_collection(collection)

    def scan_library(self, workspace_id: str) -> list[MaterialAsset]:
        if (not self._material_library_available() or self.get_material_ingestion_binding()
                != (workspace_id, str(self.video_library_path.resolve()))):
            return []
        if not self.video_library_path.exists() or not self.video_library_path.is_dir():
            return []
        discovered: list[MaterialAsset] = []
        for path in sorted(self.video_library_path.rglob("*")):
            if (
                not path.is_file()
                or path.is_symlink()
                or path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS
                or "_已处理" in path.parts
            ):
                continue
            if not path.resolve().is_relative_to(self.video_library_path.resolve()):
                continue
            try:
                self._require_material_copy_confirmation(workspace_id, path.resolve())
            except ValueError:
                continue
            discovered.append(self._upsert_discovered_asset(workspace_id, path))
        return discovered

    def bind_material_ingestion(
        self,
        workspace_id: str,
        *,
        expected_directory: str,
        verified_owner_user_id: str | None = None,
    ) -> None:
        """Bind the configured inbox to one authenticated workspace, never to the current login."""
        if not workspace_id.strip():
            raise ValueError("必须先选择已验证的工作区")
        if not expected_directory.strip() or not Path(expected_directory).expanduser().is_absolute():
            raise ValueError("请先确认当前素材目录")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            status = self._material_library_status()
            if status == "read_only":
                raise ValueError("素材目录当前不可写；请检查磁盘挂载方式或目录权限，或在设置中改选一个可写目录")
            if status != "ready":
                raise ValueError("素材目录不可用；请连接外接盘并重新选择目录")
            root_path = str(self.video_library_path.resolve())
            expected_path = str(Path(expected_directory).expanduser().resolve())
            if expected_path != root_path:
                raise ValueError("素材目录配置已变化，请刷新后重新确认")
            row = conn.execute(
                "SELECT workspace_id, library_path, intake_owner_user_id FROM material_ingestion_binding WHERE id = 1"
            ).fetchone()
            if row is not None and (row["workspace_id"], row["library_path"]) != (workspace_id, root_path):
                raise ValueError("素材目录已绑定另一工作区或目录；请先明确解除原绑定")
            # 已绑定同一目录时保留当时绑定者的身份，不因再次确认而改写归属。
            intake_owner = (row["intake_owner_user_id"] if row is not None
                            else str(verified_owner_user_id or "").strip() or None)
            conn.execute(
                "INSERT OR REPLACE INTO material_ingestion_binding "
                "(id, workspace_id, library_path, intake_owner_user_id) VALUES (1, ?, ?, ?)",
                (workspace_id, root_path, intake_owner),
            )

    def get_material_ingestion_owner(self) -> str | None:
        """历史目录绑定者；不用于推断文件上传者。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT intake_owner_user_id FROM material_ingestion_binding WHERE id = 1"
            ).fetchone()
        if row is None or "intake_owner_user_id" not in row.keys():
            return None
        return str(row["intake_owner_user_id"] or "").strip() or None

    def get_material_ingestion_binding(self) -> tuple[str, str] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT workspace_id, library_path FROM material_ingestion_binding WHERE id = 1").fetchone()
        return (str(row["workspace_id"]), str(row["library_path"])) if row else None

    @staticmethod
    def material_copy_revision(stat: os.stat_result) -> dict[str, str]:
        """Decimal strings preserve nanosecond precision in desktop/browser JSON."""
        return {
            "size_bytes": str(stat.st_size), "mtime_ns": str(stat.st_mtime_ns),
            "ctime_ns": str(stat.st_ctime_ns), "inode": str(stat.st_ino),
        }

    def confirm_material_copy_complete(
        self, workspace_id: str, *, relative_path: str,
        expected_revision: dict[str, str], confirmation_source: Literal["manual", "app_import"],
        verified_owner_user_id: str | None = None,
    ) -> None:
        """Persist a copy declaration for this bound file revision, not media validity."""
        import time

        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts or "_已处理" in relative.parts:
            raise ValueError("请确认当前绑定目录内的素材文件")
        raw_path = self.video_library_path / relative
        if raw_path.is_symlink():
            raise ValueError("不能确认符号链接素材")
        if confirmation_source not in {"manual", "app_import"}:
            raise ValueError("复制完成确认来源无效")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            binding = conn.execute("SELECT workspace_id, library_path FROM material_ingestion_binding WHERE id=1").fetchone()
            if (binding is None or (binding["workspace_id"], binding["library_path"])
                    != (workspace_id, str(self.video_library_path.resolve()))):
                raise ValueError("当前工作区未绑定此素材目录")
            if not self._material_library_available():
                raise ValueError("素材目录当前不可用，请连接原目录后重试")
            path = self._validated_library_file(str(raw_path))
            stat = path.stat()
            revision = self.material_copy_revision(stat)
            if revision != expected_revision:
                raise ValueError("文件修订已变化，请重新检查并确认复制完成")
            conn.execute(
                """INSERT INTO material_ingestion_files
                   (workspace_id, file_path, size_bytes, mtime_ns, ctime_ns, inode,
                    observed_at, state, confirmed_revision, confirmation_source, copy_confirmed_at,
                    copy_uploader_user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'observing', ?, ?, ?, ?)
                   ON CONFLICT(workspace_id, file_path) DO UPDATE SET
                     size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns,
                     ctime_ns=excluded.ctime_ns, inode=excluded.inode, observed_at=excluded.observed_at,
                     state=CASE WHEN confirmed_revision=excluded.confirmed_revision THEN state ELSE 'observing' END,
                     copy_uploader_user_id=CASE WHEN confirmed_revision=excluded.confirmed_revision
                       THEN copy_uploader_user_id ELSE excluded.copy_uploader_user_id END,
                     confirmed_revision=excluded.confirmed_revision,
                     confirmation_source=excluded.confirmation_source,
                     copy_confirmed_at=excluded.copy_confirmed_at""",
                (workspace_id, str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                 stat.st_ino, time.time(), json.dumps(revision, sort_keys=True),
                 confirmation_source, utc_now().isoformat(),
                 str(verified_owner_user_id or "").strip() or None),
            )

    def _require_material_copy_confirmation(self, workspace_id: str, path: Path) -> None:
        if self.get_material_ingestion_binding() != (workspace_id, str(self.video_library_path.resolve())):
            raise ValueError("当前工作区未绑定此素材目录")
        revision = json.dumps(self.material_copy_revision(path.stat()), sort_keys=True)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT confirmed_revision FROM material_ingestion_files WHERE workspace_id=? AND file_path=?",
                (workspace_id, str(path)),
            ).fetchone()
        if row is None or row["confirmed_revision"] != revision:
            raise ValueError("请先确认此文件已复制完成，再开始接收或重试")

    def pending_material_copies(self, workspace_id: str) -> list[dict[str, object]]:
        if (not self._material_library_available() or self.get_material_ingestion_binding()
                != (workspace_id, str(self.video_library_path.resolve()))):
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path, confirmed_revision FROM material_ingestion_files WHERE workspace_id=? ORDER BY file_path",
                (workspace_id,),
            ).fetchall()
        pending = []
        for row in rows:
            path = Path(row["file_path"])
            if (not path.resolve().is_relative_to(self.video_library_path.resolve())
                    or not path.is_file() or path.is_symlink()):
                continue
            revision = self.material_copy_revision(path.stat())
            if row["confirmed_revision"] != json.dumps(revision, sort_keys=True):
                pending.append({"relative_path": str(path.relative_to(self.video_library_path.resolve())),
                                "expected_revision": revision})
        return pending

    def material_ingestion_directory_state(self, workspace_id: str) -> dict[str, object]:
        """Describe the configured directory without revealing another workspace's binding."""
        if not workspace_id.strip():
            raise ValueError("必须先选择已验证的工作区")
        binding = self.get_material_ingestion_binding()
        current_path = str(self.video_library_path)
        resolved_path = str(self.video_library_path.resolve())
        foreign_binding = binding is not None and binding[0] != workspace_id
        return {
            "directory": None if foreign_binding else current_path,
            "status": self._material_library_status(),
            "bound_to_current_workspace": binding == (workspace_id, resolved_path),
            "bound_to_another_workspace": foreign_binding,
            "configured_path_changed": binding is not None and binding[1] != resolved_path,
        }

    def material_ingestion_state_counts(self, workspace_id: str) -> dict[str, int]:
        """Return intake totals for one workspace without exposing file paths."""
        counts = {state: 0 for state in ("observing", "running", "ready", "failed", "blocked_disk")}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) AS total FROM material_ingestion_files "
                "WHERE workspace_id=? GROUP BY state",
                (workspace_id,),
            ).fetchall()
        for row in rows:
            state = str(row["state"])
            if state in counts:
                counts[state] = int(row["total"])
        return counts

    def _material_library_available(self) -> bool:
        return self._material_library_status() == "ready"

    def _material_library_status(self) -> Literal["missing", "unmounted", "invalid", "read_only", "ready"]:
        root = self.video_library_path
        if not root.is_absolute() or root.is_symlink():
            return "invalid"
        # A leftover /Volumes/<name> directory is not evidence that its disk is mounted.
        if len(root.parts) >= 3 and root.parts[1] == "Volumes":
            volume = Path(*root.parts[:3])
            if not volume.is_mount():
                return "unmounted"
        if not root.exists():
            return "missing"
        if not root.is_dir():
            return "invalid"
        if not directory_accepts_writes(root):
            return "read_only"
        return "ready"

    def discover_material_hash_candidates(self, *, stable_seconds: float = 10.0) -> list[MaterialHashCandidate]:
        """Observe stable files for the opt-in scheduler path without reading their content."""
        import time

        if stable_seconds < 0:
            raise ValueError("素材稳定等待时间无效")
        binding = self.get_material_ingestion_binding()
        if binding is None:
            return []
        workspace_id, bound_path = binding
        root = self.video_library_path
        if not self._material_library_available() or str(root.resolve()) != bound_path:
            self._mark_material_ingestion_disk_missing(workspace_id)
            return []
        with self._connect() as conn:
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(material_ingestion_files)")}
        if "provisional_revision" not in columns:
            raise RuntimeError("素材调度检查点迁移尚未就绪")

        now = time.time()
        candidates: list[MaterialHashCandidate] = []
        for path in sorted(root.rglob("*")):
            if (path.is_symlink() or not path.is_file()
                    or path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS
                    or "_已处理" in path.parts):
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()):
                continue
            try:
                stat = resolved.stat()
            except FileNotFoundError:
                continue
            asset_id = "asset_" + hashlib.sha256(
                f"{workspace_id}\0{resolved}".encode("utf-8")
            ).hexdigest()[:16]
            candidate = _material_hash_candidate(workspace_id, bound_path, resolved, stat, asset_id)
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM material_ingestion_files WHERE workspace_id=? AND file_path=?",
                    (workspace_id, str(resolved)),
                ).fetchone()
                changed = row is None or row["provisional_revision"] != candidate.provisional_revision
                if changed:
                    conn.execute(
                        """INSERT INTO material_ingestion_files
                           (workspace_id, file_path, size_bytes, mtime_ns, ctime_ns, inode,
                            observed_at, state, provisional_revision)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'observing', ?)
                           ON CONFLICT(workspace_id, file_path) DO UPDATE SET
                             size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns,
                             ctime_ns=excluded.ctime_ns, inode=excluded.inode,
                             observed_at=excluded.observed_at, state='observing',
                             provisional_revision=excluded.provisional_revision,
                             attempted_digest=NULL""",
                        (workspace_id, str(resolved), stat.st_size, stat.st_mtime_ns,
                         stat.st_ctime_ns, stat.st_ino, now, candidate.provisional_revision),
                    )
                elif row["state"] in {"ready", "failed"} or row["attempted_digest"]:
                    continue
                elif now - float(row["observed_at"]) >= stable_seconds:
                    conn.execute(
                        "UPDATE material_ingestion_files SET state='waiting' "
                        "WHERE workspace_id=? AND file_path=? AND provisional_revision=?",
                        (workspace_id, str(resolved), candidate.provisional_revision),
                    )
                    candidates.append(candidate)
            if changed:
                try:
                    self._upsert_discovered_asset(workspace_id, resolved)
                except FileNotFoundError:
                    continue
                self._mark_material_hash_asset_blocked(
                    workspace_id, asset_id, "文件仍在复制或变化；写入稳定后自动安排校验。"
                )
            elif candidate in candidates:
                # Repair a crash between recording the observation and creating its asset.
                try:
                    self._asset_by_path(workspace_id, str(resolved))
                except KeyError:
                    try:
                        self._upsert_discovered_asset(workspace_id, resolved)
                    except FileNotFoundError:
                        candidates.remove(candidate)
                        continue
                    self._mark_material_hash_asset_blocked(
                        workspace_id, asset_id, "文件已稳定，等待设备许可后校验。"
                    )
        return candidates

    def _mark_material_hash_asset_blocked(self, workspace_id: str, asset_id: str, message: str) -> None:
        self._material_assets.mark_hash_asset_blocked(workspace_id, asset_id, message)

    def material_hash_verdict(
        self, workspace_id: str, asset_id: str, input_digest: str,
        unit_key: str, conn: sqlite3.Connection,
    ) -> Literal["ready", "dependency", "stale", "unauthorized", "unknown"]:
        binding = conn.execute(
            "SELECT workspace_id, library_path FROM material_ingestion_binding WHERE id=1"
        ).fetchone()
        if binding is None or binding["workspace_id"] != workspace_id:
            return "unauthorized"
        if not self._material_library_available():
            return "dependency"
        bound_path = str(self.video_library_path.resolve())
        if binding["library_path"] != bound_path:
            return "stale"
        asset_row = conn.execute(
            "SELECT file_path FROM material_assets WHERE workspace_id=? AND id=?",
            (workspace_id, asset_id),
        ).fetchone()
        if asset_row is None:
            return "stale"
        path = Path(str(asset_row["file_path"]))
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(Path(bound_path)):
                return "stale"
        except OSError:
            return "unknown"
        observation = conn.execute(
            "SELECT provisional_revision, state, attempted_digest FROM material_ingestion_files "
            "WHERE workspace_id=? AND file_path=?",
            (workspace_id, str(path)),
        ).fetchone()
        if observation is None:
            return "stale"
        if observation["state"] != "waiting" or observation["attempted_digest"] is not None:
            return "stale"
        try:
            stat = path.stat()
        except FileNotFoundError:
            return "dependency"
        except OSError:
            return "unknown"
        candidate = _material_hash_candidate(workspace_id, bound_path, path, stat, asset_id)
        if (observation["provisional_revision"] != candidate.provisional_revision
                or input_digest != candidate.provisional_revision
                or unit_key != candidate.unit_key):
            return "stale"
        return "ready"

    def material_hash_candidate_for_grant(self, grant: Grant) -> MaterialHashCandidate:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, attempt_number "
                "FROM device_work_requests WHERE id=?",
                (grant.request_id,),
            ).fetchone()
            if (row is None or row["owner_type"] != "material_asset"
                    or (row["workspace_id"], row["input_digest"], row["unit_key"], row["attempt_number"])
                    != (grant.workspace_id, grant.input_digest, grant.unit_key, grant.attempt_number)):
                raise PermissionError("素材哈希许可与执行对象不匹配")
            asset_id = str(row["owner_id"])
            verdict = self.material_hash_verdict(
                grant.workspace_id, asset_id, grant.input_digest, grant.unit_key, conn
            )
            if verdict != "ready":
                raise ValueError(f"素材哈希输入不可执行：{verdict}")
            asset = conn.execute(
                "SELECT file_path FROM material_assets WHERE workspace_id=? AND id=?",
                (grant.workspace_id, asset_id),
            ).fetchone()
        path = Path(str(asset["file_path"]))
        candidate = _material_hash_candidate(
            grant.workspace_id, str(self.video_library_path.resolve()), path,
            path.stat(), asset_id,
        )
        if (candidate.provisional_revision, candidate.unit_key) != (
            grant.input_digest, grant.unit_key
        ):
            raise ValueError("素材哈希源修订已变化")
        return candidate

    def material_hash_verdict_for_grant(
        self, grant: Grant,
    ) -> Literal["ready", "dependency", "stale", "unauthorized", "unknown"]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, attempt_number "
                "FROM device_work_requests WHERE id=?",
                (grant.request_id,),
            ).fetchone()
            if (row is None or row["owner_type"] != "material_asset"
                    or (row["workspace_id"], row["input_digest"], row["unit_key"], row["attempt_number"])
                    != (grant.workspace_id, grant.input_digest, grant.unit_key, grant.attempt_number)):
                return "unauthorized"
            return self.material_hash_verdict(
                grant.workspace_id, str(row["owner_id"]), grant.input_digest,
                grant.unit_key, conn,
            )

    def hash_granted_material(
        self, grant: Grant, assert_grant: Callable[[Grant], None],
    ) -> MaterialHashResult:
        """Read content only under an active grant, checking it between chunks."""
        candidate = self.material_hash_candidate_for_grant(grant)
        path = Path(candidate.file_path)
        if path.is_symlink() or not path.resolve().is_relative_to(self.video_library_path.resolve()):
            raise ValueError("素材路径不在已绑定目录内")
        before = path.stat()
        digest = hashlib.sha256()
        assert_grant(grant)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            if ((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns, opened.st_ino)
                    != (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)):
                raise ValueError("素材文件在打开时已变化")
            while True:
                assert_grant(grant)
                chunk = source.read(4 * 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        after = path.stat()
        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino)):
            raise ValueError("哈希期间源文件发生变化")
        current = _material_hash_candidate(
            candidate.workspace_id, str(self.video_library_path.resolve()),
            path, after, candidate.asset_id,
        )
        if current != candidate:
            raise ValueError("哈希期间素材绑定或源修订发生变化")
        return MaterialHashResult(candidate, digest.hexdigest())

    def commit_material_hash(
        self, conn: sqlite3.Connection, grant: Grant, result: MaterialHashResult,
    ) -> str:
        """Write the hash fact on the scheduler's permit transaction connection."""
        candidate = result.candidate
        if not re.fullmatch(r"[0-9a-f]{64}", result.source_sha256):
            raise ValueError("素材哈希结果无效")
        request = conn.execute(
            "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, "
            "attempt_number, state FROM device_work_requests WHERE id=?",
            (grant.request_id,),
        ).fetchone()
        if (request is None or request["owner_type"] != "material_asset"
                or (request["owner_id"], request["workspace_id"], request["input_digest"],
                    request["unit_key"], request["attempt_number"])
                != (candidate.asset_id, grant.workspace_id, grant.input_digest,
                    grant.unit_key, grant.attempt_number)
                or request["state"] not in {"granted", "yield_requested"}
                or (candidate.workspace_id, candidate.provisional_revision, candidate.unit_key)
                != (grant.workspace_id, grant.input_digest, grant.unit_key)):
            raise PermissionError("素材哈希检查点不属于当前许可尝试")
        if self.material_hash_verdict(
            candidate.workspace_id, candidate.asset_id, candidate.provisional_revision,
            candidate.unit_key, conn,
        ) != "ready":
            raise ValueError("素材来源已变化，未保存过期哈希")
        checkpoint = conn.execute(
            """SELECT stage_input_digest, result_ref FROM material_analysis_checkpoints
               WHERE workspace_id=? AND asset_id=? AND provisional_revision=?
                 AND source_sha256=? AND unit_key=?""",
            (candidate.workspace_id, candidate.asset_id, candidate.provisional_revision,
             result.source_sha256, candidate.unit_key),
        ).fetchone()
        result_ref = f"sha256:{result.source_sha256}"
        if checkpoint is not None and (
            checkpoint["stage_input_digest"] != candidate.stage_input_digest
            or checkpoint["result_ref"] != result_ref
        ):
            raise ValueError("既有哈希检查点结果不匹配")
        updated = conn.execute(
            """UPDATE material_ingestion_files
               SET attempted_digest=?, state='waiting'
               WHERE workspace_id=? AND file_path=? AND provisional_revision=?""",
            (result.source_sha256, candidate.workspace_id, candidate.file_path,
             candidate.provisional_revision),
        )
        if updated.rowcount != 1:
            raise ValueError("素材观察已失效，未写入哈希检查点")
        if checkpoint is None:
            conn.execute(
                """INSERT INTO material_analysis_checkpoints
                   (workspace_id, asset_id, source_sha256, provisional_revision,
                    unit_key, stage_input_digest, result_ref, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate.workspace_id, candidate.asset_id, result.source_sha256,
                 candidate.provisional_revision, candidate.unit_key,
                 candidate.stage_input_digest, result_ref, utc_now().isoformat()),
            )
        return f"material_hash:{candidate.asset_id}:{candidate.provisional_revision}"

    def pending_material_probe_dependencies(self) -> list[MaterialProbeDependency]:
        """Expose durable hash outputs for a future permitted probe submitter."""
        binding = self.get_material_ingestion_binding()
        if binding is None or not self._material_library_available():
            return []
        workspace_id, bound_path = binding
        if str(self.video_library_path.resolve()) != bound_path:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT f.provisional_revision, f.attempted_digest, a.id AS asset_id
                   FROM material_ingestion_files f
                   JOIN material_assets a ON a.workspace_id=f.workspace_id
                                         AND a.file_path=f.file_path
                   WHERE f.workspace_id=? AND f.state='waiting'
                     AND f.attempted_digest IS NOT NULL""",
                (workspace_id,),
            ).fetchall()
            dependencies: list[MaterialProbeDependency] = []
            for row in rows:
                source_sha256 = str(row["attempted_digest"])
                try:
                    unit_key = material_probe_unit_key(source_sha256)
                except ValueError:
                    continue
                if self.material_probe_source_verdict(
                    workspace_id, str(row["asset_id"]), source_sha256, unit_key, conn,
                ) != "ready":
                    continue
                dependencies.append(MaterialProbeDependency(
                    workspace_id, str(row["asset_id"]), source_sha256,
                    str(row["provisional_revision"]),
                ))
        return dependencies

    def material_probe_key_for_dependency(self, dependency: MaterialProbeDependency) -> str:
        with self._connect() as conn:
            return self._preferred_material_probe_key(
                conn, dependency.workspace_id, dependency.asset_id,
                dependency.source_sha256, dependency.provisional_revision,
            )

    def _preferred_material_probe_key(
        self, conn: sqlite3.Connection, workspace_id: str, asset_id: str,
        source_sha256: str, provisional_revision: str,
    ) -> str:
        v1_key = material_probe_unit_key(source_sha256)
        row = conn.execute(
            """SELECT stage_input_digest, result_json, result_ref
               FROM material_analysis_checkpoints WHERE workspace_id=? AND asset_id=?
                 AND source_sha256=? AND provisional_revision=? AND unit_key=?""",
            (workspace_id, asset_id, source_sha256, provisional_revision, v1_key),
        ).fetchone()
        if row is None or not isinstance(row["result_json"], str):
            return v1_key
        result_json = row["result_json"]
        if (row["stage_input_digest"] != v1_key.rsplit(":", 1)[-1]
                or row["result_ref"] != "sha256:" + hashlib.sha256(result_json.encode()).hexdigest()):
            return v1_key
        try:
            legacy = probe_checkpoint_needs_rate_refresh(result_json)
        except ValueError:
            return v1_key
        return material_probe_unit_key(source_sha256, version=2) if legacy else v1_key

    def legacy_probe_request_for_replacement(
        self, dependency: MaterialProbeDependency,
    ) -> str | None:
        """Locate the old scene-named request without changing scheduler queue state."""
        with self._connect() as conn:
            current = conn.execute(
                """SELECT id FROM device_work_requests
                   WHERE workspace_id=? AND owner_type='material_asset' AND owner_id=?
                     AND input_digest=? AND unit_key=?""",
                (dependency.workspace_id, dependency.asset_id,
                 dependency.source_sha256, material_probe_unit_key(dependency.source_sha256)),
            ).fetchone()
            legacy = conn.execute(
                """SELECT id, state, wait_reason FROM device_work_requests
                   WHERE workspace_id=? AND owner_type='material_asset' AND owner_id=?
                     AND input_digest=? AND unit_key LIKE 'probe-scene:v1:%'""",
                (dependency.workspace_id, dependency.asset_id, dependency.source_sha256),
            ).fetchall()
        if len(legacy) > 1:
            raise ValueError("存在多个旧媒体探测请求，拒绝自动替换")
        if not legacy:
            return None
        old = legacy[0]
        if (old["state"] == "cancelled" and current is not None
                and old["wait_reason"] == f"replaced_by:{current['id']}"):
            return None
        if old["state"] not in {"waiting", "blocked"}:
            raise ValueError("旧素材探测请求已执行或终止，拒绝自动替换")
        return str(old["id"])

    def material_probe_source_verdict(
        self, workspace_id: str, asset_id: str, source_sha256: str,
        unit_key: str, conn: sqlite3.Connection,
    ) -> Literal["ready", "dependency", "stale", "unauthorized", "unknown"]:
        """Check the committed hash fact; no probe execution is authorized here."""
        binding = conn.execute(
            "SELECT workspace_id, library_path FROM material_ingestion_binding WHERE id=1"
        ).fetchone()
        if binding is None or binding["workspace_id"] != workspace_id:
            return "unauthorized"
        if not self._material_library_available():
            return "dependency"
        bound_path = str(self.video_library_path.resolve())
        if binding["library_path"] != bound_path:
            return "stale"
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            return "stale"
        if unit_key not in {material_probe_unit_key(source_sha256),
                            material_probe_unit_key(source_sha256, version=2)}:
            return "stale"
        asset = conn.execute(
            "SELECT file_path FROM material_assets WHERE workspace_id=? AND id=?",
            (workspace_id, asset_id),
        ).fetchone()
        if asset is None:
            return "stale"
        path = Path(str(asset["file_path"]))
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(Path(bound_path)):
                return "stale"
        except OSError:
            return "unknown"
        observation = conn.execute(
            "SELECT provisional_revision, state, attempted_digest FROM material_ingestion_files "
            "WHERE workspace_id=? AND file_path=?",
            (workspace_id, str(path)),
        ).fetchone()
        if (observation is None or observation["state"] != "waiting"
                or observation["attempted_digest"] != source_sha256):
            return "stale"
        try:
            stat = path.stat()
        except FileNotFoundError:
            return "dependency"
        except OSError:
            return "unknown"
        candidate = _material_hash_candidate(
            workspace_id, bound_path, path, stat, asset_id,
        )
        if candidate.provisional_revision != observation["provisional_revision"]:
            return "stale"
        checkpoint = conn.execute(
            """SELECT stage_input_digest, result_ref
               FROM material_analysis_checkpoints
               WHERE workspace_id=? AND asset_id=? AND provisional_revision=?
                 AND source_sha256=? AND unit_key=?""",
            (workspace_id, asset_id, candidate.provisional_revision,
             source_sha256, candidate.unit_key),
        ).fetchone()
        if (checkpoint is None
                or checkpoint["stage_input_digest"] != candidate.stage_input_digest
                or checkpoint["result_ref"] != f"sha256:{source_sha256}"):
            return "stale"
        return "ready"

    def material_probe_candidate_for_grant(self, grant: Grant) -> MaterialProbeCandidate:
        with self._connect() as conn:
            request = conn.execute(
                "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, attempt_number "
                "FROM device_work_requests WHERE id=?", (grant.request_id,),
            ).fetchone()
            if (request is None or request["owner_type"] != "material_asset"
                    or (request["workspace_id"], request["input_digest"], request["unit_key"],
                        request["attempt_number"])
                    != (grant.workspace_id, grant.input_digest, grant.unit_key, grant.attempt_number)):
                raise PermissionError("素材媒体探测许可与执行对象不匹配")
            asset_id = str(request["owner_id"])
            verdict = self.material_probe_source_verdict(
                grant.workspace_id, asset_id, grant.input_digest, grant.unit_key, conn,
            )
            if verdict != "ready":
                raise ValueError(f"素材媒体探测输入不可执行：{verdict}")
            asset = conn.execute(
                "SELECT file_path FROM material_assets WHERE workspace_id=? AND id=?",
                (grant.workspace_id, asset_id),
            ).fetchone()
            observation = conn.execute(
                "SELECT provisional_revision FROM material_ingestion_files "
                "WHERE workspace_id=? AND file_path=?",
                (grant.workspace_id, asset["file_path"]),
            ).fetchone()
        return MaterialProbeCandidate(
            grant.workspace_id, asset_id, str(asset["file_path"]), grant.input_digest,
            str(observation["provisional_revision"]), grant.unit_key,
        )

    def material_probe_verdict_for_grant(
        self, grant: Grant,
    ) -> Literal["ready", "dependency", "stale", "unauthorized", "unknown"]:
        with self._connect() as conn:
            request = conn.execute(
                "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, attempt_number "
                "FROM device_work_requests WHERE id=?", (grant.request_id,),
            ).fetchone()
            if (request is None or request["owner_type"] != "material_asset"
                    or (request["workspace_id"], request["input_digest"], request["unit_key"],
                        request["attempt_number"])
                    != (grant.workspace_id, grant.input_digest, grant.unit_key, grant.attempt_number)):
                return "unauthorized"
            return self.material_probe_source_verdict(
                grant.workspace_id, str(request["owner_id"]), grant.input_digest,
                grant.unit_key, conn,
            )

    def commit_material_probe(
        self, conn: sqlite3.Connection, grant: Grant,
        candidate: MaterialProbeCandidate, facts: MaterialProbeFacts,
    ) -> str:
        """Commit typed ffprobe facts and duration on the permit's SQLite transaction."""
        request = conn.execute(
            "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, "
            "attempt_number, state FROM device_work_requests WHERE id=?",
            (grant.request_id,),
        ).fetchone()
        if (request is None or request["owner_type"] != "material_asset"
                or (request["owner_id"], request["workspace_id"], request["input_digest"],
                    request["unit_key"], request["attempt_number"])
                != (candidate.asset_id, grant.workspace_id, grant.input_digest,
                    grant.unit_key, grant.attempt_number)
                or request["state"] not in {"granted", "yield_requested"}
                or (candidate.workspace_id, candidate.source_sha256, candidate.unit_key)
                != (grant.workspace_id, grant.input_digest, grant.unit_key)):
            raise PermissionError("素材媒体检查点不属于当前许可尝试")
        if self.material_probe_source_verdict(
            candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
            candidate.unit_key, conn,
        ) != "ready":
            raise ValueError("素材来源已变化，未保存过期媒体探测结果")
        if candidate.unit_key != self._preferred_material_probe_key(
            conn, candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
            candidate.provisional_revision,
        ):
            raise ValueError("素材媒体探测契约已更新，未保存旧检查点")
        asset_row = self._material_assets._probe_asset_source_row(
            conn, candidate.workspace_id, candidate.asset_id,
        )
        observation = conn.execute(
            "SELECT provisional_revision FROM material_ingestion_files "
            "WHERE workspace_id=? AND file_path=?",
            (candidate.workspace_id, candidate.file_path),
        ).fetchone()
        if (asset_row is None or asset_row["file_path"] != candidate.file_path
                or observation is None
                or observation["provisional_revision"] != candidate.provisional_revision):
            raise ValueError("素材媒体探测来源修订已变化")
        stage_input_digest = candidate.unit_key.rsplit(":", 1)[-1]
        result_json = facts.checkpoint_json()
        result_ref = "sha256:" + hashlib.sha256(result_json.encode("utf-8")).hexdigest()
        existing = conn.execute(
            """SELECT stage_input_digest, result_json, result_ref FROM material_analysis_checkpoints
               WHERE workspace_id=? AND asset_id=? AND provisional_revision=?
                 AND source_sha256=? AND unit_key=?""",
            (candidate.workspace_id, candidate.asset_id, candidate.provisional_revision,
             candidate.source_sha256, candidate.unit_key),
        ).fetchone()
        if existing is not None and (
            existing["stage_input_digest"] != stage_input_digest
            or existing["result_json"] != result_json
            or existing["result_ref"] != result_ref
        ):
            raise ValueError("既有媒体探测检查点结果不匹配")
        self._material_assets.apply_probe_metadata(
            conn, candidate.workspace_id, candidate.asset_id,
            str(asset_row["payload"]), facts.duration_seconds,
        )
        if existing is None:
            conn.execute(
                """INSERT INTO material_analysis_checkpoints
                   (workspace_id, asset_id, source_sha256, provisional_revision,
                    unit_key, stage_input_digest, result_json, result_ref, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
                 candidate.provisional_revision, candidate.unit_key,
                 stage_input_digest, result_json, result_ref, utc_now().isoformat()),
            )
        return f"material_probe_meta:{candidate.asset_id}:{candidate.provisional_revision}"

    def fail_material_probe(
        self, conn: sqlite3.Connection, grant: Grant,
        candidate: MaterialProbeCandidate, reason: Literal[
            "probe_invalid_media", "probe_timeout", "probe_output_limit", "probe_failed",
        ],
    ) -> None:
        """Record a terminal metadata failure with the scheduler's failed request."""
        if self.material_probe_source_verdict(
            candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
            candidate.unit_key, conn,
        ) != "ready":
            raise ValueError("素材来源已变化，不能把旧媒体探测错误写给新素材")
        request = conn.execute(
            "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, "
            "attempt_number, state FROM device_work_requests WHERE id=?",
            (grant.request_id,),
        ).fetchone()
        if (request is None or request["owner_type"] != "material_asset"
                or (request["owner_id"], request["workspace_id"], request["input_digest"],
                    request["unit_key"], request["attempt_number"])
                != (candidate.asset_id, grant.workspace_id, grant.input_digest,
                    grant.unit_key, grant.attempt_number)
                or request["state"] not in {"granted", "yield_requested"}):
            raise PermissionError("素材媒体失败不属于当前许可尝试")
        row = self._material_assets._probe_asset_source_row(
            conn, candidate.workspace_id, candidate.asset_id,
        )
        if row is None or row["file_path"] != candidate.file_path:
            raise ValueError("素材媒体失败来源已变化")
        self._material_assets.mark_probe_failed(
            conn, candidate.workspace_id, candidate.asset_id,
            str(row["payload"]), reason,
        )
        updated = conn.execute(
            """UPDATE material_ingestion_files SET state='failed'
               WHERE workspace_id=? AND file_path=? AND provisional_revision=?
                 AND attempted_digest=?""",
            (candidate.workspace_id, candidate.file_path, candidate.provisional_revision,
             candidate.source_sha256),
        )
        if updated.rowcount != 1:
            raise ValueError("素材观察已变化，未记录旧媒体探测失败")

    def pending_material_scene_dependencies(self) -> list[MaterialProbeDependency]:
        """Expose verified metadata checkpoints needing a future scene contract."""
        binding = self.get_material_ingestion_binding()
        if binding is None or not self._material_library_available():
            return []
        workspace_id, bound_path = binding
        if str(self.video_library_path.resolve()) != bound_path:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT c.asset_id, c.source_sha256, c.provisional_revision,
                          c.unit_key, c.stage_input_digest, c.result_json, c.result_ref,
                          a.payload
                   FROM material_analysis_checkpoints c
                   JOIN material_assets a ON a.workspace_id=c.workspace_id AND a.id=c.asset_id
                   WHERE c.workspace_id=? AND c.unit_key LIKE 'probe-meta:v%:%'""",
                (workspace_id,),
            ).fetchall()
            pending: list[MaterialProbeDependency] = []
            for row in rows:
                source_sha256 = str(row["source_sha256"])
                unit_key = str(row["unit_key"])
                if (unit_key != self._preferred_material_probe_key(
                            conn, workspace_id, str(row["asset_id"]), source_sha256,
                            str(row["provisional_revision"]),
                        )
                        or row["stage_input_digest"] != unit_key.rsplit(":", 1)[-1]
                        or self.material_probe_source_verdict(
                            workspace_id, str(row["asset_id"]), source_sha256,
                            unit_key, conn,
                        ) != "ready"):
                    continue
                result_json = row["result_json"]
                if (not isinstance(result_json, str)
                        or row["result_ref"] != "sha256:" + hashlib.sha256(
                            result_json.encode("utf-8")
                        ).hexdigest()):
                    continue
                try:
                    facts = MaterialProbeFacts.from_checkpoint_json(result_json)
                    asset = self._asset_from_payload(json.loads(str(row["payload"])))
                except (ValueError, TypeError, KeyError):
                    continue
                if asset.duration_seconds != facts.duration_seconds or asset.analysis_status != "blocked":
                    continue
                pending.append(MaterialProbeDependency(
                    workspace_id, str(row["asset_id"]), source_sha256,
                    str(row["provisional_revision"]),
                ))
        return pending

    def _material_scene_source(
        self, conn: sqlite3.Connection, workspace_id: str, asset_id: str,
        source_sha256: str,
    ) -> tuple[sqlite3.Row, MaterialProbeFacts, str] | None:
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            return None
        if self.material_probe_source_verdict(
            workspace_id, asset_id, source_sha256,
            material_probe_unit_key(source_sha256), conn,
        ) != "ready":
            return None
        rows = conn.execute(
            """SELECT c.provisional_revision, c.unit_key, c.stage_input_digest,
                      c.result_json, c.result_ref,
                      a.file_path, a.payload, f.size_bytes
               FROM material_analysis_checkpoints c
               JOIN material_assets a ON a.workspace_id=c.workspace_id AND a.id=c.asset_id
               JOIN material_ingestion_files f ON f.workspace_id=a.workspace_id
                    AND f.file_path=a.file_path
               WHERE c.workspace_id=? AND c.asset_id=? AND c.source_sha256=?
                 AND c.unit_key IN (?, ?) AND c.provisional_revision=f.provisional_revision""",
            (workspace_id, asset_id, source_sha256,
             material_probe_unit_key(source_sha256),
             material_probe_unit_key(source_sha256, version=2)),
        ).fetchall()
        if not rows:
            return None
        probe_key = self._preferred_material_probe_key(
            conn, workspace_id, asset_id, source_sha256,
            str(rows[0]["provisional_revision"]),
        )
        row = next((item for item in rows if item["unit_key"] == probe_key), None)
        if (row is None or row["stage_input_digest"] != probe_key.rsplit(":", 1)[-1]
                or not isinstance(row["result_json"], str)):
            return None
        result_ref = "sha256:" + hashlib.sha256(row["result_json"].encode()).hexdigest()
        if row["result_ref"] != result_ref:
            return None
        try:
            facts = MaterialProbeFacts.from_checkpoint_json(row["result_json"])
            asset = self._asset_from_payload(json.loads(str(row["payload"])))
        except (ValueError, TypeError, KeyError):
            return None
        if asset.duration_seconds != facts.duration_seconds or asset.analysis_status != "blocked":
            return None
        return row, facts, result_ref

    def material_scene_candidate(
        self, workspace_id: str, asset_id: str, source_sha256: str,
        unit_key: str, conn: sqlite3.Connection,
    ) -> MaterialSceneCandidate | None:
        source = self._material_scene_source(conn, workspace_id, asset_id, source_sha256)
        if source is None:
            return None
        row, facts, result_ref = source
        video = next(stream for stream in facts.streams if stream.kind == "video")
        window = scene_window_for_key(
            facts.duration_seconds, source_sha256, result_ref, video.index, unit_key,
        )
        if window is None:
            return None
        rate = (video.avg_frame_rate_fps
                if video.avg_frame_rate_fps is not None
                and video.avg_frame_rate_fps == video.r_frame_rate_fps else None)
        return MaterialSceneCandidate(
            workspace_id, asset_id, str(row["file_path"]), source_sha256,
            str(row["provisional_revision"]), result_ref, video.index, video.codec,
            int(video.width), int(video.height), rate, facts.duration_seconds,
            int(row["size_bytes"]), window, unit_key,
        )

    def pending_material_scene_windows(self) -> list[MaterialSceneCandidate]:
        """Replay missing windows from the metadata fact; completed windows stay durable."""
        pending: list[MaterialSceneCandidate] = []
        for dependency in self.pending_material_scene_dependencies():
            with self._connect() as conn:
                source = self._material_scene_source(
                    conn, dependency.workspace_id, dependency.asset_id,
                    dependency.source_sha256,
                )
                if source is None:
                    continue
                _, facts, result_ref = source
                video = next(stream for stream in facts.streams if stream.kind == "video")
                for window in scene_windows(facts.duration_seconds):
                    unit_key = scene_unit_key(
                        dependency.source_sha256, result_ref, video.index, window,
                    )
                    checkpoint = conn.execute(
                        """SELECT stage_input_digest, result_json, result_ref
                           FROM material_analysis_checkpoints WHERE workspace_id=?
                             AND asset_id=? AND source_sha256=? AND provisional_revision=?
                             AND unit_key=?""",
                        (dependency.workspace_id, dependency.asset_id,
                         dependency.source_sha256, dependency.provisional_revision, unit_key),
                    ).fetchone()
                    if checkpoint is not None and self._valid_scene_checkpoint(checkpoint, unit_key, window):
                        continue
                    candidate = self.material_scene_candidate(
                        dependency.workspace_id, dependency.asset_id,
                        dependency.source_sha256, unit_key, conn,
                    )
                    if candidate is not None:
                        pending.append(candidate)
                    if len(pending) >= 8:
                        return pending
        return pending

    @staticmethod
    def _valid_scene_checkpoint(row: sqlite3.Row, unit_key: str, window: SceneWindow) -> bool:
        result_json = row["result_json"]
        if (row["stage_input_digest"] != unit_key.rsplit(":", 1)[-1]
                or not isinstance(result_json, str)
                or row["result_ref"] != "sha256:" + hashlib.sha256(result_json.encode()).hexdigest()):
            return False
        try:
            return SceneWindowFacts.from_checkpoint_json(result_json).window == window
        except ValueError:
            return False

    def material_scene_verdict_for_grant(self, grant: Grant) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, "
                "attempt_number FROM device_work_requests WHERE id=?", (grant.request_id,),
            ).fetchone()
            if (row is None or row["owner_type"] != "material_asset"
                    or (row["workspace_id"], row["input_digest"], row["unit_key"],
                        row["attempt_number"])
                    != (grant.workspace_id, grant.input_digest, grant.unit_key,
                        grant.attempt_number)):
                return "unauthorized"
            if not re.fullmatch(r"[0-9a-f]{64}", grant.input_digest):
                return "stale"
            source_verdict = self.material_probe_source_verdict(
                grant.workspace_id, str(row["owner_id"]), grant.input_digest,
                material_probe_unit_key(grant.input_digest), conn,
            )
            if source_verdict != "ready":
                return source_verdict
            return ("ready" if self.material_scene_candidate(
                grant.workspace_id, str(row["owner_id"]), grant.input_digest,
                grant.unit_key, conn,
            ) is not None else "stale")

    def material_scene_candidate_for_grant(self, grant: Grant) -> MaterialSceneCandidate:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, "
                "attempt_number FROM device_work_requests WHERE id=?", (grant.request_id,),
            ).fetchone()
            if (row is None or row["owner_type"] != "material_asset"
                    or (row["workspace_id"], row["input_digest"], row["unit_key"],
                        row["attempt_number"])
                    != (grant.workspace_id, grant.input_digest, grant.unit_key,
                        grant.attempt_number)):
                raise PermissionError("镜头许可与执行对象不匹配")
            candidate = self.material_scene_candidate(
                grant.workspace_id, str(row["owner_id"]), grant.input_digest,
                grant.unit_key, conn,
            )
            if candidate is None:
                raise ValueError("镜头窗口来源已变化")
            return candidate

    def commit_material_scene(
        self, conn: sqlite3.Connection, grant: Grant,
        candidate: MaterialSceneCandidate, facts: SceneWindowFacts,
    ) -> str:
        request = conn.execute(
            "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, "
            "attempt_number, state FROM device_work_requests WHERE id=?",
            (grant.request_id,),
        ).fetchone()
        if (request is None or request["owner_type"] != "material_asset"
                or (request["owner_id"], request["workspace_id"], request["input_digest"],
                    request["unit_key"], request["attempt_number"])
                != (candidate.asset_id, grant.workspace_id, grant.input_digest,
                    grant.unit_key, grant.attempt_number)
                or request["state"] not in {"granted", "yield_requested"}
                or facts.window != candidate.window
                or self.material_scene_candidate(
                    candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
                    candidate.unit_key, conn,
                ) != candidate):
            raise ValueError("镜头检查点不属于当前许可或来源已变化")
        result_json = facts.checkpoint_json()
        if SceneWindowFacts.from_checkpoint_json(result_json) != facts:
            raise ValueError("镜头切点事实无效")
        result_ref = "sha256:" + hashlib.sha256(result_json.encode()).hexdigest()
        existing = conn.execute(
            """SELECT stage_input_digest, result_json, result_ref
               FROM material_analysis_checkpoints WHERE workspace_id=? AND asset_id=?
                 AND source_sha256=? AND provisional_revision=? AND unit_key=?""",
            (candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
             candidate.provisional_revision, candidate.unit_key),
        ).fetchone()
        if existing is not None and (
            existing["stage_input_digest"] != candidate.unit_key.rsplit(":", 1)[-1]
            or existing["result_json"] != result_json or existing["result_ref"] != result_ref
        ):
            raise ValueError("既有镜头检查点结果不匹配")
        if existing is None:
            conn.execute(
                """INSERT INTO material_analysis_checkpoints
                   (workspace_id, asset_id, source_sha256, provisional_revision,
                    unit_key, stage_input_digest, result_json, result_ref, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
                 candidate.provisional_revision, candidate.unit_key,
                 candidate.unit_key.rsplit(":", 1)[-1], result_json, result_ref,
                 utc_now().isoformat()),
            )
        self._assemble_material_scene_if_complete(conn, candidate)
        return f"material_scene:{candidate.asset_id}:{candidate.window.start_seconds:.3f}"

    def _assemble_material_scene_if_complete(
        self, conn: sqlite3.Connection, candidate: MaterialSceneCandidate,
    ) -> None:
        expected_windows = math.ceil(candidate.duration_seconds / SCENE_LOGICAL_WINDOW_SECONDS)
        completed_windows = conn.execute(
            """SELECT COUNT(*) FROM material_analysis_checkpoints
               WHERE workspace_id=? AND asset_id=? AND source_sha256=?
                 AND provisional_revision=? AND unit_key LIKE 'scene-window:v1:%'""",
            (candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
             candidate.provisional_revision),
        ).fetchone()[0]
        if completed_windows < expected_windows:
            return
        boundaries: list[float] = []
        for window in scene_windows(candidate.duration_seconds):
            unit_key = scene_unit_key(
                candidate.source_sha256, candidate.probe_result_ref,
                candidate.video_stream_index, window,
            )
            row = conn.execute(
                """SELECT stage_input_digest, result_json, result_ref
                   FROM material_analysis_checkpoints WHERE workspace_id=? AND asset_id=?
                     AND source_sha256=? AND provisional_revision=? AND unit_key=?""",
                (candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
                 candidate.provisional_revision, unit_key),
            ).fetchone()
            if row is None:
                return
            if not self._valid_scene_checkpoint(row, unit_key, window):
                raise ValueError("镜头检查点损坏，不能装配片段")
            boundaries.extend(SceneWindowFacts.from_checkpoint_json(row["result_json"]).boundaries_seconds)
        row = self._material_assets._asset_payload_row_by_id(
            conn, candidate.workspace_id, candidate.asset_id,
        )
        if row is None:
            raise ValueError("镜头装配时素材已不存在")
        self._material_assets.assemble_scene_segments(conn, str(row["payload"]), boundaries, candidate.duration_seconds)

    def fail_material_scene(
        self, conn: sqlite3.Connection, grant: Grant,
        candidate: MaterialSceneCandidate, reason: str,
    ) -> None:
        request = conn.execute(
            "SELECT owner_type, owner_id, workspace_id, input_digest, unit_key, "
            "attempt_number, state FROM device_work_requests WHERE id=?",
            (grant.request_id,),
        ).fetchone()
        if (request is None or request["owner_type"] != "material_asset"
                or (request["owner_id"], request["workspace_id"], request["input_digest"],
                    request["unit_key"], request["attempt_number"])
                != (candidate.asset_id, grant.workspace_id, grant.input_digest,
                    grant.unit_key, grant.attempt_number)
                or request["state"] not in {"granted", "yield_requested"}
                or self.material_scene_candidate(
            candidate.workspace_id, candidate.asset_id, candidate.source_sha256,
            candidate.unit_key, conn,
        ) != candidate):
            raise ValueError("镜头失败来源已变化")
        row = self._material_assets._asset_payload_row_by_id(
            conn, candidate.workspace_id, candidate.asset_id,
        )
        self._material_assets.mark_scene_failed(conn, str(row["payload"]), reason)

    def scan_material_ingestion(self, *, stable_seconds: float = 10.0) -> list[tuple[str, str]]:
        """Discover pending copies, then claim only explicitly confirmed revisions."""
        import time

        binding = self.get_material_ingestion_binding()
        if binding is None:
            return []
        workspace_id, bound_path = binding
        root = self.video_library_path
        if not self._material_library_available() or str(root.resolve()) != bound_path:
            self._mark_material_ingestion_disk_missing(workspace_id)
            return []
        now = time.time()
        claimed: list[tuple[str, str]] = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS or "_已处理" in path.parts:
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()):
                continue
            try:
                stat = resolved.stat()
            except FileNotFoundError:
                continue
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM material_ingestion_files WHERE workspace_id = ? AND file_path = ?",
                    (workspace_id, str(resolved)),
                ).fetchone()
                changed = row is None or (
                    row["size_bytes"], row["mtime_ns"], row["ctime_ns"], row["inode"]
                ) != (
                    stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino
                )
                if changed:
                    conn.execute(
                        "INSERT INTO material_ingestion_files (workspace_id, file_path, size_bytes, mtime_ns, ctime_ns, inode, observed_at, state) VALUES (?, ?, ?, ?, ?, ?, ?, 'observing') "
                        "ON CONFLICT(workspace_id, file_path) DO UPDATE SET size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns, ctime_ns=excluded.ctime_ns, inode=excluded.inode, observed_at=excluded.observed_at, state='observing'",
                        (workspace_id, str(resolved), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, now),
                    )
                elif row["state"] in {"ready", "failed"}:
                    # Size, mtime, ctime and inode are the incremental boundary.
                    # Full content is hashed only on change, recovery, or retry.
                    continue
                elif row["confirmed_revision"] != json.dumps(self.material_copy_revision(stat), sort_keys=True):
                    continue
            if changed:
                try:
                    asset = self._asset_by_path(workspace_id, str(resolved))
                except KeyError:
                    continue
                asset.analysis_status = "blocked"
                asset.analysis_message = "文件已变化；请确认复制完成后重新接收。"
                self._save_asset(asset)
                continue
            try:
                with resolved.open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                final_stat = resolved.stat()
                if (final_stat.st_size, final_stat.st_mtime_ns, final_stat.st_ctime_ns, final_stat.st_ino) != (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino):
                    continue
            except OSError:
                continue
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM material_ingestion_files WHERE workspace_id = ? AND file_path = ?",
                    (workspace_id, str(resolved)),
                ).fetchone()
                if row is None or (
                    row["size_bytes"], row["mtime_ns"], row["ctime_ns"], row["inode"]
                ) != (
                    stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino
                ):
                    continue
                if row["confirmed_revision"] != json.dumps(self.material_copy_revision(final_stat), sort_keys=True):
                    continue
                if row["completed_digest"] == digest:
                    if row["state"] != "ready":
                        conn.execute("UPDATE material_ingestion_files SET state='ready' WHERE workspace_id=? AND file_path=?", (workspace_id, str(resolved)))
                    completed = True
                    failed_before = False
                elif row["state"] == "failed" and row["attempted_digest"] == digest:
                    completed = False
                    failed_before = True
                else:
                    completed = False
                    failed_before = False
                    # An interrupted process leaves 'running'; the next process can reclaim it.
                    conn.execute(
                        "UPDATE material_ingestion_files SET attempted_digest=?, state='running' WHERE workspace_id=? AND file_path=?",
                        (digest, workspace_id, str(resolved)),
                    )
            if completed:
                try:
                    asset = self._asset_by_path(workspace_id, str(resolved))
                except KeyError:
                    continue
                if asset.analysis_status == "blocked":
                    asset.analysis_status = "ready"
                    asset.analysis_message = "分析已完成；外接盘已恢复。"
                    self._save_asset(asset)
                continue
            if failed_before:
                continue
            try:
                asset = self._upsert_discovered_asset(workspace_id, resolved)
            except FileNotFoundError:
                continue
            if asset.segments or asset.analysis_status in {"ready", "failed"}:
                self._reset_changed_material_asset(asset)
            else:
                asset.analysis_status = "pending"
                asset.analysis_message = "复制完成确认已保存，正在分析；确认不代表文件通过校验。"
                self._save_asset(asset)
            claimed.append((asset.id, digest))
        return claimed

    def finish_material_ingestion(self, workspace_id: str, asset_id: str, digest: str, *, failed: bool) -> None:
        asset = self.get_asset(workspace_id, asset_id)
        with self._connect() as conn:
            conn.execute(
                "UPDATE material_ingestion_files SET state=?, completed_digest=CASE WHEN ? THEN completed_digest ELSE ? END "
                "WHERE workspace_id=? AND file_path=? AND attempted_digest=?",
                ("failed" if failed else "ready", int(failed), digest, workspace_id, asset.path, digest),
            )

    def fail_material_ingestion(self, workspace_id: str, asset_id: str, digest: str, reason: Exception) -> None:
        asset = self.get_asset(workspace_id, asset_id)
        asset.analysis_status = "failed"
        asset.analysis_message = f"素材分析失败：{_material_analysis_public_error(reason)}。检查后可手动重试。"
        self._save_asset(asset)
        self.finish_material_ingestion(workspace_id, asset_id, digest, failed=True)

    def retry_material_ingestion(self, workspace_id: str, asset_id: str) -> None:
        asset = self.get_asset(workspace_id, asset_id)
        binding = self.get_material_ingestion_binding()
        if binding is None or binding[0] != workspace_id:
            raise ValueError("当前工作区未绑定素材目录")
        self._require_material_copy_confirmation(workspace_id, self._validated_library_file(asset.path))
        with self._connect() as conn:
            conn.execute("UPDATE material_ingestion_files SET state='observing', attempted_digest=NULL, completed_digest=NULL WHERE workspace_id=? AND file_path=?", (workspace_id, asset.path))
        asset.analysis_status = "pending"
        asset.analysis_message = "已请求重试，将按已确认的文件修订重新分析。"
        self._save_asset(asset)

    def update_asset_tags(self, workspace_id: str, asset_id: str, user_tags: list[str], *, expected_asset_updated_at: datetime | None = None) -> MaterialAsset:
        return self._material_assets.update_asset_tags(workspace_id, asset_id, user_tags, expected_asset_updated_at=expected_asset_updated_at)

    def update_assets_tags_batch(
        self, workspace_id: str, asset_ids: list[str], *, add: list[str], remove: list[str]
    ) -> list[MaterialAsset]:
        return self._material_assets.update_assets_tags_batch(workspace_id, asset_ids, add=add, remove=remove)

    def update_asset_notes(
        self, workspace_id: str, notes: list[MaterialAssetNote]
    ) -> list[MaterialAsset]:
        return self._material_assets.update_asset_notes(workspace_id, notes)

    def list_business_objects(self, workspace_id: str) -> list[MaterialBusinessObject]:
        return self._material_business_objects.list_business_objects(workspace_id)

    def create_business_object(self, workspace_id: str, kind: str, name: str) -> MaterialBusinessObject:
        return self._material_business_objects.create_business_object(workspace_id, kind, name)

    def list_asset_business_objects(self, workspace_id: str, asset_id: str) -> list[MaterialBusinessObject]:
        return self._material_business_objects.list_asset_business_objects(workspace_id, asset_id)

    def set_asset_business_objects(
        self, workspace_id: str, asset_id: str, business_object_ids: list[str]
    ) -> list[MaterialBusinessObject]:
        return self._material_business_objects.set_asset_business_objects(workspace_id, asset_id, business_object_ids)

    def _mark_material_ingestion_disk_missing(self, workspace_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE material_ingestion_files SET state='blocked_disk' WHERE workspace_id=? AND state='ready'",
                (workspace_id,),
            )
            rows = self._material_assets._disk_missing_asset_rows(conn, workspace_id)
        self._material_assets.mark_disk_missing_assets(rows)

    def _reset_changed_material_asset(self, asset: MaterialAsset) -> None:
        self._preserve_unmatched_corrections(asset, asset.segments)
        asset.segments = []
        asset.speech_analysis = None
        asset.duration_seconds = None
        asset.analysis_status = "pending"
        asset.analysis_message = "源文件已变化，正在重新分析；原有人工纠正已保留待核对。"
        self._save_asset(asset)

    def _preserve_unmatched_corrections(self, asset: MaterialAsset, segments: list[MaterialSegment]) -> int:
        corrected = self._corrected_material_segments(segments)
        if not corrected:
            return 0
        with self._connect() as conn:
            for segment in corrected:
                conn.execute(
                    "INSERT OR IGNORE INTO material_unmatched_corrections (workspace_id, asset_id, segment_id, payload, preserved_at) VALUES (?, ?, ?, ?, ?)",
                    (asset.workspace_id, asset.id, segment.id, _json_payload(segment), utc_now().isoformat()),
                )
        return len(corrected)

    _corrected_material_segments = staticmethod(MaterialAssetStore._corrected_material_segments)

    def list_unmatched_material_corrections(self, workspace_id: str, asset_id: str) -> list[dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM material_unmatched_corrections WHERE workspace_id=? AND asset_id=? ORDER BY preserved_at", (workspace_id, asset_id)).fetchall()
        return [json.loads(str(row["payload"])) for row in rows]

    def list_assets(
        self,
        workspace_id: str,
        *,
        include_private: bool = True,
        viewer_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> list[MaterialAsset]:
        return self._material_assets.list_assets(workspace_id, include_private=include_private, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)

    def list_assets_page(
        self,
        workspace_id: str,
        *,
        limit: int,
        offset: int,
        query: str | None = None,
        tag: str | None = None,
        review_status: str | None = None,
        media_type: str | None = None,
        include_private: bool = True,
        viewer_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> tuple[list[MaterialAsset], int]:
        return self._material_assets.list_assets_page(workspace_id, limit=limit, offset=offset, query=query, tag=tag, review_status=review_status, media_type=media_type, include_private=include_private, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)

    def search_material_segments(
        self,
        workspace_id: str,
        *,
        query: str = "",
        asset_ids: list[str] | None = None,
        eligible_segment_refs: set[tuple[str, str]] | None = None,
        usage_roles: list[AssetUsageRole] | None = None,
        tags: list[str] | None = None,
        min_duration_seconds: float | None = None,
        max_duration_seconds: float | None = None,
        limit: int = 20,
        viewer_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> list[MaterialSegmentMatch]:
        """Search one workspace's approved, persisted segments and explain every candidate.

        硬过滤（workspace 隔离、已审核、已持久化、调用方给的结构化条件）保持严格，
        任何放宽都只发生在排序上：文字匹配不上时依次降级到「分词包含」和
        「仅按结构化条件返回」，并且每条候选都带上可核对的依据。
        """
        roles = set(usage_roles or [])
        if not roles <= {"unassigned", "a_roll", "b_roll", "exclude"}:
            raise ValueError("素材用途条件无效")
        requested_tags = [value.strip().casefold() for value in (tags or [])]
        if any(not value for value in requested_tags):
            raise ValueError("素材标签条件不能为空")
        if min_duration_seconds is not None and min_duration_seconds < 0:
            raise ValueError("最小镜头时长不能为负数")
        if max_duration_seconds is not None and max_duration_seconds < 0:
            raise ValueError("最大镜头时长不能为负数")
        if (
            min_duration_seconds is not None
            and max_duration_seconds is not None
            and min_duration_seconds > max_duration_seconds
        ):
            raise ValueError("镜头时长范围无效")
        if not 1 <= limit <= 100:
            raise ValueError("素材检索 limit 必须在 1 到 100 之间")

        normalized_query = query.strip().casefold()
        # 「仅按结构化条件返回」只在调用方真的给了结构化条件时才有意义。
        has_structured_condition = bool(roles or requested_tags) or (
            min_duration_seconds is not None or max_duration_seconds is not None
        )
        matches: list[MaterialSegmentMatch] = []
        selected_assets = set(asset_ids) if asset_ids is not None else None
        for asset in self.list_assets(
            workspace_id,
            include_private=viewer_user_id is not None or local_diagnostic,
            viewer_user_id=viewer_user_id,
            local_diagnostic=local_diagnostic,
        ):
            if selected_assets is not None and asset.id not in selected_assets:
                continue
            if asset.analysis_status != "ready" or asset.review_status == "rejected" or not material_allows_footage(asset):
                continue
            for segment in asset.segments:
                if eligible_segment_refs is not None and (asset.id, segment.id) not in eligible_segment_refs:
                    continue
                if segment.review_status == "rejected":
                    continue
                duration = max(0.0, segment.end_seconds - segment.start_seconds)
                if (
                    min_duration_seconds is not None
                    and duration < min_duration_seconds
                ):
                    continue
                if (
                    max_duration_seconds is not None
                    and duration > max_duration_seconds
                ):
                    continue
                role = selectable_segment_usage_role(asset, segment)
                if roles and role not in roles:
                    continue
                segment_tags = [
                    value.strip()
                    for value in (*segment.system_tags, *segment.user_tags, *asset.user_tags)
                    if value.strip()
                ]
                if requested_tags and not set(requested_tags) <= {
                    value.casefold() for value in segment_tags
                }:
                    continue

                score = 0.1
                reasons = ["当前工作区已分析素材"]
                conditions: list[str] = []
                if roles:
                    score += 0.25
                    reasons.append(f"用途匹配 {role}")
                    conditions.append(f"用途 {role}")
                if requested_tags:
                    score += 0.15 * len(requested_tags)
                    reasons.append("标签匹配 " + "、".join(tags or []))
                    conditions.append("标签 " + "、".join(tags or []))
                if min_duration_seconds is not None or max_duration_seconds is not None:
                    score += 0.1
                    lower = "不限" if min_duration_seconds is None else f"{min_duration_seconds:.1f}"
                    upper = "不限" if max_duration_seconds is None else f"{max_duration_seconds:.1f}"
                    reasons.append(f"时长 {duration:.1f} 秒在 {lower}–{upper} 秒内")
                    conditions.append(f"时长 {duration:.1f} 秒")
                if min_duration_seconds is not None and duration > min_duration_seconds:
                    surplus = min(0.15, 0.02 * (duration - min_duration_seconds))
                    score += surplus
                    reasons.append(
                        f"比要求的最短 {min_duration_seconds:.1f} 秒多"
                        f" {duration - min_duration_seconds:.1f} 秒"
                    )
                    conditions.append(f"比最短要求多 {duration - min_duration_seconds:.1f} 秒")

                tier = "structural"
                matched_terms: list[str] = []
                if normalized_query:
                    text_reasons = _material_query_reasons(
                        normalized_query, segment, asset
                    )
                    if text_reasons:
                        tier = "text"
                        score += 0.45 + 0.1 * len(text_reasons)
                        reasons.extend(text_reasons)
                        matched_terms = _material_partial_terms(
                            normalized_query, segment, asset
                        )
                    else:
                        matched_terms = _material_partial_terms(
                            normalized_query, segment, asset
                        )
                        if matched_terms:
                            tier = "partial"
                            score += 0.25 + 0.03 * min(len(matched_terms), 5)
                            reasons.append(
                                "文字未整段匹配，分词命中 "
                                + "、".join(f"「{term}」" for term in matched_terms)
                            )
                        elif has_structured_condition:
                            tier = "structural"
                            score += 0.05
                            reasons.append("文字未匹配，按结构化条件返回")
                        else:
                            # 既没有文字重合，也没有结构化条件可退：没有东西可返回。
                            continue

                matches.append(
                    MaterialSegmentMatch(
                        asset_ref=f"asset:{asset.id}",
                        segment_ref=f"segment:{segment.id}",
                        score=round(score, 4),
                        reasons=reasons,
                        match_tier=tier,
                        matched_terms=matched_terms,
                        matched_conditions=conditions,
                        asset_file_name=asset.file_name,
                        start_seconds=segment.start_seconds,
                        end_seconds=segment.end_seconds,
                        duration_seconds=round(duration, 3),
                        time_range=(
                            f"{_material_timecode(segment.start_seconds)} – "
                            f"{_material_timecode(segment.end_seconds)}"
                        ),
                        tags=segment_tags,
                    )
                )
        matches.sort(key=lambda item: (-item.score, item.asset_ref, item.segment_ref))
        selected = matches[:limit]
        total = len(selected)
        return [
            replace(
                match,
                rank=rank,
                reasons=[*match.reasons, f"综合排第 {rank} 名（本次候选 {total} 条）"],
            )
            for rank, match in enumerate(selected, start=1)
        ]

    def get_asset(self, workspace_id: str, asset_id: str, *, connection: sqlite3.Connection | None = None) -> MaterialAsset:
        return self._material_assets.get_asset(workspace_id, asset_id, connection=connection)

    def _asset_by_path(self, workspace_id: str, file_path: str) -> MaterialAsset:
        return self._material_assets.get_asset_by_path(workspace_id, file_path)

    def analyze_asset(self, workspace_id: str, asset_id: str, *, expected_source_digest: str | None = None) -> MaterialAsset:
        asset = self.get_asset(workspace_id, asset_id)
        if asset.use_state == "excluded":
            raise ValueError("素材已设为不使用，请先修改使用状态再分析")
        path = self._validated_library_file(asset.path)
        self._require_material_copy_confirmation(workspace_id, path)
        source_stat = path.stat()
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
            source_fingerprint = None
            if self.visual_understanding is not None:
                with path.open("rb") as source:
                    source_fingerprint = hashlib.file_digest(source, "sha256").hexdigest()
            if self.speech_analyzer is not None:
                asset.speech_analysis = self.speech_analyzer.analyze(path, duration, asset.speech_analysis, source_fingerprint)
            if self.visual_understanding is not None:
                observations = self.visual_understanding.analyze(path, segments, source_fingerprint)
                for segment in segments:
                    segment.understanding = observations[segment.id]
            visual_message = self._analyze_visual_semantics(asset, path)
            role_message = self._suggest_usage_roles(asset)
            for segment in asset.segments:
                segment.system_tags = _infer_system_tags(asset, segment)
            asset.analysis_message = (
                f"已拆分 {len(segments)} 个镜头；{visual_message} {role_message} "
                f"{asset.speech_analysis.message if asset.speech_analysis else '未分析原片声音。'} "
                f"画面观察 {sum(bool(item.understanding and item.understanding.status == 'ready') for item in segments)}/{len(segments)} 个片段。"
            ).strip()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            logger.exception("Material analysis failed for asset %s", asset_id)
            asset.analysis_status = "failed"
            asset.analysis_message = f"镜头分析失败：{_material_analysis_public_error(exc)}。检查后可手动重试。"
        final_stat = path.stat()
        if self.material_copy_revision(source_stat) != self.material_copy_revision(final_stat):
            raise ValueError("分析期间原片发生变化，未保存本次结果，请重新分析。")
        if expected_source_digest is not None:
            with path.open("rb") as source:
                final_digest = hashlib.file_digest(source, "sha256").hexdigest()
            if final_digest != expected_source_digest:
                raise ValueError("分析期间原片内容发生变化，未保存本次结果，请重新分析。")
        return self._save_asset(asset, analysis_only=True)

    def get_visual_feature(
        self,
        workspace_id: str,
        segment_id: str,
        model_id: str,
    ) -> list[float] | None:
        with self._connect() as conn:
            row = self._material_assets._visual_feature_asset_payload_row(
                conn, workspace_id, segment_id, model_id,
            )
        if row is None:
            return None
        asset = self._asset_from_payload(json.loads(str(row["payload"])))
        segment = next((item for item in asset.segments if item.id == segment_id), None)
        if segment is None:
            return None
        return self._visual_feature_for_segment(asset, segment, model_id)

    def _visual_feature_for_segment(
        self, asset: MaterialAsset, segment: MaterialSegment, model_id: str
    ) -> list[float] | None:
        # Analysis also calls this before newly detected segments are persisted.
        try:
            path = self._validated_library_file(asset.path)
            fingerprint = _visual_fingerprint(path, segment, model_id)
        except (OSError, ValueError):
            return None
        with self._connect() as conn:
            row = self._material_assets._visual_feature_vector_row(
                conn, asset.workspace_id, asset.id, segment.id, model_id, fingerprint,
            )
        if row is None:
            return None
        payload = json.loads(str(row["vector_json"]))
        if not isinstance(payload, list):
            return None
        return [float(value) for value in payload]

    def set_asset_visibility(
        self, workspace_id: str, asset_id: str, visibility: ContentVisibility
    ) -> MaterialAsset:
        return self._material_assets.set_asset_visibility(workspace_id, asset_id, visibility)

    def set_creation_task_visibility(
        self, workspace_id: str, task_id: str, visibility: ContentVisibility
    ) -> CreationTask:
        """明确共享或收回一条创作任务的可见性。"""
        if visibility not in ("private", "shared"):
            raise ValueError("可见性只能是 private 或 shared。")
        task = self.get_creation_task(workspace_id, task_id)
        task = replace(task, visibility=visibility, updated_at=utc_now())
        with self._connect() as conn:
            conn.execute(
                "UPDATE creation_tasks SET payload=?, visibility=?, updated_at=? WHERE workspace_id=? AND id=?",
                (_json_payload(task), visibility, task.updated_at.isoformat(), workspace_id, task_id),
            )
        return task

    def set_creation_plan_visibility(
        self, workspace_id: str, plan_id: str, visibility: ContentVisibility
    ) -> CreationPlan:
        """明确共享或收回一个创作方案的可见性。"""
        if visibility not in ("private", "shared"):
            raise ValueError("可见性只能是 private 或 shared。")
        plan = self.get_creation_plan(workspace_id, plan_id)
        plan = replace(plan, visibility=visibility, updated_at=utc_now())
        with self._connect() as conn:
            conn.execute(
                "UPDATE creation_plans SET payload=?, visibility=?, updated_at=? WHERE workspace_id=? AND id=?",
                (_json_payload(plan), visibility, plan.updated_at.isoformat(), workspace_id, plan_id),
            )
        return plan

    def set_video_template_visibility(
        self, workspace_id: str, template_id: str, visibility: ContentVisibility
    ) -> VideoTemplate:
        """明确共享或收回一个用户模板的可见性，当前及历史版本一起改。"""
        if visibility not in ("private", "shared"):
            raise ValueError("可见性只能是 private 或 shared。")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload, version FROM video_templates WHERE workspace_id=? AND id=? ORDER BY version DESC",
                (workspace_id, template_id),
            ).fetchall()
            if not rows:
                raise KeyError(template_id)
            latest = None
            for row in rows:
                template = VideoTemplate(**_with_datetime(json.loads(str(row["payload"])), "created_at"))
                template = replace(template, visibility=visibility)
                conn.execute(
                    "UPDATE video_templates SET payload=?, visibility=? WHERE workspace_id=? AND id=? AND version=?",
                    (_json_payload(template), visibility, workspace_id, template_id, int(row["version"])),
                )
                if latest is None:
                    latest = template
        assert latest is not None
        return latest

    def update_asset_use_state(self, workspace_id: str, asset_id: str, use_state: ContentUseState) -> MaterialAsset:
        return self._material_assets.update_asset_use_state(workspace_id, asset_id, use_state)

    def update_asset_review(
        self,
        workspace_id: str,
        asset_id: str,
        *,
        review_status: AssetReviewStatus,
        usage_role: AssetUsageRole = "unassigned",
    ) -> MaterialAsset:
        return self._material_assets.update_asset_review(
            workspace_id, asset_id, review_status=review_status, usage_role=usage_role,
        )

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
        expected_asset_updated_at: datetime | None = None,
        expected_segment_updated_at: datetime | None = None,
    ) -> MaterialAsset:
        return self._material_assets.update_segment(workspace_id, asset_id, segment_id, description=description, transcript=transcript, review_status=review_status, usage_role=usage_role, user_tags=user_tags, collection_ids=collection_ids, expected_asset_updated_at=expected_asset_updated_at, expected_segment_updated_at=expected_segment_updated_at)

    def record_segment_usage(
        self,
        workspace_id: str,
        asset_id: str,
        segment_id: str,
        *,
        plan_id: str | None = None,
        clip_id: str | None = None,
    ) -> MaterialAsset:
        return self._material_assets.record_segment_usage(
            workspace_id, asset_id, segment_id, plan_id=plan_id, clip_id=clip_id,
        )

    def list_segment_usage_records(
        self, workspace_id: str, asset_id: str, segment_id: str
    ) -> list[dict[str, object]]:
        """Read back what used one segment, naming the task it went into.

        Only rows already written by a real render are read, and only for the
        material and segment asked for inside the caller's workspace. Each row
        names the montage plan, its clip and the creation task behind it, so a
        material detail page can show a real traceback instead of a bare count.

        The usage table carries no timeline version, and a task keeps several
        snapshots that may all contain the same clip. Nearest time or highest
        sequence would therefore be a guess, so no version is claimed here: the
        task's current version is reported separately and flagged as current,
        and a task without one reports the version as unrecorded.
        """
        asset = self.get_asset(workspace_id, asset_id)
        if all(segment.id != segment_id for segment in asset.segments):
            raise KeyError(segment_id)
        with self._connect() as conn:
            usage_rows = conn.execute(
                """
                SELECT plan_id, clip_id, used_at
                FROM material_segment_usage
                WHERE workspace_id = ? AND asset_id = ? AND segment_id = ?
                ORDER BY used_at DESC
                """,
                (workspace_id, asset_id, segment_id),
            ).fetchall()
            plan_rows = conn.execute(
                """
                SELECT id, task_id, payload
                FROM creation_task_montage_plans
                WHERE workspace_id = ?
                """,
                (workspace_id,),
            ).fetchall()
            task_rows = conn.execute(
                """
                SELECT id, payload
                FROM creation_tasks
                WHERE workspace_id = ?
                """,
                (workspace_id,),
            ).fetchall()

        plans: dict[str, dict[str, object]] = {}
        for row in plan_rows:
            payload = json.loads(str(row["payload"]))
            clips = {
                str(clip.get("id")): clip
                for clip in payload.get("clips", [])
                if isinstance(clip, dict)
            }
            plans[str(row["id"])] = {"task_id": str(row["task_id"]), "clips": clips}
        tasks = {str(row["id"]): json.loads(str(row["payload"])) for row in task_rows}

        records: list[dict[str, object]] = []
        for row in usage_rows:
            plan = plans.get(str(row["plan_id"]))
            clip = plan["clips"].get(str(row["clip_id"])) if plan else None
            task = tasks.get(str(plan["task_id"])) if plan else None
            current_version_id = task.get("current_timeline_version_id") if task else None
            records.append(
                {
                    "plan_id": str(row["plan_id"]),
                    "clip_id": str(row["clip_id"]),
                    "used_at": datetime.fromisoformat(str(row["used_at"])),
                    "task": {
                        "id": str(plan["task_id"]) if plan else None,
                        "name": str(task.get("name") or "") if task else None,
                        "stage": str(task.get("stage") or "") if task else None,
                        "status": str(task.get("status") or "") if task else None,
                        "current_timeline_version_id": str(current_version_id)
                        if current_version_id
                        else None,
                    }
                    if plan
                    else None,
                    "role": str(clip.get("role")) if clip else None,
                    "manually_selected": bool(clip.get("manually_selected")) if clip else None,
                    "timeline_version_id": str(current_version_id) if current_version_id else None,
                    "resolution": "current" if current_version_id else "unknown",
                }
            )
        return records

    def library_insights(
        self,
        workspace_id: str,
        *,
        viewer_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> dict[str, object]:
        """素材页统计。与列表同一判据：统计里的数字只包含该调用方读得到的素材，
        否则聚合本身就会泄露别人的私有内容。"""
        assets = self.list_assets(
            workspace_id,
            viewer_user_id=viewer_user_id,
            local_diagnostic=local_diagnostic,
        )
        segments = [segment for asset in assets for segment in asset.segments]
        stats: dict[str, dict[str, object]] = {}

        def blank(tag: str) -> dict[str, object]:
            return {"tag": tag, "asset_count": 0, "segment_count": 0, "duration_seconds": 0.0, "usage_count": 0}

        for asset in assets:
            # A material is counted once per tag name, however many of its
            # segments repeat that name and whichever list carried it. The
            # segment count keeps its own meaning beside it. Names are grouped
            # exactly as stored — the same equality the tag filter uses — so a
            # count here and the rows behind a click are always the same set.
            asset_tags = {
                value
                for value in (
                    *asset.user_tags,
                    *(tag for segment in asset.segments for tag in (*segment.system_tags, *segment.user_tags)),
                )
                if value
            }
            for tag in asset_tags:
                item = stats.setdefault(tag, blank(tag))
                item["asset_count"] = int(item["asset_count"]) + 1
        for segment in segments:
            duration = max(0.0, segment.end_seconds - segment.start_seconds)
            for tag in {*segment.system_tags, *segment.user_tags}:
                item = stats.setdefault(tag, blank(tag))
                item["segment_count"] = int(item["segment_count"]) + 1
                item["duration_seconds"] = float(item["duration_seconds"]) + duration
                item["usage_count"] = int(item["usage_count"]) + segment.usage_count
        coverage_categories = set()
        for tag in stats:
            # Existing taxonomy synonyms and role facts are used only for coverage;
            # original observations and click-to-filter tag counts stay unchanged.
            lowered = tag.casefold()
            for category, terms in _MATERIAL_COVERAGE_TERMS.items():
                if any(term in lowered for term in terms):
                    coverage_categories.add(category)
        if any(effective_segment_usage_role(asset, segment) == "a_roll" for asset in assets for segment in asset.segments):
            coverage_categories.add("主体:人物")
        for tag in DEFAULT_MATERIAL_TAXONOMY:
            stats.setdefault(tag, blank(tag))
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
                if str(item["tag"]) not in coverage_categories
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

    def list_video_templates(
        self,
        workspace_id: str,
        *,
        viewer_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> list[VideoTemplate]:
        """列出模板。与其它内容同一判据：私有模板只对上传者本人可见。"""
        templates = self._list_video_templates_by_visibility(workspace_id)
        if local_diagnostic:
            return templates
        return [
            item for item in templates
            if content_readable(
                user_id=viewer_user_id,
                local_diagnostic=False,
                visibility=item.visibility,
                owner_user_id=item.owner_user_id,
            )
        ]

    def _list_video_templates_by_visibility(self, workspace_id: str) -> list[VideoTemplate]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload, visibility FROM video_templates WHERE workspace_id = ? ORDER BY id, version DESC", (workspace_id,)).fetchall()
        return [self._template_from_row(row) for row in rows]

    def get_video_template(self, workspace_id: str, template_id: str, version: int) -> VideoTemplate:
        with self._connect() as conn:
            row = conn.execute("SELECT payload, visibility FROM video_templates WHERE workspace_id = ? AND id = ? AND version = ?", (workspace_id, template_id, version)).fetchone()
        if row is None:
            raise KeyError(template_id)
        return self._template_from_row(row)

    def save_video_template(
        self,
        template: VideoTemplate,
        *,
        verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> VideoTemplate:
        if template.origin != "user" or template.version < 1 or not template.name.strip():
            raise ValueError("用户模板必须有名称和有效版本。")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior_owner = conn.execute(
                "SELECT payload FROM video_templates WHERE workspace_id=? AND id=? ORDER BY version DESC LIMIT 1",
                (template.workspace_id, template.id),
            ).fetchone()
            if prior_owner is not None:
                # 已有模板沿用库里那一版的归属（legacy 空值也照旧保留）；
                # 新版本不因换人保存而改归属。
                owner = str(
                    json.loads(str(prior_owner["payload"])).get("owner_user_id") or ""
                ).strip() or None
            else:
                # 新模板只取已验证身份；输入里的 owner_user_id 不继承。
                owner = content_owner(verified_owner_user_id)
                if owner is None and not local_diagnostic:
                    owner = None
            template = replace(template, owner_user_id=owner)
            row = conn.execute("SELECT COALESCE(MAX(version), 0) AS version FROM video_templates WHERE workspace_id = ? AND id = ?", (template.workspace_id, template.id)).fetchone()
            if template.version != row["version"] + 1:
                raise ValueError("模板版本已变化，请刷新后重新保存。")
            previous = conn.execute(
                "SELECT visibility FROM video_templates WHERE workspace_id = ? AND id = ? ORDER BY version DESC LIMIT 1",
                (template.workspace_id, template.id),
            ).fetchone()
            # 新版本继承上一版的可见性：共享状态不会因为又存了一版就被收回。
            visibility = previous["visibility"] if previous is not None else template.visibility
            # 继承来的可见性同时进入载荷和返回值：存的和立刻拿到的必须是同一个值。
            template = replace(template, visibility=visibility)
            conn.execute(
                "INSERT INTO video_templates (workspace_id, id, version, payload, visibility) VALUES (?, ?, ?, ?, ?)",
                (template.workspace_id, template.id, template.version, _json_payload(template), visibility),
            )
        return template

    @staticmethod
    def _creation_request_requirements(output_relation, requirements, material_scope, clarification_questions):
        if output_relation is not None and (not isinstance(output_relation, str) or not output_relation.strip()):
            raise ValueError("创作请求的成片关系需要明确填写")
        if not isinstance(requirements, dict) or not isinstance(material_scope, dict):
            raise ValueError("创作要求和素材范围必须是结构化对象")
        if not isinstance(clarification_questions, list) or any(not isinstance(item, str) or not item.strip() for item in clarification_questions):
            raise ValueError("澄清问题必须包含可读的问题正文")
        return (_json_object(requirements), _json_object(material_scope), json.dumps(clarification_questions, ensure_ascii=False))

    @staticmethod
    def _creation_request_row(conn, workspace_id, request_id, user_id, local_diagnostic):
        row = conn.execute("SELECT * FROM creation_requests WHERE workspace_id=? AND id=?", (workspace_id, request_id)).fetchone()
        if row is None or (not local_diagnostic and (not user_id or row["owner_user_id"] != user_id)):
            raise KeyError(request_id)
        return row

    @staticmethod
    def _creation_request_revision(row, expected_revision):
        if type(expected_revision) is not int or row["revision"] != expected_revision:
            raise CreationRequestConflict("创作请求已变化，请重新查看当前要求和规划窗口")

    def _creation_request_window(self, conn, workspace_id, window):
        if window is None:
            return None
        entries = []
        for item in conn.execute(
            """SELECT i.*, p.payload AS actual_plan, t.payload AS actual_task
               FROM creation_request_items i
               LEFT JOIN creation_plans p ON p.id=i.plan_id AND p.workspace_id=?
               LEFT JOIN creation_tasks t ON t.id=i.task_id AND t.workspace_id=?
               WHERE i.window_id=? ORDER BY i.ordinal LIMIT 8""",
            (workspace_id, workspace_id, window["id"]),
        ):
            entries.append({"ordinal": item["ordinal"], "plan_id": item["plan_id"], "task_id": item["task_id"],
                "plan": json.loads(item["actual_plan"] if window["status"] == "confirmed" else item["plan_payload"]),
                "task": json.loads(item["actual_task"] if window["status"] == "confirmed" else item["task_payload"])})
        return {"id": window["id"], "request_id": window["request_id"], "ordinal_start": window["ordinal_start"],
            "status": window["status"], "input_fingerprint": window["input_fingerprint"],
            "created_at": window["created_at"], "confirmed_at": window["confirmed_at"], "entries": entries}

    def _creation_request_header(self, conn, row):
        counts = conn.execute(
            """SELECT COUNT(*) AS expanded_count, COALESCE(SUM(w.status='confirmed'),0) AS confirmed_count
               FROM creation_request_items i JOIN creation_request_windows w ON w.id=i.window_id
               WHERE w.request_id=? AND w.status IN ('proposed','confirmed')""", (row["id"],),
        ).fetchone()
        statuses = conn.execute(
            """SELECT json_extract(t.payload,'$.status') AS status, COUNT(*) AS count
               FROM creation_request_items i JOIN creation_request_windows w ON w.id=i.window_id
               JOIN creation_tasks t ON t.id=i.task_id AND t.workspace_id=?
               WHERE w.request_id=? AND w.status='confirmed' GROUP BY json_extract(t.payload,'$.status')""",
            (row["workspace_id"], row["id"]),
        ).fetchall()
        window = (conn.execute("SELECT * FROM creation_request_windows WHERE request_id=? AND id=?", (row["id"], row["current_window_id"])).fetchone()
                  if row["current_window_id"] else None)
        return {"id": row["id"], "workspace_id": row["workspace_id"], "owner_user_id": row["owner_user_id"] or None,
            "request_key": row["request_key"], "goal": row["goal"], "target_count": row["target_count"],
            "output_relation": row["output_relation"], "requirements": json.loads(row["requirements_json"]),
            "material_scope": json.loads(row["material_scope_json"]), "clarification_questions": json.loads(row["clarification_questions_json"]),
            "revision": row["revision"], "status": row["status"], "expanded_count": counts["expanded_count"],
            "confirmed_count": counts["confirmed_count"], "remaining_count": row["target_count"] - counts["expanded_count"],
            "task_status_counts": {item["status"]: item["count"] for item in statuses},
            "current_window": self._creation_request_window(conn, row["workspace_id"], window),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def create_creation_request(self, workspace_id: str, *, request_key: str, input_fingerprint: str,
                                goal: str, target_count: int, output_relation: str | None,
                                requirements: dict, material_scope: dict, clarification_questions: list[str],
                                verified_owner_user_id: str | None = None, local_diagnostic: bool = False) -> dict:
        owner = content_owner(verified_owner_user_id)
        if not owner and not local_diagnostic:
            raise PermissionError("创作请求需要当前可信成员身份")
        if not workspace_id.strip() or not request_key.strip() or not input_fingerprint.strip() or not goal.strip():
            raise ValueError("创作请求缺少工作区、稳定身份、输入摘要或目标")
        if type(target_count) is not int or not 1 <= target_count <= 9007199254740991:
            raise ValueError("总目标数量必须为正安全整数")
        requirements_json, scope_json, questions_json = self._creation_request_requirements(output_relation, requirements, material_scope, clarification_questions)
        digest = hashlib.sha256(json.dumps({"goal": goal, "target_count": target_count, "output_relation": output_relation,
            "requirements": requirements, "material_scope": material_scope, "clarification_questions": clarification_questions},
            ensure_ascii=False, sort_keys=True, default=_json_default).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT * FROM creation_requests WHERE workspace_id=? AND owner_user_id=? AND request_key=?",
                (workspace_id, owner or "", request_key)).fetchone()
            if prior is not None:
                if prior["input_fingerprint"] != input_fingerprint or prior["creation_input_digest"] != digest:
                    raise CreationRequestConflict("同一创作请求身份已绑定不同的目标或要求")
                return self._creation_request_header(conn, prior)
            request_id = f"request_{uuid4().hex}"
            now = utc_now().isoformat()
            status = "awaiting_clarification" if clarification_questions or (target_count > 1 and output_relation is None) else "awaiting_plan"
            conn.execute("""INSERT INTO creation_requests
                (id,workspace_id,owner_user_id,request_key,input_fingerprint,creation_input_digest,goal,target_count,
                 output_relation,requirements_json,material_scope_json,clarification_questions_json,revision,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                (request_id,workspace_id,owner or "",request_key,input_fingerprint,digest,goal,target_count,output_relation,
                 requirements_json,scope_json,questions_json,status,now,now))
            return self._creation_request_header(conn, self._creation_request_row(conn,workspace_id,request_id,owner,local_diagnostic))

    def get_creation_request(self, workspace_id: str, request_id: str, *, viewer_user_id: str | None = None, local_diagnostic: bool = False) -> dict:
        with self._connect() as conn:
            conn.execute("BEGIN")
            return self._creation_request_header(conn, self._creation_request_row(conn, workspace_id, request_id, content_owner(viewer_user_id), local_diagnostic))

    def revise_creation_request(self, workspace_id: str, request_id: str, *, expected_revision: int,
                                output_relation: str | None, requirements: dict, material_scope: dict, clarification_questions: list[str],
                                verified_owner_user_id: str | None = None, local_diagnostic: bool = False) -> dict:
        values = self._creation_request_requirements(output_relation, requirements, material_scope, clarification_questions)
        owner = content_owner(verified_owner_user_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._creation_request_row(conn, workspace_id, request_id, owner, local_diagnostic)
            self._creation_request_revision(row, expected_revision)
            if conn.execute("SELECT 1 FROM creation_request_windows WHERE request_id=? AND status='confirmed' LIMIT 1", (request_id,)).fetchone():
                raise CreationRequestConflict("已有确认窗口，不能改写其共同要求；请建立新的创作请求")
            conn.execute("UPDATE creation_request_windows SET status='superseded' WHERE request_id=? AND status='proposed'", (request_id,))
            status = "awaiting_clarification" if clarification_questions or (row["target_count"] > 1 and output_relation is None) else "awaiting_plan"
            conn.execute("""UPDATE creation_requests SET output_relation=?,requirements_json=?,material_scope_json=?,clarification_questions_json=?,
                revision=revision+1,status=?,current_window_id=NULL,updated_at=? WHERE id=?""",
                (output_relation,*values,status,utc_now().isoformat(),request_id))
            return self._creation_request_header(conn, self._creation_request_row(conn,workspace_id,request_id,owner,local_diagnostic))

    def save_creation_request_window(self, workspace_id: str, request_id: str, *, expected_revision: int,
                                    input_fingerprint: str, entries: list[tuple[CreationPlan, CreationTask]],
                                    verified_owner_user_id: str | None = None, local_diagnostic: bool = False,
                                    replace_window_id: str | None = None) -> dict:
        if not input_fingerprint.strip() or not 1 <= len(entries) <= 8:
            raise ValueError("规划窗口需要输入摘要且只能包含1至8项")
        owner = content_owner(verified_owner_user_id)
        for plan, task in entries:
            if (plan.workspace_id != workspace_id or task.workspace_id != workspace_id or task.plan_id != plan.id
                    or type(plan.target_count) is not int or plan.target_count != 1):
                raise ValueError("每项必须关联当前工作区的独立单片方案和任务")
        if len({plan.id for plan,_ in entries}) != len(entries) or len({task.id for _,task in entries}) != len(entries):
            raise ValueError("规划窗口不能重复使用方案或任务身份")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._creation_request_row(conn,workspace_id,request_id,owner,local_diagnostic)
            current = conn.execute("SELECT * FROM creation_request_windows WHERE id=?", (row["current_window_id"],)).fetchone()
            if replace_window_id is not None:
                self._creation_request_revision(row,expected_revision)
                if current is None or current["id"] != replace_window_id or current["status"] != "proposed":
                    raise CreationRequestConflict("待重新核对的窗口已变化或已确认，请重新查看当前窗口")
                if current["input_fingerprint"] == input_fingerprint:
                    return self._creation_request_header(conn,row)
            else:
                if current is not None:
                    if current["input_fingerprint"] == input_fingerprint:
                        return self._creation_request_header(conn,row)
                    if current["status"] == "proposed":
                        raise CreationRequestConflict("当前规划窗口尚未确认，请先审阅或修订要求")
                self._creation_request_revision(row,expected_revision)
            if row["status"] == "awaiting_clarification":
                raise CreationRequestConflict("当前创作请求仍有重要决定待澄清，不能生成生产窗口")
            ordinal_start = conn.execute("""SELECT COALESCE(MAX(i.ordinal),0)+1 FROM creation_request_items i
                JOIN creation_request_windows w ON w.id=i.window_id WHERE w.request_id=? AND w.status='confirmed'""", (request_id,)).fetchone()[0]
            if ordinal_start + len(entries) - 1 > row["target_count"]:
                raise CreationRequestConflict("规划窗口超过该请求的总目标数量")
            for plan,task in entries:
                if (conn.execute("SELECT 1 FROM creation_plans WHERE id=?", (plan.id,)).fetchone()
                        or conn.execute("SELECT 1 FROM creation_tasks WHERE id=?", (task.id,)).fetchone()):
                    raise CreationRequestConflict("窗口方案或任务身份已用于现有对象，不能重新关联")
            window_id = f"window_{uuid4().hex}"
            now = utc_now().isoformat()
            if replace_window_id is not None:
                conn.execute("UPDATE creation_request_windows SET status='superseded' WHERE id=?", (replace_window_id,))
            conn.execute("INSERT INTO creation_request_windows(id,request_id,ordinal_start,input_fingerprint,request_revision,status,created_at) VALUES(?,?,?,?,?,'proposed',?)",
                (window_id,request_id,ordinal_start,input_fingerprint,row["revision"],now))
            for offset,(plan,task) in enumerate(entries):
                frozen_plan = replace(plan,owner_user_id=owner)
                frozen_task = replace(task,owner_user_id=owner)
                conn.execute("INSERT INTO creation_request_items(window_id,ordinal,plan_id,task_id,plan_payload,task_payload) VALUES(?,?,?,?,?,?)",
                    (window_id,ordinal_start+offset,plan.id,task.id,_json_payload(frozen_plan),_json_payload(frozen_task)))
            conn.execute("UPDATE creation_requests SET current_window_id=?,status='awaiting_confirmation',revision=revision+1,updated_at=? WHERE id=?", (window_id,now,request_id))
            return self._creation_request_header(conn,self._creation_request_row(conn,workspace_id,request_id,owner,local_diagnostic))

    def confirm_creation_request_window(self, workspace_id: str, request_id: str, window_id: str, *,
                                       expected_revision: int, input_fingerprint: str,
                                       verified_owner_user_id: str | None = None, local_diagnostic: bool = False) -> dict:
        owner = content_owner(verified_owner_user_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._creation_request_row(conn,workspace_id,request_id,owner,local_diagnostic)
            window = conn.execute("SELECT * FROM creation_request_windows WHERE request_id=? AND id=?", (request_id,window_id)).fetchone()
            if window is None:
                raise KeyError(window_id)
            if window["input_fingerprint"] != input_fingerprint:
                raise CreationRequestConflict("规划窗口输入摘要已变化，未确认任务")
            if window["status"] == "confirmed":
                return self._creation_request_header(conn,row)
            self._creation_request_revision(row,expected_revision)
            if window["status"] != "proposed" or row["current_window_id"] != window_id:
                raise CreationRequestConflict("该规划窗口已失效，请审阅当前窗口")
            for item in conn.execute("SELECT * FROM creation_request_items WHERE window_id=? ORDER BY ordinal", (window_id,)).fetchall():
                plan = self._creation_plan_from_payload(json.loads(item["plan_payload"]))
                task = self._creation_task_from_payload(json.loads(item["task_payload"]))
                plan,task = self._prepare_confirmed_creation_plan(plan,task,verified_owner_user_id=owner,local_diagnostic=local_diagnostic)
                for match in plan.material_matches:
                    asset_row = self._material_assets._asset_payload_visibility_row_by_id(
                        conn, workspace_id, match.asset_ref.removeprefix("asset:"),
                    )
                    if asset_row is None:
                        raise ValueError("规划窗口引用的素材已不存在")
                    asset = self._asset_from_payload(json.loads(asset_row["payload"]))
                    segment = next((part for part in asset.segments if part.id == match.segment_ref.removeprefix("segment:")),None)
                    if not content_readable(user_id=owner,local_diagnostic=local_diagnostic,visibility=asset_row["visibility"],owner_user_id=asset.owner_user_id):
                        raise PermissionError("当前成员已无权使用规划窗口的素材")
                    if segment is None or not material_segment_eligible(asset,segment,workspace_id,automation_only=True):
                        raise ValueError("规划窗口引用的素材文件或使用资格已变化")
                if plan.selected_voice:
                    self.creation_plan_voice_reference(workspace_id,plan.selected_voice.profile_id,plan.selected_voice.version,viewer_user_id=owner,local_diagnostic=local_diagnostic)
                if plan.selected_person:
                    self.creation_plan_person_reference(workspace_id,plan.selected_person.profile_id,plan.selected_person.version,viewer_user_id=owner,local_diagnostic=local_diagnostic)
                self._insert_confirmed_creation_plan(conn,plan,task)
            now = utc_now().isoformat()
            conn.execute("UPDATE creation_request_windows SET status='confirmed',confirmed_at=? WHERE id=?", (now,window_id))
            conn.execute("UPDATE creation_requests SET status='active',revision=revision+1,updated_at=? WHERE id=?", (now,request_id))
            return self._creation_request_header(conn,self._creation_request_row(conn,workspace_id,request_id,owner,local_diagnostic))

    def list_creation_request_items(self, workspace_id: str, request_id: str, *, after_ordinal: int = 0, limit: int = 20,
                                   viewer_user_id: str | None = None, local_diagnostic: bool = False) -> dict:
        if type(after_ordinal) is not int or after_ordinal < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("请求条目分页需要非负序号和1至100项页面")
        with self._connect() as conn:
            conn.execute("BEGIN")
            self._creation_request_row(conn,workspace_id,request_id,content_owner(viewer_user_id),local_diagnostic)
            rows = conn.execute("""SELECT i.ordinal,i.window_id,i.plan_id,i.task_id,p.payload AS plan,t.payload AS task
                FROM creation_request_items i JOIN creation_request_windows w ON w.id=i.window_id
                LEFT JOIN creation_plans p ON p.id=i.plan_id AND p.workspace_id=? AND w.status='confirmed'
                LEFT JOIN creation_tasks t ON t.id=i.task_id AND t.workspace_id=? AND w.status='confirmed'
                WHERE w.request_id=? AND w.status IN ('proposed','confirmed') AND i.ordinal>?
                ORDER BY i.ordinal LIMIT ?""",(workspace_id,workspace_id,request_id,after_ordinal,limit+1)).fetchall()
            items = [{"ordinal": item["ordinal"], "window_id": item["window_id"], "plan_id": item["plan_id"], "task_id": item["task_id"],
                "plan": json.loads(item["plan"]) if item["plan"] is not None else None,
                "task": json.loads(item["task"]) if item["task"] is not None else None} for item in rows[:limit]]
            return {"items": items, "has_more": len(rows)>limit, "next_after_ordinal": items[-1]["ordinal"] if len(rows)>limit else None}

    def confirm_creation_plan(
        self,
        plan: CreationPlan,
        task: CreationTask,
        *,
        verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
        reply_reference: dict[str, str] | None = None,
        request_id: str | None = None,
    ) -> tuple[CreationPlan, CreationTask]:
        if reply_reference is not None or request_id is not None:
            if reply_reference is None or request_id is None:
                raise ValueError("对话回复来源绑定需要稳定请求身份")
            return self.confirm_creation_plan_from_reply(
                plan,
                task,
                reply_reference=reply_reference,
                request_id=request_id,
                verified_owner_user_id=verified_owner_user_id,
                local_diagnostic=local_diagnostic,
            )
        plan, task = self._prepare_confirmed_creation_plan(
            plan, task, verified_owner_user_id=verified_owner_user_id,
            local_diagnostic=local_diagnostic,
        )
        with self._connect() as conn:
            self._insert_confirmed_creation_plan(conn, plan, task)
        return self.get_creation_plan(plan.workspace_id, plan.id), self.get_creation_task(task.workspace_id, task.id)

    def confirm_creation_plan_from_reply(
        self,
        plan: CreationPlan,
        task: CreationTask,
        *,
        reply_reference: dict[str, str],
        request_id: str,
        verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> tuple[CreationPlan, CreationTask]:
        reference = _validated_assistant_reply_reference(reply_reference)
        if not request_id.strip():
            raise ValueError("确认方案请求身份不能为空")
        plan, task = self._prepare_confirmed_creation_plan(
            plan, task, verified_owner_user_id=verified_owner_user_id,
            local_diagnostic=local_diagnostic,
        )
        fingerprint_payload = {
            "workspace_id": plan.workspace_id,
            "owner_user_id": task.owner_user_id,
            "reply_reference": reference,
            "plan": _creation_plan_conversion_intent(plan),
            "task": _creation_task_conversion_intent(task),
        }
        request_fingerprint = hashlib.sha256(
            _json_object(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        event_id = _assistant_reply_request_event_id(
            "assistant_reply_plan_confirmed", plan.workspace_id, request_id,
        )
        replay_ids: tuple[str, str] | None = None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM task_events WHERE id = ?", (event_id,),
            ).fetchone()
            if existing is not None:
                event = self._task_event_from_row(existing)
                if (
                    event.event_type != "assistant_reply_plan_confirmed"
                    or event.workspace_id != plan.workspace_id
                    or event.payload.get("request_fingerprint") != request_fingerprint
                ):
                    raise ValueError("方案请求身份已用于不同的对话或方案输入")
                replay_ids = (str(event.payload["plan_id"]), str(event.payload["task_id"]))
            else:
                self._insert_confirmed_creation_plan(conn, plan, task)
                self.append_task_event(
                    TaskEvent(
                        id=event_id,
                        workspace_id=plan.workspace_id,
                        task_id=task.id,
                        job_id=None,
                        event_type="assistant_reply_plan_confirmed",
                        payload={
                            "request_id": request_id,
                            "request_fingerprint": request_fingerprint,
                            "reply_reference": reference,
                            "reply_owner_user_id": task.owner_user_id,
                            "plan_id": plan.id,
                            "task_id": task.id,
                        },
                    ),
                    connection=conn,
                )
        if replay_ids is not None:
            return (
                self.get_creation_plan(plan.workspace_id, replay_ids[0]),
                self.get_creation_task(plan.workspace_id, replay_ids[1]),
            )
        return self.get_creation_plan(plan.workspace_id, plan.id), self.get_creation_task(task.workspace_id, task.id)

    @staticmethod
    def _prepare_confirmed_creation_plan(
        plan: CreationPlan,
        task: CreationTask,
        *,
        verified_owner_user_id: str | None,
        local_diagnostic: bool,
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
        if plan.approved_clip_count > 0 and not plan.material_matches:
            raise ValueError("新创作方案必须固化本次实际命中的素材片段")
        _validate_plan_material_matches(plan.material_matches)
        # 上传者只认已验证身份。dataclass 里的 owner_user_id 是调用方能自填的
        # 字段，新建时一律以这里解析出的身份覆盖：自填的归属不参与判断，
        # 没有可验证身份就不算任何成员的私有内容。
        owner = content_owner(verified_owner_user_id)
        if owner is None and not local_diagnostic:
            # 认证范围里解析不出身份：不算任何成员的私有内容，也不采信输入值。
            plan = replace(plan, owner_user_id=None)
            task = replace(task, owner_user_id=None)
        elif owner:
            plan = replace(plan, owner_user_id=owner)
            task = replace(task, owner_user_id=owner)
        return plan, task

    def _insert_confirmed_creation_plan(
        self, conn: sqlite3.Connection, plan: CreationPlan, task: CreationTask,
    ) -> None:
        now = utc_now()
        plan.creation_task_id = task.id
        plan.updated_at = now
        task.updated_at = now
        for match in plan.material_matches:
            asset_id = match.asset_ref.removeprefix("asset:")
            segment_id = match.segment_ref.removeprefix("segment:")
            asset_row = self._material_assets._asset_payload_row_by_id(
                conn, plan.workspace_id, asset_id,
            )
            if asset_row is None:
                raise ValueError("创作方案引用的素材不属于当前工作区")
            asset = self._asset_from_payload(json.loads(str(asset_row["payload"])))
            segment = next((item for item in asset.segments if item.id == segment_id), None)
            if (
                segment is None
                or asset.analysis_status != "ready"
                or asset.review_status == "rejected"
                or not material_allows_footage(asset)
                or segment.review_status == "rejected"
            ):
                raise ValueError("创作方案引用的素材片段不可用")
        conn.execute(
            "INSERT INTO creation_plans (id, workspace_id, payload, visibility, updated_at) VALUES (?, ?, ?, ?, ?)",
            (plan.id, plan.workspace_id, _json_payload(plan), plan.visibility, plan.updated_at.isoformat()),
        )
        conn.execute(
            "INSERT INTO creation_tasks (id, workspace_id, plan_id, payload, visibility, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (task.id, task.workspace_id, task.plan_id, _json_payload(task), task.visibility, task.updated_at.isoformat()),
        )

    def get_creation_plan(self, workspace_id: str, plan_id: str) -> CreationPlan:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload, visibility FROM creation_plans
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, plan_id),
            ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        data = json.loads(str(row["payload"]))
        if "visibility" in row.keys():
            data["visibility"] = str(row["visibility"])
        return self._creation_plan_from_payload(data)

    def get_creation_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        include_private: bool = True,
    ) -> CreationTask:
        with self._connect() as conn:
            visibility_clause = "" if include_private else " AND visibility = 'shared'"
            row = conn.execute(
                f"""
                SELECT payload, visibility FROM creation_tasks
                WHERE workspace_id = ? AND id = ?{visibility_clause}
                """,
                (workspace_id, task_id),
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._task_from_row(row)

    def list_creation_tasks(
        self,
        workspace_id: str,
        *,
        include_private: bool = True,
        viewer_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> list[CreationTask]:
        """列出创作任务。给出可信读取身份时，私有任务只对上传者本人可见。

        `include_private` 是旧的粗粒度开关（本地单机用）；一旦传入可信身份，
        判据就换成与内容域同一套规则：私有按 owner 过滤，不因为「没带令牌」
        就把别人的私有任务也算进来。
        """
        if local_diagnostic or (viewer_user_id is None and include_private):
            return self._list_creation_tasks_by_visibility(workspace_id, include_private)
        return [
            task for task in self._list_creation_tasks_by_visibility(workspace_id, True)
            if content_readable(
                user_id=viewer_user_id,
                local_diagnostic=False,
                visibility=task.visibility,
                owner_user_id=task.owner_user_id,
            )
        ]

    def _list_creation_tasks_by_visibility(
        self, workspace_id: str, include_private: bool
    ) -> list[CreationTask]:
        with self._connect() as conn:
            visibility_clause = "" if include_private else " AND visibility = 'shared'"
            rows = conn.execute(
                f"""
                SELECT payload, visibility FROM creation_tasks
                WHERE workspace_id = ?{visibility_clause} ORDER BY updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def create_timeline_version(
        self, version: TimelineVersion, *, connection: sqlite3.Connection | None = None,
        expected_current_version_id: str | None | Literal[False] = False,
    ) -> TimelineVersion:
        _validate_timeline_version(version)
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.create_timeline_version(version, connection=conn,
                    expected_current_version_id=expected_current_version_id)
            return self.get_timeline_version(version.workspace_id, version.id)
        conn = connection
        task = self._require_creation_task(conn, version.workspace_id, version.task_id)
        if (expected_current_version_id is not False
                and task.current_timeline_version_id != expected_current_version_id):
            raise ValueError("当前时间线已变化，不能发布旧版本成片")
        existing_row = conn.execute(
            "SELECT * FROM timeline_versions WHERE id = ?",
            (version.id,),
        ).fetchone()
        if existing_row is None:
            existing_row = conn.execute(
                """
                SELECT * FROM timeline_versions
                WHERE workspace_id = ? AND task_id = ? AND sequence = ?
                """,
                (version.workspace_id, version.task_id, version.sequence),
            ).fetchone()
        if existing_row is not None:
            persisted = self._timeline_version_from_row(existing_row)
            if persisted != version:
                raise ValueError("时间线版本已存在，不能覆盖")
            if task.current_timeline_version_id != version.id:
                task.current_timeline_version_id = version.id
                task.updated_at = utc_now()
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
            return persisted
        latest_row = conn.execute(
            """
            SELECT id, sequence FROM timeline_versions
            WHERE workspace_id = ? AND task_id = ?
            ORDER BY sequence DESC LIMIT 1
            """,
            (version.workspace_id, version.task_id),
        ).fetchone()
        if latest_row is None:
            if version.parent_version_id is not None or version.sequence != 1:
                raise ValueError("首个时间线版本必须从序号 1 开始且不能有父版本")
        else:
            parent = conn.execute(
                """
                SELECT sequence FROM timeline_versions
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (
                    version.workspace_id,
                    version.task_id,
                    version.parent_version_id,
                ),
            ).fetchone()
            if parent is None:
                raise ValueError("父时间线版本不属于当前工作区与任务")
            if version.sequence != int(latest_row["sequence"]) + 1:
                raise ValueError("时间线版本序号必须连续递增")
        try:
            conn.execute(
                """
                INSERT INTO timeline_versions
                    (id, workspace_id, task_id, parent_version_id, sequence,
                     snapshot_json, created_by_operation_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version.id,
                    version.workspace_id,
                    version.task_id,
                    version.parent_version_id,
                    version.sequence,
                    _json_object(version.snapshot),
                    version.created_by_operation_id,
                    version.created_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("时间线版本已存在，不能覆盖") from exc
        task.current_timeline_version_id = version.id
        task.updated_at = utc_now()
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
        return version

    def get_timeline_version(
        self, workspace_id: str, version_id: str
    ) -> TimelineVersion:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM timeline_versions WHERE workspace_id = ? AND id = ?",
                (workspace_id, version_id),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return self._timeline_version_from_row(row)

    def list_timeline_versions(
        self, workspace_id: str, task_id: str
    ) -> list[TimelineVersion]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM timeline_versions
                WHERE workspace_id = ? AND task_id = ? ORDER BY sequence ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self._timeline_version_from_row(row) for row in rows]

    def create_job_run(self, run: JobRun, *, connection: sqlite3.Connection | None = None) -> JobRun:
        _validate_new_job_run(run)
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            self._require_creation_task(conn, run.workspace_id, run.task_id)
            existing = conn.execute(
                """
                SELECT * FROM job_runs
                WHERE workspace_id = ? AND idempotency_key = ?
                """,
                (run.workspace_id, run.idempotency_key),
            ).fetchone()
            if existing is not None:
                persisted = self._job_run_from_row(existing)
                if (
                    persisted.task_id != run.task_id
                    or persisted.kind != run.kind
                    or persisted.input_digest != run.input_digest
                ):
                    raise ValueError("幂等键已绑定到不同的任务输入")
                return persisted
            try:
                conn.execute(
                    """
                    INSERT INTO job_runs
                        (id, workspace_id, task_id, kind, idempotency_key,
                         input_digest, status, progress, attempt, output_refs_json,
                         failure_reason, impact, repair_action, created_at,
                         started_at, finished_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _job_run_values(run),
                )
            except sqlite3.IntegrityError as exc:
                raced = conn.execute(
                    "SELECT * FROM job_runs WHERE workspace_id = ? AND idempotency_key = ?",
                    (run.workspace_id, run.idempotency_key),
                ).fetchone()
                if raced is not None:
                    persisted = self._job_run_from_row(raced)
                    if (
                        persisted.task_id != run.task_id
                        or persisted.kind != run.kind
                        or persisted.input_digest != run.input_digest
                    ):
                        raise ValueError("幂等键已绑定到不同的任务输入") from exc
                    return persisted
                raise ValueError("任务运行记录 ID 已存在") from exc
        return run if connection is not None else self.get_job_run(run.workspace_id, run.id)

    def get_job_run(self, workspace_id: str, job_id: str) -> JobRun:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM job_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, job_id),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._job_run_from_row(row)

    def get_job_run_by_idempotency_key(
        self, workspace_id: str, idempotency_key: str
    ) -> JobRun | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM job_runs
                WHERE workspace_id = ? AND idempotency_key = ?
                """,
                (workspace_id, idempotency_key),
            ).fetchone()
        return self._job_run_from_row(row) if row is not None else None

    def list_job_runs(self, workspace_id: str, task_id: str) -> list[JobRun]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM job_runs
                WHERE workspace_id = ? AND task_id = ? ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self._job_run_from_row(row) for row in rows]

    def list_pending_creation_executions(self) -> list[JobRun]:
        """Recover only explicitly persisted execution jobs and their original workspace."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM job_runs WHERE kind = 'creation_task_execution' "
                "AND status IN ('queued', 'interrupted') ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return [self._job_run_from_row(row) for row in rows]

    def claim_job_run(self, workspace_id: str, job_id: str, *, connection: sqlite3.Connection | None = None) -> JobRun:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            row = conn.execute(
                "SELECT * FROM job_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, job_id),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = self._job_run_from_row(row)
            if current.status not in {"queued", "interrupted"}:
                raise ValueError("任务运行记录当前不可领取")
            now = utc_now()
            changed = conn.execute(
                """
                UPDATE job_runs
                SET status = 'running', attempt = ?, started_at = ?, finished_at = NULL,
                    failure_reason = NULL, impact = NULL, repair_action = NULL,
                    updated_at = ?
                WHERE workspace_id = ? AND id = ? AND status = ?
                """,
                (
                    current.attempt + 1,
                    now.isoformat(),
                    now.isoformat(),
                    workspace_id,
                    job_id,
                    current.status,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("任务运行记录已被其他执行者领取")
            row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND id=?", (workspace_id, job_id)).fetchone()
            return self._job_run_from_row(row)

    def update_job_run_progress(
        self, workspace_id: str, job_id: str, progress: int
    ) -> JobRun:
        if not 0 <= progress <= 100:
            raise ValueError("任务进度必须在 0 到 100 之间")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT progress, status FROM job_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, job_id),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if str(row["status"]) != "running":
                raise ValueError("只能更新运行中任务的进度")
            if progress < int(row["progress"]):
                raise ValueError("任务进度不能倒退")
            now = utc_now()
            changed = conn.execute(
                """
                UPDATE job_runs SET progress = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ? AND status = 'running'
                """,
                (progress, now.isoformat(), workspace_id, job_id),
            ).rowcount
            if changed != 1:
                raise ValueError("任务运行记录已结束，进度未写入")
        return self.get_job_run(workspace_id, job_id)

    def finish_job_run(
        self,
        workspace_id: str,
        job_id: str,
        *,
        status: Literal["succeeded", "failed", "cancelled", "interrupted"],
        output_refs: list[str] | None = None,
        failure_reason: str | None = None,
        impact: str | None = None,
        repair_action: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> JobRun:
        if status != "succeeded" and not all(
            value is not None and value.strip()
            for value in (failure_reason, impact, repair_action)
        ):
            raise ValueError("未成功的任务必须记录原因、影响和修复动作")
        refs = output_refs or []
        for reference in refs:
            _validate_logical_reference(reference, "产物输出引用")
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            row = conn.execute(
                "SELECT status, task_id FROM job_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, job_id),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current_status = str(row["status"])
            allowed_sources = {"running"}
            if status == "cancelled":
                allowed_sources.update({"queued", "interrupted"})
            if current_status not in allowed_sources:
                raise ValueError("任务运行记录当前不可结束")
            if status == "succeeded" and refs:
                registered_refs = {
                    str(artifact_row["logical_ref"])
                    for artifact_row in conn.execute(
                        """
                        SELECT logical_ref FROM artifact_records
                        WHERE workspace_id = ? AND task_id = ? AND producer_job_id = ?
                        """,
                        (workspace_id, str(row["task_id"]), job_id),
                    ).fetchall()
                }
                missing_refs = [ref for ref in refs if ref not in registered_refs]
                if missing_refs:
                    raise ValueError("成功任务的输出必须先登记为可追溯产物")
            now = utc_now()
            progress = 100 if status == "succeeded" else None
            changed = conn.execute(
                """
                UPDATE job_runs
                SET status = ?, progress = COALESCE(?, progress), output_refs_json = ?,
                    failure_reason = ?, impact = ?, repair_action = ?,
                    finished_at = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ? AND status = ?
                """,
                (
                    status,
                    progress,
                    json.dumps(refs, ensure_ascii=False),
                    None if status == "succeeded" else failure_reason,
                    None if status == "succeeded" else impact,
                    None if status == "succeeded" else repair_action,
                    now.isoformat(),
                    now.isoformat(),
                    workspace_id,
                    job_id,
                    current_status,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("任务运行记录已被其他执行者结束")
            row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND id=?", (workspace_id, job_id)).fetchone()
            return self._job_run_from_row(row)

    def interrupt_running_job_runs(self, workspace_id: str | None = None) -> int:
        now = utc_now().isoformat()
        with self._connect() as conn:
            workspace_clause = "" if workspace_id is None else " AND workspace_id = ?"
            parameters = (
                (now, now) if workspace_id is None else (now, now, workspace_id)
            )
            return conn.execute(
                """
                UPDATE job_runs
                SET status = 'interrupted', failure_reason = 'process_interrupted',
                    impact = '本次执行未产生可信的成功结果。',
                    repair_action = '重新领取该任务并从已持久化输入恢复。',
                    finished_at = ?, updated_at = ?
                WHERE status = 'running'
                """
                + workspace_clause,
                parameters,
            ).rowcount

    def create_artifact_record(self, artifact: ArtifactRecord, *, connection: sqlite3.Connection | None = None) -> ArtifactRecord:
        _validate_artifact_record(artifact)
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            self._require_creation_task(conn, artifact.workspace_id, artifact.task_id)
            job = conn.execute(
                """
                SELECT task_id FROM job_runs
                WHERE workspace_id = ? AND id = ?
                """,
                (artifact.workspace_id, artifact.producer_job_id),
            ).fetchone()
            if job is None or str(job["task_id"]) != artifact.task_id:
                raise ValueError("产物生产任务不属于当前工作区与创作任务")
            existing_row = conn.execute(
                "SELECT * FROM artifact_records WHERE id = ?",
                (artifact.id,),
            ).fetchone()
            if existing_row is None:
                existing_row = conn.execute(
                    """
                    SELECT * FROM artifact_records
                    WHERE workspace_id = ? AND logical_ref = ?
                    """,
                    (artifact.workspace_id, artifact.logical_ref),
                ).fetchone()
            if existing_row is not None:
                persisted = self._artifact_record_from_row(existing_row)
                if persisted != artifact:
                    raise ValueError("产物记录或逻辑引用已存在，且领域字段不一致")
                return persisted
            try:
                conn.execute(
                    """
                    INSERT INTO artifact_records
                        (id, workspace_id, task_id, kind, logical_ref, sha256,
                         size_bytes, producer_job_id, input_refs_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact.id,
                        artifact.workspace_id,
                        artifact.task_id,
                        artifact.kind,
                        artifact.logical_ref,
                        artifact.sha256,
                        artifact.size_bytes,
                        artifact.producer_job_id,
                        json.dumps(artifact.input_refs, ensure_ascii=False),
                        artifact.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("产物记录或逻辑引用已存在") from exc
            row = conn.execute("SELECT * FROM artifact_records WHERE workspace_id=? AND id=?", (artifact.workspace_id, artifact.id)).fetchone()
            return self._artifact_record_from_row(row)

    def get_artifact_record(
        self, workspace_id: str, artifact_id: str
    ) -> ArtifactRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifact_records WHERE workspace_id = ? AND id = ?",
                (workspace_id, artifact_id),
            ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return self._artifact_record_from_row(row)

    def list_artifact_records(
        self, workspace_id: str, task_id: str
    ) -> list[ArtifactRecord]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM artifact_records
                WHERE workspace_id = ? AND task_id = ? ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self._artifact_record_from_row(row) for row in rows]

    def append_task_event(self, event: TaskEvent, *, connection: sqlite3.Connection | None = None) -> TaskEvent:
        _validate_task_event(event)
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            self._require_creation_task(conn, event.workspace_id, event.task_id)
            if event.job_id is not None:
                job = conn.execute(
                    """
                    SELECT task_id FROM job_runs
                    WHERE workspace_id = ? AND id = ?
                    """,
                    (event.workspace_id, event.job_id),
                ).fetchone()
                if job is None or str(job["task_id"]) != event.task_id:
                    raise ValueError("任务事件关联的运行记录不属于当前任务")
            existing_row = conn.execute(
                "SELECT * FROM task_events WHERE id = ?",
                (event.id,),
            ).fetchone()
            if existing_row is not None:
                persisted = self._task_event_from_row(existing_row)
                if persisted != event:
                    raise ValueError("任务事件 ID 已存在，且领域字段不一致")
                return persisted
            try:
                conn.execute(
                    """
                    INSERT INTO task_events
                        (id, workspace_id, task_id, job_id, event_type,
                         payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        event.workspace_id,
                        event.task_id,
                        event.job_id,
                        event.event_type,
                        _json_object(event.payload),
                        event.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raced = conn.execute(
                    "SELECT * FROM task_events WHERE id = ?", (event.id,)
                ).fetchone()
                if raced is not None:
                    persisted = self._task_event_from_row(raced)
                    if persisted != event:
                        raise ValueError("任务事件 ID 已存在，且领域字段不一致") from exc
                    return persisted
                raise ValueError("任务事件 ID 已存在") from exc
        return event if connection is not None else self.get_task_event(event.workspace_id, event.id)

    def get_task_event(self, workspace_id: str, event_id: str, *,
                       connection: sqlite3.Connection | None = None) -> TaskEvent:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            row = conn.execute(
                "SELECT * FROM task_events WHERE workspace_id = ? AND id = ?",
                (workspace_id, event_id),
            ).fetchone()
        if row is None:
            raise KeyError(event_id)
        return self._task_event_from_row(row)

    def latest_task_resource_admission(self, workspace_id: str, task_id: str, job_id: str,
                                       request_id: str, *,
                                       connection: sqlite3.Connection | None = None) -> TaskEvent | None:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                "SELECT * FROM task_events WHERE workspace_id=? AND task_id=? AND job_id=? "
                "AND event_type='task_media_resource_admission' "
                "AND json_extract(payload_json, '$.device_request_id')=? "
                "ORDER BY json_extract(payload_json, '$.attempt_number') DESC, "
                "COALESCE(json_extract(payload_json, '$.last_grant_generation'), -1) DESC, "
                "created_at DESC, rowid DESC LIMIT 1",
                (workspace_id, task_id, job_id, request_id),
            ).fetchone()
        return self._task_event_from_row(row) if row is not None else None

    def list_task_events(self, workspace_id: str, task_id: str) -> list[TaskEvent]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM task_events
                WHERE workspace_id = ? AND task_id = ? ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self._task_event_from_row(row) for row in rows]

    def create_transform_run(self, run: TransformRun, *, connection: sqlite3.Connection | None = None) -> TransformRun:
        _validate_transform_run(run)
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            self._require_creation_task(conn, run.workspace_id, run.task_id)
            job = conn.execute(
                """
                SELECT task_id FROM job_runs
                WHERE workspace_id = ? AND id = ?
                """,
                (run.workspace_id, run.job_id),
            ).fetchone()
            if job is None or str(job["task_id"]) != run.task_id:
                raise ValueError("变换运行关联的 JobRun 不属于当前工作区与任务")
            if run.status == "succeeded":
                placeholders = ", ".join("?" for _ in run.output_refs)
                rows = conn.execute(
                    f"""
                    SELECT logical_ref, producer_job_id FROM artifact_records
                    WHERE workspace_id = ? AND task_id = ?
                      AND logical_ref IN ({placeholders})
                    """,
                    (run.workspace_id, run.task_id, *run.output_refs),
                ).fetchall()
                artifacts = {
                    str(row["logical_ref"]): str(row["producer_job_id"])
                    for row in rows
                }
                if any(
                    artifacts.get(reference) != run.job_id
                    for reference in run.output_refs
                ):
                    raise ValueError("成功变换的输出必须是该 JobRun 已登记的产物")
            existing_row = conn.execute(
                "SELECT * FROM transform_runs WHERE id = ?",
                (run.id,),
            ).fetchone()
            if existing_row is not None:
                persisted = self._transform_run_from_row(existing_row)
                if persisted != run:
                    raise ValueError("变换运行记录 ID 已存在，且领域字段不一致")
                return persisted
            try:
                conn.execute(
                    """
                    INSERT INTO transform_runs
                        (id, workspace_id, task_id, job_id, transform_type,
                         input_digest, input_refs_json, output_refs_json, engine,
                         parameters_json, status, started_at, finished_at,
                         failure_reason, remediation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id,
                        run.workspace_id,
                        run.task_id,
                        run.job_id,
                        run.transform_type,
                        run.input_digest,
                        json.dumps(run.input_refs, ensure_ascii=False),
                        json.dumps(run.output_refs, ensure_ascii=False),
                        run.engine,
                        _json_object(run.parameters),
                        run.status,
                        run.started_at.isoformat(),
                        run.finished_at.isoformat() if run.finished_at else None,
                        run.failure_reason,
                        run.remediation,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("变换运行记录 ID 已存在") from exc
            row = conn.execute("SELECT * FROM transform_runs WHERE workspace_id=? AND id=?", (run.workspace_id, run.id)).fetchone()
            return self._transform_run_from_row(row)

    def get_transform_run(self, workspace_id: str, transform_id: str) -> TransformRun:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM transform_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, transform_id),
            ).fetchone()
        if row is None:
            raise KeyError(transform_id)
        return self._transform_run_from_row(row)

    def list_transform_runs(
        self, workspace_id: str, task_id: str
    ) -> list[TransformRun]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM transform_runs
                WHERE workspace_id = ? AND task_id = ? ORDER BY started_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self._transform_run_from_row(row) for row in rows]

    def create_dependency_edge(self, edge: DependencyEdge, *, connection: sqlite3.Connection | None = None) -> DependencyEdge:
        _validate_dependency_edge(edge)
        if connection is None:
            with self._connect() as conn:
                self.create_dependency_edge(edge, connection=conn)
            return self.get_dependency_edge(edge.workspace_id, edge.id)
        conn = connection
        self._require_creation_task(conn, edge.workspace_id, edge.task_id)
        transform_row = conn.execute(
            """
            SELECT * FROM transform_runs
            WHERE workspace_id = ? AND task_id = ? AND id = ?
            """,
            (
                edge.workspace_id,
                edge.task_id,
                edge.created_by_transform_id,
            ),
        ).fetchone()
        if transform_row is None:
            raise ValueError("依赖边关联的变换不属于当前工作区与任务")
        transform = self._transform_run_from_row(transform_row)
        if transform.status != "succeeded":
            raise ValueError("只能为成功变换登记依赖边")
        if edge.upstream_ref not in transform.input_refs:
            raise ValueError("依赖边上游不在变换输入引用中")
        if edge.downstream_ref not in transform.output_refs:
            raise ValueError("依赖边下游不在变换输出引用中")
        existing_row = conn.execute(
            "SELECT * FROM dependency_edges WHERE id = ?",
            (edge.id,),
        ).fetchone()
        if existing_row is None:
            existing_row = conn.execute(
                """
                SELECT * FROM dependency_edges
                WHERE workspace_id = ? AND task_id = ?
                  AND upstream_ref = ? AND downstream_ref = ?
                  AND created_by_transform_id = ?
                """,
                (
                    edge.workspace_id,
                    edge.task_id,
                    edge.upstream_ref,
                    edge.downstream_ref,
                    edge.created_by_transform_id,
                ),
            ).fetchone()
        if existing_row is not None:
            persisted = self._dependency_edge_from_row(existing_row)
            if persisted != edge:
                raise ValueError("依赖边 ID 或逻辑唯一键已存在，且领域字段不一致")
            return persisted
        try:
            conn.execute(
                """
                INSERT INTO dependency_edges
                    (id, workspace_id, task_id, upstream_ref, downstream_ref,
                     created_by_transform_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.id,
                    edge.workspace_id,
                    edge.task_id,
                    edge.upstream_ref,
                    edge.downstream_ref,
                    edge.created_by_transform_id,
                    edge.created_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("依赖边 ID 或同一变换中的边已存在") from exc
        return edge

    def get_dependency_edge(
        self, workspace_id: str, edge_id: str
    ) -> DependencyEdge:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM dependency_edges WHERE workspace_id = ? AND id = ?",
                (workspace_id, edge_id),
            ).fetchone()
        if row is None:
            raise KeyError(edge_id)
        return self._dependency_edge_from_row(row)

    def list_dependency_edges(
        self, workspace_id: str, task_id: str
    ) -> list[DependencyEdge]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM dependency_edges
                WHERE workspace_id = ? AND task_id = ? ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self._dependency_edge_from_row(row) for row in rows]

    def save_narration_segments(
        self,
        workspace_id: str,
        task_id: str,
        script_id: str,
        segments: list[NarrationSegment],
    ) -> list[NarrationSegment]:
        """Persist the complete current narration fact set for one confirmed script."""
        with self._connect() as conn:
            self._upsert_narration_segments(
                conn,
                workspace_id=workspace_id,
                task_id=task_id,
                script_id=script_id,
                segments=segments,
            )
        return self.list_narration_segments(workspace_id, task_id, script_id)

    def get_narration_segment(
        self, workspace_id: str, task_id: str, segment_id: str
    ) -> NarrationSegment:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                """
                SELECT * FROM narration_segments
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, segment_id),
            ).fetchone()
        if row is None:
            raise KeyError(segment_id)
        return self._narration_segment_from_row(row)

    def list_narration_segments(
        self, workspace_id: str, task_id: str, script_id: str | None = None
    ) -> list[NarrationSegment]:
        with self._connect() as conn:
            task = self._require_creation_task(conn, workspace_id, task_id)
            current_script_id = script_id or task.confirmed_script_id
            if current_script_id is None:
                return []
            rows = conn.execute(
                """
                SELECT * FROM narration_segments
                WHERE workspace_id = ? AND task_id = ? AND script_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id, current_script_id),
            ).fetchall()
        return [self._narration_segment_from_row(row) for row in rows]

    def create_person_render(self, render: PersonRender) -> PersonRender:
        _validate_person_render(render)
        if render.status != "pending":
            raise ValueError("新人物片段必须从 pending 状态开始")
        with self._connect() as conn:
            self._require_creation_task(conn, render.workspace_id, render.task_id)
            narration_row = conn.execute(
                """
                SELECT * FROM narration_segments
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (render.workspace_id, render.task_id, render.narration_segment_id),
            ).fetchone()
            if narration_row is None:
                raise ValueError("人物片段关联的配音分段不属于当前任务")
            narration = self._narration_segment_from_row(narration_row)
            if narration.status != "ready" or narration.audio_reference is None:
                raise ValueError("人物片段只能使用 ready 配音分段")
            if (
                narration.voice_profile_id != render.voice_profile_id
                or narration.voice_version != render.voice_version
            ):
                raise ValueError("人物片段声音版本必须与配音分段一致")
            self._get_person_profile(
                conn,
                render.workspace_id,
                render.person_profile_id,
                render.person_version,
            )
            self._get_versioned_voice_profile(
                conn,
                render.workspace_id,
                render.voice_profile_id,
                render.voice_version,
            )
            self.resolve_workspace_media_reference(
                render.workspace_id,
                render.visual_reference,
                require_registered=True,
            )
            existing = conn.execute(
                """
                SELECT * FROM person_renders
                WHERE workspace_id = ? AND task_id = ? AND cache_key = ?
                """,
                (render.workspace_id, render.task_id, render.cache_key),
            ).fetchone()
            if existing is not None:
                persisted = self._person_render_from_row(existing)
                if _person_render_identity(persisted) != _person_render_identity(render):
                    raise ValueError("人物片段缓存键已绑定到不同输入")
                return persisted
            conn.execute(
                """
                INSERT INTO person_renders
                    (id, workspace_id, task_id, person_profile_id, person_version,
                     voice_profile_id, voice_version, narration_segment_id,
                     visual_reference, model_id, motion, expression, cache_key,
                     status, artifact_ref, output_path, duration_seconds, alignment_evidence,
                     failure_reason, impact, repair_action, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _person_render_values(render),
            )
        return self.get_person_render(render.workspace_id, render.task_id, render.id)

    def claim_person_render(
        self, workspace_id: str, task_id: str, render_id: str
    ) -> PersonRender:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status FROM person_renders
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, render_id),
            ).fetchone()
            if row is None:
                raise KeyError(render_id)
            current = str(row["status"])
            if current not in {"pending", "failed"}:
                raise ValueError("人物片段当前不可领取")
            now = utc_now().isoformat()
            changed = conn.execute(
                """
                UPDATE person_renders
                SET status = 'running', output_path = NULL,
                    duration_seconds = NULL, alignment_evidence = NULL,
                    failure_reason = NULL, impact = NULL, repair_action = NULL,
                    updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ? AND status = ?
                """,
                (now, workspace_id, task_id, render_id, current),
            ).rowcount
            if changed != 1:
                raise ValueError("人物片段已被其他执行者领取")
        return self.get_person_render(workspace_id, task_id, render_id)

    def finish_person_render(
        self,
        workspace_id: str,
        task_id: str,
        render_id: str,
        *,
        status: Literal["ready", "failed"],
        output_path: str | None = None,
        duration_seconds: float | None = None,
        alignment_evidence: str | None = None,
        failure_reason: str | None = None,
        impact: str | None = None,
        repair_action: str | None = None,
    ) -> PersonRender:
        candidate = replace(
            self.get_person_render(workspace_id, task_id, render_id),
            status=status,
            output_path=output_path,
            duration_seconds=duration_seconds,
            alignment_evidence=alignment_evidence,
            failure_reason=failure_reason,
            impact=impact,
            repair_action=repair_action,
            updated_at=utc_now(),
        )
        _validate_person_render(candidate)
        with self._connect() as conn:
            changed = conn.execute(
                """
                UPDATE person_renders
                SET status = ?, output_path = ?, duration_seconds = ?,
                    alignment_evidence = ?, failure_reason = ?, impact = ?,
                    repair_action = ?, updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                  AND status = 'running'
                """,
                (
                    candidate.status,
                    candidate.output_path,
                    candidate.duration_seconds,
                    candidate.alignment_evidence,
                    candidate.failure_reason,
                    candidate.impact,
                    candidate.repair_action,
                    candidate.updated_at.isoformat(),
                    workspace_id,
                    task_id,
                    render_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("只能结束运行中的人物片段")
        return self.get_person_render(workspace_id, task_id, render_id)

    def bind_person_render_artifact(
        self,
        workspace_id: str,
        task_id: str,
        render_id: str,
        artifact_ref: str,
    ) -> PersonRender:
        _validate_logical_reference(artifact_ref, "人物片段产物引用")
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            render_row = conn.execute(
                """
                SELECT * FROM person_renders
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, render_id),
            ).fetchone()
            if render_row is None:
                raise KeyError(render_id)
            render = self._person_render_from_row(render_row)
            if render.status != "ready":
                raise ValueError("只能为 ready 人物片段绑定产物")
            if render.artifact_ref is not None:
                if render.artifact_ref != artifact_ref:
                    raise ValueError("人物片段已绑定其他产物引用")
                return render
            artifact_row = conn.execute(
                """
                SELECT task_id, kind FROM artifact_records
                WHERE workspace_id = ? AND logical_ref = ?
                """,
                (workspace_id, artifact_ref),
            ).fetchone()
            if artifact_row is None or str(artifact_row["task_id"]) != task_id:
                raise ValueError("人物片段产物不属于当前工作区与创作任务")
            if str(artifact_row["kind"]) != "person_render":
                raise ValueError("人物片段产物类型必须为 person_render")
            now = utc_now().isoformat()
            changed = conn.execute(
                """
                UPDATE person_renders
                SET artifact_ref = ?, updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                  AND status = 'ready' AND artifact_ref IS NULL
                """,
                (artifact_ref, now, workspace_id, task_id, render_id),
            ).rowcount
            if changed != 1:
                raise ValueError("人物片段产物引用未能绑定")
        return self.get_person_render(workspace_id, task_id, render_id)

    def get_person_render(
        self, workspace_id: str, task_id: str, render_id: str
    ) -> PersonRender:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                """
                SELECT * FROM person_renders
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, render_id),
            ).fetchone()
        if row is None:
            raise KeyError(render_id)
        return self._person_render_from_row(row)

    def list_person_renders(
        self, workspace_id: str, task_id: str
    ) -> list[PersonRender]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM person_renders
                WHERE workspace_id = ? AND task_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self._person_render_from_row(row) for row in rows]

    def interrupt_running_person_renders(self, workspace_id: str | None = None) -> int:
        now = utc_now().isoformat()
        workspace_clause = "" if workspace_id is None else " AND workspace_id = ?"
        parameters: tuple[object, ...] = (
            (now,) if workspace_id is None else (now, workspace_id)
        )
        with self._connect() as conn:
            return conn.execute(
                """
                UPDATE person_renders
                SET status = 'failed', failure_reason = 'process_interrupted',
                    impact = '本次人物片段执行未产生可信输出。',
                    repair_action = '重新生成该人物片段。', updated_at = ?
                WHERE status = 'running'
                """
                + workspace_clause,
                parameters,
            ).rowcount

    def create_subtitle_track(self, track: SubtitleTrack) -> SubtitleTrack:
        _validate_subtitle_track(track)
        with self._connect() as conn:
            self._require_creation_task(conn, track.workspace_id, track.task_id)
            timeline = conn.execute(
                """
                SELECT 1 FROM timeline_versions
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (track.workspace_id, track.task_id, track.timeline_version_id),
            ).fetchone()
            if timeline is None:
                raise ValueError("字幕轨必须关联当前任务的时间线版本")
            try:
                conn.execute(
                    """
                    INSERT INTO subtitle_tracks
                        (id, workspace_id, task_id, timeline_version_id, language,
                         payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        track.id,
                        track.workspace_id,
                        track.task_id,
                        track.timeline_version_id,
                        track.language,
                        _json_payload(track),
                        track.created_at.isoformat(),
                        track.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("同一时间线版本的字幕轨已存在") from exc
        return self.get_subtitle_track(track.workspace_id, track.task_id, track.id)

    def get_subtitle_track(
        self, workspace_id: str, task_id: str, track_id: str
    ) -> SubtitleTrack:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                """
                SELECT * FROM subtitle_tracks
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, track_id),
            ).fetchone()
        if row is None:
            raise KeyError(track_id)
        return self._subtitle_track_from_row(row)

    def list_subtitle_tracks(
        self,
        workspace_id: str,
        task_id: str,
        *,
        timeline_version_id: str | None = None,
    ) -> list[SubtitleTrack]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            if timeline_version_id is None:
                rows = conn.execute(
                    """
                    SELECT * FROM subtitle_tracks
                    WHERE workspace_id = ? AND task_id = ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (workspace_id, task_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM subtitle_tracks
                    WHERE workspace_id = ? AND task_id = ? AND timeline_version_id = ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (workspace_id, task_id, timeline_version_id),
                ).fetchall()
        return [self._subtitle_track_from_row(row) for row in rows]

    def create_cover_asset(self, cover: CoverAsset) -> CoverAsset:
        _validate_cover_asset(cover)
        with self._connect() as conn:
            self._require_creation_task(conn, cover.workspace_id, cover.task_id)
            timeline = conn.execute(
                """
                SELECT 1 FROM timeline_versions
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (cover.workspace_id, cover.task_id, cover.timeline_version_id),
            ).fetchone()
            if timeline is None:
                raise ValueError("封面必须关联当前任务的时间线版本")
            try:
                conn.execute(
                    """
                    INSERT INTO cover_assets
                        (id, workspace_id, task_id, timeline_version_id,
                         payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cover.id,
                        cover.workspace_id,
                        cover.task_id,
                        cover.timeline_version_id,
                        _json_payload(cover),
                        cover.created_at.isoformat(),
                        cover.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("同一时间线版本的封面已存在") from exc
        return self.get_cover_asset(cover.workspace_id, cover.task_id, cover.id)

    def get_cover_asset(
        self, workspace_id: str, task_id: str, cover_id: str
    ) -> CoverAsset:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                """
                SELECT * FROM cover_assets
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, cover_id),
            ).fetchone()
        if row is None:
            raise KeyError(cover_id)
        return self._cover_asset_from_row(row)

    def list_cover_assets(
        self,
        workspace_id: str,
        task_id: str,
        *,
        timeline_version_id: str | None = None,
    ) -> list[CoverAsset]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            if timeline_version_id is None:
                rows = conn.execute(
                    """
                    SELECT * FROM cover_assets
                    WHERE workspace_id = ? AND task_id = ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (workspace_id, task_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM cover_assets
                    WHERE workspace_id = ? AND task_id = ? AND timeline_version_id = ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (workspace_id, task_id, timeline_version_id),
                ).fetchall()
        return [self._cover_asset_from_row(row) for row in rows]

    def save_clip_preview_proposal(self, intent: EditIntent, proposal: EditProposal, *, expected_version_id: str):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._require_creation_task(conn, intent.workspace_id, intent.task_id)
            if task.current_timeline_version_id != expected_version_id:
                raise TimelineVersionConflict("预览期间时间线发生变化，请重新预览")
            if (proposal.workspace_id, proposal.task_id, proposal.intent_id) != (intent.workspace_id, intent.task_id, intent.id):
                raise ValueError("预览提案不属于原编辑意图")
            self.create_edit_intent(intent, connection=conn, source=None)
            self.create_edit_proposal(proposal, connection=conn)
        return intent, proposal

    def create_clip_preview_job(self, run: JobRun, payload: dict, *, expected_version_id: str) -> JobRun:
        """Compare the caller's original version and save the job/input together."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._require_creation_task(conn, run.workspace_id, run.task_id)
            if task.current_timeline_version_id != expected_version_id:
                raise TimelineVersionConflict("提交预览前时间线发生变化，请重新预览")
            if task.owner_user_id and task.owner_user_id != (payload.get("user_id") or payload["member_id"]):
                raise PermissionError("当前成员不能安排此任务的媒体处理")
            job = self.create_job_run(run, connection=conn)
            self.append_task_event(TaskEvent(id=f"event_{job.id}_start", workspace_id=job.workspace_id,
                task_id=job.task_id, job_id=job.id, event_type="task_media_submitted", payload=payload,
                created_at=job.created_at), connection=conn)
        return job

    def record_task_media_publication(self, artifact: ArtifactRecord, transform: TransformRun, edges: list[DependencyEdge]):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.create_artifact_record(artifact, connection=conn)
            self.create_transform_run(transform, connection=conn)
            for edge in edges:
                self.create_dependency_edge(edge, connection=conn)
        return artifact

    def create_edit_intent(self, intent: EditIntent, *, connection: sqlite3.Connection | None = None, source: dict | None | Literal[False] = False) -> EditIntent:
        source = EXACT_EDIT_SOURCE.get() if source is False else source
        if source is not None:
            if (source["workspace_id"], source["task_id"]) != (intent.workspace_id, intent.task_id):
                raise ValueError("类型化编辑来源不属于当前任务")
            intent = replace(intent, raw_command=source.get("raw_command") or intent.raw_command,
                             constraints={**intent.constraints, "request_source": source})
        _validate_edit_intent(intent)
        if connection is None:
            with self._connect() as conn:
                self.create_edit_intent(intent, connection=conn, source=None)
            return self.get_edit_intent(intent.workspace_id, intent.id)
        conn = connection
        self._require_creation_task(conn, intent.workspace_id, intent.task_id)
        existing_row = conn.execute(
            "SELECT * FROM edit_intents WHERE id = ?",
            (intent.id,),
        ).fetchone()
        if existing_row is not None:
            persisted = self._edit_intent_from_row(existing_row)
            if persisted != intent:
                raise ValueError("编辑意图 ID 已存在，不能覆盖")
            return persisted
        conn.execute(
            """
            INSERT INTO edit_intents
                (id, workspace_id, task_id, raw_command, context_stage,
                 selected_object_refs_json, playhead_ms, intent_type,
                 scope_json, constraints_json, confidence, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent.id,
                intent.workspace_id,
                intent.task_id,
                intent.raw_command,
                intent.context_stage,
                json.dumps(intent.selected_object_refs, ensure_ascii=False),
                intent.playhead_ms,
                intent.intent_type,
                _json_object(intent.scope),
                _json_object(intent.constraints),
                intent.confidence,
                intent.status,
                intent.created_at.isoformat(),
            ),
        )
        return intent

    def get_edit_intent(self, workspace_id: str, intent_id: str) -> EditIntent:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM edit_intents WHERE workspace_id = ? AND id = ?",
                (workspace_id, intent_id),
            ).fetchone()
        if row is None:
            raise KeyError(intent_id)
        return self._edit_intent_from_row(row)

    def assistant_business_candidates(self, *, limit: int = 32) -> list[dict]:
        """Pending projections of existing jobs/proposals, never another queue."""
        with self._connect() as conn:
            jobs = conn.execute(
                "SELECT j.id,j.workspace_id,j.task_id,j.status,j.created_at,e.payload_json "
                "FROM job_runs j JOIN task_events e ON e.workspace_id=j.workspace_id AND e.job_id=j.id "
                "WHERE e.event_type='task_media_submitted' AND j.status IN ('succeeded','failed','cancelled') "
                "AND json_extract(e.payload_json,'$.arguments._typed_source.assistant_turn_id') IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM task_events done WHERE done.id='event_assistant_job_'||j.id||'_'||j.status) "
                "ORDER BY j.created_at LIMIT ?", (limit,),
            ).fetchall()
            candidates = [{"kind": "job", "id": row["id"], "workspace_id": row["workspace_id"],
                "task_id": row["task_id"], "status": row["status"], "created_at": row["created_at"],
                "source": json.loads(row["payload_json"])["arguments"]["_typed_source"],
                "event_id": f"event_assistant_job_{row['id']}_{row['status']}"} for row in jobs]
            proposals = conn.execute(
                "SELECT p.id,p.workspace_id,p.task_id,p.status,p.created_at,i.constraints_json "
                "FROM edit_proposals p JOIN edit_intents i ON i.id=p.intent_id AND i.workspace_id=p.workspace_id "
                "WHERE json_extract(i.constraints_json,'$.request_source.assistant_turn_id') IS NOT NULL "
                "AND p.status IN ('proposed','confirmed','rejected','expired') "
                "AND (p.status!='proposed' OR (json_extract(i.constraints_json,'$.request_source.media_job_id') IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM task_events submitted WHERE submitted.workspace_id=p.workspace_id "
                "AND submitted.task_id=p.task_id AND submitted.event_type='task_media_submitted' "
                "AND json_extract(submitted.payload_json,'$.arguments._typed_source.request_id')=json_extract(i.constraints_json,'$.request_source.request_id')))) "
                "AND NOT EXISTS (SELECT 1 FROM task_events done WHERE done.id='event_assistant_proposal_'||p.id||'_'||p.status) "
                "ORDER BY p.created_at LIMIT ?", (limit,),
            ).fetchall()
            candidates.extend({"kind": "proposal", "id": row["id"], "workspace_id": row["workspace_id"],
                "task_id": row["task_id"], "status": row["status"], "created_at": row["created_at"],
                "source": json.loads(row["constraints_json"])["request_source"],
                "event_id": f"event_assistant_proposal_{row['id']}_{row['status']}"} for row in proposals)
            requests = conn.execute(
                "SELECT e.* FROM task_events e WHERE e.event_type='assistant_edit_request_located' "
                "AND (json_extract(e.payload_json,'$.locator.status') IN ('rejected','not_submitted') "
                "OR EXISTS (SELECT 1 FROM task_events receipt WHERE receipt.id=json_extract(e.payload_json,'$.request_event_id')||'_result')) "
                "AND NOT EXISTS (SELECT 1 FROM task_events done WHERE done.id='event_assistant_request_'||e.id) "
                "ORDER BY e.created_at LIMIT ?", (limit,),
            ).fetchall()
            candidates.extend({"kind": "request", "id": row["id"], "workspace_id": row["workspace_id"],
                "task_id": row["task_id"], "created_at": row["created_at"], "status": "recorded",
                "source": json.loads(row["payload_json"])["source"], "request": json.loads(row["payload_json"]),
                "event_id": f"event_assistant_request_{row['id']}"} for row in requests)
        return candidates

    def list_edit_intents(
        self, workspace_id: str, task_id: str
    ) -> list[EditIntent]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM edit_intents
                WHERE workspace_id = ? AND task_id = ? ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self._edit_intent_from_row(row) for row in rows]

    def create_edit_proposal(self, proposal: EditProposal, *, connection: sqlite3.Connection | None = None) -> EditProposal:
        _validate_new_edit_proposal(proposal)
        if connection is None:
            with self._connect() as conn:
                self.create_edit_proposal(proposal, connection=conn)
            return self.get_edit_proposal(proposal.workspace_id, proposal.id)
        conn = connection
        self._require_creation_task(conn, proposal.workspace_id, proposal.task_id)
        intent_row = conn.execute(
            """
            SELECT 1 FROM edit_intents
            WHERE workspace_id = ? AND task_id = ? AND id = ?
            """,
            (proposal.workspace_id, proposal.task_id, proposal.intent_id),
        ).fetchone()
        if intent_row is None:
            raise ValueError("编辑提案关联的意图不属于当前工作区与任务")
        existing_row = conn.execute(
            "SELECT * FROM edit_proposals WHERE id = ?",
            (proposal.id,),
        ).fetchone()
        if existing_row is not None:
            persisted = self._edit_proposal_from_row(existing_row)
            if persisted != proposal:
                raise ValueError("编辑提案 ID 已存在，且领域字段不一致")
            return persisted
        conn.execute(
            """
            INSERT INTO edit_proposals
                (id, workspace_id, task_id, intent_id, summary,
                 target_refs_json, operation_json, invalidated_refs_json,
                 preview_json, external_effects_json, status, created_at,
                 updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal.id,
                proposal.workspace_id,
                proposal.task_id,
                proposal.intent_id,
                proposal.summary,
                json.dumps(proposal.target_refs, ensure_ascii=False),
                _json_object(proposal.operation),
                json.dumps(proposal.invalidated_refs, ensure_ascii=False),
                _json_object(proposal.preview),
                json.dumps(
                    proposal.external_effects,
                    ensure_ascii=False,
                    default=_json_default,
                ),
                proposal.status,
                proposal.created_at.isoformat(),
                proposal.updated_at.isoformat(),
            ),
        )
        return proposal

    def get_edit_proposal(
        self, workspace_id: str, proposal_id: str
    ) -> EditProposal:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM edit_proposals WHERE workspace_id = ? AND id = ?",
                (workspace_id, proposal_id),
            ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return self._edit_proposal_from_row(row)

    def list_edit_proposals(
        self, workspace_id: str, task_id: str, *,
        base_version_id: str | None | Literal[False] = False, limit: int | None = None,
    ) -> list[EditProposal]:
        if limit is not None and not 1 <= limit <= 100:
            raise ValueError("编辑提案读取数量需为1至100")
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            current_clause = " AND status='proposed' AND json_extract(operation_json,'$.payload.base_version_id') IS ?" if base_version_id is not False else ""
            order = "DESC" if limit is not None else "ASC"
            rows = conn.execute(
                f"""
                SELECT * FROM edit_proposals
                WHERE workspace_id = ? AND task_id = ?{current_clause} ORDER BY created_at {order}, id {order}{' LIMIT ?' if limit is not None else ''}
                """,
                (workspace_id, task_id, *([base_version_id] if base_version_id is not False else []), *([limit] if limit is not None else [])),
            ).fetchall()
        return [self._edit_proposal_from_row(row) for row in rows]

    def transition_edit_proposal(
        self,
        workspace_id: str,
        task_id: str,
        proposal_id: str,
        *,
        status: Literal["confirmed", "rejected", "expired"],
    ) -> EditProposal:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                """
                SELECT * FROM edit_proposals
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, proposal_id),
            ).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            current = self._edit_proposal_from_row(row)
            if current.status != "proposed":
                raise ValueError("只有 proposed 编辑提案可以确认、拒绝或过期")
            now = utc_now()
            changed = conn.execute(
                """
                UPDATE edit_proposals SET status = ?, updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                  AND status = 'proposed'
                """,
                (
                    status,
                    now.isoformat(),
                    workspace_id,
                    task_id,
                    proposal_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("编辑提案已被其他操作转换")
        return self.get_edit_proposal(workspace_id, proposal_id)

    def resolve_edit_clip_source(self, workspace_id, task_id, clip, values, *, user_id, local_diagnostic):
        replacing = values.get("asset_id") is not None or values.get("segment_id") is not None
        if clip.person_render_id and not replacing:
            render = self.get_person_render(workspace_id, task_id, clip.person_render_id)
            if render.status != "ready" or render.duration_seconds is None:
                raise TimelineVersionConflict("人物口型片段不可用，请重新生成")
            return ClipSourceSelection(0., render.duration_seconds, True)
        asset = self.get_asset(workspace_id, values.get("asset_id") or clip.asset_id)
        segment_id = values.get("segment_id") or clip.segment_id
        segment = next((item for item in asset.segments if item.id == segment_id), None)
        if not content_readable(user_id=user_id, local_diagnostic=local_diagnostic,
                visibility=asset.visibility, owner_user_id=asset.owner_user_id):
            raise PermissionError("当前成员无权使用该素材")
        if segment is None:
            raise KeyError("material segment not found")
        if not material_segment_eligible(asset, segment, workspace_id, automation_only=bool(values.get('automation_only'))):
            raise TimelineVersionConflict("素材片段当前不可用，请检查来源、范围和使用限制")
        speech = effective_segment_usage_role(asset, segment) == "a_roll"
        return ClipSourceSelection(segment.start_seconds, segment.end_seconds, speech,
            asset.id, segment.id, self.workspace_media_reference_for_asset(workspace_id, asset.id),
            "a_roll" if speech else "b_roll")

    def clip_edit_source_facts(self, workspace_id: str, task_id: str, *, clip_ids: list[str],
                               replacement: tuple[str, str] | None, user_id: str | None,
                               local_diagnostic: bool, connection: sqlite3.Connection | None = None):
        """The current material/source authority shared by typed preview and direct commit."""
        if connection is None:
            with self._connect() as conn:
                return self.clip_edit_source_facts(workspace_id, task_id, clip_ids=clip_ids,
                    replacement=replacement, user_id=user_id, local_diagnostic=local_diagnostic, connection=conn)
        plan = self.get_creation_task_montage_plan(workspace_id, task_id, connection=connection)
        clips = [clip for clip in plan["clips"] if clip["id"] in clip_ids]
        if len(clips) != len(clip_ids):
            raise KeyError("当前任务镜头不存在")
        pairs = [(clip["asset_id"], clip["segment_id"]) for clip in clips if not clip.get("person_render_id")]
        if replacement is not None:
            pairs.append(replacement)
        facts = []
        for asset_id, segment_id in dict.fromkeys(pairs):
            asset = self.get_asset(workspace_id, asset_id, connection=connection)
            if not content_readable(user_id=user_id, local_diagnostic=local_diagnostic,
                    visibility=asset.visibility, owner_user_id=asset.owner_user_id):
                raise PermissionError("当前成员不能使用该镜头素材")
            segment = next((item for item in asset.segments if item.id == segment_id), None)
            if segment is None:
                raise ValueError("镜头来源或使用状态已失效")
            reference = self.workspace_media_reference_for_asset(workspace_id, asset_id, connection=connection)
            path = self.resolve_clip_media_reference(workspace_id, asset_id, reference, connection=connection)
            stat = path.stat()
            facts.append({"asset_id":asset_id, "segment_id":segment_id, "reference":reference,
                "modified_at":asset.modified_at.isoformat(), "start_seconds":segment.start_seconds,
                "end_seconds":segment.end_seconds, "size":stat.st_size, "mtime_ns":stat.st_mtime_ns,
                "ctime_ns":stat.st_ctime_ns, "inode":stat.st_ino, "device":stat.st_dev})
        return facts

    def claim_edit_request(self, task, member_id, request_id, phase, inputs):
        identity = hashlib.sha256(json.dumps(
            [task.workspace_id, member_id, str(request_id), phase], sort_keys=True,
            ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()
        event_id = f"event_edit_request_{identity}"
        digest = hashlib.sha256(json.dumps([task.id, inputs], sort_keys=True,
            ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload_json FROM task_events WHERE id=?", (event_id,)).fetchone()
            if row:
                previous = json.loads(row["payload_json"])
                if previous["input_digest"] != digest:
                    raise ValueError("请求身份已绑定不同编辑输入")
                return event_id, False
            self.append_task_event(TaskEvent(id=event_id, workspace_id=task.workspace_id,
                task_id=task.id, job_id=None, event_type="edit_request_claimed",
                payload={"member_id": member_id, "request_id": str(request_id), "phase": phase,
                         "input_digest": digest, "inputs": inputs}, created_at=utc_now()), connection=conn)
        return event_id, True

    def save_edit_request_result(self, task, event_id, *, status_code, response):
        return self.append_task_event(TaskEvent(id=event_id + "_result", workspace_id=task.workspace_id,
            task_id=task.id, job_id=None, event_type="edit_request_result",
            payload={"status_code": status_code, "response": response}, created_at=utc_now()))

    def read_edit_request_result(self, task, event_id):
        try:
            event = self.get_task_event(task.workspace_id, event_id + "_result")
        except KeyError:
            return None
        return event.payload

    def confirm_direct_edit(self, command, intent: EditIntent, proposal: EditProposal, change, *, verify_sources, connection=None):
        """Save lineage and full change in the caller transaction or one owned CAS."""
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                return self.confirm_direct_edit(command, intent, proposal, change,
                    verify_sources=verify_sources, connection=conn)
        conn = connection
        task = self._require_creation_task(conn, command.workspace_id, command.task_id)
        if task.current_timeline_version_id != command.base_version_id:
            raise TimelineVersionConflict('当前时间线已变化，未保存这项修改')
        member = command.actor_identity.get('user_id') or command.actor_identity['member_id']
        if task.owner_user_id and task.owner_user_id != member and (not command.actor_identity.get('local_diagnostic')):
            raise PermissionError('当前成员不能修改此任务')
        if (intent.workspace_id, intent.task_id, proposal.intent_id, change.operation.proposal_id) != (command.workspace_id, command.task_id, intent.id, proposal.id):
            raise ValueError('直接编辑命令与变更谱系不一致')
        verify_sources(command, conn)
        self.create_edit_intent(intent, connection=conn, source=None)
        self.create_edit_proposal(proposal, connection=conn)
        result = self.confirm_edit_proposal(command.workspace_id, command.task_id, proposal.id, operation=change.operation, result_version=change.version, montage_plan_id=change.montage_plan_id, montage_payload=change.montage_payload, subtitle_tracks=change.subtitle_tracks, cover_asset=change.cover_asset, narration_segments=change.narration_segments, narration_artifact=change.narration_artifact, connection=conn)
        return result

    def confirm_edit_proposal(
        self,
        workspace_id: str,
        task_id: str,
        proposal_id: str,
        *,
        operation: EditOperation,
        result_version: TimelineVersion,
        montage_plan_id: str | None = None,
        montage_payload: dict[str, object] | None = None,
        narration_segments: list[NarrationSegment] | None = None,
        narration_artifact: NarrationArtifactRecord | None = None,
        subtitle_tracks: list[SubtitleTrack] | None = None,
        cover_asset: CoverAsset | None = None,
        connection: sqlite3.Connection | None = None,
        verify_sources=None,
    ) -> tuple[EditProposal, EditOperation, TimelineVersion, CreationTask]:
        """Confirm applied facts; a supplied permit transaction owns the commit."""
        _validate_edit_operation(operation)
        _validate_timeline_version(result_version)
        if operation.status != "applied":
            raise ValueError("原子确认只能写入已应用编辑操作")
        if (
            operation.workspace_id != workspace_id
            or operation.task_id != task_id
            or operation.proposal_id != proposal_id
            or result_version.workspace_id != workspace_id
            or result_version.task_id != task_id
            or operation.result_version_id != result_version.id
            or result_version.parent_version_id != operation.base_version_id
            or result_version.created_by_operation_id != operation.id
        ):
            raise ValueError("提案、编辑操作与结果时间线版本谱系不一致")
        if (montage_plan_id is None) != (montage_payload is None):
            raise ValueError("混剪计划 ID 与 payload 必须同时提供")
        if operation.operation_type == "RegenerateNarrationSegment" and (
            narration_segments is None or montage_payload is None
        ):
            raise ValueError("声音或字幕编辑确认必须同时保存完整配音分段与 montage/cue 事实")
        if operation.operation_type == "UpdateSubtitleCue" and not (
            subtitle_tracks or (narration_segments is not None and montage_payload is not None)
        ):
            raise ValueError("字幕编辑确认必须保存完整字幕轨或迁移前 montage/cue 事实")
        if operation.operation_type in {"ConfigureSubtitleTrack", "ReplaceSubtitleText", "AlignSubtitleTiming"} and not subtitle_tracks:
            raise ValueError("字幕轨配置确认必须保存字幕轨事实")
        if operation.operation_type == "GenerateCoverFromFrame" and cover_asset is None:
            raise ValueError("封面确认必须保存封面事实")
        for track in subtitle_tracks or []:
            _validate_subtitle_track(track)
            if (
                track.workspace_id != workspace_id
                or track.task_id != task_id
                or track.timeline_version_id != result_version.id
            ):
                raise ValueError("字幕轨与结果时间线版本谱系不一致")
        if cover_asset is not None:
            _validate_cover_asset(cover_asset)
            if (
                cover_asset.workspace_id != workspace_id
                or cover_asset.task_id != task_id
                or cover_asset.timeline_version_id != result_version.id
            ):
                raise ValueError("封面与结果时间线版本谱系不一致")
        if narration_artifact is not None:
            if narration_segments is None:
                raise ValueError("整轨配音产物必须与完整分段事实同时保存")
            if (
                narration_artifact.workspace_id != workspace_id
                or narration_artifact.task_id != task_id
                or not Path(narration_artifact.audio_path).is_file()
                or Path(narration_artifact.audio_path).stat().st_size == 0
            ):
                raise ValueError("整轨配音产物与当前任务不同源或音频无效")
        if montage_payload is not None:
            allowed_montage_statuses = (
                {"waiting_for_user", "ready", "rendered"}
                if operation.operation_type in {"UndoTimelineVersion", "RedoTimelineVersion"}
                else {"waiting_for_user", "ready"}
            )
            if (
                str(montage_payload.get("task_id", "")) != task_id
                or str(montage_payload.get("status", ""))
                not in allowed_montage_statuses
            ):
                raise ValueError("确认编辑后的混剪计划状态与操作类型不一致")
            _json_object(montage_payload)

        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            if connection is None:
                conn.execute("BEGIN IMMEDIATE")
            task = self._require_creation_task(conn, workspace_id, task_id)
            proposal_row = conn.execute(
                """
                SELECT * FROM edit_proposals
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, proposal_id),
            ).fetchone()
            if proposal_row is None:
                raise KeyError(proposal_id)
            proposal = self._edit_proposal_from_row(proposal_row)
            if proposal.status != "proposed":
                raise ValueError("只能原子确认 proposed 编辑提案")
            base_row = conn.execute(
                """
                SELECT sequence FROM timeline_versions
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, operation.base_version_id),
            ).fetchone()
            if base_row is None:
                raise ValueError("编辑操作基础版本不属于当前工作区与任务")
            if task.current_timeline_version_id != operation.base_version_id:
                raise ValueError("编辑操作基础版本已不是当前任务版本")
            if verify_sources is not None:
                verify_sources(conn)
            if result_version.sequence != int(base_row["sequence"]) + 1:
                raise ValueError("结果时间线版本序号必须连续递增")
            conflicts = (
                conn.execute(
                    "SELECT 1 FROM edit_operations WHERE id = ? OR (workspace_id = ? AND task_id = ? AND proposal_id = ?)",
                    (operation.id, workspace_id, task_id, proposal_id),
                ).fetchone(),
                conn.execute(
                    "SELECT 1 FROM timeline_versions WHERE id = ? OR (workspace_id = ? AND task_id = ? AND sequence = ?)",
                    (
                        result_version.id,
                        workspace_id,
                        task_id,
                        result_version.sequence,
                    ),
                ).fetchone(),
            )
            if any(row is not None for row in conflicts):
                raise ValueError("编辑操作或结果版本已存在，不能重复确认")
            if montage_payload is not None:
                montage_row = conn.execute(
                    """
                    SELECT id FROM creation_task_montage_plans
                    WHERE workspace_id = ? AND task_id = ?
                    """,
                    (workspace_id, task_id),
                ).fetchone()
                if montage_row is None or str(montage_row["id"]) != montage_plan_id:
                    raise ValueError("当前任务不存在同源混剪计划")
            if narration_segments is not None:
                script_ids = {segment.script_id for segment in narration_segments}
                if len(script_ids) != 1:
                    raise ValueError("原子确认只能保存当前已确认文案的分段")
                narration_script_id = next(iter(script_ids))
                self._upsert_narration_segments(
                    conn,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    script_id=narration_script_id,
                    segments=narration_segments,
                )
                if (
                    narration_artifact is not None
                    and narration_artifact.script_id != narration_script_id
                ):
                    raise ValueError("整轨配音产物与分段文案谱系不一致")

            now = utc_now()
            conn.execute(
                """
                INSERT INTO edit_operations
                    (id, workspace_id, task_id, proposal_id, operation_type,
                     payload_json, base_version_id, result_version_id, status,
                     failure_reason, created_at, applied_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation.id,
                    workspace_id,
                    task_id,
                    proposal_id,
                    operation.operation_type,
                    _json_object(operation.payload),
                    operation.base_version_id,
                    operation.result_version_id,
                    operation.status,
                    operation.failure_reason,
                    operation.created_at.isoformat(),
                    operation.applied_at.isoformat() if operation.applied_at else None,
                ),
            )
            conn.execute(
                """
                INSERT INTO timeline_versions
                    (id, workspace_id, task_id, parent_version_id, sequence,
                     snapshot_json, created_by_operation_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_version.id,
                    workspace_id,
                    task_id,
                    result_version.parent_version_id,
                    result_version.sequence,
                    _json_object(result_version.snapshot),
                    result_version.created_by_operation_id,
                    result_version.created_at.isoformat(),
                ),
            )
            for track in subtitle_tracks or []:
                conn.execute(
                    """
                    INSERT INTO subtitle_tracks
                        (id, workspace_id, task_id, timeline_version_id, language,
                         payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        track.id,
                        workspace_id,
                        task_id,
                        result_version.id,
                        track.language,
                        _json_payload(track),
                        track.created_at.isoformat(),
                        track.updated_at.isoformat(),
                    ),
                )
            if cover_asset is not None:
                conn.execute(
                    """
                    INSERT INTO cover_assets
                        (id, workspace_id, task_id, timeline_version_id,
                         payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cover_asset.id,
                        workspace_id,
                        task_id,
                        result_version.id,
                        _json_payload(cover_asset),
                        cover_asset.created_at.isoformat(),
                        cover_asset.updated_at.isoformat(),
                    ),
                )
            conn.execute(
                """
                UPDATE edit_proposals SET status = 'confirmed', updated_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                  AND status = 'proposed'
                """,
                (now.isoformat(), workspace_id, task_id, proposal_id),
            )
            if montage_payload is not None:
                saved_montage = {**montage_payload, "updated_at": now.isoformat()}
                conn.execute(
                    """
                    UPDATE creation_task_montage_plans
                    SET payload = ?, updated_at = ?
                    WHERE workspace_id = ? AND task_id = ? AND id = ?
                    """,
                    (
                        json.dumps(saved_montage, ensure_ascii=False),
                        now.isoformat(),
                        workspace_id,
                        task_id,
                        montage_plan_id,
                    ),
                )
                task.montage_plan_id = montage_plan_id
                restored_rendered_output = bool(
                    operation.operation_type in {"UndoTimelineVersion", "RedoTimelineVersion"}
                    and montage_payload.get("status") == "rendered"
                    and montage_payload.get("output_path")
                )
                task.output_ready = restored_rendered_output
                task.stage = "review" if restored_rendered_output else "montage"
                task.stage_status = "waiting_for_user"
                task.status = "waiting_for_user"
                task.progress = 90 if restored_rendered_output else 75
                task.recovery_point = "review" if restored_rendered_output else "montage"
                task.stage_reason = None
                task.stage_impact = None
                task.stage_repair_action = None
                task.stage_retryable = None
            if narration_artifact is not None:
                conn.execute(
                    """
                    INSERT INTO narration_artifacts
                        (id, workspace_id, cache_key, payload, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(workspace_id, cache_key) DO UPDATE SET
                        id = excluded.id,
                        payload = excluded.payload,
                        created_at = excluded.created_at
                    """,
                    (
                        narration_artifact.id,
                        narration_artifact.workspace_id,
                        narration_artifact.cache_key,
                        _json_payload(narration_artifact),
                        narration_artifact.created_at.isoformat(),
                    ),
                )
                task.narration_cache_key = narration_artifact.cache_key
            if narration_segments is not None and any(
                segment.status in {"pending", "generating"}
                for segment in narration_segments
            ):
                task.stage = "narration"
                task.stage_status = (
                    "running"
                    if any(
                        segment.status == "generating"
                        for segment in narration_segments
                    )
                    else "not_started"
                )
                task.status = "running"
                task.recovery_point = "narration"
            task.current_timeline_version_id = result_version.id
            task.updated_at = now
            conn.execute(
                """
                UPDATE creation_tasks SET payload = ?, updated_at = ?
                WHERE workspace_id = ? AND id = ?
                """,
                (
                    _json_payload(task),
                    now.isoformat(),
                    workspace_id,
                    task_id,
                ),
            )

        if connection is not None:
            return replace(proposal, status="confirmed", updated_at=now), operation, result_version, task
        return (
            self.get_edit_proposal(workspace_id, proposal_id),
            self.get_edit_operation(workspace_id, operation.id),
            self.get_timeline_version(workspace_id, result_version.id),
            self.get_creation_task(workspace_id, task_id),
        )

    def create_edit_operation(self, operation: EditOperation) -> EditOperation:
        _validate_edit_operation(operation)
        with self._connect() as conn:
            self._require_creation_task(
                conn, operation.workspace_id, operation.task_id
            )
            proposal_row = conn.execute(
                """
                SELECT status FROM edit_proposals
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (
                    operation.workspace_id,
                    operation.task_id,
                    operation.proposal_id,
                ),
            ).fetchone()
            if proposal_row is None or str(proposal_row["status"]) != "confirmed":
                raise ValueError("编辑操作必须关联同源且已确认的提案")
            self._require_operation_timeline_lineage(conn, operation)
            existing_row = conn.execute(
                "SELECT * FROM edit_operations WHERE id = ?",
                (operation.id,),
            ).fetchone()
            if existing_row is None:
                existing_row = conn.execute(
                    """
                    SELECT * FROM edit_operations
                    WHERE workspace_id = ? AND task_id = ? AND proposal_id = ?
                    """,
                    (
                        operation.workspace_id,
                        operation.task_id,
                        operation.proposal_id,
                    ),
                ).fetchone()
            if existing_row is not None:
                persisted = self._edit_operation_from_row(existing_row)
                if persisted != operation:
                    raise ValueError("编辑操作 ID 或提案已存在，不能覆盖")
                return persisted
            conn.execute(
                """
                INSERT INTO edit_operations
                    (id, workspace_id, task_id, proposal_id, operation_type,
                     payload_json, base_version_id, result_version_id, status,
                     failure_reason, created_at, applied_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation.id,
                    operation.workspace_id,
                    operation.task_id,
                    operation.proposal_id,
                    operation.operation_type,
                    _json_object(operation.payload),
                    operation.base_version_id,
                    operation.result_version_id,
                    operation.status,
                    operation.failure_reason,
                    operation.created_at.isoformat(),
                    operation.applied_at.isoformat() if operation.applied_at else None,
                ),
            )
        return self.get_edit_operation(operation.workspace_id, operation.id)

    def finish_edit_operation(
        self,
        workspace_id: str,
        task_id: str,
        operation_id: str,
        *,
        status: Literal["applied", "failed"],
        result_version_id: str | None = None,
        failure_reason: str | None = None,
        applied_at: datetime | None = None,
    ) -> EditOperation:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                """
                SELECT * FROM edit_operations
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                """,
                (workspace_id, task_id, operation_id),
            ).fetchone()
            if row is None:
                raise KeyError(operation_id)
            current = self._edit_operation_from_row(row)
            if current.status != "running":
                raise ValueError("只有 running 编辑操作可以进入终态")
            terminal = replace(
                current,
                status=status,
                result_version_id=result_version_id,
                failure_reason=failure_reason,
                applied_at=applied_at,
            )
            _validate_edit_operation(terminal)
            self._require_operation_timeline_lineage(conn, terminal)
            changed = conn.execute(
                """
                UPDATE edit_operations
                SET result_version_id = ?, status = ?, failure_reason = ?, applied_at = ?
                WHERE workspace_id = ? AND task_id = ? AND id = ?
                  AND status = 'running'
                """,
                (
                    terminal.result_version_id,
                    terminal.status,
                    terminal.failure_reason,
                    terminal.applied_at.isoformat() if terminal.applied_at else None,
                    workspace_id,
                    task_id,
                    operation_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("编辑操作已被其他操作结束")
        return self.get_edit_operation(workspace_id, operation_id)

    def get_edit_operation(
        self, workspace_id: str, operation_id: str
    ) -> EditOperation:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM edit_operations WHERE workspace_id = ? AND id = ?",
                (workspace_id, operation_id),
            ).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return self._edit_operation_from_row(row)

    def list_edit_operations(
        self, workspace_id: str, task_id: str
    ) -> list[EditOperation]:
        with self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            rows = conn.execute(
                """
                SELECT * FROM edit_operations
                WHERE workspace_id = ? AND task_id = ? ORDER BY created_at ASC, id ASC
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [self._edit_operation_from_row(row) for row in rows]

    @staticmethod
    def _require_operation_timeline_lineage(
        conn: sqlite3.Connection, operation: EditOperation
    ) -> None:
        base_row = conn.execute(
            """
            SELECT * FROM timeline_versions
            WHERE workspace_id = ? AND task_id = ? AND id = ?
            """,
            (
                operation.workspace_id,
                operation.task_id,
                operation.base_version_id,
            ),
        ).fetchone()
        if base_row is None:
            raise ValueError("编辑操作基础版本不属于当前工作区与任务")
        if operation.status != "applied":
            return
        result_row = conn.execute(
            """
            SELECT * FROM timeline_versions
            WHERE workspace_id = ? AND task_id = ? AND id = ?
            """,
            (
                operation.workspace_id,
                operation.task_id,
                operation.result_version_id,
            ),
        ).fetchone()
        if result_row is None:
            raise ValueError("已应用编辑操作的结果版本不属于当前任务")
        result = ProductionRepository._timeline_version_from_row(result_row)
        if (
            result.parent_version_id != operation.base_version_id
            or result.created_by_operation_id != operation.id
        ):
            raise ValueError("编辑操作与时间线版本谱系不一致")

    @staticmethod
    def _require_creation_task(
        conn: sqlite3.Connection, workspace_id: str, task_id: str
    ) -> CreationTask:
        row = conn.execute(
            "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
            (workspace_id, task_id),
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return ProductionRepository._creation_task_from_payload(
            json.loads(str(row["payload"]))
        )

    def _upsert_narration_segments(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        task_id: str,
        script_id: str,
        segments: list[NarrationSegment],
    ) -> None:
        task = self._require_creation_task(conn, workspace_id, task_id)
        if task.confirmed_script_id != script_id:
            raise ValueError("配音分段必须关联当前任务已确认的文案")
        script_row = conn.execute(
            """
            SELECT payload FROM creation_task_scripts
            WHERE workspace_id = ? AND task_id = ? AND id = ?
            """,
            (workspace_id, task_id, script_id),
        ).fetchone()
        if script_row is None:
            raise ValueError("配音分段关联的文案不属于当前工作区与任务")
        script = self._creation_task_script_from_payload(
            json.loads(str(script_row["payload"]))
        )
        if script.selection_status != "confirmed":
            raise ValueError("配音分段只能关联已确认文案")
        script_digest = self.confirmed_creation_task_script_digest(
            workspace_id, task_id, script_id,
        )
        sections = {section.id: section for section in script.sections}
        supplied_section_ids = [segment.source_section_id for segment in segments]
        if (
            not sections
            or len(supplied_section_ids) != len(set(supplied_section_ids))
            or set(supplied_section_ids) != set(sections)
        ):
            raise ValueError("必须为已确认文案的每个段落保存唯一配音分段")

        existing_rows = conn.execute(
            """
            SELECT * FROM narration_segments
            WHERE workspace_id = ? AND task_id = ? AND script_id = ?
            """,
            (workspace_id, task_id, script_id),
        ).fetchall()
        if existing_rows and len(existing_rows) != len(sections):
            raise ValueError("已持久化的配音分段集不完整，不能覆盖")
        existing_by_section = {
            str(row["source_section_id"]): self._narration_segment_from_row(row)
            for row in existing_rows
        }

        for segment in segments:
            _validate_narration_segment(segment)
            if (
                segment.workspace_id != workspace_id
                or segment.task_id != task_id
                or segment.script_id != script_id
            ):
                raise ValueError("配音分段与当前工作区、任务或文案谱系不一致")
            if segment.text != sections[segment.source_section_id].content:
                raise ValueError("配音分段文字必须与已确认文案段落一致")
            if segment.source_kind == "recorded" and segment.source_script_digest != script_digest:
                raise ValueError("电脑录音必须绑定当前确认文案内容")
            identity_row = conn.execute(
                "SELECT * FROM narration_segments WHERE id = ?",
                (segment.id,),
            ).fetchone()
            existing = existing_by_section.get(segment.source_section_id)
            if identity_row is not None and (
                existing is None or str(identity_row["id"]) != existing.id
            ):
                raise ValueError("配音分段 ID 已属于其他段落，不能覆盖")
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO narration_segments
                        (id, workspace_id, task_id, script_id, source_section_id,
                         text, audio_reference, duration_seconds, sample_rate,
                         model_id, voice_profile_id, voice_version, language,
                         pronunciation_version, status, created_at, updated_at,
                         source_kind, source_artifact_ref, source_sha256,
                         source_script_digest)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _narration_segment_values(segment),
                )
                continue
            if existing.id != segment.id or existing.created_at != segment.created_at:
                raise ValueError("配音分段稳定 ID 或创建时间不能覆盖")
            if segment.updated_at < existing.updated_at:
                raise ValueError("不能用过期状态覆盖配音分段")
            if segment == existing:
                continue
            conn.execute(
                """
                UPDATE narration_segments
                SET text = ?, audio_reference = ?, duration_seconds = ?,
                    sample_rate = ?, model_id = ?, voice_profile_id = ?,
                    voice_version = ?, language = ?, pronunciation_version = ?,
                    status = ?, updated_at = ?, source_kind = ?,
                    source_artifact_ref = ?, source_sha256 = ?, source_script_digest = ?
                WHERE workspace_id = ? AND task_id = ? AND script_id = ?
                  AND source_section_id = ? AND id = ?
                """,
                (
                    segment.text,
                    segment.audio_reference,
                    segment.duration_seconds,
                    segment.sample_rate,
                    segment.model_id,
                    segment.voice_profile_id,
                    segment.voice_version,
                    segment.language,
                    segment.pronunciation_version,
                    segment.status,
                    segment.updated_at.isoformat(),
                    segment.source_kind,
                    segment.source_artifact_ref,
                    segment.source_sha256,
                    segment.source_script_digest,
                    workspace_id,
                    task_id,
                    script_id,
                    segment.source_section_id,
                    segment.id,
                ),
            )

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
        self, workspace_id: str, task_id: str, candidate_id: str, *, title: str | None = None
    ) -> tuple[CreationTask, TopicCandidate]:
        if title is not None:
            title = title.strip()
            if not 1 <= len(title) <= 80:
                raise ValueError("选题标题需要填写 1–80 个字符")
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
                if title is not None and title != candidate.title:
                    raise ValueError("选题已经确认，不能通过重复确认改写标题")
                return task, candidate
            if task.selected_topic_id is not None:
                raise ValueError("当前创作任务已经确认了另一个选题")
            if task.stage != "topic" or task.stage_status != "waiting_for_user":
                raise ValueError("当前创作任务不在等待确认选题状态")
            now = utc_now()
            candidate.selection_status = "confirmed"
            if title is not None:
                candidate.title = title
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
        if run.run_kind != "generation" or run.source_reply_reference is not None:
            raise ValueError("对话回复移交不能伪装成模型生成运行")
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
            duration_repair = task.needs_script_duration_repair and task.can_revise_script
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

    def transfer_completed_assistant_reply_to_script(
        self,
        workspace_id: str,
        task_id: str,
        *,
        request_id: str,
        reply_reference: dict[str, str],
        plan_conversion_request_id: str | None = None,
        reply_text: str,
        expected_plan_id: str,
        expected_plan_updated_at: datetime | str,
        expected_topic_id: str,
        expected_task_updated_at: datetime | str,
        input_references: list[dict[str, object]],
        script_id: str,
        run_id: str,
        title: str,
        language: Literal["zh-CN", "en-US", "zh-CN/en-US"],
        sections: list[CreationTaskScriptSection],
        replace_existing_script_id: str | None = None,
        expected_script_updated_at: datetime | str | None = None,
        verified_owner_user_id: str | None = None,
    ) -> tuple[CreationTask, ScriptGenerationRun, CreationTaskScript]:
        """Persist an explicit reply-to-draft transfer against current task facts."""
        reference = _validated_assistant_reply_reference(reply_reference)
        if not request_id.strip() or not reply_text.strip() or not title.strip() or not sections:
            raise ValueError("移交文案需要请求身份、回复正文、标题和段落")
        if hashlib.sha256(reply_text.encode("utf-8")).hexdigest() != reference["response_digest"]:
            raise ValueError("对话回复正文已变化，请重新读取当前回复")
        if any(
            not section.id.strip() or not section.title.strip() or not section.content.strip()
            for section in sections
        ):
            raise ValueError("移交文案段落必须包含标题和正文")
        if "".join(section.content for section in sections) != reply_text:
            raise ValueError("移交草稿必须保留当前对话回复正文")
        references_by_id = _assistant_reply_input_references(input_references)
        evidence_ids = {
            reference_id
            for section in sections
            for reference_id in section.evidence_reference_ids
        }
        if not evidence_ids.issubset(references_by_id):
            raise ValueError("移交文案引用了当前任务输入之外的资料")

        request_payload: dict[str, object] = {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "reply_reference": reference,
            "plan_conversion_request_id": plan_conversion_request_id,
            "expected_plan_id": expected_plan_id,
            "expected_plan_updated_at": _revision_value(expected_plan_updated_at),
            "expected_topic_id": expected_topic_id,
            "expected_task_updated_at": _revision_value(expected_task_updated_at),
            "input_references": input_references,
            "script_id": script_id,
            "run_id": run_id,
            "title": title,
            "language": language,
            "sections": [asdict(section) for section in sections],
            "replace_existing_script_id": replace_existing_script_id,
            "expected_script_updated_at": (
                _revision_value(expected_script_updated_at)
                if expected_script_updated_at is not None else None
            ),
            "owner_user_id": content_owner(verified_owner_user_id),
        }
        request_payload.pop("script_id")
        request_payload.pop("run_id")
        # Revision tokens fence a new write, but a retry may observe revisions
        # advanced by that same successful write. Keep them out of the stable
        # request identity so the persisted result can be replayed after readback.
        fingerprint_payload = {
            key: value for key, value in request_payload.items()
            if key not in {"expected_plan_updated_at", "expected_task_updated_at", "expected_script_updated_at"}
        }
        request_fingerprint = hashlib.sha256(
            _json_object(fingerprint_payload).encode("utf-8")
        ).hexdigest()
        event_id = _assistant_reply_request_event_id(
            "assistant_reply_script_transferred", workspace_id, request_id,
        )
        replay_ids: tuple[str, str] | None = None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT * FROM task_events WHERE id = ?", (event_id,),
            ).fetchone()
            if prior is not None:
                event = self._task_event_from_row(prior)
                if (
                    event.event_type != "assistant_reply_script_transferred"
                    or event.workspace_id != workspace_id
                    or event.task_id != task_id
                    or event.payload.get("request_fingerprint") != request_fingerprint
                ):
                    raise ValueError("回复移交请求身份已用于不同的任务或输入")
                replay_ids = (str(event.payload["run_id"]), str(event.payload["script_id"]))
            else:
                task = self._require_creation_task(conn, workspace_id, task_id)
                if task.plan_id != expected_plan_id:
                    raise ValueError("回复移交的创作方案已变化")
                plan_row = conn.execute(
                    "SELECT payload FROM creation_plans WHERE workspace_id = ? AND id = ?",
                    (workspace_id, task.plan_id),
                ).fetchone()
                if plan_row is None:
                    raise KeyError(task.plan_id)
                plan = self._creation_plan_from_payload(json.loads(str(plan_row["payload"])))
                if _revision_value(plan.updated_at) != _revision_value(expected_plan_updated_at):
                    raise ValueError("创作方案版本已变化，请重新读取后移交")
                if task.selected_topic_id != expected_topic_id:
                    raise ValueError("回复移交的已确认选题已变化")
                if task.confirmed_script_id is not None:
                    raise ValueError("已确认文案不能被对话回复移交覆盖")
                topic_row = conn.execute(
                    "SELECT payload FROM topic_candidates WHERE workspace_id = ? AND task_id = ? AND id = ?",
                    (workspace_id, task_id, expected_topic_id),
                ).fetchone()
                if topic_row is None:
                    raise ValueError("回复移交选题不属于当前任务")
                topic = self._topic_candidate_from_payload(json.loads(str(topic_row["payload"])))
                if topic.selection_status != "confirmed":
                    raise ValueError("回复移交前必须确认当前选题")
                if task.stage != "script" or task.stage_status not in {"not_started", "waiting_for_user"}:
                    raise ValueError("当前创作任务不在可移交的文案阶段")
                if _revision_value(task.updated_at) != _revision_value(expected_task_updated_at):
                    raise ValueError("创作任务版本已变化，请重新读取后移交")
                if plan_conversion_request_id is not None:
                    self._require_assistant_reply_plan_event(
                        conn,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        plan_id=plan.id,
                        request_id=plan_conversion_request_id,
                        reply_reference=reference,
                        reply_owner_user_id=content_owner(verified_owner_user_id),
                    )
                self._validate_assistant_reply_inputs(plan, references_by_id)

                current_scripts = conn.execute(
                    "SELECT payload FROM creation_task_scripts WHERE workspace_id = ? AND task_id = ? ORDER BY updated_at DESC, id DESC",
                    (workspace_id, task_id),
                ).fetchall()
                latest = (
                    self._creation_task_script_from_payload(json.loads(str(current_scripts[0]["payload"])))
                    if current_scripts else None
                )
                if latest is not None:
                    if (
                        replace_existing_script_id != latest.id
                        or expected_script_updated_at is None
                    ):
                        raise ValueError("当前已有文案草稿，请明确选择替换")
                    if latest.selection_status != "draft":
                        raise ValueError("已有文案不是可替换的待确认草稿")
                    if _revision_value(latest.updated_at) != _revision_value(expected_script_updated_at):
                        raise ValueError("文案草稿已变化，请重新读取后确认替换")
                    script = latest
                    script_id = latest.id
                    script.run_id = run_id
                    script.topic_id = expected_topic_id
                    script.title = title.strip()
                    script.language = language
                    script.sections = sections
                    script.selection_status = "draft"
                    script.confirmed_at = None
                    script.source_reply_reference = reference
                else:
                    if replace_existing_script_id is not None or expected_script_updated_at is not None:
                        raise ValueError("没有当前草稿可按指定修订替换")
                    script = CreationTaskScript(
                        id=script_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        run_id=run_id,
                        topic_id=expected_topic_id,
                        title=title.strip(),
                        language=language,
                        sections=sections,
                        source_reply_reference=reference,
                    )

                if task.stage_status == "not_started":
                    task.stage_status = "waiting_for_user"
                    task.status = "waiting_for_user"
                    task.progress = max(task.progress, 35)
                now = utc_now()
                run = ScriptGenerationRun(
                    id=run_id,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    plan_id=plan.id,
                    topic_id=topic.id,
                    status="succeeded",
                    route_snapshot={"operation": "explicit_assistant_reply_transfer", "provider": "none"},
                    prompt_schema_version="assistant-reply-transfer/v1",
                    input_references=list(input_references),
                    started_at=now,
                    finished_at=now,
                    run_kind="assistant_reply_transfer",
                    source_reply_reference=reference,
                )
                script.updated_at = now
                task.updated_at = now
                conn.execute(
                    "INSERT INTO script_generation_runs (id, workspace_id, task_id, plan_id, topic_id, payload, started_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (run.id, workspace_id, task_id, plan.id, topic.id, _json_payload(run), now.isoformat(), now.isoformat()),
                )
                if latest is None:
                    conn.execute(
                        "INSERT INTO creation_task_scripts (id, workspace_id, task_id, run_id, payload, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (script.id, workspace_id, task_id, run.id, _json_payload(script), now.isoformat()),
                    )
                else:
                    conn.execute(
                        "UPDATE creation_task_scripts SET run_id = ?, payload = ?, updated_at = ? WHERE workspace_id = ? AND task_id = ? AND id = ?",
                        (run.id, _json_payload(script), now.isoformat(), workspace_id, task_id, script.id),
                    )
                conn.execute(
                    "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
                    (_json_payload(task), now.isoformat(), workspace_id, task_id),
                )
                self.append_task_event(
                    TaskEvent(
                        id=event_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        job_id=None,
                        event_type="assistant_reply_script_transferred",
                        payload={
                            "request_id": request_id,
                            "request_fingerprint": request_fingerprint,
                            "reply_reference": reference,
                            "plan_conversion_request_id": plan_conversion_request_id,
                            "plan_id": plan.id,
                            "topic_id": topic.id,
                            "task_id": task_id,
                            "run_id": run.id,
                            "script_id": script.id,
                            "expected_task_updated_at": _revision_value(expected_task_updated_at),
                            "expected_script_updated_at": (
                                _revision_value(expected_script_updated_at)
                                if expected_script_updated_at is not None else None
                            ),
                        },
                    ),
                    connection=conn,
                )
        if replay_ids is not None:
            return (
                self.get_creation_task(workspace_id, task_id),
                self.get_script_generation_run(workspace_id, replay_ids[0]),
                self.get_creation_task_script(workspace_id, replay_ids[1]),
            )
        return (
            self.get_creation_task(workspace_id, task_id),
            self.get_script_generation_run(workspace_id, run_id),
            self.get_creation_task_script(workspace_id, script_id),
        )

    def _require_assistant_reply_plan_event(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        task_id: str,
        plan_id: str,
        request_id: str,
        reply_reference: dict[str, str],
        reply_owner_user_id: str | None,
    ) -> None:
        event_id = _assistant_reply_request_event_id(
            "assistant_reply_plan_confirmed", workspace_id, request_id,
        )
        row = conn.execute(
            "SELECT payload_json FROM task_events WHERE workspace_id = ? AND task_id = ? AND id = ? AND event_type = 'assistant_reply_plan_confirmed'",
            (workspace_id, task_id, event_id),
        ).fetchone()
        payload = _json_object_from_text(str(row["payload_json"])) if row is not None else {}
        if not (
            payload.get("plan_id") == plan_id
            and payload.get("reply_reference") == reply_reference
            and payload.get("reply_owner_user_id") == reply_owner_user_id
        ):
            raise ValueError("当前方案没有绑定这条已完成对话回复")

    @staticmethod
    def _validate_assistant_reply_inputs(
        plan: CreationPlan,
        input_references: dict[str, dict[str, object]],
    ) -> None:
        expected: dict[str, dict[str, str]] = {}
        for item in plan.knowledge_references:
            reference_id = f"knowledge:{item.id}:{item.version}"
            expected[reference_id] = {
                "kind": "knowledge", "object_id": item.id,
                "version_or_identity": item.version, "title": item.title,
            }
        for item in plan.source_references:
            reference_id = f"source:{item.id}"
            expected[reference_id] = {
                "kind": "source", "object_id": item.id,
                "version_or_identity": item.identity, "title": item.title,
            }
        for reference_id, item in input_references.items():
            if not reference_id or item != expected.get(reference_id):
                raise ValueError("文案输入来源与当前方案的固定版本不一致")

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
            if run.run_kind != "generation":
                raise ValueError("回复移交记录不能作为模型生成运行完成")
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
        provider_failed: bool = True,
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
                    "availability": "failed" if provider_failed else "succeeded",
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

    def confirmed_creation_task_script_digest(
        self, workspace_id: str, task_id: str, script_id: str | None = None,
    ) -> str | None:
        task = self.get_creation_task(workspace_id, task_id)
        current_script_id = script_id or task.confirmed_script_id
        if not current_script_id or task.confirmed_script_id != current_script_id:
            return None
        try:
            script = self.get_creation_task_script(workspace_id, current_script_id)
        except KeyError:
            return None
        if script.task_id != task_id or script.selection_status != "confirmed" or not script.sections:
            return None
        manifest = {
            "script_id": script.id,
            "sections": [{"id": section.id, "content": section.content}
                         for section in script.sections],
        }
        return hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def update_assistant_script_draft(
        self,
        workspace_id: str,
        task_id: str,
        script_id: str,
        *,
        title: str,
        sections: list[CreationTaskScriptSection],
        expected_updated_at: datetime | str | None = None,
    ) -> CreationTaskScript:
        """Revise only the current unconfirmed assistant draft in its existing run."""
        if not title.strip() or not sections or any(
            not section.id.strip()
            or not section.title.strip()
            or not section.content.strip()
            for section in sections
        ):
            raise ValueError("文案草稿必须包含标题和正文")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute(
                "SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND task_id=? AND id=?",
                (workspace_id, task_id, script_id),
            ).fetchone()
            if row is None:
                raise KeyError(script_id)
            script = self._creation_task_script_from_payload(json.loads(str(row["payload"])))
            if expected_updated_at is None:
                raise ValueError("更新文案草稿必须提供当前 updated_at")
            if _revision_value(script.updated_at) != _revision_value(expected_updated_at):
                raise ValueError("文案草稿修订已变化，请重新读取后再保存")
            if (
                task.stage != "script"
                or task.stage_status != "waiting_for_user"
                or task.confirmed_script_id is not None
                or script.selection_status != "draft"
                or script.topic_id != task.selected_topic_id
            ):
                raise ValueError("当前创作任务不允许修改这份待确认文案")
            if script.source_reply_reference is None and any(
                not section.visual_prompt.strip() or not section.evidence_reference_ids
                for section in sections
            ):
                raise ValueError("文案草稿必须包含标题、正文、画面建议和来源证据")
            run_row = conn.execute(
                "SELECT payload FROM script_generation_runs WHERE workspace_id=? AND task_id=? "
                "AND json_extract(payload, '$.status')='succeeded' "
                "ORDER BY started_at DESC, id DESC LIMIT 1",
                (workspace_id, task_id),
            ).fetchone()
            if run_row is None:
                raise ValueError("这份文案没有已完成的生成记录")
            run = self._script_generation_run_from_payload(json.loads(str(run_row["payload"])))
            if script.run_id != run.id or run.topic_id != task.selected_topic_id or run.plan_id != task.plan_id:
                raise ValueError("这份文案不是当前任务的最新草稿")
            allowed_references = {
                str(item["reference_id"])
                for item in run.input_references
                if isinstance(item, dict) and item.get("reference_id")
            }
            used_references = {
                reference_id for section in sections for reference_id in section.evidence_reference_ids
            }
            if not used_references.issubset(allowed_references):
                raise ValueError("文案引用了本次模型输入之外的资料")
            script.title = title.strip()
            script.sections = sections
            script.updated_at = utc_now()
            conn.execute(
                "UPDATE creation_task_scripts SET payload=?, updated_at=? WHERE workspace_id=? AND task_id=? AND id=?",
                (_json_payload(script), script.updated_at.isoformat(), workspace_id, task_id, script_id),
            )
        return script

    def save_script_library_entry(
        self, workspace_id: str, *, source_script_id: str, name: str, category: str,
    ) -> ScriptLibraryEntry:
        if not name.strip():
            raise ValueError("请为保存的文案命名")
        script = self.get_creation_task_script(workspace_id, source_script_id)
        if script.selection_status != "confirmed":
            raise ValueError("只能保存已确认的文案")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_creation_task(conn, workspace_id, script.task_id)
            previous = conn.execute(
                "SELECT payload FROM script_library_entries WHERE workspace_id=? AND source_task_id=? AND name=? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (workspace_id, script.task_id, name.strip()),
            ).fetchone()
            prior = self._script_library_entry_from_payload(json.loads(previous["payload"])) if previous else None
            entry = ScriptLibraryEntry(
                id=f"script_library_{uuid4().hex[:12]}", workspace_id=workspace_id,
                name=name.strip(), category=category.strip(), source_script_id=script.id,
                source_task_id=script.task_id, source_run_id=script.run_id, source_topic_id=script.topic_id,
                version=prior.version + 1 if prior else 1, supersedes_entry_id=prior.id if prior else None,
                title=script.title, language=script.language,
                sections=[ScriptLibrarySection(
                    id=section.id, title=section.title, content=section.content,
                    visual_prompt=section.visual_prompt, reference_ids=list(section.evidence_reference_ids),
                ) for section in script.sections],
            )
            conn.execute(
                "INSERT INTO script_library_entries (id, workspace_id, source_task_id, name, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (entry.id, workspace_id, script.task_id, entry.name, _json_payload(entry), entry.created_at.isoformat()),
            )
        return entry

    @staticmethod
    def _script_library_entry_from_payload(data: dict[str, object]) -> ScriptLibraryEntry:
        sections = data.pop("sections", [])
        return ScriptLibraryEntry(
            **_with_datetime(data, "created_at", "updated_at"),
            sections=[ScriptLibrarySection(**section) for section in sections],
        )

    def get_script_library_entry(self, workspace_id: str, entry_id: str) -> ScriptLibraryEntry:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM script_library_entries WHERE workspace_id=? AND id=?",
                (workspace_id, entry_id),
            ).fetchone()
        if row is None:
            raise KeyError(entry_id)
        return self._script_library_entry_from_payload(json.loads(row["payload"]))

    def list_script_library_entries(
        self, workspace_id: str, *, query: str = "", category: str = "",
    ) -> list[ScriptLibraryEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM script_library_entries WHERE workspace_id=? ORDER BY created_at DESC, id DESC",
                (workspace_id,),
            ).fetchall()
        entries = [self._script_library_entry_from_payload(json.loads(row["payload"])) for row in rows]
        keyword = query.strip().casefold()
        return [entry for entry in entries if (not category or entry.category == category)
                and (not keyword or keyword in " ".join(
                    [entry.name, entry.title, *(section.content for section in entry.sections)]
                ).casefold())]

    def reuse_script_library_entry(
        self, workspace_id: str, entry_id: str, *, plan: CreationPlan,
    ) -> tuple[CreationTask, CreationTaskScript]:
        entry = self.get_script_library_entry(workspace_id, entry_id)
        if plan.workspace_id != workspace_id or plan.language != entry.language:
            raise ValueError("复用方案必须属于当前工作区并保留文案语言")
        now = utc_now()
        task = CreationTask(
            id=f"task_{uuid4().hex[:12]}", workspace_id=workspace_id, plan_id=plan.id,
            name=plan.name, scenario="script_library_reuse", stage="script",
            stage_status="waiting_for_user", status="running", progress=20, recovery_point="script",
        )
        draft = CreationTaskScript(
            id=f"script_{uuid4().hex[:12]}", workspace_id=workspace_id, task_id=task.id,
            run_id="", topic_id="", title=entry.title, language=entry.language,
            sections=[CreationTaskScriptSection(
                id=f"section_{uuid4().hex[:12]}", title=section.title, content=section.content,
                visual_prompt=section.visual_prompt, evidence_reference_ids=list(section.reference_ids),
            ) for section in entry.sections],
            reused_from_entry_id=entry.id,
        )
        plan.creation_task_id = task.id
        # The caller's explicit new requirements confirmation creates this plan;
        # neither the original task confirmation nor a model generation run is copied.
        plan.confirmed_at = now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO creation_plans (id, workspace_id, payload, visibility, updated_at) VALUES (?, ?, ?, ?, ?)",
                (plan.id, workspace_id, _json_payload(plan), plan.visibility, plan.updated_at.isoformat()),
            )
            conn.execute(
                "INSERT INTO creation_tasks (id, workspace_id, plan_id, payload, visibility, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (task.id, workspace_id, plan.id, _json_payload(task), task.visibility, task.updated_at.isoformat()),
            )
            conn.execute(
                "INSERT INTO creation_task_scripts (id, workspace_id, task_id, run_id, payload, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (draft.id, workspace_id, task.id, "", _json_payload(draft), draft.updated_at.isoformat()),
            )
        return task, draft

    def _demote_other_confirmed_scripts(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        task_id: str,
        confirmed_script_id: str,
        updated_at: datetime,
    ) -> None:
        rows = conn.execute(
            """
            SELECT id, payload FROM creation_task_scripts
            WHERE workspace_id = ? AND task_id = ? AND id != ?
            """,
            (workspace_id, task_id, confirmed_script_id),
        ).fetchall()
        for row in rows:
            other = self._creation_task_script_from_payload(
                json.loads(str(row["payload"]))
            )
            if other.selection_status != "confirmed":
                continue
            other.selection_status = "draft"
            other.confirmed_at = None
            other.updated_at = updated_at
            conn.execute(
                "UPDATE creation_task_scripts SET payload = ?, updated_at = ? WHERE id = ?",
                (_json_payload(other), updated_at.isoformat(), other.id),
            )

    def confirm_creation_task_script(
        self,
        workspace_id: str,
        task_id: str,
        script_id: str,
        *,
        title: str,
        sections: list[CreationTaskScriptSection],
        expected_updated_at: datetime | str | None = None,
    ) -> tuple[CreationTask, CreationTaskScript]:
        if not title.strip() or not sections or any(
            not section.content.strip()
            or not section.visual_prompt.strip()
            or not section.evidence_reference_ids
            for section in sections
        ):
            raise ValueError("确认文案必须包含正文、画面建议和来源证据")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
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
            if expected_updated_at is None:
                raise ValueError("确认文案必须提供当前 updated_at")
            if _revision_value(script.updated_at) != _revision_value(expected_updated_at):
                raise ValueError("文案草稿修订已变化，请重新读取后再确认")
            confirmed_script_repair = (
                task.confirmed_script_id == script_id
                and script.selection_status == "confirmed"
                and task.can_revise_script
            )
            if (
                task.confirmed_script_id == script_id
                and script.selection_status == "confirmed"
                and not confirmed_script_repair
            ):
                self._demote_other_confirmed_scripts(
                    conn,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    confirmed_script_id=script_id,
                    updated_at=utc_now(),
                )
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
            self._demote_other_confirmed_scripts(
                conn,
                workspace_id=workspace_id,
                task_id=task_id,
                confirmed_script_id=script.id,
                updated_at=now,
            )
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
        inputs = self.resolve_narration_inputs(
            workspace_id=workspace_id,
            episode_id=task_id,
            voice_profile_id=voice_profile_id,
            voice_version=voice_version,
        )
        task = self.get_creation_task(workspace_id, task_id)
        self.profile_reference_input(inputs.voice, viewer_user_id=task.owner_user_id)
        return inputs

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
        self,
        artifact: NarrationArtifactRecord,
        segments: list[NarrationSegment] | None = None,
    ) -> tuple[CreationTask, NarrationArtifactRecord]:
        if artifact.task_id is None:
            raise ValueError("创作任务配音必须关联 task_id")
        if not Path(artifact.audio_path).is_file() or Path(artifact.audio_path).stat().st_size == 0:
            raise ValueError("声音克隆没有生成有效 WAV")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task_row = conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (artifact.workspace_id, artifact.task_id),
            ).fetchone()
            if task_row is None:
                raise KeyError(artifact.task_id)
            task = self._creation_task_from_payload(json.loads(str(task_row["payload"])))
            expected_statuses = {"not_started", "failed"} if artifact.source_kind == "recorded" else {"running"}
            if (
                task.stage != "narration"
                or task.stage_status not in expected_statuses
                or task.confirmed_script_id != artifact.script_id
            ):
                raise ValueError("配音产物与当前创作任务不一致")
            if artifact.source_kind == "recorded" and not self._recorded_narration_source_is_current(artifact):
                raise ValueError("采用的电脑录音不再是当前任务的有效来源")
            if artifact.source_kind == "recorded" and (
                segments is None or len(segments) != 1
                or segments[0].source_artifact_ref != artifact.source_artifact_ref
                or segments[0].source_sha256 != artifact.source_sha256
                or segments[0].source_script_digest != artifact.source_script_digest
            ):
                raise ValueError("录音产物与唯一采用的文案分段来源不一致")
            if segments is not None:
                self._upsert_narration_segments(
                    conn,
                    workspace_id=artifact.workspace_id,
                    task_id=artifact.task_id,
                    script_id=artifact.script_id,
                    segments=segments,
                )
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
        *, connection: sqlite3.Connection | None = None,
        expected_current_version_id: str | None | Literal[False] = False,
    ) -> tuple[CreationTask, dict[str, object]]:
        status = str(payload.get("status", ""))
        if status not in {"waiting_for_user", "ready", "rendering", "rendered", "failed"}:
            raise ValueError("混剪计划状态无效")
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.save_creation_task_montage_plan(workspace_id, task_id, plan_id, payload,
                    connection=conn, expected_current_version_id=expected_current_version_id)
            return self.get_creation_task(workspace_id, task_id), self.get_creation_task_montage_plan(workspace_id, task_id)
        conn = connection
        task_row = conn.execute(
            "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
            (workspace_id, task_id),
        ).fetchone()
        if task_row is None:
            raise KeyError(task_id)
        task = self._creation_task_from_payload(json.loads(str(task_row["payload"])))
        if (expected_current_version_id is not False
                and task.current_timeline_version_id != expected_current_version_id):
            raise ValueError("当前时间线已变化，不能覆盖新确认的剪辑计划")
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
        return task, payload

    def get_creation_task_montage_plan(
        self, workspace_id: str, task_id: str, *, connection: sqlite3.Connection | None = None
    ) -> dict[str, object]:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
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
        connection: sqlite3.Connection | None = None,
    ) -> CreationTask:
        if not all(value.strip() for value in (reason, impact, repair_action)):
            raise ValueError("阶段失败必须包含原因、影响和修复动作")
        if connection is None:
            with self._connect() as conn:
                self.fail_creation_task_stage(
                    workspace_id, task_id, stage=stage, reason=reason, impact=impact,
                    repair_action=repair_action, retryable=retryable, connection=conn,
                )
            return self.get_creation_task(workspace_id, task_id)
        row = connection.execute(
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
        connection.execute(
            "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
            (_json_payload(task), task.updated_at.isoformat(), workspace_id, task_id),
        )
        return task

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
        viewer_user_id: str | None = None,
        local_diagnostic: bool = False,
        topic_sources_configured: bool = False,
    ) -> dict[str, object]:
        profile = profile or self.get_profile(workspace_id)
        assets = self.list_assets(
            workspace_id, include_private=viewer_user_id is not None or local_diagnostic,
            viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic,
        )
        qualified = [
            (asset, [segment for segment in asset.segments
                     if material_segment_eligible(asset, segment, workspace_id)])
            for asset in assets
        ]
        usable_assets = [asset for asset, segments in qualified if segments]
        usable_segments = [
            segment
            for asset, segments in qualified
            for segment in segments
            if selectable_segment_usage_role(asset, segment) == "b_roll"
        ]
        blockers: list[dict[str, str]] = []
        versioned_voices = self.list_versioned_voice_profiles(workspace_id)
        usable_voice = None
        reference_issue = None
        reference_issue_code = "voice_reference_unavailable"
        for voice in versioned_voices:
            if not self.profile_readable(voice, viewer_user_id, local_diagnostic):
                continue
            try:
                self.profile_reference_input(voice, viewer_user_id=viewer_user_id,
                                             local_diagnostic=local_diagnostic)
            except (ValueError, PermissionError, KeyError) as exc:
                reference_issue = str(exc)
                reference_issue_code = "voice_reference_unavailable"
            else:
                if not voice.reference_audio_path or not voice.reference_text.strip():
                    reference_issue = "声音档案缺少工作区参考音频或对应文字；仍可先讨论需求或编写文案。"
                    reference_issue_code = "voice_reference_missing"
                    continue
                usable_voice = voice
                break
        if usable_voice is None:
            blockers.append(
                {
                    "code": reference_issue_code if reference_issue else "versioned_voice_authorization_missing",
                    "message": reference_issue or "尚未登记可用于配音的版本化声音；仍可先讨论需求或编写文案。",
                }
            )
        if profile.voice_model_status != "ready":
            blockers.append(
                {
                    "code": "voice_model_not_ready",
                    "message": profile.voice_model_message,
                }
            )
        if not topic_sources_configured:
            blockers.append(
                {
                    "code": "source_pool_empty",
                    "message": "尚未配置可尝试的选题来源；仍可先讨论需求或编写文案。",
                }
            )
        if not usable_segments:
            blockers.append(
                {
                    "code": "approved_materials_empty",
                    "message": "可用素材库中还没有明确可作补充画面的镜头。",
                }
            )
        return {
            "ready": not blockers,
            "blockers": blockers,
            "asset_count": len(assets),
            "approved_asset_count": len(usable_assets),
            "approved_segment_count": len(usable_segments),
            "total_duration_seconds": round(
                sum(asset.duration_seconds or 0 for asset in assets), 3
            ),
        }

    def _upsert_discovered_asset(
        self,
        workspace_id: str,
        path: Path,
        *,
        media_type: MaterialMediaType | None = None,
        source_url: str = "",
        owner_user_id: str | None = None,
    ) -> MaterialAsset:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.video_library_path.resolve())
        except ValueError as exc:
            raise ValueError("素材文件不在配置的素材库中") from exc
        with self._connect() as conn:
            row = self._material_assets._asset_payload_row_by_path(conn, workspace_id, str(resolved))
        existing = (
            self._asset_from_row(row)
            if row is not None
            else None
        )
        stat = resolved.stat()
        copy_uploader_user_id = None
        if existing is None:
            # 可信接收身份随这一文件修订的确认持久化；无身份外部文件不猜上传者。
            with self._connect() as conn:
                confirmation = conn.execute(
                    "SELECT copy_uploader_user_id FROM material_ingestion_files "
                    "WHERE workspace_id=? AND file_path=? AND confirmed_revision=?",
                    (workspace_id, str(resolved), json.dumps(self.material_copy_revision(stat), sort_keys=True)),
                ).fetchone()
            copy_uploader_user_id = confirmation["copy_uploader_user_id"] if confirmation is not None else None
        return self._material_assets.save_discovered_asset(
            existing, workspace_id, resolved, stat, media_type=media_type, source_url=source_url,
            owner_user_id=owner_user_id, copy_uploader_user_id=copy_uploader_user_id,
        )

    def material_link_target(self, workspace_id: str) -> Path:
        """Return the intake directory a direct link may write into, or refuse.

        Direct-link import reuses the existing binding and workspace checks; it
        never creates its own directory or its own authorization path.
        """
        state = self.material_ingestion_directory_state(workspace_id)
        if state["bound_to_another_workspace"]:
            raise ValueError("素材目录已关联其他工作区；当前工作区不能写入。")
        if state["configured_path_changed"]:
            raise ValueError("当前设置的素材目录与本工作区原绑定目录不同；请先处理原绑定。")
        if not state["bound_to_current_workspace"]:
            raise ValueError("请先把素材目录关联到当前工作区，再导入链接。")
        if state["status"] != "ready":
            raise ValueError("素材目录当前不可写；请检查磁盘挂载方式或目录权限。")
        return self.video_library_path

    def _asset_by_source_url(self, workspace_id: str, source_url: str) -> MaterialAsset | None:
        """Find the material already registered for one original direct link."""
        return self._material_assets.find_asset_by_source_url(workspace_id, source_url)

    def _material_file_intact(self, asset: MaterialAsset) -> bool:
        """原文件是否仍完整可读——大小相等只是其中一条。

        登记时非空，这里同样要求非空；还要是普通文件，并且真实格式仍能被本地
        工具读出来、且与登记时的类型一致。只做格式判定，不做内容分析。
        """
        path = Path(asset.path)
        try:
            if path.is_symlink() or not path.is_file():
                return False
            size = path.stat().st_size
        except OSError:
            return False
        if size == 0 or size != asset.size_bytes:
            return False
        try:
            return _probe_file_media_type(path) == asset.media_type
        except OSError:
            return False

    def existing_link_material(
        self, workspace_id: str, source_url: str, *,
        viewer_user_id: str | None = None, local_diagnostic: bool = False,
    ) -> MaterialAsset | None:
        """The material already registered for one original direct link.

        Returns it only while its own file is still there at the recorded size, so
        a repeat submission of the same URL can reuse both instead of downloading
        again over the top of it.
        """
        asset = self._asset_by_source_url(workspace_id, source_url)
        if asset is not None and not content_readable(
            user_id=viewer_user_id, local_diagnostic=local_diagnostic,
            visibility=asset.visibility, owner_user_id=asset.owner_user_id,
        ):
            raise KeyError("material asset not found")
        if asset is None or not self._material_file_intact(asset):
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
            resolved.relative_to(self.video_library_path.resolve())
        except ValueError as exc:
            raise ValueError("素材文件不在配置的素材库中") from exc
        if resolved.name.startswith("link-") and resolved.is_file():
            resolved.unlink(missing_ok=True)

    def _repoint_asset(self, asset: MaterialAsset) -> None:
        """Move an existing material record onto a restored file, keeping its id.

        `_save_asset` upserts on (workspace_id, file_path), so a record whose file
        changed path cannot go through it without colliding on its own id.
        """
        self._material_assets.repoint_asset(asset)

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
        recorded = self._asset_by_source_url(workspace_id, source_url)
        if recorded is not None:
            if not content_readable(
                user_id=verified_owner_user_id, local_diagnostic=local_diagnostic,
                visibility=recorded.visibility, owner_user_id=recorded.owner_user_id,
            ):
                raise KeyError("material asset not found")
            if self._material_file_intact(recorded):
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
            self._repoint_asset(recorded)
            return self._apply_media_analysis_status(recorded)
        asset = self._upsert_discovered_asset(
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
        return self._save_asset(asset)

    def _save_asset(self, asset: MaterialAsset, *, analysis_only: bool = False) -> MaterialAsset:
        return self._material_assets.save_asset(asset, analysis_only=analysis_only)

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
                    self._material_assets.write_visual_segment_feature(
                        conn,
                        asset.workspace_id,
                        asset.id,
                        segment.id,
                        provider.model_id,
                        fingerprints[segment.id],
                        json.dumps(embedding.vector),
                        embedding.sample_count,
                        utc_now().isoformat(),
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
                vector = self._visual_feature_for_segment(asset, segment, provider.model_id)
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
            rows = self._material_assets._visual_feature_cache_rows(
                conn, workspace_id, model_id,
            )
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

    _authorization_from_payload = staticmethod(_authorization_from_payload)

    def _profile_authorization_from_payload(self, conn, workspace_id, data):
        return self._profile_records._profile_authorization_from_payload(conn, workspace_id, data)

    def _person_profile_from_payload(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        data: dict[str, object],
    ) -> PersonProfile:
        return self._profile_records._person_profile_from_payload(conn, workspace_id, data)

    def _versioned_voice_profile_from_payload(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        data: dict[str, object],
    ) -> VersionedVoiceProfile:
        return self._profile_records._versioned_voice_profile_from_payload(conn, workspace_id, data)

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

    _asset_from_row = staticmethod(MaterialAssetStore._asset_from_row)

    _asset_from_payload = staticmethod(MaterialAssetStore._asset_from_payload)

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
        # Missing key identifies historical payloads only. Explicit None stays
        # unselected even if an old voice relationship exists.
        person_payload = data.pop("selected_person", None) if "selected_person" in data else (
            {"profile_id": voice_payload["person_profile_id"], "version": voice_payload["person_version"]}
            if isinstance(voice_payload, dict) and voice_payload.get("person_profile_id")
            and voice_payload.get("person_version") else None)
        pronunciation_payloads = data.pop("pronunciation_references", [])
        blocker_payloads = data.pop("blockers", [])
        material_match_payloads = data.pop("material_matches", [])
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
            selected_person=(CreationPlanPersonReference(**person_payload)
                             if isinstance(person_payload, dict) else None),
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
            material_matches=[
                MaterialSegmentMatch(**item)
                for item in material_match_payloads
                if isinstance(item, dict)
            ],
        )

    @staticmethod
    def _template_from_row(row: sqlite3.Row) -> VideoTemplate:
        """模板回读以列为准：存量模板的 visibility 只写在列上。"""
        data = json.loads(str(row["payload"]))
        if "visibility" in row.keys():
            data["visibility"] = str(row["visibility"])
        return VideoTemplate(**_with_datetime(data, "created_at"))

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> CreationTask:
        """任务回读以列为准：存量行的 visibility 只写在列上。"""
        data = json.loads(str(row["payload"]))
        if "visibility" in row.keys():
            data["visibility"] = str(row["visibility"])
        return ProductionRepository._creation_task_from_payload(data)

    @staticmethod
    def _creation_task_from_payload(data: dict[str, object]) -> CreationTask:
        return CreationTask(**_with_datetime(data, "created_at", "updated_at"))

    @staticmethod
    def _timeline_version_from_row(row: sqlite3.Row) -> TimelineVersion:
        return TimelineVersion(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            parent_version_id=(
                str(row["parent_version_id"])
                if row["parent_version_id"] is not None
                else None
            ),
            sequence=int(row["sequence"]),
            snapshot=_json_object_from_text(str(row["snapshot_json"])),
            created_by_operation_id=(
                str(row["created_by_operation_id"])
                if row["created_by_operation_id"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _job_run_from_row(row: sqlite3.Row) -> JobRun:
        return JobRun(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            kind=str(row["kind"]),
            idempotency_key=str(row["idempotency_key"]),
            input_digest=str(row["input_digest"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            progress=int(row["progress"]),
            attempt=int(row["attempt"]),
            output_refs=_string_list_from_text(str(row["output_refs_json"])),
            failure_reason=(
                str(row["failure_reason"])
                if row["failure_reason"] is not None
                else None
            ),
            impact=str(row["impact"]) if row["impact"] is not None else None,
            repair_action=(
                str(row["repair_action"])
                if row["repair_action"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            started_at=(
                datetime.fromisoformat(str(row["started_at"]))
                if row["started_at"] is not None
                else None
            ),
            finished_at=(
                datetime.fromisoformat(str(row["finished_at"]))
                if row["finished_at"] is not None
                else None
            ),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _artifact_record_from_row(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            kind=str(row["kind"]),
            logical_ref=str(row["logical_ref"]),
            sha256=str(row["sha256"]),
            size_bytes=int(row["size_bytes"]),
            producer_job_id=str(row["producer_job_id"]),
            input_refs=_string_list_from_text(str(row["input_refs_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _narration_segment_from_row(row: sqlite3.Row) -> NarrationSegment:
        return NarrationSegment(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            script_id=str(row["script_id"]),
            source_section_id=str(row["source_section_id"]),
            text=str(row["text"]),
            audio_reference=(
                str(row["audio_reference"])
                if row["audio_reference"] is not None
                else None
            ),
            duration_seconds=(
                float(row["duration_seconds"])
                if row["duration_seconds"] is not None
                else None
            ),
            sample_rate=(
                int(row["sample_rate"]) if row["sample_rate"] is not None else None
            ),
            model_id=str(row["model_id"]) if row["model_id"] is not None else None,
            voice_profile_id=(str(row["voice_profile_id"])
                              if row["voice_profile_id"] is not None else None),
            voice_version=str(row["voice_version"]) if row["voice_version"] is not None else None,
            language=str(row["language"]),
            pronunciation_version=str(row["pronunciation_version"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            source_kind=str(row["source_kind"]),  # type: ignore[arg-type]
            source_artifact_ref=(str(row["source_artifact_ref"])
                                 if row["source_artifact_ref"] is not None else None),
            source_sha256=(str(row["source_sha256"])
                           if row["source_sha256"] is not None else None),
            source_script_digest=(str(row["source_script_digest"])
                                  if row["source_script_digest"] is not None else None),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _person_render_from_row(row: sqlite3.Row) -> PersonRender:
        return PersonRender(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            person_profile_id=str(row["person_profile_id"]),
            person_version=str(row["person_version"]),
            voice_profile_id=str(row["voice_profile_id"]),
            voice_version=str(row["voice_version"]),
            narration_segment_id=str(row["narration_segment_id"]),
            visual_reference=str(row["visual_reference"]),
            model_id=str(row["model_id"]),
            motion=str(row["motion"]),
            expression=str(row["expression"]),
            cache_key=str(row["cache_key"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            artifact_ref=(
                str(row["artifact_ref"])
                if row["artifact_ref"] is not None
                else None
            ),
            output_path=(
                str(row["output_path"]) if row["output_path"] is not None else None
            ),
            duration_seconds=(
                float(row["duration_seconds"])
                if row["duration_seconds"] is not None
                else None
            ),
            alignment_evidence=(
                str(row["alignment_evidence"])
                if row["alignment_evidence"] is not None
                else None
            ),
            failure_reason=(
                str(row["failure_reason"])
                if row["failure_reason"] is not None
                else None
            ),
            impact=str(row["impact"]) if row["impact"] is not None else None,
            repair_action=(
                str(row["repair_action"])
                if row["repair_action"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _subtitle_track_from_row(row: sqlite3.Row) -> SubtitleTrack:
        payload = _json_object_from_text(str(row["payload_json"]))
        raw_cues = payload.pop("cues", [])
        return SubtitleTrack(
            **_with_datetime(payload, "created_at", "updated_at"),
            cues=[
                SubtitleCue(**cue)
                for cue in raw_cues
                if isinstance(cue, dict)
            ],
        )

    @staticmethod
    def _cover_asset_from_row(row: sqlite3.Row) -> CoverAsset:
        payload = _json_object_from_text(str(row["payload_json"]))
        return CoverAsset(**_with_datetime(payload, "created_at", "updated_at"))

    @staticmethod
    def _task_event_from_row(row: sqlite3.Row) -> TaskEvent:
        return TaskEvent(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            job_id=str(row["job_id"]) if row["job_id"] is not None else None,
            event_type=str(row["event_type"]),
            payload=_json_object_from_text(str(row["payload_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _transform_run_from_row(row: sqlite3.Row) -> TransformRun:
        return TransformRun(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            job_id=str(row["job_id"]),
            transform_type=str(row["transform_type"]),
            input_digest=str(row["input_digest"]),
            input_refs=_string_list_from_text(str(row["input_refs_json"])),
            output_refs=_string_list_from_text(str(row["output_refs_json"])),
            engine=str(row["engine"]),
            parameters=_json_object_from_text(str(row["parameters_json"])),
            status=str(row["status"]),  # type: ignore[arg-type]
            started_at=datetime.fromisoformat(str(row["started_at"])),
            finished_at=(
                datetime.fromisoformat(str(row["finished_at"]))
                if row["finished_at"] is not None
                else None
            ),
            failure_reason=(
                str(row["failure_reason"])
                if row["failure_reason"] is not None
                else None
            ),
            remediation=(
                str(row["remediation"])
                if row["remediation"] is not None
                else None
            ),
        )

    @staticmethod
    def _dependency_edge_from_row(row: sqlite3.Row) -> DependencyEdge:
        return DependencyEdge(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            upstream_ref=str(row["upstream_ref"]),
            downstream_ref=str(row["downstream_ref"]),
            created_by_transform_id=str(row["created_by_transform_id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _edit_intent_from_row(row: sqlite3.Row) -> EditIntent:
        return EditIntent(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            raw_command=str(row["raw_command"]),
            context_stage=str(row["context_stage"]),
            selected_object_refs=_string_list_from_text(
                str(row["selected_object_refs_json"])
            ),
            playhead_ms=(
                int(row["playhead_ms"])
                if row["playhead_ms"] is not None
                else None
            ),
            intent_type=str(row["intent_type"]),
            scope=_json_object_from_text(str(row["scope_json"])),
            constraints=_json_object_from_text(str(row["constraints_json"])),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _edit_proposal_from_row(row: sqlite3.Row) -> EditProposal:
        return EditProposal(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            intent_id=str(row["intent_id"]),
            summary=str(row["summary"]),
            target_refs=_string_list_from_text(str(row["target_refs_json"])),
            operation=_json_object_from_text(str(row["operation_json"])),
            invalidated_refs=_string_list_from_text(
                str(row["invalidated_refs_json"])
            ),
            preview=_json_object_from_text(str(row["preview_json"])),
            external_effects=_json_list_from_text(
                str(row["external_effects_json"])
            ),
            status=str(row["status"]),  # type: ignore[arg-type]
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _edit_operation_from_row(row: sqlite3.Row) -> EditOperation:
        return EditOperation(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            proposal_id=str(row["proposal_id"]),
            operation_type=str(row["operation_type"]),
            payload=_json_object_from_text(str(row["payload_json"])),
            base_version_id=str(row["base_version_id"]),
            result_version_id=(
                str(row["result_version_id"])
                if row["result_version_id"] is not None
                else None
            ),
            status=str(row["status"]),  # type: ignore[arg-type]
            failure_reason=(
                str(row["failure_reason"])
                if row["failure_reason"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            applied_at=(
                datetime.fromisoformat(str(row["applied_at"]))
                if row["applied_at"] is not None
                else None
            ),
        )

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


class MaterialLinkProvenanceSink:
    """Record a completed direct-link download on the existing material fact source.

    The imported file becomes a `material_assets` row of the current workspace —
    the same fact source, key and lookup the directory scan already uses. No
    parallel store is introduced.
    """

    def __init__(
        self,
        repository: ProductionRepository,
        workspace_id: str,
        verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> None:
        self._repository = repository
        self._workspace_id = workspace_id
        self._owner_user_id = verified_owner_user_id
        self._local_diagnostic = local_diagnostic

    def existing_link_import(self, url: str):
        asset = self._repository.existing_link_material(
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
        asset = self._repository.register_link_material(
            self._workspace_id,
            Path(record.file_path),
            media_type=record.media_type,
            source_url=record.url,
            verified_owner_user_id=self._owner_user_id,
            local_diagnostic=self._local_diagnostic,
        )
        return asset.id



def _local_adoption_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _local_adoption_repository(cls, database_path, media_root):
    # Maintenance deliberately bypasses __init__: even dry-run must not provision.
    repository = cls.__new__(cls)
    repository.database_path = Path(database_path).expanduser()
    repository.video_library_path = Path(media_root).expanduser()
    repository._material_assets = MaterialAssetStore(repository._connect)
    repository._initialize_profile_stores()
    return repository


def _local_adoption_database_identity(repository) -> dict:
    stat = repository.database_path.stat()
    return {"device": stat.st_dev, "inode": stat.st_ino}


def _local_adoption_target(repository, conn, member_id):
    member = conn.execute("SELECT member_id,name,role,created_at FROM local_members WHERE member_id=?",
                          (member_id,)).fetchone()
    if member is None or member["role"] != "owner":
        raise ValueError("local_adoption_existing_owner_required")
    binding = conn.execute("SELECT * FROM material_ingestion_binding WHERE id=1").fetchone()
    if (binding is None or binding["workspace_id"] != "local" or
            Path(binding["library_path"]).resolve() != repository.video_library_path.resolve()):
        raise ValueError("local_adoption_existing_local_root_required")
    return dict(member), dict(binding)


def _local_adoption_file_revision(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError("local_adoption_regular_source_required")
    stat = path.stat()
    if stat.st_size <= 0:
        raise ValueError("local_adoption_empty_source")
    return {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns, "inode": stat.st_ino, "device": stat.st_dev}


def _local_adoption_plan(repository, conn, *, member_id, operation_id, asset_ids,
                         voice_id, voice_version, new_voice_version, authorization_basis):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_id):
        raise ValueError("local_adoption_operation_id_invalid")
    if not authorization_basis.strip() or not asset_ids or len(asset_ids) != len(set(asset_ids)):
        raise ValueError("local_adoption_explicit_selection_and_consent_required")
    if not voice_id or not voice_version or not new_voice_version or voice_version == new_voice_version:
        raise ValueError("local_adoption_new_voice_version_required")
    member, binding = _local_adoption_target(repository, conn, member_id)
    root = repository.video_library_path.resolve()
    assets, references = [], {}
    originals = {"assets": [], "references": [], "member": member, "binding": binding}

    def source(reference, *, must_be_registered=False):
        path = repository.resolve_workspace_media_reference("local", reference)
        canonical = "workspace://" + path.relative_to(root).as_posix()
        if reference != canonical:
            raise ValueError("local_adoption_canonical_reference_required")
        row = conn.execute("SELECT * FROM workspace_media_references WHERE reference=?", (reference,)).fetchone()
        if row is not None and row["workspace_id"] != "local":
            raise ValueError("local_adoption_reference_other_workspace")
        if must_be_registered and row is None:
            raise ValueError("local_adoption_existing_voice_reference_required")
        # A differently encoded logical spelling cannot conceal another space's claim.
        for other in conn.execute("SELECT reference FROM workspace_media_references WHERE workspace_id!='local'"):
            if repository.resolve_workspace_media_reference("local", other["reference"]) == path:
                raise ValueError("local_adoption_reference_other_workspace")
        for other in repository._material_assets._asset_file_path_rows_outside_workspace(conn, "local"):
            if Path(other["file_path"]).resolve() == path:
                raise ValueError("local_adoption_source_other_workspace")
        if reference not in references:
            references[reference] = {"reference": reference,
                "before_sha256": _local_adoption_digest(dict(row)) if row else None}
            originals["references"].append(dict(row) if row else {"reference": reference, "absent": True})
        return path, _local_adoption_file_revision(path)

    for asset_id in sorted(asset_ids):
        row = repository._material_assets._asset_row_by_id(conn, "local", asset_id)
        if row is None:
            raise ValueError("local_adoption_selected_local_asset_missing")
        data = json.loads(row["payload"])
        if data.get("owner_user_id") is not None:
            raise ValueError("local_adoption_asset_already_owned")
        if data["id"] != asset_id or data["workspace_id"] != "local" or data["path"] != row["file_path"]:
            raise ValueError("local_adoption_asset_record_mismatch")
        path = Path(row["file_path"])
        if path != path.resolve():
            raise ValueError("local_adoption_canonical_path_required")
        try:
            reference = "workspace://" + path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("local_adoption_source_outside_root") from exc
        path, revision = source(reference)
        if (data["size_bytes"] != revision["size_bytes"] or
                abs(datetime.fromisoformat(data["modified_at"]).timestamp() - path.stat().st_mtime) > .00001):
            raise ValueError("local_adoption_asset_file_revision_mismatch")
        assets.append({"id": asset_id, "row_sha256": _local_adoption_digest(dict(row)),
            "payload_sha256": hashlib.sha256(row["payload"].encode()).hexdigest(),
            "visibility": row["visibility"], "updated_at": row["updated_at"],
            "reference": reference, "file_revision": revision,
            "target_owner_user_id": member_id, "target_visibility": "private"})
        originals["assets"].append(dict(row))

    voice_row = repository._profile_records._voice_version_row(conn, "local", voice_id, voice_version)
    latest = repository._profile_records._latest_voice_version_row(conn, "local", voice_id)
    if voice_row is None or latest["version"] != voice_version:
        raise ValueError("local_adoption_selected_current_voice_missing")
    if repository._profile_records._voice_version_existence_row(conn, "local", voice_id, new_voice_version):
        raise ValueError("local_adoption_voice_version_exists")
    data = json.loads(voice_row["payload"])
    if data.get("owner_user_id") is not None or data.get("reference") is not None:
        raise ValueError("local_adoption_voice_not_unowned_legacy")
    if data["id"] != voice_id or data["workspace_id"] != "local" or data["version"] != voice_version:
        raise ValueError("local_adoption_voice_record_mismatch")
    voice = repository._versioned_voice_profile_from_payload(conn, "local", data)
    current_authorization = repository._current_authorization(conn, "local", voice.authorization)
    if voice.authorization is None or current_authorization is None:
        raise ValueError("local_adoption_existing_voice_authorization_required")
    for authorization in (voice.authorization, current_authorization):
        failure = profile_use_failure(replace(voice, authorization=authorization),
                                      content_type="voice", usage_scope="narration")
        if failure:
            raise ValueError("local_adoption_voice_" + failure)
    path, revision = source(voice.reference_audio_path, must_be_registered=True)
    if repository._material_assets._asset_file_path_existence_row(conn, str(path)):
        raise ValueError("local_adoption_voice_source_already_registered")
    duration = _profile_wav_duration(path)
    if _local_adoption_file_revision(path) != revision:
        raise ValueError("local_adoption_voice_file_changed")
    authorization_rows = [dict(row) for row in repository._media_authorizations._authorization_history_rows(
        conn, "local", voice.authorization.id)]
    source_id = "voice_source_" + hashlib.sha256((operation_id + ":" + voice_id).encode()).hexdigest()[:32]
    if repository._material_assets._asset_id_existence_row(conn, source_id):
        raise ValueError("local_adoption_voice_source_id_exists")
    originals.update(voice=dict(voice_row), voice_path=str(path), authorizations=authorization_rows)
    manifest = {"format": "local-adoption/v1", "operation_id": operation_id, "workspace_id": "local",
        "member_id": member_id, "member_sha256": _local_adoption_digest(member),
        "database_identity": _local_adoption_database_identity(repository),
        "media_root": str(root), "root_binding_sha256": _local_adoption_digest(binding),
        "authorization_basis": authorization_basis, "assets": assets,
        "references": [references[key] for key in sorted(references)],
        "voice": {"id": voice_id, "version": voice_version, "new_version": new_voice_version,
            "row_sha256": _local_adoption_digest(dict(voice_row)),
            "payload_sha256": hashlib.sha256(voice_row["payload"].encode()).hexdigest(),
            "created_at": voice_row["created_at"], "authorization_sha256": _local_adoption_digest(authorization_rows),
            "target_owner_user_id": member_id, "target_visibility": "private",
            "source": {"id": source_id, "reference": voice.reference_audio_path,
                       "file_revision": revision, "duration_seconds": duration}}}
    return manifest, originals


def _validated_assistant_reply_reference(value: dict[str, str]) -> dict[str, str]:
    conversation_id = value.get("conversation_id", "").strip()
    turn_id = value.get("turn_id", "").strip()
    response_digest = value.get("response_digest", "").strip().lower()
    if not conversation_id or not turn_id or re.fullmatch(r"[0-9a-f]{64}", response_digest) is None:
        raise ValueError("对话回复来源必须包含会话、已完成轮次和 SHA-256 摘要")
    return {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "response_digest": response_digest,
    }


def _assistant_reply_input_references(
    values: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    required = ("reference_id", "kind", "object_id", "version_or_identity", "title")
    for value in values:
        if not isinstance(value, dict) or any(not str(value.get(key, "")).strip() for key in required):
            raise ValueError("文案输入来源缺少固定版本身份")
        reference_id = str(value["reference_id"])
        if reference_id in result:
            raise ValueError("文案输入来源标识重复")
        result[reference_id] = {key: value[key] for key in required if key != "reference_id"}
    return result


def _revision_value(value: datetime | str) -> str:
    result = value.isoformat() if isinstance(value, datetime) else value.strip()
    if not result:
        raise ValueError("对象修订令牌不能为空")
    return result


def _assistant_reply_request_event_id(event_type: str, workspace_id: str, request_id: str) -> str:
    identity = hashlib.sha256(f"{workspace_id}\0{request_id}".encode("utf-8")).hexdigest()[:32]
    return f"{event_type}_{identity}"


def _creation_plan_conversion_intent(plan: CreationPlan) -> dict[str, object]:
    value = asdict(plan)
    for key in ("id", "creation_task_id", "owner_user_id", "created_at", "confirmed_at", "updated_at"):
        value.pop(key, None)
    return value


def _creation_task_conversion_intent(task: CreationTask) -> dict[str, object]:
    value = asdict(task)
    for key in ("id", "plan_id", "owner_user_id", "created_at", "updated_at"):
        value.pop(key, None)
    return value


def _json_object_from_text(value: str) -> dict[str, object]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("持久化 JSON 对象无效")
    return decoded


def _string_list_from_text(value: str) -> list[str]:
    decoded = json.loads(value)
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) for item in decoded
    ):
        raise ValueError("持久化引用列表无效")
    return decoded


def _json_list_from_text(value: str) -> list[object]:
    decoded = json.loads(value)
    if not isinstance(decoded, list):
        raise ValueError("持久化 JSON 列表无效")
    return decoded


def _job_run_values(run: JobRun) -> tuple[object, ...]:
    return (
        run.id,
        run.workspace_id,
        run.task_id,
        run.kind,
        run.idempotency_key,
        run.input_digest,
        run.status,
        run.progress,
        run.attempt,
        json.dumps(run.output_refs, ensure_ascii=False),
        run.failure_reason,
        run.impact,
        run.repair_action,
        run.created_at.isoformat(),
        run.started_at.isoformat() if run.started_at else None,
        run.finished_at.isoformat() if run.finished_at else None,
        run.updated_at.isoformat(),
    )


def _narration_segment_values(segment: NarrationSegment) -> tuple[object, ...]:
    return (
        segment.id,
        segment.workspace_id,
        segment.task_id,
        segment.script_id,
        segment.source_section_id,
        segment.text,
        segment.audio_reference,
        segment.duration_seconds,
        segment.sample_rate,
        segment.model_id,
        segment.voice_profile_id,
        segment.voice_version,
        segment.language,
        segment.pronunciation_version,
        segment.status,
        segment.created_at.isoformat(),
        segment.updated_at.isoformat(),
        segment.source_kind,
        segment.source_artifact_ref,
        segment.source_sha256,
        segment.source_script_digest,
    )


def _person_render_values(render: PersonRender) -> tuple[object, ...]:
    return (
        render.id,
        render.workspace_id,
        render.task_id,
        render.person_profile_id,
        render.person_version,
        render.voice_profile_id,
        render.voice_version,
        render.narration_segment_id,
        render.visual_reference,
        render.model_id,
        render.motion,
        render.expression,
        render.cache_key,
        render.status,
        render.artifact_ref,
        render.output_path,
        render.duration_seconds,
        render.alignment_evidence,
        render.failure_reason,
        render.impact,
        render.repair_action,
        render.created_at.isoformat(),
        render.updated_at.isoformat(),
    )


def _person_render_identity(render: PersonRender) -> tuple[str, ...]:
    return (
        render.workspace_id,
        render.task_id,
        render.person_profile_id,
        render.person_version,
        render.voice_profile_id,
        render.voice_version,
        render.narration_segment_id,
        render.visual_reference,
        render.model_id,
        render.motion,
        render.expression,
        render.cache_key,
    )


def _profile_wav_duration(path: Path) -> float:
    if path.suffix.lower() != ".wav":
        raise ValueError("声音参考需要有效 WAV；视频来源需先明确派生音频")
    try:
        with wave.open(str(path), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            if rate <= 0 or frames <= 0 or audio.getnchannels() not in {1, 2} or audio.getsampwidth() not in {1, 2, 3, 4}:
                raise ValueError("声音参考 WAV 无有效音频")
            duration = frames / rate
            if not 1 <= duration <= 120:
                raise ValueError("声音参考 WAV 时长必须在 1–120 秒之间")
            if len(audio.readframes(frames)) != frames * audio.getnchannels() * audio.getsampwidth():
                raise ValueError("声音参考 WAV 已截断")
            return duration
    except (EOFError, wave.Error) as exc:
        raise ValueError("声音参考不是有效 PCM WAV") from exc


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


def _validate_timeline_version(version: TimelineVersion) -> None:
    if not all(
        value.strip() for value in (version.id, version.workspace_id, version.task_id)
    ):
        raise ValueError("时间线版本缺少 ID、工作区或任务")
    if version.sequence < 1:
        raise ValueError("时间线版本序号必须从 1 开始")
    if version.parent_version_id is not None and not version.parent_version_id.strip():
        raise ValueError("父时间线版本 ID 不能为空")
    if (
        version.created_by_operation_id is not None
        and not version.created_by_operation_id.strip()
    ):
        raise ValueError("创建时间线版本的操作 ID 不能为空")
    _json_object(version.snapshot)


def _validate_new_job_run(run: JobRun) -> None:
    if not all(
        value.strip()
        for value in (
            run.id,
            run.workspace_id,
            run.task_id,
            run.kind,
            run.idempotency_key,
            run.input_digest,
        )
    ):
        raise ValueError("任务运行记录缺少必要标识或输入摘要")
    if run.status != "queued" or run.progress != 0 or run.attempt != 0:
        raise ValueError("新任务运行记录必须从 queued、0% 且未尝试开始")
    if run.started_at is not None or run.finished_at is not None:
        raise ValueError("新任务运行记录不能已有开始或结束时间")
    if run.output_refs or any(
        value is not None for value in (run.failure_reason, run.impact, run.repair_action)
    ):
        raise ValueError("新任务运行记录不能预先包含输出或失败结果")


def _validate_artifact_record(artifact: ArtifactRecord) -> None:
    if not all(
        value.strip()
        for value in (
            artifact.id,
            artifact.workspace_id,
            artifact.task_id,
            artifact.kind,
            artifact.logical_ref,
            artifact.sha256,
            artifact.producer_job_id,
        )
    ):
        raise ValueError("产物记录缺少必要标识、逻辑引用或摘要")
    _validate_logical_reference(artifact.logical_ref, "产物逻辑引用")
    if not re.fullmatch(r"[0-9a-f]{64}", artifact.sha256):
        raise ValueError("产物摘要必须是小写 SHA-256")
    if artifact.size_bytes < 0:
        raise ValueError("产物大小不能为负数")
    for reference in artifact.input_refs:
        _validate_logical_reference(reference, "产物输入引用")


def _validate_narration_segment(segment: NarrationSegment) -> None:
    identity = (segment.id, segment.workspace_id, segment.task_id, segment.script_id,
                segment.source_section_id, segment.text, segment.language)
    if not all(value.strip() for value in identity):
        raise ValueError("配音分段缺少必要谱系或文字")
    if segment.source_kind == "generated":
        if not all(value and value.strip() for value in
                   (segment.model_id, segment.voice_profile_id, segment.voice_version)):
            raise ValueError("生成配音分段必须有关联模型与声音版本")
        if (segment.source_artifact_ref is not None or segment.source_sha256 is not None
                or segment.source_script_digest is not None):
            raise ValueError("生成配音不能携带电脑录音来源")
    elif segment.source_kind == "recorded":
        if (segment.model_id is not None or segment.voice_profile_id is not None
                or segment.voice_version is not None
                or not segment.source_artifact_ref
                or not segment.source_sha256
                or not segment.source_script_digest
                or not re.fullmatch(r"[0-9a-f]{64}", segment.source_sha256)):
            raise ValueError("电脑录音分段必须仅关联真实录音产物与SHA-256")
        _validate_logical_reference(segment.source_artifact_ref, "电脑录音来源")
    else:
        raise ValueError("配音来源类型无效")
    if segment.audio_reference is not None:
        _validate_logical_reference(segment.audio_reference, "配音分段音频引用")
    if segment.duration_seconds is not None and (
        not math.isfinite(segment.duration_seconds) or segment.duration_seconds < 0
    ):
        raise ValueError("配音分段时长无效")
    if segment.sample_rate is not None and segment.sample_rate <= 0:
        raise ValueError("配音分段采样率无效")
    if segment.status == "ready" and (
        segment.audio_reference is None
        or segment.duration_seconds is None
        or segment.duration_seconds <= 0
        or (segment.sample_rate is None and segment.source_kind != "recorded")
    ):
        raise ValueError("ready 配音分段必须有可定位音频、时长和采样率")
    if segment.status not in {"pending", "generating", "ready", "failed"}:
        raise ValueError("配音分段状态无效")
    if segment.updated_at < segment.created_at:
        raise ValueError("配音分段更新时间不能早于创建时间")


def _validate_person_render(render: PersonRender) -> None:
    if not all(
        value.strip()
        for value in (
            render.id,
            render.workspace_id,
            render.task_id,
            render.person_profile_id,
            render.person_version,
            render.voice_profile_id,
            render.voice_version,
            render.narration_segment_id,
            render.visual_reference,
            render.model_id,
            render.motion,
            render.expression,
            render.cache_key,
        )
    ):
        raise ValueError("人物片段缺少必要谱系、输入版本或模型参数")
    _validate_logical_reference(render.visual_reference, "人物片段画面引用")
    if render.artifact_ref is not None:
        _validate_logical_reference(render.artifact_ref, "人物片段产物引用")
    if not re.fullmatch(r"[0-9a-f]{64}", render.cache_key):
        raise ValueError("人物片段缓存键必须是小写 SHA-256")
    if render.updated_at < render.created_at:
        raise ValueError("人物片段更新时间不能早于创建时间")
    if render.status in {"pending", "running"}:
        if any(
            value is not None
            for value in (
                render.output_path,
                render.artifact_ref,
                render.duration_seconds,
                render.alignment_evidence,
                render.failure_reason,
                render.impact,
                render.repair_action,
            )
        ):
            raise ValueError("未完成的人物片段不能包含输出或失败终态")
    elif render.status == "ready":
        if (
            not (render.output_path or "").strip()
            or Path(render.output_path or "").is_absolute() is False
            or render.duration_seconds is None
            or not math.isfinite(render.duration_seconds)
            or render.duration_seconds <= 0
            or not (render.alignment_evidence or "").strip()
        ):
            raise ValueError("ready 人物片段必须有有效输出、时长和对齐证据")
        if any(
            value is not None
            for value in (render.failure_reason, render.impact, render.repair_action)
        ):
            raise ValueError("ready 人物片段不能包含失败终态")
    elif render.status == "failed":
        if not all(
            (value or "").strip()
            for value in (render.failure_reason, render.impact, render.repair_action)
        ):
            raise ValueError("失败人物片段必须记录原因、影响和修复动作")
        if any(
            value is not None
            for value in (
                render.output_path,
                render.artifact_ref,
                render.duration_seconds,
                render.alignment_evidence,
            )
        ):
            raise ValueError("失败人物片段不能登记成功输出")
    else:
        raise ValueError("人物片段状态无效")


def _validate_subtitle_track(track: SubtitleTrack) -> None:
    if not all(
        value.strip()
        for value in (
            track.id,
            track.workspace_id,
            track.task_id,
            track.timeline_version_id,
            track.language,
            track.generation_method,
        )
    ):
        raise ValueError("字幕轨缺少任务、时间线、语言或生成方式")
    if track.language not in {"zh-CN", "en-US"}:
        raise ValueError("字幕轨语言无效")
    if track.generation_method not in {"timeline", "translation"}:
        raise ValueError("字幕轨生成方式无效")
    if track.source_language is not None and track.source_language not in {
        "zh-CN",
        "en-US",
    }:
        raise ValueError("字幕轨源语言无效")
    if track.updated_at < track.created_at:
        raise ValueError("字幕轨更新时间不能早于创建时间")
    cue_ids: set[str] = set()
    previous_end = 0.0
    for cue in track.cues:
        if not cue.id.strip() or not cue.text.strip():
            raise ValueError("字幕条目缺少稳定标识或文字")
        if cue.id in cue_ids:
            raise ValueError("字幕轨包含重复条目标识")
        cue_ids.add(cue.id)
        if (
            not math.isfinite(cue.start_seconds)
            or not math.isfinite(cue.end_seconds)
            or cue.start_seconds < 0
            or cue.end_seconds <= cue.start_seconds
            or cue.start_seconds < previous_end - 0.001
        ):
            raise ValueError("字幕条目时间无效或相互重叠")
        if any(index <= 0 or index >= len(cue.text) for index in cue.line_breaks):
            raise ValueError("字幕断行位置超出文字范围")
        previous_end = cue.end_seconds
    if track.status == "valid":
        if not track.cues or not track.artifact_ref or not track.transform_id:
            raise ValueError("valid 字幕轨必须有条目、产物和变换证据")
        _validate_logical_reference(track.artifact_ref, "字幕产物引用")
        if track.failure_reason or track.impact or track.repair_action:
            raise ValueError("valid 字幕轨不能包含失败终态")
    elif track.status in {"failed", "unavailable"}:
        if not all(
            (value or "").strip()
            for value in (track.failure_reason, track.impact, track.repair_action)
        ):
            raise ValueError("失败或不可用字幕轨必须记录原因、影响和修复动作")
        if track.artifact_ref is not None or track.transform_id is not None:
            raise ValueError("失败或不可用字幕轨不能登记成功证据")
    elif track.status != "superseded":
        raise ValueError("字幕轨状态无效")


def _validate_cover_asset(cover: CoverAsset) -> None:
    if not all(
        value.strip()
        for value in (
            cover.id,
            cover.workspace_id,
            cover.task_id,
            cover.timeline_version_id,
            cover.source_video_ref,
            cover.title,
            cover.generation_method,
            cover.model_or_engine,
        )
    ):
        raise ValueError("封面缺少任务、时间线、源画面、标题或执行器")
    if not math.isfinite(cover.frame_time_seconds) or cover.frame_time_seconds < 0:
        raise ValueError("封面选帧时间无效")
    _validate_logical_reference(cover.source_video_ref, "封面源视频引用")
    if cover.generation_method not in {"video_frame", "image_model"}:
        raise ValueError("封面生成方式无效")
    if cover.updated_at < cover.created_at:
        raise ValueError("封面更新时间不能早于创建时间")
    if cover.status == "valid":
        if not cover.artifact_ref or not cover.transform_id:
            raise ValueError("valid 封面必须有产物和变换证据")
        _validate_logical_reference(cover.artifact_ref, "封面产物引用")
        if cover.failure_reason or cover.impact or cover.repair_action:
            raise ValueError("valid 封面不能包含失败终态")
    elif cover.status in {"failed", "unavailable"}:
        if not all(
            (value or "").strip()
            for value in (cover.failure_reason, cover.impact, cover.repair_action)
        ):
            raise ValueError("失败或不可用封面必须记录原因、影响和修复动作")
        if cover.artifact_ref is not None or cover.transform_id is not None:
            raise ValueError("失败或不可用封面不能登记成功证据")
    elif cover.status != "superseded":
        raise ValueError("封面状态无效")


def _validate_task_event(event: TaskEvent) -> None:
    if not all(
        value.strip()
        for value in (
            event.id,
            event.workspace_id,
            event.task_id,
            event.event_type,
        )
    ):
        raise ValueError("任务事件缺少必要标识或类型")
    if event.job_id is not None and not event.job_id.strip():
        raise ValueError("任务事件的运行记录 ID 不能为空")
    _json_object(event.payload)


def _validate_transform_run(run: TransformRun) -> None:
    if not all(
        value.strip()
        for value in (
            run.id,
            run.workspace_id,
            run.task_id,
            run.job_id,
            run.transform_type,
            run.input_digest,
            run.engine,
        )
    ):
        raise ValueError("变换运行缺少必要标识、输入摘要或引擎")
    for reference in (*run.input_refs, *run.output_refs):
        _validate_logical_reference(reference, "变换输入输出引用")
    if len(set(run.input_refs)) != len(run.input_refs) or len(
        set(run.output_refs)
    ) != len(run.output_refs):
        raise ValueError("变换输入输出引用不能重复")
    _json_object(run.parameters)
    if run.finished_at is not None and run.finished_at < run.started_at:
        raise ValueError("变换结束时间不能早于开始时间")
    if run.status == "running":
        if (
            run.finished_at is not None
            or run.output_refs
            or run.failure_reason is not None
            or run.remediation is not None
        ):
            raise ValueError("运行中变换不能已有输出或终态信息")
    elif run.status == "succeeded":
        if run.finished_at is None or not run.output_refs:
            raise ValueError("成功变换必须包含结束时间和输出引用")
        if run.failure_reason is not None or run.remediation is not None:
            raise ValueError("成功变换不能包含失败信息")
    elif run.status == "failed":
        if (
            run.finished_at is None
            or not (run.failure_reason or "").strip()
            or not (run.remediation or "").strip()
        ):
            raise ValueError("失败变换必须包含结束时间、原因和修复动作")
        if run.output_refs:
            raise ValueError("失败变换不能登记成功输出")
    else:
        raise ValueError("变换运行状态无效")


def _validate_dependency_edge(edge: DependencyEdge) -> None:
    if not all(
        value.strip()
        for value in (
            edge.id,
            edge.workspace_id,
            edge.task_id,
            edge.created_by_transform_id,
        )
    ):
        raise ValueError("依赖边缺少必要标识或变换引用")
    _validate_logical_reference(edge.upstream_ref, "依赖边上游引用")
    _validate_logical_reference(edge.downstream_ref, "依赖边下游引用")
    if edge.upstream_ref == edge.downstream_ref:
        raise ValueError("依赖边不能指向自身")


def _validate_edit_intent(intent: EditIntent) -> None:
    if not all(
        value.strip()
        for value in (
            intent.id,
            intent.workspace_id,
            intent.task_id,
            intent.raw_command,
            intent.context_stage,
            intent.intent_type,
            intent.status,
        )
    ):
        raise ValueError("编辑意图缺少必要标识、命令、上下文或状态")
    for reference in intent.selected_object_refs:
        _validate_logical_reference(reference, "编辑意图选中对象引用")
    if intent.playhead_ms is not None and intent.playhead_ms < 0:
        raise ValueError("播放位置不能为负数")
    if not 0.0 <= intent.confidence <= 1.0:
        raise ValueError("编辑意图置信度必须在 0 到 1 之间")
    _json_object(intent.scope)
    _json_object(intent.constraints)


def _validate_plan_material_matches(
    matches: list[MaterialSegmentMatch],
) -> None:
    identities: set[tuple[str, str]] = set()
    for match in matches:
        if (
            not match.asset_ref.startswith("asset:")
            or not match.asset_ref.removeprefix("asset:").strip()
            or not match.segment_ref.startswith("segment:")
            or not match.segment_ref.removeprefix("segment:").strip()
        ):
            raise ValueError("创作方案素材命中必须使用逻辑 asset/segment 引用")
        if not math.isfinite(match.score) or match.score < 0:
            raise ValueError("创作方案素材命中分数无效")
        if not match.reasons or any(not reason.strip() for reason in match.reasons):
            raise ValueError("创作方案素材命中必须包含可解释理由")
        identity = (match.asset_ref, match.segment_ref)
        if identity in identities:
            raise ValueError("创作方案不能重复引用同一素材片段")
        identities.add(identity)


def _validate_new_edit_proposal(proposal: EditProposal) -> None:
    if not all(
        value.strip()
        for value in (
            proposal.id,
            proposal.workspace_id,
            proposal.task_id,
            proposal.intent_id,
            proposal.summary,
        )
    ):
        raise ValueError("编辑提案缺少必要标识或摘要")
    if proposal.status != "proposed":
        raise ValueError("新编辑提案必须从 proposed 状态开始")
    if not proposal.target_refs:
        raise ValueError("编辑提案必须包含目标引用")
    for reference in (*proposal.target_refs, *proposal.invalidated_refs):
        _validate_logical_reference(reference, "编辑提案对象引用")
    if not proposal.operation:
        raise ValueError("编辑提案必须包含类型化操作")
    _json_object(proposal.operation)
    _json_object(proposal.preview)
    json.dumps(proposal.external_effects, ensure_ascii=False, default=_json_default)
    if proposal.updated_at < proposal.created_at:
        raise ValueError("编辑提案更新时间不能早于创建时间")


def _validate_edit_operation(operation: EditOperation) -> None:
    if not all(
        value.strip()
        for value in (
            operation.id,
            operation.workspace_id,
            operation.task_id,
            operation.proposal_id,
            operation.operation_type,
            operation.base_version_id,
        )
    ):
        raise ValueError("编辑操作缺少必要标识、提案或基础版本")
    if operation.operation_type not in SUPPORTED_EDIT_OPERATION_TYPES:
        raise ValueError("编辑操作类型不在当前冻结集合中")
    if not operation.payload:
        raise ValueError("编辑操作必须包含类型化 payload")
    _json_object(operation.payload)
    if operation.status == "running":
        if (
            operation.result_version_id is not None
            or operation.failure_reason is not None
            or operation.applied_at is not None
        ):
            raise ValueError("运行中编辑操作不能已有终态字段")
    elif operation.status == "applied":
        if not (operation.result_version_id or "").strip() or operation.applied_at is None:
            raise ValueError("已应用编辑操作必须包含结果版本和应用时间")
        if operation.failure_reason is not None:
            raise ValueError("已应用编辑操作不能包含失败原因")
        if operation.applied_at < operation.created_at:
            raise ValueError("编辑操作应用时间不能早于创建时间")
    elif operation.status == "failed":
        if not (operation.failure_reason or "").strip():
            raise ValueError("失败编辑操作必须包含原因")
        if operation.result_version_id is not None or operation.applied_at is not None:
            raise ValueError("失败编辑操作不能包含成功结果")
    else:
        raise ValueError("编辑操作状态无效")


def _validate_logical_reference(reference: str, label: str) -> None:
    if not reference.strip():
        raise ValueError(f"{label}不能为空")
    if Path(reference).is_absolute() or reference.startswith("file://"):
        raise ValueError(f"{label}不能是绝对路径")




def _validate_media_preview_run(record: MediaPreviewRunRecord) -> None:
    if not record.workspace_id.strip() or not record.id.strip() or not record.identity.strip():
        raise ValueError("预览运行记录缺少工作区、ID 或幂等身份")
    if record.preview_type not in {"voice", "avatar_lipsync"}:
        raise ValueError("预览类型无效")
    if record.status not in {"queued", "running", "succeeded", "unavailable", "blocked", "failed", "cancelled"}:
        raise ValueError("预览运行状态无效")
    if not record.capability_id.strip() or not record.model_id.strip() or not record.language.strip():
        raise ValueError("预览运行记录缺少能力、模型或语言")
    if record.attempt < 1:
        raise ValueError("预览尝试序号必须从 1 开始")
    if record.status in {"queued", "running"}:
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


def _validate_profile_reference_fields(profile) -> None:
    if profile.use_state not in {"available", "reference_only", "excluded", "not_for_automation", "not_for_publish"}:
        raise ValueError("档案使用状态无效")
    if profile.visibility not in {"private", "shared"}:
        raise ValueError("档案可见范围无效")
    reference = profile.reference
    if reference is None:
        raise ValueError("新档案需要明确来源引用，不能猜测历史来源")
    if profile.reference_issue is not None:
        raise ValueError("历史参考未可靠映射，请先重新定位来源")
    if reference.segment_id and not reference.asset_id:
        raise ValueError("参考片段必须绑定素材")
    if (reference.start_seconds is None) != (reference.end_seconds is None):
        raise ValueError("参考范围必须同时包含起止时间")
    if reference.start_seconds is not None:
        if any(type(value) not in {int, float} or not math.isfinite(value)
               for value in (reference.start_seconds, reference.end_seconds)):
            raise ValueError("参考时间必须是有限数值")
        if reference.start_seconds < 0 or reference.end_seconds <= reference.start_seconds:
            raise ValueError("参考范围无效")


def _validate_person_profile(person: PersonProfile) -> None:
    if not all(
        value.strip()
        for value in (person.id, person.workspace_id, person.display_name, person.version, person.avatar_reference)
    ):
        raise ValueError("人物档案缺少必要标识或形象引用")
    _validate_profile_reference_fields(person)
    if person.authorization is not None and person.authorization.workspace_id != person.workspace_id:
        raise ValueError("人物授权不属于当前工作区")


def _validate_versioned_voice_profile(voice: VersionedVoiceProfile) -> None:
    if not all(
        value.strip()
        for value in (
            voice.id,
            voice.workspace_id,
            voice.version,
            voice.reference_audio_path,
        )
    ):
        raise ValueError("声音档案缺少必要标识或参考资料")
    if voice.language not in {"zh", "en", "zh-en"}:
        raise ValueError("声音语言必须是 zh、en 或 zh-en")
    if bool(voice.person_profile_id) != bool(voice.person_version):
        raise ValueError("人物关系必须同时指定人物和版本")
    _validate_profile_reference_fields(voice)
    if voice.reference is not None and not voice.display_name.strip():
        raise ValueError("独立声音需要名称")
    if voice.authorization is not None and voice.authorization.workspace_id != voice.workspace_id:
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




def _string_tuple(data: dict[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"持久化媒体对象的 {key} 无效")
    return tuple(value)








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


def _material_contains_text(text: str, phrase: str) -> bool:
    """中文按子串算包含，英文按词边界算——避免 "usb" 命中 "bus"。"""
    if re.search(r"[\u4e00-\u9fff]", phrase):
        return phrase in text
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


# 英文虚词不作为匹配依据：命中它们不构成「这条更好」的证据。
_MATERIAL_QUERY_STOPWORDS = frozenset(
    {"a", "an", "the", "of", "to", "in", "on", "at", "is", "it", "and", "or", "for"}
)
# 中文虚字：切分前先剔掉，它们不携带检索意图。
# 「更」「长」「太」「吵」都不在表内，所以「更长」「太吵」能切出来。
_MATERIAL_QUERY_FUNCTION_CHARS = frozenset("的了着过和与或是在这那不也就都而之其为以于被把从对向")


def _material_query_units(normalized_query: str) -> list[str]:
    """把一句查询切成可核对的最小单元：英文/数字词 + 中文二元组。"""
    units: list[str] = []
    for token in re.findall(r"[^\W_]+", normalized_query):
        chunks = (
            re.findall(r"[\u4e00-\u9fff]+", token)
            if re.search(r"[\u4e00-\u9fff]", token)
            else [token]
        )
        for chunk in chunks:
            if re.search(r"[\u4e00-\u9fff]", chunk):
                # 先剔虚字再切二元组：「更长一点的」要切出「更长」而不是「点的」。
                meaningful = "".join(
                    char for char in chunk if char not in _MATERIAL_QUERY_FUNCTION_CHARS
                )
                units.extend(
                    meaningful[index : index + 2] for index in range(len(meaningful) - 1)
                )
            elif len(chunk) >= 2 and chunk not in _MATERIAL_QUERY_STOPWORDS:
                units.append(chunk)
    # 去重且保持出现顺序，让「为什么被选中」稳定可复现。
    return list(dict.fromkeys(units))


def _material_searchable_corpus(segment: MaterialSegment, asset: MaterialAsset) -> str:
    """一个片段所有能被人读出来的文字，作为「依据」的取样范围。"""
    return " ".join(
        value.casefold()
        for value in (
            segment.description,
            searchable_ocr_text(segment.understanding),
            segment.transcript,
            *segment.system_tags,
            *segment.user_tags,
            *(asset.user_tags if isinstance(asset, MaterialAsset) else ()),
        )
        if value
    )


def _material_partial_terms(
    normalized_query: str, segment: MaterialSegment, asset: MaterialAsset
) -> list[str]:
    """字面没整段命中时，看查询里还有哪些词真的出现在这条素材上。"""
    corpus = _material_searchable_corpus(segment, asset)
    if not corpus:
        return []
    return [unit for unit in _material_query_units(normalized_query) if unit in corpus]


def _material_timecode(seconds: float) -> str:
    """把秒数写成 00:12 这种人能读的时间点。"""
    total = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _material_query_reasons(
    normalized_query: str, segment: MaterialSegment, asset: MaterialAsset
) -> list[str]:
    reasons: list[str] = []
    text_fields = (
        ("片段描述", segment.description),
        ("本地画面观察", segment.understanding.summary if not segment.description and segment.understanding and segment.understanding.status == "ready" else ""),
        ("原片画面文字", searchable_ocr_text(segment.understanding)),
        ("片段转录", segment.transcript or speech_text_for_range(asset.speech_analysis, segment.start_seconds, segment.end_seconds)),
    )
    query_terms = re.findall(r"[^\W_]+", normalized_query)

    def contains_text(text: str, phrase: str) -> bool:
        return _material_contains_text(text, phrase)
    for label, raw_value in text_fields:
        value = raw_value.strip().casefold()
        if not value:
            continue
        if (
            contains_text(value, normalized_query)
            or contains_text(normalized_query, value)
            or any(contains_text(value, term) for term in query_terms)
        ):
            reasons.append(f"自然语言匹配{label}")
    for source, values in (
        ("系统标签", segment.system_tags),
        ("用户标签", segment.user_tags),
        ("整条素材标签", asset.user_tags if isinstance(asset, MaterialAsset) else []),
    ):
        matched = []
        for raw_value in values:
            value = raw_value.casefold()
            suffix = value.split(":", 1)[-1]
            if value in normalized_query or suffix in normalized_query:
                matched.append(raw_value)
        reasons.extend(
            f"自然语言匹配{source} {value}" for value in matched
        )
    role_aliases = {
        "a_roll": ("人物主镜头", "主镜头", "口播画面", "人物画面"),
        "b_roll": ("补充画面", "素材画面", "辅助镜头", "产品画面"),
    }
    role = segment.usage_role
    if role == "unassigned":
        role = next(
            (
                value.split(":", 1)[1]
                for value in segment.system_tags
                if value.startswith("用途:")
                and value.split(":", 1)[1] in {"a_roll", "b_roll"}
            ),
            "unassigned",
        )
    if role in role_aliases and any(
        alias in normalized_query for alias in role_aliases[role]
    ):
        reasons.append(f"自然语言匹配用途 {role}")
    return reasons





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
