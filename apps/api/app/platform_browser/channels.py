"""微信视频号适配器。第 0 步查证：视频号无发布/草稿开放 API，网页自动化是唯一可能路径。"""

from __future__ import annotations

from app.platform_browser.base import BaseBrowserAdapter


class ChannelsAdapter(BaseBrowserAdapter):
    platform = "wechat"
    # 未验证：微信视频号助手网页版（channels.weixin.qq.com）URL 与控件选择器。
    publish_url = "https://channels.weixin.qq.com/platform"  # 未验证
    upload_selector = "input[type=file]"  # 未验证
    title_selector = "[data-e2e='title']"  # 未验证
    desc_selector = "[data-e2e='desc']"  # 未验证
    topics_selector = "[data-e2e='topics']"  # 未验证
    draft_button_selector = "[data-e2e='save-draft']"  # 未验证
