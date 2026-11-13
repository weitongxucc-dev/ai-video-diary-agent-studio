"""HTTP read views for the editing resource manifest."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query

from app.editing_resource_catalog import ResourceStatus, search_editing_resources


def build_editing_resource_router(catalog_for: Callable):
    router = APIRouter()

    @router.get("/api/editing-resource-catalog")
    def read_editing_resource_catalog(
        q: str | None = Query(default=None, max_length=120),
        category: str | None = Query(default=None, max_length=80),
        status: ResourceStatus | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        catalog = catalog_for()
        try:
            items, total_count = search_editing_resources(catalog, query=q, category=category,
                status=status, offset=offset, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "categories": [item.model_dump(mode="json") for item in catalog.categories],
            "resources": [item.model_dump(mode="json") for item in items],
            "offset": offset,
            "total_count": total_count,
            "truncated": offset + len(items) < total_count,
            "next_offset": offset + len(items) if offset + len(items) < total_count else None,
            "provenance_fields": catalog.provenance_fields,
            "write_boundary": catalog.write_boundary,
        }

    @router.get("/api/editing-resources")
    def list_editing_resources(
        q: str | None = Query(default=None, max_length=120),
        category: str | None = Query(default=None, max_length=80),
        status: ResourceStatus | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return read_editing_resource_catalog(q=q, category=category, status=status, offset=offset, limit=limit)

    @router.get("/api/editing-resources/{resource_id}")
    def read_editing_resource(resource_id: str, version: int = Query(default=1, ge=1)):
        catalog = catalog_for()
        item = next((item for item in catalog.resources if item.id == resource_id and item.version == version), None)
        if item is None:
            raise HTTPException(status_code=404, detail="资源版本不存在")
        return item.model_dump(mode="json")

    @router.get("/api/editing-resources/{resource_id}/preflight")
    def preflight_editing_resource(resource_id: str, version: int = Query(default=1, ge=1)):
        catalog = catalog_for()
        item = next((item for item in catalog.resources if item.id == resource_id and item.version == version), None)
        if item is None:
            raise HTTPException(status_code=404, detail="资源版本不存在")
        requirements = item.runtime_requirements
        return {
            "resource_id": item.id,
            "version": item.version,
            "status": item.status,
            "ready": item.status == "connected",
            "provenance": [entry.model_dump(mode="json") for entry in item.provenance],
            "install_checks": requirements.get("install_checks", {"mode": "not_declared", "checks": []}),
            "adoption": requirements.get("adoption", {"status": "not_declared"}),
            "rollback": requirements.get("rollback", {"mode": "not_declared"}),
            "write_boundary": "资源导入/读取本身只读；编辑采用仍须经现有 EditProposal/TimelineVersion。",
        }

    return router
