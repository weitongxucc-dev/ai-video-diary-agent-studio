"""Body-free execution snapshots and the existing execution digest wire bytes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from typing import TYPE_CHECKING, Iterable

from app.creation.contracts import CreationTask
from app.creation.plan_contracts import creation_plan_payload
from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection

if TYPE_CHECKING:
    from app.creation.plan_contracts import CreationPlan


@dataclass(frozen=True)
class ExecutionScriptHeader:
    id: str
    task_id: str
    title: str
    selection_status: str
    updated_at: datetime
    sections_total_count: int
    workspace_id: str
    language: str


@dataclass(frozen=True)
class ExecutionInputSnapshot:
    task: CreationTask
    plan: CreationPlan
    script_header: ExecutionScriptHeader | None
    input_digest: str


def execution_input_chunks(script_fields: dict | None, plan: CreationPlan,
                           sections: Iterable[CreationTaskScriptSection]):
    """Byte-identical to legacy dumps({script: asdict, plan: asdict}).

    Callers supply decoded/defaulted metadata, never storage JSON or row hashes.
    Only one section is encoded at a time. JSON's original separators, datetime
    str(), sorted dictionaries and ordered arrays are part of the saved Job key.
    """
    def encoded(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")

    if script_fields is not None and "sections" in script_fields:
        raise ValueError("执行摘要 metadata 不能携带正文")
    yield b'{"plan": '
    yield encoded(creation_plan_payload(plan))
    if script_fields is None:
        yield b', "script": null}'
        return
    yield b', "script": {'
    for index, name in enumerate(sorted((*script_fields, "sections"))):
        if index:
            yield b", "
        yield encoded(name) + b": "
        if name != "sections":
            yield encoded(script_fields[name])
            continue
        yield b"["
        for ordinal, section in enumerate(sections):
            if ordinal:
                yield b", "
            yield encoded(asdict(section))
        yield b"]"
    yield b"}}"


def execution_input_digest(script_fields, plan, sections) -> str:
    digest = hashlib.sha256()
    for chunk in execution_input_chunks(script_fields, plan, sections):
        digest.update(chunk)
    return digest.hexdigest()


def validate_execution_relationships(task: CreationTask,
                                     script: CreationTaskScript | ExecutionScriptHeader | None,
                                     plan: CreationPlan) -> None:
    if plan.source_media is not None:
        if (plan.creation_task_id != task.id or plan.id != task.plan_id or plan.workspace_id != task.workspace_id
                or plan.status != "confirmed"):
            raise ValueError("当前制作要求尚未确认")
        if (script is not None or task.confirmed_script_id is not None or task.narration_cache_key is not None
                or plan.presenter_share_target != 0):
            raise ValueError("原声来源任务不能混入文案、配音或人物制作输入")
        return
    if script is None:
        raise ValueError("请先确认实际文案")
    if task.confirmed_script_id != script.id:
        raise ValueError("请先确认当前文案；方案确认不能代替文案确认")
    if script.task_id != task.id or script.selection_status != "confirmed":
        raise ValueError("当前文案不是已确认的任务文案")
    if plan.creation_task_id != task.id or plan.status != "confirmed":
        raise ValueError("当前制作要求尚未确认")
