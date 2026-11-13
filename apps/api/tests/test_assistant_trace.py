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
