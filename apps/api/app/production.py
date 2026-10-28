from __future__ import annotations

from app.creation.script_contracts import (
    ScriptGenerationRunStatus as _ScriptGenerationRunStatus, ScriptSelectionStatus as _ScriptSelectionStatus, ScriptGenerationRun as _ScriptGenerationRun,
    CreationTaskScriptSection as _CreationTaskScriptSection, CreationTaskScript as _CreationTaskScript, ScriptLibrarySection as _ScriptLibrarySection, ScriptLibraryEntry as _ScriptLibraryEntry,
    _validated_assistant_reply_reference as _validated_assistant_reply_reference, _assistant_reply_input_references as _assistant_reply_input_references, _assistant_reply_request_event_id as _assistant_reply_request_event_id,
)
from app.creation.request_records import CreationRequestRecordStore
from app.creation.request_adoption_use_case import CreationRequestAdoptionUseCase
from app.creation.execution_records import ExecutionRecordStore
from app.creation.script_records import ScriptRecordStore
from app.creation.topic_records import TopicRecordStore
from app.creation.lineage_records import LineageRecordStore
from app.materials.probe_records import MaterialProbeRecordStore
from app.materials.scene_records import MaterialSceneRecordStore
from app.materials.hash_intake import MaterialHashIntake
from app.materials.hash_records import MaterialHashRecordStore
from app.material_hash import (
    MaterialHashCandidate as _MaterialHashCandidate,
    MaterialHashResult as _MaterialHashResult,
    material_hash_candidate as _material_hash_candidate,
)
from app.material_scene import MaterialSceneCandidate as _MaterialSceneCandidate
from app.material_probe import (
    MaterialProbeDependency as _MaterialProbeDependency,
    MaterialProbeCandidate as _MaterialProbeCandidate,
    material_probe_unit_key as _material_probe_unit_key,
)
from app.creation.topic_contracts import (
    TopicCandidate as _TopicCandidate,
    TopicEvidenceKind as _TopicEvidenceKind,
    TopicEvidenceReference as _TopicEvidenceReference,
    TopicGenerationRun as _TopicGenerationRun,
    TopicGenerationRunStatus as _TopicGenerationRunStatus,
    TopicSelectionStatus as _TopicSelectionStatus,
)

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
from contextlib import closing, contextmanager, nullcontext
from contextvars import ContextVar
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from app.profiles.preview_records import PreviewRecordStore
from app.creation.transactions import EditAdoptionTransaction
from app.creation.timeline_restore_records import TimelineRestoreRecordStore
from typing import TYPE_CHECKING, Literal
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from app.avatar_lipsync import (AvatarAndLipSyncRequest)
from app.creation.contracts import (
    ClipSourceSelection as _ClipSourceSelection, CreationTask as _CreationTask, NarrationSegment as _NarrationSegment, NarrationSegmentStatus as _NarrationSegmentStatus,
    validate_logical_reference as _validate_logical_reference,
)
from app.creation.person_renders import PersonRenderStore, PersonRender, PersonRenderStatus
from app.creation.task_input_reads import (
    require_creation_task, creation_task_from_payload, narration_segment_from_row,
    narration_segment_row, narration_segment_rows,
)
from app.profiles.contracts import (Authorization, PersonProfile, ProfileReference, profile_use_failure, _as_utc, PronunciationEntry, VoiceProfile as VersionedVoiceProfile)
from app.profiles.authorizations import MediaAuthorizationStore, _authorization_payload, _authorization_from_payload, _validate_authorization
from app.creation.editing.timeline_history_repository import TimelineHistoryReadRepository
from app.profiles.versioned_payloads import _save_versioned_payload, _list_versioned_payloads, _versioned_row
from app.profiles.legacy_voice import LegacyVoiceProfileStore, VoiceProfile
from app.profiles.workspace_profile import WorkspaceProductionProfileStore, ProductionProfile
from app.profiles.records import (
    ProfileRecordStore, _person_profile_payload, _versioned_voice_profile_payload,
    _profile_reference_payload, _profile_reference_fields,
)
from app.creation.script_windows import SCRIPT_SECTION_WINDOW_LIMIT
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
from app.materials.analysis import MaterialAnalysisUseCase, _material_analysis_public_error
from app.materials.analysis_execution import probe_duration, detect_scene_boundaries
from app.materials.source_files import MaterialSourceFileAccess
from app.materials.workspace_references import WorkspaceMediaReferenceAccess
from app.profiles.reference_access import ProfileReferenceAccess, _profile_wav_duration
from app.profiles.registration import ProfileRegistrationUseCase
from app.profiles.voice_sampling import VoiceSamplingUseCase
from app.materials.discovery import MaterialDiscoveryService
from app.materials.discovery_store import DiscoveryStore
from app.materials.discovery_intake import DiscoveryIntake
from app.source_policy import content_owner
from app.source_policy import knowledge_item_readable as _content_readable
from app.materials.contracts import MediaAuthorityError as _MediaAuthorityError
from app.creation.contracts import (
    CreationPlanVoiceReference as _CreationPlanVoiceReference,
    CreationPlanPersonReference as _CreationPlanPersonReference,
)
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
    material_usage_eligibility,
    effective_segment_usage_role,
    selectable_segment_usage_role,
)
from app.asr import SubtitleSegment as SpeechObservation
from app.material_speech import LocalMaterialSpeechAnalyzer, MaterialSpeechAnalysis, speech_text_for_range
# Only direct-link import admits non-video media. The directory scan whitelist
# (SUPPORTED_VIDEO_EXTENSIONS) is unchanged: nothing else starts watching these.
from app.video import SUPPORTED_VIDEO_EXTENSIONS
from app.visual_semantics import (
    UnavailableVisualSemanticProvider,
    VisualSemanticProvider,
)
from app.material_probe import MaterialProbeFacts, probe_checkpoint_needs_rate_refresh
from app.media_link_import import RegisteredLink
from app.material_scene import (
    SCENE_LOGICAL_WINDOW_SECONDS, SceneWindow, SceneWindowFacts, scene_unit_key,
    scene_window_for_key, scene_windows,
)

if TYPE_CHECKING:
    from app.device_scheduler import Grant
    from app.profile_reference_media import ProfileReferenceInput, ProfileReferenceArtifact

