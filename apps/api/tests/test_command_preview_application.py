"""One real natural-language subtitle preview through the final explicit worker."""
import ast
from dataclasses import asdict
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import time

import pytest

import app.main as main
from app.task_audio import media_digest,TASK_MEDIA_CONTEXT
from app.production import EXACT_EDIT_SOURCE
import test_montage_pipeline as domain_fixture
import test_visual_preview_application as visual_fixture
import test_task_media_grants as grant_fixture
from test_montage_pipeline import person_render_edit_state as person_render_edit_state
from test_visual_preview_application import _inspect


def _function(value):
    return getattr(value,'_fixture_function',getattr(value,'__wrapped__',value))


def _fixture_clone(value,*,constructor=False,visual=False,permitted=False):
    function=_function(value)
    tree=ast.parse(inspect.getsource(function))
    node=tree.body[0]
    node.decorator_list=[]
    before=ast.dump(node,include_attributes=False)
    class AssemblyOnly(ast.NodeTransformer):
        def visit_Call(self,n):
            n=self.generic_visit(n)
            if constructor and isinstance(n.func,ast.Name) and n.func.id=='TaskMediaJobs':
                n.keywords=[k for k in n.keywords if k.arg not in {'execute_operation','validate_result'}]
            if permitted and isinstance(n.func,ast.Name) and n.func.id=='_granted_domain_preview_adapter':
                n.keywords=[k for k in n.keywords if k.arg!='validate_override']
            return n
        def visit_Assign(self,n):
            if visual and any(isinstance(t,ast.Attribute) and t.attr=='validate_result' and isinstance(t.value,ast.Attribute) and t.value.attr=='media' for t in n.targets):
                return None
            return self.generic_visit(n)
    tree=ast.fix_missing_locations(AssemblyOnly().visit(tree))
    namespace=dict(function.__globals__)
    exec(compile(tree,function.__code__.co_filename,'exec'),namespace)
    record={'source_file':function.__code__.co_filename,'source_sha256':hashlib.sha256(Path(function.__code__.co_filename).read_bytes()).hexdigest(),
        'original_complete_fixture_AST_sha256':hashlib.sha256(before.encode()).hexdigest(),
        'adapted_complete_fixture_AST_sha256':hashlib.sha256(ast.dump(tree.body[0],include_attributes=False).encode()).hexdigest(),
        'only_changes':('drop retired constructor execute/validate kwargs' if constructor else
            'drop retired main validation bridge argument' if permitted else 'drop restoration of retired main validation bridge'),
        'product_business_functions_replaced':0}
    return namespace[node.name],record


@pytest.fixture
def command_application(person_render_edit_state,monkeypatch,request):
    install,install_proof=_fixture_clone(domain_fixture._granted_domain_preview_adapter,constructor=True)
    monkeypatch.setattr(domain_fixture,'_granted_domain_preview_adapter',install)
    permitted_fixture,permitted_proof=_fixture_clone(domain_fixture.permitted_media_state,permitted=True)
    lifecycle=permitted_fixture(person_render_edit_state,monkeypatch,request)
    permitted=next(lifecycle)
    visual,visual_proof=_fixture_clone(visual_fixture.visual_application,visual=True)
    state=visual(permitted,monkeypatch)
    media_grant,grant_proof=_fixture_clone(grant_fixture.case,constructor=True)
    monkeypatch.setattr(grant_fixture,'case',media_grant)
    # The finite helper profile is injected through the shared resource getter.
    state.media.command_preview=main._command_preview_use_case(state.production)
    state.fixture_assembly=[install_proof,permitted_proof,visual_proof,grant_proof]
    try:
        yield state
    finally:
        with pytest.raises(StopIteration):
            next(lifecycle)


