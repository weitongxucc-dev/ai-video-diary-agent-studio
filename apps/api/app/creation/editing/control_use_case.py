"""Current proposal controls shared by HTTP and the task-bound assistant host."""
from contextlib import nullcontext
from dataclasses import replace
import re

from app.creation.editing.compound_use_case import CompoundSelectionCommand
from app.creation.editing.metadata_use_case import EditPreviewRejected
from app.creation.editing.sources import EditSourceVerifier
from app.creation.contracts import EditCommand
from app.creation.intent_use_case import CreationIntentUseCase
from app.creation.projection import project_task_summary
from app.media.picture_preview import PicturePreviewRejected
from app.source_policy import knowledge_item_readable as content_readable


class EditControlUseCase:
    def __init__(self, repository, *, conversations, compound, preview_response, wake=None):
        if compound.repository is not repository:
            raise ValueError('编辑控制必须绑定同一仓储负责人')
        self.repository, self.conversations, self.compound = repository, conversations, compound
        self.preview_response, self.wake = preview_response, wake

    @staticmethod
    def require_writer(task, actor):
        if (actor.workspace_id != task.workspace_id
                or task.owner_user_id and task.owner_user_id != (actor.user_id or actor.member_id)):
            raise EditPreviewRejected(403, '当前成员不能修改此任务')

    def read(self, task, *, actor=None):
        # The HTTP adapter keeps its existing visible/shared read gate. A host
        # caller carries its original actor, independently of write ownership.
        if actor is not None and (actor.workspace_id != task.workspace_id or not content_readable(
                user_id=actor.user_id or actor.member_id, local_diagnostic=False,
                visibility=task.visibility, owner_user_id=task.owner_user_id)):
            raise EditPreviewRejected(404, '没有找到当前任务。')
        summary = project_task_summary(self.repository, task).summary
        proposals = self.repository.edit_records.list_edit_proposals(task.workspace_id, task.id,
            base_version_id=task.current_timeline_version_id, limit=8)
        return {'task_id': task.id, 'timeline_version_id': summary['current_timeline_version_id'],
            'timeline_sequence': summary['timeline_sequence'], 'can_undo': summary['can_undo'],
            'can_redo': summary['can_redo'], 'proposals': [{'id': item.id, 'summary': item.summary,
                'operation_type': item.operation['operation_type'], 'steps': item.operation['payload'].get('steps', [])}
                for item in proposals]}

    def current_origin(self, task, actor, turn_id, *, origin=None, connection=None):
        if turn_id is None:
            raise EditPreviewRejected(409, '当前修改要求不可核验，未改变任务。')
        with (self.repository._connect() if connection is None else nullcontext(connection)) as conn:
            try:
                current = self.conversations._task_turn_source(conn, str(turn_id), task.workspace_id,
                    actor.user_id or actor.member_id, task.id)
            except ValueError as exc:
                raise EditPreviewRejected(409, str(exc)) from exc
        if origin is not None and any(current.get(key) != origin.get(key)
                for key in ('conversation_id', 'raw_command', 'input_id', 'input_turn_id')):
            raise EditPreviewRejected(409, '原编辑控制输入已变化，未提交操作')
        CreationIntentUseCase.reject_discussion_only_input(current['raw_command'])
        return current

    def require_head(self, task, version_id, sequence, *, connection=None):
        version = (self.repository.timeline_records.get_timeline_version(task.workspace_id,
            task.current_timeline_version_id, connection=connection) if task.current_timeline_version_id else None)
        if (task.current_timeline_version_id, version.sequence if version else 0) != (version_id, sequence):
            raise EditPreviewRejected(409, '修改所依据的任务版本已变化，未改变任务。')

    def proposal(self, task, proposal_id, actor, *, connection=None):
        try:
            proposal = self.repository.edit_records.get_edit_proposal(task.workspace_id, proposal_id, connection=connection)
        except KeyError as exc:
            raise EditPreviewRejected(404, '修改提案不存在') from exc
        intent = self.repository.edit_records.get_edit_intent(task.workspace_id, proposal.intent_id, connection=connection)
        if proposal.task_id != task.id or proposal.operation['payload'].get('base_version_id') != task.current_timeline_version_id:
            raise EditPreviewRejected(409, '这份修改的任务或基础版本已变化。')
        source = intent.constraints.get('request_source')
        if source is not None and source['member_id'] != (actor.user_id or actor.member_id):
            raise EditPreviewRejected(403, '提案不属于当前请求成员')
        return proposal

    @staticmethod
    def require_discard_instruction(command):
        if re.search('不要|不用|别|不(?:撤销|重做|取消|放弃)', command):
            raise EditPreviewRejected(409, '当前要求没有授权这一动作，任务保持不变。')
        prefix = '(?:请|麻烦|现在|先|帮我|请帮我|可以帮我|能不能帮我)?'
        scope = '(?:这(?:个|份|次)|当前|刚才的)?(?:修改)?(?:提案|方案|修改(?:建议)?)'
        if not re.fullmatch(prefix + f'(?:取消|放弃|丢弃)(?:一下)?{scope}(?:一下|吧|吗|好吗)?[。！!？?]?', command.strip()):
            raise EditPreviewRejected(409, '需要明确的撤销、重做或取消修改方案要求；未改变任务。')

    def discard(self, task, proposal_id, payload, *, actor, origin=None, write_guard=None):
        self.require_writer(task, actor)
        current = self.current_origin(task, actor, payload.assistant_turn_id, origin=origin)
        self.require_discard_instruction(current['raw_command'])
        self.require_head(task, payload.expected_timeline_version_id, payload.expected_timeline_sequence)
        snapshot = current['context'].get('target_snapshot', {})
        if (payload.expected_timeline_version_id, payload.expected_timeline_sequence) != (
                snapshot.get('timeline_version_id'), snapshot.get('timeline_sequence')):
            raise EditPreviewRejected(409, '修改所依据的任务版本已变化，未改变任务。')
        proposed = self.proposal(task, proposal_id, actor)

        def guarded(conn):
            if write_guard is not None:
                write_guard(conn)
            latest = self.repository.task_records.get_creation_task(task.workspace_id, task.id, connection=conn)
            self.require_writer(latest, actor)
            self.require_head(latest, payload.expected_timeline_version_id, payload.expected_timeline_sequence, connection=conn)
            if self.current_origin(latest, actor, payload.assistant_turn_id, connection=conn) != current:
                raise EditPreviewRejected(409, '原编辑控制输入在提交前已变化')
            actual = self.proposal(latest, proposal_id, actor, connection=conn)
            if actual != proposed:
                raise EditPreviewRejected(409, '原提案在提交前已变化')

        with self.repository._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            proposal = self.proposal(task, proposal_id, actor, connection=conn)
            if proposal.status == 'rejected':
                guarded(conn)
            else:
                proposal = self.repository.edit_records.transition_edit_proposal(task.workspace_id, task.id,
                    proposal_id, status='rejected', connection=conn, write_guard=guarded)
        if self.wake is not None:
            self.wake()
        return {'task_id': task.id, 'proposal_id': proposal.id, 'status': proposal.status,
            'timeline_version_id': task.current_timeline_version_id}

    def preview_selection(self, task, proposal_id, payload, *, actor, origin=None, write_guard=None, draft_only=True):
        self.require_writer(task, actor)
        proposed = self.proposal(task, proposal_id, actor)
        version = (self.repository.timeline_records.get_timeline_version(task.workspace_id, task.current_timeline_version_id)
            if task.current_timeline_version_id else None)
        turn_id = origin['assistant_turn_id'] if origin is not None else None
        current = self.current_origin(task, actor, turn_id, origin=origin) if origin is not None else None
        if current is not None:
            snapshot = current['context'].get('target_snapshot', {})
            if (snapshot.get('timeline_version_id'), snapshot.get('timeline_sequence')) != (
                    task.current_timeline_version_id, version.sequence if version else 0):
                raise EditPreviewRejected(409, '助手轮次的目标版本已失效')
        identity = {'member_id': actor.user_id or actor.member_id, 'local_diagnostic': False}
        action = {'operation_type': 'EditCompoundTimeline', 'base_version_id': task.current_timeline_version_id}
        command = EditCommand(task.workspace_id, task.id, identity, task.current_timeline_version_id,
            'EditCompoundTimeline', {'base_version_id': task.current_timeline_version_id})
        verifier = EditSourceVerifier(self.repository, identity=identity,
            audio_service=self.compound.montage_edits.audio_service, audio_actor=actor)
        source = {'source_kind': 'assistant' if current is not None else 'interactive', 'workspace_id': task.workspace_id,
            'task_id': task.id, 'member_id': actor.user_id or actor.member_id, 'actor_identity': actor.identity_payload(),
            'assistant_turn_id': turn_id, 'expected_timeline_version_id': task.current_timeline_version_id,
            'expected_timeline_sequence': version.sequence if version else 0,
            'action': action, 'source_facts': verifier.freeze(command),
            **({key: current[key] for key in ('conversation_id', 'raw_command', 'input_id', 'input_turn_id')
                if key in current} if current is not None else {})}

        def guarded(conn):
            if write_guard is not None:
                write_guard(conn)
            latest = self.repository.task_records.get_creation_task(task.workspace_id, task.id, connection=conn)
            self.require_writer(latest, actor)
            self.require_head(latest, task.current_timeline_version_id, version.sequence if version else 0, connection=conn)
            if self.proposal(latest, proposal_id, actor, connection=conn) != proposed:
                raise EditPreviewRejected(409, '原提案在提交前已变化')
            if current is not None and self.current_origin(latest, actor, turn_id, connection=conn) != current:
                raise EditPreviewRejected(409, '原编辑控制输入在提交前已变化')
            verifier.verify(replace(command, source=source), conn)

        control = CompoundSelectionCommand(task.workspace_id, task.id, proposal_id,
            identity,
            payload.included_step_indices, payload.raw_command, source)
        try:
            outcome = self.compound.preview_selection(control, draft_only=draft_only, execution=None, write_guard=guarded)
        except PicturePreviewRejected as exc:
            raise EditPreviewRejected(exc.status_code, exc.detail) from exc
        return self.preview_response(*outcome) if outcome is not None else None
