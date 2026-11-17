"""Persisted receipt replay; no provider, scheduler or user media is used."""
import json

from app.conversation_repository import ConversationRepository
from app.assistant.trace import project_turn_trace, project_usage_ledger


def test_cross_job_receipts_survive_restart_pagination_and_duplicate_delivery(tmp_path):
    database = tmp_path / 'trace.sqlite3'
    repo = ConversationRepository(database)
    chat = repo.create('w', 'm', 'home', 'home')
    draft = repo.save_draft(chat['id'], 'w', 'm', 'fixture instruction', 0)
    turn, created = repo.create_turn_idempotent(chat['id'], 'w', 'm',
        'fixture instruction', 'send-1', draft['draft_version'])
    assert created
    repo.finish_turn(turn['id'], 'completed', 'fixture answer', result={
        'usage_status': 'known', 'provider_usage': {'input': 7, 'output': 3, 'totalTokens': 10},
        'trace_events': [{'type': 'turn_end', 'at_ms': 1000}],
    })
    receipts = [
        {'event_id': 'failed', 'job_id': 'render-job', 'status': 'failed'},
        {'event_id': 'unknown', 'job_id': 'render-job', 'status': 'unknown',
         'failure': {'submission_effect': 'unknown', 'reason': '/private/secret key'}},
        {'event_id': 'recovered', 'job_id': 'render-job', 'status': 'succeeded', 'output_ref': 'render-output'},
        {'event_id': 'observed', 'job_id': 'observation-job', 'status': 'succeeded',
         'output_ref': 'render-output', 'observation_id': 'observation-output'},
    ]
    for receipt in receipts:
        event = {'type': 'business_result', 'phase': 'observation', 'task_id': 'task',
                 'request_id': 'original-request', 'timeline_version_id': 'timeline', **receipt}
        for _ in range(2):
            assert repo.project_business_result(turn['id'], 'w', 'm', event,
                response_patch={}, message='persisted fixture receipt')
    restarted = ConversationRepository(database)
    saved = restarted.get_turn(turn['id'])
    trace = project_turn_trace(saved, limit=2)
    assert trace['event_count'] == 5
    older = project_turn_trace(saved, before=trace['next_before'], limit=10)
    assert [e.get('status') for e in older['events']] == [None, 'failed', 'unknown']
    assert [e['status'] for e in trace['events']] == ['succeeded', 'succeeded']
    assert {r['kind'] for r in trace['associations']} >= {'task', 'timeline', 'business_request', 'job', 'output'}
    assert len({r['ref'] for r in trace['associations'] if r['kind'] == 'job'}) == 2
    assert '/private/' not in json.dumps(trace) and 'secret' not in json.dumps(older)
    ledger = project_usage_ledger(restarted.list_turns(chat['id']))
    assert ledger['turn_count'] == 1
    assert ledger['measured_usage']['totalTokens'] == 10
    # Read/replay only; no model resubmission or new turn is needed.
    assert project_turn_trace(restarted.get_turn(turn['id']), limit=2) == trace


def test_unknown_job_result_does_not_invent_token_usage():
    trace = project_turn_trace({'id': 't', 'conversation_id': 'c', 'status': 'failed',
        'sequence': 1, 'created_at': 'now', 'updated_at': 'now', 'result': {},
        'events': [{'type': 'business_result', 'event_id': 'e', 'status': 'unknown', 'job_id': 'j'}]})
    assert trace['events'][0]['status'] == 'unknown'
    assert trace['usage_status'] == 'unknown' and trace['provider_usage'] is None
