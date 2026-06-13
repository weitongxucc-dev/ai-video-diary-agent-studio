from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

import httpx

from app.config import Settings
from app.content_templates import ContentTemplate
from app.creator_profile import CreatorProfile, creator_profile_prompt_payload
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


DEFAULT_TOPIC_CANDIDATE_COUNT = 5


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
        creator_profile: CreatorProfile | None = None,
    ) -> list[TopicCandidate]:
        ...


class MissingTopicGenerator:
    def generate(
        self,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
        creator_profile: CreatorProfile | None = None,
    ) -> list[TopicCandidate]:
        raise TopicGenerationConfigurationError("DeepSeek and at least one topic source must be configured")


class TemplateTopicGenerator:
    def generate(
        self,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
        creator_profile: CreatorProfile | None = None,
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
            TopicCandidate(
                id=f"topic_{uuid4().hex[:6]}",
                title="旧笔记里的真实复盘，往往比今天的新鲜输入更有价值",
                core_viewpoint="选题池应该同时看今天的新输入和过去沉淀下来的高价值经验。",
                reason="示例数据：正式运行时会混合 Obsidian 近期输入和历史沉淀。",
                audience="技术型个人创作者、AI 产品经理",
                outline=["今天输入负责新鲜度", "历史复盘负责厚度", "组合后形成判断"],
                score=80,
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
        template: ContentTemplate | None = None,
        creator_profile: CreatorProfile | None = None,
    ) -> list[TopicCandidate]:
        if not self._settings.deepseek_api_key:
            raise TopicGenerationConfigurationError("DEEPSEEK_API_KEY is not configured")
        source_items = self._vault.topic_source_items() + self._external_source_provider()
        if not source_items:
            raise TopicGenerationConfigurationError("No topic sources are available")

        payload = {
            "model": self._settings.deepseek_model,
            "messages": [
                {"role": "system", "content": self._system_prompt(template)},
                {
                    "role": "user",
                    "content": self._user_prompt(
                        day_number,
                        source_items,
                        feedback_events or [],
                        template,
                        creator_profile,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.75,
            "max_tokens": 8000,
        }
        url = f"{self._settings.deepseek_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self._settings.deepseek_api_key}"}

        data: object | None = None
        parse_error: TopicGenerationResponseError | None = None
        for attempt in range(2):
            request_payload = dict(payload)
            if attempt > 0:
                request_payload["temperature"] = 0.35
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                response = client.post(url, json=request_payload, headers=headers)

            try:
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
            except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise TopicGenerationResponseError("DeepSeek did not return valid topic JSON") from exc

            try:
                data = self._json_from_content(content)
                break
            except TopicGenerationResponseError as exc:
                parse_error = exc
                if attempt == 1:
                    raise parse_error

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
            f"{DEFAULT_TOPIC_CANDIDATE_COUNT} 个选题之间必须有明显不同的切入角度，禁止同义反复。"
            "score 评分标准：真实案例支撑度 30 分、观点鲜明度 30 分、口播转化度 20 分、与近期偏好匹配度 20 分。"
            "source_refs 只能使用用户提供 source_items 里的 source_type、title、ref，不能编造不存在的来源。"
        )

    def _user_prompt(
        self,
        day_number: int,
        source_items: list[TopicSourceItem],
        feedback_events: list[FeedbackEvent],
        template: ContentTemplate | None = None,
        creator_profile: CreatorProfile | None = None,
    ) -> str:
        return json.dumps(
            {
                "day_number": day_number,
                "content_template": _content_template_payload(template),
                "creator_profile": creator_profile_prompt_payload(creator_profile),
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
                    f"生成 {DEFAULT_TOPIC_CANDIDATE_COUNT} 个适合 2-3 分钟 AI 视频日记的候选选题。",
                    "每个选题必须符合 content_template 的场景、选题结构和标题规则。",
                    "优先遵守 preference_memory",
                    "优先遵守 creator_profile",
                    "preference_memory.prefer 用来放大选题方向，preference_memory.avoid 用来避开低分表达。",
                    "creator_profile 用来稳定创作者口吻和长期内容边界，但 source_refs 仍必须来自 source_items。",
                    "候选池要同时参考素材定位为“近期输入”和“历史沉淀”的 source_items；历史沉淀中有价值的复盘、案例、工作流经验可以被推荐。",
                    "近期输入代表今天的新鲜度，历史沉淀代表长期判断；两者都不能完全压过另一方。",
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
        for raw_topic in data["topics"][:DEFAULT_TOPIC_CANDIDATE_COUNT]:
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
                    outline=self._outline(raw_topic.get("outline")),
                    score=self._score(raw_topic.get("score")),
                    source_refs=source_refs,
                )
            )
        if not topics:
            raise TopicGenerationResponseError("DeepSeek returned no topic candidates")
        return topics

    def _json_from_content(self, content: object) -> object:
        if not isinstance(content, str):
            raise TopicGenerationResponseError("DeepSeek topic content was not valid JSON")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            json_text = self._extract_json_object(content)
            if json_text is None:
                raise TopicGenerationResponseError("DeepSeek topic content was not valid JSON")
            try:
                return json.loads(json_text)
            except json.JSONDecodeError as exc:
                raise TopicGenerationResponseError("DeepSeek topic content was not valid JSON") from exc

    def _extract_json_object(self, content: str) -> str | None:
        start = content.find("{")
        if start < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(content)):
            char = content[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return content[start : index + 1]
        return None

    def _outline(self, raw_outline: object) -> list[str]:
        if isinstance(raw_outline, list):
            outline = [str(item).strip() for item in raw_outline if str(item).strip()]
            if len(outline) == 1:
                split_outline = self._split_outline_text(outline[0])
                if len(split_outline) > 1:
                    outline = split_outline
        elif isinstance(raw_outline, str):
            outline = self._split_outline_text(raw_outline)
        else:
            raise TopicGenerationResponseError("DeepSeek topic outline must be a list or text")
        if not outline:
            raise TopicGenerationResponseError("DeepSeek topic outline must not be empty")
        return outline[:3]

    def _split_outline_text(self, text: str) -> list[str]:
        normalized = text.strip()
        if not normalized:
            return []
        parts = re.split(r"(?:^|\n)\s*(?:\d+|[一二三四五六七八九十])[\.\)、）:：]\s*", normalized)
        outline = [part.strip() for part in parts if part.strip()]
        if len(outline) >= 2:
            return [self._trim_outline_sentence(part) for part in outline]
        labeled_parts = re.split(
            r"\s*(?=(?:开头3秒|主要经历|观点反思|行动建议|方法建议|结尾)[：:])",
            normalized,
        )
        outline = [part.strip() for part in labeled_parts if part.strip()]
        if len(outline) >= 2:
            return [self._trim_outline_sentence(part) for part in outline]
        return [self._trim_outline_sentence(line.strip()) for line in normalized.splitlines() if line.strip()]

    def _trim_outline_sentence(self, text: str) -> str:
        return text.strip().rstrip("。；;")

    def _score(self, raw_score: object) -> int:
        if isinstance(raw_score, dict):
            values = [self._score_value(value) for value in raw_score.values()]
            numeric_values = [value for value in values if value is not None]
            if not numeric_values:
                raise TopicGenerationResponseError("DeepSeek topic score must be numeric")
            return max(0, min(100, round(sum(numeric_values))))
        score = self._score_value(raw_score)
        if score is None:
            raise TopicGenerationResponseError("DeepSeek topic score must be numeric")
        return max(0, min(100, round(score)))

    def _score_value(self, value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            try:
                return float(normalized)
            except ValueError:
                return None
        return None

    def _source_refs(
        self,
        raw_topic: dict[str, object],
        sources_by_ref: dict[str, TopicSourceItem],
    ) -> list[SourceRef]:
        raw_refs = raw_topic.get("source_refs")
        refs: list[SourceRef] = []
        if isinstance(raw_refs, list):
            for raw_ref in raw_refs:
                if isinstance(raw_ref, str):
                    source = sources_by_ref.get(raw_ref)
                    if source:
                        refs.append(SourceRef(source.source_type, source.title, source.ref))
                    continue
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
