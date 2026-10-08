"""Douyin creator-page candidate adapter; selectors remain unverified."""

from __future__ import annotations

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
    account_identity_selector = "[data-e2e='creator-name']"
    submit_selector = "[data-e2e='publish-submit']"
    published_id_selector = "[data-e2e='publish-result-id']"
