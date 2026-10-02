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
        with pytest.raises(ValueError):
            replace_subtitle_text(track(),find,replacement)


def test_split_merge_delete_keep_caption_metadata_and_time():
    from app.subtitle_edits import restructure_subtitle
    source=track()
    split,_=restructure_subtitle(source,"SplitSubtitleCue","c",split_offset=9,split_time_seconds=2)
    assert [cue.text for cue in split.cues]==["AI tool. ","AI notes."]
    assert [(cue.start_seconds,cue.end_seconds) for cue in split.cues]==[(1,2),(2,3)]
    assert all(cue.emphasis==["AI"] for cue in split.cues)
    assert len({cue.id for cue in split.cues})==2
    merged,_=restructure_subtitle(split,"MergeSubtitleCue","c")
    assert merged.cues[0].text==source.cues[0].text
    assert (merged.cues[0].start_seconds,merged.cues[0].end_seconds)==(1,3)
    deleted,_=restructure_subtitle(merged,"DeleteSubtitleCue","c")
    assert deleted.cues==[] and deleted.artifact_ref is None
    assert source.cues[0].text=="AI tool. AI notes."


@pytest.mark.parametrize("offset,time", [(0,2),(99,2),(3,.99),(3,2.99),(3,float('nan'))])
def test_split_rejects_invalid_boundaries(offset,time):
    from app.subtitle_edits import restructure_subtitle
    with pytest.raises(ValueError):
        restructure_subtitle(track(),"SplitSubtitleCue","c",split_offset=offset,split_time_seconds=time)


@pytest.mark.parametrize("problem", ["missing", "duplicate", "time", "order", "multiple", "unlinked"])
def test_linked_merge_rejects_uncertain_mapping(problem):
    from dataclasses import replace
    from app.subtitle_edits import merge_linked_subtitle_tracks
    chinese = replace(track(), id="zh", language="zh-CN", cues=[
        SubtitleCue(id="z1", source_cue_id=None, text="甲", start_seconds=0, end_seconds=1),
        SubtitleCue(id="z2", source_cue_id=None, text="乙", start_seconds=1, end_seconds=2)])
    english = replace(chinese, id="en", language="en-US", source_language="zh-CN", cues=[
        SubtitleCue(id="e1", source_cue_id="z1", text="First", start_seconds=0, end_seconds=1),
        SubtitleCue(id="e2", source_cue_id="z2", text="Second", start_seconds=1, end_seconds=2)])
    if problem == "missing": english = replace(english, cues=english.cues[:1])
    if problem == "duplicate": english = replace(english, cues=[english.cues[0], replace(english.cues[1], source_cue_id="z1")])
    if problem == "time": english = replace(english, cues=[replace(english.cues[0], end_seconds=.9), english.cues[1]])
    if problem == "order": english = replace(english, cues=[replace(english.cues[0], source_cue_id="z2"), replace(english.cues[1], source_cue_id="z1")])
    if problem == "unlinked": english = replace(english, source_language=None)
    inputs = [chinese, english] + ([replace(english, id="other")] if problem == "multiple" else [])
    with pytest.raises(ValueError, match="关联|对应关系|时间"):
        merge_linked_subtitle_tracks(inputs, chinese, "z1")
    assert len(chinese.cues) == 2 and chinese.artifact_ref == "old.srt"
    changed, _ = merge_linked_subtitle_tracks([chinese], chinese, "z1")
    assert len(changed) == 1 and changed[0].cues[0].text == "甲乙"
