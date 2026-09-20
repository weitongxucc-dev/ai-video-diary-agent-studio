"""Versioned audio items and one local mixing path for preview and export."""
from __future__ import annotations

import math
import json
import re
from pathlib import Path
import subprocess
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


# FFmpeg loudnorm can emit NaN for fully silent input; preserve silence for encoders.
FINITE_LOUDNESS_FILTER = "aeval=exprs=\'if(isnan(val(ch)),0,val(ch))\':c=same"


class AudioGainPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset_ms: int = Field(ge=0)
    gain_db: float = Field(ge=-60, le=12, allow_inf_nan=False)


class TimelineAudioClip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=120)
    source_ref: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=120)
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    timeline_start_ms: int = Field(ge=0)
    gain_db: float = Field(default=0, ge=-60, le=12, allow_inf_nan=False)
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)
    muted: bool = False
    locked: bool = False
    denoise_db: float = Field(default=0, ge=0, le=24, allow_inf_nan=False)
    loudness_target_lufs: float | None = Field(default=None, ge=-30, le=-10, allow_inf_nan=False)
    gain_points: list[AudioGainPoint] = Field(default_factory=list, max_length=100)
    envelope_offset_ms: int = Field(default=0, ge=0)
    envelope_duration_ms: int | None = Field(default=None, gt=0)

    @property
    def duration_ms(self) -> int:
        return self.source_end_ms - self.source_start_ms

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.duration_ms < 10:
            raise ValueError("音频片段至少需要 10 毫秒")
        envelope_duration = self.envelope_duration_ms or self.duration_ms
        if self.envelope_offset_ms + self.duration_ms > envelope_duration:
            raise ValueError("音量包络范围不能短于音频片段")
        if self.fade_in_ms + self.fade_out_ms > envelope_duration:
            raise ValueError("淡入和淡出的总时长不能超过音频片段")
        offsets = [point.offset_ms for point in self.gain_points]
        if offsets != sorted(set(offsets)) or any(offset > envelope_duration for offset in offsets):
            raise ValueError("音量关键点必须按时间排列且位于片段内")
        if not self.source_ref.startswith(("narration://", "workspace://", "artifacts/")):
            raise ValueError("音轨必须使用当前工作区的音频引用")
        return self


class TimelineAudioTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=40)
    role: Literal["narration", "original", "music", "effect"]
    gain_db: float = Field(default=0, ge=-60, le=12, allow_inf_nan=False)
    muted: bool = False
    locked: bool = False
    duck_under_voice: bool = False
    clips: list[TimelineAudioClip] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def ducking_role(self):
        if self.duck_under_voice and self.role != "music":
            raise ValueError("自动压低只适用于音乐轨")
        return self


class AudioTimeline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: Literal[1] = 1
    tracks: list[TimelineAudioTrack] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def unique_objects(self):
        ids = [track.id for track in self.tracks] + [clip.id for track in self.tracks for clip in track.clips]
        if len(ids) != len(set(ids)):
            raise ValueError("音轨和片段的标识不能重复")
        return self


class AudioEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_version_id: str | None = None
    action: Literal["add", "update", "delete", "split", "duplicate", "track"]
    target_id: str
    values: dict[str, object] = Field(default_factory=dict)
    raw_command: str = Field(default="精确音频编辑", max_length=500)


class AudioListenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_version_id: str | None = None
    start_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)
    solo_track_ids: set[str] = Field(default_factory=set, max_length=24)


def visible_gain_points(clip: TimelineAudioClip) -> list[AudioGainPoint]:
    points = clip.gain_points
    if not points:
        return []
    start, end = clip.envelope_offset_ms, clip.envelope_offset_ms + clip.duration_ms
    def value_at(offset):
        if offset <= points[0].offset_ms:
            return points[0].gain_db
        for left, right in zip(points, points[1:]):
            if offset <= right.offset_ms:
                return left.gain_db + (right.gain_db-left.gain_db)*(offset-left.offset_ms)/(right.offset_ms-left.offset_ms)
        return points[-1].gain_db
    offsets = sorted({start, end, *(point.offset_ms for point in points if start < point.offset_ms < end)})
    return [AudioGainPoint(offset_ms=offset-start,gain_db=value_at(offset)) for offset in offsets]


