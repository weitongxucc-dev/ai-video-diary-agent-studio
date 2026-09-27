import array
import math
import subprocess
import time
import wave

import pytest

from app.audio_timeline import AudioEditRequest, AudioTimeline, TimelineAudioClip, TimelineAudioTrack, apply_audio_edit, compile_audio_command, render_audio_mix
from app.host_resource_probes import HostProcessProbe


def tone(path, frequency):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=4",
                    "-c:a", "pcm_s16le", str(path)], check=True)


def samples(path, start, end):
    with wave.open(str(path)) as audio:
        assert audio.getframerate() == 48000
        audio.setpos(round(start * 48000))
        return list(array.array("h", audio.readframes(round((end-start)*48000))))[::audio.getnchannels()]


def amplitude(values, hz):
    real = sum(value * math.cos(2*math.pi*hz*index/48000) for index, value in enumerate(values))
    imag = sum(value * math.sin(2*math.pi*hz*index/48000) for index, value in enumerate(values))
    return math.hypot(real, imag) * 2 / len(values)


def test_actual_mix_preserves_both_tracks_and_solo_does_not_change_export(tmp_path):
    voice, music = tmp_path / "voice.wav", tmp_path / "music.wav"
    tone(voice, 440)
    tone(music, 660)
    timeline = AudioTimeline(tracks=[
        TimelineAudioTrack(id="voice", name="配音", role="narration", clips=[TimelineAudioClip(
            id="v", name="人声", source_ref="artifacts/voice", source_start_ms=0, source_end_ms=4000, timeline_start_ms=0)]),
        TimelineAudioTrack(id="music", name="音乐", role="music", clips=[TimelineAudioClip(
            id="m", name="音乐", source_ref="artifacts/music", source_start_ms=0, source_end_ms=2000,
            timeline_start_ms=1000, gain_db=-6, fade_out_ms=500)]),
    ])
    def resolver(ref):
        return {"artifacts/voice": voice, "artifacts/music": music}[ref]
    snapshot = timeline.model_dump()
    full = render_audio_mix(timeline, 4, resolver, tmp_path / "full.wav")
    both = samples(full, 1.2, 1.7)
    assert 2700 < amplitude(both, 440) < 3100  # Mono to stereo uses -3 dB per channel.
    assert .49 < amplitude(both, 660) / amplitude(both, 440) < .51
    assert amplitude(samples(full, .1, .6), 660) < 10
    assert amplitude(samples(full, 3.1, 3.6), 660) < 10
    solo = render_audio_mix(timeline, 4, resolver, tmp_path / "solo.wav", solo_track_ids={"music"})
    assert amplitude(samples(solo, 1.2, 1.7), 440) < 10
    assert timeline.model_dump() == snapshot
    export = render_audio_mix(timeline, 4, resolver, tmp_path / "export.wav")
    assert samples(export, 1.2, 1.7) == both


def test_audio_mix_probe_and_export_share_granted_process_runner(tmp_path):
    source = tmp_path / "voice.wav"
    tone(source, 440)
    timeline = AudioTimeline(tracks=[TimelineAudioTrack(
        id="voice", name="配音", role="narration", clips=[TimelineAudioClip(
            id="clip", name="人声", source_ref="artifacts/voice", source_start_ms=0,
            source_end_ms=1000, timeline_start_ms=0,
        )],
    )])

    class RecordingGrant:
        processes = HostProcessProbe()

        def __init__(self):
            self.children = []

        def assert_grant(self, grant):
            assert grant == "one-audio-grant"

        def register_child(self, grant, child):
            self.assert_grant(grant)
            self.children.append(child)

    scheduler = RecordingGrant()
    output = render_audio_mix(
        timeline, 1, lambda _ref: source, tmp_path / "mix.wav", scheduler=scheduler,
        grant="one-audio-grant", deadline_monotonic=time.monotonic() + 30,
        max_output_bytes=1024 * 1024, threads=1,
    )
    assert output.is_file()
    assert len(scheduler.children) == 2  # source ffprobe and bounded FFmpeg mix


