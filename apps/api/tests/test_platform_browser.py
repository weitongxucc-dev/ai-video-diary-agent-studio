"""platform_browser 最小测试：发布桩必抛错、错误分类、适配器结构、mock driver 走通存草稿。

不真跑浏览器、不用真实账号（红线）。真实 URL/选择器未验证，逻辑用 mock driver 验证。
"""

from __future__ import annotations

import pytest

from app.platform_browser import (
    ChannelsAdapter,
    DouyinAdapter,
    PublishNotAvailable,
    XiaohongshuAdapter,
    needs_human_intervention,
)


class _MockDriver:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._draft_text = "draft-box-id-1"

    async def goto(self, url: str) -> None:
        self.calls.append(f"goto:{url}")

    async def upload(self, selector: str, path: str) -> None:
        self.calls.append(f"upload:{selector}:{path}")

    async def fill(self, selector: str, value: str) -> None:
        self.calls.append(f"fill:{selector}:{value}")

    async def click(self, selector: str) -> None:
        self.calls.append(f"click:{selector}")

    async def read_text(self, selector: str) -> str:
        self.calls.append(f"read:{selector}")
        return self._draft_text


ALL_ADAPTERS = (DouyinAdapter, XiaohongshuAdapter, ChannelsAdapter)


@pytest.mark.parametrize("cls", ALL_ADAPTERS)
def test_publish_is_a_stub_that_raises(cls):
    adapter = cls()
    with pytest.raises(PublishNotAvailable) as exc:
        import asyncio
        asyncio.run(adapter.publish(None))
    assert "发布功能未开放" in str(exc.value)
    assert "需用户授权" in str(exc.value)


def test_needs_human_intervention_classification():
    assert needs_human_intervention("captcha") is True
    assert needs_human_intervention("risk_control") is True
    assert needs_human_intervention("rate_limit") is True
    assert needs_human_intervention("network") is False


def test_save_draft_without_driver_refuses_honestly():
    adapter = DouyinAdapter()  # 无 driver → 不冒充成功
    import asyncio
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(adapter.save_draft())
    assert "浏览器" in str(exc.value)


def test_save_draft_flow_with_mock_driver_is_mocked():
    driver = _MockDriver()
    adapter = DouyinAdapter(driver)
    import asyncio

    async def run():
        await adapter.login()
        await adapter.upload_video("/tmp/video.mp4")
        await adapter.fill_copywriting("标题", "描述", ("话题1", "话题2"))
        return await adapter.save_draft()

    receipt = asyncio.run(run())
    assert receipt.mocked is True  # 明确 mock，不冒充「已打通平台」
    assert receipt.platform == "douyin"
    # 公共流程按序执行：登录→上传→填文案→存草稿
    assert driver.calls[0].startswith("goto:")
    assert any(c.startswith("upload:") for c in driver.calls)
    assert any(c.startswith("fill:") for c in driver.calls)
    assert any(c.startswith("click:") for c in driver.calls)


def test_each_adapter_declares_platform():
    assert DouyinAdapter.platform == "douyin"
    assert XiaohongshuAdapter.platform == "xiaohongshu"
    assert ChannelsAdapter.platform == "wechat"
