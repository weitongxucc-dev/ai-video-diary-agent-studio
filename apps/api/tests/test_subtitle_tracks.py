from __future__ import annotations

import json

import httpx
import pytest

from app.subtitle_tracks import (
    OpenAICompatibleSubtitleTranslationProvider,
    SubtitleCue,
    SubtitleStyle,
    SubtitleTrack,
    SubtitleTranslation,
    SubtitleTranslationProviderError,
    SubtitleTranslationProviderResult,
    SubtitleTranslationService,
    UnavailableSubtitleTranslationProvider,
    render_bilingual_srt,
    render_srt,
    subtitle_track_to_json,
)


def _source_track() -> SubtitleTrack:
    return SubtitleTrack(
        id="subtitle_zh_v3",
        workspace_id="workspace_a",
        task_id="task_a",
        timeline_version_id="timeline_v3",
        language="zh-CN",
        source_language="zh-CN",
        style=SubtitleStyle(name="yellow-emphasis"),
        cues=(
            SubtitleCue(
                id="cue_001",
                text="别只讲概念先看真实运行",
                start_seconds=1.25,
                end_seconds=4.9,
                speaker="narrator",
                line_breaks=(5,),
                emphasis=("真实运行",),
            ),
            SubtitleCue(
                id="cue_002",
                text="这才是结果。",
                start_seconds=4.9,
                end_seconds=7.125,
                speaker="narrator",
            ),
        ),
        generation_method="timeline",
    )


@pytest.mark.parametrize("status,repair,retryable", [
    (401, "API 密钥", False),
    (402, "余额", False),
    (403, "有权", False),
    (404, "模型名称", False),
    (429, "限流", True),
    (503, "服务状态", True),
])
def test_http_failure_is_actionable_without_exposing_provider_body(status, repair, retryable):
    provider = OpenAICompatibleSubtitleTranslationProvider(
        provider_name="test", base_url="https://llm.example/v1", model_id="test",
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private-provider-body")),
    )
    result = SubtitleTranslationService(provider).translate_zh_to_en(
        _source_track(), translated_track_id="en", timeline_version_id="v4",
    )
    assert result.failure_code == f"http_{status}"
    assert f"HTTP {status}" in result.failure_reason
    assert repair in result.repair_action
    assert result.retryable is retryable
    assert result.translated_track is None
    assert "private-provider-body" not in result.failure_reason


class _SuccessfulProvider:
    provider_name = "configured-provider"
    model_id = "configured-model"
    ready = True
    message = "ready"

    def translate(self, cues):
        assert [(cue.id, cue.start_seconds, cue.end_seconds) for cue in cues] == [
            ("cue_001", 1.25, 4.9),
            ("cue_002", 4.9, 7.125),
        ]
        return SubtitleTranslationProviderResult(
            translations=(
                SubtitleTranslation("cue_001", "Don't just explain concepts\nSee it run for real"),
                SubtitleTranslation("cue_002", "That is the result."),
            ),
            provider_execution_id="chatcmpl_real_1",
        )


def test_translation_keeps_stable_source_mapping_and_exact_existing_timing():
    source = _source_track()

    result = SubtitleTranslationService(_SuccessfulProvider()).translate_zh_to_en(
        source,
        translated_track_id="subtitle_en_v4",
        timeline_version_id="timeline_v4",
    )

    assert result.status == "succeeded"
    assert result.source_track is source
    assert result.translated_track is not None
    assert result.translated_track.model_id == "configured-model"
    assert result.translated_track.transform_id == result.execution_id
    assert [cue.source_cue_id for cue in result.translated_track.cues] == [
        "cue_001",
        "cue_002",
    ]
    assert [
        (cue.start_seconds, cue.end_seconds) for cue in result.translated_track.cues
    ] == [(1.25, 4.9), (4.9, 7.125)]
    assert [cue.id for cue in result.translated_track.cues] == [
        "subtitle_en_v4:cue_001",
        "subtitle_en_v4:cue_002",
    ]


def test_chinese_and_english_tracks_are_json_serializable():
    result = SubtitleTranslationService(_SuccessfulProvider()).translate_zh_to_en(
        _source_track(),
        translated_track_id="subtitle_en_v4",
        timeline_version_id="timeline_v4",
    )
    assert result.translated_track is not None

    chinese = json.loads(subtitle_track_to_json(result.source_track))
    english = json.loads(subtitle_track_to_json(result.translated_track))

    assert chinese["language"] == "zh-CN"
    assert chinese["cues"][0]["emphasis"] == ["真实运行"]
    assert chinese["cues"][0]["line_breaks"] == [5]
    assert english["language"] == "en-US"
    assert english["source_language"] == "zh-CN"
    assert english["cues"][0]["source_cue_id"] == "cue_001"


def test_srt_rendering_preserves_manual_line_breaks_and_bilingual_order():
    result = SubtitleTranslationService(_SuccessfulProvider()).translate_zh_to_en(
        _source_track(),
        translated_track_id="subtitle_en_v4",
        timeline_version_id="timeline_v4",
    )
    assert result.translated_track is not None

    chinese_srt = render_srt(result.source_track)
    bilingual_srt = render_bilingual_srt(result.source_track, result.translated_track)

    assert (
        "00:00:01,250 --> 00:00:04,900\n"
        "别只讲概念\n"
        "先看真实运行"
    ) in chinese_srt
    assert (
        "别只讲概念\n"
        "先看真实运行\n"
        "Don't just explain concepts\n"
        "See it run for real"
    ) in bilingual_srt


