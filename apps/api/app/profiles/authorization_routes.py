"""Versioned media authorization HTTP; persistence remains with existing records."""
from fastapi import APIRouter, HTTPException, Request
from app.schemas import AuthorizationResponse, AuthorizationsResponse, AuthorizationUpsertRequest


def build_authorization_router(*, records_for, workspace_for, authorization_for,
                               response_for, write_error_for) -> APIRouter:
    router = APIRouter()

    @router.get("/api/media/authorizations", response_model=AuthorizationsResponse)
    def list_media_authorizations(request: Request) -> AuthorizationsResponse:
        authorizations = records_for().list_authorizations(workspace_for(request))
        return AuthorizationsResponse(
            items=[response_for(item) for item in authorizations],
            total_count=len(authorizations),
        )


    @router.post("/api/media/authorizations", response_model=AuthorizationResponse)
    def save_media_authorization(
        request: Request,
        payload: AuthorizationUpsertRequest,
    ) -> AuthorizationResponse:
        try:
            saved = records_for().save_authorization(
                authorization_for(workspace_for(request), payload)
            )
        except ValueError as exc:
            raise write_error_for(exc) from exc
        return response_for(saved)


    @router.get("/api/media/authorizations/{authorization_id}", response_model=AuthorizationResponse)
    def get_media_authorization(
        authorization_id: str,
        request: Request,
        version: str | None = None,
    ) -> AuthorizationResponse:
        try:
            authorization = records_for().get_authorization(
                workspace_for(request), authorization_id, version
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="authorization not found") from exc
        return response_for(authorization)


    @router.get(
        "/api/media/authorizations/{authorization_id}/versions",
        response_model=AuthorizationsResponse,
    )
    def list_media_authorization_versions(
        authorization_id: str,
        request: Request,
    ) -> AuthorizationsResponse:
        workspace_id = workspace_for(request)
        try:
            records_for().get_authorization(workspace_id, authorization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="authorization not found") from exc
        items = records_for().list_authorization_versions(workspace_id, authorization_id)
        return AuthorizationsResponse(
            items=[response_for(item) for item in items],
            total_count=len(items),
        )


    return router
