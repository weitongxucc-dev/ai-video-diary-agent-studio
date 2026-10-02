"""平台发布浏览器自动化基座：按平台适配器，公共流程收在 base。

「留路不堵死」：save_draft 本轮实现（结构到位），publish 本轮只留桩、显式抛错。
真实平台执行需要已登录浏览器 + 真实账号，本包不真跑（红线：不许用真实平台账号验证）；
所有真实 URL/选择器标注「未验证」，逻辑用单元测试 + mock driver 验证。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class PublishNotAvailable(RuntimeError):
    """发布 action 未开放（本轮只留桩）。"""

    def __init__(self) -> None:
        super().__init__("发布功能未开放，需用户授权后开启")


@dataclass(frozen=True)
class LoginState:
    logged_in: bool
    platform: str
    detail: str = ""


@dataclass(frozen=True)
class UploadHandle:
    platform: str
    ref: str


@dataclass(frozen=True)
class DraftRef:
    platform: str
    draft_id: str
    mocked: bool  # 真实平台未跑时必为 True，绝不冒充「已打通平台」


@dataclass(frozen=True)
class PublishConfirm:
    title: str
    description: str
    topics: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublishRef:
    platform: str
    publish_id: str


# 风控 / 验证码 / 频控 → 一律「需人工介入」，不绕过。
_INTERVENTION_KINDS = {"captcha", "risk_control", "rate_limit"}


def needs_human_intervention(kind: str) -> bool:
    return kind in _INTERVENTION_KINDS


class BrowserDriver(Protocol):
    """真实浏览器驱动（Playwright/CDP）的最小接口。真跑时由已登录 profile 注入。"""

    async def goto(self, url: str) -> None: ...
    async def upload(self, selector: str, path: str) -> None: ...
    async def fill(self, selector: str, value: str) -> None: ...
    async def click(self, selector: str) -> None: ...
    async def read_text(self, selector: str) -> str: ...


@runtime_checkable
class PlatformAdapter(Protocol):
    platform: str

    async def login(self) -> LoginState: ...
    async def upload_video(self, path: str) -> UploadHandle: ...
    async def fill_copywriting(self, title: str, desc: str, topics: tuple[str, ...]) -> None: ...
    async def save_draft(self) -> DraftRef: ...          # 本轮实现（结构到位）
    async def publish(self, confirm: PublishConfirm) -> PublishRef: ...  # 本轮只留桩


class BaseBrowserAdapter:
    """公共流程：拿登录态、传文件、错误分类；平台差异在子类常量/选择器。"""

    platform: str = ""
    # 真实 URL / 选择器——全部「未验证」，需真实账号核实后替换。
    publish_url: str = ""
    upload_selector: str = ""
    title_selector: str = ""
    desc_selector: str = ""
    topics_selector: str = ""
    draft_button_selector: str = ""
    # 登录态证据元素：已登录才有的元素（用户名/头像）；空 = 无证据可判 → logged_in=False
    login_evidence_selector: str = ""

    def __init__(self, driver: BrowserDriver | None = None) -> None:
        self._driver = driver

    def _require_driver(self) -> BrowserDriver:
        if self._driver is None:
            raise RuntimeError("未接入真实浏览器会话（需已登录账号），无法执行")
        return self._driver

    async def login(self) -> LoginState:
        # 不许默认 True：真去读登录态证据；判不出来就 logged_in=False，不冒充已登录。
        if self._driver is None:
            return LoginState(
                logged_in=False, platform=self.platform, detail="无浏览器驱动，无法判定登录态"
            )
        await self._driver.goto(self.publish_url)
        evidence = ""
        if self.login_evidence_selector:
            try:
                evidence = (await self._driver.read_text(self.login_evidence_selector)).strip()
            except Exception:
                evidence = ""
        if evidence:
            return LoginState(
                logged_in=True, platform=self.platform, detail=f"已登录：{evidence}"
            )
        return LoginState(
            logged_in=False, platform=self.platform, detail="无法判定登录态（无已登录证据）"
        )

    async def upload_video(self, path: str) -> UploadHandle:
        driver = self._require_driver()
        await driver.upload(self.upload_selector, path)
        return UploadHandle(platform=self.platform, ref=path)

    async def fill_copywriting(self, title: str, desc: str, topics: tuple[str, ...]) -> None:
        driver = self._require_driver()
        await driver.fill(self.title_selector, title)
        await driver.fill(self.desc_selector, desc)
        if topics:
            await driver.fill(self.topics_selector, " ".join(topics))

    async def save_draft(self) -> DraftRef:
        # 本轮实现到「点保存草稿」为止；真实平台未跑，draft_id 只能是 mock 占位。
        driver = self._require_driver()
        await driver.click(self.draft_button_selector)
        draft_id = await driver.read_text(self.draft_button_selector)
        return DraftRef(platform=self.platform, draft_id=f"mock:{self.platform}:{draft_id or 'draft'}", mocked=True)

    async def publish(self, confirm: PublishConfirm) -> PublishRef:
        # 发布本轮不实现，但位置留出：将来真发 = 填这个方法 + 加人工确认，不是重写。
        raise PublishNotAvailable()
