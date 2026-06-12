from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.edit_plan import apply_confirmed_edit_plan
from app.models import Episode, EpisodeStatus, GenerationJobStatus, VideoJobStatus, utc_now
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
    episode = recover_interrupted_topic_job_if_needed(
        context,
        recover_interrupted_video_job_if_needed(context, episode),
    )
    episode.workflow_phase = workflow_phase_for_episode(episode)
    return EpisodeResponse.model_validate(
        episode,
        from_attributes=True,
    )


def workflow_phase_for_episode(episode: Episode) -> dict[str, str]:
    if episode.topic_job_status == GenerationJobStatus.PROCESSING:
        return {
            "id": "topics_generating",
            "label": "选题生成中",
            "description": episode.topic_job_message or "后台正在生成候选选题。",
        }
    if episode.video_job_status in {VideoJobStatus.PROCESSING, VideoJobStatus.DRAFTING}:
        return {
            "id": "video_processing",
            "label": "视频处理中",
            "description": episode.video_job_message or "后台正在处理视频。",
        }
    if episode.topic_job_status == GenerationJobStatus.FAILED:
        return {
            "id": "topics_failed",
            "label": "选题生成失败",
            "description": episode.topic_job_error or episode.topic_job_message or "请检查配置后重试。",
        }
    if episode.video_job_status in {VideoJobStatus.FAILED, VideoJobStatus.INTERRUPTED}:
        return {
            "id": "video_failed",
            "label": "视频处理异常",
            "description": episode.video_job_error or episode.video_job_message or "请检查视频处理日志。",
        }
    return _status_workflow_phase(episode.status)


def _status_workflow_phase(status: EpisodeStatus) -> dict[str, str]:
    labels: dict[EpisodeStatus, tuple[str, str]] = {
        EpisodeStatus.CREATED: ("今日工作台", "等待信息接入或生成候选选题。"),
        EpisodeStatus.SOURCES_INGESTING: ("信息接入中", "正在汇总素材来源。"),
        EpisodeStatus.TOPICS_READY: ("选题待确认", "候选选题已生成，等待人工复核。"),
        EpisodeStatus.TOPIC_APPROVED: ("选题已确认", "可以生成口播稿。"),
        EpisodeStatus.SCRIPT_READY: ("口播稿待确认", "脚本已生成，等待修改或确认。"),
        EpisodeStatus.SCRIPT_APPROVED: ("等待录制", "脚本已确认，可以录制视频。"),
        EpisodeStatus.WAITING_VIDEO: ("等待视频", "等待上传或处理原始视频。"),
        EpisodeStatus.VIDEO_PROCESSING: ("视频处理中", "后台正在处理视频。"),
        EpisodeStatus.DRAFT_READY: ("剪映草稿待确认", "草稿素材包已生成，等待确认和发布。"),
        EpisodeStatus.PUBLISHED: ("已发布", "等待登记发布反馈。"),
        EpisodeStatus.FAILED: ("流程异常", "请查看运行健康或内测日志。"),
    }
    label, description = labels.get(status, (status.value, "当前 Episode 主状态。"))
    return {"id": status.value, "label": label, "description": description}


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


def recover_interrupted_topic_job_if_needed(
    context: AppContext,
    episode: Episode,
) -> Episode:
    runner = getattr(context, "topic_job_runner", None)
    if (
        episode.topic_job_status == GenerationJobStatus.PROCESSING
        and runner is not None
        and not runner.is_active(episode.id)
    ):
        episode.topic_job_status = GenerationJobStatus.INTERRUPTED
        episode.topic_job_message = "后台任务已中断"
        episode.topic_job_error = (
            "API restarted or the worker stopped before topic generation finished."
        )
        episode.topic_job_finished_at = utc_now()
        return context.repo.save(episode)
    return episode


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
