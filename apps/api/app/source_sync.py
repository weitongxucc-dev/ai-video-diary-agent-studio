"""Source-fetch failure semantics and the repository-backed sync use case."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from app.source_types import SourceItem, SourceRunEvidence

if TYPE_CHECKING:
    from app.source_repository import SourceRepository


class SourceBoundaryError(RuntimeError):
    """A platform boundary failure with a UI/API-safe, actionable semantic."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        impact: str | None = None,
        repair_action: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.impact = impact or "No new items were imported; existing items are unchanged."
        self.repair_action = repair_action or "Review the source configuration before retrying."
        self.retryable = retryable


class SourceSyncFailedError(RuntimeError):
    def __init__(self, run: SourceRunEvidence) -> None:
        super().__init__(run.reason or "source sync failed")
        self.run = run


def source_error_from_http_status(status_code: int, source_name: str) -> SourceBoundaryError:
    if status_code == 401:
        return SourceBoundaryError(
            "authentication_required", f"{source_name} requires login or a valid token",
            repair_action="Sign in again or update the source credential.", retryable=False,
        )
    if status_code == 403:
        return SourceBoundaryError(
            "access_denied", f"{source_name} denied access for the configured account or rule",
            repair_action="Confirm the account has permission for this source.", retryable=False,
        )
    if status_code == 429:
        return SourceBoundaryError(
            "rate_limited", f"{source_name} rate limit was reached",
            repair_action="Wait for the platform limit window, then retry.", retryable=True,
        )
    return SourceBoundaryError(
        "temporary_failure", f"{source_name} request failed with HTTP {status_code}",
        repair_action="Check the source service and retry.", retryable=True,
    )


def sync_source_connector(
    repository: SourceRepository,
    workspace_id: str,
    connector_id: str,
    fetch: Callable[[str | None], tuple[list[SourceItem], str | None]],
) -> SourceRunEvidence:
    """Persist an import attempt and force callers to observe failures."""
    connector = repository.get_connector(workspace_id, connector_id)
    try:
        items, cursor_after = fetch(connector.cursor)
        return repository.record_sync_success(
            workspace_id, connector_id, items, cursor_after
        )
    except SourceBoundaryError as exc:
        run = repository.record_sync_failure(
            workspace_id,
            connector_id,
            reason=str(exc),
            impact=exc.impact,
            repair_action=exc.repair_action,
            retryable=exc.retryable,
            connection_status=exc.kind,
        )
        raise SourceSyncFailedError(run) from exc
    except Exception as exc:
        boundary_error = SourceBoundaryError(
            "temporary_failure",
            f"{connector.platform} sync failed: {exc}",
            repair_action="Check the connector configuration and retry.",
            retryable=True,
        )
        run = repository.record_sync_failure(
            workspace_id,
            connector_id,
            reason=str(boundary_error),
            impact=boundary_error.impact,
            repair_action=boundary_error.repair_action,
            retryable=boundary_error.retryable,
            connection_status=boundary_error.kind,
        )
        raise SourceSyncFailedError(run) from exc
