from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app import episode_media_flow
from app.cover_artifacts import (
    cover_artifact_payload,
    cover_preview_path,
    write_cover,
)
from app.cover_workspace import (
    cover_draft_from_episode,
    cover_template,
    script_with_cover_copy,
)
from app.routers.context import AppContext
from app.schemas import (
    CoverArtifactResponse,
    CoverDraftResponse,
    CoverUpdateRequest,
    EpisodeResponse,
)


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/episodes/{episode_id}/cover", response_model=CoverDraftResponse)
    def get_episode_cover(episode_id: str) -> CoverDraftResponse:
        context = get_context()
        episode = _episode_or_404(context, episode_id)
        try:
            draft = cover_draft_from_episode(episode)
            draft.update(cover_artifact_payload(context.settings, episode))
            return CoverDraftResponse.model_validate(draft)
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
        try:
            cover_template(request.template_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        updated.cover_template_id = request.template_id
        return episode_media_flow.episode_response(context, context.repo.save(updated))

    @router.post(
        "/api/episodes/{episode_id}/cover/generate",
        response_model=CoverArtifactResponse,
    )
    def generate_episode_cover(episode_id: str) -> CoverArtifactResponse:
        context = get_context()
        episode = _episode_or_404(context, episode_id)
        if episode.script is None:
            raise HTTPException(status_code=400, detail="generate a script first")
        path = cover_preview_path(context.settings, episode.id)
        write_cover(path, episode)
        payload = cover_artifact_payload(context.settings, episode)
        return CoverArtifactResponse.model_validate(payload)

    @router.get("/api/episodes/{episode_id}/cover/image")
    def get_episode_cover_image(episode_id: str) -> FileResponse:
        context = get_context()
        episode = _episode_or_404(context, episode_id)
        path = cover_preview_path(context.settings, episode.id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="cover image not generated")
        return FileResponse(path, media_type="image/png")

    return router


def _episode_or_404(context: AppContext, episode_id: str):
    try:
        return episode_media_flow.get_episode(context, episode_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc
