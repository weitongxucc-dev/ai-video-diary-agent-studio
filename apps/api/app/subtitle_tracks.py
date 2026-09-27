from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from app.config import structured_completion_options

if TYPE_CHECKING:
    from app.config import Settings


SubtitleTrackStatus = Literal["valid", "failed", "unavailable", "superseded"]
SubtitleTranslationStatus = Literal["succeeded", "failed", "unavailable"]

_PROMPT_SCHEMA_VERSION = "subtitle-translation/v1"
_MAX_CUES = 1_000
_MAX_CUE_TEXT = 4_000
_MAX_TOTAL_TEXT = 100_000


@dataclass(frozen=True)
class SubtitleStyle:
    enabled: bool = True
    name: str = "default"
    font_family: str = "PingFang SC"
    font_size: int = 40
    stroke_width: int = 3
    stroke_color: str = "#000000"
    background_color: str = "#000000"
    background_opacity: float = 158 / 255
    primary_color: str = "#FFFFFF"
    emphasis_color: str = "#FFD54F"
    position: Literal["top", "middle", "bottom"] = "bottom"
    bilingual_order: Literal[
        "source_then_translation", "translation_then_source"
    ] = "source_then_translation"


@dataclass(frozen=True)
class SubtitleCue:
    id: str
    text: str
    start_seconds: float
    end_seconds: float
    source_cue_id: str | None = None
    speaker: str | None = None
    line_breaks: tuple[int, ...] = ()
    emphasis: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubtitleTrack:
    """Immutable execution DTO; ProductionRepository remains the fact owner."""

    id: str
    workspace_id: str
    task_id: str
    timeline_version_id: str
    language: str
    source_language: str
    style: SubtitleStyle
    cues: tuple[SubtitleCue, ...]
    generation_method: Literal["timeline", "translation"]
    status: SubtitleTrackStatus = "valid"
    model_id: str | None = None
    artifact_ref: str | None = None
    transform_id: str | None = None


@dataclass(frozen=True)
class SubtitleTranslation:
    source_cue_id: str
    text: str


@dataclass(frozen=True)
class SubtitleTranslationProviderResult:
    translations: tuple[SubtitleTranslation, ...]
    provider_execution_id: str | None = None


class SubtitleTranslationProviderError(RuntimeError):
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


class SubtitleTranslationProvider(Protocol):
    provider_name: str
    model_id: str

    @property
    def ready(self) -> bool: ...

    @property
    def message(self) -> str: ...

    def translate(
        self, cues: tuple[SubtitleCue, ...]
    ) -> SubtitleTranslationProviderResult: ...


@dataclass(frozen=True)
class SubtitleExecutionEvidence:
    track_ref: str
    timeline_version_id: str
    cue_ids: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class SubtitleTranslationExecution:
    execution_id: str
    status: SubtitleTranslationStatus
    source_track: SubtitleTrack
    translated_track: SubtitleTrack | None
    provider_name: str
    model_id: str
    prompt_schema_version: str
    input_evidence: SubtitleExecutionEvidence | None
    output_evidence: SubtitleExecutionEvidence | None
    provider_execution_id: str | None = None
    failure_code: str | None = None
    failure_reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    retryable: bool | None = None


class UnavailableSubtitleTranslationProvider:
    def __init__(
        self,
        *,
        provider_name: str,
        model_id: str,
        message: str,
    ) -> None:
        self.provider_name = provider_name.strip() or "unconfigured"
        self.model_id = model_id.strip() or "unconfigured"
        self._message = message.strip()

    @property
    def ready(self) -> bool:
        return False

    @property
    def message(self) -> str:
        return self._message

    def translate(
        self, cues: tuple[SubtitleCue, ...]
    ) -> SubtitleTranslationProviderResult:
        del cues
        raise SubtitleTranslationProviderError(
            code="capability_unavailable",
            reason="文字模型翻译能力不可用。",
            impact="未生成英文字幕轨；中文字幕轨保持不变。",
            repair_action=self.message,
            retryable=False,
        )


class OpenAICompatibleSubtitleTranslationProvider:
    """Translate explicit cues through the configured OpenAI-compatible route."""

    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        model_id: str,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.provider_name = provider_name.strip() or "custom"
        self.model_id = model_id.strip()
        self._base_url = base_url.strip().rstrip("/")
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout
        parsed_url = urlsplit(self._base_url)
        self._ready = bool(
            self.model_id
            and self._base_url
            and parsed_url.scheme in {"http", "https"}
            and parsed_url.hostname
        )

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def message(self) -> str:
        if self.ready:
            return "文字模型已配置；确认生成后将实际调用，余额与服务状态以调用结果为准。"
        return "在本机运行时设置中填写有效的 OpenAI-compatible 地址和模型标识。"

    def translate(
        self, cues: tuple[SubtitleCue, ...]
    ) -> SubtitleTranslationProviderResult:
        if not self.ready:
            raise self._failure(
                code="route_configuration_invalid",
                reason="文字模型连接缺少有效服务地址或模型标识。",
                impact="未生成英文字幕轨；中文字幕轨保持不变。",
                repair_action=self.message,
                retryable=False,
            )
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是中文字幕到英文字幕的翻译器。只返回单一 JSON object，"
                        "不要 Markdown。顶层必须为 {\"translations\": [...]}。"
                        "每项只包含 source_cue_id 和 text，source_cue_id、顺序和数量"
                        "必须与输入完全一致。只翻译文本，不合并、拆分或新增 cue；"
                        "保留输入文本中的人工换行。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_language": "zh-CN",
                            "target_language": "en-US",
                            "cues": [
                                {
                                    "source_cue_id": cue.id,
                                    "text": _text_with_line_breaks(
                                        cue.text, cue.line_breaks
                                    ),
                                }
                                for cue in cues
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": min(8_000, max(1_000, sum(len(cue.text) for cue in cues) * 3)),
            **structured_completion_options(self.model_id),
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
                impact="未生成英文字幕轨；中文字幕轨保持不变。",
                repair_action="检查本机模型连接、网络或本地服务后重试。",
                retryable=True,
            ) from exc
        if response.is_error:
            status = response.status_code
            repair = {
                401: "检查当前文字模型的 API 密钥后重试。",
                402: "检查当前文字模型账号的余额或付费状态后重试。",
                403: "检查账号是否有权使用当前模型。",
                404: "检查文字模型地址和模型名称。",
                429: "请求受到限流或配额限制；检查配额并稍后重试。",
            }.get(status, "检查模型服务状态和请求配置后重试。")
            raise self._failure(
                code=f"http_{status}",
                reason=f"文字模型服务拒绝了字幕翻译请求（HTTP {status}）。",
                impact="未生成英文字幕轨；中文字幕轨保持不变。",
                repair_action=repair,
                retryable=status == 429 or status >= 500,
            )
        try:
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
            document = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise self._failure(
                code="response_envelope_invalid",
                reason="文字模型返回的字幕翻译协议无效。",
                impact="未保存无效英文字幕；中文字幕轨保持不变。",
                repair_action="检查 OpenAI-compatible JSON 响应格式后重试。",
                retryable=True,
            ) from exc
        translations = self._validated_translations(document, cues)
        provider_execution_id = envelope.get("id")
        return SubtitleTranslationProviderResult(
            translations=translations,
            provider_execution_id=(
                provider_execution_id
                if isinstance(provider_execution_id, str) and provider_execution_id.strip()
                else None
            ),
        )

    def _validated_translations(
        self, document: object, cues: tuple[SubtitleCue, ...]
    ) -> tuple[SubtitleTranslation, ...]:
        raw_translations = document.get("translations") if isinstance(document, dict) else None
        if not isinstance(raw_translations, list) or len(raw_translations) != len(cues):
            raise self._schema_failure("文字模型返回的翻译 cue 数量与中文轨不一致。")
        translations: list[SubtitleTranslation] = []
        for source, raw in zip(cues, raw_translations, strict=True):
            if not isinstance(raw, dict) or raw.get("source_cue_id") != source.id:
                raise self._schema_failure("文字模型改变了字幕 cue 的稳定映射或顺序。")
            text = raw.get("text")
            if not _valid_rendered_translation_text(text):
                raise self._schema_failure("文字模型返回了空白、过长或断行无效的字幕文本。")
            translations.append(SubtitleTranslation(source_cue_id=source.id, text=text))
        return tuple(translations)

    def _schema_failure(self, reason: str) -> SubtitleTranslationProviderError:
        return self._failure(
            code="response_schema_invalid",
            reason=reason,
            impact="未保存无效英文字幕；中文字幕轨保持不变。",
            repair_action="检查模型 JSON 输出结构及 cue 映射后重试。",
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
    ) -> SubtitleTranslationProviderError:
        return SubtitleTranslationProviderError(
            code=code,
            reason=reason,
            impact=impact,
            repair_action=repair_action,
            retryable=retryable,
        )


