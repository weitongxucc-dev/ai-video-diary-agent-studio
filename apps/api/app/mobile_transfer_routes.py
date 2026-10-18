"""HTTP transport only; formal identity and material owners are injected."""
from collections.abc import Callable
import sqlite3
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr
from starlette.requests import ClientDisconnect

from app.mobile_transfers import MobileTransferActor, MobileTransferError, MobileTransfers


class MobileTransferDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: StrictStr
    mime_type: StrictStr
    byte_length: StrictInt
    sha256: StrictStr
    description: StrictStr = ""


class MobileTransferFailure(BaseModel):
    code: str
    message: str
    retryable: bool


class MobileTransferReceipt(MobileTransferDeclaration):
    request_id: UUID
    workspace_id: str
    member_id: str
    state: Literal["declared", "receiving", "received", "completed", "cancelled", "failed"]
    received_bytes: int
    restart_from_zero: bool
    asset_id: str | None
    asset_ref: str | None
    error: MobileTransferFailure | None


def build_mobile_transfer_router(
    *, transfers_for: Callable[[], MobileTransfers],
    actor_for: Callable[[Request], MobileTransferActor],
) -> APIRouter:
    """actor_for must freshly validate account/device/member/workspace each time."""
    router = APIRouter()

    def invoke(request, action):
        actor = actor_for(request)
        try:
            return action(transfers_for(), actor)
        except MobileTransferError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.payload()) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail={"code": "storage_unavailable",
                "message": "原素材盘操作未完成，请恢复后查询原请求。", "retryable": True}) from exc
        except sqlite3.OperationalError as exc:
            raise HTTPException(status_code=503, detail={"code": "persistence_unavailable",
                "message": "接收记录暂不可用，请恢复后查询原请求。", "retryable": True}) from exc

    @router.put("/api/mobile-transfers/{request_id}", response_model=MobileTransferReceipt)
    def declare(request_id: UUID, request: Request, payload: MobileTransferDeclaration):
        return invoke(request, lambda service, actor: service.declare(actor, request_id, **payload.model_dump()))

    @router.get("/api/mobile-transfers/{request_id}", response_model=MobileTransferReceipt)
    def query(request_id: UUID, request: Request):
        return invoke(request, lambda service, actor: service.query(actor, request_id))

    @router.put("/api/mobile-transfers/{request_id}/content", response_model=MobileTransferReceipt)
    async def receive(request_id: UUID, request: Request):
        actor = actor_for(request)
        length = request.headers.get("content-length")
        if length is not None and (not length.isascii() or not length.isdigit()):
            raise HTTPException(status_code=422, detail={"code": "invalid_length",
                "message": "Content-Length 必须为完整文件长度。", "retryable": False})
        try:
            return await transfers_for().receive_content(actor, request_id, request.stream(),
                content_length=int(length) if length is not None else None)
        except MobileTransferError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.payload()) from exc
        except ClientDisconnect as exc:
            raise HTTPException(status_code=400, detail={"code": "transfer_interrupted",
                "message": "连接中断，请查询原请求并从零重发。", "retryable": True}) from exc
        except sqlite3.OperationalError as exc:
            raise HTTPException(status_code=503, detail={"code": "persistence_unavailable",
                "message": "接收记录暂不可用，请恢复后查询原请求。", "retryable": True}) from exc

    @router.post("/api/mobile-transfers/{request_id}/complete", response_model=MobileTransferReceipt)
    def complete(request_id: UUID, request: Request):
        return invoke(request, lambda service, actor: service.complete(actor, request_id))

    @router.post("/api/mobile-transfers/{request_id}/cancel", response_model=MobileTransferReceipt)
    def cancel(request_id: UUID, request: Request):
        return invoke(request, lambda service, actor: service.cancel(actor, request_id))

    return router
