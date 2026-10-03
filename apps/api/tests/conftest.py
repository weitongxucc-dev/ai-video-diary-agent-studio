import os
from pathlib import Path
from tempfile import TemporaryDirectory

# app.main initializes repositories during import, before per-test monkeypatches.
# Keep those initializers away from every developer/app database and output tree.
_test_directory = TemporaryDirectory(prefix="ai-video-tests-", dir=os.environ.get("TMPDIR"))
_test_root = Path(_test_directory.name)
(_test_root / "empty.env").touch()
for name, relative in {
    "DATABASE_PATH": "data/studio.sqlite3",
    "VIDEO_INBOX_PATH": "inbox",
    "VIDEO_OUTPUT_PATH": "output",
    "DRAFT_OUTPUT_PATH": "drafts",
    "JIANYING_DRAFT_ROOT_PATH": "jianying",
    "MODEL_ROOT_PATH": "models",
    "TEMPORARY_ROOT_PATH": "temporary",
}.items():
    path = _test_root / relative
    (path.parent if name == "DATABASE_PATH" else path).mkdir(parents=True, exist_ok=True)
    os.environ[name] = str(path)
os.environ.update({
    "AI_VIDEO_DIARY_DOTENV": str(_test_root / "empty.env"),
    "NATIVE_OCR_WORKER_PATH": "",
    "NATIVE_PORTRAIT_WORKER_PATH": "",
    "LLM_PROVIDER": "deepseek",
    "LLM_API_KEY": "test-only-key",
    "LLM_API_KEY_PROVIDER": "deepseek",
    "LLM_BASE_URL": "http://127.0.0.1:9",
    "DEEPSEEK_API_KEY": "test-only-key",
    "DEEPSEEK_BASE_URL": "http://127.0.0.1:9",
    "TENCENTCLOUD_SECRET_ID": "",
    "TENCENTCLOUD_SECRET_KEY": "",
    "PRODUCT_HUNT_TOKEN": "",
    "SUPABASE_URL": "",
    "SUPABASE_PUBLISHABLE_KEY": "",
    "SUPABASE_JWT_ISSUER": "",
})


def pytest_sessionfinish(session, exitstatus):
    _test_directory.cleanup()


# ── 文字服务密钥：测试期的工具替身 ──────────────────────────────────────
# 普通用例不该碰这台机器的登录钥匙串：Linux CI 上也没有 `security`。这里统一把
# 工作区配置根指向临时目录，并把钥匙串调用换成内存实现；需要真实系统安全存储
# 实证的用例单独自建临时钥匙串，只在 macOS 上跑。
import pytest as _pytest


class _InMemorySecretTool:
    """按 `security` 的参数约定实现的最小替身，只服务本单的工作区密钥。"""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self.locked = False

    def _item(self, arguments: list[str]) -> tuple[str, str]:
        def value_of(flag: str) -> str:
            return arguments[arguments.index(flag) + 1]

        return value_of("-a"), value_of("-s")

    def run(self, arguments: list[str]) -> "subprocess.CompletedProcess[str]":
        verb = arguments[0]
        account, service = self._item(arguments)
        if self.locked:
            return _completed(arguments, 36, "", f"security: {verb}: User interaction is not allowed.")
        if verb == "add-generic-password":
            self.items[(account, service)] = value = arguments[arguments.index("-w") + 1]
            return _completed(arguments, 0, value, "")
        if verb == "find-generic-password":
            if (account, service) not in self.items:
                return _not_found(arguments, verb)
            return _completed(arguments, 0, self.items[(account, service)], "")
        if verb == "delete-generic-password":
            if (account, service) not in self.items:
                return _not_found(arguments, verb)
            del self.items[(account, service)]
            return _completed(arguments, 0, "password has been deleted.", "")
        raise AssertionError(f"测试替身不认识这个命令：{verb}")


def _completed(arguments, returncode, stdout, stderr):
    import subprocess

    return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)


def _not_found(arguments, verb):
    return _completed(
        arguments, 44, "",
        f"security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain. ({verb})",
    )


@_pytest.fixture(autouse=True)
def workspace_secrets_tool_double(tmp_path_factory, monkeypatch):
    from app import workspace_settings as workspace_settings_module

    monkeypatch.setenv(
        workspace_settings_module._CONFIG_ROOT_ENV,
        str(tmp_path_factory.mktemp("workspace-config")),
    )
    monkeypatch.delenv(workspace_settings_module._KEYCHAIN_ENV, raising=False)
    tool = _InMemorySecretTool()
    monkeypatch.setattr(workspace_settings_module, "_run_security", tool.run)
    with workspace_settings_module._lock:
        workspace_settings_module._secret_cache.clear()
    yield tool
    with workspace_settings_module._lock:
        workspace_settings_module._secret_cache.clear()
