from __future__ import annotations

import json
import traceback

import httpx
import pytest

from app.topics import (
    CreationTaskGeneratedScript,
    CreationTaskGeneratedScriptSection,
    CreationTaskGeneratedTopic,
    CreationTaskScriptGenerationError,
    CreationTaskScriptInput,
    CreationTaskTopicGenerationError,
    CreationTaskTopicInput,
    CreationTaskTopicSource,
    OpenAICompatibleCreationTaskScriptGenerator,
    OpenAICompatibleCreationTaskTopicGenerator,
)


def _input() -> CreationTaskTopicInput:
    return CreationTaskTopicInput(
        goal="解释工作流为什么比单个模型更重要",
        language="zh-CN",
        sources=(
            CreationTaskTopicSource(
                reference_id="knowledge-1",
                kind="knowledge",
                title="工作流笔记",
                text="用户维护的工作流可以留下可验证的过程证据。",
            ),
            CreationTaskTopicSource(
                reference_id="source-1",
                kind="source",
                title="行业观察",
                text="行业文章指出模型能力会变化，但可靠流程应能复用。",
            ),
        ),
    )


def _topic(*, title: str = "AI 工作流的真正价值") -> dict[str, object]:
    return {
        "title": title,
        "angle": "将模型能力放进可审计的生产流程。",
        "audience": "关注 AI 产品与内容生产的创作者。",
        "outline": ["模型并不等于流程", "证据如何沉淀", "从一个任务开始"],
        "score": 87.5,
        "evidence_reference_ids": ["knowledge-1", "source-1"],
    }


def _response(document: object) -> httpx.Response:
    name = "submit_script_draft" if isinstance(document, dict) and "script" in document else "submit_topic_candidates"
    return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
        "content": None, "tool_calls": [{"id": "controlled-generation", "type": "function",
            "function": {"name": name, "arguments": json.dumps(document, ensure_ascii=False)}}]}}]})


def _review_tool_response(document: object) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
        "content": None, "tool_calls": [{"id": "controlled-report", "type": "function",
            "function": {"name": "submit_script_review", "arguments": json.dumps(document, ensure_ascii=False)}}]}}]})


def _script_input() -> CreationTaskScriptInput:
    return CreationTaskScriptInput(
        goal="解释本地创作如何保护隐私",
        language="zh-CN",
        duration_target_seconds=30,
        topic_title="本地创作与隐私",
        topic_angle="数据留在本机",
        topic_audience="短视频创作者",
        topic_outline=("说明风险", "解释本地链路"),
        sources=(CreationTaskTopicSource(reference_id="knowledge-1", kind="knowledge", title="工作流笔记",
            text="素材不必为了创作离开本机。用户维护的工作流可以留下可验证的过程证据。"), _input().sources[1]),
    )


def _script_document(*, evidence_ids: list[str]) -> dict[str, object]:
    return {
        "script": {
            "title": "素材留在本机，创作更安心",
            "language": "zh-CN",
            "sections": [
                {
                    "title": "开场",
                    "content": "你的素材，不必为了创作离开本机。",
                    "visual_prompt": "本机素材库画面",
                    "evidence_reference_ids": evidence_ids,
                }
            ],
        }
    }


def _script_review(script: dict, source_pieces: list[dict]) -> dict:
    """The controlled model selects IDs from the supplied original fragments."""
    return {"sections": [{"index": section["index"], "grounded": True, "constraints_met": True,
        "supports": [{"candidate_quote_id": section["content"][0]["quote_id"],
                      "source_quote_id": source_pieces[0]["quote_id"]}], "issues": []}
        for section in script["sections"]]}


