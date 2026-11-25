"""Exercise the formal cut route, worker, and publication against real synthetic media."""
from array import array
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main
from app.artifact_store import ArtifactStore
from app.creation.contracts import TimelineVersion
from app.creation.task_authorization import TaskAuthorizationUseCase
from app.creation_execution import CreationExecutionActor
from app.host_resource_probes import HostProcessProbe
from app.local_members import LocalMemberService
from app.media_execution import TaskMediaExecutionDelegate, build_media_execution_entrypoint
from app.production import MaterialAsset, MaterialSegment
from test_host_resource_probes import _render_budget
from test_task_media_grants import case


def _assert_cut_history(client, base, child, job_id):
    history = '/api/creation-tasks/task/timeline/versions'
    assert child.created_by_operation_id is None
    assert child.snapshot['media_execution']['job_id'] == job_id
    listing, version = client.get(history), client.get(f'{history}/{child.id}')
    assert listing.status_code == version.status_code == 200, (listing.text, version.text)
    assert listing.json()['current_version_id'] == child.id
    assert [item['id'] for item in listing.json()['items']] == [child.id, base.id]
    summary = version.json()['version']
    assert summary == listing.json()['items'][0]
    assert (summary['parent_version_id'], summary['operation_id'], summary['operation_type']) == (base.id, None, None)


