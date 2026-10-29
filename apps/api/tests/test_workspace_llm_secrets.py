"""按工作区隔离的文字服务配置与密钥（WIL-165 阶段A）。

普通用例走 tests/conftest.py 的临时工作区根 + 内存钥匙串替身，任何平台都能跑，
也不碰登录钥匙串。系统安全存储的真实实证另用 `real_keychain`：只在本单自建的
临时钥匙串里进行，仅 macOS 上执行。
"""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import workspace_settings as module
from app.config import Settings
from app.runtime_settings import (
    apply_workspace_runtime_settings_update,
    split_runtime_settings_updates,
)


macos = pytest.mark.skipif(shutil.which("security") is None, reason="系统安全存储只在 macOS 上可用")

@pytest.fixture
def real_keychain(tmp_path, monkeypatch):
    """本单自建的临时钥匙串：真实系统安全存储实证，用完删掉。"""
    if shutil.which("security") is None:
        pytest.skip("系统安全存储只在 macOS 上可用")
    keychain_path = tmp_path / "wil165-test.keychain"
    created = subprocess.run(
        ["security", "create-keychain", "-p", "wil165-test", str(keychain_path)],
        text=True, capture_output=True, check=False,
    )
    if created.returncode != 0:
        pytest.skip("无法创建临时钥匙串")
    monkeypatch.setattr(module, "_run_security", _real_security)
    monkeypatch.setenv(module._KEYCHAIN_ENV, str(keychain_path))
    with module._lock:
        module._secret_cache.clear()
    try:
        yield tmp_path
    finally:
        subprocess.run(["security", "delete-keychain", str(keychain_path)], check=False)
        with module._lock:
            module._secret_cache.clear()


