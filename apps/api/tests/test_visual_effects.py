import json
from types import SimpleNamespace
import subprocess
from PIL import Image
import pytest
from app.montage import MontageRenderer
from app.visual_effects import VisualEffects, clip_video_filters, compile_visual_command, render_color_preview


def frame(path):
    raw=subprocess.run(["ffmpeg","-v","error","-ss","0.2","-i",str(path),"-frames:v","1","-vf","scale=36:64","-f","rawvideo","-pix_fmt","rgb24","pipe:1"],capture_output=True,check=True).stdout
    return list(zip(raw[0::3],raw[1::3],raw[2::3]))


def test_local_color_preview_and_export_apply_same_effects(tmp_path):
    source=tmp_path/"source.mp4"
    subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","color=c=0x304050:s=180x320:d=1:r=30","-c:v","libx264",str(source)],check=True)
    clip=SimpleNamespace(visual_effects={},fit_mode="fill",crop_x=.5,crop_y=.5,source_start_seconds=0,source_end_seconds=1,
        source_path=str(source),timeline_start_seconds=0,timeline_end_seconds=1)
    before=render_color_preview(clip,source,tmp_path/"before.mp4")
    clip.visual_effects=VisualEffects(exposure=1,saturation=0).model_dump()
    after=render_color_preview(clip,source,tmp_path/"after.mp4")
    a,b=frame(before),frame(after)
    assert sum(sum(p) for p in b) > sum(sum(p) for p in a)*1.25
    assert max(max(p)-min(p) for p in b) <= 3
    voice=tmp_path/"voice.wav"
    subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","anullsrc=r=48000:cl=stereo","-t","1",str(voice)],check=True)
    overlay=tmp_path/"blank.png"
    Image.new("RGBA",(1080,1920),(0,0,0,0)).save(overlay)
    exported=tmp_path/"export.mp4"
    command=MontageRenderer(tmp_path).build_command(SimpleNamespace(clips=[clip]),voice,exported,[overlay])
    subprocess.run(command,capture_output=True,check=True,timeout=60)
    c=frame(exported)
    assert max(abs(x-y) for p,q in zip(b,c) for x,y in zip(p,q)) <= 5
    metadata=json.loads(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","json",str(exported)],capture_output=True,text=True,check=True).stdout)
    assert float(metadata["format"]["duration"]) == pytest.approx(1,abs=.05)


def test_color_commands_are_local_bounded_and_do_not_silently_drop_constraints():
    effect=compile_visual_command("亮一点，暖一点",{})
    assert effect.exposure==.25 and effect.temperature==.15
    assert compile_visual_command("恢复原色",effect.model_dump())==VisualEffects()
    assert compile_visual_command("曝光设为0.5",{}).exposure==.5
    assert compile_visual_command("替换这个镜头",{}) is None
    with pytest.raises(ValueError,match="限制或操作"):
        compile_visual_command("亮一点，同时改字幕",{})
    with pytest.raises(ValueError):
        VisualEffects(exposure=float("nan"))
    with pytest.raises(ValueError):
        VisualEffects(unknown_filter="shell")


def test_portrait_commands_preserve_color_and_respect_face_constraints():
    effects=compile_visual_command("美颜一下，轻一点，不要改变脸型，配音不变",{"exposure":.4})
    assert effects.beauty_strength==.2 and effects.exposure==.4
    assert effects.skin_exposure==0
    brighter=compile_visual_command("肤色亮一点",effects.model_dump())
    assert brighter.skin_exposure==.15 and brighter.exposure==.4
    off=compile_visual_command("关闭美颜",effects.model_dump())
    assert off.beauty_strength==0 and off.exposure==.4
    specific=compile_visual_command("肤色曝光设为0.2",effects.model_dump())
    assert specific.skin_exposure==.2 and specific.exposure==.4


def test_missing_portrait_runtime_rejects_processing_before_touching_source(tmp_path):
    from app.local_portrait import LocalPortrait
    processor=LocalPortrait(None,tmp_path/"cache")
    assert processor.available is False
    with pytest.raises(ValueError,match="未就绪"):
        processor.process(tmp_path/"missing.mp4",0,1,.3,0)
    assert not (tmp_path/"cache").exists()


def test_picture_speed_changes_actual_frame_time_without_changing_output_length(tmp_path):
    source = tmp_path / "timed-colors.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=180x320:r=30:d=0.3",
        "-f", "lavfi", "-i", "color=blue:s=180x320:r=30:d=1.7",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-c:v", "libx264", str(source)], check=True)
    clip = SimpleNamespace(visual_effects={}, fit_mode="fill", crop_x=.5, crop_y=.5,
        source_start_seconds=0, source_end_seconds=2, timeline_start_seconds=0, timeline_end_seconds=1)
    output = render_color_preview(clip, source, tmp_path / "double-speed.mp4")
    # At 0.2 output seconds, 2x playback has reached the blue source frame at 0.4.
    original, sped = frame(source), frame(output)
    assert original[0][0] > 200 and original[0][2] < 20
    assert sped[0][2] > 200 and sped[0][0] < 20
    metadata = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(output)], capture_output=True, text=True, check=True).stdout)
    assert float(metadata["format"]["duration"]) == pytest.approx(1, abs=.05)


@pytest.mark.parametrize("timeline_end, expected", [(12, 5), (1.597, 1.6)])
def test_speed_preview_obeys_output_clock_and_five_second_limit(tmp_path, timeline_end, expected):
    source = tmp_path / "long-source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=180x320:r=30:d=6",
        "-c:v", "libx264", str(source)], check=True)
    clip = SimpleNamespace(visual_effects={}, fit_mode="fill", crop_x=.5, crop_y=.5,
        source_start_seconds=0, source_end_seconds=6, timeline_start_seconds=0, timeline_end_seconds=timeline_end)
    output = render_color_preview(clip, source, tmp_path / "slow-preview.mp4")
    metadata = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(output)], capture_output=True, text=True, check=True).stdout)
    assert float(metadata["format"]["duration"]) == pytest.approx(expected, abs=.005)


def test_transform_commands_keep_color_and_color_reset_keeps_geometry():
    original=VisualEffects(exposure=.5,zoom=1.3,rotation=15).model_dump()
    assert compile_visual_command('当前镜头放大到1.5倍',original).zoom==1.5
    assert compile_visual_command('再放大一点',original).zoom==1.4
    assert compile_visual_command('逆时针旋转90度',original).rotation==-90
    moved=compile_visual_command('向右移动10%',original)
    assert moved.offset_x==.1 and moved.zoom==1.3 and moved.exposure==.5
    restored=compile_visual_command('恢复原色',moved.model_dump())
    assert restored.exposure==0 and restored.zoom==1.3 and restored.offset_x==.1
    reset=compile_visual_command('重置画面变换',moved.model_dump())
    assert reset.zoom==1 and reset.rotation==0 and reset.exposure==.5
    with pytest.raises(ValueError):compile_visual_command('放大到9倍',{})


def test_geometry_preview_and_export_match_without_stretching_or_clock_change(tmp_path):
    source=tmp_path/'pattern.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=white:s=180x320:d=1:r=30',
        '-vf','drawbox=x=10:y=20:w=50:h=50:color=red:t=fill,drawbox=x=100:y=230:w=40:h=60:color=blue:t=fill',
        '-c:v','libx264',str(source)],check=True)
    clip=SimpleNamespace(visual_effects=VisualEffects(zoom=.7,rotation=30,offset_x=.1,offset_y=-.1).model_dump(),
        fit_mode='fill',crop_x=.5,crop_y=.5,source_start_seconds=0,source_end_seconds=1,source_path=str(source),
        timeline_start_seconds=0,timeline_end_seconds=1)
    preview=render_color_preview(clip,source,tmp_path/'transformed.mp4')
    voice=tmp_path/'voice.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','1',str(voice)],check=True)
    output=tmp_path/'output.mp4'
    command=MontageRenderer(tmp_path).build_command(SimpleNamespace(clips=[clip]),voice,output,[],subtitle_intervals=[])
    subprocess.run(command,capture_output=True,check=True,timeout=60)
    a,b=frame(preview),frame(output)
    assert sum(abs(x-y) for p,q in zip(a,b) for x,y in zip(p,q))/(len(a)*3)<4
    assert sum(max(pixel)<10 for pixel in b)>len(b)*.35
    assert any(r>180 and b<50 for r,g,b in b)
    metadata=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(output)]))
    assert float(metadata['format']['duration'])==pytest.approx(1,abs=.034)


