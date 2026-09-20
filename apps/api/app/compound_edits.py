"""Compile cross-object picture/audio requests against one existing montage."""
from copy import deepcopy
import re
from app.audio_timeline import apply_audio_edit, compile_audio_command
from app.visual_effects import compile_visual_command, with_visual_effects


def compile_compound_edit(command, plan, audio, selected_refs, base_version_id, playhead_ms, included_step_indices=None):
    clauses=[part.strip() for part in re.split(r'[，,；;]|(?:同时|并且|然后)',command.rstrip('。！')) if part.strip()]
    constraints={'配音不变','声音不变','画面不变','不改画面','字幕不变','其他不要动','其他别动','不要改变脸型','保留口型'}
    active=[part for part in clauses if part not in constraints]
    # Single-domain requests continue through their existing exact-edit consumer.
    def audio_subject(part):return re.match(r'(?:把)?(?:当前|这个)?(音乐|配音|原声|音效)(?:片段(\d+))?',part)
    has_picture=any(re.match(r'(?:把)?(?:当前|这个)?(?:镜头|画面)',part) for part in active) or any(ref.startswith('clip:') for ref in selected_refs)
    if len(active)<2 or not has_picture or not any(audio_subject(part) for part in active) or all(audio_subject(part) for part in active):return None
    included=set(range(len(active))) if included_step_indices is None else set(included_step_indices)
    if not included or any(type(index) is not int or index<0 or index>=len(active) for index in included):
        raise ValueError('请保留至少一项有效修改；取消全部请使用取消提案')
    result=deepcopy(plan);result.audio_timeline=audio.model_copy(deep=True)
    steps=[];last_audio_prefix=None
    for step_index,part in enumerate(active):
        routed=part
        if last_audio_prefix and not audio_subject(part) and not re.match(r'(?:把)?(?:当前|这个)?(?:镜头|画面)',part):
            routed=last_audio_prefix+part
        subject=audio_subject(routed)
        if subject:
            role={'音乐':'music','配音':'narration','原声':'original','音效':'effect'}[subject[1]]
            tracks=[track for track in result.audio_timeline.tracks if track.role==role]
            if len(tracks)!=1:raise ValueError('该音轨类型不存在或有多个对象，请先明确选择')
            track=tracks[0];target=track.id
            if not track.clips:raise ValueError("该音轨还没有音频，请先导入或生成声音")
            if subject[2]:
                index=int(subject[2])-1
                if not 0<=index<len(track.clips):raise ValueError('指定音频片段不存在')
                target=track.clips[index].id
            else:
                selected=[clip.id for clip in track.clips if f'audio:{clip.id}' in selected_refs]
                if len(selected)>1:raise ValueError('请只选择一个音频片段')
                if selected:target=selected[0]
            normalized=subject[1]+routed[subject.end():]
            last_audio_prefix=subject[0]
            descriptor={'index':step_index,'enabled':step_index in included,'operation_type':'EditAudioTimeline','target_ref':f'audio:{target}','command':part}
            if step_index not in included:
                steps.append(descriptor);continue
            edit=compile_audio_command(normalized,target,result.audio_timeline,base_version_id,playhead_ms)
            if edit.action not in {'update','track'}:raise ValueError('跨对象修改暂不包含音频分割、删除或复制，请分别预览')
            result.audio_timeline=apply_audio_edit(result.audio_timeline,edit,round(result.duration_seconds*1000))
            steps.append({**descriptor,'values':edit.values})
        else:
            last_audio_prefix=None
            subject=re.match(r'(?:把)?镜头\s*(\d+)',part)
            if subject:
                index=int(subject[1])-1
                if not 0<=index<len(result.clips):raise ValueError('指定镜头不存在')
                target=result.clips[index];normalized='当前镜头'+part[subject.end():]
            else:
                candidates=[clip for clip in result.clips if f'clip:{clip.id}' in selected_refs]
                if len(candidates)!=1:raise ValueError('请明确画面镜头，例如“镜头2亮一点”')
                target=candidates[0];normalized=part
            descriptor={'index':step_index,'enabled':step_index in included,'operation_type':'AdjustVisualEffects','target_ref':f'clip:{target.id}','command':part}
            if step_index not in included:
                steps.append(descriptor);continue
            if target.locked:raise ValueError('所选镜头已锁定，请先解锁')
            effects=compile_visual_command(normalized,target.visual_effects)
            if effects is None:raise ValueError('不能完整执行这项修改：'+part)
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
        match=re.fullmatch(r'(只保留|保留|取消|去掉|恢复)(画面|声音|音频)(?:修改|调整)?',text)
        if match:
            action=match[1];kind='AdjustVisualEffects' if match[2]=='画面' else 'EditAudioTimeline'
            targets={step['index'] for step in steps if step['operation_type']==kind}
        else:
            number=r'(?:\d+|[一二三四五六七八九十]+)'
            match=re.fullmatch(r'(只保留|保留|取消|去掉|恢复)(第?'+number+r'项?(?:(?:、|和|及|，)第?'+number+r'项?)*)(?:修改)?',text)
            if not match:raise ValueError('请明确要保留或取消的项目，例如“取消第2项”或“只保留画面修改”')
            action=match[1]
            def integer(value):
                if value.isdecimal():return int(value)
                digits={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
                if value=='十':return 10
                if value in digits:return digits[value]
                tens=re.fullmatch(r'([一二三四五六七八九]?)十([一二三四五六七八九]?)',value)
                if not tens:raise ValueError('修改序号无法识别，请使用阿拉伯数字')
                return digits.get(tens[1],1)*10+digits.get(tens[2],0)
            targets={integer(value)-1 for value in re.findall(number,match[2])}
        if not targets or not targets<=available:raise ValueError('指定修改项不存在，请查看当前提案编号')
        chosen=enabled-targets if action in {'取消','去掉'} else enabled|targets if action=='恢复' else targets
    if not chosen:raise ValueError('没有剩余修改；取消整份提案请使用取消按钮')
    if chosen==enabled:raise ValueError('保留项没有变化，请指定其他修改')
    return sorted(chosen)
