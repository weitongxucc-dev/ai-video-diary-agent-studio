from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.production import SubtitleCue, SubtitleTrack
from app.subtitle_schedule import new_subtitle_gap_intervals, narration_schedule, schedule_srt, subtitle_schedule


def track(language="zh-CN", start=1.0, end=3.0, text="字幕", **style):
    return SubtitleTrack(id=language, workspace_id="w", task_id="t", timeline_version_id="v",
        language=language, source_language=None if language=="zh-CN" else "zh-CN",
        style=style, cues=[SubtitleCue(id="c", source_cue_id=None, text=text,
            start_seconds=start, end_seconds=end)], generation_method="timeline", model_id=None,
        artifact_ref=None, transform_id=None, status="valid")


@pytest.mark.parametrize("before_spans,after_spans,expected", [
    ([(0, 2), (2, 5)], [(0, 1.8), (2, 5)], [(1.8, 2)]),
    ([(0, 5)], [(0.5, 4.5)], [(0, 0.5), (4.5, 5)]),
    ([(0, 2), (3, 5)], [(0, 1.8), (3.5, 5)], [(1.8, 2), (3, 3.5)]),
    ([(1, 2), (3, 4)], [(0, 2.5), (3, 5)], []),
    ([(0, 2), (2, 5)], [(0, 2), (2, 5)], []),
    # A later compound step fills the gap left by an earlier step: final state only.
    ([(0, 2), (2, 5)], [(0, 1.8), (1.8, 5)], []),
])
def test_new_subtitle_gap_intervals_report_only_lost_coverage(before_spans, after_spans, expected):
    original = track()
    def with_spans(spans):
        return replace(original, cues=[replace(original.cues[0], id=str(index),
            start_seconds=start, end_seconds=end) for index, (start, end) in enumerate(spans)])
    before, after = with_spans(before_spans), with_spans(after_spans)
    assert new_subtitle_gap_intervals(before, after, 5) == expected
    assert [(cue.start_seconds, cue.end_seconds) for cue in before.cues] == before_spans
    assert [(cue.start_seconds, cue.end_seconds) for cue in after.cues] == after_spans


def test_new_subtitle_gap_intervals_keep_language_coverage_separate():
    chinese = track(start=0, end=5, enabled=False)
    changed = replace(chinese, cues=[replace(chinese.cues[0], end_seconds=4)])
    english = track("en-US", start=0, end=5)
    assert new_subtitle_gap_intervals(chinese, changed, 5) == [(4, 5)]
    assert new_subtitle_gap_intervals(english, english, 5) == []
    with pytest.raises(ValueError, match="同一语言轨"):
        new_subtitle_gap_intervals(chinese, english, 5)
    assert new_subtitle_gap_intervals(chinese, replace(changed, status="unavailable"), 5) == []


def test_tracks_keep_voice_clock_and_gaps_without_mutating_source():
    source=track()
    captions=subtitle_schedule([source], 5)
    assert [(item.start_seconds,item.end_seconds,item.text) for item in captions]==[(1,3,"字幕")]
    assert "00:00:01,000 --> 00:00:03,000" in schedule_srt(captions)
    assert source.cues[0].start_seconds==1


def test_english_only_and_disabled_track_and_bilingual_order():
    zh=track(enabled=False)
    en=track("en-US", text="English words")
    assert subtitle_schedule([zh,en],5)[0].text=="English words"
    assert subtitle_schedule([zh],5)==[]
    zh=replace(zh,style={"bilingual_order":"translation_then_source"})
    assert subtitle_schedule([zh,en],5)[0].text=="English words\n字幕"


def test_srt_partitions_simultaneous_different_positions():
    schedule=subtitle_schedule([track(position="top"),track("en-US",2,4,"English",position="bottom")],5)
    srt=schedule_srt(schedule)
    assert "00:00:01,000 --> 00:00:02,000\n字幕" in srt
    assert "00:00:02,000 --> 00:00:03,000" in srt
    assert "00:00:03,000 --> 00:00:04,000\nEnglish" in srt
    assert len(srt.strip().split("\n\n"))==3


@pytest.mark.parametrize("start,end",[(-1,2),(1,1),(1,6),(1,float("nan"))])
def test_invalid_track_time_rejected(start,end):
    with pytest.raises(ValueError,match="字幕区间"):
        subtitle_schedule([track(start=start,end=end)],5)


def test_overlapping_same_track_rejected():
    source=track()
    source=replace(source,cues=[*source.cues,replace(source.cues[0],id="b",start_seconds=2,end_seconds=4)])
    with pytest.raises(ValueError,match="重叠"):
        subtitle_schedule([source],5)


def test_initial_subtitles_use_narration_clock_even_without_shots():
    cues=[SimpleNamespace(text=text,duration_seconds=duration,subtitle_style={},
        subtitle_emphasis=[],subtitle_emphasis_ranges=None) for text,duration in [("first",2),("second",3)]]
    schedule=narration_schedule(cues,5)
    assert [(c.start_seconds,c.end_seconds,c.text) for c in schedule]==[(0,2,"first"),(2,5,"second")]


def test_narration_retime_preserves_manual_offsets_languages_and_clears_old_files():
    from app.subtitle_schedule import retime_subtitle_tracks
    before=[SimpleNamespace(id="a",duration_seconds=2),SimpleNamespace(id="b",duration_seconds=3)]
    after=[SimpleNamespace(id="a",duration_seconds=4),SimpleNamespace(id="b",duration_seconds=3)]
    source=track(start=.5,end=1.5,text="用户修改",enabled=False)
    english=track("en-US",start=2.5,end=4.5,text="Manual text",font_size=40)
    source=replace(source,artifact_ref="old.srt",transform_id="old")
    moved=retime_subtitle_tracks([source,english],before,after)
    assert [(t.cues[0].start_seconds,t.cues[0].end_seconds) for t in moved]==[(1,3),(4.5,6.5)]
    assert [t.cues[0].text for t in moved]==["用户修改","Manual text"]
    assert moved[0].style["enabled"] is False
    assert moved[1].style["font_size"]==40
    assert all(t.artifact_ref is None and t.transform_id is None for t in moved)
    assert source.cues[0].start_seconds==.5 and source.artifact_ref=="old.srt"
    restored=retime_subtitle_tracks(moved,after,before)
    assert [t.cues for t in restored]==[source.cues,english.cues]


def test_retime_rejects_missing_cue_or_outside_old_clock():
    from app.subtitle_schedule import retime_subtitle_tracks
    before=[SimpleNamespace(id="a",duration_seconds=2)]
    after=[SimpleNamespace(id="a",duration_seconds=3)]
    with pytest.raises(ValueError,match="超出"):
        retime_subtitle_tracks([track(start=1,end=4)],before,after)
    with pytest.raises(ValueError,match="一致"):
        retime_subtitle_tracks([track()],before,[])
