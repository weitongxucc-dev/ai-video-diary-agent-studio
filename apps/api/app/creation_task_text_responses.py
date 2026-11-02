"""Topic and script read response encoding with current host route snapshots."""
from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from app.creation.projection import project_task_summary
from app.schemas import CreationTaskTopicsResponse, CreationTaskScriptsResponse

if TYPE_CHECKING:
    from app.creation.contracts import CreationTask
    from app.creation.source_errors import SourcePreflightError as _CreationTaskTopicPreflightError
    from app.creation.topic_contracts import TopicGenerationRun


class CreationTaskTextResponseAdapter:
    def __init__(self, *, repository_for, topic_route_snapshot):
        self.repository_for = repository_for
        self.topic_route_snapshot = topic_route_snapshot

    def projected_topic_route_snapshot(
        self,
        current_snapshot: dict[str, object],
        latest_run: TopicGenerationRun | None,
    ) -> dict[str, object]:
        if latest_run is None:
            return current_snapshot
        previous = latest_run.route_snapshot
        if previous.get("connection_version") != current_snapshot["connection_version"]:
            return current_snapshot
        availability = previous.get("availability")
        if availability not in {"succeeded", "failed"}:
            return current_snapshot
        return {
            **current_snapshot,
            "availability": availability,
            "checked_at": previous.get("checked_at", current_snapshot["checked_at"]),
        }

    def creation_task_topic_response(
        self,
        workspace_id: str,
        task: CreationTask,
        *,
        route_snapshot: dict[str, object] | None = None,
        preflight_error: _CreationTaskTopicPreflightError | None = None,
        preflight_input_count: int = 0,
    ) -> CreationTaskTopicsResponse:
        route_snapshot = route_snapshot or self.topic_route_snapshot(workspace_id)
        latest_run = self.repository_for().topic_records.latest_topic_generation_run(workspace_id, task.id)
        route_snapshot = self.projected_topic_route_snapshot(route_snapshot, latest_run)
        candidates = self.repository_for().topic_records.list_topic_candidates(workspace_id, task.id)
        evidence_references = self.repository_for().topic_records.list_topic_evidence_references(workspace_id, task.id)
        cloud_egress = route_snapshot["execution_location"] == "cloud"
        return CreationTaskTopicsResponse.model_validate(
            {
                "task": project_task_summary(self.repository_for(), task).summary,
                "candidates": [
                    {
                        "id": candidate.id,
                        "run_id": candidate.run_id,
                        "title": candidate.title,
                        "angle": candidate.angle,
                        "audience": candidate.audience,
                        "outline": candidate.outline,
                        "score": candidate.score,
                        "selection_status": candidate.selection_status,
                        "created_at": candidate.created_at,
                    }
                    for candidate in candidates
                ],
                "evidence_references": [
                    {
                        "id": reference.id,
                        "candidate_id": reference.candidate_id,
                        "kind": reference.kind,
                        "reference_id": reference.reference_id,
                        "version_or_identity": reference.version_or_identity,
                        "title": reference.title,
                        "authorization_or_license": reference.authorization_or_license,
                        "source_url": reference.source_url,
                        "created_at": reference.created_at,
                    }
                    for reference in evidence_references
                ],
                "selected_topic_id": task.selected_topic_id,
                "latest_run": latest_run.__dict__ if latest_run is not None else None,
                "route_preflight": route_snapshot,
                "egress_preflight": {
                    "cloud_egress": cloud_egress,
                    "source_count": len(latest_run.input_references)
                    if latest_run is not None
                    else preflight_input_count,
                    "text_excerpt_count": len(latest_run.input_references)
                    if latest_run is not None
                    else preflight_input_count,
                    "reason": preflight_error.reason if preflight_error else None,
                    "impact": preflight_error.impact if preflight_error else None,
                    "repair_action": preflight_error.repair_action if preflight_error else None,
                },
            }
        )

    def creation_task_scripts_response(
        self,
        workspace_id: str,
        task: CreationTask,
        *, include_body: bool = True,
    ) -> CreationTaskScriptsResponse:
        latest_run = self.repository_for().script_records.latest_script_generation_run(workspace_id, task.id)
        if latest_run is not None and latest_run.run_kind != "generation":
            latest_run = None
        route_snapshot = self.projected_topic_route_snapshot(self.topic_route_snapshot(workspace_id), latest_run)
        return CreationTaskScriptsResponse.model_validate(
            {
                "task": project_task_summary(self.repository_for(), task).summary,
                "scripts": ([asdict(script) for script in self.repository_for().script_records.list_creation_task_scripts(workspace_id, task.id)]
                            if include_body else self.repository_for().script_records.active_creation_task_script_headers(workspace_id, task.id)),
                "scripts_scope": "history" if include_body else "active",
                "latest_run": latest_run.__dict__ if latest_run is not None else None,
                "route_preflight": route_snapshot,
            }
        )
