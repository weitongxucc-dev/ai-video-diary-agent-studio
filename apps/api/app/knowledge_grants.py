"""Knowledge-only read/reference grants; identity and workspace resolution stay upstream."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from app.repository import LEGACY_WORKSPACE_ID
from app.source_storage_schema import KNOWLEDGE_GRANTS_DDL

if TYPE_CHECKING:
    from fastapi import Request
    from app.source_repository import SourceRepository
    from app.source_types import KnowledgeGrant


@dataclass(frozen=True)
class KnowledgeGrantTarget:
    workspace_id: str
    name: str


@dataclass(frozen=True)
class GrantTargetResolution:
    status: Literal["resolved", "same_workspace", "unresolved"]
    message: str
    targets: tuple[KnowledgeGrantTarget, ...] = ()


class GrantTargetResolver(Protocol):
    """The sole injection boundary for authoritative same-device targets."""

    def resolve(self, request: Request, *, workspace_id: str, user_id: str) -> GrantTargetResolution: ...


class VerifiedMemberGrantTargetResolver:
    """Production resolution without inventing missing membership facts.

    LocalMember's verified identity belongs only to the reserved local space.
    The existing device-account RPCs do not provide a target-membership read
    adapter, and that remote path is not activated. No knowledge rows, renderer
    targets or device-owner declarations can supply that missing relationship.
    """

    def __init__(self, *, local_mode: bool) -> None:
        self.local_mode = local_mode

    def resolve(self, request: Request, *, workspace_id: str, user_id: str) -> GrantTargetResolution:
        if self.local_mode and workspace_id == LEGACY_WORKSPACE_ID:
            return GrantTargetResolution(
                status="same_workspace",
                message="本地成员仅同空间；跨空间授权不成立，真实跨空间验收未验证。",
            )
        return GrantTargetResolution(
            status="unresolved",
            message="已验证的同设备成员与空间关系事实源不可用；跨空间授权未验证。",
        )


def grant_to_verified_target(
    repository: SourceRepository,
    *,
    source_workspace_id: str,
    item_id: str,
    target_workspace_id: str,
    user_id: str,
    targets: list[KnowledgeGrantTarget],
) -> KnowledgeGrant:
    """Targets must come from server-side same-device membership resolution."""
    if target_workspace_id not in {target.workspace_id for target in targets}:
        raise KeyError(target_workspace_id)
    return repository.grant_knowledge_item(
        source_workspace_id, item_id, target_workspace_id, user_id=user_id
    )
