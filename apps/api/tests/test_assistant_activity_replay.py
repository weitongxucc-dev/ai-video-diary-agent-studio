from datetime import datetime
import json
import logging
import threading
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.assistant.projection import _PiStreamProjector, public_history_turn
from app.assistant.stream_response import AssistantStreamTextSanitizer, AssistantStreamTransport
from app.assistant.trace import project_turn_trace
from app.conversation_repository import ConversationRepository
from app.schemas import AssistantStreamEvent


def project(projector, kind, ms, **fields):
    return projector.project(json.dumps({"type": kind, "timestamp": ms, **fields}))


def assistant_message(text, **extra):
    return {"role": "assistant", "content": [{"type": "text", "text": text}], **extra}


def test_activity_stream_and_reopened_turn_share_safe_events(tmp_path):
    path = tmp_path / "conversation.sqlite"
    repo = ConversationRepository(path)
    chat = repo.current_or_create("local", "member", "home", "home")
    draft = repo.save_draft(chat["id"], "local", "member", "核查", 0)
    turn, _ = repo.create_turn_idempotent(chat["id"], "local", "member", "核查", "send", draft["draft_version"])
    secret = "FIXTURE_KEY_123456"
    private = "/Users/private/fixture.json"

    def redact(text):
        return text.replace(secret, "[已隐藏凭据]").replace(private, "[已隐藏路径]")

    transport = AssistantStreamTransport(conversations_for=lambda: repo, runtime_lock=threading.Lock(),
        workers=set(), safe_text=lambda text, _: redact(text), known_secrets_for=lambda _: [secret],
        forget_control=lambda _: None, logger=logging.getLogger(__name__))
    api = FastAPI()

    @api.get("/")
    def stream():
        def execute(publish):
            projector = _PiStreamProjector()
            raw = [
                ("agent_start", {}),
                ("message_update", {"assistantMessageEvent": {"type": "thinking_delta", "delta": "PRIVATE_THOUGHT"}}),
                ("message_update", {"assistantMessageEvent": {"type": "text_delta", "delta": f"先检查 {secret}"}}),
                ("message_end", {"message": assistant_message(f"先检查 {secret}")}),
                ("tool_execution_start", {"toolCallId": "sdk-a", "toolName": "preview_typed_edit",
                                          "args": {"command": private}}),
                ("tool_execution_start", {"toolCallId": "sdk-b", "toolName": "apply_typed_edit"}),
                ("tool_execution_update", {"toolCallId": "sdk-b", "toolName": "apply_typed_edit",
                                           "partialResult": {"details": {"progress": 0.4, "secret": secret},
                                                             "content": [{"type": "text", "text": private}]}}),
                ("tool_execution_update", {"toolCallId": "sdk-b", "toolName": "apply_typed_edit",
                                           "partialResult": {"details": {"progress": 80}}}),
                ("tool_execution_end", {"toolCallId": "sdk-a", "toolName": "preview_typed_edit",
                                        "result": {"details": {"kind": "edit_preview",
                                                               "preview": {"proposal": {"status": "proposed"}}}}}),
                ("tool_execution_end", {"toolCallId": "sdk-b", "toolName": "apply_typed_edit",
                                        "result": {"details": {"needs_confirmation": True, "failure": {
                                            "submission_effect": "unknown", "request_id": "original-1",
                                            "query_tool": "read_typed_edit_request"}}}}),
                ("workspace_saved", {"status": "saved", "task_id": "task-spoof", "event_id": "provider-spoof"}),
                ("unknown", {"reason": private}),
                ("error", {"message": f"RAW_PROVIDER_EXCEPTION {secret} {private}"}),
                ("agent_end", {"willRetry": True}),
                ("message_end", {"message": assistant_message("已回读。<think>PRIVATE_FINAL</think>")}),
                ("agent_end", {"willRetry": False}),
            ]
            for index, (kind, fields) in enumerate(raw):
                if event := project(projector, kind, 1_000 + index * 100, **fields):
                    publish(event)
            return SimpleNamespace(model_dump=lambda mode: {"reply": "已回读。"})

        return transport.response(execute, workspace_id="local", turn_id=turn["id"])

    response = TestClient(api).get("/")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    activity = events[1:-2]
    assert [event["type"] for event in activity if event["type"] != "text_delta"] == [
        "agent_start", "tool_start", "tool_start", "tool_progress", "tool_progress", "confirmation_wait",
        "tool_end", "unknown", "error", "assistant_text", "agent_end",
    ]
    assert "".join(event["delta"] for event in activity if event["type"] == "text_delta") == "先检查 [已隐藏凭据]"
    assert all(event["type"] == "text_delta" for event in activity[1:3])
    progress, unmeasured = [event for event in activity if event["type"] == "tool_progress"]
    assert (progress["tool_id"], progress["progress"], progress["at"]) == (2, 0.4, "1970-01-01T00:00:01.600Z")
    assert "progress" not in unmeasured and "summary" not in unmeasured
    waiting = next(event for event in activity if event["type"] == "confirmation_wait")
    assert waiting["tool_id"] == 1 and waiting["message"] == "等待确认后继续。"
    unknown = next(event for event in activity if event["type"] == "tool_end")
    assert unknown["tool_id"] == 2 and unknown["ok"] is False and "activity_label" not in unknown
    assert unknown["failure"] == {
        "submission_effect": "unknown", "request_id": "original-1", "query_tool": "read_typed_edit_request",
    }
    assert activity[-2]["text"] == "已回读。"
    assert "turn_state" not in activity[-1]
    assert "status" not in next(event for event in activity if event["type"] == "error")
    assert "provider-spoof" not in response.text
    assert [event["type"] for event in events[-2:]] == ["turn_status", "result"]
    reopened = ConversationRepository(path).get_turn(turn["id"])
    assert public_history_turn(reopened, redact)["events"] == activity
    for value in (secret, private, "PRIVATE_THOUGHT", "PRIVATE_FINAL", "RAW_PROVIDER_EXCEPTION", "sdk-a", "sdk-b"):
        assert value not in response.text
        assert value not in json.dumps(reopened, ensure_ascii=False)


