import pytest
from PIL import Image
from app.subtitle_style import compile_subtitle_style_command, validate_style_patch
from app.subtitle_schedule import TimedCaption
from app.montage import _write_subtitle_image


def test_style_language_compiles_to_precise_local_parameters():
    assert compile_subtitle_style_command("字幕背景透明度改成75%") == {"background_opacity":.25}
    assert compile_subtitle_style_command("字幕字号改成64") == {"font_size":64}
    assert compile_subtitle_style_command("字幕文字颜色改成黄色") == {"primary_color":"#FFD54F"}
    assert compile_subtitle_style_command("英文放在中文下面") == {"bilingual_order":"source_then_translation"}
    assert compile_subtitle_style_command("把字幕改成你好") is None


@pytest.mark.parametrize("patch", [{"stroke_width":-1},{"stroke_width":True},{"background_opacity":2},{"background_opacity":float('nan')},
    {"primary_color":"red"},{"font_family":""},{"position":[]},{"enabled":False},{"shadow":4}])
def test_style_rejects_unsupported_or_invalid_parameters(patch):
    with pytest.raises(ValueError):validate_style_patch(patch)


def test_actual_overlay_renders_background_stroke_and_transparency(tmp_path):
    style={"font_size":64,"primary_color":"#FFFFFF","stroke_width":8,"stroke_color":"#FF0000",
        "background_color":"#0000FF","background_opacity":.5,"position":"top"}
    caption=TimedCaption(0,1,"Real captions",style)
    first=tmp_path/'style.png';_write_subtitle_image(caption,first)
    with Image.open(first) as image:
        colors={color for _, color in image.getcolors(1080*1920)}
        assert (0,0,255,128) in colors
        assert (255,0,0,255) in colors
        assert image.getbbox()[1]==250
    caption.subtitle_style={**style,"background_opacity":0,"stroke_width":0}
    second=tmp_path/'clear.png';_write_subtitle_image(caption,second)
    with Image.open(second) as image:
        visible=[color for _, color in image.getcolors(1080*1920) if color[3]]
        assert visible and all(r==g==b==255 for r,g,b,a in visible)