@pytest.mark.parametrize(('variant', 'fps', 'start', 'end', 'presentations', 'failure'), [
    ('mp4', 30, 0, 2, [index / 30 for index in range(60)], None),
    ('mp4', 1, 0.5, 1.5, [0, 0.5], None),
    ('mp4', 1, 0.25, 0.75, [0], None),
    ('mp4_delayed', 1, 0.25, 0.75, [], 'media_cut_no_video_frames'),
    ('mkv', 30, 0.1, 0.6, [round(index * 1000 / 30) / 1000 - 0.1 for index in range(3, 18)], None),
    ('mkv', '24000/1001', 0.125, 0.625,
     [int(index * 1001 / 24 + 0.5) / 1000 - 0.125 for index in range(3, 15)], None),
    ('mov', '30000/1001', 0.1001, 0.6001, [index * 1001 / 30000 for index in range(15)], None),
    ('mkv_gap', 30, 0.1, 0.6, [], 'media_cut_presentation_gap'),
    ('mp4_probe_timeout', 1, 0.25, 0.75, [], 'media_command_timeout'),
])
def test_bound_video_cut_publishes_only_verified_video(
    tmp_path, monkeypatch, granted_media, variant, fps, start, end, presentations, failure,
):
    repo, actor, _, _, build, media = case(tmp_path)
    members = LocalMemberService(repo.database_path)
    _, token = members.whoami(None)
    source_owner, other_token = members.join(members.create_invite_code(), '样片上传者')
    suffix = variant.split('_')[0]
    source = repo.video_library_path / f'synthetic-video.{suffix}'
    source.parent.mkdir(parents=True, exist_ok=True)
    picture = "drawbox=c=blue:t=fill:enable='gte(t,1)'"
    if variant == 'mp4_delayed':
        picture += ',trim=start=1'
    if variant == 'mkv_gap':
        picture += r',setpts=PTS+3*gte(N\,8)'
    # Picture and audio change together at source time one second.
    audio_input = ['-f', 'lavfi', '-i', r'aevalsrc=0.5*sin(2*PI*if(lt(t\,1)\,440\,880)*t):d=4'] if suffix == 'mp4' else []
    with granted_media(kind='creation_render') as context:
        encoded = context.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=red:s=96x64:r={fps}:d=4',
            *audio_input, '-vf', picture, '-fps_mode:v', 'passthrough', '-c:v', 'libx264', '-threads:v', '1',
            '-c:a', 'aac', str(source)], output_paths=(source,))
    assert encoded.returncode == 0, encoded.stderr
    stats = source.stat()
    asset = repo._save_asset(MaterialAsset(id='authorized-qa', workspace_id='local', file_name=source.name,
        path=str(source), size_bytes=stats.st_size, modified_at=datetime.fromtimestamp(stats.st_mtime, UTC),
        duration_seconds=4, owner_user_id=source_owner.member_id, visibility='shared', analysis_status='ready',
        usage_role='b_roll', segments=[MaterialSegment('qa-source-segment', 'authorized-qa', 0, 4, usage_role='b_roll')]))
    reference = repo.workspace_media_reference_for_asset('local', asset.id)
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id='local', reference=reference)
    base = repo.timeline_records.create_timeline_version(TimelineVersion('qa-base', 'local', 'task', None, 1,
        {'montage': {'clips': [{'asset_id': asset.id, 'source_path': reference}]}}, None))
    media.task_authorizations = TaskAuthorizationUseCase(repo, lambda identity: media.verify_identity(
        CreationExecutionActor.from_event(identity['workspace_id'], identity)))
    task = repo.task_records.get_creation_task('local', 'task')
    media.task_authorizations.ensure(task, actor, interactive=True)
    store = ArtifactStore(tmp_path / 'artifacts')
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'artifact_store', store)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'settings', replace(main.settings, database_path=str(repo.database_path),
        video_output_path=str(tmp_path / 'artifacts'), video_inbox_path=str(repo.video_library_path),
        temporary_root_path=str(tmp_path / 'tmp')))
    probe_times_out = variant == 'mp4_probe_timeout'
    render = replace(_render_budget(), max_threads=1, max_timeout_seconds=3 if probe_times_out else 20,
                     max_output_bytes=2 * 1024**2)
    monkeypatch.setattr(main, '_device_operation_budget', lambda key: render if key == 'cpu_render' else None)
    media.facts = main._task_media_facts
    entrypoint = build_media_execution_entrypoint(repository=repo, output_root=tmp_path / 'artifacts',
        connection_factory=repo.connection_factory, artifact_store=store)
    media.media_execution_delegate = TaskMediaExecutionDelegate(entrypoint)
    probe_pid = tmp_path / 'probe.pid'
    if probe_times_out:
        slow_probe = tmp_path / 'slow-probe'
        slow_probe.write_text(f'#!{sys.executable}\n'
            f'import os, time\nfrom pathlib import Path\nPath({str(probe_pid)!r}).write_text(str(os.getpid()))\n'
            'time.sleep(15)\n')
        slow_probe.chmod(0o700)
        entrypoint.layer.dependencies['ffprobe'] = replace(entrypoint.layer.dependencies['ffprobe'], path=str(slow_probe))
    runtime = build(media)
    runtime.scheduler.processes = HostProcessProbe()
    runtime.scheduler.resources.budget = SimpleNamespace(cpu_render=render, presenter_cpu_render=None)
    registered_children = []
    register_child = runtime.scheduler.register_child

    def record_child(grant, child):
        register_child(grant, child)
        registered_children.append(child)

    monkeypatch.setattr(runtime.scheduler, 'register_child', record_child)
    monkeypatch.setattr(main, '_creation_runtime', lambda: runtime)
    monkeypatch.setattr(main, '_media_result_read_components', lambda: (media, runtime.scheduler))
    request = {'request_id': str(uuid4()), 'base_version_id': base.id, 'asset_id': asset.id,
               'start_seconds': start, 'end_seconds': end}
    root = '/api/creation-tasks/task/media-jobs'
    client = TestClient(main.app, headers={'X-Local-Member-Token': token})
    try:
        if start == 0.5:
            gate = Barrier(2)
            submit = media.submit

            def compete(*args, **kwargs):
                gate.wait(timeout=5)
                return submit(*args, **kwargs)

            with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=2) as pool:
                patch.setattr(media, 'submit', compete)
                first = pool.submit(client.post, root, json=request)
                second = pool.submit(client.post, root, json=request)
                accepted, duplicate = first.result(), second.result()
            assert duplicate.status_code == 202, duplicate.text
            assert accepted.json() == duplicate.json()
        else:
            accepted = client.post(root, json=request)
        assert accepted.status_code == 202, accepted.text
        queued = accepted.json()
        assert queued['status'] == 'queued'
        assert client.post(root, json=request).json() == queued
        assert runtime.run_once()
        read = client.get(queued['result_url'])
        assert read.status_code == 200, read.text
        receipt = read.json()
        replay = client.post(root, json=request)
        assert replay.status_code == 202 and replay.json() == receipt
        assert len(repo.job_records.list_creation_job_request_states('local', receipt['job_id'])) == 1
        outer = repo.execution_records.get_task_event('local', f"event_{receipt['job_id']}_result").payload['result']
        assert {job.id for job in repo.job_records.list_job_runs('local', 'task')} == {receipt['job_id'], outer['job_id']}
        versions = repo.timeline_records.list_timeline_versions('local', 'task')
        records = repo.execution_records.list_artifact_records('local', 'task')
        assert len(registered_children) == (1 if failure else 3)
        assert runtime.scheduler.processes.children_released(tuple(registered_children)) is True
        assert runtime.scheduler.shutdown_released()
        if failure:
            assert receipt['status'] == 'failed' and receipt['failure_reason'] == failure
            assert receipt['result'] is None
            assert [version.id for version in versions] == [base.id] and not records
            assert repo.task_records.get_creation_task('local', 'task').current_timeline_version_id == base.id
            assert Path(outer['log_path']).is_file()
            assert client.get(queued['result_url'] + '/outputs/1').status_code == 409
            if probe_times_out:
                assert int(probe_pid.read_text()) == registered_children[0].pid
            return

        assert receipt['status'] == 'succeeded'
        assert [version.id for version in versions] == [base.id, outer['timeline_version_id']]
        assert versions[-1].parent_version_id == base.id
        assert versions[-1].created_by_operation_id is None
        artifact, = records
        assert artifact.logical_ref in outer['output_refs'] and artifact.producer_job_id == outer['job_id']
        assert reference in artifact.input_refs
        if variant == 'mp4' and fps == 30:
            _assert_cut_history(client, base, versions[-1], outer['job_id'])
        url, = receipt['result']['output_urls']
        downloaded = client.get(url)
        assert downloaded.status_code == 200
        assert hashlib.sha256(downloaded.content).hexdigest() == artifact.sha256
        output = tmp_path / 'downloaded.mp4'
        output.write_bytes(downloaded.content)
        probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_frames', '-show_streams',
            '-show_format', '-show_entries', 'frame=media_type,best_effort_timestamp_time:'
            'stream=codec_type,codec_name,start_time,duration:format=duration', '-of', 'json', str(output)], timeout=10))
        tracks = {item['codec_type']: item for item in probe['streams']}
        assert tracks['video']['codec_name'] == 'h264'
        actual_pts = [float(frame['best_effort_timestamp_time']) for frame in probe['frames']
                      if frame['media_type'] == 'video']
        assert actual_pts == pytest.approx(presentations, abs=0.001)
        assert float(tracks['video']['duration']) == pytest.approx(end - start, abs=0.001)
        if audio_input:
            assert tracks['audio']['codec_name'] == 'aac'
            assert float(tracks['audio']['start_time']) == pytest.approx(0, abs=0.001)
        else:
            assert 'audio' not in tracks
        assert float(probe['format']['duration']) == pytest.approx(end - start, abs=0.001)
        if fps == 1:
            cmd = ['ffmpeg', '-v', 'error', '-nostdin', '-i', str(output)]
            pixels = subprocess.check_output([*cmd, '-vf', 'scale=1:1,format=rgb24', '-fps_mode', 'passthrough',
                '-f', 'rawvideo', 'pipe:1'], timeout=10)
            assert len(pixels) == 3 * len(presentations)
            assert pixels[0] > 180 and pixels[2] < 40
            if start == 0.5:
                assert pixels[3] < 40 and pixels[5] > 180
                samples = array('h', subprocess.check_output([*cmd, '-map', '0:a:0', '-ar', '8000',
                    '-f', 's16le', 'pipe:1'], timeout=10))
                # The original 440 Hz and 880 Hz tones meet at the picture cut.
                for lo, hi, frequency in ((0.39, 0.49, 440), (0.51, 0.61, 880)):
                    window = samples[round(lo * 8000):round(hi * 8000)]
                    rising = sum(a <= 0 < b for a, b in zip(window, window[1:]))
                    assert rising == pytest.approx(frequency * (hi - lo), abs=2)
        assert client.get(url.replace('/task/media-jobs/', '/another-task/media-jobs/')).status_code == 404
        assert client.get(url, headers={'X-Local-Member-Token': other_token}).status_code == 404
        repo.set_asset_visibility('local', asset.id, 'private')
        assert client.get(queued['result_url']).status_code == 404
        assert client.get(url).status_code == 404
        assert client.post(root, json=request).status_code == 403
    finally:
        client.close()
        runtime.stop(1)
