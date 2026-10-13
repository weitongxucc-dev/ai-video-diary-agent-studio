"""同进程内第二次更正密钥到底生不生效（B01 · C1）。

进程启动时 `os.environ` 里可能已经有 `LLM_API_KEY`——桌面壳 `apps/desktop/main.js:283`
把 Electron 自己的 `process.env` 原样透传给 API 子进程，而 `load_dotenv_files` 是
first-wins（`if key and key not in os.environ`）。全仓又没有一处会在重新加载前
把旧值作废，于是「第二次更正」在同一个进程里静默不生效。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import env_files
from app.config import (
    LLM_SERVICE_AVAILABLE,
    LLM_SERVICE_UNVERIFIED,
    Settings,
    llm_service_fingerprint,
    llm_service_verification,
    record_llm_service_verification,
)
from app.runtime_settings import FIELD_TO_ENV, apply_runtime_settings_update

# 设置页能改写的全部环境变量名。测试里逐个清理，免得上一条用例的残留值
# 通过 first-wins 语义漏进下一条。
OWNED_KEYS = set(FIELD_TO_ENV.values()) | {"LLM_API_KEY_PROVIDER", "TOPIC_SOURCE_URLS"}


@pytest.fixture
def dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 .env 指到 tmp，并保证这些键在用例前后是干净的。"""
    path = tmp_path / ".env"
    for key in OWNED_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AI_VIDEO_DIARY_DOTENV", str(path))
    monkeypatch.setattr(env_files, "_repo_root", lambda: tmp_path / "repo")
    return path


def write_dotenv(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_a_second_correction_to_the_same_key_takes_effect_in_the_same_process(dotenv: Path) -> None:
    write_dotenv(dotenv, "LLM_PROVIDER=minimax\nLLM_API_KEY=dummy-first\n")
    assert Settings.from_env().effective_llm_api_key == "dummy-first"

    write_dotenv(dotenv, "LLM_PROVIDER=minimax\nLLM_API_KEY=dummy-second\n")
    second = Settings.from_env()

    assert second.effective_llm_api_key == "dummy-second"


def test_reload_prefers_the_file_over_the_value_the_process_started_with(
    dotenv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启动环境里带同名变量时，.env 里改过的值必须能压过它。"""
    write_dotenv(dotenv, "LLM_PROVIDER=minimax\nLLM_API_KEY=dummy-from-launcher\n")
    monkeypatch.setenv("LLM_API_KEY", "dummy-from-launcher")
    Settings.from_env()

    write_dotenv(dotenv, "LLM_PROVIDER=minimax\nLLM_API_KEY=dummy-correction\n")
    corrected = Settings.from_env()

    assert corrected.effective_llm_api_key == "dummy-correction"


def test_correcting_the_key_invalidates_the_previous_service_verification(dotenv: Path) -> None:
    write_dotenv(
        dotenv,
        "LLM_PROVIDER=minimax\n"
        "LLM_BASE_URL=https://example.invalid/v1\n"
        "LLM_MODEL=demo-model\n"
        "LLM_API_KEY=dummy-green\n",
    )
    before = Settings.from_env()
    record_llm_service_verification(before, ok=True, cause="")
    assert before.llm_service_state == LLM_SERVICE_AVAILABLE

    write_dotenv(
        dotenv,
        "LLM_PROVIDER=minimax\n"
        "LLM_BASE_URL=https://example.invalid/v1\n"
        "LLM_MODEL=demo-model\n"
        "LLM_API_KEY=dummy-corrected\n",
    )
    after = Settings.from_env()

    assert llm_service_fingerprint(after) != llm_service_fingerprint(before)
    assert llm_service_verification(after) is None
    assert after.llm_service_state == LLM_SERVICE_UNVERIFIED


def test_saving_blank_fields_leaves_the_existing_value_alone(dotenv: Path) -> None:
    """留空表示「别动它」，不得顺手把既有值作废。"""
    write_dotenv(dotenv, "LLM_PROVIDER=minimax\nLLM_API_KEY=dummy-kept\n")

    changed = apply_runtime_settings_update(
        dotenv, {"llm_api_key": "   ", "llm_model": ""}, Settings.from_env
    )

    assert changed is False
    assert Settings.from_env().effective_llm_api_key == "dummy-kept"


def test_reload_keeps_unrelated_environment_variables(
    dotenv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/Users/example")

    write_dotenv(dotenv, "LLM_PROVIDER=minimax\nLLM_API_KEY=dummy-1\n")
    write_dotenv(dotenv, "LLM_PROVIDER=minimax\nLLM_API_KEY=dummy-2\n")
    reloaded = Settings.from_env()

    assert reloaded.effective_llm_api_key == "dummy-2"
    assert os.environ["PATH"] == "/usr/bin:/bin"
    assert os.environ["HOME"] == "/Users/example"


def test_inherited_environment_still_wins_for_keys_the_settings_page_cannot_write(
    dotenv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """设置页管不到的键保持 first-wins，别把 12-factor 的语义一起改掉。"""
    monkeypatch.setenv("SOME_VENDOR_TOKEN", "from-launcher")
    write_dotenv(dotenv, "SOME_VENDOR_TOKEN=from-file\n")

    Settings.from_env()

    assert os.environ["SOME_VENDOR_TOKEN"] == "from-launcher"


def test_an_empty_line_never_blanks_out_the_environment(dotenv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "dummy-from-launcher")
    write_dotenv(dotenv, "LLM_PROVIDER=minimax\nLLM_API_KEY=\n")

    Settings.from_env()

    assert os.environ["LLM_API_KEY"] == "dummy-from-launcher"


def test_the_owned_key_list_matches_the_settings_page() -> None:
    """设置页新增字段却忘了登记，就会在重载时重新被启动环境遮蔽。"""
    from app.config import SETTINGS_OWNED_ENV_KEYS

    assert OWNED_KEYS == set(SETTINGS_OWNED_ENV_KEYS)
