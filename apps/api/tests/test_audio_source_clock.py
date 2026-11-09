"""Video source clocks stay distinct from actual audio streams and output PCM."""
import array
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import time
from types import SimpleNamespace
import wave

import pytest

import app.audio_timeline as audio
from app.artifact_store import ArtifactStore
from app.host_resource_probes import CpuAudioBudget, HostBudget, HostProcessProbe, HostResourceProbe
from app.production import MaterialAsset
from app.task_audio import TaskAudio
from tests.host_memory_boundary import IsolatedMemoryMonitor


def graph(reference, duration_ms=2000):
    return audio.AudioTimeline(tracks=[audio.TimelineAudioTrack(id='original', name='原声', role='original',
        clips=[audio.TimelineAudioClip(id='original-clip', name='原片', source_ref=reference,
            source_start_ms=0, source_end_ms=duration_ms, timeline_start_ms=0)])])


@pytest.fixture
def metadata_probe(tmp_path, monkeypatch):
    source = tmp_path/'declared-input'
    source.write_bytes(b'opaque metadata protocol input, not decoded')
    declared = {'path': str(source), 'duration_seconds': 2, 'sample_rate': None, 'channels': None, 'media_type': 'video'}
    resources = {'kind': 'task_audio', 'inputs': [declared], 'source_duration_limit_seconds': 3,
        'source_sample_rate_limit': 48000, 'source_channels_limit': 2,
        'output_duration_limit_seconds': 3, 'output_sample_rate_limit': 48000, 'output_channels_limit': 2}
    state = SimpleNamespace(source=source, declared=declared, resources=resources, calls=[],
        audio={'streams': [{'duration': '1', 'sample_rate': '48000', 'channels': 1}], 'format': {'duration': '2'}},
        video={'streams': [{'duration': '2', 'disposition': {'attached_pic': 0}}], 'format': {'duration': '2'}})
    monkeypatch.setattr(audio, '_audio_grant_resources', lambda scheduler, grant: resources)
    def run(scheduler, grant, command, **options):
        stream = command[command.index('-select_streams')+1]
        state.calls.append(stream)
        assert options['deadline_monotonic'] is not None
        return SimpleNamespace(stdout=json.dumps(state.audio if stream == 'a:0' else state.video))
    monkeypatch.setattr(audio, '_run_granted_audio_command', run)
    state.probe = lambda: audio.probe_audio_metadata(source, scheduler=object(), grant=object(),
        deadline_monotonic=time.monotonic()+5)
    return state


def test_sealed_video_uses_picture_clock_while_retaining_real_short_audio(metadata_probe, tmp_path):
    state = metadata_probe
    measured = state.probe()
    assert measured == {'duration_seconds': 1., 'sample_rate': 48000, 'channels': 1, 'source_duration_seconds': 2.}
    assert state.calls == ['a:0', 'v:0']
    assert audio.probe_audio_source(state.source, scheduler=object(), grant=object(),
        deadline_monotonic=time.monotonic()+5) == 1
    command = audio.build_audio_mix_command(graph('workspace://video.mp4'), 2, lambda _: state.source, tmp_path/'mix.wav',
        probe_duration=lambda _: measured['source_duration_seconds'])
    filters = command[command.index('-filter_complex')+1]
    assert 'atrim=start=0.0:end=2.0' in filters and 'apad=whole_dur=2' in filters
    with pytest.raises(ValueError, match='音频源区间超出'):
        audio.build_audio_mix_command(graph('workspace://video.mp4', 2100), 2.1, lambda _: state.source, tmp_path/'outside.wav',
            probe_duration=lambda _: measured['source_duration_seconds'])


def test_audio_or_old_untyped_input_cannot_borrow_video_container_duration(metadata_probe, tmp_path):
    state = metadata_probe
    del state.declared['media_type']
    state.declared['duration_seconds'] = 1
    measured = state.probe()
    assert measured == {'duration_seconds': 1., 'sample_rate': 48000, 'channels': 1}
    assert state.calls == ['a:0']
    with pytest.raises(ValueError, match='音频源区间超出'):
        audio.build_audio_mix_command(graph('workspace://audio.wav'), 2, lambda _: state.source, tmp_path/'mix.wav',
            probe_duration=lambda _: measured['duration_seconds'])


