"""知识参数比较与人工裁定。

比较范围只来自用户明确确认的对象、参数名、单位和适用条件：范围里没有的东西
（对象归属、同义词、未知单位换算）一律不猜。同一范围内两侧值不同就是待裁定
冲突，两侧证据都保留；未裁定时不给出唯一事实。裁定记录操作者、理由、时间和
双方证据版本，未选的一侧不删除。

裁定绑定在具体两个知识版本上：重新解析或出现新版本会形成新的待裁定冲突，不会
覆盖历史裁定，也不会自动沿用旧的裁定结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4


class KnowledgeConflictError(ValueError):
    """比较范围或裁定内容不成立时抛出，调用方必须显式失败。"""


@dataclass(frozen=True)
class ParameterScope:
    """用户明确确认的比较范围。缺任何一项都不比较。"""

    object_label: str
    parameter_key: str
    unit: str
    condition: str


@dataclass(frozen=True)
class ParameterSide:
    """一侧证据：固定到知识条目版本和页码/段落定位。"""

    knowledge_item_id: str
    version: str
    value: str
    locator: str
    title: str = ""

    @property
    def evidence_ref(self) -> str:
        return f"{self.knowledge_item_id}@{self.version}"


@dataclass(frozen=True)
class ParameterConflict:
    """同一范围内的两侧证据。值相同就不是冲突，不需要裁定。"""

    id: str
    workspace_id: str
    scope: ParameterScope
    left: ParameterSide
    right: ParameterSide
    created_at: datetime
    adjudication: "ParameterAdjudication | None" = None

    @property
    def is_conflict(self) -> bool:
        return self.left.value.strip() != self.right.value.strip()

    @property
    def status(self) -> str:
        if not self.is_conflict:
            return "agreed"
        return "adjudicated" if self.adjudication else "pending"


@dataclass(frozen=True)
class ParameterAdjudication:
    """一次人工裁定：谁、为什么、什么时候、按哪两个版本裁的。"""

    id: str
    workspace_id: str
    conflict_id: str
    operator: str
    reason: str
    decided_at: datetime
    left_version_ref: str
    right_version_ref: str
    retained_side: str
    retained_value: str
    corrected_value: str | None = None


def build_parameter_conflict(
    workspace_id: str,
    *,
    object_label: str,
    parameter_key: str,
    unit: str,
    condition: str,
    left: ParameterSide,
    right: ParameterSide,
) -> ParameterConflict:
    """按用户确认的范围建立一个比较；范围不全或两侧指同一条证据都不成立。"""
    scope_fields = {
        "object_label": object_label,
        "parameter_key": parameter_key,
        "unit": unit,
        "condition": condition,
    }
    missing = [name for name, value in scope_fields.items() if not str(value or "").strip()]
    if missing:
        # 范围缺项就只能靠猜对象或猜换算，这里明确拒绝。
        raise KnowledgeConflictError(
            "比较范围需要明确的对象、参数名、单位和适用条件，缺少：" + "、".join(missing)
        )
    if left.evidence_ref == right.evidence_ref:
        raise KnowledgeConflictError("比较两侧必须来自不同的知识版本")
    scope = ParameterScope(
        object_label=object_label.strip(),
        parameter_key=parameter_key.strip(),
        unit=unit.strip(),
        condition=condition.strip(),
    )
    return ParameterConflict(
        id=f"conflict_{uuid4().hex}",
        workspace_id=workspace_id,
        scope=scope,
        left=left,
        right=right,
        created_at=datetime.now(UTC),
    )


def adjudicate_parameter_conflict(
    workspace_id: str,
    conflict: ParameterConflict,
    *,
    operator: str,
    reason: str,
    retained_side: str,
    retained_value: str | None = None,
    corrected_value: str | None = None,
) -> ParameterConflict:
    """记录一次人工裁定。两侧证据保留，未选一侧不删除。

    裁定绑定在当前两侧版本上：之后任一侧出现新版本，比较就是新冲突，
    不会自动沿用这里的裁定。
    """
    if not conflict.is_conflict:
        raise KnowledgeConflictError("两侧取值相同，不构成待裁定冲突")
    if retained_side not in {"left", "right"}:
        raise KnowledgeConflictError("请选择保留左侧或右侧证据")
    if not str(operator or "").strip():
        raise KnowledgeConflictError("裁定需要记录当前操作者")
    if not str(reason or "").strip():
        raise KnowledgeConflictError("裁定需要记录理由")
    side = conflict.left if retained_side == "left" else conflict.right
    value = retained_value.strip() if retained_value else side.value
    if not value.strip():
        raise KnowledgeConflictError("保留的取值不能为空")
    correction = corrected_value.strip() if corrected_value and corrected_value.strip() else None
    adjudication = ParameterAdjudication(
        id=f"adjudication_{uuid4().hex}",
        workspace_id=workspace_id,
        conflict_id=conflict.id,
        operator=operator.strip(),
        reason=reason.strip(),
        decided_at=datetime.now(UTC),
        left_version_ref=conflict.left.evidence_ref,
        right_version_ref=conflict.right.evidence_ref,
        retained_side=retained_side,
        retained_value=value,
        corrected_value=correction,
    )
    return ParameterConflict(
        id=conflict.id,
        workspace_id=conflict.workspace_id,
        scope=conflict.scope,
        left=conflict.left,
        right=conflict.right,
        created_at=conflict.created_at,
        adjudication=adjudication,
    )