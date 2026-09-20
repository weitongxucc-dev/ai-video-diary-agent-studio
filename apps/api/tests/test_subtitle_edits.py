import pytest
from app.production import SubtitleCue, SubtitleTrack
from app.subtitle_edits import replace_subtitle_text


def track():
    return SubtitleTrack(id="t", workspace_id="w",task_id="task",timeline_version_id="v",language="en-US",source_language=None,
        style={"enabled":False},cues=[SubtitleCue(id="c",source_cue_id=None,text="AI tool. AI notes.",start_seconds=1,end_seconds=3,
            line_breaks=[9],emphasis=["AI"])],generation_method="timeline",model_id=None,artifact_ref="old.srt",transform_id="old",status="valid")


def test_literal_replace_preserves_clock_style_and_maps_manual_breaks():
    source=track()
    updated,changes=replace_subtitle_text(source,"AI","Local AI")
    assert updated.cues[0].text=="Local AI tool. Local AI notes."
    assert updated.cues[0].line_breaks==[15]
    assert updated.cues[0].emphasis==["Local AI"]
    assert (updated.cues[0].start_seconds,updated.cues[0].end_seconds)==(1,3)
    assert updated.style==source.style and updated.artifact_ref is None
    assert changes[0]["occurrences"]==2
    assert source.cues[0].text=="AI tool. AI notes."
    updated,_=replace_subtitle_text(source,".","!")
    assert updated.cues[0].text=="AI tool! AI notes!"


def test_replace_rejects_no_match_no_change_and_empty_caption():
    for find,replacement in [("missing","x"),("AI","AI"),("", "x"),("AI tool. AI notes.",""),("AI", "new\ncaption"),("AI\r", "x")]:
        with pytest.raises(ValueError):replace_subtitle_text(track(),find,replacement)
