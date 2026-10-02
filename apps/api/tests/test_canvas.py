from PIL import Image
import pytest
from app.canvas import canvas_size,preview_size
from app.montage import _write_subtitle_image
from app.subtitle_schedule import TimedCaption


@pytest.mark.parametrize('ratio', ['9:16','16:9','1:1'])
def test_caption_canvas_and_safe_area_use_selected_ratio(tmp_path,ratio):
    size=canvas_size(ratio)
    cue=TimedCaption(0,1,'Long English captions retain safe margins and never stretch the source picture. 中文字幕安全区。',{'font_size':72})
    path=tmp_path/'subtitle.png'
    _write_subtitle_image(cue,path,size=size)
    with Image.open(path) as image:
        assert image.size==size
        left,top,right,bottom=image.getbbox()
        assert 0<left<right<size[0] and 0<top<bottom<size[1]
    assert max(preview_size(ratio))==640
