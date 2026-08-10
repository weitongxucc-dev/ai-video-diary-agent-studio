from __future__ import annotations

from copy import deepcopy

from app.content_templates import ContentTemplate, get_content_template
from app.llm import ScriptGenerator, TemplateScriptGenerator
from app.models import (
    Episode,
    EpisodeIntakeMode,
    EpisodeStatus,
    FeedbackEvent,
    FeedbackValue,
    ProductionChainStage,
    ProductionChainStep,
    ScriptPackage,
    ScriptSection,
    ScriptVersion,
    ScriptVersionSource,
    SourceFileState,
    TopicCandidate,
    new_id,
    utc_now,
)
from app.topics import TemplateTopicGenerator, TopicGenerator
from app.video import PendingVideo, ProcessedVideo, SourceTracker


class EpisodeService:
    def __init__(
        self,
        script_generator: ScriptGenerator | None = None,
        topic_generator: TopicGenerator | None = None,
    ) -> None:
        self._script_generator = script_generator or TemplateScriptGenerator()
        self._topic_generator = topic_generator or TemplateTopicGenerator()

    def generate_topics(
        self,
        episode: Episode,
        feedback_history: list[FeedbackEvent] | None = None,
    ) -> Episode:
        if episode.topics:
            if episode.status in {EpisodeStatus.CREATED, EpisodeStatus.SOURCES_INGESTING}:
                episode.status = EpisodeStatus.TOPICS_READY
            return episode

        episode.topics = self._topic_generator.generate(
            day_number=episode.day_number,
            feedback_events=self._active_feedback(episode, feedback_history),
            template=self._content_template(episode),
        )
        episode.status = EpisodeStatus.TOPICS_READY
        return episode

    def _selected_topic(self, episode: Episode) -> TopicCandidate:
        if episode.selected_topic_id is None:
            raise ValueError("approve a topic first")
        selected = next(
            (topic for topic in episode.topics if topic.id == episode.selected_topic_id),
            None,
        )
        if selected is None:
            raise ValueError("selected topic not found")
        return selected

    def _content_template(self, episode: Episode) -> ContentTemplate:
        try:
            return get_content_template(episode.content_template_id)
        except KeyError:
            return get_content_template("video_diary")

    def _active_feedback(
        self,
        episode: Episode,
        feedback_history: list[FeedbackEvent] | None = None,
    ) -> list[FeedbackEvent]:
        return [
            event
            for event in (feedback_history or []) + episode.feedback_events
            if event.active
        ]

    def _record_script_version(
        self,
        episode: Episode,
        source: ScriptVersionSource,
        script: ScriptPackage,
        changed_section_ids: list[str] | None = None,
        reason: str | None = None,
    ) -> Episode:
        for version in episode.script_versions:
            version.active = False
        episode.script = deepcopy(script)
        episode.script_versions.append(
            ScriptVersion(
                id=new_id("sv"),
                source=source,
                script=deepcopy(script),
                changed_section_ids=changed_section_ids or [],
                reason=reason,
                active=True,
            )
        )
        episode.status = EpisodeStatus.SCRIPT_READY
        return episode

    def regenerate_topics(
        self,
        episode: Episode,
        feedback_history: list[FeedbackEvent] | None = None,
    ) -> Episode:
        episode.topics = self._topic_generator.generate(
            day_number=episode.day_number,
            feedback_events=self._active_feedback(episode, feedback_history),
            template=self._content_template(episode),
        )
        episode.selected_topic_id = None
        episode.script = None
        episode.script_versions = []
        episode.status = EpisodeStatus.TOPICS_READY
        return episode

    def approve_topic(self, episode: Episode, topic_id: str) -> Episode:
        if topic_id not in {topic.id for topic in episode.topics}:
            raise ValueError("topic not found")
        episode.selected_topic_id = topic_id
        episode.status = EpisodeStatus.TOPIC_APPROVED
        return episode

    def generate_script(
        self,
        episode: Episode,
        feedback_history: list[FeedbackEvent] | None = None,
    ) -> Episode:
        if episode.script is not None:
            if episode.status in {
                EpisodeStatus.CREATED,
                EpisodeStatus.SOURCES_INGESTING,
                EpisodeStatus.TOPICS_READY,
                EpisodeStatus.TOPIC_APPROVED,
            }:
                episode.status = EpisodeStatus.SCRIPT_READY
            return episode

        selected = self._selected_topic(episode)
        script = self._script_generator.generate(
            selected,
            day_number=episode.day_number,
            feedback_events=self._active_feedback(episode, feedback_history),
            template=self._content_template(episode),
        )
        self._record_script_version(
            episode,
            ScriptVersionSource.AI_GENERATED,
            script,
            reason="initial generation",
        )
        return episode

    def update_script(
        self,
        episode: Episode,
        script: ScriptPackage,
        reason: str | None = None,
    ) -> Episode:
        if episode.script is None:
            raise ValueError("generate a script first")
        changed = [
            section.id
            for section in script.sections
            if next(
                (
                    old.content
                    for old in episode.script.sections
                    if old.id == section.id
                ),
                None,
            )
            != section.content
        ]
        return self._record_script_version(
            episode,
            ScriptVersionSource.MANUAL_EDIT,
            script,
            changed_section_ids=changed,
            reason=reason,
        )

    def regenerate_script(
        self,
        episode: Episode,
        feedback_history: list[FeedbackEvent] | None = None,
        reason: str | None = None,
    ) -> Episode:
        selected = self._selected_topic(episode)
        script = self._script_generator.generate(
            selected,
            day_number=episode.day_number,
            feedback_events=self._active_feedback(episode, feedback_history),
            template=self._content_template(episode),
        )
        return self._record_script_version(
            episode,
            ScriptVersionSource.FULL_REGENERATION,
            script,
            reason=reason,
        )

    def regenerate_script_section(
        self,
        episode: Episode,
        section_id: str,
        feedback_history: list[FeedbackEvent] | None = None,
        reason: str | None = None,
    ) -> Episode:
        if episode.script is None:
            raise ValueError("generate a script first")
        if not any(section.id == section_id for section in episode.script.sections):
            raise ValueError("script section not found")

        selected = self._selected_topic(episode)
        replacement = self._script_generator.regenerate_section(
            selected,
            episode.script,
            section_id,
            reason=reason,
            feedback_events=self._active_feedback(episode, feedback_history),
            template=self._content_template(episode),
        )
        sections: list[ScriptSection] = [
            replacement if section.id == section_id else deepcopy(section)
            for section in episode.script.sections
        ]
        script = ScriptPackage(
            id=new_id("script"),
            hook=episode.script.hook,
            sections=sections,
            title_options=[*episode.script.title_options],
            cover_copy=episode.script.cover_copy,
            publish_copy=episode.script.publish_copy,
            comment_prompt=episode.script.comment_prompt,
        )
        return self._record_script_version(
            episode,
            ScriptVersionSource.SECTION_REGENERATION,
            script,
            changed_section_ids=[section_id],
            reason=reason,
        )

    def revert_script_version(
        self,
        episode: Episode,
        version_id: str,
        reason: str | None = None,
    ) -> Episode:
        version = next(
            (
                candidate
                for candidate in episode.script_versions
                if candidate.id == version_id
            ),
            None,
        )
        if version is None:
            raise ValueError("script version not found")
        return self._record_script_version(
            episode,
            ScriptVersionSource.REVERT,
            deepcopy(version.script),
            changed_section_ids=[section.id for section in version.script.sections],
            reason=reason,
        )

    def apply_feedback(
        self,
        episode: Episode,
        target_type: str,
        target_id: str,
        value: FeedbackValue,
        reason: str | None = None,
    ) -> Episode:
        updated = False
        before_snapshot: dict[str, object] | None = None
        after_snapshot: dict[str, object] | None = None
        if target_type == "topic":
            for topic in episode.topics:
                if topic.id == target_id:
                    before_snapshot = {"feedback": topic.feedback}
                    topic.feedback = value
                    after_snapshot = {"feedback": topic.feedback}
                    updated = True
        if target_type == "script_section" and episode.script:
            for section in episode.script.sections:
                if section.id == target_id:
                    before_snapshot = {"feedback": section.feedback}
                    section.feedback = value
                    after_snapshot = {"feedback": section.feedback}
                    updated = True
        if not updated:
            raise ValueError("feedback target not found")
        episode.feedback_events.append(
            FeedbackEvent(
                target_type,
                target_id,
                value,
                reason,
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
            )
        )
        return episode

    def revert_feedback(
        self,
        episode: Episode,
        feedback_event_id: str,
        reason: str | None = None,
    ) -> Episode:
        event = next(
            (
                candidate
                for candidate in episode.feedback_events
                if candidate.id == feedback_event_id
            ),
            None,
        )
        if event is None:
            raise ValueError("feedback event not found")

        event.active = False
        event.reverted_at = utc_now()
        event.revert_reason = reason
        restored_feedback = _feedback_value_from_snapshot(event.before_snapshot)
        if event.target_type == "topic":
            for topic in episode.topics:
                if topic.id == event.target_id:
                    topic.feedback = restored_feedback
        if event.target_type == "script_section" and episode.script:
            for section in episode.script.sections:
                if section.id == event.target_id:
                    section.feedback = restored_feedback
        return episode

    def approve_script(self, episode: Episode) -> Episode:
        if episode.script is None:
            raise ValueError("generate a script first")
        episode.status = EpisodeStatus.WAITING_VIDEO
        return episode

    def bind_video(self, episode: Episode, video: PendingVideo) -> Episode:
        if episode.script is None and episode.intake_mode != EpisodeIntakeMode.AD_HOC_SINGLE:
            raise ValueError("generate a script before binding video")
        episode.video_source_file_name = video.file_name
        episode.video_source_file_names = [video.file_name]
        episode.source_file_states[video.file_name] = SourceFileState.PENDING
        if episode.status not in {EpisodeStatus.DRAFT_READY, EpisodeStatus.PUBLISHED}:
            episode.status = EpisodeStatus.WAITING_VIDEO
        return episode

    def bind_ad_hoc_video(self, episode: Episode, video: PendingVideo) -> Episode:
        episode.intake_mode = EpisodeIntakeMode.AD_HOC_SINGLE
        episode.topics = []
        episode.selected_topic_id = None
        episode.script = None
        episode.script_versions = []
        episode.video_source_file_name = video.file_name
        episode.video_source_file_names = [video.file_name]
        episode.source_file_states[video.file_name] = SourceFileState.PENDING
        if episode.status != EpisodeStatus.PUBLISHED:
            episode.status = EpisodeStatus.WAITING_VIDEO
        return episode

    def bind_ad_hoc_video_batch(
        self,
        episode: Episode,
        videos: list[PendingVideo],
    ) -> Episode:
        if len(videos) < 2:
            raise ValueError("at least two video files are required")
        episode.intake_mode = EpisodeIntakeMode.AD_HOC_BATCH
        episode.topics = []
        episode.selected_topic_id = None
        episode.script = None
        episode.script_versions = []
        episode.video_source_file_name = videos[0].file_name
        episode.video_source_file_names = [video.file_name for video in videos]
        for video in videos:
            episode.source_file_states[video.file_name] = SourceFileState.PENDING
        if episode.status != EpisodeStatus.PUBLISHED:
            episode.status = EpisodeStatus.WAITING_VIDEO
        return episode

    def bind_processed_video(self, episode: Episode, video: ProcessedVideo) -> Episode:
        episode.video_source_file_name = video.source_file_name
        episode.video_source_file_names = video.source_file_names or [video.source_file_name]
        episode.video_processed_file_name = video.file_name
        episode.video_processed_path = video.output_path
        episode.video_edit_plan_path = video.edit_plan_path
        episode.video_dedup_report_path = video.dedup_report_path
        for file_name in episode.video_source_file_names:
            episode.source_file_states[file_name] = SourceFileState.PROCESSED
        episode.video_production_chain = _build_production_chain(
            episode.intake_mode,
            num_source_files=len(episode.video_source_file_names),
            has_dedup=video.dedup_report_path is not None,
        )
        if episode.status != EpisodeStatus.PUBLISHED:
            episode.status = EpisodeStatus.VIDEO_PROCESSING
        return episode

    def archive_source_files(
        self,
        episode: Episode,
        source_tracker: SourceTracker,
    ) -> Episode:
        for file_name in list(episode.source_file_states.keys()):
            if episode.source_file_states[file_name] == SourceFileState.PROCESSED:
                source_tracker.archive(file_name)
                episode.source_file_states[file_name] = SourceFileState.ARCHIVED
        return episode

    def bind_draft_package(
        self,
        episode: Episode,
        package_name: str,
        package_path: str,
    ) -> Episode:
        episode.draft_package_name = package_name
        episode.draft_package_path = package_path
        _advance_chain(episode, ProductionChainStage.DRAFT_PACKAGE)
        if episode.status != EpisodeStatus.PUBLISHED:
            episode.status = EpisodeStatus.DRAFT_READY
        return episode

    def mark_published(
        self,
        episode: Episode,
        publish_url: str | None = None,
        platform: str | None = None,
        play_count: int | None = None,
        like_count: int | None = None,
        comment_count: int | None = None,
        notes: str | None = None,
    ) -> Episode:
        episode.publish_url = publish_url
        episode.platform = platform
        episode.play_count = play_count
        episode.like_count = like_count
        episode.comment_count = comment_count
        episode.notes = notes
        episode.published_at = utc_now()
        episode.status = EpisodeStatus.PUBLISHED
        return episode

    def update_publish_metrics(
        self,
        episode: Episode,
        publish_url: str | None = None,
        platform: str | None = None,
        play_count: int | None = None,
        like_count: int | None = None,
        comment_count: int | None = None,
        notes: str | None = None,
    ) -> Episode:
        if publish_url is not None:
            episode.publish_url = publish_url
        if platform is not None:
            episode.platform = platform
        if play_count is not None:
            episode.play_count = play_count
        if like_count is not None:
            episode.like_count = like_count
        if comment_count is not None:
            episode.comment_count = comment_count
        if notes is not None:
            episode.notes = notes
        return episode


