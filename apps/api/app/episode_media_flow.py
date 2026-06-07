from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.edit_plan import apply_confirmed_edit_plan
from app.models import Episode, EpisodeStatus, VideoJobStatus, utc_now
from app.routers.context import AppContext
from app.schemas import EpisodeResponse


def pending_video_by_name(context: AppContext, file_name: str) -> Any:
    for video in context.video_inbox.pending_videos():
        if video.file_name == file_name:
            return video
    raise ValueError("pending video not found")


def pending_videos_by_name(context: AppContext, file_names: list[str]) -> list[Any]:
    pending_by_name = {
        video.file_name: video
        for video in context.video_inbox.pending_videos()
    }
    try:
        return [pending_by_name[file_name] for file_name in file_names]
    except KeyError as exc:
        raise ValueError("pending video not found") from exc


def get_episode(context: AppContext, episode_id: str) -> Episode:
    return context.repo.get(episode_id)


def episode_response(context: AppContext, episode: Episode) -> EpisodeResponse:
    return EpisodeResponse.model_validate(
        recover_interrupted_video_job_if_needed(context, episode),
        from_attributes=True,
    )


def recover_interrupted_video_job_if_needed(
    context: AppContext,
    episode: Episode,
) -> Episode:
    active_statuses = {VideoJobStatus.PROCESSING, VideoJobStatus.DRAFTING}
    job_status = video_job_status(episode.video_job_status)
    if (
        episode.status == EpisodeStatus.VIDEO_PROCESSING
        and job_status in active_statuses
        and not context.video_job_runner.is_active(episode.id)
    ):
        episode.status = EpisodeStatus.FAILED
        episode.video_job_status = VideoJobStatus.INTERRUPTED
        episode.video_job_message = "后台任务已中断"
        episode.video_job_error = (
            "API restarted or the worker stopped before the video job finished."
        )
        episode.video_job_finished_at = utc_now()
        return context.repo.save(episode)
    return episode


def video_job_status(value: object) -> VideoJobStatus:
    try:
        return value if isinstance(value, VideoJobStatus) else VideoJobStatus(str(value))
    except ValueError:
        return VideoJobStatus.IDLE


def create_draft_for_episode(context: AppContext, episode: Episode) -> Episode:
    if not episode.video_processed_file_name:
        raise ValueError("process a video first")
    confirmed_video = apply_confirmed_edit_plan(
        episode,
        context.draft_workspace.video_output_path,
        runner=context.edit_plan_apply_runner,
    )
    if confirmed_video is not None:
        episode.video_processed_file_name = confirmed_video["file_name"]
        episode.video_processed_path = confirmed_video["path"]
        episode = context.repo.save(episode)
    package = context.draft_workspace.create_package(
        video_file_name=episode.video_processed_file_name,
        episode=episode,
    )
    return context.workflow.run(
        "bind_draft_package",
        episode,
        package_name=package.package_name,
        package_path=package.path,
    )


def complete_ad_hoc_episode_video(
    get_context: Callable[[], AppContext],
    episode_id: str,
    file_name: str,
    *,
    logger: logging.Logger | None = None,
) -> None:
    context = get_context()
    try:
        mark_video_job_stage(
            context,
            episode_id,
            VideoJobStatus.PROCESSING,
            "正在生成精剪视频",
        )
        processed = context.video_pipeline.process(file_name)
        mark_video_job_stage(
            get_context(),
            episode_id,
            VideoJobStatus.DRAFTING,
            "正在生成草稿素材包",
        )
        bind_processed_video_and_draft(get_context(), episode_id, processed)
        mark_video_job_completed(get_context(), episode_id)
    except Exception as exc:
        mark_video_job_failed(get_context(), episode_id, exc)
        if logger is not None:
            logger.exception("ad hoc video processing failed for %s", episode_id)


def complete_ad_hoc_episode_video_batch(
    get_context: Callable[[], AppContext],
    episode_id: str,
    file_names: list[str],
    *,
    logger: logging.Logger | None = None,
) -> None:
    context = get_context()
    try:
        mark_video_job_stage(
            context,
            episode_id,
            VideoJobStatus.PROCESSING,
            "正在合并并精剪视频",
        )
        processed = context.video_pipeline.process_batch(file_names)
        mark_video_job_stage(
            get_context(),
            episode_id,
            VideoJobStatus.DRAFTING,
            "正在生成草稿素材包",
        )
        bind_processed_video_and_draft(get_context(), episode_id, processed)
        mark_video_job_completed(get_context(), episode_id)
    except Exception as exc:
        mark_video_job_failed(get_context(), episode_id, exc)
        if logger is not None:
            logger.exception("ad hoc video batch processing failed for %s", episode_id)


def bind_processed_video_and_draft(
    context: AppContext,
    episode_id: str,
    processed: Any,
) -> None:
    episode = context.workflow.run(
        "bind_processed_video",
        get_episode(context, episode_id),
        video=processed,
    )
    package = context.draft_workspace.create_package(
        video_file_name=processed.file_name,
        episode=episode,
    )
    episode = context.workflow.run(
        "bind_draft_package",
        episode,
        package_name=package.package_name,
        package_path=package.path,
    )
    context.repo.save(episode)


def start_video_job(episode: Episode) -> None:
    episode.status = EpisodeStatus.VIDEO_PROCESSING
    episode.video_job_status = VideoJobStatus.PROCESSING
    episode.video_job_message = "后台正在处理视频"
    episode.video_job_error = None
    episode.video_job_started_at = utc_now()
    episode.video_job_finished_at = None


def mark_video_job_stage(
    context: AppContext,
    episode_id: str,
    status: VideoJobStatus,
    message: str,
) -> None:
    episode = get_episode(context, episode_id)
    episode.video_job_status = status
    episode.video_job_message = message
    episode.video_job_error = None
    if episode.video_job_started_at is None:
        episode.video_job_started_at = utc_now()
    context.repo.save(episode)


def mark_video_job_completed(context: AppContext, episode_id: str) -> None:
    episode = get_episode(context, episode_id)
    episode.video_job_status = VideoJobStatus.COMPLETED
    episode.video_job_message = "视频已处理，并已生成待确认的剪映草稿素材包"
    episode.video_job_error = None
    episode.video_job_finished_at = utc_now()
    context.repo.save(episode)


def mark_video_job_failed(
    context: AppContext,
    episode_id: str,
    error: Exception,
) -> None:
    episode = get_episode(context, episode_id)
    episode.status = EpisodeStatus.FAILED
    episode.video_job_status = VideoJobStatus.FAILED
    episode.video_job_message = "视频处理失败"
    episode.video_job_error = f"{type(error).__name__}: {error}"
    episode.video_job_finished_at = utc_now()
    context.repo.save(episode)
