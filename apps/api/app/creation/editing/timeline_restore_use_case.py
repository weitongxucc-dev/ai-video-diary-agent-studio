"""Finite historical restore using original projection and save owners."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from contextlib import nullcontext
import json
import re
from app.creation.task_input_reads import require_creation_task
from copy import deepcopy
from typing import TYPE_CHECKING
from uuid import uuid4

from app.creation.contracts import (
    EditIntent,
    EditProposal,
    SubtitleTrack,
    SubtitleCue,
    CoverAsset,
    TimelineVersion,
)
from app.creation.editing.metadata_use_case import (
    MetadataEditUseCase, MetadataEditResult, prepare_changed_montage, logical_montage_output_refs,
)
from app.creation.editing.picture import EditConflict
from app.creation.projection import undo_target_versions, redo_target_versions
from app.creation.transactions import clone_subtitle_track, clone_cover_asset, changed_stage_sections
from app.models import utc_now
from app.montage import CreationTaskMontagePlan, creation_task_montage_plan_from_payload

if TYPE_CHECKING:
    from app.creation.contracts import CreationTask, NarrationSegment
    from app.creation.narration_artifacts import NarrationArtifactRecord
    from app.production import ProductionRepository


@dataclass(frozen=True)
class TimelineRestoreCommand:
    workspace_id: str
    task: CreationTask
    redo: bool
    target_operation_id: str | None = None
    restore_request_id: str | None = None
    restore_member_id: str | None = None
    target_undo_operation_id: str | None = None
    write_guard: object | None = None
    actor_identity: dict | None = None

    def control_payload(self):
        return ({'restore_request_id': self.restore_request_id, 'restore_member_id': self.restore_member_id,
            'target_operation_id': self.target_operation_id, 'target_undo_operation_id': self.target_undo_operation_id}
            if self.restore_request_id is not None else {})


@dataclass(frozen=True)
class PreparedTimelineRestore:
    command: TimelineRestoreCommand
    changed_version: TimelineVersion
    target_version: TimelineVersion
    current_plan: CreationTaskMontagePlan
    restored_plan: CreationTaskMontagePlan
    restored_segments: list[NarrationSegment] | None
    restored_narration: NarrationArtifactRecord | None
    subtitle_cover_undo: bool
    operation_type: str
    action_label: str
    section_history: dict | None = None
    section_dependencies: dict | None = None
    local_tracks: list | None = None
    local_sources: list | None = None


@dataclass(frozen=True)
class TimelineOutputQualification:
    prepared: PreparedTimelineRestore
    output_is_valid: bool


class TimelineRestoreUnavailable(EditConflict):
    def __init__(self, redo: bool):
        action_label = '重做' if redo else '撤销'
        self.details = {'status_code': 409,
            'error': 'timeline_redo_unavailable' if redo else 'timeline_undo_unavailable',
            'reason': f'当前没有可{action_label}的内容修改。', 'impact': '任务和时间线保持不变。',
            'repair_action': '先撤销一次修改，再使用重做。' if redo else '先完成一次镜头、配音或字幕修改，再使用撤销。',
            'retryable': False}
        super().__init__(self.details['reason'])


class TimelineRestoreUseCase:
    def __init__(self, repository: ProductionRepository, montage_edits: MetadataEditUseCase, *, qualify_output=None, conversations=None):
        if montage_edits.repository is not repository:
            raise ValueError('历史恢复必须绑定同一仓储负责人')
        self.repository = repository
        self.montage_edits = montage_edits
        self.qualify_output = qualify_output
        self.conversations = conversations

    @staticmethod
    def require_actor(task, actor):
        if actor.workspace_id != task.workspace_id or task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id):
            raise PermissionError('当前成员不能读取或恢复此作品的修改')

    def history(self, workspace_id, task_id, actor, *, limit=20, before_sequence=None):
        self.require_actor(self.repository.task_records.get_creation_task(workspace_id, task_id), actor)
        return self.repository.timeline_history.page(workspace_id, task_id, limit=limit, before_sequence=before_sequence)

    def read_request(self, workspace_id, task_id, request_id, actor):
        self.require_actor(self.repository.task_records.get_creation_task(workspace_id, task_id), actor)
        return self.repository.restore_requests.receipt(workspace_id, task_id, actor.user_id or actor.member_id, request_id)

    def replay(self, workspace_id, task_id, receipt):
        operation = self.repository.edit_records.get_edit_operation(workspace_id, receipt['operation_id'])
        version = self.repository.timeline_records.get_timeline_version(workspace_id, operation.result_version_id)
        payload = version.snapshot.get('montage')
        plan = creation_task_montage_plan_from_payload(payload) if payload else None
        key = version.snapshot.get('narration_cache_key')
        narration = self.repository.narration_artifacts.get_narration_artifact(workspace_id, key) if key else None
        return MetadataEditResult(operation, plan, self.repository.task_records.get_creation_task(workspace_id, task_id), narration, receipt,
            receipt.get('execution_readiness'))

    @staticmethod
    def require_restore_instruction(message, *, redo):
        if re.search('不要|不用|别|不(?:撤销|重做|取消|放弃)', message):
            raise EditConflict('当前要求没有授权这一动作，任务保持不变。')
        prefix = '(?:请|麻烦|现在|先|帮我|请帮我|可以帮我|能不能帮我)?'
        scope = '(?:上一步(?:修改|编辑|操作)?|上一次(?:内容)?(?:修改|编辑|操作)?|刚才(?:撤销(?:的)?|的)?(?:那)?(?:一步|一次|修改|编辑|操作)?|最后一次(?:修改|编辑|操作)?)'
        verb = '重做|恢复' if redo else '撤销|回退'
        if re.search('调色|颜色|字幕|音量|音乐|配音|口型|镜头|只撤销.+项|仅撤销.+项', message):
            raise EditConflict('不能用整项历史恢复代替局部修改，任务保持不变。')
        if not re.fullmatch(prefix + f'(?:整体)?(?:{verb})(?:一下)?{scope}?(?:一下|吧|吗|好吗)?[。！!？?]?', message.strip()):
            raise EditConflict('需要明确的撤销或重做原修改要求，任务保持不变。')

    def execute(self, command, *, actor, request_id, target_operation_id=None,
                expected_timeline_version_id=None, expected_timeline_sequence=None, write_guard=None, source=None):
        from app.device_scheduler import RecoveryBlocked
        workspace_id, task_id = command.workspace_id, command.task.id
        task = self.repository.task_records.get_creation_task(workspace_id, task_id)
        self.require_actor(task, actor)
        member_id = actor.user_id or actor.member_id
        if not request_id or len(request_id) > 128:
            raise EditConflict('恢复修改需要稳定原请求身份')
        inputs = {'action': 'redo' if command.redo else 'undo', 'target_operation_id': target_operation_id,
            'expected_timeline_version_id': expected_timeline_version_id,
            'expected_timeline_sequence': expected_timeline_sequence, 'source': source,
            'actor_identity': actor.identity_payload()}
        source_input = None
        if source is not None:
            from app.creation.intent_use_case import CreationIntentUseCase
            if self.conversations is None:
                raise PermissionError('恢复指令的来源核验未装配')
            source_input = self.conversations.task_turn_source(source['assistant_turn_id'], workspace_id, member_id, task_id)
            if source_input.get('input_id') != source['input_id']:
                raise PermissionError('恢复动作不属于原已消费输入')
            CreationIntentUseCase.reject_discussion_only_input(source_input['raw_command'])
            self.require_restore_instruction(source_input['raw_command'], redo=command.redo)
        def guarded(connection):
            self.require_actor(self.repository.task_records.get_creation_task(workspace_id, task_id, connection=connection), actor)
            if source_input is not None:
                current_source = self.conversations._task_turn_source(connection, source['assistant_turn_id'], workspace_id, member_id, task_id)
                if current_source != source_input:
                    raise EditConflict('原恢复指令在提交前已变化')
            if write_guard is not None:
                write_guard(connection)
        event_id, fresh = self.repository.restore_requests.claim(task, member_id, request_id, inputs, write_guard=guarded)
        if not fresh:
            receipt = self.read_request(workspace_id, task_id, request_id, actor)
            if receipt['status'] == 'applied':
                return self.replay(workspace_id, task_id, receipt)
            if receipt['status'] == 'refused':
                raise EditConflict(receipt['failure']['reason'])
            raise RecoveryBlocked('原恢复请求结果未知，须先查询原请求，不重复撤销')
        try:
            if (expected_timeline_version_id is None) != (expected_timeline_sequence is None):
                raise EditConflict('恢复需要同时提供所见版本与序号')
            if target_operation_id is not None and expected_timeline_version_id is None:
                raise EditConflict('指定修改的恢复需要原读取版本')
            if expected_timeline_version_id is not None:
                seen = self.repository.timeline_records.get_timeline_version(workspace_id, expected_timeline_version_id)
                if (seen.task_id != task_id or seen.sequence != expected_timeline_sequence
                        or not self.repository.timeline_records.timeline_contains_version(workspace_id, task_id, task.current_timeline_version_id, seen.id)):
                    raise EditConflict('恢复所依据的版本不属于当前作品历史')
            command = replace(command, task=task, target_operation_id=target_operation_id,
                restore_request_id=request_id, restore_member_id=member_id, write_guard=guarded,
                actor_identity=actor.identity_payload())
            if command.target_operation_id is None:
                if expected_timeline_version_id is not None and task.current_timeline_version_id != expected_timeline_version_id:
                    raise EditConflict('时间线已变化，普通撤销须重新读取当前版本；指定历史修改需明确原操作')
                changed_version, _ = (redo_target_versions if command.redo else undo_target_versions)(self.repository, workspace_id, task)
                if changed_version is None:
                    raise TimelineRestoreUnavailable(command.redo)
                changed = self.repository.edit_records.get_edit_operation(workspace_id, changed_version.created_by_operation_id)
                resolved = changed.payload.get('target_operation_id')
                if resolved is None and changed.operation_type in {'UndoTimelineVersion', 'RedoTimelineVersion'}:
                    original_version = self.repository.timeline_records.get_timeline_version(workspace_id, changed.payload['changed_version_id'])
                    resolved = original_version.created_by_operation_id
                command = replace(command, target_operation_id=resolved or changed.id)
            prepared = self.prepare_restore(command)
            valid = self.qualify_output(workspace_id, task_id, prepared.target_version) if self.qualify_output is not None else False
            result = self.apply_restore(prepared, TimelineOutputQualification(prepared, valid))
            return replace(result, restore_request=self.read_request(workspace_id, task_id, request_id, actor))
        except (EditConflict, ValueError, PermissionError, KeyError) as exc:
            receipt = self.read_request(workspace_id, task_id, request_id, actor)
            if receipt['status'] == 'applied':
                return self.replay(workspace_id, task_id, receipt)
            self.repository.restore_requests.record_refusal(event_id, workspace_id, task_id, str(exc))
            raise

    def prepare_restore(self, command: TimelineRestoreCommand) -> PreparedTimelineRestore:
        workspace_id, task = command.workspace_id, command.task
        task_id = task.id
        if command.target_operation_id is not None:
            target = self.repository.edit_records.get_edit_operation(workspace_id, command.target_operation_id)
            if (target.task_id != task_id or target.operation_type in {'UndoTimelineVersion', 'RedoTimelineVersion'}
                    or not self.repository.timeline_records.timeline_contains_version(workspace_id, task_id, task.current_timeline_version_id, target.result_version_id)):
                raise EditConflict('指定原修改不属于当前作品的有效历史')
            last = self.repository.restore_requests.latest_operation_restore(workspace_id, task_id, target.id)
            if command.redo:
                if last is None or last.operation_type != 'UndoTimelineVersion':
                    raise EditConflict('指定原修改没有对应的可重做撤销记录')
                command = replace(command, target_undo_operation_id=last.id)
                changed_version = self.repository.timeline_records.get_timeline_version(workspace_id, last.result_version_id)
                target_version = self.repository.timeline_records.get_timeline_version(workspace_id, target.result_version_id)
            else:
                if last is not None and last.operation_type == 'UndoTimelineVersion':
                    raise EditConflict('指定原修改已经撤销')
                changed_version = self.repository.timeline_records.get_timeline_version(workspace_id, target.result_version_id)
                target_version = self.repository.timeline_records.get_timeline_version(workspace_id, target.base_version_id)
        else:
            changed_version, target_version = (redo_target_versions if command.redo else undo_target_versions)(self.repository, workspace_id, task)
        operation_type = "RedoTimelineVersion" if command.redo else "UndoTimelineVersion"
        action_label = "重做" if command.redo else "撤销"
        if changed_version is None or target_version is None:
            raise TimelineRestoreUnavailable(command.redo)
        try:
            changed_operation = self.repository.edit_records.get_edit_operation(
                workspace_id, changed_version.created_by_operation_id or ""
            )
        except KeyError as exc:
            raise EditConflict("timeline undo operation is unavailable") from exc
        history = changed_operation.payload if changed_operation.operation_type == 'ReviseScriptSection' else changed_operation.payload.get('section_history')
        if isinstance(history,dict) and 'before_publication' in history:
            expected = (history['before_dependencies'] if command.redo else self.repository.execution_records.get_task_event(
                workspace_id,f'event_{history["operation_id"]}_dependencies').payload['dependencies'])
            # A prior scoped control version records the exact composed dependencies it created.
            cursor = task.current_timeline_version_id
            while cursor:
                recent = self.repository.timeline_records.get_timeline_version(workspace_id,cursor)
                if recent.created_by_operation_id:
                    control = self.repository.edit_records.get_edit_operation(workspace_id,recent.created_by_operation_id)
                    if (control.payload.get('section_history',{}).get('after_publication') == history['after_publication']
                            and control.payload.get('section_restore_side') == ('before' if command.redo else 'after')):
                        expected = self.repository.execution_records.get_task_event(workspace_id,f'event_{control.id}_dependencies').payload['dependencies']
                        break
                    if control.operation_type == 'ReviseScriptSection':
                        break
                cursor = recent.parent_version_id
            return PreparedTimelineRestore(command,changed_version,target_version,None,None,None,None,False,
                operation_type,action_label,history,expected)
        if command.target_operation_id is not None:
            original = self.repository.edit_records.get_edit_operation(workspace_id, command.target_operation_id)
            if original.operation_type in {'AdjustVisualEffects', 'SetClipLock', 'SetClipAudio', 'ReplaceClip',
                    'SetClipTransition', 'EditGraphicOverlay', 'UpdateSubtitleCue', 'ReplaceSubtitleText',
                    'ConfigureSubtitleTrack', 'SplitSubtitleCue', 'MergeSubtitleCue', 'DeleteSubtitleCue'}:
                return self.prepare_local_restore(command, original, changed_version, target_version, operation_type, action_label)
        self.verify_whole_timeline_restore(workspace_id,task_id,changed_version.id)
        subtitle_cover_undo = command.redo or changed_operation.operation_type in {
            "RedoTimelineVersion",
            "ConfigureSubtitleTrack",
            "GenerateCoverFromFrame",
            "UpdateSubtitleCue",
            "ReplaceSubtitleText",
            "SplitSubtitleCue",
            "MergeSubtitleCue",
            "DeleteSubtitleCue",
            "ChangeCanvas",
            "SetClipTransition",
            "EditGraphicOverlay",
            "EditCompoundTimeline",
            "RemoveSilence",
            "AlignSubtitleTiming",
            "ReorderClip",
            "ReorderTimelineItems",
        }
        montage_snapshot = target_version.snapshot.get("montage")
        if not isinstance(montage_snapshot, dict):
            raise EditConflict("timeline undo snapshot is unavailable")
        try:
            current_plan = creation_task_montage_plan_from_payload(
                self.repository.get_creation_task_montage_plan(workspace_id, task_id)
            )
            restored_plan = creation_task_montage_plan_from_payload(montage_snapshot)
            restored_segments = self.montage_edits.narration_segments_from_snapshot(target_version.snapshot)
            if restored_segments is not None:
                restored_at = utc_now()
                restored_segments = [replace(segment, updated_at=restored_at) for segment in restored_segments]
            restored_narration = None
            restored_cache_key = target_version.snapshot.get("narration_cache_key")
            if isinstance(restored_cache_key, str) and restored_cache_key:
                restored_narration = self.repository.narration_artifacts.get_narration_artifact(
                    workspace_id, restored_cache_key
                )
                if restored_narration is None:
                    raise ValueError("时间线引用的配音总轨已丢失")
        except (KeyError, ValueError) as exc:
            raise EditConflict("timeline undo snapshot is invalid") from exc

        return PreparedTimelineRestore(command, changed_version, target_version, current_plan, restored_plan,
            restored_segments, restored_narration, subtitle_cover_undo, operation_type, action_label)

    @staticmethod
    def restore_fields(current, expected, desired, *, label):
        """Reverse the fields of one known business object, retaining later other fields."""
        result = deepcopy(current)
        absent = object()
        for key in expected.keys() | desired.keys():
            before, after = expected.get(key, absent), desired.get(key, absent)
            if before == after:
                continue
            if key == 'visual_effects' and isinstance(before, dict) and isinstance(after, dict):
                result[key] = TimelineRestoreUseCase.restore_fields(result.get(key, {}), before, after, label=label)
                continue
            if current.get(key, absent) != before:
                raise EditConflict(f'{label}的实际修改字段已变化，不能覆盖后续编辑')
            if after is absent:
                result.pop(key, None)
            else:
                result[key] = deepcopy(after)
        return result

    @classmethod
    def restore_objects(cls, current, expected, desired, *, label, allow_membership=False, source_fields=()):
        now = {item['id']: item for item in current}
        old = {item['id']: item for item in expected}
        wanted = {item['id']: item for item in desired}
        if len(now) != len(current) or len(old) != len(expected) or len(wanted) != len(desired):
            raise EditConflict(f'{label}身份不唯一，不能猜测恢复对象')
        if not allow_membership and old.keys() != wanted.keys():
            raise EditConflict(f'{label}集合发生广域变化，不能作为局部恢复')
        for key in old.keys() | wanted.keys():
            if old.get(key) == wanted.get(key):
                continue
            if key not in old:
                if key in now:
                    raise EditConflict(f'{label}已被后续编辑重新使用')
                now[key] = deepcopy(wanted[key])
            elif key not in wanted:
                if now.get(key) != old[key]:
                    raise EditConflict(f'{label}删除目标已变化')
                del now[key]
            else:
                if key not in now or any(now[key].get(field) != old[key].get(field) for field in source_fields):
                    raise EditConflict(f'{label}的实际来源依赖已变化')
                now[key] = cls.restore_fields(now[key], old[key], wanted[key], label=label)
        order = [item['id'] for item in current if item['id'] in now]
        order.extend(item['id'] for item in desired if item['id'] not in order)
        return [now[key] for key in order]

    def prepare_local_restore(self, command, original, changed_version, target_version, operation_type, action_label):
        ws, task = command.workspace_id, command.task
        expected_version = self.repository.timeline_records.get_timeline_version(ws, original.base_version_id if command.redo else original.result_version_id)
        desired_version = self.repository.timeline_records.get_timeline_version(ws, original.result_version_id if command.redo else original.base_version_id)
        expected, desired = expected_version.snapshot.get('montage'), desired_version.snapshot.get('montage')
        current_version = self.repository.timeline_records.get_timeline_version(ws, task.current_timeline_version_id)
        current = current_version.snapshot.get('montage')
        if not all(isinstance(value, dict) for value in (expected, desired, current)):
            raise EditConflict('原局部操作缺少完整时间线依据')
        restored = deepcopy(current)
        derived = {'id','created_at','updated_at','status','message','output_path','subtitle_path','low_confidence_clip_count'}
        collections = {'clips','cues','graphic_overlays'}
        if any(expected.get(key) != desired.get(key) for key in (expected.keys() | desired.keys()) - derived - collections):
            raise EditConflict('原操作包含全局时间线变化，需核对完整依赖后恢复')
        affected_clips = [item['id'] for item in expected['clips'] if item != next((value for value in desired['clips'] if value['id']==item['id']), None)]
        restored['clips'] = self.restore_objects(current['clips'], expected['clips'], desired['clips'], label='目标镜头',
            source_fields=('asset_id','segment_id','source_path','source_start_seconds','source_end_seconds','playback_rate','cue_id'))
        restored['cues'] = self.restore_objects(current['cues'], expected['cues'], desired['cues'], label='旁白对应字幕')
        restored['graphic_overlays'] = self.restore_objects(current.get('graphic_overlays',[]), expected.get('graphic_overlays',[]),
            desired.get('graphic_overlays',[]), label='图文层', allow_membership=True)

        plans = [creation_task_montage_plan_from_payload(value) for value in (current, expected, desired)]
        tracks = [self.montage_edits.current_subtitle_tracks(ws, task, current_version=version, montage=plan)
            for version, plan in zip((current_version, expected_version, desired_version), plans)]
        merged_tracks = self.restore_subtitle_tracks(*tracks, allow_membership=original.operation_type in {
            'UpdateSubtitleCue', 'ReplaceSubtitleText', 'ConfigureSubtitleTrack',
            'SplitSubtitleCue', 'MergeSubtitleCue', 'DeleteSubtitleCue'})
        sources = []
        person_sources = {}
        for clip_id in affected_clips:
            desired_clip = next(item for item in desired['clips'] if item['id']==clip_id)
            replacement = None if desired_clip.get('person_render_id') else (desired_clip['asset_id'], desired_clip['segment_id'])
            facts = self.repository.clip_sources.facts(ws, task.id, clip_ids=[clip_id], replacement=replacement,
                user_id=command.restore_member_id, local_diagnostic=False)
            sources.append((clip_id, replacement, facts))
            for plan in (plans[0], plans[2]):
                clip = next(item for item in plan.clips if item.id == clip_id)
                if clip.person_render_id:
                    cue = next(item for item in plan.cues if item.id == clip.cue_id)
                    person_sources[(clip.person_render_id, cue.id)] = (clip, cue, self.person_source_facts(task, clip, cue))
        still_changed = changed_stage_sections(desired_version.snapshot, restored, tracks[2], merged_tracks)
        original_sections = changed_stage_sections(expected_version.snapshot, desired, tracks[1], tracks[2])
        validity = {section_id: desired_version.snapshot.get('stage_segment_invalidations',{}).get(section_id)
            for section_id, publication in desired_version.snapshot.get('stage_segments',{}).items()
            if section_id in original_sections and section_id not in still_changed
            and current_version.snapshot.get('stage_segments',{}).get(section_id)==publication}
        controls = {'sources': sources, 'person_sources': list(person_sources.values()),
            'stage_adoption_restore': validity, 'current_version_id': current_version.id}
        return PreparedTimelineRestore(command, changed_version, target_version, plans[0],
            creation_task_montage_plan_from_payload(restored), None, None, True, operation_type, action_label,
            local_tracks=merged_tracks, local_sources=controls)

    def person_source_facts(self, task, clip, cue):
        from app.creation.person_stage_use_case import adopted_person_render_source

        path = adopted_person_render_source(self.repository, task, clip, cue, self.montage_edits.picture_renderer.person_path)
        stat = path.stat()
        return (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, stat.st_dev)

    @classmethod
    def restore_subtitle_tracks(cls, current, expected, desired, *, allow_membership):
        def key(track):
            return (track.language, track.source_language, track.generation_method)

        mapped = [{key(track): track for track in values} for values in (current, expected, desired)]
        if any(len(mapping) != len(values) for mapping, values in zip(mapped, (current, expected, desired))):
            raise EditConflict('原字幕轨身份不唯一，不能猜测恢复对象')
        if not allow_membership and mapped[1].keys() != mapped[2].keys():
            raise EditConflict('原字幕轨集合不适合局部恢复')
        # Version IDs and generated artifacts change on every save. Membership
        # conflicts compare the actual subtitle content, style and provenance.
        derived = {'id', 'timeline_version_id', 'created_at', 'updated_at', 'artifact_ref', 'transform_id'}
        def member(track):
            return {'id': key(track), **{field: value for field, value in asdict(track).items() if field not in derived}}

        changed_members = mapped[1].keys() ^ mapped[2].keys()
        membership = cls.restore_objects([member(track) for track in current],
            [member(track) for track in expected if key(track) in changed_members],
            [member(track) for track in desired if key(track) in changed_members],
            label='目标字幕轨', allow_membership=allow_membership)
        merged = [mapped[0][item['id']] if item['id'] in mapped[0] else mapped[2][item['id']] for item in membership]
        for identity in mapped[1].keys() & mapped[2].keys():
            old, wanted = mapped[1][identity], mapped[2][identity]
            if member(old) == member(wanted):
                continue
            current_track = mapped[0].get(identity)
            if current_track is None:
                raise EditConflict('原字幕轨已被后续编辑移除')
            cues = cls.restore_objects([asdict(cue) for cue in current_track.cues], [asdict(cue) for cue in old.cues],
                [asdict(cue) for cue in wanted.cues], label='目标字幕条目', allow_membership=True)
            style = cls.restore_fields(current_track.style, old.style, wanted.style, label='字幕样式')
            changed = replace(current_track, cues=[SubtitleCue(**cue) for cue in sorted(cues,key=lambda cue:cue['start_seconds'])], style=style)
            merged = [changed if key(item) == identity else item for item in merged]
        return merged

    def apply_restore(self, prepared: PreparedTimelineRestore, qualification: TimelineOutputQualification) -> MetadataEditResult:
        if qualification.prepared is not prepared:
            raise EditConflict('历史输出资格不属于本次恢复准备')
        command = prepared.command
        workspace_id, task = command.workspace_id, command.task
        task_id = task.id
        if prepared.local_tracks is not None:
            return self.apply_local_restore(prepared)
        if prepared.section_history is not None:
            from app.creation.section_change_use_case import SectionTimelineChange,SectionPreparationStale
            from app.creation.contracts import JobRun,NarrationSegment
            from datetime import datetime
            import hashlib
            import json
            history = prepared.section_history
            side = 'after' if command.redo else 'before'
            values = dict(history[f'{side}_segment'])
            for key in ('created_at','updated_at'):
                values[key] = datetime.fromisoformat(values[key])
            segment = NarrationSegment(**values)
            desired_version = self.repository.timeline_records.get_timeline_version(workspace_id,history[f'{side}_timeline_version_id'])
            publication = self.repository.execution_records.get_task_event(workspace_id,history[f'{side}_publication'])
            job = self.repository.job_records.create_job_run(JobRun(id=f'job_{uuid4().hex}',workspace_id=workspace_id,task_id=task_id,
                kind='section_timeline_restore',idempotency_key=f'section_restore:{uuid4().hex}',
                input_digest=hashlib.sha256(json.dumps([history,command.redo,prepared.section_dependencies],sort_keys=True).encode()).hexdigest()))
            self.repository.job_records.claim_job_run(workspace_id,job.id)
            change = SectionTimelineChange(self.repository,self.montage_edits.artifact_store)
            try:
                for attempt in range(2):
                    change.prepare_current(workspace_id,task_id,history['section_id'],publication.payload['montage'],segment,job,
                        desired_version=desired_version)
                    with self.repository.execution_inputs.creation_execution_read() as reader:
                        latest=self.repository.task_records.get_creation_task(workspace_id,task_id,connection=reader)
                        baseline=self.repository.timeline_records.get_timeline_version(workspace_id,latest.current_timeline_version_id,connection=reader)
                        prospective,_,_=change.compute(latest,baseline.snapshot,history['section_id'],
                            publication.payload['montage'],segment,'restore-permission-preparation',reader,desired_version=desired_version)
                    if baseline.snapshot.get('montage') is not None and prospective.get('montage') is not None:
                        authorization_write=self.montage_edits.prepare_material_scope(latest,
                            creation_task_montage_plan_from_payload(baseline.snapshot['montage']),
                            creation_task_montage_plan_from_payload(prospective['montage']),command.actor_identity,connection=None)
                    else:
                        current_section=self.repository.section_inputs.stage_segment_state(workspace_id,task_id,history['section_id'])
                        if current_section is None:
                            raise EditConflict('目标段的当前采用来源已丢失')
                        authorization_write=self.montage_edits.prepare_material_scope(latest,
                            creation_task_montage_plan_from_payload(current_section['montage']),
                            creation_task_montage_plan_from_payload(publication.payload['montage']),command.actor_identity,connection=None)
                    try:
                        operation,payload,current = self.repository.section_adoption.restore_script_section(workspace_id,task_id,
                            history=history,expected_dependencies=prepared.section_dependencies,redo=command.redo,
                            changed_version_id=prepared.changed_version.id,restore_version_id=prepared.target_version.id,
                            timeline_change=change,job=job,control_payload=command.control_payload(),write_guard=command.write_guard,
                            execution_readiness_write=authorization_write)
                        break
                    except SectionPreparationStale:
                        if attempt:
                            raise
            except Exception as exc:
                if self.repository.job_records.get_job_run(workspace_id,job.id).status=='running':
                    self.repository.job_records.finish_job_run(workspace_id,job.id,status='failed',failure_reason=str(exc),
                        impact='原时间线与已采用成果保留',repair_action='读取当前目标段及原恢复请求后核对冲突')
                raise
            plan = creation_task_montage_plan_from_payload(payload) if payload is not None else None
            return MetadataEditResult(operation,plan,current,None,
                execution_readiness=self.repository.edit_execution_readiness(workspace_id,task_id,operation.id))
        changed_version, target_version = prepared.changed_version, prepared.target_version
        def verify_restore(connection):
            if command.write_guard is not None:
                command.write_guard(connection)
            self.verify_whole_timeline_restore(workspace_id,task_id,changed_version.id,connection=connection)
        current_plan, restored_plan = prepared.current_plan, prepared.restored_plan
        restored_segments, restored_narration = prepared.restored_segments, prepared.restored_narration
        subtitle_cover_undo = prepared.subtitle_cover_undo
        operation_type, action_label = prepared.operation_type, prepared.action_label
        restore_output = qualification.output_is_valid
        if not restore_output:
            restored_plan, _ = prepare_changed_montage(
                restored_plan, message="草稿已恢复；原成片依赖或产物已失效，请明确重新生成。",
            )
        current_version = self.repository.timeline_records.get_timeline_version(
            workspace_id, task.current_timeline_version_id or ""
        )
        restored_tracks: list[SubtitleTrack] = []
        restored_cover: CoverAsset | None = None
        result_version_id: str | None = None
        if subtitle_cover_undo:
            result_version_id = f"timeline_{uuid4().hex}"
            target_tracks = self.montage_edits.current_subtitle_tracks(
                workspace_id,
                task,
                current_version=target_version,
                montage=restored_plan,
            )
            restored_tracks = [
                clone_subtitle_track(track, timeline_version_id=result_version_id)
                for track in target_tracks
            ]
            target_cover = self.montage_edits.cover_asset_for_version(workspace_id, task.id, target_version)
            restored_cover = (
                clone_cover_asset(target_cover, timeline_version_id=result_version_id)
                if target_cover is not None
                else None
            )
            current_tracks = self.montage_edits.current_subtitle_tracks(
                workspace_id,
                task,
                current_version=current_version,
                montage=current_plan,
            )
            current_cover = self.montage_edits.cover_asset_for_version(workspace_id, task.id, current_version)
            restored_refs = {
                *logical_montage_output_refs(restored_plan),
                *(track.artifact_ref for track in target_tracks if track.artifact_ref),
                *([target_cover.artifact_ref] if target_cover and target_cover.artifact_ref else []),
            }
            current_refs = [
                *logical_montage_output_refs(current_plan),
                *(track.artifact_ref for track in current_tracks if track.artifact_ref),
                *([current_cover.artifact_ref] if current_cover and current_cover.artifact_ref else []),
            ]
            invalidated_refs = [reference for reference in current_refs if reference not in restored_refs]
        else:
            invalidated_refs = logical_montage_output_refs(current_plan)
            if not restore_output:
                restored_plan, _montage_payload = prepare_changed_montage(
                    restored_plan, message="草稿已恢复，可以重新生成。",
                )
        now = utc_now()
        intent = self.repository.edit_records.create_edit_intent(
            EditIntent(
                id=f"intent_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task_id,
                raw_command=f"{action_label}上一次内容修改",
                context_stage="subtitle_cover" if subtitle_cover_undo else "montage",
                selected_object_refs=[],
                playhead_ms=None,
                intent_type="redo_timeline" if command.redo else "undo_timeline",
                scope={"kind": "timeline", "version_id": changed_version.id},
                constraints={},
                confidence=1.0,
                status="interpreted",
                created_at=now,
            )
        )
        proposal = self.repository.edit_records.create_edit_proposal(
            EditProposal(
                id=f"proposal_{uuid4().hex}",
                workspace_id=workspace_id,
                task_id=task_id,
                intent_id=intent.id,
                summary="重新应用刚才撤销的修改。" if command.redo else "恢复到上一次内容修改之前的版本。",
                target_refs=[f"timeline:{changed_version.id}"],
                operation={
                    "operation_type": operation_type,
                    "payload": {
                        "changed_version_id": changed_version.id,
                        "restore_version_id": target_version.id,
                        "base_version_id": task.current_timeline_version_id,
                        **command.control_payload(),
                    },
                },
                invalidated_refs=invalidated_refs,
                preview={
                    "before": {
                        "ref": f"timeline:{task.current_timeline_version_id}",
                        "label": "当前版本",
                        "detail": f"版本 {self.repository.timeline_records.get_timeline_version(workspace_id, task.current_timeline_version_id or '').sequence}",
                    },
                    "after": {
                        "ref": f"timeline:{target_version.id}",
                        "label": "恢复的任务状态",
                        "detail": f"来源版本 {target_version.sequence}",
                    },
                },
                external_effects=[f"{action_label}会创建新的历史版本，不会删除原版本。"],
                created_at=now,
                updated_at=now,
            )
        )
        if subtitle_cover_undo and result_version_id is not None:
            return self.montage_edits.persist_subtitle_edit(
                workspace_id=workspace_id,
                task=task,
                proposal=proposal,
                base_version=current_version,
                result_version_id=result_version_id,
                operation_type=operation_type,
                operation_payload=dict(proposal.operation["payload"]),
                subtitle_tracks=restored_tracks,
                cover_asset=restored_cover,
                updated_plan=restored_plan,
                snapshot_source=target_version.snapshot,
                narration_segments=restored_segments,
                narration_artifact=restored_narration,
                verify_sources=verify_restore,
                actor_identity=command.actor_identity,
            )
        return self.montage_edits.persist_montage_edit(
            workspace_id=workspace_id,
            task=task,
            proposal=proposal,
            base_version=current_version,
            updated_plan=restored_plan,
            operation_type=operation_type,
            operation_payload=dict(proposal.operation["payload"]),
            narration_segments=restored_segments,
            narration_artifact=restored_narration,
            subtitle_source_version=target_version,
            verify_sources=verify_restore,
            actor_identity=command.actor_identity,
        )

    def apply_local_restore(self, prepared):
        from app.creation.editing.metadata_use_case import execution_subtitle_track
        from app.subtitle_tracks import render_srt
        command = prepared.command
        ws, task = command.workspace_id, command.task
        base = self.repository.timeline_records.get_timeline_version(ws, prepared.local_sources['current_version_id'])
        now = utc_now()
        intent = self.repository.edit_records.create_edit_intent(EditIntent(id=f'intent_{uuid4().hex}',workspace_id=ws,
            task_id=task.id,raw_command=f'{prepared.action_label}指定原修改',context_stage='montage',
            selected_object_refs=[f'operation:{command.target_operation_id}'],playhead_ms=None,
            intent_type='redo_timeline' if command.redo else 'undo_timeline',scope={'operation_id':command.target_operation_id},
            constraints={},confidence=1,status='interpreted'))
        payload = {'changed_version_id':prepared.changed_version.id,'restore_version_id':prepared.target_version.id,
            'base_version_id':base.id,**command.control_payload(),
            'stage_adoption_restore':prepared.local_sources['stage_adoption_restore']}
        proposal = self.repository.edit_records.create_edit_proposal(EditProposal(id=f'proposal_{uuid4().hex}',workspace_id=ws,
            task_id=task.id,intent_id=intent.id,summary=f'{prepared.action_label}指定原修改，保留无关后续编辑',
            target_refs=[f'operation:{command.target_operation_id}'],operation={'operation_type':prepared.operation_type,'payload':payload},
            invalidated_refs=logical_montage_output_refs(prepared.current_plan),preview={},external_effects=[],created_at=now,updated_at=now))
        original_tracks = self.montage_edits.current_subtitle_tracks(ws,task,current_version=base,montage=prepared.current_plan)
        by_key = {(track.language,track.source_language,track.generation_method):track for track in original_tracks}
        tracks = []
        for track in prepared.local_tracks:
            previous = by_key.get((track.language,track.source_language,track.generation_method))
            if previous is None or track.cues != previous.cues or track.style != previous.style:
                if track.status == 'valid':
                    reference,transform = self.montage_edits.publish_subtitle_text(workspace_id=ws,task_id=task.id,
                        proposal_id=proposal.id,kind='subtitle_local_restore',input_refs=[f'timeline:{base.id}'],
                        content=render_srt(execution_subtitle_track(track)),engine='subtitle-srt/v1',parameters={'request_id':command.restore_request_id})
                    track = replace(track,artifact_ref=reference,transform_id=transform)
            tracks.append(track)
        def verify(connection):
            if command.write_guard is not None:
                command.write_guard(connection)
            for clip_id,replacement,facts in prepared.local_sources['sources']:
                current = self.repository.clip_sources.facts(ws,task.id,clip_ids=[clip_id],replacement=replacement,
                    user_id=command.restore_member_id,local_diagnostic=False,connection=connection)
                if current != facts:
                    raise EditConflict('局部恢复的实际来源或权限已变化')
            for clip, cue, facts in prepared.local_sources['person_sources']:
                if self.person_source_facts(task, clip, cue) != facts:
                    raise EditConflict('局部恢复的人物产物或固定授权已变化')
        prepare_changed_montage(prepared.restored_plan,message='指定原修改已恢复，无关后续编辑保留；可重新生成成片。')
        return self.montage_edits.persist_montage_edit(workspace_id=ws,task=task,proposal=proposal,base_version=base,
            updated_plan=prepared.restored_plan,operation_type=prepared.operation_type,operation_payload=payload,
            edited_subtitle_tracks=tracks,verify_sources=verify,actor_identity=command.actor_identity)

    def _timeline_restore_business_facts(self,version,connection):
        snapshot = dict(version.snapshot)
        for field in ('edit_operation_id','invalidated_refs','output_refs','transform_run_id','reused_job_id','audio_delivery',
                'creation_render_request_id','render_input_digest','render_input_timeline_version_id',
                'render_input_subtitle_tracks','render_input_subtitles_visible'):
            snapshot.pop(field,None)
        if isinstance(snapshot.get('montage'),dict):
            snapshot['montage'] = {key:value for key,value in snapshot['montage'].items()
                if key not in {'created_at','updated_at','message','status','output_path','subtitle_path'}}
        if isinstance(snapshot.get('narration_segments'),list):
            snapshot['narration_segments'] = [{key:value for key,value in row.items()
                if key not in {'created_at','updated_at'}} for row in snapshot['narration_segments']]
        record_fields = {'id','workspace_id','task_id','timeline_version_id','created_at','updated_at'}
        tracks = self.repository.subtitle_records.list_subtitle_tracks(version.workspace_id,version.task_id,timeline_version_id=version.id,connection=connection)
        track_facts = [{key:value for key,value in asdict(track).items()
            if key not in record_fields | {'artifact_ref','transform_id'}} for track in tracks]
        declared_tracks = snapshot.pop('subtitle_track_ids',None)
        snapshot['subtitles'] = {'items':sorted(track_facts,key=lambda item:json.dumps(item,sort_keys=True)),
            'explicit_empty':declared_tracks==[] and not track_facts}
        cover_id = snapshot.pop('cover_asset_id',None)
        cover = None
        if cover_id:
            cover = self.repository.cover_records.read_restore_facts(
                version.workspace_id, version.task_id, cover_id, connection=connection)
        snapshot['cover'] = cover
        return snapshot

    def verify_whole_timeline_restore(self,workspace_id,task_id,changed_version_id,*,connection=None):
        """The legacy snapshot restore is safe only while no later facts are lost."""
        with nullcontext(connection) if connection is not None else self.repository._connect() as conn:
            task = require_creation_task(conn,workspace_id,task_id)
            changed = self.repository.timeline_records.get_timeline_version(workspace_id,changed_version_id,connection=conn)
            current = self.repository.timeline_records.get_timeline_version(workspace_id,task.current_timeline_version_id,connection=conn)
            if (changed.task_id!=task_id or self._timeline_restore_business_facts(current,conn)
                    !=self._timeline_restore_business_facts(changed,conn)):
                raise ValueError('此修改之后已有其他时间线成果；当前编辑尚不支持局部反向恢复，不能覆盖后来成果')
            if 'narration_segments' in changed.snapshot:
                rows = self.repository.narration_records.list_narration_segments(workspace_id,task_id,task.confirmed_script_id,connection=conn)
                def facts(items):
                    return [{key:value for key,value in item.items() if key not in {'created_at','updated_at'}} for item in items]
                if facts([asdict(row) for row in rows])!=facts(changed.snapshot['narration_segments']):
                    raise ValueError('此修改之后已有新的配音成果；不能用旧整片快照覆盖，请保留当前成果并核对目标修改')
            if 'narration_cache_key' in changed.snapshot and task.narration_cache_key!=changed.snapshot['narration_cache_key']:
                raise ValueError('此修改之后已有新的配音总轨；不能用旧整片快照覆盖')
