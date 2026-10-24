"""Bounded source/goal review with controlled HTTP and isolated persistence."""
import copy
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings, llm_service_verification, record_llm_service_verification
from app.local_members import LocalMemberService
from app.production import (
    CreationPlan, CreationPlanKnowledgeReference, CreationTask, CreationTaskScript,
    CreationTaskScriptSection, ProductionRepository, ScriptGenerationRun, TopicCandidate,
    TopicEvidenceReference, TopicGenerationRun,
)
from app.source_repository import SourceRepository
from app.sources import KnowledgeItem
from app.topics import (
    CreationTaskScriptGenerationError, CreationTaskScriptInput, CreationTaskTopicSource,
    OpenAICompatibleCreationTaskScriptGenerator,
)
from test_creation_task_topic_provider import _response, _review_tool_response

SOURCE = '本机工作区有素材库，用户可为素材设置用途。声音参考和声音生成分别处理。'
GOAL = '介绍本机工作区，人物不出镜，中文旁白、中英字幕；先准备文案，暂不开始制作。'
FACT = '用户可以为素材设置用途。'


def inputs():
    return CreationTaskScriptInput(goal=GOAL, language='zh-CN', duration_target_seconds=120,
        presenter_share_target=0, topic_title='本机工作区导览', topic_angle='讲清操作入口',
        topic_audience='本机创作者', topic_outline=('素材设置用途', '计划自动备份'),
        sources=(CreationTaskTopicSource(reference_id='knowledge:fixture:v1', kind='knowledge',
            title='本机工作区导览资料', text=SOURCE),), style_instruction='用积木作比喻，讲得自然。')


def draft(content=FACT, visual='展示素材用途设置界面'):
    return {'script': {'title': '本机工作区导览', 'language': 'zh-CN', 'sections': [
        {'title': '素材用途', 'content': content, 'visual_prompt': visual, 'evidence_reference_ids': ['evidence_1']},
    ]}}


def reviewed(document):
    return {'sections': [{'index': index,
        'supports': [{'candidate_quote_id': '@content:0', 'source_quote_id': '@evidence_1:0'}]}
        for index, _ in enumerate(document['script']['sections'], start=1)]}


def rejected(document, *, kind, field, quote, detail):
    assert quote in document['script']['sections'][0][field]
    result = reviewed(document)
    review = result['sections'][0]
    if kind == 'unsupported_fact':
        del review['supports']
    review['issues'] = [{'kind': kind, 'candidate_quote_id': '@' + field + ':0',
        **({'reference_ids': ['evidence_1']} if kind == 'unsupported_fact' else {}),
        'missing_or_conflicting': detail}]
    return result


def bound_review(review, model_input):
    """Controlled model responses select supplied IDs by field/ordinal.

    Fixture locators are not a provider protocol or a text-match fallback.
    Legacy response objects are deliberately returned unchanged for rejection.
    """
    result = copy.deepcopy(review)
    sections = model_input['script']['sections']
    def select(value, section, *, source=False):
        if not isinstance(value, str) or not value.startswith('@'):
            return value
        field, ordinal = value[1:].split(':')
        if source:
            choices = next((item['text'] for item in model_input['sources'] if item['reference_id'] == field), [])
        else:
            choices = model_input['script']['title'] if field == 'script_title' else section.get(field, [])
        index = int(ordinal)
        return choices[index]['quote_id'] if 0 <= index < len(choices) else 'fixture-unknown-fragment'
    if not isinstance(result, dict) or not isinstance(result.get('sections'), list):
        return result
    for position, entry in enumerate(result['sections']):
        if not isinstance(entry, dict):
            continue
        section = sections[min(position, len(sections) - 1)]
        for support in entry.get('supports', []) if isinstance(entry.get('supports'), list) else []:
            if isinstance(support, dict):
                if 'candidate_quote_id' in support:
                    support['candidate_quote_id'] = select(support['candidate_quote_id'], section)
                if 'source_quote_id' in support:
                    support['source_quote_id'] = select(support['source_quote_id'], section, source=True)
        for issue in entry.get('issues', []) if isinstance(entry.get('issues'), list) else []:
            if isinstance(issue, dict) and 'candidate_quote_id' in issue:
                issue['candidate_quote_id'] = select(issue['candidate_quote_id'], section)
    return result


def provider(document, review, calls):
    def respond(request):
        body = json.loads(request.content)
        calls.append((request, body))
        if len(calls) == 1:
            return _response(document)
        if isinstance(review, Exception):
            raise review
        if isinstance(review, httpx.Response):
            return review
        return _review_tool_response(bound_review(review, json.loads(body['messages'][1]['content'])))
    return OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        api_key='isolated-key', transport=httpx.MockTransport(respond))


def test_supported_creative_draft_is_reviewed_once_with_same_sources_and_requirements():
    document = draft(FACT + '像搭积木一样，从用途开始整理。')
    calls = []
    result = provider(document, reviewed(document), calls).generate(inputs())
    assert result.sections[0].content == document['script']['sections'][0]['content']
    assert result.sections[0].evidence_reference_ids == ('knowledge:fixture:v1',)
    assert len(calls) == 2
    first, second = [json.loads(body['messages'][1]['content']) for _, body in calls]
    assert first['sources'][0]['text'] == ''.join(piece['text'] for piece in second['sources'][0]['text'])
    assert ''.join(piece['text'] for piece in second['sources'][0]['text']) == SOURCE
    assert second['goal'] == GOAL and second['requirements']['presenter_share_target'] == 0
    assert second['confirmed_topic']['outline'] == ['素材设置用途', '计划自动备份']
    assert first['requirements']['spoken_language'] == second['requirements']['language'] == 'zh-CN'
    assert [body['model'] for _, body in calls] == ['same-model', 'same-model']
    assert calls[0][0].url == calls[1][0].url
    assert all(request.headers['Authorization'] == 'Bearer isolated-key' for request, _ in calls)
    assert calls[1][1]['max_tokens'] == 8192 and calls[1][1]['temperature'] == 0
    assert 'response_format' not in calls[1][1] and calls[1][1]['tool_choice'] == 'required'
    assert len(calls[1][1]['tools']) == 1
    tool = calls[1][1]['tools'][0]
    assert tool['type'] == 'function' and tool['function']['name'] == 'submit_script_review'
    schema = tool['function']['parameters']
    assert schema['required'] == ['sections'] and schema['additionalProperties'] is False
    supported, branch = schema['properties']['sections']['items']['anyOf']
    assert supported['required'] == ['index', 'supports']
    assert set(supported['properties']) == {'index', 'supports'} and supported['additionalProperties'] is False
    properties = branch['properties']
    assert set(properties) == {'index', 'supports', 'issues'}
    assert branch['required'] == ['index', 'issues'] and branch['additionalProperties'] is False
    assert properties['supports']['minItems'] == properties['issues']['minItems'] == 1
    assert set(properties['supports']['items']['properties']) == {'candidate_quote_id', 'source_quote_id'}
    candidate_ids = [piece['quote_id'] for piece in second['script']['title']]
    candidate_ids += [piece['quote_id'] for section in second['script']['sections']
        for field in ('title', 'content', 'visual_prompt') for piece in section[field]]
    source_ids = [piece['quote_id'] for source in second['sources'] for piece in source['text']]
    assert properties['supports']['items']['properties']['candidate_quote_id']['enum'] == candidate_ids
    assert properties['supports']['items']['properties']['source_quote_id']['enum'] == source_ids
    issue_properties = properties['issues']['items']['anyOf'][0]['properties']
    assert issue_properties['candidate_quote_id']['enum'] == candidate_ids
    assert issue_properties['reference_ids']['items']['enum'] == ['evidence_1']
    assert issue_properties['reference_ids']['uniqueItems'] is True
    assert issue_properties['kind']['enum'] == ['unsupported_fact']
    assert issue_properties['reference_ids']['minItems'] == 1
    goal_properties = properties['issues']['items']['anyOf'][1]['properties']
    assert goal_properties['kind']['enum'] == ['goal_conflict']
    assert goal_properties['reference_ids']['minItems'] == 1
    assert 'reference_ids' not in properties['issues']['items']['anyOf'][1]['required']
    assert 'reference_ids' in properties['issues']['items']['anyOf'][0]['required']
    assert not any(keyword in json.dumps(schema) for keyword in ('"oneOf"', '"if"', '"then"', '"$ref"'))
    assert 'response_format' not in calls[0][1] and calls[0][1]['tool_choice'] == 'required'
    assert calls[0][1]['tools'][0]['function']['name'] == 'submit_script_draft'
    assert '事实只能来自' in calls[0][1]['messages'][0]['content']


@pytest.mark.parametrize('template_count', [None, 2])
def test_two_section_review_schema_pins_actual_count_and_keeps_template_count_semantics(template_count):
    document = draft()
    document['script']['sections'].append(copy.deepcopy(document['script']['sections'][0]))
    calls = []
    result = provider(document, reviewed(document), calls).generate(replace(inputs(), script_section_count=template_count))
    assert len(result.sections) == 2 and len(calls) == 2
    payload = calls[1][1]
    actual_input = json.loads(payload['messages'][1]['content'])
    sections_schema = payload['tools'][0]['function']['parameters']['properties']['sections']
    assert sections_schema['minItems'] == sections_schema['maxItems'] == 2
    assert [branch['properties']['index'] for branch in sections_schema['items']['anyOf']] == [
        {'type': 'integer', 'enum': [1]}, {'type': 'integer', 'enum': [1]},
        {'type': 'integer', 'enum': [2]}, {'type': 'integer', 'enum': [2]}]
    assert actual_input['review_section_count'] == len(actual_input['script']['sections']) == 2
    assert actual_input['requirements']['section_count'] == template_count
    assert '必须恰好返回2项' in payload['messages'][0]['content']
    assert payload['max_tokens'] == 8192


def test_review_scope_keeps_chinese_delivery_and_planned_visuals_separate_from_parent_goal():
    goal = '制作中英文各一条30秒视频，各用对应语言配音字幕；标明公开素材并保留署名和许可，不编造设备性能。'
    visual = '计划在右上角显示公开素材演示，片尾显示作者署名及许可信息。'
    document = draft(FACT, visual)
    calls = []
    result = provider(document, reviewed(document), calls).generate(replace(inputs(), goal=goal,
        current_instruction=goal, duration_target_seconds=30, language='zh-CN', presenter_share_target=0))
    assert result.sections[0].visual_prompt == visual and len(calls) == 2
    data = json.loads(calls[1][1]['messages'][1]['content'])
    assert data['goal'] == data['current_instruction'] == goal
    assert data['review_scope'] == {'stage': 'script_draft', 'delivery_count': 1, 'language': 'zh-CN',
        'duration_target_seconds': 30, 'presenter_share_target': 0}
    system = calls[1][1]['messages'][0]['content']
    assert '其它条视频不要求在本稿中一并完成' in system
    assert '不因实际图层尚未生成、未单独建片尾段而拒绝' in system
    assert '不默认要求双语字幕' in system
    assert '不能由本条范围覆盖明确用户限制' in system


