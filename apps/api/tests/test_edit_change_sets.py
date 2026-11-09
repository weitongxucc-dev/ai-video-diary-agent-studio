"""Direct metadata controls and adopted proposals share one complete version transaction."""
from app.content_identity_http import ContentIdentityHttpAdapter
from dataclasses import replace
import ast
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from test_task_media_grants import _visual_scope_case


@pytest.mark.parametrize('action', ['picture', 'overlay', 'transition', 'audio', 'crop', 'speed', 'lock', 'subtitle'])
def test_direct_control_never_calls_http_preview_or_confirm_and_undo_redo_is_complete(tmp_path, monkeypatch, action):
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError('direct metadata control cannot reenter HTTP routes or media runtime')
    try:
        canonical_guard_routes = [route for route in main.app.routes if route.name == 'preview_creation_task_exact_edit' and route.path == '/api/creation-tasks/{task_id}/edits/exact/preview' and ('POST' in route.methods)]
        assert len(canonical_guard_routes) == 1
        monkeypatch.setattr(canonical_guard_routes[0], 'endpoint', forbidden)
        monkeypatch.setattr(canonical_guard_routes[0].dependant, 'call', forbidden)
        canonical_guard_routes = [route for route in main.app.routes if route.name == 'preview_creation_task_edit' and route.path == '/api/creation-tasks/{task_id}/edits/preview' and ('POST' in route.methods)]
        assert len(canonical_guard_routes) == 1
        monkeypatch.setattr(canonical_guard_routes[0], 'endpoint', forbidden)
        monkeypatch.setattr(canonical_guard_routes[0].dependant, 'call', forbidden)
        canonical_guard_routes = [route for route in main.app.routes if route.name == 'confirm_creation_task_edit' and route.path == '/api/creation-tasks/{task_id}/edits/{proposal_id}/confirm' and ('POST' in route.methods)]
        assert len(canonical_guard_routes) == 1
        monkeypatch.setattr(canonical_guard_routes[0], 'endpoint', forbidden)
        monkeypatch.setattr(canonical_guard_routes[0].dependant, 'call', forbidden)
        monkeypatch.setattr(main, '_creation_runtime', forbidden)
        payloads = {
            'subtitle':{'operation_type':'ConfigureSubtitleTrack', 'subtitle_display':'none'},
            'crop':{'operation_type':'ChangeCrop', 'clip_id':'clip_1', 'fit_mode':'fit', 'crop_x':.5, 'crop_y':.5},
            'speed':{'operation_type':'ChangeClipSpeed', 'clip_id':'clip_1', 'playback_rate':.7},
            'lock':{'operation_type':'SetClipLock', 'clip_id':'clip_1', 'locked':True},
            'picture':{'operation_type':'AdjustVisualEffects', 'clip_ids':['clip_1'], 'effects':{'exposure':.15}},
            'overlay':{'operation_type':'EditGraphicOverlay', 'overlay_action':'delete', 'overlay_id':'outside'},
            'transition':{'operation_type':'SetClipTransition', 'clip_id':'clip_2', 'transition_seconds':.1},
            'audio':{'operation_type':'EditAudioTimeline', 'audio_edit':{'action':'update', 'target_id':'voice-clip', 'values':{'gain_db':-3}}},
        }
        jobs = len(repo.job_records.list_job_runs('local', 'task'))
        if action == 'audio':
            # The fixture deliberately locks its narrator; only this control's test input unlocks it.
            original['audio_timeline']['tracks'][0]['locked'] = False
            original['audio_timeline']['tracks'][0]['clips'][0]['locked'] = False
            repo.montage_plan_adoption.save_creation_task_montage_plan('local', 'task', 'montage', original)
            original = repo.get_creation_task_montage_plan('local', 'task')
            from app.creation.contracts import TimelineVersion
            from app.creation.transactions import clone_subtitle_track
            copied = clone_subtitle_track(track, timeline_version_id='audio-input')
            base = repo.timeline_records.get_timeline_version('local', 'timeline')
            repo.timeline_records.create_timeline_version(TimelineVersion('audio-input', 'local', 'task', 'timeline', 2,
                {**base.snapshot, 'montage':original, 'subtitle_track_ids':[copied.id]}, None))
            repo.subtitle_records.create_subtitle_track(copied)
        response = client.post('/api/creation-tasks/task/edits/exact', json=payloads[action])
        assert response.status_code == 200, response.text
        assert response.json()['operation']['operation_type'] == payloads[action]['operation_type']
        saved = repo.get_creation_task_montage_plan('local', 'task')
        assert len(repo.job_records.list_job_runs('local', 'task')) == jobs
        assert len(repo.edit_records.list_edit_operations('local', 'task')) == 1
        assert len(repo.edit_records.list_edit_proposals('local', 'task')) == 1
        assert repo.edit_records.list_edit_proposals('local', 'task')[0].status == 'confirmed'
        if action != 'audio':
            assert saved['audio_timeline'] == original['audio_timeline']
        else:
            assert saved['clips'] == original['clips']
        if action == 'subtitle':
            tracks = repo.subtitle_records.list_subtitle_tracks('local', 'task', timeline_version_id=repo.task_records.get_creation_task('local', 'task').current_timeline_version_id)
            assert tracks[0].style['enabled'] is False
        if action == 'picture':
            assert saved['clips'][1]['visual_effects']['exposure'] == .15
        if action == 'overlay':
            assert [item['id'] for item in saved['graphic_overlays']] == ['in-window']
        undo = client.post('/api/creation-tasks/task/timeline/undo', json={'request_id': 'test_direct_control_never_calls_http_preview_or_confirm_and_undo_redo_is_complete:1'})
        assert undo.status_code == 200, undo.text
        restored = repo.get_creation_task_montage_plan('local', 'task')
        assert restored['clips'] == original['clips'] and restored['audio_timeline'] == original['audio_timeline']
        assert restored['graphic_overlays'] == original['graphic_overlays']
        redo = client.post('/api/creation-tasks/task/timeline/redo', json={'request_id': 'test_direct_control_never_calls_http_preview_or_confirm_and_undo_redo_is_complete:2'})
        assert redo.status_code == 200, redo.text
        restored = repo.get_creation_task_montage_plan('local', 'task')
        assert restored['clips'] == saved['clips'] and restored['audio_timeline'] == saved['audio_timeline']
    finally:
        runtime.stop(0)