logger = logging.getLogger(__name__)
EXACT_EDIT_SOURCE = ContextVar("exact_edit_source", default=None)






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
    if (asset.workspace_id != workspace_id
            or not material_usage_eligibility(asset,segment,purpose='automatic' if automation_only else 'manual').eligible
            or asset.media_type != "video"
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










@dataclass(frozen=True)
class VersionedNarrationInputs:
    voice: VersionedVoiceProfile
    pronunciation_entries: tuple[PronunciationEntry, ...]







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
    selected_voice: _CreationPlanVoiceReference | None
    pronunciation_references: list[CreationPlanPronunciationReference]
    approved_asset_count: int
    approved_clip_count: int
    material_rule_summary: str
    model_snapshot: list[dict[str, object]]
    confirmation_points: list[str]
    blockers: list[CreationPlanBlocker]
    preflight_fingerprint: str
    selected_person: _CreationPlanPersonReference | None = None
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
    creation_intent: dict[str, object] | None = None
    adoption: dict[str, object] | None = None
    duration_mode: str = "exact"
    duration_range_seconds: tuple[float, float] | None = None


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
























# Public imports retained while downstream creation consumers migrate.
from app.creation.contracts import (
    JobRunStatus as _JobRunStatus,
    TimelineVersion as _TimelineVersion,
    SubtitleTrackStatus as _SubtitleTrackStatus,
    SubtitleCue as _SubtitleCue,
    SubtitleTrack as _SubtitleTrack,
    CoverAsset as _CoverAsset,
    JobRun as _JobRun,
    ArtifactRecord as _ArtifactRecord,
    TaskEvent as _TaskEvent,
    TransformRunStatus as _TransformRunStatus,
    TransformRun as _TransformRun,
    DependencyEdge as _DependencyEdge,
    EditIntent as _EditIntent,
    EditProposalStatus as _EditProposalStatus,
    EditProposal as _EditProposal,
    EditOperationStatus as _EditOperationStatus,
    EditOperationType as _EditOperationType,
    SUPPORTED_EDIT_OPERATION_TYPES as _SUPPORTED_EDIT_OPERATION_TYPES,
    EditOperation as _EditOperation
)


class TimelineVersionConflict(ValueError):
    """The original timeline changed before an atomic edit or preview submission."""




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
        discovery_excluded_roots: tuple[Path, ...] = (),
    ) -> None:
        self.database_path = Path(database_path).expanduser()
        self._material_source_files = MaterialSourceFileAccess(self._connect, Path(video_library_path).expanduser())
        self._material_business_objects = MaterialBusinessObjectStore(self._connect)
        self._material_collections = MaterialCollectionStore(self._connect)
        self._material_assets = MaterialAssetStore(self._connect)
        self._workspace_media_references = WorkspaceMediaReferenceAccess(
            self._connect, self._material_source_files, self._material_assets)
        self._material_analysis = MaterialAnalysisUseCase(
            self._material_assets, self._material_source_files, connection_factory=self._connect,
            duration_reader=duration_reader or probe_duration,
            scene_reader=scene_reader or detect_scene_boundaries,
            visual_provider=visual_provider or UnavailableVisualSemanticProvider(),
            speech_analyzer=speech_analyzer, visual_understanding=visual_understanding,
        )
        self._initialize_profile_stores()
        self._legacy_voice_profiles = LegacyVoiceProfileStore(self._connect)
        self._workspace_profiles = WorkspaceProductionProfileStore(self._connect)
        self._profile_references = ProfileReferenceAccess(
            self._connect, self._profile_records, self._media_authorizations,
            self._material_assets, self._workspace_media_references, self._material_source_files)
        self._profile_registration = ProfileRegistrationUseCase(
            self._connect, self._profile_records, self._media_authorizations,
            self._workspace_profiles, self._legacy_voice_profiles, self._profile_references,
            self._workspace_media_references, self._material_assets)
        self._person_renders = PersonRenderStore(
            self._connect, self._profile_records, self._workspace_media_references)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()
        self.material_discovery = MaterialDiscoveryService(
            DiscoveryStore(self._connect),
            excluded_roots=lambda: [self.video_library_path, self.database_path, *discovery_excluded_roots],
            target_for=self.material_link_target,
            accept_copy=DiscoveryIntake(self._connect, self._material_assets,
                workspace_references=self._workspace_media_references,
                target_for=self.material_link_target, confirm_copy=self.confirm_material_copy_complete),
        )

    @property
    def video_library_path(self) -> Path:
        return self._material_source_files.video_library_path

    @property
    def voice_sampling(self) -> VoiceSamplingUseCase:
        return VoiceSamplingUseCase(
            self._connect, library_path=self.video_library_path,
            registration=self._profile_registration,
        )

    @video_library_path.setter
    def video_library_path(self, value: Path) -> None:
        self._material_source_files.video_library_path = value

    @property
    def duration_reader(self) -> Callable[[Path], float]:
        return self._material_analysis.duration_reader

    @duration_reader.setter
    def duration_reader(self, value: Callable[[Path], float]) -> None:
        self._material_analysis.duration_reader = value

    @property
    def visual_understanding(self) -> LocalVisualUnderstanding | None:
        return self._material_analysis.visual_understanding

    @visual_understanding.setter
    def visual_understanding(self, value: LocalVisualUnderstanding | None) -> None:
        self._material_analysis.visual_understanding = value

    def _initialize_profile_stores(self) -> None:
        """Bind record owners; do not open connections or provision schema."""
        self.edit_adoption = EditAdoptionTransaction(self._connect,
            require_task=self._require_creation_task, decode_proposal=self._edit_proposal_from_row,
            read_task=self.get_creation_task, read_proposal=self.get_edit_proposal,
            read_operation=self.get_edit_operation, read_version=self.get_timeline_version,
            read_narration_segment=self.get_narration_segment,
            iter_narration_edit_segments=self.iter_narration_edit_segments,
            replace_adopted_narration_segment=self.replace_adopted_narration_segment,
            upsert_narration_segments=self._upsert_narration_segments,
            validate_operation=_validate_edit_operation, validate_version=_validate_timeline_version,
            validate_track=_validate_subtitle_track, validate_cover=_validate_cover_asset)
        self.preview_records = PreviewRecordStore(self._connect)
        self._media_authorizations = MediaAuthorizationStore(self._connect)
        self._profile_records = ProfileRecordStore(self._connect, self._media_authorizations)
        self.hash_records = MaterialHashRecordStore(self._connect)
        self.hash_intake = MaterialHashIntake(self._connect, records=self.hash_records,
            library_root=lambda: self.video_library_path,
            library_available=self._material_library_available,
            read_binding=self.get_material_ingestion_binding,
            mark_disk_missing=self._mark_material_ingestion_disk_missing,
            upsert_asset=self._upsert_discovered_asset,
            asset_by_path=self._material_assets.get_asset_by_path,
            mark_asset_blocked=self._material_assets.mark_hash_asset_blocked)
        self.probe_records = MaterialProbeRecordStore(self._connect,
            library_root=lambda: self.video_library_path,
            library_available=self._material_library_available,
            read_binding=self.get_material_ingestion_binding,
            hash_candidate_from_stat=_material_hash_candidate,
            read_asset_source=self._material_assets._probe_asset_source_row,
            apply_probe_metadata=self._material_assets.apply_probe_metadata,
            mark_probe_failed=self._material_assets.mark_probe_failed)
        self.scene_records = MaterialSceneRecordStore(self._connect,
            library_root=lambda: self.video_library_path,
            library_available=self._material_library_available,
            read_binding=self.get_material_ingestion_binding,
            preferred_probe_key=self.probe_records._preferred_material_probe_key,
            probe_source_verdict=self.probe_records.material_probe_source_verdict,
            decode_asset=self._asset_from_payload,
            read_asset_payload=self._material_assets._asset_payload_row_by_id,
            assemble_scene_segments=self._material_assets.assemble_scene_segments,
            mark_scene_failed=self._material_assets.mark_scene_failed)
        self.execution_records = ExecutionRecordStore(self._connect,
            require_task=self._require_creation_task, job_belongs=self._lineage_job_belongs,
            decode_reference_list=_string_list_from_text, decode_payload_object=_json_object_from_text)
        self.restore_requests = TimelineRestoreRecordStore(self._connect,
            append_event=self.execution_records.append_task_event,
            get_event=self.execution_records.get_task_event,
            decode_operation=self._edit_operation_from_row, read_readiness=self.edit_execution_readiness)
        self.lineage_records = LineageRecordStore(self._connect,
            require_task=self._require_creation_task,
            job_belongs=self._lineage_job_belongs, output_producers=self.execution_records.output_producers,
            decode_reference_list=_string_list_from_text, decode_parameter_object=_json_object_from_text)
        self.topic_records = TopicRecordStore(self._connect,
            read_task=self.get_creation_task, require_task=self._require_creation_task,
            write_task=self._write_topic_task, read_plan_intent=self._topic_plan_intent,
            append_event=self.execution_records.append_task_event)
        self.script_records = ScriptRecordStore(self._connect,
            read_task=self.get_creation_task, require_task=self._require_creation_task,
            decode_plan=self._creation_plan_from_payload, read_topic=self.topic_records.get_topic_candidate,
            transition_task=self._script_task_transition, create_library_task=self._create_script_library_task,
            knowledge_inputs_current=self._knowledge_inputs_current, append_event=self.execution_records.append_task_event,
            get_event=self.execution_records.get_task_event, decode_event=self.execution_records.task_event_from_row)
        self.creation_requests = CreationRequestRecordStore(self._connect)
        self.creation_request_adoption = CreationRequestAdoptionUseCase(self._connect,
            records=self.creation_requests, decode_plan=self._creation_plan_from_payload,
            decode_task=self._creation_task_from_payload,
            prepare_plan=self.prepare_confirmed_creation_plan, insert_plan=self.insert_confirmed_creation_plan,
            read_asset=self._material_assets._asset_payload_visibility_row_by_id,
            decode_asset=self._asset_from_payload, material_eligible=material_segment_eligible,
            voice_reference=self.creation_plan_voice_reference, person_reference=self.creation_plan_person_reference,
            write_task=self._write_topic_task, topic_records=self.topic_records,
            script_records=self.script_records, execution_records=self.execution_records)


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
        return self._workspace_media_references.resolve_workspace_media_reference(workspace_id, reference, require_registered=require_registered)

    def validate_material_reference_use(self, workspace_id: str, reference: str) -> None:
        """Check current material restrictions for a source retained in artifact lineage."""
        path = self.resolve_workspace_media_reference(workspace_id, reference)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM material_assets WHERE workspace_id = ? AND file_path = ?",
                (workspace_id, str(path)),
            ).fetchone()
        if row is not None:
            eligibility=material_usage_eligibility(self._asset_from_payload(json.loads(row['payload'])),purpose='manual')
            if not eligibility.eligible:
                raise ValueError(f'{eligibility.status}：{eligibility.reason}；{eligibility.repair_action}')

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
            raise _MediaAuthorityError(
                reason="logical_media_reference_required",
                impact="任意本机路径不能作为旁白或渲染输入。",
                repair_action="从当前工作区选择 workspace:// 媒体，或使用已有 narration://artifact 资源。",
            )
        artifact = self.get_narration_artifact(
            workspace_id,
            parsed.path.removeprefix("/"),
        )
        if artifact is None:
            raise _MediaAuthorityError(
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
        return self._workspace_media_references.workspace_media_reference_for_asset(workspace_id, asset_id, connection=connection)

    def resolve_clip_media_reference(
        self,
        workspace_id: str,
        asset_id: str,
        reference: str,
        *, connection: sqlite3.Connection | None = None,
    ) -> Path:
        asset = self.get_asset(workspace_id, asset_id, connection=connection)
        eligibility=material_usage_eligibility(asset,purpose='manual')
        if not eligibility.eligible:
            raise _MediaAuthorityError(reason=f'material_{eligibility.status}',
                impact=eligibility.reason,repair_action=eligibility.repair_action)
        expected_reference = self.workspace_media_reference_for_asset(
            workspace_id,
            asset_id, connection=connection,
        )
        if reference != expected_reference:
            raise _MediaAuthorityError(
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
        return self._profile_references.resolve_profile_reference(profile, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic, for_generation=for_generation)

    def _profile_reference_source(
        self, profile: PersonProfile | VersionedVoiceProfile, *,
        viewer_user_id: str | None, local_diagnostic: bool, for_generation: bool,
        probe_media: bool = True,
    ) -> tuple[Path, float | None, float | None]:
        return self._profile_references._profile_reference_source(profile, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic, for_generation=for_generation, probe_media=probe_media)

    def profile_reference_input(self, profile: PersonProfile | VersionedVoiceProfile, *,
                                viewer_user_id: str | None = None, local_diagnostic: bool = False,
                                for_generation: bool = True) -> ProfileReferenceInput:
        return self._profile_references.profile_reference_input(profile, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic, for_generation=for_generation)

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
        return _content_readable(user_id=user_id, local_diagnostic=local_diagnostic, visibility=profile.visibility, owner_user_id=profile.owner_user_id)

    def current_profile_authorization(self, profile):
        return self.get_authorization(profile.workspace_id, profile.authorization.id) if profile.authorization else None

    def set_default_versioned_voice(self, workspace_id: str, voice_profile_id: str, version: str, *,
                                    viewer_user_id: str | None = None, local_diagnostic: bool = False) -> VersionedVoiceProfile:
        return self._profile_registration.set_default_versioned_voice(workspace_id, voice_profile_id, version, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)

    def get_default_versioned_voice(self, workspace_id: str) -> VersionedVoiceProfile | None:
        return self._profile_registration.get_default_versioned_voice(workspace_id)

    def creation_plan_voice_reference(self, workspace_id, profile_id, version, *, viewer_user_id=None, local_diagnostic=False):
        return self._profile_registration.creation_plan_voice_reference(workspace_id, profile_id, version, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)

    def automatic_material_segments(self, workspace_id, *, viewer_user_id=None, local_diagnostic=False, asset_ids=None, connection=None):
        result = []
        for asset in self.list_assets(workspace_id, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic, connection=connection):
            if asset_ids is not None and asset.id not in asset_ids:
                continue
            try:
                self.resolve_workspace_media_reference(workspace_id, self.workspace_media_reference_for_asset(workspace_id, asset.id), require_registered=True)
            except (ValueError, KeyError, OSError):
                continue
            result.extend((asset, segment) for segment in asset.segments if automatic_segment_eligible(asset, segment, workspace_id))
        return result

    def creation_plan_person_reference(self, workspace_id: str, profile_id: str, version: str, *,
                                       viewer_user_id: str | None = None, local_diagnostic: bool = False) -> _CreationPlanPersonReference:
        return self._profile_registration.creation_plan_person_reference(workspace_id, profile_id, version, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)


    def save_person_profile(
        self, person: PersonProfile, *, verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> PersonProfile:
        return self._profile_registration.save_person_profile(person, verified_owner_user_id=verified_owner_user_id, local_diagnostic=local_diagnostic)

    def capture_profile_local_check(self, conn, workspace_id, profile_refs, pronunciation_refs=(), *, pronunciation_scope=None):
        return self._profile_records.capture_local_check(conn, workspace_id, profile_refs, pronunciation_refs,
            pronunciation_scope=pronunciation_scope)

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
        return self._profile_registration.save_versioned_voice_profile(voice, verified_owner_user_id=verified_owner_user_id, local_diagnostic=local_diagnostic)

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
        return self._profile_records.save_pronunciation_entry(entry)

    def get_pronunciation_entry(
        self,
        workspace_id: str,
        entry_id: str,
        version: str | None = None,
    ) -> PronunciationEntry:
        return self._profile_records.get_pronunciation_entry(workspace_id, entry_id, version)

    def list_pronunciation_entries(self, workspace_id: str) -> list[PronunciationEntry]:
        return self._profile_records.list_pronunciation_entries(workspace_id)

    def list_pronunciation_entry_versions(
        self,
        workspace_id: str,
        entry_id: str,
    ) -> list[PronunciationEntry]:
        return self._profile_records.list_pronunciation_entry_versions(workspace_id, entry_id)

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
        return self._profile_registration.migrate_legacy_voice_profile(workspace_id=workspace_id, legacy_voice_profile_id=legacy_voice_profile_id, person_profile_id=person_profile_id, person_version=person_version, version=version, language=language, authorization=authorization, verified_owner_user_id=verified_owner_user_id, local_diagnostic=local_diagnostic)

    def list_applicable_pronunciation_entries(
        self,
        *,
        workspace_id: str,
        person_profile_id: str | None,
        episode_id: str,
    ) -> list[PronunciationEntry]:
        return self._profile_records.list_applicable_pronunciation_entries(workspace_id=workspace_id, person_profile_id=person_profile_id, episode_id=episode_id)


    def _claim_workspace_media_reference(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        reference: str,
    ) -> None:
        return self._workspace_media_references.claim_workspace_media_reference(conn, workspace_id=workspace_id, reference=reference)





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
        return self._workspace_profiles.get_profile(workspace_id)

    def save_profile(self, profile: ProductionProfile) -> ProductionProfile:
        return self._workspace_profiles.save_profile(profile)

    def list_voice_profiles(self, workspace_id: str) -> list[VoiceProfile]:
        return self._legacy_voice_profiles.list_voice_profiles(workspace_id)

    def get_voice_profile(self, workspace_id: str, voice_profile_id: str) -> VoiceProfile:
        return self._legacy_voice_profiles.get_voice_profile(workspace_id, voice_profile_id)

    def save_voice_profile(self, voice: VoiceProfile) -> VoiceProfile:
        return self._legacy_voice_profiles.save_voice_profile(voice)

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
            raise _MediaAuthorityError(
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
            raise _MediaAuthorityError(
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
            raise _MediaAuthorityError(
                reason=f"voice_authorization_{authorization_failure}",
                impact="声音授权当前不可用，未调用声音模型或复用旧配音。",
                repair_action="恢复或更新该声音的 narration 授权后重试。",
            )
        return inputs

    def get_narration_artifact(
        self,
        workspace_id: str,
        cache_key: str,
        *, connection: sqlite3.Connection | None = None,
    ) -> NarrationArtifactRecord | None:
        with nullcontext(connection) if connection is not None else self._connect() as conn:
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
            source = self.execution_records.artifact_from_row(row)
            if source.kind != "task_recording" or source.sha256 != artifact.source_sha256:
                return False
            try:
                job = self.get_job_run(artifact.workspace_id, source.producer_job_id)
                transform = next((item for item in self.lineage_records.list_transform_runs(artifact.workspace_id, artifact.task_id)
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
        return self._material_source_files.get_material_ingestion_binding()

    material_copy_revision = staticmethod(MaterialSourceFileAccess.material_copy_revision)

    def confirm_material_copy_complete(
        self, workspace_id: str, *, relative_path: str,
        expected_revision: dict[str, str], confirmation_source: Literal["manual", "app_import"],
        verified_owner_user_id: str | None = None,
        connection: sqlite3.Connection | None = None,
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
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            if connection is None:
                conn.execute("BEGIN IMMEDIATE")
            elif not conn.in_transaction:
                raise ValueError("接收确认必须加入调用方已开始的事务")
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
        return self._material_source_files.require_material_copy_confirmation(workspace_id, path)

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
        connection=None,
    ) -> list[MaterialAsset]:
        return self._material_assets.list_assets(workspace_id, include_private=include_private, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic, connection=connection)

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
        purpose: Literal['manual','automatic'] = 'manual',
        include_eligibility: bool = False,
    ) -> list[MaterialSegmentMatch] | dict:
        """Search one workspace's qualified, persisted segments and explain every candidate.

        硬过滤（workspace 隔离、用途资格、已持久化、调用方给的结构化条件）保持严格，
        任何放宽都只发生在排序上：文字匹配不上时依次降级到「分词包含」和
        「仅按结构化条件返回」，并且每条候选都带上可核对的依据。
        """
        roles = set(usage_roles or [])
        if purpose not in {'manual','automatic'}:
            raise ValueError('成片素材检索用途必须明确为人工或自动')
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
        qualification_counts = {'processing':0,'restricted':0,'input_changed':0,'source_unavailable':0}
        selected_assets = set(asset_ids) if asset_ids is not None else None
        for asset in self.list_assets(
            workspace_id,
            include_private=viewer_user_id is not None or local_diagnostic,
            viewer_user_id=viewer_user_id,
            local_diagnostic=local_diagnostic,
        ):
            if selected_assets is not None and asset.id not in selected_assets:
                continue
            qualification=material_usage_eligibility(asset,purpose=purpose)
            if not qualification.eligible:
                qualification_counts[qualification.status]+=1
                continue
            for segment in asset.segments:
                if eligible_segment_refs is not None and (asset.id, segment.id) not in eligible_segment_refs:
                    continue
                qualification=material_usage_eligibility(asset,segment,purpose=purpose)
                if not qualification.eligible:
                    qualification_counts[qualification.status]+=1
                    continue
                if not material_segment_eligible(asset,segment,workspace_id,automation_only=purpose=='automatic'):
                    qualification_counts['source_unavailable']+=1
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
                        evidence_excerpt=_material_searchable_corpus(segment, asset)[:900],
                        needs_confirmation=tier in {"partial", "structural", "semantic"},
                    )
                )
        matches.sort(key=lambda item: (-item.score, item.asset_ref, item.segment_ref))
        selected = matches[:limit]
        total = len(selected)
        ranked = [
            replace(
                match,
                rank=rank,
                reasons=[*match.reasons, f"综合排第 {rank} 名（本次候选 {total} 条）"],
            )
            for rank, match in enumerate(selected, start=1)
        ]
        if not include_eligibility:
            return ranked
        limitations = [f'{qualification_counts[status]}项{label}' for status,label in (
            ('processing','分析尚未就绪'),('restricted','受本次用途限制'),('input_changed','输入版本已变化'),
            ('source_unavailable','文件、媒体类型或源区间不可用')) if qualification_counts[status]]
        message = f'在当前身份可读取范围内，按{"自动" if purpose=="automatic" else "人工"}用途返回{len(ranked)}条候选。'
        if limitations:
            message += '当前可读素材还有'+ '、'.join(limitations)+'，未作为候选。'
        if not ranked:
            message += '没有匹配候选不代表当前范围外对象不存在；可调整筛选或沿原素材查看处理状态。'
        message += '本次只按已有文字与结构条件检索，未提交画面语义推理。'
        return {'matches':ranked,'qualification_message':message,'total_count':len(matches),'truncated':len(matches)>len(ranked)}

    def get_asset(self, workspace_id: str, asset_id: str, *, connection: sqlite3.Connection | None = None) -> MaterialAsset:
        return self._material_assets.get_asset(workspace_id, asset_id, connection=connection)

    def _asset_by_path(self, workspace_id: str, file_path: str) -> MaterialAsset:
        return self._material_assets.get_asset_by_path(workspace_id, file_path)

    def analyze_asset(self, workspace_id: str, asset_id: str, *, expected_source_digest: str | None = None) -> MaterialAsset:
        return self._material_analysis.analyze_asset(workspace_id, asset_id, expected_source_digest=expected_source_digest)

    def get_visual_feature(
        self,
        workspace_id: str,
        segment_id: str,
        model_id: str,
    ) -> list[float] | None:
        return self._material_analysis.get_visual_feature(workspace_id, segment_id, model_id)


    def set_asset_visibility(
        self, workspace_id: str, asset_id: str, visibility: ContentVisibility
    ) -> MaterialAsset:
        return self._material_assets.set_asset_visibility(workspace_id, asset_id, visibility)

    def set_creation_task_visibility(
        self, workspace_id: str, task_id: str, visibility: ContentVisibility
    ) -> _CreationTask:
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
            if _content_readable(
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














    def confirm_creation_plan(
        self,
        plan: CreationPlan,
        task: _CreationTask,
        *,
        verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
        reply_reference: dict[str, str] | None = None,
        request_id: str | None = None,
    ) -> tuple[CreationPlan, _CreationTask]:
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
        plan, task = self.prepare_confirmed_creation_plan(
            plan, task, verified_owner_user_id=verified_owner_user_id,
            local_diagnostic=local_diagnostic,
        )
        with self._connect() as conn:
            self.insert_confirmed_creation_plan(conn, plan, task)
        return self.get_creation_plan(plan.workspace_id, plan.id), self.get_creation_task(task.workspace_id, task.id)

    def confirm_creation_plan_from_reply(
        self,
        plan: CreationPlan,
        task: _CreationTask,
        *,
        reply_reference: dict[str, str],
        request_id: str,
        verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> tuple[CreationPlan, _CreationTask]:
        reference = _validated_assistant_reply_reference(reply_reference)
        if not request_id.strip():
            raise ValueError("确认方案请求身份不能为空")
        plan, task = self.prepare_confirmed_creation_plan(
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
                event = self.execution_records.task_event_from_row(existing)
                if (
                    event.event_type != "assistant_reply_plan_confirmed"
                    or event.workspace_id != plan.workspace_id
                    or event.payload.get("request_fingerprint") != request_fingerprint
                ):
                    raise ValueError("方案请求身份已用于不同的对话或方案输入")
                replay_ids = (str(event.payload["plan_id"]), str(event.payload["task_id"]))
            else:
                self.insert_confirmed_creation_plan(conn, plan, task)
                self.execution_records.append_task_event(
                    _TaskEvent(
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
    def prepare_confirmed_creation_plan(
        plan: CreationPlan,
        task: _CreationTask,
        *,
        verified_owner_user_id: str | None,
        local_diagnostic: bool,
    ) -> tuple[CreationPlan, _CreationTask]:
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

    def insert_confirmed_creation_plan(
        self, conn: sqlite3.Connection, plan: CreationPlan, task: _CreationTask,
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
            if segment is None or not material_usage_eligibility(asset,segment,purpose='manual').eligible:
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
            return self._read_creation_plan(conn, workspace_id, plan_id)

    def _read_creation_plan(self, connection, workspace_id, plan_id):
        row = connection.execute(
            """SELECT payload, visibility FROM creation_plans
            WHERE workspace_id = ? AND id = ?""",
            (workspace_id, plan_id),
        ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        data = json.loads(str(row["payload"]))
        if "visibility" in row.keys():
            data["visibility"] = str(row["visibility"])
        return self._creation_plan_from_payload(data)

    @contextmanager
    def creation_execution_read(self, connection=None):
        """Own a read snapshot, or borrow an already active owner transaction."""
        with (nullcontext(connection) if connection is not None else closing(self._connect())) as conn:
            if connection is None:
                conn.execute("BEGIN")
            elif not conn.in_transaction:
                raise ValueError("执行输入读取必须复用已开启的事务")
            yield conn

    def read_creation_execution_snapshot(self, workspace_id, task_id, *, connection=None):
        """Finish metadata, ordered body digest and count in one read transaction."""
        from app.creation.execution_input import ExecutionInputSnapshot, execution_input_digest
        with self.creation_execution_read(connection) as conn:
            task = self._require_creation_task(conn, workspace_id, task_id)
            if not task.confirmed_script_id:
                raise ValueError("请先确认实际文案")
            plan = self._read_creation_plan(conn, workspace_id, task.plan_id)
            header, fields = self.script_records.read_execution_script_metadata(conn, workspace_id, task.confirmed_script_id)
            digest = execution_input_digest(fields, plan,
                self.script_records.iter_execution_script_sections(conn, workspace_id, header.id))
            return ExecutionInputSnapshot(task, plan, header, digest)

    def iter_creation_narration_sections(self, task, script, *, connection):
        """Effective text and its audio, one row at a time in the same snapshot.

        This projection never changes the original script digest. Check orphan
        audio even when the first section still needs narration.
        """
        if not connection.in_transaction:
            raise ValueError("配音段落读取必须复用已开启的事务")
        if (script.workspace_id, script.task_id, script.id) != (
                task.workspace_id, task.id, task.confirmed_script_id):
            raise ValueError('采用的段版本不属于当前确认文案')
        orphan = connection.execute(
            'SELECT 1 FROM narration_segments n WHERE n.workspace_id=? AND n.task_id=? AND n.script_id=? '
            'AND NOT EXISTS (SELECT 1 FROM creation_task_script_sections s WHERE s.workspace_id=n.workspace_id '
            'AND s.script_id=n.script_id AND s.section_id=n.source_section_id) LIMIT 1',
            (task.workspace_id, task.id, script.id)).fetchone()
        if orphan is not None:
            raise ValueError('现有配音片段集不属于当前唯一文案段落')
        for section in self.script_records.iter_execution_script_sections(connection, task.workspace_id, script.id):
            state = self.stage_segment_state(task.workspace_id, task.id, section.id, connection=connection)
            if state is not None:
                if state['script_id'] != script.id:
                    raise ValueError('采用的段版本不属于当前确认文案')
                section = replace(section, content=state['text'])
            rows = connection.execute(
                'SELECT * FROM narration_segments WHERE workspace_id=? AND task_id=? AND script_id=? '
                'AND source_section_id=? LIMIT 2', (task.workspace_id, task.id, script.id, section.id)).fetchmany(2)
            if len(rows) > 1:
                raise ValueError('现有配音片段集不属于当前唯一文案段落')
            segment = self._narration_segment_from_row(rows[0]) if rows else None
            if segment is not None and segment.status == 'ready' and segment.text != section.content:
                raise ValueError('已保存配音片段的文字与采用正文不匹配，须先核对原产物')
            yield section, segment

    def read_creation_section_input(self, task, script, section_id, *, connection):
        """Read one actual effective section and its original audio in the caller snapshot."""
        if not connection.in_transaction:
            raise ValueError('段落规划读取必须复用已开启的事务')
        if (script.workspace_id, script.task_id, script.id) != (
                task.workspace_id, task.id, task.confirmed_script_id):
            raise ValueError('采用的段版本不属于当前确认文案')
        row = connection.execute(
            'SELECT ordinal,section_json FROM creation_task_script_sections '
            'WHERE workspace_id=? AND script_id=? AND section_id=?',
            (task.workspace_id, script.id, section_id)).fetchone()
        if row is None:
            raise ValueError('稳定段落ID不属于当前确认文案')
        section = _CreationTaskScriptSection(**json.loads(row['section_json']))
        adopted = self.stage_segment_state(task.workspace_id, task.id, section_id, connection=connection)
        if adopted is not None:
            if adopted['script_id'] != script.id:
                raise ValueError('采用的段版本不属于当前确认文案')
            section = replace(section, content=adopted['text'])
        audio_rows = connection.execute(
            'SELECT * FROM narration_segments WHERE workspace_id=? AND task_id=? AND script_id=? '
            'AND source_section_id=? LIMIT 2', (task.workspace_id, task.id, script.id, section_id)).fetchmany(2)
        if len(audio_rows) > 1:
            raise ValueError('现有配音片段不属于当前唯一文案段落')
        return {'ordinal': row['ordinal'], 'section': section, 'adopted': adopted,
            'audio': self._narration_segment_from_row(audio_rows[0]) if audio_rows else None}

    def creation_section_ids(self, task, script, *, offset, limit, connection):
        if not connection.in_transaction:
            raise ValueError('段落读取必须复用已开启的事务')
        start, stop, _ = slice(offset, offset + limit).indices(script.sections_total_count)
        return [row[0] for row in connection.execute(
            'SELECT section_id FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? '
            'ORDER BY ordinal LIMIT ? OFFSET ?', (task.workspace_id, script.id, max(0,stop-start), start))]

    def creation_section_neighbors(self, task, script, section_id, *, connection):
        row = connection.execute('SELECT ordinal FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? AND section_id=?',
            (task.workspace_id,script.id,section_id)).fetchone()
        ids = [item[0] for item in connection.execute(
            'SELECT section_id FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? '
            'AND ordinal BETWEEN ? AND ? AND section_id<>? ORDER BY ordinal',
            (task.workspace_id,script.id,row[0]-1,row[0]+1,section_id))]
        return [self.read_creation_section_input(task,script,item,connection=connection)['section'] for item in ids]

    def iter_creation_section_planning_inputs(self, task, script, *, connection):
        """Yield effective text once per section, plus scalar clocks; never collect bodies."""
        if not connection.in_transaction:
            raise ValueError('段落规划读取必须复用已开启的事务')
        cursor = connection.execute(
            'SELECT section_id FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? ORDER BY ordinal',
            (task.workspace_id, script.id))
        try:
            for row in cursor:
                current = self.read_creation_section_input(task, script, row['section_id'], connection=connection)
                audio, adopted = current['audio'], current['adopted']
                measured = (float(audio.duration_seconds) if audio is not None and audio.status == 'ready'
                    and audio.duration_seconds is not None else 0)
                window = adopted['montage'].get('delivery_window') if adopted is not None else None
                yield current['section'], measured, window
        finally:
            cursor.close()

    def creation_section_titles(self, workspace_id, task_id, section_ids, *, connection=None):
        with self.creation_execution_read(connection) as conn:
            task = self._require_creation_task(conn, workspace_id, task_id)
            return {row['section_id']: row['title'] for row in conn.execute(
                "SELECT section_id,json_extract(section_json,'$.title') AS title FROM creation_task_script_sections "
                'WHERE workspace_id=? AND script_id=? AND section_id IN (SELECT value FROM json_each(?))',
                (workspace_id, task.confirmed_script_id, json.dumps(section_ids)))}

    @staticmethod
    def _knowledge_adoption_event_id(workspace_id: str, member_id: str, request_id: str) -> str:
        return "event_knowledge_adoption_" + hashlib.sha256(
            json.dumps([workspace_id, member_id, request_id]).encode()).hexdigest()

    @staticmethod
    def _knowledge_adoption_owner(task: _CreationTask, member_id: str) -> None:
        if not member_id or not _content_readable(user_id=member_id, local_diagnostic=False,
                visibility=task.visibility, owner_user_id=task.owner_user_id):
            raise KeyError(task.id)
        if task.owner_user_id and task.owner_user_id != member_id:
            raise PermissionError("当前成员不能更改此任务的资料。")

    def get_task_knowledge_adoption(
        self, workspace_id: str, task_id: str, request_id: str, *, member_id: str,
    ) -> dict[str, object]:
        with self._connect() as conn:
            task = self._require_creation_task(conn, workspace_id, task_id)
            self._knowledge_adoption_owner(task, member_id)
            row = conn.execute("SELECT task_id,payload_json FROM task_events WHERE workspace_id=? AND id=? AND event_type='task_knowledge_adopted'",
                (workspace_id, self._knowledge_adoption_event_id(workspace_id, member_id, request_id))).fetchone()
            if row is None or row["task_id"] != task_id:
                raise KeyError(request_id)
            return json.loads(row["payload_json"])["receipt"]

    def adopt_task_knowledge(
        self, workspace_id: str, task_id: str, *, member_id: str, request_id: str,
        expected_plan_updated_at: datetime | str, expected_task_updated_at: datetime | str,
        requested_references: tuple[tuple[str, str], ...],
        resolve_references: Callable[[], list[CreationPlanKnowledgeReference]],
    ) -> dict[str, object]:
        """Append fixed inputs and their immutable receipt under one write lock."""
        if not request_id.strip():
            raise ValueError("采用资料需要稳定请求身份。")
        fingerprint = hashlib.sha256(json.dumps({"task_id": task_id, "member_id": member_id,
            "references": requested_references}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        event_id = self._knowledge_adoption_event_id(workspace_id, member_id, request_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._require_creation_task(conn, workspace_id, task_id)
            self._knowledge_adoption_owner(task, member_id)
            previous = conn.execute("SELECT task_id,payload_json FROM task_events WHERE id=?", (event_id,)).fetchone()
            if previous:
                payload = json.loads(previous["payload_json"])
                if previous["task_id"] != task_id or payload["fingerprint"] != fingerprint:
                    raise ValueError("此资料采用请求已用于不同任务或资料，未重复采用。")
                return payload["receipt"]
            row = conn.execute("SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?", (workspace_id, task.plan_id)).fetchone()
            if row is None:
                raise KeyError(task.plan_id)
            plan = self._creation_plan_from_payload(json.loads(row["payload"]))
            if (_revision_value(plan.updated_at) != _revision_value(expected_plan_updated_at)
                    or _revision_value(task.updated_at) != _revision_value(expected_task_updated_at)):
                raise ValueError("方案或任务版本已变化，请读取当前版本后重新采用资料。")
            resolved = resolve_references()
            existing = {item.id: item for item in plan.knowledge_references}
            added = []
            for item in resolved:
                if item.id in existing:
                    if existing[item.id] != item:
                        raise ValueError("任务已固定了此资料的另一版本或授权；本入口只追加，不覆盖旧引用。")
                else:
                    existing[item.id] = item
                    added.append(item)
            if added:
                if task.stage_status == "running" or conn.execute(
                    "SELECT 1 FROM job_runs WHERE workspace_id=? AND task_id=? AND status IN ('queued','running','interrupted') LIMIT 1",
                    (workspace_id, task_id)).fetchone():
                    raise ValueError("此任务仍有未结束的制作或生成作业；先等作业结算或取消，再采用资料。")
                if (task.stage not in {"topic", "script"} or task.confirmed_script_id or task.narration_cache_key
                        or task.montage_plan_id or task.current_timeline_version_id or task.output_ready):
                    raise ValueError("此任务已有采用稿或下游结果，需要明确文案修订及影响范围；本次未追加或改动原结果。")
                if len(existing) + len(plan.source_references) > 64:
                    raise ValueError("文案输入最多64份资料，请只采用本次需要的固定资料。")
            before = [asdict(item) for item in plan.knowledge_references]
            previous_status = task.stage_status
            now = utc_now()
            if added:
                plan.knowledge_references.extend(added)
                plan.updated_at = task.updated_at = now
                if task.stage == "script":
                    task.stage_status, task.status, task.recovery_point = "not_started", "running", "script"
                    task.stage_reason = task.stage_impact = task.stage_repair_action = task.stage_retryable = None
                conn.execute("UPDATE creation_plans SET payload=?,updated_at=? WHERE workspace_id=? AND id=?",
                    (_json_payload(plan), now.isoformat(), workspace_id, plan.id))
                conn.execute("UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?",
                    (_json_payload(task), now.isoformat(), workspace_id, task.id))
            retained = conn.execute("SELECT COUNT(*) FROM creation_task_scripts WHERE workspace_id=? AND task_id=?",
                (workspace_id, task_id)).fetchone()[0]
            receipt = {"request_id": request_id, "task_id": task_id, "plan_id": plan.id,
                "status": "adopted" if added else "unchanged", "recorded_at": now.isoformat(),
                "added_references": [asdict(item) for item in added], "previous_references": before,
                "knowledge_references": [asdict(item) for item in plan.knowledge_references],
                "plan_updated_at": _revision_value(plan.updated_at), "task_updated_at": _revision_value(task.updated_at),
                "retained_script_count": retained, "previous_stage_status": previous_status,
                "next_step": "generate_new_script" if added and task.stage == "script" else "unchanged" if not added else "continue_topic"}
            self.execution_records.append_task_event(_TaskEvent(event_id, workspace_id, task_id, None, "task_knowledge_adopted",
                {"fingerprint": fingerprint, "member_id": member_id, "receipt": receipt}), connection=conn)
            return receipt

    @staticmethod
    def _knowledge_inputs_current(
        conn: sqlite3.Connection, workspace_id: str, task_id: str, plan_id: str,
        input_references: list[dict[str, object]],
    ) -> bool:
        adopted = conn.execute("SELECT 1 FROM task_events WHERE workspace_id=? AND task_id=? AND event_type='task_knowledge_adopted' "
            "AND json_extract(payload_json,'$.receipt.status')='adopted' LIMIT 1", (workspace_id, task_id)).fetchone()
        if not adopted:
            return True
        row = conn.execute("SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?", (workspace_id, plan_id)).fetchone()
        planned = {(item["id"], item["version"]) for item in json.loads(row["payload"])["knowledge_references"]}
        bound = {(item["object_id"], item["version_or_identity"]) for item in input_references if item.get("kind") == "knowledge"}
        return planned.issubset(bound)

    def script_knowledge_inputs_current(self, workspace_id: str, task_id: str, script_id: str) -> bool:
        with self._connect() as conn:
            task = self._require_creation_task(conn, workspace_id, task_id)
            return self._script_knowledge_inputs_current(conn, task, script_id)

    def _script_knowledge_inputs_current(self, conn: sqlite3.Connection, task: _CreationTask, script_id: str) -> bool:
        return self.script_records._script_knowledge_inputs_current(conn, task, script_id)

    def get_creation_task(
        self,
        workspace_id: str,
        task_id: str,
        *,
        include_private: bool = True,
        connection: sqlite3.Connection | None = None,
    ) -> _CreationTask:
        if connection is None:
            with self._connect() as conn:
                return self.get_creation_task(workspace_id, task_id,
                    include_private=include_private, connection=conn)
        visibility_clause = "" if include_private else " AND visibility = 'shared'"
        row = connection.execute(
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
    ) -> list[_CreationTask]:
        """列出创作任务。给出可信读取身份时，私有任务只对上传者本人可见。

        `include_private` 是旧的粗粒度开关（本地单机用）；一旦传入可信身份，
        判据就换成与内容域同一套规则：私有按 owner 过滤，不因为「没带令牌」
        就把别人的私有任务也算进来。
        """
        if local_diagnostic or (viewer_user_id is None and include_private):
            return self._list_creation_tasks_by_visibility(workspace_id, include_private)
        return [
            task for task in self._list_creation_tasks_by_visibility(workspace_id, True)
            if _content_readable(
                user_id=viewer_user_id,
                local_diagnostic=False,
                visibility=task.visibility,
                owner_user_id=task.owner_user_id,
            )
        ]

    def _list_creation_tasks_by_visibility(
        self, workspace_id: str, include_private: bool
    ) -> list[_CreationTask]:
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
        self, version: _TimelineVersion, *, connection: sqlite3.Connection | None = None,
        expected_current_version_id: str | None | Literal[False] = False,
    ) -> _TimelineVersion:
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
        self, workspace_id: str, version_id: str, *, connection: sqlite3.Connection | None = None
    ) -> _TimelineVersion:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            row = conn.execute(
                "SELECT * FROM timeline_versions WHERE workspace_id = ? AND id = ?",
                (workspace_id, version_id),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return self._timeline_version_from_row(row)

    def next_timeline_sequence(
        self, workspace_id: str, task_id: str, *, connection: sqlite3.Connection,
    ) -> int:
        self._require_creation_task(connection, workspace_id, task_id)
        row = connection.execute(
            "SELECT MAX(sequence) FROM timeline_versions WHERE workspace_id = ? AND task_id = ?",
            (workspace_id, task_id),
        ).fetchone()
        return (row[0] or 0) + 1

    def list_timeline_versions(
        self, workspace_id: str, task_id: str
    ) -> list[_TimelineVersion]:
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

    def timeline_history_page(self, workspace_id, task_id, *, limit=20, before_sequence=None):
        return TimelineHistoryReadRepository(self._connect).page(
            workspace_id, task_id, limit=limit, before_sequence=before_sequence)

    def timeline_history_summary(self, workspace_id, task_id, version_id):
        return TimelineHistoryReadRepository(self._connect).summary(workspace_id, task_id, version_id)

    def timeline_contains_version(self, workspace_id, task_id, current_id, ancestor_id):
        with self._connect() as conn:
            return conn.execute("WITH RECURSIVE history(id,parent_version_id) AS ("
                "SELECT id,parent_version_id FROM timeline_versions WHERE workspace_id=? AND task_id=? AND id=? UNION "
                "SELECT v.id,v.parent_version_id FROM timeline_versions v JOIN history h ON h.parent_version_id=v.id "
                "WHERE v.workspace_id=? AND v.task_id=?) SELECT 1 FROM history WHERE id=? LIMIT 1",
                (workspace_id, task_id, current_id, workspace_id, task_id, ancestor_id)).fetchone() is not None


    def edit_execution_readiness(self,workspace_id,task_id,operation_id,*,connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row=conn.execute('SELECT task_id,payload_json FROM task_events WHERE workspace_id=? AND id=? '
                "AND event_type='edit_execution_readiness'",(workspace_id,f'event_{operation_id}_execution_readiness')).fetchone()
        if row is None:
            return None
        value=json.loads(row['payload_json'])
        if row['task_id']!=task_id or value['operation_id']!=operation_id:
            raise ValueError('编辑执行状态回执不属于原操作')
        return {key:value[key] for key in ('status','reason_code','device_admission')}


    def create_creation_execution_job(self, run, *, start_payload, event_type="creation_execution_started",
                                      write_guard=None, execution_source=None, source_preparation=None, budget_scope_actor=None):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            current=self.create_job_run(run,connection=conn)
            event_id=f"event_{current.id}_start"
            try:
                event=self.execution_records.get_task_event(run.workspace_id,event_id,connection=conn)
            except KeyError:
                if source_preparation is not None:
                    source_id = self.seal_creation_narration_source(current, source_preparation,
                        input_digest=run.input_digest, origin_event_id=event_id, connection=conn)
                    start_payload = {**start_payload, 'source_binding_event_id': source_id}
                event=self.execution_records.append_task_event(_TaskEvent(event_id,run.workspace_id,run.task_id,current.id,
                    event_type,start_payload,created_at=current.created_at),connection=conn)
            if {key:value for key,value in event.payload.items() if key != 'source_binding_event_id'} != {
                    key:value for key,value in start_payload.items() if key != 'source_binding_event_id'}:
                raise PermissionError("已有制作任务的原输入与身份不同")
            policy = start_payload.get('execution_budget_policy')
            if policy is not None and policy.get('policy_kind') == 'scope':
                if budget_scope_actor is None:
                    raise PermissionError('授权范围绑定需要本次可信执行身份')
                self.bind_execution_budget_scope(run.workspace_id, current.id, budget_scope_actor,
                    scope_id=policy['scope_id'], expected_revision=policy['revision'], connection=conn)
            if execution_source is not None:
                self.bind_creation_execution_source(current,execution_source,connection=conn)
            return current

    def bind_creation_execution_source(self, job, source, *, connection=None, write_guard=None):
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                return self.bind_creation_execution_source(job,source,connection=conn,write_guard=write_guard)
        if write_guard is not None:
            write_guard(connection)
        event_id=f"event_assistant_execution_locator_{source['assistant_turn_id']}"+(f"_{source['input_id']}" if source.get('input_id') else "")
        try:
            event=self.execution_records.get_task_event(job.workspace_id,event_id,connection=connection)
        except KeyError:
            return self.execution_records.append_task_event(_TaskEvent(event_id,job.workspace_id,job.task_id,job.id,
                "assistant_execution_located",{"job_id":job.id,"input_digest":job.input_digest,"source":source}),connection=connection)
        if event.job_id!=job.id or event.payload['source']!=source:
            raise ValueError("原助手输入已绑定不同制作作业")
        return event

    def create_job_run(self, run: _JobRun, *, connection: sqlite3.Connection | None = None) -> _JobRun:
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

    def get_job_run(self, workspace_id: str, job_id: str, *, connection=None) -> _JobRun:
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM job_runs WHERE workspace_id = ? AND id = ?",
                (workspace_id, job_id),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._job_run_from_row(row)

    def get_job_run_by_idempotency_key(
        self, workspace_id: str, idempotency_key: str
    ) -> _JobRun | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM job_runs
                WHERE workspace_id = ? AND idempotency_key = ?
                """,
                (workspace_id, idempotency_key),
            ).fetchone()
        return self._job_run_from_row(row) if row is not None else None

    def list_job_runs(self, workspace_id: str, task_id: str) -> list[_JobRun]:
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

    def list_pending_task_media_jobs(self, kind: str):
        with self._connect() as conn:
            pending_media = conn.execute("SELECT * FROM job_runs WHERE kind=? AND status IN ('queued','interrupted','running','cancelled')", (kind,)).fetchall()
        return (self._job_run_from_row(row) for row in pending_media)

    def list_creation_job_request_states(self, workspace_id: str, job_id: str):
        with self._connect() as conn:
            request_rows = conn.execute("SELECT id,state,unit_key,wait_reason,last_failure_reason FROM device_work_requests "
                "WHERE workspace_id=? AND owner_id=? AND owner_type='creation_job' ORDER BY created_at,id", (workspace_id, job_id)).fetchall()
        return request_rows

    def list_cancelled_creation_execution_requests(self):
        with self._connect() as conn:
            cancelled_requests = conn.execute(
                "SELECT r.id, r.workspace_id, r.member_id, r.owner_id FROM device_work_requests r "
                "JOIN job_runs j ON j.workspace_id=r.workspace_id AND j.id=r.owner_id "
                "WHERE r.owner_type='creation_job' AND j.kind='creation_task_execution' "
                "AND j.status='cancelled' AND r.state IN ('waiting', 'blocked', 'granted')"
            ).fetchall()
        return cancelled_requests

    def creation_execution_wait_row(self, workspace_id: str, member_id: str, job_id: str, input_digest: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT unit_key, wait_reason, updated_at FROM device_work_requests "
                "WHERE workspace_id=? AND member_id=? AND owner_type='creation_job' AND owner_id=? "
                "AND input_digest=? AND state IN ('waiting', 'blocked', 'granted', 'yield_requested') "
                "ORDER BY created_at DESC LIMIT 1",
                (workspace_id, member_id, job_id, input_digest),
            ).fetchone()
        return row

    def storyboard_regeneration_job(self, workspace_id: str, task_id: str, request_id: str) -> _JobRun | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND task_id=? AND idempotency_key=?",
                (workspace_id, task_id, f"storyboard-regeneration:{task_id}:{request_id}")).fetchone()
        return self._job_run_from_row(row) if row is not None else None

    def read_storyboard_regeneration_receipt(self, workspace_id: str, task_id: str, request_id: str):
        """Read job status and its committed result from the same SQLite snapshot."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND task_id=? AND idempotency_key=?",
                (workspace_id, task_id, f"storyboard-regeneration:{task_id}:{request_id}")).fetchone()
            if row is None:
                raise KeyError(request_id)
            job = self._job_run_from_row(row)
            start = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?",
                (workspace_id, f"event_{job.id}_start")).fetchone()
            if start is None:
                raise ValueError("故事板原请求缺少持久输入记录")
            result = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?",
                (workspace_id, f"event_{job.id}_storyboard_result")).fetchone()
            return job, json.loads(start["payload_json"]), json.loads(result["payload_json"]) if result else None

    def claim_storyboard_regeneration(self, run: _JobRun, payload: dict, *, verify_inputs) -> tuple[_JobRun, bool]:
        """Bind one explicit request, its original actor and JobRun atomically."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND idempotency_key=?",
                (run.workspace_id, run.idempotency_key)).fetchone()
            if row is not None:
                saved = self._job_run_from_row(row)
                event = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?",
                    (run.workspace_id, f"event_{saved.id}_start")).fetchone()
                if event is None:
                    raise ValueError("故事板原请求缺少持久输入记录")
                original = json.loads(event["payload_json"])
                if original.get("member_id") != payload.get("member_id") or any(
                    original.get(key) != payload.get(key) for key in ("identity_kind", "device_id", "user_id")
                ):
                    raise PermissionError("故事板原请求属于其他制作身份")
                if original.get("request_inputs") != payload.get("request_inputs"):
                    raise ValueError("故事板请求身份已用于不同输入")
                return saved, False
            task = self._require_creation_task(conn, run.workspace_id, run.task_id)
            if conn.execute("SELECT 1 FROM job_runs WHERE workspace_id=? AND task_id=? "
                    "AND status IN ('queued','running','interrupted') LIMIT 1",
                    (run.workspace_id, run.task_id)).fetchone():
                raise ValueError("原制作安排尚未结束；先沿现有取消入口停止并回读终态，再重新生成故事板")
            if conn.execute("SELECT 1 FROM device_work_requests r JOIN job_runs j ON j.id=r.owner_id "
                    "AND j.workspace_id=r.workspace_id WHERE j.workspace_id=? AND j.task_id=? "
                    "AND r.owner_type='creation_job' AND r.state IN ('waiting','blocked','granted','yield_requested') LIMIT 1",
                    (run.workspace_id, run.task_id)).fetchone():
                raise ValueError("原制作单元尚未释放，不能重新生成故事板")
            verify_inputs(conn, task)
            saved = self.create_job_run(run, connection=conn)
            self.execution_records.append_task_event(_TaskEvent(id=f"event_{saved.id}_start", workspace_id=saved.workspace_id,
                task_id=saved.task_id, job_id=saved.id, event_type="storyboard_regeneration_requested",
                payload=payload, created_at=saved.created_at), connection=conn)
        return saved, True

    def active_creation_request_ids(self, workspace_id: str, job_id: str):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM device_work_requests WHERE workspace_id=? AND owner_type='creation_job' "
                "AND owner_id=? AND state IN ('waiting', 'blocked', 'granted')",
                (workspace_id, job_id),
            ).fetchall()
        return rows

    def creation_job_for_device_request_row(self, workspace_id: str, request_id: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_id FROM device_work_requests "
                "WHERE id=? AND workspace_id=? AND owner_type='creation_job'",
                (request_id, workspace_id),
            ).fetchone()
        return row

    def list_pending_creation_executions(self) -> list[_JobRun]:
        """Recover only explicitly persisted execution jobs and their original workspace."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM job_runs WHERE kind = 'creation_task_execution' "
                "AND status IN ('queued', 'interrupted') ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return [self._job_run_from_row(row) for row in rows]

    def claim_job_run(self, workspace_id: str, job_id: str, *, connection: sqlite3.Connection | None = None) -> _JobRun:
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
    ) -> _JobRun:
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

    def reject_creation_execution_admission(self, workspace_id, job_id, request_id, input_digest, *,
                                           event, reason, impact, repair_action, connection):
        """Reject one ungranted fixed-input unit without pretending it executed."""
        if not connection.in_transaction:
            raise ValueError('准入拒绝须与原请求处于同一事务')
        row=connection.execute('SELECT * FROM job_runs WHERE workspace_id=? AND id=?',(workspace_id,job_id)).fetchone()
        request=connection.execute('SELECT * FROM device_work_requests WHERE workspace_id=? AND id=?',
            (workspace_id,request_id)).fetchone()
        if (row is None or request is None or row['kind']!='creation_task_execution'
                or row['status'] not in {'queued','interrupted'} or row['input_digest']!=input_digest
                or request['owner_type']!='creation_job' or request['owner_id']!=job_id
                or request['input_digest']!=input_digest or request['state'] not in {'waiting','blocked'}
                or (event.workspace_id,event.task_id,event.job_id)!=(workspace_id,row['task_id'],job_id)
                or event.payload.get('device_request_id')!=request_id or event.payload.get('input_digest')!=input_digest
                or event.payload.get('unit_key')!=request['unit_key']):
            raise PermissionError('固定输入拒绝与原请求、作业或输入摘要不一致')
        if connection.execute("SELECT 1 FROM device_work_requests WHERE workspace_id=? AND owner_type='creation_job' "
                "AND owner_id=? AND state IN ('granted','yield_requested')",(workspace_id,job_id)).fetchone():
            raise PermissionError('原作业仍有执行许可，不能按未准入结束')
        self.execution_records.append_task_event(event,connection=connection)
        now=event.created_at.isoformat()
        connection.execute("UPDATE job_runs SET status='failed',failure_reason=?,impact=?,repair_action=?,finished_at=?,updated_at=? "
            "WHERE workspace_id=? AND id=?",(reason,impact,repair_action,now,now,workspace_id,job_id))
        return self._job_run_from_row(connection.execute('SELECT * FROM job_runs WHERE workspace_id=? AND id=?',
            (workspace_id,job_id)).fetchone())

    def find_segment_revision_request(self, workspace_id, task_id, member_id, request_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM task_events WHERE workspace_id=? AND task_id=? AND event_type='segment_revision_requested' "
                "AND json_extract(payload_json,'$.member_id')=? AND json_extract(payload_json,'$.command.request_id')=? LIMIT 1",
                (workspace_id, task_id, member_id, request_id)).fetchone()
        return self.execution_records.task_event_from_row(row) if row is not None else None

    def native_available_inputs(self,workspace_id,task_id,*,offset=0,limit=30):
        query = """WITH candidates AS (
            SELECT a.id,a.logical_ref AS source_ref,a.kind AS source_kind,a.logical_ref AS name
            FROM artifact_records a JOIN job_runs j ON j.workspace_id=a.workspace_id AND j.id=a.producer_job_id
            WHERE a.workspace_id=? AND a.task_id=? AND j.status='succeeded'
            UNION ALL
            SELECT a.id,NULL AS source_ref,'material' AS source_kind,json_extract(a.payload,'$.file_name') AS name
            FROM material_assets a JOIN task_authorizations t ON t.workspace_id=a.workspace_id AND t.task_id=?
            WHERE a.workspace_id=? AND a.id IN (SELECT value FROM json_each(t.resource_scope_json,'$.assets'))
        ) """
        args = (workspace_id,task_id,task_id,workspace_id)
        with self._connect() as conn:
            total = conn.execute(query+'SELECT count(*) FROM candidates',args).fetchone()[0]
            rows = conn.execute(query+'SELECT * FROM candidates ORDER BY source_kind,id LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        values = [{'id':row['id'],'name':row['name'],'source_kind':row['source_kind'],
            'source_ref':row['source_ref'] or self.workspace_media_reference_for_asset(workspace_id,row['id'])} for row in rows]
        return {'items':values,'offset':offset,'total_count':total,'truncated':offset+len(values)<total}

    def find_task_artifact_reference(self,workspace_id,task_id,reference):
        with self._connect() as conn:
            row = conn.execute("SELECT a.* FROM artifact_records a JOIN job_runs j ON j.workspace_id=a.workspace_id AND j.id=a.producer_job_id "
                "WHERE a.workspace_id=? AND a.task_id=? AND a.logical_ref=? AND j.task_id=a.task_id AND j.status='succeeded'",
                (workspace_id,task_id,reference)).fetchone()
            return self.execution_records.artifact_from_row(row) if row else None

    def task_asset_for_path(self,workspace_id,path,allowed_ids):
        with self._connect() as conn:
            row = conn.execute('SELECT id FROM material_assets WHERE workspace_id=? AND file_path=? AND id IN (SELECT value FROM json_each(?))',
                (workspace_id,path,json.dumps(allowed_ids))).fetchone()
            return row['id'] if row else None

    def record_native_input(self,actor,origin,name,entry,*,write_guard):
        key = hashlib.sha256(json.dumps([origin,name,entry],sort_keys=True,default=str).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            write_guard(conn)
            row = conn.execute('SELECT id FROM task_events WHERE workspace_id=? AND id=?',(actor.workspace_id,'native_input_'+key)).fetchone()
            if row:
                return
            self.execution_records.append_task_event(_TaskEvent(id='native_input_'+key,workspace_id=actor.workspace_id,task_id=origin['task_id'],
                job_id=None,event_type='native_input_mapped',payload={'origin':origin,'name':name,'entry':entry}),connection=conn)

    def list_native_jobs(self,workspace_id,task_id,*,limit=30,offset=0,turn_id=None,session_id=None,unreleased_only=False,before=None,started_only=False):
        with self._connect() as conn:
            where = "j.workspace_id=? AND j.task_id=? AND j.kind IN ('native_task_command','native_task_file')"
            args = [workspace_id,task_id]
            if started_only:
                where += ' AND j.attempt>0'
            if unreleased_only:
                where += " AND j.kind='native_task_command' AND (j.status IN ('queued','running','interrupted') OR j.attempt>0) "
                where += "AND NOT (j.status='cancelled' AND j.attempt=0) AND NOT EXISTS "
                where += "(SELECT 1 FROM task_events released WHERE released.workspace_id=j.workspace_id AND released.id='event_'||j.id||'_native_released')"
            if turn_id is not None:
                where += " AND json_extract(e.payload_json,'$.origin.assistant_turn_id')=?"
                args.append(turn_id)
            if session_id is not None:
                where += " AND json_extract(e.payload_json,'$.origin.session_id')=?"
                args.append(session_id)
            if before is not None:
                where += ' AND (j.created_at<? OR (j.created_at=? AND j.id<?))'
                args.extend([before[0],before[0],before[1]])
            join = " FROM job_runs j JOIN task_events e ON e.workspace_id=j.workspace_id AND e.id='event_'||j.id||'_start' WHERE "+where
            count = conn.execute('SELECT count(*)'+join,args).fetchone()[0]
            rows = conn.execute('SELECT j.*'+join+' ORDER BY j.created_at DESC,j.id LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
            return [self._job_run_from_row(row) for row in rows],count

    def create_native_operation(self,actor,origin,request_id,operation,arguments,resources,*,write_guard):
        identity = {'operation':operation,'arguments':arguments,'origin':origin,'resources':resources}
        input_digest = hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
        run = _JobRun(id=f'job_{uuid4().hex}',workspace_id=actor.workspace_id,task_id=origin['task_id'],
            kind='native_task_file' if operation=='native_task_file' else 'native_task_command',input_digest=input_digest,
            idempotency_key=f'native:{origin["task_id"]}:{actor.member_id}:{request_id}')
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            write_guard(conn)
            task = self._require_creation_task(conn,actor.workspace_id,origin['task_id'])
            if task.owner_user_id!=(actor.user_id or actor.member_id):
                raise PermissionError('通用执行提交时任务归属不一致')
            job = self.create_job_run(run,connection=conn)
            event = _TaskEvent(id=f'event_{job.id}_start',workspace_id=job.workspace_id,task_id=job.task_id,
                job_id=job.id,event_type='native_operation_submitted',payload={**actor.identity_payload(),
                    'request_id':request_id,**identity},created_at=job.created_at)
            self.execution_records.append_task_event(event,connection=conn)
        return job,event

    def complete_native_operation(self,job,result,*,status,connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            if connection is None:
                conn.execute('BEGIN IMMEDIATE')
            current = self.get_job_run(job.workspace_id,job.id,connection=conn)
            if current.status=='queued' and status!='cancelled':
                self.claim_job_run(job.workspace_id,job.id,connection=conn)
            event = _TaskEvent(id=f'event_{job.id}_native_result',workspace_id=job.workspace_id,task_id=job.task_id,
                job_id=job.id,event_type='native_operation_result',payload=result)
            self.execution_records.append_task_event(event,connection=conn)
            self.finish_job_run(job.workspace_id,job.id,status=status,connection=conn,
                output_refs=[item['logical_ref'] for item in result.get('artifacts',[])],
                failure_reason=result.get('error') or f'native_operation_{status}' if status!='succeeded' else None,
                impact='原任务已采用成果保留' if status!='succeeded' else None,
                repair_action='读取原执行及实际停止/资源释放回执后继续' if status!='succeeded' else None)
            return event.id

    def native_operation_result(self,workspace_id,job_id,*,connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute("SELECT json_remove(payload_json,'$.work_outputs') FROM task_events WHERE workspace_id=? AND id=?",
                (workspace_id,f'event_{job_id}_native_result')).fetchone()
            if row is None:
                return None
            value = json.loads(row[0])
            released = conn.execute('SELECT id FROM task_events WHERE workspace_id=? AND id=?',
                (workspace_id,f'event_{job_id}_native_released')).fetchone()
            return {**value,**({'release_state':'released'} if released else {})}

    def native_output_window(self,workspace_id,job_id,*,offset=0,limit=30):
        with self._connect() as conn:
            row = conn.execute("SELECT json_array_length(payload_json,'$.work_outputs') FROM task_events WHERE workspace_id=? AND id=?",
                (workspace_id,f'event_{job_id}_native_result')).fetchone()
            count = row[0] or 0 if row else 0
            values = conn.execute("SELECT output.value FROM task_events e,json_each(e.payload_json,'$.work_outputs') output "
                "WHERE e.workspace_id=? AND e.id=? ORDER BY cast(output.key AS INTEGER) LIMIT ? OFFSET ?",
                (workspace_id,f'event_{job_id}_native_result',limit,offset)).fetchall()
            return {'items':[json.loads(item[0]) for item in values],'total_count':count,'truncated':offset+len(values)<count}

    def native_output_for_path(self,workspace_id,job_id,path):
        with self._connect() as conn:
            row = conn.execute("SELECT output.value FROM task_events e,json_each(e.payload_json,'$.work_outputs') output "
                "WHERE e.workspace_id=? AND e.id=? AND json_extract(output.value,'$.path')=? LIMIT 1",
                (workspace_id,f'event_{job_id}_native_result',path)).fetchone()
            return json.loads(row[0]) if row else None

    def settle_native_execution(self,grant,*,elapsed_seconds,output_bytes,connection):
        job = self.get_job_run(grant.workspace_id,grant.owner_id,connection=connection)
        self.execution_records.append_task_event(_TaskEvent(id=f'event_{job.id}_native_usage_{grant.generation}',workspace_id=job.workspace_id,
            task_id=job.task_id,job_id=job.id,event_type='task_execution_unit_settled',payload={
                'usage_id':f'{grant.request_id}:{grant.generation}','request_id':grant.request_id,'permit_generation':grant.generation,
                'elapsed_seconds':elapsed_seconds,'output_bytes':output_bytes,
                'output_coverage':'unknown' if output_bytes is None else 'known','usage_source':'local_retained_output_measurement'}),connection=connection)

    def mark_native_released(self,job):
        return self.execution_records.append_task_event(_TaskEvent(id=f'event_{job.id}_native_released',workspace_id=job.workspace_id,
            task_id=job.task_id,job_id=job.id,event_type='native_execution_released',payload={'release_state':'released'}))

    def create_segment_revision_job(self, task, actor, command, rules_check, *, write_guard=None, budget_policy=None):
        from app.creation.narration_pcm import prepare_narration_source
        plan = self.get_creation_plan(task.workspace_id, task.plan_id)
        with prepare_narration_source(self, task, plan, actor) as prepared:
            return self._create_segment_revision_job(task, actor, command, rules_check,
                prepared, write_guard=write_guard, budget_policy=budget_policy)

    def _create_segment_revision_job(self, task, actor, command, rules_check, prepared, *, write_guard=None, budget_policy=None):
        encoded = json.dumps(command, ensure_ascii=False, sort_keys=True, default=str)
        input_digest = hashlib.sha256(encoded.encode()).hexdigest()
        run = _JobRun(id=f'job_{uuid4().hex}', workspace_id=task.workspace_id, task_id=task.id,
            kind='creation_task_segment_revision', input_digest=input_digest,
            idempotency_key=f'segment-revision:{task.id}:{actor.member_id}:{command["request_id"]}')
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if write_guard is not None:
                write_guard(conn)
            current = self._require_creation_task(conn, task.workspace_id, task.id)
            if current.owner_user_id != (actor.user_id or actor.member_id):
                raise PermissionError('局部修改的任务归属已变化')
            script = self.effective_creation_script(task.workspace_id, task.id, connection=conn)
            section = next((item for item in script.sections if item.id == command['section_id']), None)
            if (section is None or script.id != command['script_id']
                    or self.stage_section_version(task.workspace_id, task.id, section.id, connection=conn) != command['expected_segment_version']
                    or hashlib.sha256(section.content.encode()).hexdigest() != command['source_text_sha256']):
                raise ValueError('提交前段版本已变化，原采用成果保持不变')
            job = self.create_job_run(run, connection=conn)
            if job.id != run.id:
                return job  # The concurrent original commit owns its baseline.
            source_id = self.seal_creation_narration_source(job, prepared, input_digest=input_digest,
                origin_event_id=f'event_{job.id}_start', connection=conn)
            payload = {**actor.identity_payload(), 'command': command, 'rules_check': rules_check,
                'input_digest': input_digest, 'script_id': command['script_id'], 'plan_id': current.plan_id, 'source_binding_event_id': source_id,
                **({'execution_budget_policy': budget_policy} if budget_policy is not None else {})}
            self.execution_records.append_task_event(_TaskEvent(id=f'event_{job.id}_start', workspace_id=job.workspace_id,
                task_id=job.task_id, job_id=job.id, event_type='segment_revision_requested', payload=payload,
                created_at=job.created_at), connection=conn)
            if budget_policy is not None and budget_policy.get('policy_kind') == 'scope':
                self.bind_execution_budget_scope(job.workspace_id, job.id, actor, scope_id=budget_policy['scope_id'],
                    expected_revision=budget_policy['revision'], connection=conn)
        return job

    def list_segment_revision_requests(self, workspace_id, task_id, member_id, *, limit=30, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            args = (workspace_id, task_id, member_id)
            where = "workspace_id=? AND task_id=? AND event_type='segment_revision_requested' AND json_extract(payload_json,'$.member_id')=?"
            count = conn.execute('SELECT count(*) FROM task_events WHERE '+where, args).fetchone()[0]
            rows = conn.execute('SELECT * FROM task_events WHERE '+where+' ORDER BY created_at DESC LIMIT ?', (*args,limit)).fetchall()
            values = []
            for row in rows:
                event = self.execution_records.task_event_from_row(row)
                job = self.get_job_run(workspace_id, event.job_id, connection=conn)
                result = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                    "AND event_type='segment_publication' ORDER BY created_at DESC LIMIT 1", (workspace_id,job.id)).fetchone()
                waiting = conn.execute("SELECT wait_reason,last_failure_reason FROM device_work_requests WHERE workspace_id=? "
                    "AND owner_type='creation_job' AND owner_id=? ORDER BY created_at DESC,id DESC LIMIT 1",(workspace_id,job.id)).fetchone()
                values.append({'request_id':event.payload['command']['request_id'], 'input_id':event.payload['command']['input_id'],
                    'section_id':event.payload['command']['section_id'], 'job_id':job.id, 'status':job.status,
                    'result':json.loads(result[0]) if result else None,
                    'waiting_reason':job.failure_reason or (waiting['wait_reason'] or waiting['last_failure_reason'] if waiting else 'queue_submission_pending')})
        return {'items':values,'total_count':count,'truncated':len(values)<count}

    def save_segment_video_preparation(self,event, *, write_guard=None):
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if write_guard is not None:
                write_guard(conn)
            current = self.stage_section_version(event.workspace_id,event.task_id,event.payload['section_id'],connection=conn)
            if current != event.payload['expected_segment_version']:
                raise ValueError('保存视频准备前段落版本已变化')
            self.execution_records.append_task_event(event,connection=conn)

    def start_segment_unit(self, grant):
        from app.device_scheduler import RecoveryBlocked
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            pending = conn.execute("SELECT s.id FROM task_events s WHERE s.workspace_id=? AND s.job_id=? "
                "AND s.event_type='task_execution_unit_started' AND NOT EXISTS (SELECT 1 FROM task_events e "
                "WHERE e.workspace_id=s.workspace_id AND e.job_id=s.job_id AND e.event_type='task_execution_unit_settled' "
                "AND json_extract(e.payload_json,'$.usage_id')=json_extract(s.payload_json,'$.usage_id')) LIMIT 1",
                (grant.workspace_id,grant.owner_id)).fetchone()
            if pending:
                raise RecoveryBlocked('原段落执行的用量未知，保留原请求待核查')
            job = self.claim_job_run(grant.workspace_id,grant.owner_id,connection=conn)
            event = _TaskEvent(id=f'event_{job.id}_unit_start_{grant.generation}', workspace_id=job.workspace_id,
                task_id=job.task_id,job_id=job.id,event_type='task_execution_unit_started',payload={
                    'usage_id':f'{grant.request_id}:{grant.generation}','request_id':grant.request_id,
                    'permit_generation':grant.generation,'unit_key':grant.unit_key})
            self.execution_records.append_task_event(event,connection=conn)
        return job

    def resume_segment_job(self,workspace_id,job_id,*,connection):
        pending = connection.execute("SELECT 1 FROM task_events s WHERE s.workspace_id=? AND s.job_id=? "
            "AND s.event_type='task_execution_unit_started' AND NOT EXISTS (SELECT 1 FROM task_events e WHERE "
            "e.workspace_id=s.workspace_id AND e.job_id=s.job_id AND e.event_type='task_execution_unit_settled' "
            "AND json_extract(e.payload_json,'$.usage_id')=json_extract(s.payload_json,'$.usage_id')) LIMIT 1",(workspace_id,job_id)).fetchone()
        if pending:
            raise ValueError('原段落用量未核对，不能重启原请求')
        changed = connection.execute("UPDATE job_runs SET status='interrupted',failure_reason=NULL,impact=NULL,repair_action=NULL,"
            "finished_at=NULL,updated_at=? WHERE workspace_id=? AND id=? AND status IN ('failed','cancelled') "
            "AND kind IN ('creation_task_execution','creation_task_segment_revision')",(utc_now().isoformat(),workspace_id,job_id)).rowcount
        if changed!=1:
            raise ValueError('原段落作业不是已释放的可恢复状态')

    def settle_segment_unit(self, grant, *, elapsed_seconds, output_bytes, connection):
        if elapsed_seconds < 0 or output_bytes < 0 or not math.isfinite(elapsed_seconds):
            raise ValueError('段落计量必须来自本次执行的非负实测值')
        job = self.get_job_run(grant.workspace_id,grant.owner_id,connection=connection)
        event = _TaskEvent(id=f'event_{job.id}_unit_settle_{grant.generation}',workspace_id=job.workspace_id,
            task_id=job.task_id,job_id=job.id,event_type='task_execution_unit_settled',payload={
                'usage_id':f'{grant.request_id}:{grant.generation}','request_id':grant.request_id,
                'permit_generation':grant.generation,'unit_key':grant.unit_key,
                'elapsed_seconds':elapsed_seconds,'output_bytes':output_bytes,'usage_source':'local_execution_measurement'})
        self.execution_records.append_task_event(event,connection=connection)

    def checkpoint_segment_unit(self, grant, *, status, connection, reason=None, payload=None):
        job = self.get_job_run(grant.workspace_id,grant.owner_id,connection=connection)
        if status == 'queued':
            changed = connection.execute("UPDATE job_runs SET status='queued',updated_at=? WHERE workspace_id=? AND id=? AND status='running'",
                (utc_now().isoformat(),job.workspace_id,job.id)).rowcount
            if changed != 1:
                raise ValueError('原段落作业不可继续提交')
        elif not (status=='cancelled' and job.status=='cancelled'):
            self.finish_job_run(job.workspace_id,job.id,status=status,connection=connection,
                failure_reason=reason,impact='原已采用有声版本保留' if reason else None,
                repair_action='核查原请求及执行回执后恢复' if reason else None)
        event = _TaskEvent(id=f'event_{job.id}_unit_checkpoint_{grant.generation}',workspace_id=job.workspace_id,
            task_id=job.task_id,job_id=job.id,event_type='segment_execution_checkpoint',
            payload={'status':status,'reason':reason,'request_id':grant.request_id,'unit_key':grant.unit_key,**(payload or {})})
        self.execution_records.append_task_event(event,connection=connection)
        return event.id

    def _iter_section_change_segments(self, task, replacement, *, connection, adopted):
        """Validate the complete set one row at a time; replace only the target."""
        if not connection.in_transaction:
            raise ValueError('局部联动必须复用已开启的事务')
        header, _ = self.script_records.read_execution_script_metadata(
            connection, task.workspace_id, task.confirmed_script_id)
        if (header.task_id != task.id or header.selection_status != 'confirmed'
                or (replacement.workspace_id, replacement.task_id, replacement.script_id) !=
                (task.workspace_id, task.id, header.id)):
            raise ValueError('配音分段与当前确认文案谱系不一致')
        count = connection.execute('SELECT COUNT(*),COUNT(DISTINCT source_section_id) FROM narration_segments '
            'WHERE workspace_id=? AND task_id=? AND script_id=?', (task.workspace_id, task.id, header.id)).fetchone()
        orphan = connection.execute('SELECT 1 FROM narration_segments n WHERE n.workspace_id=? AND n.task_id=? '
            'AND n.script_id=? AND NOT EXISTS (SELECT 1 FROM creation_task_script_sections s WHERE '
            's.workspace_id=n.workspace_id AND s.script_id=n.script_id AND s.section_id=n.source_section_id) LIMIT 1',
            (task.workspace_id, task.id, header.id)).fetchone()
        if not header.sections_total_count or tuple(count) != (header.sections_total_count, header.sections_total_count) or orphan:
            raise ValueError('已持久化的配音分段集不完整或不唯一，不能覆盖')
        target = self.read_creation_section_input(task, header, replacement.source_section_id, connection=connection)['audio']
        if target is None or target.id != replacement.id or target.created_at != replacement.created_at:
            raise ValueError('配音分段稳定 ID 或创建时间不能覆盖')
        if adopted and replacement.updated_at < target.updated_at:
            raise ValueError('不能用过期状态覆盖配音分段')
        recorded = replacement.source_kind == 'recorded' or connection.execute(
            "SELECT 1 FROM narration_segments WHERE workspace_id=? AND task_id=? AND script_id=? "
            "AND source_section_id<>? AND source_kind='recorded' LIMIT 1",
            (task.workspace_id, task.id, header.id, replacement.source_section_id)).fetchone() is not None
        script_digest = None
        if recorded:
            checksum = hashlib.sha256()
            checksum.update(('{"script_id": ' + json.dumps(header.id, ensure_ascii=False) + ', "sections": [').encode())
            for index, section in enumerate(self.script_records.iter_execution_script_sections(connection, task.workspace_id, header.id)):
                if index:
                    checksum.update(b', ')
                checksum.update(json.dumps({'id': section.id, 'content': section.content},
                    ensure_ascii=False, sort_keys=True).encode())
            checksum.update(b']}')
            script_digest = checksum.hexdigest()
        cursor = connection.execute('SELECT * FROM narration_segments WHERE workspace_id=? AND task_id=? AND script_id=? '
            'ORDER BY created_at ASC,id ASC', (task.workspace_id, task.id, header.id))
        try:
            for row in cursor:
                current = self._narration_segment_from_row(row)
                segment = replacement if current.source_section_id == replacement.source_section_id else current
                _validate_narration_segment(segment)
                section = self.read_creation_section_input(task, header, segment.source_section_id, connection=connection)['section']
                if (adopted or segment.source_section_id != replacement.source_section_id) and segment.text != section.content:
                    raise ValueError('配音分段文字必须与已采用文案段落一致')
                if segment.source_kind == 'recorded' and segment.source_script_digest != script_digest:
                    raise ValueError('电脑录音必须绑定当前确认文案内容')
                yield segment
        finally:
            cursor.close()

    def read_section_change_inputs(self, task, replacement, *, connection):
        """Keep one real final snapshot body plus ordered audio metadata, not full DTO copies."""
        header, _ = self.script_records.read_execution_script_metadata(
            connection, task.workspace_id, task.confirmed_script_id)
        snapshot_rows, audio = [], {}
        for segment in self._iter_section_change_segments(task, replacement, connection=connection, adopted=False):
            snapshot_rows.append(json.loads(json.dumps(asdict(segment), default=str)))
            audio[segment.source_section_id] = {'section_id': segment.source_section_id, 'narration_segment_id': segment.id,
                'audio_reference': segment.audio_reference, 'duration_seconds': segment.duration_seconds, 'status': segment.status}
        ordered = [audio[row[0]] for row in connection.execute('SELECT section_id FROM creation_task_script_sections '
            'WHERE workspace_id=? AND script_id=? ORDER BY ordinal', (task.workspace_id, header.id))]
        return {'script_header': header, 'narration_snapshot': snapshot_rows, 'audio': ordered}

    def replace_adopted_narration_segment(self, task, replacement, *, connection):
        for _ in self._iter_section_change_segments(task, replacement, connection=connection, adopted=True):
            pass
        # Validation above preserves the complete-set contract, while this write
        # cannot insert, overwrite another segment, or rewrite unrelated rows.
        connection.execute('UPDATE narration_segments SET text=?,audio_reference=?,duration_seconds=?,sample_rate=?,model_id=?, '
            'voice_profile_id=?,voice_version=?,language=?,pronunciation_version=?,status=?,updated_at=?,source_kind=?, '
            'source_artifact_ref=?,source_sha256=?,source_script_digest=? '
            'WHERE workspace_id=? AND task_id=? AND script_id=? AND source_section_id=? AND id=?',
            (replacement.text,replacement.audio_reference,replacement.duration_seconds,replacement.sample_rate,replacement.model_id,
             replacement.voice_profile_id,replacement.voice_version,replacement.language,replacement.pronunciation_version,
             replacement.status,replacement.updated_at.isoformat(),replacement.source_kind,replacement.source_artifact_ref,
             replacement.source_sha256,replacement.source_script_digest,task.workspace_id,task.id,task.confirmed_script_id,
             replacement.source_section_id,replacement.id))
        return replacement

    def publish_segment_version(self, event, artifacts, transform, edges, *, adopted, expected_version_id,
                                expected_segment_version, segment=None, timeline_change=None, connection):
        task = self._require_creation_task(connection,event.workspace_id,event.task_id)
        payload = event.payload
        if self.stage_section_version(task.workspace_id,task.id,payload['section_id'],connection=connection) != expected_segment_version:
            raise ValueError('发布前段落或时间线已变化；原采用版本保留')
        for artifact in artifacts:
            self.execution_records.create_artifact_record(artifact,connection=connection)
        self.lineage_records.create_transform_run(transform,connection=connection)
        for edge in edges:
            self.lineage_records.create_dependency_edge(edge,connection=connection)
        self.execution_records.append_task_event(event,connection=connection)
        if adopted:
            row = connection.execute('SELECT snapshot_json,sequence FROM timeline_versions WHERE workspace_id=? AND id=?',
                (task.workspace_id,task.current_timeline_version_id)).fetchone()
            snapshot = json.loads(row['snapshot_json']) if row else {}
            snapshot['stage_segments'] = {**snapshot.get('stage_segments',{}),payload['section_id']:event.id}
            invalidations = dict(snapshot.get('stage_segment_invalidations', {}))
            invalidations.pop(payload['section_id'], None)
            snapshot['stage_segment_invalidations'] = invalidations
            # A local change invalidates the composed output while preserving its immutable ancestor.
            for key in ('rendered_video_ref','rendered_artifact_id','creation_render_request_id','output_refs'):
                snapshot.pop(key,None)
            operation = None
            tracks = []
            if segment is not None:
                header, _ = self.script_records.read_execution_script_metadata(connection, task.workspace_id, payload['script_id'])
                before = self.read_creation_section_input(task, header, payload['section_id'], connection=connection)['audio']
                if before is None:
                    raise ValueError('局部修订缺少原配音分段')
                before_state = self.stage_segment_state(task.workspace_id,task.id,payload['section_id'],connection=connection)
                if before_state is None or task.current_timeline_version_id is None:
                    raise ValueError('局部修订缺少原采用段及历史基础，未覆盖原成果')
                history = {'section_id':payload['section_id'],'before_segment':asdict(before),'after_segment':asdict(segment),
                    'before_publication':before_state['segment_version'],'after_publication':event.id,
                    'request_id':payload['request_id'],'input_id':payload['input_id'],
                    'before_dependencies':self.section_dependency_snapshot(task.workspace_id,task.id,payload['section_id'],connection=connection)}
                history = json.loads(json.dumps(history,default=lambda value:value.isoformat()))
                result_version_id = f'timeline_{uuid4().hex}'
                history['before_timeline_version_id'] = task.current_timeline_version_id
                history['after_timeline_version_id'] = result_version_id
                if timeline_change is None:
                    raise ValueError('局部修改缺少同源时间线联动负责人')
                snapshot,tracks = timeline_change(task,snapshot,payload['section_id'],payload['montage'],segment,
                    result_version_id,self.get_job_run(task.workspace_id,event.job_id,connection=connection),connection)
                operation_id = f'operation_{uuid4().hex}'
                history['operation_id'] = operation_id
                intent = _EditIntent(id=f'intent_{uuid4().hex}',workspace_id=task.workspace_id,task_id=task.id,
                    raw_command='受约束局部文案修订',context_stage='narration',selected_object_refs=[f'section:{payload["section_id"]}'],
                    playhead_ms=None,intent_type='revise_script_section',scope={'section_id':payload['section_id']},
                    constraints={},confidence=1.0,status='interpreted')
                self.create_edit_intent(intent,connection=connection,source=None)
                proposal = _EditProposal(id=f'proposal_{uuid4().hex}',workspace_id=task.workspace_id,task_id=task.id,
                    intent_id=intent.id,summary='更新目标段的正文、配音、字幕及镜头',target_refs=[f'section:{payload["section_id"]}'],
                    operation={'operation_type':'ReviseScriptSection','payload':history},invalidated_refs=[],preview={},external_effects=[])
                self.create_edit_proposal(proposal,connection=connection)
                applied_at = utc_now()
                operation = _EditOperation(id=operation_id,workspace_id=task.workspace_id,task_id=task.id,proposal_id=proposal.id,
                    operation_type='ReviseScriptSection',payload=history,base_version_id=task.current_timeline_version_id,
                    result_version_id=result_version_id,status='applied',created_at=applied_at,applied_at=applied_at)
            version = _TimelineVersion(id=f'timeline_{uuid4().hex}',workspace_id=task.workspace_id,task_id=task.id,
                parent_version_id=task.current_timeline_version_id,sequence=row['sequence']+1 if row else 1,
                snapshot=snapshot,created_by_operation_id=None)
            if operation is not None:
                version = replace(version,id=operation.result_version_id,created_by_operation_id=operation.id)
                full = snapshot.get('montage')
                self.edit_adoption.confirm(task.workspace_id,task.id,proposal.id,operation=operation,result_version=version,
                    montage_plan_id=full['id'] if full else None,montage_payload=full,subtitle_tracks=tracks,connection=connection)
            else:
                self.create_timeline_version(version,connection=connection,expected_current_version_id=task.current_timeline_version_id)
            if segment is not None:
                self.replace_adopted_narration_segment(task, segment, connection=connection)
                self._invalidate_section_composition(task.workspace_id,task.id,connection)
                self.execution_records.append_task_event(_TaskEvent(id=f'event_{operation.id}_dependencies',workspace_id=task.workspace_id,
                    task_id=task.id,job_id=event.job_id,event_type='section_edit_dependencies',payload={
                    'operation_id':operation.id,'dependencies':self.section_dependency_snapshot(task.workspace_id,task.id,
                    payload['section_id'],connection=connection)}),connection=connection)
        return event.id

    def _invalidate_section_composition(self,workspace_id,task_id,connection):
        task = self._require_creation_task(connection,workspace_id,task_id)
        task.narration_cache_key = None
        task.output_ready = False
        task.stage,task.stage_status,task.status = 'narration','not_started','running'
        task.recovery_point = 'narration'
        task.updated_at = utc_now()
        connection.execute('UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?',
            (_json_payload(task),task.updated_at.isoformat(),workspace_id,task_id))
        return task

    def _timeline_restore_business_facts(self,version,connection):
        snapshot = dict(version.snapshot)
        for field in ('edit_operation_id','invalidated_refs'):
            snapshot.pop(field,None)
        if isinstance(snapshot.get('montage'),dict):
            snapshot['montage'] = {key:value for key,value in snapshot['montage'].items()
                if key not in {'created_at','updated_at','message'}}
        if isinstance(snapshot.get('narration_segments'),list):
            snapshot['narration_segments'] = [{key:value for key,value in row.items()
                if key not in {'created_at','updated_at'}} for row in snapshot['narration_segments']]
        record_fields = {'id','workspace_id','task_id','timeline_version_id','created_at','updated_at'}
        tracks = self.list_subtitle_tracks(version.workspace_id,version.task_id,timeline_version_id=version.id,connection=connection)
        track_facts = [{key:value for key,value in asdict(track).items() if key not in record_fields} for track in tracks]
        declared_tracks = snapshot.pop('subtitle_track_ids',None)
        snapshot['subtitles'] = {'items':sorted(track_facts,key=lambda item:json.dumps(item,sort_keys=True)),
            'explicit_empty':declared_tracks==[] and not track_facts}
        cover_id = snapshot.pop('cover_asset_id',None)
        cover = None
        if cover_id:
            row = connection.execute('SELECT payload_json FROM cover_assets WHERE workspace_id=? AND task_id=? AND id=?',
                (version.workspace_id,version.task_id,cover_id)).fetchone()
            if row is None:
                raise ValueError('历史封面记录缺失，不能证明整片恢复安全')
            cover = {key:value for key,value in json.loads(row[0]).items() if key not in record_fields}
        snapshot['cover'] = cover
        return snapshot

    def verify_whole_timeline_restore(self,workspace_id,task_id,changed_version_id,*,connection=None):
        """The legacy snapshot restore is safe only while no later facts are lost."""
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            task = self._require_creation_task(conn,workspace_id,task_id)
            changed = self.get_timeline_version(workspace_id,changed_version_id,connection=conn)
            current = self.get_timeline_version(workspace_id,task.current_timeline_version_id,connection=conn)
            if (changed.task_id!=task_id or self._timeline_restore_business_facts(current,conn)
                    !=self._timeline_restore_business_facts(changed,conn)):
                raise ValueError('此修改之后已有其他时间线成果；当前编辑尚不支持局部反向恢复，不能覆盖后来成果')
            if 'narration_segments' in changed.snapshot:
                rows = self.list_narration_segments(workspace_id,task_id,task.confirmed_script_id,connection=conn)
                def facts(items):
                    return [{key:value for key,value in item.items() if key not in {'created_at','updated_at'}} for item in items]
                if facts([asdict(row) for row in rows])!=facts(changed.snapshot['narration_segments']):
                    raise ValueError('此修改之后已有新的配音成果；不能用旧整片快照覆盖，请保留当前成果并核对目标修改')
            if 'narration_cache_key' in changed.snapshot and task.narration_cache_key!=changed.snapshot['narration_cache_key']:
                raise ValueError('此修改之后已有新的配音总轨；不能用旧整片快照覆盖')

    def restore_script_section(self,workspace_id,task_id,*,history,expected_dependencies,redo,
                               changed_version_id,restore_version_id,timeline_change,job,control_payload=None,write_guard=None,
                               execution_readiness_write=None):
        """Restore one edit on the latest facts, with one owner and one SQLite commit."""
        side,expected = ('after','before') if redo else ('before','after')
        section_id = history['section_id']
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if write_guard is not None:
                write_guard(conn)
            task = self._require_creation_task(conn,workspace_id,task_id)
            state = self.stage_segment_state(workspace_id,task_id,section_id,connection=conn)
            if state is None or state['segment_version'] != history[f'{expected}_publication']:
                raise ValueError('目标段在修改后已变化，不能覆盖新的采用成果')
            actual = self.section_dependency_snapshot(workspace_id,task_id,section_id,connection=conn)
            if json.dumps(actual,sort_keys=True,default=str) != json.dumps(expected_dependencies,sort_keys=True,default=str):
                raise ValueError('目标段的实际音画、字幕或来源依赖已变化，请先核对冲突')
            publication = self.execution_records.get_task_event(workspace_id,history[f'{side}_publication'],connection=conn)
            if publication.task_id != task_id or publication.event_type != 'segment_publication':
                raise ValueError('历史段版本不属于当前任务')
            values = dict(history[f'{side}_segment'])
            for key in ('created_at','updated_at'):
                values[key] = datetime.fromisoformat(values[key])
            segment = _NarrationSegment(**values)
            segment = replace(segment,updated_at=utc_now())
            if publication.payload['narration_reference'] != segment.audio_reference or publication.payload['text'] != segment.text:
                raise ValueError('历史正文、配音与采用段来源不一致')
            current = self.get_timeline_version(workspace_id,task.current_timeline_version_id,connection=conn)
            snapshot = dict(current.snapshot)
            snapshot['stage_segments'] = {**snapshot.get('stage_segments',{}),section_id:publication.id}
            version_id = f'timeline_{uuid4().hex}'
            persisted_job = self.get_job_run(workspace_id,job.id,connection=conn)
            if persisted_job.task_id!=task_id or persisted_job.kind!='section_timeline_restore' or persisted_job.status!='running':
                raise ValueError('局部恢复作业不属于当前运行请求')
            source_version = self.get_timeline_version(workspace_id,history[f'{expected}_timeline_version_id'],connection=conn)
            desired_version = self.get_timeline_version(workspace_id,history[f'{side}_timeline_version_id'],connection=conn)
            if source_version.task_id!=task_id or desired_version.task_id!=task_id:
                raise ValueError('局部恢复来源版本不属于当前任务')
            invalidations = dict(snapshot.get('stage_segment_invalidations', {}))
            desired_invalidation = desired_version.snapshot.get('stage_segment_invalidations', {}).get(section_id)
            if desired_invalidation is not None and desired_invalidation['publication_id'] == publication.id:
                invalidations[section_id] = desired_invalidation
            else:
                invalidations.pop(section_id, None)
            snapshot['stage_segment_invalidations'] = invalidations
            snapshot,tracks = timeline_change.restore(task,snapshot,section_id,publication.payload['montage'],segment,
                version_id,job,conn,source_version=source_version,desired_version=desired_version)
            action = '重做' if redo else '撤销'
            operation_type = 'RedoTimelineVersion' if redo else 'UndoTimelineVersion'
            intent = self.create_edit_intent(_EditIntent(id=f'intent_{uuid4().hex}',workspace_id=workspace_id,task_id=task_id,
                raw_command=f'{action}目标段修改',context_stage='narration',selected_object_refs=[f'section:{section_id}'],
                playhead_ms=None,intent_type='redo_timeline' if redo else 'undo_timeline',scope={'section_id':section_id},
                constraints={},confidence=1.0,status='interpreted'),connection=conn,source=None)
            payload = {'changed_version_id':changed_version_id,'restore_version_id':restore_version_id,
                'base_version_id':current.id,'section_history':history,'section_restore_side':side,**(control_payload or {})}
            proposal = self.create_edit_proposal(_EditProposal(id=f'proposal_{uuid4().hex}',workspace_id=workspace_id,
                task_id=task_id,intent_id=intent.id,summary=f'{action}目标段修改，保留其他片段的后续成果',
                target_refs=[f'section:{section_id}'],operation={'operation_type':operation_type,'payload':payload},
                invalidated_refs=[],preview={},external_effects=[]),connection=conn)
            applied_at = utc_now()
            operation = _EditOperation(id=f'operation_{uuid4().hex}',workspace_id=workspace_id,task_id=task_id,
                proposal_id=proposal.id,operation_type=operation_type,payload=payload,base_version_id=current.id,
                result_version_id=version_id,status='applied',created_at=applied_at,applied_at=applied_at)
            version = _TimelineVersion(version_id,workspace_id,task_id,current.id,current.sequence+1,snapshot,operation.id)
            full = snapshot.get('montage')
            self.edit_adoption.confirm(workspace_id,task_id,proposal.id,operation=operation,result_version=version,
                montage_plan_id=full['id'] if full else None,montage_payload=full,subtitle_tracks=tracks,connection=conn)
            if execution_readiness_write is not None:
                readiness=execution_readiness_write(conn)
                self.execution_records.append_task_event(_TaskEvent(f'event_{operation.id}_execution_readiness',workspace_id,task_id,None,
                    'edit_execution_readiness',{'operation_id':operation.id,**readiness}),connection=conn)
            self.replace_adopted_narration_segment(task, segment, connection=conn)
            task = self._invalidate_section_composition(workspace_id,task_id,conn)
            self.execution_records.append_task_event(_TaskEvent(id=f'event_{operation.id}_dependencies',workspace_id=workspace_id,task_id=task_id,
                job_id=job.id,event_type='section_edit_dependencies',payload={'operation_id':operation.id,
                'dependencies':self.section_dependency_snapshot(workspace_id,task_id,section_id,connection=conn)}),connection=conn)
            self.finish_job_run(workspace_id,job.id,status='succeeded',output_refs=[track.artifact_ref for track in tracks
                if track.artifact_ref and track.transform_id==f'transform_artifact_{version_id}_{track.language}'],connection=conn)
        return operation,full,task

    def segment_revision_result(self, workspace_id, job_id):
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                "AND event_type='segment_publication' ORDER BY created_at DESC LIMIT 1", (workspace_id, job_id)).fetchone()
        return json.loads(row['payload_json']) if row is not None else None

    def save_semantic_review(self, event, *, write_guard):
        """Persist the trusted host's bound Pi comparison without judging its semantics."""
        if (event.event_type != 'narration_semantic_review' or event.job_id is not None
                or event.payload.get('evidence_kind') != 'pi_semantic_comparison'
                or not event.payload.get('source', {}).get('assistant_turn_id')):
            raise ValueError('语义复核必须来自同一可信宿主回合')
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            write_guard(conn)
            self._require_creation_task(conn, event.workspace_id, event.task_id)
            row = conn.execute('SELECT * FROM task_events WHERE workspace_id=? AND id=?',
                (event.workspace_id, event.id)).fetchone()
            if row is not None:
                previous = self.execution_records.task_event_from_row(row)
                if (previous.task_id, previous.event_type, previous.job_id, previous.payload) != (
                        event.task_id, event.event_type, event.job_id, event.payload):
                    raise ValueError('原语义复核请求已绑定其他内容')
                return previous
            self.execution_records.append_task_event(event, connection=conn)
        return event

    def _section_timeline_dependency_inputs(self, conn, task, section_id, adopted):
        from app.creation.section_timeline import SectionDependencyInputs, _ms, _section_bounds
        # SQLite traverses the stored document; only target bodies and global clocks
        # cross into Python. The publication fallback is already section-scoped.
        params = (task.workspace_id, task.current_timeline_version_id)
        present = conn.execute("SELECT EXISTS(SELECT 1 FROM json_each(snapshot_json,'$.montage')) "
            'FROM timeline_versions WHERE workspace_id=? AND id=?', params).fetchone()
        if present and present[0]:
            source = "SELECT json_extract(snapshot_json,'$.montage') AS montage FROM timeline_versions WHERE workspace_id=? AND id=?"
        else:
            source, params = 'SELECT ? AS montage', (json.dumps((adopted or {}).get('montage') or {}),)
        def query(sql, values=()):
            return conn.execute('WITH source AS (' + source + ') ' + sql, (*params, *values))
        meta = json.loads(query("SELECT json_remove(montage,'$.cues','$.clips','$.graphic_overlays') FROM source").fetchone()[0])
        cues, order, durations = [], [], []
        for row in query("SELECT json_extract(j.value,'$.id'),json_extract(j.value,'$.duration_seconds'), "
                "CASE WHEN json_extract(j.value,'$.source_section_id')=? THEN j.value END "
                "FROM source,json_each(source.montage,'$.cues') j ORDER BY CAST(j.key AS INTEGER)", (section_id,)):
            order.append(row[0]); durations.append(row[1])
            if row[2] is not None:
                cues.append(json.loads(row[2]))
        clocks = [dict(row) for row in query("SELECT j.key AS _position,json_extract(j.value,'$.cue_id') AS cue_id, "
            "json_extract(j.value,'$.timeline_start_seconds') AS timeline_start_seconds, "
            "json_extract(j.value,'$.timeline_end_seconds') AS timeline_end_seconds, "
            "COALESCE(json_extract(j.value,'$.transition_in_seconds'),0) AS transition_in_seconds "
            "FROM source,json_each(source.montage,'$.clips') j ORDER BY CAST(j.key AS INTEGER)")]
        ids = {cue['id'] for cue in cues}
        selected = [clip for clip in clocks if clip['cue_id'] in ids]
        wanted = {clip['_position'] for clip in selected}
        overlays = []
        if selected:
            _, start, end = _section_bounds(order, ids, clocks, meta['duration_seconds'])
            ordered = sorted(clocks, key=lambda clip: clip['timeline_start_seconds'])
            for left, right in zip(ordered, ordered[1:]):
                if right['transition_in_seconds'] and (left['cue_id'] in ids or right['cue_id'] in ids):
                    wanted.update((left['_position'], right['_position']))
            overlay_ids = [row[0] for row in query("SELECT j.key,json_extract(j.value,'$.start_seconds'), "
                "json_extract(j.value,'$.end_seconds') FROM source,json_each(source.montage,'$.graphic_overlays') j "
                "ORDER BY CAST(j.key AS INTEGER)") if _ms(row[1]) < end and _ms(row[2]) > start]
            overlays = [json.loads(row[0]) for row in query("SELECT j.value FROM source, "
                "json_each(source.montage,'$.graphic_overlays') j WHERE j.key IN (SELECT value FROM json_each(?)) "
                "ORDER BY CAST(j.key AS INTEGER)", (json.dumps(overlay_ids),))]
        facts = {row[0]: json.loads(row[1]) for row in query("SELECT j.key,j.value FROM source, "
            "json_each(source.montage,'$.clips') j WHERE j.key IN (SELECT value FROM json_each(?)) "
            "ORDER BY CAST(j.key AS INTEGER)", (json.dumps(sorted(wanted)),))}
        return SectionDependencyInputs(cues, order, clocks, facts, meta.get('duration_seconds'),
            meta.get('audio_timeline'), meta.get('narration_audio_path'),
            sum(durations) if selected and meta.get('audio_timeline') is None and meta.get('narration_audio_path') else 0,
            meta.get('narration_linkage_created_audio_timeline', False) or meta.get('audio_timeline') is None, overlays)

    def section_dependency_snapshot(self,workspace_id,task_id,section_id,*,connection=None):
        """Actual target dependencies, in the owner's snapshot (including uncommitted edits)."""
        # Historically this entry starts a snapshot on a borrowed idle connection.
        # Leave its lifetime/commit to that caller, just as effective_creation_script did.
        if connection is not None and not connection.in_transaction:
            connection.execute('BEGIN')
        with self.creation_execution_read(connection) as conn:
            task = self._require_creation_task(conn,workspace_id,task_id)
            script, _ = self.script_records.read_execution_script_metadata(conn,workspace_id,task.confirmed_script_id)
            current = self.read_creation_section_input(task,script,section_id,connection=conn)
            section, audio = current['section'], current['audio']
            plan = self._read_creation_plan(conn,workspace_id,task.plan_id)
            inputs = self._section_timeline_dependency_inputs(conn,task,section_id,current['adopted'])
            cue_ids = {item['id'] for item in inputs.cues}
            clips = [item for item in inputs.clip_clocks if item['cue_id'] in cue_ids]
            start = min((item['timeline_start_seconds'] for item in clips),default=0)
            end = max((item['timeline_end_seconds'] for item in clips),default=start)
            assets = sorted({inputs.clip_facts[item['_position']]['asset_id'] for item in clips})
            sources = [tuple(row) for row in conn.execute('SELECT id,updated_at FROM material_assets WHERE workspace_id=? '
                'AND id IN (SELECT value FROM json_each(?)) ORDER BY id',(workspace_id,json.dumps(assets)))]
            tracks = []
            if inputs.cues and task.current_timeline_version_id:
                for row in conn.execute("SELECT id,json_remove(payload_json,'$.cues') FROM subtitle_tracks WHERE workspace_id=? AND task_id=? "
                        'AND timeline_version_id=? ORDER BY id',(workspace_id,task_id,task.current_timeline_version_id)):
                    track = json.loads(row[1])
                    relevant_ids = [cue[0] for cue in conn.execute("SELECT j.key,json_extract(j.value,'$.source_cue_id'), "
                        "COALESCE(json_extract(j.value,'$.start_seconds'),0),COALESCE(json_extract(j.value,'$.end_seconds'),0) "
                        "FROM subtitle_tracks t,json_each(t.payload_json,'$.cues') j WHERE t.workspace_id=? AND t.id=? "
                        "ORDER BY CAST(j.key AS INTEGER)",(workspace_id,row[0]))
                        if cue[1] in cue_ids or cue[2]<end and cue[3]>start]
                    relevant = []
                    for value in conn.execute("SELECT j.value FROM subtitle_tracks t,json_each(t.payload_json,'$.cues') j "
                            "WHERE t.workspace_id=? AND t.id=? AND j.key IN (SELECT value FROM json_each(?)) ORDER BY CAST(j.key AS INTEGER)",
                            (workspace_id,row[0],json.dumps(relevant_ids))):
                        cue = json.loads(value[0])
                        relevant.append({**cue,'start_seconds':cue['start_seconds']-start,'end_seconds':cue['end_seconds']-start})
                    if relevant:
                        tracks.append({'language':track.get('language'),'generation_method':track.get('generation_method'),
                            'source_language':track.get('source_language'),'style':track.get('style'),'cues':relevant})
            tracks.sort(key=lambda track:(track['language'],track['generation_method'],track['source_language'] or ''))
            from app.creation.section_timeline import section_dependency_facts
            return {'script_id':script.id,'section':asdict(section),
                'audio':{key:value for key,value in asdict(audio).items() if key not in {'created_at','updated_at'}} if audio else None,
                'voice':asdict(plan.selected_voice) if plan.selected_voice else None,
                'person':asdict(plan.selected_person) if plan.selected_person else None,'aspect_ratio':plan.aspect_ratio,
                'timeline':section_dependency_facts(inputs),'source_versions':sources,'subtitles':tracks}

    def timeline_sequence(self, workspace_id, version_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute('SELECT sequence FROM timeline_versions WHERE workspace_id=? AND id=?',
                (workspace_id, version_id)).fetchone()
            if row is None:
                raise KeyError(version_id)
            return row[0]

    def stage_section_version(self, workspace_id, task_id, section_id, *, connection=None):
        if connection is None or not connection.in_transaction:
            with self.creation_execution_read() as reader:
                return self.stage_section_version(workspace_id, task_id, section_id, connection=reader)
        adopted = self.stage_segment_state(workspace_id, task_id, section_id, connection=connection)
        if adopted is not None:
            return adopted['segment_version']
        task = self._require_creation_task(connection, workspace_id, task_id)
        header, _ = self.script_records.read_execution_script_metadata(connection, workspace_id, task.confirmed_script_id)
        current = self.read_creation_section_input(task, header, section_id, connection=connection)
        section, audio = current['section'], current['audio']
        return hashlib.sha256(json.dumps([header.id, section_id, section.content, audio.audio_reference if audio else None],
            ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()

    def stage_segment_state(self, workspace_id, task_id, section_id, *, connection=None):
        if connection is None or not connection.in_transaction:
            with self.creation_execution_read() as reader:
                return self.stage_segment_state(workspace_id, task_id, section_id, connection=reader)
        task = self._require_creation_task(connection, workspace_id, task_id)
        version = connection.execute(
            "SELECT s.value AS publication_id,i.value AS invalidation_json FROM timeline_versions v "
            "JOIN json_each(v.snapshot_json,'$.stage_segments') s ON s.key=? "
            "LEFT JOIN json_each(v.snapshot_json,'$.stage_segment_invalidations') i ON i.key=s.key "
            'WHERE v.workspace_id=? AND v.task_id=? AND v.id=?',
            (section_id, workspace_id, task_id, task.current_timeline_version_id)).fetchone()
        if version is None or version['publication_id'] is None:
            return None
        reference = version['publication_id']
        row = connection.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND id=? '
            "AND event_type='segment_publication'", (workspace_id, task_id, reference)).fetchone()
        if row is None:
            raise ValueError('段版本引用的原产物记录不可读取')
        invalidation = json.loads(version['invalidation_json']) if version['invalidation_json'] is not None else None
        invalid = invalidation is not None and invalidation['publication_id'] == reference
        return {'segment_version': reference, **json.loads(row['payload_json']),
            'current_validity': 'invalidated' if invalid else 'current',
            'invalidation_reason': invalidation['reason'] if invalid else None}

    def unsettled_segment_units(self, workspace_id, job_id):
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM task_events s WHERE s.workspace_id=? AND s.job_id=? "
                "AND s.event_type='task_execution_unit_started' AND NOT EXISTS (SELECT 1 FROM task_events e "
                "WHERE e.workspace_id=s.workspace_id AND e.job_id=s.job_id AND e.event_type='task_execution_unit_settled' "
                "AND json_extract(e.payload_json,'$.usage_id')=json_extract(s.payload_json,'$.usage_id')) LIMIT 1",
                (workspace_id, job_id)).fetchone() is not None

    def effective_creation_script(self, workspace_id, task_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN")
            task = self._require_creation_task(conn, workspace_id, task_id)
            row = conn.execute('SELECT payload FROM creation_task_scripts WHERE workspace_id=? AND task_id=? AND id=?',
                (workspace_id, task_id, task.confirmed_script_id)).fetchone()
            if row is None:
                raise ValueError('当前任务没有确认文案')
            script = self._creation_task_script_from_payload(json.loads(row['payload']), connection=conn)
            sections = []
            for section in script.sections:
                state = self.stage_segment_state(workspace_id, task_id, section.id, connection=conn)
                if state is not None and state['script_id'] != script.id:
                    raise ValueError('采用的段版本不属于当前确认文案')
                sections.append(replace(section, content=state['text']) if state is not None else section)
            return replace(script, sections=sections)

    def task_budget_projection(self, workspace_id, task_id, *, connection=None):
        """Read original resource receipts; missing monetary evidence never blocks local work."""
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            jobs = conn.execute('SELECT id,attempt FROM job_runs WHERE workspace_id=? AND task_id=?',
                (workspace_id, task_id)).fetchall()
            rows = conn.execute("SELECT job_id,payload_json FROM task_events WHERE workspace_id=? AND task_id=? "
                "AND event_type IN ('creation_narration_unit_settled','task_execution_unit_settled')",
                (workspace_id, task_id)).fetchall()
            grants = conn.execute("SELECT DISTINCT r.id,r.last_grant_generation,e.permit_generation FROM device_work_requests r "
                "JOIN job_runs j ON j.workspace_id=r.workspace_id AND j.id=r.owner_id "
                "LEFT JOIN device_request_events e ON e.request_id=r.id "
                "WHERE j.workspace_id=? AND j.task_id=? AND (r.last_grant_generation IS NOT NULL OR e.permit_generation IS NOT NULL)",
                (workspace_id, task_id)).fetchall()
            active_rows = conn.execute("SELECT s.payload_json FROM task_events s WHERE s.workspace_id=? AND s.task_id=? "
                "AND s.event_type IN ('creation_narration_unit_started','task_execution_unit_started') AND NOT EXISTS "
                "(SELECT 1 FROM task_events e WHERE e.workspace_id=s.workspace_id AND e.task_id=s.task_id "
                "AND e.event_type IN ('creation_narration_unit_settled','task_execution_unit_settled') "
                "AND json_extract(e.payload_json,'$.usage_id')=json_extract(s.payload_json,'$.usage_id')) LIMIT 32",
                (workspace_id, task_id)).fetchall()
            waiting = conn.execute("SELECT r.id,r.owner_id,r.state,r.wait_reason,r.last_failure_reason FROM device_work_requests r "
                "JOIN job_runs j ON j.workspace_id=r.workspace_id AND j.id=r.owner_id WHERE j.workspace_id=? AND j.task_id=? "
                "AND r.state IN ('waiting','blocked','granted','yield_requested','failed') ORDER BY r.updated_at DESC LIMIT 1",
                (workspace_id, task_id)).fetchone()
        receipts, conflicts, accounted_jobs = {}, set(), set()
        for row in rows:
            payload = json.loads(row['payload_json']); key = payload['usage_id']
            value = (float(payload['elapsed_seconds']), int(payload['output_bytes']) if payload['output_bytes'] is not None else None)
            if key in receipts and receipts[key] != value:
                conflicts.add(key)
            receipts[key] = value
            accounted_jobs.add(row['job_id'])
        expected = {f"{row['id']}:{generation}" for row in grants
            for generation in (row['last_grant_generation'], row['permit_generation']) if generation is not None}
        missing = expected - receipts.keys()
        unmeasured_jobs = {row['id'] for row in jobs if row['attempt'] > 0} - accounted_jobs
        in_flight = [json.loads(row['payload_json']) for row in active_rows]
        unknown_outputs = {key for key,value in receipts.items() if value[1] is None}
        coverage = ('conflict' if conflicts else 'partial' if missing or unmeasured_jobs or in_flight or unknown_outputs else 'complete')
        seconds = sum(value[0] for key, value in receipts.items() if key not in conflicts)
        generated = sum(value[1] for key, value in receipts.items() if key not in conflicts and value[1] is not None)
        unit_budget_blocked = waiting is not None and waiting['last_failure_reason'] == 'declared_resources_exceed_budget'
        return {'task_id': task_id, 'status': 'unit_budget_blocked' if unit_budget_blocked else 'local_work_authorized',
            'waiting_reason': waiting['wait_reason'] if waiting is not None else None,
            'waiting_request': dict(waiting) if waiting is not None else None,
            'resources': {'settled_execution_seconds': seconds, 'settled_generated_bytes': generated,
                'total_execution_seconds': seconds if coverage == 'complete' else None,
                'total_generated_bytes': generated if coverage == 'complete' else None,
                'storage_peak_bytes': None, 'coverage': coverage, 'receipt_count': len(receipts),
                'missing_usage_ids': sorted(missing), 'unmeasured_job_ids': sorted(unmeasured_jobs),
                'conflicting_usage_ids': sorted(conflicts), 'evidence_kind': 'original_execution_receipts'},
            'actual_cost': {'status': 'pending_verification', 'amount': None, 'currency': None,
                'supplier_usage_refs': [], 'pricing_or_invoice_refs': []},
            'estimates': {'eta_seconds': None, 'cost': None}, 'in_flight': in_flight}

    def authorize_execution_budget_scope(self, workspace_id, origin_task_id, actor, **command):
        from app.creation.execution_budget_scope import ExecutionBudgetScopes
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            scopes = ExecutionBudgetScopes(self)
            scopes.install_indexes(conn)
            return scopes.authorize(conn, workspace_id, origin_task_id, actor, **command)

    def bind_execution_budget_scope(self, workspace_id, job_id, actor, *, scope_id, expected_revision, connection=None):
        from app.creation.execution_budget_scope import ExecutionBudgetScopes
        if connection is None:
            with self._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                return self.bind_execution_budget_scope(workspace_id, job_id, actor,
                    scope_id=scope_id, expected_revision=expected_revision, connection=conn)
        return ExecutionBudgetScopes(self).bind(connection, workspace_id, job_id, actor,
            scope_id=scope_id, expected_revision=expected_revision)

    def execution_budget_scopes_for_task(self, workspace_id, task_id, actor, *, connection=None):
        from app.creation.execution_budget_scope import ExecutionBudgetScopes
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN")
            self._require_creation_task(conn, workspace_id, task_id)
            return ExecutionBudgetScopes(self).for_task(conn, workspace_id, task_id, actor)

    def execution_budget_scope_usage(self, workspace_id, scope_id):
        from app.creation.execution_budget_scope import ExecutionBudgetScopes
        with self._connect() as conn:
            scopes = ExecutionBudgetScopes(self)
            current = scopes.current(conn, workspace_id, scope_id)
            return {'authorization': current.payload, 'usage': scopes.usage(conn, workspace_id, scope_id)}

    def creation_narration_usage(self, workspace_id, job_id, *, connection=None):
        """The budget checkpoint indexes receipts; only settlements own cumulative facts."""
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?',
                (workspace_id, f'event_{job_id}_narration_budget')).fetchone()
            budget = json.loads(row['payload_json']) if row is not None else None
            receipts = []
            for key in ('settled_event_id', 'active_event_id'):
                event_id = budget.get(key) if budget is not None else None
                receipt = conn.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? AND id=?',
                    (workspace_id, job_id, event_id)).fetchone() if event_id is not None else None
                if event_id is not None and receipt is None:
                    from app.device_scheduler import RecoveryBlocked
                    raise RecoveryBlocked('配音预算检查点的原消耗回执不存在，不能按零恢复')
                receipts.append(json.loads(receipt['payload_json']) if receipt is not None else None)
            from app.creation.execution_budget_scope import ExecutionBudgetScopes
            scopes = ExecutionBudgetScopes(self)
            try:
                binding = scopes.binding(conn, workspace_id, job_id)
            except KeyError:
                scope = None
            else:
                authorization = scopes.current(conn, workspace_id, binding.payload['scope_id'])
                scope = {'authorization': authorization.payload,
                    'usage': scopes.usage(conn, workspace_id, binding.payload['scope_id'])}
        settled, active = receipts
        return {'budget': budget, 'scope': scope, 'used_seconds': settled['total_seconds'] if settled is not None else 0,
                'used_output_bytes': settled['total_output_bytes'] if settled is not None else 0,
                'consumption_known': active is None, 'unsettled': [active] if active is not None else []}

    def creation_execution_input(self, workspace_id, job_id, *, connection=None):
        """Read effective inputs while keeping the original start audit immutable."""
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            job = self.get_job_run(workspace_id, job_id, connection=conn)
            start = self.execution_records.get_task_event(workspace_id, f'event_{job_id}_start', connection=conn)
            if (start.task_id, start.job_id) != (job.task_id, job.id):
                raise ValueError('原制作开始记录不属于当前任务')
            payload = start.payload
            if payload['input_digest'] != job.input_digest:
                row = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? AND job_id=? "
                    "AND event_type='creation_execution_input_rebound' AND json_extract(payload_json,'$.input_digest')=? "
                    "ORDER BY rowid DESC LIMIT 1", (workspace_id, job.task_id, job.id, job.input_digest)).fetchone()
                if row is None or json.loads(row['payload_json'])['start_event_id'] != start.id:
                    from app.device_scheduler import RecoveryBlocked
                    raise RecoveryBlocked('原作业有效输入缺少声音改选回执，不能继续')
                rebound = json.loads(row['payload_json'])
                payload = {**payload, 'input_digest': job.input_digest,
                    'source_binding_event_id': rebound.get('source_binding_event_id')}
            render = conn.execute("SELECT id,task_id,job_id,payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                "AND event_type='creation_execution_stage' AND json_extract(payload_json,'$.execution_purpose')='render_current' "
                "ORDER BY rowid DESC LIMIT 1", (workspace_id, job_id)).fetchone()
            if render is not None:
                current = json.loads(render['payload_json'])
                head, unit = current.get('input_timeline_version_id'), current.get('unit_key')
                identity = hashlib.sha256(json.dumps([head, unit]).encode()).hexdigest()
                if (render['task_id'] != job.task_id or render['job_id'] != job.id
                        or render['id'] != f'event_{job_id}_render_requested_{identity}'
                        or current.get('stage') != 'render' or current.get('status') != 'pending'
                        or current.get('start_event_id') != start.id
                        or not isinstance(head, str) or not head or not isinstance(unit, str) or not unit.startswith('render:')
                        or current.get('script_id') != payload.get('script_id')
                        or current.get('input_digest') != job.input_digest
                        or any(current.get(key) != payload.get(key) for key in ('member_id','identity_kind','device_id','user_id'))):
                    raise ValueError('重新输出事实与原作业身份、输入或成片单元不一致')
                payload = {**payload, 'operation': 'render_current', 'render_request_id': render['id'],
                    'render_unit_key': unit, 'render_input_timeline_version_id': head}
            return payload

    def creation_narration_source_binding(self, workspace_id, job_id, *, connection=None):
        from app.device_scheduler import RecoveryBlocked
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            job = self.get_job_run(workspace_id, job_id, connection=conn)
            inputs = self.creation_execution_input(workspace_id, job_id, connection=conn)
            source_id = inputs.get('source_binding_event_id')
            if source_id is None:
                raise RecoveryBlocked('original_source_content_proof_missing: 原作业未封存来源字节，须实际重新选择声音')
            event = self.execution_records.get_task_event(workspace_id, source_id, connection=conn)
            payload = event.payload
            if (event.event_type != 'creation_narration_source_bound' or event.task_id != job.task_id
                    or payload.get('version') != 1 or payload.get('business_input_digest') != job.input_digest
                    or payload.get('workspace_id') != workspace_id or payload.get('task_id') != job.task_id):
                raise RecoveryBlocked('原作业来源封存引用不可核实')
            origin = self.execution_records.get_task_event(workspace_id, payload['origin_event_id'], connection=conn)
            if origin.job_id != event.job_id or origin.payload.get('source_binding_event_id') != source_id:
                raise RecoveryBlocked('原作业来源缺少同事务采用关联')
            return event

    def seal_creation_narration_source(self, job, prepared, *, input_digest, origin_event_id, connection):
        from app.creation.narration_pcm import verify_narration_source, source_binding_payload
        from app.creation.execution_input import validate_execution_relationships
        snapshot = self.read_creation_execution_snapshot(job.workspace_id, job.task_id, connection=connection)
        task, plan = snapshot.task, snapshot.plan
        validate_execution_relationships(task, snapshot.script_header, plan)
        if snapshot.input_digest != prepared['adoption_revision_digest']:
            raise ValueError('文案或方案在来源准备后变化，未封存新输入')
        if job.kind == 'creation_task_execution' and input_digest != prepared['adoption_revision_digest']:
            raise ValueError('原制作输入摘要与采用来源不一致')
        verify_narration_source(self, task, plan, prepared, connection=connection)
        source_id = f'{origin_event_id}_source'
        self.execution_records.append_task_event(_TaskEvent(source_id, job.workspace_id, job.task_id, job.id,
            'creation_narration_source_bound', source_binding_payload(prepared,
                input_digest=input_digest, origin_event_id=origin_event_id)), connection=connection)
        return source_id

    def creation_narration_reference(self, workspace_id, job_id, *, connection):
        inputs = self.creation_execution_input(workspace_id, job_id, connection=connection)
        if not inputs.get('source_binding_event_id'):
            return None  # Legacy started work retains its existing physical fences.
        source = self.creation_narration_source_binding(workspace_id, job_id, connection=connection)
        row = connection.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
            "AND event_type='creation_narration_source_revalidated' "
            "AND json_extract(payload_json,'$.source_binding_event_id')=? ORDER BY rowid DESC LIMIT 1",
            (workspace_id, job_id, source.id)).fetchone()
        return json.loads(row['payload_json'])['narration_reference'] if row else source.payload['narration_reference']

    def rebind_unstarted_creation_voice(self, job, input_digest, selection_event_id, *, connection, source_binding_event_id=None):
        usage = self.creation_narration_usage(job.workspace_id, job.id, connection=connection)
        if (job.kind != 'creation_task_execution' or job.status != 'queued' or job.attempt != 0
                or job.started_at is not None or not usage['consumption_known']
                or usage['used_seconds'] != 0 or usage['used_output_bytes'] != 0
                or connection.execute("SELECT 1 FROM task_events WHERE workspace_id=? AND job_id=? "
                    "AND event_type IN ('creation_narration_unit_started','creation_narration_unit_settled') LIMIT 1",
                    (job.workspace_id, job.id)).fetchone()):
            raise ValueError('原配音作业已开始或消耗未知，不能改选声音')
        task = self._require_creation_task(connection, job.workspace_id, job.task_id)
        if (task.narration_cache_key is not None
                or self.list_narration_segments(job.workspace_id, job.task_id, task.confirmed_script_id, connection=connection)
                or connection.execute("SELECT 1 FROM narration_artifacts WHERE workspace_id=? "
                    "AND json_extract(payload,'$.task_id')=? LIMIT 1", (job.workspace_id, job.task_id)).fetchone()):
            raise ValueError('原任务已有配音产物或片段，不能按未开始改选声音')
        changed = connection.execute("UPDATE job_runs SET input_digest=?,updated_at=? WHERE workspace_id=? AND id=? "
            "AND input_digest=? AND status='queued' AND attempt=0 AND started_at IS NULL",
            (input_digest, utc_now().isoformat(), job.workspace_id, job.id, job.input_digest)).rowcount
        if changed != 1:
            raise ValueError('原配音作业版本已变化，声音选择未提交')
        self.execution_records.append_task_event(_TaskEvent(id=f'{selection_event_id}_execution', workspace_id=job.workspace_id,
            task_id=job.task_id, job_id=job.id, event_type='creation_execution_input_rebound',
            payload={'start_event_id': f'event_{job.id}_start', 'selection_event_id': selection_event_id,
                'previous_input_digest': job.input_digest, 'input_digest': input_digest,
                'source_binding_event_id': source_binding_event_id}), connection=connection)
        return self.get_job_run(job.workspace_id, job.id, connection=connection)

    def start_creation_narration_unit(self, grant, resources, pending_segments, *, pending_segment_for=None):
        from app.device_scheduler import RecoveryBlocked
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            request = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
            if (request is None or request['state'] != 'granted'
                    or request['last_grant_generation'] != grant.generation
                    or (request['workspace_id'], request['owner_id'], request['input_digest'], request['unit_key']) !=
                       (grant.workspace_id, grant.owner_id, grant.input_digest, grant.unit_key)
                    or json.loads(request['resources_json']) != resources):
                raise PermissionError('配音用量开始记录不属于当前许可')
            usage = self.creation_narration_usage(grant.workspace_id, grant.owner_id, connection=conn)
            if usage['unsettled']:
                raise RecoveryBlocked('原配音单元消耗尚未结算，须先核对原请求与进程回执')
            job = self.claim_job_run(grant.workspace_id, grant.owner_id, connection=conn)
            task = self._require_creation_task(conn, job.workspace_id, job.task_id)
            if (job.kind != 'creation_task_narration_edit' and task.stage != 'narration'
                    or task.confirmed_script_id != resources['script_id']):
                raise PermissionError('配音许可与当前文案阶段不一致')
            if usage['budget'] is None:
                budget = {key: resources[key] for key in ('parent_timeout_seconds', 'parent_max_output_bytes', 'script_id', 'input_contract')}
                if usage['scope'] is not None:
                    from app.creation.execution_budget import narration_limits
                    from types import SimpleNamespace
                    expected = narration_limits(usage, SimpleNamespace(
                        max_timeout_seconds=resources['timeout_seconds'], max_output_bytes=resources['max_output_bytes']))
                    if any(budget[key] != expected[key] for key in expected):
                        raise PermissionError('累计额度声明与可信任务策略不一致')
                    budget.update(policy_kind='scope', scope_id=usage['scope']['authorization']['scope_id'])
                elif budget['parent_timeout_seconds'] is None or budget['parent_max_output_bytes'] is None:
                    raise PermissionError('历史任务不能缺字段解除原封存预算')
                budget.update(settled_event_id=None, active_event_id=None)
                self.execution_records.append_task_event(_TaskEvent(id=f'event_{job.id}_narration_budget', workspace_id=job.workspace_id,
                    task_id=job.task_id, job_id=job.id, event_type='creation_narration_budget', payload=budget), connection=conn)
                usage['budget'] = budget
            budget = usage['budget']
            from app.creation.execution_budget import finite_unit_limit
            remaining_seconds = finite_unit_limit(resources['timeout_seconds'], None if budget['parent_timeout_seconds'] is None
                else budget['parent_timeout_seconds'] - usage['used_seconds'])
            remaining_bytes = finite_unit_limit(resources['max_output_bytes'], None if budget['parent_max_output_bytes'] is None
                else budget['parent_max_output_bytes'] - usage['used_output_bytes'])
            if remaining_seconds <= 0 or remaining_bytes <= 0:
                raise ValueError('原配音阶段累计额度已耗尽，不能重置额度重启')
            if job.kind == 'creation_task_narration_edit':
                pass  # Candidate generation does not replace applied segment facts.
            elif resources['narration_phase'] == 'merge':
                snapshot = self.read_creation_execution_snapshot(task.workspace_id, task.id, connection=conn)
                header = snapshot.script_header
                if snapshot.input_digest != grant.input_digest:
                    raise RecoveryBlocked('原PCM启动前固定文案或制作输入已变化')
                count = 0
                for _, segment in self.iter_creation_narration_sections(task, header, connection=conn):
                    if segment is None or segment.status != 'ready' or not segment.audio_reference:
                        raise RecoveryBlocked('原配音片段集合不完整，不能覆盖恢复')
                    count += 1
                if count == 0 or count != header.sections_total_count:
                    raise RecoveryBlocked('原配音片段集合不完整，不能覆盖恢复')
            elif pending_segment_for is not None:
                snapshot = self.read_creation_execution_snapshot(task.workspace_id, task.id, connection=conn)
                header = snapshot.script_header
                if snapshot.input_digest != grant.input_digest or header.selection_status != 'confirmed':
                    raise RecoveryBlocked('原单段启动前固定文案或制作输入已变化')
                count = conn.execute('SELECT COUNT(*) FROM narration_segments WHERE workspace_id=? AND task_id=? '
                    'AND script_id=?', (task.workspace_id, task.id, header.id)).fetchone()[0]
                if not header.sections_total_count or count not in (0, header.sections_total_count):
                    raise RecoveryBlocked('原配音片段集合不完整，不能覆盖恢复')
                for section, segment in self.iter_creation_narration_sections(task, header, connection=conn):
                    if count:
                        if segment is None or segment.text != section.content:
                            raise RecoveryBlocked('原配音片段集合与采用正文不匹配，不能覆盖恢复')
                        _validate_narration_segment(segment)
                        continue
                    segment = pending_segment_for(task, header, section, resources)
                    _validate_narration_segment(segment)
                    if ((segment.workspace_id, segment.task_id, segment.script_id, segment.source_section_id, segment.text)
                            != (task.workspace_id, task.id, header.id, section.id, section.content)
                            or segment.status != 'pending'):
                        raise ValueError('初始配音分段必须属于当前唯一采用文案')
                    conn.execute('INSERT INTO narration_segments '
                        '(id,workspace_id,task_id,script_id,source_section_id,text,audio_reference,duration_seconds,sample_rate,'
                        'model_id,voice_profile_id,voice_version,language,pronunciation_version,status,created_at,updated_at,'
                        'source_kind,source_artifact_ref,source_sha256,source_script_digest) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', _narration_segment_values(segment))
            else:
                current_segments = self.list_narration_segments(task.workspace_id, task.id, task.confirmed_script_id, connection=conn)
                if not current_segments:
                    self.save_narration_segments(task.workspace_id, task.id, task.confirmed_script_id, pending_segments, connection=conn)
                elif len(current_segments) != len(pending_segments):
                    raise RecoveryBlocked('原配音片段集合不完整，不能覆盖恢复')
            if job.kind != 'creation_task_narration_edit':
                task.stage_status, task.status = 'running', 'running'
            task.stage_reason = task.stage_impact = task.stage_repair_action = task.stage_retryable = None
            task.updated_at = utc_now()
            conn.execute('UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?',
                (_json_payload(task), task.updated_at.isoformat(), task.workspace_id, task.id))
            payload = {'usage_id': f'{grant.request_id}:{grant.generation}', 'request_id': grant.request_id,
                'permit_generation': grant.generation, 'unit_key': grant.unit_key,
                'segment_id': resources.get('segment_id'), 'section_id': resources.get('section_id'),
                'ordinal': resources.get('ordinal'), 'phase': resources['narration_phase'],
                'remaining_seconds': remaining_seconds, 'remaining_output_bytes': remaining_bytes}
            start_id = f'event_{job.id}_narration_started_{grant.generation}'
            self.execution_records.append_task_event(_TaskEvent(id=start_id,
                workspace_id=job.workspace_id, task_id=job.task_id, job_id=job.id,
                event_type='creation_narration_unit_started', payload=payload), connection=conn)
            if usage['scope'] is not None:
                from app.creation.execution_budget_scope import ExecutionBudgetScopes
                from app.creation_execution import CreationExecutionActor
                scopes = ExecutionBudgetScopes(self)
                actor = CreationExecutionActor.from_event(job.workspace_id, self.creation_execution_input(
                    job.workspace_id, job.id, connection=conn))
                scopes.reserve(conn, job.workspace_id, job.id, actor, request_id=grant.request_id,
                    permit_generation=grant.generation, started_event_id=start_id,
                    receipt_id=f'event_{job.id}_narration_settled_{grant.generation}',
                    authorization_revision=usage['scope']['authorization']['revision'],
                    maximum={'execution_seconds': min(remaining_seconds, resources['timeout_seconds']),
                        'generated_bytes': min(remaining_bytes, resources['max_output_bytes'])})
            budget['active_event_id'] = start_id
            conn.execute('UPDATE task_events SET payload_json=? WHERE workspace_id=? AND id=?',
                (json.dumps(budget), job.workspace_id, f'event_{job.id}_narration_budget'))
            return job, remaining_seconds, remaining_bytes

    def previous_creation_narration_output(self, grant):
        with self._connect() as conn:
            row = conn.execute('SELECT permit_generation FROM device_request_events WHERE request_id=? '
                'AND permit_generation IS NOT NULL AND permit_generation<>? ORDER BY id DESC LIMIT 1',
                (grant.request_id, grant.generation)).fetchone()
            if row is None:
                return None
            receipt = conn.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? AND id=?',
                (grant.workspace_id, grant.owner_id, f"event_{grant.owner_id}_narration_settled_{row['permit_generation']}")).fetchone()
        return json.loads(receipt['payload_json']).get('output_file') if receipt is not None else None

    def settle_creation_narration_unit(self, grant, *, elapsed_seconds, output_bytes, connection, output_file=None):
        if (not connection.in_transaction or type(output_bytes) is not int or output_bytes < 0
                or not math.isfinite(elapsed_seconds) or elapsed_seconds < 0):
            raise ValueError('配音消耗须以可核实的时间和字节数在许可事务内结算')
        usage_id = f'{grant.request_id}:{grant.generation}'
        start = connection.execute('SELECT task_id,payload_json FROM task_events WHERE workspace_id=? AND job_id=? AND id=?',
            (grant.workspace_id, grant.owner_id, f'event_{grant.owner_id}_narration_started_{grant.generation}')).fetchone()
        if start is None or json.loads(start['payload_json'])['usage_id'] != usage_id:
            raise ValueError('配音消耗缺少同一次许可的开始记录')
        event_id = f'event_{grant.owner_id}_narration_settled_{grant.generation}'
        previous = connection.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?',
            (grant.workspace_id, event_id)).fetchone()
        if previous is not None:
            recorded = json.loads(previous['payload_json'])
            if (recorded['elapsed_seconds'] != elapsed_seconds or recorded['output_bytes'] != output_bytes
                    or recorded.get('output_file') != output_file):
                raise ValueError('同一配音许可不能重复结算不同消耗')
            return event_id
        usage = self.creation_narration_usage(grant.workspace_id, grant.owner_id, connection=connection)
        if usage['budget']['active_event_id'] != f'event_{grant.owner_id}_narration_started_{grant.generation}':
            raise ValueError('只能结算原预算当前未完成的同一次配音许可')
        payload = {**json.loads(start['payload_json']), 'elapsed_seconds': elapsed_seconds, 'output_bytes': output_bytes,
            'output_file': output_file,
            'total_seconds': usage['used_seconds'] + elapsed_seconds,
            'total_output_bytes': usage['used_output_bytes'] + output_bytes}
        self.execution_records.append_task_event(_TaskEvent(id=event_id, workspace_id=grant.workspace_id,
            task_id=start['task_id'], job_id=grant.owner_id, event_type='creation_narration_unit_settled',
            payload=payload), connection=connection)
        if usage['scope'] is not None:
            from app.creation.execution_budget_scope import ExecutionBudgetScopes
            ExecutionBudgetScopes(self).settle(connection, grant.workspace_id, grant.owner_id,
                request_id=grant.request_id, permit_generation=grant.generation, receipt_id=event_id)
        budget = {**usage['budget'], 'settled_event_id': event_id, 'active_event_id': None}
        connection.execute('UPDATE task_events SET payload_json=? WHERE workspace_id=? AND id=?',
            (json.dumps(budget), grant.workspace_id, f'event_{grant.owner_id}_narration_budget'))
        return event_id

    def commit_creation_narration_segment(self, artifact, segment, *, connection):
        if (artifact.workspace_id, artifact.task_id, artifact.script_id, artifact.voice_profile_id) != (
                segment.workspace_id, segment.task_id, segment.script_id, segment.voice_profile_id):
            raise ValueError('单段产物与片段谱系不一致')
        if segment.status != 'ready' or segment.audio_reference != self.narration_artifact_reference(artifact.cache_key):
            raise ValueError('单段检查点必须绑定已核实的ready产物')
        self.save_narration_artifact(artifact, connection=connection)
        task = self._require_creation_task(connection, segment.workspace_id, segment.task_id)
        self.replace_adopted_narration_segment(task, segment, connection=connection)

    def checkpoint_creation_narration_segment(self, grant, artifact, segment, ordinal, *, connection):
        self.commit_creation_narration_segment(artifact, segment, connection=connection)
        now = utc_now()
        changed = connection.execute("UPDATE job_runs SET status='queued',updated_at=? WHERE workspace_id=? AND id=? AND status='running'",
            (now.isoformat(), grant.workspace_id, grant.owner_id)).rowcount
        if changed != 1:
            raise PermissionError('原配音作业已停止，不能写入片段成功')
        event_id = f'event_{grant.owner_id}_narration_segment_{grant.generation}'
        self.execution_records.append_task_event(_TaskEvent(id=event_id, workspace_id=grant.workspace_id,
            task_id=segment.task_id, job_id=grant.owner_id, event_type='creation_execution_stage',
            payload={'stage': 'narration', 'status': 'pending', 'request_id': grant.request_id,
                'unit_key': grant.unit_key, 'segment_id': segment.id, 'ordinal': ordinal,
                'phase': 'segment', 'output': segment.audio_reference}, created_at=now), connection=connection)
        return event_id

    def narration_edit_generated(self, workspace_id, job_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                "AND event_type='creation_narration_edit_generated' ORDER BY rowid DESC LIMIT 1",(workspace_id,job_id)).fetchone()
            return json.loads(row['payload_json']) if row else None

    def checkpoint_narration_edit_generated(self, grant, artifact, segment, source_key, *, connection):
        self.save_narration_artifact(artifact,connection=connection)
        changed=connection.execute("UPDATE job_runs SET status='queued',updated_at=? WHERE workspace_id=? AND id=? AND status='running'",
            (utc_now().isoformat(),grant.workspace_id,grant.owner_id)).rowcount
        if changed != 1:
            raise PermissionError('原局部配音Job已停止，未采用候选')
        event_id=f'event_{grant.owner_id}_narration_edit_generated'
        self.execution_records.append_task_event(_TaskEvent(event_id,grant.workspace_id,segment.task_id,grant.owner_id,
            'creation_narration_edit_generated',{'source_key':source_key,'segment_id':segment.id,
                'cache_key':artifact.cache_key,'request_id':grant.request_id,'permit_generation':grant.generation}),connection=connection)
        return event_id

    def creation_narration_merge_checkpoint(self, workspace_id, job_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND job_id=? "
                "AND event_type='creation_execution_stage' AND json_extract(payload_json,'$.phase')='merge' "
                "AND json_extract(payload_json,'$.status')='pending' ORDER BY rowid DESC LIMIT 1",
                (workspace_id, job_id)).fetchone()
            return json.loads(row['payload_json']) if row is not None else None

    def checkpoint_creation_narration_merge(self, grant, *, source_digest, section_ids,
                                          input_references, pcm_receipt, connection):
        job = self.get_job_run(grant.workspace_id, grant.owner_id, connection=connection)
        task = self._require_creation_task(connection, job.workspace_id, job.task_id)
        if (job.kind != 'creation_task_narration_edit' and task.stage != 'narration' or len(section_ids) < 1
                or pcm_receipt['job_id'] != job.id
                or pcm_receipt['unit_id'] != f'{grant.request_id}:{grant.generation}'
                or pcm_receipt['source_digest'] != source_digest or pcm_receipt['finalized']):
            raise ValueError('中间PCM检查点不属于原作业，不能发布为音频产物')
        now = utc_now()
        changed = connection.execute("UPDATE job_runs SET status='queued',updated_at=? WHERE workspace_id=? AND id=? AND status='running'",
            (now.isoformat(), grant.workspace_id, grant.owner_id)).rowcount
        if changed != 1:
            raise PermissionError('原配音作业已停止，不能写入合并成功')
        event_id = f'event_{grant.owner_id}_narration_merge_{grant.generation}'
        self.execution_records.append_task_event(_TaskEvent(id=event_id, workspace_id=grant.workspace_id,
            task_id=job.task_id, job_id=job.id, event_type='creation_execution_stage',
            payload={'stage': 'narration', 'status': 'pending', 'phase': 'merge',
                'request_id': grant.request_id, 'unit_key': grant.unit_key, 'source_digest': source_digest,
                'section_ids': section_ids, 'input_references': input_references,
                'pcm': pcm_receipt},
            created_at=now), connection=connection)
        return event_id

    def exhaust_creation_narration_budget(self, grant, task_id, *, connection):
        reason = '原配音阶段累计额度已耗尽；已完成片段保留，未发布整体配音'
        now = utc_now()
        changed = connection.execute("UPDATE job_runs SET status='failed',failure_reason=?,impact=?,repair_action=?,finished_at=?,updated_at=? "
            "WHERE workspace_id=? AND id=? AND task_id=? AND status='queued'",
            (reason, '已完成片段保留，不能重置父预算继续。', '核对原作业预算与消耗。', now.isoformat(), now.isoformat(),
             grant.workspace_id, grant.owner_id, task_id)).rowcount
        if changed != 1:
            raise PermissionError('原配音预算结束时作业状态已变化')
        self.fail_creation_task_stage(grant.workspace_id, task_id, stage='narration', reason=reason,
            impact='已完成片段保留，不能重置父预算继续。', repair_action='核对原作业预算与消耗。', retryable=False, connection=connection)
        self.execution_records.append_task_event(_TaskEvent(id=f'event_{grant.owner_id}_narration_exhausted', workspace_id=grant.workspace_id,
            task_id=task_id, job_id=grant.owner_id, event_type='creation_execution_stage',
            payload={'stage': 'narration', 'status': 'failed', 'reason': reason, 'retryable': False}), connection=connection)

    def requeue_ungranted_narration_job(self, workspace_id, job_id, request_id, *, connection):
        """Only the verified aggregate-to-unit contract migration may revive this Job."""
        row = connection.execute('SELECT * FROM job_runs WHERE workspace_id=? AND id=?', (workspace_id, job_id)).fetchone()
        if (row is None or row['kind'] != 'creation_task_execution' or row['attempt'] != 0
                or row['started_at'] is not None or row['status'] not in {'queued', 'interrupted', 'failed'}
                or (row['status'] == 'failed' and not connection.execute(
                    "SELECT 1 FROM task_events WHERE workspace_id=? AND job_id=? AND event_type='creation_execution_stage' "
                    "AND json_extract(payload_json,'$.failure_kind')='input_budget_exceeded' "
                    "AND json_extract(payload_json,'$.reason_code')='declared_resources_exceed_budget' "
                    "AND json_extract(payload_json,'$.device_request_id')=? LIMIT 1",
                    (workspace_id, job_id, request_id)).fetchone())):
            raise PermissionError('只有原未授予的配音契约拒绝可在原作业继续')
        connection.execute("UPDATE job_runs SET status='queued',failure_reason=NULL,impact=NULL,repair_action=NULL,"
            "finished_at=NULL,updated_at=? WHERE workspace_id=? AND id=?", (utc_now().isoformat(), workspace_id, job_id))

    def reopen_creation_execution_for_render(self, run, event, *, expected_timeline_version_id, connection):
        """Reopen this completed creation only for its current, fixed render input."""
        if not connection.in_transaction:
            raise ValueError('重新输出必须使用原作业的提交事务')
        row = connection.execute('SELECT * FROM job_runs WHERE workspace_id=? AND id=?',
            (run.workspace_id, run.id)).fetchone()
        task = self._require_creation_task(connection, run.workspace_id, run.task_id)
        if (row is None or self._job_run_from_row(row) != run
                or run.kind != 'creation_task_execution' or run.status != 'succeeded'
                or not expected_timeline_version_id or task.current_timeline_version_id != expected_timeline_version_id
                or (event.workspace_id, event.task_id, event.job_id) != (run.workspace_id, run.task_id, run.id)
                or event.event_type != 'creation_execution_stage' or event.payload.get('stage') != 'render'
                or event.payload.get('status') != 'pending'
                or event.payload.get('input_timeline_version_id') != expected_timeline_version_id
                or event.payload.get('execution_purpose') != 'render_current'
                or event.payload.get('script_id') != task.confirmed_script_id
                or event.payload.get('input_digest') != run.input_digest
                or event.payload.get('start_event_id') != f'event_{run.id}_start'
                or not event.payload.get('unit_key', '').startswith('render:')):
            raise PermissionError('原完成作业或重新输出的时间线已变化')
        connection.execute("UPDATE job_runs SET status='queued',progress=66,failure_reason=NULL,impact=NULL,repair_action=NULL,"
            "finished_at=NULL,updated_at=? WHERE workspace_id=? AND id=?",
            (event.created_at.isoformat(), run.workspace_id, run.id))
        self.execution_records.append_task_event(event, connection=connection)
        return self.get_job_run(run.workspace_id, run.id, connection=connection)

    def reopen_failed_job_run(self, workspace_id, job_id, *, expected_updated_at, expected_attempt, connection):
        if not connection.in_transaction:
            raise ValueError('恢复失败作业必须属于原调度事务')
        changed=connection.execute("""UPDATE job_runs SET status='queued',finished_at=NULL,updated_at=?
            WHERE workspace_id=? AND id=? AND status='failed' AND updated_at=? AND attempt=?""",
            (utc_now().isoformat(),workspace_id,job_id,_revision_value(expected_updated_at),expected_attempt)).rowcount
        if changed!=1:
            raise ValueError('失败作业状态、版本或尝试序号已变化，未恢复')
        return self.get_job_run(workspace_id,job_id,connection=connection)


    def resume_creation_narration_job(self, workspace_id, job_id, *, connection):
        usage = self.creation_narration_usage(workspace_id, job_id, connection=connection)
        if usage['budget'] is None or usage['unsettled']:
            raise ValueError('原配音消耗尚不可核实，不能重置作业继续')
        changed = connection.execute("UPDATE job_runs SET status='interrupted',failure_reason=NULL,impact=NULL,repair_action=NULL,"
            "finished_at=NULL,updated_at=? WHERE workspace_id=? AND id=? AND kind='creation_task_execution' AND status IN ('failed','cancelled')",
            (utc_now().isoformat(), workspace_id, job_id)).rowcount
        if changed != 1:
            raise ValueError('原配音作业当前不可显式继续')

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
        connection: sqlite3.Connection | None = None, write_guard=None,
    ) -> _JobRun:
        if status != "succeeded" and not all(
            value is not None and value.strip()
            for value in (failure_reason, impact, repair_action)
        ):
            raise ValueError("未成功的任务必须记录原因、影响和修复动作")
        refs = output_refs or []
        for reference in refs:
            _validate_logical_reference(reference, "产物输出引用")
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            if write_guard is not None:
                if not conn.in_transaction:
                    conn.execute("BEGIN IMMEDIATE")
                write_guard(conn)
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

    def get_task_authorization(self, workspace_id: str, task_id: str, *, connection=None, metadata_only=False):
        from app.creation.contracts import TaskAuthorization as _TaskAuthorization
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            scope = "'{}' AS resource_scope_json" if metadata_only else "resource_scope_json"
            row = conn.execute("SELECT workspace_id,task_id,id,revision,identity_json,goal_revision,allowed_operations_json,"+scope+
                ",issued_at,verified_at,expires_at,revoked_at,last_checked_at,last_verdict FROM task_authorizations WHERE workspace_id=? AND task_id=?",
                               (workspace_id, task_id)).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["identity"] = json.loads(payload.pop("identity_json"))
        payload["resource_scope"] = json.loads(payload.pop("resource_scope_json"))
        payload["allowed_operations"] = tuple(json.loads(payload.pop("allowed_operations_json")))
        for key in ("issued_at", "verified_at", "expires_at", "revoked_at", "last_checked_at"):
            if payload.get(key) is not None:
                payload[key] = datetime.fromisoformat(payload[key])
        return _TaskAuthorization(**payload)

    def save_task_authorization(self, authorization, *, expected_revision: int | None, connection=None):
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                return self.save_task_authorization(authorization, expected_revision=expected_revision, connection=conn)
        self._require_creation_task(connection, authorization.workspace_id, authorization.task_id)
        current = self.get_task_authorization(authorization.workspace_id, authorization.task_id, connection=connection)
        if (current.revision if current is not None else None) != expected_revision:
            from app.creation.contracts import TaskAuthorizationConflict as _TaskAuthorizationConflict
            raise _TaskAuthorizationConflict("任务授权在核验期间已变化")
        if current is not None and current.revoked_at is not None and authorization.revoked_at is None:
            raise PermissionError("已撤销的任务授权不能由迟到核验恢复")
        payload = asdict(authorization)
        for key in ("identity", "resource_scope", "allowed_operations"):
            payload[key+"_json"] = json.dumps(payload.pop(key), ensure_ascii=False)
        for key in ("issued_at", "verified_at", "expires_at", "revoked_at", "last_checked_at"):
            if payload[key] is not None:
                payload[key] = payload[key].isoformat()
        columns = tuple(payload)
        connection.execute("INSERT INTO task_authorizations("+",".join(columns)+") VALUES("+",".join("?" for _ in columns)+") "
            "ON CONFLICT(workspace_id,task_id) DO UPDATE SET "+",".join(key+"=excluded."+key for key in columns if key not in {"workspace_id","task_id"}),
            tuple(payload.values()))
        self.execution_records.append_task_event(_TaskEvent(id=f"event_{authorization.id}_{authorization.revision}",
            workspace_id=authorization.workspace_id, task_id=authorization.task_id, job_id=None,
            event_type="task_authorization", payload={"authorization_id":authorization.id,
                "revision":authorization.revision, "goal_revision":authorization.goal_revision,
                **({"identity":authorization.identity, "allowed_operations":authorization.allowed_operations,
                     "resource_scope":authorization.resource_scope} if current is None or current.issued_at is None else {}),
                "verdict":authorization.last_verdict,
                "expires_at":authorization.expires_at.isoformat() if authorization.expires_at else None,
                "revoked":authorization.revoked_at is not None}), connection=connection)
        return authorization

    def list_task_authorizations(self, *, due_before: datetime, limit: int = 16):
        with self._connect() as conn:
            rows = conn.execute("SELECT a.workspace_id,a.task_id FROM task_authorizations a "
                "WHERE a.revoked_at IS NULL AND (a.last_checked_at IS NULL OR a.last_checked_at<=?) "
                "AND EXISTS(SELECT 1 FROM job_runs j WHERE j.workspace_id=a.workspace_id "
                "AND j.task_id=a.task_id AND j.status IN ('queued','running','interrupted')) "
                "ORDER BY coalesce(a.last_checked_at,'') LIMIT ?",
                (due_before.isoformat(), limit)).fetchall()
            return [self.get_task_authorization(row["workspace_id"], row["task_id"], connection=conn) for row in rows]

    def task_authorization_material_scope(self, workspace_id, task_id):
        with self._connect() as conn:
            row = conn.execute("SELECT r.material_scope_json FROM creation_request_items i "
                "JOIN creation_request_windows w ON w.id=i.window_id JOIN creation_requests r ON r.id=w.request_id "
                "WHERE r.workspace_id=? AND i.task_id=?", (workspace_id,task_id)).fetchone()
        return json.loads(row[0]) if row is not None else None

    def task_source_revisions(self, workspace_id, knowledge_ids, source_ids, *, connection=None):
        """Finite source facts for this goal, re-read on the owner's write connection."""
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            knowledge=[list(row) for row in conn.execute("SELECT id,version,content_hash,use_state,visibility,owner_user_id "
                "FROM knowledge_items WHERE workspace_id=? AND id IN (SELECT value FROM json_each(?)) ORDER BY id",
                (workspace_id,json.dumps(knowledge_ids)))] if knowledge_ids else []
            sources=[list(row) for row in conn.execute("SELECT id,use_state FROM source_items "
                "WHERE workspace_id=? AND id IN (SELECT value FROM json_each(?)) ORDER BY id",
                (workspace_id,json.dumps(source_ids)))] if source_ids else []
        if len(knowledge)!=len(set(knowledge_ids)) or len(sources)!=len(set(source_ids)):
            raise PermissionError("原目标的固定资料已不可用")
        return {"knowledge":knowledge,"sources":sources}

    def task_authorization_goal_revision(self, workspace_id, task_id, *, connection=None):
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            row = conn.execute("SELECT json_extract(payload,'$.plan_id') FROM creation_tasks WHERE workspace_id=? AND id=?", (workspace_id,task_id)).fetchone()
            if row is None:
                raise KeyError(task_id)
            keys = ("id","goal","target_count","language","aspect_ratio","duration_target_seconds")
            plan_row = conn.execute("SELECT "+",".join("json_extract(payload,'$."+key+"')" for key in keys)+
                " FROM creation_plans WHERE workspace_id=? AND id=?", (workspace_id,row[0])).fetchone()
            # Window creation/confirmation also advances the request's optimistic
            # revision. Bind goal contents so another bounded window cannot revoke
            # this already approved task by merely advancing that cursor.
            origin = conn.execute("SELECT r.id,r.goal,r.target_count,r.output_relation,r.requirements_json,r.material_scope_json FROM creation_request_items i "
                "JOIN creation_request_windows w ON w.id=i.window_id JOIN creation_requests r ON r.id=w.request_id "
                "WHERE r.workspace_id=? AND i.task_id=?", (workspace_id,task_id)).fetchone()
        identity = dict(zip(keys, plan_row, strict=True))
        identity["request"] = list(origin) if origin is not None else None
        return hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

    def interrupt_creation_execution(
        self, event: _TaskEvent, *, impact: str, repair_action: str,
        task_is_current: Callable[[sqlite3.Connection], bool],
        expected_free_permit_generation: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Save one owner's interrupted job, current stage and receipt together."""
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.interrupt_creation_execution(event, impact=impact,
                    repair_action=repair_action, task_is_current=task_is_current,
                    expected_free_permit_generation=expected_free_permit_generation,
                    connection=conn)
            return
        if expected_free_permit_generation is not None:
            permit = connection.execute("SELECT state,generation FROM device_permit WHERE id=1").fetchone()
            if permit is None or (permit["state"], permit["generation"]) != ("free", expected_free_permit_generation):
                raise ValueError("设备许可在制作恢复前已变化，原作业保持待核查")
        row = connection.execute("SELECT * FROM job_runs WHERE workspace_id=? AND id=?",
                                 (event.workspace_id, event.job_id)).fetchone()
        if row is None or row["task_id"] != event.task_id:
            raise ValueError("中断回执不属于当前制作作业")
        if row["status"] not in {"queued", "running", "interrupted"}:
            return  # A stopped dispatcher cannot overwrite a committed terminal result.
        reason = event.payload["reason"]
        now = event.created_at.isoformat()
        connection.execute(
            "UPDATE job_runs SET status='interrupted',failure_reason=?,impact=?,repair_action=?,"
            "finished_at=?,updated_at=? WHERE workspace_id=? AND id=?",
            (reason, impact, repair_action, now, now, event.workspace_id, event.job_id))
        stage = event.payload["stage"]
        task_stage = "narration" if stage == "narration" else "montage"
        if (row["kind"] == "creation_task_execution"
                and stage in {"narration", "person_render", "montage_plan", "render"}
                and task_is_current(connection)):
            task_row = connection.execute("SELECT payload FROM creation_tasks WHERE workspace_id=? AND id=?",
                                          (event.workspace_id, event.task_id)).fetchone()
            payload = json.loads(task_row["payload"])
            if payload["stage"] == task_stage and payload["stage_status"] == "running":
                payload.update(stage_status="not_started", stage_reason=reason,
                    stage_impact=impact, stage_repair_action=repair_action, stage_retryable=True,
                    recovery_point=task_stage, updated_at=now)
                connection.execute("UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?",
                    (json.dumps(payload, ensure_ascii=False), now, event.workspace_id, event.task_id))
        self.execution_records.append_task_event(event, connection=connection)

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














    def save_narration_segments(
        self,
        workspace_id: str,
        task_id: str,
        script_id: str,
        segments: list[_NarrationSegment],
        *, connection: sqlite3.Connection | None = None,
    ) -> list[_NarrationSegment]:
        """Persist the complete current narration fact set for one confirmed script."""
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            self._upsert_narration_segments(
                conn,
                workspace_id=workspace_id,
                task_id=task_id,
                script_id=script_id,
                segments=segments,
            )
        return self.list_narration_segments(workspace_id, task_id, script_id, connection=connection)

    def get_narration_segment(
        self, workspace_id: str, task_id: str, segment_id: str, *, connection=None
    ) -> _NarrationSegment:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            self._require_creation_task(conn, workspace_id, task_id)
            row = narration_segment_row(conn, workspace_id, task_id, segment_id)
        if row is None:
            raise KeyError(segment_id)
        return self._narration_segment_from_row(row)

    def iter_narration_segments(self, workspace_id, task_id, script_id, *, connection):
        """Original manifest order; the caller owns the entire read snapshot."""
        if not connection.in_transaction:
            raise ValueError('配音行读取必须属于原事务')
        task = self._require_creation_task(connection, workspace_id, task_id)
        if task.confirmed_script_id != script_id:
            raise ValueError('配音行不属于当前确认文案')
        cursor = connection.execute('SELECT * FROM narration_segments WHERE workspace_id=? AND task_id=? '
            'AND script_id=? ORDER BY created_at ASC,id ASC', (workspace_id, task_id, script_id))
        try:
            for row in cursor:
                yield self._narration_segment_from_row(row)
        finally:
            cursor.close()

    def iter_narration_edit_segments(self, task, replacement, *, connection):
        """Validate the whole set, then emit the real prospective rows in script order."""
        for _ in self._iter_section_change_segments(task, replacement, connection=connection, adopted=True):
            pass
        header, _ = self.script_records.read_execution_script_metadata(
            connection, task.workspace_id, task.confirmed_script_id)
        for _, segment in self.iter_creation_narration_sections(task, header, connection=connection):
            yield replacement if segment.id == replacement.id else segment

    def list_narration_segments(
        self, workspace_id: str, task_id: str, script_id: str | None = None,
        *, connection: sqlite3.Connection | None = None,
    ) -> list[_NarrationSegment]:
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            task = self._require_creation_task(conn, workspace_id, task_id)
            current_script_id = script_id or task.confirmed_script_id
            if current_script_id is None:
                return []
            rows = narration_segment_rows(conn, workspace_id, task_id, current_script_id)
        return [self._narration_segment_from_row(row) for row in rows]

    def create_person_render(self, render: PersonRender) -> PersonRender:
        return self._person_renders.create_person_render(render)

    def claim_person_render(
        self, workspace_id: str, task_id: str, render_id: str
    ) -> PersonRender:
        return self._person_renders.claim_person_render(workspace_id, task_id, render_id)

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
        return self._person_renders.finish_person_render(workspace_id, task_id, render_id, status=status, output_path=output_path, duration_seconds=duration_seconds, alignment_evidence=alignment_evidence, failure_reason=failure_reason, impact=impact, repair_action=repair_action)

    def bind_person_render_artifact(
        self,
        workspace_id: str,
        task_id: str,
        render_id: str,
        artifact_ref: str,
    ) -> PersonRender:
        return self._person_renders.bind_person_render_artifact(workspace_id, task_id, render_id, artifact_ref)

    def get_person_render(
        self, workspace_id: str, task_id: str, render_id: str
    ) -> PersonRender:
        return self._person_renders.get_person_render(workspace_id, task_id, render_id)

    def list_person_renders(
        self, workspace_id: str, task_id: str
    ) -> list[PersonRender]:
        return self._person_renders.list_person_renders(workspace_id, task_id)

    def interrupt_running_person_renders(self, workspace_id: str | None = None) -> int:
        return self._person_renders.interrupt_running_person_renders(workspace_id)

    def create_subtitle_track(
        self, track: _SubtitleTrack, *, connection: sqlite3.Connection | None = None,
    ) -> _SubtitleTrack:
        _validate_subtitle_track(track)
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
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
        return self.get_subtitle_track(track.workspace_id, track.task_id, track.id, connection=connection)

    def get_subtitle_track(
        self, workspace_id: str, task_id: str, track_id: str, *, connection: sqlite3.Connection | None = None,
    ) -> _SubtitleTrack:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
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
        connection: sqlite3.Connection | None = None,
    ) -> list[_SubtitleTrack]:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
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

    def create_cover_asset(
        self, cover: _CoverAsset, *, connection: sqlite3.Connection | None = None,
    ) -> _CoverAsset:
        _validate_cover_asset(cover)
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
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
        return self.get_cover_asset(cover.workspace_id, cover.task_id, cover.id, connection=connection)

    def get_cover_asset(
        self, workspace_id: str, task_id: str, cover_id: str, *, connection: sqlite3.Connection | None = None,
    ) -> _CoverAsset:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
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
    ) -> list[_CoverAsset]:
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

    def save_clip_preview_proposal(self, intent: _EditIntent, proposal: _EditProposal, *, expected_version_id: str):
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

    def create_cover_generation_job(self, run: _JobRun, payload: dict, *, expected_version_id: str) -> _JobRun:
        """Save the original cover Job and sealed identity/input in one transaction."""
        command = payload["command"]
        operation = json.loads(command["operation_json"])
        identity = json.loads(command["identity_json"])
        if (run.kind != "cover_frame_generation" or run.idempotency_key != f"cover:{command['proposal_id']}"
                or (run.workspace_id, run.task_id) != (command["workspace_id"], command["task_id"])
                or run.input_digest != hashlib.sha256(command["operation_json"].encode()).hexdigest()
                or operation["base_version_id"] != expected_version_id
                or identity != {key: payload[key] for key in identity}):
            raise ValueError("封面Job与原封存输入或身份不一致")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._require_creation_task(conn, run.workspace_id, run.task_id)
            if task.current_timeline_version_id != expected_version_id:
                raise TimelineVersionConflict("提交封面前时间线发生变化，请重新预览")
            if task.owner_user_id and task.owner_user_id != (payload.get("user_id") or payload["member_id"]):
                raise PermissionError("当前成员不能安排此任务的封面")
            proposal = self.get_edit_proposal(run.workspace_id, command["proposal_id"])
            original = proposal.operation.get("payload", {})
            normalized = {**original, "title_position": original.get("title_position", "bottom")}
            if (proposal.task_id != run.task_id or proposal.status != "proposed"
                    or proposal.operation.get("operation_type") != "GenerateCoverFromFrame"
                    or normalized != operation):
                raise ValueError("封面原提案已经变化")
            job = self.create_job_run(run, connection=conn)
            self.execution_records.append_task_event(_TaskEvent(id=f"event_{job.id}_cover_start", workspace_id=job.workspace_id,
                task_id=job.task_id, job_id=job.id, event_type="cover_submitted", payload=payload,
                created_at=job.created_at), connection=conn)
        return job

    def create_clip_preview_job(self, run: _JobRun, payload: dict, *, expected_version_id: str) -> _JobRun:
        """Compare the caller's original version and save the job/input together."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._require_creation_task(conn, run.workspace_id, run.task_id)
            if task.current_timeline_version_id != expected_version_id:
                raise TimelineVersionConflict("提交预览前时间线发生变化，请重新预览")
            if task.owner_user_id and task.owner_user_id != (payload.get("user_id") or payload["member_id"]):
                raise PermissionError("当前成员不能安排此任务的媒体处理")
            job = self.create_job_run(run, connection=conn)
            self.execution_records.append_task_event(_TaskEvent(id=f"event_{job.id}_start", workspace_id=job.workspace_id,
                task_id=job.task_id, job_id=job.id, event_type="task_media_submitted", payload=payload,
                created_at=job.created_at), connection=conn)
        return job

    def record_task_media_publication(self, artifact: _ArtifactRecord, transform: _TransformRun, edges: list[_DependencyEdge]):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.execution_records.create_artifact_record(artifact, connection=conn)
            self.lineage_records.create_transform_run(transform, connection=conn)
            for edge in edges:
                self.lineage_records.create_dependency_edge(edge, connection=conn)
        return artifact

    def create_edit_intent(self, intent: _EditIntent, *, connection: sqlite3.Connection | None = None, source: dict | None | Literal[False] = False) -> _EditIntent:
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

    def get_edit_intent(self, workspace_id: str, intent_id: str) -> _EditIntent:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM edit_intents WHERE workspace_id = ? AND id = ?",
                (workspace_id, intent_id),
            ).fetchone()
        if row is None:
            raise KeyError(intent_id)
        return self._edit_intent_from_row(row)

    def goal_completed_source_rows(self, workspace_id, task_id, conversation_id, member_id):
        with self._connect() as conn:
            return conn.execute("SELECT j.id,e.payload_json FROM job_runs j JOIN task_events e "
                "ON e.workspace_id=j.workspace_id AND e.job_id=j.id WHERE j.workspace_id=? AND j.task_id=? "
                "AND j.status='succeeded' AND e.event_type='task_media_submitted' "
                "AND json_extract(e.payload_json,'$.result_kind')='graphic_source' "
                "AND json_extract(e.payload_json,'$.operation') IN ('media_script_preview','media_script_tool_preview') "
                "AND json_extract(e.payload_json,'$.arguments._typed_source.conversation_id')=? "
                "AND json_extract(e.payload_json,'$.arguments._typed_source.member_id')=? ORDER BY j.created_at,j.id",
                (workspace_id, task_id, conversation_id, member_id)).fetchall()

    def goal_pending_preview_rows(self, workspace_id, task_id, timeline_version_id, conversation_id, member_id):
        with self._connect() as conn:
            proposals = conn.execute("SELECT p.id,i.constraints_json FROM edit_proposals p JOIN edit_intents i "
                "ON i.workspace_id=p.workspace_id AND i.id=p.intent_id WHERE p.workspace_id=? AND p.task_id=? "
                "AND p.status='proposed' AND json_extract(p.operation_json,'$.payload.base_version_id') IS ? "
                "AND json_extract(i.constraints_json,'$.request_source.conversation_id')=? "
                "AND json_extract(i.constraints_json,'$.request_source.member_id')=? ORDER BY p.created_at",
                (workspace_id, task_id, timeline_version_id, conversation_id, member_id)).fetchall()
            jobs = conn.execute("SELECT j.id,e.payload_json FROM job_runs j JOIN task_events e "
                "ON e.workspace_id=j.workspace_id AND e.job_id=j.id WHERE j.workspace_id=? AND j.task_id=? "
                "AND e.event_type='task_media_submitted' AND j.status IN ('queued','running','interrupted') "
                "AND json_extract(e.payload_json,'$.result_kind')='edit_preview' "
                "AND json_extract(e.payload_json,'$.arguments._typed_source.expected_timeline_version_id') IS ? "
                "AND json_extract(e.payload_json,'$.arguments._typed_source.conversation_id')=? "
                "AND json_extract(e.payload_json,'$.arguments._typed_source.member_id')=? ORDER BY j.created_at",
                (workspace_id, task_id, timeline_version_id, conversation_id, member_id)).fetchall()
        return proposals, jobs

    def goal_request_effect_rows(self, workspace_id, task_id, request_id):
        with self._connect() as conn:
            jobs = conn.execute("SELECT 1 FROM task_events WHERE workspace_id=? AND task_id=? "
                "AND event_type='task_media_submitted' AND json_extract(payload_json,'$.arguments._typed_source.request_id')=? LIMIT 1",
                (workspace_id, task_id, request_id)).fetchone()
            proposals = conn.execute("SELECT 1 FROM edit_intents WHERE workspace_id=? AND task_id=? "
                "AND json_extract(constraints_json,'$.request_source.request_id')=? LIMIT 1",
                (workspace_id, task_id, request_id)).fetchone()
        return jobs, proposals

    def assistant_execution_projection_rows(self):
        with self._connect() as conn:
            return conn.execute("""SELECT e.*,j.status FROM task_events e JOIN job_runs j
                ON j.workspace_id=e.workspace_id AND j.id=e.job_id
                WHERE e.event_type='assistant_execution_located' AND j.kind='creation_task_execution'
                AND j.status IN ('succeeded','failed','cancelled')
                AND NOT EXISTS(SELECT 1 FROM task_events done WHERE done.id='event_assistant_execution_'||e.id||'_'||j.status)
                ORDER BY e.created_at LIMIT 16""").fetchall()

    def goal_unreleased_job_request(self, workspace_id, job_id):
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM device_work_requests WHERE workspace_id=? AND owner_id=? "
                "AND state IN ('granted','yield_requested')", (workspace_id, job_id)).fetchone()

    def goal_render_request_row(self, workspace_id, request_id):
        with self._connect() as conn:
            return conn.execute("SELECT owner_id,state FROM device_work_requests WHERE workspace_id=? AND id=?",
                (workspace_id, request_id)).fetchone() if request_id else None

    def goal_output_provenance_rows(self, workspace_id, task_id, request_id, conversation_id, member_id, media_kind,
                                    *, input_id=None, input_turn_id=None):
        with self._connect() as conn:
            rendered = conn.execute("SELECT owner_id,state FROM device_work_requests WHERE workspace_id=? AND id=?",
                (workspace_id, request_id)).fetchone()
            located = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? "
                "AND event_type='assistant_execution_located' AND job_id=? "
                "AND json_extract(payload_json,'$.source.conversation_id')=? AND json_extract(payload_json,'$.source.member_id')=? "
                "AND json_extract(payload_json,'$.source.input_id') IS ? AND json_extract(payload_json,'$.source.input_turn_id') IS ?",
                (workspace_id, task_id, rendered["owner_id"] if rendered else None, conversation_id, member_id,
                 input_id, input_turn_id)).fetchone()
            pending = conn.execute("SELECT e.payload_json FROM job_runs j JOIN task_events e "
                "ON e.workspace_id=j.workspace_id AND e.id='event_' || j.id || '_start' "
                "WHERE j.workspace_id=? AND j.task_id=? AND j.kind=? AND j.status IN ('queued','running','interrupted') "
                "AND json_extract(e.payload_json,'$.arguments._typed_source.conversation_id')=? "
                "AND json_extract(e.payload_json,'$.arguments._typed_source.member_id')=?",
                (workspace_id, task_id, media_kind, conversation_id, member_id)).fetchall()
            unknown = conn.execute("SELECT payload_json FROM task_events WHERE workspace_id=? AND task_id=? "
                "AND event_type='assistant_edit_request_located' AND json_extract(payload_json,'$.source.conversation_id')=? "
                "AND json_extract(payload_json,'$.source.member_id')=? "
                "AND json_extract(payload_json,'$.locator.status') IN ('unknown','result_unknown')",
                (workspace_id, task_id, conversation_id, member_id)).fetchall()
        return rendered, located, pending, unknown

    def assistant_script_tool_projection_rows(self):
        with self._connect() as conn:
            return conn.execute(
                "SELECT e.* FROM task_events e JOIN job_runs j ON j.workspace_id=e.workspace_id "
                "AND j.id=json_extract(e.payload_json,'$.registration_id') "
                "WHERE e.event_type='assistant_script_tool_located' AND j.kind='media_script_tool_registration' "
                "AND j.status='succeeded' AND NOT EXISTS (SELECT 1 FROM task_events done "
                "WHERE done.id='event_assistant_script_tool_'||e.id) ORDER BY e.created_at LIMIT 32",
            ).fetchall()

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
    ) -> list[_EditIntent]:
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

    def create_edit_proposal(self, proposal: _EditProposal, *, connection: sqlite3.Connection | None = None) -> _EditProposal:
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
        self, workspace_id: str, proposal_id: str, *, connection=None
    ) -> _EditProposal:
        with (self._connect() if connection is None else nullcontext(connection)) as conn:
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
    ) -> list[_EditProposal]:
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
    ) -> _EditProposal:
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
            return _ClipSourceSelection(0., render.duration_seconds, True)
        asset = self.get_asset(workspace_id, values.get("asset_id") or clip.asset_id)
        segment_id = values.get("segment_id") or clip.segment_id
        segment = next((item for item in asset.segments if item.id == segment_id), None)
        if not _content_readable(user_id=user_id, local_diagnostic=local_diagnostic,
                visibility=asset.visibility, owner_user_id=asset.owner_user_id):
            raise PermissionError("当前成员无权使用该素材")
        if segment is None:
            raise KeyError("material segment not found")
        eligibility=material_usage_eligibility(asset,segment,purpose='automatic' if values.get('automation_only') else 'manual')
        if not eligibility.eligible:
            raise TimelineVersionConflict(f'{eligibility.status}：{eligibility.reason}；{eligibility.repair_action}')
        if not material_segment_eligible(asset, segment, workspace_id, automation_only=bool(values.get('automation_only'))):
            raise TimelineVersionConflict("素材片段当前不可用，请检查来源、范围和使用限制")
        speech = effective_segment_usage_role(asset, segment) == "a_roll"
        return _ClipSourceSelection(segment.start_seconds, segment.end_seconds, speech,
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
            if not _content_readable(user_id=user_id, local_diagnostic=local_diagnostic,
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

    def claim_edit_request(self, task, member_id, request_id, phase, inputs, *, write_guard=None):
        identity = hashlib.sha256(json.dumps(
            [task.workspace_id, member_id, str(request_id), phase], sort_keys=True,
            ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()
        event_id = f"event_edit_request_{identity}"
        digest = hashlib.sha256(json.dumps([task.id, inputs], sort_keys=True,
            ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            row = conn.execute("SELECT payload_json FROM task_events WHERE id=?", (event_id,)).fetchone()
            if row:
                previous = json.loads(row["payload_json"])
                if previous["input_digest"] != digest:
                    raise ValueError("请求身份已绑定不同编辑输入")
                return event_id, False
            self.execution_records.append_task_event(_TaskEvent(id=event_id, workspace_id=task.workspace_id,
                task_id=task.id, job_id=None, event_type="edit_request_claimed",
                payload={"member_id": member_id, "request_id": str(request_id), "phase": phase,
                         "input_digest": digest, "inputs": inputs}, created_at=utc_now()), connection=conn)
        return event_id, True

    def save_edit_request_result(self, task, event_id, *, status_code, response):
        return self.execution_records.append_task_event(_TaskEvent(id=event_id + "_result", workspace_id=task.workspace_id,
            task_id=task.id, job_id=None, event_type="edit_request_result",
            payload={"status_code": status_code, "response": response}, created_at=utc_now()))

    def read_edit_request_result(self, task, event_id):
        try:
            event = self.execution_records.get_task_event(task.workspace_id, event_id + "_result")
        except KeyError:
            return None
        return event.payload

    def confirm_direct_edit(self, command, intent: _EditIntent, proposal: _EditProposal, change, *, verify_sources, connection=None):
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
        result = self.edit_adoption.confirm(command.workspace_id, command.task_id, proposal.id, operation=change.operation, result_version=change.version, montage_plan_id=change.montage_plan_id, montage_payload=change.montage_payload, subtitle_tracks=change.subtitle_tracks, cover_asset=change.cover_asset, narration_segments=change.narration_segments, narration_replacement=change.narration_replacement, narration_artifact=change.narration_artifact, connection=conn)
        return result


    def create_edit_operation(self, operation: _EditOperation) -> _EditOperation:
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
    ) -> _EditOperation:
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
    ) -> _EditOperation:
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
    ) -> list[_EditOperation]:
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
        conn: sqlite3.Connection, operation: _EditOperation
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
    ) -> _CreationTask:
        return require_creation_task(conn, workspace_id, task_id)

    def _upsert_narration_segments(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        task_id: str,
        script_id: str,
        segments: list[_NarrationSegment],
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
            json.loads(str(script_row["payload"])), connection=conn
        )
        if script.selection_status != "confirmed":
            raise ValueError("配音分段只能关联已确认文案")
        script_digest = self.confirmed_creation_task_script_digest(
            workspace_id, task_id, script_id,
        )
        sections = {section.id: section for section in script.sections}
        for section_id, section in tuple(sections.items()):
            adopted = self.stage_segment_state(workspace_id, task_id, section_id, connection=conn)
            if adopted is not None and adopted['script_id'] == script_id:
                sections[section_id] = replace(section, content=adopted['text'])
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










    @staticmethod
    def _lineage_job_belongs(connection, workspace_id, task_id, job_id):
        row = connection.execute(
            "SELECT task_id FROM job_runs WHERE workspace_id = ? AND id = ?",
            (workspace_id, job_id),
        ).fetchone()
        return row is not None and str(row["task_id"]) == task_id


    def _write_topic_task(self, connection, task):
        if not connection.in_transaction:
            raise ValueError("选题任务状态保存必须属于原事务")
        connection.execute(
            "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
            (_json_payload(task), task.updated_at.isoformat(), task.workspace_id, task.id),
        )

    def _topic_plan_intent(self, connection, workspace_id, plan_id):
        row = connection.execute(
            "SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?",
            (workspace_id, plan_id),
        ).fetchone()
        plan = self._creation_plan_from_payload(json.loads(row["payload"]))
        return plan.creation_intent

    def _script_task_transition(self, conn, task, transition, now, *, script_id=None,
                                reason=None, impact=None, repair_action=None, retryable=None):
        if not conn.in_transaction:
            raise ValueError("文案引起的任务状态转换必须属于原事务")
        if transition == "started":
            task.stage = "script"
            task.stage_status = task.status = "running"
            task.stage_reason = task.stage_impact = task.stage_repair_action = task.stage_retryable = None
        elif transition == "saved":
            task.stage_status = task.status = "waiting_for_user"
            task.progress = max(task.progress,35)
        elif transition == "transferred":
            if task.stage_status == "not_started":
                task.stage_status = task.status = "waiting_for_user"
                task.progress = max(task.progress,35)
        elif transition == "failed":
            task.stage_status = task.status = "failed"
            task.recovery_point = "script"
            task.stage_reason,task.stage_impact = reason,impact
            task.stage_repair_action,task.stage_retryable = repair_action,retryable
        elif transition == "adopted":
            task.confirmed_script_id=script_id
            task.stage,task.stage_status,task.status="narration","not_started","running"
            task.progress=max(task.progress,40)
            task.recovery_point="narration"
            task.stage_reason=task.stage_impact=task.stage_repair_action=task.stage_retryable=None
        else:
            raise ValueError("未知文案任务转换")
        task.updated_at=now
        conn.execute("UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?",
            (_json_payload(task),now.isoformat(),task.workspace_id,task.id))

    def _create_script_library_task(self, conn, workspace_id, plan, now):
        if not conn.in_transaction: raise ValueError("文案库复用必须属于原事务")
        task=_CreationTask(id=f"task_{uuid4().hex[:12]}",workspace_id=workspace_id,plan_id=plan.id,
            name=plan.name,owner_user_id=plan.owner_user_id,scenario="script_library_reuse",stage="script",stage_status="waiting_for_user",
            status="running",progress=20,recovery_point="script")
        plan.creation_task_id=task.id
        plan.confirmed_at=now
        conn.execute("INSERT INTO creation_plans (id,workspace_id,payload,visibility,updated_at) VALUES (?,?,?,?,?)",
            (plan.id,workspace_id,_json_payload(plan),plan.visibility,plan.updated_at.isoformat()))
        conn.execute("INSERT INTO creation_tasks (id,workspace_id,plan_id,payload,visibility,updated_at) VALUES (?,?,?,?,?,?)",
            (task.id,workspace_id,plan.id,_json_payload(task),task.visibility,task.updated_at.isoformat()))
        return task

    def generation_run_state(self, connection, workspace_id, task_id, run_id):
        return self.script_records.generation_run_state(connection,workspace_id,task_id,run_id)

    def start_script_generation(self, run: _ScriptGenerationRun, *, write_guard=None, connection=None) -> tuple[_CreationTask, _ScriptGenerationRun]:
        return self.script_records.start_script_generation(run, write_guard=write_guard, connection=connection)

    def transfer_completed_assistant_reply_to_script(self, workspace_id: str, task_id: str, *, request_id: str, reply_reference: dict[str, str], plan_conversion_request_id: str | None=None, reply_text: str, expected_plan_id: str, expected_plan_updated_at: datetime | str, expected_topic_id: str, expected_task_updated_at: datetime | str, input_references: list[dict[str, object]], script_id: str, run_id: str, title: str, language: Literal['zh-CN', 'en-US', 'zh-CN/en-US'], sections: list[_CreationTaskScriptSection], replace_existing_script_id: str | None=None, expected_script_updated_at: datetime | str | None=None, verified_owner_user_id: str | None=None) -> tuple[_CreationTask, _ScriptGenerationRun, _CreationTaskScript]:
        return self.script_records.transfer_completed_assistant_reply_to_script(workspace_id, task_id, request_id=request_id, reply_reference=reply_reference, plan_conversion_request_id=plan_conversion_request_id, reply_text=reply_text, expected_plan_id=expected_plan_id, expected_plan_updated_at=expected_plan_updated_at, expected_topic_id=expected_topic_id, expected_task_updated_at=expected_task_updated_at, input_references=input_references, script_id=script_id, run_id=run_id, title=title, language=language, sections=sections, replace_existing_script_id=replace_existing_script_id, expected_script_updated_at=expected_script_updated_at, verified_owner_user_id=verified_owner_user_id)

    def _require_assistant_reply_plan_event(self, conn: sqlite3.Connection, *, workspace_id: str, task_id: str, plan_id: str, request_id: str, reply_reference: dict[str, str], reply_owner_user_id: str | None) -> None:
        return self.script_records._require_assistant_reply_plan_event(conn, workspace_id=workspace_id, task_id=task_id, plan_id=plan_id, request_id=request_id, reply_reference=reply_reference, reply_owner_user_id=reply_owner_user_id)

    @staticmethod
    def _validate_assistant_reply_inputs(plan: CreationPlan, input_references: dict[str, dict[str, object]]):
        return ScriptRecordStore._validate_assistant_reply_inputs(plan, input_references)

    def save_reviewed_script_generation(self, run, script, *, write_guard=None, result_write=None):
        return self.script_records.save_reviewed_script_generation(run, script, write_guard=write_guard, result_write=result_write)

    def complete_script_generation(self, run_id: str, script: _CreationTaskScript, *, write_guard=None, result_write=None, connection=None) -> tuple[_CreationTask, _ScriptGenerationRun, _CreationTaskScript]:
        return self.script_records.complete_script_generation(run_id, script, write_guard=write_guard, result_write=result_write, connection=connection)

    def fail_script_generation(self, run_id: str, *, reason: str, impact: str, repair_action: str, retryable: bool, provider_attempted: bool=False, provider_failed: bool=True, connection=None) -> tuple[_CreationTask, _ScriptGenerationRun]:
        return self.script_records.fail_script_generation(run_id, reason=reason, impact=impact, repair_action=repair_action, retryable=retryable, provider_attempted=provider_attempted, provider_failed=provider_failed, connection=connection)

    def get_script_generation_run(self, workspace_id: str, run_id: str, *, connection=None) -> _ScriptGenerationRun:
        if connection is None:
            return self.script_records.get_script_generation_run(workspace_id, run_id)
        row = connection.execute('SELECT payload FROM script_generation_runs WHERE workspace_id=? AND id=?',
            (workspace_id,run_id)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._script_generation_run_from_payload(json.loads(row[0]))

    def latest_script_generation_run(self, workspace_id: str, task_id: str) -> _ScriptGenerationRun | None:
        return self.script_records.latest_script_generation_run(workspace_id, task_id)

    def list_creation_task_scripts(self, workspace_id: str, task_id: str) -> list[_CreationTaskScript]:
        return self.script_records.list_creation_task_scripts(workspace_id, task_id)

    def get_creation_task_script(self, workspace_id: str, script_id: str) -> _CreationTaskScript:
        return self.script_records.get_creation_task_script(workspace_id, script_id)

    def active_creation_task_script_headers(self, workspace_id, task_id):
        return self.script_records.active_creation_task_script_headers(workspace_id, task_id)

    def read_creation_task_script_sections(self, workspace_id, task_id, script_id, *, expected_updated_at=None, after_ordinal=0, limit=SCRIPT_SECTION_WINDOW_LIMIT):
        return self.script_records.read_creation_task_script_sections(workspace_id, task_id, script_id, expected_updated_at=expected_updated_at, after_ordinal=after_ordinal, limit=limit)

    def confirmed_creation_task_script_digest(self, workspace_id: str, task_id: str, script_id: str | None=None) -> str | None:
        return self.script_records.confirmed_creation_task_script_digest(workspace_id, task_id, script_id)

    def record_assistant_script_save(self, script, source, request_digest, *, connection):
        return self.script_records.record_assistant_script_save(script, source, request_digest, connection=connection)

    def _mutable_assistant_script_metadata(self, conn, workspace_id, task_id, script_id, sections, expected_updated_at):
        return self.script_records._mutable_assistant_script_metadata(conn, workspace_id, task_id, script_id, sections, expected_updated_at)

    def update_assistant_script_draft(self, workspace_id: str, task_id: str, script_id: str, *, title: str, sections: list[_CreationTaskScriptSection], expected_updated_at: datetime | str | None=None, write_guard=None, result_write=None) -> _CreationTaskScript:
        return self.script_records.update_assistant_script_draft(workspace_id, task_id, script_id, title=title, sections=sections, expected_updated_at=expected_updated_at, write_guard=write_guard, result_write=result_write)

    @staticmethod
    def _window_claim_key(task_id, script_id, operation, request_id, member_id):
        return ScriptRecordStore._window_claim_key(task_id, script_id, operation, request_id, member_id)

    def claim_script_window_request(self, workspace_id, task_id, script_id, operation, request_id, member_id, input_digest, *, submitted_input, write_guard=None):
        return self.script_records.claim_script_window_request(workspace_id, task_id, script_id, operation, request_id, member_id, input_digest, submitted_input=submitted_input, write_guard=write_guard)

    def end_script_window_request_with_error(self, workspace_id, task_id, script_id, operation, request_id, member_id, *, error_detail=None):
        return self.script_records.end_script_window_request_with_error(workspace_id, task_id, script_id, operation, request_id, member_id, error_detail=error_detail)

    def pending_script_window_request(self, workspace_id, task_id, script_id, operation, request_id, member_id, *, include_input=False):
        return self.script_records.pending_script_window_request(workspace_id, task_id, script_id, operation, request_id, member_id, include_input=include_input)

    def script_window_start_result(self, workspace_id, task_id, request_id, member_id):
        return self.script_records.script_window_start_result(workspace_id, task_id, request_id, member_id)

    def record_script_window_start(self, script, request_id, member_id, request_digest, binding_digest, *, connection):
        return self.script_records.record_script_window_start(script, request_id, member_id, request_digest, binding_digest, connection=connection)

    def saved_script_review_context(self, workspace_id, task_id, script_id, expected_updated_at):
        return self.script_records.saved_script_review_context(workspace_id, task_id, script_id, expected_updated_at)

    def record_saved_script_section_review(self, workspace_id, task_id, script_id, sections, expected_updated_at, *, binding_digest, layout_digest, write_guard=None):
        return self.script_records.record_saved_script_section_review(workspace_id, task_id, script_id, sections, expected_updated_at, binding_digest=binding_digest, layout_digest=layout_digest, write_guard=write_guard)

    def script_confirm_saved_result(self, workspace_id, task_id, script_id, request_id, member_id):
        return self.script_records.script_confirm_saved_result(workspace_id, task_id, script_id, request_id, member_id)

    def confirmed_script_adoption_facts(self, workspace_id, task_id, script_id, expected_updated_at):
        return self.script_records.confirmed_script_adoption_facts(workspace_id, task_id, script_id, expected_updated_at)

    def confirm_reviewed_script(self, workspace_id, task_id, script_id, *, expected_updated_at, request_id, member_id, binding_digest, layout_digest, adoption_kind='manual', write_guard=None):
        return self.script_records.confirm_reviewed_script(workspace_id, task_id, script_id, expected_updated_at=expected_updated_at, request_id=request_id, member_id=member_id, binding_digest=binding_digest, layout_digest=layout_digest, adoption_kind=adoption_kind, write_guard=write_guard)

    def script_patch_request_result(self, workspace_id, task_id, script_id, request_id, member_id):
        return self.script_records.script_patch_request_result(workspace_id, task_id, script_id, request_id, member_id)

    def script_section_patch_context(self, workspace_id, task_id, script_id, sections, expected_updated_at, *, insert_after_section_id=None):
        return self.script_records.script_section_patch_context(workspace_id, task_id, script_id, sections, expected_updated_at, insert_after_section_id=insert_after_section_id)

    def _record_script_section_review(self, conn, workspace_id, task_id, script_id, sections, *, binding_digest, layout_digest):
        return self.script_records._record_script_section_review(conn, workspace_id, task_id, script_id, sections, binding_digest=binding_digest, layout_digest=layout_digest)

    def script_sections_have_review(self, workspace_id, task_id, script_id, sections, *, binding_digest, layout_digest):
        return self.script_records.script_sections_have_review(workspace_id, task_id, script_id, sections, binding_digest=binding_digest, layout_digest=layout_digest)

    def patch_assistant_script_sections(self, workspace_id, task_id, script_id, *, sections, expected_updated_at, request_id, member_id, insert_after_section_id=None, write_guard=None, review_binding=None):
        return self.script_records.patch_assistant_script_sections(workspace_id, task_id, script_id, sections=sections, expected_updated_at=expected_updated_at, request_id=request_id, member_id=member_id, insert_after_section_id=insert_after_section_id, write_guard=write_guard, review_binding=review_binding)

    def save_script_library_entry(self, workspace_id: str, *, source_script_id: str, name: str, category: str) -> _ScriptLibraryEntry:
        return self.script_records.save_script_library_entry(workspace_id, source_script_id=source_script_id, name=name, category=category)

    def _script_library_entry_from_payload(self, data, *, connection=None):
        return self.script_records._script_library_entry_from_payload(data,connection=connection)

    def get_script_library_entry(self, workspace_id: str, entry_id: str) -> _ScriptLibraryEntry:
        return self.script_records.get_script_library_entry(workspace_id, entry_id)

    def list_script_library_entries(self, workspace_id: str, *, query: str='', category: str='') -> list[_ScriptLibraryEntry]:
        return self.script_records.list_script_library_entries(workspace_id, query=query, category=category)

    def reuse_script_library_entry(self, workspace_id: str, entry_id: str, *, plan: CreationPlan) -> tuple[_CreationTask, _CreationTaskScript]:
        return self.script_records.reuse_script_library_entry(workspace_id, entry_id, plan=plan)

    def _demote_other_confirmed_scripts(self, conn: sqlite3.Connection, *, workspace_id: str, task_id: str, confirmed_script_id: str, updated_at: datetime) -> None:
        return self.script_records._demote_other_confirmed_scripts(conn, workspace_id=workspace_id, task_id=task_id, confirmed_script_id=confirmed_script_id, updated_at=updated_at)

    def _script_adoption_context(self, conn, workspace_id, task_id, script_id, expected_updated_at, adoption_kind, member_id):
        return self.script_records._script_adoption_context(conn, workspace_id, task_id, script_id, expected_updated_at, adoption_kind, member_id)

    def _persist_script_adoption(self, conn, task, script_id, metadata, now):
        return self.script_records._persist_script_adoption(conn, task, script_id, metadata, now)

    def confirm_creation_task_script(self, workspace_id: str, task_id: str, script_id: str, *, title: str, sections: list[_CreationTaskScriptSection], expected_updated_at: datetime | str | None=None, adoption_kind: Literal['manual', 'delegated_system']='manual', verified_owner_user_id: str | None=None, write_guard=None, result_write=None) -> tuple[_CreationTask, _CreationTaskScript]:
        return self.script_records.confirm_creation_task_script(workspace_id, task_id, script_id, title=title, sections=sections, expected_updated_at=expected_updated_at, adoption_kind=adoption_kind, verified_owner_user_id=verified_owner_user_id, write_guard=write_guard, result_write=result_write)

    def start_creation_task_narration(
        self, workspace_id: str, task_id: str
    ) -> _CreationTask:
        return self._start_creation_task_stage(workspace_id, task_id, "narration")

    def start_creation_task_montage(
        self, workspace_id: str, task_id: str
    ) -> _CreationTask:
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

    @staticmethod
    def _narration_voice_selection_owner(task: _CreationTask, member_id: str) -> None:
        if not member_id or not _content_readable(user_id=member_id, local_diagnostic=False,
                visibility=task.visibility, owner_user_id=task.owner_user_id):
            raise KeyError(task.id)
        if task.owner_user_id and task.owner_user_id != member_id:
            raise PermissionError("当前成员不能更改此任务的配音声音。")

    def get_creation_task_narration_voice_selection(
        self, workspace_id: str, task_id: str, request_id: str, *, member_id: str,
    ) -> dict[str, object]:
        with self._connect() as conn:
            task = self._require_creation_task(conn, workspace_id, task_id)
            self._narration_voice_selection_owner(task, member_id)
            event_id = _assistant_reply_request_event_id("task_narration_voice_selected", workspace_id, request_id)
            row = conn.execute("SELECT task_id,payload_json FROM task_events WHERE workspace_id=? AND id=? AND event_type='task_narration_voice_selected'",
                (workspace_id, event_id)).fetchone()
            if row is None or row["task_id"] != task_id:
                raise KeyError(request_id)
            payload = json.loads(row["payload_json"])
            if payload["member_id"] != member_id:
                raise KeyError(request_id)
            return payload["receipt"]

    def select_creation_task_narration_voice(
        self,
        *,
        workspace_id: str,
        task_id: str,
        member_id: str,
        request_id: str,
        voice_profile_id: str,
        voice_version: str,
        expected_plan_updated_at: datetime | str,
        expected_task_updated_at: datetime | str,
        queued_job_rebind=None,
        voice_source_preparation=None,
        write_guard=None,
    ) -> dict[str, object]:
        """Select one fixed voice and record its original request in the same transaction."""
        if not request_id.strip():
            raise ValueError("选择配音声音需要稳定请求身份。")
        inputs = {"task_id": task_id, "member_id": member_id, "voice_profile_id": voice_profile_id,
            "voice_version": voice_version, "expected_plan_updated_at": _revision_value(expected_plan_updated_at),
            "expected_task_updated_at": _revision_value(expected_task_updated_at)}
        fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        event_id = _assistant_reply_request_event_id("task_narration_voice_selected", workspace_id, request_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if write_guard is not None:
                write_guard(conn)
            task = self._require_creation_task(conn, workspace_id, task_id)
            self._narration_voice_selection_owner(task, member_id)
            previous = conn.execute("SELECT task_id,payload_json FROM task_events WHERE id=?", (event_id,)).fetchone()
            if previous is not None:
                saved = json.loads(previous["payload_json"])
                if previous["task_id"] != task_id or saved["fingerprint"] != fingerprint:
                    raise ValueError("此声音采用请求已用于不同输入，请查询原请求。")
                return saved["receipt"]
            plan_row = conn.execute(
                "SELECT payload FROM creation_plans WHERE workspace_id = ? AND id = ?",
                (workspace_id, task.plan_id),
            ).fetchone()
            if plan_row is None:
                raise KeyError(task.plan_id)
            plan = self._creation_plan_from_payload(json.loads(str(plan_row["payload"])))
            if plan.creation_task_id != task.id:
                raise ValueError("创作方案与任务不一致")
            if (not _content_readable(user_id=member_id, local_diagnostic=False,
                    visibility=plan.visibility, owner_user_id=plan.owner_user_id)
                    or plan.owner_user_id and plan.owner_user_id != member_id):
                raise PermissionError("当前成员不能更改此创作方案的配音声音。")
            if (_revision_value(plan.updated_at) != inputs["expected_plan_updated_at"]
                    or _revision_value(task.updated_at) != inputs["expected_task_updated_at"]):
                raise ValueError("方案或任务版本已变化，请读取当前版本后重新选择声音。")
            if task.stage != "narration" or task.stage_status not in {
                "not_started",
                "failed",
            }:
                raise ValueError("当前任务不能选择配音声音")
            active_jobs = conn.execute("SELECT * FROM job_runs WHERE workspace_id=? AND task_id=? AND status IN ('queued','running','interrupted')",
                    (workspace_id, task_id)).fetchall()
            if active_jobs and (queued_job_rebind is None or len(active_jobs) != 1):
                raise ValueError("此任务仍有未结束的制作作业；先等作业结算或取消，再选择配音声音。")
            try:
                if voice_source_preparation is None:
                    selected_voice = self.creation_plan_voice_reference(workspace_id, voice_profile_id, voice_version,
                        viewer_user_id=member_id)
                else:
                    selected_voice = voice_source_preparation['selected_voice']
                    if (selected_voice.profile_id, selected_voice.version) != (voice_profile_id, voice_version):
                        raise ValueError('本次来源准备不属于所选声音')
                entries = self.list_applicable_pronunciation_entries(workspace_id=workspace_id,
                    person_profile_id=selected_voice.person_profile_id, episode_id=task.id)
            except (KeyError, ValueError, PermissionError, OSError) as exc:
                raise _MediaAuthorityError(reason="voice_reference_unavailable", impact="声音引用未更新，未调用模型。",
                    repair_action=str(exc)) from exc
            if selected_voice.language not in {{"zh-CN":"zh", "en-US":"en", "zh-CN/en-US":"zh-en"}[plan.language], "zh-en"}:
                raise _MediaAuthorityError(reason="voice_language_unavailable", impact="声音语言不匹配。",
                    repair_action="选择匹配方案语言的声音。")
            pronunciation_references = [CreationPlanPronunciationReference(id=entry.id, version=entry.version,
                scope=entry.scope, scope_reference=entry.scope_reference, language=entry.language) for entry in entries]

            now = utc_now()
            previous_plan = replace(plan)
            previous_voice = asdict(plan.selected_voice) if plan.selected_voice else None
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
            receipt = {"request_id": request_id, "workspace_id": workspace_id, "member_id": member_id,
                "task_id": task.id, "plan_id": plan.id, "status": "selected", "recorded_at": now.isoformat(),
                "expected_plan_updated_at": inputs["expected_plan_updated_at"],
                "expected_task_updated_at": inputs["expected_task_updated_at"],
                "plan_updated_at": now.isoformat(), "task_updated_at": now.isoformat(),
                "previous_voice": previous_voice, "selected_voice": asdict(selected_voice),
                "pronunciation_references": [asdict(item) for item in pronunciation_references],
                "confirmed_script_id": task.confirmed_script_id}
            self.execution_records.append_task_event(_TaskEvent(event_id, workspace_id, task_id, None, "task_narration_voice_selected",
                {"fingerprint": fingerprint, "member_id": member_id, "receipt": receipt}), connection=conn)
            if active_jobs:
                queued_job_rebind(conn, self._job_run_from_row(active_jobs[0]), previous_plan, event_id)
            return receipt

    def complete_creation_task_narration(
        self,
        artifact: NarrationArtifactRecord,
        segments: list[_NarrationSegment] | None = None,
        *, connection: sqlite3.Connection | None = None,
    ) -> tuple[_CreationTask, NarrationArtifactRecord]:
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                return self.complete_creation_task_narration(artifact, segments, connection=conn)
        if artifact.task_id is None:
            raise ValueError("创作任务配音必须关联 task_id")
        if not Path(artifact.audio_path).is_file() or Path(artifact.audio_path).stat().st_size == 0:
            raise ValueError("声音克隆没有生成有效 WAV")
        with nullcontext(connection) as conn:
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
        return task, artifact

    def publish_creation_task_render(
        self, workspace_id: str, task_id: str, plan_id: str, payload: dict[str, object],
        version: _TimelineVersion, *,
        expected_current_version_id: str | None | Literal[False] = False,
    ) -> None:
        with self._connect() as publish_conn:
            publish_conn.execute("BEGIN IMMEDIATE")
            self.save_creation_task_montage_plan(
                workspace_id, task_id, plan_id, payload, connection=publish_conn,
                expected_current_version_id=expected_current_version_id,
            )
            self.create_timeline_version(
                version, connection=publish_conn,
                expected_current_version_id=expected_current_version_id,
            )

    def save_failed_creation_task_render_if_current(
        self, workspace_id: str, task_id: str, plan_id: str, payload: dict[str, object], *,
        expected_current_version_id: str | None | Literal[False] = False,
    ) -> None:
        with self._connect() as failure_conn:
            failure_conn.execute("BEGIN IMMEDIATE")
            current_task = self._require_creation_task(failure_conn, workspace_id, task_id)
            # A failed old render must not replace a crop confirmed while it ran.
            if expected_current_version_id is False or current_task.current_timeline_version_id == expected_current_version_id:
                self.save_creation_task_montage_plan(
                    workspace_id, task_id, plan_id, payload, connection=failure_conn,
                )

    def save_creation_task_montage_plan(
        self,
        workspace_id: str,
        task_id: str,
        plan_id: str,
        payload: dict[str, object],
        *, connection: sqlite3.Connection | None = None,
        verify_inputs=None,
        expected_current_version_id: str | None | Literal[False] = False,
    ) -> tuple[_CreationTask, dict[str, object]]:
        status = str(payload.get("status", ""))
        if status not in {"waiting_for_user", "ready", "rendering", "rendered", "failed"}:
            raise ValueError("混剪计划状态无效")
        if connection is None:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.save_creation_task_montage_plan(workspace_id, task_id, plan_id, payload,
                    connection=conn, expected_current_version_id=expected_current_version_id, verify_inputs=verify_inputs)
            return self.get_creation_task(workspace_id, task_id), self.get_creation_task_montage_plan(workspace_id, task_id)
        conn = connection
        task_row = conn.execute(
            "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
            (workspace_id, task_id),
        ).fetchone()
        if task_row is None:
            raise KeyError(task_id)
        task = self._creation_task_from_payload(json.loads(str(task_row["payload"])))
        if verify_inputs is not None:
            verify_inputs(conn, task)
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
    ) -> _CreationTask:
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
    ) -> _CreationTask:
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
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            saved = self._material_assets.save_discovered_asset(
                existing, workspace_id, resolved, stat, media_type=media_type, source_url=source_url,
                owner_user_id=owner_user_id, copy_uploader_user_id=copy_uploader_user_id,
                connection=conn,
            )
            self._workspace_media_references.claim_asset_reference(conn, workspace_id, saved.id)
        return saved

    def mobile_transfer_service(self, *, min_free_disk_bytes: int):
        """Compose byte intake with the material owners and their one transaction."""
        from app.mobile_transfers import MobileTransfers

        def confirm(conn: sqlite3.Connection, workspace_id: str, **values) -> None:
            self.confirm_material_copy_complete(workspace_id, connection=conn, **values)

        return MobileTransfers(self._connect, assets=self._material_assets,
            workspace_references=self._workspace_media_references,
            target_for=self.material_link_target, confirm_copy_in_transaction=confirm,
            min_free_disk_bytes=min_free_disk_bytes)

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
        return self._material_source_files.material_file_intact(asset)

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
        if asset is not None and not _content_readable(
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
            if not _content_readable(
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




    def _validated_library_file(self, raw_path: str) -> Path:
        return self._material_source_files.validated_library_file(raw_path)



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
    def _narration_artifact_from_payload(
        data: dict[str, object],
    ) -> NarrationArtifactRecord:
        data = _with_datetime(data, "created_at")
        return NarrationArtifactRecord(**data)


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
                _CreationPlanVoiceReference(**voice_payload)
                if isinstance(voice_payload, dict)
                else None
            ),
            selected_person=(_CreationPlanPersonReference(**person_payload)
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
    def _task_from_row(row: sqlite3.Row) -> _CreationTask:
        """任务回读以列为准：存量行的 visibility 只写在列上。"""
        data = json.loads(str(row["payload"]))
        if "visibility" in row.keys():
            data["visibility"] = str(row["visibility"])
        return ProductionRepository._creation_task_from_payload(data)

    @staticmethod
    def _creation_task_from_payload(data: dict[str, object]) -> _CreationTask:
        return creation_task_from_payload(data)

    @staticmethod
    def _timeline_version_from_row(row: sqlite3.Row) -> _TimelineVersion:
        return _TimelineVersion(
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
    def _job_run_from_row(row: sqlite3.Row) -> _JobRun:
        return _JobRun(
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
    def _narration_segment_from_row(row: sqlite3.Row) -> _NarrationSegment:
        return narration_segment_from_row(row)


    @staticmethod
    def _subtitle_track_from_row(row: sqlite3.Row) -> _SubtitleTrack:
        payload = _json_object_from_text(str(row["payload_json"]))
        raw_cues = payload.pop("cues", [])
        return _SubtitleTrack(
            **_with_datetime(payload, "created_at", "updated_at"),
            cues=[
                _SubtitleCue(**cue)
                for cue in raw_cues
                if isinstance(cue, dict)
            ],
        )

    @staticmethod
    def _cover_asset_from_row(row: sqlite3.Row) -> _CoverAsset:
        payload = _json_object_from_text(str(row["payload_json"]))
        return _CoverAsset(**_with_datetime(payload, "created_at", "updated_at"))




    @staticmethod
    def _edit_intent_from_row(row: sqlite3.Row) -> _EditIntent:
        return _EditIntent(
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
    def _edit_proposal_from_row(row: sqlite3.Row) -> _EditProposal:
        return _EditProposal(
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
    def _edit_operation_from_row(row: sqlite3.Row) -> _EditOperation:
        return _EditOperation(
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
    def _script_generation_run_from_payload(data: dict[str, object]):
        return ScriptRecordStore._script_generation_run_from_payload(data)

    def _script_payload(self, connection, script, *, write_sections=True):
        return self.script_records._script_payload(connection, script, write_sections=write_sections)

    def _creation_task_script_from_payload(self, data: dict[str, object], *, connection=None) -> _CreationTaskScript:
        return self.script_records._creation_task_script_from_payload(data, connection=connection)



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
    repository._material_source_files = MaterialSourceFileAccess(repository._connect, Path(media_root).expanduser())
    repository._material_assets = MaterialAssetStore(repository._connect)
    repository._workspace_media_references = WorkspaceMediaReferenceAccess(
        repository._connect, repository._material_source_files, repository._material_assets)
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






def _revision_value(value: datetime | str) -> str:
    result = value.isoformat() if isinstance(value, datetime) else value.strip()
    if not result:
        raise ValueError("对象修订令牌不能为空")
    return result




def _creation_plan_conversion_intent(plan: CreationPlan) -> dict[str, object]:
    value = asdict(plan)
    for key in ("id", "creation_task_id", "owner_user_id", "created_at", "confirmed_at", "updated_at"):
        value.pop(key, None)
    return value


def _creation_task_conversion_intent(task: _CreationTask) -> dict[str, object]:
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


def _job_run_values(run: _JobRun) -> tuple[object, ...]:
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


def _narration_segment_values(segment: _NarrationSegment) -> tuple[object, ...]:
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










def _validate_timeline_version(version: _TimelineVersion) -> None:
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


def _validate_new_job_run(run: _JobRun) -> None:
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




def _validate_narration_segment(segment: _NarrationSegment) -> None:
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




def _validate_subtitle_track(track: _SubtitleTrack) -> None:
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


def _validate_cover_asset(cover: _CoverAsset) -> None:
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








def _validate_edit_intent(intent: _EditIntent) -> None:
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


def _validate_new_edit_proposal(proposal: _EditProposal) -> None:
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


def _validate_edit_operation(operation: _EditOperation) -> None:
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
    if operation.operation_type not in _SUPPORTED_EDIT_OPERATION_TYPES:
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


















def _string_tuple(data: dict[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"持久化媒体对象的 {key} 无效")
    return tuple(value)










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
