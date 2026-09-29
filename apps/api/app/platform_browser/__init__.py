"""platform_browser 导出：适配器 + 基座 + 桩异常。"""

from app.platform_browser.base import (
    BaseBrowserAdapter,
    DraftRef,
    LoginState,
    PlatformAdapter,
    PublishConfirm,
    PublishNotAvailable,
    PublishRef,
    UploadHandle,
    needs_human_intervention,
)
from app.platform_browser.channels import ChannelsAdapter
from app.platform_browser.douyin import DouyinAdapter
from app.platform_browser.xiaohongshu import XiaohongshuAdapter

__all__ = [
    "BaseBrowserAdapter",
    "DraftRef",
    "LoginState",
    "PlatformAdapter",
    "PublishConfirm",
    "PublishNotAvailable",
    "PublishRef",
    "UploadHandle",
    "needs_human_intervention",
    "DouyinAdapter",
    "XiaohongshuAdapter",
    "ChannelsAdapter",
]
