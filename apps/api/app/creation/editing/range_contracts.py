"""Explicit timeline-clock cuts; creative selection remains the Agent's job."""
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class TimelineRangeEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    removed_ranges_ms: list[tuple[StrictInt, StrictInt]] = Field(min_length=1, max_length=100,
        description="当前基础时间线的毫秒删除区间；按起点排序且不重叠。源时钟须先按镜头映射，不按字数推切点。")
    reason: str = Field(min_length=1, max_length=1600,
        description="本次取舍的简要证据、保留信息与限定；语义由Agent判断，不宣称程序已听过。")

    @model_validator(mode="after")
    def ordered_intervals(self):
        end = 0
        for left, right in self.removed_ranges_ms:
            if not end <= left < right:
                raise ValueError("删除区间须为有序、非负、不重叠的毫秒范围")
            end = right
        return self