def apply_audio_edit(timeline: AudioTimeline, edit: AudioEditRequest, duration_ms: int) -> AudioTimeline:
    result = timeline.model_copy(deep=True)
    track = next((item for item in result.tracks if item.id == edit.target_id or any(clip.id == edit.target_id for clip in item.clips)), None)
    if track is None:
        raise ValueError("所选音轨或音频片段不存在")
    if track.locked and not (edit.action == "track" and edit.values == {"locked": False}):
        raise ValueError("音轨已锁定，请先解锁")
    if edit.action == "track":
        if edit.target_id != track.id or not edit.values.keys() <= {"gain_db", "muted", "locked", "duck_under_voice", "name"}:
            raise ValueError("音轨设置参数无效")
        result.tracks[result.tracks.index(track)] = TimelineAudioTrack.model_validate({**track.model_dump(), **edit.values})
    elif edit.action == "add":
        if edit.target_id != track.id:
            raise ValueError("请先选择要加入的音轨")
        track.clips.append(TimelineAudioClip.model_validate({**edit.values, "id": f"audio-{uuid4().hex[:16]}"}))
    else:
        clip = next((item for item in track.clips if item.id == edit.target_id), None)
        if clip is None:
            raise ValueError("请先选择音频片段")
        if clip.locked and not (edit.action == "update" and edit.values == {"locked": False}):
            raise ValueError("音频片段已锁定，请先解锁")
        index = track.clips.index(clip)
        if edit.action == "delete":
            if edit.values:
                raise ValueError("删除音频不接受附加参数")
            track.clips.pop(index)
        elif edit.action == "duplicate":
            if set(edit.values) != {"timeline_start_ms"} or type(edit.values["timeline_start_ms"]) is not int:
                raise ValueError("复制音频需要具体的时间线位置")
            track.clips.insert(index+1, TimelineAudioClip.model_validate({**clip.model_dump(),
                "id": f"audio-{uuid4().hex[:16]}", "timeline_start_ms": edit.values["timeline_start_ms"]}))
        elif edit.action == "update":
            if not edit.values.keys() <= {"source_start_ms", "source_end_ms", "timeline_start_ms", "gain_db", "fade_in_ms", "fade_out_ms", "muted", "locked", "denoise_db", "loudness_target_lufs", "gain_points"}:
                raise ValueError("音频修改参数无效")
            data = {**clip.model_dump(), **edit.values}
            if edit.values.keys() & {"fade_in_ms", "fade_out_ms"}:
                data["gain_points"] = edit.values.get("gain_points", [point.model_dump() for point in visible_gain_points(clip)])
                data.update(envelope_offset_ms=0, envelope_duration_ms=None)
            elif "gain_points" in edit.values:
                points = [AudioGainPoint.model_validate(point) for point in edit.values["gain_points"]]
                if any(point.offset_ms > clip.duration_ms for point in points):
                    raise ValueError("音量关键点必须位于所选片段内")
                data["gain_points"] = [{"offset_ms":point.offset_ms+clip.envelope_offset_ms,"gain_db":point.gain_db} for point in points]
            track.clips[index] = TimelineAudioClip.model_validate(data)
        else:
            if set(edit.values) != {"at_ms"} or type(edit.values["at_ms"]) is not int:
                raise ValueError("分割需要具体的时间线位置")
            offset = edit.values["at_ms"] - clip.timeline_start_ms
            if not 10 <= offset <= clip.duration_ms - 10:
                raise ValueError("分割位置必须位于音频片段内部")
            base = {**clip.model_dump(), "envelope_duration_ms": clip.envelope_duration_ms or clip.duration_ms}
            left = {**base, "source_end_ms": clip.source_start_ms + offset}
            right = {**base, "id": f"audio-{uuid4().hex[:16]}", "source_start_ms": clip.source_start_ms + offset,
                     "envelope_offset_ms": clip.envelope_offset_ms + offset,
                     "timeline_start_ms": edit.values["at_ms"]}
            track.clips[index:index+1] = [TimelineAudioClip.model_validate(left), TimelineAudioClip.model_validate(right)]
    result = AudioTimeline.model_validate(result.model_dump())
    if any(clip.timeline_start_ms + clip.duration_ms > duration_ms for item in result.tracks for clip in item.clips):
        raise ValueError("音频片段超出成片范围，请调整位置或源区间")
    if result == timeline:
        raise ValueError("这项音频设置没有变化")
    return result