def _build_production_chain(
    intake_mode: EpisodeIntakeMode,
    num_source_files: int = 1,
    has_dedup: bool = False,
) -> list[ProductionChainStep]:
    now = utc_now()
    is_batch = intake_mode in {
        EpisodeIntakeMode.PLANNED_BATCH,
        EpisodeIntakeMode.AD_HOC_BATCH,
    }
    steps: list[ProductionChainStep] = [
        ProductionChainStep(
            stage=ProductionChainStage.SOURCE_CLIPS,
            status="completed",
            started_at=now,
            finished_at=now,
        ),
    ]
    if is_batch:
        steps.append(
            ProductionChainStep(
                stage=ProductionChainStage.MERGE,
                status="completed",
                started_at=now,
                finished_at=now,
            )
        )
    else:
        steps.append(
            ProductionChainStep(
                stage=ProductionChainStage.MERGE,
                status="skipped",
                detail="single source clip, no merge required",
            )
        )
    steps.extend([
        ProductionChainStep(
            stage=ProductionChainStage.SILENCE_CUT,
            status="completed",
            started_at=now,
            finished_at=now,
        ),
        ProductionChainStep(
            stage=ProductionChainStage.SPEED_PASS,
            status="completed",
            started_at=now,
            finished_at=now,
        ),
    ])
    if has_dedup:
        steps.extend([
            ProductionChainStep(
                stage=ProductionChainStage.RETAKE_SCAN,
                status="completed",
                started_at=now,
                finished_at=now,
            ),
            ProductionChainStep(
                stage=ProductionChainStage.RETAKE_REMOVAL,
                status="completed",
                started_at=now,
                finished_at=now,
            ),
        ])
    else:
        steps.append(
            ProductionChainStep(
                stage=ProductionChainStage.RETAKE_SCAN,
                status="skipped",
                detail="retake scan not configured or skipped",
            )
        )
        steps.append(
            ProductionChainStep(
                stage=ProductionChainStage.RETAKE_REMOVAL,
                status="skipped",
            )
        )
    steps.extend([
        ProductionChainStep(
            stage=ProductionChainStage.SUBTITLE,
            status="pending",
        ),
        ProductionChainStep(
            stage=ProductionChainStage.COVER,
            status="pending",
        ),
        ProductionChainStep(
            stage=ProductionChainStage.DRAFT_PACKAGE,
            status="pending",
        ),
        ProductionChainStep(
            stage=ProductionChainStage.JIANYING_IMPORT,
            status="pending",
        ),
    ])
    return steps


def _advance_chain(
    episode: Episode,
    stage: ProductionChainStage,
    status: str = "completed",
    detail: str | None = None,
) -> None:
    now = utc_now()
    for step in episode.video_production_chain:
        if step.stage == stage:
            step.status = status
            step.finished_at = now
            if detail:
                step.detail = detail
            return
    episode.video_production_chain.append(
        ProductionChainStep(
            stage=stage,
            status=status,
            started_at=now,
            finished_at=now,
            detail=detail,
        )
    )


def _feedback_value_from_snapshot(
    snapshot: dict[str, object] | None,
) -> FeedbackValue | None:
    if not snapshot:
        return None
    feedback = snapshot.get("feedback")
    return FeedbackValue(str(feedback)) if feedback else None
