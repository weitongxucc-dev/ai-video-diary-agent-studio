import subprocess
import pytest
from app.graphic_overlays import TextOverlay, validate_overlays, write_text_overlay, splice_overlays, compile_overlay_command
from app.montage import CreationTaskMontagePlan, MontageClip, MontageRenderer, _resolve_subtitle_font


def _available_cjk_font() -> str:
    # CI installs fonts-noto-cjk on Linux; local dev usually runs macOS with PingFang SC.
    for family in ('PingFang SC', 'Noto Sans CJK SC'):
        try:
            _resolve_subtitle_font(family)
            return family
        except OSError:
            continue
    pytest.fail('环境中没有可用的中文字体（PingFang SC / Noto Sans CJK SC）；请先安装系统字体')
CHINESE_FONT = _available_cjk_font()


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
        with pytest.raises(ValueError):
            validate_overlays(invalid,3)
    with pytest.raises(ValueError):
        TextOverlay(text=' ',start_seconds=0,end_seconds=1)


def test_overlay_raster_bounds_and_missing_font_are_explicit(tmp_path):
    value=TextOverlay(text='真实本地标题 Local video',start_seconds=0,end_seconds=1,font_family=CHINESE_FONT)
    write_text_overlay(value.model_dump(),tmp_path/'title.png',(1080,1920))
    from PIL import Image
    image=Image.open(tmp_path/'title.png')
    assert image.getbbox() is not None and image.size==(1080,1920)
    with pytest.raises(ValueError,match='超出画幅'):
        write_text_overlay(value.model_copy(update={'y':0}).model_dump(),tmp_path/'bad.png',(1080,1920))
    with pytest.raises(OSError,match='未安装'):
        write_text_overlay(value.model_copy(update={'font_family':'Missing Actual Font 011'}).model_dump(),tmp_path/'bad.png',(1080,1920))


