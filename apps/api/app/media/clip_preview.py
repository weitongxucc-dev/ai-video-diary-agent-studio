"""Execute sealed short previews with the original actor and device grant."""
from dataclasses import asdict
from pathlib import Path

from app.creation.clip_preview import ClipPreviewCommand
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
        command = ClipPreviewCommand(**event["arguments"]["_clip_preview"])
        if command.task_id != job.task_id:
            raise PermissionError("镜头预览输入不属于原作业任务")
        facts, resources = self.use_case.seal(actor, command)
        if facts != event["input_facts"] or resources != event["resources"]:
            raise ValueError("镜头预览封存输入或资源声明已变化")
        kind = "speed_preview" if command.operation_type == "ChangeClipSpeed" else "color_preview"
        records = []
        for side, clip in (("before", facts["clip"]), ("after", facts["candidates"][0])):
            context.check()
            records.append(self.publisher.publish(job.workspace_id, job.task_id, kind=kind, suffix=".mp4",
                engine="local-clip-preview-v1", parameters={"base_version_id":command.base_version_id,
                    "side":side, "clip_id":clip["id"], "max_seconds":5, "silent":True},
                input_refs=[f"timeline:{command.base_version_id}", clip["source_path"]], operation="clip_preview",
                media_input={"clip":clip, "source_path":facts["source_path"], "aspect_ratio":facts["aspect_ratio"], "max_seconds":5},
                execution=(context, job, event, actor)))
        context.check()
        intent, proposal = self.use_case.proposal(actor, command, records=records, job=job)
        return {"intent":asdict(intent), "proposal":asdict(proposal)}

    def validate_result(self, job, event, result, context):
        return self.publisher.validate_publications(job, event, result, context)
