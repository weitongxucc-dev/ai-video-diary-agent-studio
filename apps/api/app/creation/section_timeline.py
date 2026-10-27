"""Pure section replacement; repositories own publication, artifacts and undo."""
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256

from app.audio_timeline import AudioTimeline, TimelineAudioClip, narration_audio_timeline
from app.creation.contracts import SubtitleCue, SubtitleTrack
from app.creation.transactions import clone_subtitle_track
from app.graphic_overlays import parse_overlay, validate_overlays
from app.linked_timeline import splice_audio
from app.montage import MontageClip
from app.subtitle_schedule import subtitle_schedule
from app.transitions import dissolve_frames


def _ms(seconds):
    return round(seconds * 1000)


def _section(montage, section_id):
    cues = [cue for cue in montage['cues'] if cue['source_section_id'] == section_id]
    ids = {cue['id'] for cue in cues}
    selected, start, end = _section_bounds([cue['id'] for cue in montage['cues']], ids,
        montage['clips'], montage['duration_seconds'])
    return cues, selected, start, end


def _section_bounds(cue_order, ids, all_clips, duration_seconds):
    positions = [index for index, cue_id in enumerate(cue_order) if cue_id in ids]
    if positions and positions != list(range(positions[0], positions[-1] + 1)):
        raise ValueError('目标段旁白顺序不连续，不能猜测原声音区间')
    clips = sorted((clip for clip in all_clips if clip['cue_id'] in ids),
                   key=lambda clip: clip['timeline_start_seconds'])
    if not clips:
        raise ValueError('当前时间线没有目标段，不能猜测替换位置')
    start, end = _ms(clips[0]['timeline_start_seconds']), _ms(clips[-1]['timeline_end_seconds'])
    cursor = start
    for clip in clips:
        left, right = _ms(clip['timeline_start_seconds']), _ms(clip['timeline_end_seconds'])
        if left != cursor or right <= left:
            raise ValueError('目标段镜头不是连续区间，须先核对实际联动范围')
        cursor = right
    if not 0 <= start < end <= _ms(duration_seconds):
        raise ValueError('目标段区间超出当前时间线')
    if any(clip['cue_id'] not in ids and _ms(clip['timeline_start_seconds']) < end
           and _ms(clip['timeline_end_seconds']) > start for clip in all_clips):
        raise ValueError('其他段与目标区间交叠，须先核对实际联动范围')
    return clips, start, end


def _clock(current, section_id, local):
    cues, clips, start, end = _section(current, section_id)
    new_cues, new_clips, local_start, local_end = _section(local, section_id)
    if (local_start != 0 or local_end != _ms(local['duration_seconds'])
            or len(new_cues) != len(local['cues']) or len(new_clips) != len(local['clips'])):
        raise ValueError('局部计划必须只包含目标段，并从零覆盖其实际时长')
    length = local_end
    delta = length - (end - start)

    def move(position):
        if position <= start:
            return position
        if position >= end:
            return position + delta
        return start + round((position - start) * length / (end - start))

    return cues, clips, start, end, length, delta, move


def _scale_audio(clip, duration, at):
    """Keep source ranges and envelope coordinates, as in retime_clip."""
    factor = duration / clip.duration_ms
    offset = round(clip.envelope_offset_ms * factor)
    envelope = max(offset + duration, round((clip.envelope_duration_ms or clip.duration_ms) * factor))
    fade_in = round(clip.fade_in_ms * factor)
    points = {round(point.offset_ms * factor): point.gain_db for point in clip.gain_points}
    return TimelineAudioClip.model_validate({**clip.model_dump(), 'timeline_start_ms': at,
        'playback_rate': (clip.source_end_ms - clip.source_start_ms) / duration,
        'envelope_offset_ms': offset, 'envelope_duration_ms': envelope,
        'fade_in_ms': fade_in, 'fade_out_ms': min(round(clip.fade_out_ms * factor), envelope - fade_in),
        'gain_points': [{'offset_ms': position, 'gain_db': gain} for position, gain in points.items()]})


