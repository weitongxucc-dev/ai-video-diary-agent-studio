"""platform_auth 最小测试（mock，无网络、无真实账号、无模型）。

覆盖：能力表、绑定+持久化、多用户隔离、二次进入不重登、过期自动刷新、
刷新失败→明确要求重新授权、token 脱敏。
"""

from __future__ import annotations

import pytest

from app.platform_auth import (
    CAPABILITIES,
    DraftPayload,
    MockOAuthProvider,
    NeedsReauth,
    PlatformAuthService,
    capability_for,
)


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _service(tmp_path, now=1_700_000_000.0):
    return PlatformAuthService(
        tmp_path / "agent.sqlite3", clock=_Clock(now)
    )


def _bind(service, workspace, user, platform="douyin"):
    pending = service.begin_authorization(workspace, user, platform)
    return service.complete_authorization(
        workspace, user, platform, code="mock-code", state=pending["state"]
    )


def test_capability_table_is_honest():
    assert CAPABILITIES["douyin"].scan_oauth is True
    assert CAPABILITIES["douyin"].long_term_token is True
    assert CAPABILITIES["douyin"].publish_scope == "video_publish"
    assert CAPABILITIES["wechat"].scan_oauth is True
    assert CAPABILITIES["wechat"].publish_scope == "identity_only"
    assert CAPABILITIES["xiaohongshu"].verified is False
    assert capability_for("nope") is None


def test_bind_persists_and_lists(tmp_path):
    service = _service(tmp_path)
    account = _bind(service, "ws-1", "user-A")
    assert account.platform == "douyin"
    accounts = service.list_accounts("ws-1", "user-A")
    assert len(accounts) == 1
    assert accounts[0].account_ref == account.account_ref


def test_multi_user_isolation(tmp_path):
    service = _service(tmp_path)
    _bind(service, "ws-1", "user-A")
    assert service.list_accounts("ws-1", "user-B") == []
    assert isinstance(service.get_valid_tokens("ws-1", "user-B", "douyin"), NeedsReauth)


def test_reentry_needs_no_relogin(tmp_path):
    service = _service(tmp_path)
    account = _bind(service, "ws-1", "user-A")
    tokens = service.get_valid_tokens("ws-1", "user-A", "douyin")
    assert not isinstance(tokens, NeedsReauth)
    assert tokens.account_ref == account.account_ref


def test_expired_access_auto_refreshes(tmp_path):
    service = _service(tmp_path, now=1_700_000_000.0)
    account = _bind(service, "ws-1", "user-A")
    # 推时钟到 access 过期之后（expires_in=3600s）
    service._clock.now = 1_700_000_000.0 + 7200
    tokens = service.get_valid_tokens("ws-1", "user-A", "douyin")
    assert not isinstance(tokens, NeedsReauth)
    # 刷新后落库的 access_token 已变化（账号还在，token 已换）
    refreshed = service._get("ws-1", "user-A", "douyin")
    assert refreshed.access_token != account.access_token
    assert refreshed.revoked_at is None


class _RefuseRefreshProvider(MockOAuthProvider):
    def refresh(self, refresh_token):
        return None  # 模拟平台撤销 / 刷新失败


def test_refresh_failure_marks_revoked_and_asks_reauth(tmp_path):
    service = PlatformAuthService(
        tmp_path / "agent.sqlite3",
        provider_factory=_RefuseRefreshProvider,
        clock=_Clock(1_700_000_000.0),
    )
    _bind(service, "ws-1", "user-A")
    service._clock.now = 1_700_000_000.0 + 7200
    result = service.get_valid_tokens("ws-1", "user-A", "douyin")
    assert isinstance(result, NeedsReauth)
    assert "重新授权" in result.reason
    account = service._get("ws-1", "user-A", "douyin")
    assert account.revoked_at is not None  # 明确标记失效，不静默失败


def test_token_is_redacted_from_repr(tmp_path):
    service = _service(tmp_path)
    account = _bind(service, "ws-1", "user-A")
    rendered = repr(account)
    assert account.access_token not in rendered
    assert account.refresh_token not in rendered
    assert "***" in rendered


def test_platform_without_scan_oauth_refuses_to_fake_flow(tmp_path):
    service = _service(tmp_path)
    # xiaohongshu 未查证/无扫码能力：不伪造流程，明确上报"该平台无此能力"
    result = service.begin_authorization("ws-1", "user-A", "xiaohongshu")
    assert isinstance(result, NeedsReauth)


# ── 草稿箱（第 0 步第 2 条：程序化写草稿的能力）────────────────────────

def test_draft_box_capability_is_honest():
    assert CAPABILITIES["douyin"].draft_box == "unknown"  # 未查证
    assert CAPABILITIES["wechat"].draft_box == "none"  # 无此能力（已查证）
    assert CAPABILITIES["xiaohongshu"].draft_box == "unknown"


def test_draft_payload_validates_length():
    assert DraftPayload("标题", "描述", ("话题",)).validated().title == "标题"
    with pytest.raises(ValueError):
        DraftPayload("", "描述").validated()  # 标题为空
    with pytest.raises(ValueError):
        DraftPayload("x" * 31, "").validated()  # 标题超 30 字
    with pytest.raises(ValueError):
        DraftPayload("标题", "", tuple(f"#{i}" for i in range(21))).validated()  # 标签超 20 个


def test_save_draft_is_mock_and_persists(tmp_path):
    service = _service(tmp_path)
    _bind(service, "ws-1", "user-A")
    receipt = service.save_draft(
        "ws-1", "user-A", "douyin",
        DraftPayload("一条视频", "这是描述", ("产品", "教程")),
        video_ref="artifact:video:1",
    )
    assert receipt.mocked is True  # 明确是 mock，不冒充打通平台
    drafts = service.list_drafts("ws-1", "user-A")
    assert len(drafts) == 1
    assert drafts[0]["title"] == "一条视频"
    assert drafts[0]["hashtags"] == "产品\n教程"


def test_save_draft_on_platform_with_no_draft_box(tmp_path):
    service = _service(tmp_path)
    _bind(service, "ws-1", "user-A")  # douyin 可绑
    # 微信视频号 draft_box=none：明确上报，不伪造写入
    result = service.save_draft(
        "ws-1", "user-A", "wechat",
        DraftPayload("标题", "描述"),
        video_ref="artifact:video:1",
    )
    assert isinstance(result, NeedsReauth)


def test_save_draft_with_revoked_token_requires_reauth(tmp_path):
    service = _service(tmp_path)
    _bind(service, "ws-1", "user-A")
    # 直接吊销 → save_draft 应要求重新授权，不静默失败
    service.revoke("ws-1", "user-A", "douyin")
    result = service.save_draft(
        "ws-1", "user-A", "douyin",
        DraftPayload("标题", "描述"),
        video_ref="artifact:video:1",
    )
    assert isinstance(result, NeedsReauth)