@pytest.mark.parametrize('failure', ['version', 'after_proposal', 'after_version'])
def test_direct_edit_cas_or_failure_rolls_back_all_lineage_and_cross_track_writes(tmp_path, monkeypatch, failure):
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    try:
        if failure == 'version':
            original_commit = repo.edit_adoption.confirm_direct
            def commit(command, *args, **kwargs):
                if kwargs.get('connection') is not None:
                    return original_commit(command, *args, **kwargs)
                from app.creation.contracts import TimelineVersion
                repo.timeline_records.create_timeline_version(TimelineVersion('next', 'local', 'task', 'timeline', 2, {'montage':original}, None))
                return original_commit(command, *args, **kwargs)
            monkeypatch.setattr(repo.edit_adoption, 'confirm_direct', commit)
        else:
            write = repo.edit_adoption.create_proposal if failure == 'after_proposal' else repo.edit_adoption.confirm
            def crash(*args, **kwargs):
                write(*args, **kwargs)
                raise ValueError('injected atomic commit failure')
            monkeypatch.setattr(repo.edit_adoption,
                'create_proposal' if failure == 'after_proposal' else 'confirm', crash)
        jobs = len(repo.job_records.list_job_runs('local', 'task'))
        response = client.post('/api/creation-tasks/task/edits/exact', json={
            'operation_type':'AdjustVisualEffects', 'clip_ids':['clip_1'], 'effects':{'exposure':.15}})
        assert response.status_code == 409, response.text
        assert repo.task_records.get_creation_task('local', 'task').current_timeline_version_id == ('next' if failure == 'version' else 'timeline')
        assert repo.edit_records.list_edit_operations('local', 'task') == []
        assert repo.edit_records.list_edit_intents('local', 'task') == []
        assert repo.edit_records.list_edit_proposals('local', 'task') == []
        assert repo.get_creation_task_montage_plan('local', 'task') == original
        assert len(repo.job_records.list_job_runs('local', 'task')) == jobs
        assert len(repo.subtitle_records.list_subtitle_tracks('local', 'task')) == 1
    finally:
        runtime.stop(0)


def test_direct_request_replay_recovers_a_lost_receipt_without_reapplying(tmp_path, monkeypatch):
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    try:
        payload = {'operation_type':'AdjustVisualEffects', 'clip_ids':['clip_1'], 'effects':{'exposure':.15},
            'request_id':str(uuid4()), 'expected_timeline_version_id':'timeline', 'expected_timeline_sequence':1}
        path = '/api/creation-tasks/task/edits/exact'
        saved = client.post(path, json=payload)
        assert saved.status_code == 200, saved.text
        assert client.post(path, json=payload).json() == saved.json()
        with repo._connect() as conn:
            conn.execute("DELETE FROM task_events WHERE event_type='edit_request_result'")
        recovered = client.post(path, json=payload)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()['operation'] == saved.json()['operation']
        assert len(repo.edit_records.list_edit_operations('local', 'task')) == 1
        assert client.post(path, json={**payload, 'effects':{'exposure':.25}}).status_code == 409
    finally:
        runtime.stop(0)


