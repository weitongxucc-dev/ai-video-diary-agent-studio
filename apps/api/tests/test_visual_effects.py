import json
from types import SimpleNamespace
import subprocess
from PIL import Image
import pytest
from app.montage import MontageRenderer
from app.visual_effects import VisualEffects, clip_video_filters, compile_crop_command, compile_visual_command, render_color_preview, reusable_color_settings


def frame(path):
    raw=subprocess.run(["ffmpeg","-v","error","-ss","0.2","-i",str(path),"-frames:v","1","-vf","scale=36:64","-f","rawvideo","-pix_fmt","rgb24","pipe:1"],capture_output=True,check=True).stdout
    return list(zip(raw[0::3],raw[1::3],raw[2::3]))


def test_local_color_preview_and_export_apply_same_effects(tmp_path):
    source=tmp_path/"source.mp4"
    subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","color=c=0x304050:s=180x320:d=1:r=30","-c:v","libx264",str(source)],check=True)
    clip=SimpleNamespace(visual_effects={},fit_mode="fill",crop_x=.5,crop_y=.5,source_start_seconds=0,source_end_seconds=1,
        source_path=str(source),timeline_start_seconds=0,timeline_end_seconds=1)
    before=render_color_preview(clip,source,tmp_path/"before.mp4")
    saved_style = reusable_color_settings(VisualEffects(exposure=1,saturation=0,zoom=2,beauty_strength=.5,fade_in_seconds=1).model_dump())
    assert saved_style == {"exposure": 1, "contrast": 1, "saturation": 0, "temperature": 0}
    clip.visual_effects=saved_style
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
    with pytest.raises(ValueError):
        compile_visual_command('放大到9倍',{})


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
    overlay=tmp_path/'overlay.png'
    Image.new('RGBA',(1080,1920),(0,0,0,0)).save(overlay)
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
    before=samples(preview)
    after=samples(output)
    assert len(before)==len(after)==180
    for pixels in (before,after):
        assert max(pixels[0])<8 and max(pixels[-1])<8
        assert pixels[90][0]>245 and pixels[90][1]<10
        assert pixels[3][0]<pixels[8][0]<pixels[90][0]
        assert pixels[170][0]>pixels[175][0]>pixels[-1][0]
    assert max(abs(x-y) for p,q in zip(before,after) for x,y in zip(p,q))<8


def test_granted_clip_preview_keeps_its_five_second_limit(tmp_path):
    from app.montage import render_granted_clip_preview
    checks = []
    context = SimpleNamespace(check=lambda: checks.append('checked'))
    with pytest.raises(ValueError, match='五秒以内'):
        render_granted_clip_preview(None, tmp_path/'unused.mp4', tmp_path/'unused-output.mp4',
            context, aspect_ratio='9:16', max_seconds=6)
    assert checks == ['checked'] and not (tmp_path/'unused-output.mp4').exists()


def test_split_inside_fades_has_same_visible_frames_as_unsplit(tmp_path):
    from dataclasses import replace
    from app.montage import MontageClip
    from app.visual_effects import with_visual_effects
    source=tmp_path/'red.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=180x320:r=30:d=1','-c:v','libx264',str(source)],check=True)
    original=MontageClip(id='a',cue_id='cue',asset_id='asset',segment_id='seg',source_path=str(source),
        source_start_seconds=0,source_end_seconds=1,timeline_start_seconds=0,timeline_end_seconds=1,
        match_score=1,match_reasons=[],needs_confirmation=False,visual_effects={'fade_in_seconds':.4,'fade_out_seconds':.4})
    whole=render_color_preview(original,source,tmp_path/'whole.mp4',max_seconds=None)
    # Cuts inside both incoming and outgoing fades, not just a flat middle.
    paths=[]
    for i,(start,end) in enumerate([(0,.2),(.2,.8),(.8,1)]):
        piece=replace(original,source_start_seconds=start,source_end_seconds=end,timeline_start_seconds=start,timeline_end_seconds=end,
            fade_envelope_offset_frames=round(start*30),fade_envelope_duration_frames=30)
        paths.append(render_color_preview(piece,source,tmp_path/f'piece{i}.mp4',max_seconds=None))
        if i==1:
            recolored=with_visual_effects(piece,{**piece.visual_effects,'exposure':.5})
            assert recolored.fade_envelope_offset_frames==6
            newfade=with_visual_effects(piece,{**piece.visual_effects,'fade_in_seconds':.1})
            assert newfade.fade_envelope_duration_frames is None and newfade.fade_envelope_offset_frames==0
    def pixels(path):
        return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
    a=pixels(whole)
    b=b''.join(pixels(path) for path in paths)
    assert len(a)==len(b)==90
    assert max(abs(x-y) for x,y in zip(a,b))<=4


def test_fade_clock_scales_with_linked_speed_without_changing_output_frame_count(tmp_path):
    source=tmp_path/'red.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=180x320:r=30:d=1','-c:v','libx264',str(source)],check=True)
    clip=SimpleNamespace(visual_effects={'fade_in_seconds':.4,'fade_out_seconds':.4},fit_mode='fill',crop_x=.5,crop_y=.5,
        source_start_seconds=0,source_end_seconds=1,timeline_start_seconds=0,timeline_end_seconds=.5,
        fade_envelope_duration_frames=30,fade_envelope_offset_frames=0,fade_envelope_rate=2)
    output=render_color_preview(clip,source,tmp_path/'fast.mp4',max_seconds=None)
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
    red=raw[::3]
    assert len(red)==15
    assert red[0]<8 and red[7]>245
    assert red[1]<red[3]<red[5] and red[10]>red[12]>red[14]


