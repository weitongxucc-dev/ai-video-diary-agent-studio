from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

import httpx

from app.config import Settings
from app.external_sources import ExternalSourceResponseError, HtmlPageSourceClient
from app.hermes_radar import HermesRadarSourceClient
from app.models import FeedbackEvent, SourceRef, TopicCandidate
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


class TopicGenerator(Protocol):
    def generate(
        self,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> list[TopicCandidate]:
        ...


class MissingTopicGenerator:
    def generate(
        self,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> list[TopicCandidate]:
        raise TopicGenerationConfigurationError("DeepSeek and at least one topic source must be configured")


class TemplateTopicGenerator:
    def generate(
        self,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
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
        self._external_source_provider = external_source_provider or (lambda: [])
        self._transport = transport
        self._timeout = timeout

    def generate(
        self,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> list[TopicCandidate]:
        if not self._settings.deepseek_api_key:
            raise TopicGenerationConfigurationError("DEEPSEEK_API_KEY is not configured")
        notes = self._vault.recent_notes(limit=16)
        source_items = [note.to_topic_source() for note in notes] + self._external_source_provider()
        if not source_items:
            raise TopicGenerationConfigurationError("No topic sources are available")

        payload = {
            "model": self._settings.deepseek_model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": self._user_prompt(day_number, source_items, feedback_events or [])},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.75,
            "max_tokens": 3200,
        }
        url = f"{self._settings.deepseek_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self._settings.deepseek_api_key}"}

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

    def _system_prompt(self) -> str:
        return (
            "你是一个中文 AI 视频日记选题 Agent。只输出 JSON，不要 Markdown，不要解释。"
            "JSON 顶层必须是 {\"topics\": [...]}。"
            "每个 topic 必须包含 title、core_viewpoint、reason、audience、outline、score、source_refs。"
            "source_refs 只能使用用户提供 source_items 里的 source_type、title、ref，不能编造不存在的来源。"
        )

    def _user_prompt(
        self,
        day_number: int,
        source_items: list[TopicSourceItem],
        feedback_events: list[FeedbackEvent],
    ) -> str:
        return json.dumps(
            {
                "day_number": day_number,
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
                "requirements": [
                    "生成 4 个适合 2-3 分钟 AI 视频日记的候选选题。",
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


def topic_generator_from_settings(settings: Settings, vault: ObsidianVault) -> TopicGenerator:
    if settings.deepseek_api_key and (
        vault.exists
        or settings.product_hunt_token
        or settings.topic_source_urls
        or settings.hermes_radar_path
    ):
        return DeepSeekTopicGenerator(
            settings,
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
