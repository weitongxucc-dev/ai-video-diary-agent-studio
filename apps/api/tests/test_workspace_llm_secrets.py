"""按工作区隔离的文字服务配置与密钥（WIL-165 阶段A）。

只碰本单自建的临时钥匙串和临时配置根：不读登录钥匙串、不读真实 dotenv。
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from app import workspace_settings as module
from app.config import Settings
from app.runtime_settings import (
    apply_workspace_runtime_settings_update,
    split_runtime_settings_updates,
)


macos = pytest.mark.skipif(shutil.which("security") is None, reason="系统安全存储只在 macOS 上可用")


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """临时钥匙串 + 临时配置根。测试结束删掉钥匙串。"""
    config_root = tmp_path / "workspace-config"
    monkeypatch.setenv(module._CONFIG_ROOT_ENV, str(config_root))
    keychain_path = tmp_path / "wil165-test.keychain"
    created = subprocess.run(
        ["security", "create-keychain", "-p", "wil165-test", str(keychain_path)],
        text=True, capture_output=True, check=False,
    )
    if created.returncode != 0:
        pytest.skip("无法创建临时钥匙串")
    monkeypatch.setenv(module._KEYCHAIN_ENV, str(keychain_path))
    with module._lock:
        module._secret_cache.clear()
    try:
        yield tmp_path
    finally:
        subprocess.run(["security", "delete-keychain", str(keychain_path)], check=False)
        with module._lock:
            module._secret_cache.clear()


@macos
def test_two_workspaces_save_read_delete_their_own_secret(isolated):
    module.save_workspace_secret("ws-a", "llm_api_key", "key-a")
    module.save_workspace_secret("ws-b", "llm_api_key", "key-b")

    assert module.read_workspace_secret("ws-a", "llm_api_key") == "key-a"
    assert module.read_workspace_secret("ws-b", "llm_api_key") == "key-b"

    assert module.delete_workspace_secret("ws-a", "llm_api_key") is True
    assert module.read_workspace_secret("ws-a", "llm_api_key") is None
    # 删掉A的密钥不能让B失效，也不该把A的旧值从缓存里复活。
    assert module.read_workspace_secret("ws-b", "llm_api_key") == "key-b"


@macos
def test_delete_then_read_never_revives_the_old_key(isolated):
    module.save_workspace_secret("ws-a", "deepseek_api_key", "ds-old")
    assert module.delete_workspace_secret("ws-a", "deepseek_api_key") is True
    assert module.read_workspace_secret("ws-a", "deepseek_api_key") is None
    assert module.delete_workspace_secret("ws-a", "deepseek_api_key") is False


def test_snapshot_refuses_plaintext_secret_fields(isolated):
    with pytest.raises(module.WorkspaceSecretStoreError):
        module.save_workspace_config("ws-a", {"llm_api_key": "must-not-be-plaintext"})


def test_snapshot_keeps_workspaces_apart(isolated):
    module.save_workspace_config("ws-a", {"llm_provider": "minimax", "llm_model": "MiniMax-M3"})
    module.save_workspace_config("ws-b", {"llm_provider": "deepseek"})

    assert module.load_workspace_config("ws-a") == {
        "llm_provider": "minimax",
        "llm_model": "MiniMax-M3",
    }
    assert module.load_workspace_config("ws-b") == {"llm_provider": "deepseek"}
    assert module.load_workspace_config("ws-c") == {}
    raw = module.workspace_config_path("ws-a").read_text(encoding="utf-8")
    assert "api_key" not in raw


@macos
def test_workspace_settings_use_that_workspace_secret_only(isolated):
    base = Settings(llm_provider="minimax")
    module.save_workspace_secret("ws-a", "llm_api_key", "key-a")

    a = module.workspace_settings("ws-a", base).settings
    b = module.workspace_settings("ws-b", base).settings

    assert a.effective_llm_api_key == "key-a"
    assert b.effective_llm_api_key is None


@macos
def test_apply_update_keeps_key_out_of_snapshot(isolated):
    reloaded: list[bool] = []
    changed = apply_workspace_runtime_settings_update(
        "ws-a",
        {"llm_provider": "minimax", "llm_api_key": "key-a"},
        lambda: reloaded.append(True),
    )

    assert changed is True
    assert reloaded == [True]
    assert module.read_workspace_secret("ws-a", "llm_api_key") == "key-a"
    assert "key-a" not in module.workspace_config_path("ws-a").read_text(encoding="utf-8")


def test_split_routes_only_llm_keys_to_the_secret_store():
    config_updates, secret_updates = split_runtime_settings_updates({
        "llm_provider": "minimax",
        "llm_api_key": "key-a",
        "deepseek_api_key": "ds-a",
        "llm_model": "MiniMax-M3",
    })

    assert secret_updates == {"llm_api_key": "key-a", "deepseek_api_key": "ds-a"}
    assert config_updates == {"llm_provider": "minimax", "llm_model": "MiniMax-M3",
                              "llm_api_key_provider": "minimax"}


@macos
def test_secret_store_failure_never_falls_back_to_plaintext(isolated, monkeypatch):
    module.save_workspace_secret("ws-a", "llm_api_key", "key-a")

    monkeypatch.setattr(
        module, "_run_security",
        lambda arguments: subprocess.CompletedProcess(arguments, 1, "", "security: SecKeychainItemCreateFromContent failed"),
    )
    with pytest.raises(module.WorkspaceSecretStoreError):
        module.save_workspace_secret("ws-a", "llm_api_key", "rotated-key")

    snapshot = module.workspace_config_path("ws-a")
    assert not snapshot.exists() or "rotated-key" not in snapshot.read_text(encoding="utf-8")


def test_pi_config_dir_is_per_workspace(isolated):
    parent = isolated / "agent"
    parent.mkdir()

    a = module.workspace_pi_config_dir("ws-a", parent)
    b = module.workspace_pi_config_dir("ws-b", parent)

    assert a != b
    assert a.is_dir() and b.is_dir()
    assert (a / "sessions").is_dir() and (b / "sessions").is_dir()


def test_pi_verification_does_not_light_up_another_workspace(isolated):
    from app.config import record_llm_service_verification

    settings = Settings(llm_provider="minimax", llm_api_key="key-a", llm_model="MiniMax-M3")
    record_llm_service_verification(settings, ok=True, cause="verified", workspace_id="ws-a")

    assert settings.llm_service_status("ws-a")["state"] == "available"
    assert settings.llm_service_status("ws-b")["state"] == "unverified"
    assert settings.llm_service_status()["state"] == "unverified"


def test_pi_config_dir_sanitises_workspace_id(isolated):
    parent = isolated / "agent"
    parent.mkdir()

    root = module.workspace_pi_config_dir("ws/../../escape", parent)

    assert root.parent == parent / "workspaces"
    assert ".." not in root.name
    assert root.resolve().is_relative_to(parent.resolve())