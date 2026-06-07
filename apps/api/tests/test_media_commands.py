from pathlib import Path
import subprocess

from app.media_commands import (
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