def test_script_uses_bounded_evidence_aliases_and_restores_authoritative_ids():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        inputs = json.loads(payload["messages"][1]["content"])
        if "script" in inputs:
            return _review_tool_response(_script_review(inputs["script"], inputs["sources"][0]["text"]))
        seen["prompt"] = inputs
        return _response(_script_document(evidence_ids=["evidence_1", "evidence_2"]))

    generated = OpenAICompatibleCreationTaskScriptGenerator(
        "https://models.example/v1",
        "script-model",
        transport=httpx.MockTransport(handler),
    ).generate(_script_input())

    assert generated == CreationTaskGeneratedScript(
        title="素材留在本机，创作更安心",
        language="zh-CN",
        sections=(
            CreationTaskGeneratedScriptSection(
                title="开场",
                content="你的素材，不必为了创作离开本机。",
                visual_prompt="本机素材库画面",
                evidence_reference_ids=("knowledge-1", "source-1"),
            ),
        ),
    )
    assert seen["prompt"]["sources"] == [
        {
            "reference_id": "evidence_1",
            "kind": "knowledge",
            "title": "工作流笔记",
            "text": "素材不必为了创作离开本机。用户维护的工作流可以留下可验证的过程证据。",
        },
        {
            "reference_id": "evidence_2",
            "kind": "source",
            "title": "行业观察",
            "text": "行业文章指出模型能力会变化，但可靠流程应能复用。",
        },
    ]


def test_script_rejects_unknown_evidence_alias_without_persisting_it():
    generator = OpenAICompatibleCreationTaskScriptGenerator(
        "https://models.example/v1",
        "script-model",
        transport=httpx.MockTransport(
            lambda _request: _response(_script_document(evidence_ids=["knowledge-1"]))
        ),
    )

    with pytest.raises(CreationTaskScriptGenerationError) as exc_info:
        generator.generate(_script_input())

    assert exc_info.value.code == "response_schema_invalid"
    assert exc_info.value.reason == "文字模型返回了无效或未授权的文案证据引用。"


def test_explicit_input_is_the_only_prompt_source_and_key_is_sent_when_given():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["payload"] = json.loads(request.content)
        return _response({"topics": [_topic()]})

    generator = OpenAICompatibleCreationTaskTopicGenerator(
        base_url="https://models.example/v1/",
        model="topic-model",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )

    topics = generator.generate(_input())

    assert topics == [
        CreationTaskGeneratedTopic(
            title="AI 工作流的真正价值",
            angle="将模型能力放进可审计的生产流程。",
            audience="关注 AI 产品与内容生产的创作者。",
            outline=("模型并不等于流程", "证据如何沉淀", "从一个任务开始"),
            score=87.5,
            evidence_reference_ids=("knowledge-1", "source-1"),
        )
    ]
    assert seen["url"] == "https://models.example/v1/chat/completions"
    assert seen["authorization"] == "Bearer test-key"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "topic-model"
    prompt = json.loads(payload["messages"][1]["content"])
    assert prompt["goal"] == _input().goal
    assert prompt["sources"] == [
        {
            "reference_id": "knowledge-1",
            "kind": "knowledge",
            "title": "工作流笔记",
            "text": "用户维护的工作流可以留下可验证的过程证据。",
        },
        {
            "reference_id": "source-1",
            "kind": "source",
            "title": "行业观察",
            "text": "行业文章指出模型能力会变化，但可靠流程应能复用。",
        },
    ]


def test_local_or_custom_endpoint_allows_no_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") is None
        assert request.url == httpx.URL("http://127.0.0.1:11434/v1/chat/completions")
        return _response({"topics": [_topic()]})

    topics = OpenAICompatibleCreationTaskTopicGenerator(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen-local",
        transport=httpx.MockTransport(handler),
    ).generate(_input())

    assert len(topics) == 1


@pytest.mark.parametrize(
    "base_url,model",
    [
        ("", "topic-model"),
        ("https://models.example/v1", "  "),
        ("file:///tmp/model", "topic-model"),
        ("ftp://models.example/v1", "topic-model"),
        ("models.example/v1", "topic-model"),
        ("https:///v1", "topic-model"),
    ],
)
def test_route_requires_nonempty_base_url_and_model(base_url: str, model: str):
    with pytest.raises(CreationTaskTopicGenerationError) as exc_info:
        OpenAICompatibleCreationTaskTopicGenerator(base_url=base_url, model=model)

    assert exc_info.value.code == "route_configuration_invalid"