def test_real_title_only_appears_in_its_interval(tmp_path):
    source=tmp_path/'blue.mp4'
    voice=tmp_path/'voice.wav'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=blue:s=180x320:r=30:d=1','-c:v','libx264',str(source)],check=True)
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','1',str(voice)],check=True)
    clip=MontageClip(id='clip',cue_id='cue',asset_id='a',segment_id='s',source_path=str(source),source_start_seconds=0,source_end_seconds=1,
        timeline_start_seconds=0,timeline_end_seconds=1,match_score=1,match_reasons=[],needs_confirmation=False)
    overlay=TextOverlay(text='本地视频 Local',start_seconds=.2,end_seconds=.6,font_size=96,font_family=CHINESE_FONT).model_dump()
    plan=CreationTaskMontagePlan(id='plan',workspace_id='w',task_id='t',script_id='s',script_title='t',status='ready',cues=[],clips=[clip],
        duration_seconds=1,low_confidence_clip_count=0,graphic_overlays=[overlay])
    MontageRenderer(tmp_path).render(plan,voice,job_staging_path=tmp_path/'render',subtitle_tracks=[])
    def raw(at):
        return subprocess.check_output(['ffmpeg','-v','error','-ss',str(at),'-i',str(tmp_path/'render/preview.mp4'),'-frames:v','1','-vf','crop=1080:500:0:0,scale=108:50','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
    before,during,after=raw(.1),raw(.3),raw(.8)
    assert max(before[::3])<15 and max(after[::3])<15
    assert sum(v>150 for v in during[::3])>50


def test_image_overlay_preserves_alpha_aspect_and_natural_parameters(tmp_path):
    from PIL import Image,ImageDraw
    from app.graphic_overlays import ImageOverlay,write_graphic_overlay
    source=tmp_path/'logo.png'
    image=Image.new('RGBA',(200,100),(0,0,0,0))
    ImageDraw.Draw(image).rectangle((50,0,149,99),fill=(255,0,0,255))
    image.save(source)
    value=ImageOverlay(source_ref=str(source),name='logo.png',start_seconds=0,end_seconds=1,width=.4,opacity=.5)
    output=tmp_path/'layer.png'
    write_graphic_overlay(value.model_dump(),output,(1000,1000))
    rendered=Image.open(output)
    left,top,right,bottom=rendered.getbbox()
    assert (left+right)/2==500 and (top,bottom)==(400,600)
    assert abs((right-left)-200)<=12  # Antialiased resize edges may extend a few pixels.
    assert rendered.getpixel((500,500))==(255,0,0,128)
    assert rendered.getpixel((350,500))[3]==0
    action,changed,_=compile_overlay_command('图片1宽度改为20%',[value.model_dump()])
    assert action=='update' and changed['width']==.2
    assert compile_overlay_command('图片1不透明度改为50%',[value.model_dump()])[1]['opacity']==.5
    assert compile_overlay_command('图片1显示1到3秒',[value.model_dump()])[1]['end_seconds']==3
    sources=[{'name':'logo.png','source_ref':'artifacts/current.png'}]
    assert compile_overlay_command('前3秒加图片“logo.png”',[],sources)[1]['source_ref']=='artifacts/current.png'
    with pytest.raises(ValueError,match='同名'):
        compile_overlay_command('前3秒加图片“logo.png”',[],sources+sources)
    with pytest.raises(ValueError,match='超出画幅'):
        write_graphic_overlay(value.model_copy(update={'y':0}).model_dump(),output,(1000,1000))


def test_graphic_import_rejects_oversize_empty_and_animation_before_publication(tmp_path):
    import io
    from types import SimpleNamespace
    from PIL import Image
    from app.graphic_sources import import_graphic_source
    def attempt(data):
        return import_graphic_source(None,None,tmp_path,'w','t',SimpleNamespace(filename='test.png',file=io.BytesIO(data)))
    with pytest.raises(ValueError,match='为空'):
        attempt(b'')
    with pytest.raises(ValueError,match='20MB'):
        attempt(b'x'*(20*1024*1024+1))
    large=io.BytesIO()
    Image.new('RGB',(8193,1)).save(large,format='PNG')
    with pytest.raises(ValueError,match='尺寸过大'):
        attempt(large.getvalue())
    animated=io.BytesIO()
    Image.new('RGBA',(8,8),'red').save(animated,format='PNG',save_all=True,append_images=[Image.new('RGBA',(8,8),'blue')],duration=100,loop=0)
    with pytest.raises(ValueError,match='静态'):
        attempt(animated.getvalue())


def test_overlay_context_is_bound_to_identity_and_type():
    from app.graphic_overlays import ImageOverlay
    title=TextOverlay(text='本地',start_seconds=0,end_seconds=2).model_dump()
    image=ImageOverlay(source_ref='artifacts/source.png',name='logo.png',start_seconds=0,end_seconds=2).model_dump()
    ref=f"overlay:{image['id']}"
    # A reordered list must still address the selected image, not its previous index.
    for values in ([title,image],[image,title]):
        action,value,identity=compile_overlay_command('把当前图片不透明度改为50%',values,selected_refs=[ref])
        assert action=='update' and identity==image['id'] and value['opacity']==.5
    assert compile_overlay_command('这个标题字号改为96',[title],selected_refs=[f"overlay:{title['id']}"])[1]['font_size']==96
    for refs in ([],['overlay:missing'],[ref,f"overlay:{title['id']}"]):
        with pytest.raises(ValueError,match='选择'):
            compile_overlay_command('当前图片删除',[image,title],selected_refs=refs)
    with pytest.raises(ValueError,match='类型不符'):
        compile_overlay_command('当前标题删除',[image],selected_refs=[ref])


def test_arrow_overlay_parse_defaults_and_validation():
    from app.graphic_overlays import ArrowOverlay, parse_overlay
    value=ArrowOverlay(x=.46,y=.35,name='进料口',purpose='原料从这里进入',start_seconds=.2,end_seconds=1.2)
    dump=value.model_dump()
    assert dump['kind']=='arrow'
    assert dump['position']=='right-top' and dump['color']=='#FFFFFF'
    assert dump['id'].startswith('overlay_')
    assert isinstance(parse_overlay(dump),ArrowOverlay)
    with pytest.raises(ValueError,match='至少0.04'):
        ArrowOverlay(x=.5,y=.5,name='a',start_seconds=1,end_seconds=1.01)
    with pytest.raises(ValueError):
        ArrowOverlay(x=1.5,y=.5,name='a',start_seconds=0,end_seconds=1)
    with pytest.raises(ValueError):
        ArrowOverlay(x=.5,y=-.1,name='a',start_seconds=0,end_seconds=1)
    with pytest.raises(ValueError):
        ArrowOverlay(x=.5,y=.5,name='a',start_seconds=0,end_seconds=1,color='red')
    with pytest.raises(ValueError):
        ArrowOverlay(x=.5,y=.5,name='x'*25,start_seconds=0,end_seconds=1)
    with pytest.raises(ValueError):
        ArrowOverlay(x=.5,y=.5,name='',start_seconds=0,end_seconds=1)


def test_arrow_overlay_rasterizes_and_dispatches_without_breaking_others(tmp_path):
    from PIL import Image
    from app.graphic_overlays import ArrowOverlay, write_arrow_overlay, write_graphic_overlay, validate_overlays
    arrow=ArrowOverlay(x=.46,y=.35,name='进料口',purpose='原料从这里进入',start_seconds=0,end_seconds=1).model_dump()
    output=tmp_path/'arrow.png'
    write_arrow_overlay(arrow,output,(1080,1920))
    image=Image.open(output)
    assert image.size==(1080,1920) and image.getbbox() is not None
    dispatched=tmp_path/'dispatched.png'
    write_graphic_overlay(arrow,dispatched,(1080,1920))
    assert Image.open(dispatched).getbbox() is not None
    # Arrow parses through the shared validate_overlays entry without breaking text.
    title=TextOverlay(text='本地',start_seconds=0,end_seconds=1).model_dump()
    overlays=validate_overlays([title,arrow],2)
    assert [item.kind for item in overlays]==['text','arrow']
