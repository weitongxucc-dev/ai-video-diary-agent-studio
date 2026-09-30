"""Compile cross-object picture/audio requests against one existing montage."""
from copy import deepcopy
from dataclasses import replace
from app.subtitle_edits import update_subtitle_cue
from app.subtitle_schedule import subtitle_schedule
from app.subtitle_style import compile_subtitle_style_command, validate_style_patch
import re
from app.audio_timeline import apply_audio_edit, compile_audio_command
from app.visual_effects import compile_visual_command, with_visual_effects


def is_indexed_subtitle_command(command):
    return bool(re.match(r'(?:把)?(?:中文|英文)?字幕\s*\d+', command.strip()))


def compile_compound_edit(command, plan, audio, selected_refs, base_version_id, playhead_ms, included_step_indices=None):
    clauses=split_compound_clauses(command)

    constraints={'配音不变','声音不变','画面不变','不改画面','字幕不变','其他不要动','其他别动','不要改变脸型','保留口型'}
    active=[part for part in clauses if part not in constraints]
    # Explicit cue numbers share the same resolver for single and compound edits.
    def audio_subject(part):return re.match(r'(?:把)?(?:当前|这个)?(音乐|配音|原声|音效)(?:片段(\d+))?',part)
    def subtitle_subject(part):return '字幕' in part or part.startswith('英文放在中文')
    has_picture=any(re.match(r'(?:把)?(?:当前|这个)?(?:镜头|画面)',part) for part in active) or any(ref.startswith('clip:') for ref in selected_refs)
    has_subtitle=any(subtitle_subject(part) for part in active)
    if len(active)<2 and not (len(active)==1 and is_indexed_subtitle_command(active[0])):
        return None
    if not has_subtitle and (not has_picture or not any(audio_subject(part) for part in active) or all(audio_subject(part) for part in active)):
        return None
    included=set(range(len(active))) if included_step_indices is None else set(included_step_indices)
    if not included or any(type(index) is not int or index<0 or index>=len(active) for index in included):
        raise ValueError('请保留至少一项有效修改；取消全部请使用取消提案')
    result=deepcopy(plan)
    result.audio_timeline=audio.model_copy(deep=True)
    steps=[]
    last_audio_prefix=None
    last_subtitle=False
    for step_index,part in enumerate(active):
        routed=part
        explicit_picture=bool(re.match(r'(?:把)?(?:当前|这个)?(?:镜头|画面)',part))
        if subtitle_subject(part) or (last_subtitle and not audio_subject(part) and not explicit_picture):
            normalized=part if subtitle_subject(part) else '字幕'+part
            normalized=re.sub(r'^((?:把)?(?:中文|英文)?字幕)\s*(\d+)\s*',r'\1\2',normalized)
            last_audio_prefix=None
            last_subtitle=True
            descriptor={'index':step_index,'enabled':step_index in included,'operation_type':'ConfigureSubtitleTrack','target_ref':'subtitle:all','command':part}
            if step_index not in included:
                if re.match(r'(?:把)?(?:中文|英文)?字幕\d+',normalized):
                    descriptor['operation_type']='UpdateSubtitleCue'
                steps.append(descriptor)
                continue
            cue_match=re.fullmatch(r'(?:把)?(中文|英文)?字幕(\d+)(?:改成|改为)[“「"](.+)[”」"]',normalized,re.S)
            time_match=re.fullmatch(r'(?:把)?(中文|英文)?字幕(\d+)(?:显示|时间改为)(\d+(?:\.\d+)?)到(\d+(?:\.\d+)?)秒',normalized)
            shift_match=re.fullmatch(r'(?:把)?(中文|英文)?字幕(\d+)(?:整体)?(向前|往前|提前|向后|往后|延后|推迟)\s*(\d+(?:\.\d+)?)\s*(毫秒|秒)',normalized)
            match=cue_match or time_match or shift_match
            if match:
                language={'中文':'zh-CN','英文':'en-US'}.get(match[1])
                values={'language':language,'cue_number':int(match[2])}
                if cue_match:
                    values['text']=match[3]
                elif shift_match:
                    offset=float(match[4])/(1000 if match[5]=='毫秒' else 1)
                    values['offset_seconds']=-offset if match[3] in {'向前','往前','提前'} else offset
                else:
                    values.update(start_seconds=float(match[3]),end_seconds=float(match[4]))
                steps.append({**descriptor,'operation_type':'UpdateSubtitleCue','target_ref':f"subtitle:{language or 'selected'}:{match[2]}",'values':values})
                continue
            patch=compile_subtitle_style_command(normalized)
            if patch is None:
                raise ValueError('不能完整执行这项字幕修改：'+part)
            steps.append({**descriptor,'values':validate_style_patch(patch)})
            continue
        last_subtitle=False
        if last_audio_prefix and not audio_subject(part) and not re.match(r'(?:把)?(?:当前|这个)?(?:镜头|画面)',part):
            routed=last_audio_prefix+part
        subject=audio_subject(routed)
        if subject:
            role={'音乐':'music','配音':'narration','原声':'original','音效':'effect'}[subject[1]]
            tracks=[track for track in result.audio_timeline.tracks if track.role==role]
            if len(tracks)!=1:
                raise ValueError('该音轨类型不存在或有多个对象，请先明确选择')
            track=tracks[0]
            target=track.id
            if not track.clips:
                raise ValueError("该音轨还没有音频，请先导入或生成声音")
            if subject[2]:
                index=int(subject[2])-1
                if not 0<=index<len(track.clips):
                    raise ValueError('指定音频片段不存在')
                target=track.clips[index].id
            else:
                selected=[clip.id for clip in track.clips if f'audio:{clip.id}' in selected_refs]
                if len(selected)>1:
                    raise ValueError('请只选择一个音频片段')
                if selected:
                    target=selected[0]
            normalized=subject[1]+routed[subject.end():]
            last_audio_prefix=subject[0]
            descriptor={'index':step_index,'enabled':step_index in included,'operation_type':'EditAudioTimeline','target_ref':f'audio:{target}','command':part}
            if step_index not in included:
                steps.append(descriptor)
                continue
            edit=compile_audio_command(normalized,target,result.audio_timeline,base_version_id,playhead_ms)
            if edit.action not in {'update','track'}:
                raise ValueError('跨对象修改暂不包含音频分割、删除或复制，请分别预览')
            result.audio_timeline=apply_audio_edit(result.audio_timeline,edit,round(result.duration_seconds*1000))
            steps.append({**descriptor,'values':edit.values})
        else:
            last_audio_prefix=None
            subject=re.match(r'(?:把)?镜头\s*(\d+)',part)
            if subject:
                index=int(subject[1])-1
                if not 0<=index<len(result.clips):
                    raise ValueError('指定镜头不存在')
                target=result.clips[index]
                normalized='当前镜头'+part[subject.end():]
            else:
                candidates=[clip for clip in result.clips if f'clip:{clip.id}' in selected_refs]
                if len(candidates)!=1:
                    raise ValueError('请明确画面镜头，例如“镜头2亮一点”')
                target=candidates[0]
                normalized=part
            descriptor={'index':step_index,'enabled':step_index in included,'operation_type':'AdjustVisualEffects','target_ref':f'clip:{target.id}','command':part}
            if step_index not in included:
                steps.append(descriptor)
                continue
            if target.locked:
                raise ValueError('所选镜头已锁定，请先解锁')
            effects=compile_visual_command(normalized,target.visual_effects)
            if effects is None:
                raise ValueError('不能完整执行这项修改：'+part)
            result.clips[result.clips.index(target)]=with_visual_effects(target,effects.model_dump())
            steps.append({**descriptor,'values':effects.model_dump()})
    voice_changed=[track for track in result.audio_timeline.tracks if track.role=='narration']!=[track for track in audio.tracks if track.role=='narration']
    if ('声音不变' in clauses and result.audio_timeline!=audio) or ('配音不变' in clauses and voice_changed):
        raise ValueError('声音不变与音频修改冲突，请明确要保留或修改的内容')
    if any(part in clauses for part in ('画面不变','不改画面')) and result.clips!=plan.clips:
        raise ValueError('画面不变与镜头修改冲突，请明确要保留或修改的内容')
    return result,steps