def test_each_schema_branch_pairs_only_current_section_candidates_and_declared_evidence():
    source = replace(inputs(), sources=(*inputs().sources, CreationTaskTopicSource(
        reference_id='other-fixed-source', kind='knowledge', title='第二份资料', text=FACT)))
    document = draft()
    document['script']['sections'].append({**copy.deepcopy(document['script']['sections'][0]),
        'evidence_reference_ids': ['evidence_2']})
    review = reviewed(document)
    review['sections'][1]['supports'][0]['source_quote_id'] = '@evidence_2:0'
    calls = []
    assert len(provider(document, review, calls).generate(source).sections) == 2
    payload = calls[1][1]
    data = json.loads(payload['messages'][1]['content'])
    schema = payload['tools'][0]['function']['parameters']['properties']['sections']
    assert schema['minItems'] == schema['maxItems'] == 2
    assert len(schema['items']['anyOf']) == 4
    global_title_ids = [piece['quote_id'] for piece in data['script']['title']]
    for section, branch in zip(data['script']['sections'], schema['items']['anyOf'][1::2]):
        props = branch['properties']
        assert props['index'] == {'type': 'integer', 'enum': [section['index']]}
        local_ids = global_title_ids + [piece['quote_id'] for field in ('title', 'content', 'visual_prompt') for piece in section[field]]
        source_ids = [value for ids in section['allowed_source_quote_ids'].values() for value in ids]
        support = props['supports']['items']['properties']
        assert support['candidate_quote_id']['enum'] == local_ids
        assert support['source_quote_id']['enum'] == source_ids
        for issue_branch, kind in zip(props['issues']['items']['anyOf'], ('unsupported_fact', 'goal_conflict')):
            issue = issue_branch['properties']
            assert issue['kind']['enum'] == [kind]
            assert issue['candidate_quote_id']['enum'] == local_ids
            assert issue['reference_ids']['items']['enum'] == section['evidence_reference_ids']
            assert issue['reference_ids']['minItems'] == 1
            assert ('reference_ids' in issue_branch['required']) is (kind == 'unsupported_fact')
            assert issue['reference_ids']['uniqueItems'] is True
    assert payload['max_tokens'] == 8192


@pytest.mark.parametrize('role', ['support', 'issue'])
@pytest.mark.parametrize('mismatch', ['missing', 'type', 'unknown', 'cross_section'])
def test_invalid_candidate_quotes_still_stop_with_bounded_resolution_diagnostic(caplog, role, mismatch):
    import logging

    document = draft()
    document['script']['sections'].append(copy.deepcopy(document['script']['sections'][0]))
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _response(document)
        data = json.loads(body['messages'][1]['content'])
        review = bound_review(reviewed(document), data)
        bad_quote = {'missing': None, 'type': ['private-value'], 'unknown': 'private-unknown-quote',
            'cross_section': data['script']['sections'][1]['content'][0]['quote_id']}[mismatch]
        if role == 'support':
            review['sections'][0]['supports'][0]['candidate_quote_id'] = bad_quote
        else:
            review['sections'][0]['issues'] = [{'kind': 'unsupported_fact', 'candidate_quote_id': bad_quote,
                'reference_ids': ['evidence_1'], 'missing_or_conflicting': 'private-issue-explanation'}]
        return _review_tool_response(review)

    caplog.set_level(logging.WARNING, logger='app.topics')
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        api_key='private-key', transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        generator.generate(inputs())
    assert failure.value.reason_code == ('support_quote' if role == 'support' else 'issue_quote')
    assert failure.value.code == 'script_review_invalid' and failure.value.review_issues is None
    assert failure.value.retryable is False and len(calls) == 2
    records = [record for record in caplog.records if record.name == 'app.topics']
    assert len(records) == 1
    text = records[0].getMessage()
    diagnostic = json.loads(text.removeprefix('creation_script_review_invalid '))['candidate_quote_mismatch']
    assert diagnostic == {'subtype': mismatch, 'item_index': 1, 'expected_section_index': 1,
        'resolved_section_index': 2 if mismatch == 'cross_section' else None,
        'candidate_field': 'content' if mismatch == 'cross_section' else None}
    assert 'private-' not in text and FACT not in text and len(text) < 1200


@pytest.mark.parametrize('encoded', [False, True])
def test_array_fields_decode_once_without_losing_findings(encoded):
    document = draft()
    document['script']['sections'] *= 3
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _response(document)
        assert len(calls) == 2
        data = json.loads(body['messages'][1]['content'])
        report = bound_review(reviewed(document), data)
        report['sections'][0]['issues'] = [{'kind': 'unsupported_fact',
            'candidate_quote_id': data['script']['sections'][0]['content'][0]['quote_id'],
            'reference_ids': ['evidence_1'], 'missing_or_conflicting': '资料未支持此承诺'}]
        report['sections'][2]['issues'] = [{'kind': 'goal_conflict',
            'candidate_quote_id': data['script']['sections'][2]['visual_prompt'][0]['quote_id'],
            'missing_or_conflicting': '画面要求与原交付约束冲突'}]
        if encoded:
            for section in report['sections']:
                for issue in section.get('issues', []):
                    if 'reference_ids' in issue:
                        issue['reference_ids'] = json.dumps(issue['reference_ids'])
                if 'issues' in section:
                    section['issues'] = json.dumps(section['issues'], ensure_ascii=False)
        return _review_tool_response(report)

    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'MiniMax-M3',
        transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        generator.generate(inputs())
    assert len(calls) == 2 and failure.value.code == 'script_fact_unsupported'
    findings = failure.value.review_issues
    assert len(findings) == 2
    assert findings[0]['error'] == 'script_fact_unsupported' and findings[0]['review_explanation'] == '资料未支持此承诺'
    assert findings[1]['error'] == 'script_goal_conflict' and findings[1]['section_index'] == 3
    assert findings[1]['candidate_field'] == 'visual_prompt'
    assert findings[1]['review_explanation'] == '画面要求与原交付约束冲突'


@pytest.mark.parametrize('field,value,subtype,decoded_type', [
    ('issues', 'private-natural-language', 'invalid_json', None),
    ('issues', '[] private-trailing-text', 'invalid_json', None),
    ('issues', json.dumps('[]'), 'not_array', 'string'),
    ('reference_ids', 'null', 'not_array', 'null'),
    ('reference_ids', '{}', 'not_array', 'object'),
    ('issues', '[{"kind":"goal_conflict","kind":"unsupported_fact"}]', 'duplicate_key', None),
    ('reference_ids', '[NaN]', 'non_standard_number', None),
    ('issues', '```json\n[]\n```', 'invalid_json', None),
])
def test_array_fields_reject_non_lossless_or_invalid_representation(caplog, field, value, subtype, decoded_type):
    document = draft()
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _response(document)
        assert len(calls) == 2
        report = bound_review(rejected(document, kind='unsupported_fact', field='content', quote=FACT,
            detail='资料未支持此承诺'), json.loads(body['messages'][1]['content']))
        if field == 'issues':
            report['sections'][0]['issues'] = value
        else:
            report['sections'][0]['issues'][0]['reference_ids'] = value
        return _review_tool_response(report)

    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'MiniMax-M3',
        api_key='private-credential', transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        generator.generate(inputs())
    assert len(calls) == 2 and failure.value.code == 'script_review_invalid' and not failure.value.retryable
    assert failure.value.reason_code == ('issue_shape' if field == 'issues' else 'issue_reference')
    message = next(record.getMessage() for record in caplog.records
        if record.getMessage().startswith('creation_script_review_invalid '))
    diagnostic = json.loads(message.removeprefix('creation_script_review_invalid '))
    assert diagnostic['array_decode_mismatch'] == {'field': field, 'subtype': subtype,
        'returned_type': 'string', 'decoded_type': decoded_type, 'issue_index': None if field == 'issues' else 1}
    for secret in ('private-', FACT, SOURCE, 'knowledge:fixture:v1', 'https://models.invalid', 'Authorization'):
        assert secret not in caplog.text


@pytest.mark.parametrize('field,value,reason', [
    ('issues', '[{"kind":"unsupported_fact"}]', 'issue_shape'),
    ('reference_ids', '["private-unknown-reference"]', 'issue_reference'),
])
def test_decoded_arrays_still_require_original_structure_and_references(field, value, reason):
    document = draft()
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _response(document)
        assert len(calls) == 2
        report = bound_review(rejected(document, kind='unsupported_fact', field='content', quote=FACT,
            detail='资料未支持此承诺'), json.loads(body['messages'][1]['content']))
        if field == 'issues':
            report['sections'][0]['issues'] = value
        else:
            report['sections'][0]['issues'][0]['reference_ids'] = value
        return _review_tool_response(report)

    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'MiniMax-M3',
        transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        generator.generate(inputs())
    assert len(calls) == 2 and failure.value.code == 'script_review_invalid'
    assert failure.value.reason_code == reason


def test_review_deadline_cancels_inflight_request():
    import asyncio
    from app.topics import CreationTaskGeneratedScript, CreationTaskGeneratedScriptSection

    document = draft()
    section = document['script']['sections'][0]
    candidate = CreationTaskGeneratedScript(title=document['script']['title'], language='zh-CN', sections=(
        CreationTaskGeneratedScriptSection(title=section['title'], content=section['content'],
            visual_prompt=section['visual_prompt'], evidence_reference_ids=('knowledge:fixture:v1',)),))
    calls, cancelled = [], []

    async def respond(request):
        calls.append(json.loads(request.content))
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        timeout=0.03, transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        generator.check_draft(inputs(), candidate)
    assert failure.value.code == 'transport_failed'
    assert len(calls) == 1 and cancelled == [True]


@pytest.mark.parametrize('returned,type_name,actual_count,reason', [
    ('less', 'array', 1, 'section_count'),
    ('more', 'array', 3, 'section_count'),
    ('duplicate', 'array', 2, 'index'),
    ('missing', 'missing', None, 'section_count'),
    (None, 'null', None, 'section_count'),
    ('private-invalid-sections', 'string', None, 'section_count'),
    ({'private-unknown-key': 'private-body'}, 'object', None, 'section_count'),
    (True, 'boolean', None, 'section_count'),
    (7, 'number', None, 'section_count'),
])
def test_review_still_rejects_incomplete_extra_or_duplicate_sections_with_safe_counts(caplog, returned, type_name, actual_count, reason):
    import logging

    document = draft()
    document['script']['sections'].append(copy.deepcopy(document['script']['sections'][0]))
    review = reviewed(document)
    if returned == 'less':
        review['sections'] = review['sections'][:1]
    elif returned == 'more':
        review['sections'].append({**copy.deepcopy(review['sections'][1]), 'index': 3})
    elif returned == 'duplicate':
        review['sections'][1]['index'] = 1
    elif returned == 'missing':
        del review['sections']
    else:
        review['sections'] = returned
    calls = []
    caplog.set_level(logging.WARNING, logger='app.topics')
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        provider(document, review, calls).generate(inputs())
    assert failure.value.code == 'script_review_invalid' and failure.value.reason_code == reason
    assert failure.value.retryable is False and failure.value.review_issues is None
    assert len(calls) == 2 and calls[1][1]['max_tokens'] == 8192
    records = [record for record in caplog.records if record.name == 'app.topics']
    assert len(records) == 1
    text = records[0].getMessage()
    diagnostic = json.loads(text.removeprefix('creation_script_review_invalid '))
    assert diagnostic['section_count'] == 2
    assert diagnostic['returned_sections_type'] == type_name
    assert diagnostic['returned_section_count'] == actual_count
    assert 'private-' not in text and FACT not in text and len(text) < 1200


@pytest.mark.parametrize('claim', ['按天自动备份', '断电误删可回溯', '实时预览帧率不掉', '中文旁白已对准口型'])
def test_same_valid_reference_does_not_support_unstated_promises(claim):
    document = draft(FACT + claim + '。')
    review = rejected(document, kind='unsupported_fact', field='content', quote=claim,
        detail=f'本机工作区导览资料没有说明{claim}。')
    calls = []
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        provider(document, review, calls).generate(inputs())
    error = failure.value
    assert error.code == 'script_fact_unsupported' and not error.retryable
    assert error.reason_code == 'unsupported_fact' and error.section_index == 1
    assert claim in error.reason and '本机工作区导览资料' in error.repair_action
    assert 'evidence_1' not in error.repair_action and 'knowledge:fixture:v1' not in error.repair_action
    assert len(calls) == 2


