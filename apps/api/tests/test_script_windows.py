"""SQL window boundaries on an isolated production-shaped script record."""
import json
import sqlite3

import pytest

from app.creation.script_windows import ScriptWindowConflict, read_script_sections, section_digest, migrate_script_sections


@pytest.fixture
def script_db():
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE creation_task_scripts (id TEXT, workspace_id TEXT, task_id TEXT, payload TEXT, updated_at TEXT)')
        sections = [{'id': f'section-{index}', 'title': f'段落{index}', 'content': f'正文{index}',
                     'visual_prompt': f'画面{index}', 'evidence_reference_ids': ['source-1']} for index in range(1, 46)]
        payload = {'title': '同一作品', 'language': 'zh-CN', 'selection_status': 'draft', 'sections': sections,
                   'unrelated_large_field': '不得进入窗口' * 1000}
        connection.execute('INSERT INTO creation_task_scripts VALUES (?,?,?,?,?)',
                           ('script', 'workspace', 'task', json.dumps(payload), '2026-10-24T00:00:00+00:00'))
        migrate_script_sections(connection)
        yield connection, sections


def test_sql_windows_read_all_sections_without_hydrating_the_script(script_db, monkeypatch):
    connection, expected = script_db
    original = json.loads
    decoded = []
    def bounded_loads(value, *args, **kwargs):
        decoded.append(value)
        assert 'unrelated_large_field' not in value
        parsed = original(value, *args, **kwargs)
        assert isinstance(parsed, dict) and set(parsed) == set(expected[0])
        return parsed
    monkeypatch.setattr('app.creation.script_windows.json.loads', bounded_loads)
    cursor, received, version = 0, [], None
    while cursor is not None:
        page = read_script_sections(connection, 'workspace', 'task', 'script', after_ordinal=cursor,
                                    expected_updated_at=version)
        version = page['updated_at']
        received.extend(item['section'] for item in page['items'])
        assert [item['ordinal'] for item in page['items']] == list(range(cursor + 1, cursor + 1 + len(page['items'])))
        assert all(item['digest'] == section_digest(item['section']) for item in page['items'])
        assert page['total_count'] == 45
        assert page['truncated'] == (page['next_after_ordinal'] is not None)
        cursor = page['next_after_ordinal']
    assert received == expected and len(decoded) == 45


def test_sql_window_never_stitches_a_new_revision_into_an_old_cursor(script_db):
    connection, _ = script_db
    first = read_script_sections(connection, 'workspace', 'task', 'script')
    connection.execute("UPDATE creation_task_scripts SET updated_at='2026-10-24T00:00:01+00:00'")
    with pytest.raises(ScriptWindowConflict):
        read_script_sections(connection, 'workspace', 'task', 'script', after_ordinal=20,
                             expected_updated_at=first['updated_at'])
    current = read_script_sections(connection, 'workspace', 'task', 'script', after_ordinal=20,
                                   expected_updated_at='2026-10-24T00:00:01+00:00')
    assert current['items'][0]['section']['id'] == 'section-21'


@pytest.mark.parametrize('workspace,task', [('other', 'task'), ('workspace', 'other')])
def test_sql_window_cannot_cross_task_or_workspace(script_db, workspace, task):
    connection, _ = script_db
    with pytest.raises(KeyError):
        read_script_sections(connection, workspace, task, 'script')


@pytest.mark.parametrize('kwargs', [{'limit': 21}, {'limit': 0}, {'after_ordinal': -1}, {'after_ordinal': True}, {'after_ordinal': 46, 'expected_updated_at': '2026-10-24T00:00:00+00:00'}])
def test_single_read_limits_remain_real_without_capping_total_script_size(script_db, kwargs):
    connection, _ = script_db
    with pytest.raises(ValueError):
        read_script_sections(connection, 'workspace', 'task', 'script', **kwargs)


def test_empty_tail_and_equivalent_version_timezone_are_explicit(script_db):
    connection, _ = script_db
    page = read_script_sections(connection, 'workspace', 'task', 'script', after_ordinal=45,
                                expected_updated_at='2026-10-24T08:00:00+08:00')
    assert page['items'] == [] and page['total_count'] == 45
    assert page['next_after_ordinal'] is None and page['truncated'] is False


def test_migration_removes_the_legacy_body_in_the_same_transaction(script_db):
    connection, expected = script_db
    header = json.loads(connection.execute('SELECT payload FROM creation_task_scripts').fetchone()[0])
    assert 'sections' not in header and header['_sections_storage'] == 'rows'
    assert connection.execute('SELECT COUNT(*) FROM creation_task_script_sections').fetchone()[0] == len(expected)
    migrate_script_sections(connection)
    assert connection.execute('SELECT COUNT(*) FROM creation_task_script_sections').fetchone()[0] == len(expected)


def test_window_read_is_not_a_json_each_scan_or_python_full_script_slice(script_db):
    connection, _ = script_db
    statements = []
    connection.set_trace_callback(statements.append)
    page = read_script_sections(connection, 'workspace', 'task', 'script', after_ordinal=20, limit=2,
                                expected_updated_at='2026-10-24T00:00:00+00:00')
    assert [item['section']['id'] for item in page['items']] == ['section-21', 'section-22']
    assert all('json_each' not in sql.lower() and 'select payload' not in sql.lower() for sql in statements)
    plan = connection.execute('EXPLAIN QUERY PLAN SELECT ordinal,section_json,digest FROM creation_task_script_sections '
        'WHERE workspace_id=? AND script_id=? AND ordinal>? ORDER BY ordinal LIMIT ?', ('workspace', 'script', 20, 2)).fetchall()
    assert any('SEARCH' in row[3] and 'INDEX' in row[3] for row in plan)


def test_migration_failure_does_not_commit_half_a_script():
    from app.creation.script_windows import migrate_script_sections
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE creation_task_scripts (id TEXT, workspace_id TEXT, task_id TEXT, payload TEXT, updated_at TEXT)')
        body = {'sections': [{'id': 'same'}, {'id': 'same'}]}
        connection.execute('INSERT INTO creation_task_scripts VALUES (?,?,?,?,?)', ('script', 'workspace', 'task', json.dumps(body), 'version'))
        connection.commit()
        connection.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.IntegrityError):
            migrate_script_sections(connection)
        connection.rollback()
        assert json.loads(connection.execute('SELECT payload FROM creation_task_scripts').fetchone()[0]) == body
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='creation_task_script_sections'").fetchone() is None


def test_continuation_requires_the_first_windows_version(script_db):
    connection, _ = script_db
    with pytest.raises(ValueError, match='版本'):
        read_script_sections(connection, 'workspace', 'task', 'script', after_ordinal=20)