def _reflow_audio(timeline, start, end, duration, delta, move):
    if not delta:
        return timeline.model_copy(deep=True)
    for track in timeline.tracks:
        for clip in track.clips:
            left, right = clip.timeline_start_ms, clip.timeline_start_ms + clip.duration_ms
            if (track.locked or clip.locked) and (left < end and right > start or left >= end):
                raise ValueError('目标时长变化会改变已锁定音轨或片段，请先解锁')
    intervals = [(a, b, a) for a, b in [(0, start), (start, end), (end, duration)] if a < b]
    result = splice_audio(timeline, intervals)
    for track in result.tracks:
        clips = []
        for clip in track.clips:
            at = clip.timeline_start_ms
            if start <= at < end:
                clip = _scale_audio(clip, move(at + clip.duration_ms) - move(at), move(at))
            elif at >= end:
                clip = clip.model_copy(update={'timeline_start_ms': at + delta})
            clips.append(clip)
        track.clips = clips
    return AudioTimeline.model_validate(result.model_dump())


def _replace_audio(current, section_id, local, rows, start, end, length, delta, move):
    if not rows or len({row['section_id'] for row in rows}) != len(rows):
        raise ValueError('局部配音替换缺少按确认文案排序的真实分段音频引用')
    target = next((row for row in rows if row['section_id'] == section_id), None)
    if target is None or _ms(target['duration_seconds']) != length or local.get('narration_audio_path') != target['audio_reference']:
        raise ValueError('局部计划与目标段真实配音引用或时长不一致')
    if local.get('audio_timeline') is not None and any(track['clips'] for track in local['audio_timeline']['tracks']):
        raise ValueError('局部额外音轨尚未明确与当前音轨的合并关系，未覆盖已有音轨')
    if current.get('audio_timeline') is None:
        # Rebuild legacy linear narration only from actual segment references.
        order, durations = [], {}
        for cue in current['cues']:
            key = cue['source_section_id']
            if not order or order[-1] != key:
                order.append(key)
            durations[key] = durations.get(key, 0) + _ms(cue['duration_seconds'])
        if order != [row['section_id'] for row in rows] or any(
                row['section_id'] != section_id and durations[row['section_id']] != _ms(row['duration_seconds']) for row in rows):
            raise ValueError('原配音不是可证明的线性分段时钟，不能重建并覆盖已有配音')
        if sum(durations[key] for key in order[:order.index(section_id)]) != start or durations[section_id] != end - start:
            raise ValueError('目标段画面与原配音时钟不一致，不能按时间重绑声音')
        if not current.get('narration_audio_path'):
            raise ValueError('原时间线缺少实际组合配音引用，不能猜测并覆盖声音')
        graph = narration_audio_timeline(current['narration_audio_path'], sum(durations.values()) / 1000)
        return _replace_audio({**current, 'audio_timeline': graph.model_dump()}, section_id, local,
                              rows, start, end, length, delta, move)

    timeline = AudioTimeline.model_validate(current['audio_timeline'])
    pairs = [(track, clip) for track in timeline.tracks if track.role == 'narration' for clip in track.clips
             if clip.timeline_start_ms < end and clip.timeline_start_ms + clip.duration_ms > start]
    if len(pairs) != 1:
        raise ValueError('目标旁白来源被分割或存在多条关联，不能猜测替换声音')
    track, original = pairs[0]
    if track.locked or original.locked:
        raise ValueError('目标旁白音轨或片段已锁定，请先解锁')
    if not original.timeline_start_ms <= start < end <= original.timeline_start_ms + original.duration_ms:
        raise ValueError('原旁白没有完整覆盖目标段，不能沿用失配声音')
    segment_ids = {cue['narration_segment_id'] for cue in current['cues'] if cue['source_section_id'] == section_id}
    linked = original.id in {f'audio-narration-{identity}' for identity in segment_ids}
    old_voice_start = 0
    for cue in current['cues']:
        if cue['source_section_id'] == section_id:
            break
        old_voice_start += _ms(cue['duration_seconds'])
    source_start = original.source_start_ms + round((start - original.timeline_start_ms) * original.playback_rate)
    source_end = original.source_start_ms + round((end - original.timeline_start_ms) * original.playback_rate)
    combined = original.source_ref == current.get('narration_audio_path') and source_start == old_voice_start and (
        source_end - source_start == sum(_ms(cue['duration_seconds']) for cue in current['cues'] if cue['source_section_id'] == section_id))
    if not linked and not combined:
        raise ValueError('目标声音缺少可核对的分段来源或组合源区间，不能只按重叠时间改绑')
    template = splice_audio(AudioTimeline(tracks=[track.model_copy(update={'clips':[original]}, deep=True)]),
                            [(start, end, start)]).tracks[0].clips[0]
    replacement = _scale_audio(template, length, start)
    replacement = TimelineAudioClip.model_validate({**replacement.model_dump(),
        'id': f"audio-narration-{target['narration_segment_id']}", 'source_ref': target['audio_reference'],
        'source_start_ms': 0, 'source_end_ms': length, 'playback_rate': 1.0})
    intervals = [(a, b, c) for a, b, c in [(0, start, 0), (end, _ms(current['duration_seconds']), end + delta)] if a < b]
    retained_sections = []
    if combined and not linked:
        # Give retained sections their explicit source association as well. A
        # later revision must not depend on a stale whole-narration pointer.
        intervals, cursor, groups = [], 0, []
        for cue in current['cues']:
            right = cursor + _ms(cue['duration_seconds'])
            if groups and groups[-1][0] == cue['source_section_id']:
                groups[-1][2] = right
            else:
                groups.append([cue['source_section_id'], cursor, right])
            cursor = right
        row_by_section = {row['section_id']: row for row in rows}
        for key, left, right in groups:
            if key != section_id:
                at = left + (delta if left >= end else 0)
                intervals.append((left, right, at))
                retained_sections.append((at, at + right - left, row_by_section[key]['narration_segment_id']))
        if cursor < _ms(current['duration_seconds']):
            intervals.append((cursor, _ms(current['duration_seconds']), cursor + delta))
    voice = splice_audio(AudioTimeline(tracks=[item for item in timeline.tracks if item.role == 'narration']), intervals)
    for voice_track in voice.tracks:
        for clip in voice_track.clips:
            match = [(left, right, identity) for left, right, identity in retained_sections
                     if left == clip.timeline_start_ms and right == clip.timeline_start_ms + clip.duration_ms]
            if len(match) == 1:
                clip.id = f'audio-narration-{match[0][2]}'
    next(item for item in voice.tracks if item.id == track.id).clips.append(replacement)
    others = _reflow_audio(AudioTimeline(tracks=[item for item in timeline.tracks if item.role != 'narration']),
        start, end, _ms(current['duration_seconds']), delta, move)
    by_id = {item.id: item for item in [*voice.tracks, *others.tracks]}
    for item in by_id.values():
        item.clips.sort(key=lambda clip: clip.timeline_start_ms)
    return AudioTimeline.model_validate({'tracks': [by_id[item.id].model_dump() for item in timeline.tracks]})