@pytest.mark.parametrize('quote,field,detail', [
    ('安排人物出镜并口播', 'visual_prompt', '方案要求人物不出镜，请使用素材画面。'),
    ('旁白中英文轮流朗读', 'content', '目标要求中文旁白；中英字幕不等于旁白混读。'),
    ('现在已生成并发布成片', 'visual_prompt', '目标是暂不开始制作，不能把准备文案写成已发布。'),
])
def test_goal_conflicts_are_separate_from_valid_source_support(quote, field, detail):
    document = draft(FACT + (quote if field == 'content' else ''), quote if field == 'visual_prompt' else '素材设置界面')
    calls = []
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        provider(document, rejected(document, kind='goal_conflict', field=field, quote=quote, detail=detail), calls).generate(inputs())
    assert failure.value.code == 'script_goal_conflict' and not failure.value.retryable
    assert failure.value.reason_code == 'goal_conflict' and failure.value.section_index == 1
    assert detail in failure.value.repair_action and len(calls) == 2


@pytest.mark.parametrize('malformed', ['missing-section', 'duplicate-index', 'wrong-alias', 'fake-quote',
                                     'fake-source-quote', 'bare-pass', 'wrong-field-type', 'truncated',
                                     'empty-issues', 'encoded-empty-issues', 'empty-goal-refs', 'goal-without-support'])
def test_review_must_cover_every_section_with_exact_support(malformed):
    document = draft()
    review = reviewed(document)
    if malformed == 'missing-section':
        review['sections'] = []
    elif malformed == 'duplicate-index':
        document['script']['sections'].append(copy.deepcopy(document['script']['sections'][0]))
        review['sections'].append(copy.deepcopy(review['sections'][0]))
    elif malformed == 'wrong-alias':
        review['sections'][0]['supports'][0]['source_quote_id'] = '@evidence_2:0'
    elif malformed == 'fake-quote':
        review['sections'][0]['supports'][0]['candidate_quote_id'] = 'unknown-candidate-fragment'
    elif malformed == 'fake-source-quote':
        review['sections'][0]['supports'][0]['source_quote_id'] = 'unknown-source-fragment'
    elif malformed == 'bare-pass':
        review['sections'][0]['supports'] = []
    elif malformed == 'wrong-field-type':
        review['sections'][0]['supports'][0]['candidate_quote_id'] = []
    elif malformed in {'empty-issues', 'encoded-empty-issues'}:
        review['sections'][0]['issues'] = [] if malformed == 'empty-issues' else '[]'
    elif malformed in {'empty-goal-refs', 'goal-without-support'}:
        review = rejected(document, kind='goal_conflict', field='content', quote=FACT, detail='与当前要求冲突。')
        if malformed == 'empty-goal-refs':
            review['sections'][0]['issues'][0]['reference_ids'] = []
        else:
            del review['sections'][0]['supports']
    elif malformed == 'truncated':
        review = httpx.Response(200, json={'choices': [{'finish_reason': 'length',
            'message': {'content': json.dumps(review)}}]})
    calls = []
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        provider(document, review, calls).generate(inputs())
    assert failure.value.code == 'script_review_invalid'
    assert len(calls) == 2 and not failure.value.retryable
    if malformed == 'goal-without-support':
        # Check the actual generation contract as well as consumer rejection:
        # goal-only reports need support, while mixed findings may omit it.
        schema = calls[1][1]['tools'][0]['function']['parameters']
        problem = schema['properties']['sections']['items']['anyOf'][1]
        support_branch, unsupported_branch = problem['anyOf']
        assert support_branch == {'required': ['supports']}
        assert problem['properties']['supports']['minItems'] == 1
        assert unsupported_branch == {'properties': {'issues': {'contains': {
            'type': 'object', 'required': ['kind'],
            'properties': {'kind': {'enum': ['unsupported_fact']}}}}}}
        assert [branch['properties']['kind']['enum'] for branch in
            problem['properties']['issues']['items']['anyOf']] == [['unsupported_fact'], ['goal_conflict']]


@pytest.mark.parametrize('invalid', ['language', 'references', 'sections'])
def test_invalid_generated_draft_never_reaches_review(invalid):
    document = draft()
    source = inputs()
    if invalid == 'language':
        document['script']['language'] = 'en-US'
    elif invalid == 'references':
        document['script']['sections'][0]['evidence_reference_ids'] = ['not-an-alias']
    else:
        source = replace(source, script_section_count=2)
    calls = []
    with pytest.raises(CreationTaskScriptGenerationError):
        provider(document, reviewed(document), calls).generate(source)
    assert len(calls) == 1


@pytest.fixture
def api_state(tmp_path, monkeypatch):
    repository = ProductionRepository(tmp_path / 'isolated.sqlite', tmp_path / 'library')
    members = LocalMemberService(repository.database_path)
    owner, token = members.whoami(None)
    sources = SourceRepository(repository.database_path)
    knowledge = sources.create_knowledge_item_owned(KnowledgeItem(id='fixture', workspace_id='local',
        content_type='note', title='本机工作区导览资料', body=SOURCE, file_reference=None,
        authorization='authorized', ownership='user_owned', source_item_id=None, version='v1'),
        verified_user_id=owner.member_id)
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'source_repository', sources)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'auth_context', SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: 'local')
    settings = Settings(llm_provider='custom', llm_model='same-model',
        llm_base_url='https://models.invalid/v1', llm_api_key='isolated-key', llm_api_key_provider='custom')
    monkeypatch.setattr(main, '_workspace_settings_for', lambda workspace_id: settings)
    monkeypatch.setattr(main, '_topic_route_snapshot', lambda workspace_id: {
        'availability': 'unverified', 'execution_location': 'cloud', 'provider_kind': 'controlled',
        'model_id': 'same-model', 'endpoint_identity': 'isolated', 'connection_version': 'isolated-v1',
        'checked_at': datetime.now(timezone.utc).isoformat()})
    verifications = []
    def record_verification(settings, **record):
        verifications.append(record)
        return record_llm_service_verification(settings, **record)
    monkeypatch.setattr(main, 'record_llm_service_verification', record_verification)
    plan = CreationPlan(id='plan', workspace_id='local', goal=GOAL, name='导览', target_count=1,
        language='zh-CN', aspect_ratio='9:16', duration_target_seconds=120, presenter_share_target=0,
        knowledge_references=[CreationPlanKnowledgeReference(knowledge.id, knowledge.version, knowledge.title,
            knowledge.authorization, knowledge.ownership)], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0, material_rule_summary='无媒体',
        model_snapshot=[], confirmation_points=[], blockers=[], preflight_fingerprint='isolated')
    task = CreationTask(id='task', workspace_id='local', plan_id=plan.id, name=plan.name, scenario=GOAL)
    plan, task = repository.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    _, input_refs, _ = main._creation_task_topic_inputs('local', task, plan, cloud_egress=True)
    run = TopicGenerationRun(id='topic-run', workspace_id='local', task_id=task.id, plan_id=plan.id,
        status='running', route_snapshot={}, prompt_schema_version='isolated', input_references=input_refs)
    repository.start_topic_generation(run)
    topic = TopicCandidate(id='topic', workspace_id='local', task_id=task.id, run_id=run.id,
        title='本机工作区导览', angle='讲清入口', audience='创作者', outline=['素材用途', '计划自动备份'], score=90)
    evidence = [TopicEvidenceReference(id='topic-evidence', workspace_id='local', task_id=task.id,
        candidate_id=topic.id, kind='knowledge', reference_id=knowledge.id, version_or_identity=knowledge.version,
        title=knowledge.title, authorization_or_license='authorized', source_url=None)]
    repository.complete_topic_generation(run.id, [topic], evidence)
    repository.confirm_topic_candidate('local', task.id, topic.id)
    app = FastAPI()
    app.add_api_route('/api/creation-tasks/{task_id}/scripts/generate', main.generate_creation_task_script, methods=['POST'])
    app.add_api_route('/api/creation-tasks/{task_id}/assistant/script-draft', main.save_assistant_script_draft, methods=['POST'])
    app.add_api_route('/api/creation-tasks/{task_id}/assistant/script-context', main.get_assistant_script_context, methods=['GET'])
    return SimpleNamespace(repository=repository, owner=owner, task=task, plan=plan, refs=input_refs,
        client=TestClient(app, headers={'X-Local-Member-Token': token}), verifications=verifications, settings=settings, sources=sources)


def set_api_provider(monkeypatch, document, review, calls):
    generator = provider(document, review, calls)
    monkeypatch.setattr(main, '_creation_task_script_generator_for', lambda settings: generator)


def test_api_saves_supported_draft_as_unconfirmed_v2_run(api_state, monkeypatch):
    document = draft()
    calls = []
    set_api_provider(monkeypatch, document, reviewed(document), calls)
    result = api_state.client.post('/api/creation-tasks/task/scripts/generate', json={})
    assert result.status_code == 200, result.text
    scripts = api_state.repository.list_creation_task_scripts('local', 'task')
    assert len(scripts) == 1 and scripts[0].selection_status == 'draft'
    assert api_state.repository.get_creation_task('local', 'task').confirmed_script_id is None
    run = api_state.repository.latest_script_generation_run('local', 'task')
    assert run.status == 'succeeded' and run.prompt_schema_version == 'creation-task-script/v2'
    assert api_state.verifications[-1]['ok'] is True and len(calls) == 2
    assert llm_service_verification(api_state.settings, workspace_id='local')['ok'] is True
    assert json.loads(calls[1][1]['messages'][1]['content'])['requirements']['presenter_share_target'] == api_state.plan.presenter_share_target


@pytest.mark.parametrize('endpoint', ['scripts/generate', 'assistant/script-draft'])
@pytest.mark.parametrize('code,reason_code,section_index', [
    ('script_review_invalid', 'issue_reference', 3),
    ('script_review_invalid', 'support_quote', 5),
    ('script_fact_unsupported', 'unsupported_fact', 3),
    ('script_goal_conflict', 'goal_conflict', 2),
])
def test_script_http_errors_preserve_safe_category_section_and_retryability(
    api_state, monkeypatch, endpoint, code, reason_code, section_index,
):
    location = ({'candidate_section_index': 0, 'candidate_field': 'title',
        'candidate_fragment_index': 0, 'candidate_field_digest': 'a' * 64} if code == 'script_fact_unsupported'
        else {'candidate_section_index': section_index, 'candidate_field': 'visual_prompt',
            'candidate_fragment_index': 2, 'candidate_field_digest': 'b' * 64} if code == 'script_goal_conflict' else {})
    review_issues = ([{'error': code, 'reason_code': reason_code, 'section_index': section_index,
        **location, 'review_explanation': '已核查的有限说明。'},
        {'error': 'script_goal_conflict', 'reason_code': 'goal_conflict', 'section_index': 2,
            'candidate_section_index': 2, 'candidate_field': 'content', 'candidate_fragment_index': 0,
            'candidate_field_digest': 'c' * 64, 'review_explanation': '另一字段存在要求冲突。'}] if location else None)
    failure = CreationTaskScriptGenerationError(code=code, reason='安全的分段失败说明。',
        impact='没有保存草稿。', repair_action='先核对指定段落及资料。', retryable=False,
        reason_code=reason_code, section_index=section_index, review_issues=review_issues, **location)
    def reject(*args):
        raise failure
    monkeypatch.setattr(main, '_creation_task_script_generator_for', lambda settings:
        SimpleNamespace(generate=reject, check_draft=reject))
    body = {} if endpoint == 'scripts/generate' else agent_payload(api_state)
    response = api_state.client.post('/api/creation-tasks/task/' + endpoint, json=body)
    assert response.status_code == (502 if code == 'script_review_invalid' else 422), response.text
    detail = response.json()['detail']
    assert {key: detail[key] for key in ('error', 'reason_code', 'section_index', 'retryable')} == {
        'error': code, 'reason_code': reason_code, 'section_index': section_index, 'retryable': False}
    assert {key: value for key, value in detail.items() if key.startswith('candidate_')} == location
    assert detail.get('review_issues') == review_issues
    assert api_state.repository.list_creation_task_scripts('local', 'task') == []


