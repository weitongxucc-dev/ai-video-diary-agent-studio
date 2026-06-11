from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from app import episode_media_flow
from app.edit_plan import (
    build_edit_plan_workspace,
    revert_edit_plan_segment_decision,
    update_edit_plan_segment_decision,
)
from app.models import Episode
from app.routers.context import AppContext
from app.schemas import EditPlanResponse, EditPlanSegmentDecisionRequest


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/episodes/{episode_id}/edit-plan", response_model=EditPlanResponse)
    def get_episode_edit_plan(episode_id: str) -> EditPlanResponse:
        context = get_context()
        episode = episode_media_flow.recover_interrupted_video_job_if_needed(
            context,
            _episode_or_404(context, episode_id),
        )
        return EditPlanResponse.model_validate(
            build_edit_plan_workspace(episode, context.settings.video_output_path)
        )

    @router.post(
        "/api/episodes/{episode_id}/edit-plan/segments/{segment_id}/decision",
        response_model=EditPlanResponse,
    )
    def update_episode_edit_plan_segment_decision(
        episode_id: str,
        segment_id: str,
        request: EditPlanSegmentDecisionRequest,
    ) -> EditPlanResponse:
        context = get_context()
        episode = episode_media_flow.recover_interrupted_video_job_if_needed(
            context,
            _episode_or_404(context, episode_id),
        )
        try:
            workspace = update_edit_plan_segment_decision(
                episode,
                context.settings.video_output_path,
                segment_id=segment_id,
                action=request.action,
                reason=request.reason,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return EditPlanResponse.model_validate(workspace)

    @router.post(
        "/api/episodes/{episode_id}/edit-plan/segments/{segment_id}/revert",
        response_model=EditPlanResponse,
    )
    def revert_episode_edit_plan_segment_decision(
        episode_id: str,
        segment_id: str,
    ) -> EditPlanResponse:
        context = get_context()
        episode = episode_media_flow.recover_interrupted_video_job_if_needed(
            context,
            _episode_or_404(context, episode_id),
        )
        try:
            workspace = revert_edit_plan_segment_decision(
                episode,
                context.settings.video_output_path,
                segment_id=segment_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return EditPlanResponse.model_validate(workspace)

    return router


def _episode_or_404(context: AppContext, episode_id: str) -> Episode:
    try:
        return context.repo.get(episode_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc
