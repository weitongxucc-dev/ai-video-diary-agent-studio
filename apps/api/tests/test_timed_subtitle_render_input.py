"""Subtitle timing/input topology checks; never launch a media process."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import json
import re
import time

import pytest
from PIL import Image

import app.montage as montage
from app.host_resource_probes import HostCapacity
from app.creation.contracts import SubtitleCue, SubtitleTrack
from app.subtitle_schedule import TimedCaption, subtitle_schedule


@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Structural subtitle tests must never launch media")
    monkeypatch.setattr(montage.subprocess, "run", forbidden)
    monkeypatch.setattr(montage.subprocess, "Popen", forbidden)


def manifest_entries(path):
    entries = []
    for line in path.read_text().splitlines():
        if line.startswith("file "):
            entries.append([path.parent / line[5:], None])
        elif line.startswith("duration "):
            entries[-1][1] = Decimal(line[9:])
        elif line.startswith("outpoint "):
            pass
        elif line != "ffconcat version 1.0":
            assert line in ("option framerate 30", "option loop 1")
    return entries


def plan_for(tmp_path, seconds):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"structural fixture only, not a playable video")
    return montage.CreationTaskMontagePlan(
        id="plan", workspace_id="fixture", task_id="task", script_id="script",
        script_title="双语字幕", status="ready",
        cues=[montage.NarrationCue("cue", "section", "合成字幕", seconds)],
        clips=[montage.MontageClip("clip", "cue", "asset", "segment", str(source),
                                  0, seconds, 0, seconds, 1, [], False)],
        duration_seconds=seconds, low_confidence_clip_count=0, aspect_ratio="9:16",
    )


def bilingual_tracks(seconds, *, enabled=True):
    chinese = SubtitleTrack(
        id="zh", workspace_id="fixture", task_id="task", timeline_version_id="version",
        language="zh-CN", source_language=None,
        style={"enabled": enabled, "position": "bottom", "primary_color": "#ABCDEF"},
        cues=[SubtitleCue(f"cue-{i}", None, f"合成字幕 {i}", i, min(seconds, i + 4))
              for i in range(0, seconds, 4)],
        generation_method="timeline", model_id=None, artifact_ref=None, transform_id=None,
        status="valid",
    )
    english = replace(chinese, id="en", language="en-US", source_language="zh-CN",
                      cues=[replace(cue, text=f"Synthetic caption {i}")
                            for i, cue in enumerate(chinese.cues)])
    return [chinese, english]


def capture_render(monkeypatch, tmp_path, plan, tracks, granted_media):
    captured = {}
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"structural fixture only, not playable audio")
    monkeypatch.setattr(montage, "sample_host_capacity", lambda **kwargs: HostCapacity(
        cpu_count=8, cpu_threads_ceiling=7, available_memory_bytes=8 * 1024**3, load_avg_1m=.5))

    def run(scheduler_arg, grant_arg, command, **kwargs):
        assert scheduler_arg is scheduler and grant_arg is grant
        if command[0] == "ffprobe":
            return SimpleNamespace(stdout=str(plan.duration_seconds).encode(), returncode=0)
        if "-an" in command:
            Path(command[-1]).write_bytes(b"structural picture placeholder, not rendered media")
            return SimpleNamespace(stdout=b"",returncode=0)
        captured["command"] = command
        inputs = [Path(command[i + 1]) for i, value in enumerate(command) if value == "-i"]
        sequence = [path for path in inputs if path.name == "subtitles.ffconcat"]
        captured["entries"] = manifest_entries(sequence[0]) if sequence else []
        captured["alpha_extrema"] = []
        captured["subtitle_sizes"] = set()
        for path, duration in captured["entries"]:
            with Image.open(path) as image:
                assert image.mode == "RGBA"
                captured["subtitle_sizes"].add(image.size)
                captured["alpha_extrema"].append(image.getchannel("A").getextrema())
        captured["sidecar"] = (Path(command[-1]).parent / "subtitles.srt").read_text()
        Path(command[-1]).write_bytes(b"structural placeholder, not a rendered result")
        return SimpleNamespace(stdout=b"", returncode=0)

    monkeypatch.setattr(montage, "_run_granted_media_command", run)
    monkeypatch.setattr(montage, "_validate_rendered_media", lambda *args, **kwargs: None)
    monkeypatch.setattr(montage, "probe_preview_video", lambda *args, **kwargs: None)
    monkeypatch.setattr(montage, "_probe_serial_picture_input", lambda *args, **kwargs: None)
    paths = [audio, *(Path(clip.source_path) for clip in plan.clips)]
    with granted_media(paths, kind='creation_render') as context:
        scheduler, grant = context.scheduler, context.grant
        montage.MontageRenderer(tmp_path / 'unused').render(
            plan, audio, job_staging_path=tmp_path / 'job', subtitle_tracks=tracks,
            scheduler=scheduler, grant=grant, threads=1, max_output_bytes=context.max_output_bytes,
            deadline_monotonic=context.deadline_monotonic)
    return captured


@pytest.mark.parametrize("seconds", [20, 120])
def test_real_creation_plan_uses_one_subtitle_input_at_both_durations(tmp_path, monkeypatch, seconds, granted_media):
    tracks = bilingual_tracks(seconds)
    schedule = subtitle_schedule(tracks, seconds)
    assert len(schedule) == seconds // 4
    assert schedule[0].text == "合成字幕 0\nSynthetic caption 0"
    assert schedule[0].subtitle_style["primary_color"] == "#ABCDEF"
    captured = capture_render(monkeypatch, tmp_path, plan_for(tmp_path, seconds), tracks, granted_media)
    command = captured["command"]
    inputs = [i for i, value in enumerate(command) if value == "-i"]
    assert len(inputs) == 3  # source, authorized narration, one timed subtitle stream
    assert sum(command[i + 1].endswith(".ffconcat") for i in inputs) == 1
    assert "-loop" not in command
    assert all(command[i - 2:i] == ["-threads", "1"] for i in inputs)
    assert command[command.index("-filter_complex_threads") + 1] == "1"
    assert command[command.index("-threads:v") + 1] == "1"
    assert command[command.index("-fs") + 1] == str(32 * 1024**2)
    graph = command[command.index("-filter_complex") + 1]
    assert "[2:v]setpts=PTS-STARTPTS,format=rgba[subtitleclock]" in graph
    assert graph.count("fps=30") == 1  # only the existing main-picture clock
    assert "eof_action=pass:repeatlast=0:shortest=0" in graph
    assert len(captured["subtitle_sizes"]) == 1
    width, height = next(iter(captured["subtitle_sizes"]))
    assert width % 2 == height % 2 == 0
    assert width * height < 1080 * 1920 / 2
    position = re.search(r"\[subtitleclock\]overlay=(\d+):(\d+):", graph)
    assert position and all(int(value) % 2 == 0 for value in position.groups())
    entries = captured["entries"]
    assert sum(duration for path, duration in entries[:-1]) == seconds
    assert entries[-1][1] is None and captured["alpha_extrema"][-1] == (0, 0)
    assert all(high > 0 for low, high in captured["alpha_extrema"][:-1])
    assert "合成字幕 0" in captured["sidecar"] and "Synthetic caption 0" in captured["sidecar"]


def test_initial_two_shot_caption_uses_five_second_narration_but_explicit_track_keeps_its_end(tmp_path, monkeypatch, granted_media):
    plan = plan_for(tmp_path, 5)
    first = replace(plan.clips[0], source_end_seconds=2.5, timeline_end_seconds=2.5)
    second = replace(first, id="clip-2", timeline_start_seconds=2.5, timeline_end_seconds=5.)
    plan.clips = [first, second]
    initial = capture_render(monkeypatch, tmp_path, plan, None, granted_media)
    assert "00:00:00,000 --> 00:00:05,000\n合成字幕" in initial["sidecar"]
    # Current authored tracks have their own clock, even when it is shorter than narration.
    explicit = replace(bilingual_tracks(5)[0], cues=[SubtitleCue("manual", None, "用户设置", 0., 2.5)])
    rendered = capture_render(monkeypatch, tmp_path, replace(plan, status="ready"), [explicit], granted_media)
    assert "00:00:00,000 --> 00:00:02,500\n用户设置" in rendered["sidecar"]
    assert "00:00:05,000" not in rendered["sidecar"]
    assert any(high == 0 and duration is not None for (path, duration), (low, high)
        in zip(rendered["entries"], rendered["alpha_extrema"], strict=True))


def test_transparent_gaps_fractional_edges_and_simultaneous_positions_keep_frame_visibility(tmp_path):
    # Distinct alpha colors make both order and transparency observable without text/font fixtures.
    schedule = [TimedCaption(.05, .21, "bottom", {"position": "bottom"}),
                TimedCaption(.10, .27, "top", {"position": "top"}),
                TimedCaption(.40, .47, "last", {"position": "bottom"})]
    colors = [(255, 0, 0, 128), (0, 0, 255, 128), (0, 255, 0, 255)]
    paths = []
    for index, color in enumerate(colors):
        path = tmp_path / f"subtitle-{index:03}.png"
        Image.new("RGBA", (2, 2), color).save(path)
        paths.append(path)
    manifest, offset = montage._write_timed_subtitle_input(schedule, paths, tmp_path,
                                                  size=(2, 2), duration_seconds=.5)
    assert offset == (0, 0)
    entries = manifest_entries(manifest)
    cursor = Decimal(0)
    timeline = []
    for path, duration in entries[:-1]:
        first = round(cursor * 30)
        cursor += duration
        timeline.append((first, round(cursor * 30), path))
    assert cursor == Decimal(".5")
    for frame in range(15):
        expected = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
        for cue, color in zip(schedule, colors):
            if float(f"{cue.start_seconds:.6f}") <= frame / 30 < float(f"{cue.end_seconds:.6f}"):
                expected.alpha_composite(Image.new("RGBA", (2, 2), color))
        path = next(path for first, last, path in timeline if first <= frame < last)
        with Image.open(path) as actual:
            assert actual.tobytes() == expected.tobytes(), f"frame {frame}"
    with Image.open(entries[-1][0]) as terminal:
        assert terminal.getchannel("A").getextrema() == (0, 0)


def test_first_and_last_picture_frames_and_many_short_intervals_do_not_drift(tmp_path):
    path = tmp_path / "subtitle-000.png"
    Image.new("RGBA", (2, 2), (255, 255, 255, 255)).save(path)
    schedule = [TimedCaption(frame / 30, (frame + 1) / 30, "text", {}) for frame in range(3600)]
    manifest, offset = montage._write_timed_subtitle_input(schedule, [path] * len(schedule), tmp_path,
                                                  size=(2, 2), duration_seconds=120)
    assert offset == (0, 0)
    entries = manifest_entries(manifest)
    assert entries[0][0] == path and entries[-2][0] == path
    cursor = Decimal(0)
    for image, duration in entries[:-1]:
        assert image == path
        # Six-decimal enable boundaries can assign one or two frames to a cue;
        # they must still tile the whole clock, with no cumulative time drift.
        assert abs(cursor * 30 - round(cursor * 30)) < Decimal(".00002")
        cursor += duration
    assert cursor == 120
    assert entries[-1][0].name == "subtitle-transparent.png"


@pytest.mark.parametrize("frames", [1, 2, 7, 120, 3600])
def test_single_demuxer_emits_bounded_cadence_packets_until_the_exact_frame_end(tmp_path, frames):
    path = tmp_path / "subtitle-000.png"
    Image.new("RGBA", (2, 2), (255, 255, 255, 255)).save(path)
    cue = TimedCaption(0, frames / 30, "text", {})
    manifest, offset = montage._write_timed_subtitle_input([cue], [path], tmp_path,
                                                  size=(2, 2), duration_seconds=frames / 30)
    assert offset == (0, 0)
    text = manifest.read_text()
    assert text.count("option loop 1") == 1
    outpoint = Decimal(next(line[9:] for line in text.splitlines() if line.startswith("outpoint ")))
    # PNG has PTS=DTS. Concat's exclusive outpoint must keep exactly these
    # packets, with no four-second lookahead gap and no extra boundary frame.
    kept = [frame for frame in range(frames + 1) if Decimal(frame) / 30 < outpoint]
    assert kept == list(range(frames))
    assert text.endswith("file subtitle-transparent.png\noption framerate 30\n")
    assert round(sum(duration for path, duration in manifest_entries(manifest)[:-1]) * 30) == frames


def test_graphics_keep_their_clock_below_subtitles_and_hidden_tracks_have_no_subtitle_input(tmp_path, monkeypatch, granted_media):
    plan = plan_for(tmp_path, 20)
    plan.graphic_overlays = [{"kind": "arrow", "name": "图形", "x": .5, "y": .5,
                             "start_seconds": 2, "end_seconds": 6}]
    # Graphic drawing is unchanged; only its existing input/order/clock is under review.
    monkeypatch.setattr(montage, "write_graphic_overlay",
                        lambda value, path, size: Image.new("RGBA", size).save(path))
    visible = capture_render(monkeypatch, tmp_path, plan, bilingual_tracks(20), granted_media)
    command = visible["command"]
    inputs = [command[i + 1] for i, value in enumerate(command) if value == "-i"]
    assert len(inputs) == 4 and inputs[2].endswith("graphic-0.png") and inputs[3].endswith(".ffconcat")
    assert command.count("-loop") == 1
    graph = command[command.index("-filter_complex") + 1]
    assert "gte(t,2.000000)*lt(t,6.000000)" in graph
    assert "[vsub0][subtitleclock]overlay" in graph
    hidden = capture_render(monkeypatch, tmp_path, replace(plan, status="ready"),
                            bilingual_tracks(20, enabled=False), granted_media)
    assert sum(value == "-i" for value in hidden["command"]) == 3
    assert not hidden["entries"]
    assert "subtitleclock" not in hidden["command"][hidden["command"].index("-filter_complex") + 1]
    assert "合成字幕 0" in hidden["sidecar"]  # hidden display still exports the current sidecar


@pytest.mark.parametrize("english_position", ["bottom", "top"])
@pytest.mark.parametrize("background_opacity", [0, .6])
def test_cropped_real_bilingual_captions_restore_exact_rgba_at_every_picture_frame(tmp_path, english_position, background_opacity):
    size = (640, 960)
    chinese, english = bilingual_tracks(8)
    chinese = replace(chinese, style={**chinese.style, "stroke_width": 5,
                                     "background_color": "#446688", "background_opacity": background_opacity}, cues=[
        replace(chinese.cues[0], start_seconds=.05, end_seconds=2.2, emphasis=["合成"]),
        replace(chinese.cues[1], start_seconds=4.3, end_seconds=7.7),
    ])
    english = replace(english, style={**english.style, "position": english_position}, cues=[
        replace(english.cues[0], start_seconds=.10, end_seconds=2.7, emphasis=["Synthetic"]),
        replace(english.cues[1], start_seconds=4.7, end_seconds=7.4),
    ])
    schedule = subtitle_schedule([chinese, english], 8)
    if english_position == "bottom":
        assert any("合成字幕" in cue.text and "Synthetic caption" in cue.text for cue in schedule)
    paths, original = [], []
    for index, cue in enumerate(schedule):
        path = tmp_path / f"subtitle-{index:03}.png"
        montage._write_subtitle_image(cue, path, size=size)
        with Image.open(path) as image:
            original.append(image.copy())
        paths.append(path)
    manifest, offset = montage._write_timed_subtitle_input(schedule, paths, tmp_path,
                                                          size=size, duration_seconds=8)
    assert all(value % 2 == 0 for value in offset)
    entries = manifest_entries(manifest)
    cursor = Decimal(0)
    timeline, common_size = [], None
    for path, duration in entries:
        with Image.open(path) as image:
            if common_size is None:
                common_size = image.size
            assert image.size == common_size
            restored = Image.new("RGBA", size)
            restored.paste(image, offset)  # copy RGBA, without applying alpha twice
        first = round(cursor * 30)
        if duration is None:
            assert first == 240 and restored.tobytes() == Image.new("RGBA", size).tobytes()
        else:
            cursor += duration
            timeline.append((first, round(cursor * 30), restored))
    assert common_size[0] < size[0] or common_size[1] < size[1]
    for frame in range(240):
        expected = Image.new("RGBA", size)
        active = [layer for cue, layer in zip(schedule, original, strict=True)
                  if float(f"{cue.start_seconds:.6f}") <= frame / 30 < float(f"{cue.end_seconds:.6f}")]
        if len(active) == 1:
            expected = active[0]  # the original single-cue stream copies PNG bytes directly
        else:
            for layer in active:
                expected.alpha_composite(layer)
        restored = next(image for first, last, image in timeline if first <= frame < last)
        assert restored.tobytes() == expected.tobytes(), f"frame {frame}"


@pytest.mark.parametrize("mode", ["odd_edges", "canvas_edges", "transparent"])
def test_common_crop_keeps_low_alpha_edges_and_never_clips_full_canvas(tmp_path, mode):
    size = (20, 18)
    first, second = Image.new("RGBA", size), Image.new("RGBA", size)
    if mode == "odd_edges":
        first.putpixel((3, 5), (200, 100, 50, 1))  # faint outer stroke/shadow counts
        second.putpixel((12, 10), (80, 90, 100, 255))
        expected_size, expected_offset = (12, 8), (2, 4)
    elif mode == "canvas_edges":
        first.putpixel((0, 0), (1, 2, 3, 255))
        second.putpixel((19, 17), (20, 30, 40, 1))
        expected_size, expected_offset = size, (0, 0)
    else:
        expected_size, expected_offset = size, (0, 0)
    originals = [first.copy(), second.copy()]
    paths = []
    for index, image in enumerate(originals):
        path = tmp_path / f"subtitle-{index:03}.png"
        image.save(path)
        paths.append(path)
    schedule = [TimedCaption(0, .2, "first", {}), TimedCaption(.3, .4, "second", {})]
    manifest, offset = montage._write_timed_subtitle_input(schedule, paths, tmp_path,
                                                          size=size, duration_seconds=.5)
    assert offset == expected_offset
    for path, original in zip(paths, originals, strict=True):
        with Image.open(path) as cropped:
            assert cropped.size == expected_size
            restored = Image.new("RGBA", size)
            restored.paste(cropped, offset)
            assert restored.tobytes() == original.tobytes()
    for path, duration in manifest_entries(manifest):
        with Image.open(path) as image:
            assert image.size == expected_size


@pytest.mark.parametrize("ends, expected_frames", [([2 / 30], [2]), ([.051, .103], [2, 1])])
def test_creation_output_endpoint_matches_absolute_picture_frames_and_audio_padding(tmp_path, ends, expected_frames):
    plan = plan_for(tmp_path, ends[-1])
    starts = [0, *ends[:-1]]
    plan.clips = [replace(plan.clips[0], id=f"clip-{i}", source_start_seconds=start,
                          source_end_seconds=end, timeline_start_seconds=start, timeline_end_seconds=end)
                  for i, (start, end) in enumerate(zip(starts, ends, strict=True))]
    command = montage.MontageRenderer(tmp_path).build_command(plan, tmp_path / "voice.wav",
                                                            tmp_path / "preview.mp4", [], subtitle_intervals=[])
    graph = command[command.index("-filter_complex") + 1]
    frame_counts = [int(value) for value in re.findall(r"trim=end_frame=(\d+)", graph)]
    assert frame_counts == expected_frames
    last_input = max(index for index, value in enumerate(command) if value == "-i")
    output_options = command[last_input + 2:]
    end = Decimal(output_options[output_options.index("-t") + 1])
    assert end == Decimal(f"{sum(frame_counts) / 30:.10f}")
    assert command[command.index("-af") + 1].endswith(f"apad=whole_dur={end}")
    assert "-shortest" not in output_options
    assert command[command.index("-crf") + 1] == "20"
    assert command[command.index("-preset") + 1] == "veryfast"


def test_legacy_output_keeps_shortest_policy(tmp_path):
    task_plan = plan_for(tmp_path, 20)
    legacy = montage.MontagePlan(id="legacy", workspace_id="fixture", batch_id="batch", episode_id="episode",
                                 script_title="旧路径", status="ready", cues=task_plan.cues,
                                 clips=task_plan.clips, duration_seconds=20, low_confidence_clip_count=0)
    command = montage.MontageRenderer(tmp_path).build_command(legacy, tmp_path / "voice.wav",
                                                            tmp_path / "preview.mp4", [], subtitle_intervals=[])
    last_input = max(index for index, value in enumerate(command) if value == "-i")
    output_options = command[last_input + 2:]
    assert "-shortest" in output_options and "-t" not in output_options
    assert command[command.index("-af") + 1].endswith("apad=whole_dur=20.0000000000")


@pytest.mark.parametrize("short_stream", ["video", "audio"])
def test_individual_short_stream_is_rejected_even_when_container_duration_is_long(tmp_path, monkeypatch, short_stream):
    output = tmp_path / "preview.mp4"
    output.write_bytes(b"metadata fixture only, not playable media")
    streams = [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "duration": "20"},
               {"codec_type": "audio", "codec_name": "aac", "duration": "20"}]
    next(stream for stream in streams if stream["codec_type"] == short_stream)["duration"] = "19.89"
    metadata = {"streams": streams, "format": {"duration": "999"}}
    def probe(scheduler, grant, command, **kwargs):
        assert command[0] == "ffprobe"
        return SimpleNamespace(returncode=0, stdout=json.dumps(metadata).encode(), stderr=b"")
    monkeypatch.setattr(montage, "_run_granted_media_command", probe)
    with pytest.raises(OSError, match="时长不足"):
        montage._validate_rendered_media(output, expected_duration_seconds=20, scheduler=object(), grant=object(),
                                        deadline_monotonic=time.monotonic() + 30, cancel_event=None)


@pytest.mark.parametrize("failure", [None,"disk","late","source"])
def test_serial_picture_commands_keep_clock_and_one_grant_with_cumulative_temp_cap(tmp_path,monkeypatch,failure,granted_media):
    plan=plan_for(tmp_path,2)
    first=replace(plan.clips[0],source_end_seconds=1,timeline_end_seconds=1,
        visual_effects={"exposure":.5,"fade_out_seconds":.2},fade_envelope_duration_frames=60,fade_envelope_offset_frames=0)
    second=replace(first,id="second",source_start_seconds=1,source_end_seconds=2,
        timeline_start_seconds=1,timeline_end_seconds=2,fade_envelope_offset_frames=30)
    plan.clips=[first,second]
    root=tmp_path/"job"
    root.mkdir()
    output=root/"preview.mp4"
    (root/"timeline-mix.wav").write_bytes(b"a"*20)
    calls=[]
    with granted_media([root/'timeline-mix.wav', *(Path(clip.source_path) for clip in plan.clips)], kind='creation_render') as context:
        scheduler, grant = context.scheduler, context.grant
        deadline = context.deadline_monotonic
        def run(current_scheduler,current_grant,command,**kwargs):
            assert current_scheduler is scheduler and current_grant is grant
            assert kwargs["deadline_monotonic"]==deadline
            if command[0] == 'ffprobe':
                calls.append(command)
                return SimpleNamespace(returncode=0, stdout=json.dumps({'format': {'format_name': 'nut'}, 'streams': [{
                    'codec_type': 'video', 'codec_name': 'ffv1', 'pix_fmt': 'yuv420p', 'width': 1080,
                    'height': 1920, 'r_frame_rate': '30/1', 'nb_read_frames': '30'}]}).encode())
            assert kwargs["max_output_file_bytes"]==1000
            assert root/"timeline-mix.wav" in kwargs["output_paths"]
            calls.append(command)
            Path(command[-1]).write_bytes(b"x"*(1001 if failure=="disk" else 30))
            return SimpleNamespace(returncode=0,stdout=b"",stderr=b"")
        monkeypatch.setattr(montage,"_run_granted_media_command",run)
        if failure=="late":
            deadline=time.monotonic()-1
        if failure=="source":
            first.source_path=str(tmp_path/'missing.mp4')
        renderer=montage.MontageRenderer(tmp_path)
        if failure in {"disk","source"}:
            with pytest.raises(ValueError):
                renderer._render_serial_picture_input(plan,root/'pieces',output,1000,
                    scheduler=scheduler,grant=grant,cancel_event=None,threads=1,deadline_monotonic=deadline)
            return
        if failure=="late":
            # Deadline refusal belongs to the unchanged granted runner, not a new per-clip timer.
            from app.granted_subprocess import GrantedCommandTimeout
            monkeypatch.setattr(montage,"_run_granted_media_command",lambda *args,**kwargs:
                (_ for _ in ()).throw(GrantedCommandTimeout("same deadline expired")))
            with pytest.raises(GrantedCommandTimeout):
                renderer._render_serial_picture_input(plan,root/'pieces',output,1000,
                    scheduler=scheduler,grant=grant,cancel_event=None,threads=1,deadline_monotonic=deadline)
            return
        manifest=renderer._render_serial_picture_input(plan,root/'pieces',output,1000,scheduler=scheduler,
            grant=grant,cancel_event=None,threads=1,deadline_monotonic=deadline)
        assert manifest.read_text().splitlines()==["ffconcat version 1.0","file clip-000.nut","duration 1.0000000000","file clip-001.nut","duration 1.0000000000"]
        commands=[item for item in calls if isinstance(item,list) and item[0]=='ffmpeg']
        assert len([item for item in calls if isinstance(item,list) and item[0]=='ffprobe']) == 2
        assert len(commands)==2 and all(command.count('-i')==1 for command in commands)
        for clip,command in zip(plan.clips,commands,strict=True):
            assert command[command.index('-c:v')+1]=='ffv1' and '-an' in command
            assert command[command.index('-f')+1]=='nut'
            assert command[command.index('-g')+1]=='1'
            assert command[command.index('-vf')+1]==montage.clip_video_filters(clip,width=1080,height=1920)
            assert command[command.index('-frames:v')+1]=='30'
        probes = [item for item in calls if isinstance(item,list) and item[0]=='ffprobe']
        assert all('-count_frames' in command and 'codec_type,codec_name,width,height,pix_fmt,r_frame_rate,nb_read_frames' in command[command.index('-show_entries')+1] for command in probes)
        captions=root/'subtitles.ffconcat'
        captions.write_text('synthetic')
        command=renderer.build_command(plan,root/'timeline-mix.wav',output,[],subtitle_intervals=[],
            subtitle_sequence=(captions,(0,0)),picture_sequence=manifest)
        assert command.count('-i')==3
        assert 'concat=n=2' not in command[command.index('-filter_complex')+1]
        assert '[2:v]setpts=PTS-STARTPTS' in command[command.index('-filter_complex')+1]
        assert command[command.index('-map',command.index('-map')+1)+1]=='1:a:0'
