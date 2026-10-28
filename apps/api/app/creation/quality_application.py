"""Shared quality reads and original-failure continuation; no execution owner here."""
from app.creation.quality_inspection_use_case import QUALITY_KIND
from app.device_runtime import RuntimeUnavailable
from app.source_policy import knowledge_item_readable as content_readable


class QualityApplication:
    def __init__(self, production, *, offline_reader, current_runtime, bind_runtime, lifecycle_lock):
        self.production = production
        self.offline_reader = offline_reader
        self.current_runtime = current_runtime
        self.bind_runtime = bind_runtime
        self.lifecycle_lock = lifecycle_lock

    def _registration(self):
        runtime = self.current_runtime()
        registration = runtime.registrations.get(QUALITY_KIND) if runtime is not None else None
        return runtime, registration

    def _reader(self):
        _, registration = self._registration()
        return registration.owner.service if registration is not None else self.offline_reader()

    def _task(self, workspace_id, task_id, reader_id):
        if reader_id is None:
            raise PermissionError('请使用可信成员身份检查成片')
        task = self.production.get_creation_task(workspace_id, task_id)
        if not content_readable(user_id=reader_id, local_diagnostic=False,
                visibility=task.visibility, owner_user_id=task.owner_user_id):
            raise KeyError(task_id)
        return task

    def _job(self, workspace_id, task_id, job_id, actor):
        self._task(workspace_id, task_id, actor.user_id or actor.member_id)
        job = self.production.job_records.get_job_run(workspace_id, job_id)
        if job.task_id != task_id or job.kind != QUALITY_KIND:
            raise KeyError(job_id)
        return job

    def read(self, workspace_id, task_id, reader_id, *, actor=None):
        self._task(workspace_id, task_id, reader_id)
        _, registration = self._registration()
        service = registration.owner.service if registration is not None else self.offline_reader()
        result = service.projection(workspace_id, task_id)
        if result['check_state'] == 'failed':
            result['continuation'] = service.continuation_projection(workspace_id, task_id,
                actor, runtime_available=registration is not None)
        return result

    def read_continuation(self, workspace_id, task_id, job_id, failed_request_id, actor):
        self._job(workspace_id, task_id, job_id, actor)
        receipt = self._reader().continued_phase_receipt(workspace_id, job_id, failed_request_id, actor)
        return {'job_id': job_id, 'failed_request_id': failed_request_id,
                'status': 'recorded' if receipt is not None else 'unknown', 'receipt': receipt}

    def continue_failed(self, workspace_id, task_id, job_id, failed_request_id, actor, *, write_guard=None):
        # An existing successor is a read, even if HEAD/runtime/policy changed.
        original = self.read_continuation(workspace_id, task_id, job_id, failed_request_id, actor)
        if original['receipt'] is not None:
            return original['receipt']
        with self.lifecycle_lock:
            if write_guard is not None:
                write_guard(None)
            runtime, registration = self._registration()
            if runtime is None:
                runtime = self.bind_runtime()
                registration = runtime.registrations.get(QUALITY_KIND) if runtime is not None else None
            if registration is None:
                raise RuntimeUnavailable('当前设备正处于能力准备阶段，请先收束原准备请求')
            service = registration.owner.service
            successor = service.continue_failed_phase(runtime.scheduler, workspace_id, job_id,
                failed_request_id, actor, write_guard=write_guard)
            current_job, _, record, _ = service.read(workspace_id, job_id)
            if current_job.status in {'queued', 'running'}:
                if not runtime.running:
                    runtime.start()
                runtime.wake()
            return {'check_id': record['check_id'], 'job_id': job_id, 'request_id': successor}