def subtitle_translation_provider_from_settings(
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 60.0,
) -> SubtitleTranslationProvider:
    if not settings.llm_configured:
        return UnavailableSubtitleTranslationProvider(
            provider_name=settings.effective_llm_provider,
            model_id=settings.effective_llm_model,
            message="配置可用的文字模型与凭据后重试字幕翻译。",
        )
    return OpenAICompatibleSubtitleTranslationProvider(
        provider_name=settings.effective_llm_provider,
        base_url=settings.effective_llm_base_url,
        model_id=settings.effective_llm_model,
        api_key=settings.effective_llm_api_key,
        transport=transport,
        timeout=timeout,
    )


class SubtitleTranslationService:
    def __init__(self, provider: SubtitleTranslationProvider) -> None:
        self.provider = provider

    def translate_zh_to_en(
        self,
        source_track: SubtitleTrack,
        *,
        translated_track_id: str,
        timeline_version_id: str,
    ) -> SubtitleTranslationExecution:
        execution_id = f"subtitle_translation_{uuid4().hex[:12]}"
        validation_failure = _track_validation_failure(source_track)
        if validation_failure is not None:
            code, reason, repair_action = validation_failure
            return self._failed_execution(
                execution_id=execution_id,
                source_track=source_track,
                code=code,
                reason=reason,
                impact="未调用文字模型，未生成英文字幕轨；中文字幕轨保持不变。",
                repair_action=repair_action,
                retryable=False,
            )
        if source_track.language != "zh-CN" or source_track.status != "valid":
            return self._failed_execution(
                execution_id=execution_id,
                source_track=source_track,
                code="source_track_invalid",
                reason="字幕翻译需要当前时间线中的有效简体中文轨。",
                impact="未调用文字模型，未生成英文字幕轨；现有字幕轨保持不变。",
                repair_action="选择当前时间线版本的有效 zh-CN 字幕轨后重试。",
                retryable=False,
            )
        if not translated_track_id.strip() or not timeline_version_id.strip():
            return self._failed_execution(
                execution_id=execution_id,
                source_track=source_track,
                code="target_identity_invalid",
                reason="英文字幕轨缺少稳定标识或目标时间线版本。",
                impact="未调用文字模型，未生成英文字幕轨；中文字幕轨保持不变。",
                repair_action="由当前 CreationTask 和 TimelineVersion 分配英文轨标识后重试。",
                retryable=False,
            )
        input_evidence = _track_evidence(source_track)
        if not self.provider.ready:
            return SubtitleTranslationExecution(
                execution_id=execution_id,
                status="unavailable",
                source_track=source_track,
                translated_track=None,
                provider_name=self.provider.provider_name,
                model_id=self.provider.model_id,
                prompt_schema_version=_PROMPT_SCHEMA_VERSION,
                input_evidence=input_evidence,
                output_evidence=None,
                failure_code="capability_unavailable",
                failure_reason="文字模型翻译能力不可用。",
                impact="未生成英文字幕轨；中文字幕轨保持不变。",
                repair_action=self.provider.message,
                retryable=False,
            )
        try:
            provider_result = self.provider.translate(source_track.cues)
            translations = _validated_provider_result(provider_result, source_track.cues)
        except SubtitleTranslationProviderError as exc:
            return self._failed_execution(
                execution_id=execution_id,
                source_track=source_track,
                code=exc.code,
                reason=exc.reason,
                impact=exc.impact,
                repair_action=exc.repair_action,
                retryable=exc.retryable,
                input_evidence=input_evidence,
            )
        translated_cues: list[SubtitleCue] = []
        for source, translation in zip(source_track.cues, translations, strict=True):
            translated_text, line_breaks = _text_and_line_breaks(translation.text)
            translated_cues.append(
                SubtitleCue(
                    id=f"{translated_track_id}:{source.id}",
                    source_cue_id=source.id,
                    text=translated_text,
                    start_seconds=source.start_seconds,
                    end_seconds=source.end_seconds,
                    speaker=source.speaker,
                    line_breaks=line_breaks,
                )
            )
        translated_track = SubtitleTrack(
            id=translated_track_id,
            workspace_id=source_track.workspace_id,
            task_id=source_track.task_id,
            timeline_version_id=timeline_version_id,
            language="en-US",
            source_language=source_track.language,
            style=source_track.style,
            cues=tuple(translated_cues),
            generation_method="translation",
            status="valid",
            model_id=self.provider.model_id,
            transform_id=execution_id,
        )
        return SubtitleTranslationExecution(
            execution_id=execution_id,
            status="succeeded",
            source_track=source_track,
            translated_track=translated_track,
            provider_name=self.provider.provider_name,
            model_id=self.provider.model_id,
            prompt_schema_version=_PROMPT_SCHEMA_VERSION,
            input_evidence=input_evidence,
            output_evidence=_track_evidence(translated_track),
            provider_execution_id=provider_result.provider_execution_id,
        )

    def _failed_execution(
        self,
        *,
        execution_id: str,
        source_track: SubtitleTrack,
        code: str,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
        input_evidence: SubtitleExecutionEvidence | None = None,
    ) -> SubtitleTranslationExecution:
        return SubtitleTranslationExecution(
            execution_id=execution_id,
            status="failed",
            source_track=source_track,
            translated_track=None,
            provider_name=self.provider.provider_name,
            model_id=self.provider.model_id,
            prompt_schema_version=_PROMPT_SCHEMA_VERSION,
            input_evidence=input_evidence,
            output_evidence=None,
            failure_code=code,
            failure_reason=reason,
            impact=impact,
            repair_action=repair_action,
            retryable=retryable,
        )


