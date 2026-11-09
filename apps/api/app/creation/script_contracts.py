"""Canonical document, generation-run and script-library records."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
import hashlib
import re
from app.models import utc_now

ScriptGenerationRunStatus = Literal["running", "succeeded", "failed"]
ScriptSelectionStatus = Literal["draft", "confirmed"]

@dataclass
class ScriptGenerationRun:
    id: str
    workspace_id: str
    task_id: str
    plan_id: str
    topic_id: str
    status: ScriptGenerationRunStatus
    route_snapshot: dict[str, object]
    prompt_schema_version: str
    input_references: list[dict[str, object]]
    reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    retryable: bool | None = None
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    run_kind: Literal["generation", "assistant_reply_transfer", "provided_copy"] = "generation"
    source_reply_reference: dict[str, str] | None = None

@dataclass
class CreationTaskScriptSection:
    id: str
    title: str
    content: str
    visual_prompt: str
    evidence_reference_ids: list[str]

@dataclass
class CreationTaskScript:
    id: str
    workspace_id: str
    task_id: str
    run_id: str
    topic_id: str
    title: str
    language: Literal["zh-CN", "en-US", "zh-CN/en-US"]
    sections: list[CreationTaskScriptSection]
    selection_status: ScriptSelectionStatus = "draft"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    confirmed_at: datetime | None = None
    # 复用稿没有自己的生成记录，这里记下它来自文案库的哪一条，供界面如实标注。
    reused_from_entry_id: str | None = None
    source_reply_reference: dict[str, str] | None = None
    adoption: dict[str, object] | None = None
    locked_body_digest: str | None = None
    draft_complete: bool = True

@dataclass
class ScriptLibrarySection:
    """库条目里冻结的一段文案。

    只保存用户自己写的正文与画面建议。资料标题、来源地址、授权字段一律不落库：
    那些是授权校验之后才可读的内容，存进库里就等于给快照开了一条绕过当前
    workspace 权限的路。出处只留 reference_ids，用来回查时定位。
    """

    id: str
    title: str
    content: str
    visual_prompt: str
    reference_ids: list[str]

@dataclass
class ScriptLibraryEntry:
    """一条命名保存的文案：可搜索、可复用，关联原创作任务。

    每次保存都追加一行，version 递增并指向被取代的那一条，旧版本原样留着。
    不静默合并，也就不会把用户已经保存过的文案悄悄改掉。
    """

    id: str
    workspace_id: str
    name: str
    category: str
    source_script_id: str
    source_task_id: str
    source_run_id: str
    source_topic_id: str
    version: int
    supersedes_entry_id: str | None
    title: str
    language: Literal["zh-CN", "en-US", "zh-CN/en-US"]
    sections: list[ScriptLibrarySection]
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

def _validated_assistant_reply_reference(value: dict[str, str]) -> dict[str, str]:
    conversation_id = value.get("conversation_id", "").strip()
    turn_id = value.get("turn_id", "").strip()
    response_digest = value.get("response_digest", "").strip().lower()
    if not conversation_id or not turn_id or re.fullmatch(r"[0-9a-f]{64}", response_digest) is None:
        raise ValueError("对话回复来源必须包含会话、已完成轮次和 SHA-256 摘要")
    return {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "response_digest": response_digest,
    }

def _assistant_reply_input_references(
    values: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    required = ("reference_id", "kind", "object_id", "version_or_identity", "title")
    for value in values:
        if not isinstance(value, dict) or any(not str(value.get(key, "")).strip() for key in required):
            raise ValueError("文案输入来源缺少固定版本身份")
        reference_id = str(value["reference_id"])
        if reference_id in result:
            raise ValueError("文案输入来源标识重复")
        result[reference_id] = {key: value[key] for key in required if key != "reference_id"}
    return result

def _assistant_reply_request_event_id(event_type: str, workspace_id: str, request_id: str) -> str:
    identity = hashlib.sha256(f"{workspace_id}\0{request_id}".encode("utf-8")).hexdigest()[:32]
    return f"{event_type}_{identity}"
