"""Editable, self-contained visual source, independent of HTTP and Chromium."""
from __future__ import annotations

import hashlib
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TemplateBinding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    kind: Literal['text', 'variable']
    target: str = Field(min_length=1, max_length=200)
    default: str = Field(max_length=4000)


class TemplateProvenance(BaseModel):
    model_config = ConfigDict(extra='forbid')
    repository: str = Field(min_length=1, max_length=500)
    revision: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=500)
    license: str = Field(min_length=1, max_length=120)
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class VisualTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    engine: Literal['openfilm-html-v1'] = 'openfilm-html-v1'
    category: Literal['title', 'chapter', 'callout', 'data', 'product', 'ending']
    width: int = Field(ge=64, le=4096)
    height: int = Field(ge=64, le=4096)
    duration_seconds: float = Field(gt=0, le=300, allow_inf_nan=False)
    source: str = Field(min_length=1, max_length=262144)
    bindings: list[TemplateBinding] = Field(default_factory=list, max_length=100)
    values: dict[str, str] = Field(default_factory=dict)
    provenance: list[TemplateProvenance] = Field(min_length=1, max_length=20)
    dependencies: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def valid_bindings(self):
        keys = [item.key for item in self.bindings]
        if len(set(keys)) != len(keys) or set(self.values) - set(keys):
            raise ValueError('模板参数重复或不在参数定义中')
        if any(len(value) > 4000 for value in self.values.values()):
            raise ValueError('模板参数文本超过4000字，请分行或调整设计')
        return self

    @property
    def source_digest(self):
        return hashlib.sha256(self.source.encode()).hexdigest()


class OverlayKeyframe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    seconds: float = Field(ge=0, allow_inf_nan=False)
    x: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    scale: float = Field(default=1, gt=0, le=4, allow_inf_nan=False)
    opacity: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)


class VisualTemplateOverlay(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['template'] = 'template'
    id: str = Field(default_factory=lambda: f'overlay_{uuid4().hex[:12]}', min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    template_id: str = Field(min_length=1, max_length=120)
    template_version: int = Field(ge=1)
    definition: VisualTemplate
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)
    x: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    width: float = Field(default=.8, ge=.05, le=1, allow_inf_nan=False)
    opacity: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)
    keyframes: list[OverlayKeyframe] = Field(default_factory=list, max_length=120)
    z_index: int = Field(default=0, ge=-1000, le=1000)
    animation_offset_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)

    @model_validator(mode='after')
    def interval(self):
        duration = self.end_seconds - self.start_seconds
        if duration < .04:
            raise ValueError('模板显示时间至少0.04秒')
        times = [item.seconds for item in self.keyframes]
        if times != sorted(set(times)) or any(t > duration for t in times):
            raise ValueError('关键帧时间必须递增且属于模板显示区间')
        return self


def slice_template_motion(value, offset, duration):
    """Keep the original source clock and linear motion when timeline ranges cut."""
    result = dict(value)
    result['animation_offset_seconds'] = value.get('animation_offset_seconds', 0) + offset
    keys = value.get('keyframes', [])
    if not keys:
        return result
    def at(t):
        if t <= keys[0]['seconds']:
            return {key: keys[0][key] for key in ('x','y','scale','opacity')}
        for left, right in zip(keys, keys[1:]):
            if t <= right['seconds']:
                p = (t-left['seconds'])/(right['seconds']-left['seconds'])
                return {key: left[key]+p*(right[key]-left[key]) for key in ('x','y','scale','opacity')}
        return {key: keys[-1][key] for key in ('x','y','scale','opacity')}
    frames = [{'seconds': 0., **at(offset)}]
    frames += [{**key, 'seconds': key['seconds']-offset} for key in keys if offset < key['seconds'] < offset+duration]
    frames.append({'seconds': duration, **at(offset+duration)})
    def same(left, right):
        return all(left[key] == right[key] for key in ('x','y','scale','opacity'))
    if len(frames) > 1 and same(frames[0], frames[1]):
        frames.pop(0)
    if len(frames) > 1 and same(frames[-1], frames[-2]):
        frames.pop()
    result['keyframes'] = frames
    return result
