"""B03：外部文字模型服务的四档状态。

核心断言只有一条：**字段填全但从没真连过，绝不能显示成「可用」**。
"""
from __future__ import annotations

import pytest

from app import config as config_module
from app.config import (
    LLM_SERVICE_AVAILABLE,
    LLM_SERVICE_FAILED,
    LLM_SERVICE_MISSING,
    LLM_SERVICE_UNVERIFIED,
    Settings,
    record_llm_service_verification,
)

FILLED_KEY = "private-test-key"
ABSENT_KEY = "another-private-key"


@pytest.fixture(autouse=True)
def _clear_verifications():
    """验证记录是进程内全局的，测试之间必须清干净，否则会串味。"""
    with config_module._llm_service_lock:
        config_module._llm_service_verifications.clear()
    yield
    with config_module._llm_service_lock:
        config_module._llm_service_verifications.clear()


def _filled() -> Settings:
    return Settings(llm_api_key=FILLED_KEY, llm_api_key_provider="minimax")


def _filled_with(other_key: str) -> Settings:
    return Settings(llm_api_key=other_key, llm_api_key_provider="minimax")


# ── 四档各自的判定 ────────────────────────────────────────────────────────

def test_missing_when_fields_are_not_filled():
    settings = Settings()
    assert settings.llm_configured is False
    assert settings.llm_service_state == LLM_SERVICE_MISSING
    assert settings.llm_service_status()["label"] == "未填写"


def test_unverified_when_fields_are_filled_but_never_tried():
    settings = _filled()
    assert settings.llm_configured is True
    assert settings.llm_service_state == LLM_SERVICE_UNVERIFIED
    assert settings.llm_service_status()["label"] == "已填写，未验证"


def test_available_only_after_a_recorded_successful_call():
    settings = _filled()
    record_llm_service_verification(settings, ok=True, cause="verified")
    assert settings.llm_service_state == LLM_SERVICE_AVAILABLE
    assert settings.llm_service_status()["verified"] is True


def test_failed_after_a_recorded_real_failure():
    settings = _filled()
    record_llm_service_verification(settings, ok=False, cause="key_rejected")
    assert settings.llm_service_state == LLM_SERVICE_FAILED
    assert settings.llm_service_status()["verified"] is False


# ── 本包最关键的一条 ──────────────────────────────────────────────────────

def test_filled_never_counts_as_available():
    """把每一处「声称可用」的入口都堵一遍：状态、标签、详情、设置页那一行。"""
    settings = _filled()
    status = settings.llm_service_status()
    assert settings.llm_service_state != LLM_SERVICE_AVAILABLE
    assert status["verified"] is False
    assert "可用" not in status["label"]
    assert "可用" not in status["detail"]
    rows = {row["name"]: row["value"] for row in settings.safe_runtime_settings()["settings"]}
    assert rows["LLM_SERVICE_STATE"] == "已填写，未验证"
    assert rows["LLM_API_KEY"] == "已填写"


def test_public_status_still_carries_only_the_schema_backed_keys():
    """`public_status()` 经 LLMStatusResponse 校验，多写的字段会被 Pydantic 静默丢掉。

    所以四档状态只走 `safe_runtime_settings()`，这里一个字都不多。
    """
    assert set(_filled().public_status()["llm"]) == {
        "provider", "configured", "model", "base_url",
    }


def test_saving_the_form_does_not_fabricate_a_connection():
    """保存配置只写字段，不写验证记录——保存完必须还是「未验证」。"""
    saved = Settings(llm_api_key="brand-new-key", llm_api_key_provider="minimax")
    assert saved.llm_configured is True
    assert saved.llm_service_state == LLM_SERVICE_UNVERIFIED
    assert _filled().llm_service_state == LLM_SERVICE_UNVERIFIED


def test_changing_the_key_invalidates_an_earlier_available():
    verified = _filled()
    record_llm_service_verification(verified, ok=True, cause="verified")
    assert verified.llm_service_state == LLM_SERVICE_AVAILABLE
    replaced = _filled_with(ABSENT_KEY)
    assert replaced.llm_service_state == LLM_SERVICE_UNVERIFIED


def test_changing_the_model_invalidates_an_earlier_available():
    verified = _filled()
    record_llm_service_verification(verified, ok=True, cause="verified")
    other_model = Settings(llm_api_key=FILLED_KEY, llm_api_key_provider="minimax",
                           llm_model="MiniMax-M3-preview")
    assert other_model.llm_service_state == LLM_SERVICE_UNVERIFIED


def test_state_is_process_local_and_survives_nothing_but_the_process():
    """不落盘：换一个不同的 key 组合拿不到旧结论，只能回到未验证。"""
    verified = _filled()
    record_llm_service_verification(verified, ok=True, cause="verified")
    reloaded = Settings(llm_api_key=FILLED_KEY, llm_api_key_provider="minimax",
                        llm_base_url="https://api.minimax.cn/v1")
    assert reloaded.llm_service_state == LLM_SERVICE_AVAILABLE
    with config_module._llm_service_lock:
        config_module._llm_service_verifications.clear()
    assert reloaded.llm_service_state == LLM_SERVICE_UNVERIFIED


# ── 失败原因要如实，拿不到就说拿不到 ──────────────────────────────────────

def test_failure_detail_names_the_observed_cause():
    settings = _filled()
    record_llm_service_verification(settings, ok=False, cause="key_rejected")
    assert "密钥" in settings.llm_service_status()["detail"]


def test_unknown_failure_cause_says_it_cannot_tell():
    settings = _filled()
    record_llm_service_verification(settings, ok=False, cause="something-else")
    assert settings.llm_service_state == LLM_SERVICE_FAILED
    assert "拿不到更具体的原因" in settings.llm_service_status()["detail"]


# ── 提示里不许出现密钥 ────────────────────────────────────────────────────

@pytest.mark.parametrize("cause", ["key_rejected", "address_unreachable", "timeout", "unknown"])
@pytest.mark.parametrize("ok", [True, False])
def test_service_status_never_leaks_the_key(cause, ok):
    settings = _filled()
    record_llm_service_verification(settings, ok=ok, cause=cause)
    text = " ".join(str(value) for value in settings.llm_service_status().values())
    assert FILLED_KEY not in text
    assert FILLED_KEY not in str(settings.public_status()["llm"])
    rows = " ".join(str(row) for row in settings.safe_runtime_settings()["settings"])
    assert FILLED_KEY not in rows
