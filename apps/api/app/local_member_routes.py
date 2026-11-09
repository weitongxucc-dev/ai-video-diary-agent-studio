"""Local roster and member identity HTTP; policy and storage stay with their owners."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.local_members import LocalMembersError


LOCAL_MEMBERS_ERROR_STATUS: dict[str, int] = {
    "invalid": 400,
    "not_found": 404,
    "conflict": 409,
}


class MemberResponse(BaseModel):
    member_id: str
    name: str
    role: str
    created_at: str


class MemberListResponse(BaseModel):
    members: list[MemberResponse]


class AddMemberRequest(BaseModel):
    name: str
    role: str = "member"


class SetMemberRoleRequest(BaseModel):
    role: str


class JoinMemberRequest(BaseModel):
    code: str
    name: str


class InviteCodeResponse(BaseModel):
    invite_code: str


class MemberWithTokenResponse(MemberResponse):
    """Join/whoami response: the only places a member's bearer token is exposed."""

    member_token: str


def _member_response(member) -> MemberResponse:
    return MemberResponse(
        member_id=member.member_id,
        name=member.name,
        role=member.role,
        created_at=member.created_at,
    )


def _member_with_token_response(member, token: str) -> MemberWithTokenResponse:
    return MemberWithTokenResponse(
        member_id=member.member_id,
        name=member.name,
        role=member.role,
        created_at=member.created_at,
        member_token=token,
    )


def _local_members_error_response(exc: LocalMembersError) -> JSONResponse:
    status = LOCAL_MEMBERS_ERROR_STATUS.get(exc.kind, 500)
    return JSONResponse(
        status_code=status,
        content={"error": exc.kind, "detail": str(exc)},
    )


def build_local_member_router(*, service_for, write_guard, token_for) -> APIRouter:
    router = APIRouter()

    @router.get("/api/members")
    def list_local_members() -> MemberListResponse:
        return MemberListResponse(
            members=[_member_response(m) for m in service_for().list_members()]
        )


    @router.post("/api/members")
    def add_local_member(request: Request, payload: AddMemberRequest):
        denied = write_guard(request)
        if denied is not None:
            return denied
        try:
            member = service_for().add_member(payload.name, payload.role)
        except LocalMembersError as exc:
            return _local_members_error_response(exc)
        return _member_response(member)


    @router.delete("/api/members/{member_id}")
    def remove_local_member(member_id: str, request: Request):
        denied = write_guard(request)
        if denied is not None:
            return denied
        try:
            service_for().remove_member(member_id)
        except LocalMembersError as exc:
            return _local_members_error_response(exc)
        return {"deleted": member_id}


    @router.patch("/api/members/{member_id}")
    def set_local_member_role(member_id: str, request: Request, payload: SetMemberRoleRequest):
        denied = write_guard(request)
        if denied is not None:
            return denied
        try:
            member = service_for().set_role(member_id, payload.role)
        except LocalMembersError as exc:
            return _local_members_error_response(exc)
        return _member_response(member)


    @router.post("/api/members/invite-code")
    def create_local_invite_code(request: Request) -> InviteCodeResponse:
        denied = write_guard(request)
        if denied is not None:
            return denied
        return InviteCodeResponse(invite_code=service_for().create_invite_code())


    @router.post("/api/members/join")
    def join_local_member(payload: JoinMemberRequest):
        try:
            member, token = service_for().join(payload.code, payload.name)
        except LocalMembersError as exc:
            return _local_members_error_response(exc)
        return _member_with_token_response(member, token)


    @router.get("/api/members/whoami")
    def whoami_local_member(request: Request):
        try:
            member, token = service_for().whoami(
                token_for(request) or None
            )
        except LocalMembersError as exc:
            return _local_members_error_response(exc)
        return _member_with_token_response(member, token)


    return router
