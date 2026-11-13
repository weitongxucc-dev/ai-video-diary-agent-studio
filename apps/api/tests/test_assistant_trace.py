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