def _restore_audio(current, local, start, end, length, delta):
    """Replace only the saved interval with its actual source ranges and clocks."""
    if local.get('audio_timeline') is None or current.get('audio_timeline') is None:
        raise ValueError('恢复目标段缺少实际音轨图，不能从旧配音时长猜测声音')
    old = AudioTimeline.model_validate(current['audio_timeline'])
    saved = AudioTimeline.model_validate(local['audio_timeline'])
    intervals = [(a, b, at) for a, b, at in
        [(0, start, 0), (end, _ms(current['duration_seconds']), start + length)] if a < b]
    retained = splice_audio(old, intervals)
    by_id = {track.id: track for track in retained.tracks}
    used = {clip.id for track in retained.tracks for clip in track.clips} | set(by_id)
    segment_ids = {cue['narration_segment_id'] for cue in local['cues']}
    for track in saved.tracks:
        if track.id in by_id:
            result = by_id[track.id]
            if result.clips and result.model_dump(exclude={'clips'}) != track.model_dump(exclude={'clips'}):
                raise ValueError('恢复会改变其他段共用的音轨设置，须先核对关联范围')
            if not result.clips:
                result = track.model_copy(update={'clips': []}, deep=True)
                by_id[track.id] = result
        else:
            result = track.model_copy(update={'clips': []}, deep=True)
            by_id[track.id] = result
        for clip in track.clips:
            if not 0 <= clip.timeline_start_ms < clip.timeline_start_ms + clip.duration_ms <= length:
                raise ValueError('已保存音频片段超出目标局部区间，不能恢复')
            restored = clip.model_copy(deep=True)
            restored.timeline_start_ms += start
            if track.role == 'narration' and len(segment_ids) == 1 and len(track.clips) == 1:
                restored.id = f'audio-narration-{next(iter(segment_ids))}'
            if restored.id in used:
                restored.id = 'audio-restore-' + sha256(repr((track.id, restored.model_dump())).encode()).hexdigest()[:24]
            used.add(restored.id)
            result.clips.append(restored)
    for track in by_id.values():
        track.clips.sort(key=lambda clip: clip.timeline_start_ms)
    return AudioTimeline.model_validate({'tracks': [track.model_dump() for track in by_id.values()]})


