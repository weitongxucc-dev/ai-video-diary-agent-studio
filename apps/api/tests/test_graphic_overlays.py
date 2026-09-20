from dataclasses import replace
from pathlib import Path
import subprocess
import pytest
from app.graphic_overlays import TextOverlay, validate_overlays, write_text_overlay, splice_overlays, compile_overlay_command
from app.montage import CreationTaskMontagePlan, MontageClip, MontageRenderer


def test_overlay_commands_and_linked_intervals_preserve_text():
    action,value,_=compile_overlay_command('前3秒加标题“本地 / Local”',[])
    assert action=='add' and value['text']=='本地 / Local'
    action,changed,_=compile_overlay_command('标题1改成“新的标题”',[value])
    assert action=='update' and changed['text']=='新的标题'
    assert compile_overlay_command('标题1移到中间',[value])[1]['y']==.5
    assert compile_overlay_command('标题1删除',[value])[0]=='delete'
    pieces=splice_overlays([value],[(0,1000,0),(2000,3000,1000)])
    assert [(p['start_seconds'],p['end_seconds']) for p in pieces]==[(0,1),(1,2)]
    assert len({p['id'] for p in pieces})==2 and all(p['text']==value['text'] for p in pieces)
    for invalid in ([{**value,'end_seconds':5}],[value,value]):
        with pytest.raises(ValueError):validate_overlays(invalid,3)
    with pytest.raises(ValueError):TextOverlay(text=' ',start_seconds=0,end_seconds=1)


def test_overlay_raster_bounds_and_missing_font_are_explicit(tmp_path):
    value=TextOverlay(text='真实本地标题 Local video',start_seconds=0,end_seconds=1)
    write_text_overlay(value.model_dump(),tmp_path/'title.png',(1080,1920))
    from PIL import Image
    image=Image.open(tmp_path/'title.png')
    assert image.getbbox() is not None and image.size==(1080,1920)
    with pytest.raises(ValueError,match='超出画幅'):
        write_text_overlay(value.model_copy(update={'y':0}).model_dump(),tmp_path/'bad.png',(1080,1920))
    with pytest.raises(OSError,match='未安装'):
        write_text_overlay(value.model_copy(update={'font_family':'Missing Actual Font 011'}).model_dump(),tmp_path/'bad.png',(1080,1920))


def test_real_title_only_appears_in_its_interval(tmp_path):
    source=tmp_path/'blue.mp4';voice=tmp_path/'voice.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=blue:s=180x320:r=30:d=1','-c:v','libx264',str(source)],check=True)
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','1',str(voice)],check=True)
    clip=MontageClip(id='clip',cue_id='cue',asset_id='a',segment_id='s',source_path=str(source),source_start_seconds=0,source_end_seconds=1,
        timeline_start_seconds=0,timeline_end_seconds=1,match_score=1,match_reasons=[],needs_confirmation=False)
    overlay=TextOverlay(text='本地视频 Local',start_seconds=.2,end_seconds=.6,font_size=96).model_dump()
    plan=CreationTaskMontagePlan(id='plan',workspace_id='w',task_id='t',script_id='s',script_title='t',status='ready',cues=[],clips=[clip],
        duration_seconds=1,low_confidence_clip_count=0,graphic_overlays=[overlay])
    MontageRenderer(tmp_path).render(plan,voice,job_staging_path=tmp_path/'render',subtitle_tracks=[])
    def raw(at):
        return subprocess.check_output(['ffmpeg','-v','error','-ss',str(at),'-i',str(tmp_path/'render/preview.mp4'),'-frames:v','1','-vf','crop=1080:500:0:0,scale=108:50','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
    before,during,after=raw(.1),raw(.3),raw(.8)
    assert max(before[::3])<15 and max(after[::3])<15
    assert sum(v>150 for v in during[::3])>50