def test_provided_topic_without_generated_metadata_reaches_real_script_provider(api_state, monkeypatch):
    state = api_state
    with state.repository._connect() as conn:
        plan = json.loads(conn.execute("SELECT payload FROM creation_plans WHERE id='plan'").fetchone()[0])
        plan['creation_intent'] = {'kind': 'goal_delegation', 'topic_title': '本机工作区导览',
            'source': {'kind': 'user_action', 'member_id': state.owner.member_id, 'request_id': 'provided-topic-fixture'}}
        topic = json.loads(conn.execute("SELECT payload FROM topic_candidates WHERE id='topic'").fetchone()[0])
        topic.update(angle='', audience='', outline=[])
        run = json.loads(conn.execute("SELECT payload FROM topic_generation_runs WHERE id='topic-run'").fetchone()[0])
        run['route_snapshot'] = {'operation': 'provided_topic', 'provider': 'none'}
        conn.execute("UPDATE creation_plans SET payload=? WHERE id='plan'", (json.dumps(plan),))
        conn.execute("UPDATE topic_candidates SET payload=? WHERE id='topic'", (json.dumps(topic),))
        conn.execute("UPDATE topic_generation_runs SET payload=? WHERE id='topic-run'", (json.dumps(run),))
    document, calls = draft(), []
    set_api_provider(monkeypatch, document, reviewed(document), calls)
    response = state.client.post('/api/creation-tasks/task/scripts/generate', json={})
    assert response.status_code == 200, response.text
    supplied = json.loads(calls[0][1]['messages'][1]['content'])['confirmed_topic']
    assert supplied == {'origin': 'provided', 'title': '本机工作区导览', 'angle': '', 'audience': '', 'outline': []}
    assert len(calls) == 2
    assert state.repository.get_creation_task('local', 'task').selected_topic_id == 'topic'


def test_generated_topic_still_requires_its_actual_metadata():
    calls = []
    generator = provider(draft(), reviewed(draft()), calls)
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        generator.generate(replace(inputs(), topic_angle='', topic_audience='', topic_outline=()))
    assert failure.value.code == 'input_invalid' and calls == []


@pytest.mark.parametrize('failure', ['unsupported', 'goal', 'review', 'transport', 'http'])
def test_api_failed_review_preserves_old_draft_and_classifies_connection(api_state, monkeypatch, caplog, failure):
    repository = api_state.repository
    old_run = ScriptGenerationRun(id='old-run', workspace_id='local', task_id='task', plan_id='plan', topic_id='topic',
        status='running', route_snapshot={}, prompt_schema_version='old-fixture', input_references=api_state.refs)
    repository.start_script_generation(old_run)
    old = CreationTaskScript(id='old-script', workspace_id='local', task_id='task', run_id=old_run.id,
        topic_id='topic', title='已有未确认草稿', language='zh-CN', sections=[CreationTaskScriptSection(
            id='old-section', title='用途', content=FACT, visual_prompt='素材界面',
            evidence_reference_ids=[item['reference_id'] for item in api_state.refs])])
    repository.complete_script_generation(old_run.id, old)
    # Recovery fixture: a failed script stage has retained its previous,
    # unconfirmed draft. Keep the real route's stage/membership/source checks.
    with repository._connect() as conn:
        row = conn.execute("SELECT payload FROM creation_tasks WHERE id='task'").fetchone()
        task_payload = json.loads(row['payload'])
        task_payload.update(stage_status='failed', status='failed', recovery_point='script')
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id='task'", (json.dumps(task_payload),))
        before = tuple(conn.execute("SELECT * FROM creation_task_scripts WHERE id='old-script'").fetchone())
    document = draft(FACT + '每天自动备份。', '安排人物出镜口播')
    expected_code = {'unsupported': 'script_fact_unsupported', 'goal': 'script_goal_conflict',
        'review': 'script_review_invalid', 'transport': 'transport_failed', 'http': 'http_failed'}[failure]
    if failure == 'unsupported':
        review = rejected(document, kind='unsupported_fact', field='content', quote='每天自动备份',
            detail='本机工作区导览资料没有自动备份说明。')
    elif failure == 'goal':
        review = rejected(document, kind='goal_conflict', field='visual_prompt', quote='人物出镜口播',
            detail='本次方案要求人物不出镜，请采用素材画面。')
    elif failure == 'review':
        review = {'sections': []}
    elif failure == 'transport':
        review = httpx.ConnectError('isolated connection failure')
    else:
        review = httpx.Response(503, text='isolated provider failure')
    calls = []
    set_api_provider(monkeypatch, document, review, calls)
    response = api_state.client.post('/api/creation-tasks/task/scripts/generate', json={})
    assert response.status_code == (422 if failure in {'unsupported', 'goal'} else 502), response.text
    assert response.json()['detail']['error'] == expected_code
    if failure in {'transport', 'http'}:
        records = [record.getMessage() for record in caplog.records
            if record.name == 'app.topics' and record.getMessage().startswith('creation_script_review_request_failed ')]
        assert len(records) == 1
        assert json.loads(records[0].removeprefix('creation_script_review_request_failed ')) == {
            'error_category': expected_code, 'http_status': 503 if failure == 'http' else None}
        assert 'isolated' not in records[0] and FACT not in records[0]
        assert ('HTTP 503' if failure == 'http' else '未取得完整HTTP响应') in response.json()['detail']['reason']
        assert '额度' not in response.json()['detail']['repair_action']
    assert len(calls) == 2
    assert len(repository.list_creation_task_scripts('local', 'task')) == 1
    with repository._connect() as conn:
        assert tuple(conn.execute("SELECT * FROM creation_task_scripts WHERE id='old-script'").fetchone()) == before
    run = repository.latest_script_generation_run('local', 'task')
    assert run.status == 'failed' and run.prompt_schema_version == 'creation-task-script/v2'
    assert repository.get_creation_task('local', 'task').confirmed_script_id is None
    assert api_state.verifications[-1]['ok'] is (failure not in {'transport', 'http'})
    assert llm_service_verification(api_state.settings, workspace_id='local')['ok'] is (failure not in {'transport', 'http'})
    assert run.route_snapshot['availability'] == ('failed' if failure in {'transport', 'http'} else 'succeeded')


def saved_agent_draft(state):
    """Use the real successful lifecycle; the task stays waiting_for_user."""
    run = ScriptGenerationRun(id='retained-run', workspace_id='local', task_id='task', plan_id='plan', topic_id='topic',
        status='running', route_snapshot={}, prompt_schema_version='creation-task-script/v2', input_references=state.refs)
    state.repository.start_script_generation(run)
    script = CreationTaskScript(id='retained-script', workspace_id='local', task_id='task', run_id=run.id,
        topic_id='topic', title='已有草稿', language='zh-CN', sections=[CreationTaskScriptSection(
            id='retained-section', title='素材用途', content=FACT, visual_prompt='用途设置界面',
            evidence_reference_ids=[item['reference_id'] for item in state.refs])])
    return state.repository.complete_script_generation(run.id, script)[2]


def agent_payload(state, script=None, *, content=FACT, visual='用途设置界面'):
    body = {'title': '修订后的导览', 'sections': [{'id': script.sections[0].id if script else 'agent-section',
        'title': '素材用途', 'content': content, 'visual_prompt': visual,
        'evidence_reference_ids': [item['reference_id'] for item in state.refs]}]}
    if script:
        body['expected_updated_at'] = script.updated_at.isoformat()
    return body


def set_agent_reviewer(monkeypatch, review, calls, during_check=None):
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        assert 'script' in json.loads(body['messages'][1]['content'])
        if during_check:
            during_check()
        return _review_tool_response(bound_review(review, json.loads(body['messages'][1]['content'])))
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        transport=httpx.MockTransport(respond))
    monkeypatch.setattr(main, '_creation_task_script_generator_for', lambda settings: generator)


def private_task_rows(state):
    with state.repository._connect() as conn:
        return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY id')]
                for table in ('creation_tasks', 'creation_task_scripts', 'script_generation_runs')}


def test_agent_waiting_draft_is_checked_once_then_updated_with_original_ids(api_state, monkeypatch):
    original = saved_agent_draft(api_state)
    original_task = api_state.repository.get_creation_task('local', 'task')
    original_run = api_state.repository.get_script_generation_run('local', original.run_id)
    assert original_task.stage_status == 'waiting_for_user'
    body = agent_payload(api_state, original, content=FACT + '像搭积木一样，先定用途再组织内容。')
    calls = []
    set_agent_reviewer(monkeypatch, reviewed(draft()), calls)
    context = api_state.client.get('/api/creation-tasks/task/assistant/script-context')
    assert context.status_code == 200, context.text
    assert context.json()['presenter_share_target'] == 0
    assert context.json()['script_structure'] == [] and context.json()['script_section_count'] is None
    response = api_state.client.post('/api/creation-tasks/task/assistant/script-draft', json=body)
    assert response.status_code == 200, response.text
    revised = response.json()
    assert revised['id'] == original.id and revised['sections'][0]['id'] == original.sections[0].id
    assert revised['sections'][0]['content'] == body['sections'][0]['content']
    assert revised['selection_status'] == 'draft' and revised['updated_at'] != original.updated_at.isoformat()
    assert len(calls) == 1
    sent = json.loads(calls[0]['messages'][1]['content'])
    assert ''.join(piece['text'] for piece in sent['sources'][0]['text']) == SOURCE and sent['requirements']['presenter_share_target'] == 0
    assert sent['goal'] == GOAL
    assert api_state.repository.get_creation_task('local', 'task') == original_task
    assert api_state.repository.get_script_generation_run('local', original.run_id) == original_run
    assert len(api_state.repository.list_creation_task_scripts('local', 'task')) == 1


@pytest.mark.parametrize('kind', ['unsupported_fact', 'goal_conflict', 'invalid_review'])
def test_agent_rejection_preserves_waiting_task_draft_and_succeeded_run(api_state, monkeypatch, kind):
    original = saved_agent_draft(api_state)
    body = agent_payload(api_state, original, content=FACT + '每天自动备份。', visual='人物出镜口播')
    document = draft(body['sections'][0]['content'], body['sections'][0]['visual_prompt'])
    if kind == 'invalid_review':
        review = {'sections': []}
    else:
        review = rejected(document, kind=kind, field='content' if kind == 'unsupported_fact' else 'visual_prompt',
            quote='每天自动备份' if kind == 'unsupported_fact' else '人物出镜口播',
            detail='资料没有备份说明。' if kind == 'unsupported_fact' else '方案要求人物不出镜。')
    calls = []
    set_agent_reviewer(monkeypatch, review, calls)
    before = private_task_rows(api_state)
    response = api_state.client.post('/api/creation-tasks/task/assistant/script-draft', json=body)
    assert response.status_code == (502 if kind == 'invalid_review' else 422), response.text
    assert private_task_rows(api_state) == before
    assert len(calls) == 1
    assert llm_service_verification(api_state.settings, workspace_id='local')['ok'] is True


def test_agent_first_draft_is_checked_before_any_run_or_script_write(api_state, monkeypatch):
    calls = []
    def before_response():
        assert api_state.repository.list_creation_task_scripts('local', 'task') == []
        assert api_state.repository.latest_script_generation_run('local', 'task') is None
    set_agent_reviewer(monkeypatch, reviewed(draft()), calls, before_response)
    response = api_state.client.post('/api/creation-tasks/task/assistant/script-draft', json=agent_payload(api_state))
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    run = api_state.repository.latest_script_generation_run('local', 'task')
    assert run.status == 'succeeded' and run.prompt_schema_version == 'assistant-script-draft/v2'
    assert api_state.repository.get_creation_task('local', 'task').stage_status == 'waiting_for_user'
    assert api_state.repository.get_creation_task('local', 'task').confirmed_script_id is None


