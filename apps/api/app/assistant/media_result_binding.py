"""Bind reported media identities to original assistant sources and output slots."""
from dataclasses import dataclass
from app.creation.media_result_use_case import MediaResultRejected
from app.graphic_sources import graphic_source_facts
from app.schemas import TaskMediaJobResponse


@dataclass(frozen=True)
class MediaResultSelection:
    job_id: str
    completed_slot: dict | None = None


class AssistantMediaResultBinding:
    def __init__(self, repository, conversations, projection, artifacts, outputs):
        self.repository, self.conversations, self.projection = repository, conversations, projection
        self.artifacts, self.outputs = artifacts, outputs

    def related_job(self, media, workspace_id, task_id, turn_id, conversation_id, reported):
        related = TaskMediaJobResponse.model_validate(reported)
        original, submitted = media.read(workspace_id, related.job_id)
        source = submitted['arguments'].get('_typed_source', {})
        if original.task_id != task_id or source.get('assistant_turn_id') != turn_id or source.get('conversation_id') != conversation_id:
            raise MediaResultRejected(502, "先前媒体回执不属于本轮真实目标")
        return original.id

    def select_finish(self, turn_id, reported):
        candidate = TaskMediaJobResponse.model_validate(reported)
        if (candidate.result or {}).get('reused_completed_source') is True:
            goal = self.conversations.get_task_goal(turn_id)
            slot = next((item for item in (goal or {}).get('completed_source_slots', []) if item['job_id'] == candidate.job_id), None)
            if slot is None:
                raise MediaResultRejected(409, "复用来源不属于当前原目标的已完成输出槽")
            return MediaResultSelection(candidate.job_id, slot)
        return MediaResultSelection(candidate.job_id)

    def completed_receipt(self, task, actor, turn_id, slot, *, contract, input_refs):
        goal = self.conversations.get_task_goal(turn_id)
        bound = self.conversations.completed_goal_source(turn_id, requested_outputs=[contract], input_refs=input_refs)
        version = self.repository.timeline_records.get_timeline_version(task.workspace_id, task.current_timeline_version_id) if task.current_timeline_version_id else None
        if (bound is None or bound["job_id"] != slot["job_id"] or
                (goal["target_snapshot"].get("timeline_version_id"), goal["target_snapshot"].get("timeline_sequence")) != (
                    task.current_timeline_version_id, version.sequence if version else 0)):
            raise MediaResultRejected(409, "已完成来源的原目标版本链已失效")
        try:
            actual = self.projection.script_source_slot(goal, slot["job_id"], actor, self.artifacts, self.outputs)
            if any(actual[key] != slot[key] for key in ("source_ref", "artifact_id", "sha256", "size_bytes", "width", "height", "name", "input_refs")):
                raise ValueError("已完成来源登记事实发生变化")
            if actual["size_bytes"] > contract["max_bytes"] or actual["width"] > contract["width"] or actual["height"] > contract["height"]:
                raise ValueError("已完成来源不符合本次输出尺寸或大小上限")
            for item in input_refs:
                facts = graphic_source_facts(self.repository, self.artifacts, task.workspace_id, task.id, item["source_ref"], actor=actor)
                if facts["artifact_id"] != item["expected_artifact_id"] or facts["sha256"] != item["expected_sha256"]:
                    raise ValueError("原固定输入登记修订已变化")
            job = self.repository.job_records.get_job_run(task.workspace_id, slot["job_id"])
            start = self.repository.execution_records.get_task_event(task.workspace_id, f"event_{job.id}_start").payload
            result = self.repository.execution_records.get_task_event(task.workspace_id, f"event_{job.id}_result").payload["result"]
        except PermissionError as exc:
            raise MediaResultRejected(403, str(exc)) from exc
        except (KeyError, ValueError, OSError) as exc:
            raise MediaResultRejected(409, str(exc)) from exc
        return TaskMediaJobResponse(operation=start["operation"], target_refs=[],
            input_digest=job.input_digest, created_at=job.created_at, job_id=job.id, status=job.status,
            result_kind="graphic_source", result_url=f"/api/creation-tasks/{task.id}/media-jobs/{job.id}",
            base_version_id=start["input_facts"]["base_version_id"], result={**result, "reused_completed_source": True}).model_dump(mode="json")
