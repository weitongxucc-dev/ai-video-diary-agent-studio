"""Pi event parsing and task-turn receipts with isolated runtime doubles."""

from assistant_sdk_fixtures import sdk_completed
from pathlib import Path
from assistant_sdk_fixtures import project_event_fixture
import json
import sys

import pytest

import app.assistant_pi as pi
from app.config import Settings


def receipt(status="queued"):
    return {
        "job_id":"job-current", "status":status, "result_kind":"edit_preview",
        "result_url":"/api/creation-tasks/task-current/media-jobs/job-current",
        "device_request_id":None, "waiting_reason":"resource_pressure",
        "base_version_id":"version-current", "result":None,
        "failure_reason":None, "repair_action":None,
    }


def events(details, model_reply="预览已完成，修改已应用。", *, failed=False):
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in [
        {"type":"tool_execution_end","toolName":"preview_typed_edit","isError":failed,
         "result":{"details":details}},
        {"type":"message_end","message":{"role":"assistant","stopReason":"stop",
         "content":[{"type":"text","text":model_reply}]}},
        {"type":"agent_settled"},
    ])


@pytest.mark.parametrize("status", ["queued", "running", "interrupted", "succeeded"])
def test_pending_media_job_overrides_model_success_claim_and_preserves_receipt(status):
    job = receipt(status)
    if status == "succeeded":
        job["result"] = {"proposal":{"id":"stored-result"}}
    details = {"kind":"task_media_job","media_job":job}
    reply, result = project_event_fixture(events(details))
    assert result == {"kind":"media_job","media_job":job}
    assert "已安排，预览尚未完成" in reply
    assert "修改已应用" not in reply and "预览已完成" not in reply
    assert pi._safe_tool_result_summary("preview_typed_edit", {"details":details}, False) == reply


def test_immediate_proposal_keeps_business_preview_and_confirmation_reply():
    preview = {"proposal":{
        "id":"proposal-current", "summary":"实际改为0.7倍速",
        "operation":{"operation_type":"ChangeClipSpeed"},
        "preview":{"before":{"detail":"原速度"},"after":{"detail":"0.7倍速"}},
        "invalidated_refs":[], "external_effects":[],
    }}
    reply, result = project_event_fixture(events({"kind":"edit_preview","preview":preview}))
    assert result == preview
    assert "实际改为0.7倍速" in reply and "待确认，尚未应用修改" in reply
    assert "修改前：原速度" in reply and "修改后：0.7倍速" in reply
    assert "预览尚未完成" not in reply


def test_run_task_turn_returns_media_job_without_restarting_model(monkeypatch, tmp_path):
    calls = []
    job = receipt()
    monkeypatch.setattr(pi, "pi_runtime_checks", lambda: [])
    monkeypatch.setattr("app.assistant.runtime_context.sdk_module", lambda _: Path("/synthetic/index.js"))
    monkeypatch.setattr(pi, "_pi_executable", lambda: sys.executable)
    monkeypatch.setattr(pi, "_configured_model", lambda _: ("https://synthetic.invalid", "fixture", ""))
    monkeypatch.setattr(pi, "_agent_root", lambda *_: tmp_path / "agent")
    monkeypatch.setattr(pi, "_record_service", lambda *_, **__: None)
    monkeypatch.setattr(pi, "_read_task_before_pi", lambda _extension, env, _cwd: {
        "task_id":env["AI_VIDEO_DIARY_AGENT_TASK_ID"], "selected_refs":[],
    })
    monkeypatch.setattr(pi, "_read_memory_before_pi", lambda *_: {
        "revision":0,"common_expressions":[],"banned_words":[],"style_preferences":{},
    })

    def fake_stream(cmd, **kwargs):
        calls.append(json.loads(kwargs["message"]))
        return sdk_completed(cmd, 0, stdout=events({"kind":"task_media_job","media_job":job}), stderr="")

    monkeypatch.setattr(pi, "_run_pi_streaming", fake_stream)
    reply, result = pi.run_task_turn(
        Settings(model_root_path=str(tmp_path / "models")), workspace_id="workspace-current",
        task_id="task-current", conversation_id="conversation-current",
        message="把选中镜头速度改为0.7倍，保留时间槽和旁白", stage="montage", selected_refs=[], playhead_ms=None,
        caller_user_id="member-current", turn_id="trusted-turn",
    )
    assert result == {"kind":"media_job","media_job":job}
    assert "已安排，预览尚未完成" in reply
    assert len(calls) == 1
    prompt = calls[0]["systemPrompt"]
    assert "未知结果先查原请求" in prompt
    assert "相同失败不重放" in prompt and "无进展不盲循环" in prompt
    assert "preview_edit" not in calls[0]["tools"]


@pytest.mark.parametrize("status", ["result_unknown", "rejected", "not_submitted"])
def test_request_failure_keeps_original_locator_instead_of_model_success(status):
    locator = {"kind":"edit_request_status", "status":status, "phase":"preview", "task_id":"task-current",
        "request_id":"11111111-1111-5111-8111-111111111111", "query_url":"/original-request?phase=preview",
        "assistant_turn_id":"trusted-turn", "base_version_id":"version-current", "base_sequence":4,
        "failure_reason":"实际拒绝或回执未知"}
    reply, result = project_event_fixture(events(locator, failed=True))
    assert result == locator
    assert "修改已应用" not in reply and "预览已完成" not in reply
    expected = {"result_unknown":"不重复提交", "rejected":"请求已被拒绝", "not_submitted":"请求未提交"}
    assert expected[status] in reply