def test_agent_first_draft_rejection_leaves_no_run_or_script(api_state, monkeypatch):
    body = agent_payload(api_state, content=FACT + '每天自动备份。')
    review = rejected(draft(body['sections'][0]['content']), kind='unsupported_fact', field='content',
        quote='每天自动备份', detail='资料没有备份说明。')
    calls = []
    set_agent_reviewer(monkeypatch, review, calls)
    before = private_task_rows(api_state)
    response = api_state.client.post('/api/creation-tasks/task/assistant/script-draft', json=body)
    assert response.status_code == 422 and len(calls) == 1
    assert private_task_rows(api_state) == before


def test_agent_stale_cas_is_rejected_before_model_call(api_state, monkeypatch):
    original = saved_agent_draft(api_state)
    body = agent_payload(api_state, original)
    body['expected_updated_at'] = '2000-01-01T00:00:00Z'
    calls = []
    set_agent_reviewer(monkeypatch, reviewed(draft()), calls)
    before = private_task_rows(api_state)
    response = api_state.client.post('/api/creation-tasks/task/assistant/script-draft', json=body)
    assert response.status_code == 409 and calls == []
    assert private_task_rows(api_state) == before


def test_agent_source_revocation_during_check_cannot_write(api_state, monkeypatch):
    original = saved_agent_draft(api_state)
    body = agent_payload(api_state, original)
    before = private_task_rows(api_state)
    def revoke():
        api_state.sources.set_knowledge_item_use_state('local', 'fixture', 'excluded')
    calls = []
    set_agent_reviewer(monkeypatch, reviewed(draft()), calls, revoke)
    response = api_state.client.post('/api/creation-tasks/task/assistant/script-draft', json=body)
    assert response.status_code == 409 and len(calls) == 1, response.text
    assert private_task_rows(api_state) == before


def test_agent_concurrent_edit_during_check_preserves_newer_draft(api_state, monkeypatch):
    original = saved_agent_draft(api_state)
    body = agent_payload(api_state, original)
    concurrent = []
    def change_draft():
        concurrent.append(api_state.repository.update_assistant_script_draft('local', 'task', original.id,
            title='另一个明确修订', sections=original.sections, expected_updated_at=original.updated_at))
    calls = []
    set_agent_reviewer(monkeypatch, reviewed(draft()), calls, change_draft)
    response = api_state.client.post('/api/creation-tasks/task/assistant/script-draft', json=body)
    assert response.status_code == 409 and len(calls) == 1
    assert api_state.repository.get_creation_task_script('local', original.id) == concurrent[0]


def test_compact_five_section_review_closes_with_one_issue_per_failure_without_retry():
    document = draft(FACT + '每天自动备份。')
    document['script']['sections'] = [copy.deepcopy(document['script']['sections'][0]) for _ in range(5)]
    review = {'sections': [{'index': index,
        'issues': [{'kind': 'unsupported_fact', 'candidate_quote_id': '@content:0',
            'reference_ids': ['evidence_1'], 'missing_or_conflicting': '导览资料没有自动备份说明。'}]}
        for index in range(1, 6)]}
    calls = []
    with pytest.raises(CreationTaskScriptGenerationError) as error:
        provider(document, review, calls).generate(inputs())
    assert error.value.code == 'script_fact_unsupported'
    assert len(calls) == 2 and calls[1][1]['max_tokens'] == 8192
    # Five fully reviewed sections may be rejected for content, not misclassified
    # as an incomplete review; no repair generation or extra review follows.
    assert not error.value.retryable


def test_compact_review_keeps_both_failure_kinds_when_both_judgements_fail():
    document = draft(FACT + '每天自动备份。', '人物出镜口播')
    review = rejected(document, kind='unsupported_fact', field='content', quote='每天自动备份',
        detail='导览资料没有自动备份说明。')
    review['sections'][0]['issues'].append({'kind': 'goal_conflict', 'candidate_quote_id': '@visual_prompt:0', 'missing_or_conflicting': '方案要求人物不出镜。'})
    calls = []
    with pytest.raises(CreationTaskScriptGenerationError) as error:
        provider(document, review, calls).generate(inputs())
    assert error.value.code == 'script_fact_unsupported' and len(calls) == 2


@pytest.mark.parametrize('spoken_only', [False, True])
def test_english_spoken_text_and_credit_directions_have_distinct_review_results(spoken_only):
    content = ('The footage shows an outdoor maize shelling site, with a green cylindrical machine at the center.'
        if spoken_only else 'Opening tag "Public footage / 公开素材演示", on-screen text.')
    document = draft(content, 'Opening tag "Public footage / 公开素材演示" appears on screen.')
    document['script']['language'] = 'en-US'
    instruction = '正文只写会朗读的英语；素材演示标记和来源署名仅放画面建议。'
    value = replace(inputs(), language='en-US', current_instruction=instruction,
        sources=(CreationTaskTopicSource(reference_id='knowledge:fixture:v1', kind='knowledge',
            title='现场观察', text='户外玉米脱粒现场，绿色圆筒设备，可见侧面轮带、白色袋子、蓝色篷布及多人协作。'),))
    review = (reviewed(document) if spoken_only else rejected(document, kind='goal_conflict',
        field='content', quote=content, detail='片头文字应放在画面建议，正文里的说明都会被朗读。'))
    calls = []
    if spoken_only:
        result = provider(document, review, calls).generate(value)
        assert result.sections[0].content == content
    else:
        with pytest.raises(CreationTaskScriptGenerationError) as error:
            provider(document, review, calls).generate(value)
        assert error.value.code == 'script_goal_conflict'
        assert error.value.reason_code == 'goal_conflict' and error.value.section_index == 1
    assert len(calls) == 2
    assert json.loads(calls[1][1]['messages'][1]['content'])['current_instruction'] == instruction


def test_minimax_reasoning_only_length_response_is_rejected_without_retry():
    calls = []
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _response(draft())
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {
            'content': None, 'reasoning_content': 'controlled test reasoning, not a reviewed script',
        }}]})
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'MiniMax-M3',
        transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as error:
        generator.generate(inputs())
    assert error.value.code == 'script_review_invalid' and not error.value.retryable
    assert error.value.review_issues is None
    assert len(calls) == 2 and calls[1]['max_tokens'] == 8192
    assert all(body['reasoning_split'] is True for body in calls)


@pytest.mark.parametrize('failure_kind', ['length', 'envelope', 'tool_arguments', 'section_count',
    'section_shape', 'index', 'legacy_flags', 'support_shape', 'support_quote', 'support_reference',
    'source_quote', 'issue_shape', 'issue_quote', 'issue_reference', 'issue_count'])
def test_protocol_rejection_logs_fixed_reason_and_counts_without_sensitive_data(caplog, failure_kind):
    import logging

    candidate_secret = 'private-candidate-sentinel'
    source_secret = 'private-source-sentinel'
    reasoning_secret = 'private-reasoning-sentinel'
    credential_secret = 'private-credential-sentinel'
    source_id = 'private-original-source-id'
    document = draft(FACT + candidate_secret)
    source = replace(inputs(), sources=(CreationTaskTopicSource(reference_id=source_id, kind='knowledge',
        title='private-source-title', text=SOURCE + source_secret),))
    review = reviewed(document)
    section = review['sections'][0]
    finish = 'length' if failure_kind == 'length' else 'tool_calls'
    if failure_kind == 'section_count':
        review['sections'] = []
    elif failure_kind == 'section_shape':
        review['sections'] = [None]
    elif failure_kind == 'index':
        section['index'] = source_id
    elif failure_kind == 'legacy_flags':
        section.update(grounded=True, constraints_met=False)
    elif failure_kind == 'support_shape':
        section['supports'] = []
    elif failure_kind == 'support_quote':
        section['supports'][0]['candidate_quote_id'] = 'private-absent-quote'
    elif failure_kind == 'support_reference':
        section['supports'][0]['source_quote_id'] = '@evidence_2:0'
        source = replace(source, sources=(*source.sources, CreationTaskTopicSource(
            reference_id='other-source-id', kind='source', title='other-source', text=SOURCE)))
    elif failure_kind == 'source_quote':
        section['supports'][0]['source_quote_id'] = 'private-absent-source-quote'
    elif failure_kind in {'issue_shape', 'issue_quote', 'issue_reference'}:
        review = rejected(document, kind='unsupported_fact', field='content', quote=FACT,
            detail='private-issue-detail')
        issue = review['sections'][0]['issues'][0]
        if failure_kind == 'issue_shape':
            issue['kind'] = credential_secret
        elif failure_kind == 'issue_quote':
            issue['candidate_quote_id'] = 'private-absent-quote'
        else:
            issue['reference_ids'] = [source_id]
    elif failure_kind == 'issue_count':
        document['script']['sections'] *= 5
        issue = {'kind': 'unsupported_fact', 'candidate_quote_id': '@content:0',
            'reference_ids': ['evidence_1'], 'missing_or_conflicting': 'private-issue-detail'}
        review['sections'] = [{'index': index,
            'issues': [copy.deepcopy(issue) for _ in range(3)]} for index in range(1, 6)]
    content = 'private-content-report-sentinel'
    message = {'content': content, 'reasoning_content': reasoning_secret,
               'refusal': 'private-refusal-sentinel', 'tool_calls': [{'id': source_id, 'type': 'function', 'function': {
                   'name': 'submit_script_review', 'arguments': ''}}], credential_secret: source_secret}
    usage = {'prompt_tokens': 123, 'completion_tokens': 456, 'total_tokens': 579,
             'completion_tokens_details': {'reasoning_tokens': 300}, 'model': credential_secret}
    envelope = {'choices': [{'finish_reason': finish, 'message': message}], 'usage': usage}
    if failure_kind == 'envelope':
        envelope['choices'] = []
    calls = []
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return _response(document)
        if failure_kind != 'envelope':
            model_input = json.loads(json.loads(request.content)['messages'][1]['content'])
            message['tool_calls'][0]['function']['arguments'] = ('private-invalid-json' if failure_kind == 'tool_arguments'
                else json.dumps(bound_review(review, model_input), ensure_ascii=False))
        return httpx.Response(200, json=envelope)
    caplog.set_level(logging.WARNING, logger='app.topics')
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        api_key=credential_secret, transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as error:
        generator.generate(source)
    assert error.value.code == 'script_review_invalid' and not error.value.retryable
    assert len(calls) == 2
    records = [record for record in caplog.records if record.name == 'app.topics']
    assert len(records) == 1
    prefix = 'creation_script_review_invalid '
    text = records[0].getMessage()
    assert text.startswith(prefix) and len(text) < 1200
    diagnostic = json.loads(text.removeprefix(prefix))
    assert diagnostic['reason_code'] == ('section_shape' if failure_kind == 'legacy_flags' else failure_kind)
    assert error.value.reason_code == diagnostic['reason_code']
    expected_section = diagnostic['section_index']
    if type(expected_section) is not int or not 1 <= expected_section <= 20:
        expected_section = None
    assert error.value.section_index == expected_section
    assert '不要据此改写正文或原样重试' in error.value.repair_action
    assert diagnostic['requested_max_tokens'] == 8192
    assert diagnostic['usage'] == {'prompt_tokens': 123, 'completion_tokens': 456,
                                   'total_tokens': 579, 'reasoning_tokens': 300}
    if failure_kind != 'envelope':
        assert diagnostic['finish_reason'] == finish
        assert diagnostic['message_lengths'] == {key: len(message[key]) for key in
            ('content', 'reasoning_content', 'refusal')}
        assert diagnostic['tool_call_count'] == 1
        if failure_kind != 'length':
            assert diagnostic['tool_arguments_length'] == len(message['tool_calls'][0]['function']['arguments'])
    for secret in (candidate_secret, source_secret, reasoning_secret, credential_secret, source_id,
                   'private-source-title', 'private-issue-detail', 'private-absent-quote',
                   'private-absent-source-quote', 'private-refusal-sentinel', 'private-invalid-json',
                   'https://models.invalid', 'Authorization', content):
        assert secret not in caplog.text


