"""抖音适配器：平台差异集中于此。真实 URL/选择器「未验证」。"""

from __future__ import annotations

from app.platform_browser.base import BaseBrowserAdapter


class DouyinAdapter(BaseBrowserAdapter):
    platform = "douyin"
    # 未验证：抖音创作者服务平台网页版（creator.douyin.com）上传/文案/草稿控件选择器。
    publish_url = "https://creator.douyin.com/creator-micro/content/upload"  # 未验证
    upload_selector = "input[type=file]"  # 未验证
    title_selector = "[data-e2e='title']"  # 未验证
    desc_selector = "[data-e2e='desc']"  # 未验证
    topics_selector = "[data-e2e='topics']"  # 未验证
    draft_button_selector = "[data-e2e='save-draft']"  # 未验证