def test_video_container_clock_is_used_only_after_a_real_video_stream_is_found(metadata_probe):
    del metadata_probe.video['streams'][0]['duration']
    assert metadata_probe.probe()['source_duration_seconds'] == 2


def test_live_audio_validation_uses_sealed_source_clock_but_ingress_probe_keeps_real_audio(metadata_probe, tmp_path, monkeypatch):
    state = metadata_probe
    service = TaskAudio(None, None, tmp_path)
    monkeypatch.setattr(service, 'source', lambda *args, **kwargs: state.source)
    context = SimpleNamespace(scheduler=object(), grant=object(), cancel_event=None,
        deadline_monotonic=time.monotonic()+5, check=lambda: None)
    service.validate('local', 'task', graph('workspace://video.mp4'), 2, context=context)
    assert service.probe(state.source, output=False, context=context) == 1
    del state.declared['media_type']
    state.declared['duration_seconds'] = 1
    with pytest.raises(ValueError, match='音频裁剪区间超出'):
        service.validate('local', 'task', graph('workspace://audio.wav'), 2, context=context)


@pytest.mark.parametrize('failure', ['missing_audio', 'missing_audio_clock', 'missing_video', 'attached_picture', 'video_budget', 'saved_video_clock'])
def test_video_clock_never_bypasses_missing_streams_or_source_limits(metadata_probe, failure):
    state = metadata_probe
    if failure == 'missing_audio':
        state.audio['streams'] = []
    elif failure == 'missing_audio_clock':
        del state.audio['streams'][0]['duration']
    elif failure == 'missing_video':
        state.video['streams'] = []
    elif failure == 'attached_picture':
        state.video['streams'][0]['disposition']['attached_pic'] = 1
    elif failure == 'video_budget':
        state.resources['source_duration_limit_seconds'] = 1.5
    else:
        state.declared['duration_seconds'] = 3
    with pytest.raises(ValueError):
        state.probe()


def finite_audio_budget():
    return CpuAudioBudget(1, 8_000_000, 3, 3, 48000, 2, 2_000_000, 1, 30,
        128*1024**2, 16, .05, 128*1024**2, 1_000_000)


def test_source_metadata_adds_video_clock_declaration_without_changing_audio_or_narration_shape(tmp_path):
    from app.creation.narration_artifacts import NarrationArtifactRecord
    from _media_preview_test_fixtures import synthetic_wav_bytes
    from test_task_media_grants import case
    repository, actor, _, _, _, _ = case(tmp_path)
    service = TaskAudio(repository, ArtifactStore(tmp_path/'artifacts'), tmp_path/'temporary')
    repository.video_library_path.mkdir(parents=True, exist_ok=True)
    for kind in ('video', 'audio'):
        source = tmp_path/'library'/kind
        source.write_bytes(b'opaque source metadata, not decoded')
        repository._save_asset(MaterialAsset(id=kind, workspace_id='local', file_name=source.name, path=str(source),
            size_bytes=source.stat().st_size, modified_at=datetime.fromtimestamp(source.stat().st_mtime, timezone.utc),
            duration_seconds=2, media_type=kind, owner_user_id=actor.member_id))
        reference = repository.workspace_media_reference_for_asset('local', kind)
        with repository._connect() as conn:
            repository._claim_workspace_media_reference(conn, workspace_id='local', reference=reference)
        expected = {'duration_seconds': 2, 'sample_rate': None, 'channels': None}
        if kind == 'video':
            expected['media_type'] = 'video'
        assert service.source_metadata('local', 'task', reference) == expected
    narration = tmp_path/'narration.wav'
    narration.write_bytes(synthetic_wav_bytes(1))
    repository.narration_artifacts.save_narration_artifact(NarrationArtifactRecord(id='narration', workspace_id='local',
        task_id='task', script_id='script', voice_profile_id='voice', cache_key='a'*64,
        audio_path=str(narration), duration_seconds=1, sample_rate=8000, model_id='test'))
    assert service.source_metadata('local', 'task', 'narration://artifact/'+'a'*64) == {
        'duration_seconds': 1, 'sample_rate': 8000, 'channels': None}