def compile_audio_command(command: str, target_id: str, timeline: AudioTimeline,
                          base_version_id: str | None, playhead_ms: int | None) -> AudioEditRequest:
    track = next((item for item in timeline.tracks if item.id == target_id or any(clip.id == target_id for clip in item.clips)), None)
    if track is None:
        raise ValueError("请先选择音乐、配音、原声或音效对象")
    clip = next((item for item in track.clips if item.id == target_id), None)
    action = "update" if clip else "track"
    target = clip or track
    parts=[part.strip() for part in re.split(r"[，,；;]|(?:同时|顺便|然后|并且|以及|和)",command.strip().casefold().rstrip("。！")) if part.strip()]
    values={};structural=None
    for part in parts:
        if part in {"画面不变","不改画面","字幕不变","其他不要动","其他别动"}:continue
        # Object names refer to the explicitly selected track, never another implicit track.
        named_roles=re.findall(r"音乐|配音|原声|音效",part)
        if any(track.role!={"音乐":"music","配音":"narration","原声":"original","音效":"effect"}[name] for name in named_roles):
            raise ValueError("指令中的音轨类型与当前选择不同，请先选择对应对象")
        text=re.sub(r"^(?:把)?(?:当前|这个)?(?:音乐|配音|原声|音效|音频片段|音频|音轨)","",part)
        text=re.sub(r"(?:当前|这个)?(?:音乐|配音|原声|音效|音频片段|音频|音轨)$","",text)
        patch=None
        if text in {"解锁","锁定","取消静音","恢复声音","静音"}:
            patch={"locked":text=="锁定"} if text in {"解锁","锁定"} else {"muted":text=="静音"}
        elif not clip and text in {"说话时压低","说话时压低音乐","有人说话时压低音乐"}:patch={"duck_under_voice":True}
        elif not clip and text in {"取消自动压低","关闭自动压低"}:patch={"duck_under_voice":False}
        elif clip and text in {"复制","删除","分割","在播放位置分割"}:
            if structural or values or len(parts)>1:raise ValueError("分割、复制和删除暂不能与其他音频操作合并，请分别预览")
            if text=="复制":structural=("duplicate",{"timeline_start_ms":clip.timeline_start_ms+clip.duration_ms})
            elif text=="删除":structural=("delete",{})
            elif playhead_ms is not None:structural=("split",{"at_ms":playhead_ms})
            else:raise ValueError("请先定位音频分割位置")
            continue
        else:
            match=re.fullmatch(r"(淡入|淡出)(?:设为|改为|为)?\s*(\d+(?:\.\d+)?)\s*秒",text)
            if clip and match:patch={"fade_in_ms" if match[1]=="淡入" else "fade_out_ms":round(float(match[2])*1000)}
            elif clip and text in {"轻降噪","轻微降噪","降噪","降噪一下","关闭降噪","取消降噪"}:
                patch={"denoise_db":0 if text.startswith(("关闭","取消")) else 6 if "轻" in text else 12}
            elif clip and text in {"统一响度","响度标准化","关闭响度","取消响度标准化"}:
                patch={"loudness_target_lufs":None if text.startswith(("关闭","取消")) else -16}
            else:
                move=re.fullmatch(r"移到(?:第)?\s*(\d+(?:\.\d+)?)\s*秒",text)
                gain=re.fullmatch(r"(?:音量)?(降低|减小|提高|设为|改为|到|为)?\s*([+-]?\d+(?:\.\d+)?)\s*(?:db|分贝)",text)
                level=re.sub(r"^音量","",text)
                current_gain=values.get("gain_db",target.gain_db)
                if clip and move:patch={"timeline_start_ms":round(float(move[1])*1000)}
                elif gain:
                    amount=float(gain[2]);patch={"gain_db":current_gain-abs(amount) if gain[1] in {"降低","减小"} else current_gain+abs(amount) if gain[1]=="提高" else amount}
                elif level in {"减半","降低一半","降到一半","一半","小一点","低一点","轻一点","大一点","高一点"}:
                    patch={"gain_db":current_gain+(-6.0206 if "半" in level else 3 if level in {"大一点","高一点"} else -3)}
        if patch is None:raise ValueError("无法完整执行音频要求："+part+"。没有应用任何修改，请明确参数后重新预览")
        values.update(patch)
    if structural:action,values=structural
    elif not values:raise ValueError("请指定要修改的音频参数")
    return AudioEditRequest(base_version_id=base_version_id, action=action, target_id=target_id, values=values, raw_command=command)


