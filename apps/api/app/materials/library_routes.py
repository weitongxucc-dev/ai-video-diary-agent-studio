"""Material-library HTTP reads and member annotations.

Asset/Search owners retain persistence, eligibility, CAS and transaction scope.
The adapter resolves current finite owners and HTTP leaves at each original use.
"""
from __future__ import annotations

from app.materials.assets import MaterialAssetStore

from dataclasses import asdict
from pathlib import Path
from typing import Callable, Literal, Protocol

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.materials.assets import MaterialVersionConflict
from app.materials.usage_records import MaterialUsageRecordReader
from app.materials.contracts import MaterialAssetNote
from app.schemas import (
    ContentVisibilityRequest, MaterialAssetResponse, MaterialAssetTagsRequest,
    MaterialBatchTagsRequest, MaterialBatchTagsResponse, MaterialAssetNotesRequest,
    MaterialAssetNotesResponse, MaterialLibraryResponse, MaterialUsageRecordsResponse,
    MaterialSegmentMatchResponse, MaterialSegmentSearchResponse,
    MaterialUseStateRequest, MaterialReviewRequest, MaterialSegmentUpdateRequest,
)


class MaterialLibraryRecords(Protocol):
    def list_assets_page(self, workspace_id, *, limit, offset, query=None, tag=None,
                         review_status=None, media_type=None, include_private=True,
                         viewer_user_id=None, local_diagnostic=False): ...
    def get_asset(self, workspace_id, asset_id): ...
    def update_asset_tags(self, workspace_id, asset_id, user_tags, *, expected_asset_updated_at=None): ...
    def update_assets_tags_batch(self, workspace_id, asset_ids, *, add, remove): ...
    def update_asset_notes(self, workspace_id, notes): ...
    def set_asset_visibility(self, workspace_id, asset_id, visibility): ...
    def update_asset_use_state(self, workspace_id, asset_id, use_state): ...
    def update_asset_review(self, workspace_id, asset_id, *, review_status, usage_role='unassigned'): ...
    def update_segment(self, workspace_id, asset_id, segment_id, *, description, transcript,
                       review_status, usage_role=None, user_tags=None, collection_ids=None,
                       expected_asset_updated_at=None, expected_segment_updated_at=None): ...
    material_usage_records: MaterialUsageRecordReader
    material_assets: MaterialAssetStore


class MaterialLibrarySearch(Protocol):
    def search_material_segments(self, workspace_id, *, query, usage_roles, tags,
                                min_duration_seconds, max_duration_seconds, limit,
                                viewer_user_id, local_diagnostic, purpose,
                                include_eligibility): ...


