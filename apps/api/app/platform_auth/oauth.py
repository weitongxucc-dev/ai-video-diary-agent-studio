"""OAuth 抽象与 mock provider。

红线：本包不联网、不发真实凭据、不用真实平台账号。真实 token 交换由后续
平台接入包（拿到 AppID/AppSecret 后）替换 `MockOAuthProvider` 即可，接口不变。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str
    expires_in: int  # 秒
    refresh_expires_in: int | None  # 秒；None 表示无长期刷新
    account_ref: str  # openid / 平台账号标识


class MockOAuthProvider:
    """确定性 mock：不联网，authorize_url 假、exchange 假、refresh 可注入失败。

    refresh_token 以 ``revoked_`` 前缀时，refresh() 返回 None，模拟 token 被平台撤销，
    用于验证「刷新失败 → 明确提示重新授权、不静默失败」。
    """

    def __init__(self, platform: str) -> None:
        self.platform = platform

    def authorize_url(self, state: str) -> str:
        return f"mock://{self.platform}/authorize?state={state}"

    def exchange(self, code: str, state: str) -> OAuthTokens:
        if not code or code == "revoked":
            raise ValueError("mock 授权码无效")
        return OAuthTokens(
            access_token=f"at_{secrets.token_hex(12)}",
            refresh_token=f"rt_{secrets.token_hex(12)}",
            expires_in=3600,
            refresh_expires_in=86400 * 30,
            account_ref=f"openid_{self.platform}_{secrets.token_hex(4)}",
        )

    def refresh(self, refresh_token: str) -> OAuthTokens | None:
        if refresh_token.startswith("revoked_"):
            return None  # 平台已撤销 → 刷新失败
        return OAuthTokens(
            access_token=f"at_{secrets.token_hex(12)}",
            refresh_token=refresh_token,
            expires_in=3600,
            refresh_expires_in=86400 * 30,
            account_ref=f"openid_{self.platform}_{secrets.token_hex(4)}",
        )
