from app.assistant.trace import project_turn_trace


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
                {"step": 1, "type": "turn_start", "at_ms": 1000},
                {"step": 2, "type": "tool_start", "at_ms": 1100, "tool_call_id": "call-1", "tool_name": "read"},
                {"step": 3, "type": "tool_end", "at_ms": 1300, "tool_call_id": "call-1", "tool_name": "read",
                 "status": "failed", "failure_class": "business_tool_failed",
                 "references": {"request_id": "request-1", "job_id": "job-1"}},
                {"step": 4, "type": "compaction_start", "at_ms": 1400, "status": "started"},
                {"step": 5, "type": "compaction_end", "at_ms": 1600, "status": "completed"},
                {"step": 6, "type": "retry", "at_ms": 1700, "status": "started"},
                {"step": 7, "type": "nested_tool", "at_ms": 1800, "tool_call_id": "nested-1",
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
