"""Readable library sources and reuse orchestration, independent of HTTP.

ScriptDraftUseCase owns request replay and ScriptRecords owns every write. This
use case supplies the current read authority and the reuse commit guard.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4

from app.creation.script_draft_use_case import ScriptDraftRejected, ScriptDraftUseCase
from app.production import (
    CreationPlan,
    CreationPlanKnowledgeReference,
    CreationPlanSourceReference,
    ProductionRepository,
)
from app.source_policy import knowledge_item_readable as content_readable
from app.source_policy import content_allows_use
from app.source_repository import SourceRepository
from app.source_types import source_item_identity


@dataclass
class ScriptLibrarySource:
    reference_id: str
    status: Literal["readable", "missing", "version_changed"]
    title: str | None = None
    version_or_identity: str | None = None
    source_url: str | None = None
    cited_version: str | None = None
    cited_source_url: str | None = None
    cited_fetched_at: datetime | None = None
    cited_content_hash: str | None = None
    locator: str | None = None


class ScriptLibraryUseCase:
    def __init__(self, repository: ProductionRepository, source_repository: SourceRepository,
                 *, reader: Callable[[str], tuple[str | None, bool]],
                 draft_use_case: ScriptDraftUseCase):
        self.repository = repository
        self.source_repository = source_repository
        self.reader = reader
        self.draft_use_case = draft_use_case

    def _read_task(self, workspace_id: str, task_id: str):
        task = self.repository.get_creation_task(workspace_id, task_id)
        user_id, local_diagnostic = self.reader(workspace_id)
        if not content_readable(user_id=user_id, local_diagnostic=local_diagnostic,
                                visibility=task.visibility, owner_user_id=task.owner_user_id):
            raise KeyError(task_id)
        return task

    def read_header(self, workspace_id: str, entry_id: str):
        header = self.repository.script_records.read_script_library_entry_header(workspace_id, entry_id)
        self._read_task(workspace_id, header['source_task_id'])
        return header

    def read_script_header(self, workspace_id: str, script_id: str):
        header = self.repository.script_records.read_creation_task_script_header(workspace_id, script_id)
        self._read_task(workspace_id, header['task_id'])
        return header

    def list_headers(self, workspace_id: str, **window):
        page = self.repository.script_records.list_script_library_entry_headers(workspace_id, **window)
        items = []
        for entry in page['items']:
            try:
                self._read_task(workspace_id, entry['source_task_id'])
            except KeyError:
                continue
            items.append(entry)
        return {**page, 'items': items, 'total_count': None}

    def read_sections(self, workspace_id: str, entry_id: str, **window):
        self.read_header(workspace_id, entry_id)
        return self.repository.script_records.read_script_library_sections(workspace_id, entry_id, **window)

    def read_sources(self, workspace_id: str, entry_id: str):
        states, _, _ = self._resolve_sources(workspace_id, self.read_header(workspace_id, entry_id))
        return states

    def _attach_cited_version_facts(
        self, state: ScriptLibrarySource,
        workspace_id: str,
        item_id: str,
        version: str,
    ) -> None:
        """回填引用绑定那一版的真实快照事实，供前端回查当时内容。

        只读版本快照里已经存下的来源链接、抓取时刻和指纹；引用本身只记到条目与
        版本，没有段落定位，所以 `locator` 留空，不拿第一段冒充引用定位。
        """
        try:
            versions = self.source_repository.list_knowledge_item_versions(workspace_id, item_id)
        except KeyError:
            return
        snapshot = next(
            (value for value in versions if value.version == version),
            None,
        )
        if snapshot is None:
            return
        state.cited_version = snapshot.version
        state.cited_source_url = snapshot.source_url
        state.cited_fetched_at = snapshot.fetched_at
        state.cited_content_hash = snapshot.content_hash


    def _resolve_sources(
        self, workspace_id: str, entry: dict,
    ) -> tuple[list[ScriptLibrarySource], list[CreationPlanKnowledgeReference], list[CreationPlanSourceReference]]:
        task = self.repository.get_creation_task(workspace_id, entry['source_task_id'])
        user_id, local_diagnostic = self.reader(workspace_id)
        if not content_readable(user_id=user_id, local_diagnostic=local_diagnostic,
                                visibility=task.visibility, owner_user_id=task.owner_user_id):
            raise KeyError(entry['id'])
        plan = self.repository.get_creation_plan(workspace_id, task.plan_id)
        if plan.creation_task_id != task.id:
            raise KeyError(task.id)
        knowledge = {reference.id: reference for reference in plan.knowledge_references}
        sources = {reference.id: reference for reference in plan.source_references}
        resolved_knowledge: list[CreationPlanKnowledgeReference] = []
        resolved_sources: list[CreationPlanSourceReference] = []
        result: list[ScriptLibrarySource] = []
        references = self.repository.script_records.script_library_reference_ids(workspace_id, entry['id'])
        for ref in references:
            state = ScriptLibrarySource(reference_id=ref, status="missing")
            if ref.startswith("knowledge:"):
                parts = ref.removeprefix("knowledge:").rsplit(":", 1)
                if len(parts) == 2:
                    item_id, version = parts
                    try:
                        # 与知识域读取同一个判据：私有只对上传者本人，读不到当不存在。
                        item = self.source_repository.get_knowledge_item_readable_by(
                            workspace_id, item_id,
                            user_id=user_id, local_diagnostic=local_diagnostic,
                        )
                    except (KeyError, ValueError):
                        item = None
                    original = knowledge.get(item_id)
                    if item is not None and original is not None:
                        # 有权读到这一版（无论是当前版还是历史版）才回填快照事实；
                        # 读不到就不给来源链接、抓取时刻和指纹。
                        self._attach_cited_version_facts(
                            state, item.workspace_id, item_id, version
                        )
                        if (item.version != version or item.version != original.version
                                or not content_allows_use(item.use_state, "reference")):
                            state.status = "version_changed"
                        else:
                            state.status = "readable"
                            state.title = item.title
                            state.version_or_identity = item.version
                            resolved_knowledge.append(original)
            elif ref.startswith("source:"):
                item_id = ref.removeprefix("source:")
                try:
                    item = self.source_repository.get_source_item(workspace_id, item_id)
                except KeyError:
                    item = None
                original = sources.get(item_id)
                if item is not None and original is not None:
                    if (not original.identity or source_item_identity(item) != original.identity
                            or item.connector_id != original.connector_id or item.license != original.license):
                        state.status = "version_changed"
                    elif content_allows_use(item.use_state, "reference"):
                        state.status = "readable"
                        state.title = item.title
                        state.source_url = item.source_url
                        state.version_or_identity = original.identity
                        resolved_sources.append(original)
            result.append(state)
        return result, resolved_knowledge, resolved_sources


    def read_reusable_entry(self, workspace_id: str, entry_id: str):
        entry = self.read_header(workspace_id, entry_id)
        states, knowledge, sources = self._resolve_sources(workspace_id, entry)
        if any(source.status != "readable" for source in states):
            raise ScriptDraftRejected(status_code=409,
                detail="文案引用的来源已不可读或版本已变化，请核对原任务来源")
        return entry, knowledge, sources

    def save_entry(self, workspace_id: str, payload):
        return self.draft_use_case.save_library_entry(workspace_id, payload,
            member_id=self.reader(workspace_id)[0])

    def read_request(self, workspace_id: str, request_id: str, operation: Literal['save', 'reuse']):
        read = (self.draft_use_case.library_save_request_result if operation == 'save'
                else self.draft_use_case.library_reuse_request_result)
        return read(workspace_id, request_id, member_id=self.reader(workspace_id)[0])

    def reuse_entry(self, workspace_id: str, entry_id: str, payload):
        member_id = self.reader(workspace_id)[0]
        # A completed request belongs to its original result. The draft use case
        # still checks the submitted digest and result ownership; current source
        # eligibility only gates a new write, not recovery of that receipt.
        if self.repository.script_records.library_reuse_request_result(
            workspace_id, str(payload.request_id), member_id,
        ) is not None:
            return self.draft_use_case.reuse_library_entry(
                workspace_id, entry_id, payload, member_id=member_id, plan=None)
        if not payload.requirements_confirmed:
            raise ScriptDraftRejected(status_code=409, detail="请明确确认新任务要求")
        entry, knowledge, sources = self.read_reusable_entry(workspace_id, entry_id)
        plan = CreationPlan(
            id=f"plan_{uuid4().hex[:12]}", workspace_id=workspace_id,
            name=payload.name.strip(), goal=f"复用文案：{entry['name']}（版本{entry['version']}），待重新确认正文",
            target_count=1, language=entry['language'], aspect_ratio=payload.aspect_ratio,
            duration_target_seconds=payload.duration_target_seconds,
            knowledge_references=knowledge, source_references=sources,
            selected_voice=None, pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
            material_rule_summary="复用稿尚未选材，确认正文后沿当前任务继续", model_snapshot=[],
            confirmation_points=[], blockers=[], preflight_fingerprint=f"script-library:{entry['id']}",
        )
        if not plan.name:
            raise ScriptDraftRejected(status_code=422, detail="请输入新任务名称")

        def guard(conn):
            current = self.read_reusable_entry(workspace_id, entry_id)
            if current != (entry, knowledge, sources):
                raise ScriptDraftRejected(status_code=409, detail="文案库来源或许可已变化，没有创建新任务")

        return self.draft_use_case.reuse_library_entry(workspace_id, entry_id, payload,
            member_id=member_id, plan=plan, write_guard=guard)
