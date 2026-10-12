"""Bounded color controls shared by local previews and montage rendering."""
from app.canvas import preview_size
from pathlib import Path
from typing import Literal
import re
import subprocess
import math
from pydantic import BaseModel, ConfigDict, Field


class VisualEffects(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fade_in_seconds: float = Field(default=0, ge=0, le=5, allow_inf_nan=False)
    fade_out_seconds: float = Field(default=0, ge=0, le=5, allow_inf_nan=False)
    zoom: float = Field(default=1, ge=.1, le=4, allow_inf_nan=False)
    offset_x: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    offset_y: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    rotation: float = Field(default=0, ge=-180, le=180, allow_inf_nan=False)
    beauty_strength: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    skin_exposure: float = Field(default=0, ge=-.5, le=.5, allow_inf_nan=False)
    exposure: float = Field(default=0, ge=-2, le=2, allow_inf_nan=False)
    contrast: float = Field(default=1, ge=.5, le=1.5, allow_inf_nan=False)
    saturation: float = Field(default=1, ge=0, le=2, allow_inf_nan=False)
    temperature: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)


class VisualEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_version_id: str | None = None
    clip_ids: list[str] = Field(min_length=1, max_length=100)
    effects: VisualEffects
    raw_command: str = Field(default="调整画面颜色", max_length=500)


def reusable_color_settings(value: dict) -> dict[str, float]:
    """Extract color only; framing, fades and face treatment belong to the shot."""
    return VisualEffects.model_validate(value).model_dump(
        include={"exposure", "contrast", "saturation", "temperature"}
    )


def color_filters(value: dict | VisualEffects) -> list[str]:
    effect = value if isinstance(value, VisualEffects) else VisualEffects.model_validate(value)
    filters = []
    if effect.exposure:
        filters.append(f"exposure=exposure={effect.exposure}")
    if effect.temperature:
        # Keep RGB operations together before the YUV contrast/saturation stage.
        shift = effect.temperature * .15
        filters.append(f"colorbalance=rm={shift}:bm={-shift}:pl=1")
    if effect.contrast != 1:
        filters.append(f"eq=contrast={effect.contrast}")
    if effect.saturation != 1:
        filters.append(f"hue=s={effect.saturation}")
    return filters


