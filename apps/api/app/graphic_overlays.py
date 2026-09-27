"""Versioned text overlays and deterministic local rasterization."""
from pathlib import Path
from typing import Literal
import re
from uuid import uuid4
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TextOverlay(BaseModel):
    kind: Literal["text"] = "text"
    model_config=ConfigDict(extra='forbid')
    id: str=Field(default_factory=lambda:f'overlay_{uuid4().hex[:12]}',min_length=1,max_length=80)
    text: str=Field(min_length=1,max_length=200)
    start_seconds: float=Field(ge=0,allow_inf_nan=False)
    end_seconds: float=Field(gt=0,allow_inf_nan=False)
    x: float=Field(default=.5,ge=0,le=1,allow_inf_nan=False)
    y: float=Field(default=.15,ge=0,le=1,allow_inf_nan=False)
    width: float=Field(default=.8,ge=.05,le=1,allow_inf_nan=False)
    font_size: int=Field(default=64,ge=12,le=256)
    font_family: str=Field(default='PingFang SC',min_length=1,max_length=120)
    color: str=Field(default='#FFFFFF',pattern=r'^#[0-9a-fA-F]{6}$')
    @model_validator(mode='after')
    def valid_interval(self):
        if not self.text.strip() or self.end_seconds-self.start_seconds<.04:
            raise ValueError('标题文本不能为空，显示时间至少0.04秒')
        if self.x-self.width/2 < -1e-6 or self.x+self.width/2 > 1+1e-6:
            raise ValueError('标题宽度超出画幅，请调整水平位置或缩小宽度')
        return self