def test_real_video_with_one_second_shorter_audio_keeps_sound_and_silent_tail_under_grant(tmp_path):
    from test_task_media_grants import case
    budget = finite_audio_budget()
    probe = HostResourceProbe(tmp_path/'media.sqlite', disk_paths=(tmp_path,), budget=HostBudget(
        100, 1, 2**63, 0, 2**63, 8_000_000, sample_seconds=.01, cpu_audio=budget),
        memory_monitor=IsolatedMemoryMonitor())
    repository, actor, _, _, build, media = case(tmp_path, resources=probe)
    source = tmp_path/'library/short-audio.mp4'
    source.parent.mkdir(parents=True, exist_ok=True)
    # Only input construction is outside the grant; the measured mix/probes below use it.
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-filter_complex_threads', '1',
        '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=30:d=2',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-threads:v', '1', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-threads:a', '1', '-t', '2', str(source)], check=True)
    repository._save_asset(MaterialAsset(id='original-video', workspace_id='local', file_name=source.name,
        path=str(source), size_bytes=source.stat().st_size,
        modified_at=datetime.fromtimestamp(source.stat().st_mtime, timezone.utc), duration_seconds=2,
        media_type='video', usage_role='b_roll', review_status='approved', analysis_status='ready', owner_user_id=actor.member_id))
    reference = repository.workspace_media_reference_for_asset('local', 'original-video')
    with repository._connect() as conn:
        repository._claim_workspace_media_reference(conn, workspace_id='local', reference=reference)
    service = TaskAudio(repository, ArtifactStore(tmp_path/'artifacts'), tmp_path/'temporary')
    timeline = graph(reference)
    facts = service.mix_facts('local', 'task', timeline, 2, actor=actor, normalize_voice_sources=False, budget=budget)
    assert facts['resources']['inputs'][0]['media_type'] == 'video'
    assert facts['resources']['inputs'][0]['duration_seconds'] == 2
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    media.facts = lambda *args: ({'sealed_mix': facts}, facts['resources'])
    runtime = build(media)
    runtime.scheduler.processes = HostProcessProbe()
    grant = None
    try:
        job = media.submit(runtime, actor, 'task', 'audio_mix', {'audio_timeline': timeline.model_dump(), 'duration_seconds': 2}, 'audio_listen')
        grant = runtime.scheduler.pick_and_grant()
        assert grant is not None and grant.owner_id == job.id
        deadline = time.monotonic()+30
        measured = audio.probe_audio_metadata(source, scheduler=runtime.scheduler, grant=grant, deadline_monotonic=deadline)
        assert measured['source_duration_seconds'] == 2 and measured['source_duration_seconds']-measured['duration_seconds'] >= .9
        output = audio.render_audio_mix(timeline, 2, lambda _: source, tmp_path/'original-mix.wav',
            scheduler=runtime.scheduler, grant=grant, deadline_monotonic=deadline, max_output_bytes=2_000_000, threads=1)
        with wave.open(str(output), 'rb') as stream:
            assert (stream.getframerate(), stream.getnchannels(), stream.getnframes()) == (48000, 2, 96000)
            pcm = array.array('h', stream.readframes(stream.getnframes()))
        assert max(abs(value) for value in pcm[4800:24000]) > 100
        assert max(abs(value) for value in pcm[120000:]) <= 1
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_sha
        runtime.scheduler.assert_grant(grant)
        runtime.scheduler.complete_unit(grant, checkpoint_database_path=repository.database_path,
            checkpoint_write=lambda conn, current: 'verified-original-video-mix')
        runtime.scheduler.assert_released(grant)
        grant = None
    finally:
        if grant is not None:
            runtime.scheduler.fail_unit(grant, reason='video_clock_test_failed',
                failure_write=lambda conn, current: 'video_clock_test_failed')
        assert runtime.stop(1)