def framing_filter(fit_mode: str, crop_x: float, crop_y: float, width=1080, height=1920) -> str:
    if fit_mode not in {"fill", "fit"} or not (0 <= crop_x <= 1 and 0 <= crop_y <= 1):
        raise ValueError("镜头画面适配参数无效")
    if fit_mode == "fit":
        return (f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    return (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:(iw-ow)*{crop_x}:(ih-oh)*{crop_y}")


def geometry_filters(value: dict | VisualEffects, width: int, height: int) -> list[str]:
    effects = value if isinstance(value, VisualEffects) else VisualEffects.model_validate(value)
    if effects.zoom == 1 and effects.rotation == 0 and effects.offset_x == effects.offset_y == 0:
        return []
    filters = []
    if effects.rotation in {90,-90}:
        filters.append("transpose=clock" if effects.rotation == 90 else "transpose=cclock")
    elif abs(effects.rotation) == 180:
        filters.extend(["hflip","vflip"])
    elif effects.rotation:
        angle = effects.rotation * math.pi / 180
        filters.append(f"rotate={angle:.10f}:ow='ceil(rotw({angle:.10f})/2)*2':oh='ceil(roth({angle:.10f})/2)*2':c=black")
    if effects.zoom != 1:
        filters.append(f"scale=trunc(iw*{effects.zoom}/2)*2:trunc(ih*{effects.zoom}/2)*2")
    dx = round(width * effects.offset_x / 2) * 2
    dy = round(height * effects.offset_y / 2) * 2
    filters.extend([f"pad=w='max(iw,{width})+{2*abs(dx)}':h='max(ih,{height})+{2*abs(dy)}':x='(ow-iw)/2':y='(oh-ih)/2':color=black",
        f"crop={width}:{height}:(iw-ow)/2-({dx}):(ih-oh)/2-({dy})"])
    return filters


def clip_playback_rate(clip) -> float:
    source_duration = clip.source_end_seconds - clip.source_start_seconds
    timeline_duration = clip.timeline_end_seconds - clip.timeline_start_seconds
    if not all(math.isfinite(value) and value > 0 for value in (source_duration, timeline_duration)):
        raise ValueError("镜头源区间和时间线时长必须有效")
    return source_duration / timeline_duration


def clip_video_filters(clip, *, width=1080, height=1920, max_seconds=None) -> str:
    end_seconds = clip.timeline_end_seconds
    if max_seconds is not None:
        end_seconds = min(end_seconds, clip.timeline_start_seconds + max_seconds)
    # Quantize absolute boundaries once: independently rounding each clip length
    # accumulates frames and eventually cuts off later shots when audio ends.
    first_frame = round(clip.timeline_start_seconds * 30)
    last_frame = round(end_seconds * 30)
    frame_count = last_frame - first_frame
    if frame_count < 1:
        raise ValueError("镜头区间不足一个输出帧，请延长镜头")
    # Color before framing keeps letterbox black and avoids tinting padding.
    return ",".join([*color_filters(clip.visual_effects),framing_filter(clip.fit_mode,clip.crop_x,clip.crop_y,width,height),
                     *geometry_filters(clip.visual_effects,width,height),
                     "setsar=1",f"setpts=(PTS-STARTPTS)/{clip_playback_rate(clip):.9f}","fps=30",
                     "tpad=stop_mode=clone:stop_duration=0.1",f"trim=end_frame={frame_count}",
                     *fade_filters(clip)])


def fade_filters(clip) -> list[str]:
    effects = VisualEffects.model_validate(clip.visual_effects)
    frames = round(clip.timeline_end_seconds * 30) - round(clip.timeline_start_seconds * 30)
    envelope = getattr(clip, "fade_envelope_duration_frames", None)
    frames = frames if envelope is None else envelope
    offset = getattr(clip, "fade_envelope_offset_frames", 0)
    rate = getattr(clip, "fade_envelope_rate", 1)
    if not math.isfinite(frames) or not math.isfinite(offset) or not math.isfinite(rate) or rate <= 0 or frames <= 0 or offset < 0:
        raise ValueError("淡入淡出效果时钟无效")
    incoming = round(effects.fade_in_seconds * 30)
    outgoing = round(effects.fade_out_seconds * 30)
    if ((effects.fade_in_seconds and incoming < 2) or
            (effects.fade_out_seconds and outgoing < 2)):
        raise ValueError("淡入或淡出至少需要两帧，请设置为 0.07 秒以上或设为 0 关闭")
    if incoming + outgoing > frames:
        raise ValueError("淡入淡出总时长超过镜头区间，请缩短效果或延长镜头")
    filters = ["settb=1/90000", f"setpts=PTS*{rate:.10f}+{offset/30:.10f}/TB"] if offset or rate != 1 else []
    if incoming:
        filters.append(f"fade=t=in:st=0:d={(incoming-1)/30:.10f}")
    if outgoing:
        filters.append(f"fade=t=out:st={(frames-outgoing)/30:.10f}:d={(outgoing-1)/30:.10f}")
    if offset or rate != 1:
        filters.append("setpts=N/(30*TB)")
    return filters


def with_visual_effects(clip, effects: dict):
    """Color/geometry preserve a cut envelope; new fades target this piece."""
    from dataclasses import replace
    before = VisualEffects.model_validate(clip.visual_effects)
    after = VisualEffects.model_validate(effects)
    changed = (before.fade_in_seconds, before.fade_out_seconds) != (after.fade_in_seconds, after.fade_out_seconds)
    return replace(clip, visual_effects=effects, **({"fade_envelope_offset_frames":0,"fade_envelope_rate":1,
        "fade_envelope_duration_frames":None} if changed else {}))


def render_color_preview(clip, source: Path, output: Path, portrait=None, *, aspect_ratio="9:16", max_seconds=5) -> Path:
    from dataclasses import replace
    width,height=preview_size(aspect_ratio)
    effects=VisualEffects.model_validate(clip.visual_effects)
    if effects.beauty_strength or effects.skin_exposure:
        if portrait is None:
            raise ValueError("本地人像组件未就绪")
        source,metadata=portrait.process(source,clip.source_start_seconds,clip.source_end_seconds,effects.beauty_strength,effects.skin_exposure)
        clip=replace(clip,source_start_seconds=0,source_end_seconds=metadata["duration_seconds"])
    duration=clip.source_end_seconds-clip.source_start_seconds
    if max_seconds is not None:
        duration=min(max_seconds * clip_playback_rate(clip),duration)
    subprocess.run(["ffmpeg","-v","error","-y","-protocol_whitelist","file,pipe",
        "-ss",str(clip.source_start_seconds),"-t",str(duration),"-i",str(source),
        "-vf",clip_video_filters(clip,width=width,height=height,max_seconds=max_seconds),"-an","-c:v","libx264","-preset","veryfast",
        "-crf","20","-pix_fmt","yuv420p","-movflags","+faststart",str(output)],
        capture_output=True,check=True,timeout=120)
    return output


def publish_color_preview(repository, artifact_store, temporary_root: Path, workspace_id: str,
                          task_id: str, clip, source: Path, portrait=None, *, kind: Literal["color_preview", "speed_preview", "canvas_preview"]="color_preview", aspect_ratio="9:16", max_seconds=5):
    import hashlib
    import json
    from dataclasses import asdict
    from tempfile import TemporaryDirectory
    from uuid import uuid4
    from app.models import utc_now
    from app.production import ArtifactRecord, DependencyEdge, JobRun, TransformRun
    width,height=preview_size(aspect_ratio)
    parameters={"clip":asdict(clip),"width":width,"height":height,"max_seconds":max_seconds}
    effects=VisualEffects.model_validate(clip.visual_effects)
    if effects.beauty_strength or effects.skin_exposure:
        if portrait is None:
            raise ValueError("本地人像组件未就绪")
        parameters["portrait_runtime_fingerprint"]=portrait.fingerprint()
    digest=hashlib.sha256(json.dumps(parameters,sort_keys=True).encode()).hexdigest()
    identity=uuid4().hex
    job=repository.create_job_run(JobRun(id=f"job_{identity}",workspace_id=workspace_id,task_id=task_id,
        kind=kind,idempotency_key=f"{kind}:{identity}",input_digest=digest))
    running=repository.claim_job_run(workspace_id,job.id)
    try:
        temporary_root.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(prefix="color-preview-",dir=temporary_root) as directory:
            output=render_color_preview(clip,source,Path(directory)/"preview.mp4",portrait,aspect_ratio=aspect_ratio,max_seconds=max_seconds)
            publication=artifact_store.publish_file(f"artifacts/{task_id}/{job.id}/preview.mp4",output)
        record=repository.create_artifact_record(ArtifactRecord(id=f"artifact_{identity}",workspace_id=workspace_id,
            task_id=task_id,kind=kind,logical_ref=publication.logical_ref,sha256=publication.sha256,
            size_bytes=publication.size_bytes,producer_job_id=job.id,input_refs=[clip.source_path]))
        transform=repository.create_transform_run(TransformRun(id=f"transform_{identity}",workspace_id=workspace_id,
            task_id=task_id,job_id=job.id,transform_type=kind,input_digest=digest,input_refs=[clip.source_path],
            output_refs=[record.logical_ref],engine="local-visual-v3",parameters=parameters,status="succeeded",
            started_at=running.started_at,finished_at=utc_now()))
        repository.create_dependency_edge(DependencyEdge(id=f"edge_{identity}",workspace_id=workspace_id,task_id=task_id,
            upstream_ref=clip.source_path,downstream_ref=record.logical_ref,created_by_transform_id=transform.id))
        repository.finish_job_run(workspace_id,job.id,status="succeeded",output_refs=[record.logical_ref])
        return record
    except Exception as exc:
        repository.finish_job_run(workspace_id,job.id,status="failed",failure_reason=type(exc).__name__,
            impact="没有改变当前镜头或成片。",repair_action="检查源素材、FFmpeg 和外接盘后重试。")
        raise


def _compile_visual_step(command: str, current: dict) -> VisualEffects | None:
    import re
    text=command.strip().casefold().rstrip("。！")
    text=re.sub(r"^(?:把)?(?:(?:当前|这个)?镜头|画面)", "", text)
    base=VisualEffects.model_validate(current)
    if text in {"取消淡入淡出", "关闭淡入淡出"}:
        return base.model_copy(update={"fade_in_seconds":0,"fade_out_seconds":0})
    match=re.fullmatch(r"(?:添加|加上)?(?:画面)?(淡入|淡出)(?:到|为|设为)?\s*(\d+(?:\.\d+)?)\s*秒",text)
    if match:
        key="fade_in_seconds" if match[1]=="淡入" else "fade_out_seconds"
        return VisualEffects.model_validate({**base.model_dump(),key:float(match[2])})
    relative={"放大一点":("zoom",.1),"缩小一点":("zoom",-.1),"向右一点":("offset_x",.05),"向左一点":("offset_x",-.05),"向上一点":("offset_y",-.05),"向下一点":("offset_y",.05)}
    if text.removeprefix("再") in relative:
        key,delta=relative[text.removeprefix("再")]
        return VisualEffects.model_validate({**base.model_dump(),key:round(getattr(base,key)+delta,6)})
    if text in {"重置位置和缩放", "重置画面变换"}:
        return base.model_copy(update={"zoom":1,"rotation":0,"offset_x":0,"offset_y":0})
    match=re.fullmatch(r"(?:放大|缩小|缩放)(?:到|至|为|设为)?\s*(\d+(?:\.\d+)?)\s*倍",text)
    if match:
        return VisualEffects.model_validate({**base.model_dump(),"zoom":float(match[1])})
    match=re.fullmatch(r"(顺时针|逆时针)?旋转\s*([+-]?\d+(?:\.\d+)?)\s*度",text)
    if match:
        return VisualEffects.model_validate({**base.model_dump(),"rotation":float(match[2])*(-1 if match[1]=="逆时针" else 1)})
    match=re.fullmatch(r"(?:向|往)(左|右|上|下)(?:移动|移)(?:画面)?\s*(\d+(?:\.\d+)?)\s*[%％]",text)
    if match:
        direction,amount=match.groups()
        key="offset_x" if direction in {"左","右"} else "offset_y"
        value=getattr(base,key)+float(amount)/100*(-1 if direction in {"左","上"} else 1)
        return VisualEffects.model_validate({**base.model_dump(),key:value})
    if text in {"重置调色", "恢复原色"}:
        return base.model_copy(update={"exposure":0,"contrast":1,"saturation":1,"temperature":0})
    values=base.model_dump()
    relative_color={"亮一点":("exposure",.25),"暗一点":("exposure",-.25),"暖一点":("temperature",.15),"冷一点":("temperature",-.15),
        "皮肤亮一点":("skin_exposure",.15),"肤色亮一点":("skin_exposure",.15)}
    if text.removeprefix("再") in relative_color:
        key,delta=relative_color[text.removeprefix("再")]
        values[key]+=delta
        return VisualEffects.model_validate(values)
    match=re.fullmatch(r"(?:轻(?:微)?(?:一点)?(?:地)?)?(?:美颜|磨皮)(?:一下)?(?:轻一点)?",text)
    if match:
        values["beauty_strength"]=.2 if "轻" in text else .35
        return VisualEffects.model_validate(values)
    match=re.fullmatch(r"(?:关闭|取消)(美颜|磨皮)",text)
    if match:
        values["beauty_strength"]=0
        if match[1]=="美颜":
            values["skin_exposure"]=0
        return VisualEffects.model_validate(values)
    match=re.fullmatch(r"(美颜|磨皮)(?:强度)?(?:设为|改为|为)?\s*(\d+(?:\.\d+)?)",text)
    if match:
        values["beauty_strength"]=float(match[2])
        return VisualEffects.model_validate(values)
    match=re.fullmatch(r"((?:肤色|皮肤)曝光|曝光|对比度|饱和度)(?:(?:设为|改为|到|为)?\s*([+-]?\d+(?:\.\d+)?)|(?:低|小|降低|减小)(?:一点)?|(?:高|大|提高|增加)(?:一点)?)",text)
    if match:
        key={"曝光":"exposure","对比度":"contrast","饱和度":"saturation"}.get(match[1],"skin_exposure")
        if match[2]:
            values[key]=float(match[2])
        else:
            values[key]+=-.1 if re.search(r"低|小|降低|减小",text) else .1
        return VisualEffects.model_validate(values)
    return None


def compile_visual_command(command: str, current: dict) -> VisualEffects | None:
    """Compile every clause before publishing one atomic visual proposal."""
    import re
    parts=[part.strip() for part in re.split(r"[，,；;]|(?:并且|同时|然后)",command.strip().rstrip("。！")) if part.strip()]
    constraints={"其他不要动","其他别动","不要改变脸型","不改变脸型","别改脸型","保留口型","保留配音","配音不变","声音不变"}
    effects=VisualEffects.model_validate(current)
    applied=False
    unknown=[]
    last_beauty=False
    for part in parts:
        if part in constraints:
            continue
        if part=="轻一点" and last_beauty:
            effects=effects.model_copy(update={"beauty_strength":.2})
            continue
        candidate=_compile_visual_step(part,effects.model_dump())
        if candidate is None:
            unknown.append(part)
            last_beauty=False
            continue
        applied=True
        effects=candidate
        last_beauty=bool(re.search(r"美颜|磨皮",part)) and effects.beauty_strength>0
    if unknown and (applied or any(re.search(r"曝光|对比度|饱和度|美颜|磨皮|亮一点|淡入|淡出|缩放|旋转",part) for part in unknown)):
        raise ValueError("这条指令包含尚未支持的限制或操作："+"；".join(unknown)+"。没有应用任何修改，请调整后重新预览")
    return effects if applied else None


_CROP_KEYWORDS=re.compile(r"裁剪|裁切|裁|适配|完整画面|完整显示|填满|铺满|满屏")
_CROP_MODE=re.compile(r"(?:(?:画面|视频|镜头)(?:的)?)?(?:(?:显示)?完整(?:显示|画面)?|(?:填|铺)满(?:画面)?)")
_CROP_AXIS=re.compile(r"(?:(?:画面|视频|镜头)(?:的)?)?(?:裁剪|裁切|裁)?\s*(?:到|为|是)?\s*(水平|横向|左右|垂直|纵向|上下)\s*"
    # A signed value is still an explicit position, so it is read and refused as out of range
    # rather than mistaken for a clause that is missing its amount.
    r"(?:裁剪|裁切|裁|位置|焦点)?\s*(?:调到|设为|改为|移动到|到|为|是)?\s*(-?\d+(?:\.\d+)?)\s*[%％]")
_CROP_MOVE=re.compile(r"(?:裁剪|裁切|裁)?\s*(?:到)?\s*(?:往|向)\s*(左|右|上|下)\s*(?:移动|移|挪|裁|调)\s*(\d+(?:\.\d+)?)\s*[%％]")
_CROP_FOREIGN=re.compile(r"声音|配音|字幕|口型|美颜|磨皮|曝光|对比度|饱和度|亮度|色调|缩放|旋转|淡入|淡出|转场|叠化|速度|删除|替换")
_CROP_AXIS_WORDS={"水平":"x","横向":"x","左右":"x","垂直":"y","纵向":"y","上下":"y"}
_CROP_SAFE_CLAUSES={"其他不要动","其他别动","不要改变脸型","不改变脸型","别改脸型","保留口型","保留配音","配音不变","声音不变","保持镜头顺序、时长、配音和字幕不变","生成修改提案"}
_CROP_LEAD=re.compile(r"^(?:请|帮我|麻烦|把|将|给|让|需要|想)?(?:(?:当前选中的|当前|所选|这个|该)?(?:第[1-9]\d*个)?(?:镜头|画面|视频))?(?:的)?(?:改为|改成)?")
# 整条只有一个方向、又没有给量：既可能是裁剪也可能要求画面位移，落到裁剪侧只为了回参数提示，
# 带上百分比或夹带别的子句时仍留给既有画面位移与视觉词表。
_CROP_BARE_DIRECTION=re.compile(r"(?:往|向)(?:左|右|上|下)(?:移动|移|挪|裁|调)?(?:一点|点|一些)?$")
_CROP_CLARIFY=("这条裁剪要求还缺少可执行参数：可以说“填满画面”“完整显示”，或给出明确位置，"
    "例如“水平裁剪到 40%”“垂直裁剪到 60%”。没有应用任何修改，请补充后重新预览")


def _strip_crop_lead(text):
    for _ in range(3):
        stripped=_CROP_LEAD.sub("",text,count=1)
        if stripped==text:
            return text
        text=stripped
    return text


def _crop_percent(text):
    """An explicit percentage is either honoured exactly or refused, never silently trimmed."""
    value=float(text)
    if not 0<=value<=100:
        raise ValueError(f"裁剪位置要用 0% 到 100% 之间的数值，收到 {text}%。没有应用任何修改，请修改后重新预览")
    return round(value/100,6)


def _compile_crop_clause(text,state):
    """Resolve one clause into the fields it alone changes; the rest keep their current value."""
    if _CROP_MODE.fullmatch(text):
        return {"fit_mode":"fit" if ("完整" in text) else "fill"}
    axis=_CROP_AXIS.fullmatch(text)
    if axis:
        return {f"crop_{_CROP_AXIS_WORDS[axis[1]]}":_crop_percent(axis[2])}
    move=_CROP_MOVE.fullmatch(text)
    if move:
        # crop_x/crop_y place the crop window inside the overflow (0 = source left/top edge),
        # so a left/up move decreases the same axis a picture offset would.
        side,amount=move.groups()
        key="crop_x" if side in {"左","右"} else "crop_y"
        value=round(state[key]+_crop_percent(amount)*(1 if side in {"右","下"} else -1),6)
        if not 0<=value<=1:
            raise ValueError(f"这个裁剪位置会让画面超出可移动范围（{side}移动 {amount}%）。没有应用任何修改，请给出 0% 到 100% 之间的位置")
        return {key:value}
    return None


def compile_crop_command(command,current):
    """Compile an explicit fit/fill or crop position into one ChangeCrop payload.

    Returns None when the command is not a crop request. Every clause is applied in order and
    one unrecognized clause rejects the whole command, so a partly-understood request is never
    reported as an executed change. A missing mode or amount is refused with an actionable
    prompt instead of a guessed step.
    """
    raw=command.strip().casefold().rstrip("。！")
    if not _CROP_KEYWORDS.search(raw) and not _CROP_BARE_DIRECTION.fullmatch(_strip_crop_lead(raw)):
        return None
    ordinal = re.search(r"第([1-9]\d*)个镜头", raw)
    if ordinal and int(ordinal[1]) != current.get("clip_index"):
        raise ValueError("指令中的镜头序号与当前选中镜头不一致，没有生成修改提案")
    fit_mode=current.get("fit_mode")
    crop_x=current.get("crop_x")
    crop_y=current.get("crop_y")
    if fit_mode not in {"fit","fill"} or crop_x is None or crop_y is None:
        raise ValueError("当前镜头的裁剪位置不可用，请先在右侧控件调整后再试")
    state={"fit_mode":fit_mode,"crop_x":crop_x,"crop_y":crop_y}
    applied=False
    unknown=[]
    for part in re.split(r"[，,；;。]|(?:并且|同时|然后)",raw):
        part=_strip_crop_lead(part.strip())
        if not part or part in _CROP_SAFE_CLAUSES:
            continue
        change=_compile_crop_clause(part,state)
        if change is None:
            unknown.append(part)
            continue
        state.update(change)
        applied=True
    if unknown:
        # A crop-shaped clause that resolved to nothing is missing a parameter and needs the
        # parameter prompt; a clause naming another operation is refused as out of scope.
        if not any(_CROP_FOREIGN.search(part) for part in unknown):
            raise ValueError(_CROP_CLARIFY)
        raise ValueError("这条指令包含尚未支持的裁剪限制或操作："+"；".join(unknown)+"。没有应用任何修改，请调整后重新预览")
    if not applied:
        raise ValueError(_CROP_CLARIFY)
    return state
