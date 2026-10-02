"""授权服务：把能力表、持久化与 OAuth 流程串起来。

只授权、不发内容。`get_valid_tokens` 负责「二次进入不重登」与「过期自动刷新、
刷新失败明确要求重新授权」。多用户隔离以 (workspace_id, user_id) 为主键维度。
"""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from pathlib import Path

from app.platform_auth.capabilities import PlatformCapability, capability_for
from app.platform_auth.draft import DraftPayload, DraftReceipt, MockDraftProvider
from app.platform_auth.oauth import MockOAuthProvider, OAuthTokens
from app.platform_auth.storage import (
    PlatformAccount,
    _account_from_row,
    _open,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class NeedsReauth:
    """刷新失败/令牌被撤销时的明确信号（不是静默失败）。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __repr__(self) -> str:
        return f"NeedsReauth(reason={self.reason!r})"


class PlatformAuthService:
    def __init__(
        self,
        database_path: str | Path,
        *,
        provider_factory=MockOAuthProvider,
        draft_provider_factory=MockDraftProvider,
        clock=time.time,
    ) -> None:
        self._database_path = Path(database_path).expanduser()
        self._provider_factory = provider_factory
        self._draft_provider_factory = draft_provider_factory
        self._clock = clock
        # 授权中转 state：一次性、带过期，进程内（同 local_members 邀请码的本地内存态）
        self._pending: dict[str, tuple[str, str, str, float]] = {}

    def capability(self, platform: str) -> PlatformCapability | None:
        return capability_for(platform)

    def begin_authorization(
        self, workspace_id: str, user_id: str, platform: str
    ) -> dict[str, str] | NeedsReauth:
        if not workspace_id or not user_id:
            raise ValueError("缺少工作区或用户身份")
        capability = self.capability(platform)
        if capability is None:
            raise ValueError("未知平台")
        if not capability.scan_oauth:
            # 平台无扫码授权能力：不伪造流程，明确上报为"需要（但平台不支持）"
            return NeedsReauth(capability.requirement)
        state = secrets.token_urlsafe(16)
        self._pending[state] = (workspace_id, user_id, platform, self._clock() + 600)
        return {
            "state": state,
            "authorize_url": self._provider_factory(platform).authorize_url(state),
        }

    def complete_authorization(
        self, workspace_id: str, user_id: str, platform: str, code: str, state: str
    ) -> PlatformAccount:
        pending = self._pending.pop(state, None)
        if pending is None:
            raise ValueError("授权状态无效或已过期")
        p_ws, p_user, p_platform, expires = pending
        if (p_ws, p_user, p_platform) != (workspace_id, user_id, platform):
            raise ValueError("授权状态与请求身份不一致")
        if self._clock() > expires:
            raise ValueError("授权状态已过期")
        tokens = self._provider_factory(platform).exchange(code, state)
        now = _utc_now_iso()
        expires_at = datetime.fromtimestamp(self._clock() + tokens.expires_in, UTC).isoformat()
        refresh_expires_at = (
            datetime.fromtimestamp(
                self._clock() + tokens.refresh_expires_in, UTC
            ).isoformat()
            if tokens.refresh_expires_in is not None
            else None
        )
        with _open(self._database_path) as conn:
            conn.execute(
                """INSERT INTO platform_auth_accounts
                   (workspace_id, user_id, platform, account_ref, access_token, refresh_token,
                    expires_at, refresh_expires_at, bound_at, updated_at, revoked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                   ON CONFLICT(workspace_id, user_id, platform) DO UPDATE SET
                     account_ref=excluded.account_ref, access_token=excluded.access_token,
                     refresh_token=excluded.refresh_token, expires_at=excluded.expires_at,
                     refresh_expires_at=excluded.refresh_expires_at,
                     updated_at=excluded.updated_at, revoked_at=NULL""",
                (
                    workspace_id, user_id, platform, tokens.account_ref,
                    tokens.access_token, tokens.refresh_token,
                    expires_at, refresh_expires_at, now, now,
                ),
            )
        return self._get(workspace_id, user_id, platform)

    def list_accounts(self, workspace_id: str, user_id: str) -> list[PlatformAccount]:
        with _open(self._database_path) as conn:
            rows = conn.execute(
                """SELECT * FROM platform_auth_accounts
                   WHERE workspace_id = ? AND user_id = ? ORDER BY bound_at ASC""",
                (workspace_id, user_id),
            ).fetchall()
        return [_account_from_row(row) for row in rows]

    def _get(self, workspace_id: str, user_id: str, platform: str) -> PlatformAccount:
        with _open(self._database_path) as conn:
            row = conn.execute(
                """SELECT * FROM platform_auth_accounts
                   WHERE workspace_id = ? AND user_id = ? AND platform = ?""",
                (workspace_id, user_id, platform),
            ).fetchone()
        if row is None:
            raise KeyError(platform)
        return _account_from_row(row)

    def get_valid_tokens(
        self, workspace_id: str, user_id: str, platform: str
    ) -> OAuthTokens | NeedsReauth:
        try:
            account = self._get(workspace_id, user_id, platform)
        except KeyError:
            return NeedsReauth("尚未授权")
        if account.is_revoked():
            return NeedsReauth("授权已失效，需要重新授权")
        expires_ts = datetime.fromisoformat(account.expires_at).timestamp()
        if self._clock() < expires_ts:
            return OAuthTokens(
                access_token=account.access_token,
                refresh_token=account.refresh_token,
                expires_in=int(expires_ts - self._clock()),
                refresh_expires_in=None,
                account_ref=account.account_ref,
            )
        # access 过期 → 用 refresh 刷新
        refreshed = self._provider_factory(platform).refresh(account.refresh_token)
        if refreshed is None:
            self._mark_revoked(workspace_id, user_id, platform)
            return NeedsReauth("刷新失败，token 已失效，需要重新授权")
        now = _utc_now_iso()
        expires_at = datetime.fromtimestamp(self._clock() + refreshed.expires_in, UTC).isoformat()
        with _open(self._database_path) as conn:
            conn.execute(
                """UPDATE platform_auth_accounts
                   SET access_token = ?, expires_at = ?, updated_at = ?
                   WHERE workspace_id = ? AND user_id = ? AND platform = ?""",
                (refreshed.access_token, expires_at, now, workspace_id, user_id, platform),
            )
        return refreshed

    def _mark_revoked(self, workspace_id: str, user_id: str, platform: str) -> None:
        with _open(self._database_path) as conn:
            conn.execute(
                """UPDATE platform_auth_accounts SET revoked_at = ?
                   WHERE workspace_id = ? AND user_id = ? AND platform = ?""",
                (_utc_now_iso(), workspace_id, user_id, platform),
            )

    def revoke(self, workspace_id: str, user_id: str, platform: str) -> None:
        self._mark_revoked(workspace_id, user_id, platform)

    # ── 草稿箱（只到草稿为止，禁止真实发布）─────────────────────────────

    def save_draft(
        self,
        workspace_id: str,
        user_id: str,
        platform: str,
        draft: DraftPayload,
        video_ref: str,
    ) -> DraftReceipt | NeedsReauth:
        """校验文案三件套 → 取有效 token → 写入草稿箱（当前为 mock）。"""
        capability = self.capability(platform)
        if capability is None:
            raise ValueError("未知平台")
        if capability.draft_box == "none":
            # 该平台无草稿箱能力（如微信视频号）：明确上报，不伪造写入
            return NeedsReauth(capability.requirement)
        payload = draft.validated()
        tokens = self.get_valid_tokens(workspace_id, user_id, platform)
        if isinstance(tokens, NeedsReauth):
            return tokens
        receipt = self._draft_provider_factory(platform).save_draft(
            payload, video_ref, tokens.access_token
        )
        self._persist_draft(workspace_id, user_id, platform, payload, video_ref, receipt)
        return receipt

    def list_drafts(
        self, workspace_id: str, user_id: str
    ) -> list[dict[str, str]]:
        with _open(self._database_path) as conn:
            rows = conn.execute(
                """SELECT platform, draft_id, title, description, hashtags, video_ref,
                          mocked, created_at FROM platform_auth_drafts
                   WHERE workspace_id = ? AND user_id = ? ORDER BY created_at DESC""",
                (workspace_id, user_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def _persist_draft(
        self,
        workspace_id: str,
        user_id: str,
        platform: str,
        payload: DraftPayload,
        video_ref: str,
        receipt: DraftReceipt,
    ) -> None:
        with _open(self._database_path) as conn:
            conn.execute(
                """INSERT INTO platform_auth_drafts
                   (workspace_id, user_id, platform, draft_id, title, description,
                    hashtags, video_ref, mocked, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    workspace_id, user_id, platform, receipt.draft_id,
                    payload.title, payload.description, "\n".join(payload.hashtags),
                    video_ref, int(receipt.mocked), _utc_now_iso(),
                ),
            )