def test_protocol_diagnostic_sanitizes_arbitrary_finish_and_usage_values(caplog):
    import logging

    secret = 'private-provider-metadata-sentinel'
    document = draft()
    calls = []
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return _response(document)
        return httpx.Response(200, json={'choices': [{'finish_reason': secret, 'message': {
            'content': '{}', 'reasoning_content': secret}}], 'usage': {
                'prompt_tokens': secret, 'completion_tokens': True, 'total_tokens': 2**64,
                'completion_tokens_details': {'reasoning_tokens': -1}}})
    caplog.set_level(logging.WARNING, logger='app.topics')
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError):
        generator.generate(inputs())
    record = [record for record in caplog.records if record.name == 'app.topics'][0]
    diagnostic = json.loads(record.getMessage().split(' ', 1)[1])
    assert diagnostic['reason_code'] == 'tool_finish' and diagnostic['finish_reason'] == 'other'
    assert all(count is None for count in diagnostic['usage'].values())
    assert secret not in caplog.text and len(calls) == 2


def test_fragment_payload_losslessly_covers_unicode_fields_and_sources_without_fulltext_duplicates():
    from app.topics import CreationTaskGeneratedScript, CreationTaskGeneratedScriptSection

    content = '  ' + FACT + '\n' + ('中文😀 café e\u0301 words\n' * 60) + '  '
    visual = ' 画面观察\n' + '🙂' * 430
    title = '  原稿标题  '
    source_text = SOURCE + '\n' + ('source 中文👩‍👩‍👧‍👦\n' * 80)
    request_input = replace(inputs(), duration_target_seconds=600, sources=(
        CreationTaskTopicSource(reference_id='private-original-id', kind='knowledge', title='资料', text=source_text),))
    candidate = CreationTaskGeneratedScript(title=title, language='zh-CN', sections=(
        CreationTaskGeneratedScriptSection(title=' 段标题 ', content=content, visual_prompt=visual,
            evidence_reference_ids=('private-original-id',)),))
    seen = []
    def respond(request):
        payload = json.loads(request.content)
        data = json.loads(payload['messages'][1]['content'])
        seen.append(data)
        return _review_tool_response(bound_review(reviewed(data), data))
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        transport=httpx.MockTransport(respond))
    generator.check_draft(request_input, candidate)
    assert len(seen) == 1
    data = seen[0]
    fields = {'title': title, 'content': content, 'visual_prompt': visual}
    all_ids = []
    assert ''.join(item['text'] for item in data['script']['title']) == title
    for field, original in fields.items():
        pieces = data['script']['sections'][0][field] if field != 'title' else data['script']['title']
        assert isinstance(pieces, list) and ''.join(item['text'] for item in pieces) == original
        assert all(0 < len(item['text']) <= 200 for item in pieces)
        all_ids.extend(item['quote_id'] for item in pieces)
    assert ''.join(item['text'] for item in data['script']['sections'][0]['title']) == candidate.sections[0].title
    source_pieces = data['sources'][0]['text']
    # Source preparation retains its existing strip/excerpt rules; chunking
    # itself preserves every character of that prepared source string.
    assert ''.join(item['text'] for item in source_pieces) == source_text.strip()
    assert all(0 < len(item['text']) <= 200 for item in source_pieces)
    all_ids.extend(item['quote_id'] for item in source_pieces)
    assert len(set(all_ids)) == len(all_ids)
    assert 'private-original-id' not in json.dumps(data)
    # The original fields are arrays of fragments, not an additional fulltext property.
    assert set(data['script']) == {'title', 'language', 'sections'}
    assert set(data['sources'][0]) == {'reference_id', 'kind', 'title', 'text'}


@pytest.mark.parametrize('mismatch', ['cross-section', 'unknown-candidate', 'unknown-source',
                                    'uncited-source', 'old-candidate-digest', 'old-source-digest', 'legacy-text'])
def test_review_fragment_ids_are_bound_to_current_field_source_and_section(mismatch):
    document = draft()
    document['script']['sections'].append(copy.deepcopy(document['script']['sections'][0]))
    request_input = replace(inputs(), sources=(*inputs().sources,
        CreationTaskTopicSource(reference_id='other-source', kind='source', title='第二份资料', text=SOURCE)))
    calls = []
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _response(document)
        data = json.loads(body['messages'][1]['content'])
        result = bound_review(reviewed(document), data)
        support = result['sections'][0]['supports'][0]
        if mismatch == 'cross-section':
            support['candidate_quote_id'] = data['script']['sections'][1]['content'][0]['quote_id']
        elif mismatch == 'unknown-candidate':
            support['candidate_quote_id'] = 'unknown-candidate'
        elif mismatch == 'unknown-source':
            support['source_quote_id'] = 'unknown-source'
        elif mismatch == 'uncited-source':
            support['source_quote_id'] = data['sources'][1]['text'][0]['quote_id']
        elif mismatch == 'old-candidate-digest':
            support['candidate_quote_id'] = support['candidate_quote_id'].rsplit('_', 1)[0] + '_000000000000'
        elif mismatch == 'old-source-digest':
            support['source_quote_id'] = support['source_quote_id'].rsplit('_', 1)[0] + '_000000000000'
        else:
            result['sections'][0]['supports'] = [{'field': 'content', 'quote': FACT,
                'reference_id': 'evidence_1', 'source_quote': '用户可为素材设置用途。'}]
        return _review_tool_response(result)
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as error:
        generator.generate(request_input)
    assert error.value.code == 'script_review_invalid' and len(calls) == 2


def test_issue_resolves_original_fragment_and_never_displays_internal_ids():
    document = draft(FACT + '每天自动备份。')
    calls = []
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _response(document)
        data = json.loads(body['messages'][1]['content'])
        result = bound_review(rejected(document, kind='unsupported_fact', field='content',
            quote='每天自动备份', detail='资料不支持此承诺。'), data)
        fragment_id = result['sections'][0]['issues'][0]['candidate_quote_id']
        result['sections'][0]['issues'][0]['missing_or_conflicting'] = fragment_id + '没有依据。c1c_0_000000000000 C1C_0_ABCDEF012345'
        return _review_tool_response(result)
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as error:
        generator.generate(inputs())
    assert error.value.code == 'script_fact_unsupported'
    assert document['script']['sections'][0]['content'] in error.value.reason
    assert 'c1c_' not in error.value.repair_action.casefold()
    assert 'evidence_1' not in error.value.repair_action and len(calls) == 2


def test_global_title_fragment_can_be_referenced_by_each_section():
    document = draft()
    calls = []
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _response(document)
        data = json.loads(body['messages'][1]['content'])
        result = bound_review(reviewed(document), data)
        result['sections'][0]['supports'][0]['candidate_quote_id'] = data['script']['title'][0]['quote_id']
        return _review_tool_response(result)
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        transport=httpx.MockTransport(respond))
    assert generator.generate(inputs()).title == document['script']['title']
    assert len(calls) == 2


@pytest.mark.parametrize('changed', ['candidate', 'source'])
def test_real_previous_fragment_id_is_rejected_after_rest_of_field_changes(changed):
    from app.topics import CreationTaskGeneratedScript, CreationTaskGeneratedScriptSection

    original_content = FACT + '中' * 240
    source_text = SOURCE + '源' * 240
    candidate = CreationTaskGeneratedScript(title='导览', language='zh-CN', sections=(
        CreationTaskGeneratedScriptSection(title='用途', content=original_content, visual_prompt='用途界面',
            evidence_reference_ids=('knowledge:fixture:v1',)),))
    request_input = replace(inputs(), duration_target_seconds=600, sources=(
        replace(inputs().sources[0], text=source_text),))
    requests = []
    previous = []
    def respond(request):
        body = json.loads(request.content)
        data = json.loads(body['messages'][1]['content'])
        requests.append(data)
        result = bound_review(reviewed(draft()), data)
        support = result['sections'][0]['supports'][0]
        key = 'candidate_quote_id' if changed == 'candidate' else 'source_quote_id'
        if previous:
            assert support[key] != previous[0]
            support[key] = previous[0]
        else:
            previous.append(support[key])
        return _review_tool_response(result)
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        transport=httpx.MockTransport(respond))
    generator.check_draft(request_input, candidate)
    if changed == 'candidate':
        revised = replace(candidate, sections=(replace(candidate.sections[0], content=original_content + '改'),))
        with pytest.raises(CreationTaskScriptGenerationError) as error:
            generator.check_draft(request_input, revised)
        assert requests[0]['script']['sections'][0]['content'][0]['text'] == requests[1]['script']['sections'][0]['content'][0]['text']
    else:
        revised_input = replace(request_input, sources=(replace(request_input.sources[0], text=source_text + '改'),))
        with pytest.raises(CreationTaskScriptGenerationError) as error:
            generator.check_draft(revised_input, candidate)
        assert requests[0]['sources'][0]['text'][0]['text'] == requests[1]['sources'][0]['text'][0]['text']
    assert error.value.code == 'script_review_invalid' and len(requests) == 2


def test_legacy_issue_text_protocol_is_rejected_even_when_quote_is_exact():
    document = draft(FACT + '每天自动备份。')
    calls = []
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return _response(document)
        return _review_tool_response({'sections': [{'index': 1, 'grounded': False, 'constraints_met': True,
            'supports': [], 'issues': [{'kind': 'unsupported_fact', 'field': 'content',
                'quote': '每天自动备份', 'reference_ids': ['evidence_1'],
                'missing_or_conflicting': '资料没有备份说明。'}]}]})
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as error:
        generator.generate(inputs())
    assert error.value.code == 'script_review_invalid' and len(calls) == 2


@pytest.mark.parametrize('invalid,reason', [
    ('missing', 'tool_call_count'), ('multiple', 'tool_call_count'),
    ('wrong-type', 'tool_type'), ('wrong-name', 'tool_name'),
    ('bad-args', 'tool_arguments'), ('fenced-args', 'tool_arguments'),
    ('trailing-args', 'tool_arguments'), ('not-object', 'tool_arguments_shape'),
    ('length', 'length'), ('old-content', 'tool_finish'),
])
def test_only_one_complete_report_tool_is_accepted_without_execution_or_fallback(caplog, invalid, reason):
    import logging

    calls = []
    sentinel = 'private-tool-envelope-sentinel'
    document = draft()
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _response(document)
        data = json.loads(body['messages'][1]['content'])
        report = bound_review(reviewed(document), data)
        envelope = _review_tool_response(report).json()
        choice, message = envelope['choices'][0], envelope['choices'][0]['message']
        # A valid old content report cannot rescue an invalid tool envelope.
        message['content'] = json.dumps(report)
        tool = message['tool_calls'][0]
        tool['id'] = sentinel
        if invalid == 'missing':
            message.pop('tool_calls')
        elif invalid == 'multiple':
            message['tool_calls'].append(copy.deepcopy(tool))
        elif invalid == 'wrong-type':
            tool['type'] = sentinel
        elif invalid == 'wrong-name':
            tool['function']['name'] = sentinel
        elif invalid == 'bad-args':
            tool['function']['arguments'] = sentinel
        elif invalid == 'fenced-args':
            tool['function']['arguments'] = '```json\n' + tool['function']['arguments'] + '\n```'
        elif invalid == 'trailing-args':
            tool['function']['arguments'] += ' ' + sentinel
        elif invalid == 'not-object':
            tool['function']['arguments'] = '[]'
        elif invalid == 'length':
            choice['finish_reason'] = 'length'
        else:
            choice['finish_reason'] = 'stop'
            message.pop('tool_calls')
        return httpx.Response(200, json=envelope)
    caplog.set_level(logging.WARNING, logger='app.topics')
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as error:
        generator.generate(inputs())
    assert error.value.code == 'script_review_invalid' and not error.value.retryable
    assert len(calls) == 2  # No tool execution/result message or repeated provider request.
    records = [record for record in caplog.records if record.name == 'app.topics']
    assert len(records) == 1
    diagnostic = json.loads(records[0].getMessage().split(' ', 1)[1])
    assert diagnostic['reason_code'] == reason
    assert sentinel not in caplog.text
    assert document['script']['sections'][0]['content'] not in caplog.text


