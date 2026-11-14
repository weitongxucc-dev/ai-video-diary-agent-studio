from app.assistant.trace import project_turn_trace, project_usage_ledger


def test_project_usage_ledger_keeps_measured_estimated_and_unknown_separate():
    ledger = project_usage_ledger([
        {'result': {'usage_status': 'known', 'provider_usage': {'input': 2, 'totalTokens': 3}}},
        {'result': {'usage_status': 'estimated', 'provider_usage': {'input': 9, 'totalTokens': 9}}},
        {'result': {'usage_status': 'unknown'}},
    ])
    assert ledger == {'turn_count': 3,
        'usage_status_counts': {'known': 1, 'estimated': 1, 'unknown': 1},
        'measured_usage': {'input': 2, 'totalTokens': 3},
        'estimated_usage': {'input': 9, 'totalTokens': 9}}


def test_project_turn_trace_redacts_text_and_aggregates_known_usage():
    trace = project_turn_trace({
        "id": "turn-1", "conversation_id": "conversation-1", "status": "completed", "sequence": 2,
        "created_at": "2026-11-13T00:00:00Z", "updated_at": "2026-11-13T00:00:01Z",
        "events": [
            {"type": "text_delta", "delta": "private prompt"},
            {"type": "tool_start", "tool_id": 1, "label": "读取素材", "arguments": {"token": "secret"}},
            {"type": "tool_end", "tool_id": 1, "label": "读取素材", "ok": False,
             "failure": {"error": "business_tool_failed", "reason": "secret", "submission_effect": "not_submitted"}},
            {"type": "context_compaction", "status": "completed", "trigger": "threshold",
             "provider_usage": {"input": 10, "output": 3, "totalTokens": 13}},
        ],
    })
    assert trace["usage_status"] == "known"
    assert trace["provider_usage"] == {"input": 10, "output": 3, "totalTokens": 13}
    assert trace["tool_counts"] == {"读取素材": 1}
    assert all("private prompt" not in str(item) and "secret" not in str(item) for item in trace["events"])


def test_project_turn_trace_marks_missing_provider_usage_unknown():
    trace = project_turn_trace({
        "id": "turn-2", "conversation_id": "conversation-1", "status": "running", "sequence": 3,
        "created_at": "now", "updated_at": "now", "events": [],
    })
    assert trace["usage_status"] == "unknown"
    assert trace["provider_usage"] is None


def test_project_turn_trace_paginates_older_events_without_changing_usage_or_associations():
    turn = {
        "id": "turn-page", "conversation_id": "conversation-1", "status": "completed", "sequence": 1,
        "created_at": "now", "updated_at": "now",
        "events": [{"type": "tool_start", "tool_id": index, "label": f"工具{index}"} for index in range(5)],
        "result": {"usage_status": "known", "provider_usage": {"input": 4, "output": 2, "totalTokens": 6}},
    }
    latest = project_turn_trace(turn, limit=2)
    assert latest["event_count"] == 5
    assert latest["events_returned"] == 2
    assert latest["next_before"] == 3
    older = project_turn_trace(turn, before=latest["next_before"], limit=2)
    assert [event["tool_id"] for event in older["events"]] == [1, 2]
    assert older["next_before"] == 1
    assert older["provider_usage"] == latest["provider_usage"]


def test_project_turn_trace_paginates_runtime_events_beyond_default_page():
    turn = {
        "id": "turn-runtime-page", "conversation_id": "conversation-1", "status": "completed", "sequence": 1,
        "created_at": "now", "updated_at": "now",
        "result": {"trace_events": [
            {"type": "retry", "event_id": f"event-{index}", "at_ms": index}
            for index in range(205)
        ]},
        "events": [],
    }
    latest = project_turn_trace(turn)
    assert latest["event_count"] == 205
    assert latest["events_returned"] == 200
    assert latest["next_before"] == 5
    oldest = project_turn_trace(turn, before=5)
    assert oldest["events_returned"] == 5
    assert all(len(event["event_id"]) == 20 for event in oldest["events"])
    assert oldest["events"][0]["event_id"] != latest["events"][0]["event_id"]


