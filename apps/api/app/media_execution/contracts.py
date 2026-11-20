from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class MediaExecutionError(RuntimeError):
    """A failed media boundary with an actionable, persisted failure."""

    def __init__(self, reason: str, impact: str, repair_action: str) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (reason, impact, repair_action)):
            raise ValueError("媒体失败必须包含原因、影响和修复动作")
        super().__init__(reason)
        self.reason = reason
        self.impact = impact
        self.repair_action = repair_action


@dataclass(frozen=True)
class MediaOperation:
    """One business-level operation mapped by the FFmpeg adapter."""

    operation: str
    inputs: tuple[str, ...] = ()
    output_name: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.operation.strip():
            raise ValueError("媒体操作名称不能为空")
        if any(not str(value).strip() for value in self.inputs):
            raise ValueError("媒体输入路径不能为空")
        if self.output_name is not None and (not self.output_name.strip() or Path(self.output_name).is_absolute()):
            raise ValueError("媒体输出名称必须是相对路径")

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "MediaOperation":
        return cls(
            operation=str(value["operation"]),
            inputs=tuple(str(item) for item in value.get("inputs", ())),
            output_name=str(value["output_name"]) if value.get("output_name") is not None else None,
            options=dict(value.get("options", {})),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"operation": self.operation, "inputs": list(self.inputs),
                "output_name": self.output_name, "options": dict(self.options)}


@dataclass(frozen=True)
class EditPlan:
    workspace_id: str
    task_id: str
    base_version_id: str
    request_id: str
    operations: tuple[MediaOperation, ...]
    member_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in
                   (self.workspace_id, self.task_id, self.base_version_id, self.request_id)):
            raise ValueError("媒体计划缺少工作区、任务、版本或请求 ID")
        if not self.operations:
            raise ValueError("媒体计划至少需要一个操作")

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "EditPlan":
        operations = tuple(item if isinstance(item, MediaOperation) else MediaOperation.from_value(item)
                           for item in value["operations"])
        return cls(
            workspace_id=str(value["workspace_id"]), task_id=str(value["task_id"]),
            base_version_id=str(value["base_version_id"]), request_id=str(value["request_id"]),
            operations=operations, member_id=value.get("member_id"), metadata=dict(value.get("metadata", {})),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "base_version_id": self.base_version_id,
            "request_id": self.request_id,
            "member_id": self.member_id,
            "metadata": dict(self.metadata),
            "operations": [operation.as_dict() for operation in self.operations],
        }


@dataclass(frozen=True)
class MediaJob:
    """Public projection of a persisted media_execution JobRun."""

    id: str
    workspace_id: str
    task_id: str
    request_id: str
    base_version_id: str
    status: str
    attempt: int
    output_refs: tuple[str, ...] = ()
    timeline_version_id: str | None = None
    log_path: str | None = None
    failure_reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
