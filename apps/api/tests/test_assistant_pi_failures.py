"""B04：对话失败要说人话。

每一类失败都要有自己的中文说法 + 一个能照做的下一步；
拿不到原因时如实说拿不到；提示里不许出现密钥。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app import config as config_module
from app.assistant_pi import (
    FAILURE_ADDRESS_UNREACHABLE,
    FAILURE_KEY_REJECTED,
    FAILURE_RUNTIME_MISSING,
    FAILURE_TIMEOUT,
    FAILURE_TOOL_FAILED,
    FAILURE_UNKNOWN,
    AssistantUnavailable,
    _classify_output,
    _fail,
    _last_answer_and_preview,
    run_task_turn,
)
from app.config import LLM_SERVICE_AVAILABLE, LLM_SERVICE_FAILED, LLM_SERVICE_UNVERIFIED, Settings

KEY = "private-test-key"


@pytest.fixture(autouse=True)
def _isolated_host_preflight(monkeypatch):
    # These tests classify synthetic model responses, independently of HTTP
    # task reads. Real preflight and object snapshots have separate coverage.
    monkeypatch.setattr("app.assistant_pi._read_task_before_pi", lambda _extension, env, _cwd: {
        "task_id": env["AI_VIDEO_DIARY_AGENT_TASK_ID"], "selected_refs": [],
    })


@pytest.fixture(autouse=True)
def _clear_verifications():
    with config_module._llm_service_lock:
        config_module._llm_service_verifications.clear()
    yield
    with config_module._llm_service_lock:
        config_module._llm_service_verifications.clear()


def _settings(tmp_path, key: str = KEY) -> Settings:
    (tmp_path / "models").mkdir(exist_ok=True)
    return Settings(model_root_path=str(tmp_path / "models"), llm_api_key=key,
                    llm_api_key_provider="minimax")


def _install_fake_pi(monkeypatch, tmp_path):
    fake_pi = tmp_path / "pi"
    fake_pi.write_text("#!/bin/sh\necho 1.0.4\n")
    fake_pi.chmod(0o700)
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(fake_pi))
    return fake_pi


def _turn(monkeypatch, tmp_path, *, returncode=0, stdout="", stderr="", key=KEY):
    _install_fake_pi(monkeypatch, tmp_path)
    settings = _settings(tmp_path, key)

    def fake_run(cmd, **_kwargs):
        if cmd[-1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, stdout=("1.0.4\n" if Path(cmd[0]).name == "pi" else "v24.16.0\n"), stderr="")
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr("app.assistant_pi.subprocess.run", fake_run)
    return run_task_turn(settings, workspace_id="w", task_id="t", message="你好",
                         stage="montage", selected_refs=[], playhead_ms=None)


# ── 分类：日志里真的出现过的才算 ──────────────────────────────────────────

@pytest.mark.parametrize("log,expected", [
    ('{"error":"invalid api key"}', FAILURE_KEY_REJECTED),
    ("HTTP 401 Unauthorized", FAILURE_KEY_REJECTED),
    ("AuthenticationError: no auth credentials", FAILURE_KEY_REJECTED),
    ("Error: getaddrinfo ENOTFOUND api.minimax.io", FAILURE_ADDRESS_UNREACHABLE),
    ("connect ECONNREFUSED 127.0.0.1:443", FAILURE_ADDRESS_UNREACHABLE),
    ("read timeout after 180s", FAILURE_TIMEOUT),
    ('{"type":"tool_execution_end","isError":true}', FAILURE_TOOL_FAILED),
    ("一切正常，就是没下文", FAILURE_UNKNOWN),
    ("", FAILURE_UNKNOWN),
])
def test_classification_reads_the_log_not_the_exit_code(log, expected):
    assert _classify_output(log) == expected


def test_stderr_is_also_read():
    assert _classify_output("", "401 Unauthorized") == FAILURE_KEY_REJECTED


def test_key_rejection_wins_over_a_merely_slow_log():
    assert _classify_output("401 invalid api key\nread timeout") == FAILURE_KEY_REJECTED


# ── 四类失败：文案 + 下一步 + 不含密钥 ────────────────────────────────────

def test_key_rejection_says_the_service_refused_the_key(monkeypatch, tmp_path):
    with pytest.raises(AssistantUnavailable) as caught:
        _turn(monkeypatch, tmp_path, returncode=1, stdout='{"error":"invalid api key"}')
    text = str(caught.value)
    assert "密钥" in text
    assert "下一步" in text
    assert caught.value.cause == FAILURE_KEY_REJECTED
    assert KEY not in text


def test_unreachable_address_is_not_blamed_on_the_key(monkeypatch, tmp_path):
    with pytest.raises(AssistantUnavailable) as caught:
        _turn(monkeypatch, tmp_path, returncode=1, stderr="getaddrinfo ENOTFOUND api.minimax.io")
    text = str(caught.value)
    assert "服务地址" in text
    assert "不是密钥的问题" in text
    assert "下一步" in text
    assert caught.value.cause == FAILURE_ADDRESS_UNREACHABLE


def test_timeout_offers_retry_then_address_check(monkeypatch, tmp_path):
    def fake_run(cmd, **_kwargs):
        if cmd[-1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, stdout=("1.0.4\n" if Path(cmd[0]).name == "pi" else "v24.16.0\n"), stderr="")
        raise subprocess.TimeoutExpired(cmd, 180)

    _install_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setattr("app.assistant_pi.subprocess.run", fake_run)
    with pytest.raises(AssistantUnavailable) as caught:
        run_task_turn(_settings(tmp_path), workspace_id="w", task_id="t", message="你好",
                      stage="montage", selected_refs=[], playhead_ms=None)
    text = str(caught.value)
    assert "超时" in text
    assert "任务和时间线没有因此改变" in text
    assert "重试" in text
    assert caught.value.cause == FAILURE_TIMEOUT


def test_missing_pi_runtime_is_its_own_class(monkeypatch, tmp_path):
    # 让三条发现途径都落空：环境变量、PATH、nvm 目录。
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(tmp_path / "nope"))
    monkeypatch.setattr("app.assistant_pi.shutil.which", lambda _name, **_kwargs: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    with pytest.raises(AssistantUnavailable) as caught:
        run_task_turn(_settings(tmp_path), workspace_id="w", task_id="t", message="你好",
                      stage="montage", selected_refs=[], playhead_ms=None)
    text = str(caught.value)
    assert caught.value.cause == FAILURE_RUNTIME_MISSING
    assert "没碰到密钥和网络" in text
    assert "npm install -g @earendil-works/pi-coding-agent" in text
    assert "22.19" in text
    assert "@pi/cli" not in text
    assert KEY not in text


def test_missing_extension_points_at_the_business_tool(monkeypatch, tmp_path):
    _install_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXTENSION", str(tmp_path / "gone.ts"))
    with pytest.raises(AssistantUnavailable) as caught:
        run_task_turn(_settings(tmp_path), workspace_id="w", task_id="t", message="你好",
                      stage="montage", selected_refs=[], playhead_ms=None)
    assert caught.value.cause == FAILURE_RUNTIME_MISSING
    assert "业务工具" in str(caught.value)


def test_business_tool_failure_is_not_reported_as_a_key_problem(monkeypatch, tmp_path):
    log = json.dumps({"type": "tool_execution_end", "toolName": "preview_edit",
                      "isError": True}) + "\n"
    with pytest.raises(AssistantUnavailable) as caught:
        _turn(monkeypatch, tmp_path, returncode=1, stdout=log)
    text = str(caught.value)
    assert caught.value.cause == FAILURE_TOOL_FAILED
    assert "本轮业务操作未完成" in text
    assert "工具记录中的具体阻断" in text
    assert "密钥" not in text


def test_unknown_failure_admits_it_cannot_tell():
    text = str(_fail(FAILURE_UNKNOWN))
    assert "拿不到更具体的原因" in text
    assert "下一步" in text


def test_incomplete_reply_without_a_reason_still_honours_the_old_wording():
    with pytest.raises(AssistantUnavailable) as caught:
        _last_answer_and_preview('{"type":"agent_settled"}')
    text = str(caught.value)
    assert "完整回复" in text
    assert "拿不到更具体的原因" in text


def test_incomplete_reply_with_a_reason_uses_that_reason():
    log = "\n".join(json.dumps(item, ensure_ascii=False) for item in [
        {"type": "error", "message": "401 Unauthorized: invalid api key"},
        {"type": "message_end", "message": {"role": "assistant",
         "stopReason": "error", "content": []}},
    ])
    with pytest.raises(AssistantUnavailable) as caught:
        _last_answer_and_preview(log)
    assert caught.value.cause == FAILURE_KEY_REJECTED
    assert "密钥" in str(caught.value)


def test_unreadable_event_says_so_instead_of_a_fake_reason():
    with pytest.raises(AssistantUnavailable) as caught:
        _last_answer_and_preview("这不是 JSON")
    assert caught.value.cause == FAILURE_UNKNOWN
    assert "无法读取" in str(caught.value)


@pytest.mark.parametrize("stdout,stderr", [
    ('{"error":"invalid api key"}', ""),
    ("", "401 Unauthorized"),
    ("", "getaddrinfo ENOTFOUND api.minimax.io"),
    ("", "read timeout"),
    ("not json at all", "something odd"),
    ('{"error":"invalid api key: ' + KEY + '"}', ""),
])
def test_no_failure_text_echoes_the_raw_log_or_the_key(monkeypatch, tmp_path, stdout, stderr):
    with pytest.raises(AssistantUnavailable) as caught:
        _turn(monkeypatch, tmp_path, returncode=1, stdout=stdout, stderr=stderr)
    text = str(caught.value)
    assert KEY not in text
    assert not stdout or stdout not in text
    assert not stderr or stderr not in text
    assert "下一步" in text


# ── 什么才能写进服务状态 ──────────────────────────────────────────────────

def test_a_real_rejection_marks_the_service_failed(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    assert settings.llm_service_status("w")["state"] == LLM_SERVICE_UNVERIFIED
    with pytest.raises(AssistantUnavailable):
        _turn(monkeypatch, tmp_path, returncode=1, stdout='{"error":"invalid api key"}')
    assert settings.llm_service_status("w")["state"] == LLM_SERVICE_FAILED
    assert settings.llm_service_status("w")["cause"] == FAILURE_KEY_REJECTED


def test_a_missing_runtime_does_not_mark_the_service_failed(monkeypatch, tmp_path):
    """服务压根没被问过，不能因此说它坏了——这正是 B03 要修的那种谎。"""
    settings = _settings(tmp_path)
    _install_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXTENSION", str(tmp_path / "gone.ts"))
    with pytest.raises(AssistantUnavailable):
        run_task_turn(settings, workspace_id="w", task_id="t", message="你好",
                      stage="montage", selected_refs=[], playhead_ms=None)
    assert settings.llm_service_state == LLM_SERVICE_UNVERIFIED


def test_a_business_tool_failure_does_not_mark_the_service_failed(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    log = json.dumps({"type": "tool_execution_end", "toolName": "preview_edit",
                      "isError": True}) + "\n"
    with pytest.raises(AssistantUnavailable):
        _turn(monkeypatch, tmp_path, returncode=1, stdout=log)
    assert settings.llm_service_state == LLM_SERVICE_UNVERIFIED


def test_a_successful_turn_marks_the_service_available(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    answer, _preview = _turn(monkeypatch, tmp_path, stdout="\n".join([
        json.dumps({"type": "message_end", "message": {"role": "assistant",
                    "stopReason": "stop", "content": [{"type": "text", "text": "收到。"}]}}),
        '{"type":"agent_settled"}',
    ]))
    assert answer == "收到。"
    assert settings.llm_service_status("w")["state"] == LLM_SERVICE_AVAILABLE


@pytest.mark.parametrize("version,ready", [("v22.18.0", False), ("v22.19.0", True), ("v24.16.0", True)])
def test_node_requirement_uses_the_same_path_as_the_launcher(monkeypatch, tmp_path, version, ready):
    from app.assistant_pi import pi_runtime_checks
    pi = _install_fake_pi(monkeypatch, tmp_path)
    seen = {}
    def which(name, path=None):
        assert name == "node"
        seen["path"] = path
        return str(tmp_path / "node")
    monkeypatch.setattr("app.assistant_pi.shutil.which", which)
    monkeypatch.setattr("app.assistant_pi.subprocess.run", lambda cmd, **kwargs:
                        subprocess.CompletedProcess(cmd, 0, stdout=("1.0.4" if Path(cmd[0]).name == "pi" else version), stderr=""))
    checks = {c["id"]: c for c in pi_runtime_checks()}
    assert seen["path"].split(__import__("os").pathsep)[0] == str(pi.parent)
    assert (checks["pi_node"]["status"] == "ready") == ready
    assert checks["pi"]["version"] == ("1.0.4" if ready else None)


def test_node_missing_blocks_before_provider_and_does_not_change_saved_key(monkeypatch, tmp_path):
    _install_fake_pi(monkeypatch, tmp_path)
    monkeypatch.setattr("app.assistant_pi.shutil.which", lambda *args, **kwargs: None)
    settings = _settings(tmp_path)
    with pytest.raises(AssistantUnavailable) as caught:
        run_task_turn(settings, workspace_id="w", task_id="t", message="你好",
                      stage="montage", selected_refs=[], playhead_ms=None)
    assert caught.value.cause == FAILURE_RUNTIME_MISSING
    assert not caught.value.service_contacted
    assert "Node.js" in str(caught.value)
    assert settings.effective_llm_api_key == KEY
    assert settings.llm_service_status("w")["state"] == LLM_SERVICE_UNVERIFIED


def test_missing_business_tool_import_has_reinstall_action_not_pi_install(monkeypatch, tmp_path):
    from app.assistant_pi import pi_runtime_checks
    _install_fake_pi(monkeypatch, tmp_path)
    extension = tmp_path / "diary_tools.ts"
    extension.write_text("// initial test fixture, missing required sibling")
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXTENSION", str(extension))
    checks = {c["id"]: c for c in pi_runtime_checks()}
    assert checks["pi_tools"]["status"] == "missing"
    assert "retrieval.ts" in checks["pi_tools"]["repair_action"]
    assert checks["pi_tools"]["fix_command"] is None



def test_pi_launch_check_failure_is_not_a_provider_key_rejection(monkeypatch, tmp_path):
    _install_fake_pi(monkeypatch, tmp_path)
    settings = _settings(tmp_path)
    def failed_pi_version(cmd, **kwargs):
        assert cmd[-1] == "--version"  # No conversation request reached the provider.
        if Path(cmd[0]).name == "pi":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="401 private-test-key")
        return subprocess.CompletedProcess(cmd, 0, stdout="v24.16.0", stderr="")
    monkeypatch.setattr("app.assistant_pi.subprocess.run", failed_pi_version)
    with pytest.raises(AssistantUnavailable) as caught:
        run_task_turn(settings, workspace_id="w", task_id="t", message="你好",
                      stage="montage", selected_refs=[], playhead_ms=None)
    assert caught.value.cause == FAILURE_RUNTIME_MISSING
    assert not caught.value.service_contacted
    assert KEY not in str(caught.value)
    assert settings.llm_service_status("w")["state"] == LLM_SERVICE_UNVERIFIED
