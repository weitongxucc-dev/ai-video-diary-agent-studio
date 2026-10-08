"""Browser-backed adapters and shared publishing contracts."""

from app.platform_browser.base import (
    BaseBrowserAdapter,
    BrowserUnavailable,
    HumanInterventionRequired,
    LoginRequired,
    LoginState,
    PlatformAdapter,
    PublishConfirm,
    PublishOutcomeUnknown,
    PublishRef,
    UploadHandle,
    needs_human_intervention,
)
from app.platform_browser.channels import ChannelsAdapter
from app.platform_browser.douyin import DouyinAdapter
from app.platform_browser.xiaohongshu import XiaohongshuAdapter
from app.platform_browser.playwright_driver import PlaywrightSessionManager, profile_key

__all__ = [
    "BaseBrowserAdapter",
    "BrowserUnavailable",
    "HumanInterventionRequired",
    "LoginRequired",
    "LoginState",
    "PlatformAdapter",
    "PublishConfirm",
    "PublishOutcomeUnknown",
    "PublishRef",
    "UploadHandle",
    "needs_human_intervention",
    "DouyinAdapter",
    "XiaohongshuAdapter",
    "ChannelsAdapter",
    "PlaywrightSessionManager",
    "profile_key",
]
