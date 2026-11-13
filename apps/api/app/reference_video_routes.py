"""Reference-video study and bounded compare HTTP boundary."""
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request

from app.schemas import ReferenceVideoCompareReportResponse, ReferenceVideoCompareRequest, ReferenceVideoStudyResponse


def build_reference_video_router(*, workspace_for, reader_for, visible_for, production_for):
    router = APIRouter()

    @router.post("/api/production/materials/{asset_id}/reference-study", response_model=ReferenceVideoStudyResponse)
    def create_reference_video_study(asset_id: str, request: Request) -> ReferenceVideoStudyResponse:
        workspace_id = workspace_for()(request)
        production = production_for()
        try:
            asset = production.get_asset(workspace_id, asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="material asset not found") from exc
        # Resolve the trusted reader first so invalid member credentials keep
        # the same 401 boundary as the other material routes. Visibility then
        # applies the shared content policy for this asset.
        _owner, _local_diagnostic = reader_for()(request, workspace_id)
        if not visible_for()(request, workspace_id, asset.visibility, asset.owner_user_id):
            raise HTTPException(status_code=404, detail="material asset not found")
        try:
            study = production.create_reference_video_study(workspace_id, asset_id)
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="reference video source not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ReferenceVideoStudyResponse.model_validate(asdict(study))

    @router.get("/api/production/reference-studies/{study_id}", response_model=ReferenceVideoStudyResponse)
    def read_reference_video_study(study_id: str, request: Request) -> ReferenceVideoStudyResponse:
        workspace_id = workspace_for()(request)
        production = production_for()
        try:
            study = production.get_reference_video_study(workspace_id, study_id)
            asset = production.get_asset(workspace_id, study.asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="reference study not found") from exc
        if not visible_for()(request, workspace_id, asset.visibility, asset.owner_user_id):
            raise HTTPException(status_code=404, detail="reference study not found")
        return ReferenceVideoStudyResponse.model_validate(asdict(study))

    @router.post("/api/production/reference-studies/{study_id}/compare", response_model=ReferenceVideoCompareReportResponse)
    def compare_reference_video_study(study_id: str, request: Request, payload: ReferenceVideoCompareRequest) -> ReferenceVideoCompareReportResponse:
        workspace_id = workspace_for()(request)
        production = production_for()
        try:
            study = production.get_reference_video_study(workspace_id, study_id)
            asset = production.get_asset(workspace_id, study.asset_id)
            task = production.task_records.get_creation_task(workspace_id, payload.task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="reference study or task not found") from exc
        if not visible_for()(request, workspace_id, asset.visibility, asset.owner_user_id):
            raise HTTPException(status_code=404, detail="reference study not found")
        if not visible_for()(request, workspace_id, task.visibility, task.owner_user_id):
            raise HTTPException(status_code=404, detail="creation task not found")
        try:
            report = production.compare_reference_video_study(
                workspace_id, study_id, payload.task_id, payload.timeline_version_id,
            )
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="reference study or timeline version not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ReferenceVideoCompareReportResponse.model_validate(asdict(report))

    return router
