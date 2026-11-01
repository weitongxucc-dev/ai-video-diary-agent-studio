from __future__ import annotations

from app.creation.plan_contracts import (
    CreationPlan as _CreationPlan,
    CreationPlanBlocker as _CreationPlanBlocker,
    CreationPlanKnowledgeReference as _CreationPlanKnowledgeReference,
    CreationPlanSourceReference as _CreationPlanSourceReference,
)
from app.creation.plan_records import CreationPlanRecordStore
from app.creation.plan_confirmation_use_case import CreationPlanConfirmationUseCase
from app.creation.knowledge_adoption_use_case import TaskKnowledgeAdoptionUseCase
from app.creation.narration_voice_selection import NarrationVoiceSelectionTransaction
from app.creation.narration_completion import NarrationCompletionTransaction
from app.creation.montage_plan_adoption import MontagePlanAdoptionTransaction

from app.creation.script_contracts import (
    ScriptGenerationRunStatus as _ScriptGenerationRunStatus, ScriptSelectionStatus as _ScriptSelectionStatus, ScriptGenerationRun as _ScriptGenerationRun,
    CreationTaskScript as _CreationTaskScript, ScriptLibrarySection as _ScriptLibrarySection,
    _validated_assistant_reply_reference as _validated_assistant_reply_reference, _assistant_reply_input_references as _assistant_reply_input_references, _assistant_reply_request_event_id as _assistant_reply_request_event_id,
)
from app.creation.request_records import CreationRequestRecordStore
from app.creation.request_adoption_use_case import CreationRequestAdoptionUseCase
from app.creation.job_records import JobRunRecordStore
from app.creation.execution_job_transactions import CreationExecutionJobTransactions
from app.creation.execution_records import ExecutionRecordStore
from app.creation.section_inputs import SectionInputReader
from app.creation.narration_unit_transactions import NarrationUnitTransactions
from app.creation.narration_artifacts import NarrationArtifactStore
from app.creation.native_execution_records import NativeExecutionRecordStore
from app.creation.task_authorization_records import TaskAuthorizationRecordStore
from app.creation.narration_checkpoints import NarrationCheckpointTransactions
from app.creation.section_adoption import SectionAdoptionTransaction, _NarrationSegment as _NarrationSegment
from app.creation.script_records import ScriptRecordStore
from app.creation.topic_records import TopicRecordStore
from app.creation.lineage_records import LineageRecordStore
from app.materials.library_insights import material_library_insights
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
from urllib.parse import unquote
from uuid import uuid4

