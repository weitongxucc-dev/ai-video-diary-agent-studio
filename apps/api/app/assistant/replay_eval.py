"""Small deterministic replay checks for persisted assistant traces."""

from __future__ import annotations


def evaluate_replay(events: list[dict], expected: dict) -> dict:
    """Check observable behavior without rerunning a model or business write."""
    starts = [event.get('tool_name') or event.get('label') for event in events
              if event.get('type') == 'tool_start' and (event.get('tool_name') or event.get('label'))]
    tool_names = starts or [event.get('tool_name') or event.get('label') for event in events
                            if event.get('type') == 'tool_end' and
                            (event.get('tool_name') or event.get('label'))]
    failures = [event for event in events if event.get('type') == 'tool_end' and event.get('ok') is False]
    unknown = [event for event in failures
               if (event.get('failure') or {}).get('submission_effect') == 'unknown']
    cancelled = any(event.get('type') in {'turn_cancelled', 'turn_stopped'} or
                    (event.get('failure') or {}).get('error') in {'cancelled', 'aborted'} for event in events)
    recovered = any(event.get('type') in {'business_result', 'recovery', 'turn_recovered'} and
                    event.get('status') in {'succeeded', 'confirmed', 'recovered'} for event in events)
    final_states = [event.get('status') for event in events if event.get('type') == 'business_result'
                    and isinstance(event.get('status'), str)]
    checks = {
        'tool_selection': tool_names[:len(expected.get('tool_names', []))] == expected.get('tool_names', []),
        'failure': bool(failures) == bool(expected.get('failed', False)),
        'cancel': cancelled == bool(expected.get('cancelled', False)),
        'unknown_result': bool(unknown) == bool(expected.get('unknown_result', False)),
        'recovery': recovered == bool(expected.get('recovered', False)),
        'final_business_state': (not expected.get('final_status') or
                                 expected['final_status'] in final_states),
    }
    return {'passed': all(checks.values()), 'checks': checks,
            'observed': {'tool_names': tool_names, 'final_statuses': final_states,
                         'unknown_result': bool(unknown)}}
