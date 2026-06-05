from __future__ import annotations

import json
from typing import Protocol
from uuid import uuid4

import httpx

from app.config import Settings
from app.models import FeedbackEvent, ScriptPackage, ScriptSection, TopicCandidate


class LLMConfigurationError(RuntimeError):
    pass


class LLMResponseError(RuntimeError):
    pass


class ScriptGenerator(Protocol):
    def generate(
        self,
        topic: TopicCandidate,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptPackage:
        ...

    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptSection:
        ...


class MissingScriptGenerator:
    def generate(
        self,
        topic: TopicCandidate,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptPackage:
        raise LLMConfigurationError("DEEPSEEK_API_KEY is not configured")

    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptSection:
        raise LLMConfigurationError("DEEPSEEK_API_KEY is not configured")


class TemplateScriptGenerator:
    def generate(
        self,
        topic: TopicCandidate,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptPackage:
        return ScriptPackage(
            id=f"script_{uuid4().hex[:6]}",
            hook=f"今天这个选题我想讲一个真实判断：{topic.core_viewpoint}",
            sections=[
                ScriptSection("hook", "开头 3 秒钩子", 12, f"{topic.title} 这件事，表面看是技术问题，其实是产品生存问题。"),
                ScriptSection("why", "为什么今天聊这个", 24, topic.reason),
                ScriptSection("point_1", "原因 1", 36, topic.outline[0]),
                ScriptSection("point_2", "原因 2", 36, topic.outline[1]),
                ScriptSection("point_3", "原因 3", 36, topic.outline[2]),
                ScriptSection("ending", "结尾金句", 18, "AI 产品最终比拼的不是演示效果，而是能不能进入真实生活和真实工作流。"),
            ],
            title_options=[topic.title, f"第 {day_number} 天：{topic.title}", f"一个 AI 产品判断：{topic.core_viewpoint}"],
            cover_copy=topic.title,
            publish_copy=f"AI视频日记今天聊：{topic.title}。我的判断是：{topic.core_viewpoint}",
            comment_prompt="你最近有没有遇到看起来很强、但自己不会天天用的 AI 产品？",
        )

    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptSection:
        current = next(
            section for section in current_script.sections if section.id == section_id
        )
        reason_suffix = f" 用户要求：{reason}" if reason else ""
        return ScriptSection(
            id=current.id,
            title=current.title,
            estimated_seconds=current.estimated_seconds,
            content=(
                f"{current.content} 这一版改成更贴近日记的表达，"
                f"围绕「{topic.core_viewpoint}」讲得更具体。{reason_suffix}"
            ),
            feedback=current.feedback,
        )


class DeepSeekScriptGenerator:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._timeout = timeout

    def generate(
        self,
        topic: TopicCandidate,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptPackage:
        if not self._settings.deepseek_api_key:
            raise LLMConfigurationError("DEEPSEEK_API_KEY is not configured")

        payload = {
            "model": self._settings.deepseek_model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": self._user_prompt(topic, day_number, feedback_events or [])},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
            "max_tokens": 2400,
        }
        url = f"{self._settings.deepseek_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self._settings.deepseek_api_key}"}

        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            response = client.post(url, json=payload, headers=headers)

        try:
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMResponseError("DeepSeek did not return a valid chat completion") from exc

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMResponseError("DeepSeek response content was not valid JSON") from exc

        return self._script_from_json(data)

    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptSection:
        if not self._settings.deepseek_api_key:
            raise LLMConfigurationError("DEEPSEEK_API_KEY is not configured")

        current = next(
            section for section in current_script.sections if section.id == section_id
        )
        payload = {
            "model": self._settings.deepseek_model,
            "messages": [
                {"role": "system", "content": self._section_system_prompt()},
                {
                    "role": "user",
                    "content": self._section_user_prompt(
                        topic,
                        current_script,
                        current,
                        reason,
                        feedback_events or [],
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
            "max_tokens": 900,
        }
        url = f"{self._settings.deepseek_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self._settings.deepseek_api_key}"}

        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            response = client.post(url, json=payload, headers=headers)

        try:
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMResponseError("DeepSeek did not return valid section JSON") from exc

        if not isinstance(data, dict):
            raise LLMResponseError("DeepSeek section JSON must be an object")
        return ScriptSection(
            id=str(data.get("id") or current.id),
            title=str(data.get("title") or current.title),
            estimated_seconds=int(
                data.get("estimated_seconds") or current.estimated_seconds
            ),
            content=str(data["content"]),
            feedback=current.feedback,
        )

    def _system_prompt(self) -> str:
        return (
            "你是一个中文 AI 视频日记口播编导。只输出 JSON，不要 Markdown，不要解释。"
            "JSON 字段必须包含 hook、sections、title_options、cover_copy、publish_copy、comment_prompt。"
            "sections 至少 5 段，每段包含 id、title、estimated_seconds、content。"
            "口播要像真人日记：直接、具体、有判断，不要营销腔。"
            "不要使用动物、怪物、玄幻、热血鸡汤类比。"
        )

    def _section_system_prompt(self) -> str:
        return (
            "你是一个中文 AI 视频日记口播编导。只输出 JSON，不要 Markdown，不要解释。"
            "JSON 字段必须包含 id、title、estimated_seconds、content。"
            "只重写用户指定段落，保持真人日记感，不要营销腔。"
        )

    def _user_prompt(
        self,
        topic: TopicCandidate,
        day_number: int,
        feedback_events: list[FeedbackEvent],
    ) -> str:
        sources = [
            {"type": source.source_type, "title": source.title, "ref": source.ref}
            for source in topic.source_refs
        ]
        feedback = [
            {
                "target_type": event.target_type,
                "value": event.value,
                "reason": event.reason,
            }
            for event in feedback_events[-10:]
        ]
        return json.dumps(
            {
                "day_number": day_number,
                "topic": {
                    "title": topic.title,
                    "core_viewpoint": topic.core_viewpoint,
                    "reason": topic.reason,
                    "audience": topic.audience,
                    "outline": topic.outline,
                    "sources": sources,
                },
                "recent_feedback": feedback,
                "requirements": [
                    "生成 2 到 3 分钟中文口播稿。",
                    "开头 3 秒要有明确冲突或判断。",
                    "保留三段核心原因，每段都要有一句可直接口播的内容。",
                    "生成 3 个标题候选、1 条封面文案、1 条发布文案、1 条评论引导。",
                ],
            },
            ensure_ascii=False,
        )

    def _section_user_prompt(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        current_section: ScriptSection,
        reason: str | None,
        feedback_events: list[FeedbackEvent],
    ) -> str:
        return json.dumps(
            {
                "topic": {
                    "title": topic.title,
                    "core_viewpoint": topic.core_viewpoint,
                    "reason": topic.reason,
                    "outline": topic.outline,
                },
                "current_script": {
                    "hook": current_script.hook,
                    "sections": [
                        {
                            "id": section.id,
                            "title": section.title,
                            "content": section.content,
                        }
                        for section in current_script.sections
                    ],
                },
                "target_section": {
                    "id": current_section.id,
                    "title": current_section.title,
                    "estimated_seconds": current_section.estimated_seconds,
                    "content": current_section.content,
                },
                "rewrite_reason": reason,
                "recent_feedback": [
                    {
                        "target_type": event.target_type,
                        "value": event.value,
                        "reason": event.reason,
                    }
                    for event in feedback_events[-10:]
                    if event.active
                ],
            },
            ensure_ascii=False,
        )

    def _script_from_json(self, data: object) -> ScriptPackage:
        if not isinstance(data, dict):
            raise LLMResponseError("DeepSeek script JSON must be an object")

        raw_sections = data.get("sections")
        if not isinstance(raw_sections, list) or len(raw_sections) < 5:
            raise LLMResponseError("DeepSeek script JSON must contain at least five sections")

        sections: list[ScriptSection] = []
        for index, raw_section in enumerate(raw_sections):
            if not isinstance(raw_section, dict):
                raise LLMResponseError("DeepSeek section must be an object")
            sections.append(
                ScriptSection(
                    id=str(raw_section.get("id") or f"section_{index + 1}"),
                    title=str(raw_section["title"]),
                    estimated_seconds=int(raw_section["estimated_seconds"]),
                    content=str(raw_section["content"]),
                )
            )

        return ScriptPackage(
            id=f"script_{uuid4().hex[:6]}",
            hook=str(data["hook"]),
            sections=sections,
            title_options=[str(item) for item in data["title_options"]],
            cover_copy=str(data["cover_copy"]),
            publish_copy=str(data["publish_copy"]),
            comment_prompt=str(data["comment_prompt"]),
        )


def script_generator_from_settings(settings: Settings) -> ScriptGenerator:
    if settings.deepseek_api_key:
        return DeepSeekScriptGenerator(settings)
    return MissingScriptGenerator()
