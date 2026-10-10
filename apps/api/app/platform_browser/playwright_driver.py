"""Lazy, isolated Chrome profile manager using the approved Playwright runtime.

No browser is installed or started at import time. A profile is created only
after an explicit account-connect action and is partitioned by workspace,
member and platform. Playwright objects are confined to one asyncio loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from concurrent.futures import Future, TimeoutError
from pathlib import Path

from app.platform_browser.base import BrowserUnavailable, PUBLISH_OPERATION_TIMEOUT_SECONDS


def profile_key(workspace_id: str, user_id: str, platform: str) -> str:
    if not workspace_id or not user_id or not platform:
        raise ValueError("独立浏览器配置需要工作区、成员和平台")
    raw = "\0".join((workspace_id, user_id, platform)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class PlaywrightPageDriver:
    def __init__(self, page) -> None:
        self._page = page

    async def goto(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded")

    async def upload(self, selector: str, path: str) -> None:
        await self._page.locator(selector).set_input_files(path)

    async def fill(self, selector: str, value: str) -> None:
        await self._page.locator(selector).fill(value)

    async def click(self, selector: str) -> None:
        await self._page.locator(selector).click()

    async def read_text(self, selector: str) -> str:
        locator = self._page.locator(selector).first
        if await locator.count() == 0:
            return ""
        return (await locator.inner_text()).strip()


class PlaywrightSessionManager:
    def __init__(self, profile_root: str | Path) -> None:
        self._profile_root = Path(profile_root).expanduser().resolve()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._contexts: dict[str, object] = {}
        self._pages: dict[str, object] = {}
        self._drivers: dict[str, PlaywrightPageDriver] = {}
        self._playwright = None
        self._launch_lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._thread is None:
            with self._launch_lock:
                if self._thread is None:
                    self._thread = threading.Thread(
                        target=self._run_loop, name="platform-browser-loop", daemon=True
                    )
                    self._thread.start()
        if not self._ready.wait(timeout=10) or self._loop is None:
            raise BrowserUnavailable("浏览器会话运行线程未就绪")
        return self._loop

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    def _call(self, coroutine, *, timeout: float = PUBLISH_OPERATION_TIMEOUT_SECONDS):
        loop = self._ensure_loop()
        future: Future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            future.cancel()
            raise BrowserUnavailable("浏览器动作超时；发布结果不得按成功处理") from exc
        except Exception:
            raise

    async def _open(self, key: str, profile_dir: Path, url: str) -> PlaywrightPageDriver:
        if key not in self._contexts:
            from playwright.async_api import async_playwright

            profile_dir.mkdir(parents=True, exist_ok=True)
            self._playwright = self._playwright or await async_playwright().start()
            try:
                context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir), channel="chrome", headless=False,
                    accept_downloads=False,
                )
            except Exception as exc:
                raise BrowserUnavailable(
                    "无法通过 channel=chrome 打开独立平台配置；请安装并启动受支持的 Google Chrome"
                ) from exc
            page = context.pages[0] if context.pages else await context.new_page()
            self._contexts[key] = context
            self._pages[key] = page
            self._drivers[key] = PlaywrightPageDriver(page)
        page = self._pages[key]
        await page.goto(url, wait_until="domcontentloaded")
        return self._drivers[key]

    def open(self, workspace_id: str, user_id: str, platform: str, url: str) -> PlaywrightPageDriver:
        key = profile_key(workspace_id, user_id, platform)
        profile_dir = (self._profile_root / key).resolve()
        if self._profile_root not in profile_dir.parents:
            raise BrowserUnavailable("独立浏览器配置路径无效")
        return self._call(self._open(key, profile_dir, url), timeout=60)

    def driver(self, workspace_id: str, user_id: str, platform: str) -> PlaywrightPageDriver:
        key = profile_key(workspace_id, user_id, platform)
        if key not in self._drivers:
            raise BrowserUnavailable("尚未为当前成员打开该平台的独立浏览器配置")
        return self._drivers[key]

    def invoke(self, workspace_id: str, user_id: str, platform: str, action,
               *, timeout: float = PUBLISH_OPERATION_TIMEOUT_SECONDS):
        """Run an async action with its page driver on Playwright's owning loop."""
        key = profile_key(workspace_id, user_id, platform)

        async def run_action():
            driver = self._drivers.get(key)
            if driver is None:
                raise BrowserUnavailable("尚未为当前成员打开该平台的独立浏览器配置")
            result = action(driver)
            if hasattr(result, "__await__"):
                return await result
            return result

        return self._call(run_action(), timeout=timeout)

    async def _close_all(self) -> None:
        for context in tuple(self._contexts.values()):
            await context.close()
        self._contexts.clear()
        self._pages.clear()
        self._drivers.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    def close_all(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._call(self._close_all(), timeout=30)
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._loop = None
            self._thread = None
            self._ready.clear()