def _real_security(arguments: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(["security", *arguments], text=True, capture_output=True, check=False)


@macos
def test_two_workspaces_save_read_delete_their_own_secret(real_keychain):
    module.save_workspace_secret("ws-a", "llm_api_key", "key-a")
    module.save_workspace_secret("ws-b", "llm_api_key", "key-b")

    assert module.read_workspace_secret("ws-a", "llm_api_key") == "key-a"
    assert module.read_workspace_secret("ws-b", "llm_api_key") == "key-b"

    assert module.delete_workspace_secret("ws-a", "llm_api_key") is True
    assert module.read_workspace_secret("ws-a", "llm_api_key") is None
    # 删掉A的密钥不能让B失效，也不该把A的旧值从缓存里复活。
    assert module.read_workspace_secret("ws-b", "llm_api_key") == "key-b"


@macos
def test_delete_then_read_never_revives_the_old_key(real_keychain):
    module.save_workspace_secret("ws-a", "deepseek_api_key", "ds-old")
    assert module.delete_workspace_secret("ws-a", "deepseek_api_key") is True
    assert module.read_workspace_secret("ws-a", "deepseek_api_key") is None
    assert module.delete_workspace_secret("ws-a", "deepseek_api_key") is False


def test_snapshot_refuses_plaintext_secret_fields():
    with pytest.raises(module.WorkspaceSecretStoreError):
        module.save_workspace_config("ws-a", {"llm_api_key": "must-not-be-plaintext"})


def test_snapshot_keeps_workspaces_apart():
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
def test_workspace_settings_use_that_workspace_secret_only(real_keychain):
    base = Settings(llm_provider="minimax")
    module.save_workspace_secret("ws-a", "llm_api_key", "key-a")

    a = module.workspace_settings("ws-a", base).settings
    b = module.workspace_settings("ws-b", base).settings

    assert a.effective_llm_api_key == "key-a"
    assert b.effective_llm_api_key is None


@macos
def test_apply_update_keeps_key_out_of_snapshot(real_keychain):
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


@macos
def test_blank_key_input_keeps_the_stored_key(real_keychain):
    module.save_workspace_secret("ws-a", "llm_api_key", "key-a")
    changed = apply_workspace_runtime_settings_update(
        "ws-a",
        {"llm_provider": "minimax", "llm_api_key": "   "},
        lambda: None,
    )

    assert changed is True
    assert module.read_workspace_secret("ws-a", "llm_api_key") == "key-a"


@macos
def test_explicit_delete_action_removes_the_key(real_keychain):
    module.save_workspace_secret("ws-a", "llm_api_key", "key-a")
    module.save_workspace_config("ws-a", {"llm_provider": "minimax", "llm_api_key_provider": "minimax"})

    changed = apply_workspace_runtime_settings_update(
        "ws-a",
        {"llm_key_action": "delete"},
        lambda: None,
    )

    assert changed is True
    assert module.read_workspace_secret("ws-a", "llm_api_key") is None
    assert module.load_workspace_config("ws-a") == {"llm_provider": "minimax"}


def test_workspace_settings_do_not_inherit_the_global_plaintext_key():
    """旧全局明文没有工作区归属，不能被这个工作区继承去用。"""
    base = Settings(llm_provider="minimax", llm_api_key="global-plaintext",
                    llm_base_url="https://api.minimax.io/v1", llm_model="MiniMax-M3")

    resolved = module.workspace_settings("ws-a", base).settings

    assert resolved.effective_llm_api_key is None
    assert resolved.llm_api_key is None
    assert resolved.effective_llm_model == "MiniMax-M3"


@macos
def test_deleted_key_is_not_revived_by_the_global_plaintext(real_keychain):
    module.save_workspace_secret("ws-a", "llm_api_key", "key-a")
    base = Settings(llm_provider="minimax", llm_api_key="global-plaintext")
    assert module.workspace_settings("ws-a", base).settings.effective_llm_api_key == "key-a"

    module.delete_workspace_secret("ws-a", "llm_api_key")

    assert module.workspace_settings("ws-a", base).settings.effective_llm_api_key is None


def test_snapshot_update_keeps_earlier_fields():
    module.save_workspace_config("ws-a", {"llm_provider": "minimax", "llm_base_url": "https://api.minimax.io/v1"})
    module.save_workspace_config("ws-a", {"llm_model": "MiniMax-M3"})

    assert module.load_workspace_config("ws-a") == {
        "llm_provider": "minimax",
        "llm_base_url": "https://api.minimax.io/v1",
        "llm_model": "MiniMax-M3",
    }


@macos
def test_settings_routes_scope_reads_and_writes_to_the_request_workspace(real_keychain):
    """同一套保存路由，两个工作区各自读回自己的配置和密钥。"""
    import app.main as main_module
    from app.auth import AuthPrincipal
    from app.schemas import RuntimeSettingsUpdateRequest

    def call(workspace_id, payload):
        request = SimpleNamespace(
            state=SimpleNamespace(
                principal=AuthPrincipal(user_id="u", workspace_id=workspace_id)
            ),
            client=SimpleNamespace(host="127.0.0.1"),
        )
        return main_module.update_runtime_settings(
            request, RuntimeSettingsUpdateRequest(**payload)
        )

    call("ws-a", {"llm_provider": "minimax", "llm_api_key": "key-a", "llm_model": "MiniMax-M3"})
    call("ws-b", {"llm_provider": "deepseek"})

    assert module.load_workspace_config("ws-a")["llm_model"] == "MiniMax-M3"
    assert module.load_workspace_config("ws-b") == {"llm_provider": "deepseek"}
    assert module.read_workspace_secret("ws-b", "llm_api_key") is None


def test_settings_route_reports_a_refused_keychain_instead_of_success(monkeypatch):
    import app.main as main_module
    from app.auth import AuthPrincipal
    from app.schemas import RuntimeSettingsUpdateRequest

    monkeypatch.setattr(
        module, "_run_security",
        lambda arguments: subprocess.CompletedProcess(
            arguments, 36, "", "security: SecKeychainItemCreateFromContent: User interaction is not allowed."
        ),
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            principal=AuthPrincipal(user_id="u", workspace_id="ws-a")
        ),
        client=SimpleNamespace(host="127.0.0.1"),
    )

    with pytest.raises(HTTPException) as refused:
        main_module.update_runtime_settings(
            request, RuntimeSettingsUpdateRequest(llm_api_key="key-a")
        )

    assert refused.value.status_code == 503


