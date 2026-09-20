from dataclasses import replace

import pytest

from app.audio_timeline import AudioTimeline, TimelineAudioClip, TimelineAudioTrack
from app.linked_timeline import splice_audio, splice_intervals, splice_subtitles
from app.production import SubtitleCue, SubtitleTrack


def audio():
    return AudioTimeline(tracks=[TimelineAudioTrack(id="voice", name="配音", role="narration", clips=[
        TimelineAudioClip(id="v", name="旁白", source_ref="narration://artifact/original",
            source_start_ms=1000, source_end_ms=7000, timeline_start_ms=0,
            fade_in_ms=500, fade_out_ms=500)])])


def subtitles():
    return [SubtitleTrack(id="zh", workspace_id="w", task_id="t", timeline_version_id="v",
        language="zh-CN", source_language=None, style={"enabled": False},
        cues=[SubtitleCue(id="c", source_cue_id=None, text="保留用户文字", start_seconds=1, end_seconds=5)],
        generation_method="timeline", model_id=None, artifact_ref="old.srt", transform_id="old", status="valid")]


@pytest.mark.parametrize("action,expected", [
    ("delete", [(1000, 3000, 0, 0), (5000, 7000, 2000, 4000)]),
    ("duplicate", [(1000, 5000, 0, 0), (3000, 5000, 4000, 2000), (5000, 7000, 6000, 4000)]),
])
def test_linked_audio_preserves_source_and_envelope_coordinates(action, expected):
    original = audio()
    edited = splice_audio(original, splice_intervals(6000, 2000, 4000, action))
    clips = edited.tracks[0].clips
    assert [(c.source_start_ms, c.source_end_ms, c.timeline_start_ms, c.envelope_offset_ms) for c in clips] == expected
    assert all(c.envelope_duration_ms == 6000 and c.fade_out_ms == 500 for c in clips)
    assert len({c.id for c in clips}) == len(clips)
    assert original.tracks[0].clips[0].duration_ms == 6000


def test_linked_subtitles_preserve_hidden_manual_text_and_remove_deleted_intervals():
    original = subtitles()
    deleted = splice_subtitles(original, splice_intervals(6000, 2000, 4000, "delete"), 4)
    assert [(c.start_seconds, c.end_seconds) for c in deleted[0].cues] == [(1, 2), (2, 3)]
    copied = splice_subtitles(original, splice_intervals(6000, 2000, 4000, "duplicate"), 8)
    assert [(c.start_seconds, c.end_seconds) for c in copied[0].cues] == [(1, 4), (4, 6), (6, 7)]
    assert all(c.text == "保留用户文字" for c in copied[0].cues)
    assert copied[0].style["enabled"] is False and copied[0].artifact_ref is None
    assert splice_subtitles([replace(original[0], cues=[replace(original[0].cues[0], start_seconds=2, end_seconds=4)])],
        splice_intervals(6000, 2000, 4000, "delete"), 4) == []


def test_locked_audio_changes_fail_without_mutating_source():
    original = audio()
    original.tracks[0].locked = True
    with pytest.raises(ValueError, match="锁定"):
        splice_audio(original, splice_intervals(6000, 2000, 4000, "delete"))
    assert original.tracks[0].clips[0].source_end_ms == 7000


def test_cannot_delete_whole_video():
    with pytest.raises(ValueError, match="全部"):
        splice_intervals(6000, 0, 6000, "delete")
