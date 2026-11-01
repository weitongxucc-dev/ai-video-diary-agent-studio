"""Material scan and analysis HTTP; ingestion and readiness remain with their original owners."""
from fastapi import APIRouter, Request
from app.schemas import MaterialLibraryResponse, MaterialAssetResponse

def build_material_processing_router(*, workspace_for, reader_for, mode_for, ingestion_for, production_for, library_response_for, asset_response_for, http_error_for):
    router = APIRouter()

    @router.post('/api/production/materials/scan', response_model=MaterialLibraryResponse)
    def scan_production_materials(request: Request) -> MaterialLibraryResponse:
        workspace_id = workspace_for()(request)
        viewer_user_id, local_diagnostic = reader_for()(request, workspace_id)
        if mode_for() == 'scheduled':
            ingestion_for()(production_for()).scan_once()
        else:
            production_for().material_intake.scan_library(workspace_id)
        assets, total = production_for().list_assets_page(workspace_id, limit=24, offset=0, include_private=local_diagnostic, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
        return library_response_for()(assets=[asset_response_for()(asset) for asset in assets], total_count=total, limit=24, offset=0, has_more=len(assets) < total)

    @router.post('/api/production/materials/{asset_id}/analyze', response_model=MaterialAssetResponse)
    def analyze_production_material(asset_id: str, request: Request) -> MaterialAssetResponse:
        if mode_for() == 'scheduled':
            ingestion_for()(production_for())
            raise http_error_for()(status_code=503, detail='素材手动分析的调度授权尚未接通。')
        try:
            asset = production_for().analyze_asset(workspace_for()(request), asset_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='material asset not found') from exc
        except ValueError as exc:
            raise http_error_for()(status_code=422, detail=str(exc)) from exc
        return asset_response_for()(asset)
    return router
