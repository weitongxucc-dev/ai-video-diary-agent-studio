"""Semantic failures and invocation progress for task-bound business operations."""
from dataclasses import dataclass
from typing import Literal

TaskFailureKind = Literal['forbidden','invalid_input','not_found','conflict','unavailable']


class TaskOperationRejected(Exception):
    def __init__(self, detail, *, kind: TaskFailureKind):
        self.kind,self.detail=kind,detail
        super().__init__(str(detail))


class TaskBusinessRejected(PermissionError):
    def __init__(self, reason, *, kind: TaskFailureKind='forbidden'):
        self.kind=kind
        self.detail={'error':'task_goal_authority_unavailable','reason':reason}
        super().__init__(reason)


@dataclass
class TaskInvocation:
    owner_entered: bool = False