def test_edit_contract_imports_no_repository_or_app_and_computations_have_no_http_or_sql():
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, '-c', "import sys; import app.creation.contracts; assert 'app.main' not in sys.modules; assert 'app.production' not in sys.modules"],
        cwd=root, check=True, capture_output=True, timeout=10)
    for path in (root/'app/creation/editing').glob('*.py'):
        tree = ast.parse(path.read_text())
        assert not any(isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(('app.main', 'fastapi', 'starlette')) for node in ast.walk(tree))
        # State owners keep their transactions; edit computations stay free of I/O.
        if path.name not in {'records.py', 'timeline_history_repository.py', 'metadata_use_case.py',
                'timeline_restore_use_case.py', 'exact_preview_use_case.py'}:
            assert not any(isinstance(node, ast.Attribute) and node.attr == '_connect' for node in ast.walk(tree))


def test_clone_preserves_explicit_empty_subtitle_result_and_distinguishes_none():
    from app.creation.contracts import SubtitleCue, SubtitleTrack
    from app.creation.transactions import clone_subtitle_track
    from app.subtitle_edits import restructure_subtitle
    source = SubtitleTrack('track', 'local', 'task', 'v1', 'en-US', None, {'enabled':True},
        [SubtitleCue('cue', None, 'last subtitle', 0, 1)], 'timeline', None, None, None, 'valid')
    deleted, _ = restructure_subtitle(source, 'DeleteSubtitleCue', 'cue')
    assert deleted.cues == []
    assert clone_subtitle_track(source, timeline_version_id='v2', cues=deleted.cues).cues == []
    assert clone_subtitle_track(source, timeline_version_id='v2', cues=None).cues == source.cues
    assert clone_subtitle_track(source, timeline_version_id='v2', style={}).style == {}
    assert clone_subtitle_track(source, timeline_version_id='v2', style=None).style == source.style


@pytest.mark.parametrize('change', ['file', 'owner', 'restriction', 'segment'])
@pytest.mark.parametrize('typed', [True, False])
def test_source_change_after_binding_before_direct_transaction_creates_no_lineage(tmp_path, monkeypatch, change, typed):
    main, repo, actor, media, runtime, client, original, track, store = _visual_scope_case(tmp_path, monkeypatch)
    commit = repo.edit_adoption.confirm_direct
    called = False
    def change_source(command, *args, **kwargs):
        nonlocal called
        if kwargs.get('connection') is not None:
            return commit(command, *args, **kwargs)
        assert command.source['source_facts'] if typed else command.input_source_facts
        asset = repo.get_asset('local', 'asset')
        if change == 'file':
            with Path(asset.path).open('ab') as source:
                source.write(b'changed source before direct commit')
        elif change == 'owner':
            repo._save_asset(replace(asset, visibility='private', owner_user_id='other-member'))
        elif change == 'restriction':
            repo._save_asset(replace(asset, use_state='excluded'))
        else:
            asset.segments[0].start_seconds = .1
            repo._save_asset(asset)
        called = True
        verify = kwargs['verify_sources']
        def in_write_transaction(current, connection):
            assert connection.in_transaction
            return verify(current, connection)
        kwargs['verify_sources'] = in_write_transaction
        return commit(command, *args, **kwargs)
    try:
        # Plain controls also carry the trusted member, so private ownership cannot turn into diagnostic access.
        monkeypatch.setattr(ContentIdentityHttpAdapter, 'reader', lambda adapter, *args:(actor.member_id, False))
        monkeypatch.setattr(repo.edit_adoption, 'confirm_direct', change_source)
        payload = {'operation_type':'AdjustVisualEffects', 'clip_ids':['clip_1'], 'effects':{'exposure':.15}}
        if typed:
            payload.update(request_id=str(uuid4()), expected_timeline_version_id='timeline', expected_timeline_sequence=1)
        response = client.post('/api/creation-tasks/task/edits/exact', json=payload)
        assert called
        assert response.status_code == (403 if change == 'owner' else 409), response.text
        assert repo.task_records.get_creation_task('local', 'task').current_timeline_version_id == 'timeline'
        assert repo.edit_records.list_edit_intents('local', 'task') == []
        assert repo.edit_records.list_edit_proposals('local', 'task') == []
        assert repo.edit_records.list_edit_operations('local', 'task') == []
        assert len(repo.timeline_records.list_timeline_versions('local', 'task')) == 1
        assert repo.get_creation_task_montage_plan('local', 'task') == original
    finally:
        runtime.stop(0)


