"""Append authorized fixed knowledge versions to an existing task's Plan."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from app.source_policy import content_allows_use

if TYPE_CHECKING:
    from app.production import ProductionRepository
    from app.source_repository import SourceRepository


@dataclass(frozen=True)
class KnowledgeAdoptionCommand:
    request_id: str
    expected_plan_updated_at: datetime
    expected_task_updated_at: datetime
    references: tuple[tuple[str, str], ...]


class TaskKnowledgeAdoptionUseCase:
    def __init__(self, production: ProductionRepository, sources: SourceRepository):
        self.production, self.sources = production, sources

    def adopt(self, workspace_id: str, task_id: str, member_id: str, command: KnowledgeAdoptionCommand) -> dict[str, object]:
        from app.creation.plan_contracts import CreationPlanKnowledgeReference

        if not 1 <= len(command.references) <= 64 or len(set(command.references)) != len(command.references):
            raise ValueError("一次采用需包含1至64份不同的固定资料版本。")
        if any(not identity.strip() or not version.strip() for identity, version in command.references):
            raise ValueError("资料采用必须包含实际ID和固定版本。")

        def resolve() -> list[CreationPlanKnowledgeReference]:
            # The production owner holds its write transaction while these
            # public reads recheck current grants in the shared application DB.
            references = []
            for identity, version in command.references:
                item = self.sources.get_knowledge_reference_readable_by(workspace_id, identity, version, user_id=member_id)
                if not content_allows_use(item.use_state, "reference") or item.use_state == "not_for_automation":
                    raise ValueError("所选资料当前不允许引用，没有追加到任务。")
                references.append(CreationPlanKnowledgeReference(item.id, item.version, item.title, item.authorization, item.ownership))
            return references

        return self.production.adopt_task_knowledge(workspace_id, task_id, member_id=member_id,
            request_id=command.request_id, expected_plan_updated_at=command.expected_plan_updated_at,
            expected_task_updated_at=command.expected_task_updated_at, requested_references=command.references,
            resolve_references=resolve)
