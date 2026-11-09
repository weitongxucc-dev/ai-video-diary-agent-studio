"""Real PCM output preserves compiled picture samples at both rounding directions."""
from datetime import datetime, timezone
import time
import wave

import pytest

from app.audio_timeline import render_audio_mix
from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe
from app.production import MaterialAsset
from app.task_audio import TaskAudio
from test_pcm_output_clock import budget, graph
from test_task_media_grants import case
from tests.host_memory_boundary import IsolatedMemoryMonitor


@pytest.mark.parametrize('duration_ms,samples', [(9167, 440000), (102, 4800), (119, 6400)])
def test_granted_pcm_uses_exact_picture_samples_without_changing_editorial_range(tmp_path, duration_ms, samples):
    limits = budget()
    resources = HostResourceProbe(tmp_path/'media.sqlite', disk_paths=(tmp_path,), budget=HostBudget(
        100, 1, 2**63, 0, 2**63, 8_000_000, sample_seconds=.01, cpu_audio=limits),
        memory_monitor=IsolatedMemoryMonitor())
    repo, actor, _, _, build, media = case(tmp_path, resources=resources)
    source = repo.video_library_path/'source.wav'
    source.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(source), 'wb') as stream:
        stream.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x10\x27' * 480000)
    repo._save_asset(MaterialAsset(id='audio', workspace_id='local', file_name=source.name, path=str(source),
        size_bytes=source.stat().st_size, modified_at=datetime.fromtimestamp(source.stat().st_mtime, timezone.utc),
        media_type='audio', duration_seconds=10, analysis_status='ready', review_status='approved',
        usage_role='b_roll', owner_user_id=actor.member_id))
    reference = repo.workspace_media_reference_for_asset('local', 'audio')
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id='local', reference=reference)
    timeline = graph(duration_ms)
    timeline.tracks[0].clips[0].source_ref = reference
    original = timeline.model_dump()
    audio = TaskAudio(repo, None, tmp_path/'temporary')
    facts = audio.mix_facts('local', 'task', timeline, duration_ms/1000, actor=actor,
        normalize_voice_sources=False, budget=limits, output_sample_count=samples)
    media.facts = lambda *args: ({'sample_clock': samples}, facts['resources'])
    runtime = build(media)
    runtime.scheduler.processes = HostProcessProbe()
    grant = None
    try:
        job = media.submit(runtime, actor, 'task', 'audio_mix', {'sample_count': samples}, 'audio_listen')
        grant = runtime.scheduler.pick_and_grant()
        assert grant is not None and grant.owner_id == job.id
        output = render_audio_mix(timeline, duration_ms/1000, lambda _: source, tmp_path/'mix.wav',
            scheduler=runtime.scheduler, grant=grant, threads=1, max_output_bytes=4_000_000,
            deadline_monotonic=time.monotonic()+30, output_sample_count=samples)
        with wave.open(str(output), 'rb') as stream:
            assert (stream.getnframes(), stream.getframerate(), stream.getnchannels()) == (samples, 48000, 2)
        assert timeline.model_dump() == original
        runtime.scheduler.complete_unit(grant, checkpoint_database_path=repo.database_path,
            checkpoint_write=lambda conn, current: 'exact-picture-samples')
        grant = None
    finally:
        if grant is not None:
            runtime.scheduler.fail_unit(grant, reason='pcm_clock_test_failed',
                failure_write=lambda conn, current: 'pcm_clock_test_failed')
        assert runtime.stop(1)