def test_all_muted_tracks_produce_explicit_silence_with_requested_duration(tmp_path):
    output = render_audio_mix(AudioTimeline(), 2, lambda ref: pytest.fail("no input expected"), tmp_path / "silence.wav")
    assert max(map(abs, samples(output, 0, 2))) == 0
    with wave.open(str(output)) as audio:
        assert audio.getnframes() == 96000


def test_audio_ranges_and_unknown_fields_are_rejected():
    with pytest.raises(ValueError, match="淡入"):
        TimelineAudioClip(id="a", name="a", source_ref="artifacts/source", source_start_ms=0, source_end_ms=1000,
                          timeline_start_ms=0, fade_in_ms=600, fade_out_ms=600)
    with pytest.raises(ValueError):
        AudioTimeline(tracks=[], solo=True)


def test_split_keeps_original_source_and_fade_envelope(tmp_path):
    source = tmp_path / "source.wav"
    tone(source, 440)
    timeline = AudioTimeline(tracks=[TimelineAudioTrack(id="track",name="音乐",role="music",clips=[TimelineAudioClip(
        id="clip",name="音乐",source_ref="artifacts/source",source_start_ms=0,source_end_ms=4000,timeline_start_ms=0,fade_out_ms=2000)])])
    edit = AudioEditRequest(action="split",target_id="clip",values={"at_ms":3000})
    split = apply_audio_edit(timeline, edit, 4000)
    assert [(clip.source_start_ms,clip.source_end_ms) for clip in split.tracks[0].clips] == [(0,3000),(3000,4000)]
    before = render_audio_mix(timeline,4,lambda ref:source,tmp_path/"before.wav")
    after = render_audio_mix(split,4,lambda ref:source,tmp_path/"after.wav")
    for start in (2.2,2.7,3.2,3.5):
        assert amplitude(samples(after,start,start+.2),440) == pytest.approx(amplitude(samples(before,start,start+.2),440),rel=.06)
    assert timeline.tracks[0].clips[0].source_end_ms == 4000


def test_ducking_recovers_after_voice_and_rejects_source_overrun(tmp_path):
    voice,music=tmp_path/"voice.wav",tmp_path/"music.wav"
    tone(voice,440)
    tone(music,660)
    timeline=AudioTimeline(tracks=[
        TimelineAudioTrack(id="voice",name="配音",role="narration",clips=[TimelineAudioClip(id="v",name="v",source_ref="artifacts/voice",source_start_ms=0,source_end_ms=1000,timeline_start_ms=1000)]),
        TimelineAudioTrack(id="music",name="音乐",role="music",duck_under_voice=True,clips=[TimelineAudioClip(id="m",name="m",source_ref="artifacts/music",source_start_ms=0,source_end_ms=4000,timeline_start_ms=0)]),
    ])
    def resolver(ref):
        return voice if ref.endswith("voice") else music
    output=render_audio_mix(timeline,4,resolver,tmp_path/"duck.wav")
    before=amplitude(samples(output,.2,.5),660)
    during=amplitude(samples(output,1.3,1.6),660)
    recovered=amplitude(samples(output,3.4,3.7),660)
    assert during < before*.8
    assert recovered > during*1.4
    timeline.tracks[0].clips[0].source_end_ms=5000
    with pytest.raises(ValueError,match="实际文件时长"):
        render_audio_mix(timeline,7,resolver,tmp_path/"invalid.wav")


def test_natural_audio_gain_and_locked_track_share_exact_edit_rules():
    timeline=AudioTimeline(tracks=[TimelineAudioTrack(id="music",name="音乐",role="music")])
    edit=compile_audio_command("音乐小一点","music",timeline,"version",None)
    assert apply_audio_edit(timeline,edit,4000).tracks[0].gain_db == -3
    timeline.tracks[0].locked=True
    with pytest.raises(ValueError,match="已锁定"):
        apply_audio_edit(timeline,edit,4000)
    unlocked=apply_audio_edit(timeline,compile_audio_command("解锁音乐","music",timeline,"version",None),4000)
    assert unlocked.tracks[0].locked is False


