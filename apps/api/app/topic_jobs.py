from __future__ import annotations

import logging
from collections.abc import Callable

from app import episode_workflow_flow
from app.models import Episode, GenerationJobStatus, utc_now
from app.routers.context import AppContext


def start_topic_job(episode: Episode, message: str) -> Episode:
    episode.topic_job_status = GenerationJobStatus.PROCESSING
    episode.topic_job_message = message
    episode.topic_job_error = None
    episode.topic_job_started_at = utc_now()
    episode.topic_job_finished_at = None
    return episode


def complete_topic_generation(
    get_context: Callable[[], AppContext],
    episode_id: str,
    *,
    force: bool,
    logger: logging.Logger | None = None,
) -> None:
    try:
        context = get_context()
        episode = context.repo.get(episode_id)
        episode = context.workflow.run(
            "regenerate_topics" if force else "generate_topics",
            episode,
            feedback_history=episode_workflow_flow.feedback_history_for(
                context,
                episode_id,
            ),
            creator_profile=context.creator_profile_repo.load(),
        )
        context.repo.save(episode)
        mark_topic_job_completed(get_context(), episode_id)
    except Exception as exc:
        mark_topic_job_failed(get_context(), episode_id, exc)
        if logger is not None:
            logger.exception("topic generation failed for %s", episode_id)


def mark_topic_job_completed(context: AppContext, episode_id: str) -> None:
    episode = context.repo.get(episode_id)
    episode.topic_job_status = GenerationJobStatus.COMPLETED
    episode.topic_job_message = "候选选题已生成"
    episode.topic_job_error = None
    episode.topic_job_finished_at = utc_now()
    context.repo.save(episode)


def mark_topic_job_failed(
    context: AppContext,
    episode_id: str,
    error: Exception,
) -> None:
    episode = context.repo.get(episode_id)
    episode.topic_job_status = GenerationJobStatus.FAILED
    episode.topic_job_message = "选题生成失败"
    episode.topic_job_error = str(error)
    episode.topic_job_finished_at = utc_now()
    context.repo.save(episode)
