"""B03：外部文字模型服务的四档状态。

核心断言只有一条：**字段填全但从没真连过，绝不能显示成「可用」**。
"""
from __future__ import annotations

import pytest

from app import config as config_module
from app import env_files, service_status_projection
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
def _clear_verifications(tmp_path, monkeypatch):
    """验证记录是进程内全局的，测试之间必须清干净，否则会串味。"""
    monkeypatch.setattr(env_files, "_repo_root", lambda: tmp_path / "env-repo")
    monkeypatch.setenv("AI_VIDEO_DIARY_DOTENV", str(tmp_path / "synthetic.env"))
    with config_module._llm_service_lock:
        config_module._llm_service_verifications.clear()
    yield
    with config_module._llm_service_lock:
        config_module._llm_service_verifications.clear()


def _settings(tmp_path, **values) -> Settings:
    return Settings(
        obsidian_vault_path=str(tmp_path / "vault"),
        hermes_radar_path=str(tmp_path / "hermes"),
        video_inbox_path=str(tmp_path / "inbox"),
        video_output_path=str(tmp_path / "output"),
        draft_output_path=str(tmp_path / "drafts"),
        jianying_draft_root_path=str(tmp_path / "jianying"),
        database_path=str(tmp_path / "studio.sqlite3"),
        model_root_path=str(tmp_path / "models"),
        temporary_root_path=str(tmp_path / "temporary"),
        **values,
    )


def _filled(tmp_path) -> Settings:
    return _settings(tmp_path, llm_api_key=FILLED_KEY, llm_api_key_provider="minimax")


def _filled_with(tmp_path, other_key: str) -> Settings:
    return _settings(tmp_path, llm_api_key=other_key, llm_api_key_provider="minimax")


def test_public_constants_are_the_projection_owners_objects():
    for name in (
        "LLM_SERVICE_MISSING", "LLM_SERVICE_UNVERIFIED",
        "LLM_SERVICE_AVAILABLE", "LLM_SERVICE_FAILED",
    ):
        assert getattr(config_module, name) is getattr(service_status_projection, name)


@pytest.mark.parametrize(
    ("fields_filled", "verification", "expected_state"),
    [
        (False, None, LLM_SERVICE_MISSING),
        (True, None, LLM_SERVICE_UNVERIFIED),
        (True, {"ok": True, "cause": "verified", "observed_at": "synthetic-time"}, LLM_SERVICE_AVAILABLE),
        (True, {"ok": False, "cause": "key_rejected", "observed_at": "synthetic-time"}, LLM_SERVICE_FAILED),
    ],
)
def test_projection_has_exact_eight_fields_and_does_not_mutate_observation(
    fields_filled, verification, expected_state,
):
    before = dict(verification) if verification is not None else None
    result = service_status_projection.project_service_status(
        fields_filled=fields_filled, verification=verification,
    )
    assert set(result) == {
        "state", "label", "detail", "fields_filled", "verified",
        "cause", "repair_action", "observed_at",
    }
    assert result["state"] == expected_state
    assert result["fields_filled"] is fields_filled
    assert result["verified"] is (expected_state == LLM_SERVICE_AVAILABLE)
    assert verification == before


def test_projection_reads_only_the_requested_workspace_observation(tmp_path):
    settings = _filled(tmp_path)
    record_llm_service_verification(
        settings, ok=True, cause="verified", workspace_id="workspace-a",
    )
    assert settings.llm_service_status("workspace-a")["state"] == LLM_SERVICE_AVAILABLE
    assert settings.llm_service_status("workspace-b")["state"] == LLM_SERVICE_UNVERIFIED
    assert settings.llm_service_status()["state"] == LLM_SERVICE_UNVERIFIED


# ── 四档各自的判定 ────────────────────────────────────────────────────────

def test_missing_when_fields_are_not_filled(tmp_path):
    settings = _settings(tmp_path)
    assert settings.llm_configured is False
    assert settings.llm_service_state == LLM_SERVICE_MISSING
    assert settings.llm_service_status()["label"] == "未填写"


def test_unverified_when_fields_are_filled_but_never_tried(tmp_path):
    settings = _filled(tmp_path)
    assert settings.llm_configured is True
    assert settings.llm_service_state == LLM_SERVICE_UNVERIFIED
    assert settings.llm_service_status()["label"] == "已填写，未验证"


def test_available_only_after_a_recorded_successful_call(tmp_path):
    settings = _filled(tmp_path)
    record_llm_service_verification(settings, ok=True, cause="verified")
    assert settings.llm_service_state == LLM_SERVICE_AVAILABLE
    assert settings.llm_service_status()["verified"] is True