# The time and call pairs are the OpenFilm reference pack replay-fixture.json
# events. The shell titles are replaced by authorized product tool identities.
def test_openfilm_replay_retains_timed_facts_for_c0_through_c8():
    pairs = [
        (26500, 27500), (31000, 31600), (32000, 33000), (33500, 34500), (35000, 37000),
        (44000, 44400), (46000, 48000), (54000, 60000), *[(63200, 64000)] * 4,
        (75000, 80500), (75000, 80000),
    ]
    raw = [(0, "agent_start", {}), (90000, "agent_end", {})]
    for at, text in [(25000, "先看素材和时间线。"), (28500, "再查项目素材。"), (85000, "分析完成。")]:
        raw.append((at, "message_end", {"message": assistant_message(text)}))
    for number, (start, end) in enumerate(pairs, 1):
        tool = {"toolCallId": f"t{number}", "toolName": "read" if number == 6 else "bash"}
        raw.extend([(start, "tool_execution_start", tool), (end, "tool_execution_end", tool)])
    projector = _PiStreamProjector()
    sanitizer = AssistantStreamTextSanitizer("fixture", known_secrets=[], safe_text=lambda text, _: text)
    events = []
    for at, kind, fields in sorted(raw, key=lambda item: item[0]):
        event = project(projector, kind, at, **fields)
        event = AssistantStreamEvent.model_validate(sanitizer.activity_event(event)).model_dump(exclude_none=True)
        events.append((int(datetime.fromisoformat(event["at"]).timestamp() * 1000), event))

    def observed_at(ms):
        return [(at, event) for at, event in events if at <= ms]

    def facts(ms):
        observed = observed_at(ms)
        starts = {event["tool_id"]: at for at, event in observed if event["type"] == "tool_start"}
        ends = {event["tool_id"]: event["label"] for _, event in observed if event["type"] == "tool_end"}
        return ends, {identifier: ms - starts[identifier] for identifier in starts.keys() - ends.keys()}, ms - observed[-1][0]

    assert facts(3000) == ({}, {}, 3000)  # C0
    assert facts(24000) == ({}, {}, 24000)  # C1
    assert [event["type"] for _, event in observed_at(30000)] == [
        "agent_start", "assistant_text", "tool_start", "tool_end", "assistant_text",
    ]  # C2
    command = "执行任务程序"
    assert facts(36000)[:2] == ({identifier: command for identifier in range(1, 5)}, {5: 1000})  # C3
    assert facts(42000) == ({identifier: command for identifier in range(1, 6)}, {}, 5000)  # C4
    returned, active, _ = facts(54000)  # C5
    assert returned[6] == "读取工作文件" and list(returned.values()).count(command) == 6  # includes t1
    assert active == {8: 0}
    assert facts(63000)[1:] == ({}, 3000)  # C6
    assert facts(66000)[1:] == ({}, 2000)  # C6b
    assert facts(78000)[1] == {13: 3000, 14: 3000}  # C7
    assert facts(80000)[1] == {13: 5000}
    returned, active, _ = facts(92000)  # C8
    assert not active and list(returned.values()).count(command) == 13
    assert [event["type"] for _, event in events[-2:]] == ["assistant_text", "agent_end"]
    assert events[-2][1]["text"] == "分析完成。"
    assert {event["type"] for _, event in events} == {
        "agent_start", "assistant_text", "tool_start", "tool_end", "agent_end",
    }


