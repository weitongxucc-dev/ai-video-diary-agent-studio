"""Pure command text parsing; callers retain lazy domain reads and action order."""


import re

from dataclasses import dataclass
from app.creation.editing.clips import ClipPreviewCommand
from app.schemas import CreationTaskExactEditRequest

class CommandParseRejected(Exception):
    def __init__(self,status_code,detail):
        super().__init__(str(detail))
        self.status_code,self.detail=status_code,detail


def indexed_reorder_command(command: str) -> tuple[str, int, int] | None:
    number = '(?:\\d+|[一二三四五六七八九十]+)'
    ordinal = f'(?:镜头\\s*|第?)({number})(?:个)?(?:镜头)?'
    prefix = '(?:(?:那|你|现在|就|请|帮我|麻烦你|把|将)\\s*)*'
    suffix = '(?:一下)?(?:吧)?'
    text = command.strip().rstrip('。！')
    text = re.sub('[，,。]\\s*(?:(?:只)?生成(?:修改)?(?:提案|方案|预览)|其他不变|保持(?:总)?时长(?:、配音和字幕)?不变)[。！]?', '', text)
    swap = re.fullmatch(f'{prefix}{ordinal}(?:和|与|、){ordinal}(?:的?(?:位置|顺序))?(?:交换|互换|对调|换位|调换)(?:位置|顺序)?{suffix}', text)
    if swap is None:
        swap = re.fullmatch(f'{prefix}(?:交换|互换|对调)(?:一下)?{ordinal}(?:和|与|、){ordinal}(?:的?(?:位置|顺序))?{suffix}', text)
    if swap is None:
        swap = re.fullmatch(f'{prefix}{ordinal}\\s*↔\\s*{ordinal}(?:交换|互换|对调)?{suffix}', text)
    move = re.fullmatch(f'{prefix}{ordinal}(?:移到|移动到|调到|放到){ordinal}(?:的?位置)?{suffix}', text)
    match = swap or move
    if match is None:
        if any((word in text for word in ('交换', '互换', '对调', '↔', '移到', '移动到'))):
            raise CommandParseRejected(status_code=422, detail='请明确要交换的两个镜头序号，或镜头要移动到第几个位置；未生成修改。')
        return None

    def integer(value):
        if value.isdecimal():
            return int(value)
        digits = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
        if value in digits:
            return digits[value]
        tens = re.fullmatch('([一二三四五六七八九]?)十([一二三四五六七八九]?)', value)
        if tens is None:
            raise CommandParseRejected(status_code=422, detail='镜头序号无法识别，请使用阿拉伯数字。')
        return digits.get(tens[1], 1) * 10 + digits.get(tens[2], 0)
    return ('swap' if swap else 'move', integer(match[1]) - 1, integer(match[2]) - 1)


def subtitle_text_from_command(command: str) -> str | None:
    for marker in ('改成', '改为', '换成', 'replace with'):
        if marker in command.casefold():
            value = command[command.casefold().index(marker) + len(marker):].strip()
            value = value.lstrip(' ：:').strip()
            for opening, closing in (('“', '”'), ('‘', '’'), ('"', '"'), ("'", "'")):
                if len(value) >= 2 and value.startswith(opening) and value.endswith(closing):
                    value = value[1:-1].strip()
                    break
            return value or None
    return None


def edit_material_constraint(command: str) -> str:
    value = command.casefold()
    value = re.sub('(?:把|将|给)?(?:当前选中的|选中的|当前|这个)?(?:第(?:\\d+|[一二三四五六七八九十]+)个?)?镜头', '', value)
    value = re.sub('(?:保持|保留)(?:镜头顺序、)?(?:时长、)?配音(?:和|、)字幕不变', '', value)
    value = value.replace('生成修改提案', '').replace('其他不变', '')
    generic = re.fullmatch('[\\s，。！？,.!?;；:：]*(?:请)?(?:换|替换)(?:成|为|一下)?(?:一个|一段)?(?:另一个|其他|其它|别的|新的|不同的)?(?:一个|一段)?(?:镜头|素材|画面|片段)?[\\s，。！？,.!?;；:：]*', value)
    if generic:
        return ''
    for phrase in ('保留当前配音', '保留原配音', '保留配音', '配音不变', '把这个镜头', '把当前镜头', '换成另一个镜头', '换成其他镜头', '换成另一个画面', '替换成另一个镜头', 'replace this clip', 'with another clip', '改用', '替换成', '替换', '换成', '请'):
        value = value.replace(phrase, ' ')
    value = value.strip(' ，。！？,.!?;；:：')
    if re.fullmatch('(?:另一个|其他|其它|别的|新的|一个|一段|不同的)?(?:镜头|素材|画面|片段)?', value):
        return ''
    return value



@dataclass(frozen=True)
class AudioCommandPreview:
    request: object
    original_clip: bool = False