def test_failed_after_a_recorded_real_failure(tmp_path):
    settings = _filled(tmp_path)
    record_llm_service_verification(settings, ok=False, cause="key_rejected")
    assert settings.llm_service_state == LLM_SERVICE_FAILED
    assert settings.llm_service_status()["verified"] is False


# ── 本包最关键的一条 ──────────────────────────────────────────────────────

def test_filled_never_counts_as_available(tmp_path):
    """把每一处「声称可用」的入口都堵一遍：状态、标签、详情、设置页那一行。"""
    settings = _filled(tmp_path)
    status = settings.llm_service_status()
    assert settings.llm_service_state != LLM_SERVICE_AVAILABLE
    assert status["verified"] is False
    assert "可用" not in status["label"]
    assert "可用" not in status["detail"]
    rows = {row["name"]: row["value"] for row in settings.safe_runtime_settings()["settings"]}
    assert rows["LLM_SERVICE_STATE"] == "已填写，未验证"
    assert rows["LLM_API_KEY"] == "已填写"


def test_public_status_still_carries_only_the_schema_backed_keys(tmp_path):
    """`public_status()` 经 LLMStatusResponse 校验，多写的字段会被 Pydantic 静默丢掉。

    所以四档状态只走 `safe_runtime_settings()`，这里一个字都不多。
    """
    assert set(_filled(tmp_path).public_status()["llm"]) == {
        "provider", "configured", "model", "base_url",
    }


def test_saving_the_form_does_not_fabricate_a_connection(tmp_path):
    """保存配置只写字段，不写验证记录——保存完必须还是「未验证」。"""
    saved = _settings(tmp_path, llm_api_key="brand-new-key", llm_api_key_provider="minimax")
    assert saved.llm_configured is True
    assert saved.llm_service_state == LLM_SERVICE_UNVERIFIED
    assert _filled(tmp_path).llm_service_state == LLM_SERVICE_UNVERIFIED


def test_changing_the_key_invalidates_an_earlier_available(tmp_path):
    verified = _filled(tmp_path)
    record_llm_service_verification(verified, ok=True, cause="verified")
    assert verified.llm_service_state == LLM_SERVICE_AVAILABLE
    replaced = _filled_with(tmp_path, ABSENT_KEY)
    assert replaced.llm_service_state == LLM_SERVICE_UNVERIFIED


def test_changing_the_model_invalidates_an_earlier_available(tmp_path):
    verified = _filled(tmp_path)
    record_llm_service_verification(verified, ok=True, cause="verified")
    other_model = _settings(tmp_path, llm_api_key=FILLED_KEY, llm_api_key_provider="minimax",
                           llm_model="MiniMax-M3-preview")
    assert other_model.llm_service_state == LLM_SERVICE_UNVERIFIED


def test_state_is_process_local_and_survives_nothing_but_the_process(tmp_path):
    """不落盘：换一个不同的 key 组合拿不到旧结论，只能回到未验证。"""
    verified = _filled(tmp_path)
    record_llm_service_verification(verified, ok=True, cause="verified")
    reloaded = _settings(tmp_path, llm_api_key=FILLED_KEY, llm_api_key_provider="minimax",
                        llm_base_url="https://api.minimax.cn/v1")
    assert reloaded.llm_service_state == LLM_SERVICE_AVAILABLE
    with config_module._llm_service_lock:
        config_module._llm_service_verifications.clear()
    assert reloaded.llm_service_state == LLM_SERVICE_UNVERIFIED


# ── 失败原因要如实，拿不到就说拿不到 ──────────────────────────────────────

def test_failure_detail_names_the_observed_cause(tmp_path):
    settings = _filled(tmp_path)
    record_llm_service_verification(settings, ok=False, cause="key_rejected")
    assert "密钥" in settings.llm_service_status()["detail"]


def test_unknown_failure_cause_says_it_cannot_tell(tmp_path):
    settings = _filled(tmp_path)
    record_llm_service_verification(settings, ok=False, cause="something-else")
    assert settings.llm_service_state == LLM_SERVICE_FAILED
    assert "拿不到更具体的原因" in settings.llm_service_status()["detail"]


# ── 提示里不许出现密钥 ────────────────────────────────────────────────────

@pytest.mark.parametrize("cause", ["key_rejected", "address_unreachable", "timeout", "unknown"])
@pytest.mark.parametrize("ok", [True, False])
def test_service_status_never_leaks_the_key(tmp_path, cause, ok):
    settings = _filled(tmp_path)
    record_llm_service_verification(settings, ok=ok, cause=cause)
    text = " ".join(str(value) for value in settings.llm_service_status().values())
    assert FILLED_KEY not in text
    assert FILLED_KEY not in str(settings.public_status()["llm"])
    rows = " ".join(str(row) for row in settings.safe_runtime_settings()["settings"])
    assert FILLED_KEY not in rows
