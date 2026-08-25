from __future__ import annotations

import json
import random
import time as time_module
from typing import Protocol
from uuid import uuid4

import httpx

from app.config import Settings
from app.content_templates import ContentTemplate
from app.creator_profile import get_creator_profile
from app.models import FeedbackEvent, ScriptPackage, ScriptSection, TopicCandidate
from app.preference_memory import preference_memory_payload
from app.repository import active_workspace_id


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
        template: ContentTemplate | None = None,
        target_duration_seconds: int | None = None,
    ) -> ScriptPackage:
        ...

    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
    ) -> ScriptSection:
        ...


class MissingScriptGenerator:
    def generate(
        self,
        topic: TopicCandidate,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
        target_duration_seconds: int | None = None,
    ) -> ScriptPackage:
        raise LLMConfigurationError("DEEPSEEK_API_KEY is not configured")

    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
    ) -> ScriptSection:
        raise LLMConfigurationError("DEEPSEEK_API_KEY is not configured")


class TemplateScriptGenerator:
    def generate(
        self,
        topic: TopicCandidate,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
        target_duration_seconds: int | None = None,
    ) -> ScriptPackage:
        outline = [*topic.outline]
        while len(outline) < 3:
            outline.append("回到来源核对这个判断，不补充未经支持的事实。")
        return ScriptPackage(
            id=f"script_{uuid4().hex[:6]}",
            hook=f"先说结论：{topic.core_viewpoint}",
            sections=[
                ScriptSection("source", "信息来源", 8, topic.reason),
                ScriptSection("point_1", "第一个信息点", 12, outline[0]),
                ScriptSection("point_2", "第二个信息点", 12, outline[1]),
                ScriptSection("point_3", "第三个信息点", 12, outline[2]),
                ScriptSection("ending", "结尾判断", 8, f"所以我目前的判断是：{topic.core_viewpoint}"),
            ],
            title_options=[topic.title, f"今天值得关注：{topic.title}", f"一个可验证的判断：{topic.core_viewpoint}"],
            cover_copy=topic.title,
            publish_copy=f"今天的来源选题：{topic.title}。当前判断：{topic.core_viewpoint}",
            comment_prompt="你会如何验证这个判断？",
        )

    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
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


