"""Original source facts, rechecked at the repository's direct-edit commit boundary."""
from app.audio_timeline import AudioEditRequest, apply_audio_edit, narration_audio_timeline
from app.creation.editing.picture import EditConflict
from app.montage import creation_task_montage_plan_from_payload
from dataclasses import replace
import sqlite3
from app.creation.contracts import ClipSourceSelection
from app.source_policy import knowledge_item_readable as _content_readable
from app.materials.contracts import material_usage_eligibility, effective_segment_usage_role
from app.creation.editing.affected import affected_source_clip_ids, affected_clip_facts


class EditClipSourceReader:
    """Resolve clip bounds and read current source facts through explicit readers."""
    def __init__(self, *, connection_factory, read_montage, read_asset, read_person_render,
                 asset_reference, resolve_reference, segment_eligible, conflict_error):
        self._connection_factory = connection_factory
        self._read_montage = read_montage
        self._read_asset = read_asset
        self._read_person_render = read_person_render
        self._asset_reference = asset_reference
        self._resolve_reference = resolve_reference
        self._segment_eligible = segment_eligible
        self._conflict_error = conflict_error

    def resolve(self, workspace_id, task_id, clip, values, *, user_id, local_diagnostic):
        replacing = values.get("asset_id") is not None or values.get("segment_id") is not None
        if clip.person_render_id and not replacing:
            render = self._read_person_render(workspace_id, task_id, clip.person_render_id)
            if render.status != "ready" or render.duration_seconds is None:
                raise self._conflict_error("人物口型片段不可用，请重新生成")
            return ClipSourceSelection(0., render.duration_seconds, True)
        asset = self._read_asset(workspace_id, values.get("asset_id") or clip.asset_id)
        segment_id = values.get("segment_id") or clip.segment_id
        segment = next((item for item in asset.segments if item.id == segment_id), None)
        if not _content_readable(user_id=user_id, local_diagnostic=local_diagnostic,
                visibility=asset.visibility, owner_user_id=asset.owner_user_id):
            raise PermissionError("当前成员无权使用该素材")
        if segment is None:
            raise KeyError("material segment not found")
        eligibility=material_usage_eligibility(asset,segment,purpose='automatic' if values.get('automation_only') else 'manual')
        if not eligibility.eligible:
            raise self._conflict_error(f'{eligibility.status}：{eligibility.reason}；{eligibility.repair_action}')
        if not self._segment_eligible(asset, segment, workspace_id, automation_only=bool(values.get('automation_only'))):
            raise self._conflict_error("素材片段当前不可用，请检查来源、范围和使用限制")
        speech = effective_segment_usage_role(asset, segment) == "a_roll"
        return ClipSourceSelection(segment.start_seconds, segment.end_seconds, speech,
            asset.id, segment.id, self._asset_reference(workspace_id, asset.id),
            "a_roll" if speech else "b_roll")

    def facts(self, workspace_id: str, task_id: str, *, clip_ids: list[str],
                               replacement: tuple[str, str] | None, user_id: str | None,
                               local_diagnostic: bool, connection: sqlite3.Connection | None = None):
        """The current material/source authority shared by typed preview and direct commit."""
        if connection is None:
            with self._connection_factory() as conn:
                return self.facts(workspace_id, task_id, clip_ids=clip_ids,
                    replacement=replacement, user_id=user_id, local_diagnostic=local_diagnostic, connection=conn)
        plan = self._read_montage(workspace_id, task_id, connection=connection)
        clips = [clip for clip in plan["clips"] if clip["id"] in clip_ids]
        if len(clips) != len(clip_ids):
            raise KeyError("当前任务镜头不存在")
        pairs = [(clip["asset_id"], clip["segment_id"]) for clip in clips if not clip.get("person_render_id")]
        if replacement is not None:
            pairs.append(replacement)
        facts = []
        for asset_id, segment_id in dict.fromkeys(pairs):
            asset = self._read_asset(workspace_id, asset_id, connection=connection)
            if not _content_readable(user_id=user_id, local_diagnostic=local_diagnostic,
                    visibility=asset.visibility, owner_user_id=asset.owner_user_id):
                raise PermissionError("当前成员不能使用该镜头素材")
            segment = next((item for item in asset.segments if item.id == segment_id), None)
            if segment is None:
                raise ValueError("镜头来源或使用状态已失效")
            reference = self._asset_reference(workspace_id, asset_id, connection=connection)
            path = self._resolve_reference(workspace_id, asset_id, reference, connection=connection)
            stat = path.stat()
            facts.append({"asset_id":asset_id, "segment_id":segment_id, "reference":reference,
                "modified_at":asset.modified_at.isoformat(), "start_seconds":segment.start_seconds,
                "end_seconds":segment.end_seconds, "size":stat.st_size, "mtime_ns":stat.st_mtime_ns,
                "ctime_ns":stat.st_ctime_ns, "inode":stat.st_ino, "device":stat.st_dev})
        return facts


