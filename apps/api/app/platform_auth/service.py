"""Browser-session account binding; platform secrets remain in Chrome profiles."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.platform_auth.capabilities import capability_for
from app.platform_auth.storage import PlatformAccount, _account_from_row, _open
from app.platform_browser import ChannelsAdapter, DouyinAdapter, XiaohongshuAdapter
from app.platform_browser.playwright_driver import PlaywrightSessionManager, profile_key


ADAPTERS = {
    "douyin": DouyinAdapter,
    "xiaohongshu": XiaohongshuAdapter,
    "wechat": ChannelsAdapter,
}


class PlatformAuthService:
    """Open per-user Chrome sessions and persist only observed account metadata."""

    def __init__(self, database_path: str | Path, profile_root: str | Path,
                 *, session_manager: PlaywrightSessionManager | None = None) -> None:
        self._database_path = Path(database_path).expanduser()
        self.sessions = session_manager or PlaywrightSessionManager(profile_root)

    def capability(self, platform: str):
        return capability_for(platform)

    def connect(self, workspace_id: str, user_id: str, platform: str) -> dict[str, str]:
        if not workspace_id or not user_id:
            raise ValueError("缺少工作区或用户身份")
        if platform != "douyin":
            raise ValueError("该平台的浏览器发布接入尚未完成")
        adapter_type = ADAPTERS[platform]
        adapter = adapter_type()
        self.sessions.open(workspace_id, user_id, platform, adapter.publish_url)
        return {"platform": platform, "state": "waiting_for_user_login",
                "detail": "已打开此成员专用浏览器窗口；登录后返回验证账号。"}

    def verify_current(self, workspace_id: str, user_id: str, platform: str) -> dict[str, object]:
        if platform != "douyin":
            raise ValueError("该平台的浏览器发布接入尚未完成")
        adapter = ADAPTERS[platform]()
        self.sessions.open(workspace_id, user_id, platform, adapter.publish_url)
        state = self.sessions.invoke(workspace_id, user_id, platform, lambda driver: adapter_type_login(adapter, driver))
        if not state.logged_in or not state.account_ref:
            return {"platform": platform, "connected": False, "detail": state.detail}
        account = PlatformAccount(
            workspace_id=workspace_id, user_id=user_id, platform=platform,
            account_ref=state.account_ref,
            profile_key=profile_key(workspace_id, user_id, platform),
            verified_at=datetime.now(UTC).isoformat(),
        )
        with _open(self._database_path) as conn:
            conn.execute(
                """INSERT INTO platform_browser_accounts
                   (workspace_id,user_id,platform,account_ref,profile_key,verified_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(workspace_id,user_id,platform) DO UPDATE SET
                   account_ref=excluded.account_ref, profile_key=excluded.profile_key,
                   verified_at=excluded.verified_at""",
                (account.workspace_id, account.user_id, account.platform, account.account_ref,
                 account.profile_key, account.verified_at),
            )
        return {"platform": platform, "connected": True, "account_ref": account.account_ref,
                "verified_at": account.verified_at}

    def list_accounts(self, workspace_id: str, user_id: str) -> list[PlatformAccount]:
        with _open(self._database_path) as conn:
            rows = conn.execute(
                "SELECT * FROM platform_browser_accounts WHERE workspace_id=? AND user_id=? ORDER BY platform",
                (workspace_id, user_id),
            ).fetchall()
        return [_account_from_row(row) for row in rows]

    def get_account(self, workspace_id: str, user_id: str, platform: str) -> PlatformAccount | None:
        with _open(self._database_path) as conn:
            row = conn.execute(
                "SELECT * FROM platform_browser_accounts WHERE workspace_id=? AND user_id=? AND platform=?",
                (workspace_id, user_id, platform),
            ).fetchone()
        return _account_from_row(row) if row is not None else None


def adapter_type_login(adapter, driver):
    adapter._driver = driver
    return adapter.login()
