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
        return self.repository.task_budget_projection(workspace_id, task_id)

    def resume(self, workspace_id, task_id, actor, command, *, execution_control, runtime, write_guard=None):
        self.require_actor(workspace_id, task_id, actor)
        job = self.repository.get_job_run(workspace_id, command.expected_job_id)
        if job.task_id != task_id:
            raise ValueError('恢复请求不属于原任务')
        if job.kind=='creation_task_segment_revision':
            event = self.repository.get_task_event(workspace_id,f'event_{job.id}_start')
            result = runtime.registrations['creation_job'].owner.segments.resume_request(workspace_id,task_id,
                event.payload['command']['request_id'],actor,runtime=runtime,write_guard=write_guard)
            return {'request_id':command.request_id,'execution':result,'budget':self.read(workspace_id,task_id,actor)}
        latest = execution_control.execution.latest(workspace_id, task_id)
        if latest is None or latest.id != job.id:
            raise ValueError('原任务已有新作业，请读取原回执再恢复')
        result = execution_control.resume(workspace_id, task_id, actor, runtime,
            verify_media=False, write_guard=write_guard)
        return {'request_id': command.request_id, 'execution': result, 'budget': self.read(workspace_id, task_id, actor)}