@pytest.mark.parametrize("model", ["MiniMax-M3", "deepseek-flash"])
@pytest.mark.parametrize("outcome", ["supported", "unsupported", "invalid-report"])
def test_disabled_thinking_keeps_fact_and_complete_report_gate(outcome, model):
    document = draft(FACT + ("每天自动备份。" if outcome == "unsupported" else ""))
    calls = []
    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["thinking"] == {"type": "disabled"}
        assert payload.get("reasoning_split") is (True if model == "MiniMax-M3" else None)
        assert payload["tool_choice"] == "required"
        function = payload["tools"][0]["function"]
        assert function["name"] == ("submit_script_draft" if len(calls) == 1 else "submit_script_review")
        assert function["parameters"]["type"] == "object"
        if len(calls) == 1:
            return _response(document)
        data = json.loads(payload["messages"][1]["content"])
        if outcome == "invalid-report":
            return _review_tool_response({"sections": []})
        report = rejected(document, kind="unsupported_fact", field="content", quote="每天自动备份", detail="资料没有备份说明。") if outcome == "unsupported" else reviewed(document)
        return _review_tool_response(bound_review(report, data))
    generator = OpenAICompatibleCreationTaskScriptGenerator("https://models.invalid/v1", model,
        transport=httpx.MockTransport(respond))
    if outcome == "supported":
        assert generator.generate(inputs()).sections[0].content == FACT
    else:
        with pytest.raises(CreationTaskScriptGenerationError) as error:
            generator.generate(inputs())
        assert error.value.code == ("script_fact_unsupported" if outcome == "unsupported" else "script_review_invalid")
    assert len(calls) == 2


def test_each_review_section_receives_only_its_declared_source_quote_id_mapping():
    source = replace(inputs(), sources=(*inputs().sources, CreationTaskTopicSource(
        reference_id='second-fixed-source', kind='knowledge', title='第二份资料', text=FACT + '补充操作说明。')))
    document = draft()
    document['script']['sections'].append({**copy.deepcopy(document['script']['sections'][0]),
        'evidence_reference_ids': ['evidence_2']})
    review = reviewed(document)
    review['sections'][1]['supports'][0]['source_quote_id'] = '@evidence_2:0'
    calls = []
    assert len(provider(document, review, calls).generate(source).sections) == 2
    supplied = json.loads(calls[1][1]['messages'][1]['content'])
    ids = {item['reference_id']: [piece['quote_id'] for piece in item['text']] for item in supplied['sources']}
    assert supplied['script']['sections'][0]['allowed_source_quote_ids'] == {'evidence_1': ids['evidence_1']}
    assert supplied['script']['sections'][1]['allowed_source_quote_ids'] == {'evidence_2': ids['evidence_2']}
    assert ''.join(piece['text'] for piece in supplied['sources'][0]['text']) == source.sources[0].text
    assert len(calls) == 2


def test_fifth_section_wrong_source_still_rejects_and_reports_only_bounded_ordinals(caplog):
    import logging

    private_text = 'private-source-body-sentinel'
    source = replace(inputs(), sources=(*inputs().sources, CreationTaskTopicSource(
        reference_id='private-source-id-sentinel', kind='knowledge', title='private-source-title-sentinel',
        text=FACT + private_text)))
    document = draft()
    document['script']['sections'] *= 5
    review = reviewed(document)
    review['sections'][4]['supports'][0]['source_quote_id'] = '@evidence_2:0'
    calls = []
    caplog.set_level(logging.WARNING, logger='app.topics')
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        provider(document, review, calls).generate(source)
    assert failure.value.code == 'script_review_invalid' and not failure.value.retryable
    records = [item for item in caplog.records if item.name == 'app.topics']
    assert len(records) == 1
    diagnostic = json.loads(records[0].getMessage().split(' ', 1)[1])
    assert diagnostic['reason_code'] == 'support_reference' and diagnostic['section_index'] == 5
    assert diagnostic['section_count'] == 5 and diagnostic['source_count'] == 2
    assert diagnostic['support_reference_mismatch'] == {
        'support_index': 1, 'selected_source_ordinal': 2, 'allowed_source_ordinals': [1]}
    for secret in (private_text, 'private-source-id-sentinel', 'private-source-title-sentinel', FACT, 'isolated-key'):
        assert secret not in caplog.text
    assert len(records[0].getMessage()) < 1200 and len(calls) == 2


@pytest.mark.parametrize('refs,subtype,returned_ordinals,form_counts', [
    (None, 'not_array', [], {}),
    ({'private-unknown-reference': 'private-value'}, 'not_array', [], {}),
    (['evidence_1'] * 13, 'too_many', [1] * 12, {'alias': 12}),
    ([1, {'private-key': 'private-value'}], 'non_string', [], {'non_string': 2}),
    (['private-unknown-reference'], 'unknown_alias', [], {'other': 1}),
    (['evidence_2'], 'not_in_section', [2], {'alias': 1}),
    (['evidence_1', 'evidence_1'], 'duplicate', [1, 1], {'alias': 2}),
    ([], 'empty_unsupported', [], {}),
    (['@source_quote'], 'unknown_alias', [], {'source_quote_id': 1}),
    (['@candidate_quote'], 'unknown_alias', [], {'candidate_quote_id': 1}),
    (['private-original-source-id'], 'unknown_alias', [], {'original_reference_id': 1}),
])
def test_issue_reference_rejection_reports_safe_subtype_without_fixing_model_reference(
    caplog, refs, subtype, returned_ordinals, form_counts,
):
    import logging

    source = replace(inputs(), sources=(
        CreationTaskTopicSource(reference_id='private-original-source-id', kind='knowledge',
            title='private-source-title', text=SOURCE + 'private-source-body'),
        CreationTaskTopicSource(reference_id='private-second-source-id', kind='knowledge',
            title='private-second-title', text='另一份资料。private-second-body'),
    ))
    document = draft()
    calls = []
    forbidden_ids = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _response(document)
        data = json.loads(body['messages'][1]['content'])
        schema = body['tools'][0]['function']['parameters']
        reference_schema = schema['properties']['sections']['items']['anyOf'][1]['properties']['issues']['items']['anyOf'][0]['properties']['reference_ids']
        assert reference_schema['items']['enum'] == ['evidence_1']
        assert reference_schema['minItems'] == 1
        assert reference_schema['uniqueItems'] is True
        assert data['script']['sections'][0]['evidence_reference_ids'] == ['evidence_1']
        review = bound_review(rejected(document, kind='unsupported_fact', field='content', quote=FACT,
            detail='private-issue-detail'), data)
        source_quote_id = data['sources'][0]['text'][0]['quote_id']
        candidate_quote_id = data['script']['sections'][0]['content'][0]['quote_id']
        forbidden_ids.extend((source_quote_id, candidate_quote_id))
        assigned = copy.deepcopy(refs)
        if assigned == ['@source_quote']:
            assigned = [source_quote_id]
        elif assigned == ['@candidate_quote']:
            assigned = [candidate_quote_id]
        review['sections'][0]['issues'][0]['reference_ids'] = assigned
        return _review_tool_response(review)

    caplog.set_level(logging.WARNING, logger='app.topics')
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        api_key='private-credential', transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        generator.generate(source)
    assert failure.value.code == 'script_review_invalid' and failure.value.reason_code == 'issue_reference'
    assert failure.value.retryable is False and len(calls) == 2
    records = [record for record in caplog.records if record.name == 'app.topics']
    assert len(records) == 1
    text = records[0].getMessage()
    diagnostic = json.loads(text.removeprefix('creation_script_review_invalid '))
    assert diagnostic['reason_code'] == 'issue_reference' and diagnostic['section_index'] == 1
    mismatch = diagnostic['issue_reference_mismatch']
    assert mismatch['subtype'] == subtype and mismatch['issue_index'] == 1
    assert mismatch['kind'] == 'unsupported_fact' and mismatch['candidate_field'] == 'content'
    assert mismatch['candidate_section_index'] == 1
    assert mismatch['reference_count'] == (len(refs) if isinstance(refs, list) else None)
    assert mismatch['allowed_source_ordinals'] == [1]
    assert mismatch['returned_source_ordinals'] == returned_ordinals
    assert {kind: count for kind, count in mismatch['sampled_reference_forms'].items() if count} == form_counts
    assert len(text) < 1200
    for secret in (*forbidden_ids, 'evidence_1', 'evidence_2', 'private-', SOURCE, FACT, 'https://models.invalid', 'Authorization'):
        assert secret not in text


@pytest.mark.parametrize('kind,refs,expected_code', [
    ('unsupported_fact', ['evidence_1'], 'script_fact_unsupported'),
    ('goal_conflict', ['evidence_1'], 'script_goal_conflict'),
])
def test_valid_issue_references_still_reject_unfounded_content_or_goal_conflict(kind, refs, expected_code):
    document = draft()
    review = rejected(document, kind=kind, field='content', quote=FACT, detail='资料或本次要求不支持该表述。')
    review['sections'][0]['issues'][0]['reference_ids'] = refs
    calls = []
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        provider(document, review, calls).generate(inputs())
    assert failure.value.code == expected_code and failure.value.retryable is False
    assert len(calls) == 2


@pytest.mark.parametrize('field,target_section,text,fragment_index', [
    ('title', 0, '  总标题😀  ', 0),
    ('title', 1, '段落标题😀', 0),
    ('content', 1, '😀' * 200 + FACT, 1),
    ('visual_prompt', 1, '😀' * 401, 2),
])
def test_content_rejection_locator_is_derived_from_the_verified_candidate_fragment(field, target_section, text, fragment_index):
    import hashlib
    from app.topics import CreationTaskGeneratedScript, CreationTaskGeneratedScriptSection

    section_fields = {'title': '段落', 'content': FACT, 'visual_prompt': '展示素材用途'}
    title = '总标题'
    if target_section == 0:
        title = text
    else:
        section_fields[field] = text
    candidate = CreationTaskGeneratedScript(title=title, language='zh-CN', sections=(
        CreationTaskGeneratedScriptSection(**section_fields, evidence_reference_ids=('knowledge:fixture:v1',)),))
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        data = json.loads(body['messages'][1]['content'])
        fragments = data['script']['title'] if target_section == 0 else data['script']['sections'][0][field]
        assert ''.join(piece['text'] for piece in fragments) == text
        assert 0 < len(fragments[fragment_index]['text']) <= 200
        return _review_tool_response({'sections': [{'index': 1, 'issues': [{
            'kind': 'unsupported_fact', 'candidate_quote_id': fragments[fragment_index]['quote_id'],
            'reference_ids': ['evidence_1'], 'missing_or_conflicting': 'private-model-explanation'}]}]})

    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        generator.check_draft(inputs(), candidate)
    error = failure.value
    assert error.code == 'script_fact_unsupported' and error.section_index == 1
    assert error.candidate_section_index == target_section and error.candidate_field == field
    assert error.candidate_fragment_index == fragment_index
    assert error.candidate_field_digest == hashlib.sha256(text.encode('utf-8')).hexdigest()
    assert len(calls) == 1