def canvas_request(payload, command):
    canvas_commands = {'改横版': '16:9', '改成横版': '16:9', '改成16:9': '16:9', '改成竖版': '9:16', '改竖版': '9:16', '改成9:16': '9:16', '改成方形': '1:1', '改成1:1': '1:1'}
    canvas_command = command.rstrip('。！')
    keep_picture = canvas_command.endswith('，别裁掉人物') or canvas_command.endswith('，保留完整画面')
    if keep_picture:
        canvas_command = canvas_command.split('，')[0]
    if payload.context_stage in {'montage', 'review'} and canvas_command in canvas_commands:
        return CreationTaskExactEditRequest(operation_type='ChangeCanvas', aspect_ratio=canvas_commands[canvas_command], **{'fit_mode': 'fit'} if keep_picture else {})
    return None


def display_request(payload, command):
    display_commands = {'关闭字幕': 'none', '不显示字幕': 'none', '只显示中文字幕': 'zh', '只显示英文字幕': 'en', '显示中英双语字幕': 'zh-en'}
    if payload.context_stage in {'montage', 'review', 'subtitle_cover'} and command.rstrip('。！') in display_commands:
        return CreationTaskExactEditRequest(operation_type='ConfigureSubtitleTrack', subtitle_display=display_commands[command.rstrip('。！')])
    return None


def transition_request(command, selected_ref):
    transition = re.fullmatch('(?:(?:当前|这个)?镜头)?(?:与前一个镜头)?(?:添加|改为|设置)?叠化(?:转场)?\\s*(\\d+(?:\\.\\d+)?)\\s*秒[。！]?', command)
    remove_transition = command.rstrip('。！') in {'取消叠化', '改为硬切'}
    if transition or remove_transition:
        return CreationTaskExactEditRequest(operation_type='SetClipTransition', clip_id=selected_ref.removeprefix('clip:'), transition_seconds=0 if remove_transition else float(transition[1]))
    return None


def clip_request(task_id, payload, selected_ref, command, base_version_id, request_source):
    linked_speed = re.fullmatch('(?:把)?(?:这个|当前)?镜头(?:改成|改为|调到)\\s*(\\d+(?:\\.\\d+)?)\\s*倍速[，, ]*(?:声音和字幕一起|同步声音和字幕|连同声音字幕)[。！]?', command)
    if linked_speed:
        value = float(linked_speed.group(1))
        if not 0.25 <= value <= 4:
            raise CommandParseRejected(status_code=422, detail='关联倍速须在0.25–4之间')
        return CreationTaskExactEditRequest(operation_type='ChangeLinkedSpeed', clip_id=selected_ref.removeprefix('clip:'), playback_rate=value)
    speed_match = re.fullmatch('(?:把)?(?:这个|当前)?镜头(?:画面)?(?:改成|改为|调到|设为)\\s*(\\d+(?:\\.\\d+)?)\\s*倍速[，, ]*(?:保留配音|声音不变|保持时长)[。！]?', command)
    if speed_match:
        if not 0.25 <= float(speed_match.group(1)) <= 4:
            raise CommandParseRejected(status_code=422, detail='画面倍速须在0.25–4之间')
        return ClipPreviewCommand(task_id, base_version_id, 'ChangeClipSpeed', [selected_ref.removeprefix('clip:')], {'playback_rate': float(speed_match.group(1))}, command, request_source)
    linked_commands = {'删除这个镜头': 'DeleteClip', '删除当前镜头': 'DeleteClip', '删掉这个镜头，把后面接上': 'DeleteClip', '复制这个镜头': 'DuplicateClip', '复制当前镜头': 'DuplicateClip', '插入镜头': 'InsertClip', '插入当前镜头': 'InsertClip', '在这个镜头后插入镜头': 'InsertClip'}
    linked_type = linked_commands.get(command.rstrip('。！'))
    if linked_type:
        if linked_type == 'InsertClip':
            return CreationTaskExactEditRequest(operation_type=linked_type, clip_id=selected_ref.removeprefix('clip:'))
        return CreationTaskExactEditRequest(operation_type=linked_type, clip_id=selected_ref.removeprefix('clip:'))
    split_match = re.fullmatch('(?:把)?(?:这个|当前)?镜头(?:在|从)\\s*(\\d+(?:\\.\\d+)?)\\s*秒(?:处)?(?:分割|切开)[。！]?', command)
    split_here = command.rstrip('。！') in {'从这里分割镜头', '在播放位置分割镜头', '从这里切开', '分割当前镜头'}
    if split_match or split_here:
        if split_here and payload.playhead_ms is None:
            raise CommandParseRejected(status_code=422, detail='请先在播放器定位分割位置，或指定成片秒数。')
        return CreationTaskExactEditRequest(operation_type='SplitClip', clip_id=selected_ref.removeprefix('clip:'), split_time_seconds=float(split_match.group(1)) if split_match else payload.playhead_ms / 1000)
    return None


def selected_reference(payload):
    selected_ref = payload.selected_object_refs[0] if len(payload.selected_object_refs) == 1 else ''
    if payload.context_stage == 'subtitle_cover' and len(payload.selected_object_refs) == 2:
        cues = [ref for ref in payload.selected_object_refs if ref.startswith('cue:')]
        tracks = [ref for ref in payload.selected_object_refs if ref.startswith('subtitle-track:')]
        if len(cues) == len(tracks) == 1:
            selected_ref = cues[0]
    return selected_ref
