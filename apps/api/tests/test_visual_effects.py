import json
from types import SimpleNamespace
import subprocess
from PIL import Image
import pytest
from app.montage import MontageRenderer
from app.visual_effects import VisualEffects, compile_color_command, render_color_preview


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
    effect=compile_color_command("亮一点，暖一点",{})
    assert effect.exposure==.25 and effect.temperature==.15
    assert compile_color_command("恢复原色",effect.model_dump())==VisualEffects()
    assert compile_color_command("曝光设为0.5",{}).exposure==.5
    assert compile_color_command("替换这个镜头",{}) is None
    with pytest.raises(ValueError,match="限制或操作"):
        compile_color_command("亮一点，同时美颜",{})
    with pytest.raises(ValueError):
        VisualEffects(exposure=float("nan"))
    with pytest.raises(ValueError):
        VisualEffects(unknown_filter="shell")
