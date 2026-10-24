import json
from pathlib import Path

from app.config import Settings
from app.video import FfmpegVideoProcessor, VideoInbox


def test_video_inbox_lists_supported_videos_newest_first(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    old_video = inbox / "old.mov"
    new_video = inbox / "new.mp4"
    ignored = inbox / "notes.txt"
    old_video.write_bytes(b"old")
    new_video.write_bytes(b"new")
    ignored.write_text("not a video", encoding="utf-8")
    old_time = 1_700_000_000
    new_time = 1_800_000_000
    old_video.touch()
    new_video.touch()
    ignored.touch()
    import os

    os.utime(old_video, (old_time, old_time))
    os.utime(new_video, (new_time, new_time))

    videos = VideoInbox(inbox).pending_videos()

    assert [video.file_name for video in videos] == ["new.mp4", "old.mov"]
    assert videos[0].size_bytes == 3


def test_ffmpeg_processor_builds_accelerated_preview_command(tmp_path):
    inbox = tmp_path / "inbox"
    output = tmp_path / "out"
    inbox.mkdir()
    (inbox / "talk.mov").write_bytes(b"video")
    calls = []

    def runner(command: list[str]) -> None:
        calls.append(command)
        output.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"processed")

    processor = FfmpegVideoProcessor(
        Settings(video_inbox_path=str(inbox), video_output_path=str(output)),
        runner=runner,
        duration_reader=lambda path: 10.0,
    )

    processed = processor.process("talk.mov")

    assert processed.file_name == "talk_preview.mp4"
    assert processed.output_path == str(output / "talk_preview.mp4")
    command = calls[0]
    assert command[0] == "ffmpeg"
    assert str(inbox / "talk.mov") in command
    assert "setpts=PTS/1.05" in command
    assert "atempo=1.05,loudnorm" in command
    assert command[command.index("-crf") + 1] == "20"


def test_ffmpeg_processor_builds_conservative_silence_cut_plan(tmp_path):
    inbox = tmp_path / "inbox"
    output = tmp_path / "out"
    inbox.mkdir()
    (inbox / "talk.mov").write_bytes(b"video")
    calls = []

    def runner(command: list[str]) -> None:
        calls.append(command)
        output.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"processed")

    processor = FfmpegVideoProcessor(
        Settings(video_inbox_path=str(inbox), video_output_path=str(output)),
        runner=runner,
        silence_detector=lambda path: [(1.0, 2.3), (5.0, 5.4), (7.0, 8.2)],
        duration_reader=lambda path: 10.0,
    )

    processed = processor.process("talk.mov")

    assert processed.edit_plan_path == str(output / "talk_preview.edit_plan.json")
    plan = (output / "talk_preview.edit_plan.json").read_text(encoding="utf-8")
    assert '"removed_silences"' in plan
    assert '"start": 1.0' in plan
    assert '"start": 5.0' not in plan
    command = calls[0]
    assert "-filter_complex" in command
    assert "concat=n=3:v=1:a=1" in " ".join(command)


def test_ffmpeg_processor_rejects_path_traversal(tmp_path):
    inbox = tmp_path / "inbox"
    output = tmp_path / "out"
    inbox.mkdir()
    processor = FfmpegVideoProcessor(
        Settings(video_inbox_path=str(inbox), video_output_path=str(output)),
        runner=lambda command: None,
    )

    try:
        processor.process("../secret.mov")
    except ValueError as exc:
        assert "invalid video file name" in str(exc)
    else:
        raise AssertionError("path traversal should be rejected")


def test_ffmpeg_processor_merges_ordered_batch_before_preview(tmp_path):
    inbox = tmp_path / "inbox"
    output = tmp_path / "out"
    inbox.mkdir()
    (inbox / "part-02.mov").write_bytes(b"second")
    (inbox / "part-01.mov").write_bytes(b"first")
    calls = []

    def runner(command: list[str]) -> None:
        calls.append(command)
        output.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"processed")

    processor = FfmpegVideoProcessor(
        Settings(video_inbox_path=str(inbox), video_output_path=str(output)),
        runner=runner,
        silence_detector=lambda path: [],
        duration_reader=lambda path: 10.0,
    )

    processed = processor.process_batch(["part-02.mov", "part-01.mov"])

    assert len(calls) == 2
    merge_command = calls[0]
    assert merge_command[0] == "ffmpeg"
    assert merge_command.index(str(inbox / "part-02.mov")) < merge_command.index(
        str(inbox / "part-01.mov")
    )
    assert "concat=n=2:v=1:a=1" in " ".join(merge_command)
    assert processed.source_file_names == ["part-02.mov", "part-01.mov"]
    edit_plan = json.loads(Path(processed.edit_plan_path).read_text(encoding="utf-8"))
    assert edit_plan["source_files"] == ["part-02.mov", "part-01.mov"]
