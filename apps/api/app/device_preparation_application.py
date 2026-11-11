"""Coordinate preparation with the existing device runtime and persisted owner.

The composition root supplies lifecycle access; this use case owns transitions,
not HTTP, media execution, or a second scheduler.
"""
from app.device_resource_preparation import OWNER_TYPE, CpuPreparationShape


class DevicePreparationConflict(RuntimeError):
    pass


class DevicePreparationApplication:
    def __init__(self, *, service, database_path, lifecycle_lock, current_runtime,
                 replace_runtime, stop_runtime, bind_runtime, shape_factory=CpuPreparationShape):
        self.service, self.database_path = service, database_path
        self.lifecycle_lock = lifecycle_lock
        self.current_runtime, self.replace_runtime = current_runtime, replace_runtime
        self.stop_runtime, self.bind_runtime = stop_runtime, bind_runtime
        self.shape_factory = shape_factory

    def status(self, request_id, actor):
        return self.service.status(database_path=self.database_path, request_id=request_id, actor=actor)

    def dispatch(self, action, *, actor, request_id, shape=None):
        """Dispatch the task-bound adapter using its verified execution identity."""
        if action == 'prepare':
            if not isinstance(shape, dict):
                raise ValueError('设备准备需要明确的单次输入形状')
            return self.prepare(self.shape_factory(**shape), request_id, actor)
        if shape is not None:
            raise ValueError('只有设备准备动作接受输入形状')
        if action == 'status':
            return self.status(request_id, actor)
        if action == 'finalize':
            return self.finalize(request_id, actor)
        raise ValueError('未知设备准备动作')

    def prepare(self, shape, request_id, actor):
        with self.lifecycle_lock:
            plan_id, budget = self.service.prepare(shape, actor, request_id=request_id)
            previous = self.service.status_for_plan(database_path=self.database_path,
                                                    plan_id=plan_id, actor=actor)
            if previous is not None and previous['state'] in {'done', 'failed', 'cancelled'}:
                return previous
            runtime = self.current_runtime()
            scope = (OWNER_TYPE, plan_id)
            if runtime is not None and OWNER_TYPE in runtime.registrations:
                if runtime.scheduler.dispatch_owner != scope:
                    raise DevicePreparationConflict('另一次设备准备仍在处理；请先读取并收束原请求。')
            else:
                if runtime is not None:
                    if not runtime.pause(30.0):
                        raise DevicePreparationConflict('当前执行单元尚未到达安全边界，原工作保持运行。')
                    self.stop_runtime()
                runtime = self.bind_runtime(self.service, budget, scope)
                self.replace_runtime(runtime)
            submitted = runtime.scheduler.submit(self.service.intent(plan_id, actor), actor)
            # A repeated request cannot restart terminal or uncertain execution.
            if submitted.state in {'waiting', 'blocked'} and runtime.last_error is None and not runtime.running:
                runtime.start()
            return self.status(submitted.id, actor)

    def finalize(self, request_id, actor):
        with self.lifecycle_lock:
            result = self.status(request_id, actor)
            if result['state'] not in {'done', 'failed', 'cancelled'}:
                raise DevicePreparationConflict('设备准备尚未完成，请观察原请求；不创建替代任务。')
            runtime = self.current_runtime()
            if result['state'] != 'done':
                # Closing a failed receipt publishes no profile. After restart
                # the normal driver can already own unrelated work; leave it
                # untouched rather than trapping the old preparation in the UI.
                if runtime is not None and runtime.scheduler.dispatch_owner == (OWNER_TYPE, result['plan_id']):
                    self.stop_runtime()
                return {**result, 'profile_published': False, 'normal_work_started': False}
            if runtime is not None:
                if runtime.scheduler.dispatch_owner != (OWNER_TYPE, result['plan_id']):
                    raise DevicePreparationConflict('当前设备驱动仍绑定其他工作，暂不能切换资源档。')
                self.stop_runtime()
            profile = self.service.finalize(database_path=self.database_path, request_id=request_id, actor=actor)
            # Normal work stays stopped until its next authorized operation.
            return {**result, 'profile_published': True, 'profile_id': profile.profile_id,
                    'revision': profile.revision, 'profile_digest': profile.digest,
                    'normal_work_started': False}
