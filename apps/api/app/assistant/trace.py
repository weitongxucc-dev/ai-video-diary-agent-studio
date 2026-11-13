from __future__ import annotations

import hashlib


REVIEWER_KINDS = ('evidence_scout', 'script_reviewer', 'visual_reviewer', 'audio_reviewer')
_REFERENCE_KINDS = {
    'conversation_id': 'conversation', 'turn_id': 'turn', 'tool_call_id': 'tool_call',
    'request_id': 'business_request', 'failed_request_id': 'business_request',
    'job_id': 'job', 'execution_job_id': 'job', 'output_id': 'output',
    'output_reference': 'output', 'artifact_id': 'output', 'operation_id': 'operation',
    'task_id': 'task', 'input_id': 'input', 'input_turn_id': 'turn',
    'assistant_turn_id': 'turn',
}


def _opaque(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]


def _references(event: dict) -> list[dict]:
    values = {}
    for key, kind in _REFERENCE_KINDS.items():
        raw = event.get(key)
        if raw is None and isinstance(event.get('trace_references'), dict):
            raw = event['trace_references'].get(key)
        ref = _opaque(raw)
        if ref:
            values[(kind, ref)] = {'kind': kind, 'ref': ref}
    return list(values.values())


def _nested_references(value: object, *, depth: int = 0) -> list[dict]:
    if depth > 5 or not isinstance(value, dict):
        return []
    refs = []
    for key, item in value.items():
        kind = _REFERENCE_KINDS.get(key)
        ref = _opaque(item) if kind else None
        if ref:
            refs.append({'kind': kind, 'ref': ref})
        elif isinstance(item, dict):
            refs.extend(_nested_references(item, depth=depth + 1))
        elif isinstance(item, list):
            for child in item[:16]:
                refs.extend(_nested_references(child, depth=depth + 1))
    return refs


def project_evidence_candidates(turn: dict) -> list[dict]:
    """Project future reviewer candidates without creating a reviewer runtime."""
    candidates = []
    for event in turn.get('events') or []:
        if not isinstance(event, dict) or event.get('type') != 'evidence_candidate':
            continue
        kind = event.get('reviewer_kind')
        candidate_id = _opaque(event.get('candidate_id'))
        status = event.get('status')
        if kind not in REVIEWER_KINDS or not candidate_id or status not in {'pending', 'accepted', 'rejected', 'unknown'}:
            continue
        source_refs = event.get('source_refs') if isinstance(event.get('source_refs'), list) else []
        candidates.append({'reviewer_kind': kind, 'candidate_ref': candidate_id, 'status': status,
                           'source_refs': [_opaque(value) for value in source_refs
                                           if _opaque(value)]})
    return candidates


def project_turn_trace(turn: dict) -> dict:
    """Project one persisted turn into a redacted diagnostic trace."""
    events = turn.get('events') or []
    projected = []
    tool_counts = {}
    provider_usage = {}
    usage_source = None
    compactions = 0
    associations = [{'kind': 'conversation', 'ref': _opaque(turn.get('conversation_id'))},
                    {'kind': 'turn', 'ref': _opaque(turn.get('id'))}]
    associations = [item for item in associations if item['ref']]
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get('type')
        if event_type == 'context_compaction':
            compactions += 1
            for key in ('input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens'):
                value = (event.get('provider_usage') or {}).get(key)
                if type(value) is int and value >= 0:
                    provider_usage[key] = provider_usage.get(key, 0) + value
                    usage_source = usage_source or 'compaction'
            projected.append({
                'type': event_type,
                'status': event.get('status'),
                'trigger': event.get('trigger'),
                'failure_origin': event.get('failure_origin'),
            })
            continue
        if event_type not in {'tool_start', 'tool_end'}:
            continue
        label = event.get('label') if isinstance(event.get('label'), str) else '助手操作'
        item = {'type': event_type, 'tool_id': event.get('tool_id'), 'label': label}
        refs = _references(event)
        if refs:
            item['associations'] = refs
            associations.extend(refs)
        if event.get('tool_id') is not None:
            item['tool_call_ref'] = _opaque(f"{turn.get('id')}:{event.get('tool_id')}")
        if event_type == 'tool_end':
            item['ok'] = bool(event.get('ok'))
            failure = event.get('failure')
            if isinstance(failure, dict):
                item['failure'] = {key: failure[key] for key in ('error', 'code', 'stage', 'submission_effect') if key in failure}
            tool_counts[label] = tool_counts.get(label, 0) + 1
        projected.append(item)
    result = turn.get('result') if isinstance(turn.get('result'), dict) else {}
    associations.extend(_nested_references(result))
    result_usage = result.get('provider_usage') if isinstance(result.get('provider_usage'), dict) else None
    if result.get('usage_status') == 'known' and result_usage:
        provider_usage = {key: value for key, value in result_usage.items()
                          if key in {'input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens'}
                          and type(value) is int and value >= 0}
        usage_source = 'provider'
    deduped = list({(item['kind'], item['ref']): item for item in associations}.values())
    business = result.get('continuation') if isinstance(result.get('continuation'), dict) else {}
    business_status = business.get('status') if isinstance(business.get('status'), str) else None
    return {
        'turn_id': turn['id'],
        'conversation_id': turn['conversation_id'],
        'status': turn['status'],
        'sequence': turn['sequence'],
        'created_at': turn['created_at'],
        'updated_at': turn['updated_at'],
        'events': projected[-200:],
        'event_count': len(projected),
        'tool_counts': tool_counts,
        'compaction_count': compactions,
        'provider_usage': provider_usage or None,
        'usage_status': 'known' if provider_usage else 'unknown',
        'usage_source': usage_source,
        'associations': deduped,
        'business_status': business_status,
        'evidence_candidates': project_evidence_candidates(turn),
    }
