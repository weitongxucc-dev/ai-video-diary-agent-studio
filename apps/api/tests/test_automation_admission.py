from datetime import datetime,timezone
from app.automation_service import AutomationCreate,AutomationService
from app.automation_runtime import AutomationCreationOwner
from app.creation_execution import submit_creation_stage
from app.member_permits import MemberDeviceAccess
from test_creation_execution import _render_version_fixture
import app.automation_runtime as runtime_module


def test_pause_and_cross_midnight_gate_only_linked_next_unit_after_canonical_ready(tmp_path,monkeypatch):
    repo,task_id,script_id,digest,actor,execution,owner,scheduler,version,members=_render_version_fixture(tmp_path)
    config=vars(owner).copy()
    config.pop('repository')
    scheduler.owners=AutomationCreationOwner(repo,**config)
    service=AutomationService(repo.database_path,verify_identity=MemberDeviceAccess(members).verify_identity,read_inputs=lambda actor,definition:{'template':{'id':'fixture','version':1}},project_run=lambda actor,run:None)
    payload=AutomationCreate(name='窗口检查',goal='已确认任务',template_id='fixture',template_version=1,trigger={'cadence':'once','timezone':'Asia/Shanghai','start_local':'2026-10-10T22:00','window_start':'22:00','window_end':'07:00'},source_scope={'connector_ids':[],'knowledge_ids':[],'material_asset_ids':[]})
    try:
        job=execution.start('local',task_id,script_id,digest,actor=actor)
        request_id=submit_creation_stage(execution,scheduler,job,actor)
        request=scheduler._get(request_id)
        with repo._connect() as conn:
            assert scheduler.owners.check_current(request,conn)=='ready' # ordinary job untouched
        definition=service.create(actor,payload)
        service.tick(now=datetime(2026,10,10,14,1,tzinfo=timezone.utc))
        run=service.runs(actor,definition['id'])[0]
        with repo._connect() as conn:
            conn.execute('UPDATE automation_runs SET task_id=? WHERE id=?',(task_id,run['id']))
        monkeypatch.setattr(runtime_module,'utc_now',lambda:datetime(2026,10,10,15,tzinfo=timezone.utc))
        with repo._connect() as conn:
            assert scheduler.owners.check_current(request,conn)=='ready'
        monkeypatch.setattr(runtime_module,'utc_now',lambda:datetime(2026,10,10,23,tzinfo=timezone.utc))
        with repo._connect() as conn:
            assert scheduler.owners.check_current(request,conn)=='dependency'
        monkeypatch.setattr(runtime_module,'utc_now',lambda:datetime(2026,10,10,15,tzinfo=timezone.utc))
        current=service.get(actor,definition['id'])
        service.pause(actor,definition['id'],True,current['revision'])
        with repo._connect() as conn:
            assert scheduler.owners.check_current(request,conn)=='dependency'
        current=service.get(actor,definition['id'])
        service.pause(actor,definition['id'],False,current['revision'])
        grant=scheduler.pick_and_grant()
        assert grant is not None
        current=service.get(actor,definition['id'])
        service.pause(actor,definition['id'],True,current['revision'])
        monkeypatch.setattr(runtime_module,'utc_now',lambda:datetime(2026,10,10,23,tzinfo=timezone.utc))
        scheduler.assert_grant(grant) # actual current held permit remains valid; no false release
        with repo._connect() as conn:
            assert scheduler.owners.check_current(scheduler._get(request_id),conn)=='ready'
        scheduler.owners.verify_identity=lambda actor:'unauthorized'
        with repo._connect() as conn:
            assert scheduler.owners.check_current(request,conn)=='unauthorized' # original rule precedes window
        scheduler.defer_unit(grant,reason='authorization_lost')
        scheduler.assert_released(grant)
        assert repo.job_records.get_job_run('local',job.id).status=='queued' and service.get(actor,definition['id'])['paused']
    finally:
        scheduler.close()
