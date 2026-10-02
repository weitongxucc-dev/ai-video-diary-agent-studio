"""本地发布账号授权层：扫码授权一次、长期免登录。

只做授权，不发内容。真实平台 token 交换由 WIL-114 及后续平台接入包负责；
本模块交付：平台能力登记、授权流程抽象、token 持久化、刷新与失效处理。
token 永不进入日志/报告（凭据物化禁令），存储层仅落库、repr 一律脱敏。
"""

from app.platform_auth.capabilities import (
    CAPABILITIES,
    PlatformCapability,
    capability_for,
)
from app.platform_auth.draft import DraftPayload, DraftReceipt, MockDraftProvider
from app.platform_auth.oauth import MockOAuthProvider, OAuthTokens
from app.platform_auth.service import (
    NeedsReauth,
    PlatformAccount,
    PlatformAuthService,
)

__all__ = [
    "CAPABILITIES",
    "PlatformCapability",
    "capability_for",
    "DraftPayload",
    "DraftReceipt",
    "MockDraftProvider",
    "MockOAuthProvider",
    "OAuthTokens",
    "NeedsReauth",
    "PlatformAccount",
    "PlatformAuthService",
]
