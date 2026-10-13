"""Original source facts, rechecked at the repository's direct-edit commit boundary."""
from app.audio_timeline import AudioEditRequest, apply_audio_edit, narration_audio_timeline
from app.creation.editing.picture import EditConflict
from app.montage import creation_task_montage_plan_from_payload


class EditSourceVerifier:
    def __init__(self, repository, *, identity, audio_service=None, audio_actor=None):
        self.repository = repository
        self.identity = identity
        self.audio_service = audio_service
        self.audio_actor = audio_actor

    def facts(self, command, action, *, connection=None):
        if action['operation_type'] == 'EditAudioTimeline':
            plan = creation_task_montage_plan_from_payload(self.repository.get_creation_task_montage_plan(
                command.workspace_id, command.task_id, connection=connection))
            graph = plan.audio_timeline or narration_audio_timeline(plan.narration_audio_path or '', plan.duration_seconds)
            edit = action['audio_edit']
            apply_audio_edit(graph, AudioEditRequest(base_version_id=command.base_version_id,
                action=edit['action'], target_id=edit['target_id'], values=edit['values']), round(plan.duration_seconds*1000))
            facts = []
            for reference in sorted({clip.source_ref for track in graph.tracks for clip in track.clips}):
                stat = self.audio_service.source(command.workspace_id, command.task_id, reference, actor=self.audio_actor).stat()
                facts.append({'reference':reference, 'size':stat.st_size, 'mtime_ns':stat.st_mtime_ns,
                    'ctime_ns':stat.st_ctime_ns, 'inode':stat.st_ino, 'device':stat.st_dev})
            return facts
        ids = action.get('clip_ids') if action['operation_type'] == 'AdjustVisualEffects' else (
            [action['clip_id']] if action.get('clip_id') else [])
        if not ids:
            return []
        replacement = (action['asset_id'], action['segment_id']) if action.get('asset_id') is not None and action.get('segment_id') is not None else None
        return self.repository.clip_edit_source_facts(command.workspace_id, command.task_id, clip_ids=ids,
            replacement=replacement, user_id=self.identity['member_id'],
            local_diagnostic=self.identity['local_diagnostic'], connection=connection)

    def freeze(self, command):
        action = {'operation_type':command.operation_type, **command.values}
        return self.facts(command, action)

    def verify(self, command, connection):
        action = command.source['action'] if command.source is not None else {
            'operation_type':command.operation_type, **command.values}
        expected = command.source['source_facts'] if command.source is not None else command.input_source_facts
        if self.facts(command, action, connection=connection) != expected:
            raise EditConflict("预览后的源文件或片段修订已变化，请重新预览")