def test_direct_adoption_tail_failure_preserves_verify_order_and_rolls_back_lineage(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from app.creation.contracts import EditIntent, EditProposal, EditOperation, EditCommand, TimelineVersion
    from app.creation.transactions import EditChangeSet
    from test_production_foundation import _repository, _save_creation_task, edit_adoption_no_execution
    edit_adoption_no_execution(monkeypatch)
    repo = _repository(tmp_path)
    _save_creation_task(repo,workspace_id='workspace_a',task_id='task-a')
    base = repo.timeline_records.create_timeline_version(TimelineVersion('direct-base','workspace_a','task-a',None,1,{},None))
    intent = EditIntent('direct-intent','workspace_a','task-a','declared direct edit','montage',[],None,
        'replace_visual',{}, {},1,'interpreted')
    proposal = EditProposal('direct-proposal','workspace_a','task-a',intent.id,'declared direct adoption',['clip:clip-1'],
        {'operation_type':'ReplaceClip'},[],{},[])
    now = datetime.now(timezone.utc)
    operation = EditOperation('direct-operation','workspace_a','task-a',proposal.id,'ReplaceClip',{'target_clip_id':'clip-1','replacement_clip_id':'clip-2'},base.id,
        'direct-result','applied',created_at=now,applied_at=now)
    version = TimelineVersion(operation.result_version_id,'workspace_a','task-a',base.id,2,{},operation.id)
    change = EditChangeSet(operation,version,None,None,[],None)
    command = EditCommand('workspace_a','task-a',{'member_id':'declared-member','local_diagnostic':True},
        base.id,'ReplaceClip',{})
    sources = []
    def verify(original, connection):
        assert original is command and connection.in_transaction
        assert connection.execute('SELECT 1 FROM edit_intents WHERE id=?',(intent.id,)).fetchone() is None
        assert connection.execute('SELECT 1 FROM edit_proposals WHERE id=?',(proposal.id,)).fetchone() is None
        sources.append('before-lineage')
    original = repo.edit_adoption.confirm
    def refuse_after_adoption(*args, **kwargs):
        assert kwargs['connection'].in_transaction
        result = original(*args, **kwargs)
        assert result[3].current_timeline_version_id==version.id
        raise ValueError('injected adoption tail failure')
    with monkeypatch.context() as tail:
        tail.setattr(repo.edit_adoption,'confirm',refuse_after_adoption)
        with pytest.raises(ValueError,match='adoption tail failure'):
            repo.edit_adoption.confirm_direct(command,intent,proposal,change,verify_sources=verify)
    assert sources==['before-lineage']
    assert repo.edit_records.list_edit_intents('workspace_a','task-a')==[]
    assert repo.edit_records.list_edit_proposals('workspace_a','task-a')==[]
    assert repo.edit_records.list_edit_operations('workspace_a','task-a')==[]
    assert repo.timeline_records.list_timeline_versions('workspace_a','task-a')==[base]
    assert repo.task_records.get_creation_task('workspace_a','task-a').current_timeline_version_id==base.id
    write_proposal = repo.edit_adoption.create_proposal
    injected_proposals = []
    def refuse_after_proposal(*args, **kwargs):
        connection = kwargs['connection']
        assert connection.in_transaction
        result = write_proposal(*args, **kwargs)
        assert connection.execute('SELECT 1 FROM edit_intents WHERE id=?',(intent.id,)).fetchone() is not None
        assert connection.execute('SELECT 1 FROM edit_proposals WHERE id=?',(proposal.id,)).fetchone() is not None
        injected_proposals.append(result.id)
        raise ValueError('injected proposal write failure')
    with monkeypatch.context() as proposal_write:
        proposal_write.setattr(repo.edit_adoption,'create_proposal',refuse_after_proposal)
        with pytest.raises(ValueError,match='proposal write failure'):
            repo.edit_adoption.confirm_direct(command,intent,proposal,change,verify_sources=verify)
    assert injected_proposals==[proposal.id]
    assert sources==['before-lineage','before-lineage']
    assert repo.edit_records.list_edit_intents('workspace_a','task-a')==[]
    assert repo.edit_records.list_edit_proposals('workspace_a','task-a')==[]
    assert repo.edit_records.list_edit_operations('workspace_a','task-a')==[]
    assert repo.timeline_records.list_timeline_versions('workspace_a','task-a')==[base]
    assert repo.task_records.get_creation_task('workspace_a','task-a').current_timeline_version_id==base.id
    confirmed, applied, saved, task = repo.edit_adoption.confirm_direct(command,intent,proposal,change,verify_sources=verify)
    assert sources==['before-lineage','before-lineage','before-lineage']
    assert confirmed.status=='confirmed' and applied==operation and saved==version and task.current_timeline_version_id==version.id
