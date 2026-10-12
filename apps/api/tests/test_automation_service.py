from datetime import datetime, timezone
import json
import sqlite3

import pytest
from pydantic import ValidationError

from app.automation_service import AutomationBlocked, AutomationCreate, AutomationService, AutomationTrigger
from app.creation_execution import CreationExecutionActor
from app.production import ProductionRepository
from app.scheduled_tasks import ScheduledTaskService, ScheduledTaskCreateRequest


def dt(value): return datetime.fromisoformat(value)

@pytest.fixture
def state(tmp_path):
    repo=ProductionRepository(tmp_path/'state.sqlite3',tmp_path/'media')
    actor=CreationExecutionActor('workspace','member')
    snapshot={'template':{'id':'template','version':1},'sources':[],'source_run_ids':[]}
    service=AutomationService(repo.database_path,verify_identity=lambda actor:'ready',read_inputs=lambda actor,definition:snapshot,project_run=lambda actor,run:None)
    payload=AutomationCreate(name='夜间安排',goal='真实目标',template_id='template',template_version=1,trigger={'cadence':'daily','timezone':'Asia/Shanghai','start_local':'2026-10-10T22:00:00','window_start':'22:00','window_end':'07:00'},source_scope={'connector_ids':[],'knowledge_ids':[],'material_asset_ids':[]})
    return service,actor,payload


def test_claim_is_durable_unique_on_fresh_reopen_and_pause_preserves_legacy(state):
    service,actor,payload=state
    legacy=ScheduledTaskService(service.database_path).create_task(actor.workspace_id,ScheduledTaskCreateRequest(task_type="material_analysis",trigger_mode="idle",target_ref="asset:original"))
    item=service.create(actor,payload)
    now=dt('2026-10-10T14:01:00+00:00')
    service.tick(now=now);service.tick(now=now)
    reopened=AutomationService(service.database_path,verify_identity=service.verify_identity,read_inputs=service.read_inputs,project_run=service.project_run)
    reopened.tick(now=now)
    runs=reopened.runs(actor,item['id'])
    assert len(runs)==1 and runs[0]['state']=='waiting_review'
    assert runs[0]['due_key']=='2026-10-10'
    assert runs[0]['input_snapshot']['sources']==[]
    current=reopened.get(actor,item['id']);reopened.pause(actor,item['id'],True,current['revision'])
    reopened.tick(now=dt('2026-10-11T14:01:00+00:00'))
    assert len(reopened.runs(actor,item['id']))==1
    with reopened.connect() as conn: assert conn.execute("SELECT target_ref FROM scheduled_tasks WHERE id=?",(legacy.id,)).fetchone()[0]=='asset:original'


def test_claim_crash_recovery_uses_same_run_and_fixed_revision(state,monkeypatch):
    service,actor,payload=state;item=service.create(actor,payload)
    original=service.prepare
    monkeypatch.setattr(service,'prepare',lambda actor,run: (_ for _ in ()).throw(RuntimeError('crash-after-claim')))
    with pytest.raises(RuntimeError):service.tick(now=dt('2026-10-10T14:01:00+00:00'))
    run=service.runs(actor,item['id'])[0]
    assert run['state']=='running'
    monkeypatch.setattr(service,'prepare',original)
    service.tick(now=dt('2026-10-10T14:02:00+00:00'))
    recovered=service.runs(actor,item['id'])
    assert len(recovered)==1 and recovered[0]['id']==run['id'] and recovered[0]['state']=='waiting_review'


def test_window_cross_midnight_and_closed_occurrence_has_no_task(state):
    service,actor,payload=state;item=service.create(actor,payload)
    trigger=payload.trigger
    assert trigger.allows(dt('2026-10-10T15:00:00+00:00'))
    assert trigger.allows(dt('2026-10-10T22:59:00+00:00'))
    assert not trigger.allows(dt('2026-10-10T23:00:00+00:00'))
    service.tick(now=dt('2026-10-11T01:00:00+00:00'))
    run=service.runs(actor,item['id'])[0]
    assert run['reason_code']=='outside_window' and run['task_id'] is None


@pytest.mark.parametrize('zone,local',[('Bad/Zone','2026-10-10T12:00'),('America/New_York','2026-11-01T01:30'),('America/New_York','2026-03-08T02:30'),('Asia/Shanghai','2026-10-10T12:00+08:00')])
def test_invalid_and_dst_ambiguous_wall_times_rejected(zone,local):
    with pytest.raises(ValidationError):AutomationTrigger(cadence='once',timezone=zone,start_local=local)


def test_daily_dst_change_preserves_wall_clock_and_ambiguity_blocks_run(state):
    service,actor,payload=state
    trigger=AutomationTrigger(cadence='daily',timezone='America/New_York',start_local='2026-03-07T10:00')
    assert trigger.first_due()==dt('2026-03-07T15:00:00+00:00')
    assert trigger.next_due(trigger.first_due(),trigger.first_due())==dt('2026-03-08T14:00:00+00:00')
    payload=payload.model_copy(update={'trigger':AutomationTrigger(cadence='daily',timezone='America/New_York',start_local='2026-10-31T01:30')})
    item=service.create(actor,payload);service.tick(now=dt('2026-10-31T05:31:00+00:00'))
    run=service.runs(actor,item['id'])[0]
    assert run['reason_code']=='schedule_timezone_ambiguity' and run['task_id'] is None
    assert service.get(actor,item['id'])['status']=='blocked'


def test_permissions_scope_and_definition_cas_are_current(state):
    service,actor,payload=state;item=service.create(actor,payload)
    stranger=CreationExecutionActor('workspace','other')
    assert service.list(stranger)==[]
    with pytest.raises(KeyError):service.get(stranger,item['id'])
    edited=service.revise(actor,item['id'],payload.model_copy(update={'name':'新版'}),item['revision'])
    with pytest.raises(ValueError):service.pause(actor,item['id'],True,item['revision'])
    service.verify_identity=lambda actor:'unauthorized'
    with pytest.raises(AutomationBlocked):service.list(actor)
    service.tick(now=dt('2026-10-10T14:01:00+00:00'))
    with service.connect() as conn: row=conn.execute('SELECT reason_code,automation_revision,definition_json FROM automation_runs').fetchone()
    assert row[0]=='identity_unauthorized' and row[1]==edited['revision']
    assert json.loads(row[2])['name']=='新版'


def test_missing_model_blocks_only_actual_run_and_no_implicit_generation(state):
    service,actor,payload=state
    service.check_capabilities=lambda actor: (_ for _ in ()).throw(AutomationBlocked('capability_unavailable','缺少模型','配置后重试'))
    item=service.create(actor,payload);service.tick(now=dt('2026-10-10T14:01:00+00:00'))
    run=service.runs(actor,item['id'])[0]
    assert run['state']=='blocked' and run['reason_code']=='capability_unavailable'
    assert run['task_id'] is None and run['input_snapshot']['template']['version']==1