def test_transport_forwards_committed_save_signal_and_keeps_terminal_error_distinct():
    transport = AssistantStreamTransport(conversations_for=lambda: None, runtime_lock=threading.Lock(),
        workers=set(), safe_text=lambda text, _: text, known_secrets_for=lambda _: [],
        forget_control=lambda _: None, logger=logging.getLogger(__name__))
    api = FastAPI()

    @api.get("/")
    def stream():
        def execute(publish):
            publish({"type": "workspace_saved", "status": "saved", "task_id": "task-1",
                     "event_id": "/private/not-a-receipt.json"})
            publish({"type": "workspace_saved", "status": "saved", "task_id": "task-1"})
            publish({"type": "workspace_saved", "status": "saved", "task_id": "task-1",
                     "event_id": "event-1", "arguments": {"private": "discard"}})
            publish({"type": "error", "message": "供方异常正文", "status": 500})
            raise HTTPException(409, "请重新读回当前任务。")

        return transport.response(execute, workspace_id="local")

    response = TestClient(api).get("/")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events == [
        {"type": "workspace_saved", "status": "saved", "task_id": "task-1", "event_id": "event-1"},
        {"type": "error", "message": "本次助手调用未能正常返回。"},
        {"type": "error", "status": 409, "message": "请重新读回当前任务。"},
    ]


def test_runtime_trace_preserves_measured_tool_duration_and_unknown_status():
    trace = project_turn_trace({"id": "turn", "conversation_id": "conversation", "status": "failed", "sequence": 1,
        "created_at": "1970-01-01T00:00:01Z", "updated_at": "1970-01-01T00:00:01.5Z", "result": {"trace_events": [
        {"type": "tool_start", "at_ms": 1000, "tool_call_id": "sdk-call"},
        {"type": "tool_progress", "at_ms": 1100, "tool_call_id": "sdk-call", "partialResult": "PRIVATE_PARTIAL"},
        {"type": "tool_end", "at_ms": 1500, "tool_call_id": "sdk-call", "duration_ms": 270, "status": "unknown"},
    ]}})
    start, progress, end = trace["events"]
    assert start["tool_call_ref"] == progress["tool_call_ref"] == end["tool_call_ref"]
    assert end["duration_ms"] == 270 and end["status"] == "unknown"
    assert "PRIVATE_PARTIAL" not in json.dumps(trace) and "sdk-call" not in json.dumps(trace)



def test_read_receipts_describe_the_read_instead_of_the_workflow_state():
    projector = _PiStreamProjector()
    task = project(projector, "tool_execution_end", 1000, toolName="read_current_task",
                   result={"details": {"status": "waiting_for_user", "needs_confirmation": True}})
    assert task["type"] == "tool_end" and task["ok"] is True

    file = project(projector, "tool_execution_end", 2000, toolName="read", isError=True,
                   result={"details": {"failure": {
                       "reason": "原生文件路径必须位于 /task", "submission_effect": "unknown",
                   }}})
    assert file["type"] == "tool_end" and file["ok"] is False
    assert file["summary"] == "原生文件路径必须位于 /task"
    assert "submission_effect" not in file["failure"]



def test_stop_release_error_still_finishes_stream_and_persists_failure(tmp_path):
    from app.assistant.turn_control import AssistantProcessReleaseError

    repo = ConversationRepository(tmp_path / "stop.sqlite")
    chat = repo.current_or_create("local", "member", "home", "home")
    draft = repo.save_draft(chat["id"], "local", "member", "停止核查", 0)
    turn, _ = repo.create_turn_idempotent(chat["id"], "local", "member", "停止核查", "send", draft["draft_version"])
    message = "停止已登记，但原生执行的退出与资源释放尚未核实；请回读原请求。"

    def forget(_turn_id):
        raise AssistantProcessReleaseError("private cleanup details")

    transport = AssistantStreamTransport(conversations_for=lambda: repo, runtime_lock=threading.Lock(),
        workers=set(), safe_text=lambda text, _: text, known_secrets_for=lambda _: [],
        forget_control=forget, logger=logging.getLogger(__name__))
    api = FastAPI()

    @api.get("/")
    def stream():
        def execute(_publish):
            raise HTTPException(409, message)

        return transport.response(execute, workspace_id="local", turn_id=turn["id"])

    response = TestClient(api).get("/")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == {"type": "error", "status": 409, "message": message}
    saved = repo.get_turn(turn["id"])
    assert saved["status"] == "failed" and saved["result"]["message"] == message


def test_media_root_refusal_requires_a_typed_authority_failure():
    from app.creation.editing.exact_preview_use_case import _media_root_refusal
    from app.creation.editing.metadata_use_case import EditPreviewRejected
    from app.materials.contracts import MediaAuthorityError

    plain = EditPreviewRejected(422, "重新扫描当前工作区已授权素材根内的文件。")
    assert _media_root_refusal(plain) is None
    wrapped = EditPreviewRejected(422, str(plain))
    wrapped.__cause__ = MediaAuthorityError(reason="asset_outside_authorized_root",
        impact="素材不会被采用。", repair_action="重新扫描已授权素材。")
    assert _media_root_refusal(wrapped) == {
        "error": "edit_media_authorization_rejected", "reason_code": "asset_outside_authorized_root",
        "reason": "服务端拒绝使用当前素材，素材路径超出本次授权范围。",
        "impact": "本次修改未保存。", "repair_action": "重新扫描已授权素材。",
        "submission_effect": "rejected", "retryable": False,
    }
    wrapped.__cause__ = MediaAuthorityError(reason="media_reference_not_registered",
        impact="素材尚未登记。", repair_action="登记后重试。")
    assert _media_root_refusal(wrapped) is None
