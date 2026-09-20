"""Bounded color controls shared by local previews and montage rendering."""
from app.canvas import preview_size
from pathlib import Path
from typing import Literal
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
        if portrait is None: raise ValueError("本地人像组件未就绪")
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


def compile_visual_command(command: str, current: dict) -> VisualEffects | None:
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
        direction,amount=match.groups(); key="offset_x" if direction in {"左","右"} else "offset_y"
        value=getattr(base,key)+float(amount)/100*(-1 if direction in {"左","上"} else 1)
        return VisualEffects.model_validate({**base.model_dump(),key:value})
    tokens=("亮一点","暗一点","曝光","对比度","饱和度","暖一点","冷一点","重置调色","恢复原色","美颜","磨皮","皮肤亮一点","肤色亮一点")
    if not any(token in text for token in tokens):
        return None
    for constraint in ("其他不要动","其他别动","不要改变脸型","不改变脸型","别改脸型","保留口型","保留配音","配音不变","声音不变"):
        text=text.replace(constraint, "")
    if any(token in text for token in ("不要", "别", "裁剪", "替换", "配音", "字幕", "音乐", "音量", "降噪", "放大", "缩小", "缩放", "旋转", "移动", "删除", "复制", "logo")):
        raise ValueError("这条指令包含调色之外的限制或操作，当前请分别预览，以免漏改")
    if "重置调色" in text or "恢复原色" in text:
        current=VisualEffects.model_validate(current)
        return current.model_copy(update={"exposure":0,"contrast":1,"saturation":1,"temperature":0})
    import re
    values=VisualEffects.model_validate(current).model_dump()
    if "美颜" in text or "磨皮" in text:
        values["beauty_strength"] = 0 if "关闭" in text or "取消" in text else .2 if "轻" in text else .35
        if "美颜" in text and ("关闭" in text or "取消" in text): values["skin_exposure"]=0
        amount=re.search(r"(?:磨皮|美颜)(?:强度)?(?:设为|改为|为)?\s*(\d+(?:\.\d+)?)",text)
        if amount: values["beauty_strength"]=float(amount[1])
    skin_amount=re.search(r"(?:肤色|皮肤)曝光(?:设为|改为|为)?\s*([+-]?\d+(?:\.\d+)?)",text)
    if skin_amount:
        values["skin_exposure"]=float(skin_amount[1])
        text=text[:skin_amount.start()]+text[skin_amount.end():]
    if "皮肤亮一点" in text or "肤色亮一点" in text: values["skin_exposure"] += .15
    elif "亮一点" in text: values["exposure"] += .25
    if "暗一点" in text: values["exposure"] -= .25
    if "暖一点" in text: values["temperature"] += .15
    if "冷一点" in text: values["temperature"] -= .15
    for word,key in (("曝光","exposure"),("对比度","contrast"),("饱和度","saturation")):
        if word not in text: continue
        match=re.search(word+r"(?:设为|改为|到|为)?\s*([+-]?\d+(?:\.\d+)?)",text)
        if match: values[key]=float(match[1])
        elif re.search(word+r"(?:低|小|降低|减小)",text): values[key]-=.1
        elif re.search(word+r"(?:高|大|提高|增加)",text): values[key]+=.1
        else: raise ValueError(f"请指定{word}的数值或增减方向")
    return VisualEffects.model_validate(values)
