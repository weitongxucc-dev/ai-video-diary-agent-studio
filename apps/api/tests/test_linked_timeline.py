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


def test_linked_speed_moves_subtitles_audio_and_envelopes_together():
    from app.linked_timeline import retime_clip
    from app.montage import CreationTaskMontagePlan, MontageClip
    plan = CreationTaskMontagePlan(id="p",workspace_id="w",task_id="t",script_id="s",script_title="t",
        status="ready",cues=[],clips=[MontageClip(id=str(i),cue_id="c",asset_id="a",segment_id="s",
            source_path="workspace://a.mp4",source_start_seconds=i*2,source_end_seconds=i*2+2,
            timeline_start_seconds=i*2,timeline_end_seconds=i*2+2,match_score=1,match_reasons=[],needs_confirmation=False)
            for i in range(3)],duration_seconds=6,low_confidence_clip_count=0)
    plan.clips[1].visual_effects={"fade_in_seconds":.4,"fade_out_seconds":.4}
    calls = []
    def stretch(clip, rate, duration):
        calls.append((clip.source_start_ms,clip.source_end_ms,rate,duration))
        return "artifacts/stretched.wav"
    changed, captions = retime_clip(plan, subtitles(), audio(), "1", 2, stretch)
    assert changed.clips[1].fade_envelope_duration_frames==60
    assert changed.clips[1].fade_envelope_rate==2
    assert changed.clips[1].visual_effects==plan.clips[1].visual_effects
    assert changed.duration_seconds == 5
    assert [(c.timeline_start_seconds,c.timeline_end_seconds) for c in changed.clips] == [(0,2),(2,3),(3,5)]
    assert changed.clips[1].source_end_seconds == 4
    assert calls == [(3000,5000,2,1000)]
    clips = changed.audio_timeline.tracks[0].clips
    assert [(c.timeline_start_ms,c.duration_ms,c.envelope_offset_ms) for c in clips] == [(0,2000,0),(2000,1000,1000),(3000,2000,4000)]
    assert clips[1].fade_in_ms == 250 and clips[1].envelope_duration_ms == 3000
    assert [(c.start_seconds,c.end_seconds) for c in captions[0].cues] == [(1,4)]
    plan.clips[-1].locked=True
    with pytest.raises(ValueError,match="锁定"):
        retime_clip(plan,subtitles(),audio(),"1",2,lambda *_: pytest.fail("locked edit must not execute media"))
    plan.clips[-1].locked=False
    plan.clips[0].timeline_end_seconds=3.96
    plan.clips[1].timeline_start_seconds=3.96
    with pytest.raises(ValueError, match="0.04"):
        retime_clip(plan,subtitles(),audio(),"1",4,lambda *_: pytest.fail("invalid frame duration must not execute media"))
