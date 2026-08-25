import json
from datetime import date

import httpx

from app.config import Settings
from app.content_templates import get_content_template
from app.llm import DeepSeekScriptGenerator, TemplateScriptGenerator
from app.models import (
    FeedbackEvent,
    FeedbackValue,
    ScriptPackage,
    ScriptSection,
    SourceRef,
    TopicCandidate,
)
from app.repository import EpisodeRepository
from app.services import EpisodeService
from app.sources import ObsidianVault
from app.topics import DeepSeekTopicGenerator, TemplateTopicGenerator


def test_episode_service_passes_content_template_to_generators():
    class CapturingTopicGenerator:
        def __init__(self) -> None:
            self.template_id = None

        def generate(self, day_number, feedback_events=None, template=None):
            self.template_id = template.template_id if template else None
            return TemplateTopicGenerator().generate(
                day_number,
                feedback_events,
                template=template,
            )

    class CapturingScriptGenerator:
        def __init__(self) -> None:
            self.generate_template_id = None
            self.section_template_id = None

        def generate(self, topic, day_number, feedback_events=None, template=None):
            self.generate_template_id = template.template_id if template else None
            return TemplateScriptGenerator().generate(
                topic,
                day_number,
                feedback_events,
                template=template,
            )

        def regenerate_section(
            self,
            topic,
            current_script,
            section_id,
            reason=None,
            feedback_events=None,
            template=None,
        ):
            self.section_template_id = template.template_id if template else None
            return TemplateScriptGenerator().regenerate_section(
                topic,
                current_script,
                section_id,
                reason=reason,
                feedback_events=feedback_events,
                template=template,
            )

    repo = EpisodeRepository(today_provider=lambda: date(2026, 6, 7))
    episode = repo.create()
    episode.content_template_id = "tool_review"
    topic_generator = CapturingTopicGenerator()
    script_generator = CapturingScriptGenerator()
    service = EpisodeService(
        topic_generator=topic_generator,
        script_generator=script_generator,
    )

    service.generate_topics(episode)
    service.approve_topic(episode, episode.topics[0].id)
    service.generate_script(episode)
    service.regenerate_script_section(episode, "point_1", reason="更像实测")

    assert topic_generator.template_id == "tool_review"
    assert script_generator.generate_template_id == "tool_review"
    assert script_generator.section_template_id == "tool_review"


def test_deepseek_topic_prompt_includes_content_template_context(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "tool.md").write_text(
        "# Composer 实测\n\nComposer 能把文章整理成结构化脚本，但中文表达有时发卡。",
        encoding="utf-8",
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "topics": [
                                        {
                                            "title": "Composer 实测：适合创作者的 AI 吗？",
                                            "core_viewpoint": "它适合整理素材，但不适合完全替代判断。",
                                            "reason": "来自 Composer 实测笔记。",
                                            "audience": "AI 内容创作者",
                                            "outline": ["工具是什么", "真实体验", "推荐边界"],
                                            "score": 90,
                                            "source_refs": [
                                                {
                                                    "source_type": "Obsidian",
                                                    "title": "Composer 实测",
                                                    "ref": "tool.md",
                                                }
                                            ],
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    generator = DeepSeekTopicGenerator(
        Settings(deepseek_api_key="secret", obsidian_vault_path=str(vault)),
        ObsidianVault(vault),
        transport=httpx.MockTransport(handler),
    )

    generator.generate(day_number=7, template=get_content_template("tool_review"))

    system_prompt = captured["payload"]["messages"][0]["content"]
    user_prompt = json.loads(captured["payload"]["messages"][1]["content"])
    assert "AI 工具测评" in system_prompt
    assert "一句话结论" in system_prompt
    assert "score 评分标准" in system_prompt
    assert "真实案例支撑度" in system_prompt
    assert user_prompt["content_template"]["template_id"] == "tool_review"
    assert "推荐判断" in user_prompt["content_template"]["default_feedback_dimensions"]


def test_deepseek_topic_prompt_includes_preference_memory(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "agent.md").write_text(
        "# Agent 复盘\n\n真实项目里的 Agent 工作流比模型参数更重要。",
        encoding="utf-8",
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "topics": [
                                        {
                                            "title": "真实项目里的 Agent 工作流",
                                            "core_viewpoint": "工作流比模型参数更能形成差异。",
                                            "reason": "来自 Agent 复盘笔记。",
                                            "audience": "AI 产品经理",
                                            "outline": ["真实项目", "踩坑", "方法"],
                                            "score": 92,
                                            "source_refs": [
                                                {
                                                    "source_type": "Obsidian",
                                                    "title": "Agent 复盘",
                                                    "ref": "agent.md",
                                                }
                                            ],
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    generator = DeepSeekTopicGenerator(
        Settings(deepseek_api_key="secret", obsidian_vault_path=str(vault)),
        ObsidianVault(vault),
        transport=httpx.MockTransport(handler),
    )

    generator.generate(
        day_number=7,
        feedback_events=[
            FeedbackEvent("topic", "topic_1", FeedbackValue.UP, "多讲真实项目"),
            FeedbackEvent("script_section", "section_1", FeedbackValue.UP, "多讲真实项目"),
            FeedbackEvent("topic", "topic_2", FeedbackValue.DOWN, "太像概念课"),
        ],
        template=get_content_template("video_diary"),
    )

    user_prompt = json.loads(captured["payload"]["messages"][1]["content"])
    assert user_prompt["preference_memory"]["prefer"][0] == {
        "signal": "多讲真实项目",
        "evidence_count": 2,
        "confidence": 0.65,
        "enabled": True,
    }
    assert user_prompt["preference_memory"]["avoid"][0]["signal"] == "太像概念课"
    assert "优先遵守 preference_memory" in user_prompt["requirements"]


def test_deepseek_script_prompt_includes_template_rules_and_keeps_hook_separate():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "hook": "这个工具我试完以后，反而更谨慎了。",
                                    "sections": [
                                        {
                                            "id": "why",
                                            "title": "为什么测试它",
                                            "estimated_seconds": 24,
                                            "content": "我测试它是因为很多人把它当成万能写作工具。",
                                        },
                                        {
                                            "id": "point_1",
                                            "title": "工具是什么",
                                            "estimated_seconds": 30,
                                            "content": "它可以把素材整理成结构化草稿。",
                                        },
                                        {
                                            "id": "point_2",
                                            "title": "实测体验",
                                            "estimated_seconds": 36,
                                            "content": "实际用下来，中文语气需要人工改很多。",
                                        },
                                        {
                                            "id": "point_3",
                                            "title": "推荐边界",
                                            "estimated_seconds": 30,
                                            "content": "适合整理，不适合直接发布。",
                                        },
                                        {
                                            "id": "ending",
                                            "title": "结尾",
                                            "estimated_seconds": 18,
                                            "content": "工具能提速，但判断仍然要自己做。",
                                        },
                                    ],
                                    "title_options": ["Composer 实测", "这个 AI 工具别直接发"],
                                    "cover_copy": "能整理，别全信",
                                    "publish_copy": "今天聊 Composer 的真实体验。",
                                    "comment_prompt": "你用 AI 写作时，最常改的是哪一部分？",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    generator = DeepSeekScriptGenerator(
        Settings(deepseek_api_key="secret"),
        transport=httpx.MockTransport(handler),
    )
    topic = TopicCandidate(
        id="topic_1",
        title="Composer 实测：适合创作者的 AI 吗？",
        core_viewpoint="它适合整理素材，但不适合完全替代判断。",
        reason="来自 Composer 实测笔记。",
        audience="AI 内容创作者",
        outline=["工具是什么", "真实体验", "推荐边界"],
        score=90,
        source_refs=[SourceRef("Obsidian", "Composer 实测", "tool.md")],
    )

    script = generator.generate(
        topic,
        day_number=7,
        template=get_content_template("tool_review"),
        target_duration_seconds=300,
    )

    system_prompt = captured["payload"]["messages"][0]["content"]
    user_prompt = json.loads(captured["payload"]["messages"][1]["content"])
    assert script.hook == "这个工具我试完以后，反而更谨慎了。"
    assert "AI 工具测评" in system_prompt
    assert "工具名必须出现" in system_prompt
    assert "目标时长为 300 秒" in system_prompt
    assert "hook 是独立字段" in user_prompt["requirements"]
    assert "约 300 秒" in user_prompt["requirements"][0]
    assert user_prompt["content_template"]["script_structure"][0] == "结论先行"


def test_deepseek_script_prompt_includes_preference_memory():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "hook": "先讲一个真实项目里的坑。",
                                    "sections": [
                                        {
                                            "id": "why",
                                            "title": "为什么聊",
                                            "estimated_seconds": 20,
                                            "content": "这来自一次真实项目复盘。",
                                        },
                                        {
                                            "id": "point_1",
                                            "title": "真实案例",
                                            "estimated_seconds": 30,
                                            "content": "先讲现场发生了什么。",
                                        },
                                        {
                                            "id": "point_2",
                                            "title": "判断",
                                            "estimated_seconds": 30,
                                            "content": "再讲我为什么这么判断。",
                                        },
                                        {
                                            "id": "point_3",
                                            "title": "方法",
                                            "estimated_seconds": 30,
                                            "content": "最后给一个可执行方法。",
                                        },
                                        {
                                            "id": "ending",
                                            "title": "结尾",
                                            "estimated_seconds": 18,
                                            "content": "别只看概念，要看现场。",
                                        },
                                    ],
                                    "title_options": ["真实项目里的 Agent 坑"],
                                    "cover_copy": "别只讲概念",
                                    "publish_copy": "今天聊一次真实项目。",
                                    "comment_prompt": "你遇到过类似问题吗？",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    generator = DeepSeekScriptGenerator(
        Settings(deepseek_api_key="secret"),
        transport=httpx.MockTransport(handler),
    )
    topic = TopicCandidate(
        id="topic_1",
        title="真实项目里的 Agent 工作流",
        core_viewpoint="工作流比模型参数更重要。",
        reason="来自真实复盘。",
        audience="AI 产品经理",
        outline=["真实项目", "踩坑", "方法"],
        score=92,
        source_refs=[SourceRef("Obsidian", "Agent 复盘", "agent.md")],
    )

    generator.generate(
        topic,
        day_number=7,
        feedback_events=[
            FeedbackEvent("topic", "topic_1", FeedbackValue.UP, "多讲真实项目"),
            FeedbackEvent("script_section", "section_1", FeedbackValue.DOWN, "少讲抽象概念"),
        ],
        template=get_content_template("video_diary"),
    )

    user_prompt = json.loads(captured["payload"]["messages"][1]["content"])
    assert user_prompt["preference_memory"]["prefer"][0]["signal"] == "多讲真实项目"
    assert user_prompt["preference_memory"]["avoid"][0]["signal"] == "少讲抽象概念"


def test_deepseek_section_prompt_includes_template_and_neighbor_context():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "id": "point_1",
                                    "title": "工具是什么",
                                    "estimated_seconds": 30,
                                    "content": "这一段改成更像真实上手体验。",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    generator = DeepSeekScriptGenerator(
        Settings(deepseek_api_key="secret"),
        transport=httpx.MockTransport(handler),
    )
    topic = TopicCandidate(
        id="topic_1",
        title="Composer 实测",
        core_viewpoint="它适合整理素材，但不适合完全替代判断。",
        reason="来自真实试用。",
        audience="AI 内容创作者",
        outline=["工具是什么", "真实体验", "推荐边界"],
        score=90,
        source_refs=[SourceRef("Obsidian", "Composer 实测", "tool.md")],
    )
    current_script = ScriptPackage(
        id="script_1",
        hook="这个工具我试完以后，反而更谨慎了。",
        sections=[
            ScriptSection("why", "为什么测试它", 24, "很多人都在推荐它。"),
            ScriptSection("point_1", "工具是什么", 30, "旧内容。"),
            ScriptSection("point_2", "实测体验", 36, "中文语气需要改。"),
        ],
        title_options=["标题"],
        cover_copy="封面",
        publish_copy="发布",
        comment_prompt="评论",
    )

    section = generator.regenerate_section(
        topic,
        current_script,
        "point_1",
        reason="更像实测",
        template=get_content_template("tool_review"),
    )

    user_prompt = json.loads(captured["payload"]["messages"][1]["content"])
    assert section.content == "这一段改成更像真实上手体验。"
    assert user_prompt["content_template"]["template_id"] == "tool_review"
    assert user_prompt["script_context"]["prev_section_tail"] == "很多人都在推荐它。"
    assert user_prompt["script_context"]["next_section_head"] == "中文语气需要改。"
    assert user_prompt["target_section"]["role_in_script"] == "核心论证第 1 段"
