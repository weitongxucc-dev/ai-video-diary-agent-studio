"""Knowledge adoption, owned corrections, visibility and fixed original HTTP."""
from fastapi import APIRouter
from urllib.parse import quote
from fastapi import HTTPException, Request
from fastapi.responses import Response
from uuid import uuid4
from app.schemas import (
    KnowledgeClaimRequest,
    KnowledgeClaimResponse,
    KnowledgeClaimsResponse,
    KnowledgeClaimStatusRequest,
    KnowledgeSearchResponse,
)
from app.source_repository import KnowledgeVersionConflict
from app.knowledge_file_store import KnowledgeOriginalUnavailable, KnowledgeOriginalIntegrityError, KnowledgeOriginalStorageError
from app.schemas import KnowledgeItemResponse, KnowledgeItemsResponse, KnowledgeItemDetailResponse, KnowledgeItemVersionResponse, KnowledgeItemVisibilityRequest, KnowledgeItemContentRequest, SaveSourceItemToKnowledgeRequest
from app.source_types import KnowledgeItem
from app.materials.data_governance import KnowledgeClaim


def _knowledge_item_response(item: KnowledgeItem, visibility: str) -> KnowledgeItemResponse:
    response = KnowledgeItemResponse.model_validate(item, from_attributes=True)
    response.visibility = visibility
    return response



