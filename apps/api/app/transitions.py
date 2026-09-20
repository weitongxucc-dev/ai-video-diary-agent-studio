"""Cut-aligned dissolves using real outgoing source handles, without ripple."""
from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess

from app.visual_effects import clip_playback_rate


def dissolve_frames(clips) -> list[int]:
    result=[]
    for index,clip in enumerate(clips):
        value=getattr(clip,'transition_in_seconds',0)
        if not math.isfinite(value) or not 0 <= value <= 2:
            raise ValueError('叠化时长须为0–2秒，0表示硬切')
        frames=round(value*30)
        if value and frames < 2:
            raise ValueError('叠化至少需要两帧，请设为0.07秒以上或0关闭')
        if frames:
            if index==0:
                raise ValueError('第一个镜头之前没有可叠化的镜头')
            previous=clips[index-1]
            if abs(previous.timeline_end_seconds-clip.timeline_start_seconds)>.001:
                raise ValueError('叠化需要时间线上连续的两个镜头')
            occupied=round(clip.timeline_end_seconds*30)-round(clip.timeline_start_seconds*30)
            if frames>occupied:
                raise ValueError('叠化超过后一个镜头时长，请缩短转场')
            if previous.visual_effects.get('fade_out_seconds') or clip.visual_effects.get('fade_in_seconds'):
                raise ValueError('切点已有黑场淡入淡出，请先关闭该效果再设置叠化')
        result.append(frames)
    return result


def extend_dissolve_sources(clips):
    """Extend outgoing pictures only; the stored source ranges stay immutable."""
    transitions=dissolve_frames(clips)
    extended=[]
    for index,clip in enumerate(clips):
        extra=transitions[index+1] if index+1<len(clips) else 0
        if not extra:
            extended.append(clip)
            continue
        rate=clip_playback_rate(clip)
        end=clip.source_end_seconds+extra/30*rate
        source=Path(clip.source_path)
        metadata=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
            '-show_entries','stream=duration','-of','json',str(source)],timeout=20))
        streams=metadata.get('streams',[])
        duration=float(streams[0]['duration']) if streams and streams[0].get('duration') else 0
        if not math.isfinite(duration) or end>duration+.001:
            raise ValueError(f'镜头 {index+1} 的结尾缺少 {extra/30*rate:.2f} 秒转场素材余量，请缩短叠化或调整源区间')
        envelope=clip.fade_envelope_duration_frames
        if envelope is None and (clip.visual_effects.get('fade_in_seconds') or clip.visual_effects.get('fade_out_seconds')):
            envelope=round(clip.timeline_end_seconds*30)-round(clip.timeline_start_seconds*30)
        extended.append(replace(clip,source_end_seconds=end,timeline_end_seconds=clip.timeline_end_seconds+extra/30,
            fade_envelope_duration_frames=envelope))
    return extended,transitions


def join_picture_filters(clips, transitions):
    """Return one filter chain ending at vbase, preserving the absolute clock."""
    filters=[]
    for index in range(len(clips)):
        filters.append(f'[v{index}]settb=1/30,format=yuv420p[p{index}]')
    previous='[p0]'
    for index in range(1,len(clips)):
        output=f'[joinraw{index}]'
        if transitions[index]:
            at=round(clips[index].timeline_start_seconds*30)/30
            duration=transitions[index]/30
            filters.append(f'{previous}[p{index}]xfade=transition=fade:duration={duration:.10f}:offset={at:.10f}{output}')
        else:
            filters.append(f'{previous}[p{index}]concat=n=2:v=1:a=0{output}')
        previous=f'[joined{index}]'
        filters.append(f'{output}settb=1/30{previous}')
    total=round(clips[-1].timeline_end_seconds*30)
    filters.append(f'{previous}fps=30,trim=end_frame={total},setpts=PTS-STARTPTS[vbase]')
    return filters