def test_many_fractional_shots_do_not_accumulate_frames_or_drop_the_last_shot(tmp_path):
    sources=[]
    for color in ('red','lime'):
        source=tmp_path/f'{color}.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color={color}:s=180x320:d=1:r=30','-c:v','libx264',str(source)],check=True)
        sources.append(source)
    duration=.137*12
    clips=[SimpleNamespace(source_path=str(sources[1 if i==11 else 0]),visual_effects={},fit_mode='fill',crop_x=.5,crop_y=.5,
        source_start_seconds=0,source_end_seconds=.137,timeline_start_seconds=i*.137,timeline_end_seconds=(i+1)*.137) for i in range(12)]
    voice=tmp_path/'voice.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t',str(duration),str(voice)],check=True)
    overlay=tmp_path/'overlay.png';Image.new('RGBA',(1080,1920),(0,0,0,0)).save(overlay)
    output=tmp_path/'fractional.mp4'
    command=MontageRenderer(tmp_path).build_command(SimpleNamespace(clips=clips),voice,output,[overlay],subtitle_intervals=[(0,duration)])
    subprocess.run(command,capture_output=True,check=True,timeout=60)
    streams=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(output)]))['streams']
    video=next(stream for stream in streams if stream['codec_type']=='video')
    assert int(video['nb_frames'])==round(duration*30)
    assert abs(float(video['duration'])-duration)<=1/30
    # The green twelfth shot must survive. A total-duration-only test misses a
    # renderer that pads earlier shots and truncates the final content.
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-vf',f"select=eq(n\\,{round(duration*30)-1}),scale=1:1",
        '-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
    assert raw[1]>200 and raw[0]<30 and raw[2]<30


def test_subframe_shot_is_rejected_instead_of_silently_disappearing():
    clip = SimpleNamespace(timeline_start_seconds=1, timeline_end_seconds=1.001)
    with pytest.raises(ValueError, match="不足一个输出帧"):
        clip_video_filters(clip)


def test_fade_commands_preserve_other_effects_and_validate_frame_intervals():
    from app.visual_effects import fade_filters
    effect=compile_visual_command('当前镜头淡出0.4秒',{'exposure':.5,'fade_in_seconds':.2})
    assert effect.fade_out_seconds==.4 and effect.exposure==.5 and effect.fade_in_seconds==.2
    restored=compile_visual_command('取消淡入淡出',effect.model_dump())
    assert restored.fade_in_seconds==restored.fade_out_seconds==0 and restored.exposure==.5
    for values, message in [({'fade_out_seconds':.01},'至少需要两帧'),
                            ({'fade_in_seconds':.7,'fade_out_seconds':.7},'总时长超过')]:
        with pytest.raises(ValueError,match=message):
            fade_filters(SimpleNamespace(visual_effects=values,timeline_start_seconds=2,timeline_end_seconds=3))


def test_fade_preview_includes_tail_and_matches_export(tmp_path):
    source=tmp_path/'red.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=180x320:d=6:r=30','-c:v','libx264',str(source)],check=True)
    clip=SimpleNamespace(visual_effects={'fade_in_seconds':.4,'fade_out_seconds':.4},fit_mode='fill',crop_x=.5,crop_y=.5,
        source_path=str(source),source_start_seconds=0,source_end_seconds=6,timeline_start_seconds=2,timeline_end_seconds=8)
    preview=render_color_preview(clip,source,tmp_path/'preview.mp4',max_seconds=None)
    voice=tmp_path/'voice.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','6',str(voice)],check=True)
    output=tmp_path/'export.mp4'
    subprocess.run(MontageRenderer(tmp_path).build_command(SimpleNamespace(clips=[clip]),voice,output,[],subtitle_intervals=[]),capture_output=True,check=True,timeout=60)
    def samples(path):
        raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
        return list(zip(raw[0::3],raw[1::3],raw[2::3]))
    before=samples(preview);after=samples(output)
    assert len(before)==len(after)==180
    for pixels in (before,after):
        assert max(pixels[0])<8 and max(pixels[-1])<8
        assert pixels[90][0]>245 and pixels[90][1]<10
        assert pixels[3][0]<pixels[8][0]<pixels[90][0]
        assert pixels[170][0]>pixels[175][0]>pixels[-1][0]
    assert max(abs(x-y) for p,q in zip(before,after) for x,y in zip(p,q))<8
