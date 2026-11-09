"""Sealed PCM output samples are distinct from editorial milliseconds; no media runs."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

import app.audio_timeline as audio
from app.artifact_store import ArtifactStore
from app.creation.assembly_manifest import (
    AUDIO_ASSEMBLY_VERSION, AUDIO_ASSEMBLY_SAMPLE_CLOCK_VERSION, AudioAssemblyManifest, plan_audio_assembly,
)
from app.creation.contracts import JobRun
from app.host_resource_probes import CpuAudioBudget
from app.montage import creation_task_montage_plan_from_payload
from app.task_audio import TaskAudio, audio_mix_resources
from test_source_task_use_case import source_case as source_case


def graph(duration_ms):
    return audio.AudioTimeline(tracks=[audio.TimelineAudioTrack(id='original', name='original', role='original',
        clips=[audio.TimelineAudioClip(id='clip', name='clip', source_ref='workspace://source.mp4',
            source_start_ms=0, source_end_ms=duration_ms, timeline_start_ms=0)])])


def plan(duration_ms, sample_count=None):
    return plan_audio_assembly(graph(duration_ms), duration_ms, workspace_id='local', task_id='task',
        producer_job_id='job', source_hashes={'workspace://source.mp4':'a'*64}, max_input_count=1,
        output_sample_count=sample_count)


def budget():
    return CpuAudioBudget(1, 8_000_000, 10, 10, 48000, 2, 4_000_000, 1, 30,
        128*1024**2, 16, .05, 128*1024**2, 1_000_000)


@pytest.mark.parametrize('duration_ms,samples', [(9167, 440000), (102, 4800), (119, 6400)])
def test_exact_output_clock_keeps_editorial_ranges_and_all_units_sealed(tmp_path, duration_ms, samples):
    original = graph(duration_ms)
    before = original.model_dump()
    manifest = plan(duration_ms, samples)
    assert manifest.version == AUDIO_ASSEMBLY_SAMPLE_CLOCK_VERSION and manifest.sample_count == samples
    assert manifest.duration_ms == duration_ms and original.model_dump() == before
    assert manifest.units[0].graph.model_dump() == before
    decoded = AudioAssemblyManifest.from_payload(json.loads(json.dumps(manifest.payload())))
    assert decoded == manifest
    changed = plan(duration_ms, samples + 1)
    assert changed.recipe_digest != manifest.recipe_digest and changed.digest != manifest.digest
    assert all(a.id != b.id for a, b in zip(manifest.units, changed.units, strict=True))
    path = tmp_path / 'declared-source'
    path.write_bytes(b'opaque source, not decoded')
    command = audio.build_audio_mix_command(original, duration_ms / 1000, lambda _:path, tmp_path/'mix.wav',
        probe_duration=lambda _:duration_ms / 1000, output_sample_count=samples)
    filters = command[command.index('-filter_complex')+1]
    assert f'atrim=start=0.0:end={duration_ms / 1000}' in filters
    assert f'alimiter=limit=0.95:level=false:latency=true,apad=whole_len={samples},atrim=end_sample={samples}' in filters
    assert original.model_dump() == before
    for master, bytes_per_sample in ((True, 4), (False, 8)):
        bounds = audio.audio_mix_output_bounds(original, [], duration_ms/1000, False,
            apply_master=master, output_sample_count=samples)
        assert bounds['mix_output_bytes'] == samples * bytes_per_sample + 65536
        assert bounds['output_duration_limit_seconds'] == max(duration_ms/1000, samples/48000)


def test_v2_digest_and_payload_keep_the_committed_clock_contract():
    manifest = plan(9167)
    # Captured from the committed v2 planner before adding the explicit sample clock.
    assert manifest.digest == '71bdac88f4ea5ada5aa809070f5c353817fbfc6f1979d41fe4d68287d9691a72'
    assert manifest.recipe_digest == 'c53202969e1e7bf5f2ea06454f3a915842af66a8d242f8e43f75bd50344a97d1'
    assert [unit.id for unit in manifest.units] == [
        '018350bda9c6ebd0a799448cc67019c323b591d3aaa0d78ee2946f2cd1ff663d',
        'f4a98ffd7327c06e8095cbfc0087955483353c3726a226797f712714c9807a5e']
    assert manifest.version == AUDIO_ASSEMBLY_VERSION and manifest.sample_count == 440016
    payload = manifest.payload()
    assert 'output_sample_count' not in payload
    assert AudioAssemblyManifest.from_payload(json.loads(json.dumps(payload))) == manifest
    payload['output_sample_count'] = 440000
    with pytest.raises(ValueError, match='旧音频清单'):
        AudioAssemblyManifest.from_payload(payload)


@pytest.mark.parametrize('mutation', ['changed', 'missing', 'bool', 'zero'])
def test_output_clock_cannot_be_reinterpreted_without_its_original_digest(mutation):
    payload = json.loads(json.dumps(plan(9167, 440000).payload()))
    if mutation == 'missing':
        del payload['output_sample_count']
    else:
        payload['output_sample_count'] = {'changed':440001, 'bool':True, 'zero':0}[mutation]
    with pytest.raises(ValueError):
        AudioAssemblyManifest.from_payload(payload)


@pytest.fixture
def assembly_case(source_case, tmp_path):
    case = source_case
    receipt = case.use_case.create(replace(case.command, source_end_seconds=9.167), case.actor)
    montage = creation_task_montage_plan_from_payload(case.repo.get_creation_task_montage_plan('local', receipt['task_id']))
    job = case.repo.job_records.create_job_run(JobRun('sample-clock-job', 'local', receipt['task_id'],
        'creation_render', 'sample-clock-request', 'sample-clock-digest'))
    service = TaskAudio(case.repo, ArtifactStore(tmp_path/'artifacts'), tmp_path/'temporary')
    return case, service, montage, job


def test_original_job_seals_sample_clock_in_manifest_units_and_real_grant_facts(assembly_case):
    case, service, montage, job = assembly_case
    manifest = service.render_assembly(job, montage, actor=case.actor, budget=budget(), prepare=True,
        force_master=True, output_sample_count=440000)
    event_id = service._assembly_plan_id(job, montage)
    event = case.repo.execution_records.get_task_event('local', event_id)
    assert event.job_id == job.id and event.payload['manifest']['output_sample_count'] == 440000
    unit, facts = service.assembly_unit_facts('local', job.task_id, manifest, manifest.units[0].id,
        actor=case.actor, budget=budget(), job_id=job.id)
    assert unit.graph.model_dump() == montage.audio_timeline.model_dump()
    assert facts['resources']['output_sample_count'] == 440000
    assert facts['resources']['mix_output_bytes'] == 440000 * 8 + 65536
    assert facts['resources']['assembly_manifest_digest'] == manifest.digest
    # A later admission/read uses the original sealed manifest without guessing the picture duration again.
    assert service.render_assembly(job, montage, actor=case.actor, budget=budget()) == manifest
    before = deepcopy(event.payload)
    with pytest.raises(ValueError, match='原清单和产物保持不变'):
        service.render_assembly(job, montage, actor=case.actor, budget=budget(), prepare=True,
            force_master=True, output_sample_count=440016)
    assert case.repo.execution_records.get_task_event('local', event_id).payload == before
    assert case.repo.execution_records.list_artifact_records('local', job.task_id) == []


def test_old_manifest_is_read_only_and_mismatched_delivery_does_not_create_a_shadow_plan(assembly_case):
    case, service, montage, job = assembly_case
    old = service.render_assembly(job, montage, actor=case.actor, budget=budget(), prepare=True, force_master=True)
    original_id = service._assembly_plan_id(job, montage)
    with case.repo._connect() as conn:
        events_before = conn.execute('SELECT id,payload_json FROM task_events ORDER BY id').fetchall()
    with pytest.raises(ValueError, match='采样时钟'):
        service.render_assembly(job, montage, actor=case.actor, budget=budget(), prepare=True,
            force_master=True, output_sample_count=440000)
    assert service.render_assembly(job, montage, actor=case.actor, budget=budget(),
        output_sample_count=440016) == old
    assert service._assembly_plan_id(job, montage) == original_id
    with case.repo._connect() as conn:
        assert [tuple(row) for row in conn.execute('SELECT id,payload_json FROM task_events ORDER BY id')] == [tuple(row) for row in events_before]


def test_rounding_up_is_declared_in_actual_output_budget():
    limits = replace(budget(), max_output_duration_seconds=.12)
    with pytest.raises(ValueError, match='超出已标定'):
        audio_mix_resources([], [graph(119)], .119, normalize_voice_sources=False,
            budget=limits, output_sample_count=6400)


@pytest.mark.parametrize('declared,requested', [(None, 4800), (4800, None), (4800, 4801)])
def test_old_or_different_grant_cannot_authorize_a_new_output_clock(monkeypatch, declared, requested):
    resources = {'kind':'task_audio'}
    if declared is not None:
        resources['output_sample_count'] = declared
    monkeypatch.setattr(audio, '_audio_grant_resources', lambda *args:resources)
    monkeypatch.setattr(audio, '_run_granted_audio_command', lambda *a, **k:pytest.fail('no media before exact clock authorization'))
    with pytest.raises(PermissionError, match='原许可不一致'):
        audio.render_audio_mix(graph(102), .102, lambda _:None, None, scheduler=object(), grant=object(),
            output_sample_count=requested)


def test_pcm_output_is_measured_with_exact_stream_ticks_not_rounded_milliseconds(tmp_path, monkeypatch):
    path = tmp_path/'declared-output.wav'
    path.write_bytes(b'opaque output metadata protocol, not a WAV acceptance')
    resources = {'kind':'task_audio', 'output_duration_limit_seconds':10,
        'output_sample_rate_limit':48000, 'output_channels_limit':2}
    monkeypatch.setattr(audio, '_audio_grant_resources', lambda *args:resources)
    def measured(*args, **kwargs):
        return SimpleNamespace(stdout=json.dumps({'streams':[{'duration':'9.167', 'duration_ts':440000,
            'time_base':'1/48000', 'sample_rate':'48000', 'channels':2}]}))
    monkeypatch.setattr(audio, '_run_granted_audio_command', measured)
    result = audio.probe_audio_metadata(path, scheduler=object(), grant=object(), output=True,
        exact_output_clock=True)
    assert result['duration_seconds'] == 9.167 and result['sample_count'] == 440000
