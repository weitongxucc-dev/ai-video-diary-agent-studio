"""Douyin creator-page candidate adapter; selectors remain unverified."""

from __future__ import annotations

import re

from app.platform_browser.base import BaseBrowserAdapter


class DouyinAdapter(BaseBrowserAdapter):
    platform = "douyin"
    # Candidate selectors from the existing route review; not live-verified.
    publish_url = "https://creator.douyin.com/creator-micro/content/upload"
    upload_selector = "input[type=file]"
    title_selector = "[data-e2e='title']"
    desc_selector = "[data-e2e='desc']"
    topics_selector = "[data-e2e='topics']"
    login_evidence_selector = "[data-e2e='creator-avatar']"
    # Candidate stable identifier selector; intentionally not a display name.
    account_identity_selector = "[data-e2e='creator-uid']"
    submit_selector = "[data-e2e='publish-submit']"
    publish_success_selector = "[data-e2e='publish-success']"
    published_id_selector = "[data-e2e='publish-result-id']"
    publish_success_text = "发布成功"

    def is_success_receipt(self, success_text: str, platform_id: str) -> bool:
        # Conservative candidate parser: text from an error/placeholder node is
        # not a publish receipt. Real creator-page selectors remain unverified.
        return (success_text == self.publish_success_text
                and re.fullmatch(r"[0-9]{10,32}", platform_id) is not None)
