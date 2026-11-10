"""Source identity, version/ownership and fresh-input reuse of editable visuals."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.creation.visual_template_catalog import ASSETS, visual_templates
from app.creation.visual_template_contracts import VisualTemplateOverlay
from app.creation.visual_template_reuse import reusable_components, instantiate_components
from app.creation.video_template_use_case import VideoTemplateUseCase
from app.creation.video_template_use_case import VideoTemplateRejected
from app.graphic_overlays import validate_overlays, splice_overlays
from app.media.template_document import template_document
from app.media.template_frames import frame_command, frame_manifest
from test_video_templates import state as state
from test_production_foundation import plan_owner as plan_owner


def visual_overlay(**changes):
    definition = next(visual_templates())[2]
    definition.values = {item.key: item.default for item in definition.bindings}
    return VisualTemplateOverlay(name='姓名条', template_id='builtin_visual_lower-corporate',
        template_version=1, definition=definition, start_seconds=1, end_seconds=4, **changes)


def test_catalog_pins_real_source_and_all_bundled_use_cases():
    items = list(visual_templates())
    assert len(items) == 60
    assert {item[2].category for item in items} == {'title','chapter','callout','data','product','ending','social','reaction','chat','news','location','festival'}
    manifest = json.loads((ASSETS/'provenance.json').read_text())
    for identity, _name, definition in items:
        if definition.provenance[0].license != 'CC0-1.0':
            from app.creation.rendercomp_template_catalog import ASSETS as rc_assets
            source = rc_assets / 'upstream' / Path(definition.provenance[0].path).name
            assert hashlib.sha256(source.read_bytes()).hexdigest() == definition.provenance[0].source_sha256
            assert definition.provenance[0].license == 'MIT'
            continue
        original = (ASSETS/f'{identity}.html').read_bytes()
        expected = next(item for item in manifest['files'] if item['file']==f'{identity}.html')
        assert hashlib.sha256(original).hexdigest() == expected['sha256'] == definition.provenance[0].source_sha256
        assert definition.source.encode() == original
        assert definition.provenance[0].license == 'CC0-1.0'
        assert all(binding.default for binding in definition.bindings if binding.kind=='text')


@pytest.mark.skipif(not os.environ.get('YAOHE_TEMPLATE_ELECTRON'), reason='requires actual desktop renderer')
def test_bundled_catalog_renders_real_frames_with_bound_chinese_text(tmp_path):
    """Every imported program must execute, not only parse its metadata."""
    import subprocess
    from PIL import Image
    for identity, name, definition in visual_templates():
        # Preserve numeric/date fields and replace an actual prose binding.
        prose = next(item for item in definition.bindings if item.kind=='text'
            and len(item.default)>1 and not any(char.isdigit() for char in item.default))
        definition.values[prose.key] = '科大分享'
        overlay = VisualTemplateOverlay(name=name, template_id='builtin_visual_'+identity,
            template_version=1, definition=definition, start_seconds=0, end_seconds=4)
        directory = tmp_path / identity
        command, environment = frame_command(overlay.model_dump(), directory,
            size=(480,640), first_frame=45, frame_count=1)
        result = subprocess.run(command,env=environment,capture_output=True,timeout=15)
        assert result.returncode == 0, (identity,result.stderr.decode(errors='replace'))
        frame_manifest(directory,1)
        with Image.open(directory/'frame-0000000.png') as frame:
            assert frame.size == (480,640) and frame.getbbox(), identity


def test_visual_copy_unknown_reconciliation_cas_and_original_version(state):
    repository, _, client = state
    builtin = next(item for item in client.get('/api/video-templates').json()['items'] if item['settings'].get('visual'))
    payload = {'request_id':str(uuid4()),'name':'我的姓名条','source_template_id':builtin['id'],
        'source_template_version':1,'visual':builtin['settings']['visual']}
    created = client.post('/api/video-templates',json=payload)
    assert created.status_code == 200, created.text
    saved = created.json()
    assert saved['can_edit'] is True
    assert client.post('/api/video-templates',json=payload).json()['id'] == saved['id']
    assert client.post('/api/video-templates',json={**payload,'name':'不同内容'}).status_code == 409
    source = deepcopy(saved['settings']['visual'])
    source['source'] = source['source'].replace('#2993ff','#11aabb') + '\n<!-- authored revision -->'
    changed = client.post(f"/api/video-templates/{saved['id']}/versions",json={
        'expected_version':1,'name':'我的第二版','visual':source})
    assert changed.status_code == 200, changed.text
    assert changed.json()['version'] == 2
    assert client.get(f"/api/video-templates/{saved['id']}?version=1").json()['settings']['visual'] == saved['settings']['visual']
    assert client.post(f"/api/video-templates/{saved['id']}/versions",json={
        'expected_version':1,'name':'旧版本覆盖','visual':source}).status_code == 409
    forged = deepcopy(source)
    forged['provenance'][0]['license']='unverified'
    assert client.post(f"/api/video-templates/{saved['id']}/versions",json={
        'expected_version':2,'name':'丢失许可','visual':forged}).status_code == 422
    owner = repository.video_templates.get_video_template('workspace_a',saved['id'],2).owner_user_id
    with pytest.raises(VideoTemplateRejected, match='所有者'):
        VideoTemplateUseCase(repository).append_version(
            repository.video_templates.get_video_template('workspace_a',saved['id'],2),
            expected_version=2,name='其他成员',style_instruction='',user_id='someone-other-than-'+owner)


def test_reuse_preserves_design_but_drops_previous_inputs_and_object_ids():
    overlay = visual_overlay(keyframes=[{'seconds':0,'x':.2,'y':.5,'scale':.8,'opacity':0},
        {'seconds':3,'x':.7,'y':.5,'scale':1,'opacity':1}],z_index=4)
    overlay.definition.values['p0']='上一条作品的人名，不能复制到新主题'
    plan=SimpleNamespace(graphic_overlays=[overlay.model_dump()],duration_seconds=10)
    settings={'visual_components':reusable_components(plan)}
    assert settings['visual_components'][0]['definition']['values'] == {}
    first,second=instantiate_components(settings,20),instantiate_components(settings,20)
    assert first[0]['id'] != second[0]['id'] != overlay.id
    assert first[0]['definition']['source'] == overlay.definition.source
    assert (first[0]['start_seconds'],first[0]['end_seconds']) == (2,8)
    assert first[0]['keyframes'][-1]['seconds'] == 6
    assert first[0]['z_index'] == 4
    assert VisualTemplateOverlay.model_validate(first[0])


def test_source_errors_dependencies_and_clock_are_explicit(tmp_path):
    overlay=visual_overlay()
    document=template_document(overlay.definition)
    assert document.index('Content-Security-Policy') < document.index(overlay.definition.source)
    assert "worker-src 'none'" in document and "connect-src 'none'" in document
    assert 'window.film.frame(t) is required' in document
    assert 'filmHost.time(t);await film.frame(t);filmHost.settle(t)' in document
    assert 'securitypolicyviolation' in document
    overlay.definition.dependencies=['missing-font.woff2']
    with pytest.raises(ValueError,match='依赖'):
        frame_command(overlay.model_dump(),tmp_path/'unstarted',size=(1080,1920),first_frame=0,frame_count=30)
    assert not (tmp_path/'unstarted').exists()
    overlay.definition.dependencies=['']
    with pytest.raises(ValueError,match='空项目.*空数组'):
        template_document(overlay.definition)


def test_authored_code_uses_granted_task_preview_not_the_workbench_renderer(state):
    _repository,_template,client=state
    visual=next(visual_templates())[2].model_dump()
    stock=client.post('/api/video-templates/visual-preview',json=visual)
    assert stock.status_code==200 and stock.json()['document']
    visual['source']+='\n<script>while(true){}</script>'
    authored=client.post('/api/video-templates/visual-preview',json=visual)
    assert authored.status_code==200 and authored.json()['document'] is None
    visual=next(visual_templates())[2].model_dump()
    visual['bindings'][0]['target']='body'
    assert client.post('/api/video-templates/visual-preview',json=visual).json()['document'] is None


def test_frame_manifest_never_adopts_missing_or_partial_frames(tmp_path):
    (tmp_path/'frames.json').write_text(json.dumps({'frames':['frame-0000000.png','frame-0000001.png']}))
    (tmp_path/'frame-0000000.png').write_bytes(b'placeholder')
    with pytest.raises(ValueError,match='缺帧'):
        frame_manifest(tmp_path,2)
    (tmp_path/'frame-0000001.png').write_bytes(b'placeholder')
    result=frame_manifest(tmp_path,2,24)
    assert 'option framerate 24' in result.read_text()


def test_template_order_and_keyframe_timing_validation():
    low=visual_overlay(z_index=-1)
    high=visual_overlay(z_index=3)
    ordered=validate_overlays([high.model_dump(),low.model_dump()],5)
    assert [item.id for item in ordered] == [low.id,high.id]
    with pytest.raises(ValueError,match='关键帧'):
        visual_overlay(keyframes=[{'seconds':2},{'seconds':1}])
    animated=visual_overlay(keyframes=[{'seconds':0,'x':.2},{'seconds':3,'x':.8}])
    pieces=splice_overlays([animated.model_dump()],[(2000,3000,0)])
    assert len(pieces)==1 and pieces[0]['animation_offset_seconds']==1
    assert pieces[0]['keyframes'][0]['x']==pytest.approx(.4)
    assert pieces[0]['keyframes'][-1]['x']==pytest.approx(.6)
    assert VisualTemplateOverlay.model_validate(pieces[0])


def test_native_png_keeps_real_execution_lineage_and_is_usable_graphic_source(tmp_path):
    import base64
    from io import BytesIO
    from PIL import Image
    from test_creation_execution import _native_operations_case
    from app.graphic_sources import graphic_source_facts
    case=_native_operations_case(tmp_path)
    content=BytesIO()
    Image.new('RGBA',(32,32),(230,160,20,160)).save(content,format='PNG')
    created=case.call('native_task_file',{'action':'write','path':'/task/outputs/layer.png',
        'expected_sha256':None,'content_base64':base64.b64encode(content.getvalue()).decode()})
    registered=case.call('register_task_artifacts',{'request_id':'register_png','execution_job_id':created['job_id'],
        'files':[{'path':'/task/outputs/layer.png','role':'image_overlay','expected_sha256':created['sha256']}]})
    case.executor.execute(case.scheduler.pick_and_grant(),case.scheduler)
    result=case.call('read_task_command',{'job_id':registered['job_id']})
    assert result['status']=='succeeded',result
    artifact=result['artifacts'][0]
    facts=graphic_source_facts(case.repo,case.native.store,'local',case.task_id,artifact['source_ref'],actor=case.actor)
    assert facts['sha256']==created['sha256']
    assert (facts['width'],facts['height'])==(32,32)
    transform=next(item for item in case.repo.lineage_records.list_transform_runs('local',case.task_id)
        if artifact['source_ref'] in item.output_refs)
    assert transform.transform_type=='native_artifact_registration'
    assert transform.parameters['source_execution_job_id']==created['job_id']
    Path(facts['path']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='变化'):
        graphic_source_facts(case.repo,case.native.store,'local',case.task_id,artifact['source_ref'],actor=case.actor)


def test_native_preparation_covers_authorized_media_and_work_files_together(tmp_path,monkeypatch):
    from dataclasses import replace
    from app.artwork_resource_preparation import extend_native_budget
    from app.creation.native_task_operations import NativeTaskOperations
    from app.device_resource_preparation import CpuPreparationShape
    from test_device_resource_preparation import metadata_preparation
    _repository,actor,_access,service,_paths=metadata_preparation(tmp_path,monkeypatch)
    source_bytes=10*1024*1024
    shape=CpuPreparationShape(64,64,30,1.,1,1,8000,1,source_bytes=source_bytes)
    _identity,original=service.prepare(shape,actor,request_id=str(uuid4()))
    sample={'output_capacity_bytes':64*64*4*30,'actual_output_bytes':2048,'frames':30,
        'elapsed_seconds':1.,'observed_resources':{'rss_sample_complete':True,'threads_sample_complete':True,
            'peak_rss_bytes':24*1024*1024,'peak_threads':3}}
    budget=extend_native_budget(replace(shape,native_command=True),original,{'native_command':sample},
        {'probe_seconds':1.},1.,original,service.policy)
    root=tmp_path/'native'
    for name in ('control','work','inputs','outputs'):
        (root/name).mkdir(parents=True)
    source=tmp_path/'source.bin'
    with source.open('wb') as handle:
        handle.truncate(source_bytes)
    stat=source.stat()
    (root/'inputs'/'source').symlink_to(source)
    (root/'control'/'inputs.json').write_text(json.dumps({'source':{'path':str(source),
        'file_revision':[stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]}}))
    (root/'work'/'program.py').write_text('x'*1000)
    case=NativeTaskOperations(None,None,root,budget=budget.native_command)
    facts=case.resources(root,{})
    assert facts['input_bytes']==source_bytes+1000
    assert budget.max_unit_input_bytes>=facts['input_bytes']


@pytest.mark.skipif(sys.platform!='darwin' or not os.environ.get('YAOHE_TEMPLATE_ELECTRON'),
    reason='requires desktop Electron and native macOS sandbox')
def test_real_granted_html_and_native_preparation_share_no_fake_calibration(tmp_path,monkeypatch):
    from dataclasses import replace
    from test_device_resource_preparation import preparation
    from app.device_resource_preparation import CpuPreparationShape
    from app.device_runtime import build_device_runtime
    from app.host_resource_probes import HostResourceProbe, HostProcessProbe
    import app.device_resource_preparation as preparation_module
    original=preparation_module.run_granted_command
    failures=[]
    def record_failure(*args,**kwargs):
        value=original(*args,**kwargs)
        if value.returncode:
            failures.append(value.stderr.decode('utf-8',errors='replace')[-2000:])
        return value
    monkeypatch.setattr(preparation_module,'run_granted_command',record_failure)
    repository,actor,access,service,paths=preparation(tmp_path)
    service.settings=replace(service.settings,model_runtime_python_path=sys.executable)
    shape=CpuPreparationShape(256,256,30,1.,1,1,8000,1,overlay_count=1,template_count=1,native_command=True)
    plan_id,budget=service.prepare(shape,actor,request_id=str(uuid4()))
    resources=HostResourceProbe(repository.database_path,disk_paths=paths,budget=budget)
    runtime=build_device_runtime(database_path=repository.database_path,
        device_lock_path=service.profile_path.parent/'device-permit.lock',device_access=access,
        registrations={'device_preparation':service.registration()},resources=resources,
        processes=HostProcessProbe(),host_observer=resources)
    request=runtime.scheduler.submit(service.intent(plan_id,actor),actor)
    try:
        runtime.start()
        deadline=time.monotonic()+service.policy.timeout_seconds+5
        while time.monotonic()<deadline:
            with repository._connect() as conn:
                row=conn.execute('SELECT state,wait_reason FROM device_work_requests WHERE id=?',(request.id,)).fetchone()
            if row['state'] in {'done','failed'} or runtime.last_error is not None:
                break
            time.sleep(.1)
        assert row['state']=='done',(dict(row),runtime.last_error,failures)
        result=service.finalize(database_path=repository.database_path,request_id=request.id,actor=actor)
        assert result.budget.cpu_render.max_template_count==1
        assert result.budget.native_command is not None
        receipt=json.loads(next((service.root/'receipts').glob('*.json')).read_text())
        assert receipt['native_command']['actual_output_bytes']>0
        assert receipt['native_command']['observed_resources']['rss_sample_complete'] is True
        assert result.evidence['native_command']['status']=='recorded'
        assert not list(Path(paths['temporary']).iterdir())
    finally:
        assert runtime.stop(3)