class EditSourceVerifier:
    def __init__(self, repository, *, identity, audio_service=None, audio_actor=None):
        self.repository = repository
        self.identity = identity
        self.audio_service = audio_service
        self.audio_actor = audio_actor

    def facts(self, command, action, *, clip_ids=None, connection=None):
        if action['operation_type'] == 'EditAudioTimeline' and clip_ids is None:
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(
                command.workspace_id, command.task_id, connection=connection))
            graph = plan.audio_timeline or narration_audio_timeline(plan.narration_audio_path or '', plan.duration_seconds)
            edit = action['audio_edit']
            apply_audio_edit(graph, AudioEditRequest(base_version_id=command.base_version_id,
                action=edit['action'], target_id=edit['target_id'], values=edit['values']), round(plan.duration_seconds*1000))
            return self.audio_facts(command, graph)
        if action['operation_type'] == 'EditCompoundTimeline' and clip_ids is None:
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(
                command.workspace_id, command.task_id, connection=connection))
            graph = plan.audio_timeline or narration_audio_timeline(plan.narration_audio_path or '', plan.duration_seconds)
            return self.audio_facts(command, graph)
        ids = clip_ids if clip_ids is not None else (action.get('clip_ids') or list(action.get('expected_clips', {}))) if action['operation_type'] == 'AdjustVisualEffects' else (
            [action['clip_id']] if action.get('clip_id') else [])
        if not ids:
            return []
        replacement = (action['asset_id'], action['segment_id']) if action['operation_type'] in {'ReplaceClip','InsertClip'} and action.get('asset_id') is not None and action.get('segment_id') is not None else None
        return self.repository.clip_sources.facts(command.workspace_id, command.task_id, clip_ids=ids,
            replacement=replacement, user_id=self.identity['member_id'],
            local_diagnostic=self.identity['local_diagnostic'], connection=connection)

    def audio_facts(self, command, graph):
        facts = []
        for reference in sorted({clip.source_ref for track in graph.tracks for clip in track.clips}):
            stat = self.audio_service.source(command.workspace_id, command.task_id, reference, actor=self.audio_actor).stat()
            facts.append({'reference':reference, 'size':stat.st_size, 'mtime_ns':stat.st_mtime_ns,
                'ctime_ns':stat.st_ctime_ns, 'inode':stat.st_ino, 'device':stat.st_dev})
        return facts

    def freeze(self, command):
        action = {'operation_type':command.operation_type, **command.values}
        return self.facts(command, action)

    def verify(self, command, connection):
        action = command.source['action'] if command.source is not None else {
            'operation_type':command.operation_type, **command.values}
        expected = command.source['source_facts'] if command.source is not None else command.input_source_facts
        if self.facts(command, action, connection=connection) != expected:
            raise EditConflict("预览后的源文件或片段修订已变化，请重新预览")
        if command.affected_clip_ids is not None:
            current = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(
                command.workspace_id, command.task_id, connection=connection))
            if affected_clip_facts(current, command.affected_clip_ids) != command.affected_clip_facts:
                raise EditConflict("修改实际影响的镜头或衔接关系已变化，请重新预览")
        if command.affected_clip_ids is not None and self.facts(command, action,
                clip_ids=command.affected_clip_ids, connection=connection) != command.affected_source_facts:
            raise EditConflict("修改实际影响范围内的源文件或片段修订已变化，请重新预览")

    def verify_change(self, command, after_payload, connection):
        before = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(
            command.workspace_id, command.task_id, connection=connection))
        after = creation_task_montage_plan_from_payload(after_payload)
        actual = affected_source_clip_ids(before, after)
        if command.affected_clip_ids != actual:
            raise EditConflict("封存来源范围与本次实际影响的镜头不一致，请重新预览")
        self.verify(command, connection)

    def freeze_affected(self, command, clip_ids):
        action = command.source['action'] if command.source is not None else {
            'operation_type':command.operation_type, **command.values}
        return self.facts(command, action, clip_ids=clip_ids)

    def bind_affected(self, command, before, after):
        ids = affected_source_clip_ids(before, after)
        if command.source is None and command.input_source_facts is None:
            command = replace(command, input_source_facts=self.freeze(command))
        if not ids:
            return replace(command, affected_clip_ids=(), affected_source_facts=[], affected_clip_facts=[])
        return replace(command, affected_clip_ids=ids, affected_source_facts=self.freeze_affected(command, ids),
            affected_clip_facts=affected_clip_facts(before, ids))

    @staticmethod
    def scope_payload(command):
        return {'clip_ids':list(command.affected_clip_ids or ()), 'input_source_facts':command.input_source_facts,
            'source_facts':command.affected_source_facts, 'clip_facts':command.affected_clip_facts}

    @staticmethod
    def restore_scope(command, bound):
        return replace(command, input_source_facts=bound['input_source_facts'],
            affected_clip_ids=tuple(bound['clip_ids']), affected_source_facts=bound['source_facts'],
            affected_clip_facts=bound['clip_facts'])
