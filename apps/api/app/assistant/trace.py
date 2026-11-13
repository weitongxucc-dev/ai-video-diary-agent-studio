from __future__ import annotations

import hashlib
from datetime import datetime, timezone


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


def _iso_time(value: object) -> str | None:
    if not isinstance(value, (int, float)) or value < 0:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def _duration_between(start: object, end: object) -> int | None:
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        delta = datetime.fromisoformat(end.replace('Z', '+00:00')) - datetime.fromisoformat(start.replace('Z', '+00:00'))
    except ValueError:
        return None
    return max(0, int(delta.total_seconds() * 1000))


def _project_runtime_events(events: list[dict], turn_id: str) -> tuple[list[dict], str | None, str | None, int | None, str | None, int, list[dict]]:
    projected, opened, associations = [], {}, []
    started_ms = ended_ms = None
    failure_class = None
    retries = 0
    for source in events:
        if not isinstance(source, dict) or not isinstance(source.get('type'), str):
            continue
        event_type = source['type']
        at_ms = source.get('at_ms')
        if isinstance(at_ms, (int, float)):
            started_ms = at_ms if started_ms is None else min(started_ms, at_ms)
            ended_ms = at_ms if ended_ms is None else max(ended_ms, at_ms)
        refs = _nested_references(source)
        associations.extend(refs)
        item = {'step': source.get('step'), 'type': event_type, 'at': _iso_time(at_ms)}
        if isinstance(source.get('event_id'), str):
            item['event_id'] = _opaque(source['event_id'])
        if isinstance(source.get('parent_event_id'), str):
            item['parent_event_id'] = _opaque(source['parent_event_id'])
        if source.get('tool_call_id') is not None:
            item['tool_call_ref'] = _opaque(str(source['tool_call_id']))
        if source.get('tool_name') is not None and isinstance(source.get('tool_name'), str):
            item['tool_name'] = source['tool_name']
        status = source.get('status')
        if isinstance(status, str):
            item['status'] = status
        if isinstance(source.get('failure_class'), str):
            item['failure_class'] = source['failure_class']
            failure_class = failure_class or source['failure_class']
        if refs:
            item['associations'] = refs
        if event_type == 'retry':
            retries += 1
        correlation = ('tool' if event_type in {'tool_start', 'tool_end'} else 'compaction',
                       str(source.get('tool_call_id') or ''))
        if event_type in {'tool_start', 'compaction_start'}:
            opened[correlation] = at_ms
        elif event_type in {'tool_end', 'compaction_end'}:
            begin = opened.pop(correlation, None)
            if isinstance(begin, (int, float)) and isinstance(at_ms, (int, float)):
                item['duration_ms'] = max(0, int(at_ms - begin))
        projected.append(item)
    duration = int(ended_ms - started_ms) if isinstance(started_ms, (int, float)) and isinstance(ended_ms, (int, float)) else None
    deduped = list({(item['kind'], item['ref']): item for item in associations}.values())
    # Keep the complete projected stream here.  Pagination is applied by
    # ``project_turn_trace`` after usage, association, and duration metadata
    # have been calculated; slicing at this layer would make events older
    # than the first 200 permanently unreachable.
    return projected, _iso_time(started_ms), _iso_time(ended_ms), duration, failure_class, retries, deduped


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


def project_turn_trace(turn: dict, *, before: int | None = None, limit: int = 200) -> dict:
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
    runtime_events = result.get('trace_events') if isinstance(result.get('trace_events'), list) else []
    runtime_projected, started_at, ended_at, runtime_duration, runtime_failure, retries, runtime_associations = _project_runtime_events(runtime_events, turn.get('id'))
    if runtime_projected:
        projected = runtime_projected
    else:
        started_at, ended_at = turn.get('created_at'), turn.get('updated_at')
        runtime_duration = _duration_between(started_at, ended_at)
    associations.extend(runtime_associations)
    associations.extend(_nested_references(result))
    result_usage = result.get('provider_usage') if isinstance(result.get('provider_usage'), dict) else None
    requested_usage_status = result.get('usage_status')
    if requested_usage_status in {'known', 'estimated'} and result_usage:
        provider_usage = {key: value for key, value in result_usage.items()
                          if key in {'input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens'}
                          and type(value) is int and value >= 0}
        usage_source = 'provider' if requested_usage_status == 'known' else 'local_estimate'
    deduped = list({(item['kind'], item['ref']): item for item in associations}.values())
    business = result.get('continuation') if isinstance(result.get('continuation'), dict) else {}
    business_status = business.get('status') if isinstance(business.get('status'), str) else None
    usage_status = requested_usage_status if requested_usage_status in {'known', 'estimated'} and provider_usage else 'known' if provider_usage else 'unknown'
    total_event_count = len(projected)
    end = total_event_count if before is None else min(max(before, 0), total_event_count)
    start = max(0, end - limit)
    visible_events = projected[start:end]
    return {
        'turn_id': turn['id'],
        'conversation_id': turn['conversation_id'],
        'session_id': result.get('session_id') if isinstance(result.get('session_id'), str) else None,
        'status': turn['status'],
        'sequence': turn['sequence'],
        'created_at': turn['created_at'],
        'updated_at': turn['updated_at'],
        'events': visible_events,
        'event_count': total_event_count,
        'events_returned': len(visible_events),
        'next_before': start if start > 0 else None,
        'tool_counts': tool_counts,
        'compaction_count': compactions,
        'provider_usage': provider_usage or None,
        'usage_status': usage_status,
        'usage_source': usage_source,
        'associations': deduped,
        'business_status': business_status,
        'evidence_candidates': project_evidence_candidates(turn),
        'started_at': started_at,
        'ended_at': ended_at,
        'duration_ms': runtime_duration,
        'failure_class': runtime_failure,
        'retry_count': retries,
    }
