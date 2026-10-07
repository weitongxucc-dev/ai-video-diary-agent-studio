"""Knowledge grant HTTP commands composed with the application's existing identity resolver."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request

from app.knowledge_grants import GrantTargetResolver, grant_to_verified_target
from app.schemas import (
    KnowledgeGrantRequest,
    KnowledgeGrantResponse,
    KnowledgeGrantStateResponse,
    KnowledgeGrantTargetResponse,
    KnowledgeItemVersionResponse,
)
from app.source_repository import SourceRepository


def knowledge_grant_router(
    repository: SourceRepository,
    *,
    workspace_for: Callable[[Request], str],
    content_reader: Callable[[Request, str], tuple[str | None, bool]],
    target_resolver: GrantTargetResolver,
) -> APIRouter:
    router = APIRouter()

    def actor(request: Request) -> tuple[str, str]:
        workspace_id = workspace_for(request)
        user_id, _ = content_reader(request, workspace_id)
        if user_id is None:
            raise HTTPException(status_code=401, detail="知识授权需要已验证成员身份")
        return workspace_id, user_id

    @router.get("/api/knowledge-items/{item_id}/versions/{version}", response_model=KnowledgeItemVersionResponse)
    def fixed_version(item_id: str, version: str, request: Request) -> KnowledgeItemVersionResponse:
        workspace_id = workspace_for(request)
        user_id, local_diagnostic = content_reader(request, workspace_id)
        try:
            snapshot = repository.get_knowledge_version_readable_by(
                workspace_id, item_id, version, user_id=user_id, local_diagnostic=local_diagnostic,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="knowledge version not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return KnowledgeItemVersionResponse.model_validate(snapshot, from_attributes=True)

    @router.get("/api/knowledge-items/{item_id}/grants", response_model=KnowledgeGrantStateResponse)
    def state(item_id: str, request: Request) -> KnowledgeGrantStateResponse:
        workspace_id, user_id = actor(request)
        try:
            item = repository.get_knowledge_item_readable_by(workspace_id, item_id, user_id=user_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="knowledge item not found") from exc
        can_manage = item.workspace_id == workspace_id and item.owner_user_id == user_id
        resolution = target_resolver.resolve(request, workspace_id=workspace_id, user_id=user_id)
        if not can_manage:
            return KnowledgeGrantStateResponse(
                can_manage=False, source_workspace_id=item.workspace_id,
                access_via_grant=item.workspace_id != workspace_id,
                target_resolution=resolution.status, target_resolution_message=resolution.message,
                cross_workspace_status="AVAILABLE" if resolution.status == "resolved" else "UNVERIFIED",
            )
        return KnowledgeGrantStateResponse(
            can_manage=True,
            source_workspace_id=item.workspace_id,
            target_resolution=resolution.status, target_resolution_message=resolution.message,
            cross_workspace_status="AVAILABLE" if resolution.status == "resolved" else "UNVERIFIED",
            targets=[KnowledgeGrantTargetResponse.model_validate(target, from_attributes=True) for target in resolution.targets],
            grants=[KnowledgeGrantResponse.model_validate(grant, from_attributes=True)
                    for grant in repository.list_knowledge_grants(workspace_id, item_id, user_id=user_id)],
        )

    @router.post("/api/knowledge-items/{item_id}/grants", response_model=KnowledgeGrantResponse)
    def grant(item_id: str, request: Request, payload: KnowledgeGrantRequest) -> KnowledgeGrantResponse:
        workspace_id, user_id = actor(request)
        try:
            repository.list_knowledge_grants(workspace_id, item_id, user_id=user_id)
            resolution = target_resolver.resolve(request, workspace_id=workspace_id, user_id=user_id)
            if payload.target_workspace_id == workspace_id:
                raise HTTPException(status_code=409, detail={
                    "code": "same_workspace", "cross_workspace_status": "UNVERIFIED",
                    "message": "仅同空间，无需跨空间授权；原有私有与共享范围保持不变。",
                })
            if resolution.status != "resolved":
                raise HTTPException(status_code=503, detail={
                    "code": "grant_target_unresolved", "target_resolution": resolution.status,
                    "cross_workspace_status": "UNVERIFIED", "message": resolution.message,
                })
            # The requested space is not one this actor's server-side resolution
            # verified. Refuse loudly instead of silently doing nothing: a client
            # naming a different space must never look like a stored grant.
            if payload.target_workspace_id not in {target.workspace_id for target in resolution.targets}:
                raise HTTPException(status_code=503, detail={
                    "code": "grant_target_not_verified",
                    "target_resolution": resolution.status,
                    "cross_workspace_status": "UNVERIFIED",
                    "message": "请求的目标空间不在服务端核验通过的目标集合内，未授予任何访问。",
                })
            saved = grant_to_verified_target(
                repository, source_workspace_id=workspace_id, item_id=item_id,
                target_workspace_id=payload.target_workspace_id,
                user_id=user_id, targets=list(resolution.targets),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="knowledge item or grant target not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return KnowledgeGrantResponse.model_validate(saved, from_attributes=True)

    @router.delete("/api/knowledge-items/{item_id}/grants/{grant_id}", response_model=KnowledgeGrantResponse)
    def revoke(item_id: str, grant_id: str, request: Request) -> KnowledgeGrantResponse:
        workspace_id, user_id = actor(request)
        try:
            saved = repository.revoke_knowledge_grant(workspace_id, item_id, grant_id, user_id=user_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="knowledge grant not found") from exc
        return KnowledgeGrantResponse.model_validate(saved, from_attributes=True)

    return router
