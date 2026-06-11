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
