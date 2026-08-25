from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Protocol
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