class ImageOverlay(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind: Literal['image']='image'
    id: str=Field(default_factory=lambda:f'overlay_{uuid4().hex[:12]}',min_length=1,max_length=80)
    source_ref: str=Field(min_length=1,max_length=500)
    name: str=Field(min_length=1,max_length=120)
    start_seconds: float=Field(ge=0,allow_inf_nan=False)
    end_seconds: float=Field(gt=0,allow_inf_nan=False)
    x: float=Field(default=.5,ge=0,le=1,allow_inf_nan=False)
    y: float=Field(default=.5,ge=0,le=1,allow_inf_nan=False)
    width: float=Field(default=.3,ge=.05,le=1,allow_inf_nan=False)
    opacity: float=Field(default=1,ge=0,le=1,allow_inf_nan=False)
    @model_validator(mode='after')
    def valid_interval(self):
        if self.end_seconds-self.start_seconds<.04:
            raise ValueError('图片显示时间至少0.04秒')
        if self.x-self.width/2 < -1e-6 or self.x+self.width/2 > 1+1e-6:
            raise ValueError('图片宽度超出画幅，请调整水平位置或缩小宽度')
        return self


def parse_overlay(value):
    return (ImageOverlay if value.get('kind')=='image' else TextOverlay).model_validate(value)


def write_graphic_overlay(value,output,size):
    if value.get('kind')!='image':
        return write_text_overlay(value,output,size)
    overlay=ImageOverlay.model_validate(value)
    with Image.open(overlay.source_ref) as original:
        source=original.convert('RGBA')
    width=round(size[0]*overlay.width)
    height=round(width*source.height/source.width)
    left=round(size[0]*overlay.x-width/2)
    top=round(size[1]*overlay.y-height/2)
    if left<0 or top<0 or left+width>size[0] or top+height>size[1]:
        raise ValueError('图片超出画幅，请缩小宽度或调整位置')
    source=source.resize((width,height),Image.Resampling.LANCZOS)
    if overlay.opacity!=1:
        source.putalpha(source.getchannel('A').point(lambda value:round(value*overlay.opacity)))
    image=Image.new('RGBA',size,(0,0,0,0))
    image.alpha_composite(source,(left,top))
    image.save(output)


def validate_overlays(values, duration):
    overlays=[parse_overlay(value) for value in values]
    if len(overlays)>30 or len({item.id for item in overlays})!=len(overlays):
        raise ValueError('叠加层最多30个，标识不能重复')
    if any(item.end_seconds>duration+.001 for item in overlays):
        raise ValueError('叠加层显示区间超出成片，请调整结束时间')
    return overlays


def write_text_overlay(value, output: Path, size):
    from app.montage import _subtitle_font
    overlay=TextOverlay.model_validate(value)
    font=_subtitle_font(overlay.font_size,family=overlay.font_family)
    image=Image.new('RGBA',size,(0,0,0,0))
    draw=ImageDraw.Draw(image)
    max_width=int(size[0]*overlay.width)-8
    lines=[]
    for paragraph in overlay.text.split('\n'):
        line=''
        for token in re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*|[ \t]+|.",paragraph):
            pieces=list(token) if draw.textlength(token,font=font)>max_width else [token]
            for piece in pieces:
                if line and draw.textlength(line+piece,font=font)>max_width:
                    lines.append(line.rstrip())
                    line=piece.lstrip()
                else:
                    line+=piece
        lines.append(line)
    text='\n'.join(lines)
    bounds=draw.multiline_textbbox((0,0),text,font=font,spacing=8,stroke_width=2)
    width,height=bounds[2]-bounds[0],bounds[3]-bounds[1]
    left=size[0]*overlay.x-width/2
    top=size[1]*overlay.y-height/2
    if left<0 or top<0 or left+width>size[0] or top+height>size[1]:
        raise ValueError('标题排版超出画幅，请缩小字号、调整位置或减少文字')
    draw.multiline_text((left-bounds[0],top-bounds[1]),text,font=font,fill=overlay.color,stroke_width=2,
        stroke_fill='#000000',align='center',spacing=8)
    image.save(output)


def splice_overlays(values, intervals):
    result=[]
    for value in values:
        start,end=round(value['start_seconds']*1000),round(value['end_seconds']*1000)
        parts=[]
        for old_start,old_end,new_start in intervals:
            left,right=max(start,old_start),min(end,old_end)
            if right-left>=40:
                parts.append((left,right,new_start+left-old_start))
        for index,(left,right,at) in enumerate(parts):
            result.append({**value,'id':value['id'] if index==0 else f'overlay_{uuid4().hex[:12]}',
                'start_seconds':at/1000,'end_seconds':(at+right-left)/1000})
    return result


def compile_overlay_command(command, values, sources=(), selected_refs=()):
    text=command.strip().rstrip('。！')
    contextual=re.match(r'(?:把)?(?:当前|这个|选中的)(标题|图片|标识)',text)
    if contextual:
        refs=[ref.removeprefix('overlay:') for ref in selected_refs if ref.startswith('overlay:')]
        matches=[(index,value) for index,value in enumerate(values) if value['id'] in refs]
        if len(refs)!=1 or len(matches)!=1:
            raise ValueError('请先选择一个当前版本的标题或图片')
        index,value=matches[0]
        if (contextual[1]=='标题') == (value.get('kind')=='image'):
            raise ValueError('当前选中对象与指令类型不符，请重新选择')
        text=f'{contextual[1]}{index+1}'+text[contextual.end():]
    match=re.fullmatch(r'前(\d+(?:\.\d+)?)秒(?:加|添加)(?:图片|标识)[“「](.+)[”」]',text)
    if match:
        candidates=[item for item in sources if item['name']==match[2]]
        if len(candidates)!=1:
            raise ValueError('指定图片未导入或存在同名图片，请从图片列表选择明确来源')
        item=candidates[0]
        return 'add',ImageOverlay(source_ref=item['source_ref'],name=item['name'],start_seconds=0,end_seconds=float(match[1])).model_dump(),None
    match=re.fullmatch(r'(?:把)?(?:图片|标识)(\d+)(删除|移到右上角|移到左上角|移到中间|宽度改为\d+%|不透明度改为\d+%|显示\d+(?:\.\d+)?到\d+(?:\.\d+)?秒)',text)
    if match:
        index=int(match[1])-1
        if not 0<=index<len(values) or values[index].get('kind')!='image':
            raise ValueError('所选图片不存在，请按叠加层列表序号操作')
        value=dict(values[index])
        action=match[2]
        if action=='删除':
            return 'delete',None,value['id']
        if action=='移到右上角':
            value.update(x=1-value['width']/2-.02,y=.2)
        elif action=='移到左上角':
            value.update(x=value['width']/2+.02,y=.2)
        elif action=='移到中间':
            value.update(x=.5,y=.5)
        elif action.startswith('宽度改为'):
            value['width']=int(action[4:-1])/100
        elif action.startswith('不透明度改为'):
            value['opacity']=int(action[6:-1])/100
        else:
            times=re.findall(r'\d+(?:\.\d+)?',action)
            value.update(start_seconds=float(times[0]),end_seconds=float(times[1]))
        return 'update',ImageOverlay.model_validate(value).model_dump(),value['id']
    match=re.fullmatch(r'前(\d+(?:\.\d+)?)秒(?:加|添加)标题[“「](.+)[”」]',text)
    if match:
        return 'add',TextOverlay(text=match[2],start_seconds=0,end_seconds=float(match[1])).model_dump(),None
    match=re.fullmatch(r'(?:把)?标题(\d+)(删除|移到上面|移到中间|移到下面|字号改为\d+|改成[“「].+[”」])',text)
    if not match:
        return None
    index=int(match[1])-1
    if not 0<=index<len(values) or values[index].get('kind')=='image':
        raise ValueError('所选标题不存在，请查看标题列表序号')
    value=dict(values[index])
    action=match[2]
    if action=='删除':
        return 'delete',None,value['id']
    if action in {'移到上面','移到中间','移到下面'}:
        value['y']={'移到上面':.15,'移到中间':.5,'移到下面':.8}[action]
    elif action.startswith('字号改为'):
        value['font_size']=int(action.removeprefix('字号改为'))
    else:
        value['text']=action[3:-1]
    return 'update',TextOverlay.model_validate(value).model_dump(),value['id']
