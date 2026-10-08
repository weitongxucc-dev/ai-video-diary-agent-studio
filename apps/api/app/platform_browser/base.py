"""Browser-driven platform publishing contracts.

The driver is attached to one isolated, persistent Chromium profile per member,
workspace and platform. Platform selectors remain explicitly unverified until
an independently authorized real account acceptance confirms them.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol, runtime_checkable


class BrowserUnavailable(RuntimeError):
    """The configured browser/profile cannot be opened or used."""


class LoginRequired(RuntimeError):
    """No current, observable signed-in account was found."""


class HumanInterventionRequired(RuntimeError):
    """The page requires a user to resolve a challenge or risk check."""


class PublishOutcomeUnknown(RuntimeError):
    """Submission may have reached the platform, but no receipt was observed."""


@dataclass(frozen=True)
class LoginState:
    logged_in: bool
    platform: str
    account_ref: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class UploadHandle:
    platform: str
    file_name: str


@dataclass(frozen=True)
class PublishConfirm:
    """Exact user-confirmed content; callers construct only after confirmation."""

    account_ref: str
    title: str
    description: str
    topics: tuple[str, ...]
    content_digest: str
    confirmed: bool


@dataclass(frozen=True)
class PublishRef:
    platform: str
    publish_id: str
    account_ref: str
    response_text: str


_INTERVENTION_KINDS = {"captcha", "risk_control", "rate_limit"}


def needs_human_intervention(kind: str) -> bool:
    return kind in _INTERVENTION_KINDS


class BrowserDriver(Protocol):
    async def goto(self, url: str) -> None: ...
    async def upload(self, selector: str, path: str) -> None: ...
    async def fill(self, selector: str, value: str) -> None: ...
    async def click(self, selector: str) -> None: ...
    async def read_text(self, selector: str) -> str: ...


@runtime_checkable
class PlatformAdapter(Protocol):
    platform: str

    async def login(self) -> LoginState: ...
    async def publish(self, confirm: PublishConfirm, video_path: str,
                      before_submit: Callable[[], None]) -> PublishRef: ...


class BaseBrowserAdapter:
    platform = ""
    publish_url = ""
    upload_selector = ""
    title_selector = ""
    desc_selector = ""
    topics_selector = ""
    login_evidence_selector = ""
    account_identity_selector = ""
    submit_selector = ""
    published_id_selector = ""

    def __init__(self, driver: BrowserDriver | None = None) -> None:
        self._driver = driver

    def _require_driver(self) -> BrowserDriver:
        if self._driver is None:
            raise BrowserUnavailable("没有为此成员和平台打开独立浏览器会话")
        return self._driver

    async def login(self) -> LoginState:
        driver = self._require_driver()
        await driver.goto(self.publish_url)
        evidence = (await driver.read_text(self.login_evidence_selector)).strip()
        account_ref = (await driver.read_text(self.account_identity_selector)).strip()
        if not evidence or not account_ref:
            return LoginState(False, self.platform, detail="未读到可验证的登录账号信息")
        return LoginState(True, self.platform, account_ref=account_ref, detail="已读到当前页面账号身份")

    async def publish(self, confirm: PublishConfirm, video_path: str,
                      before_submit: Callable[[], None] = lambda: None) -> PublishRef:
        if not confirm.confirmed:
            raise PermissionError("发布内容尚未得到明确确认")
        if not confirm.account_ref or not confirm.content_digest:
            raise ValueError("缺少已确认账号或成片摘要")
        driver = self._require_driver()
        state = await self.login()
        if not state.logged_in:
            raise LoginRequired(state.detail)
        if state.account_ref != confirm.account_ref:
            raise LoginRequired("当前浏览器登录账号已变化，请重新确认目标账号")

        # Only interact after all caller-confirmed data is present and the current
        # browser identity matches that confirmation. Selectors are platform-owned.
        await driver.upload(self.upload_selector, video_path)
        await driver.fill(self.title_selector, confirm.title)
        await driver.fill(self.desc_selector, confirm.description)
        if confirm.topics:
            await driver.fill(self.topics_selector, " ".join(f"#{topic}" for topic in confirm.topics))
        before_submit()
        await driver.click(self.submit_selector)
        receipt = (await driver.read_text(self.published_id_selector)).strip()
        if not receipt:
            raise PublishOutcomeUnknown("提交动作已触发但未观察到平台回执；不得自动重发")
        return PublishRef(self.platform, receipt, confirm.account_ref, "页面回执已读取")
