from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.production import SubtitleCue, SubtitleTrack
from app.subtitle_schedule import narration_schedule, schedule_srt, subtitle_schedule


def track(language="zh-CN", start=1.0, end=3.0, text="字幕", **style):
    return SubtitleTrack(id=language, workspace_id="w", task_id="t", timeline_version_id="v",
        language=language, source_language=None if language=="zh-CN" else "zh-CN",
        style=style, cues=[SubtitleCue(id="c", source_cue_id=None, text=text,
            start_seconds=start, end_seconds=end)], generation_method="timeline", model_id=None,
        artifact_ref=None, transform_id=None, status="valid")


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
