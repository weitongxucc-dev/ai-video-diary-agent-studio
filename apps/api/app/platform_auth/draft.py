"""文案三件套 + 草稿箱写入（只到草稿为止，禁止触发真实发布）。

红线：只写 `draft`/`save_draft`/草稿箱，绝不调用 publish/commit/submit。
真实平台草稿接口当前对三平台均未确认（见 capabilities.draft_box），
故这里用 `MockDraftProvider` 走通流程并明确标注 mock，**不冒充「已打通平台」**。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

TITLE_MAX = 30
DESCRIPTION_MAX = 1000
HASHTAG_MAX_COUNT = 20
HASHTAG_MAX_LEN = 16


@dataclass(frozen=True)
class DraftPayload:
    title: str
    description: str
    hashtags: tuple[str, ...] = ()

    def validated(self) -> "DraftPayload":
        title = (self.title or "").strip()
        description = (self.description or "").strip()
        if not title:
            raise ValueError("标题不能为空")
        if len(title) > TITLE_MAX:
            raise ValueError(f"标题超过 {TITLE_MAX} 字")
        if len(description) > DESCRIPTION_MAX:
            raise ValueError(f"描述超过 {DESCRIPTION_MAX} 字")
        hashtags: list[str] = []
        for tag in self.hashtags:
            t = (tag or "").strip().lstrip("#")
            if not t:
                continue
            if len(t) > HASHTAG_MAX_LEN:
                raise ValueError(f"话题标签超过 {HASHTAG_MAX_LEN} 字")
            hashtags.append(t)
        if len(hashtags) > HASHTAG_MAX_COUNT:
            raise ValueError(f"话题标签超过 {HASHTAG_MAX_COUNT} 个")
        return DraftPayload(title=title, description=description, hashtags=tuple(hashtags))


@dataclass(frozen=True)
class DraftReceipt:
    platform: str
    draft_id: str
    mocked: bool


class MockDraftProvider:
    """确定性 mock：不联网，返回 mock draft_id，明确 `mocked=True`。"""

    def __init__(self, platform: str) -> None:
        self.platform = platform

    def save_draft(
        self, draft: DraftPayload, video_ref: str, access_token: str
    ) -> DraftReceipt:
        if access_token.startswith("revoked_"):
            raise ValueError("令牌失效，无法写入草稿")
        return DraftReceipt(
            platform=self.platform,
            draft_id=f"draft_{self.platform}_{secrets.token_hex(6)}",
            mocked=True,
        )