class MaterialLibraryHTTPAdapter:
    def __init__(self, *, records_for: Callable[[], MaterialLibraryRecords],
                 search_for: Callable[[], MaterialLibrarySearch], workspace_for,
                 reader_for, visible, write_guard, library_root, asset_response):
        self._records_for = records_for
        self._search_for = search_for
        self._workspace_for = workspace_for
        self._reader_for = reader_for
        self._visible = visible
        self._write_guard = write_guard
        self._library_root = library_root
        self._asset_response = asset_response


    def list_production_materials(self, request: Request, limit: int=24, offset: int=0, q: str | None=None, tag: str | None=None, review_status: str | None=None, media_type: str | None=None) -> MaterialLibraryResponse:
        if limit < 1 or limit > 100 or offset < 0:
            raise HTTPException(status_code=422, detail='invalid material pagination')
        if review_status not in {None, 'raw', 'approved', 'rejected'}:
            raise HTTPException(status_code=422, detail='invalid material review status')
        if media_type not in {None, 'video', 'audio', 'image'}:
            raise HTTPException(status_code=422, detail='invalid material media type')
        workspace_id = self._workspace_for(request)
        viewer_user_id, local_diagnostic = self._reader_for(request, workspace_id)
        assets, total = self._records_for().list_assets_page(workspace_id, limit=limit, offset=offset, query=q, tag=tag, review_status=review_status, media_type=media_type, include_private=local_diagnostic, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
        return MaterialLibraryResponse(assets=[self._asset_response(asset) for asset in assets], total_count=total, limit=limit, offset=offset, has_more=offset + len(assets) < total)

    def update_material_asset_tags(self, asset_id: str, request: Request, payload: MaterialAssetTagsRequest) -> MaterialAssetResponse:
        self.get_production_material(asset_id, request)
        try:
            asset = self._records_for().update_asset_tags(self._workspace_for(request), asset_id, payload.user_tags, expected_asset_updated_at=payload.expected_asset_updated_at)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='素材不属于当前工作区') from exc
        except MaterialVersionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return self._asset_response(asset)

    def update_material_asset_tags_batch(self, request: Request, payload: MaterialBatchTagsRequest) -> MaterialBatchTagsResponse:
        try:
            assets = self._records_for().update_assets_tags_batch(self._workspace_for(request), payload.asset_ids, add=payload.add, remove=payload.remove)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='所选素材有不属于当前工作区的项目，未做任何修改') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return MaterialBatchTagsResponse(assets=[self._asset_response(asset) for asset in assets])

    def update_material_asset_notes(self, request: Request, payload: MaterialAssetNotesRequest) -> MaterialAssetNotesResponse:
        "Save each named material's own text and the batch background it arrived with.\n\n    Only the materials named in the request are touched, and the repository\n    refuses the whole batch if any one of them is not this workspace's, so the\n    user is never told a note is stored that was not written.\n    "
        try:
            assets = self._records_for().update_asset_notes(self._workspace_for(request), [MaterialAssetNote(asset_id=item.asset_id, user_description=item.user_description, upload_context=item.upload_context) for item in payload.items])
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='所选素材有不属于当前工作区的项目，未做任何修改') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return MaterialAssetNotesResponse(assets=[self._asset_response(asset) for asset in assets])

    def list_material_segment_usage_records(self, request: Request, asset_id: str, segment_id: str) -> MaterialUsageRecordsResponse:
        """Show what used one segment of one material, read from real render rows."""
        workspace_id = self._workspace_for(request)
        try:
            asset = self._records_for().get_asset(workspace_id, asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='素材或片段不存在或不属于当前工作区') from exc
        if not self._visible(request, workspace_id, asset.visibility, asset.owner_user_id):
            raise HTTPException(status_code=404, detail='素材或片段不存在或不属于当前工作区')
        try:
            records = self._records_for().material_usage_records.list_segment_usage_records(workspace_id, asset_id, segment_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='素材或片段不存在或不属于当前工作区') from exc
        return MaterialUsageRecordsResponse(items=records, total_count=len(records))

    def get_unmatched_material_corrections(self, asset_id: str, request: Request) -> dict[str, object]:
        workspace_id = self._workspace_for(request)
        try:
            self._records_for().get_asset(workspace_id, asset_id)
            items = self._records_for().material_assets.list_unmatched_material_corrections(workspace_id, asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='素材不属于当前工作区') from exc
        return {'asset_id': asset_id, 'items': items}

    def search_production_material_segments(self, request: Request, q: str='', usage_role: str | None=None, tags: str | None=None, min_duration_seconds: float | None=None, max_duration_seconds: float | None=None, limit: int=20, purpose: Literal['manual', 'automatic']='manual', include_eligibility: bool=False) -> list[MaterialSegmentMatchResponse] | MaterialSegmentSearchResponse:
        if usage_role not in {None, 'a_roll', 'b_roll'}:
            raise HTTPException(status_code=422, detail='invalid material usage role')
        workspace_id = self._workspace_for(request)
        viewer_user_id, local_diagnostic = self._reader_for(request, workspace_id)
        try:
            matches = self._search_for().search_material_segments(workspace_id, query=q, usage_roles=[usage_role] if usage_role else ['a_roll', 'b_roll'], tags=[item.strip() for item in tags.split(',') if item.strip()] if tags else None, min_duration_seconds=min_duration_seconds, max_duration_seconds=max_duration_seconds, limit=limit, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic, purpose=purpose, include_eligibility=include_eligibility)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if include_eligibility:
            return MaterialSegmentSearchResponse.model_validate({**matches, 'matches': [asdict(item) for item in matches['matches']]})
        return [MaterialSegmentMatchResponse.model_validate(asdict(item)) for item in matches]

    def set_production_material_visibility(self, asset_id: str, request: Request, payload: ContentVisibilityRequest) -> MaterialAssetResponse:
        """显式共享或收回一条素材；响应回读同一列。"""
        denied = self._write_guard(request)
        if denied is not None:
            return denied
        try:
            asset = self._records_for().set_asset_visibility(self._workspace_for(request), asset_id, payload.visibility)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='material asset not found') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return self._asset_response(asset)

    def get_production_material(self, asset_id: str, request: Request) -> MaterialAssetResponse:
        workspace_id = self._workspace_for(request)
        try:
            asset = self._records_for().get_asset(workspace_id, asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='material asset not found') from exc
        if not self._visible(request, workspace_id, asset.visibility, asset.owner_user_id):
            raise HTTPException(status_code=404, detail='material asset not found')
        return self._asset_response(asset)

    def stream_production_material(self, asset_id: str, request: Request) -> FileResponse:
        """Stream a workspace-scoped library asset for the desktop review player."""
        workspace_id = self._workspace_for(request)
        try:
            asset = self._records_for().get_asset(workspace_id, asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='material asset not found') from exc
        if not self._visible(request, workspace_id, asset.visibility, asset.owner_user_id):
            raise HTTPException(status_code=404, detail='material asset not found')
        root = Path(self._library_root()).expanduser().resolve()
        path = Path(asset.path).expanduser().resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail='material asset is outside the library') from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail='material file not found')
        media_type = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.m4v': 'video/x-m4v', '.webm': 'video/webm', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.aac': 'audio/aac', '.wav': 'audio/wav', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif'}.get(path.suffix.lower(), 'application/octet-stream')
        return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type='inline')

    def update_material_use_state(self, asset_id: str, request: Request, payload: MaterialUseStateRequest) -> MaterialAssetResponse:
        try:
            asset = self._records_for().update_asset_use_state(self._workspace_for(request), asset_id, payload.use_state)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='material asset not found') from exc
        return self._asset_response(asset)

    def review_production_material(self, asset_id: str, request: Request, payload: MaterialReviewRequest) -> MaterialAssetResponse:
        try:
            asset = self._records_for().update_asset_review(self._workspace_for(request), asset_id, review_status=payload.review_status, usage_role=payload.usage_role)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='material asset not found') from exc
        return self._asset_response(asset)

    def update_production_material_segment(self, asset_id: str, segment_id: str, request: Request, payload: MaterialSegmentUpdateRequest) -> MaterialAssetResponse:
        self.get_production_material(asset_id, request)
        try:
            asset = self._records_for().update_segment(self._workspace_for(request), asset_id, segment_id, description=payload.description, transcript=payload.transcript, review_status=payload.review_status, usage_role=payload.usage_role, user_tags=payload.user_tags, collection_ids=payload.collection_ids, expected_asset_updated_at=payload.expected_asset_updated_at, expected_segment_updated_at=payload.expected_segment_updated_at)
        except MaterialVersionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='material segment not found') from exc
        return self._asset_response(asset)