@pytest.mark.skipif(shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,reason='native media tools unavailable')
def test_command_subtitle_font_preview_has_real_media_and_replays_without_render(command_application,granted_media,tmp_path):
    state=command_application
    case=state.media.command_preview
    before_task=state.production.task_records.get_creation_task(state.workspace_id,state.task.id)
    before_montage=state.production.get_creation_task_montage_plan(state.workspace_id,state.task.id)
    payload={'raw_command':'字幕字号改为64','context_stage':'montage','selected_object_refs':[]}
    queued=state.client.post(state.base_url+'/edits/preview',json=payload)
    assert queued.status_code==202 and queued.json()['result'] is None,queued.text
    job_id=queued.json()['job_id']
    assert state.runtime.run_once()
    completed=state.client.get(queued.json()['result_url'])
    assert completed.status_code==200 and completed.json()['status']=='succeeded',completed.text
    job,start=state.media.read(state.workspace_id,job_id)
    assert start['operation']=='command_edit_preview' and start['arguments']['payload']==payload
    assert '_typed_source' not in start['arguments']
    assert job.input_digest==start['input_digest']==media_digest({k:v for k,v in start.items() if k!='input_digest'})
    result=completed.json()['result']
    assert 'request_source' not in result['intent']['constraints']
    assert result['proposal']['operation']['operation_type']=='ConfigureSubtitleTrack'
    records=[r for r in state.production.execution_records.list_artifact_records(state.workspace_id,state.task.id) if r.producer_job_id==job_id]
    assert len(records)==2 and {r.kind for r in records}=={'subtitle_style_preview'}
    side_records={case.metadata.audio_service._transform_for(state.workspace_id,state.task.id,r).parameters['side']:r for r in records}
    assert set(side_records)=={'before','after'}
    files={side:main.artifact_store.resolve_file(record.logical_ref) for side,record in side_records.items()}
    physical_before={side:(hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_size,path.stat().st_mtime_ns,path.stat().st_ctime_ns) for side,path in files.items()}
    directory=tmp_path/'frames'
    directory.mkdir()
    with granted_media(files.values(),kind='creation_render') as context:
        visuals={side:_inspect(context,path,directory,side) for side,path in files.items()}
    for visual in visuals.values():
        assert (visual['video']['width'],visual['video']['height'])==(1080,1920)
        assert abs(float(visual['probe']['format']['duration'])-7)<.1
        assert any(s['codec_type']=='audio' for s in visual['probe']['streams'])
        assert visual['frames']['middle']['white_pixels']>5
    assert visuals['after']['frames']['middle']['white_pixels']>visuals['before']['frames']['middle']['white_pixels']
    replay_started=time.monotonic()
    replay=state.client.post(state.base_url+'/edits/preview',json=payload)
    replay_finished=time.monotonic()
    assert replay.status_code==202 and replay.json()['job_id']==job_id,replay.text
    assert not state.runtime.run_once()
    assert state.client.get(queued.json()['result_url']).json()==completed.json()
    assert physical_before=={side:(hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_size,path.stat().st_mtime_ns,path.stat().st_ctime_ns) for side,path in files.items()}
    assert state.production.task_records.get_creation_task(state.workspace_id,state.task.id).current_timeline_version_id==before_task.current_timeline_version_id
    assert state.production.get_creation_task_montage_plan(state.workspace_id,state.task.id)==before_montage
    assert not hasattr(main,'_TASK_MEDIA_HANDLERS') and not hasattr(next(route.endpoint for route in main.app.routes if route.name == 'preview_creation_task_edit'),'__wrapped__')
    assert not hasattr(main,'_execute_task_media_operation') and not hasattr(main,'_validate_task_media_result')
    assert not hasattr(state.media,'execute_operation') and not hasattr(state.media,'validate_result')
    assert TASK_MEDIA_CONTEXT.get() is None and EXACT_EDIT_SOURCE.get() is None
    with state.production._connect() as conn:
        permit=dict(conn.execute('SELECT state,children_json FROM device_permit WHERE id=1').fetchone())
    assert permit['state']=='free'
    flow={'payload':payload,'queued':queued.json(),'completed':completed.json(),'replay':replay.json(),
        'start_event':start,'records':[asdict(r) for r in records],'visuals':visuals,'physical_before_replay':physical_before,
        'replay_started_monotonic':replay_started,'replay_finished_monotonic':replay_finished,
        'task_before':asdict(before_task),'montage_before':before_montage,'database_path':str(state.production.database_path),'permit':permit,
        'fixture_assembly':state.fixture_assembly,'factory':'main._command_preview_use_case',
        'resource_budget':asdict(state.runtime.scheduler.resources.budget),
        'fixture_domain':'original permitted/visual fixture data and finite caps; only assembly adapted to removed callbacks/bridge; all product/renderer/qualification/state bodies actual',
        'fixture_config_ports':'same original finite main render/audio profiles at moved command/member budget getter ports; no profile values or native limits changed',
        'fixture_OS_inputs':{'swap_used_bytes':0,'global_cpu_percent':0,'disk_io_counter':state.fixture_disk_io_baseline._asdict(),'actual_swap_before':state.actual_swap_before_fixture},
        'production_auth_host_profile_GUI_full_inflight_PGID':'UNVERIFIED'}
    destination=os.environ.get('COMMAND_PREVIEW_EVIDENCE_DIR')
    if destination:
        (Path(destination)/'NORMAL_FLOW.json').write_text(json.dumps(flow,ensure_ascii=False,indent=2,default=str)+'\n')