def audio_edit_preview_range(before: AudioTimeline, after: AudioTimeline, edit: AudioEditRequest,
                             duration_seconds: float) -> tuple[float, float]:
    old_clips = {clip.id: clip for track in before.tracks for clip in track.clips}
    new_clips = {clip.id: clip for track in after.tracks for clip in track.clips}
    target = new_clips.get(edit.target_id) or old_clips.get(edit.target_id)
    if edit.action in {"add", "duplicate"}:
        target = next(clip for key, clip in new_clips.items() if key not in old_clips)
    if target is None:
        track = next(track for track in after.tracks if track.id == edit.target_id)
        target = min(track.clips, key=lambda clip: clip.timeline_start_ms) if track.clips else None
    start = target.timeline_start_ms / 1000 if target else 0
    if target and {"fade_in_ms","fade_out_ms"} <= edit.values.keys():
        return start, min(duration_seconds,(target.timeline_start_ms+target.duration_ms)/1000)
    if target and "fade_out_ms" in edit.values:
        start = max(start, (target.timeline_start_ms + target.duration_ms - max(target.fade_out_ms, 2000)) / 1000 - 1)
    elif target and target.gain_points and "gain_points" in edit.values:
        start += max(0, (min(point.offset_ms for point in target.gain_points)-target.envelope_offset_ms) / 1000 - 1)
    elif edit.action == "split":
        start = max(0, edit.values["at_ms"] / 1000 - 3)
    return start, min(duration_seconds, start+10)


def narration_audio_timeline(reference: str, duration_seconds: float) -> AudioTimeline:
    return AudioTimeline(tracks=[TimelineAudioTrack(id="audio-narration", name="配音", role="narration", clips=[
        TimelineAudioClip(id="audio-narration-main", name="当前配音", source_ref=reference,
                          source_start_ms=0, source_end_ms=round(duration_seconds * 1000), timeline_start_ms=0),
    ]), TimelineAudioTrack(id="audio-original", name="原声", role="original"),
        TimelineAudioTrack(id="audio-music", name="音乐", role="music", duck_under_voice=True),
        TimelineAudioTrack(id="audio-effects", name="音效", role="effect")])


