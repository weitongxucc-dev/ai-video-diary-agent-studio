from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request

from app.creator_profile import creator_profile_prompt_payload
from app.routers.context import AppContext
from app.runtime_settings import is_local_client_host
from app.schemas import CreatorProfileResponse, CreatorProfileUpdateRequest


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/creator-profile", response_model=CreatorProfileResponse)
    def get_creator_profile() -> CreatorProfileResponse:
        return _creator_profile_response(get_context())

    @router.post("/api/creator-profile", response_model=CreatorProfileResponse)
    def update_creator_profile(
        request: Request,
        payload: CreatorProfileUpdateRequest,
    ) -> CreatorProfileResponse:
        client_host = request.client.host if request.client else None
        if not is_local_client_host(client_host):
            raise HTTPException(
                status_code=403,
                detail="creator profile can only be updated from a local client",
            )
        context = get_context()
        context.creator_profile_repo.save(payload.model_dump(exclude_none=True))
        return _creator_profile_response(context)

    return router


def _creator_profile_response(context: AppContext) -> CreatorProfileResponse:
    profile = context.creator_profile_repo.load()
    return CreatorProfileResponse.model_validate(
        {
            **profile.__dict__,
            "prompt_payload": creator_profile_prompt_payload(profile),
        }
    )