def test_complete_review_returns_every_verified_finding_without_changing_first_item():
    import hashlib

    document = draft()
    document['script']['sections'].append(copy.deepcopy(document['script']['sections'][0]))
    review = reviewed(document)
    review['sections'][0]['issues'] = [
        {'kind': 'unsupported_fact', 'candidate_quote_id': '@script_title:0', 'reference_ids': ['evidence_1'],
            'missing_or_conflicting': '总标题承诺没有资料依据。'},
        {'kind': 'goal_conflict', 'candidate_quote_id': '@content:0',
            'missing_or_conflicting': '旁白与本轮要求冲突。'},
    ]
    review['sections'][1]['issues'] = [{'kind': 'unsupported_fact', 'candidate_quote_id': '@visual_prompt:0',
        'reference_ids': ['evidence_1'], 'missing_or_conflicting': '画面承诺没有资料依据。 isolated-key'}]
    calls = []
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        provider(document, review, calls).generate(inputs())
    error = failure.value
    assert error.code == 'script_fact_unsupported' and error.section_index == 1
    assert error.candidate_section_index == 0 and error.candidate_field == 'title'
    assert len(error.review_issues) == 3 and len(calls) == 2
    assert [(item['section_index'], item['candidate_section_index'], item['candidate_field'], item['error'])
        for item in error.review_issues] == [(1, 0, 'title', 'script_fact_unsupported'),
            (1, 1, 'content', 'script_goal_conflict'), (2, 2, 'visual_prompt', 'script_fact_unsupported')]
    texts = [document['script']['title'], document['script']['sections'][0]['content'], document['script']['sections'][1]['visual_prompt']]
    for item, text in zip(error.review_issues, texts):
        assert item['candidate_field_digest'] == hashlib.sha256(text.encode('utf-8')).hexdigest()
        assert item['candidate_fragment_index'] == 0 and len(item['review_explanation']) <= 300
        assert set(item) == {'error', 'reason_code', 'section_index', 'candidate_section_index', 'candidate_field',
            'candidate_fragment_index', 'candidate_field_digest', 'review_explanation'}
    assert error.review_issues[1]['review_explanation'] == '旁白与本轮要求冲突。'
    assert 'isolated-key' not in json.dumps(error.review_issues, ensure_ascii=False)


def test_all_twelve_findings_are_returned_with_the_existing_report_limit():
    document = draft()
    document['script']['sections'] *= 4
    review = reviewed(document)
    for section in review['sections']:
        section['issues'] = [{'kind': 'unsupported_fact', 'candidate_quote_id': '@' + field + ':0',
            'reference_ids': ['evidence_1'], 'missing_or_conflicting': '该字段承诺缺少资料支持。'}
            for field in ('title', 'content', 'visual_prompt')]
    calls = []
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        provider(document, review, calls).generate(inputs())
    assert failure.value.code == 'script_fact_unsupported' and len(failure.value.review_issues) == 12
    assert len(calls) == 2


@pytest.mark.parametrize('change,subtype,missing_keys,unknown_count', [
    ('issues_not_array', 'not_array', None, None),
    ('issues_too_many', 'too_many', None, None),
    ('not_object', 'not_object', [], None),
    ('missing', 'missing_keys', ['reference_ids'], 0),
    ('extra', 'extra_keys', [], 1),
    ('missing_and_extra', 'missing_keys', ['reference_ids'], 1),
    ('kind_type', 'kind_not_string', None, None),
    ('kind_value', 'kind_unknown', None, None),
    ('detail_type', 'detail_not_string', None, None),
    ('detail_empty', 'detail_empty', None, None),
    ('detail_long', 'detail_too_long', None, None),
])
def test_issue_shape_diagnostic_reports_only_fixed_subtype_and_bounded_structure(caplog, change, subtype, missing_keys, unknown_count):
    import logging

    document = draft()
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _response(document)
        data = json.loads(body['messages'][1]['content'])
        review = bound_review(rejected(document, kind='unsupported_fact', field='content', quote=FACT,
            detail='private-original-detail'), data)
        issue = review['sections'][0]['issues'][0]
        if change == 'issues_not_array':
            review['sections'][0]['issues'] = {'private-unknown-key': 'private-unknown-value'}
        elif change == 'issues_too_many':
            review['sections'][0]['issues'] = [copy.deepcopy(issue) for _ in range(4)]
        elif change == 'not_object':
            review['sections'][0]['issues'][0] = 'private-not-an-object'
        elif change in {'missing', 'missing_and_extra'}:
            del issue['reference_ids']
        if change in {'extra', 'missing_and_extra'}:
            issue['private-unknown-key'] = 'private-unknown-value'
        elif change == 'kind_type':
            issue['kind'] = {'private-key': 'private-value'}
        elif change == 'kind_value':
            issue['kind'] = 'private-unknown-kind'
        elif change == 'detail_type':
            issue['missing_or_conflicting'] = {'private-key': 'private-value'}
        elif change == 'detail_empty':
            issue['missing_or_conflicting'] = '  \n '
        elif change == 'detail_long':
            issue['missing_or_conflicting'] = 'private-long-detail' * 30
        return _review_tool_response(review)

    caplog.set_level(logging.WARNING, logger='app.topics')
    generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
        api_key='private-key-value', transport=httpx.MockTransport(respond))
    with pytest.raises(CreationTaskScriptGenerationError) as failure:
        generator.generate(inputs())
    assert failure.value.code == 'script_review_invalid' and failure.value.reason_code == 'issue_shape'
    assert failure.value.candidate_field is None and failure.value.candidate_field_digest is None
    assert failure.value.review_issues is None
    assert failure.value.retryable is False and len(calls) == 2
    records = [record for record in caplog.records if record.name == 'app.topics']
    assert len(records) == 1
    text = records[0].getMessage()
    diagnostic = json.loads(text.removeprefix('creation_script_review_invalid '))
    shape = diagnostic['issue_shape_mismatch']
    assert shape['subtype'] == subtype
    if change in {'issues_not_array', 'issues_too_many'}:
        assert shape == {'subtype': subtype,
            'issues_type': 'object' if change == 'issues_not_array' else 'array',
            'issue_count': None if change == 'issues_not_array' else 4}
    elif missing_keys is not None:
        assert shape['issue_index'] == 1
        assert shape['missing_keys'] == missing_keys and shape['unknown_key_count'] == unknown_count
    else:
        assert shape['issue_index'] == 1
        assert type(shape['detail_length']) is int or shape['detail_length'] is None
    assert 'private-' not in text and FACT not in text and len(text) < 1200


@pytest.mark.parametrize('language', ['zh-CN', 'en-US'])
@pytest.mark.parametrize('template_count', [None, 2])
def test_generation_section_prompt_and_requirements_follow_actual_schema(language, template_count):
    document = draft() if language == 'zh-CN' else draft(content='Set material use.')
    document['script']['language'] = language
    if template_count:
        document['script']['sections'].append(copy.deepcopy(document['script']['sections'][0]))
    calls = []
    result = provider(document, reviewed(document), calls).generate(replace(inputs(),
        language=language, script_section_count=template_count))
    payload = calls[0][1]
    sections = payload['tools'][0]['function']['parameters']['properties']['script']['properties']['sections']
    minimum, maximum = sections['minItems'], sections['maxItems']
    system = payload['messages'][0]['content']
    requirements = json.loads(payload['messages'][1]['content'])['requirements']
    assert f"本次工具schema允许{minimum}–{maximum}段正文" in system
    assert requirements['section_count'] == (template_count or f'{minimum}-{maximum}')
    assert minimum <= len(result.sections) <= maximum
    if template_count:
        assert len(result.sections) == template_count


@pytest.mark.parametrize('language', ['zh-CN', 'en-US'])
def test_estimated_speech_above_delivery_target_still_receives_full_source_review(language):
    from app.speech_timing import estimate_speech_seconds
    content = FACT * 8 if language == 'zh-CN' else ('Set material use. ' * 30).strip()
    assert estimate_speech_seconds(content) > 15
    document = draft(content=content)
    document['script']['language'] = language
    calls = []
    result = provider(document, reviewed(document), calls).generate(replace(inputs(), language=language, duration_target_seconds=15))
    assert result.sections[0].content == content
    assert len(calls) == 2
    assert '文字估算仅用于规划' in calls[0][1]['messages'][0]['content']


@pytest.mark.parametrize('endpoint', ['scripts/generate', 'assistant/script-draft'])
@pytest.mark.parametrize('template_count', [None, 2])
def test_mixed_presentation_uses_only_explicit_template_section_count(api_state, monkeypatch, endpoint, template_count):
    state = api_state
    with state.repository._connect() as conn:
        row = conn.execute("SELECT payload FROM creation_plans WHERE id='plan'").fetchone()
        plan = json.loads(row['payload'])
        plan['presenter_share_target'] = 40
        plan['duration_target_seconds'] = 15
        plan['template_settings'] = {'script_section_count': template_count} if template_count else {}
        conn.execute("UPDATE creation_plans SET payload=? WHERE id='plan'", (json.dumps(plan),))
    context = state.client.get('/api/creation-tasks/task/assistant/script-context')
    assert context.status_code == 200, context.text
    assert context.json()['script_section_count'] == template_count
    document = draft()
    document['script']['sections'] *= 6
    from app.speech_timing import estimate_speech_seconds
    assert estimate_speech_seconds(''.join(section['content'] for section in document['script']['sections'])) > 15
    calls = []
    set_api_provider(monkeypatch, document, reviewed(document), calls)
    if endpoint == 'scripts/generate':
        body = {}
    else:
        body = agent_payload(state)
        original = body['sections'][0]
        body['sections'] = [{**original, 'id': f'section-{index}'} for index in range(6)]
        # Saving an Agent draft calls review directly, without a generation request.
        calls.clear()
        def review_only(request):
            payload = json.loads(request.content)
            calls.append((request, payload))
            return _review_tool_response(bound_review(reviewed(document), json.loads(payload['messages'][1]['content'])))
        generator = OpenAICompatibleCreationTaskScriptGenerator('https://models.invalid/v1', 'same-model',
            transport=httpx.MockTransport(review_only))
        monkeypatch.setattr(main, '_creation_task_script_generator_for', lambda settings: generator)
    response = state.client.post('/api/creation-tasks/task/' + endpoint, json=body)
    if template_count:
        assert response.status_code in (422, 502), response.text
        assert '模板要求 2 段' in response.text
        assert state.repository.list_creation_task_scripts('local', 'task') == []
    else:
        assert response.status_code == 200, response.text
        scripts = state.repository.list_creation_task_scripts('local', 'task')
        assert len(scripts) == 1 and len(scripts[0].sections) == 6
        review_input = json.loads(calls[-1][1]['messages'][1]['content'])
        assert len(review_input['script']['sections']) == 6
        assert review_input['requirements']['presenter_share_target'] == 40


def test_confirm_does_not_treat_estimated_speech_as_real_delivery_duration(api_state, monkeypatch):
    state = api_state
    state.client.app.add_api_route('/api/creation-tasks/{task_id}/scripts/{script_id}/confirm',
        main.confirm_creation_task_script, methods=['POST'])
    content = FACT * 8
    with state.repository._connect() as conn:
        plan = json.loads(conn.execute("SELECT payload FROM creation_plans WHERE id='plan'").fetchone()['payload'])
        plan['duration_target_seconds'] = 15
        conn.execute("UPDATE creation_plans SET payload=? WHERE id='plan'", (json.dumps(plan),))
    document = draft(content)
    calls = []
    set_api_provider(monkeypatch, document, reviewed(document), calls)
    generated = state.client.post('/api/creation-tasks/task/scripts/generate', json={})
    assert generated.status_code == 200, generated.text
    script = state.repository.list_creation_task_scripts('local', 'task')[0]
    body = {'title': script.title, 'sections': [vars(section) for section in script.sections],
        'expected_updated_at': script.updated_at.isoformat()}
    url = f'/api/creation-tasks/task/scripts/{script.id}/confirm'
    rejected_body = {**body, 'expected_updated_at': '2000-01-01T00:00:00+00:00'}
    stale = state.client.post(url, json=rejected_body)
    assert stale.status_code == 409, stale.text
    assert state.repository.get_creation_task('local', 'task').confirmed_script_id is None
    confirmed = state.client.post(url, json=body)
    assert confirmed.status_code == 200, confirmed.text
    persisted = state.repository.get_creation_task_script('local', script.id)
    assert persisted.selection_status == 'confirmed' and persisted.sections[0].content == content
    assert state.repository.get_creation_task('local', 'task').confirmed_script_id == script.id
    assert len(calls) == 2
