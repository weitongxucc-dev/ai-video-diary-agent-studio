from __future__ import annotations


def project_turn_trace(turn: dict) -> dict:
    """Project one persisted turn into a redacted diagnostic trace."""
    events = turn.get('events') or []
    projected = []
    tool_counts = {}
    provider_usage = {}
    compactions = 0
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
        if event_type == 'tool_end':
            item['ok'] = bool(event.get('ok'))
            failure = event.get('failure')
            if isinstance(failure, dict):
                item['failure'] = {key: failure[key] for key in ('error', 'code', 'stage', 'submission_effect') if key in failure}
            tool_counts[label] = tool_counts.get(label, 0) + 1
        projected.append(item)
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
    }
