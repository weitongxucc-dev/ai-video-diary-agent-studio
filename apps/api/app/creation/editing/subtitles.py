"""Subtitle metadata changes, before publication and the joint version commit."""
from dataclasses import replace

from app.creation.editing.picture import EditConflict
from app.subtitle_edits import update_subtitle_cue
from app.subtitle_schedule import subtitle_schedule
from app.compound_edits import synchronize_linked_subtitle_cue


def subtitle_display_selection(tracks, mode):
    languages = {'none':set(), 'zh':{'zh-CN'}, 'en':{'en-US'}, 'zh-en':{'zh-CN', 'en-US'}}
    if mode not in languages:
        raise ValueError("字幕显示模式无效")
    wanted = languages[mode]
    if not wanted <= {track.language for track in tracks if track.status == 'valid'}:
        raise EditConflict("所选语言的字幕轨尚未生成；本次切换不会调用翻译模型。")
    return {track.id:track.language in wanted for track in tracks}


def apply_subtitle_display(tracks, mode):
    selected = subtitle_display_selection(tracks, mode)
    return [replace(track, style={**track.style, 'enabled':selected[track.id]}) for track in tracks]


def apply_subtitle_cue(tracks, payload, duration_seconds):
    track = next((item for item in tracks if item.id == payload['track_id']), None)
    if track is None:
        raise EditConflict("subtitle track has changed")
    cue = next((item for item in track.cues if item.id == payload['cue_id']), None)
    if cue is None or cue.text != payload['expected_text']:
        raise EditConflict("subtitle cue has changed")
    changed = update_subtitle_cue(track, cue.id, text=payload['text'],
        start_seconds=payload.get('start_seconds'), end_seconds=payload.get('end_seconds'),
        line_breaks=list(payload.get('line_breaks') or []), emphasis=list(payload.get('emphasis') or []))
    style = payload.get('style')
    if not isinstance(style, dict):
        raise EditConflict("invalid subtitle style")
    subtitle_schedule([replace(track, status='valid', style={**style, 'enabled':True}, cues=changed.cues)], duration_seconds)
    linked = synchronize_linked_subtitle_cue(tracks, track, changed, cue.id, duration_seconds,
        invalidate_all_english_on_text_change=True)
    return track, replace(changed, style=style), linked
