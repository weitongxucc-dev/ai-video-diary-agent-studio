from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.content_templates import get_content_template
from app.creator_profile import CreatorProfile
from app.llm import DeepSeekScriptGenerator
from app.models import (
    FeedbackEvent,
    FeedbackValue,
    ScriptPackage,
    ScriptSection,
    SourceRef,
    TopicCandidate,
)
from app.sources import ObsidianVault, TopicSourceItem
from app.topics import DeepSeekTopicGenerator


@dataclass(frozen=True)
class PromptGoldenCase:
    name: str
    system_prompt: str
    user_prompt: str
    required_system_terms: tuple[str, ...] = field(default_factory=tuple)
    required_user_terms: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PromptGoldenResult:
    name: str
    passed: bool
    failures: tuple[str, ...] = field(default_factory=tuple)


def evaluate_prompt_golden_cases(
    cases: list[PromptGoldenCase],
) -> list[PromptGoldenResult]:
    return [evaluate_prompt_golden_case(case) for case in cases]


def evaluate_prompt_golden_case(case: PromptGoldenCase) -> PromptGoldenResult:
    failures: list[str] = []
    for term in case.required_system_terms:
        if term not in case.system_prompt:
            failures.append(f"missing system term: {term}")
    for term in case.required_user_terms:
        if term not in case.user_prompt:
            failures.append(f"missing user term: {term}")
    return PromptGoldenResult(
        name=case.name,
        passed=not failures,
        failures=tuple(failures),
    )


def build_ai_video_diary_golden_cases() -> list[PromptGoldenCase]:
    template = get_content_template("tool_review")
    profile = _creator_profile()
    feedback_events = _feedback_events()
    topic = _topic_candidate()
    script = _script_package()
    settings = Settings(deepseek_api_key="golden")

    topic_generator = DeepSeekTopicGenerator(
        settings,
        ObsidianVault(Path(".")),
    )
    script_generator = DeepSeekScriptGenerator(settings)

    return [
        PromptGoldenCase(
            name="topic_generation_creator_voice",
            system_prompt=topic_generator._system_prompt(template),
            user_prompt=topic_generator._user_prompt(
                day_number=7,
                source_items=_source_items(),
                feedback_events=feedback_events,
                template=template,
                creator_profile=profile,
            ),
            required_system_terms=(
                "中文 AI 视频日记选题 Agent",
                "真实场景",
                "source_refs 只能使用",
            ),
            required_user_terms=(
                "creator_profile",
                "preference_memory",
                "先讲真实问题",
                "家人们",
                "多讲真实项目",
                "太像营销文案",
                "tool_review",
                "source_refs",
            ),
        ),
        PromptGoldenCase(
            name="script_generation_creator_voice",
            system_prompt=script_generator._system_prompt(template),
            user_prompt=script_generator._user_prompt(
                topic,
                day_number=7,
                feedback_events=feedback_events,
                template=template,
                creator_profile=profile,
            ),
            required_system_terms=(
                "中文 AI 视频日记口播编导",
                "不要营销腔",
                "不要使用动物",
            ),
            required_user_terms=(
                "creator_profile",
                "preference_memory",
                "先讲真实问题",
                "家人们",
                "多讲真实项目",
                "太像营销文案",
                "封面文案",
                "发布文案",
            ),
        ),
        PromptGoldenCase(
            name="section_rewrite_creator_voice",
            system_prompt=script_generator._section_system_prompt(template),
            user_prompt=script_generator._section_user_prompt(
                topic,
                script,
                script.sections[1],
                reason="更像真实踩坑",
                feedback_events=feedback_events,
                template=template,
                creator_profile=profile,
            ),
            required_system_terms=(
                "只重写用户指定段落",
                "真人日记感",
                "不要营销腔",
            ),
            required_user_terms=(
                "target_section",
                "script_context",
                "creator_profile",
                "preference_memory",
                "只重写 target_section",
                "先讲真实问题",
                "家人们",
            ),
        ),
    ]


def _creator_profile() -> CreatorProfile:
    return CreatorProfile(
        identity="技术型个人创作者",
        content_domains=["AI 产品", "自动化", "Agent 工作流"],
        tone_keywords=["直接", "真实", "少营销腔"],
        preferred_phrases=["先讲真实问题"],
        banned_phrases=["家人们", "太炸裂了"],
        good_examples=["我今天踩到一个具体的坑。"],
        bad_examples=["这个神器太炸裂了。"],
    )


def _feedback_events() -> list[FeedbackEvent]:
    return [
        FeedbackEvent("topic", "topic_good", FeedbackValue.UP, "多讲真实项目"),
        FeedbackEvent("script_section", "section_bad", FeedbackValue.DOWN, "太像营销文案"),
    ]


def _source_items() -> list[TopicSourceItem]:
    return [
        TopicSourceItem(
            source_type="Obsidian",
            title="Agent 复盘",
            ref="agent-review.md",
            excerpt="真实项目里的 Agent 工作流比模型参数更重要。",
        )
    ]


def _topic_candidate() -> TopicCandidate:
    return TopicCandidate(
        id="topic_golden",
        title="Agent 工作流为什么比模型更像护城河",
        core_viewpoint="真正的差异来自可复用工作流，而不是单次模型演示。",
        reason="来自真实项目复盘。",
        audience="AI 产品经理和个人创作者",
        outline=["真实问题", "工作流判断", "落地边界"],
        score=92,
        source_refs=[SourceRef("Obsidian", "Agent 复盘", "agent-review.md")],
    )


def _script_package() -> ScriptPackage:
    return ScriptPackage(
        id="script_golden",
        hook="今天聊一个真实判断：Agent 的护城河不是模型。",
        sections=[
            ScriptSection("hook_body", "开场延展", 15, "先讲我踩到的真实问题。"),
            ScriptSection("point_1", "第一个判断", 35, "模型能力会变平，流程会沉淀。"),
            ScriptSection("point_2", "第二个判断", 35, "工作流能记录真实协作边界。"),
            ScriptSection("point_3", "第三个判断", 35, "反馈闭环会积累个人方法。"),
            ScriptSection("ending", "结尾", 20, "所以别只盯模型，先把流程跑顺。"),
        ],
        title_options=["Agent 护城河不是模型"],
        cover_copy="Agent 护城河\n不是模型",
        publish_copy="今天聊 Agent 工作流里的真实判断。",
        comment_prompt="你更看重模型能力还是工作流？",
    )
