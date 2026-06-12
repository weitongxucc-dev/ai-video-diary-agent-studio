from app.prompt_golden_eval import (
    PromptGoldenCase,
    build_ai_video_diary_golden_cases,
    evaluate_prompt_golden_case,
    evaluate_prompt_golden_cases,
)


def test_ai_video_diary_prompt_golden_cases_pass():
    cases = build_ai_video_diary_golden_cases()

    results = evaluate_prompt_golden_cases(cases)

    assert {case.name for case in cases} == {
        "topic_generation_creator_voice",
        "script_generation_creator_voice",
        "section_rewrite_creator_voice",
    }
    assert [result for result in results if not result.passed] == []


def test_prompt_golden_eval_reports_missing_required_terms():
    result = evaluate_prompt_golden_case(
        PromptGoldenCase(
            name="broken_prompt",
            system_prompt="只输出 JSON。",
            user_prompt="{}",
            required_system_terms=("创作者口吻",),
            required_user_terms=("creator_profile", "preference_memory"),
        )
    )

    assert result.passed is False
    assert "missing system term: 创作者口吻" in result.failures
    assert "missing user term: creator_profile" in result.failures
    assert "missing user term: preference_memory" in result.failures


def test_prompt_golden_eval_accepts_semantic_term_groups():
    result = evaluate_prompt_golden_case(
        PromptGoldenCase(
            name="quality_prompt",
            system_prompt="开头 3 秒要有明确判断，内容要像真实案例，不要营销腔。",
            user_prompt=(
                "creator_profile 用来稳定口播语气。"
                "preference_memory.prefer 会影响下一轮推荐。"
                "source_refs 的 ref 必须完全等于 source_items。"
            ),
            required_system_term_groups=(
                ("明确冲突", "明确判断"),
                ("真实案例", "实测场景"),
                ("不要营销腔", "广告感"),
            ),
            required_user_term_groups=(
                ("creator_profile", "创作者画像"),
                ("preference_memory.prefer", "偏好学习"),
                ("source_refs", "来源引用"),
                ("source_items", "素材来源"),
            ),
        )
    )

    assert result.passed is True
    assert result.failures == ()


def test_prompt_golden_eval_reports_missing_semantic_term_group():
    result = evaluate_prompt_golden_case(
        PromptGoldenCase(
            name="thin_quality_prompt",
            system_prompt="只输出 JSON。",
            user_prompt="{}",
            required_system_term_groups=(("明确冲突", "明确判断"),),
            required_user_term_groups=(("source_refs", "来源引用"),),
        )
    )

    assert result.passed is False
    assert "missing system term group: 明确冲突 / 明确判断" in result.failures
    assert "missing user term group: source_refs / 来源引用" in result.failures
