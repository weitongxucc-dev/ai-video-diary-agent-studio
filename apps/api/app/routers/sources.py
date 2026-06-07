from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from app.external_sources import ExternalSourceResponseError
from app.routers.context import AppContext
from app.schemas import ManualLinkRequest, SourceItemResponse, SourceItemsResponse
from app.sources import TopicSourceItem


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sources/items", response_model=SourceItemsResponse)
    def get_source_items() -> SourceItemsResponse:
        context = get_context()
        items = [
            *_hermes_items(context),
            *context.manual_source_items,
        ]
        return _source_items_response(items)

    @router.post("/api/sources/sync", response_model=SourceItemsResponse)
    def sync_source_items() -> SourceItemsResponse:
        context = get_context()
        items = [
            *_hermes_items(context),
            *context.manual_source_items,
        ]
        if context.settings.topic_source_urls:
            try:
                items.extend(
                    _external_items(context, list(context.settings.topic_source_urls))
                )
            except ExternalSourceResponseError:
                pass
        return _source_items_response(items)

    @router.post("/api/sources/links", response_model=SourceItemsResponse)
    def ingest_manual_link(request: ManualLinkRequest) -> SourceItemsResponse:
        url = request.url.strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400,
                detail="url must start with http:// or https://",
            )
        context = get_context()
        try:
            items = _external_items(context, [url])
        except ExternalSourceResponseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        context.manual_source_items.extend(items)
        return _source_items_response(context.manual_source_items)

    return router


def _hermes_items(context: AppContext) -> list[TopicSourceItem]:
    if context.hermes_source_client_factory is None:
        return []
    return context.hermes_source_client_factory(
        context.settings.hermes_radar_path
    ).fetch_items()


def _external_items(context: AppContext, urls: list[str]) -> list[TopicSourceItem]:
    if context.external_source_client_factory is None:
        return []
    return context.external_source_client_factory(urls).fetch_items()


def _source_items_response(items: list[TopicSourceItem]) -> SourceItemsResponse:
    counts = Counter(item.source_type for item in items)
    return SourceItemsResponse(
        items=[SourceItemResponse.model_validate(item, from_attributes=True) for item in items],
        total_count=len(items),
        source_counts=dict(counts),
    )
