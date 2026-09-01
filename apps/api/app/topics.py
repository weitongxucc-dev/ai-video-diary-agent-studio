from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from app.config import Settings
from app.content_templates import ContentTemplate
from app.external_sources import ExternalSourceResponseError, HtmlPageSourceClient
from app.hermes_radar import HermesRadarSourceClient
from app.models import FeedbackEvent, SourceRef, TopicCandidate
from app.preference_memory import preference_memory_payload
from app.product_hunt import (
    ProductHuntClient,
    ProductHuntConfigurationError,
    ProductHuntResponseError,
)
from app.sources import ObsidianVault, TopicSourceItem


class TopicGenerationConfigurationError(RuntimeError):
    pass


class TopicGenerationResponseError(RuntimeError):
    pass


@dataclass(frozen=True)
class CreationTaskTopicSource:
    reference_id: str
    kind: Literal["knowledge", "source"]
    title: str
    text: str


@dataclass(frozen=True)
class CreationTaskTopicInput:
    goal: str
    language: str
    sources: tuple[CreationTaskTopicSource, ...]


@dataclass(frozen=True)
class CreationTaskGeneratedTopic:
    title: str
    angle: str
    audience: str
    outline: tuple[str, ...]
    score: float
    evidence_reference_ids: tuple[str, ...]


class CreationTaskTopicGenerationError(RuntimeError):
    """A safe, structured failure for the explicit CreationTask topic boundary."""

    def __init__(
        self,
        *,
        code: str,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
    ) -> None:
        self.code = code
        self.reason = reason
        self.impact = impact
        self.repair_action = repair_action
        self.retryable = retryable
        super().__init__(f"{code}: {reason}")


