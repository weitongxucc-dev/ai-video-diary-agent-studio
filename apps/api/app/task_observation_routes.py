"""Task output observation HTTP; original-request reconciliation and scheduling keep their existing owners."""
import json

from fastapi import APIRouter, HTTPException, Request
from app.schemas import CreationTaskObservationRequest, CreationTaskObservationTarget
from app.creation_execution import CreationExecutionActor
from app.creation.quality_projection import bounded_quality_evidence, bounded_quality_findings
from app.task_audio import TASK_MEDIA_KIND

class TaskObservationResponseAdapter:
    def __init__(self, *, repo_for, quality_response, registered_output, quality_history):
        self.repo_for = repo_for
        self.quality_response = quality_response
        self.registered_output = registered_output
        self.quality_history = quality_history

    def metadata(self, task,member_id):
        workspace_id,task_id=task.workspace_id,task.id
        current = self.quality_response(workspace_id, task_id).model_dump(mode="json")
        current.update(bounded_quality_findings(current))
        rows = self.repo_for().execution_records.list_observation_job_starts(workspace_id, task_id, TASK_MEDIA_KIND, member_id)
        observations = []
        for row in rows:
            job = self.repo_for().job_records.get_job_run(workspace_id, row["id"])
            start = json.loads(row["payload_json"])
            result = None
            if job.status == "succeeded":
                result = self.repo_for().execution_records.get_task_event(workspace_id, f"event_{job.id}_result").payload["result"]
            stale = start["input_facts"]["base_version_id"] != task.current_timeline_version_id
            if not stale:
                try:
                    original = CreationTaskObservationRequest.model_validate(start["arguments"]["payload"])
                    _path, output, _binding = self.registered_output(workspace_id, task_id, original.target,
                        CreationExecutionActor.from_event(workspace_id, start))
                    stale = output != start["input_facts"]["output"]
                except (KeyError, OSError, ValueError, PermissionError, HTTPException):
                    stale = True
            observations.append({"job_id": job.id, "status": job.status,
                "stale": stale,
                "timeline_version_id": start["input_facts"]["base_version_id"], "result": result,
                "result_url": f"/api/creation-tasks/{task_id}/media-jobs/{job.id}"})
        targets = []
        try:
            _path, output, _binding = self.registered_output(workspace_id, task_id,
                CreationTaskObservationTarget(kind="final"), None)
        except (KeyError, OSError, ValueError, PermissionError, HTTPException):
            pass  # Missing/changed final is not an available observation target.
        else:
            targets.append({"target": {"kind": "final"}, "artifact_id": output["artifact_id"], "sha256": output["sha256"],
                            "output_role": "composed_output"})
        rows = self.repo_for().execution_records.list_observation_target_artifacts(
            workspace_id, task_id, TASK_MEDIA_KIND, member_id, task.current_timeline_version_id)
        for row in rows:
            if row["logical_ref"].endswith(".mp4") or row["kind"] == "graphic_source" and row["logical_ref"].endswith(".png"):
                targets.append({"target": {"kind": "media_job", "job_id": row["producer_job_id"], "artifact_id": row["id"]},
                    "artifact_id": row["id"], "sha256": row["sha256"],
                    "output_role": "graphic_source" if row["kind"] == "graphic_source" else "composed_preview"})
        version = self.repo_for().timeline_records.get_timeline_version(workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        return {"task_id": task_id, "timeline_version_id": task.current_timeline_version_id,
            "timeline_sequence": version.sequence if version else 0,
            "quality_check": current, "quality_history": [bounded_quality_evidence(item)
                for item in self.quality_history(workspace_id, task_id)],
            "observations": observations, "targets": targets, "targets_limit": 11,
            "media_policy": "local_measurements_and_text_only"}


def build_task_observation_router(*, access_for, metadata_for, actor_for, case_for, json_response_for, prepare_for, runtime_for, receipt_for, output_error_for, http_error_for, route_names):
    router = APIRouter()

    def _task_observation_facts(task_id, request):
        task, member_id = access_for()(task_id, request)
        return metadata_for()(task, member_id)

    @router.get('/api/creation-tasks/{task_id}/observations')
    def get_creation_task_observations(task_id: str, request: Request):
        return _task_observation_facts(task_id, request)

    @router.post('/api/creation-tasks/{task_id}/observations')
    def observe_creation_task_output(task_id: str, request: Request, payload: CreationTaskObservationRequest):
        task, _member = access_for()(task_id, request, write=True)
        actor = actor_for()(request, task.workspace_id)
        try:
            previous = case_for()().original_request(task, actor, payload)
            if previous is not None:
                return json_response_for()(status_code=202, content=previous)
            source = prepare_for()(task_id, request, payload)
            runtime = runtime_for()()
            receipt = case_for()().submit(runtime, runtime.registrations['creation_job'].owner.task_media, task, actor, payload, source, receipt_for=receipt_for())
        except output_error_for() as exc:
            raise http_error_for()(status_code=exc.status_code, detail=exc.detail) from exc
        except PermissionError as exc:
            raise http_error_for()(status_code=403, detail=str(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise http_error_for()(status_code=422, detail=str(exc)) from exc
        return json_response_for()(status_code=202, content=receipt)
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
