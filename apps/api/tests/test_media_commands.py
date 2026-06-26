from pathlib import Path
import subprocess

from app.media_commands import (
    build_cut_video_command,
    build_extract_audio_command,
    build_extract_reference_frames_command,
    build_mix_bgm_command,
    capture_media_command,
    read_media_duration_seconds,
    safe_file_under_root,
    run_media_command,
    safe_child_file_path,
)


def test_run_media_command_executes_list_commands_without_shell(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("app.media_commands.subprocess.run", fake_run)

    run_media_command(["ffmpeg", "-version"])

    assert calls == [(["ffmpeg", "-version"], {"check": True})]


def test_capture_media_command_returns_text_output(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("app.media_commands.subprocess.run", fake_run)

    result = capture_media_command(["ffmpeg", "-hide_banner"], check=False)

    assert result.stdout == "ok"
    assert calls == [
        (
            ["ffmpeg", "-hide_banner"],
            {"check": False, "capture_output": True, "text": True},
        )
    ]


def test_read_media_duration_seconds_uses_ffprobe_command():
    calls = []

    def fake_capture(command, *, check):
        calls.append((command, check))
        return subprocess.CompletedProcess(command, 0, stdout="12.345\n", stderr="")

    duration = read_media_duration_seconds(Path("/tmp/talk.mp4"), capture=fake_capture)

    assert duration == 12.345
    command, check = calls[0]
    assert check is True
    assert command == [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        "/tmp/talk.mp4",
    ]


def test_build_cut_video_command_creates_plain_trim_concat_command():
    command = build_cut_video_command(
        Path("/tmp/source.mp4"),
        Path("/tmp/output.mp4"),
        [(0.0, 1.2), (2.0, 5.0)],
    )

    joined = " ".join(command)
    assert command[:4] == ["ffmpeg", "-y", "-i", "/tmp/source.mp4"]
    assert "-filter_complex" in command
    assert "trim=start=0.0:end=1.2" in joined
    assert "concat=n=2:v=1:a=1[v][a]" in joined
    assert "atempo=1.05" not in joined
    assert "loudnorm" not in joined
    assert command[command.index("-map") + 1] == "[v]"
    assert command[-1] == "/tmp/output.mp4"


def test_build_cut_video_command_can_apply_preview_speed_filters():
    command = build_cut_video_command(
        Path("/tmp/source.mp4"),
        Path("/tmp/output.mp4"),
        [(0.0, 1.2)],
        speed=1.05,
        normalize_audio=True,
    )

    joined = " ".join(command)
    assert "concat=n=1:v=1:a=1[vcat][acat]" in joined
    assert "[vcat]setpts=PTS/1.05[v]" in joined
    assert "[acat]atempo=1.05,loudnorm[a]" in joined


def test_build_cut_video_command_can_speed_full_video_without_ranges():
    command = build_cut_video_command(
        Path("/tmp/source.mp4"),
        Path("/tmp/output.mp4"),
        [],
        speed=1.05,
        normalize_audio=True,
    )

    assert "-filter:v" in command
    assert "setpts=PTS/1.05" in command
    assert "-filter:a" in command
    assert "atempo=1.05,loudnorm" in command
    assert "-filter_complex" not in command


def test_build_extract_audio_command_creates_shared_16k_mono_wav_command():
    command = build_extract_audio_command(
        Path("/tmp/source.mp4"),
        Path("/tmp/audio.wav"),
    )

    assert command == [
        "ffmpeg",
        "-y",
        "-i",
        "/tmp/source.mp4",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        "/tmp/audio.wav",
    ]


def test_build_extract_reference_frames_command_creates_fps_frame_command():
    command = build_extract_reference_frames_command(
        Path("/tmp/source.mp4"),
        Path("/tmp/frames/frame_%04d.jpg"),
        fps=1,
    )

    assert command == [
        "ffmpeg",
        "-y",
        "-i",
        "/tmp/source.mp4",
        "-vf",
        "fps=1,scale=720:-2:force_original_aspect_ratio=decrease",
        "-q:v",
        "3",
        "/tmp/frames/frame_%04d.jpg",
    ]


def test_build_mix_bgm_command_creates_ffmpeg_amix_command():
    command = build_mix_bgm_command(
        Path("/tmp/talk.mp4"),
        Path("/tmp/bgm.mp3"),
        Path("/tmp/mixed.mp4"),
        bgm_volume=0.18,
    )

    joined = " ".join(command)
    assert command[:6] == ["ffmpeg", "-y", "-i", "/tmp/talk.mp4", "-stream_loop", "-1"]
    assert "/tmp/bgm.mp3" in command
    assert "-filter_complex" in command
    assert "[1:a]volume=0.18[bgm]" in joined
    assert "amix=inputs=2:duration=first:dropout_transition=2[a]" in joined
    assert command[-1] == "/tmp/mixed.mp4"


def test_safe_child_file_path_rejects_traversal_and_unsupported_extensions(tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    (root / "talk.mov").write_bytes(b"video")

    assert safe_child_file_path(
        root,
        "talk.mov",
        allowed_extensions={".mov"},
    ) == root / "talk.mov"

    try:
        safe_child_file_path(root, "../secret.mov", allowed_extensions={".mov"})
    except ValueError as exc:
        assert "invalid video file name" in str(exc)
    else:
        raise AssertionError("path traversal should be rejected")

    try:
        safe_child_file_path(root, "notes.txt", allowed_extensions={".mov"})
    except ValueError as exc:
        assert "unsupported video file" in str(exc)
    else:
        raise AssertionError("unsupported extension should be rejected")


def test_safe_child_file_path_rejects_symlink_escape(tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.mov"
    target.write_bytes(b"secret")
    symlink = root / "linked.mov"
    symlink.symlink_to(target)

    try:
        safe_child_file_path(root, "linked.mov", allowed_extensions={".mov"})
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("symlink escape should be rejected")


def test_safe_file_under_root_rejects_external_files(tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    video = root / "talk.mp4"
    video.write_bytes(b"video")
    external = tmp_path / "secret.mp4"
    external.write_bytes(b"secret")

    assert safe_file_under_root(video, root) == video.resolve()

    try:
        safe_file_under_root(external, root)
    except ValueError as exc:
        assert "file is outside the video output directory" in str(exc)
    else:
        raise AssertionError("external file should be rejected")


def test_video_processing_modules_use_shared_media_command_runner():
    root = Path(__file__).resolve().parents[1] / "app"
    video_source = (root / "video.py").read_text(encoding="utf-8")
    pipeline_source = (root / "video_pipeline.py").read_text(encoding="utf-8")
    edit_plan_source = (root / "edit_plan.py").read_text(encoding="utf-8")
    drafts_source = (root / "drafts.py").read_text(encoding="utf-8")

    assert "subprocess.run" not in video_source
    assert "subprocess.run" not in pipeline_source
    assert "subprocess.run" not in edit_plan_source
    assert "subprocess.run" not in drafts_source
    assert "from app.media_commands import" in video_source
    assert "from app.media_commands import" in pipeline_source
    assert "from app.media_commands import" in edit_plan_source
    assert "from app.media_commands import" in drafts_source
    assert "def _build_cut_command" not in video_source
    assert "def _build_cut_command" not in pipeline_source
    assert "def _build_cut_command" not in edit_plan_source