def build_knowledge_router(*, records_for, workspace_for, reader_for, write_guard,
                           governance_for, route_names) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/source-items/{source_item_id}/save-to-knowledge",
        response_model=KnowledgeItemResponse,
    )
    def save_source_item_to_knowledge(
        source_item_id: str,
        request: Request,
        payload: SaveSourceItemToKnowledgeRequest,
    ) -> KnowledgeItemResponse:
        workspace_id = workspace_for(request)
        try:
            item = records_for().save_source_item_to_knowledge(
                workspace_id,
                source_item_id,
                verified_owner_user_id=reader_for(request, workspace_id)[0],
            )
        except (KeyError, PermissionError) as exc:
            raise HTTPException(status_code=404, detail="source item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = _knowledge_item_response(
            item, records_for().get_knowledge_item_visibility(workspace_id, item.id)
        )
        response.saved_source_item_id = source_item_id
        # 重复结果只来自解析链：内容指纹命中已有历史版本时给出对应版本号。
        if item.content_hash is not None and response.duplicate_of_version is None:
            source = records_for().get_source_item(workspace_id, source_item_id)
            if source.content_hash != item.content_hash:
                stored = records_for().find_version_by_content_hash(
                    workspace_id,
                    source.content_hash,
                    knowledge_item_id=item.id if source.source_label == "web_snapshot" else None,
                    verified_owner_user_id=reader_for(request, workspace_id)[0],
                )
                response.duplicate_of_version = stored.version if stored else None
        return response



    @router.get("/api/knowledge-items/{knowledge_item_id}/versions/{version}/original")
    def download_knowledge_original(knowledge_item_id: str, version: str, request: Request):
        workspace_id = workspace_for(request)
        owner, diagnostic = reader_for(request, workspace_id)
        try:
            original = records_for().read_knowledge_original(workspace_id, knowledge_item_id, version,
                user_id=owner, local_diagnostic=diagnostic)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="没有找到可访问的资料原件或固定版本。") from exc
        except KnowledgeOriginalUnavailable as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KnowledgeOriginalIntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (KnowledgeOriginalStorageError, OSError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return Response(original.content, media_type=original.content_type, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(original.filename, safe='')}",
            "ETag": f'"{original.content_hash}"', "X-Content-Type-Options": "nosniff"})



    @router.get("/api/knowledge-items/search", response_model=KnowledgeSearchResponse)
    def search_knowledge_items(request: Request, query: str, limit: int = 5, offset: int = 0):
        if not 1 <= len(query.strip()) <= 120 or not 1 <= limit <= 20 or offset < 0:
            raise HTTPException(status_code=422, detail="资料检索须为 1–120 字，分页大小 1–20，偏移非负")
        workspace_id = workspace_for(request)
        user_id, diagnostic = reader_for(request, workspace_id)
        try:
            items, total = records_for().search_knowledge_items(workspace_id, query=query,
                limit=limit, offset=offset, user_id=user_id, local_diagnostic=diagnostic)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return KnowledgeSearchResponse(items=items, total_count=total, limit=limit, offset=offset,
                                       next_offset=offset + len(items) if offset + len(items) < total else None)



    @router.get("/api/knowledge-items/{knowledge_item_id}", response_model=KnowledgeItemDetailResponse)
    def get_knowledge_item_detail(
        knowledge_item_id: str,
        request: Request,
    ) -> KnowledgeItemDetailResponse:
        workspace_id = workspace_for(request)
        user_id, local_diagnostic = reader_for(request, workspace_id)
        try:
            item = records_for().get_knowledge_item_readable_by(
                workspace_id, knowledge_item_id, user_id=user_id, local_diagnostic=local_diagnostic,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="knowledge item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        visibility = records_for().get_knowledge_item_visibility(item.workspace_id, item.id)
        versions = records_for().list_knowledge_item_versions(item.workspace_id, item.id)
        detail = KnowledgeItemDetailResponse.model_validate(item, from_attributes=True)
        detail.visibility = visibility
        detail.versions = [
            KnowledgeItemVersionResponse.model_validate(version, from_attributes=True)
            for version in versions
        ]
        return detail


    @router.get("/api/knowledge-items/{knowledge_item_id}/claims", response_model=KnowledgeClaimsResponse)
    def list_knowledge_claims(knowledge_item_id: str, request: Request, version: str | None = None):
        workspace_id = workspace_for(request)
        user_id, diagnostic = reader_for(request, workspace_id)
        try:
            item = records_for().get_knowledge_item_readable_by(
                workspace_id, knowledge_item_id, user_id=user_id, local_diagnostic=diagnostic,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="knowledge item not found") from exc
        if version is not None and version != item.version and not any(
            record.version == version for record in records_for().list_knowledge_item_versions(item.workspace_id, item.id)
        ):
            raise HTTPException(status_code=404, detail="knowledge item version not found")
        claims = governance_for().list_knowledge_claims(workspace_id, item.id, knowledge_version=version)
        return KnowledgeClaimsResponse(
            claims=[KnowledgeClaimResponse.model_validate(claim, from_attributes=True) for claim in claims]
        )


    @router.post("/api/knowledge-items/{knowledge_item_id}/claims", response_model=KnowledgeClaimResponse)
    def create_knowledge_claim(
        knowledge_item_id: str, request: Request, payload: KnowledgeClaimRequest,
    ) -> KnowledgeClaimResponse:
        denied = write_guard(request)
        if denied is not None:
            return denied
        workspace_id = workspace_for(request)
        user_id, diagnostic = reader_for(request, workspace_id)
        try:
            item = records_for().get_knowledge_item_readable_by(
                workspace_id, knowledge_item_id, user_id=user_id, local_diagnostic=diagnostic,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="knowledge item not found") from exc
        versions = records_for().list_knowledge_item_versions(item.workspace_id, item.id)
        if payload.knowledge_version != item.version and not any(
            record.version == payload.knowledge_version for record in versions
        ):
            raise HTTPException(status_code=404, detail="knowledge item version not found")
        claim = KnowledgeClaim(
            id=f"claim_{uuid4().hex}", workspace_id=workspace_id,
            knowledge_item_id=item.id, knowledge_version=payload.knowledge_version,
            field_ref=payload.field_ref, value=payload.value, unit=payload.unit,
            qualifiers=payload.qualifiers, evidence_refs=tuple(payload.evidence_refs),
            origin=payload.origin, confidence=payload.confidence, status=payload.status,
            asserted_by=user_id or "local-diagnostic", supersedes_ref=payload.supersedes_ref,
        )
        try:
            saved = governance_for().record_knowledge_claim(claim)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return KnowledgeClaimResponse.model_validate(saved, from_attributes=True)


    @router.put("/api/knowledge-items/{knowledge_item_id}/claims/{claim_id}/status", response_model=KnowledgeClaimResponse)
    def update_knowledge_claim_status(
        knowledge_item_id: str, claim_id: str, request: Request, payload: KnowledgeClaimStatusRequest,
    ) -> KnowledgeClaimResponse:
        denied = write_guard(request)
        if denied is not None:
            return denied
        workspace_id = workspace_for(request)
        user_id, diagnostic = reader_for(request, workspace_id)
        try:
            item = records_for().get_knowledge_item_readable_by(
                workspace_id, knowledge_item_id, user_id=user_id, local_diagnostic=diagnostic,
            )
            claim = governance_for().get_knowledge_claim(workspace_id, claim_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="knowledge item or claim not found") from exc
        if claim.knowledge_item_id != item.id:
            raise HTTPException(status_code=404, detail="knowledge item or claim not found")
        try:
            updated = governance_for().transition_knowledge_claim(
                workspace_id, claim_id, status=payload.status, expected_status=payload.expected_status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return KnowledgeClaimResponse.model_validate(updated, from_attributes=True)



    @router.put("/api/knowledge-items/{knowledge_item_id}/content", response_model=KnowledgeItemDetailResponse)
    def edit_knowledge_item_content(
        knowledge_item_id: str, request: Request, payload: KnowledgeItemContentRequest,
    ) -> KnowledgeItemDetailResponse:
        workspace_id = workspace_for(request)
        user_id, local_diagnostic = reader_for(request, workspace_id)
        try:
            records_for().edit_knowledge_item(
                workspace_id, knowledge_item_id, expected_version=payload.expected_version,
                title=payload.title, body=payload.body, verified_user_id=user_id,
                local_diagnostic=local_diagnostic,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="资料不存在或不属于当前工作区") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KnowledgeVersionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return get_knowledge_item_detail(knowledge_item_id, request)



    @router.put(
        "/api/knowledge-items/{knowledge_item_id}/visibility",
        response_model=KnowledgeItemResponse,
    )
    def set_knowledge_item_visibility_route(
        knowledge_item_id: str,
        request: Request,
        payload: KnowledgeItemVisibilityRequest,
    ) -> KnowledgeItemResponse:
        """显式共享或收回：把私有内容放进/移出团队共享范围。"""
        denied = write_guard(request)
        if denied is not None:
            return denied
        workspace_id = workspace_for(request)
        try:
            visibility = records_for().set_knowledge_item_visibility(
                workspace_id, knowledge_item_id, payload.visibility
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="knowledge item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        item = records_for().get_knowledge_item(workspace_id, knowledge_item_id)
        return _knowledge_item_response(item, visibility)



    @router.get("/api/knowledge-items", response_model=KnowledgeItemsResponse)
    def list_knowledge_items(request: Request) -> KnowledgeItemsResponse:
        workspace_id = workspace_for(request)
        user_id, local_diagnostic = reader_for(request, workspace_id)
        try:
            items = records_for().list_knowledge_items_readable_by(
                workspace_id, user_id=user_id, local_diagnostic=local_diagnostic
            )
        except ValueError as exc:
            # 已开认证却没有可信身份：没有可读身份就是没有可读内容，不退回只看共享。
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return KnowledgeItemsResponse(
            items=[
                _knowledge_item_response(
                    item, records_for().get_knowledge_item_visibility(item.workspace_id, item.id)
                )
                for item in items
            ],
            total_count=len(items),
        )



    router.routes = [route for route in router.routes if route.name in route_names]
    return router