@pytest.mark.parametrize('value',[{'fade_envelope_offset_frames':-1},{'fade_envelope_duration_frames':float('inf')},{'fade_envelope_rate':0}])
def test_invalid_persisted_fade_clock_is_explicitly_rejected(value):
    from app.visual_effects import fade_filters
    clip=SimpleNamespace(visual_effects={'fade_in_seconds':.2},timeline_start_seconds=0,timeline_end_seconds=1,**value)
    with pytest.raises(ValueError,match='效果时钟无效'):
        fade_filters(clip)


def test_compound_visual_edit_compiles_all_clauses_in_order_or_rejects_all():
    original=VisualEffects(saturation=.8).model_dump()
    result=compile_visual_command('当前镜头放大到1.2倍，向右移动5%，然后亮一点，同时淡出0.4秒，声音不变',original)
    assert result.zoom==1.2 and result.offset_x==.05 and result.exposure==.25 and result.fade_out_seconds==.4
    assert result.saturation==.8 and original==VisualEffects(saturation=.8).model_dump()
    sequential=compile_visual_command('曝光改为0.5，恢复原色，然后暖一点',{})
    assert sequential.exposure==0 and sequential.temperature==.15
    for command in ('亮一点，加上闪电特效','恢复原色但不要改饱和度','替换第二个镜头，同时亮一点','淡出0.4秒，曝光改为99'):
        with pytest.raises(ValueError):
            compile_visual_command(command,original)
    assert compile_visual_command('美颜一下，轻一点，配音不变',{}).beauty_strength==.2


def test_crop_command_only_touches_what_it_was_asked_and_never_guesses_a_step():
    current={"fit_mode":"fill","crop_x":.3,"crop_y":.7}

    # 一个模式词只改模式，两个轴沿用当前值
    assert compile_crop_command("完整显示",current)=={"fit_mode":"fit","crop_x":.3,"crop_y":.7}
    assert compile_crop_command("填满画面",current)=={"fit_mode":"fill","crop_x":.3,"crop_y":.7}

    # 单轴指令保留原 fit_mode 与另一轴
    assert compile_crop_command("水平裁剪到 40%",current)=={"fit_mode":"fill","crop_x":.4,"crop_y":.7}
    assert compile_crop_command("垂直裁剪到 60%",current)=={"fit_mode":"fill","crop_x":.3,"crop_y":.6}

    # 多个已支持子句按顺序全部应用，不是只取第一句
    assert compile_crop_command("完整显示，水平裁剪到 20%，垂直裁剪到 80%",current)=={"fit_mode":"fit","crop_x":.2,"crop_y":.8}
    assert compile_crop_command("填满画面，同时水平裁剪到 20%",{"fit_mode":"fit","crop_x":.9,"crop_y":.1})=={
        "fit_mode":"fill","crop_x":.2,"crop_y":.1}

    # 相对量沿既有 crop 坐标语义：右/下增大，左/上减小
    assert compile_crop_command("裁剪向右移动 10%",current)["crop_x"]==.4
    assert compile_crop_command("裁剪向左移动 10%",current)["crop_x"]==.2
    assert compile_crop_command("裁剪向下移动 20%",current)["crop_y"]==.9
    assert compile_crop_command("裁剪向上移动 20%",current)["crop_y"]==.5

    # 澄清提示里给出的示例必须真的可达
    for command in ("填满画面","完整显示","水平裁剪到 40%","垂直裁剪到 60%"):
        assert compile_crop_command(command,current) is not None

    # 明确越界要拒绝，不能静默截断成用户没要求的 100%
    with pytest.raises(ValueError,match="0% 到 100%"):
        compile_crop_command("水平裁剪到 120%",current)
    with pytest.raises(ValueError,match="0% 到 100%"):
        compile_crop_command("水平裁剪到 -10%",current)
    with pytest.raises(ValueError,match="超出可移动范围"):
        compile_crop_command("裁剪向右移动 80%",current)
    with pytest.raises(ValueError,match="超出可移动范围"):
        compile_crop_command("裁剪向左移动 50%",current)

    # 未定义的量不猜步长，返回需要补参数的提示而不是假装已执行
    for command in ("裁剪一下","往左一点","裁剪一点点"):
        with pytest.raises(ValueError,match="缺少可执行参数"):
            compile_crop_command(command,current)

    # 模式子串不能吞掉额外限制：整条拒绝，不只执行 fit
    with pytest.raises(ValueError,match="尚未支持的裁剪限制或操作"):
        compile_crop_command("完整显示并把声音关掉",current)
    with pytest.raises(ValueError,match="尚未支持的裁剪限制或操作"):
        compile_crop_command("填满画面，水平裁剪到 30%，把字幕改一下",current)

    # 提示里的「其他不要动」这类不改变画面的说法不构成未知子句
    assert compile_crop_command("填满画面，其他不要动",current)["fit_mode"]=="fill"

    # 非裁剪指令保持原有行为（含改画幅不误编为裁剪）
    for command in ("改成竖屏","往左移动 10%","替换这个镜头","亮一点","往左一点，同时亮一点"):
        assert compile_crop_command(command,current) is None

    # 当前镜头的裁剪位置不可用时如实报错
    with pytest.raises(ValueError,match="裁剪位置不可用"):
        compile_crop_command("完整显示",{"fit_mode":None,"crop_x":None,"crop_y":None})
