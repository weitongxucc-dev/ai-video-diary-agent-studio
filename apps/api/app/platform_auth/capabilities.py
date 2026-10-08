"""Honest publishing coverage; browser selectors remain unverified until GUI review."""

from __future__ import annotations

from dataclasses import dataclass


PLATFORMS = ("douyin", "wechat", "xiaohongshu")


@dataclass(frozen=True)
class PlatformCapability:
    platform: str
    label: str
    browser_connect: bool
    browser_publish: bool
    verified: bool
    status: str
    requirement: str


CAPABILITIES: dict[str, PlatformCapability] = {
    "douyin": PlatformCapability(
        "douyin", "抖音", True, True, False, "candidate_unverified",
        "使用此成员专用 Chrome 配置；创作者页面路由、账号证据、控件和发布回执仍待真实独立验收。",
    ),
    "wechat": PlatformCapability(
        "wechat", "微信视频号", False, False, False, "not_connected",
        "尚未实现可验证的浏览器发布路径。",
    ),
    "xiaohongshu": PlatformCapability(
        "xiaohongshu", "小红书", False, False, False, "not_connected",
        "尚未实现可验证的浏览器发布路径。",
    ),
}


def capability_for(platform: str) -> PlatformCapability | None:
    return CAPABILITIES.get(platform)