def build_material_library_router(adapter_for, route_names: tuple[str, ...] | None = None) -> APIRouter:
    """Keep registration at the host's original positions among other families."""
    router = APIRouter()

    @router.get('/api/production/materials', response_model=MaterialLibraryResponse)
    def list_production_materials(request: Request, limit: int=24, offset: int=0, q: str | None=None, tag: str | None=None, review_status: str | None=None, media_type: str | None=None) -> MaterialLibraryResponse:
        return adapter_for().list_production_materials(request, limit, offset, q, tag, review_status, media_type)

    @router.put('/api/production/materials/{asset_id}/tags', response_model=MaterialAssetResponse)
    def update_material_asset_tags(asset_id: str, request: Request, payload: MaterialAssetTagsRequest) -> MaterialAssetResponse:
        return adapter_for().update_material_asset_tags(asset_id, request, payload)

    @router.post('/api/production/materials/tags/batch', response_model=MaterialBatchTagsResponse)
    def update_material_asset_tags_batch(request: Request, payload: MaterialBatchTagsRequest) -> MaterialBatchTagsResponse:
        return adapter_for().update_material_asset_tags_batch(request, payload)

    @router.put('/api/production/materials/notes', response_model=MaterialAssetNotesResponse)
    def update_material_asset_notes(request: Request, payload: MaterialAssetNotesRequest) -> MaterialAssetNotesResponse:
        return adapter_for().update_material_asset_notes(request, payload)

    @router.get('/api/production/materials/{asset_id}/usage-records', response_model=MaterialUsageRecordsResponse)
    def list_material_segment_usage_records(request: Request, asset_id: str, segment_id: str) -> MaterialUsageRecordsResponse:
        return adapter_for().list_material_segment_usage_records(request, asset_id, segment_id)

    @router.get('/api/production/materials/{asset_id}/unmatched-corrections')
    def get_unmatched_material_corrections(asset_id: str, request: Request) -> dict[str, object]:
        return adapter_for().get_unmatched_material_corrections(asset_id, request)

    @router.get('/api/production/materials/search', response_model=list[MaterialSegmentMatchResponse] | MaterialSegmentSearchResponse)
    def search_production_material_segments(request: Request, q: str='', usage_role: str | None=None, tags: str | None=None, min_duration_seconds: float | None=None, max_duration_seconds: float | None=None, limit: int=20, purpose: Literal['manual', 'automatic']='manual', include_eligibility: bool=False) -> list[MaterialSegmentMatchResponse] | MaterialSegmentSearchResponse:
        return adapter_for().search_production_material_segments(request, q, usage_role, tags, min_duration_seconds, max_duration_seconds, limit, purpose, include_eligibility)

    @router.put('/api/production/materials/{asset_id}/visibility', response_model=MaterialAssetResponse)
    def set_production_material_visibility(asset_id: str, request: Request, payload: ContentVisibilityRequest) -> MaterialAssetResponse:
        return adapter_for().set_production_material_visibility(asset_id, request, payload)

    @router.get('/api/production/materials/{asset_id}', response_model=MaterialAssetResponse)
    def get_production_material(asset_id: str, request: Request) -> MaterialAssetResponse:
        return adapter_for().get_production_material(asset_id, request)

    @router.get('/api/production/materials/{asset_id}/media')
    def stream_production_material(asset_id: str, request: Request) -> FileResponse:
        return adapter_for().stream_production_material(asset_id, request)

    @router.put('/api/production/materials/{asset_id}/use-state', response_model=MaterialAssetResponse)
    def update_material_use_state(asset_id: str, request: Request, payload: MaterialUseStateRequest) -> MaterialAssetResponse:
        return adapter_for().update_material_use_state(asset_id, request, payload)

    @router.put('/api/production/materials/{asset_id}/review', response_model=MaterialAssetResponse)
    def review_production_material(asset_id: str, request: Request, payload: MaterialReviewRequest) -> MaterialAssetResponse:
        return adapter_for().review_production_material(asset_id, request, payload)

    @router.put('/api/production/materials/{asset_id}/segments/{segment_id}', response_model=MaterialAssetResponse)
    def update_production_material_segment(asset_id: str, segment_id: str, request: Request, payload: MaterialSegmentUpdateRequest) -> MaterialAssetResponse:
        return adapter_for().update_production_material_segment(asset_id, segment_id, request, payload)

    if route_names is not None:
        router.routes = [route for route in router.routes if route.name in route_names]
    return router
