from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from app.routers.context import AppContext
from app.schemas import (
    DraftAssetResponse,
    DraftEnvironmentResponse,
    DraftImportRequest,
    DraftImportResponse,
    DraftPackageRequest,
    DraftPackageResponse,
    DraftStatusResponse,
)


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/drafts/status", response_model=DraftStatusResponse)
    def get_draft_status() -> DraftStatusResponse:
        context = get_context()
        return DraftStatusResponse(
            environment=DraftEnvironmentResponse.model_validate(
                context.draft_workspace.environment(),
                from_attributes=True,
            ),
            processed_videos=[
                DraftAssetResponse.model_validate(
                    video,
                    from_attributes=True,
                )
                for video in context.draft_workspace.processed_videos()
            ],
            packages=[
                DraftAssetResponse.model_validate(
                    package,
                    from_attributes=True,
                )
                for package in context.draft_workspace.packages()
            ],
        )

    @router.post("/api/drafts/package", response_model=DraftPackageResponse)
    def create_draft_package(request: DraftPackageRequest) -> DraftPackageResponse:
        context = get_context()
        try:
            package = context.draft_workspace.create_package(
                video_file_name=request.video_file_name,
                episode=context.repo.today(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return DraftPackageResponse.model_validate(package, from_attributes=True)

    @router.post("/api/drafts/import", response_model=DraftImportResponse)
    def import_draft_package(request: DraftImportRequest) -> DraftImportResponse:
        context = get_context()
        try:
            result = context.draft_workspace.import_package_to_jianying(
                request.package_name
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return DraftImportResponse.model_validate(result, from_attributes=True)

    return router