def compile_compound_selection(command,steps):
    """Resolve a follow-up against this proposal's stable step numbers."""
    text=command.strip().rstrip('。！')
    enabled={step['index'] for step in steps if step['enabled']}
    available={step['index'] for step in steps}
    if text in {'恢复全部修改','全部恢复','保留全部修改'}:
        chosen=available
    else:
        match=re.fullmatch(r'(只保留|保留|取消|去掉|恢复)(画面|声音|音频|字幕)(?:修改|调整)?',text)
        if match:
            action=match[1]
            kind={'画面':'AdjustVisualEffects','字幕':'ConfigureSubtitleTrack'}.get(match[2],'EditAudioTimeline')
            targets={step['index'] for step in steps if step['operation_type']==kind or (match[2]=='字幕' and step['operation_type']=='UpdateSubtitleCue')}
        else:
            number=r'(?:\d+|[一二三四五六七八九十]+)'
            match=re.fullmatch(r'(只保留|保留|取消|去掉|恢复)(第?'+number+r'项?(?:(?:、|和|及|，)第?'+number+r'项?)*)(?:修改)?',text)
            if not match:
                raise ValueError('请明确要保留或取消的项目，例如“取消第2项”或“只保留画面修改”')
            action=match[1]
            def integer(value):
                if value.isdecimal():
                    return int(value)
                digits={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
                if value=='十':
                    return 10
                if value in digits:
                    return digits[value]
                tens=re.fullmatch(r'([一二三四五六七八九]?)十([一二三四五六七八九]?)',value)
                if not tens:
                    raise ValueError('修改序号无法识别，请使用阿拉伯数字')
                return digits.get(tens[1],1)*10+digits.get(tens[2],0)
            targets={integer(value)-1 for value in re.findall(number,match[2])}
        if not targets or not targets<=available:
            raise ValueError('指定修改项不存在，请查看当前提案编号')
        chosen=enabled-targets if action in {'取消','去掉'} else enabled|targets if action=='恢复' else targets
    if not chosen:
        raise ValueError('没有剩余修改；取消整份提案请使用取消按钮')
    if chosen==enabled:
        raise ValueError('保留项没有变化，请指定其他修改')
    return sorted(chosen)


def apply_compound_subtitles(tracks,steps,duration_seconds):
    result=list(tracks)
    for step in steps:
        if step['operation_type'] not in {'ConfigureSubtitleTrack','UpdateSubtitleCue'} or not step['enabled']:
            continue
        if not any(track.status=='valid' for track in result):
            raise ValueError('当前没有可编辑字幕，请先生成字幕')
        values=step['values']
        if step['operation_type']=='ConfigureSubtitleTrack':
            patch=validate_style_patch(values)
            result=[replace(track,style={**track.style,**patch}) if track.status=='valid' else track for track in result]
            continue
        candidates=[track for track in result if track.status=='valid' and (values['language'] is None or track.language==values['language'])]
        if len(candidates)!=1:
            raise ValueError('请明确中文或英文字幕；指定字幕轨必须存在')
        track=candidates[0]
        cues=sorted(track.cues,key=lambda cue:cue.start_seconds)
        index=values['cue_number']-1
        if not 0<=index<len(cues):
            raise ValueError('指定字幕序号不存在')
        cue=cues[index]
        changes={key:values[key] for key in ('text','start_seconds','end_seconds') if key in values}
        if 'offset_seconds' in values:
            changes.update(start_seconds=round(cue.start_seconds+values['offset_seconds'],3),
                end_seconds=round(cue.end_seconds+values['offset_seconds'],3))
        updated=update_subtitle_cue(track,cue.id,**changes)
        subtitle_schedule([replace(updated,style={**updated.style,'enabled':True})],duration_seconds)
        invalidate_translation=track.language=='zh-CN' and 'text' in values and values['text']!=cue.text
        result=[updated if item.id==track.id else item for item in result if not(invalidate_translation and item.language=='en-US' and item.source_language=='zh-CN')]
        changed_cue=next(item for item in updated.cues if item.id==cue.id)
        timing_changed=(changed_cue.start_seconds,changed_cue.end_seconds)!=(cue.start_seconds,cue.end_seconds)
        if track.language=='zh-CN' and timing_changed and not invalidate_translation:
            translations=[item for item in result if item.status=='valid' and item.language=='en-US'
                          and item.source_language=='zh-CN']
            if len(translations)>1:
                raise ValueError('存在多个关联英文字幕轨，请先检查译文来源关联；两轨时间未改动')
            if translations:
                translation=translations[0]
                source_ids=[item.id for item in updated.cues]
                references=[item.source_cue_id for item in translation.cues]
                if (len(source_ids)!=len(set(source_ids)) or not references
                    or any(reference is None or reference not in source_ids for reference in references)
                    or len(references)!=len(set(references)) or references.count(cue.id)!=1):
                    raise ValueError('译文与原字幕的对应关系缺失或有歧义，请先检查；两轨时间未改动')
                synchronized=replace(translation,cues=[replace(item,
                    start_seconds=changed_cue.start_seconds,end_seconds=changed_cue.end_seconds)
                    if item.source_cue_id==cue.id else item for item in translation.cues],
                    artifact_ref=None,transform_id=None)
                subtitle_schedule([replace(synchronized,style={**synchronized.style,'enabled':True})],duration_seconds)
                result=[synchronized if item.id==translation.id else item for item in result]
    return result


def split_compound_clauses(command):
    pairs={'“':'”','「':'」','"':'"'}
    stack=[]
    parts=[]
    buffer=''
    index=0
    while index<len(command):
        char=command[index]
        if stack and char==stack[-1]:
            stack.pop()
            buffer+=char
            index+=1
            continue
        if char in pairs:
            stack.append(pairs[char])
            buffer+=char
            index+=1
            continue
        separator=None
        if not stack:
            separator=next((word for word in ('，',',','；',';','同时','并且','然后') if command.startswith(word,index)),None)
        if separator:
            if buffer.strip():
                parts.append(buffer.strip())
            buffer=''
            index+=len(separator)
        else:
            buffer+=char
            index+=1
    if stack:
        raise ValueError('引用文字的引号未闭合，请补齐后预览')
    if buffer.strip().rstrip('。！'):
        parts.append(buffer.strip().rstrip('。！'))
    return parts
