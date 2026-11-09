"""Source-item intake and legacy source HTTP; original owners retain parsing and persistence."""
from fastapi import APIRouter
import base64
from collections import Counter
from dataclasses import replace
from urllib.parse import urlparse
from uuid import uuid4
from fastapi import HTTPException, Request
from app.external_sources import ExternalSourceResponseError
from app.knowledge_file_store import KnowledgeOriginalIntegrityError, KnowledgeOriginalStorageError
from app.schemas import ManualSourceItemRequest, ManualLinkRequest, SourceItemDetailResponse, SourceItemDetailsResponse, KnowledgeFileImportRequest, SourceItemResponse, SourceItemsResponse, SourceSyncRetryResponse
from app.knowledge_files import KnowledgeFileError
from app.source_types import SourceConnector, SourceItem
from app.source_sync import SourceSyncFailedError


def build_source_item_router(*, records_for, workspace_for, reader_for, sync_for,
                             failure_for, html_client_for, parse_for, hermes_path_for,
                             topic_urls_for, urls_required_for, route_names) -> APIRouter:
    router = APIRouter()

    def _source_item_response(item: SourceItem) -> SourceItemDetailResponse:
        return SourceItemDetailResponse.model_validate(item, from_attributes=True)



    def _manual_connector(workspace_id: str) -> SourceConnector:
        connector_id = f"manual:{workspace_id}"
        try:
            return records_for().get_connector(workspace_id, connector_id)
        except KeyError:
            return records_for().upsert_connector(
                SourceConnector(
                    id=connector_id,
                    workspace_id=workspace_id,
                    platform="manual",
                    protocol="local",
                    account_or_rule="user-provided content",
                    frequency="on_demand",
                    permissions=("write:source_items",),
                )
            )



    def _compat_connector(
        workspace_id: str,
        key: str,
        *,
        platform: str,
        protocol: str,
        account_or_rule: str,
    ) -> SourceConnector:
        connector_id = f"compat:{key}:{workspace_id}"
        try:
            existing = records_for().get_connector(workspace_id, connector_id)
            if existing.account_or_rule != account_or_rule:
                return records_for().upsert_connector(
                    replace(existing, account_or_rule=account_or_rule)
                )
            return existing
        except KeyError:
            return records_for().upsert_connector(
                SourceConnector(
                    id=connector_id,
                    workspace_id=workspace_id,
                    platform=platform,
                    protocol=protocol,
                    account_or_rule=account_or_rule,
                    frequency="on_demand",
                    permissions=("read:source_items",),
                )
            )



    def _legacy_source_items_response(
        workspace_id: str,
        items: list[SourceItem],
    ) -> SourceItemsResponse:
        source_items = [
            SourceItemResponse(
                source_type=item.source_label,
                title=item.title,
                ref=item.source_url or item.file_reference or item.id,
                excerpt=item.body or item.subtitles or "",
            )
            for item in items
        ]
        return SourceItemsResponse(
            items=source_items,
            total_count=len(source_items),
            source_counts=dict(Counter(item.source_type for item in source_items)),
        )



    @router.get("/api/source-items", response_model=SourceItemDetailsResponse)
    def list_persisted_source_items(
        request: Request,
        connector_id: str | None = None,
    ) -> SourceItemDetailsResponse:
        items = records_for().list_source_items(
            workspace_for(request), connector_id
        )
        return SourceItemDetailsResponse(
            items=[_source_item_response(item) for item in items],
            total_count=len(items),
        )



    @router.post("/api/source-items/manual", response_model=SourceItemDetailResponse)
    def create_manual_source_item(
        request: Request,
        payload: ManualSourceItemRequest,
    ) -> SourceItemDetailResponse:
        workspace_id = workspace_for(request)
        connector = _manual_connector(workspace_id)
        source_url = payload.source_url.strip() if payload.source_url else None
        if source_url:
            parsed = urlparse(source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise HTTPException(
                    status_code=422,
                    detail="manual source_url must use http or https",
                )
        try:
            item = records_for().save_source_items(
                workspace_id,
                [
                    SourceItem(
                        id=f"source_{uuid4().hex}",
                        workspace_id=workspace_id,
                        connector_id=connector.id,
                        source_label="manual",
                        source_url=source_url,
                        content_type=payload.content_type.strip(),
                        author=payload.author.strip() if payload.author else None,
                        published_at=payload.published_at,
                        body=payload.body,
                        subtitles=payload.subtitles,
                        media_references=tuple(payload.media_references),
                        license=payload.license.strip(),
                        title=payload.title.strip(),
                        file_reference=(
                            payload.file_reference.strip()
                            if payload.file_reference
                            else None
                        ),
                        use_state=payload.use_state,
                    )
                ],
            )[0]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _source_item_response(item)



    @router.post("/api/source-items/file", response_model=SourceItemDetailResponse)
    def import_knowledge_file(
        request: Request,
        payload: KnowledgeFileImportRequest,
    ) -> SourceItemDetailResponse:
        """导入 Markdown/文本/PDF/Word/Excel 文件：保存全文、真实出处和显式参数。

        解析失败（不支持格式、扫描件无文本层）直接报错，不写入伪造正文。
        """
        workspace_id = workspace_for(request)
        try:
            data = base64.b64decode(payload.content_base64, validate=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="文件内容不是合法的 base64") from exc
        try:
            parsed = parse_for(payload.filename.strip(), data)
        except KnowledgeFileError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        connector = _compat_connector(
            workspace_id,
            "knowledge_files",
            platform="knowledge_files",
            protocol="local_file",
            account_or_rule="知识文件导入",
        )
        try:
            item = records_for().save_source_file(
                workspace_id,
                    SourceItem(
                        id=f"source_{uuid4().hex}",
                        workspace_id=workspace_id,
                        connector_id=connector.id,
                        source_label="file_import",
                        source_url=None,
                        content_type=parsed.content_type,
                        author=None,
                        published_at=None,
                        body=parsed.full_text,
                        subtitles=None,
                        media_references=(),
                        license="user_provided",
                        title=parsed.title,
                        file_reference=parsed.filename,
                        content_hash=parsed.content_hash,
                        sections=parsed.sections,
                        parameters=parsed.parameters,
                        use_state=payload.use_state,
                    ), data, verified_owner_user_id=reader_for(request, workspace_id)[0],
            )
        except KnowledgeOriginalIntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (KnowledgeOriginalStorageError, OSError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _source_item_response(item)



    @router.get("/api/sources/items", response_model=SourceItemsResponse)
    def get_source_items(request: Request) -> SourceItemsResponse:
        workspace_id = workspace_for(request)
        return _legacy_source_items_response(
            workspace_id, records_for().list_source_items(workspace_id)
        )



    @router.post("/api/sources/sync", response_model=SourceItemsResponse)
    def sync_source_items(request: Request) -> SourceItemsResponse:
        workspace_id = workspace_for(request)
        connectors = [
            _compat_connector(
                workspace_id,
                "hermes_radar",
                platform="hermes_radar",
                protocol="local_file",
                account_or_rule=str(hermes_path_for() or ""),
            )
        ]
        if topic_urls_for():
            connectors.append(
                _compat_connector(
                    workspace_id,
                    "external_urls",
                    platform="external_urls",
                    protocol="https",
                    account_or_rule="\n".join(topic_urls_for()),
                )
            )
        for connector in connectors:
            sync_for(workspace_id, connector)
        return _legacy_source_items_response(
            workspace_id, records_for().list_source_items(workspace_id)
        )



    @router.post("/api/sources/links", response_model=SourceItemsResponse)
    def ingest_manual_link(
        request: Request,
        payload: ManualLinkRequest,
    ) -> SourceItemsResponse:
        url = payload.url.strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="url must start with http:// or https://")
        workspace_id = workspace_for(request)
        connector = _manual_connector(workspace_id)
        try:
            topic_items = html_client_for([url]).fetch_items()
        except ExternalSourceResponseError as exc:
            failure = SourceSyncFailedError(
                records_for().record_sync_failure(
                    workspace_id,
                    connector.id,
                    reason=str(exc),
                    impact=exc.impact,
                    repair_action=exc.repair_action,
                    retryable=exc.retryable,
                    connection_status=exc.kind,
                )
            )
            raise failure_for(failure) from exc
        records_for().save_source_items(
            workspace_id,
            [replace(item.to_source_item(workspace_id, connector.id), use_state=payload.use_state)
             for item in topic_items],
        )
        return _legacy_source_items_response(
            workspace_id,
            records_for().list_source_items(workspace_id, connector.id),
        )



    @router.post("/api/source-items/link", response_model=SourceItemDetailResponse)
    def capture_web_snapshot(
        request: Request,
        payload: ManualLinkRequest,
    ) -> SourceItemDetailResponse:
        """抓一个普通网址的完整正文快照，保存用户输入的原网址和抓取时间。

        原网址就是用户填的那个地址，并按它串版本；重定向到的地址作为附加事实记录，
        不替换原网址。相同正文不新增版本（保存到知识库时由内容指纹判定）；网址内容
        更新时另存新版本，旧版本及其快照保持不变。登录、不可达或没有正文按真实失败。
        """
        url = payload.url.strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="url must start with http:// or https://")
        workspace_id = workspace_for(request)
        connector = _compat_connector(
            workspace_id,
            "web_snapshot",
            platform="web_snapshot",
            protocol="https",
            account_or_rule="网页快照",
        )
        try:
            fetched = html_client_for([url]).fetch_page(url)
        except ExternalSourceResponseError as exc:
            failure = SourceSyncFailedError(
                records_for().record_sync_failure(
                    workspace_id,
                    connector.id,
                    reason=str(exc),
                    impact=exc.impact,
                    repair_action=exc.repair_action,
                    retryable=exc.retryable,
                    connection_status=exc.kind,
                )
            )
            raise failure_for(failure) from exc
        snapshot = fetched.snapshot
        item = records_for().save_source_items(
            workspace_id,
            [
                SourceItem(
                    id=f"source_{uuid4().hex}",
                    workspace_id=workspace_id,
                    connector_id=connector.id,
                    source_label="web_snapshot",
                    source_url=url,
                    content_type="article",
                    author=None,
                    published_at=None,
                    body=snapshot.full_text,
                    subtitles=None,
                    media_references=(),
                    license="user_provided",
                    title=snapshot.title,
                    # 版本链按用户填的原网址串，不按重定向后的地址，也不按随机条目 id。
                    file_reference=url,
                    content_hash=snapshot.content_hash,
                    sections=snapshot.sections,
                    parameters=(),
                    fetched_at=fetched.fetched_at,
                    use_state=payload.use_state,
                )
            ],
        )[0]
        return _source_item_response(item)



    @router.post("/api/sources/{key}/retry", response_model=SourceSyncRetryResponse)
    def retry_source_sync(key: str, request: Request) -> SourceSyncRetryResponse:
        """Compatibility retry route backed by the same persistent connectors."""
        workspace_id = workspace_for(request)
        if key == "manual":
            connector = _manual_connector(workspace_id)
        elif key == "hermes_radar":
            connector = _compat_connector(
                workspace_id,
                key,
                platform="hermes_radar",
                protocol="local_file",
                account_or_rule=str(hermes_path_for() or ""),
            )
        elif key == "external_urls":
            connector = _compat_connector(
                workspace_id,
                key,
                platform="external_urls",
                protocol="https",
                account_or_rule=(
                    "\n".join(topic_urls_for())
                    if topic_urls_for()
                    else urls_required_for()
                ),
            )
        else:
            raise HTTPException(status_code=404, detail=f"Unknown source key: {key}")

        run = sync_for(workspace_id, connector)
        return SourceSyncRetryResponse(
            source_type=key,
            status="success",
            message=f"{connector.platform} sync completed: {run.item_count} items",
            item_count=run.item_count,
        )



    router.routes = [route for route in router.routes if route.name in route_names]
    return router
