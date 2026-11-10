"""Model-visible audio actions; execution stays in the shared audio editor."""
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.audio_timeline import AudioGainPoint, TimelineAudioClip, TimelineAudioTrack


class AudioMixPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_start_ms: int = Field(default=0, ge=0)
    source_end_ms: int = Field(default=1, gt=0)
    timeline_start_ms: int = Field(default=0, ge=0)
    gain_db: float = deepcopy(TimelineAudioClip.model_fields["gain_db"])
    muted: bool = deepcopy(TimelineAudioClip.model_fields["muted"])
    locked: bool = deepcopy(TimelineAudioClip.model_fields["locked"])
    fade_in_ms: int = deepcopy(TimelineAudioClip.model_fields["fade_in_ms"])
    fade_out_ms: int = deepcopy(TimelineAudioClip.model_fields["fade_out_ms"])
    denoise_db: float = deepcopy(TimelineAudioClip.model_fields["denoise_db"])
    loudness_target_lufs: float | None = deepcopy(TimelineAudioClip.model_fields["loudness_target_lufs"])
    gain_points: list[AudioGainPoint] = deepcopy(TimelineAudioClip.model_fields["gain_points"])

    @model_validator(mode="after")
    def nonempty_patch(self):
        if not self.model_fields_set:
            raise ValueError("音频修改须指定至少一个参数")
        return self


class AudioTrackPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="音轨", min_length=1, max_length=40)
    gain_db: float = deepcopy(TimelineAudioTrack.model_fields["gain_db"])
    muted: bool = deepcopy(TimelineAudioTrack.model_fields["muted"])
    locked: bool = deepcopy(TimelineAudioTrack.model_fields["locked"])
    duck_under_voice: bool = deepcopy(TimelineAudioTrack.model_fields["duck_under_voice"])

    @model_validator(mode="after")
    def nonempty_patch(self):
        if not self.model_fields_set:
            raise ValueError("音轨修改须指定至少一个参数")
        return self


class AudioAddValues(AudioMixPatch):
    source_ref: str = Field(min_length=1, max_length=500,
        description="从当前受权素材/音频对象读取的逻辑引用，不是本机路径。")
    name: str = Field(min_length=1, max_length=120)
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    timeline_start_ms: int = Field(ge=0)


class AudioSplitValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_ms: int = Field(ge=0, description="分割点在整条时间线上的毫秒位置。")


class AudioDuplicateValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeline_start_ms: int = Field(ge=0)


class AudioDeleteValues(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AudioAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1, max_length=120)


class AudioUpdateAction(AudioAction):
    action: Literal["update"]
    values: AudioMixPatch


class AudioTrackAction(AudioAction):
    action: Literal["track"]
    values: AudioTrackPatch


class AudioAddAction(AudioAction):
    action: Literal["add"]
    values: AudioAddValues


class AudioSplitAction(AudioAction):
    action: Literal["split"]
    values: AudioSplitValues


class AudioDuplicateAction(AudioAction):
    action: Literal["duplicate"]
    values: AudioDuplicateValues


class AudioDeleteAction(AudioAction):
    action: Literal["delete"]
    values: AudioDeleteValues


AudioTimelineEditRequest = Annotated[
    AudioUpdateAction | AudioTrackAction | AudioAddAction | AudioSplitAction
    | AudioDuplicateAction | AudioDeleteAction,
    Field(discriminator="action"),
]
