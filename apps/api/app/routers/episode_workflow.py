from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request

from app import episode_media_flow, episode_workflow_flow, topic_jobs
from app.asr import ASRConfigurationError, ASRTranscriptionError
from app.comment_insights import extract_comment_insights
from app.content_templates import get_content_template
from app.llm import LLMConfigurationError, LLMResponseError
from app.models import FeedbackEvent, FeedbackValue
from app.routers.context import AppContext
from app.schemas import (
    ApproveTopicRequest,
    CommentInsightsRequest,
    CommentInsightsResponse,
    ContentTemplateSelectRequest,
    EpisodeResponse,
    FeedbackRequest,
    FeedbackRevertRequest,
    MarkPublishedRequest,
    ScriptRegenerateRequest,
    ScriptSectionRegenerateRequest,
    ScriptRewriteIntentsResponse,
    ScriptUpdateRequest,
    ScriptVersionRevertRequest,
    VoiceCommandRequest,
    VoiceCommandResponse,
    VoiceTranscriptionResponse,
)
from app.script_rewrite_intents import script_rewrite_intents, script_rewrite_reason
from app.topics import TopicGenerationConfigurationError, TopicGenerationResponseError
from app.voice_commands import parse_voice_command
from app.voice_transcription import transcribe_voice_audio

