"""Material insights HTTP includes legacy rendered-plan usage refresh before reader aggregation."""
from fastapi import APIRouter, Request
from app.schemas import MaterialLibraryInsightsResponse

def build_material_insights_router(*, workspace_for, montages_for, reader_for, response_for, production_for, render_for):
    router = APIRouter()

    @router.get('/api/production/materials/insights', response_model=MaterialLibraryInsightsResponse)
    def get_material_library_insights(request: Request) -> MaterialLibraryInsightsResponse:
        workspace_id = workspace_for()(request)
        for plan in montages_for().recent(workspace_id, limit=50):
            if plan.status == 'rendered':
                _record_montage_usage(workspace_id, plan)
        viewer_user_id, local_diagnostic = reader_for()(request, workspace_id)
        return response_for().model_validate(production_for().library_insights(workspace_id, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic))

    def _record_montage_usage(workspace_id: str, plan) -> None:
        render_for()().record_usage(workspace_id, plan)
    return router