def test_audio_artifact_cannot_cross_workspace_or_task_boundaries(tmp_path):
    import json
    from dataclasses import asdict
    from app.artifact_store import ArtifactStore
    from app.production import CreationTask, ProductionRepository
    from app.task_audio import TaskAudio
    repo=ProductionRepository(tmp_path/"db.sqlite",tmp_path/"library")
    for workspace,task_id in (("a","task-a"),("a","task-other"),("b","task-b")):
        task=CreationTask(id=task_id,workspace_id=workspace,plan_id=f"plan-{task_id}",name="音频边界测试",scenario="test")
        with repo._connect() as conn:
            conn.execute("INSERT INTO creation_tasks VALUES (?,?,?,?,?)",(task.id,workspace,task.plan_id,json.dumps(asdict(task),default=str),str(task.updated_at)))
    service=TaskAudio(repo,ArtifactStore(tmp_path/"output"),tmp_path/"temp")
    artifact=service.publish("a","task-a",kind="audio_source",parameters={},input_refs=[],produce=lambda path:tone(path,440))
    assert service.source("a","task-a",artifact.logical_ref).is_file()
    with pytest.raises(ValueError,match="不属于当前任务"):
        service.source("a","task-other",artifact.logical_ref)
    with pytest.raises(ValueError,match="不属于当前任务"):
        service.source("b","task-b",artifact.logical_ref)
    with pytest.raises(KeyError):
        service.source("b","task-a",artifact.logical_ref)
    with pytest.raises(ValueError):
        service.source("a","task-a","workspace://../outside.wav")


def test_denoise_reduces_background_noise_without_erasing_the_tone(tmp_path):
    source=tmp_path/"noisy.wav"
    subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","sine=frequency=440:duration=4",
        "-f","lavfi","-i","anoisesrc=color=white:seed=42:amplitude=0.04:duration=4",
        "-filter_complex","[0:a][1:a]amix=inputs=2:normalize=0","-c:a","pcm_s16le",str(source)],check=True)
    timeline=AudioTimeline(tracks=[TimelineAudioTrack(id="voice",name="人声",role="narration",clips=[TimelineAudioClip(
        id="v",name="声音",source_ref="artifacts/noisy",source_start_ms=0,source_end_ms=4000,timeline_start_ms=0)])])
    before=render_audio_mix(timeline,4,lambda ref:source,tmp_path/"before.wav")
    timeline.tracks[0].clips[0].denoise_db=12
    after=render_audio_mix(timeline,4,lambda ref:source,tmp_path/"after.wav")
    a,b=samples(before,2,3),samples(after,2,3)
    def residual(values):
        return math.sqrt(max(0,sum(value*value for value in values)/len(values)-amplitude(values,440)**2/2))
    assert .85 < amplitude(b,440)/amplitude(a,440) < 1.15
    assert residual(b) < residual(a)*.8


def test_split_automation_is_exposed_in_local_time_and_edits_keep_other_half():
    from app.audio_timeline import visible_gain_points
    from app.schemas import AudioTimelineResponse
    timeline=AudioTimeline(tracks=[TimelineAudioTrack(id="music",name="音乐",role="music",clips=[TimelineAudioClip(
        id="m",name="音乐",source_ref="artifacts/music",source_start_ms=0,source_end_ms=4000,timeline_start_ms=0,
        fade_out_ms=1000,gain_points=[{"offset_ms":0,"gain_db":0},{"offset_ms":4000,"gain_db":-12}])])])
    split=apply_audio_edit(timeline,AudioEditRequest(action="split",target_id="m",values={"at_ms":2000}),4000)
    right=split.tracks[0].clips[1]
    assert [(p.offset_ms,p.gain_db) for p in visible_gain_points(right)] == [(0,-6),(2000,-12)]
    response=AudioTimelineResponse.model_validate(split.model_dump()).model_dump()
    assert AudioTimelineResponse.model_validate(response).model_dump() == response
    assert response["tracks"][0]["clips"][1]["editor_gain_points"] == [{"offset_ms":0,"gain_db":-6},{"offset_ms":2000,"gain_db":-12}]
    edited=apply_audio_edit(split,AudioEditRequest(action="update",target_id=right.id,values={"gain_points":[{"offset_ms":0,"gain_db":-3},{"offset_ms":2000,"gain_db":-9}]}),4000)
    assert edited.tracks[0].clips[0] == split.tracks[0].clips[0]
    assert edited.tracks[0].clips[1].gain_points[0].offset_ms == 2000
    assert edited.tracks[0].clips[1].fade_out_ms == 1000


