from app.assistant.trace import project_turn_trace, project_usage_ledger
from app.conversation_repository import ConversationRepository


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


def test_persisted_success_failure_cancel_and_retry_receipts_are_read_back(tmp_path):
    repo = ConversationRepository(tmp_path / 'usage.sqlite3')
    chat = repo.create('w', 'm', 'home', 'home')
    for index, status in enumerate(('completed', 'failed', 'stopped', 'completed'), 1):
        draft = repo.save_draft(chat['id'], 'w', 'm', f'instruction-{index}', repo.get(chat['id'], 'w', 'm')['draft_version'])
        turn, created = repo.create_turn_idempotent(chat['id'], 'w', 'm', f'instruction-{index}',
            f'request-{index}', draft['draft_version'])
        assert created
        repo.finish_turn(turn['id'], status, result={
            'provider': 'minimax', 'model': 'MiniMax-M3',
            'usage_status': 'known' if index in {1, 2, 4} else 'unknown',
            'provider_usage': {'input': index, 'output': 1, 'totalTokens': index + 1} if index in {1, 2, 4} else None,
            'usage_details': {'provider': 'minimax', 'model': 'MiniMax-M3',
                'input_tokens': index if index in {1, 2, 4} else None,
                'output_tokens': 1 if index in {1, 2, 4} else None,
                'latency_ms': 100 + index if index in {1, 2, 4} else None,
                'estimated_cost': None},
            'trace_events': ([{'type': 'retry', 'at_ms': 10}] if index == 4 else []),
        })
    restarted = ConversationRepository(tmp_path / 'usage.sqlite3')
    saved = restarted.list_turns(chat['id'])
    assert [project_turn_trace(turn)['status'] for turn in saved] == ['completed', 'failed', 'stopped', 'completed']
    assert project_turn_trace(saved[1])['input_tokens'] == 2
    assert project_turn_trace(saved[2])['status'] == 'stopped'
    assert project_turn_trace(saved[3])['retry_count'] == 1
    ledger = project_usage_ledger(saved)
    assert ledger['usage_status_counts'] == {'known': 3, 'estimated': 0, 'unknown': 1}
    assert ledger['measured_usage'] == {'input': 7, 'output': 3, 'totalTokens': 10}

    # Read through the real adapter after reopening; it must enforce the
    # existing owned-conversation seam and never rewrite receipts.
    from app.assistant.conversation_routes import ConversationHTTPAdapter
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.testclient import TestClient
    def owned(request, conversation_id):
        if request.headers.get('x-fixture-member') != 'm':
            raise HTTPException(403)
        return 'w', 'm', restarted.get(conversation_id, 'w', 'm')
    adapter = ConversationHTTPAdapter(conversations_for=lambda: restarted,
        owned_conversation=owned, **{key: None for key in ('workspace_for', 'member_for',
        'validate_scope', 'clean_text', 'validate_reference', 'recover_request', 'require_actor',
        'segment_for', 'restore_for', 'input_context', 'preflight', 'render_plan')})
    app = FastAPI()
    @app.get('/api/assistant/turns/{turn_id}/trace')
    def trace(turn_id: str, request: Request):
        return adapter.trace(turn_id, request)
    client = TestClient(app)
    for turn in saved:
        response = client.get(f"/api/assistant/turns/{turn['id']}/trace", headers={'x-fixture-member': 'm'})
        assert response.status_code == 200
        assert response.json()['usage_details'] == project_turn_trace(turn)['usage_details']
        assert response.json()['usage_ledger'] == ledger
    assert client.get(f"/api/assistant/turns/{saved[0]['id']}/trace").status_code == 403
    assert restarted.list_turns(chat['id']) == saved


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


def test_compaction_usage_is_only_a_nonzero_legacy_fallback():
    empty = {"input": 0, "output": 0, "cacheWrite": 0, "totalTokens": 0}
    measured = {"input": 10, "output": 3, "cacheRead": 0, "totalTokens": 13}
    compaction = {"type": "context_compaction", "status": "completed", "trigger": "threshold"}
    turn = {"id": "turn-compaction", "conversation_id": "conversation-1", "status": "completed",
            "sequence": 1, "created_at": "now", "updated_at": "now"}

    for result, usage_samples, expected in (
        ({"usage_status": "unknown"}, [measured], ("unknown", None, None)),
        ({"usage_status": "unknown"}, [empty], ("unknown", None, None)),
        ({}, [empty], ("unknown", None, None)),
        ({}, [empty, measured], ("known", "compaction", measured)),
        ({"usage_status": "known", "provider_usage": empty}, [measured], ("known", "provider", empty)),
        ({"usage_status": "estimated", "provider_usage": {"totalTokens": 5}}, [measured],
         ("estimated", "local_estimate", {"totalTokens": 5})),
    ):
        trace = project_turn_trace({**turn, "result": result, "events": [
            {**compaction, "provider_usage": usage} for usage in usage_samples]})
        assert (trace["usage_status"], trace["usage_source"], trace["provider_usage"]) == expected
        assert trace["estimated_cost"] is None
        assert trace["compaction_count"] == len(usage_samples)
        assert trace["events"] == [{**compaction, "failure_origin": None}] * len(usage_samples)


