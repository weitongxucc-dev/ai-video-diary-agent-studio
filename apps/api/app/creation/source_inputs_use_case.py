"""Fixed content inputs and current-reader validation, independent of transport.

SourceRepository owns cross-workspace grants and exact-version reads. Preparing
inputs uses the task's source owner; validate_current separately checks the
present caller and is reused by the existing commit guards. No writes occur here.
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.creation.source_errors import SourcePreflightError
from app.production import CreationPlan
from app.creation.contracts import CreationTask
from app.repository import LEGACY_WORKSPACE_ID
from app.source_policy import content_allows_use
from app.source_repository import SourceRepository
from app.source_types import source_item_identity
from app.topics import CreationTaskTopicSource


def _safe_topic_source_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _topic_text_excerpt(value: str | None) -> str:
    return " ".join((value or "").split())[:8_000]



class CreationSourceInputs:
    def __init__(self, source_repository: SourceRepository):
        self.source_repository = source_repository

    def prepare(
        self,
        workspace_id: str,
        task: CreationTask,
        plan: CreationPlan,
        *,
        cloud_egress: bool,
    ) -> tuple[
        tuple[CreationTaskTopicSource, ...],
        list[dict[str, object]],
        dict[str, dict[str, object]],
    ]:
        if plan.workspace_id != workspace_id or task.plan_id != plan.id:
            raise SourcePreflightError(
                reason="creation_plan_relation_invalid",
                impact="无法确认本次选题使用的已确认方案，未调用文字模型。",
                repair_action="重新从当前创作任务进入内容预检并确认方案。",
                retryable=False,
            )
        provider_sources: list[CreationTaskTopicSource] = []
        input_references: list[dict[str, object]] = []
        evidence_by_reference_id: dict[str, dict[str, object]] = {}
        for reference in plan.knowledge_references:
            try:
                item = self.source_repository.get_knowledge_reference_readable_by(
                    workspace_id, reference.id, reference.version,
                    user_id=task.owner_user_id,
                    local_diagnostic=task.owner_user_id is None and workspace_id == LEGACY_WORKSPACE_ID,
                )
            except (KeyError, ValueError) as exc:
                raise SourcePreflightError(
                    reason="knowledge_reference_missing",
                    impact="方案中的知识库资料已不可用，未调用文字模型。",
                    repair_action="重新执行内容预检，确认仍可用的知识资料后重试。",
                    retryable=True,
                ) from exc
            if (
                item.version != reference.version
                or item.ownership != reference.ownership
                or item.authorization != reference.authorization
            ):
                raise SourcePreflightError(
                    reason="knowledge_reference_stale",
                    impact="方案中的知识版本已变化，未调用文字模型。",
                    repair_action="重新执行内容预检并确认最新知识版本。",
                    retryable=True,
                )
            if (not content_allows_use(item.use_state, "reference")
                    or item.use_state == "not_for_automation"):
                raise SourcePreflightError(
                    reason="knowledge_use_restricted",
                    impact="知识资料的使用范围不允许用于本次创作参考。",
                    repair_action="调整资料使用范围，或从当前方案移除此资料后重新预检。",
                    retryable=True,
                )
            text = _topic_text_excerpt(item.body)
            if not text:
                raise SourcePreflightError(
                    reason="knowledge_text_unavailable",
                    impact="知识资料没有可发送的文字摘录，未调用文字模型。",
                    repair_action="补充可读取的文字内容后重新预检；本机文件路径不会发送给模型。",
                    retryable=True,
                )
            reference_id = f"knowledge:{item.id}:{item.version}"
            provider_sources.append(
                CreationTaskTopicSource(
                    reference_id=reference_id,
                    kind="knowledge",
                    title=item.title,
                    text=text,
                )
            )
            input_references.append(
                {
                    "reference_id": reference_id,
                    "kind": "knowledge",
                    "object_id": item.id,
                    "version_or_identity": item.version,
                    "title": item.title,
                }
            )
            evidence_by_reference_id[reference_id] = {
                "kind": "knowledge",
                "reference_id": item.id,
                "version_or_identity": item.version,
                "title": item.title,
                "authorization_or_license": item.authorization,
                # 真实回查证据：网页快照才带原网址和抓取时间；文件与手填条目为空。
                # 不编造链接，也不拿当前版冒充引用当时那一版。
                "source_url": _safe_topic_source_url(item.source_url),
                "fetched_at": item.fetched_at.isoformat() if item.fetched_at else None,
                "content_hash": item.content_hash,
            }
        for reference in plan.source_references:
            try:
                item = self.source_repository.get_source_item_for_use(
                    workspace_id, reference.id, purpose="reference"
                )
            except KeyError as exc:
                raise SourcePreflightError(
                    reason="source_reference_missing",
                    impact="方案中的信息源条目已不可用，未调用文字模型。",
                    repair_action="重新同步或重新选择信息源后执行内容预检。",
                    retryable=True,
                ) from exc
            if (
                item.connector_id != reference.connector_id
                or item.title != reference.title
                or item.license != reference.license
                or not reference.identity
                or source_item_identity(item) != reference.identity
            ):
                raise SourcePreflightError(
                    reason="source_reference_stale",
                    impact="方案中的信息源身份或许可已变化，未调用文字模型。",
                    repair_action="重新执行内容预检并确认最新信息源条目。",
                    retryable=True,
                )
            if (not content_allows_use(item.use_state, "reference")
                    or item.use_state == "not_for_automation"):
                raise SourcePreflightError(
                    reason="source_use_restricted",
                    impact="信息源的使用范围不允许用于本次创作参考。",
                    repair_action="调整资料使用范围，或从当前方案移除此来源后重新预检。",
                    retryable=True,
                )
            license_value = item.license.strip()
            text = _topic_text_excerpt(item.body or item.subtitles)
            if not text:
                raise SourcePreflightError(
                    reason="source_text_unavailable",
                    impact="信息源没有可发送的文字摘录，未调用文字模型。",
                    repair_action="补充有可用正文或字幕的来源后重新预检。",
                    retryable=True,
                )
            reference_id = f"source:{item.id}"
            provider_sources.append(
                CreationTaskTopicSource(
                    reference_id=reference_id,
                    kind="source",
                    title=item.title,
                    text=text,
                )
            )
            input_references.append(
                {
                    "reference_id": reference_id,
                    "kind": "source",
                    "object_id": item.id,
                    "version_or_identity": source_item_identity(item),
                    "title": item.title,
                }
            )
            evidence_by_reference_id[reference_id] = {
                "kind": "source",
                "reference_id": item.id,
                "version_or_identity": source_item_identity(item),
                "title": item.title,
                "authorization_or_license": license_value,
                "source_url": _safe_topic_source_url(item.source_url),
            }
        if not provider_sources:
            raise SourcePreflightError(
                reason="content_sources_missing",
                impact="当前方案没有可发送的已授权文字资料，未调用文字模型。",
                repair_action="保存有正文且允许本次使用的知识或信息源后重新预检。",
                retryable=True,
            )
        return tuple(provider_sources), input_references, evidence_by_reference_id


    def validate_current(
        self, workspace_id: str, plan: CreationPlan, input_references: list[dict[str, object]],
        reader_id: str | None, local_diagnostic: bool = False,
    ) -> tuple[str | None, bool]:
        knowledge_by_id = {item.id: item for item in plan.knowledge_references}
        source_by_id = {item.id: item for item in plan.source_references}
        try:
            for reference in input_references:
                kind = reference.get("kind")
                object_id = str(reference.get("object_id") or "")
                identity = str(reference.get("version_or_identity") or "")
                if kind == "knowledge":
                    item = self.source_repository.get_knowledge_reference_readable_by(
                        workspace_id, object_id, identity, user_id=reader_id,
                        local_diagnostic=local_diagnostic,
                    )
                    planned = knowledge_by_id.get(object_id)
                    if (planned is None or item.version != planned.version
                        or item.title != planned.title
                        or item.authorization != planned.authorization
                        or item.ownership != planned.ownership
                        or not content_allows_use(item.use_state, "reference")
                        or item.use_state == "not_for_automation"):
                        raise ValueError("知识资料的当前版本、许可或使用范围已变化")
                elif kind == "source":
                    item = self.source_repository.get_source_item_for_use(workspace_id, object_id, purpose="reference")
                    planned = source_by_id.get(object_id)
                    if (planned is None or source_item_identity(item) != planned.identity
                        or item.connector_id != planned.connector_id or item.title != planned.title
                        or item.license != planned.license
                        or not content_allows_use(item.use_state, "reference")
                        or item.use_state == "not_for_automation"):
                        raise ValueError("信息源身份、许可或使用范围已变化")
                else:
                    raise ValueError("文案引用来源类型无效")
        except (KeyError, ValueError) as exc:
            raise SourcePreflightError(
                reason="assistant_reply_source_changed",
                impact="当前任务资料已不可用或使用范围已变化，文案没有写入。",
                repair_action="重新核对任务资料、固定版本和用途后再试。",
                retryable=False,
            ) from exc
        return reader_id, local_diagnostic