def test_http_failure_does_not_leak_response_body_or_api_key():
    secret = "key-must-not-leak"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"provider body {secret}")

    generator = OpenAICompatibleCreationTaskTopicGenerator(
        "https://models.example/v1",
        "topic-model",
        api_key=secret,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(CreationTaskTopicGenerationError) as exc_info:
        generator.generate(_input())

    assert exc_info.value.code == "http_failed"
    assert secret not in str(exc_info.value)
    assert secret not in exc_info.value.reason


def test_transport_failure_does_not_leak_transport_details_or_key():
    secret = "key-must-not-leak"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection failed with {secret}", request=request)

    generator = OpenAICompatibleCreationTaskTopicGenerator(
        "https://models.example/v1",
        "topic-model",
        api_key=secret,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(CreationTaskTopicGenerationError) as exc_info:
        generator.generate(_input())

    assert exc_info.value.code == "transport_failed"
    assert secret not in str(exc_info.value)
    assert secret not in exc_info.value.repair_action
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    formatted = "".join(traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb))
    assert secret not in formatted


@pytest.mark.parametrize(
    ("response_kind", "code"),
    [
        ("envelope", "response_envelope_invalid"),
        ("content", "content_json_invalid"),
    ],
)
def test_invalid_response_envelope_and_content_json_fail_closed(
    response_kind: str,
    code: str,
):
    if response_kind == "content":
        response = httpx.Response(
            200,
            json={"choices": [{"finish_reason": "tool_calls", "message": {"content": None,
                "tool_calls": [{"type": "function", "function": {"name": "submit_topic_candidates", "arguments": "not-json"}}]}}]},
        )
    else:
        response = httpx.Response(200, content=b"not an envelope")
    generator = OpenAICompatibleCreationTaskTopicGenerator(
        "https://models.example/v1",
        "topic-model",
        transport=httpx.MockTransport(lambda _request: response),
    )

    with pytest.raises(CreationTaskTopicGenerationError) as exc_info:
        generator.generate(_input())

    assert exc_info.value.code == code


@pytest.mark.parametrize(
    ("document", "code"),
    [
        ({"topics": [{"title": "only title"}]}, "response_schema_invalid"),
        ({"topics": [{**_topic(), "title": ""}]}, "response_schema_invalid"),
        ({"topics": [{**_topic(), "title": "x" * 161}]}, "response_schema_invalid"),
        ({"topics": [{**_topic(), "evidence_reference_ids": [""]}]}, "evidence_reference_invalid"),
        ({"topics": [{**_topic(), "evidence_reference_ids": ["x" * 201]}]}, "evidence_reference_invalid"),
        ({"topics": [{**_topic(), "evidence_reference_ids": ["unknown"]}]}, "evidence_reference_invalid"),
        ({"topics": [_topic(), _topic()]}, "duplicate_title"),
    ],
)
def test_invalid_topic_schema_evidence_and_duplicate_titles_fail_closed(
    document: object,
    code: str,
):
    generator = OpenAICompatibleCreationTaskTopicGenerator(
        "https://models.example/v1",
        "topic-model",
        transport=httpx.MockTransport(lambda _request: _response(document)),
    )

    with pytest.raises(CreationTaskTopicGenerationError) as exc_info:
        generator.generate(_input())

    assert exc_info.value.code == code
    assert exc_info.value.impact == "未生成选题候选。"
    assert exc_info.value.repair_action


def test_invalid_input_never_calls_transport():
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _response({"topics": [_topic()]})

    generator = OpenAICompatibleCreationTaskTopicGenerator(
        "https://models.example/v1",
        "topic-model",
        transport=httpx.MockTransport(handler),
    )
    invalid = CreationTaskTopicInput(goal="目标", language="zh-CN", sources=())

    with pytest.raises(CreationTaskTopicGenerationError) as exc_info:
        generator.generate(invalid)

    assert exc_info.value.code == "input_invalid"
    assert called is False


@pytest.mark.parametrize("language", ["en-US", "zh-CN/en-US"])
def test_script_honors_requested_language_and_rejects_wrong_response(language):
    from dataclasses import replace
    seen = {}
    def handler(request):
        payload = json.loads(request.content)
        inputs = json.loads(payload["messages"][1]["content"])
        if "script" in inputs:
            return _review_tool_response(_script_review(inputs["script"], inputs["sources"][0]["text"]))
        seen["system"] = payload["messages"][0]["content"]
        seen["input"] = inputs
        document = _script_document(evidence_ids=["evidence_1"])
        document["script"]["language"] = language
        document["script"]["sections"][0]["content"] = "Keep your footage on this computer."
        return _response(document)
    generator = OpenAICompatibleCreationTaskScriptGenerator("https://models.example/v1", "script-model", transport=httpx.MockTransport(handler))
    result = generator.generate(replace(_script_input(), language=language))
    assert result.language == language
    assert seen["input"]["requirements"]["spoken_language"] == language
    assert f"language 必须是 {language}" in seen["system"]
    wrong = OpenAICompatibleCreationTaskScriptGenerator("https://models.example/v1", "script-model",
        transport=httpx.MockTransport(lambda request: _response(_script_document(evidence_ids=["evidence_1"]))))
    with pytest.raises(CreationTaskScriptGenerationError):
        wrong.generate(replace(_script_input(), language=language))


@pytest.mark.parametrize("kind", ["topics", "script"])
@pytest.mark.parametrize("invalid", ["missing", "multiple", "wrong-name", "bad-json", "non-object", "length", "content-only", "fenced"])
def test_creation_task_generation_accepts_only_one_complete_inert_report(kind, invalid, caplog):
    import logging

    seen = []
    secret = "private-generation-report-sentinel"
    document = {"topics": [_topic()]} if kind == "topics" else _script_document(evidence_ids=["evidence_1"])
    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload)
        assert payload["tool_choice"] == "required" and "response_format" not in payload
        expected = "submit_topic_candidates" if kind == "topics" else "submit_script_draft"
        assert payload["tools"][0]["function"]["name"] == expected
        envelope = _response(document).json()
        choice, message = envelope["choices"][0], envelope["choices"][0]["message"]
        message["content"] = json.dumps(document)  # Never used to rescue the invalid report call.
        tool = message["tool_calls"][0]
        tool["id"] = secret
        if invalid == "missing":
            message.pop("tool_calls")
        elif invalid == "multiple":
            message["tool_calls"].append(dict(tool))
        elif invalid == "wrong-name":
            tool["function"]["name"] = secret
        elif invalid == "bad-json":
            tool["function"]["arguments"] = secret
        elif invalid == "non-object":
            tool["function"]["arguments"] = "[]"
        elif invalid == "length":
            choice["finish_reason"] = "length"
        elif invalid == "content-only":
            choice["finish_reason"] = "stop"
            message.pop("tool_calls")
        else:
            tool["function"]["arguments"] = "```json\n" + tool["function"]["arguments"] + "\n```"
        return httpx.Response(200, json=envelope)
    caplog.set_level(logging.WARNING, logger="app.topics")
    constructor = OpenAICompatibleCreationTaskTopicGenerator if kind == "topics" else OpenAICompatibleCreationTaskScriptGenerator
    expected_error = CreationTaskTopicGenerationError if kind == "topics" else CreationTaskScriptGenerationError
    generator = constructor("https://models.invalid/v1", "MiniMax-M3", api_key=secret, transport=httpx.MockTransport(handler))
    with pytest.raises(expected_error):
        generator.generate(_input() if kind == "topics" else _script_input())
    assert len(seen) == 1  # No tool executor/results, retry or script review for invalid generation.
    assert seen[0]["max_tokens"] == (3200 if kind == "topics" else 8192)
    assert seen[0]["reasoning_split"] is True
    assert secret not in caplog.text


