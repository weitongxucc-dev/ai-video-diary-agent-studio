"""Pure content-use and same-workspace knowledge-read rules."""

from app.database_migrations import (
    CONTENT_VISIBILITY_DEFAULT as CONTENT_VISIBILITY_PRIVATE,
    CONTENT_VISIBILITY_LEGACY as CONTENT_VISIBILITY_SHARED,
)
from app.source_types import CONTENT_USE_STATES, ContentUsePurpose


def content_allows_use(state: str, purpose: ContentUsePurpose) -> bool:
    """Return whether a saved item may serve this consumer purpose."""
    if state not in CONTENT_USE_STATES:
        raise ValueError(f"unknown content use state: {state}")
    if purpose not in {"read", "reference", "understanding", "automatic_selection", "media", "preview", "publish"}:
        raise ValueError(f"unknown content use purpose: {purpose}")
    if state == "excluded":
        return False
    if state == "reference_only" and purpose in {"automatic_selection", "media", "publish"}:
        return False
    if purpose == "automatic_selection" and state == "not_for_automation":
        return False
    if purpose == "publish" and state == "not_for_publish":
        return False
    return True


def knowledge_item_readable(
    *,
    user_id: str | None,
    local_diagnostic: bool,
    visibility: str,
    owner_user_id: str | None,
) -> bool:
    """判断一条知识能否被当前调用方读到。

    ``user_id`` 是**已经解析出的可信调用方标识**，来源有两种既有身份，都归一到
    这一个字符串参与判定，不新造第二套身份框架：
    - 已验证的 Bearer 身份（AuthPrincipal.user_id，来自 Supabase Auth）；
    - 本地成员令牌（local_members_service.resolve_member 返回的 member_id）。
    两者都不是客户端自报。local 模式的多名成员同处一个 workspace，正是靠这个
    id 区分各自的私有上传。

    ``local_diagnostic`` 只在保留的 local workspace、未开认证的受控诊断下为真：
    这时本机自己的私有内容照常可读，产品在单机上仍然可用——但这不是多人隔离，
    认证态或已带成员令牌的请求缺可信身份时不得借此降级到 local。

    共享内容对本工作区成员可读；私有内容只对上传者本人可读。可见范围未知时按
    更保守的一侧处理，不当作共享。
    """
    if local_diagnostic:
        return True
    # 认证范围内缺可信身份就是没有可用身份：不因为缺身份就只退到共享范围。
    if not user_id:
        return False
    if visibility == CONTENT_VISIBILITY_SHARED:
        return True
    if visibility != CONTENT_VISIBILITY_PRIVATE:
        return False
    if not owner_user_id:
        return False
    return user_id == owner_user_id