def test_fade_out_preview_covers_the_changed_tail():
    from app.audio_timeline import narration_audio_timeline,audio_edit_preview_range
    before=narration_audio_timeline("narration://voice",46)
    edit=AudioEditRequest(action="update",target_id="audio-narration-main",values={"fade_out_ms":2000})
    after=apply_audio_edit(before,edit,46000)
    start,end=audio_edit_preview_range(before,after,edit,46)
    assert start <= 44 and end == 46
    assert end-start <= 10


def test_regenerated_voice_keeps_effects_but_rejects_structural_edits():
    from app.audio_timeline import narration_audio_timeline,rebind_narration_audio
    original=narration_audio_timeline("narration://old",4)
    original.tracks[0].clips[0].gain_db=-3
    updated=rebind_narration_audio(original,"narration://old","narration://new",4000,4500)
    assert updated.tracks[0].clips[0].source_end_ms == 4500
    assert updated.tracks[0].clips[0].gain_db == -3
    assert original.tracks[0].clips[0].source_ref == "narration://old"
    split=apply_audio_edit(original,AudioEditRequest(action="split",target_id="audio-narration-main",values={"at_ms":2000}),4000)
    with pytest.raises(ValueError,match="已分割或移动"):
        rebind_narration_audio(split,"narration://old","narration://new",4000,4500)


def test_loudness_processing_changes_signal_and_keeps_sample_duration(tmp_path):
    from app.audio_timeline import narration_audio_timeline
    source=tmp_path/"quiet.wav"
    tone(source,440)
    graph=narration_audio_timeline("narration://voice",4)
    before=render_audio_mix(graph,4,lambda ref:source,tmp_path/"before.wav")
    graph.tracks[0].clips[0].loudness_target_lufs=-16
    after=render_audio_mix(graph,4,lambda ref:source,tmp_path/"after.wav")
    assert amplitude(samples(after,1,2),440) > amplitude(samples(before,1,2),440)*1.3
    with wave.open(str(after)) as audio:
        assert audio.getnframes() == 192000


def test_duplicate_preserves_source_and_effects_and_rejects_overrun():
    from app.audio_timeline import narration_audio_timeline
    timeline=narration_audio_timeline("narration://voice",4)
    clip=timeline.tracks[0].clips[0]
    clip.source_end_ms=2000
    clip.fade_out_ms=500
    edit=compile_audio_command("复制这个音频","audio-narration-main",timeline,"version",None)
    result=apply_audio_edit(timeline,edit,4000)
    duplicate=result.tracks[0].clips[1]
    assert duplicate.id != clip.id
    assert duplicate.timeline_start_ms == 2000
    assert duplicate.source_ref == clip.source_ref and duplicate.source_start_ms == 0
    assert duplicate.fade_out_ms == 500
    assert len(timeline.tracks[0].clips) == 1
    with pytest.raises(ValueError,match="超出成片"):
        apply_audio_edit(timeline,edit,3000)


def test_loudness_preserves_silence_and_can_encode_aac(tmp_path):
    from app.audio_timeline import narration_audio_timeline
    source=tmp_path/"silent.wav"
    render_audio_mix(AudioTimeline(),1,lambda ref:None,source)
    timeline=narration_audio_timeline("narration://silence",1)
    timeline.tracks[0].clips[0].loudness_target_lufs=-16
    mixed=render_audio_mix(timeline,1,lambda ref:source,tmp_path/"normalized.wav")
    assert max(map(abs,samples(mixed,0,1))) == 0
    subprocess.run(["ffmpeg","-v","error","-i",str(mixed),"-c:a","aac",str(tmp_path/"audio.m4a")],check=True)


