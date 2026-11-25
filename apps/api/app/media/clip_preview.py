"""Execute sealed short previews with the original actor and device grant."""
from dataclasses import asdict
from pathlib import Path

from app.creation.clip_preview import CANDIDATE_CLIP_PREVIEW, CandidateClipPreviewCommand, ClipPreviewCommand
from app.montage import MontageClip, render_granted_clip_preview


def render_clip_input(media_input, output, context):
    return render_granted_clip_preview(MontageClip(**media_input["clip"]), Path(media_input["source_path"]), output,
        context, aspect_ratio=media_input["aspect_ratio"], max_seconds=media_input["max_seconds"])


class ClipPreviewExecutor:
    def __init__(self, use_case, publisher):
        self.use_case = use_case
        self.publisher = publisher

    def execute(self, job, event, actor, context):
        from app.creation_execution import CreationExecutionActor
        from app.task_audio import media_digest
        context.check()
        if ((context.grant.workspace_id, context.grant.owner_type, context.grant.owner_id,
             context.grant.input_digest, context.grant.unit_key) !=
            (job.workspace_id, "creation_job", job.id, job.input_digest, f"task_media:{job.input_digest}")
                or actor != CreationExecutionActor.from_event(job.workspace_id, event)):
            raise PermissionError("镜头预览身份或许可不属于原作业")
        if (event["input_digest"] != job.input_digest or
                media_digest({key:value for key,value in event.items() if key != "input_digest"}) != job.input_digest):
            raise ValueError("镜头预览输入摘要不属于原作业")
        candidate = event["operation"] == CANDIDATE_CLIP_PREVIEW
        if candidate:
            command = CandidateClipPreviewCommand(**event["arguments"]["candidate"])
            facts, resources = self.use_case.seal_candidate(actor, job.task_id, command)
            kind = CANDIDATE_CLIP_PREVIEW
        else:
            command = ClipPreviewCommand(**event["arguments"]["_clip_preview"])
            if command.task_id != job.task_id:
                raise PermissionError("镜头预览输入不属于原作业任务")
            facts, resources = self.use_case.seal(actor, command)
            kind = "speed_preview" if command.operation_type == "ChangeClipSpeed" else "color_preview"
        if facts != event["input_facts"] or resources != event["resources"]:
            raise ValueError("镜头预览封存输入或资源声明已变化")
        records = []
        for side, clip in (("before", facts["clip"]), ("after", facts["candidates"][0])):
            context.check()
            version_id = facts["formal_base_version_id"] if candidate and side == "before" else facts["base_version_id"]
            parameters = {"base_version_id": version_id, "side": side,
                          "clip_id": clip["id"], "max_seconds": 5, "silent": True}
            if candidate:
                parameters.update(candidate_id=command.candidate_id, formal_base_version_id=facts["formal_base_version_id"])
            records.append(self.publisher.publish(job.workspace_id, job.task_id, kind=kind, suffix=".mp4",
                engine="local-clip-preview-v1", parameters=parameters,
                input_refs=[f"timeline:{version_id}", clip["source_path"]], operation="clip_preview",
                media_input={"clip":clip, "source_path":facts["source_path"], "aspect_ratio":facts["aspect_ratio"], "max_seconds":5},
                execution=(context, job, event, actor)))
        context.check()
        if candidate:
            return {"baseline_artifact_id": records[0].id, "candidate_artifact_id": records[1].id}
        intent, proposal = self.use_case.proposal(actor, command, records=records, job=job)
        return {"intent":asdict(intent), "proposal":asdict(proposal)}

    def validate_result(self, job, event, result, context):
        if event["operation"] == CANDIDATE_CLIP_PREVIEW:
            records = self.use_case.repository.execution_records.list_artifact_records(job.workspace_id, job.task_id)
            ids = {record.id for record in records
                   if record.producer_job_id == job.id and record.kind == CANDIDATE_CLIP_PREVIEW}
            if len(ids) != 2 or ids != {result["baseline_artifact_id"], result["candidate_artifact_id"]}:
                raise ValueError("候选短预览缺少原作业登记的两份画面")
        return self.publisher.validate_publications(job, event, result, context)