@macos
def test_delete_removes_the_deepseek_fallback_key_too(real_keychain):
    """选了 DeepSeek 时，只删新键的话旧 DEEPSEEK 键会接着生效。"""
    module.save_workspace_config("ws-a", {"llm_provider": "deepseek"})
    module.save_workspace_secret("ws-a", "llm_api_key", "new-deepseek-key")
    module.save_workspace_secret("ws-a", "deepseek_api_key", "legacy-deepseek-key")

    apply_workspace_runtime_settings_update(
        "ws-a", {"llm_key_action": "delete"}, lambda: None
    )

    assert module.read_workspace_secret("ws-a", "llm_api_key") is None
    assert module.read_workspace_secret("ws-a", "deepseek_api_key") is None
    resolved = module.workspace_settings("ws-a", Settings()).settings
    assert resolved.effective_llm_api_key is None


@macos
def test_delete_leaves_another_workspace_and_other_keys_alone(real_keychain):
    module.save_workspace_config("ws-a", {"llm_provider": "minimax"})
    module.save_workspace_secret("ws-a", "llm_api_key", "key-a")
    module.save_workspace_secret("ws-b", "llm_api_key", "key-b")

    apply_workspace_runtime_settings_update(
        "ws-a", {"llm_key_action": "delete"}, lambda: None
    )

    assert module.read_workspace_secret("ws-b", "llm_api_key") == "key-b"
    assert module.read_workspace_secret("ws-a", "llm_api_key") is None


def test_secret_write_failure_is_never_read_as_success(monkeypatch):
    monkeypatch.setattr(
        module, "_run_security",
        lambda arguments: subprocess.CompletedProcess(
            arguments, 44, "",
            "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.",
        ),
    )

    with pytest.raises(module.WorkspaceSecretStoreError):
        module.save_workspace_secret("ws-a", "llm_api_key", "key-a")


def test_topic_route_snapshot_reads_the_requesting_workspace():
    import app.main as main_module

    module.save_workspace_config("ws-a", {"llm_provider": "minimax", "llm_model": "MiniMax-M3"})
    module.save_workspace_secret("ws-a", "llm_api_key", "key-a")

    snapshot = main_module._topic_route_snapshot("ws-a")
    unconfigured = main_module._topic_route_snapshot("ws-b")

    assert snapshot["availability"] == "unverified"
    assert snapshot["model_id"] == "MiniMax-M3"
    assert unconfigured["availability"] == "unconfigured"


def test_plan_turn_reports_the_requesting_workspace_provider(monkeypatch):
    """响应里的 provider/model 必须是本次工作区的配置，且不靠网络。"""
    import app.main as main_module
    from app.auth import AuthPrincipal
    from app.schemas import AssistantConversationUpdateRequest, WorkspaceAssistantPlanTurnRequest

    module.save_workspace_config("ws-a", {"llm_provider": "minimax", "llm_model": "MiniMax-M3"})
    module.save_workspace_secret("ws-a", "llm_api_key", "key-a")
    seen: dict[str, str] = {}

    def fake_run_plan_turn(turn_settings, **kwargs):
        seen["provider"] = turn_settings.effective_llm_provider
        seen["key"] = turn_settings.effective_llm_api_key or ""
        seen["workspace"] = kwargs["workspace_id"]
        return "已记下", None

    monkeypatch.setattr(main_module, "run_plan_turn", fake_run_plan_turn)
    main_module.conversation_repo.initialize()
    conversation = main_module.conversation_repo.create("ws-a", "u", "home", "home")
    request = SimpleNamespace(
        state=SimpleNamespace(
            principal=AuthPrincipal(user_id="u", workspace_id="ws-a")
        ),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={},
    )
    main_module._conversation_http_adapter().update(
        conversation["id"], request, AssistantConversationUpdateRequest(
            draft_text="帮我列个提纲", expected_draft_version=conversation["draft_version"],
        ),
    )
    conversation = main_module.conversation_repo.get(conversation["id"], "ws-a", "u")
    assert conversation is not None and conversation["draft_text"] == "帮我列个提纲"

    response = main_module.workspace_assistant_plan_turn(
        request, WorkspaceAssistantPlanTurnRequest(
            conversation_id=conversation["id"], message="帮我列个提纲",
            client_request_id=str(uuid4()), expected_draft_version=conversation["draft_version"],
        )
    )

    assert response.provider == "minimax"
    assert response.model == "MiniMax-M3"
    assert seen == {"provider": "minimax", "key": "key-a", "workspace": "ws-a"}


