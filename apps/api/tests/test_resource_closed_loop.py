"""Replay production records only; no manual resource ledger writes/media."""
import json
import pytest
from app.materials.resource_closed_loop import ResourceClosedLoopReader
from tests.test_creation_execution import _seed


def replay(tmp_path):
    repo, task, _, _ = _seed(tmp_path, voice=False)
    ws = 'workspace_a'
    source, output = 'artifacts/task/import.wav', 'artifacts/task/final.mp4'
    catalog = {'id': 'music', 'version': 1}
    with repo._connect() as c:
        for identity, status in [('import', 'succeeded'), ('render-failed','failed'), ('render','succeeded'), ('observe','succeeded'), ('unknown','interrupted')]:
            c.execute('INSERT INTO job_runs(id,workspace_id,task_id,kind,idempotency_key,input_digest,status,progress,attempt,output_refs_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity, ws, task, 'task_media', identity, 'digest', status, 0, 1, '[]', identity, identity))
        def event(identity, job_id, kind, payload):
            c.execute('INSERT INTO task_events VALUES (?,?,?,?,?,?,?)',
                (identity, ws, task, job_id, kind, json.dumps(payload), identity))
        event('e-import', 'import', 'task_media_submitted', {'arguments': {'catalog_resource': catalog,
            '_typed_source': {'assistant_turn_id':'turn', 'request_id':'req', 'secret':'private'}}})
        event('e-unknown', 'unknown', 'task_media_submitted', {'arguments': {'catalog_resource': catalog}})
        event('e-failed', 'render-failed', 'task_execution_stage_failed', {'base_version_id':'v1'})
        event('e-observe', 'observe', 'task_media_result', {'result': {'observation_id':'observe',
            'timeline_version_id':'v2', 'output': {'artifact_id':'video'}}})
        for identity, ref, producer, kind, inputs in [('audio',source,'import','audio_source',[]),
                ('video',output,'render','rendered_video',[])]:
            c.execute('INSERT INTO artifact_records VALUES (?,?,?,?,?,?,?,?,?,?)',
                (identity,ws,task,kind,ref,'a'*64,1,producer,json.dumps(inputs),identity))
        c.execute('''INSERT INTO transform_runs(id,workspace_id,task_id,job_id,transform_type,
            input_digest,input_refs_json,output_refs_json,engine,parameters_json,status,started_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('mix-transform',ws,task,'render','montage_delivery','digest',json.dumps([source]),
             json.dumps([output]),'fixture-engine','{}','succeeded','time'))
        for seq, identity, parent, graph, outputs, op in [(1,'v1',None,source,[],None),
                (2,'v2','v1',source,[output],None), (3,'v3','v2',None,[],'undo')]:
            snapshot = {'montage': {'audio_timeline': {'tracks':[{'clips':[{'source_ref':graph}]}] if graph else []}}, 'output_refs':outputs}
            c.execute('INSERT INTO timeline_versions '
                '(id,workspace_id,task_id,parent_version_id,sequence,snapshot_json,created_by_operation_id,created_at) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (identity,ws,task,parent,seq,json.dumps(snapshot),op,identity))
        c.execute('INSERT INTO edit_operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            ('undo',ws,task,'undo-proposal','UndoTimelineVersion','{}','v2','v3','applied',None,'time','time'))
        c.execute("UPDATE creation_tasks SET payload=json_set(payload,'$.current_timeline_version_id','v3','$.owner_user_id','member') WHERE id=?", (task,))
    return repo, task


def test_durable_import_adoption_render_observation_undo_and_failure_readback(tmp_path):
    repo, task = replay(tmp_path)
    def readonly():
        conn = repo._connect()
        conn.execute('PRAGMA query_only=ON')
        return conn
    reader = ResourceClosedLoopReader(readonly)
    first = reader.read('workspace_a', task, 'music', 1, user_id='member')
    assert first == reader.read('workspace_a', task, 'music', 1, user_id='member')
    assert first['import_job_ids'] == ['import','unknown']
    assert [(v['timeline_version_id'],v['transition']) for v in first['timeline_history']] == [('v1','added'),('v2','retained'),('v3','removed')]
    assert first['timeline_history'][-1]['operation_type'] == 'UndoTimelineVersion'
    assert first['current_resource_present'] is False
    assert {j['job_id']:j['status'] for j in first['jobs']} == {
        'import':'succeeded','observe':'succeeded','render':'succeeded','render-failed':'failed','unknown':'interrupted'}
    assert first['observations'][0]['artifact_id'] == 'video'
    assert first['transforms'] == [{'transform_id': 'mix-transform', 'job_id': 'render',
        'status': 'succeeded', 'type': 'montage_delivery'}]
    assert first['media_validation'] == 'unknown'
    assert 'private' not in json.dumps(first)
    assert reader.read('workspace_a', task, 'music', 2, user_id='member')['import_job_ids'] == []
    with pytest.raises(KeyError):
        reader.read('foreign', task, 'music', 1, user_id='member')
    with repo._connect() as c:
        c.execute("UPDATE creation_tasks SET visibility='private',payload=json_set(payload,'$.owner_user_id','owner') WHERE id=?", (task,))
    with pytest.raises(KeyError):
        reader.read('workspace_a', task, 'music', 1, user_id='member')


def test_history_is_task_scoped_and_never_infers_success_from_missing_result(tmp_path):
    repo, task = replay(tmp_path)
    _, other, _, _ = _seed(tmp_path, voice=False, task_suffix='_other')
    with repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=json_set(payload,'$.owner_user_id','member') WHERE id=?", (other,))
    reader = repo.resource_governance
    absent = reader.read_production_history('workspace_a', other, 'music', 1, user_id='member')
    assert absent['import_job_ids'] == [] and absent['jobs'] == []
    original = reader.read_production_history('workspace_a', task, 'music', 1, user_id='member')
    assert next(j for j in original['jobs'] if j['job_id'] == 'unknown')['status'] == 'interrupted'
    assert len(original['observations']) == 1
    assert original['observations'][0]['job_status'] == 'succeeded'
    with repo._connect() as conn:
        assert conn.execute('SELECT count(*) FROM resource_usage_records').fetchone()[0] == 0


def test_original_resource_journal_retains_no_job_failures_unknown_and_member_scope(tmp_path):
    from app.creation.contracts import TaskEvent
    from app.models import utc_now
    repo, task_id = replay(tmp_path)
    task = repo.task_records.get_creation_task('workspace_a', task_id)
    records = repo.execution_records
    inputs = {'resource_id': 'music', 'version': 1,
              'source': {'raw_command': 'PRIVATE_INSTRUCTION'}, 'actor_identity': {'member_id': 'member'}}
    failed, _ = records.claim_edit_request(task, 'member', 'request-failed', 'audio_resource', inputs)
    records.save_edit_request_result(task, failed, status_code=409,
        response={'detail': {'error':'resource_not_connected', 'reason':'PRIVATE_REASON'}})
    records.claim_edit_request(task, 'member', 'request-unknown', 'audio_resource', inputs)
    recorded, _ = records.claim_edit_request(task, 'member', 'request-recorded', 'audio_resource', inputs)
    records.save_edit_request_result(task, recorded, status_code=202, response={'job_id':'import'})
    records.append_task_event(TaskEvent('event_import_start', 'workspace_a', task_id, 'import',
        'task_media_submitted', {'member_id':'member'}, created_at=utc_now()))
    foreign, _ = records.claim_edit_request(task, 'other-member', 'request-foreign', 'audio_resource', inputs)
    records.save_edit_request_result(task, foreign, status_code=404, response={})
    wrong = {**inputs, 'version': 999}
    records.claim_edit_request(task, 'member', 'wrong-version', 'audio_resource', wrong)
    def readonly():
        c = repo._connect()
        c.execute('PRAGMA query_only=ON')
        return c
    reader = ResourceClosedLoopReader(readonly)
    first = reader.read('workspace_a', task_id, 'music', 1, user_id='member')
    assert first == reader.read('workspace_a', task_id, 'music', 1, user_id='member')
    requests = {item['request_id']: item for item in first['requests']}
    assert requests == {
        'request-failed': {'request_id':'request-failed', 'status':'rejected', 'status_code':409,
                           'job_id':None, 'error_code':'resource_not_connected'},
        'request-unknown': {'request_id':'request-unknown', 'status':'unknown', 'job_id':None},
        'request-recorded': {'request_id':'request-recorded', 'status':'recorded', 'job_id':'import'},
    }
    serialized = json.dumps(first)
    assert 'PRIVATE_' not in serialized and 'actor_identity' not in serialized
    # A response locator whose Job identity cannot be reconciled stays unknown.
    with repo._connect() as c:
        c.execute("UPDATE task_events SET payload_json=json_set(payload_json,'$.member_id','other-member') WHERE id='event_import_start'")
    after = reader.read('workspace_a', task_id, 'music', 1, user_id='member')
    assert next(r for r in after['requests'] if r['request_id']=='request-recorded')['status'] == 'unknown'
