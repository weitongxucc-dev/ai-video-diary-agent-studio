from dataclasses import replace
import json
import subprocess
from types import SimpleNamespace
import pytest
from app.montage import MontageClip, MontageRenderer
from app.transitions import extend_dissolve_sources, dissolve_frames


def clip(path,index,transition=0):
    return MontageClip(id=str(index),cue_id='cue',asset_id='asset',segment_id='segment',source_path=str(path),
        source_start_seconds=0,source_end_seconds=.8,timeline_start_seconds=index*.8,timeline_end_seconds=(index+1)*.8,
        match_score=1,match_reasons=[],needs_confirmation=False,transition_in_seconds=transition)


@pytest.mark.parametrize('transitions',[[0,.4,.4],[0,0,.4]])
def test_real_dissolves_keep_all_frames_audio_clock_and_mix_both_sources(tmp_path,transitions):
    clips=[]
    for i,color in enumerate(['red','lime','blue']):
        source=tmp_path/f'{color}.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color={color}:s=180x320:r=30:d=1.5','-c:v','libx264',str(source)],check=True)
        clips.append(clip(source,i,transitions[i]))
    before=[(c.source_end_seconds,c.timeline_end_seconds) for c in clips]
    voice=tmp_path/'voice.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','2.4',str(voice)],check=True)
    output=tmp_path/'dissolve.mp4'
    subprocess.run(MontageRenderer(tmp_path).build_command(SimpleNamespace(clips=clips),voice,output,[],subtitle_intervals=[]),capture_output=True,check=True,timeout=60)
    metadata=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(output)]))['streams']
    video=next(s for s in metadata if s['codec_type']=='video')
    audio=next(s for s in metadata if s['codec_type']=='audio')
    assert int(video['nb_frames'])==72
    assert abs(float(audio['duration'])-2.4)<=.04
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
    pixels=list(zip(raw[0::3],raw[1::3],raw[2::3]))
    assert pixels[0][0]>240 and pixels[-1][2]>240
    assert all(sum(p)>230 for p in pixels)
    assert pixels[54][1]>80 and pixels[54][2]>80
    if transitions[1]:
        assert pixels[30][0]>80 and pixels[30][1]>80
    assert [(c.source_end_seconds,c.timeline_end_seconds) for c in clips]==before


def test_missing_real_handle_and_black_fade_conflicts_are_explicit(tmp_path):
    source=tmp_path/'short.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=16x16:r=30:d=0.8','-c:v','libx264',str(source)],check=True)
    clips=[clip(source,0),clip(source,1,.4)]
    with pytest.raises(ValueError,match='素材余量'):
        extend_dissolve_sources(clips)
    clips[0].visual_effects={'fade_out_seconds':.2}
    with pytest.raises(ValueError,match='黑场淡入淡出'):
        dissolve_frames(clips)
    with pytest.raises(ValueError,match='第一个镜头'):
        dissolve_frames([replace(clips[0],transition_in_seconds=.2)])


def test_dissolve_rejects_invalid_or_nonadjacent_intervals():
    clips=[clip('unused.mp4',0),clip('unused.mp4',1)]
    for value in (-.1,.01,2.1,float('nan'),1):
        with pytest.raises(ValueError):
            dissolve_frames([clips[0],replace(clips[1],transition_in_seconds=value)])
    with pytest.raises(ValueError,match='连续'):
        dissolve_frames([clips[0],replace(clips[1],timeline_start_seconds=1,transition_in_seconds=.2)])
