from __future__ import annotations

import json

import httpx

from app.config import Settings
from app.schemas import SameStyleReferenceJobRequest
from app.same_style_script_generator import DeepSeekSameStyleScriptGenerator
from app.same_style_script_generator import TemplateSameStyleScriptGenerator
from app.same_style_script_generator import same_style_script_generator_from_settings


def test_template_same_style_script_generator_keeps_product_and_transcript_context():
    draft = TemplateSameStyleScriptGenerator().generate(
        SameStyleReferenceJobRequest(
            reference_url="benchmark.mp4",
            product_name="AI视频日记",
            product_brief="创作者一站式视频生产工具",
        ),
        transcript="三秒看懂这个工具，先看结果再看过程。",
        shot_manifest=[
            {
                "start_ms": 0,
                "end_ms": 3000,
                "text": "三秒看懂这个工具",
                "purpose": "口播节奏参考",
            }
        ],
    )

    assert "AI视频日记" in draft["hook"]
    assert "三秒看懂这个工具" in draft["hook"]
    assert draft["title_options"][0].startswith("AI视频日记")


def test_deepseek_same_style_script_generator_requests_structured_json():
    captured_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "hook": "先别急着划走，AI视频日记能把爆款结构变成你的版本。",
                                    "sections": [
                                        {
                                            "title": "结果前置",
                                            "estimated_seconds": 10,
                                            "content": "先呈现成片效率变化。",
                                        },
                                        {
                                            "title": "场景替换",
                                            "estimated_seconds": 24,
                                            "content": "把原片卖点换成创作者真实工作台。",
                                        },
                                    ],
                                    "title_options": ["AI视频日记爆款复刻"],
                                    "cover_copy": "同款结构\nAI视频日记",
                                    "publish_copy": "用自己的产品复刻爆款节奏。",
                                    "comment_prompt": "你想先复刻哪条爆款？",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    generator = DeepSeekSameStyleScriptGenerator(
        Settings(deepseek_api_key="test-key", deepseek_model="deepseek-test"),
        transport=httpx.MockTransport(handler),
    )

    draft = generator.generate(
        SameStyleReferenceJobRequest(
            reference_url="https://www.douyin.com/video/123",
            product_name="AI视频日记",
            product_brief="创作者一站式视频生产工具",
            rewrite_mode="auto",
        ),
        transcript="这个工具三秒就能看懂。",
        shot_manifest=[
            {
                "start_ms": 0,
                "end_ms": 3000,
                "text": "这个工具三秒就能看懂",
                "purpose": "口播节奏参考",
            }
        ],
    )

    assert draft["hook"].startswith("先别急着划走")
    assert draft["sections"][0]["title"] == "结果前置"
    assert captured_payloads[0]["model"] == "deepseek-test"
    assert captured_payloads[0]["response_format"] == {"type": "json_object"}
    user_prompt = captured_payloads[0]["messages"][1]["content"]
    assert "保留对标视频的镜头结构、转化逻辑和节奏" in user_prompt
    assert "不要复制原视频原文案" in user_prompt


def test_same_style_script_generator_from_settings_uses_deepseek_when_configured():
    assert isinstance(
        same_style_script_generator_from_settings(Settings(deepseek_api_key="key")),
        DeepSeekSameStyleScriptGenerator,
    )
    assert isinstance(
        same_style_script_generator_from_settings(Settings(deepseek_api_key=None)),
        TemplateSameStyleScriptGenerator,
    )
