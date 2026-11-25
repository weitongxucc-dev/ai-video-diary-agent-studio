import hashlib
import json
import shutil
import sys
import time
from dataclasses import replace

import pytest

from app.creation_execution import CpuRenderGrantContext
from app.granted_subprocess import GrantedCommandTimeout
from app.host_resource_probes import (
    HostBudget, HostProcessProbe, HostResourceDecision, HostResourceProbe, SceneRuntimeLimits,
)
from app.media_execution.adapter import FfmpegAdapter
from app.media_execution.contracts import MediaExecutionError
from app.media_execution.cut_measurement import Mp4CutLimits, measure_mp4_cut
from test_host_resource_probes import _render_budget
from test_task_media_grants import case


@pytest.fixture
def cut_grant(tmp_path, monkeypatch):
    # This admission fixture makes no production decision. The registered
    # child identities, limits, sampling and release use the actual runner.
    render = _render_budget()

    class CutMeasurementResources(HostResourceProbe):
        def admit(self, request):
            assert request.resources["kind"] == "cut_measurement_fixture"
            return HostResourceDecision(True, dict(request.resources), {})

        def continue_unit(self, grant):
            return True

        def scene_runtime_limits(self, grant, *, purpose=None):
            return SceneRuntimeLimits(384 * 1024**2, render.max_process_threads, 0, None, self.budget.sample_seconds,
                                      self.memory_monitor, collect_cpu=True,
                                      cpu_policy=render.running_cpu_policy if sys.platform == "darwin" else None)

        def released(self, grant):
            return True

    root = tmp_path / "grant"
    root.mkdir()
    resources = CutMeasurementResources(root / "media.sqlite", disk_paths=(root,),
                                        budget=HostBudget(100, 0, 2**63, 0, 2**63, 8 * 1024**2, 0.02))
    repo, actor, _, _, build, media = case(root, resources=resources)
    original = media.facts

    def facts(*args):
        identity, resources = original(*args)
        return identity, {**resources, "kind": "cut_measurement_fixture"}

    media.facts = facts
    runtime = build(media)
    runtime.scheduler.processes = HostProcessProbe()
    job = media.submit(runtime, actor, "task", "synthetic_media", {}, "audio_listen")
    grant = runtime.scheduler.pick_and_grant()
    assert grant is not None and grant.owner_id == job.id
    registered = []
    register_child = runtime.scheduler.register_child

    def register(current, child):
        assert current == grant
        register_child(current, child)
        registered.append(child)

    monkeypatch.setattr(runtime.scheduler, "register_child", register)
    context = CpuRenderGrantContext(runtime.scheduler, grant, time.monotonic() + 15, 8 * 1024**2, 1)
    try:
        yield context, registered
    finally:
        runtime.scheduler.complete_unit(grant, checkpoint_database_path=repo.database_path,
                                        checkpoint_write=lambda conn, current: "cut-measurement-fixture")
        runtime.scheduler.assert_released(grant)
        assert runtime.stop(1)


@pytest.mark.parametrize("size, outcome", [
    ((1280, 720), "succeeded"),
    ((1920, 1080), "succeeded"),
    ((1280, 720), "media_cut_packet_limit"),
    ((1280, 720), "media_cut_input_limit"),
    ((1280, 720), "media_cut_range_limit"),
    ((1280, 720), "oversize_pixels"),
    ((1280, 720), "media_cut_source_changed"),
    ((1280, 720), "media_cut_command_failed"),
    ((1280, 720), "expired"),
])
def test_measure_mp4_cut(tmp_path, granted_media, cut_grant, size, outcome):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("requires FFmpeg and FFprobe")
    width, height = size
    context, registered = cut_grant
    source = tmp_path / "source.mp4"
    with granted_media(kind="cut_measurement_sample") as generation:
        result = generation.run([ffmpeg, "-v", "error", "-filter_threads", "1", "-f", "lavfi",
            "-i", f"testsrc2=size={width}x{height}:rate=30:duration=2", "-f", "lavfi",
            "-i", "sine=frequency=523:sample_rate=48000:duration=2", "-ac", "2", "-c:a", "aac",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-threads:v", "1",
            "-movflags", "+faststart", str(source)], output_paths=(source,))
        assert not result.returncode, result.stderr
    decode_pixels = ((width + 31) // 32 * 32) * ((height + 31) // 32 * 32)
    max_packets = 100 if outcome == "media_cut_packet_limit" else 180
    max_source_bytes = source.stat().st_size - 1 if outcome == "media_cut_input_limit" else 3 * 1024**2
    max_source_seconds = 1.5 if outcome == "media_cut_range_limit" else 2
    limits = Mp4CutLimits(width, height, decode_pixels, max_source_seconds,
                          max_source_bytes, context.max_output_bytes, max_packets)
    if outcome == "oversize_pixels":
        limits = replace(limits, width=640, height=360, max_decode_pixels=640 * 384)
    sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    if outcome == "media_cut_source_changed":
        with source.open("r+b") as handle:
            # Change the content without changing the byte count.
            handle.seek(4)
            handle.write(b"xxxx")
    if outcome == "expired":
        context = replace(context, deadline_monotonic=time.monotonic())
    commands = []

    def run(argv, paths, *, env):
        commands.append(argv)
        assert paths[0] != source and paths[0].stat().st_mode & 0o777 == 0o400
        result = context.run(argv, env=env, output_paths=paths)
        if outcome == "media_cut_command_failed":
            return replace(result, stderr=result.stderr + b"decoder reported a bad packet\n")
        if len(commands) == 3:
            shutil.copyfile(paths[1], tmp_path / "verified.mp4")
        return result

    def execute():
        return measure_mp4_cut(FfmpegAdapter(ffmpeg, ffprobe), source, tmp_path, source_sha256=sha256,
            source_bytes=source.stat().st_size, start=0.25, duration=1, limits=limits, run=run, check=context.check)

    if outcome == "succeeded":
        receipt = execute()
        assert receipt["source"]["sha256"] == sha256
        assert receipt["source"]["video_seconds"] == receipt["source"]["audio_seconds"] == 2
        assert receipt["source"]["packets"] == 155
        assert receipt["selection"] == [0.25, 1.25]
        output = receipt["output"]
        assert output["video_frames"] == 31
        assert output["video_start_seconds"] == 0 and output["video_end_seconds"] == 1
        assert output["audio_seconds"] == output["duration_seconds"] == 1
        assert hashlib.sha256((tmp_path / "verified.mp4").read_bytes()).hexdigest() == output["sha256"]
        assert len(commands) == 3 and commands[0][-1] == commands[1][commands[1].index("-i") + 1]
        assert commands[1][-1] == commands[2][-1]
        (tmp_path / "measurement.json").write_text(json.dumps({
            **receipt, "request_id": context.grant.request_id, "generation": context.grant.generation,
            "deadline_monotonic": context.command_deadline_monotonic,
            "children": [child.pid for child in registered],
        }, indent=2))
    elif outcome == "expired":
        with pytest.raises(GrantedCommandTimeout):
            execute()
        assert not commands
    else:
        with pytest.raises(MediaExecutionError) as caught:
            execute()
        reason = "media_cut_command_failed" if outcome == "oversize_pixels" else outcome
        assert caught.value.reason == reason
        assert len(commands) == (0 if outcome in {"media_cut_source_changed", "media_cut_input_limit"} else 1)
        if outcome == "media_cut_packet_limit":
            assert commands[0][commands[0].index("-read_intervals") + 1] == "%+#101"
            assert "101" in caught.value.repair_action
    assert len(registered) == len(commands)
    assert not list(tmp_path.glob("mp4-cut-*"))
