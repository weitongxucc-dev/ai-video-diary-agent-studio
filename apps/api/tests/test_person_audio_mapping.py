import pytest
from app.audio_timeline import AudioTimeline, TimelineAudioTrack, TimelineAudioClip, narration_source_range


def graph(*ranges):
    return AudioTimeline(tracks=[TimelineAudioTrack(id='voice', name='配音', role='narration', clips=[
        TimelineAudioClip(id=f'v{i}',name='配音',source_ref='narration://artifact/full',
            timeline_start_ms=t, source_start_ms=s, source_end_ms=e)
        for i,(t,s,e) in enumerate(ranges)])])


def test_maps_split_and_moved_audio_to_original_segment():
    # Segment begins at source second 10; timeline placement is independent.
    timeline=graph((2000,10000,11000),(3000,11000,14000))
    assert narration_source_range(timeline,2500,5500,{'narration://artifact/full':10000},4000)==(.5,3.5)


@pytest.mark.parametrize('ranges',[
    [(2000,10000,11000),(3500,11000,14000)],  # gap
    [(2000,10000,12000),(3000,11000,14000)],  # overlapping audio
    [(2000,10000,11000),(3000,12000,15000)],  # source cut
])
def test_discontinuous_or_overlapping_audio_cannot_claim_synchronized_lips(ranges):
    with pytest.raises(ValueError):
        narration_source_range(graph(*ranges),2000,5000,{'narration://artifact/full':10000},5000)


def test_outside_segment_unknown_source_and_muted_voice_are_explicit():
    timeline=graph((0,0,5000))
    with pytest.raises(ValueError,match='超出'):
        narration_source_range(timeline,0,5000,{'narration://artifact/full':0},4000)
    with pytest.raises(ValueError,match='替换或变速'):
        narration_source_range(timeline,0,4000,{'narration://artifact/different':0},4000)
    timeline.tracks[0].muted=True
    with pytest.raises(ValueError,match='没有完整'):
        narration_source_range(timeline,0,4000,{'narration://artifact/full':0},4000)
