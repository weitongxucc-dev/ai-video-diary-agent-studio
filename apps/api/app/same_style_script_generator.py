from __future__ import annotations

import json
from typing import Protocol

import httpx

from app.config import Settings
from app.schemas import SameStyleReferenceJobRequest


class SameStyleScriptGenerationError(RuntimeError):
    pass


class SameStyleScriptGenerator(Protocol):
    def generate(
        self,
        request: SameStyleReferenceJobRequest,
        *,
        transcript: str,
        shot_manifest: list[dict[str, object]],
    ) -> dict[str, object]:
        ...


class TemplateSameStyleScriptGenerator:
    def generate(
        self,
        request: SameStyleReferenceJobRequest,
        *,
        transcript: str,
        shot_manifest: list[dict[str, object]],
    ) -> dict[str, object]:
        product_name = request.product_name.strip() or "你的产品"
        product_brief = (
            request.product_brief.strip()
            or "把对标视频的卖点替换成自己的产品利益点"
        )
        benchmark_hook = _compact_text(transcript, 42)
        section_count = max(1, len(shot_manifest))
        return {
            "hook": f"别急着划走，{product_name} 的同款爆点在这里：{benchmark_hook}",
            "sections": [
                {
                    "title": "复刻节奏",
                    "estimated_seconds": 12,
                    "content": (
                        f"原片核心节奏是先给结果，再用 {section_count} 个连续镜头证明。"
                        f"我们保留这种节奏，但把表达换成 {product_name}。"
                    ),
                },
                {
                    "title": "产品替换",
                    "estimated_seconds": 28,
                    "content": (
                        f"把画面里的卖点替换为：{product_brief}。"
                        "每一句都围绕真实使用场景讲，不照搬原视频句子。"
                    ),
                },
                {
                    "title": "转化收口",
                    "estimated_seconds": 12,
                    "content": (
                        f"最后用一个明确行动收束：如果你也想把同款流程用在 {product_name} 上，"
                        "就先从这个场景开始试。"
                    ),
                },
            ],
            "title_options": [
                f"{product_name} 同款爆款脚本",
                f"把爆款结构换成 {product_name}",
                f"{product_name} 的 3 段式带货节奏",
            ],
            "cover_copy": f"{product_name}\n同款爆款结构\n可复刻版本",
            "publish_copy": (
                f"这条视频复刻的是对标内容的节奏和镜头逻辑，"
                f"产品已替换为 {product_name}。"
            ),
            "comment_prompt": f"你觉得 {product_name} 更适合复刻哪类爆款视频？",
        }


class DeepSeekSameStyleScriptGenerator:
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
        request: SameStyleReferenceJobRequest,
        *,
        transcript: str,
        shot_manifest: list[dict[str, object]],
    ) -> dict[str, object]:
        if not self._settings.deepseek_api_key:
            raise SameStyleScriptGenerationError("DEEPSEEK_API_KEY is not configured")

        payload = {
            "model": self._settings.deepseek_model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {
                    "role": "user",
                    "content": self._user_prompt(
                        request,
                        transcript=transcript,
                        shot_manifest=shot_manifest,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.68,
            "max_tokens": 1800,
        }
        url = f"{self._settings.deepseek_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self._settings.deepseek_api_key}"}

        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            response = client.post(url, json=payload, headers=headers)

        try:
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise SameStyleScriptGenerationError(
                "DeepSeek did not return a valid same-style completion"
            ) from exc

        data = _json_object_from_content(content)
        return _draft_from_json(data)

    def _system_prompt(self) -> str:
        return (
            "你是中文短视频爆款复刻编导。"
            "你只复刻结构，不复制原文案、不复制人物身份、不承诺未经验证的效果。"
            "只输出 JSON，不要 Markdown，不要解释。"
            "JSON 字段必须包含 hook、sections、title_options、cover_copy、publish_copy、comment_prompt。"
            "sections 每段包含 title、estimated_seconds、content。"
            "口吻要像真人创作者，具体、短句、有场景，不要营销腔。"
        )

    def _user_prompt(
        self,
        request: SameStyleReferenceJobRequest,
        *,
        transcript: str,
        shot_manifest: list[dict[str, object]],
    ) -> str:
        return json.dumps(
            {
                "reference_url": request.reference_url,
                "product": {
                    "name": request.product_name,
                    "brief": request.product_brief,
                },
                "aspect_ratio": request.aspect_ratio,
                "rewrite_mode": request.rewrite_mode,
                "benchmark_transcript": transcript,
                "shot_manifest": shot_manifest,
                "requirements": [
                    "保留对标视频的镜头结构、转化逻辑和节奏。",
                    "不要复制原视频原文案，只复刻表达结构。",
                    "把商品、场景利益点、行动引导替换为自有产品。",
                    "生成 1 个三秒钩子、2 到 5 段正文、3 个标题候选、封面文案、发布文案和评论引导。",
                    "如果 rewrite_mode 是 shot，则更强调镜头/分镜逻辑，口播可以更简短。",
                ],
            },
            ensure_ascii=False,
        )


def same_style_script_generator_from_settings(
    settings: Settings,
) -> SameStyleScriptGenerator:
    if settings.deepseek_api_key:
        return DeepSeekSameStyleScriptGenerator(settings)
    return TemplateSameStyleScriptGenerator()


def _draft_from_json(data: object) -> dict[str, object]:
    if not isinstance(data, dict):
        raise SameStyleScriptGenerationError("same-style script JSON must be an object")
    raw_sections = data.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise SameStyleScriptGenerationError(
            "same-style script JSON must contain sections"
        )

    sections: list[dict[str, object]] = []
    for raw_section in raw_sections:
        if not isinstance(raw_section, dict):
            raise SameStyleScriptGenerationError(
                "same-style script section must be an object"
            )
        sections.append(
            {
                "title": str(raw_section["title"]),
                "estimated_seconds": int(raw_section["estimated_seconds"]),
                "content": str(raw_section["content"]),
            }
        )

    return {
        "hook": str(data["hook"]),
        "sections": sections,
        "title_options": [str(item) for item in data["title_options"]],
        "cover_copy": str(data["cover_copy"]),
        "publish_copy": str(data["publish_copy"]),
        "comment_prompt": str(data["comment_prompt"]),
    }


def _json_object_from_content(content: object) -> object:
    if not isinstance(content, str):
        raise SameStyleScriptGenerationError(
            "DeepSeek same-style response content was not valid JSON"
        )
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
    raise SameStyleScriptGenerationError(
        "DeepSeek same-style response content was not valid JSON"
    )


def _compact_text(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars].rstrip()}..."
