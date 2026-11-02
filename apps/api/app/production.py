from __future__ import annotations

from app.creation.plan_contracts import (
    CreationPlan as _CreationPlan,
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
from app.creation.segment_unit_transactions import SegmentUnitTransactions
from app.creation.segment_revision_transactions import SegmentRevisionTransactions
from app.creation.execution_job_transactions import CreationExecutionJobTransactions
from app.creation.execution_records import ExecutionRecordStore
from app.creation.execution_input_reader import ExecutionInputReader
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
    material_hash_candidate as _material_hash_candidate,
)

import io
import json
import logging
import math
import sqlite3
import wave
from contextlib import nullcontext
from contextvars import ContextVar
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from app.profiles.preview_records import PreviewRecordStore
from app.creation.task_media_submission_transactions import TaskMediaSubmissionTransactions
from app.creation.task_media_publication import TaskMediaPublicationTransaction
from app.creation.transactions import EditAdoptionTransaction
from app.creation import contracts as _creation_contracts
from app.creation.timeline_restore_records import TimelineRestoreRecordStore
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from app.creation.contracts import (
    CreationTask as _CreationTask, NarrationSegmentStatus as _NarrationSegmentStatus,
    validate_logical_reference as _validate_logical_reference,
)
from app.creation.person_renders import PersonRenderStore
from app.creation.task_records import TaskRecordStore
from app.creation.video_template_records import VideoTemplateRecordStore
from app.creation.narration_segment_records import (
    NarrationSegmentRecordStore,
)
from app.creation.timeline_records import TimelineRecordStore, validate_timeline_version
from app.creation.subtitle_records import SubtitleTrackRecordStore, validate_subtitle_track
from app.creation.cover_records import CoverAssetRecordStore, validate_cover_asset
from app.creation.editing.records import EditRecordStore, validate_edit_operation
from app.creation.task_input_reads import (
    require_creation_task, creation_task_from_payload,
)
from app.profiles.contracts import (Authorization, PersonProfile, PronunciationEntry as PronunciationEntry, VoiceProfile as VersionedVoiceProfile)
from app.profiles.authorizations import MediaAuthorizationStore, _authorization_from_payload
from app.creation.editing.timeline_history_repository import TimelineHistoryReadRepository
from app.profiles.legacy_voice import LegacyVoiceProfileStore, VoiceProfile
from app.profiles.workspace_profile import WorkspaceProductionProfileStore, ProductionProfile
from app.creation.production_foundation_readiness import ProductionFoundationReadiness
from app.profiles.input_resolution import ProfileInputResolver
from app.profiles.records import (
    ProfileRecordStore, _versioned_voice_profile_payload as _versioned_voice_profile_payload,
)
from app.production_schema import initialize_production_schema
from app.visual_understanding import LocalVisualUnderstanding
from app.models import utc_now
from app.materials.business_objects import MaterialBusinessObjectStore
from app.materials.collections import MaterialCollectionStore
from app.materials.assets import MaterialAssetStore, MaterialVersionConflict as MaterialVersionConflict, _normalized_labels, _infer_system_tags as _infer_system_tags, _segment_id as _segment_id
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
    _json_payload, _json_object, _json_default as _json_default, _with_datetime,
)
from app.materials.segment_search_use_case import MaterialSegmentSearchUseCase
from app.materials.contracts import (
    AssetReviewStatus,
    ContentVisibility,
    AssetUsageRole,
    SuggestedUsageRole as SuggestedUsageRole,
    AnalysisStatus as AnalysisStatus,
    MaterialMediaType,
    MATERIAL_MEDIA_BY_SUFFIX as MATERIAL_MEDIA_BY_SUFFIX,
    BUSINESS_OBJECT_KINDS as BUSINESS_OBJECT_KINDS,
    ContentUseState,
    MaterialSegment as MaterialSegment,
    MaterialAsset,
    MaterialAssetNote,
    MaterialBusinessObject,
    MaterialCollection,
    MaterialSegmentMatch as MaterialSegmentMatch,
    material_media_type_for as material_media_type_for,
    material_media_type_label as material_media_type_label,
    material_allows_footage as material_allows_footage,
    material_allows_automation as material_allows_automation,
    material_usage_eligibility,
    effective_segment_usage_role as effective_segment_usage_role,
)
from app.material_speech import LocalMaterialSpeechAnalyzer
from app.visual_semantics import (
    UnavailableVisualSemanticProvider,
    VisualSemanticProvider,
)

if TYPE_CHECKING:
    from app.profile_reference_media import ProfileReferenceInput, ProfileReferenceArtifact

