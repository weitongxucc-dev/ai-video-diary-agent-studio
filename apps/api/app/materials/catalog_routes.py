"""Material collection and business-name HTTP management.

Existing collection/name owners retain validation, association order and SQL.
Only current workspace/finite record ports and HTTP projections are composed here.
"""
from __future__ import annotations

from typing import Callable, Protocol
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from app.materials.contracts import MaterialCollection, MaterialBusinessObject
from app.schemas import (
    MaterialCollectionsResponse, MaterialCollectionResponse,
    MaterialCollectionCreateRequest, MaterialBusinessObjectsResponse,
    MaterialBusinessObjectResponse, MaterialBusinessObjectCreateRequest,
    MaterialAssetBusinessObjectsSaveRequest,
)


class MaterialCatalogRecords(Protocol):
    def list_collections(self, workspace_id): ...
    def save_collection(self, collection): ...
    def list_business_objects(self, workspace_id): ...
    def create_business_object(self, workspace_id, kind, name): ...
    def list_asset_business_objects(self, workspace_id, asset_id): ...
    def set_asset_business_objects(self, workspace_id, asset_id, business_object_ids): ...


def business_object_response(entry: MaterialBusinessObject) -> MaterialBusinessObjectResponse:
    return MaterialBusinessObjectResponse(id=entry.id, kind=entry.kind, name=entry.name)


class MaterialCatalogHTTPAdapter:
    def __init__(self, *, records_for: Callable[[], MaterialCatalogRecords], workspace_for):
        self._records_for = records_for
        self._workspace_for = workspace_for


    def list_material_collections(self, request: Request) -> MaterialCollectionsResponse:
        collections = self._records_for().list_collections(self._workspace_for(request))
        return MaterialCollectionsResponse(collections=[MaterialCollectionResponse.model_validate(item, from_attributes=True) for item in collections], total_count=len(collections))

    def create_material_collection(self, request: Request, payload: MaterialCollectionCreateRequest) -> MaterialCollectionResponse:
        workspace_id = self._workspace_for(request)
        collection = MaterialCollection(id=f'collection_{uuid4().hex[:10]}', workspace_id=workspace_id, name=payload.name.strip(), parent_id=payload.parent_id)
        try:
            saved = self._records_for().save_collection(collection)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='parent collection not found') from exc
        return MaterialCollectionResponse.model_validate(saved, from_attributes=True)

    def list_material_business_objects(self, request: Request) -> MaterialBusinessObjectsResponse:
        "List this workspace's product/dish/person name entries.\n\n    Entries are scoped to the calling workspace, and creating one attaches it to\n    nothing: the user still chooses which materials carry it.\n    "
        entries = self._records_for().list_business_objects(self._workspace_for(request))
        return MaterialBusinessObjectsResponse(items=[business_object_response(entry) for entry in entries], total_count=len(entries))

    def create_material_business_object(self, request: Request, payload: MaterialBusinessObjectCreateRequest) -> MaterialBusinessObjectResponse:
        """Add one name entry without associating it to any material yet."""
        try:
            entry = self._records_for().create_business_object(self._workspace_for(request), payload.kind, payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return business_object_response(entry)

    def list_material_asset_business_objects(self, request: Request, asset_id: str) -> MaterialBusinessObjectsResponse:
        """Read back the name entries attached to one material."""
        try:
            entries = self._records_for().list_asset_business_objects(self._workspace_for(request), asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='素材不存在或不属于当前工作区') from exc
        return MaterialBusinessObjectsResponse(items=[business_object_response(entry) for entry in entries], total_count=len(entries))

    def save_material_asset_business_objects(self, request: Request, asset_id: str, payload: MaterialAssetBusinessObjectsSaveRequest) -> MaterialBusinessObjectsResponse:
        'Save exactly the name entries the user selected for this one material.\n\n    Only the material named in the path is touched, and the repository refuses\n    the whole selection if any entry belongs to another workspace, so the user is\n    never shown associations that were not written. An empty list clears them.\n    '
        try:
            entries = self._records_for().set_asset_business_objects(self._workspace_for(request), asset_id, list(payload.business_object_ids))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='素材或名称条目不属于当前工作区，未做任何修改') from exc
        return MaterialBusinessObjectsResponse(items=[business_object_response(entry) for entry in entries], total_count=len(entries))


def build_material_catalog_router(adapter_for, route_names: tuple[str, ...] | None = None) -> APIRouter:
    """Preserve each route's original registration position among other families."""
    router = APIRouter()

    @router.get('/api/production/material-collections', response_model=MaterialCollectionsResponse)
    def list_material_collections(request: Request) -> MaterialCollectionsResponse:
        return adapter_for().list_material_collections(request)

    @router.post('/api/production/material-collections', response_model=MaterialCollectionResponse)
    def create_material_collection(request: Request, payload: MaterialCollectionCreateRequest) -> MaterialCollectionResponse:
        return adapter_for().create_material_collection(request, payload)

    @router.get('/api/production/business-objects', response_model=MaterialBusinessObjectsResponse)
    def list_material_business_objects(request: Request) -> MaterialBusinessObjectsResponse:
        return adapter_for().list_material_business_objects(request)

    @router.post('/api/production/business-objects', response_model=MaterialBusinessObjectResponse, status_code=201)
    def create_material_business_object(request: Request, payload: MaterialBusinessObjectCreateRequest) -> MaterialBusinessObjectResponse:
        return adapter_for().create_material_business_object(request, payload)

    @router.get('/api/production/materials/{asset_id}/business-objects', response_model=MaterialBusinessObjectsResponse)
    def list_material_asset_business_objects(request: Request, asset_id: str) -> MaterialBusinessObjectsResponse:
        return adapter_for().list_material_asset_business_objects(request, asset_id)

    @router.put('/api/production/materials/{asset_id}/business-objects', response_model=MaterialBusinessObjectsResponse)
    def save_material_asset_business_objects(request: Request, asset_id: str, payload: MaterialAssetBusinessObjectsSaveRequest) -> MaterialBusinessObjectsResponse:
        return adapter_for().save_material_asset_business_objects(request, asset_id, payload)

    if route_names is not None:
        router.routes = [route for route in router.routes if route.name in route_names]
    return router
