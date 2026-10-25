"""A new finite attempt preserves its failed request and shares the owner transaction."""
from dataclasses import replace

import pytest

from app.device_scheduler import RecoveryBlocked
from test_device_scheduler import _scheduler, _intent, _complete


def failed_unit(tmp_path):
    scheduler = _scheduler(tmp_path)
    original = scheduler.submit(_intent('quality-job', owner_type='quality_check', unit='phase:old'), 'member-a')
    grant = scheduler.pick_and_grant()
    scheduler.fail_unit(grant, reason='phase_timeout', failure_write=lambda conn, current: None)
    with scheduler._connect() as conn:
        conn.execute('CREATE TABLE owner_attempts (request_id TEXT PRIMARY KEY)')
    return scheduler, scheduler._get(original.id), grant


def test_continuation_preserves_failure_replays_and_rolls_back_owner_write(tmp_path):
    scheduler, original, grant = failed_unit(tmp_path)
    intent = _intent('quality-job', owner_type='quality_check', unit='phase:attempt-digest')
    generation = scheduler.reconcile_released()
    trusted = replace(scheduler.owners.verify_submission(intent, 'member-a'), resources={'cpu_threads': 1, 'timeout_seconds': 12})
    options = dict(expected_failed_generation=grant.generation, expected_free_generation=generation,
                   owner_database_path=scheduler.database_path)
    def write(conn, old, request_id):
        assert old == original
        conn.execute('INSERT INTO owner_attempts VALUES (?)', (request_id,))
        return trusted
    def broken(conn, old, request_id):
        write(conn, old, request_id)
        raise RuntimeError('late business failure')
    with pytest.raises(RuntimeError, match='late business'):
        scheduler.continue_failed_unit(original.id, intent, 'member-a', owner_write=broken, **options)
    with scheduler._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM owner_attempts').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM device_work_requests').fetchone()[0] == 1
    replacement = scheduler.continue_failed_unit(original.id, intent, 'member-a', owner_write=write, **options)
    assert replacement.id != original.id and replacement.state == 'waiting'
    assert replacement.resources == trusted.resources
    assert scheduler._get(original.id) == original
    replay = scheduler.continue_failed_unit(original.id, intent, 'member-a',
        owner_write=lambda *args: pytest.fail('replay must not reopen the owner'), **options)
    assert replay == replacement
    with pytest.raises(RecoveryBlocked):
        scheduler.continue_failed_unit(original.id, intent, 'member-a', owner_write=write,
            **{**options, 'expected_failed_generation': grant.generation + 1})
    with scheduler._connect() as conn:
        assert conn.execute('SELECT request_id FROM owner_attempts').fetchall()[0][0] == replacement.id
        event = conn.execute("SELECT reason FROM device_request_events WHERE event_type='retried'").fetchone()
        assert event[0] == 'continued_as:' + replacement.id


def test_continuation_rejects_stale_release_actor_and_changed_source_atomically(tmp_path):
    scheduler, original, grant = failed_unit(tmp_path)
    intent = _intent('quality-job', owner_type='quality_check', unit='phase:attempt-digest')
    generation = scheduler.reconcile_released()
    options = dict(expected_failed_generation=grant.generation, expected_free_generation=generation,
                   owner_database_path=scheduler.database_path)
    trusted = scheduler.owners.verify_submission(intent, 'member-a')
    with pytest.raises(PermissionError):
        scheduler.continue_failed_unit(original.id, intent, 'member-b', owner_write=lambda *args: trusted, **options)
    with pytest.raises(RecoveryBlocked):
        scheduler.continue_failed_unit(original.id, intent, 'member-a', owner_write=lambda *args: trusted,
            **{**options, 'expected_free_generation': generation - 1})
    def wrong_source(conn, old, request_id):
        from app.device_scheduler import SourceRef
        conn.execute('INSERT INTO owner_attempts VALUES (?)', (request_id,))
        return replace(trusted, source_refs=(SourceRef('private-a', 'artifact', 'another', 'v1'),))
    with pytest.raises(PermissionError):
        scheduler.continue_failed_unit(original.id, intent, 'member-a', owner_write=wrong_source, **options)
    other = scheduler.submit(_intent('other-job'), 'member-a')
    active = scheduler.pick_and_grant()
    assert active.request_id == other.id
    with pytest.raises(RecoveryBlocked):
        scheduler.continue_failed_unit(original.id, intent, 'member-a', owner_write=lambda *args: trusted, **options)
    _complete(scheduler, active)
    with scheduler._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM owner_attempts').fetchone()[0] == 0
    assert scheduler._get(original.id) == original
