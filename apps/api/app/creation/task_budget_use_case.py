"""Project one task's existing resource receipts without creating another ledger."""
from __future__ import annotations


class TaskBudgetUseCase:
    def __init__(self, repository, *, scope_for=None):
        self.repository = repository
        self.scope_for = scope_for

    def local_start_policy(self, workspace_id, task_id, actor, input_digest, *, operation):
        if actor is None:
            raise PermissionError('本地目标执行策略需要可信发起成员')
        self.require_actor(workspace_id, task_id, actor)
        if operation not in {'creation', 'segment_revision', 'narration_edit'}:
            raise ValueError('本地目标执行策略不适用于该动作')
        if self.scope_for is None:
            raise PermissionError('本地执行需要可信目标入口提供已核验授权scope，不按Job自造额度')
        reference = self.scope_for(workspace_id, task_id, actor)
        return {'policy_kind': 'scope', 'scope_id': reference['scope_id'], 'revision': reference['revision']}

    def require_actor(self, workspace_id, task_id, actor):
        task = self.repository.get_creation_task(workspace_id, task_id)
        if actor.workspace_id != workspace_id or task.owner_user_id != (actor.user_id or actor.member_id):
            raise PermissionError('不能读取或确认其他成员的任务预算')
        return task

    def read(self, workspace_id, task_id, actor):
        self.require_actor(workspace_id, task_id, actor)
        result = self.repository.task_budget_projection(workspace_id, task_id)
        scopes = self.repository.execution_budget_scopes_for_task(workspace_id, task_id, actor)
        for scope in scopes:
            policy, usage = scope['authorization'], scope['usage']
            limits = policy['limits']
            scope['remaining'] = {
                'execution_seconds': None if limits['execution_seconds'] is None else max(0,
                    limits['execution_seconds'] - usage['execution_seconds'] - usage['reserved_seconds']),
                'generated_bytes': None if limits['generated_bytes'] is None else max(0,
                    limits['generated_bytes'] - usage['generated_bytes'] - usage['reserved_bytes'])}
            scope['status'] = ('revoked' if policy['revoked'] else 'usage_unknown' if usage['unknown_unit_count']
                else 'exhausted' if any(value == 0 for value in scope['remaining'].values()) else 'available')
            scope['coverage'] = 'tts_pcm_only'
        result['scopes'] = scopes
        result['authorization_coverage'] = 'tts_pcm_only' if scopes else 'scope_unbound'
        if result['status'] == 'local_work_authorized':
            if not scopes:
                result['status'] = 'authorization_scope_unbound'
            else:
                states = {scope['status'] for scope in scopes}
                result['status'] = ('authorization_scope_mixed' if len(states) > 1
                    else 'authorization_scope_' + next(iter(states)))
        return result

    def resume(self, workspace_id, task_id, actor, command, *, execution_control, runtime, write_guard=None):
        self.require_actor(workspace_id, task_id, actor)
        job = self.repository.job_records.get_job_run(workspace_id, command.expected_job_id)
        if job.task_id != task_id:
            raise ValueError('恢复请求不属于原任务')
        if job.kind=='creation_task_segment_revision':
            event = self.repository.execution_records.get_task_event(workspace_id,f'event_{job.id}_start')
            result = runtime.registrations['creation_job'].owner.segments.resume_request(workspace_id,task_id,
                event.payload['command']['request_id'],actor,runtime=runtime,write_guard=write_guard)
            return {'request_id':command.request_id,'execution':result,'budget':self.read(workspace_id,task_id,actor)}
        latest = execution_control.execution.latest(workspace_id, task_id)
        if latest is None or latest.id != job.id:
            raise ValueError('原任务已有新作业，请读取原回执再恢复')
        result = execution_control.resume(workspace_id, task_id, actor, runtime,
            verify_media=False, write_guard=write_guard)
        return {'request_id': command.request_id, 'execution': result, 'budget': self.read(workspace_id, task_id, actor)}
