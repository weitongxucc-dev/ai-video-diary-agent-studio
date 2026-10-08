"""Browser-account metadata tests; no OAuth mock or token persistence."""

from __future__ import annotations

import asyncio
import sqlite3

from app.platform_auth import CAPABILITIES, PlatformAuthService, capability_for
from app.platform_browser.playwright_driver import profile_key


class _Driver:
    async def goto(self, url): pass
    async def upload(self, selector, path): pass
    async def fill(self, selector, value): pass
    async def click(self, selector): pass
    async def read_text(self, selector):
        return {"[data-e2e='creator-avatar']": "avatar",
                "[data-e2e='creator-uid']": "creator-001"}.get(selector, "")


class _Sessions:
    def __init__(self):
        self.driver = _Driver()
        self.opened = []

    def open(self, workspace, user, platform, url):
        self.opened.append((workspace, user, platform, url))
        return self.driver

    def invoke(self, workspace, user, platform, action, *, timeout=30):
        assert timeout == 30
        return asyncio.run(action(self.driver))


def test_capability_table_never_claims_live_platform_verification():
    assert CAPABILITIES["douyin"].browser_connect
    assert CAPABILITIES["douyin"].browser_publish
    assert CAPABILITIES["douyin"].verified is False
    assert CAPABILITIES["douyin"].status == "candidate_unverified"
    assert not CAPABILITIES["wechat"].browser_publish
    assert not CAPABILITIES["xiaohongshu"].browser_publish
    assert capability_for("unknown") is None


def test_profiles_are_stable_and_partitioned_by_workspace_member_platform():
    base = profile_key("ws-1", "member-A", "douyin")
    assert base == profile_key("ws-1", "member-A", "douyin")
    assert len({base, profile_key("ws-2", "member-A", "douyin"),
                profile_key("ws-1", "member-B", "douyin"),
                profile_key("ws-1", "member-A", "wechat")}) == 4


def test_connection_opens_scoped_chrome_and_persists_only_observed_identity(tmp_path):
    sessions = _Sessions()
    service = PlatformAuthService(tmp_path / "main.sqlite3", tmp_path / "profiles",
                                  session_manager=sessions)
    pending = service.connect("ws-1", "member-A", "douyin")
    assert pending["state"] == "waiting_for_user_login"
    assert sessions.opened[0][:3] == ("ws-1", "member-A", "douyin")

    observed = service.verify_current("ws-1", "member-A", "douyin")
    assert observed["connected"] is True
    assert observed["account_ref"] == "creator-001"
    accounts = service.list_accounts("ws-1", "member-A")
    assert len(accounts) == 1 and accounts[0].account_ref == "creator-001"
    assert service.list_accounts("ws-1", "member-B") == []

    with sqlite3.connect(tmp_path / "main.sqlite3") as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(platform_browser_accounts)")}
    assert columns == {"workspace_id", "user_id", "platform", "account_ref", "profile_key", "verified_at"}
    assert not {"access_token", "refresh_token"} & columns


def test_unimplemented_platforms_are_not_given_fake_authorization(tmp_path):
    service = PlatformAuthService(tmp_path / "main.sqlite3", tmp_path / "profiles",
                                  session_manager=_Sessions())
    try:
        service.connect("ws-1", "member-A", "wechat")
    except ValueError as exc:
        assert "尚未完成" in str(exc)
    else:
        raise AssertionError("未接通平台不得打开假授权流程")
