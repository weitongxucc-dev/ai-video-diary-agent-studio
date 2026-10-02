"""小红书适配器。第 0 步查证：小红书 OAuth/发布/草稿能力均未查证（文档抓取空）。"""

from __future__ import annotations

from app.platform_browser.base import BaseBrowserAdapter


class XiaohongshuAdapter(BaseBrowserAdapter):
    platform = "xiaohongshu"
    # 未验证：小红书创作者服务平台网页版 URL 与控件选择器。
    publish_url = "https://creator.xiaohongshu.com/publish/publish"  # 未验证
    upload_selector = "input[type=file]"  # 未验证
    title_selector = "[data-e2e='title']"  # 未验证
    desc_selector = "[data-e2e='desc']"  # 未验证
    topics_selector = "[data-e2e='topics']"  # 未验证
    draft_button_selector = "[data-e2e='save-draft']"  # 未验证