def subtitle_track_to_payload(track: SubtitleTrack) -> dict[str, object]:
    payload = asdict(track)
    cue_payloads: list[dict[str, object]] = []
    for cue in track.cues:
        cue_payload = asdict(cue)
        cue_payload["line_breaks"] = list(cue.line_breaks)
        cue_payload["emphasis"] = list(cue.emphasis)
        cue_payloads.append(cue_payload)
    payload["cues"] = cue_payloads
    payload["style"] = asdict(track.style)
    return payload


def subtitle_track_to_json(track: SubtitleTrack) -> str:
    return json.dumps(
        subtitle_track_to_payload(track),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def render_srt(track: SubtitleTrack) -> str:
    failure = _track_validation_failure(track)
    if failure is not None:
        raise ValueError(failure[1])
    blocks = [
        _srt_block(
            index,
            _seconds_to_milliseconds(cue.start_seconds),
            _seconds_to_milliseconds(cue.end_seconds),
            _text_with_line_breaks(cue.text, cue.line_breaks),
        )
        for index, cue in enumerate(track.cues, start=1)
    ]
    return "\n\n".join(blocks) + "\n"


def render_bilingual_srt(source: SubtitleTrack, translation: SubtitleTrack) -> str:
    for track in (source, translation):
        failure = _track_validation_failure(track)
        if failure is not None:
            raise ValueError(failure[1])
    if source.task_id != translation.task_id:
        raise ValueError("双语字幕轨不属于同一个 CreationTask")
    if len(source.cues) != len(translation.cues):
        raise ValueError("双语字幕轨 cue 数量不一致")
    blocks: list[str] = []
    for index, (source_cue, translated_cue) in enumerate(
        zip(source.cues, translation.cues, strict=True), start=1
    ):
        if (
            translated_cue.source_cue_id != source_cue.id
            or translated_cue.start_seconds != source_cue.start_seconds
            or translated_cue.end_seconds != source_cue.end_seconds
        ):
            raise ValueError("双语字幕轨 cue 映射或时间不一致")
        source_text = _text_with_line_breaks(source_cue.text, source_cue.line_breaks)
        translated_text = _text_with_line_breaks(
            translated_cue.text, translated_cue.line_breaks
        )
        text = (
            f"{source_text}\n{translated_text}"
            if source.style.bilingual_order == "source_then_translation"
            else f"{translated_text}\n{source_text}"
        )
        blocks.append(
            _srt_block(
                index,
                _seconds_to_milliseconds(source_cue.start_seconds),
                _seconds_to_milliseconds(source_cue.end_seconds),
                text,
            )
        )
    return "\n\n".join(blocks) + "\n"


def _validated_provider_result(
    result: SubtitleTranslationProviderResult,
    source_cues: tuple[SubtitleCue, ...],
) -> tuple[SubtitleTranslation, ...]:
    if not isinstance(result, SubtitleTranslationProviderResult):
        raise SubtitleTranslationProviderError(
            code="provider_contract_invalid",
            reason="文字模型适配器返回了无效结果。",
            impact="未保存无效英文字幕；中文字幕轨保持不变。",
            repair_action="修复字幕翻译 provider 的结构化返回协议后重试。",
            retryable=False,
        )
    translations = result.translations
    if len(translations) != len(source_cues):
        raise _provider_mapping_failure()
    for source, translation in zip(source_cues, translations, strict=True):
        if (
            not isinstance(translation, SubtitleTranslation)
            or translation.source_cue_id != source.id
            or not _valid_rendered_translation_text(translation.text)
        ):
            raise _provider_mapping_failure()
    return translations


def _provider_mapping_failure() -> SubtitleTranslationProviderError:
    return SubtitleTranslationProviderError(
        code="provider_contract_invalid",
        reason="文字模型适配器改变了 cue 映射或返回了无效文本。",
        impact="未保存无效英文字幕；中文字幕轨保持不变。",
        repair_action="修复 provider 的逐 cue 结构化输出后重试。",
        retryable=False,
    )


def _track_validation_failure(
    track: SubtitleTrack,
) -> tuple[str, str, str] | None:
    if not all(
        value.strip()
        for value in (
            track.id,
            track.workspace_id,
            track.task_id,
            track.timeline_version_id,
            track.language,
            track.source_language,
        )
    ):
        return (
            "source_track_invalid",
            "字幕轨缺少工作区、任务、版本或语言标识。",
            "重新读取当前 CreationTask 的版本化字幕轨后重试。",
        )
    if not track.cues or len(track.cues) > _MAX_CUES:
        return (
            "source_track_invalid",
            "字幕轨 cue 数量为空或超过单次执行上限。",
            "选择包含 1 到 1000 条现有 cue 的字幕轨后重试。",
        )
    seen_ids: set[str] = set()
    total_text = 0
    for cue in track.cues:
        total_text += len(cue.text)
        if (
            not cue.id.strip()
            or cue.id in seen_ids
            or not cue.text.strip()
            or len(cue.text) > _MAX_CUE_TEXT
            or total_text > _MAX_TOTAL_TEXT
            or "\r" in cue.text
            or "\n" in cue.text
            or isinstance(cue.start_seconds, bool)
            or isinstance(cue.end_seconds, bool)
            or not isinstance(cue.start_seconds, (int, float))
            or not isinstance(cue.end_seconds, (int, float))
            or not math.isfinite(cue.start_seconds)
            or not math.isfinite(cue.end_seconds)
            or cue.start_seconds < 0
            or cue.end_seconds <= cue.start_seconds
            or not _valid_line_breaks(cue.text, cue.line_breaks)
        ):
            return (
                "source_track_invalid",
                "字幕轨包含重复 id、无效文本或无效的现有时间范围。",
                "修复当前字幕轨的 cue 标识、文本或时间后重试；不要重新猜测时轴。",
            )
        seen_ids.add(cue.id)
    return None


def _track_evidence(track: SubtitleTrack) -> SubtitleExecutionEvidence:
    serialized = subtitle_track_to_json(track)
    return SubtitleExecutionEvidence(
        track_ref=f"subtitle-track://{track.id}",
        timeline_version_id=track.timeline_version_id,
        cue_ids=tuple(cue.id for cue in track.cues),
        sha256=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    )


def _srt_block(index: int, start_ms: int, end_ms: int, text: str) -> str:
    return f"{index}\n{_srt_timestamp(start_ms)} --> {_srt_timestamp(end_ms)}\n{text}"


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _seconds_to_milliseconds(seconds: float) -> int:
    return round(seconds * 1_000)


def _valid_line_breaks(text: str, line_breaks: tuple[int, ...]) -> bool:
    if any(isinstance(offset, bool) or not isinstance(offset, int) for offset in line_breaks):
        return False
    return line_breaks == tuple(sorted(set(line_breaks))) and all(
        0 < offset < len(text) for offset in line_breaks
    )


def _valid_rendered_translation_text(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= _MAX_CUE_TEXT
        and "\r" not in value
        and all(line.strip() for line in value.split("\n"))
    )


def _text_with_line_breaks(text: str, line_breaks: tuple[int, ...]) -> str:
    if not line_breaks:
        return text
    boundaries = (0, *line_breaks, len(text))
    return "\n".join(
        text[start:end] for start, end in zip(boundaries, boundaries[1:])
    )


def _text_and_line_breaks(rendered_text: str) -> tuple[str, tuple[int, ...]]:
    lines = rendered_text.split("\n")
    text = "".join(lines)
    offsets: list[int] = []
    consumed = 0
    for line in lines[:-1]:
        consumed += len(line)
        offsets.append(consumed)
    return text, tuple(offsets)