logger = logging.getLogger(__name__)


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/script/rewrite-intents", response_model=ScriptRewriteIntentsResponse)
    def get_script_rewrite_intents() -> ScriptRewriteIntentsResponse:
        return ScriptRewriteIntentsResponse(intents=script_rewrite_intents())

    @router.get("/api/episodes/today", response_model=EpisodeResponse)
    def get_today_episode() -> EpisodeResponse:
        context = get_context()
        return episode_media_flow.episode_response(context, context.repo.today())

    @router.post("/api/episodes", response_model=EpisodeResponse)
    def create_episode() -> EpisodeResponse:
        context = get_context()
        return episode_media_flow.episode_response(context, context.repo.create())

    @router.get("/api/episodes/{episode_id}", response_model=EpisodeResponse)
    def get_episode(episode_id: str) -> EpisodeResponse:
        context = get_context()
        return episode_media_flow.episode_response(
            context,
            _episode_or_404(context, episode_id),
        )

    @router.post(
        "/api/episodes/{episode_id}/content-template",
        response_model=EpisodeResponse,
    )
    def select_episode_content_template(
        episode_id: str,
        request: ContentTemplateSelectRequest,
    ) -> EpisodeResponse:
        context = get_context()
        try:
            get_content_template(request.template_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="content template not found",
            ) from exc
        episode = _episode_or_404(context, episode_id)
        episode.content_template_id = request.template_id
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post(
        "/api/episodes/{episode_id}/generate-topics",
        response_model=EpisodeResponse,
    )
    def generate_topics(episode_id: str) -> EpisodeResponse:
        context = get_context()
        try:
            episode = _episode_or_404(context, episode_id)
            episode = context.workflow.run(
                "generate_topics",
                episode,
                feedback_history=episode_workflow_flow.feedback_history_for(
                    context,
                    episode_id,
                ),
                creator_profile=context.creator_profile_repo.load(),
            )
        except TopicGenerationConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except TopicGenerationResponseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post(
        "/api/episodes/{episode_id}/regenerate-topics",
        response_model=EpisodeResponse,
    )
    def regenerate_topics(episode_id: str) -> EpisodeResponse:
        context = get_context()
        try:
            episode = _episode_or_404(context, episode_id)
            episode = context.workflow.run(
                "regenerate_topics",
                episode,
                feedback_history=episode_workflow_flow.feedback_history_for(
                    context,
                    episode_id,
                ),
                creator_profile=context.creator_profile_repo.load(),
            )
        except TopicGenerationConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except TopicGenerationResponseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post(
        "/api/episodes/{episode_id}/start-generate-topics",
        response_model=EpisodeResponse,
    )
    def start_generate_topics(episode_id: str) -> EpisodeResponse:
        return _start_topic_generation(
            get_context,
            episode_id,
            force=False,
            message="正在生成今日候选选题",
        )

    @router.post(
        "/api/episodes/{episode_id}/start-regenerate-topics",
        response_model=EpisodeResponse,
    )
    def start_regenerate_topics(episode_id: str) -> EpisodeResponse:
        return _start_topic_generation(
            get_context,
            episode_id,
            force=True,
            message="正在重新生成候选选题",
        )

    @router.post(
        "/api/episodes/{episode_id}/approve-topic",
        response_model=EpisodeResponse,
    )
    def approve_topic(
        episode_id: str,
        request: ApproveTopicRequest,
    ) -> EpisodeResponse:
        context = get_context()
        try:
            episode = context.workflow.run(
                "approve_topic",
                _episode_or_404(context, episode_id),
                topic_id=request.topic_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post(
        "/api/episodes/{episode_id}/generate-script",
        response_model=EpisodeResponse,
    )
    def generate_script(episode_id: str) -> EpisodeResponse:
        context = get_context()
        episode = _episode_or_404(context, episode_id)
        if episode.selected_topic_id is None:
            raise HTTPException(status_code=400, detail="approve a topic first")
        try:
            episode = context.workflow.run(
                "generate_script",
                episode,
                feedback_history=episode_workflow_flow.feedback_history_for(
                    context,
                    episode_id,
                ),
                creator_profile=context.creator_profile_repo.load(),
            )
        except LLMConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except LLMResponseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post("/api/episodes/{episode_id}/script", response_model=EpisodeResponse)
    def update_episode_script(
        episode_id: str,
        request: ScriptUpdateRequest,
    ) -> EpisodeResponse:
        context = get_context()
        try:
            episode = context.workflow.run(
                "update_script",
                _episode_or_404(context, episode_id),
                script=episode_workflow_flow.script_package_from_response(
                    request.script
                ),
                reason=request.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post(
        "/api/episodes/{episode_id}/script/regenerate",
        response_model=EpisodeResponse,
    )
    def regenerate_episode_script(
        episode_id: str,
        request: ScriptRegenerateRequest,
    ) -> EpisodeResponse:
        context = get_context()
        try:
            episode = context.workflow.run(
                "regenerate_script",
                _episode_or_404(context, episode_id),
                feedback_history=episode_workflow_flow.feedback_history_for(
                    context,
                    episode_id,
                ),
                reason=request.reason,
                creator_profile=context.creator_profile_repo.load(),
            )
        except LLMConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except LLMResponseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post(
        "/api/episodes/{episode_id}/script/sections/{section_id}/regenerate",
        response_model=EpisodeResponse,
    )
    def regenerate_episode_script_section(
        episode_id: str,
        section_id: str,
        request: ScriptSectionRegenerateRequest,
    ) -> EpisodeResponse:
        context = get_context()
        try:
            reason = _script_section_regenerate_reason(request)
            episode = context.workflow.run(
                "regenerate_script_section",
                _episode_or_404(context, episode_id),
                section_id=section_id,
                feedback_history=episode_workflow_flow.feedback_history_for(
                    context,
                    episode_id,
                ),
                reason=reason,
                creator_profile=context.creator_profile_repo.load(),
            )
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LLMConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except LLMResponseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post(
        "/api/episodes/{episode_id}/script/revert",
        response_model=EpisodeResponse,
    )
    def revert_episode_script_version(
        episode_id: str,
        request: ScriptVersionRevertRequest,
    ) -> EpisodeResponse:
        context = get_context()
        try:
            episode = context.workflow.run(
                "revert_script_version",
                _episode_or_404(context, episode_id),
                version_id=request.version_id,
                reason=request.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post(
        "/api/episodes/{episode_id}/approve-script",
        response_model=EpisodeResponse,
    )
    def approve_script(episode_id: str) -> EpisodeResponse:
        context = get_context()
        try:
            episode = context.workflow.run(
                "approve_script",
                _episode_or_404(context, episode_id),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post("/api/voice/transcribe", response_model=VoiceTranscriptionResponse)
    async def transcribe_voice_command_audio(
        request: Request,
    ) -> VoiceTranscriptionResponse:
        context = get_context()
        try:
            transcription = transcribe_voice_audio(
                await request.body(),
                content_type=request.headers.get("content-type"),
                transcriber=context.voice_transcriber,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ASRConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ASRTranscriptionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return VoiceTranscriptionResponse.model_validate(transcription)

    @router.post(
        "/api/episodes/{episode_id}/voice-command/preview",
        response_model=VoiceCommandResponse,
    )
    def preview_voice_command(
        episode_id: str,
        request: VoiceCommandRequest,
    ) -> VoiceCommandResponse:
        context = get_context()
        episode = episode_media_flow.recover_interrupted_video_job_if_needed(
            context,
            _episode_or_404(context, episode_id),
        )
        plan = parse_voice_command(episode, request.transcript)
        return episode_workflow_flow.voice_command_response(context, plan)

    @router.post(
        "/api/episodes/{episode_id}/voice-command/execute",
        response_model=VoiceCommandResponse,
    )
    def execute_voice_command(
        episode_id: str,
        request: VoiceCommandRequest,
    ) -> VoiceCommandResponse:
        context = get_context()
        episode = episode_media_flow.recover_interrupted_video_job_if_needed(
            context,
            _episode_or_404(context, episode_id),
        )
        plan = parse_voice_command(episode, request.transcript)
        if not plan.can_execute:
            return episode_workflow_flow.voice_command_response(context, plan)

        try:
            updated_episode, edit_plan_workspace = (
                episode_workflow_flow.execute_voice_command_plan(
                    context,
                    episode,
                    plan,
                )
            )
        except (
            LLMConfigurationError,
            TopicGenerationConfigurationError,
        ) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (
            LLMResponseError,
            TopicGenerationResponseError,
        ) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return episode_workflow_flow.voice_command_response(
            context,
            plan,
            status="executed",
            result_episode=updated_episode,
            result_edit_plan=edit_plan_workspace,
        )

    @router.post(
        "/api/episodes/{episode_id}/mark-published",
        response_model=EpisodeResponse,
    )
    def mark_episode_published(
        episode_id: str,
        request: MarkPublishedRequest,
    ) -> EpisodeResponse:
        context = get_context()
        episode = context.workflow.run(
            "mark_published",
            _episode_or_404(context, episode_id),
            publish_url=request.publish_url,
            play_count=request.play_count,
            like_count=request.like_count,
            comment_count=request.comment_count,
            feedback_value=request.feedback_value,
            feedback_reason=request.feedback_reason,
        )
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post(
        "/api/episodes/{episode_id}/comment-insights",
        response_model=CommentInsightsResponse,
    )
    def analyze_episode_comment_insights(
        episode_id: str,
        request: CommentInsightsRequest,
    ) -> CommentInsightsResponse:
        context = get_context()
        episode = _episode_or_404(context, episode_id)
        created_candidates: list[dict[str, object]] = []
        for insight in extract_comment_insights(request.comments_text):
            source_event_ids: list[str] = []
            for source_comment in insight.source_comments:
                if episode_workflow_flow.has_comment_insight_event(
                    episode,
                    insight.content,
                    source_comment,
                ):
                    continue
                event = FeedbackEvent(
                    "published_comment_insight",
                    episode.id,
                    FeedbackValue.UP
                    if insight.kind == "prefer"
                    else FeedbackValue.DOWN,
                    insight.content,
                    before_snapshot={"source_comment": source_comment},
                    after_snapshot={"comment_insight": insight.content},
                )
                episode.feedback_events.append(event)
                source_event_ids.append(event.id)
            if not source_event_ids:
                continue
            created_candidates.append(
                {
                    "kind": insight.kind,
                    "label": "偏好" if insight.kind == "prefer" else "避开",
                    "content": insight.content,
                    "source_event_ids": source_event_ids,
                    "evidence_count": len(source_event_ids),
                    "confidence": round(
                        min(1.0, 0.35 + len(source_event_ids) * 0.15),
                        2,
                    ),
                    "scope": "publish",
                    "confirmed": False,
                }
            )
        saved = context.repo.save(episode) if created_candidates else episode
        return CommentInsightsResponse(
            episode=episode_media_flow.episode_response(context, saved),
            created_candidates=created_candidates,
        )

    @router.post(
        "/api/episodes/{episode_id}/feedback",
        response_model=EpisodeResponse,
    )
    def apply_feedback(
        episode_id: str,
        request: FeedbackRequest,
    ) -> EpisodeResponse:
        context = get_context()
        try:
            episode = context.service.apply_feedback(
                _episode_or_404(context, episode_id),
                request.target_type,
                request.target_id,
                request.value,
                request.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    @router.post(
        "/api/episodes/{episode_id}/feedback/revert",
        response_model=EpisodeResponse,
    )
    def revert_episode_feedback(
        episode_id: str,
        request: FeedbackRevertRequest,
    ) -> EpisodeResponse:
        context = get_context()
        try:
            episode = context.workflow.run(
                "revert_feedback",
                _episode_or_404(context, episode_id),
                feedback_event_id=request.feedback_event_id,
                reason=request.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return episode_media_flow.episode_response(context, context.repo.save(episode))

    return router


def _episode_or_404(context: AppContext, episode_id: str):
    try:
        return episode_media_flow.get_episode(context, episode_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc


def _start_topic_generation(
    get_context: Callable[[], AppContext],
    episode_id: str,
    *,
    force: bool,
    message: str,
) -> EpisodeResponse:
    context = get_context()
    if context.topic_job_runner.is_active(episode_id):
        return episode_media_flow.episode_response(
            context,
            _episode_or_404(context, episode_id),
        )
    episode = _episode_or_404(context, episode_id)
    episode = topic_jobs.start_topic_job(episode, message)
    context.repo.save(episode)
    context.topic_job_runner.submit(
        episode_id,
        lambda: topic_jobs.complete_topic_generation(
            get_context,
            episode_id,
            force=force,
            logger=logger,
        ),
    )
    return episode_media_flow.episode_response(context, context.repo.get(episode_id))


def _script_section_regenerate_reason(
    request: ScriptSectionRegenerateRequest,
) -> str | None:
    if not request.rewrite_intent_id:
        return request.reason
    return script_rewrite_reason(
        request.rewrite_intent_id,
        section_title=request.rewrite_section_title or "当前段落",
        user_note=request.rewrite_user_note,
    )