def rebind_narration_audio(timeline: AudioTimeline, old_reference: str, new_reference: str,
                           old_duration_ms: int, new_duration_ms: int) -> AudioTimeline:
    result = timeline.model_dump()
    for track in result["tracks"]:
        for clip in track["clips"]:
            if clip["source_ref"] != old_reference:
                continue
            if track["locked"] or clip["locked"]:
                raise ValueError("配音音轨已锁定，请先解锁再重新配音")
            if clip["source_start_ms"] != 0 or abs(clip["source_end_ms"]-old_duration_ms) > 20 or clip["timeline_start_ms"] != 0:
                raise ValueError("配音音轨已分割或移动；当前重配音尚不能保留这些编辑，请先撤销对应音轨编辑")
            clip["source_ref"] = new_reference
            clip["source_end_ms"] = new_duration_ms
    updated = AudioTimeline.model_validate(result)
    if any(clip.timeline_start_ms+clip.duration_ms > new_duration_ms for track in updated.tracks for clip in track.clips):
        raise ValueError("新配音较短，现有音乐或音效会超出成片；请先裁短这些音轨再重试")
    return updated


def _gain_expression(clip: TimelineAudioClip, track_gain: float) -> str:
    points = clip.gain_points
    expression = str(points[-1].gain_db) if points else "0"
    for left, right in reversed(list(zip(points, points[1:]))):
        start, end = left.offset_ms / 1000, right.offset_ms / 1000
        value = f"({left.gain_db}+(t-{start})*{(right.gain_db-left.gain_db)/(end-start)})"
        expression = f"if(lt(t,{end}),{value},{expression})"
    if points:
        expression = f"if(lt(t,{points[0].offset_ms/1000}),{points[0].gain_db},{expression})"
    clock = f"(t+{clip.envelope_offset_ms/1000})"
    expression = re.sub(r"\bt\b", clock, expression)
    gain = f"pow(10,({clip.gain_db+track_gain}+({expression}))/20)"
    if clip.fade_in_ms:
        gain += f"*min(1,max(0,{clock}/{clip.fade_in_ms/1000}))"
    if clip.fade_out_ms:
        end = (clip.envelope_duration_ms or clip.duration_ms) / 1000
        gain += f"*min(1,max(0,({end}-{clock})/{clip.fade_out_ms/1000}))"
    return gain