def test_topic_candidate_report_schema_preserves_all_existing_constraints():
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return _response({"topics": [_topic()]})
    generated = OpenAICompatibleCreationTaskTopicGenerator("https://models.invalid/v1", "MiniMax-M3",
        transport=httpx.MockTransport(handler)).generate(_input())
    assert len(calls) == 1 and len(generated) == 1
    schema = calls[0]["tools"][0]["function"]["parameters"]
    topics = schema["properties"]["topics"]
    assert topics["minItems"] == 1 and topics["maxItems"] == 8
    fields = topics["items"]["properties"]
    assert set(fields) == {"title", "angle", "audience", "outline", "score", "evidence_reference_ids"}
    assert fields["score"] == {"type": "number", "minimum": 0, "maximum": 100}
    assert fields["outline"]["maxItems"] == fields["evidence_reference_ids"]["maxItems"] == 8
    assert "enum" not in fields["evidence_reference_ids"]["items"]


@pytest.mark.parametrize("score", [0, 100, 87.5, 95.0])
def test_topic_score_number_contract_accepts_integer_and_float_without_diagnostic(score, caplog):
    import logging

    seen = []
    topic = {**_topic(), "score": score}
    def handler(request):
        seen.append(json.loads(request.content))
        return _response({"topics": [topic]})
    caplog.set_level(logging.WARNING, logger="app.topics")
    result = OpenAICompatibleCreationTaskTopicGenerator("https://models.invalid/v1", "same-model",
        transport=httpx.MockTransport(handler)).generate(_input())
    assert result[0].score == float(score) and len(seen) == 1
    assert seen[0]["tools"][0]["function"]["parameters"]["properties"]["topics"]["items"]["properties"]["score"] == {
        "type": "number", "minimum": 0, "maximum": 100}
    assert "creation_topic_score_invalid" not in caplog.text