def test_compound_audio_parameters_share_one_patch_and_cover_both_fades():
    from app.audio_timeline import narration_audio_timeline,audio_edit_preview_range
    timeline=narration_audio_timeline('artifacts/voice.wav',20)
    edit=compile_audio_command('当前配音音量降低3分贝，然后淡入1秒，淡出2秒，画面不变','audio-narration-main',timeline,'version',None)
    assert edit.values=={'gain_db':-3,'fade_in_ms':1000,'fade_out_ms':2000}
    after=apply_audio_edit(timeline,edit,20000)
    assert after.tracks[0].clips[0].gain_db==-3
    assert audio_edit_preview_range(timeline,after,edit,20)==(0,20)
    assert timeline.tracks[0].clips[0].gain_db==0
    ordered=compile_audio_command('音量设为-6分贝，提高3分贝','audio-narration-main',timeline,'version',None)
    assert ordered.values=={'gain_db':-3}
    assert compile_audio_command('淡入1秒和淡出2秒','audio-narration-main',timeline,'version',None).values=={'fade_in_ms':1000,'fade_out_ms':2000}
    for command in ('音量小一点，删除字幕','静音但保留原声','解锁音乐','音量降低3分贝，然后加混响'):
        with pytest.raises(ValueError):
            compile_audio_command(command,'audio-narration-main',timeline,'version',None)

@pytest.mark.parametrize('use_state', ['reference_only', 'excluded'])
def test_extracted_audio_obeys_updated_source_material_restriction(tmp_path, use_state, monkeypatch):
    import json
    from dataclasses import asdict
    from app.artifact_store import ArtifactStore
    from app.models import utc_now
    from app.production import CreationTask, MaterialAsset, ProductionRepository
    from app.task_audio import TaskAudio
    repo = ProductionRepository(tmp_path/'db.sqlite', tmp_path/'library')
    task = CreationTask(id='task', workspace_id='workspace', plan_id='plan', name='原声', scenario='test')
    with repo._connect() as conn:
        conn.execute('INSERT INTO creation_tasks VALUES (?,?,?,?,?)',
            (task.id, task.workspace_id, task.plan_id, json.dumps(asdict(task), default=str), str(task.updated_at)))
    source = repo.video_library_path/'source.wav'
    source.parent.mkdir(parents=True)
    tone(source, 440)
    asset = MaterialAsset(id='asset', workspace_id=task.workspace_id, file_name=source.name,
        path=str(source.resolve()), size_bytes=source.stat().st_size, modified_at=utc_now())
    repo._save_asset(asset)
    reference = repo.workspace_media_reference_for_asset(task.workspace_id, asset.id)
    with repo._connect() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id=task.workspace_id, reference=reference)
    service = TaskAudio(repo, ArtifactStore(tmp_path/'output'), tmp_path/'temp')
    extracted = service.publish(task.workspace_id, task.id, kind='audio_source', parameters={},
        input_refs=[reference], produce=lambda path: tone(path, 440))
    processed = service.publish(task.workspace_id, task.id, kind='audio_source', parameters={},
        input_refs=[extracted.logical_ref], produce=lambda path: tone(path, 440))
    assert service.source(task.workspace_id, task.id, processed.logical_ref).is_file()
    repo.update_asset_use_state(task.workspace_id, asset.id, use_state)
    for ref in (reference, extracted.logical_ref, processed.logical_ref):
        with pytest.raises(ValueError, match='使用状态'):
            service.source(task.workspace_id, task.id, ref)
    from types import SimpleNamespace
    from app import main as main_module
    monkeypatch.setattr(main_module, 'production_repo', repo)
    monkeypatch.setattr(main_module, '_task_audio', lambda:service)
    timeline = AudioTimeline(tracks=[TimelineAudioTrack(id='original', name='原声', role='original', clips=[TimelineAudioClip(
        id='extracted', name='镜头原声', source_ref=processed.logical_ref,
        source_start_ms=0, source_end_ms=4000, timeline_start_ms=0)])])
    plan = SimpleNamespace(task_id=task.id, clips=[], audio_timeline=timeline)
    with pytest.raises(ValueError, match='使用状态'):
        main_module._validate_montage_material_use(task.workspace_id, plan)
    repo.update_asset_use_state(task.workspace_id, asset.id, 'available')
    main_module._validate_montage_material_use(task.workspace_id, plan)
    assert service.source(task.workspace_id, task.id, processed.logical_ref).is_file()
