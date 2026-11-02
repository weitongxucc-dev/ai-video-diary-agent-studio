"""Request content identity and the existing independent list visibility rule."""
from fastapi import HTTPException, Request

from app.local_members import LocalMembersError
from app.repository import LEGACY_WORKSPACE_ID
from app.source_policy import knowledge_item_readable as content_readable


class ContentIdentityHttpAdapter:
    def __init__(self, *, local_token_for, resolve_member, auth_enabled):
        self.local_token_for = local_token_for
        self.resolve_member = resolve_member
        self.auth_enabled = auth_enabled

    def include_private(self, request: Request) -> bool:
        """Member visibility for content list routes.

        Anonymous and owner callers see everything; a valid member token sees only
        shared content. An unknown token is a 401 in the content routes' own
        HTTPException style.
        """
        token = self.local_token_for(request)
        if not token:
            return True
        try:
            member = self.resolve_member(token)
        except LocalMembersError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        assert member is not None  # a present, valid token always resolves
        return member.role == "owner"

    def reader(self, request: Request, workspace_id: str) -> tuple[str | None, bool]:
        """解析这次调用的可信内容读取身份：`(user_id, local_diagnostic)`。

        两种既有身份归一成一个 id 参与判据，不新造第二套：
        - 已验证的 Bearer 身份：`request.state.principal.user_id`；
        - 本地成员令牌：`local_members_service.resolve_member` 的 `member_id`。
        令牌无效按 401 处理，不降级成「没带令牌所以按 owner 看全部」。

        `local_diagnostic` 只在保留的 local 工作区、且请求确实没有任何认证身份时为真：
        单机本地正常使用不受影响，但这不是多人隔离——带了无效身份或已开认证的请求
        不得借此看到别人的私有内容。
        """
        principal = getattr(request.state, "principal", None)
        if principal is not None and principal.user_id:
            return str(principal.user_id), False

        token = self.local_token_for(request)
        if token:
            try:
                member = self.resolve_member(token)
            except LocalMembersError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            assert member is not None
            return str(member.member_id), False

        if self.auth_enabled():
            # 已开认证却没有可信身份：没有可读身份，不退回共享或 local。
            return None, False
        # 受控诊断只限保留的 local 工作区：本机单人使用照常可读自己的私有内容。
        # 其他 workspace 没有这个范围——那里的匿名请求没有可信身份，读不到私有内容。
        return None, workspace_id == LEGACY_WORKSPACE_ID

    def assistant_member(self, request: Request, workspace_id: str) -> str:
        member_id, _local_diagnostic = self.reader(request, workspace_id)
        if not member_id:
            raise HTTPException(status_code=403, detail="请先确认当前成员身份，再使用可恢复的对话会话。")
        return member_id

    def visible(self,
        request: Request, workspace_id: str, visibility: str, owner_user_id: str | None
    ) -> bool:
        """内容可见范围判据，与仓储层共用同一规则（app.source_policy.knowledge_item_readable）。"""
        user_id, local_diagnostic = self.reader(request, workspace_id)
        return content_readable(
            user_id=user_id,
            local_diagnostic=local_diagnostic,
            visibility=visibility,
            owner_user_id=owner_user_id,
        )
