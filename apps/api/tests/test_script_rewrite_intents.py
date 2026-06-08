from app.script_rewrite_intents import (
    get_script_rewrite_intent,
    script_rewrite_intents,
    script_rewrite_reason,
)


def test_script_rewrite_intents_include_required_shortcuts():
    intents = script_rewrite_intents()
    assert [intent["id"] for intent in intents] == [
        "sharper_angle",
        "more_conversational",
        "more_real_case",
        "shorter",
        "more_review_like",
    ]
    assert intents[0]["label"] == "更犀利"
    assert all(intent["prompt_hint"] for intent in intents)


def test_script_rewrite_reason_formats_intent_and_section_context():
    reason = script_rewrite_reason(
        "shorter",
        section_title="工具坑点",
        user_note="保留真实案例",
    )

    assert reason == (
        "快捷重写：工具坑点 -> 更短；"
        "压缩到更短、更紧凑，删掉重复解释，保留关键判断。"
        "补充要求：保留真实案例"
    )


def test_script_rewrite_reason_rejects_unknown_intent():
    try:
        get_script_rewrite_intent("unknown")
    except KeyError as exc:
        assert "unknown script rewrite intent" in str(exc)
    else:
        raise AssertionError("unknown rewrite intent should raise KeyError")