# Public imports retained while downstream creation consumers migrate.
from app.creation.contracts import (
    JobRunStatus as _JobRunStatus,
    SubtitleTrackStatus as _SubtitleTrackStatus,
    SubtitleCue as _SubtitleCue,
    SubtitleTrack as _SubtitleTrack,
    CoverAsset as _CoverAsset,
    TaskEvent as _TaskEvent,
    TransformRunStatus as _TransformRunStatus,
    EditProposalStatus as _EditProposalStatus,
    EditOperationStatus as _EditOperationStatus,
    EditOperationType as _EditOperationType,
)

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











































_JobRun = _creation_contracts.JobRun
_ArtifactRecord = _creation_contracts.ArtifactRecord
_TransformRun = _creation_contracts.TransformRun
_DependencyEdge = _creation_contracts.DependencyEdge
_EditIntent = _creation_contracts.EditIntent
_EditProposal = _creation_contracts.EditProposal


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
            self._connect, self.profile_records, self._media_authorizations,
            self._material_assets, self._workspace_media_references, self._material_source_files)
        self._profile_registration = ProfileRegistrationUseCase(
            self._connect, self.profile_records, self._media_authorizations,
            self._workspace_profiles, self._legacy_voice_profiles, self._profile_references,
            self._workspace_media_references, self._material_assets)
        self.foundation_readiness = ProductionFoundationReadiness(
            get_profile=lambda workspace_id: self.get_profile(workspace_id),
            list_assets=lambda workspace_id, *, include_private, viewer_user_id, local_diagnostic: self.list_assets(
                workspace_id, include_private=include_private, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic),
            list_versioned_voices=lambda workspace_id: self.profile_records.list_versioned_voice_profiles(workspace_id),
            profile_readable=lambda profile, viewer_user_id, local_diagnostic: self.profile_readable(
                profile, viewer_user_id, local_diagnostic),
            profile_reference_input=lambda profile, *, viewer_user_id, local_diagnostic: self.profile_reference_input(
                profile, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic),
            material_segment_eligible=lambda asset, segment, workspace_id: material_segment_eligible(asset, segment, workspace_id),
        )
        self.person_renders = PersonRenderStore(
            self._connect, self.profile_records, self._workspace_media_references)
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
        self.profile_records = ProfileRecordStore(self._connect, self._media_authorizations)
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
        self.job_records = JobRunRecordStore(lambda: self._connect(),
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
            pronunciation_entries=lambda *, workspace_id, person_profile_id, episode_id: self.profile_records.list_applicable_pronunciation_entries(
                workspace_id=workspace_id, person_profile_id=person_profile_id, episode_id=episode_id),
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
            read_snapshot=lambda connection=None: self.execution_inputs.creation_execution_read(connection),
            script_records=self.script_records, plan_records=self.plan_records,
        )
        self.narration_records = NarrationSegmentRecordStore(self._connect,
            decode_script=self.script_records._creation_task_script_from_payload,
            confirmed_script_digest=self.script_records.confirmed_creation_task_script_digest,
            iter_section_change_segments=lambda task, replacement, *, connection, adopted: self.section_inputs.iter_section_change_segments(
                task, replacement, connection=connection, adopted=adopted),
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
            create_intent=lambda intent, *, connection=None, source=False: self.edit_records.create_edit_intent(
                intent, connection=connection, source=source),
            create_proposal=lambda proposal, *, connection=None: self.edit_records.create_edit_proposal(
                proposal, connection=connection),
            version_conflict=TimelineVersionConflict,
            read_task=self.task_records.get_creation_task, read_proposal=self.edit_records.get_edit_proposal,
            read_operation=self.edit_records.get_edit_operation, read_version=self.timeline_records.get_timeline_version,
            read_narration_segment=self.narration_records.get_narration_segment,
            iter_narration_edit_segments=lambda task, replacement, *, connection: self.section_inputs.iter_narration_edit_segments(
                task, replacement, connection=connection),
            replace_adopted_narration_segment=lambda task, replacement, *, connection: self.narration_records.replace_adopted_narration_segment(
                task, replacement, connection=connection),
            upsert_narration_segments=self.narration_records.upsert_narration_segments,
            validate_operation=validate_edit_operation, validate_version=validate_timeline_version,
            validate_track=validate_subtitle_track, validate_cover=validate_cover_asset)
        self.task_media_submissions = TaskMediaSubmissionTransactions(
            connection_factory=lambda: self._connect(), edit_records=self.edit_records,
            job_records=self.job_records, execution_records=self.execution_records,
            version_conflict=TimelineVersionConflict,
        )
        self.task_media_publication = TaskMediaPublicationTransaction(
            connection_factory=lambda: self._connect(), execution_records=self.execution_records,
            lineage_records=self.lineage_records,
        )
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
            replace_adopted_narration_segment=lambda task, segment, *, connection: self.narration_records.replace_adopted_narration_segment(
                task, segment, connection=connection),
        )
        from app.creation.execution_budget_scope import ExecutionBudgetScopes
        self.narration_units = NarrationUnitTransactions(
            connection_factory=lambda: self._connect(),
            execution_records=self.execution_records, job_records=self.job_records,
            narration_records=self.narration_records, section_inputs=self.section_inputs,
            budget_scopes=ExecutionBudgetScopes(self),
            creation_execution_input=lambda workspace_id, job_id, *, connection: self.creation_execution_jobs.creation_execution_input(
                workspace_id, job_id, connection=connection),
            read_creation_execution_snapshot=lambda workspace_id, task_id, *, connection: self.execution_inputs.read_creation_execution_snapshot(
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
            replace_adopted_narration_segment=lambda task, segment, *, connection: self.narration_records.replace_adopted_narration_segment(
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
            read_voice=lambda conn, workspace_id, profile_id, version=None: self.profile_records._get_versioned_voice_profile(conn, workspace_id, profile_id, version),
            read_person=lambda conn, workspace_id, profile_id, version=None: self.profile_records._get_person_profile(conn, workspace_id, profile_id, version),
            current_authorization=lambda conn, workspace_id, authorization: self._current_authorization(conn, workspace_id, authorization),
            read_authorization=lambda conn, workspace_id, authorization_id, version: self._get_authorization(conn, workspace_id, authorization_id, version),
            list_pronunciation=lambda *, workspace_id, person_profile_id, episode_id: self.profile_records.list_applicable_pronunciation_entries(
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
        self.execution_inputs = ExecutionInputReader(
            connection_factory=lambda: self._connect(),
            read_plan=lambda conn, workspace_id, plan_id: self.plan_records.read_creation_plan(conn, workspace_id, plan_id),
            read_script_metadata=lambda conn, workspace_id, script_id: self.script_records.read_execution_script_metadata(conn, workspace_id, script_id),
            iter_script_sections=lambda conn, workspace_id, script_id: self.script_records.iter_execution_script_sections(conn, workspace_id, script_id),
        )
        self.video_templates = VideoTemplateRecordStore(lambda: self._connect())
        self.segment_units = SegmentUnitTransactions(
            connection_factory=lambda: self._connect(), job_records=self.job_records,
            execution_records=self.execution_records,
        )
        self.segment_revisions = SegmentRevisionTransactions(
            connection_factory=lambda: self._connect(), job_records=self.job_records,
            execution_records=self.execution_records,
            effective_script=lambda workspace_id, task_id, *, connection: self.section_inputs.effective_creation_script(workspace_id, task_id, connection=connection),
            stage_version=lambda workspace_id, task_id, section_id, *, connection: self.section_inputs.stage_section_version(workspace_id, task_id, section_id, connection=connection),
            seal_source=lambda job, prepared, *, input_digest, origin_event_id, connection: self.seal_creation_narration_source(
                job, prepared, input_digest=input_digest, origin_event_id=origin_event_id, connection=connection),
            bind_scope=lambda workspace_id, job_id, actor, *, scope_id, expected_revision, connection: self.bind_execution_budget_scope(
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





    def save_versioned_voice_profile(
        self,
        voice: VersionedVoiceProfile, *, verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> VersionedVoiceProfile:
        return self._profile_registration.save_versioned_voice_profile(voice, verified_owner_user_id=verified_owner_user_id, local_diagnostic=local_diagnostic)










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

























    def save_segment_video_preparation(self,event, *, write_guard=None):
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if write_guard is not None:
                write_guard(conn)
            current = self.section_inputs.stage_section_version(event.workspace_id,event.task_id,event.payload['section_id'],connection=conn)
            if current != event.payload['expected_segment_version']:
                raise ValueError('保存视频准备前段落版本已变化')
            self.execution_records.append_task_event(event,connection=conn)














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




    def seal_creation_narration_source(self, job, prepared, *, input_digest, origin_event_id, connection):
        from app.creation.narration_pcm import verify_narration_source, source_binding_payload
        from app.creation.execution_input import validate_execution_relationships
        snapshot = self.execution_inputs.read_creation_execution_snapshot(job.workspace_id, job.task_id, connection=connection)
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
        if not conn.in_transaction:
            raise ValueError("文案库复用必须属于原事务")
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