class OpenAICompatibleCreationTaskTopicGenerator:
    """Generate CreationTask topics only from explicit, server-resolved input.

    This is deliberately separate from the legacy Episode generators below: it
    never reads a vault, external URL, template, feedback, or repository.
    """

    _MAX_SOURCES = 64
    _MAX_SOURCE_TEXT = 8_000
    _MAX_TOTAL_SOURCE_TEXT = 48_000
    _MAX_TOPICS = 8

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.strip().rstrip("/")
        self._model = model.strip()
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout
        parsed_url = urlsplit(self._base_url)
        if (
            not self._base_url
            or not self._model
            or parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
        ):
            raise CreationTaskTopicGenerationError(
                code="route_configuration_invalid",
                reason="文字模型连接缺少服务地址或模型标识。",
                impact="未调用文字模型，未生成选题候选。",
                repair_action="在本机运行时设置中填写有效的 OpenAI-compatible 地址和模型标识。",
                retryable=False,
            )

    def generate(self, input: CreationTaskTopicInput) -> list[CreationTaskGeneratedTopic]:
        sources = self._validated_sources(input)
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是创作任务的选题生成器。只返回单一 JSON object，不要 Markdown。"
                        "顶层必须为 {\"topics\": [...]}。每个 topic 必须包含 "
                        "title、angle、audience、outline、score、evidence_reference_ids。"
                        "evidence_reference_ids 只能引用输入 sources 的 reference_id。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "goal": input.goal.strip(),
                            "language": input.language.strip(),
                            "sources": [
                                {
                                    "reference_id": source.reference_id,
                                    "kind": source.kind,
                                    "title": source.title,
                                    "text": source.text,
                                }
                                for source in sources
                            ],
                            "requirements": {
                                "topic_count": "1-8",
                                "outline_items": "1-8",
                                "score_range": "0-100",
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
            "max_tokens": 3200,
        }
        headers = (
            {"Authorization": f"Bearer {self._api_key}"}
            if self._api_key
            else {}
        )
        transport_failed = False
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.RequestError:
            transport_failed = True
        if transport_failed:
            raise self._failure(
                code="transport_failed",
                reason="文字模型连接失败。",
                impact="未生成选题候选。",
                repair_action="检查本机模型连接、网络或本地服务后重试。",
                retryable=True,
            )
        if response.is_error:
            raise self._failure(
                code="http_failed",
                reason="文字模型服务拒绝了选题请求。",
                impact="未生成选题候选。",
                repair_action="检查模型服务状态、账号权限或请求配额后重试。",
                retryable=response.status_code >= 500,
            )
        content = self._response_content(response)
        try:
            document = json.loads(content)
        except json.JSONDecodeError as exc:
            raise self._failure(
                code="content_json_invalid",
                reason="文字模型返回的内容不是有效 JSON。",
                impact="未生成选题候选。",
                repair_action="检查模型的 JSON 输出能力或更换兼容模型后重试。",
                retryable=True,
            ) from exc
        return self._validated_topics(document, {source.reference_id for source in sources})

    def _validated_sources(
        self,
        input: CreationTaskTopicInput,
    ) -> tuple[CreationTaskTopicSource, ...]:
        goal = self._input_text(input.goal, "goal", 2_000)
        language = self._input_text(input.language, "language", 64)
        if not isinstance(input.sources, tuple) or not input.sources or len(input.sources) > self._MAX_SOURCES:
            raise self._failure(
                code="input_invalid",
                reason="选题输入必须包含有界的授权资料。",
                impact="未调用文字模型，未生成选题候选。",
                repair_action="重新从已确认方案解析资料引用后重试。",
                retryable=False,
            )
        del goal, language
        total_text = 0
        seen_ids: set[str] = set()
        normalized: list[CreationTaskTopicSource] = []
        for source in input.sources:
            if not isinstance(source, CreationTaskTopicSource) or source.kind not in {"knowledge", "source"}:
                raise self._failure(
                    code="input_invalid",
                    reason="选题资料类型无效。",
                    impact="未调用文字模型，未生成选题候选。",
                    repair_action="重新读取当前工作区已授权的知识或信息源。",
                    retryable=False,
                )
            reference_id = self._input_text(source.reference_id, "reference id", 200)
            title = self._input_text(source.title, "source title", 300)
            text = self._input_text(source.text, "source text", self._MAX_SOURCE_TEXT)
            if reference_id in seen_ids:
                raise self._failure(
                    code="input_invalid",
                    reason="选题资料引用重复。",
                    impact="未调用文字模型，未生成选题候选。",
                    repair_action="去除重复资料引用后重新预检。",
                    retryable=False,
                )
            seen_ids.add(reference_id)
            total_text += len(text)
            if total_text > self._MAX_TOTAL_SOURCE_TEXT:
                raise self._failure(
                    code="input_invalid",
                    reason="选题资料文字超过本次请求上限。",
                    impact="未调用文字模型，未生成选题候选。",
                    repair_action="缩短已授权文字摘录后重试。",
                    retryable=False,
                )
            normalized.append(
                CreationTaskTopicSource(
                    reference_id=reference_id,
                    kind=source.kind,
                    title=title,
                    text=text,
                )
            )
        return tuple(normalized)

    def _response_content(self, response: httpx.Response) -> str:
        try:
            envelope = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise self._failure(
                code="response_envelope_invalid",
                reason="文字模型响应协议无效。",
                impact="未生成选题候选。",
                repair_action="检查 OpenAI-compatible 服务响应格式后重试。",
                retryable=True,
            ) from exc
        if not isinstance(envelope, dict):
            raise self._failure(
                code="response_envelope_invalid",
                reason="文字模型响应协议无效。",
                impact="未生成选题候选。",
                repair_action="检查 OpenAI-compatible 服务响应格式后重试。",
                retryable=True,
            )
        choices = envelope.get("choices")
        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
            or not isinstance(choices[0].get("message"), dict)
            or not isinstance(choices[0]["message"].get("content"), str)
        ):
            raise self._failure(
                code="response_envelope_invalid",
                reason="文字模型响应缺少候选内容。",
                impact="未生成选题候选。",
                repair_action="检查 OpenAI-compatible 服务响应格式后重试。",
                retryable=True,
            )
        return str(choices[0]["message"]["content"])

    def _validated_topics(
        self,
        document: object,
        allowed_reference_ids: set[str],
    ) -> list[CreationTaskGeneratedTopic]:
        if not isinstance(document, dict) or not isinstance(document.get("topics"), list):
            raise self._failure(
                code="response_schema_invalid",
                reason="文字模型内容缺少 topics 列表。",
                impact="未生成选题候选。",
                repair_action="检查模型提示词或更换支持 JSON 输出的模型后重试。",
                retryable=True,
            )
        raw_topics = document["topics"]
        if not raw_topics or len(raw_topics) > self._MAX_TOPICS:
            raise self._failure(
                code="response_schema_invalid",
                reason="文字模型返回的选题数量无效。",
                impact="未生成选题候选。",
                repair_action="要求模型返回 1 到 8 个选题后重试。",
                retryable=True,
            )
        titles: set[str] = set()
        topics: list[CreationTaskGeneratedTopic] = []
        for raw_topic in raw_topics:
            if not isinstance(raw_topic, dict):
                raise self._failure(
                    code="response_schema_invalid",
                    reason="文字模型返回的选题项无效。",
                    impact="未生成选题候选。",
                    repair_action="检查模型 JSON 输出结构后重试。",
                    retryable=True,
                )
            title = self._topic_text(raw_topic, "title", 160)
            normalized_title = title.casefold()
            if normalized_title in titles:
                raise self._failure(
                    code="duplicate_title",
                    reason="文字模型返回了重复标题。",
                    impact="未生成选题候选。",
                    repair_action="要求模型生成不同切入角度的选题后重试。",
                    retryable=True,
                )
            titles.add(normalized_title)
            angle = self._topic_text(raw_topic, "angle", 500)
            audience = self._topic_text(raw_topic, "audience", 300)
            outline = self._topic_outline(raw_topic)
            score = raw_topic.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= float(score) <= 100:
                raise self._failure(
                    code="response_schema_invalid",
                    reason="文字模型返回的选题评分无效。",
                    impact="未生成选题候选。",
                    repair_action="要求模型返回 0 到 100 的数值评分后重试。",
                    retryable=True,
                )
            evidence_ids = self._topic_evidence_ids(raw_topic, allowed_reference_ids)
            topics.append(
                CreationTaskGeneratedTopic(
                    title=title,
                    angle=angle,
                    audience=audience,
                    outline=outline,
                    score=float(score),
                    evidence_reference_ids=evidence_ids,
                )
            )
        return topics

    def _topic_text(self, topic: dict[str, object], field: str, maximum: int) -> str:
        value = topic.get(field)
        if not isinstance(value, str):
            raise self._failure(
                code="response_schema_invalid",
                reason=f"文字模型返回的 {field} 无效。",
                impact="未生成选题候选。",
                repair_action="检查模型 JSON 输出结构后重试。",
                retryable=True,
            )
        return self._response_text(value, field, maximum)

    def _topic_outline(self, topic: dict[str, object]) -> tuple[str, ...]:
        outline = topic.get("outline")
        if not isinstance(outline, list) or not outline or len(outline) > 8:
            raise self._failure(
                code="response_schema_invalid",
                reason="文字模型返回的选题大纲无效。",
                impact="未生成选题候选。",
                repair_action="要求模型返回 1 到 8 条非空大纲后重试。",
                retryable=True,
            )
        return tuple(self._topic_text({"outline": item}, "outline", 400) for item in outline)

    def _topic_evidence_ids(
        self,
        topic: dict[str, object],
        allowed_reference_ids: set[str],
    ) -> tuple[str, ...]:
        raw_ids = topic.get("evidence_reference_ids")
        if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 8:
            raise self._failure(
                code="evidence_reference_invalid",
                reason="文字模型返回的证据引用无效。",
                impact="未生成选题候选。",
                repair_action="要求模型仅引用本次输入资料后重试。",
                retryable=True,
            )
        reference_ids = tuple(
            self._evidence_reference_id(value)
            if isinstance(value, str)
            else ""
            for value in raw_ids
        )
        if (
            not all(reference_ids)
            or len(set(reference_ids)) != len(reference_ids)
            or any(reference_id not in allowed_reference_ids for reference_id in reference_ids)
        ):
            raise self._failure(
                code="evidence_reference_invalid",
                reason="文字模型引用了无效或未提供的证据。",
                impact="未生成选题候选。",
                repair_action="要求模型仅引用本次输入资料后重试。",
                retryable=True,
            )
        return reference_ids

    def _input_text(self, value: object, field: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise self._failure(
                code="input_invalid",
                reason=f"选题 {field} 无效。",
                impact="未调用文字模型，未生成选题候选。",
                repair_action="重新读取已确认方案的文字资料后重试。",
                retryable=False,
            )
        cleaned = value.strip()
        if not cleaned or len(cleaned) > maximum:
            raise self._failure(
                code="input_invalid",
                reason=f"选题 {field} 为空或超过上限。",
                impact="未调用文字模型，未生成选题候选。",
                repair_action="缩短并确认本次选题输入后重试。",
                retryable=False,
            )
        return cleaned

    def _response_text(self, value: object, field: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise self._failure(
                code="response_schema_invalid",
                reason=f"文字模型返回的 {field} 无效。",
                impact="未生成选题候选。",
                repair_action="检查模型 JSON 输出结构后重试。",
                retryable=True,
            )
        cleaned = value.strip()
        if not cleaned or len(cleaned) > maximum:
            raise self._failure(
                code="response_schema_invalid",
                reason=f"文字模型返回的 {field} 为空或超过上限。",
                impact="未生成选题候选。",
                repair_action="检查模型 JSON 输出结构后重试。",
                retryable=True,
            )
        return cleaned

    def _evidence_reference_id(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 200:
            raise self._failure(
                code="evidence_reference_invalid",
                reason="文字模型返回的证据引用为空或超过上限。",
                impact="未生成选题候选。",
                repair_action="要求模型仅引用本次输入资料后重试。",
                retryable=True,
            )
        return cleaned

    @staticmethod
    def _failure(
        *,
        code: str,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
    ) -> CreationTaskTopicGenerationError:
        return CreationTaskTopicGenerationError(
            code=code,
            reason=reason,
            impact=impact,
            repair_action=repair_action,
            retryable=retryable,
        )


@dataclass(frozen=True)
class CreationTaskScriptInput:
    goal: str
    language: str
    duration_target_seconds: int
    topic_title: str
    topic_angle: str
    topic_audience: str
    topic_outline: tuple[str, ...]
    sources: tuple[CreationTaskTopicSource, ...]


@dataclass(frozen=True)
class CreationTaskGeneratedScriptSection:
    title: str
    content: str
    visual_prompt: str
    evidence_reference_ids: tuple[str, ...]


@dataclass(frozen=True)
class CreationTaskGeneratedScript:
    title: str
    language: Literal["zh-CN"]
    sections: tuple[CreationTaskGeneratedScriptSection, ...]


class CreationTaskScriptGenerationError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
    ) -> None:
        self.code = code
        self.reason = reason
        self.impact = impact
        self.repair_action = repair_action
        self.retryable = retryable
        super().__init__(f"{code}: {reason}")


class OpenAICompatibleCreationTaskScriptGenerator:
    """Generate one editable script from a confirmed topic and explicit evidence."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 90.0,
    ) -> None:
        self._base_url = base_url.strip().rstrip("/")
        self._model = model.strip()
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout
        parsed_url = urlsplit(self._base_url)
        if (
            not self._base_url
            or not self._model
            or parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
        ):
            raise self._failure(
                code="route_configuration_invalid",
                reason="文字模型连接缺少服务地址或模型标识。",
                impact="未调用文字模型，未生成文案。",
                repair_action="在本机运行时设置中填写有效的 OpenAI-compatible 地址和模型标识。",
                retryable=False,
            )

    def generate(self, input: CreationTaskScriptInput) -> CreationTaskGeneratedScript:
        if input.language != "zh-CN":
            raise self._failure(
                code="language_unavailable",
                reason="本地真成片 v0.1 只支持中文文案与中文声音克隆。",
                impact="未调用文字模型，未生成文案。",
                repair_action="将创作方案语言改为简体中文后重试。",
                retryable=False,
            )
        if not 15 <= input.duration_target_seconds <= 600:
            raise self._failure(
                code="input_invalid",
                reason="文案目标时长无效。",
                impact="未调用文字模型，未生成文案。",
                repair_action="将目标时长设置在 15 到 600 秒之间。",
                retryable=False,
            )
        required_texts = (
            input.goal,
            input.topic_title,
            input.topic_angle,
            input.topic_audience,
        )
        if any(not value.strip() for value in required_texts) or not input.topic_outline:
            raise self._failure(
                code="input_invalid",
                reason="已确认选题缺少生成文案所需的信息。",
                impact="未调用文字模型，未生成文案。",
                repair_action="重新确认包含标题、角度、受众和大纲的选题。",
                retryable=False,
            )
        if not input.sources or len(input.sources) > 64:
            raise self._failure(
                code="input_invalid",
                reason="文案输入必须包含有界的授权资料。",
                impact="未调用文字模型，未生成文案。",
                repair_action="重新从已确认方案解析资料引用后重试。",
                retryable=False,
            )
        source_ids: set[str] = set()
        source_aliases: dict[str, str] = {}
        serialized_sources: list[dict[str, str]] = []
        total_length = 0
        for index, source in enumerate(input.sources, start=1):
            reference_id = source.reference_id.strip()
            text = source.text.strip()
            if (
                not reference_id
                or reference_id in source_ids
                or not source.title.strip()
                or not text
                or len(text) > 8_000
            ):
                raise self._failure(
                    code="input_invalid",
                    reason="文案资料引用为空、重复或超过上限。",
                    impact="未调用文字模型，未生成文案。",
                    repair_action="重新读取当前工作区已授权的文字资料。",
                    retryable=False,
                )
            source_ids.add(reference_id)
            source_alias = f"evidence_{index}"
            source_aliases[source_alias] = reference_id
            total_length += len(text)
            if total_length > 48_000:
                raise self._failure(
                    code="input_invalid",
                    reason="文案资料文字超过本次请求上限。",
                    impact="未调用文字模型，未生成文案。",
                    repair_action="缩短已授权文字摘录后重试。",
                    retryable=False,
                )
            serialized_sources.append(
                {
                    "reference_id": source_alias,
                    "kind": source.kind,
                    "title": source.title.strip(),
                    "text": text,
                }
            )
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是短视频中文口播文案生成器。只返回单一 JSON object，不要 Markdown。"
                        "顶层必须为 {\"script\": {title, language, sections}}。"
                        "language 必须是 zh-CN。sections 每项必须包含 title、content、"
                        "visual_prompt、evidence_reference_ids；证据只能引用输入 reference_id。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "goal": input.goal.strip(),
                            "duration_target_seconds": input.duration_target_seconds,
                            "confirmed_topic": {
                                "title": input.topic_title.strip(),
                                "angle": input.topic_angle.strip(),
                                "audience": input.topic_audience.strip(),
                                "outline": [item.strip() for item in input.topic_outline],
                            },
                            "sources": serialized_sources,
                            "requirements": {
                                "spoken_chinese": True,
                                "section_count": "1-20",
                                "every_section_requires_evidence": True,
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.4,
            "max_tokens": 4800,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=headers
                )
        except httpx.RequestError as exc:
            raise self._failure(
                code="transport_failed",
                reason="文字模型连接失败。",
                impact="未生成文案。",
                repair_action="检查本机模型连接、网络或本地服务后重试。",
                retryable=True,
            ) from exc
        if response.is_error:
            raise self._failure(
                code="http_failed",
                reason="文字模型服务拒绝了文案请求。",
                impact="未生成文案。",
                repair_action="检查模型服务状态、账号权限或请求配额后重试。",
                retryable=response.status_code >= 500,
            )
        try:
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
            document = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise self._failure(
                code="response_envelope_invalid",
                reason="文字模型返回的文案协议无效。",
                impact="未生成文案。",
                repair_action="检查 OpenAI-compatible JSON 响应格式后重试。",
                retryable=True,
            ) from exc
        return self._validated_script(document, source_aliases)

    def _validated_script(
        self, document: object, allowed_reference_aliases: dict[str, str]
    ) -> CreationTaskGeneratedScript:
        raw = document.get("script") if isinstance(document, dict) else None
        if not isinstance(raw, dict):
            raise self._schema_failure("文字模型内容缺少 script 对象。")
        title = self._text(raw.get("title"), "title", 160)
        if raw.get("language") != "zh-CN":
            raise self._schema_failure("文字模型返回了当前版本不支持的文案语言。")
        raw_sections = raw.get("sections")
        if not isinstance(raw_sections, list) or not 1 <= len(raw_sections) <= 20:
            raise self._schema_failure("文字模型返回的文案段落数量无效。")
        sections: list[CreationTaskGeneratedScriptSection] = []
        for section in raw_sections:
            if not isinstance(section, dict):
                raise self._schema_failure("文字模型返回的文案段落无效。")
            reference_ids = section.get("evidence_reference_ids")
            if (
                not isinstance(reference_ids, list)
                or not reference_ids
                or len(reference_ids) > 12
                or any(
                    not isinstance(item, str) or item not in allowed_reference_aliases
                    for item in reference_ids
                )
                or len(set(reference_ids)) != len(reference_ids)
            ):
                raise self._schema_failure("文字模型返回了无效或未授权的文案证据引用。")
            sections.append(
                CreationTaskGeneratedScriptSection(
                    title=self._text(section.get("title"), "section title", 160),
                    content=self._text(section.get("content"), "section content", 4_000),
                    visual_prompt=self._text(
                        section.get("visual_prompt"), "visual prompt", 1_000
                    ),
                    evidence_reference_ids=tuple(
                        allowed_reference_aliases[item] for item in reference_ids
                    ),
                )
            )
        return CreationTaskGeneratedScript(
            title=title,
            language="zh-CN",
            sections=tuple(sections),
        )

    def _text(self, value: object, field: str, maximum: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise self._schema_failure(f"文字模型返回的 {field} 为空或超过上限。")
        return value.strip()

    def _schema_failure(self, reason: str) -> CreationTaskScriptGenerationError:
        return self._failure(
            code="response_schema_invalid",
            reason=reason,
            impact="未保存无效文案。",
            repair_action="检查模型 JSON 输出结构和证据引用后重试。",
            retryable=True,
        )

    @staticmethod
    def _failure(
        *,
        code: str,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
    ) -> CreationTaskScriptGenerationError:
        return CreationTaskScriptGenerationError(
            code=code,
            reason=reason,
            impact=impact,
            repair_action=repair_action,
            retryable=retryable,
        )


class TopicGenerator(Protocol):
    def generate(
        self,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
    ) -> list[TopicCandidate]:
        ...


class MissingTopicGenerator:
    def generate(
        self,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
    ) -> list[TopicCandidate]:
        raise TopicGenerationConfigurationError("DeepSeek and at least one topic source must be configured")


class TemplateTopicGenerator:
    def generate(
        self,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
    ) -> list[TopicCandidate]:
        return [
            TopicCandidate(
                id=f"topic_{uuid4().hex[:6]}",
                title="AI Agent 真正的护城河，不是模型而是工作流",
                core_viewpoint="企业和开发者真正的差异化来自可复用、可迭代的工作流设计。",
                reason="示例数据：正式运行时会改为 Obsidian 真实来源。",
                audience="AI 产品经理、创业者、独立开发者",
                outline=["模型能力同质化", "工作流承载真实业务", "反馈闭环形成壁垒"],
                score=94,
                source_refs=[SourceRef("示例", "Phase 1 mock topic", "demo:topic")],
            ),
            TopicCandidate(
                id=f"topic_{uuid4().hex[:6]}",
                title="为什么拍照识别卡路里的软件很难长期活下去？",
                core_viewpoint="识别能力只是入口，信任、习惯和付费价值才决定生存。",
                reason="示例数据：正式运行时会改为 Obsidian 真实来源。",
                audience="AI 创业者、产品经理、健身减脂用户",
                outline=["识别不等于信任", "记录习惯很难养成", "付费价值不够强"],
                score=90,
                source_refs=[SourceRef("示例", "Phase 1 mock topic", "demo:topic")],
            ),
            TopicCandidate(
                id=f"topic_{uuid4().hex[:6]}",
                title="AI 工具为什么越多，普通人反而越不会用？",
                core_viewpoint="工具爆炸带来的不是效率，而是选择成本和流程断裂。",
                reason="示例数据：正式运行时会改为 Obsidian 真实来源。",
                audience="普通职场人、知识工作者、AI 工具用户",
                outline=["工具太多造成选择压力", "缺少稳定工作流", "需要从任务而不是工具出发"],
                score=87,
                source_refs=[SourceRef("示例", "Phase 1 mock topic", "demo:topic")],
            ),
            TopicCandidate(
                id=f"topic_{uuid4().hex[:6]}",
                title="普通人做 AI 个人 IP，最重要的不是懂技术",
                core_viewpoint="持续表达真实判断，比罗列工具功能更能建立信任。",
                reason="示例数据：正式运行时会改为 Obsidian 真实来源。",
                audience="想做个人 IP 的技术人和创业者",
                outline=["不要只讲工具", "讲真实问题", "形成稳定栏目"],
                score=82,
                source_refs=[SourceRef("示例", "Phase 1 mock topic", "demo:topic")],
            ),
        ]


class LocalSourceTopicGenerator:
    """Extractive, source-grounded fallback for the offline product path.

    This intentionally avoids pretending to be a full local LLM. It turns the
    maintained source pool into conservative topic candidates and preserves a
    traceable reference for every candidate. A downloaded local model can
    replace this generator later without changing the Episode contract.
    """

    def __init__(
        self,
        vault: ObsidianVault,
        external_source_provider: Callable[[], list[TopicSourceItem]] | None = None,
    ) -> None:
        self._vault = vault
        self._external_source_provider = external_source_provider or list

    def generate(
        self,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
    ) -> list[TopicCandidate]:
        notes = self._vault.recent_notes(limit=16)
        source_items = [note.to_topic_source() for note in notes]
        source_items.extend(self._external_source_provider())
        unique: list[TopicSourceItem] = []
        seen: set[str] = set()
        for item in source_items:
            key = item.ref.strip() or f"{item.source_type}:{item.title}"
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
        if not unique:
            raise TopicGenerationConfigurationError("团队维护的选题来源池中没有可用内容")

        candidates: list[TopicCandidate] = []
        for index, item in enumerate(unique[:4]):
            excerpt = _clean_source_text(item.excerpt)
            sentences = _source_sentences(excerpt)
            viewpoint = sentences[0] if sentences else f"这条信息值得从真实工作流角度进一步验证：{item.title}"
            outline = sentences[:3]
            outline.extend(
                [
                    f"这件事发生了什么：{item.title}",
                    "它对真实内容生产流程的影响",
                    "团队现在可以验证的一个具体动作",
                ][: 3 - len(outline)]
            )
            candidates.append(
                TopicCandidate(
                    id=f"topic_{uuid4().hex[:6]}",
                    title=item.title.strip() or f"来源选题 {day_number}-{index + 1}",
                    core_viewpoint=viewpoint[:240],
                    reason=f"候选选题直接来自「{item.title}」，未添加来源外的事实。",
                    audience="关注 AI 工具、工作流和内容生产的中文用户",
                    outline=outline,
                    score=max(70, 88 - index * 4),
                    source_refs=[SourceRef(item.source_type, item.title, item.ref)],
                )
            )
        return candidates


class DeepSeekTopicGenerator:
    def __init__(
        self,
        settings: Settings,
        vault: ObsidianVault,
        external_source_provider: Callable[[], list[TopicSourceItem]] | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._settings = settings
        self._vault = vault
        self._external_source_provider = external_source_provider or (list)
        self._transport = transport
        self._timeout = timeout

    def generate(
        self,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
    ) -> list[TopicCandidate]:
        if not self._settings.llm_configured:
            raise TopicGenerationConfigurationError("LLM provider is not configured")
        notes = self._vault.recent_notes(limit=16)
        source_items = [note.to_topic_source() for note in notes] + self._external_source_provider()
        if not source_items:
            raise TopicGenerationConfigurationError("No topic sources are available")

        payload = {
            "model": self._settings.effective_llm_model,
            "messages": [
                {"role": "system", "content": self._system_prompt(template)},
                {"role": "user", "content": self._user_prompt(day_number, source_items, feedback_events or [], template)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.75,
            "max_tokens": 3200,
        }
        url = f"{self._settings.effective_llm_base_url}/chat/completions"
        headers = {}
        if self._settings.effective_llm_api_key:
            headers["Authorization"] = (
                f"Bearer {self._settings.effective_llm_api_key}"
            )

        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            response = client.post(url, json=payload, headers=headers)

        try:
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise TopicGenerationResponseError("DeepSeek did not return valid topic JSON") from exc

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise TopicGenerationResponseError("DeepSeek topic content was not valid JSON") from exc

        return self._topics_from_json(data, source_items)

    def _system_prompt(self, template: ContentTemplate | None = None) -> str:
        template_context = ""
        if template is not None:
            template_context = (
                f"本次内容栏目：{template.name}。"
                f"场景定义：{template.scenario}"
                f"推荐时长：{template.recommended_duration}。"
                f"选题结构：{' -> '.join(template.topic_structure)}。"
                f"标题规则：{'；'.join(template.title_rules)}。"
            )
        return (
            "你是一个中文 AI 视频日记选题 Agent，服务于技术型个人创作者。"
            "只输出 JSON，不要 Markdown，不要解释。"
            "JSON 顶层必须是 {\"topics\": [...]}。"
            "每个 topic 必须包含 title、core_viewpoint、reason、audience、outline、score、source_refs。"
            f"{template_context}"
            "每个选题必须有真实场景、真实案例或真实数据支撑，观点要有判断、有立场。"
            "4 个选题之间必须有明显不同的切入角度，禁止同义反复。"
            "score 评分标准：真实案例支撑度 30 分、观点鲜明度 30 分、口播转化度 20 分、与近期偏好匹配度 20 分。"
            "source_refs 只能使用用户提供 source_items 里的 source_type、title、ref，不能编造不存在的来源。"
        )

    def _user_prompt(
        self,
        day_number: int,
        source_items: list[TopicSourceItem],
        feedback_events: list[FeedbackEvent],
        template: ContentTemplate | None = None,
    ) -> str:
        return json.dumps(
            {
                "day_number": day_number,
                "content_template": _content_template_payload(template),
                "source_items": [
                    {
                        "source_type": item.source_type,
                        "title": item.title,
                        "ref": item.ref,
                        "excerpt": item.excerpt,
                    }
                    for item in self._prompt_source_items(source_items)
                ],
                "recent_feedback": [
                    {
                        "target_type": event.target_type,
                        "value": event.value,
                        "reason": event.reason,
                    }
                    for event in feedback_events[-10:]
                ],
                "preference_memory": preference_memory_payload(feedback_events),
                "requirements": [
                    "生成 4 个适合 2-3 分钟 AI 视频日记的候选选题。",
                    "每个选题必须符合 content_template 的场景、选题结构和标题规则。",
                    "优先遵守 preference_memory",
                    "preference_memory.prefer 用来放大选题方向，preference_memory.avoid 用来避开低分表达。",
                    "优先选择有真实项目经验、Agent、AI产品、工作流、个人IP、内容生产相关的素材。",
                    "每个选题要有清晰观点、目标观众、三段口播结构。",
                    "source_refs 至少 1 个，且 ref 必须完全等于 source_items 里的 ref。",
                ],
            },
            ensure_ascii=False,
        )

    def _prompt_source_items(
        self,
        source_items: list[TopicSourceItem],
        limit: int = 24,
    ) -> list[TopicSourceItem]:
        selected: list[TopicSourceItem] = []
        selected_refs: set[str] = set()
        selected_counts: dict[str, int] = {}

        def add_matching(source_type: str, quota: int) -> None:
            for item in source_items:
                if selected_counts.get(source_type, 0) >= quota:
                    break
                if item.source_type == source_type and item.ref not in selected_refs:
                    selected.append(item)
                    selected_refs.add(item.ref)
                    selected_counts[source_type] = selected_counts.get(source_type, 0) + 1

        add_matching("Obsidian", 8)
        add_matching("Product Hunt", 8)
        add_matching("观猹", 8)

        for item in source_items:
            if len(selected) >= limit:
                break
            if item.ref in selected_refs:
                continue
            selected.append(item)
            selected_refs.add(item.ref)
            selected_counts[item.source_type] = selected_counts.get(item.source_type, 0) + 1
        return selected[:limit]

    def _topics_from_json(self, data: object, source_items: list[TopicSourceItem]) -> list[TopicCandidate]:
        if not isinstance(data, dict) or not isinstance(data.get("topics"), list):
            raise TopicGenerationResponseError("DeepSeek topic JSON must contain a topics list")
        sources_by_ref = {item.ref: item for item in source_items}
        topics: list[TopicCandidate] = []
        for raw_topic in data["topics"][:4]:
            if not isinstance(raw_topic, dict):
                raise TopicGenerationResponseError("DeepSeek topic item must be an object")
            source_refs = self._source_refs(raw_topic, sources_by_ref)
            topics.append(
                TopicCandidate(
                    id=f"topic_{uuid4().hex[:6]}",
                    title=str(raw_topic["title"]),
                    core_viewpoint=str(raw_topic["core_viewpoint"]),
                    reason=str(raw_topic["reason"]),
                    audience=str(raw_topic["audience"]),
                    outline=[str(item) for item in raw_topic["outline"]][:3],
                    score=max(0, min(100, int(raw_topic["score"]))),
                    source_refs=source_refs,
                )
            )
        if not topics:
            raise TopicGenerationResponseError("DeepSeek returned no topic candidates")
        return topics

    def _source_refs(
        self,
        raw_topic: dict[str, object],
        sources_by_ref: dict[str, TopicSourceItem],
    ) -> list[SourceRef]:
        raw_refs = raw_topic.get("source_refs")
        refs: list[SourceRef] = []
        if isinstance(raw_refs, list):
            for raw_ref in raw_refs:
                if not isinstance(raw_ref, dict):
                    continue
                ref = str(raw_ref.get("ref", ""))
                source = sources_by_ref.get(ref)
                if source:
                    refs.append(SourceRef(source.source_type, source.title, source.ref))
                    continue
                title = str(raw_ref.get("title", ""))
                title_match = self._source_by_title(title, sources_by_ref)
                if title_match:
                    refs.append(SourceRef(title_match.source_type, title_match.title, title_match.ref))
        if refs:
            return refs
        best_source = self._best_source_for_topic(raw_topic, sources_by_ref)
        if best_source:
            return [SourceRef(best_source.source_type, best_source.title, best_source.ref)]
        raise TopicGenerationResponseError("DeepSeek returned topic without a valid source")

    def _source_by_title(
        self,
        title: str,
        sources_by_ref: dict[str, TopicSourceItem],
    ) -> TopicSourceItem | None:
        normalized_title = title.strip().lower()
        if not normalized_title:
            return None
        for source in sources_by_ref.values():
            source_title = source.title.lower()
            if normalized_title == source_title or normalized_title in source_title or source_title in normalized_title:
                return source
        return None

    def _best_source_for_topic(
        self,
        raw_topic: dict[str, object],
        sources_by_ref: dict[str, TopicSourceItem],
    ) -> TopicSourceItem | None:
        topic_text = self._topic_text(raw_topic)
        topic_tokens = self._tokens(topic_text)
        best_source: TopicSourceItem | None = None
        best_score = 0
        for source in sources_by_ref.values():
            source_text = f"{source.source_type}\n{source.title}\n{source.ref}\n{source.excerpt}"
            score = len(topic_tokens.intersection(self._tokens(source_text)))
            if source.title and source.title in topic_text:
                score += 8
            if score > best_score:
                best_source = source
                best_score = score
        return best_source if best_score > 0 else None

    def _topic_text(self, raw_topic: dict[str, object]) -> str:
        values: list[str] = []
        for key in ("title", "core_viewpoint", "reason", "audience"):
            value = raw_topic.get(key)
            if value is not None:
                values.append(str(value))
        outline = raw_topic.get("outline")
        if isinstance(outline, list):
            values.extend(str(item) for item in outline)
        return "\n".join(values)

    def _tokens(self, text: str) -> set[str]:
        ascii_tokens = set(re.findall(r"[a-z0-9]{2,}", text.lower()))
        cjk = "".join(re.findall(r"[\u4e00-\u9fff]", text))
        cjk_bigrams = {cjk[index : index + 2] for index in range(max(0, len(cjk) - 1))}
        cjk_trigrams = {cjk[index : index + 3] for index in range(max(0, len(cjk) - 2))}
        return ascii_tokens | cjk_bigrams | cjk_trigrams


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
        "title_rules": [*template.title_rules],
        "cover_copy_rules": [*template.cover_copy_rules],
        "publish_copy_rules": [*template.publish_copy_rules],
        "default_feedback_dimensions": [*template.default_feedback_dimensions],
    }


def topic_generator_from_settings(settings: Settings, vault: ObsidianVault) -> TopicGenerator:
    has_sources = bool(
        vault.exists
        or settings.product_hunt_token
        or settings.topic_source_urls
        or settings.hermes_radar_path
    )
    if settings.llm_configured and has_sources:
        return DeepSeekTopicGenerator(
            settings,
            vault,
            external_source_provider=lambda: _external_topic_sources(settings),
        )
    if has_sources:
        return LocalSourceTopicGenerator(
            vault,
            external_source_provider=lambda: _external_topic_sources(settings),
        )
    return MissingTopicGenerator()


def _external_topic_sources(settings: Settings) -> list[TopicSourceItem]:
    sources: list[TopicSourceItem] = []
    if settings.hermes_radar_path:
        sources.extend(HermesRadarSourceClient(settings.hermes_radar_path).fetch_items())
    if settings.product_hunt_token:
        try:
            sources.extend(ProductHuntClient(settings).fetch_source_items(limit=8))
        except (ProductHuntConfigurationError, ProductHuntResponseError):
            pass
    if settings.topic_source_urls:
        try:
            sources.extend(HtmlPageSourceClient(settings.topic_source_urls).fetch_items())
        except ExternalSourceResponseError:
            pass
    return sources


def _clean_source_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _source_sentences(value: str) -> list[str]:
    parts = [
        part.strip(" -\t")
        for part in re.split(r"(?<=[。！？!?])\s*|\n+", value)
        if part.strip(" -\t")
    ]
    return [part[:180] for part in parts if len(part) >= 8]
