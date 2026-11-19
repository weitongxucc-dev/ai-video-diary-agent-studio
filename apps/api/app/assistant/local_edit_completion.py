"""Recognize a finished finite audio-gain request from original stored facts."""
from copy import deepcopy
import json
import math
import re

from app.audio_timeline import AudioTimeline, effective_audio_timeline
from app.montage import creation_task_montage_plan_from_payload
from app.assistant.inputs import latest_consumed_input


def completed_audio_gain_edit(connection, turn_id, locator):
    if not locator or locator.get('phase') != 'apply' or locator.get('status') != 'recorded':
        return None
    row = connection.execute('SELECT t.*, r.workspace_id,r.member_id,r.request_context_json '
        'FROM assistant_conversation_turns t JOIN assistant_conversation_request_receipts r ON r.turn_id=t.id '
        "WHERE t.id=? AND r.outcome='consumed'", (turn_id,)).fetchone()
    if row is None or latest_consumed_input(json.loads(row['events_json'])):
        return None
    context = json.loads(row['request_context_json'])
    if context.get('kind') != 'task' or context.get('source_kind'):
        return None
    clauses = [part.strip() for part in re.split('[，,。；;！!\n]', row['user_message']) if part.strip()]
    if not clauses:
        return None
    change = re.fullmatch(
        r'(?:请|帮我|麻烦)?(?:把|将)?(?P<selected>(?:已引用的|选中的|选定的)?(?:这一段|这段|当前)?)(?:背景)?'
        r'(?P<target>钢琴(?:音乐)?|音乐|配音|旁白)(?:的?音量)?'
        r'(?:从\s*(?P<before>[+−-]?\d+(?:\.\d+)?)\s*(?:分贝|dB))?\s*'
        r'(?P<direction>调大|提高|增大|调小|调低|降低|减小)\s*(?P<amount>\d+(?:\.\d+)?)\s*(?:分贝|dB)'
        r'(?:\s*(?:至|到)\s*(?P<after>[+−-]?\d+(?:\.\d+)?)\s*(?:分贝|dB))?', clauses[0], re.I)
    if change is None:
        return None
    narration = change['target'] in {'配音', '旁白'}
    if change['selected'] and not narration:
        return None
    for clause in clauses[1:]:
        if clause == '只改该片段的音量' and narration:
            continue
        if clause in {'直接保存到当前草稿', '保存到草稿', '不生成预览', '不重新出片',
                      '原声保持不变', '原声不变', '保持原声不变', '其他不变', '保存修改'}:
            continue
        if not re.fullmatch(r'(?:保存(?:修改)?后)?(?:请)?告诉我(?:调整|修改)前后的(?:音乐|配音)?音量', clause):
            return None  # A second action, render or observation must stay open.
    amount = float(change['amount'])
    if not math.isfinite(amount) or amount <= 0:
        return None
    delta = -amount if change['direction'] in {'调小', '调低', '降低', '减小'} else amount
    task_id, workspace_id = context['task_id'], row['workspace_id']
    if locator.get('task_id') != task_id:
        return None
    claims = connection.execute("SELECT id,payload_json FROM task_events WHERE workspace_id=? AND task_id=? "
        "AND event_type='edit_request_claimed' AND json_extract(payload_json,'$.phase')='apply' "
        "AND json_extract(payload_json,'$.request_id')=? AND json_extract(payload_json,'$.member_id')=?",
        (workspace_id, task_id, str(locator['request_id']), row['member_id'])).fetchall()
    if len(claims) != 1:
        return None
    claim = json.loads(claims[0]['payload_json'])
    if (claim['inputs'].get('assistant_turn_id') != turn_id
            or claim['inputs'].get('operation_type') != 'EditAudioTimeline'
            or claim['input_digest'] != locator.get('input_digest')):
        return None
    saved = connection.execute('SELECT payload_json FROM task_events WHERE workspace_id=? AND id=?',
        (workspace_id, claims[0]['id'] + '_result')).fetchone()
    if saved is None:
        return None
    saved = json.loads(saved['payload_json'])
    operation_id = (saved.get('response', {}).get('operation') or {}).get('id')
    if saved.get('status_code') != 200 or not operation_id:
        return None
    operation = connection.execute('SELECT * FROM edit_operations WHERE workspace_id=? AND task_id=? AND id=? '
        "AND status='applied' AND operation_type='EditAudioTimeline'", (workspace_id, task_id, operation_id)).fetchone()
    task = connection.execute('SELECT payload FROM creation_tasks WHERE workspace_id=? AND id=?', (workspace_id, task_id)).fetchone()
    if operation is None or task is None or json.loads(task['payload']).get('current_timeline_version_id') != operation['result_version_id']:
        return None
    versions = [connection.execute('SELECT * FROM timeline_versions WHERE workspace_id=? AND task_id=? AND id=?',
        (workspace_id, task_id, operation[key])).fetchone() for key in ('base_version_id', 'result_version_id')]
    if any(version is None for version in versions):
        return None
    base, result = versions
    if (result['parent_version_id'] != base['id'] or result['sequence'] != base['sequence'] + 1
            or context.get('target_snapshot', {}).get('timeline_version_id') != base['id']
            or context.get('target_snapshot', {}).get('timeline_sequence') != base['sequence']
            or claim['inputs'].get('expected_timeline_version_id') != base['id']
            or claim['inputs'].get('expected_timeline_sequence') != base['sequence']):
        return None
    snapshots = [json.loads(version['snapshot_json']).get('montage', {}) for version in versions]
    try:
        before, after = [(AudioTimeline.model_validate(snapshot['audio_timeline']) if snapshot.get('audio_timeline')
                          else effective_audio_timeline(creation_task_montage_plan_from_payload(snapshot))).model_dump()
                         for snapshot in snapshots]
    except (TypeError, ValueError):
        return None
    expected = deepcopy(before)
    changes = []
    piano = '钢琴' in change['target']
    def is_piano(clip):
        return '钢琴' in clip['name'] or 'piano' in clip['name'].casefold()
    for old_track, new_track in zip(expected['tracks'], after['tracks']):
        for old, new in [(old_track, new_track), *zip(old_track['clips'], new_track['clips'])]:
            if old['id'] != new['id'] or old['gain_db'] == new['gain_db']:
                continue
            if old_track['role'] != ('narration' if narration else 'music') or (piano and not (
                    is_piano(old) if 'source_ref' in old else old['clips'] and all(is_piano(clip) for clip in old['clips']))):
                return None
            changes.append((old['id'], old['gain_db'], new['gain_db'],
                'clip' if 'source_ref' in old else 'track', old_track['gain_db']))
            old['gain_db'] = new['gain_db']
    if expected != after or len(changes) != 1:
        return None
    target_id, gain_before, gain_after, target_kind, track_gain = changes[0]
    if not math.isclose(gain_after - gain_before, delta, abs_tol=1e-9):
        return None
    for name, value in [('before', gain_before), ('after', gain_after)]:
        if change[name] is not None and not math.isclose(float(change[name].replace('−', '-')), value, abs_tol=1e-9):
            return None
    if narration:
        references = context.get('target_snapshot', {}).get('selected_references', [])
        if target_kind != 'clip' or len(references) != 1:
            return None
        ref = references[0]
        if (ref.get('kind'), ref.get('object_id'), ref.get('task_id'), ref.get('version')) != ('audio', target_id, task_id, base['id']):
            return None
        summary = f'这段配音已从 {gain_before:g} dB 调整为 {gain_after:g} dB。已保存，可撤销。'
    else:
        label = ('钢琴片段' if piano else '音乐片段') if target_kind == 'clip' else '音乐轨'
        summary = (f'{label}音量已从 {gain_before:g} dB 调整为 {gain_after:g} dB。'
                   + (f'音乐轨音量仍为 {track_gain:g} dB。' if target_kind == 'clip' else '')
                   + '原声保持不变。修改已保存，可撤销。')
    return {'scope': 'single_narration_gain_edit' if narration else 'single_music_gain_edit',
        'task_id': task_id, 'request_id': str(locator['request_id']),
        'operation_id': operation_id, 'target_id': target_id, 'base_version_id': base['id'], 'result_version_id': result['id'],
        'gain_before_db': gain_before, 'gain_after_db': gain_after, 'target_kind': target_kind, 'summary': summary}