def replace_section_montage(current: dict, section_id, local: dict, *, section_audio=None, restore_audio=False) -> dict:
    old_cues, old_clips, start, end, length, delta, move = _clock(current, section_id, local)
    if current['status'] == 'rendering':
        raise ValueError('当前时间线正在渲染，不能替换目标段')
    if any(clip.get('locked') for clip in old_clips):
        raise ValueError('目标段含已锁定镜头，请先解锁')
    result = deepcopy(current)
    cue_ids = {cue['id'] for cue in old_cues}
    result['cues'], inserted = [], False
    for cue in current['cues']:
        if cue['id'] in cue_ids:
            if not inserted:
                result['cues'].extend(deepcopy(local['cues']))
                inserted = True
        else:
            result['cues'].append(deepcopy(cue))
    clips = []
    for original in current['clips']:
        if original['cue_id'] in cue_ids:
            continue
        clip = deepcopy(original)
        if _ms(clip['timeline_start_seconds']) >= end and delta:
            if clip.get('locked'):
                raise ValueError('目标时长变化会移动已锁定镜头，请先解锁')
            clip['timeline_start_seconds'] = (_ms(clip['timeline_start_seconds']) + delta) / 1000
            clip['timeline_end_seconds'] = (_ms(clip['timeline_end_seconds']) + delta) / 1000
        clips.append(clip)
    local_clips = sorted(deepcopy(local['clips']), key=lambda clip: clip['timeline_start_seconds'])
    if not restore_audio:
        local_clips[0]['transition_in_seconds'] = old_clips[0].get('transition_in_seconds', 0)
    for clip in local_clips:
        clip['timeline_start_seconds'] = (_ms(clip['timeline_start_seconds']) + start) / 1000
        clip['timeline_end_seconds'] = (_ms(clip['timeline_end_seconds']) + start) / 1000
    result['clips'] = sorted([*clips, *local_clips], key=lambda clip: clip['timeline_start_seconds'])
    if len({cue['id'] for cue in result['cues']}) != len(result['cues']) or len({clip['id'] for clip in result['clips']}) != len(result['clips']):
        raise ValueError('局部计划标识与其他段冲突，未覆盖当前时间线')
    dissolve_frames([MontageClip(**clip) for clip in result['clips']])
    old_overlays = current.get('graphic_overlays', [])
    if restore_audio:
        if any(_ms(item['start_seconds']) < end and _ms(item['end_seconds']) > start and
               not start <= _ms(item['start_seconds']) < _ms(item['end_seconds']) <= end for item in old_overlays):
            raise ValueError('恢复涉及跨段图层，局部历史不足以还原其完整时钟')
        old_overlays = [item for item in old_overlays if not start <= _ms(item['start_seconds']) < _ms(item['end_seconds']) <= end]
    overlays = [{**deepcopy(item), 'start_seconds': move(_ms(item['start_seconds'])) / 1000,
                 'end_seconds': move(_ms(item['end_seconds'])) / 1000} for item in old_overlays]
    additions = deepcopy(local.get('graphic_overlays', []))
    validate_overlays(additions, length / 1000)
    addition_ids = {item['id'] for item in additions}
    for item in overlays:
        if item['id'] in addition_ids and not start <= _ms(item['start_seconds']) < _ms(item['end_seconds']) <= start + length:
            raise ValueError('局部图层标识与其他段冲突，未覆盖原图层')
    overlays = [item for item in overlays if item['id'] not in addition_ids]
    overlays.extend({**item, 'start_seconds': (start + _ms(item['start_seconds'])) / 1000,
                     'end_seconds': (start + _ms(item['end_seconds'])) / 1000} for item in additions)
    duration = (_ms(current['duration_seconds']) + delta) / 1000
    validate_overlays(overlays, duration)
    result['graphic_overlays'] = overlays
    result['audio_timeline'] = (_restore_audio(current, local, start, end, length, delta) if restore_audio else
        _replace_audio(current, section_id, local, section_audio, start, end, length, delta, move)).model_dump()
    result['narration_linkage_created_audio_timeline'] = current.get('narration_linkage_created_audio_timeline', False) or current.get('audio_timeline') is None
    result.update(duration_seconds=duration, status='ready', output_path=None, subtitle_path=None, narration_audio_path=None)
    result['low_confidence_clip_count'] = sum(bool(clip['needs_confirmation']) for clip in result['clips'])
    return result


