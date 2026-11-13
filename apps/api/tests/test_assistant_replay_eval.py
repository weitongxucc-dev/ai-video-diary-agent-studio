from app.assistant.replay_eval import evaluate_replay


def test_replay_eval_covers_selection_failure_cancel_unknown_recovery_and_final_state():
    events = [
        {"type": "tool_start", "tool_name": "读取素材"},
        {"type": "tool_end", "tool_name": "读取素材", "ok": False,
         "failure": {"error": "business_tool_failed", "submission_effect": "unknown"}},
        {"type": "turn_stopped", "failure": {"error": "aborted"}},
        {"type": "business_result", "status": "succeeded"},
    ]
    result = evaluate_replay(events, {"tool_names": ["读取素材"], "failed": True,
                                      "cancelled": True, "unknown_result": True,
                                      "recovered": True, "final_status": "succeeded"})
    assert result["passed"]
    assert all(result["checks"].values())


def test_replay_eval_keeps_unknown_result_distinct_from_failure_without_receipt():
    result = evaluate_replay([{"type": "tool_end", "label": "写入", "ok": False,
                               "failure": {"submission_effect": "not_submitted"}}],
                             {"tool_names": ["写入"], "failed": True,
                              "unknown_result": False})
    assert result["passed"]
