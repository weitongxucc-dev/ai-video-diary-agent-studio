"""Home aggregate HTTP; visibility, statistics and storage facts remain in the original aggregation owner."""
from fastapi import APIRouter, Request
from app.schemas import HomeAggregationResponse

def build_home_router(*, http_error_for, workspace_for, reader_for, aggregate_for, production_for, sources_for, private_for):
    router = APIRouter()

    @router.get('/api/production/home', response_model=HomeAggregationResponse)
    def get_production_home(request: Request, days: int=7) -> HomeAggregationResponse:
        """首页一次取齐：当前身份可见的统计、真实日趋势、可进入的队列/待办/成果、
    整机匿名接收数量和真实存储事实。

    身份解析完全沿用既有读过滤（`_content_reader` 与 `_content_include_private`），
    聚合模块不另写可见性判据。`days` 只影响日趋势跨度，不改变其他统计口径。
    """
        if days not in {7, 30}:
            raise http_error_for()(status_code=422, detail='首页统计只支持 days=7 或 days=30')
        workspace_id = workspace_for()(request)
        viewer_user_id, local_diagnostic = reader_for()(request, workspace_id)
        return aggregate_for()(workspace_id=workspace_id, repository=production_for(), source_repository=sources_for(), days=days, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic, include_private=private_for()(request))
    return router