@macos
def test_delete_clears_a_workspace_that_only_saved_a_deepseek_key(real_keychain):
    """没写 provider、只存了旧 DeepSeek 键时，删后不能再继续生效。"""
    module.save_workspace_secret("ws-a", "deepseek_api_key", "legacy-deepseek-key")
    module.save_workspace_secret("ws-b", "deepseek_api_key", "other-workspace-key")
    assert module.workspace_settings("ws-a", Settings()).settings.effective_llm_provider == "deepseek"

    apply_workspace_runtime_settings_update(
        "ws-a", {"llm_key_action": "delete"}, lambda: None
    )

    assert module.read_workspace_secret("ws-a", "deepseek_api_key") is None
    assert module.read_workspace_secret("ws-b", "deepseek_api_key") == "other-workspace-key"


def test_workspace_settings_fall_back_to_the_product_deepseek_model():
    base = Settings(deepseek_model="global-deepseek-model")

    resolved = module.workspace_settings("ws-a", base).settings

    assert resolved.deepseek_model == Settings.deepseek_model


def test_locked_keychain_read_is_not_reported_as_no_key(monkeypatch):
    monkeypatch.setattr(
        module, "_run_security",
        lambda arguments: subprocess.CompletedProcess(
            arguments, 36, "", "security: SecKeychainSearchCopyNext: User interaction is not allowed."
        ),
    )

    with pytest.raises(module.WorkspaceSecretStoreError):
        module.read_workspace_secret("ws-a", "llm_api_key")


def test_locked_keychain_delete_is_not_reported_as_nothing_deleted(monkeypatch):
    monkeypatch.setattr(
        module, "_run_security",
        lambda arguments: subprocess.CompletedProcess(
            arguments, 36, "", "security: SecKeychainItemDelete: User interaction is not allowed."
        ),
    )

    with pytest.raises(module.WorkspaceSecretStoreError):
        module.delete_workspace_secret("ws-a", "llm_api_key")


def test_split_leaves_non_text_settings_out_of_the_workspace_route():
    config_updates, _secret_updates = split_runtime_settings_updates({
        "llm_model": "MiniMax-M3",
        "model_root_path": "/Volumes/Models/AI视频日记/models",
        "video_output_path": "/Volumes/Media/AI视频日记/output",
        "supabase_url": "https://example.supabase.co",
    })

    assert config_updates == {"llm_model": "MiniMax-M3"}


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


def test_secret_store_failure_never_falls_back_to_plaintext(monkeypatch):
    module.save_workspace_secret("ws-a", "llm_api_key", "key-a")

    monkeypatch.setattr(
        module, "_run_security",
        lambda arguments: subprocess.CompletedProcess(arguments, 1, "", "security: SecKeychainItemCreateFromContent failed"),
    )
    with pytest.raises(module.WorkspaceSecretStoreError):
        module.save_workspace_secret("ws-a", "llm_api_key", "rotated-key")

    snapshot = module.workspace_config_path("ws-a")
    assert not snapshot.exists() or "rotated-key" not in snapshot.read_text(encoding="utf-8")


def test_pi_config_dir_is_per_workspace(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()

    a = module.workspace_pi_config_dir("ws-a", parent)
    b = module.workspace_pi_config_dir("ws-b", parent)

    assert a != b
    assert a.is_dir() and b.is_dir()
    assert (a / "sessions").is_dir() and (b / "sessions").is_dir()


def test_pi_verification_does_not_light_up_another_workspace():
    from app.config import record_llm_service_verification

    settings = Settings(llm_provider="minimax", llm_api_key="key-a", llm_model="MiniMax-M3")
    record_llm_service_verification(settings, ok=True, cause="verified", workspace_id="ws-a")

    assert settings.llm_service_status("ws-a")["state"] == "available"
    assert settings.llm_service_status("ws-b")["state"] == "unverified"
    assert settings.llm_service_status()["state"] == "unverified"


def test_pi_config_dir_sanitises_workspace_id(tmp_path):
    parent = tmp_path / "agent"
    parent.mkdir()

    root = module.workspace_pi_config_dir("ws/../../escape", parent)

    assert root.parent == parent / "workspaces"
    assert ".." not in root.name
    assert root.resolve().is_relative_to(parent.resolve())