def retime_section_subtitles(tracks: list[SubtitleTrack], current: dict, section_id, local: dict,
                            version_id, source_language, *, restore_tracks=None) -> list[SubtitleTrack]:
    old_cues, _, start, end, length, delta, _ = _clock(current, section_id, local)
    ids = {cue['id'] for cue in old_cues}
    changed_text = [(cue['id'], cue['text']) for cue in old_cues] != [(cue['id'], cue['text']) for cue in local['cues']]
    if restore_tracks is not None:
        return _restore_subtitles(tracks, restore_tracks, ids, start, end, length, delta,
            (_ms(current['duration_seconds']) + delta) / 1000, version_id)
    source_tracks = [track for track in tracks if track.language == source_language and track.generation_method == 'timeline']
    source_ids = [cue.id for track in source_tracks for cue in track.cues]
    source_by_id = {cue.id: cue for track in source_tracks for cue in track.cues}
    local_ids = [cue['id'] for cue in local['cues']]
    new_clock, cursor = {}, start
    for cue in local['cues']:
        right = cursor + _ms(cue['duration_seconds'])
        if right > start + length:
            raise ValueError('局部字幕时钟超出实际配音段')
        new_clock[cue['id']] = (cursor / 1000, right / 1000)
        cursor = right
    if len(source_tracks) == 1:
        changed_text = changed_text or any(before.id == after['id'] and before.text != after['text']
            for before in source_tracks[0].cues for after in local['cues'])
    result = []
    for track in tracks:
        source = track.language == source_language and track.generation_method == 'timeline'
        target = [cue for cue in track.cues if (cue.id in ids if source else cue.source_cue_id in ids)]
        unbound = any(cue not in target and _ms(cue.start_seconds) < end and _ms(cue.end_seconds) > start for cue in track.cues)
        if source and unbound:
            raise ValueError('字幕与目标段缺少明确来源关联，不能按时间猜测替换')
        mapping_unavailable = False
        cues = [replace(cue, start_seconds=(_ms(cue.start_seconds) + delta) / 1000,
                        end_seconds=(_ms(cue.end_seconds) + delta) / 1000)
                if _ms(cue.start_seconds) >= end else deepcopy(cue) for cue in track.cues if cue not in target]
        if source:
            for cue in local['cues']:
                left, right = new_clock[cue['id']]
                prior = next((item for item in target if item.id == cue['id'] and item.text == cue['text']), None)
                cues.append(replace(prior, start_seconds=left, end_seconds=right) if prior else
                    SubtitleCue(id=cue['id'], source_cue_id=None, text=cue['text'], start_seconds=left,
                                end_seconds=right, emphasis=deepcopy(cue.get('subtitle_emphasis', []))))
        else:
            references = [cue.source_cue_id for cue in target]
            mapping_unavailable = unbound or len(source_tracks) != 1 or track.source_language != source_language or (
                len(references) != len(set(references))) or any(
                    source_ids.count(reference) != 1 or local_ids.count(reference) != 1 for reference in references)
            if not mapping_unavailable:
                mapping_unavailable = any(not source_by_id[cue.source_cue_id].start_seconds <= cue.start_seconds <
                    cue.end_seconds <= source_by_id[cue.source_cue_id].end_seconds for cue in target)
            if mapping_unavailable:
                cues.extend(deepcopy(target))
            else:
                for cue in target:
                    before = source_by_id[cue.source_cue_id]
                    left, right = new_clock[cue.source_cue_id]
                    factor = (right - left) / (before.end_seconds - before.start_seconds)
                    cues.append(replace(cue,
                        start_seconds=round(left + (cue.start_seconds - before.start_seconds) * factor, 3),
                        end_seconds=round(left + (cue.end_seconds - before.start_seconds) * factor, 3)))
        cues.sort(key=lambda cue: cue.start_seconds)
        stale_translation = not source and track.generation_method == 'translation' and changed_text
        changed = cues != track.cues or source or stale_translation or mapping_unavailable
        updated = replace(clone_subtitle_track(track, timeline_version_id=version_id, style=deepcopy(track.style), cues=cues),
            artifact_ref=None if changed else track.artifact_ref, transform_id=None if changed else track.transform_id,
            status='unavailable' if changed else track.status)
        if changed:
            updated = replace(updated, failure_reason='译文与原句的对应关系缺失或有歧义，时间尚未同步' if mapping_unavailable else
                '目标正文已更新，译文须重新复核' if stale_translation else '字幕定义已更新，尚未生成对应文件',
                impact='当前字幕尚不能作为有效成片字幕', repair_action='核对译文来源和时间并生成对应字幕文件' if mapping_unavailable else
                '复核译文并生成对应字幕文件' if stale_translation else '生成当前版本的字幕文件后恢复有效状态')
        if track.status != 'valid':
            updated = replace(updated, status=track.status, failure_reason=track.failure_reason,
                impact=track.impact, repair_action=track.repair_action)
        if not mapping_unavailable:
            subtitle_schedule([replace(updated, status='valid', style={**updated.style, 'enabled': True})],
                              (_ms(current['duration_seconds']) + delta) / 1000)
        result.append(updated)
    return result