from app.creation.contracts import (
    CreationTask as _CreationTask, NarrationSegmentStatus as _NarrationSegmentStatus,
    validate_logical_reference as _validate_logical_reference,
)
from app.creation.person_renders import PersonRenderStore
from app.creation.task_records import TaskRecordStore
from app.creation.narration_segment_records import (
    NarrationSegmentRecordStore, validate_narration_segment,
)
from app.creation.timeline_records import TimelineRecordStore, validate_timeline_version
from app.creation.subtitle_records import SubtitleTrackRecordStore, validate_subtitle_track
from app.creation.cover_records import CoverAssetRecordStore, validate_cover_asset
from app.creation.editing.records import EditRecordStore, validate_edit_operation
from app.creation.task_input_reads import (
    require_creation_task, creation_task_from_payload, narration_segment_from_row,
    narration_segment_row, narration_segment_rows,
)
from app.profiles.contracts import (Authorization, PersonProfile, _as_utc, PronunciationEntry, VoiceProfile as VersionedVoiceProfile)
from app.profiles.authorizations import MediaAuthorizationStore, _authorization_payload, _authorization_from_payload, _validate_authorization
from app.creation.editing.timeline_history_repository import TimelineHistoryReadRepository
from app.profiles.versioned_payloads import _save_versioned_payload, _list_versioned_payloads, _versioned_row
from app.profiles.legacy_voice import LegacyVoiceProfileStore, VoiceProfile
from app.profiles.workspace_profile import WorkspaceProductionProfileStore, ProductionProfile
from app.profiles.input_resolution import ProfileInputResolver
from app.profiles.records import (
    ProfileRecordStore, _person_profile_payload, _versioned_voice_profile_payload,
    _profile_reference_payload, _profile_reference_fields,
)
from app.creation.script_windows import SCRIPT_SECTION_WINDOW_LIMIT
from app.database_migrations import (
    CONTENT_VISIBILITY_DEFAULT,
)
from app.production_schema import initialize_production_schema
from app.visual_understanding import LocalVisualUnderstanding, SegmentUnderstanding, OCR_RETRIEVAL_MIN_CONFIDENCE
from app.models import utc_now
from app.materials.business_objects import MaterialBusinessObjectStore
from app.materials.collections import MaterialCollectionStore
from app.materials.assets import MaterialAssetStore, MaterialVersionConflict, _normalized_labels, _infer_system_tags, _scene_ranges, _segment_id
from app.materials.analysis import MaterialAnalysisUseCase
from app.materials.analysis_execution import probe_duration, detect_scene_boundaries
from app.materials.usage_records import MaterialUsageRecordReader
from app.materials.source_files import MaterialSourceFileAccess, directory_accepts_writes as directory_accepts_writes
from app.materials.intake import MaterialIntakeUseCase
from app.materials.link_intake import LinkMaterialIntakeUseCase
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
from app.materials.segment_search_use_case import MaterialSegmentSearchUseCase
from app.materials.contracts import (
    AssetReviewStatus,
    ContentVisibility,
    AssetUsageRole,
    SuggestedUsageRole,
    AnalysisStatus,
    MaterialMediaType,
    MATERIAL_MEDIA_BY_SUFFIX,
    BUSINESS_OBJECT_KINDS,
    ContentUseState,
    MaterialSegment as MaterialSegment,
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
    effective_segment_usage_role as effective_segment_usage_role,
    selectable_segment_usage_role,
)
from app.asr import SubtitleSegment as SpeechObservation
from app.material_speech import LocalMaterialSpeechAnalyzer, MaterialSpeechAnalysis
from app.visual_semantics import (
    UnavailableVisualSemanticProvider,
    VisualSemanticProvider,
)
from app.material_probe import MaterialProbeFacts, probe_checkpoint_needs_rate_refresh
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
        self._material_source_files = MaterialSourceFileAccess(lambda: self._connect(), Path(video_library_path).expanduser())
        self._material_business_objects = MaterialBusinessObjectStore(self._connect)
        self._material_collections = MaterialCollectionStore(self._connect)
        self._material_assets = MaterialAssetStore(lambda: self._connect())
        self.material_usage_records = MaterialUsageRecordReader(
            connection_factory=lambda: self._connect(),
            read_asset=lambda workspace_id, asset_id: self._material_assets.get_asset(workspace_id, asset_id),
        )
        self.material_intake = MaterialIntakeUseCase(
            self._material_assets, self._material_source_files,
            connection_factory=lambda: self._connect(),
            upsert_discovered_asset=lambda workspace_id, path: self._upsert_discovered_asset(workspace_id, path),
        )
        self.material_search = MaterialSegmentSearchUseCase(
            list_assets=self._material_assets.list_assets,
            segment_eligible=material_segment_eligible,
        )
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
        self.person_renders = PersonRenderStore(
            self._connect, self._profile_records, self._workspace_media_references)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        initialize_production_schema(self._connect)
        self.material_links = LinkMaterialIntakeUseCase(
            assets=self._material_assets, source_files=self.material_source_files,
            accept_discovered_asset=lambda workspace_id, path, *, media_type=None, source_url="", owner_user_id=None: self._upsert_discovered_asset(
                workspace_id, path, media_type=media_type, source_url=source_url, owner_user_id=owner_user_id),
        )
        self.material_discovery = MaterialDiscoveryService(
            DiscoveryStore(self._connect),
            excluded_roots=lambda: [self.video_library_path, self.database_path, *discovery_excluded_roots],
            target_for=self.material_source_files.material_link_target,
            accept_copy=DiscoveryIntake(self._connect, self._material_assets,
                workspace_references=self._workspace_media_references,
                target_for=self.material_source_files.material_link_target, confirm_copy=lambda workspace_id, **values: self.material_source_files.confirm_material_copy_complete(workspace_id, **values)),
        )

    @property
    def material_source_files(self) -> MaterialSourceFileAccess:
        return self._material_source_files

    @property
    def material_assets(self) -> MaterialAssetStore:
        return self._material_assets

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
        self.task_records = TaskRecordStore(lambda: self._connect())
        self.timeline_records = TimelineRecordStore(self._connect, decode_snapshot=_json_object_from_text)
        self.timeline_history = TimelineHistoryReadRepository(self._connect)
        self.subtitle_records = SubtitleTrackRecordStore(self._connect,
            decode_payload=_json_object_from_text, timeline_belongs=self.timeline_records.timeline_belongs)
        self.cover_records = CoverAssetRecordStore(self._connect,
            decode_payload=_json_object_from_text, timeline_belongs=self.timeline_records.timeline_belongs)
        self.edit_records = EditRecordStore(self._connect,
            current_source=EXACT_EDIT_SOURCE.get, decode_reference_list=_string_list_from_text,
            decode_object=_json_object_from_text, decode_timeline_version=self.timeline_records.timeline_version_from_row)
        self.preview_records = PreviewRecordStore(self._connect)
        self._media_authorizations = MediaAuthorizationStore(self._connect)
        self._profile_records = ProfileRecordStore(self._connect, self._media_authorizations)
        self.hash_records = MaterialHashRecordStore(self._connect)
        self.hash_intake = MaterialHashIntake(self._connect, records=self.hash_records,
            library_root=lambda: self.video_library_path,
            library_available=self.material_source_files.material_library_available,
            read_binding=self.material_source_files.get_material_ingestion_binding,
            mark_disk_missing=self.material_intake.mark_disk_missing,
            upsert_asset=self._upsert_discovered_asset,
            asset_by_path=self._material_assets.get_asset_by_path,
            mark_asset_blocked=self._material_assets.mark_hash_asset_blocked)
        self.probe_records = MaterialProbeRecordStore(self._connect,
            library_root=lambda: self.video_library_path,
            library_available=self.material_source_files.material_library_available,
            read_binding=self.material_source_files.get_material_ingestion_binding,
            hash_candidate_from_stat=_material_hash_candidate,
            read_asset_source=self._material_assets._probe_asset_source_row,
            apply_probe_metadata=self._material_assets.apply_probe_metadata,
            mark_probe_failed=self._material_assets.mark_probe_failed)
        self.scene_records = MaterialSceneRecordStore(self._connect,
            library_root=lambda: self.video_library_path,
            library_available=self.material_source_files.material_library_available,
            read_binding=self.material_source_files.get_material_ingestion_binding,
            preferred_probe_key=self.probe_records._preferred_material_probe_key,
            probe_source_verdict=self.probe_records.material_probe_source_verdict,
            decode_asset=self._asset_from_payload,
            read_asset_payload=self._material_assets._asset_payload_row_by_id,
            assemble_scene_segments=self._material_assets.assemble_scene_segments,
            mark_scene_failed=self._material_assets.mark_scene_failed)
        self.job_records = JobRunRecordStore(self._connect,
            require_task=require_creation_task, decode_reference_list=_string_list_from_text,
            revision_token=_revision_value, registered_job_output_refs=ExecutionRecordStore.registered_job_output_refs)
        self.execution_records = ExecutionRecordStore(self._connect,
            require_task=require_creation_task, job_belongs=self.job_records.job_belongs,
            decode_reference_list=_string_list_from_text, decode_payload_object=_json_object_from_text)
        self.restore_requests = TimelineRestoreRecordStore(self._connect,
            append_event=self.execution_records.append_task_event,
            get_event=self.execution_records.get_task_event,
            decode_operation=self.edit_records.edit_operation_from_row, read_readiness=self.edit_execution_readiness)
        self.lineage_records = LineageRecordStore(self._connect,
            require_task=require_creation_task,
            job_belongs=self.job_records.job_belongs, output_producers=self.execution_records.output_producers,
            decode_reference_list=_string_list_from_text, decode_parameter_object=_json_object_from_text)
        self.topic_records = TopicRecordStore(self._connect,
            read_task=self.task_records.get_creation_task, require_task=require_creation_task,
            write_task=self.task_records.write_adopted_task, read_plan_intent=self._topic_plan_intent,
            append_event=self.execution_records.append_task_event)
        self.plan_records = CreationPlanRecordStore(self._connect,
            read_asset=self._material_assets._asset_payload_row_by_id, decode_asset=self._asset_from_payload)
        self.plan_confirmation = CreationPlanConfirmationUseCase(self._connect,
            records=self.plan_records, read_task=self.task_records.get_creation_task, execution_records=self.execution_records)
        self.narration_voice_selection = NarrationVoiceSelectionTransaction(self._connect,
            require_task=require_creation_task, decode_plan=self.plan_records.creation_plan_from_payload,
            voice_reference=self.creation_plan_voice_reference,
            pronunciation_entries=self.list_applicable_pronunciation_entries,
            append_event=self.execution_records.append_task_event, decode_job=self.job_records.job_run_from_row,
            revision_value=_revision_value)
        self.knowledge_adoption = TaskKnowledgeAdoptionUseCase(self._connect,
            require_task=require_creation_task, plans=self.plan_records, tasks=self.task_records,
            events=self.execution_records, has_open_job=self.job_records.has_open_task_job,
            count_scripts=ScriptRecordStore.count_task_scripts, revision_value=_revision_value)
        self.script_records = ScriptRecordStore(self._connect,
            read_task=self.task_records.get_creation_task, require_task=require_creation_task,
            decode_plan=self.plan_records.creation_plan_from_payload, read_topic=self.topic_records.get_topic_candidate,
            transition_task=self.task_records._script_task_transition, create_library_task=self._create_script_library_task,
            knowledge_inputs_current=self.knowledge_adoption.inputs_current, append_event=self.execution_records.append_task_event,
            get_event=self.execution_records.get_task_event, decode_event=self.execution_records.task_event_from_row)
        self.section_inputs = SectionInputReader(
            connection_factory=lambda: self._connect(),
            read_snapshot=lambda connection=None: self.creation_execution_read(connection),
            script_records=self.script_records, plan_records=self.plan_records,
        )
        self.narration_records = NarrationSegmentRecordStore(self._connect,
            decode_script=self.script_records._creation_task_script_from_payload,
            confirmed_script_digest=self.script_records.confirmed_creation_task_script_digest,
            stage_segment_state=lambda workspace_id, task_id, section_id, *, connection: self.section_inputs.stage_segment_state(
                workspace_id, task_id, section_id, connection=connection))
        self.narration_completion = NarrationCompletionTransaction(self._connect,
            decode_task=creation_task_from_payload,
            upsert_segments=self.narration_records.upsert_narration_segments,
            artifact_from_row=self.execution_records.artifact_from_row,
            read_job=self.job_records.get_job_run, list_transforms=self.lineage_records.list_transform_runs,
            confirmed_script_digest=self.script_records.confirmed_creation_task_script_digest)
        self.montage_plan_adoption = MontagePlanAdoptionTransaction(self._connect,
            decode_task=creation_task_from_payload, read_task=self.task_records.get_creation_task,
            read_montage=self.get_creation_task_montage_plan, require_task=require_creation_task,
            create_version=self.timeline_records.create_timeline_version)
        self.edit_adoption = EditAdoptionTransaction(self._connect,
            require_task=require_creation_task, decode_proposal=self.edit_records.edit_proposal_from_row,
            create_intent=self.edit_records.create_edit_intent, create_proposal=self.edit_records.create_edit_proposal,
            version_conflict=TimelineVersionConflict,
            read_task=self.task_records.get_creation_task, read_proposal=self.edit_records.get_edit_proposal,
            read_operation=self.edit_records.get_edit_operation, read_version=self.timeline_records.get_timeline_version,
            read_narration_segment=self.narration_records.get_narration_segment,
            iter_narration_edit_segments=self.iter_narration_edit_segments,
            replace_adopted_narration_segment=self.replace_adopted_narration_segment,
            upsert_narration_segments=self.narration_records.upsert_narration_segments,
            validate_operation=validate_edit_operation, validate_version=validate_timeline_version,
            validate_track=validate_subtitle_track, validate_cover=validate_cover_asset)
        from app.creation.editing.sources import EditClipSourceReader
        self.clip_sources = EditClipSourceReader(
            connection_factory=self._connect, read_montage=self.get_creation_task_montage_plan,
            read_asset=self.get_asset, read_person_render=lambda workspace_id, task_id, render_id: self.person_renders.get_person_render(workspace_id, task_id, render_id),
            asset_reference=self.workspace_media_reference_for_asset, resolve_reference=self.resolve_clip_media_reference,
            segment_eligible=material_segment_eligible, conflict_error=TimelineVersionConflict)
        self.creation_requests = CreationRequestRecordStore(self._connect)
        self.creation_request_adoption = CreationRequestAdoptionUseCase(self._connect,
            records=self.creation_requests, decode_plan=self.plan_records.creation_plan_from_payload,
            decode_task=creation_task_from_payload,
            prepare_plan=self.plan_confirmation.prepare_confirmed_creation_plan, insert_plan=self.plan_records.insert_confirmed_creation_plan,
            read_asset=self._material_assets._asset_payload_visibility_row_by_id,
            decode_asset=self._asset_from_payload, material_eligible=material_segment_eligible,
            voice_reference=self.creation_plan_voice_reference, person_reference=self.creation_plan_person_reference,
            write_task=self.task_records.write_adopted_task, topic_records=self.topic_records,
            script_records=self.script_records, execution_records=self.execution_records)
        self.section_adoption = SectionAdoptionTransaction(
            connection_factory=lambda: self._connect(),
            execution_records=self.execution_records, lineage_records=self.lineage_records,
            script_records=self.script_records, job_records=self.job_records,
            edit_records=self.edit_records, edit_adoption=self.edit_adoption,
            timeline_records=self.timeline_records,
            stage_section_version=lambda workspace_id, task_id, section_id, *, connection: self.section_inputs.stage_section_version(
                workspace_id, task_id, section_id, connection=connection),
            stage_segment_state=lambda workspace_id, task_id, section_id, *, connection: self.section_inputs.stage_segment_state(
                workspace_id, task_id, section_id, connection=connection),
            read_creation_section_input=lambda task, header, section_id, *, connection: self.section_inputs.read_creation_section_input(
                task, header, section_id, connection=connection),
            section_dependency_snapshot=lambda workspace_id, task_id, section_id, *, connection: self.section_inputs.section_dependency_snapshot(
                workspace_id, task_id, section_id, connection=connection),
            replace_adopted_narration_segment=lambda task, segment, *, connection: self.replace_adopted_narration_segment(
                task, segment, connection=connection),
        )
        from app.creation.execution_budget_scope import ExecutionBudgetScopes
        self.narration_units = NarrationUnitTransactions(
            connection_factory=lambda: self._connect(),
            execution_records=self.execution_records, job_records=self.job_records,
            narration_records=self.narration_records, section_inputs=self.section_inputs,
            budget_scopes=ExecutionBudgetScopes(self),
            creation_execution_input=lambda workspace_id, job_id, *, connection: self.creation_execution_input(
                workspace_id, job_id, connection=connection),
            read_creation_execution_snapshot=lambda workspace_id, task_id, *, connection: self.read_creation_execution_snapshot(
                workspace_id, task_id, connection=connection),
            fail_creation_task_stage=lambda workspace_id, task_id, *, stage, reason, impact, repair_action, retryable, connection: self.task_records.fail_creation_task_stage(
                workspace_id, task_id, stage=stage, reason=reason, impact=impact,
                repair_action=repair_action, retryable=retryable, connection=connection),
        )
        self.narration_artifacts = NarrationArtifactStore(
            connection_factory=lambda: self._connect(),
            recorded_source_is_current=lambda record: self.narration_completion.recorded_source_is_current(record),
            resolve_workspace_media_reference=lambda workspace_id, reference, *, require_registered: self.resolve_workspace_media_reference(
                workspace_id, reference, require_registered=require_registered),
        )
        self.narration_checkpoints = NarrationCheckpointTransactions(
            connection_factory=lambda: self._connect(),
            execution_records=self.execution_records, job_records=self.job_records,
            narration_artifact_reference=lambda cache_key: self.narration_artifacts.narration_artifact_reference(cache_key),
            save_narration_artifact=lambda artifact, *, connection: self.narration_artifacts.save_narration_artifact(artifact, connection=connection),
            replace_adopted_narration_segment=lambda task, segment, *, connection: self.replace_adopted_narration_segment(
                task, segment, connection=connection),
        )
        self.native_execution_records = NativeExecutionRecordStore(
            connection_factory=lambda: self._connect(),
            execution_records=self.execution_records, job_records=self.job_records,
            workspace_media_reference_for_asset=lambda workspace_id, asset_id: self.workspace_media_reference_for_asset(
                workspace_id, asset_id),
        )
        self.task_authorization_records = TaskAuthorizationRecordStore(
            connection_factory=lambda: self._connect(), execution_records=self.execution_records,
        )
        self.profile_inputs = ProfileInputResolver(
            connection_factory=lambda: self._connect(), task_records=self.task_records,
            read_voice=lambda conn, workspace_id, profile_id, version=None: self._get_versioned_voice_profile(conn, workspace_id, profile_id, version),
            read_person=lambda conn, workspace_id, profile_id, version=None: self._get_person_profile(conn, workspace_id, profile_id, version),
            current_authorization=lambda conn, workspace_id, authorization: self._current_authorization(conn, workspace_id, authorization),
            read_authorization=lambda conn, workspace_id, authorization_id, version: self._get_authorization(conn, workspace_id, authorization_id, version),
            list_pronunciation=lambda *, workspace_id, person_profile_id, episode_id: self.list_applicable_pronunciation_entries(
                workspace_id=workspace_id, person_profile_id=person_profile_id, episode_id=episode_id),
            profile_readable=lambda profile, user_id, local_diagnostic=False: self.profile_readable(profile, user_id, local_diagnostic),
            profile_reference_input=lambda profile, *, viewer_user_id=None, local_diagnostic=False: self.profile_reference_input(
                profile, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic),
        )
        self.creation_execution_jobs = CreationExecutionJobTransactions(
            connection_factory=lambda: self._connect(), job_records=self.job_records,
            execution_records=self.execution_records,
            seal_creation_narration_source=lambda job, prepared, *, input_digest, origin_event_id, connection: self.seal_creation_narration_source(
                job, prepared, input_digest=input_digest, origin_event_id=origin_event_id, connection=connection),
            bind_execution_budget_scope=lambda workspace_id, job_id, actor, *, scope_id, expected_revision, connection: self.bind_execution_budget_scope(
                workspace_id, job_id, actor, scope_id=scope_id, expected_revision=expected_revision, connection=connection),
        )




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












    def list_collections(self, workspace_id: str) -> list[MaterialCollection]:
        return self._material_collections.list_collections(workspace_id)

    def save_collection(self, collection: MaterialCollection) -> MaterialCollection:
        return self._material_collections.save_collection(collection)



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


    _corrected_material_segments = staticmethod(MaterialAssetStore._corrected_material_segments)


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


    def set_creation_plan_visibility(
        self, workspace_id: str, plan_id: str, visibility: ContentVisibility
    ) -> _CreationPlan:
        """明确共享或收回一个创作方案的可见性。"""
        if visibility not in ("private", "shared"):
            raise ValueError("可见性只能是 private 或 shared。")
        plan = self.plan_records.get_creation_plan(workspace_id, plan_id)
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
        return material_library_insights(assets)

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
            task = require_creation_task(conn, workspace_id, task_id)
            if not task.confirmed_script_id:
                raise ValueError("请先确认实际文案")
            plan = self.plan_records.read_creation_plan(conn, workspace_id, task.plan_id)
            header, fields = self.script_records.read_execution_script_metadata(conn, workspace_id, task.confirmed_script_id)
            digest = execution_input_digest(fields, plan,
                self.script_records.iter_execution_script_sections(conn, workspace_id, header.id))
            return ExecutionInputSnapshot(task, plan, header, digest)


















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
        return [self.job_records.job_run_from_row(row) for row in rows]




    def find_segment_revision_request(self, workspace_id, task_id, member_id, request_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM task_events WHERE workspace_id=? AND task_id=? AND event_type='segment_revision_requested' "
                "AND json_extract(payload_json,'$.member_id')=? AND json_extract(payload_json,'$.command.request_id')=? LIMIT 1",
                (workspace_id, task_id, member_id, request_id)).fetchone()
        return self.execution_records.task_event_from_row(row) if row is not None else None

    def create_segment_revision_job(self, task, actor, command, rules_check, *, write_guard=None, budget_policy=None):
        from app.creation.narration_pcm import prepare_narration_source
        plan = self.plan_records.get_creation_plan(task.workspace_id, task.plan_id)
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
            current = require_creation_task(conn, task.workspace_id, task.id)
            if current.owner_user_id != (actor.user_id or actor.member_id):
                raise PermissionError('局部修改的任务归属已变化')
            script = self.section_inputs.effective_creation_script(task.workspace_id, task.id, connection=conn)
            section = next((item for item in script.sections if item.id == command['section_id']), None)
            if (section is None or script.id != command['script_id']
                    or self.section_inputs.stage_section_version(task.workspace_id, task.id, section.id, connection=conn) != command['expected_segment_version']
                    or hashlib.sha256(section.content.encode()).hexdigest() != command['source_text_sha256']):
                raise ValueError('提交前段版本已变化，原采用成果保持不变')
            job = self.job_records.create_job_run(run, connection=conn)
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
                job = self.job_records.get_job_run(workspace_id, event.job_id, connection=conn)
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
            current = self.section_inputs.stage_section_version(event.workspace_id,event.task_id,event.payload['section_id'],connection=conn)
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
            job = self.job_records.claim_job_run(grant.workspace_id,grant.owner_id,connection=conn)
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
        job = self.job_records.get_job_run(grant.workspace_id,grant.owner_id,connection=connection)
        event = _TaskEvent(id=f'event_{job.id}_unit_settle_{grant.generation}',workspace_id=job.workspace_id,
            task_id=job.task_id,job_id=job.id,event_type='task_execution_unit_settled',payload={
                'usage_id':f'{grant.request_id}:{grant.generation}','request_id':grant.request_id,
                'permit_generation':grant.generation,'unit_key':grant.unit_key,
                'elapsed_seconds':elapsed_seconds,'output_bytes':output_bytes,'usage_source':'local_execution_measurement'})
        self.execution_records.append_task_event(event,connection=connection)

    def checkpoint_segment_unit(self, grant, *, status, connection, reason=None, payload=None):
        job = self.job_records.get_job_run(grant.workspace_id,grant.owner_id,connection=connection)
        if status == 'queued':
            changed = connection.execute("UPDATE job_runs SET status='queued',updated_at=? WHERE workspace_id=? AND id=? AND status='running'",
                (utc_now().isoformat(),job.workspace_id,job.id)).rowcount
            if changed != 1:
                raise ValueError('原段落作业不可继续提交')
        elif not (status=='cancelled' and job.status=='cancelled'):
            self.job_records.finish_job_run(job.workspace_id,job.id,status=status,connection=connection,
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
        target = self.section_inputs.read_creation_section_input(task, header, replacement.source_section_id, connection=connection)['audio']
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
                current = narration_segment_from_row(row)
                segment = replacement if current.source_section_id == replacement.source_section_id else current
                validate_narration_segment(segment)
                section = self.section_inputs.read_creation_section_input(task, header, segment.source_section_id, connection=connection)['section']
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
        tracks = self.subtitle_records.list_subtitle_tracks(version.workspace_id,version.task_id,timeline_version_id=version.id,connection=connection)
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
            task = require_creation_task(conn,workspace_id,task_id)
            changed = self.timeline_records.get_timeline_version(workspace_id,changed_version_id,connection=conn)
            current = self.timeline_records.get_timeline_version(workspace_id,task.current_timeline_version_id,connection=conn)
            if (changed.task_id!=task_id or self._timeline_restore_business_facts(current,conn)
                    !=self._timeline_restore_business_facts(changed,conn)):
                raise ValueError('此修改之后已有其他时间线成果；当前编辑尚不支持局部反向恢复，不能覆盖后来成果')
            if 'narration_segments' in changed.snapshot:
                rows = self.narration_records.list_narration_segments(workspace_id,task_id,task.confirmed_script_id,connection=conn)
                def facts(items):
                    return [{key:value for key,value in item.items() if key not in {'created_at','updated_at'}} for item in items]
                if facts([asdict(row) for row in rows])!=facts(changed.snapshot['narration_segments']):
                    raise ValueError('此修改之后已有新的配音成果；不能用旧整片快照覆盖，请保留当前成果并核对目标修改')
            if 'narration_cache_key' in changed.snapshot and task.narration_cache_key!=changed.snapshot['narration_cache_key']:
                raise ValueError('此修改之后已有新的配音总轨；不能用旧整片快照覆盖')


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
            require_creation_task(conn, event.workspace_id, event.task_id)
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



    def timeline_sequence(self, workspace_id, version_id, *, connection=None):
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            row = conn.execute('SELECT sequence FROM timeline_versions WHERE workspace_id=? AND id=?',
                (workspace_id, version_id)).fetchone()
            if row is None:
                raise KeyError(version_id)
            return row[0]



    def unsettled_segment_units(self, workspace_id, job_id):
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM task_events s WHERE s.workspace_id=? AND s.job_id=? "
                "AND s.event_type='task_execution_unit_started' AND NOT EXISTS (SELECT 1 FROM task_events e "
                "WHERE e.workspace_id=s.workspace_id AND e.job_id=s.job_id AND e.event_type='task_execution_unit_settled' "
                "AND json_extract(e.payload_json,'$.usage_id')=json_extract(s.payload_json,'$.usage_id')) LIMIT 1",
                (workspace_id, job_id)).fetchone() is not None


    def task_budget_projection(self, workspace_id, task_id, *, connection=None):
        """Read original resource receipts; missing monetary evidence never blocks local work."""
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            require_creation_task(conn, workspace_id, task_id)
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
            require_creation_task(conn, workspace_id, task_id)
            return ExecutionBudgetScopes(self).for_task(conn, workspace_id, task_id, actor)

    def execution_budget_scope_usage(self, workspace_id, scope_id):
        from app.creation.execution_budget_scope import ExecutionBudgetScopes
        with self._connect() as conn:
            scopes = ExecutionBudgetScopes(self)
            current = scopes.current(conn, workspace_id, scope_id)
            return {'authorization': current.payload, 'usage': scopes.usage(conn, workspace_id, scope_id)}


    def creation_execution_input(self, workspace_id, job_id, *, connection=None):
        """Read effective inputs while keeping the original start audit immutable."""
        with nullcontext(connection) if connection is not None else self._connect() as conn:
            job = self.job_records.get_job_run(workspace_id, job_id, connection=conn)
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
            job = self.job_records.get_job_run(workspace_id, job_id, connection=conn)
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
        usage = self.narration_units.creation_narration_usage(job.workspace_id, job.id, connection=connection)
        if (job.kind != 'creation_task_execution' or job.status != 'queued' or job.attempt != 0
                or job.started_at is not None or not usage['consumption_known']
                or usage['used_seconds'] != 0 or usage['used_output_bytes'] != 0
                or connection.execute("SELECT 1 FROM task_events WHERE workspace_id=? AND job_id=? "
                    "AND event_type IN ('creation_narration_unit_started','creation_narration_unit_settled') LIMIT 1",
                    (job.workspace_id, job.id)).fetchone()):
            raise ValueError('原配音作业已开始或消耗未知，不能改选声音')
        task = require_creation_task(connection, job.workspace_id, job.task_id)
        if (task.narration_cache_key is not None
                or self.narration_records.list_narration_segments(job.workspace_id, job.task_id, task.confirmed_script_id, connection=connection)
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
        return self.job_records.get_job_run(job.workspace_id, job.id, connection=connection)











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
        task = require_creation_task(connection, run.workspace_id, run.task_id)
        if (row is None or self.job_records.job_run_from_row(row) != run
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
        return self.job_records.get_job_run(run.workspace_id, run.id, connection=connection)






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


















    def iter_narration_edit_segments(self, task, replacement, *, connection):
        """Validate the whole set, then emit the real prospective rows in script order."""
        for _ in self._iter_section_change_segments(task, replacement, connection=connection, adopted=True):
            pass
        header, _ = self.script_records.read_execution_script_metadata(
            connection, task.workspace_id, task.confirmed_script_id)
        for _, segment in self.section_inputs.iter_creation_narration_sections(task, header, connection=connection):
            yield replacement if segment.id == replacement.id else segment















    def save_clip_preview_proposal(self, intent: _EditIntent, proposal: _EditProposal, *, expected_version_id: str):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = require_creation_task(conn, intent.workspace_id, intent.task_id)
            if task.current_timeline_version_id != expected_version_id:
                raise TimelineVersionConflict("预览期间时间线发生变化，请重新预览")
            if (proposal.workspace_id, proposal.task_id, proposal.intent_id) != (intent.workspace_id, intent.task_id, intent.id):
                raise ValueError("预览提案不属于原编辑意图")
            self.edit_records.create_edit_intent(intent, connection=conn, source=None)
            self.edit_records.create_edit_proposal(proposal, connection=conn)
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
            task = require_creation_task(conn, run.workspace_id, run.task_id)
            if task.current_timeline_version_id != expected_version_id:
                raise TimelineVersionConflict("提交封面前时间线发生变化，请重新预览")
            if task.owner_user_id and task.owner_user_id != (payload.get("user_id") or payload["member_id"]):
                raise PermissionError("当前成员不能安排此任务的封面")
            proposal = self.edit_records.get_edit_proposal(run.workspace_id, command["proposal_id"])
            original = proposal.operation.get("payload", {})
            normalized = {**original, "title_position": original.get("title_position", "bottom")}
            if (proposal.task_id != run.task_id or proposal.status != "proposed"
                    or proposal.operation.get("operation_type") != "GenerateCoverFromFrame"
                    or normalized != operation):
                raise ValueError("封面原提案已经变化")
            job = self.job_records.create_job_run(run, connection=conn)
            self.execution_records.append_task_event(_TaskEvent(id=f"event_{job.id}_cover_start", workspace_id=job.workspace_id,
                task_id=job.task_id, job_id=job.id, event_type="cover_submitted", payload=payload,
                created_at=job.created_at), connection=conn)
        return job

    def create_clip_preview_job(self, run: _JobRun, payload: dict, *, expected_version_id: str) -> _JobRun:
        """Compare the caller's original version and save the job/input together."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = require_creation_task(conn, run.workspace_id, run.task_id)
            if task.current_timeline_version_id != expected_version_id:
                raise TimelineVersionConflict("提交预览前时间线发生变化，请重新预览")
            if task.owner_user_id and task.owner_user_id != (payload.get("user_id") or payload["member_id"]):
                raise PermissionError("当前成员不能安排此任务的媒体处理")
            job = self.job_records.create_job_run(run, connection=conn)
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







    def goal_unreleased_job_request(self, workspace_id, job_id):
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM device_work_requests WHERE workspace_id=? AND owner_id=? "
                "AND state IN ('granted','yield_requested')", (workspace_id, job_id)).fetchone()

    def goal_render_request_row(self, workspace_id, request_id):
        with self._connect() as conn:
            return conn.execute("SELECT owner_id,state FROM device_work_requests WHERE workspace_id=? AND id=?",
                (workspace_id, request_id)).fetchone() if request_id else None



































    def _topic_plan_intent(self, connection, workspace_id, plan_id):
        row = connection.execute(
            "SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?",
            (workspace_id, plan_id),
        ).fetchone()
        plan = self.plan_records.creation_plan_from_payload(json.loads(row["payload"]))
        return plan.creation_intent


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

    def start_creation_task_narration(
        self, workspace_id: str, task_id: str
    ) -> _CreationTask:
        return self.task_records._start_creation_task_stage(workspace_id, task_id, "narration")

    def start_creation_task_montage(
        self, workspace_id: str, task_id: str
    ) -> _CreationTask:
        return self.task_records._start_creation_task_stage(workspace_id, task_id, "montage")









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
                    (workspace_id, str(resolved), json.dumps(self.material_source_files.material_copy_revision(stat), sort_keys=True)),
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
            self.material_source_files.confirm_material_copy_complete(workspace_id, connection=conn, **values)

        return MobileTransfers(self._connect, assets=self._material_assets,
            workspace_references=self._workspace_media_references,
            target_for=self.material_source_files.material_link_target, confirm_copy_in_transaction=confirm,
            min_free_disk_bytes=min_free_disk_bytes)









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
    def _template_from_row(row: sqlite3.Row) -> VideoTemplate:
        """模板回读以列为准：存量模板的 visibility 只写在列上。"""
        data = json.loads(str(row["payload"]))
        if "visibility" in row.keys():
            data["visibility"] = str(row["visibility"])
        return VideoTemplate(**_with_datetime(data, "created_at"))








































def _revision_value(value: datetime | str) -> str:
    result = value.isoformat() if isinstance(value, datetime) else value.strip()
    if not result:
        raise ValueError("对象修订令牌不能为空")
    return result








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




























































def _string_tuple(data: dict[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"持久化媒体对象的 {key} 无效")
    return tuple(value)
