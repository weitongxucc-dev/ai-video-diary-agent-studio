"""Explicit constructors for the API's database, media and content bindings.

Importing this module does not initialize databases or start drivers. Callers
prepare a candidate from parsed Settings, then publish it at a safe boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.artifact_store import ArtifactStore
from app.auth import SupabaseAuthContext
from app.avatar_lipsync import AvatarAndLipSyncProvider, AvatarPreviewService, avatar_lipsync_provider_from_settings
from app.config import Settings
from app.conversation_repository import ConversationRepository
from app.drafts import DraftWorkspace
from app.llm import script_generator_from_settings
from app.local_members import LocalMemberService
from app.material_speech import LocalMaterialSpeechAnalyzer
from app.model_runtime import ModelCatalog
from app.montage import MontagePlanner, MontageRenderer, MontageRepository
from app.narration import NarrationGenerator, VoicePreviewService
from app.platform_auth.service import PlatformAuthService
from app.production import ProductionRepository
from app.quality_inspection import QualityCheckRepository
from app.repository import EpisodeRepository
from app.runtime_health import model_catalog_from_settings
from app.scheduled_tasks import ScheduledTaskService
from app.services import EpisodeService
from app.source_repository import SourceRepository
from app.sources import ObsidianVault
from app.topics import topic_generator_from_settings
from app.video import FfmpegVideoProcessor, SourceTracker, VideoInbox
from app.video_jobs import VideoJobRunner
from app.video_pipeline import VideoProcessingPipeline, video_processing_pipeline_from_settings
from app.visual_semantics import VisualSemanticProvider, visual_semantic_provider_from_catalog
from app.visual_understanding import LocalVisualUnderstanding
from app.voice_clone import VoiceCloneProvider, voice_clone_provider_from_catalog
from app.workflow import LangGraphEpisodeWorkflow


_DEVICE_LOCK_FILENAME = "device-permit.lock"


@dataclass(frozen=True)
class DatabaseOwners:
    episodes: EpisodeRepository
    sources: SourceRepository
    production: ProductionRepository
    conversations: ConversationRepository
    montage: MontageRepository
    quality: QualityCheckRepository
    members: LocalMemberService
    platform_auth: PlatformAuthService
    scheduled_tasks: ScheduledTaskService
    video_jobs: VideoJobRunner


@dataclass(frozen=True)
class ModelProviders:
    catalog: ModelCatalog
    visual: VisualSemanticProvider
    voice: VoiceCloneProvider
    avatar: AvatarAndLipSyncProvider
    latentsync: AvatarAndLipSyncProvider


@dataclass(frozen=True)
class MediaServices:
    planner: MontagePlanner
    renderer: MontageRenderer
    artifacts: ArtifactStore
    narration: NarrationGenerator
    voice_preview: VoicePreviewService
    avatar_preview: AvatarPreviewService
    inbox: VideoInbox
    processor: FfmpegVideoProcessor
    pipeline: VideoProcessingPipeline
    drafts: DraftWorkspace
    source_tracker: SourceTracker


@dataclass(frozen=True)
class ContentServices:
    vault: ObsidianVault
    episodes: EpisodeService
    workflow: LangGraphEpisodeWorkflow


@dataclass(frozen=True)
class RuntimeDependencies:
    settings: Settings
    auth: SupabaseAuthContext
    database: DatabaseOwners
    models: ModelProviders
    media: MediaServices
    content: ContentServices


def prepare_runtime_dependencies(
    settings: Settings, *, current_database: DatabaseOwners | None = None,
) -> RuntimeDependencies:
    """Create the same finite graph for cold start and a prospective reload.

    Existing database identity services survive a same-database reload, including
    pending invite codes and lazy browser sessions. A database switch builds all
    five owners together. Schema initialization never replays restart recovery.
    No driver, model execution or restart recovery runs here.
    """
    catalog = model_catalog_from_settings(settings)
    models = ModelProviders(
        catalog, visual_semantic_provider_from_catalog(catalog),
        voice_clone_provider_from_catalog(catalog, temporary_root=settings.effective_temporary_root_path),
        avatar_lipsync_provider_from_settings(settings),
        avatar_lipsync_provider_from_settings(settings, quality="high"),
    )
    production = ProductionRepository(
        database_path=settings.database_path, video_library_path=settings.video_inbox_path,
        visual_provider=models.visual, speech_analyzer=LocalMaterialSpeechAnalyzer(settings),
        visual_understanding=LocalVisualUnderstanding(
            catalog, settings.effective_temporary_root_path, settings.native_ocr_worker_path,
        ),
        discovery_excluded_roots=(
            Path(settings.video_output_path).expanduser(),
            Path(settings.draft_output_path).expanduser(),
            Path(settings.model_root_path).expanduser(),
            settings.effective_temporary_root_path,
        ),
    )
    same_database = current_database is not None and (
        Path(current_database.production.database_path).expanduser().resolve()
        == Path(settings.database_path).expanduser().resolve()
    )
    if same_database:
        conversations = current_database.conversations
        quality = current_database.quality
        members = current_database.members
        platform_auth = current_database.platform_auth
        scheduled_tasks = current_database.scheduled_tasks
    else:
        conversations = ConversationRepository(settings.database_path, initialize=False)
        conversations.initialize()
        quality = QualityCheckRepository(settings.database_path)
        members = LocalMemberService(settings.database_path)
        platform_auth = PlatformAuthService(
            settings.database_path,
            Path(settings.database_path).expanduser().resolve().parent / "platform-browser-profiles",
        )
        scheduled_tasks = ScheduledTaskService(settings.database_path)
    database = DatabaseOwners(
        EpisodeRepository(database_path=settings.database_path), SourceRepository(settings.database_path),
        production, conversations, MontageRepository(settings.database_path), quality,
        members, platform_auth, scheduled_tasks, VideoJobRunner(production),
    )
    inbox = VideoInbox(settings.video_inbox_path)
    processor = FfmpegVideoProcessor(settings)
    media = MediaServices(
        MontagePlanner(semantic_provider=models.visual, visual_feature_loader=production.get_visual_feature),
        MontageRenderer(settings.video_output_path), ArtifactStore(settings.video_output_path),
        NarrationGenerator(settings.video_output_path, models.voice),
        VoicePreviewService(settings.video_output_path, models.voice),
        AvatarPreviewService(settings.video_output_path, models.avatar),
        inbox, processor, video_processing_pipeline_from_settings(settings, processor=processor),
        DraftWorkspace(settings), SourceTracker(inbox),
    )
    vault = ObsidianVault(settings.obsidian_vault_path)
    episodes = EpisodeService(
        script_generator=script_generator_from_settings(settings),
        topic_generator=topic_generator_from_settings(settings, vault),
    )
    return RuntimeDependencies(
        settings, SupabaseAuthContext(settings), database, models, media,
        ContentServices(vault, episodes, LangGraphEpisodeWorkflow(episodes)),
    )


def device_lock_path(*, settings: Settings, database_path) -> Path:
    """Desktop API and preparation share one application execution lock."""
    from app.device_paths import device_lock_path as canonical_lock, require_legacy_locks_released
    old_paths = [Path(database_path).expanduser().resolve().parent / _DEVICE_LOCK_FILENAME]
    if settings.device_resource_profile_path:
        old_paths.append(Path(settings.device_resource_profile_path).expanduser().resolve().parent / _DEVICE_LOCK_FILENAME)
    require_legacy_locks_released(old_paths)
    return canonical_lock()


def bind_device_runtime(
    *, repository: ProductionRepository, settings: Settings, members: LocalMemberService,
    registrations, budget, material_executor, verify_identity, record_admission,
    dispatch_owner=None,
):
    """Bind one unstarted device and its material service from explicit objects."""
    from app.device_runtime import build_device_runtime
    from app.member_permits import build_host_probes
    from app.material_ingestion import MaterialIngestionService
    from app.member_permits import MemberDeviceAccess

    material_service = None
    # Reuse the existing 1s cadence for independent memory and admission IO observation;
    # dispatch can spend longer in owner/identity checks or a running unit.
    poll_interval_seconds = 1.0
    resources, processes = build_host_probes(
        database_path=repository.database_path,
        disk_paths={"database": Path(repository.database_path).expanduser().resolve().parent,
                    "output": Path(settings.video_output_path).expanduser(),
                    "temporary": settings.effective_temporary_root_path},
        budget=budget,
        device_poll_interval_seconds=poll_interval_seconds,
    )
    runtime = build_device_runtime(
        database_path=repository.database_path,
        device_lock_path=device_lock_path(settings=settings, database_path=repository.database_path),
        device_access=MemberDeviceAccess(members, identity_verifier=verify_identity),
        registrations=registrations,
        resources=resources,
        processes=processes,
        admission_recorder=record_admission,
        dispatch_owner=dispatch_owner,
        # 会话活动来自客户端真实心跳；没有心跳记录才算空闲，不猜「没人用」。
        activity_source_reliable=True,
        poll_interval_seconds=poll_interval_seconds,
        host_observer=resources,
    )
    if material_executor is not None:
        # 素材服务要拿到同一张队列，所以只能在运行时装好之后再绑。
        material_service = MaterialIngestionService(
            repository, execution_mode="scheduled", scheduler=runtime.scheduler,
            scan_interval_seconds=60, stable_seconds=10,
        )
        material_executor.bind(material_service)
    return runtime, material_service