def _restore_subtitles(current, saved, ids, start, end, length, delta, duration, version_id):
    """Restore saved target cues while retaining later content in every lane."""
    result = []
    keys = [(track.language, track.generation_method, track.source_language) for track in saved]
    if len(set(keys)) != len(keys):
        raise ValueError('局部字幕历史存在多条同用途轨道，不能猜测恢复关联')
    remaining = dict(zip(keys, saved))
    for track in current:
        key = (track.language, track.generation_method, track.source_language)
        desired = remaining.pop(key, None)
        retained = []
        for cue in track.cues:
            if cue.id in ids or cue.source_cue_id in ids:
                continue
            if _ms(cue.start_seconds) < end and _ms(cue.end_seconds) > start:
                raise ValueError('当前字幕跨越恢复区间且没有目标来源关联，不能覆盖')
            retained.append(replace(cue, start_seconds=(_ms(cue.start_seconds) + delta) / 1000,
                end_seconds=(_ms(cue.end_seconds) + delta) / 1000) if _ms(cue.start_seconds) >= end else deepcopy(cue))
        if desired is not None and retained and track.style != desired.style:
            raise ValueError('字幕样式由其他段共用，局部恢复不能覆盖后来样式')
        unresolved = track if track.status != 'valid' and (retained or not track.cues) else (
            desired if desired is not None and desired.status != 'valid' else None)
        result.append((desired or track, retained, desired, unresolved))
    result.extend((track, [], track, track if track.status != 'valid' else None) for track in remaining.values())
    restored = []
    for template, cues, desired, unresolved in result:
        if desired is not None:
            for cue in desired.cues:
                if not 0 <= _ms(cue.start_seconds) < _ms(cue.end_seconds) <= length:
                    raise ValueError('历史字幕超出实际目标区间，不能猜测恢复时间')
                cues.append(replace(cue, start_seconds=(_ms(cue.start_seconds) + start) / 1000,
                    end_seconds=(_ms(cue.end_seconds) + start) / 1000))
        cues.sort(key=lambda cue: cue.start_seconds)
        track = replace(clone_subtitle_track(template, timeline_version_id=version_id, cues=cues, style=deepcopy(template.style)),
            artifact_ref=None, transform_id=None, status='unavailable',
            failure_reason='历史字幕定义已恢复，尚未生成当前版本文件', impact='当前字幕文件待恢复',
            repair_action='按恢复后的实际定义生成当前版本字幕文件')
        if unresolved is not None:
            track = replace(track, status=unresolved.status, failure_reason=unresolved.failure_reason,
                impact=unresolved.impact, repair_action=unresolved.repair_action)
        else:
            subtitle_schedule([replace(track, status='valid', style={**track.style, 'enabled': True})], duration)
        restored.append(track)
    return restored