def build_audio_mix_command(
    timeline: AudioTimeline, duration_seconds: float, resolve_source: Callable[[str], Path], output: Path,
    *, solo_track_ids: set[str] | None = None, start_seconds: float = 0, end_seconds: float | None = None,
) -> list[str]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("音频时间线时长无效")
    end = duration_seconds if end_seconds is None else end_seconds
    if not 0 <= start_seconds < end <= duration_seconds:
        raise ValueError("试听范围必须位于时间线内")
    if solo_track_ids and not solo_track_ids <= {track.id for track in timeline.tracks}:
        raise ValueError("独听轨道不存在")
    command = ["ffmpeg", "-v", "error", "-y"]
    filters, voices, regular, ducked = [], [], [], []
    index = 0
    durations = {}
    for track in timeline.tracks:
        if track.muted or (solo_track_ids and track.id not in solo_track_ids):
            continue
        for clip in track.clips:
            if clip.timeline_start_ms + clip.duration_ms > round(duration_seconds * 1000) + 1:
                raise ValueError("音频片段超出视频时间线，请先裁短或移动")
            if clip.muted:
                continue
            source = resolve_source(clip.source_ref)
            if not source.is_file():
                raise ValueError("音轨的源文件已丢失")
            if source not in durations:
                durations[source] = probe_audio_source(source)
            if clip.source_end_ms > round(durations[source] * 1000) + 20:
                raise ValueError("音频源区间超出实际文件时长")
            command.extend(["-protocol_whitelist", "file,pipe", "-i", str(source)])
            chain = [f"atrim=start={clip.source_start_ms/1000}:end={clip.source_end_ms/1000}",
                     "asetpts=PTS-STARTPTS", "aresample=48000", "aformat=channel_layouts=stereo", "asetpts=N/SR/TB"]
            if clip.denoise_db:
                # A conservative stationary-noise preset; preview remains required before applying.
                chain.append(f"afftdn=nr={clip.denoise_db}:nf=-40:tn=1")
            if clip.loudness_target_lufs is not None:
                chain.extend([f"loudnorm=I={clip.loudness_target_lufs}:LRA=11:TP=-1.5", FINITE_LOUDNESS_FILTER, "aresample=48000", "asetpts=N/SR/TB"])
            gain = _gain_expression(clip, track.gain_db)
            chain.append(f"aeval='val(0)*({gain})|val(1)*({gain})'" if clip.gain_points or clip.fade_in_ms or clip.fade_out_ms
                         else f"volume='{gain}'")
            chain.extend([f"adelay={clip.timeline_start_ms*48}S:all=1", "asetpts=N/SR/TB", f"apad=whole_dur={duration_seconds}",
                          f"atrim=duration={duration_seconds}"])
            label = f"a{index}"
            filters.append(f"[{index}:a:0]{','.join(chain)}[{label}]")
            target = voices if track.role in {"narration", "original"} else ducked if track.duck_under_voice else regular
            target.append(label)
            index += 1
    def mix(labels: list[str], label: str):
        filters.append("".join(f"[{item}]" for item in labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[{label}]")
    if voices:
        mix(voices, "voices")
        if ducked:
            filters.append(f"[voices]asplit={len(ducked)+1}[voiceout]" + "".join(f"[key{i}]" for i in range(len(ducked))))
            regular.append("voiceout")
            for i, label in enumerate(ducked):
                filters.append(f"[{label}][key{i}]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck{i}]")
                regular.append(f"duck{i}")
        else:
            regular.append("voices")
    else:
        regular.extend(ducked)
    if regular:
        mix(regular, "mixed")
    else:
        filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration_seconds}[mixed]")
    filters.append(f"[mixed]alimiter=limit=0.95:level=false:latency=true,atrim=start={start_seconds}:end={end},asetpts=PTS-STARTPTS[out]")
    command.extend(["-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", "pcm_s16le", "-ar", "48000", str(output)])
    return command


def render_audio_mix(*args, **kwargs) -> Path:
    command = build_audio_mix_command(*args, **kwargs)
    subprocess.run(command, capture_output=True, check=True, timeout=600)
    return Path(command[-1])


def probe_audio_source(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "a:0", "-show_entries",
                             "stream=duration:format=duration", "-of", "json", str(path)],
                            capture_output=True, text=True, check=True, timeout=30)
    payload = json.loads(result.stdout)
    if not payload.get("streams"):
        raise ValueError("源文件没有可用音轨")
    raw = payload["streams"][0].get("duration", payload.get("format", {}).get("duration"))
    if raw is None:
        raise ValueError("源音频缺少时长信息")
    duration = float(raw)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("源音频时长无效")
    return duration


def render_stretched_audio(source: Path, source_start_ms: int, source_end_ms: int,
                           rate: float, duration_ms: int, output: Path) -> Path:
    """Local pitch-preserving tempo transform; pad/trim only encoder tail rounding."""
    if not math.isfinite(rate) or not .25 <= rate <= 4 or duration_ms < 10:
        raise ValueError("音频变速参数无效")
    if not 0 <= source_start_ms < source_end_ms <= round(probe_audio_source(source) * 1000) + 20:
        raise ValueError("变速音频源区间无效")
    tempos = []
    remaining = rate
    while remaining > 2:
        tempos.append("atempo=2")
        remaining /= 2
    while remaining < .5:
        tempos.append("atempo=0.5")
        remaining /= .5
    tempos.append(f"atempo={remaining:.9f}")
    chain = [f"atrim=start={source_start_ms / 1000}:end={source_end_ms / 1000}", "asetpts=PTS-STARTPTS",
        *tempos, "aresample=48000", f"apad=whole_dur={duration_ms / 1000}", f"atrim=duration={duration_ms / 1000}"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(source),
        "-af", ",".join(chain), "-c:a", "pcm_s16le", "-ar", "48000", str(output)],
        capture_output=True, check=True, timeout=180)
    return output
