"""Adapter contracts use a fake driver; no live account or platform session."""

from __future__ import annotations

import asyncio

import pytest

from app.platform_browser import (
    BrowserUnavailable, ChannelsAdapter, DouyinAdapter, LoginRequired,
    PlaywrightSessionManager, PublishConfirm, PublishOutcomeUnknown,
    XiaohongshuAdapter, needs_human_intervention, profile_key,
)


class FakeDriver:
    def __init__(self, *, account="creator-001", receipt="12345678901234567890"):
        self.calls = []
        self.account = account
        self.receipt = receipt

    async def goto(self, url):
        self.calls.append(("goto", url))
    async def upload(self, selector, path):
        self.calls.append(("upload", selector, path))
    async def fill(self, selector, value):
        self.calls.append(("fill", selector, value))
    async def click(self, selector):
        self.calls.append(("click", selector))
    async def read_text(self, selector):
        self.calls.append(("read", selector))
        if selector == "[data-e2e='creator-avatar']":
            return "avatar"
        if selector == "[data-e2e='creator-uid']":
            return self.account
        if selector == "[data-e2e='publish-success']":
            return "发布成功"
        if selector == "[data-e2e='publish-result-id']":
            return self.receipt
        return ""


def confirmed(**changes):
    values = dict(account_ref="creator-001", title="已确认标题", description="已确认说明",
                  topics=("记录",), content_digest="a" * 64, confirmed=True)
    values.update(changes)
    return PublishConfirm(**values)


def test_platform_publish_requires_explicit_confirmation_before_any_browser_write():
    driver = FakeDriver()
    adapter = DouyinAdapter(driver)
    with pytest.raises(PermissionError):
        asyncio.run(adapter.publish(confirmed(confirmed=False), "/private/final.mp4"))
    assert driver.calls == []


def test_publish_rechecks_current_account_and_persists_pre_submit_boundary():
    driver = FakeDriver()
    adapter = DouyinAdapter(driver)
    boundaries = []
    result = asyncio.run(adapter.publish(confirmed(), "/private/final.mp4",
                                         lambda: boundaries.append("submission_started")))
    assert result.platform == "douyin" and result.publish_id == "12345678901234567890"
    assert result.account_ref == "creator-001"
    assert boundaries == ["submission_started"]
    labels = [call[0] for call in driver.calls]
    assert labels.index("upload") < labels.index("fill") < labels.index("click")
    assert labels.index("read") < labels.index("upload")


def test_mismatched_account_blocks_upload():
    driver = FakeDriver(account="different-account")
    adapter = DouyinAdapter(driver)
    with pytest.raises(LoginRequired):
        asyncio.run(adapter.publish(confirmed(), "/private/final.mp4"))
    assert not any(call[0] in {"upload", "fill", "click"} for call in driver.calls)


def test_missing_remote_receipt_is_unknown_and_never_success():
    driver = FakeDriver(receipt="")
    adapter = DouyinAdapter(driver)
    with pytest.raises(PublishOutcomeUnknown):
        asyncio.run(adapter.publish(confirmed(), "/private/final.mp4"))


@pytest.mark.parametrize("status,receipt", [
    ("", "12345678901234567890"),
    ("发布失败", "12345678901234567890"),
    ("发布成功", "提交失败，请重试"),
    ("发布成功", "123"),
])
def test_non_success_status_or_non_platform_id_is_unknown(status, receipt):
    driver = FakeDriver(receipt=receipt)
    original = driver.read_text
    async def read_text(selector):
        if selector == "[data-e2e='publish-success']":
            driver.calls.append(("read", selector))
            return status
        return await original(selector)
    driver.read_text = read_text
    with pytest.raises(PublishOutcomeUnknown):
        asyncio.run(DouyinAdapter(driver).publish(confirmed(), "/private/final.mp4"))


def test_no_driver_and_nonintegrated_adapters_do_not_fall_back_to_mock():
    with pytest.raises(BrowserUnavailable):
        asyncio.run(DouyinAdapter().login())
    assert XiaohongshuAdapter.platform == "xiaohongshu"
    assert ChannelsAdapter.platform == "wechat"


def test_human_challenges_are_not_bypassed():
    assert needs_human_intervention("captcha")
    assert needs_human_intervention("risk_control")
    assert needs_human_intervention("rate_limit")
    assert not needs_human_intervention("navigation")


def test_playwright_session_loop_closes_without_launching_a_browser(tmp_path):
    manager = PlaywrightSessionManager(tmp_path)
    key = profile_key("workspace", "member", "douyin")
    driver = object()
    manager._drivers[key] = driver
    assert manager.invoke("workspace", "member", "douyin", lambda current: current) is driver
    thread = manager._thread
    assert thread is not None and thread.is_alive()

    manager.close_all()

    assert not thread.is_alive()
    assert manager._thread is None and manager._loop is None