def test_unavailable_provider_returns_actionable_failure_without_changing_chinese_track():
    source = _source_track()
    before = subtitle_track_to_json(source)
    provider = UnavailableSubtitleTranslationProvider(
        provider_name="deepseek",
        model_id="deepseek-v4-flash",
        message="配置文字模型凭据后重试。",
    )

    result = SubtitleTranslationService(provider).translate_zh_to_en(
        source,
        translated_track_id="subtitle_en_v4",
        timeline_version_id="timeline_v4",
    )

    assert result.status == "unavailable"
    assert result.translated_track is None
    assert result.failure_code == "capability_unavailable"
    assert result.failure_reason and result.impact and result.repair_action
    assert result.source_track is source
    assert subtitle_track_to_json(source) == before


def test_provider_failure_returns_actionable_failure_without_changing_chinese_track():
    class FailedProvider(_SuccessfulProvider):
        def translate(self, cues):
            del cues
            raise SubtitleTranslationProviderError(
                code="http_failed",
                reason="文字模型服务拒绝了字幕翻译请求。",
                impact="未生成英文字幕轨；中文字幕轨保持不变。",
                repair_action="检查账号权限或请求配额后重试。",
                retryable=True,
            )

    source = _source_track()
    before = subtitle_track_to_json(source)

    result = SubtitleTranslationService(FailedProvider()).translate_zh_to_en(
        source,
        translated_track_id="subtitle_en_v4",
        timeline_version_id="timeline_v4",
    )

    assert result.status == "failed"
    assert result.translated_track is None
    assert result.failure_code == "http_failed"
    assert result.failure_reason and result.impact and result.repair_action
    assert result.input_evidence is not None
    assert subtitle_track_to_json(source) == before


def test_execution_carries_model_and_input_output_evidence():
    result = SubtitleTranslationService(_SuccessfulProvider()).translate_zh_to_en(
        _source_track(),
        translated_track_id="subtitle_en_v4",
        timeline_version_id="timeline_v4",
    )

    assert result.provider_name == "configured-provider"
    assert result.model_id == "configured-model"
    assert result.provider_execution_id == "chatcmpl_real_1"
    assert result.prompt_schema_version == "subtitle-translation/v1"
    assert result.input_evidence is not None
    assert result.input_evidence.track_ref == "subtitle-track://subtitle_zh_v3"
    assert result.input_evidence.cue_ids == ("cue_001", "cue_002")
    assert len(result.input_evidence.sha256) == 64
    assert result.output_evidence is not None
    assert result.output_evidence.track_ref == "subtitle-track://subtitle_en_v4"
    assert len(result.output_evidence.sha256) == 64
    assert result.input_evidence.sha256 != result.output_evidence.sha256


@pytest.mark.parametrize("model_id", ["real-text-model", "MiniMax-M3"])
def test_openai_compatible_provider_uses_structured_cues_without_timing_rewrite(model_id):
    captured = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        content = json.loads(captured["messages"][1]["content"])
        assert content["cues"] == [
            {
                "source_cue_id": "cue_001",
                "text": "别只讲概念\n先看真实运行",
            },
            {"source_cue_id": "cue_002", "text": "这才是结果。"},
        ]
        assert all(
            "start_seconds" not in cue and "end_seconds" not in cue
            for cue in content["cues"]
        )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_provider_1",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "translations": [
                                        {
                                            "source_cue_id": "cue_001",
                                            "text": "Don't just explain concepts\nSee it run for real",
                                        },
                                        {
                                            "source_cue_id": "cue_002",
                                            "text": "That is the result.",
                                        },
                                    ]
                                }
                            )
                        }
                    }
                ],
            },
        )

    provider = OpenAICompatibleSubtitleTranslationProvider(
        provider_name="custom",
        base_url="https://llm.example/v1",
        model_id=model_id,
        api_key="secret",
        transport=httpx.MockTransport(respond),
    )

    result = SubtitleTranslationService(provider).translate_zh_to_en(
        _source_track(),
        translated_track_id="subtitle_en_v4",
        timeline_version_id="timeline_v4",
    )

    assert result.status == "succeeded"
    assert result.model_id == model_id
    assert result.provider_execution_id == "chatcmpl_provider_1"
    assert captured["response_format"] == {"type": "json_object"}
    assert captured.get("reasoning_split") is (True if model_id == "MiniMax-M3" else None)


def test_provider_mapping_failure_never_creates_partial_translation_track():
    class PartialProvider(_SuccessfulProvider):
        def translate(self, cues):
            del cues
            return SubtitleTranslationProviderResult(
                translations=(SubtitleTranslation("cue_001", "Only one cue"),)
            )

    result = SubtitleTranslationService(PartialProvider()).translate_zh_to_en(
        _source_track(),
        translated_track_id="subtitle_en_v4",
        timeline_version_id="timeline_v4",
    )

    assert result.status == "failed"
    assert result.failure_code == "provider_contract_invalid"
    assert result.translated_track is None
    assert result.output_evidence is None