def _httpx_post_with_retry(
    url: str,
    json: object,
    headers: dict[str, str],
    timeout: float,
    transport: object | None,
    max_retries: int = 3,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(
                transport=transport, timeout=timeout
            ) as client:
                response = client.post(url, json=json, headers=headers)
            if response.is_success or response.status_code < 500:
                return response
            body_preview = response.text[:200]
            raise httpx.HTTPStatusError(
                f"server error {response.status_code}: {body_preview}",
                request=response.request,
                response=response,
            )
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadError) as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = 2 ** attempt + random.random()
                time_module.sleep(delay)
    raise LLMResponseError(
        f"DeepSeek API call failed after {max_retries + 1} attempts"
    ) from last_exc


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
        template: ContentTemplate | None = None,
        target_duration_seconds: int | None = None,
    ) -> ScriptPackage:
        if not self._settings.llm_configured:
            raise LLMConfigurationError("LLM provider is not configured")

        payload = {
            "model": self._settings.effective_llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": self._system_prompt(template, target_duration_seconds),
                },
                {
                    "role": "user",
                    "content": self._user_prompt(
                        topic,
                        day_number,
                        feedback_events or [],
                        template,
                        target_duration_seconds,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
            "max_tokens": 2400,
        }
        url = f"{self._settings.effective_llm_base_url}/chat/completions"
        headers = {}
        if self._settings.effective_llm_api_key:
            headers["Authorization"] = (
                f"Bearer {self._settings.effective_llm_api_key}"
            )

        response = _httpx_post_with_retry(
            url, payload, headers, self._timeout, self._transport
        )

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
        template: ContentTemplate | None = None,
    ) -> ScriptSection:
        if not self._settings.llm_configured:
            raise LLMConfigurationError("LLM provider is not configured")

        current = next(
            section for section in current_script.sections if section.id == section_id
        )
        payload = {
            "model": self._settings.effective_llm_model,
            "messages": [
                {"role": "system", "content": self._section_system_prompt(template)},
                {
                    "role": "user",
                    "content": self._section_user_prompt(
                        topic,
                        current_script,
                        current,
                        reason,
                        feedback_events or [],
                        template,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
            "max_tokens": 900,
        }
        url = f"{self._settings.effective_llm_base_url}/chat/completions"
        headers = {}
        if self._settings.effective_llm_api_key:
            headers["Authorization"] = (
                f"Bearer {self._settings.effective_llm_api_key}"
            )

        response = _httpx_post_with_retry(
            url, payload, headers, self._timeout, self._transport
        )

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

    def _profile_context(self) -> str:
        """Build a prompt suffix from the creator profile, if available.

        The profile is scoped to the active workspace so a generation
        request can never silently inherit another workspace's (or the
        reserved 'local' workspace's) creator habits. With Supabase auth
        enabled, a missing workspace context raises instead of falling back.
        """
        profile = get_creator_profile(
            self._settings.database_path,
            workspace_id=active_workspace_id(),
        )
        if profile is None:
            return ""
        parts: list[str] = []
        if profile.common_expressions:
            parts.append(
                "优先使用以下表达方式："
                + "、".join(profile.common_expressions[:10])
                + "。"
            )
        if profile.banned_words:
            parts.append(
                "避免使用以下词汇："
                + "、".join(profile.banned_words[:10])
                + "。"
            )
        if profile.style_preferences:
            prefs = profile.style_preferences
            if "tone" in prefs:
                parts.append(f"语气偏好：{prefs['tone']}。")
            if "sentence_length" in prefs:
                parts.append(f"句长偏好：{prefs['sentence_length']}。")
            if "hook_style" in prefs:
                style_hint = {
                    "question": "开篇优先使用提问式 hook。",
                    "exclamation": "开篇优先使用感叹式 hook。",
                    "statement": "开篇优先使用陈述式 hook。",
                }.get(prefs["hook_style"], "")
                if style_hint:
                    parts.append(style_hint)
        if not parts:
            return ""
        return "以下来自创作者画像，请在生成内容时参考：" + " ".join(parts)

    def _system_prompt(
        self,
        template: ContentTemplate | None = None,
        target_duration_seconds: int | None = None,
    ) -> str:
        template_context = ""
        if template is not None:
            template_context = (
                f"本次栏目：{template.name}。"
                f"场景：{template.scenario}"
                f"推荐时长：{template.recommended_duration}。"
                f"脚本结构：{' -> '.join(template.script_structure)}。"
                f"标题规则：{'；'.join(template.title_rules)}。"
                f"封面文案规则：{'；'.join(template.cover_copy_rules)}。"
                f"发布文案规则：{'；'.join(template.publish_copy_rules)}。"
            )
        profile_ctx = self._profile_context()
        duration_context = (
            f"本次用户指定目标时长为 {target_duration_seconds} 秒，以此为准。"
            if target_duration_seconds is not None
            else "未单独指定时，默认以栏目推荐时长为目标。"
        )
        return (
            "你是一个中文 AI 视频日记口播编导，服务于技术型个人创作者。"
            "只输出 JSON，不要 Markdown，不要解释。"
            "JSON 字段必须包含 hook、sections、title_options、cover_copy、publish_copy、comment_prompt。"
            "sections 至少 5 段，每段包含 id、title、estimated_seconds、content。"
            f"{template_context}"
            f"{duration_context}"
            "hook 必须作为独立字段输出，不要放入 sections。"
            "sections 要承接 hook 之后的正文结构，并尽量对齐本次栏目的脚本结构和 topic.outline。"
            "estimated_seconds 按约 180 字/分钟估算。"
            "口播要像真人日记：直接、具体、有判断，不要营销腔。"
            "不要使用动物、怪物、玄幻、热血鸡汤类比。"
            + (f" {profile_ctx}" if profile_ctx else "")
        )

    def _section_system_prompt(self, template: ContentTemplate | None = None) -> str:
        template_context = f"本次栏目：{template.name}。" if template is not None else ""
        return (
            "你是一个中文 AI 视频日记口播编导。"
            f"{template_context}"
            "只输出 JSON，不要 Markdown，不要解释。"
            "JSON 字段必须包含 id、title、estimated_seconds、content。"
            "只重写用户指定段落，保持 id 和 title 不变，保持真人日记感，不要营销腔。"
            "重写后的段落必须与上一段和下一段自然衔接。"
        )

    def _user_prompt(
        self,
        topic: TopicCandidate,
        day_number: int,
        feedback_events: list[FeedbackEvent],
        template: ContentTemplate | None = None,
        target_duration_seconds: int | None = None,
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
                "content_template": _content_template_payload(template),
                "topic": {
                    "title": topic.title,
                    "core_viewpoint": topic.core_viewpoint,
                    "reason": topic.reason,
                    "audience": topic.audience,
                    "outline": topic.outline,
                    "sources": sources,
                },
                "recent_feedback": feedback,
                "preference_memory": preference_memory_payload(feedback_events),
                "requirements": [
                    (
                        f"生成总时长约 {target_duration_seconds} 秒的中文口播稿。"
                        if target_duration_seconds is not None
                        else f"生成总时长 {template.recommended_duration if template else '默认约 90 秒'} 的中文口播稿。"
                    ),
                    "hook 是独立字段",
                    "开头 3 秒要有明确冲突或判断。",
                    "sections 要与 topic.outline 或 content_template.script_structure 对齐。",
                    "优先遵守 preference_memory：prefer 是应该强化的表达方式，avoid 是应该避开的表达方式。",
                    "estimated_seconds 按约 180 字/分钟估算。",
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
        template: ContentTemplate | None = None,
    ) -> str:
        section_index = next(
            (
                index
                for index, section in enumerate(current_script.sections)
                if section.id == current_section.id
            ),
            -1,
        )
        previous_section = (
            current_script.sections[section_index - 1]
            if section_index > 0
            else None
        )
        next_section = (
            current_script.sections[section_index + 1]
            if 0 <= section_index < len(current_script.sections) - 1
            else None
        )
        return json.dumps(
            {
                "content_template": _content_template_payload(template),
                "topic": {
                    "title": topic.title,
                    "core_viewpoint": topic.core_viewpoint,
                    "reason": topic.reason,
                    "outline": topic.outline,
                },
                "script_context": {
                    "hook": current_script.hook,
                    "prev_section_tail": previous_section.content[-120:]
                    if previous_section is not None
                    else None,
                    "next_section_head": next_section.content[:120]
                    if next_section is not None
                    else None,
                },
                "target_section": {
                    "id": current_section.id,
                    "title": current_section.title,
                    "role_in_script": _section_role(
                        section_index,
                        len(current_script.sections),
                    ),
                    "estimated_seconds": current_section.estimated_seconds,
                    "content": current_section.content,
                },
                "rewrite_reason": reason or "优化表达",
                "recent_feedback": [
                    {
                        "target_type": event.target_type,
                        "value": event.value,
                        "reason": event.reason,
                    }
                    for event in feedback_events[-10:]
                    if event.active
                ],
                "preference_memory": preference_memory_payload(feedback_events),
                "requirements": [
                    "优先遵守 preference_memory，保留用户偏好的表达，避开用户明确踩过的方向。",
                    "只重写 target_section，不要改 hook、标题候选、封面文案和发布文案。",
                    "不要让重写段落和上下文断裂。",
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


def _content_template_payload(template: ContentTemplate | None) -> dict[str, object] | None:
    if template is None:
        return None
    return {
        "template_id": template.template_id,
        "name": template.name,
        "scenario": template.scenario,
        "recommended_duration": template.recommended_duration,
        "topic_structure": [*template.topic_structure],
        "script_structure": [*template.script_structure],
        "subtitle_style": template.subtitle_style,
        "title_rules": [*template.title_rules],
        "cover_copy_rules": [*template.cover_copy_rules],
        "publish_copy_rules": [*template.publish_copy_rules],
        "default_feedback_dimensions": [*template.default_feedback_dimensions],
    }


def _section_role(index: int, total: int) -> str:
    if index <= 0:
        return "背景或第一段正文"
    if index >= total - 1:
        return "结尾行动建议"
    return f"核心论证第 {index} 段"


def script_generator_from_settings(settings: Settings) -> ScriptGenerator:
    if settings.llm_configured:
        return DeepSeekScriptGenerator(settings)
    return TemplateScriptGenerator()