@pytest.mark.parametrize("kind,value,expected_type,numeric", [
    ("missing", None, "null", None), ("null", None, "null", None),
    ("bool", True, "boolean", None), ("string", "private-score-text-sentinel", "string", None),
    ("nan", float("nan"), "number", None), ("inf", float("inf"), "number", None),
    ("negative", -1, "number", -1.0), ("too-high", 101.0, "number", 101.0),
    ("large", 1e30, "number", None), ("array", ["private-score-text-sentinel"], "array", None),
    ("object", {"private-score-key": "private-score-text-sentinel"}, "object", None),
])
def test_topic_score_rejection_has_bounded_metadata_and_never_logs_model_text(kind, value, expected_type, numeric, caplog):
    import logging

    topic = {**_topic(title="private-topic-title-sentinel"), "score": value}
    if kind == "missing":
        topic.pop("score")
    calls = []
    def handler(request):
        calls.append(request)
        return _response({"topics": [topic]})
    caplog.set_level(logging.WARNING, logger="app.topics")
    generator = OpenAICompatibleCreationTaskTopicGenerator("https://models.invalid/v1", "same-model",
        api_key="private-score-key-sentinel", transport=httpx.MockTransport(handler))
    with pytest.raises(CreationTaskTopicGenerationError) as error:
        generator.generate(_input())
    assert error.value.code == "response_schema_invalid" and error.value.reason == "文字模型返回的选题评分无效。"
    assert len(calls) == 1
    records = [record for record in caplog.records if record.name == "app.topics"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert message.startswith("creation_topic_score_invalid ") and len(message) < 500
    diagnostic = json.loads(message.split(" ", 1)[1])
    assert diagnostic == {"candidate_index": 1, "score_present": kind != "missing", "score_type": expected_type,
        "score_numeric": numeric, "score_string_length": len(value) if kind == "string" else None}
    for secret in ("private-topic-title-sentinel", "private-score-text-sentinel", "private-score-key-sentinel",
                   "private-score-key", "https://models.invalid", "knowledge-1", _input().sources[0].text):
        assert secret not in caplog.text


def test_topic_request_prioritizes_one_complete_candidate_without_changing_allowed_count():
    calls = []
    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return _response({"topics": [_topic()]})
    result = OpenAICompatibleCreationTaskTopicGenerator("https://models.invalid/v1", "same-model",
        transport=httpx.MockTransport(respond)).generate(_input())
    assert len(result) == 1 and len(calls) == 1
    payload = calls[0]
    assert payload["max_tokens"] == 3200 and payload["tool_choice"] == "required"
    schema = payload["tools"][0]["function"]["parameters"]["properties"]["topics"]
    assert schema["minItems"] == 1 and schema["maxItems"] == 8
    assert set(schema["items"]["required"]) == {"title", "angle", "audience", "outline", "score", "evidence_reference_ids"}
    instruction = payload["messages"][0]["content"]
    assert "优先提交1个完整" in instruction and "逐条核对" in instruction
    assert "score存在且为0–100数值" in instruction


def test_third_topic_missing_score_rejects_the_entire_report_without_filling_or_retry():
    first, second = _topic(title="第一条完整候选"), _topic(title="第二条完整候选")
    third = _topic(title="第三条漏字段")
    third.pop("score")
    calls = []
    def respond(request):
        calls.append(request)
        return _response({"topics": [first, second, third]})
    generator = OpenAICompatibleCreationTaskTopicGenerator("https://models.invalid/v1", "same-model",
        transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskTopicGenerationError) as error:
        generator.generate(_input())
    assert error.value.code == "response_schema_invalid"
    assert error.value.reason == "文字模型返回的选题评分无效。"
    assert len(calls) == 1
    assert "score" not in third  # No synthesized zero, null or discarded partial list.


@pytest.mark.parametrize("section_count", [None, 2, 6])
def test_script_generation_prompt_is_compact_without_overriding_template_section_count(section_count):
    from dataclasses import replace

    calls = []
    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        model_input = json.loads(payload["messages"][1]["content"])
        if payload["tools"][0]["function"]["name"] == "submit_script_review":
            return _review_tool_response(_script_review(model_input["script"], model_input["sources"][0]["text"]))
        document = _script_document(evidence_ids=["evidence_1"])
        document["script"]["sections"] = [dict(document["script"]["sections"][0]) for _ in range(section_count or 1)]
        return _response(document)
    source = replace(_script_input(), script_section_count=section_count, duration_target_seconds=120)
    result = OpenAICompatibleCreationTaskScriptGenerator("https://models.invalid/v1", "same-model",
        transport=httpx.MockTransport(respond)).generate(source)
    assert len(result.sections) == (section_count or 1) and len(calls) == 2
    instruction = calls[0]["messages"][0]["content"]
    if section_count is None:
        assert "建议整合为3–5" in instruction
        assert "合法范围仍是1–20" in instruction  # A single valid section is still accepted.
    else:
        assert "建议整合为3–5" not in instruction
        assert f"sections 必须恰好有 {section_count} 项" in instruction
    assert "一句简短画面意图" in instruction and "不逐项复述全部资料和提纲" in instruction
    assert calls[0]["max_tokens"] == 8192 and calls[0]["temperature"] == 0.4
    assert calls[1]["max_tokens"] == 8192
    assert [call["tools"][0]["function"]["name"] for call in calls] == ["submit_script_draft", "submit_script_review"]
    sections_schema = calls[0]["tools"][0]["function"]["parameters"]["properties"]["script"]["properties"]["sections"]
    assert sections_schema["minItems"] == (section_count or 1) and sections_schema["maxItems"] == (section_count or 5)


@pytest.mark.parametrize("override,expected", [(None, 120.0), (7.5, 7.5)])
def test_script_report_deadline_is_120_seconds_by_default_and_honors_explicit_override(override, expected):
    requests = []
    def respond(request):
        payload = json.loads(request.content)
        requests.append(request)
        if payload["tools"][0]["function"]["name"] == "submit_script_draft":
            return _response(_script_document(evidence_ids=["evidence_1"]))
        model_input = json.loads(payload["messages"][1]["content"])
        return _review_tool_response(_script_review(model_input["script"], model_input["sources"][0]["text"]))
    options = {} if override is None else {"timeout": override}
    generator = OpenAICompatibleCreationTaskScriptGenerator("https://models.invalid/v1", "same-model",
        transport=httpx.MockTransport(respond), **options)
    generator.generate(_script_input())
    assert len(requests) == 2
    for request in requests:
        assert request.extensions["timeout"] == {key: expected for key in ("connect", "read", "write", "pool")}


@pytest.mark.parametrize("model", ["MiniMax-M3", "MiniMax-M3.1-Flash-Preview", "MiniMax-M2.5", "same-model", "minimax-m3"])
def test_only_exact_m3_disables_thinking_for_script_generation_and_review(model):
    calls = []
    def respond(request):
        payload = json.loads(request.content)
        calls.append((request, payload))
        if payload["tools"][0]["function"]["name"] == "submit_script_draft":
            return _response(_script_document(evidence_ids=["evidence_1"]))
        data = json.loads(payload["messages"][1]["content"])
        return _review_tool_response(_script_review(data["script"], data["sources"][0]["text"]))
    result = OpenAICompatibleCreationTaskScriptGenerator("https://models.invalid/v1", model,
        transport=httpx.MockTransport(respond)).generate(_script_input())
    assert result.sections and len(calls) == 2
    for request, payload in calls:
        if model == "MiniMax-M3":
            assert payload["thinking"] == {"type": "disabled"}
        else:
            assert "thinking" not in payload
        if model.startswith("MiniMax-"):
            assert payload["reasoning_split"] is True
        assert "reasoning_effort" not in payload
        assert payload["model"] == model and payload["max_tokens"] == 8192
        assert payload["tool_choice"] == "required" and "response_format" not in payload
        assert request.extensions["timeout"]["read"] == 120.0
    assert [payload["tools"][0]["function"]["name"] for _, payload in calls] == ["submit_script_draft", "submit_script_review"]


def test_m3_topic_request_is_not_changed_by_script_thinking_control():
    calls = []
    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return _response({"topics": [_topic()]})
    OpenAICompatibleCreationTaskTopicGenerator("https://models.invalid/v1", "MiniMax-M3",
        transport=httpx.MockTransport(respond)).generate(_input())
    assert len(calls) == 1 and "thinking" not in calls[0]
    assert calls[0]["reasoning_split"] is True and calls[0]["max_tokens"] == 3200


@pytest.mark.parametrize("language,section_count", [("zh-CN", None), ("zh-CN", 2), ("zh-CN", 6), ("zh-CN", 7),
                                                   ("en-US", None), ("zh-CN/en-US", None)])
def test_chinese_generation_schema_allocates_only_existing_total_duration_budget(language, section_count):
    from dataclasses import replace

    seen = []
    source = replace(_script_input(), language=language, duration_target_seconds=120, script_section_count=section_count)
    def respond(request):
        payload = json.loads(request.content)
        seen.append(payload)
        data = json.loads(payload["messages"][1]["content"])
        if payload["tools"][0]["function"]["name"] == "submit_script_review":
            return _review_tool_response(_script_review(data["script"], data["sources"][0]["text"]))
        doc = _script_document(evidence_ids=["evidence_1"])
        doc["script"]["language"] = language
        doc["script"]["sections"] *= section_count or 1
        return _response(doc)
    OpenAICompatibleCreationTaskScriptGenerator("https://models.invalid/v1", "same-model",
        transport=httpx.MockTransport(respond)).generate(source)
    assert len(seen) == 2
    schema = seen[0]["tools"][0]["function"]["parameters"]["properties"]["script"]["properties"]["sections"]
    req = json.loads(seen[0]["messages"][1]["content"])["requirements"]
    if language == "zh-CN":
        count = section_count or 5
        maximum = schema["items"]["properties"]["content"]["maxLength"]
        assert schema["maxItems"] == count and maximum == 480 // count
        assert maximum * count <= req["total_content_character_budget"] == 480
        assert req["section_character_budgets"] == [{"index": i, "content_max_characters": maximum} for i in range(1, count + 1)]
        assert schema["minItems"] == (section_count or 1)
    else:
        assert schema["minItems"] == 1 and schema["maxItems"] == 20
        assert schema["items"]["properties"]["content"]["maxLength"] == 4000
        assert req["section_count"] == "1-20"
        assert "total_content_character_budget" not in req and "section_character_budgets" not in req
    assert seen[0]["max_tokens"] == seen[1]["max_tokens"] == 8192


@pytest.mark.parametrize("returned_sections", [1, 6])
def test_chinese_output_exceeding_section_request_suggestion_still_uses_existing_runtime_total_gate(returned_sections):
    from dataclasses import replace

    document = _script_document(evidence_ids=["evidence_1"])
    original = document["script"]["sections"][0]["content"] * (7 if returned_sections == 1 else 1)
    assert len(original) > 96 if returned_sections == 1 else returned_sections > 5
    document["script"]["sections"][0]["content"] = original
    document["script"]["sections"] *= returned_sections
    calls = []
    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return _response(document)
        data = json.loads(payload["messages"][1]["content"])
        return _review_tool_response(_script_review(data["script"], data["sources"][0]["text"]))
    result = OpenAICompatibleCreationTaskScriptGenerator("https://models.invalid/v1", "same-model",
        transport=httpx.MockTransport(respond)).generate(replace(_script_input(), duration_target_seconds=120))
    assert result.sections[0].content == original and len(result.sections) == returned_sections and len(calls) == 2
