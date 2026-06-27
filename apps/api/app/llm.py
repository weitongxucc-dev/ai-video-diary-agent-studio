from __future__ import annotations

import json
from typing import Protocol
from uuid import uuid4

import httpx

from app.config import Settings
from app.content_templates import ContentTemplate
from app.creator_profile import CreatorProfile, creator_profile_prompt_payload
from app.models import FeedbackEvent, ScriptPackage, ScriptSection, TopicCandidate
from app.preference_memory import preference_memory_payload

SCRIPT_TARGET_WORDING = "800-1000 字，约 4-6 分钟"
SCRIPT_MIN_CHARS = 800
SCRIPT_MAX_CHARS = 1000


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
        creator_profile: CreatorProfile | None = None,
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
        creator_profile: CreatorProfile | None = None,
    ) -> ScriptSection:
        ...


class MissingScriptGenerator:
    def generate(
        self,
        topic: TopicCandidate,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
        creator_profile: CreatorProfile | None = None,
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
        creator_profile: CreatorProfile | None = None,
    ) -> ScriptSection:
        raise LLMConfigurationError("DEEPSEEK_API_KEY is not configured")


class TemplateScriptGenerator:
    def generate(
        self,
        topic: TopicCandidate,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
        creator_profile: CreatorProfile | None = None,
    ) -> ScriptPackage:
        outline = [item for item in topic.outline if item.strip()]
        first_point = outline[0] if len(outline) > 0 else topic.core_viewpoint
        second_point = outline[1] if len(outline) > 1 else topic.reason
        third_point = outline[2] if len(outline) > 2 else "把结论落到下一次行动"
        hook = (
            f"我做了半年 AI 内容后，最怕的不是没人看，而是客户突然问："
            f"“这东西到底归谁？”这次我想把「{topic.title}」讲透。"
        )
        sections = [
            ScriptSection(
                "scene",
                "真实场景",
                42,
                (
                    f"事情是从一个很具体的场景开始的。{topic.reason}"
                    f"表面看只是客户问一句边界，我解释一句流程。"
                    f"但我马上意识到，这不是补一句说明就能过去的问题。"
                    f"危险的不是做不出来，而是做出来以后，别人分不清哪些是你的方法，"
                    f"哪些是客户素材，哪些又是 AI 辅助生成。"
                ),
            ),
            ScriptSection(
                "conflict",
                "冲突拆开",
                44,
                (
                    f"这件事最容易被讲浅，因为大家会立刻把它归到版权两个字上。"
                    f"但我的判断是：{topic.core_viewpoint}"
                    f"版权只是最后被看见的名字，前面其实是一整条交付链路。"
                    f"从资料、脚本、课程结构到最终文件，"
                    f"每一环如果没有提前标注归属，最后都会变成一句难回答的话：这个东西到底算谁的？"
                ),
            ),
            ScriptSection(
                "point_1",
                "第一层判断",
                46,
                (
                    f"第一层是「{first_point}」。我以前会默认，只要核心框架是我搭的，"
                    f"客户就自然能理解这是我的原创能力。但真实合作里不能靠默认。"
                    f"客户看到的是结果，不一定看得到过程；他也不一定知道哪些素材来自他，哪些表达来自我。"
                    f"所以只要过程不透明，信任就会被消耗。不是客户为难你，"
                    f"而是你没有给他一个能放心采购和复用的说明。"
                ),
            ),
            ScriptSection(
                "point_2",
                "第二层判断",
                44,
                (
                    f"第二层是「{second_point}」。很多创作者的问题不是能力不够，"
                    f"而是交付边界写得太晚。等客户已经拿到东西，再去解释授权范围、"
                    f"二次修改和署名方式，气氛就会很被动。"
                    f"我后来反思，专业做法应该是在开工前说清三件事："
                    f"输入资料归谁，成品怎么用，AI 参与的部分怎么标注。"
                ),
            ),
            ScriptSection(
                "point_3",
                "第三层判断",
                43,
                (
                    f"第三层是「{third_point}」。如果只是口头讲一遍，下一次还会重复踩坑。"
                    f"所以我现在更倾向于把它产品化：每次课程、脚本、视频交付，"
                    f"都附一个简短的权利说明和素材清单。哪怕只有几行，也比事后解释强。"
                    f"这份说明会写清楚：课程结构、原创文案、客户资料、公开素材、AI 生成内容分别怎么处理。"
                ),
            ),
            ScriptSection(
                "reflection",
                "个人反思",
                42,
                (
                    f"这件事也提醒我，AI 创作时代的护城河，不只是你会不会调用模型。"
                    f"模型可以提速，但不能替你建立信任。真正值钱的是你能不能把混乱的过程讲清楚，"
                    f"把客户担心的问题提前处理掉，"
                    f"把一种新的创作方式变成对方敢长期合作的标准流程。"
                ),
            ),
            ScriptSection(
                "action",
                "行动建议",
                39,
                (
                    f"如果你也在做 AI 课程、视频或自动化交付，我建议下一次别只交成品。"
                    f"顺手加一页说明：用了哪些资料，哪些内容是你原创，哪些地方 AI 参与了，"
                    f"客户能怎么用，不能怎么用。"
                    f"这不是加负担，而是在降低不确定性。"
                    f"评论区告诉我，你担心版权、署名还是商用边界？"
                ),
            ),
        ]
        return ScriptPackage(
            id=f"script_{uuid4().hex[:6]}",
            hook=hook,
            sections=sections,
            title_options=[topic.title, f"第 {day_number} 天：{topic.title}", f"一个 AI 产品判断：{topic.core_viewpoint}"],
            cover_copy=f"{topic.title}\n边界讲清，合作才稳",
            publish_copy=(
                f"AI视频日记第 {day_number} 天：{topic.title}。"
                f"这次不只讲观点，也讲我从真实交付里踩到的边界问题：{topic.core_viewpoint}"
            ),
            comment_prompt="你在 AI 创作里最担心的是版权、署名，还是商用边界？",
        )

    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
        creator_profile: CreatorProfile | None = None,
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
        template: ContentTemplate | None = None,
        creator_profile: CreatorProfile | None = None,
    ) -> ScriptPackage:
        if not self._settings.deepseek_api_key:
            raise LLMConfigurationError("DEEPSEEK_API_KEY is not configured")

        payload = {
            "model": self._settings.deepseek_model,
            "messages": [
                {"role": "system", "content": self._system_prompt(template)},
                {
                    "role": "user",
                    "content": self._user_prompt(
                        topic,
                        day_number,
                        feedback_events or [],
                        template,
                        creator_profile,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
            "max_tokens": 4800,
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

        data = _json_object_from_content(
            content,
            "DeepSeek response content was not valid JSON",
        )

        return self._script_from_json(data)

    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
        template: ContentTemplate | None = None,
        creator_profile: CreatorProfile | None = None,
    ) -> ScriptSection:
        if not self._settings.deepseek_api_key:
            raise LLMConfigurationError("DEEPSEEK_API_KEY is not configured")

        current = next(
            section for section in current_script.sections if section.id == section_id
        )
        payload = {
            "model": self._settings.deepseek_model,
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
                        creator_profile,
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
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMResponseError("DeepSeek did not return valid section JSON") from exc

        data = _json_object_from_content(
            content,
            "DeepSeek did not return valid section JSON",
        )
        if not isinstance(data, dict):
            raise LLMResponseError("DeepSeek section JSON must be an object")
        if "content" not in data:
            raise LLMResponseError("DeepSeek section JSON must contain content")
        return ScriptSection(
            id=str(data.get("id") or current.id),
            title=str(data.get("title") or current.title),
            estimated_seconds=int(
                data.get("estimated_seconds") or current.estimated_seconds
            ),
            content=str(data["content"]),
            feedback=current.feedback,
        )

    def _system_prompt(self, template: ContentTemplate | None = None) -> str:
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
        return (
            "你是一个中文 AI 视频日记口播编导，服务于技术型个人创作者。"
            "只输出 JSON，不要 Markdown，不要解释。"
            "JSON 字段必须包含 hook、sections、title_options、cover_copy、publish_copy、comment_prompt。"
            "sections 至少 5 段，每段包含 id、title、estimated_seconds、content。"
            f"{template_context}"
            "hook 必须作为独立字段输出，不要放入 sections。"
            "sections 要承接 hook 之后的正文结构，并尽量对齐本次栏目的脚本结构和 topic.outline。"
            "estimated_seconds 按约 180 字/分钟估算。"
            "口播要像真人日记：直接、具体、有判断，不要营销腔。"
            "不要使用动物、怪物、玄幻、热血鸡汤类比。"
            f"成稿目标是 {SCRIPT_TARGET_WORDING}，按完整口播稿写，不要写成提纲。"
            "每段都要有现场感、推进关系和可直接念出的完整句子。"
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
            "重写后的段落要有完整展开，通常不少于 120 个中文字符，不要只给一句总结。"
        )

    def _user_prompt(
        self,
        topic: TopicCandidate,
        day_number: int,
        feedback_events: list[FeedbackEvent],
        template: ContentTemplate | None = None,
        creator_profile: CreatorProfile | None = None,
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
                "creator_profile": creator_profile_prompt_payload(creator_profile),
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
                "target_length": {
                    "chinese_characters": {
                        "min": SCRIPT_MIN_CHARS,
                        "max": SCRIPT_MAX_CHARS,
                    },
                    "spoken_duration": "4-6 分钟",
                    "pace": "按日常中文口播约 180-220 字/分钟估算",
                },
                "requirements": [
                    "生成 800-1000 字中文口播稿，总时长约 4-6 分钟。",
                    f"本次栏目参考时长：{template.recommended_duration if template else SCRIPT_TARGET_WORDING}。",
                    "hook 是独立字段",
                    "开头 3 秒要有明确冲突或判断。",
                    "sections 要与 topic.outline 或 content_template.script_structure 对齐。",
                    "sections 建议 6-7 段，除 hook 外每段至少 100 个中文字符，核心论证段建议 120-170 个中文字符。",
                    "每段都要有真实细节、个人判断和推进关系，不能只写观点标题或一句概括。",
                    "整篇要像对镜头说完的一版成稿，不要输出提纲、笔记、短句堆叠或发布摘要。",
                    "结构建议：开头冲突 -> 真实场景 -> 问题拆解 -> 三段主体判断 -> 个人反思 -> 行动建议/评论引导。",
                    "优先遵守 preference_memory：prefer 是应该强化的表达方式，avoid 是应该避开的表达方式。",
                    "优先遵守 creator_profile",
                    "creator_profile 用来稳定口播语气、标题、封面和发布文案边界，不要把 profile 当作事实来源。",
                    "estimated_seconds 按约 180-220 字/分钟估算。",
                    "保留三段核心原因，每段都要有一段可直接口播的展开内容。",
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
        creator_profile: CreatorProfile | None = None,
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
                "creator_profile": creator_profile_prompt_payload(creator_profile),
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
                    "优先遵守 creator_profile",
                    "creator_profile 用来保持重写段落的口吻边界，但只改 target_section。",
                    "只重写 target_section，不要改 hook、标题候选、封面文案和发布文案。",
                    "不要让重写段落和上下文断裂。",
                    "重写段落要补足真实细节和推理过程，通常不少于 120 个中文字符。",
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


def _json_object_from_content(content: object, error_message: str) -> object:
    if not isinstance(content, str):
        raise LLMResponseError(error_message)

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(content):
            if char != "{":
                continue
            try:
                data, _ = decoder.raw_decode(content[index:])
            except json.JSONDecodeError:
                continue
            return data
    raise LLMResponseError(error_message)


def _section_role(index: int, total: int) -> str:
    if index <= 0:
        return "背景或第一段正文"
    if index >= total - 1:
        return "结尾行动建议"
    return f"核心论证第 {index} 段"


def script_generator_from_settings(settings: Settings) -> ScriptGenerator:
    if settings.deepseek_api_key:
        return DeepSeekScriptGenerator(settings)
    return MissingScriptGenerator()
