"""Adapter contracts use a fake driver; no live account or platform session."""

from __future__ import annotations

import asyncio

import pytest

from app.platform_browser import (
    BrowserUnavailable, ChannelsAdapter, DouyinAdapter, LoginRequired,
    PublishConfirm, PublishOutcomeUnknown, XiaohongshuAdapter,
    needs_human_intervention,
)


class FakeDriver:
    def __init__(self, *, account="creator-001", receipt="item-123"):
        self.calls = []
        self.account = account
        self.receipt = receipt

    async def goto(self, url): self.calls.append(("goto", url))
    async def upload(self, selector, path): self.calls.append(("upload", selector, path))
    async def fill(self, selector, value): self.calls.append(("fill", selector, value))
    async def click(self, selector): self.calls.append(("click", selector))
    async def read_text(self, selector):
        self.calls.append(("read", selector))
        if selector == "[data-e2e='creator-avatar']": return "avatar"
        if selector == "[data-e2e='creator-name']": return self.account
        if selector == "[data-e2e='publish-result-id']": return self.receipt
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
    assert result.platform == "douyin" and result.publish_id == "item-123"
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