def test_project_turn_trace_correlates_redacted_tool_business_job_and_output_refs():
    trace = project_turn_trace({
        "id": "turn-3", "conversation_id": "conversation-1", "status": "completed", "sequence": 1,
        "created_at": "now", "updated_at": "now", "result": {
            "usage_status": "known", "provider_usage": {"input": 4, "output": 2, "totalTokens": 6},
            "continuation": {"status": "succeeded"},
        }, "events": [{"type": "tool_start", "tool_id": 1, "tool_call_id": "call-secret"},
            {"type": "tool_end", "tool_id": 1, "tool_call_id": "call-secret", "ok": True,
             "trace_references": {"request_id": "request-secret", "job_id": "job-secret",
                                   "output_reference": "output-secret"}},
            {"type": "evidence_candidate", "reviewer_kind": "visual_reviewer",
             "candidate_id": "candidate-secret", "status": "pending", "source_refs": ["output-secret"]}],
    })
    assert trace["usage_source"] == "provider"
    assert trace["business_status"] == "succeeded"
    assert {item["kind"] for item in trace["associations"]} == {"conversation", "turn", "tool_call", "business_request", "job", "output"}
    assert all(len(item["ref"]) == 20 for item in trace["associations"])
    assert trace["evidence_candidates"][0]["reviewer_kind"] == "visual_reviewer"
    assert "request-secret" not in str(trace)


def test_project_turn_trace_orders_runtime_fixture_with_duration_failure_retry_and_compaction():
    trace = project_turn_trace({
        "id": "turn-runtime", "conversation_id": "conversation-runtime", "status": "failed",
        "sequence": 1, "created_at": "now", "updated_at": "now", "result": {
            "usage_status": "known", "provider_usage": {"input": 7, "output": 3, "totalTokens": 10},
            "trace_events": [
                {"step": 1, "event_id": "session:1", "type": "turn_start", "at_ms": 1000},
                {"step": 2, "event_id": "session:2", "type": "tool_start", "at_ms": 1100, "tool_call_id": "call-1", "tool_name": "read"},
                {"step": 3, "event_id": "session:3", "parent_event_id": "session:2", "type": "tool_end", "at_ms": 1300, "tool_call_id": "call-1", "tool_name": "read",
                 "status": "failed", "failure_class": "business_tool_failed",
                 "references": {"request_id": "request-1", "job_id": "job-1"}},
                {"step": 4, "event_id": "session:4", "type": "compaction_start", "at_ms": 1400, "status": "started"},
                {"step": 5, "event_id": "session:5", "parent_event_id": "session:4", "type": "compaction_end", "at_ms": 1600, "status": "completed"},
                {"step": 6, "event_id": "session:6", "type": "retry", "at_ms": 1700, "status": "started"},
                {"step": 7, "event_id": "session:7", "parent_event_id": "session:3", "type": "nested_tool", "at_ms": 1800, "tool_call_id": "nested-1",
                 "tool_name": "read_job", "status": "succeeded", "references": {"output_id": "output-1"}},
            ],
        }, "events": [],
    })
    assert trace["started_at"].endswith("00:00:01+00:00")
    assert trace["ended_at"].endswith("00:00:01.800000+00:00")
    assert trace["duration_ms"] == 800
    assert trace["failure_class"] == "business_tool_failed"
    assert trace["retry_count"] == 1
    assert [event["type"] for event in trace["events"]] == [
        "turn_start", "tool_start", "tool_end", "compaction_start", "compaction_end", "retry", "nested_tool"]
    assert trace["events"][2]["duration_ms"] == 200
    assert len(trace["events"][2]["event_id"]) == 20
    assert len(trace["events"][2]["parent_event_id"]) == 20
    assert all("request-1" not in str(event) for event in trace["events"])
    assert {item["kind"] for item in trace["associations"]} >= {"business_request", "job", "output"}
    assert trace["usage_status"] == "known"


def test_project_turn_trace_preserves_estimated_usage_as_estimated():
    trace = project_turn_trace({
        "id": "turn-estimated", "conversation_id": "conversation-1", "status": "completed", "sequence": 1,
        "created_at": "now", "updated_at": "now", "events": [], "result": {
            "usage_status": "estimated", "provider_usage": {"input": 9, "output": 2, "totalTokens": 11},
        },
    })
    assert trace["usage_status"] == "estimated"
    assert trace["usage_source"] == "local_estimate"