def test_project_turn_trace_exposes_structured_usage_without_inventing_cost_or_latency():
    trace = project_turn_trace({
        "id": "turn-details", "conversation_id": "conversation-1", "status": "failed", "sequence": 1,
        "created_at": "now", "updated_at": "now", "events": [], "result": {
            "provider": "minimax", "model": "MiniMax-M3", "usage_status": "known",
            "provider_usage": {"input": 7, "output": 3, "totalTokens": 10},
            "usage_details": {"provider": "minimax", "model": "MiniMax-M3", "input_tokens": 7,
                "output_tokens": 3, "latency_ms": 450, "estimated_cost": {"amount": 0.003, "currency": "USD", "source": "sdk_catalog", "status": "estimated"}},
        },
    })
    assert trace["provider"] == "minimax"
    assert trace["model"] == "MiniMax-M3"
    assert trace["input_tokens"] == 7 and trace["output_tokens"] == 3
    assert trace["latency_ms"] == 450
    assert trace["estimated_cost"] == {"amount": 0.003, "currency": "USD", "source": "sdk_catalog", "status": "estimated"}

    unknown = project_turn_trace({
        "id": "turn-unknown-details", "conversation_id": "conversation-1", "status": "stopped", "sequence": 2,
        "created_at": "now", "updated_at": "now", "events": [], "result": {
            "provider": "minimax", "model": "MiniMax-M3", "usage_status": "unknown",
            "usage_details": {"provider": "minimax", "model": "MiniMax-M3", "input_tokens": None,
                "output_tokens": None, "latency_ms": None, "estimated_cost": None},
        },
    })
    assert unknown["usage_status"] == "unknown"
    assert unknown["input_tokens"] is None and unknown["estimated_cost"] is None
 
def test_stopped_turn_keeps_unknown_receipt_and_redacts_provider_secret():
    trace = project_turn_trace({
        "id": "turn-stopped", "conversation_id": "conversation-1", "status": "stopped", "sequence": 4,
        "created_at": "2026-11-17T04:25:26Z", "updated_at": "2026-11-17T04:25:27Z",
        "events": [], "result": {
            "provider": "openai", "model": "gpt-5.4", "usage_status": "unknown",
            "provider_usage": None, "usage_details": {
                "provider": "openai", "model": "gpt-5.4", "input_tokens": None,
                "output_tokens": None, "latency_ms": None, "estimated_cost": None,
            },
            "trace_events": [
                {"type": "agent_end", "event_id": "event-1", "status": "cancelled",
                 "references": {"request_id": "request-1"}},
            ],
            "provider_api_key": "sk-secret-must-not-appear",
        },
    })
    assert trace["status"] == "stopped"
    assert trace["usage_status"] == "unknown"
    assert trace["provider_usage"] is None
    assert trace.get("input_tokens") is None
    assert "sk-secret-must-not-appear" not in str(trace)


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


def test_chained_usage_requires_both_invocations_to_report_it():
    from app.assistant.trace import append_runtime_receipt

    first = {"session_id": "session", "usage_status": "known", "provider_usage": {"totalTokens": 8},
             "trace_events": [{"type": "turn_end", "step": 1}]}
    following = {"session_id": "session", "trace_events": [{"type": "turn_start", "step": 1}]}
    result = append_runtime_receipt(first, following)
    assert result["usage_status"] == "unknown" and result["provider_usage"] is None
    assert [event["step"] for event in result["trace_events"]] == [1, 2]
    result = append_runtime_receipt(first, {**following, "usage_status": "known", "provider_usage": {"totalTokens": 3}})
    assert result["provider_usage"] == {"totalTokens": 11}


def test_usage_merge_and_invalid_cost_do_not_misattribute_or_leak():
    from app.assistant.trace import append_runtime_receipt, project_usage_details
    first = {'session_id': 's', 'provider': 'one', 'model': 'a', 'usage_status': 'known',
        'provider_usage': {'input': 3, 'output': 1}, 'usage_details': {'latency_ms': 100,
        'estimated_cost': {'amount': 0.01, 'currency': 'USD', 'source': 'sdk_catalog', 'status': 'estimated'}}}
    following = {**first, 'provider': 'two', 'model': 'b', 'usage_details': {'latency_ms': 200}}
    merged = append_runtime_receipt(first, following)
    assert merged['provider'] is None and merged['model'] is None
    assert merged['usage_details']['input_tokens'] == 6
    assert merged['usage_details']['latency_ms'] == 300
    assert merged['usage_details']['estimated_cost'] is None
    assert project_usage_details({'usage_details': {'latency_ms': True, 'estimated_cost': {
        'amount': float('nan'), 'private': 'secret'}}}) == {
        'provider': None, 'model': None, 'input_tokens': None, 'output_tokens': None,
        'latency_ms': None, 'estimated_cost': None}


def test_sdk_usage_receipt_survives_business_projection_success_and_failure():
    import json
    import pytest
    from app.assistant.result_projection import read_sdk_result
    from app.assistant.failures import AssistantUnavailable
    result = {'type': 'creation_session_result', 'protocol': 1, 'owner': 'pi-coding-agent-sdk',
        'status': 'settled', 'answer': 'done', 'tool_results': [], 'provider': 'minimax', 'model': 'M3',
        'usage_status': 'known', 'provider_usage': {'input': 2, 'output': 1},
        'usage_details': {'latency_ms': 120, 'estimated_cost': None}}
    answer, receipt = read_sdk_result(json.dumps(result))
    assert answer == 'done' and receipt['usage_details']['latency_ms'] == 120
    assert receipt['provider'] == 'minimax'
    with pytest.raises(AssistantUnavailable) as caught:
        read_sdk_result(json.dumps({**result, 'status': 'failed', 'answer': '', 'failure': 'synthetic error'}))
    assert caught.value.runtime_receipt['usage_details'] == receipt['usage_details']
    assert caught.value.runtime_receipt['provider_usage'] == {'input': 2, 'output': 1}