@dataclass(frozen=True)
class SectionDependencyInputs:
    """Target body facts and global clocks, never a truncated montage plan."""
    cues: list
    cue_order: list
    clip_clocks: list
    clip_facts: dict
    duration_seconds: float | None
    audio_timeline: dict | None
    narration_audio_path: str | None
    narration_duration_seconds: float
    normalize_voice_sources: bool
    graphic_overlays: list


def section_timeline_dependencies(montage, section_id) -> dict:
    """Full-plan callers retain the same validation and dependency encoding."""
    cues = [cue for cue in montage.get('cues', []) if cue['source_section_id'] == section_id]
    ids = {cue['id'] for cue in cues}
    if not any(clip['cue_id'] in ids for clip in montage.get('clips', [])):
        return {'cues': deepcopy(cues), 'clips': []}
    clips = [{**clip, '_position': index} for index, clip in enumerate(montage.get('clips', []))]
    return section_dependency_facts(SectionDependencyInputs(cues,
        [cue['id'] for cue in montage.get('cues', [])], clips,
        {index: clip for index, clip in enumerate(montage.get('clips', []))},
        montage.get('duration_seconds'), montage.get('audio_timeline'), montage.get('narration_audio_path'),
        sum(cue['duration_seconds'] for cue in montage.get('cues', []))
            if montage.get('audio_timeline') is None and montage.get('narration_audio_path') else 0,
        montage.get('narration_linkage_created_audio_timeline', False) or montage.get('audio_timeline') is None,
        montage.get('graphic_overlays', [])))


def section_dependency_facts(inputs: SectionDependencyInputs) -> dict:
    cues = deepcopy(inputs.cues)
    ids = {cue['id'] for cue in cues}
    if not any(clip['cue_id'] in ids for clip in inputs.clip_clocks):
        return {'cues': cues, 'clips': []}
    selected, start, end = _section_bounds(inputs.cue_order, ids, inputs.clip_clocks, inputs.duration_seconds)

    def clip_facts(clock):
        clip = inputs.clip_facts[clock['_position']]
        return {**deepcopy(clip), 'timeline_start_seconds': (_ms(clip['timeline_start_seconds']) - start) / 1000,
                'timeline_end_seconds': (_ms(clip['timeline_end_seconds']) - start) / 1000}

    audio = None
    graph = inputs.audio_timeline
    if graph is None and inputs.narration_audio_path:
        graph = narration_audio_timeline(inputs.narration_audio_path, inputs.narration_duration_seconds).model_dump()
    if graph is not None:
        timeline = AudioTimeline.model_validate(graph)
        audio = {'format_version': timeline.format_version, 'tracks': []}
        for track in timeline.tracks:
            relevant = []
            for clip in track.clips:
                at = clip.timeline_start_ms
                left, right = max(at, start), min(at + clip.duration_ms, end)
                if right <= left:
                    continue
                relevant.append({**clip.model_dump(exclude={'id'}), 'timeline_start_ms': left - start,
                    'source_start_ms': clip.source_start_ms + round((left - at) * clip.playback_rate),
                    'source_end_ms': clip.source_start_ms + round((right - at) * clip.playback_rate),
                    'envelope_offset_ms': clip.envelope_offset_ms + left - at,
                    'envelope_duration_ms': clip.envelope_duration_ms or clip.duration_ms})
            if relevant:
                audio['tracks'].append({**track.model_dump(), 'clips': relevant})
    ordered = sorted(inputs.clip_clocks, key=lambda clip: clip['timeline_start_seconds'])
    transitions = [{'left': clip_facts(left), 'right': clip_facts(right)} for left, right in zip(ordered, ordered[1:])
                   if right.get('transition_in_seconds', 0) and (left['cue_id'] in ids or right['cue_id'] in ids)]
    overlays = [{**parse_overlay(item).model_dump(), 'start_seconds': (max(_ms(item['start_seconds']), start) - start) / 1000,
                 'end_seconds': (min(_ms(item['end_seconds']), end) - start) / 1000}
                for item in inputs.graphic_overlays if _ms(item['start_seconds']) < end and _ms(item['end_seconds']) > start]
    return {'cues': cues, 'clips': [clip_facts(clip) for clip in selected], 'audio_timeline': audio,
            'normalize_voice_sources': inputs.normalize_voice_sources,
            'graphic_overlays': overlays, 'transitions': transitions}
