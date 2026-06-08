from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from app import episode_media_flow
from app.cover_workspace import cover_draft_from_episode, script_with_cover_copy
from app.routers.context import AppContext
from app.schemas import CoverDraftResponse, CoverUpdateRequest, EpisodeResponse


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/episodes/{episode_id}/cover", response_model=CoverDraftResponse)
    def get_episode_cover(episode_id: str) -> CoverDraftResponse:
        context = get_context()
        episode = _episode_or_404(context, episode_id)
        try:
            return CoverDraftResponse.model_validate(cover_draft_from_episode(episode))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/episodes/{episode_id}/cover", response_model=EpisodeResponse)
    def update_episode_cover(
        episode_id: str,
        request: CoverUpdateRequest,
    ) -> EpisodeResponse:
        context = get_context()
        episode = _episode_or_404(context, episode_id)
        if episode.script is None:
            raise HTTPException(status_code=400, detail="generate a script first")
        updated = context.service.update_script(
            episode,
            script_with_cover_copy(
                episode.script,
                headline=request.headline,
                subline=request.subline,
                tag=request.tag,
            ),
            reason="封面编辑器更新",
        )
        return episode_media_flow.episode_response(context, context.repo.save(updated))

    return router


def _episode_or_404(context: AppContext, episode_id: str):
    try:
        return episode_media_flow.get_episode(context, episode_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc
