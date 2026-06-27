import json

import httpx

from app.config import Settings
from app.llm import DeepSeekScriptGenerator, LLMResponseError
from app.models import ScriptPackage, ScriptSection, SourceRef, TopicCandidate


def test_deepseek_script_generator_requests_json_chat_completion():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "hook": "开头来自 DeepSeek",
                                    "sections": [
                                        {
                                            "id": "hook",
                                            "title": "开头 3 秒钩子",
                                            "estimated_seconds": 12,
                                            "content": "这是 DeepSeek 生成的开头。",
                                        },
                                        {
                                            "id": "why",
                                            "title": "为什么今天聊这个",
                                            "estimated_seconds": 24,
                                            "content": "这是 DeepSeek 生成的背景。",
                                        },
                                        {
                                            "id": "point_1",
                                            "title": "原因 1",
                                            "estimated_seconds": 36,
                                            "content": "这是 DeepSeek 生成的原因一。",
                                        },
                                        {
                                            "id": "point_2",
                                            "title": "原因 2",
                                            "estimated_seconds": 36,
                                            "content": "这是 DeepSeek 生成的原因二。",
                                        },
                                        {
                                            "id": "point_3",
                                            "title": "原因 3",
                                            "estimated_seconds": 36,
                                            "content": "这是 DeepSeek 生成的原因三。",
                                        },
                                    ],
                                    "title_options": ["标题 A", "标题 B", "标题 C"],
                                    "cover_copy": "封面文案",
                                    "publish_copy": "发布文案",
                                    "comment_prompt": "评论引导",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    generator = DeepSeekScriptGenerator(
        Settings(deepseek_api_key="secret", deepseek_model="deepseek-v4-flash"),
        transport=httpx.MockTransport(handler),
    )
    topic = TopicCandidate(
        id="topic_1",
        title="为什么拍照识别卡路里的软件很难长期活下去？",
        core_viewpoint="识别能力只是入口，信任、习惯和付费价值才决定生存。",
        reason="来自真实知识库证据。",
        audience="AI 产品经理",
        outline=["识别不等于信任", "习惯难养成", "付费价值弱"],
        score=90,
        source_refs=[SourceRef("Obsidian", "用户习惯", "[[用户习惯]]")],
    )

    script = generator.generate(topic, day_number=1)
    user_payload = json.loads(captured["payload"]["messages"][1]["content"])
    requirements_text = "\n".join(user_payload["requirements"])

    assert script.hook == "开头来自 DeepSeek"
    assert script.sections[0].content == "这是 DeepSeek 生成的开头。"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer secret"
    assert captured["payload"]["model"] == "deepseek-v4-flash"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["max_tokens"] >= 4200
    assert "只输出 JSON" in captured["payload"]["messages"][0]["content"]
    assert "800-1000" in captured["payload"]["messages"][0]["content"]
    assert "800-1000" in requirements_text
    assert "4-6 分钟" in requirements_text
    assert "每段至少" in requirements_text
    assert "真实细节" in requirements_text


def test_template_script_generator_produces_longform_draft():
    from app.llm import TemplateScriptGenerator

    topic = TopicCandidate(
        id="topic_1",
        title="为什么原创课程会被问版权归属？",
        core_viewpoint="版权不只是署名问题，而是交付链路、合同边界和素材来源共同决定。",
        reason="客户在项目复盘里询问课程版权归属，暴露了创作者交付前没有说清权利边界。",
        audience="技术型个人创作者",
        outline=["先讲真实冲突", "拆开版权边界", "给出下次交付动作"],
        score=90,
        source_refs=[SourceRef("Obsidian", "课程交付复盘", "[[课程交付复盘]]")],
    )

    script = TemplateScriptGenerator().generate(topic, day_number=12)
    full_text = script.hook + "".join(section.content for section in script.sections)

    assert 800 <= len(full_text) <= 1000
    assert len(script.sections) >= 6
    assert all(len(section.content) >= 95 for section in script.sections)
    assert script.sections[-1].title == "行动建议"


def test_settings_status_reports_deepseek_without_exposing_key():
    settings = Settings(deepseek_api_key="secret", deepseek_model="deepseek-v4-flash")

    status = settings.public_status()

    assert status["llm"]["provider"] == "deepseek"
    assert status["llm"]["configured"] is True
    assert status["llm"]["model"] == "deepseek-v4-flash"
    assert "secret" not in json.dumps(status, ensure_ascii=False)


def test_deepseek_script_generator_regenerates_one_section():
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
                                    "title": "原因 1",
                                    "estimated_seconds": 36,
                                    "content": "这是重新生成后的单段内容。",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    generator = DeepSeekScriptGenerator(
        Settings(deepseek_api_key="secret", deepseek_model="deepseek-v4-flash"),
        transport=httpx.MockTransport(handler),
    )
    topic = TopicCandidate(
        id="topic_1",
        title="AI 工具为什么越多越难用？",
        core_viewpoint="选择成本和流程断裂才是核心问题。",
        reason="来自真实知识库证据。",
        audience="AI 创作者",
        outline=["选择成本", "流程断裂", "回到任务"],
        score=90,
        source_refs=[SourceRef("Obsidian", "工具选择", "[[工具选择]]")],
    )
    current_script = ScriptPackage(
        id="script_1",
        hook="旧开头",
        sections=[ScriptSection("point_1", "原因 1", 36, "旧内容")],
        title_options=["标题"],
        cover_copy="封面",
        publish_copy="发布",
        comment_prompt="评论",
    )

    section = generator.regenerate_section(
        topic,
        current_script,
        "point_1",
        reason="更像日记",
    )

    assert section.id == "point_1"
    assert section.content == "这是重新生成后的单段内容。"
    assert "只输出 JSON" in captured["payload"]["messages"][0]["content"]
    assert "更像日记" in captured["payload"]["messages"][1]["content"]


def test_deepseek_script_generator_recovers_fenced_section_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "```json\n"
                                '{"id":"point_1","title":"原因 1",'
                                '"estimated_seconds":24,'
                                '"content":"保留真实例子，但表达更短。"}'
                                "\n```"
                            )
                        }
                    }
                ]
            },
        )

    generator = DeepSeekScriptGenerator(
        Settings(deepseek_api_key="secret", deepseek_model="deepseek-v4-flash"),
        transport=httpx.MockTransport(handler),
    )
    current_script = ScriptPackage(
        id="script_1",
        hook="旧开头",
        sections=[ScriptSection("point_1", "原因 1", 36, "旧内容")],
        title_options=["标题"],
        cover_copy="封面",
        publish_copy="发布",
        comment_prompt="评论",
    )

    section = generator.regenerate_section(
        TopicCandidate(
            id="topic_1",
            title="AI 工具为什么越多越难用？",
            core_viewpoint="选择成本和流程断裂才是核心问题。",
            reason="来自真实知识库证据。",
            audience="AI 创作者",
            outline=["选择成本", "流程断裂", "回到任务"],
            score=90,
            source_refs=[],
        ),
        current_script,
        "point_1",
    )

    assert section.content == "保留真实例子，但表达更短。"
    assert section.estimated_seconds == 24


def test_deepseek_script_generator_wraps_incomplete_section_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"id":"point_1"}'}}]},
        )

    generator = DeepSeekScriptGenerator(
        Settings(deepseek_api_key="secret", deepseek_model="deepseek-v4-flash"),
        transport=httpx.MockTransport(handler),
    )
    current_script = ScriptPackage(
        id="script_1",
        hook="旧开头",
        sections=[ScriptSection("point_1", "原因 1", 36, "旧内容")],
        title_options=["标题"],
        cover_copy="封面",
        publish_copy="发布",
        comment_prompt="评论",
    )

    try:
        generator.regenerate_section(
            TopicCandidate(
                id="topic_1",
                title="AI 工具为什么越多越难用？",
                core_viewpoint="选择成本和流程断裂才是核心问题。",
                reason="来自真实知识库证据。",
                audience="AI 创作者",
                outline=["选择成本", "流程断裂", "回到任务"],
                score=90,
                source_refs=[],
            ),
            current_script,
            "point_1",
        )
    except LLMResponseError as exc:
        assert "DeepSeek section JSON must contain content" in str(exc)
    else:
        raise AssertionError("incomplete section JSON should raise LLMResponseError")
