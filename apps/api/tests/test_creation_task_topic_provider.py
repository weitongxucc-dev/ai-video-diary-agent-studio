from __future__ import annotations

import json
import traceback

import httpx
import pytest

from app.topics import (
    CreationTaskGeneratedTopic,
    CreationTaskTopicGenerationError,
    CreationTaskTopicInput,
    CreationTaskTopicSource,
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
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(document, ensure_ascii=False)}}]},
    )


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
            json={"choices": [{"message": {"content": "not-json"}}]},
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
