"""Bound public-link check/import HTTP and finite service composition.

The existing importer and provenance owner retain security, downloads, file
publication and persistence. No network or format policy lives in the routes.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Protocol

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.media_link_import import (
    DEFAULT_MAX_BYTES, MediaLinkImportError, MediaLinkImportService,
)
from app.materials.source_files import MaterialSourceFileAccess
from app.schemas import (
    MediaLinkCheckRequest, MediaLinkCheckResponse,
    MediaLinkImportRequest, MediaLinkImportResponse,
)


class MaterialLinkHTTPRecords(Protocol):
    @property
    def material_source_files(self) -> MaterialSourceFileAccess: ...
    def get_asset(self, workspace_id, asset_id): ...


MEDIA_LINK_STATUS = {'invalid_url': 422, 'unsupported_scheme': 422, 'credentials_in_url': 422, 'social_page': 422, 'private_address': 422, 'host_unresolved': 422, 'web_page_not_media': 422, 'unsupported_media_type': 422, 'too_large': 413, 'credentials_required': 415, 'not_found': 404}


def media_link_failure(exc: MediaLinkImportError) -> HTTPException:
    return HTTPException(status_code=MEDIA_LINK_STATUS.get(exc.code, 502), detail={'code': exc.code, 'detail': exc.message})


class MaterialLinkHTTPAdapter:
    def __init__(self, *, records_for: Callable[[], MaterialLinkHTTPRecords],
                 workspace_for, reader_for, provenance_for):
        self._records_for = records_for
        self._workspace_for = workspace_for
        self._reader_for = reader_for
        self._provenance_for = provenance_for


    def service(self, workspace_id: str, verified_owner_user_id: str | None=None, local_diagnostic: bool=False) -> MediaLinkImportService:
        """Build the direct-link importer on top of the existing intake binding."""
        repository = self._records_for()
        try:
            intake_root = repository.material_source_files.material_link_target(workspace_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return MediaLinkImportService(client_factory=lambda: httpx.Client(follow_redirects=False, timeout=60.0, trust_env=False), intake_root=intake_root, provenance=self._provenance_for(repository, workspace_id, verified_owner_user_id, local_diagnostic), max_bytes=DEFAULT_MAX_BYTES)

    def check_production_material_link(self, request: Request, payload: MediaLinkCheckRequest) -> MediaLinkCheckResponse:
        workspace_id = self._workspace_for(request)
        service = self.service(workspace_id)
        try:
            result = service.check(payload.url)
        except MediaLinkImportError as exc:
            raise media_link_failure(exc) from exc
        return MediaLinkCheckResponse.model_validate(asdict(result))

    def import_production_material_link(self, request: Request, payload: MediaLinkImportRequest) -> MediaLinkImportResponse:
        workspace_id = self._workspace_for(request)
        viewer_user_id, local_diagnostic = self._reader_for(request, workspace_id)
        service = self.service(workspace_id, viewer_user_id, local_diagnostic)
        try:
            result = service.import_link(payload.url)
            asset = self._records_for().get_asset(workspace_id, result.asset_id)
        except MediaLinkImportError as exc:
            raise media_link_failure(exc) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='material asset not found') from exc
        return MediaLinkImportResponse(url=result.url, media_type=asset.media_type, file_name=asset.file_name, size_bytes=asset.size_bytes, file_path=asset.path, asset_id=asset.id, provenance_id=result.provenance_id, reused_existing_file=result.reused_existing_file, analysis_status=asset.analysis_status, analysis_message=asset.analysis_message)


def build_material_link_router(adapter_for, route_names: tuple[str, ...] | None = None) -> APIRouter:
    """Keep the two routes at their original positions among other families."""
    router = APIRouter()

    @router.post('/api/production/materials/link/check', response_model=MediaLinkCheckResponse)
    def check_production_material_link(request: Request, payload: MediaLinkCheckRequest) -> MediaLinkCheckResponse:
        return adapter_for().check_production_material_link(request, payload)

    @router.post('/api/production/materials/link/import', response_model=MediaLinkImportResponse)
    def import_production_material_link(request: Request, payload: MediaLinkImportRequest) -> MediaLinkImportResponse:
        return adapter_for().import_production_material_link(request, payload)

    if route_names is not None:
        router.routes = [route for route in router.routes if route.name in route_names]
    return router
