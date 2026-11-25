"""Bind a task's MP4 cut to a specific completed preparation and source."""
from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from app.device_resource_profile import DeviceProfileUnavailable
from app.material_probe import MaterialProbeFacts, material_probe_unit_key

from .adapter import FfmpegAdapter, is_bound_video_cut
from .contracts import MediaExecutionError
from .cut_measurement import Mp4CutLimits, measure_mp4_cut


BOUND_CUT_KIND = "task_video_cut"
BOUND_CUT_FACT = "bounded_mp4_cut"


class BoundMp4CutUseCase:
    def __init__(self, repository, preparation, task_authorizations):
        self.repository = repository
        self.preparation = preparation
        self.task_authorizations = task_authorizations

    def seal(self, plan, actor, preparation_request_id: str) -> dict:
        """Read current facts only. Admission and its resource caps belong to the host."""
        if ((plan.workspace_id, plan.member_id) != (actor.workspace_id, actor.member_id)
                or len(plan.operations) != 1 or not is_bound_video_cut(plan.operations[0])
                or len(plan.operations[0].inputs) != 1):
            raise PermissionError("截取必须绑定同一工作区、成员和单份正式素材。")
        operation = plan.operations[0]
        asset_id = operation.options["asset_id"]
        if self.task_authorizations.verdict(
                actor, plan.task_id, "task_media", references={f"asset:{asset_id}"}) != "ready":
            raise PermissionError("当前任务素材许可无效。")
        try:
            evidence = self.preparation.read_mp4_cut_measurement(
                database_path=self.repository.database_path, request_id=preparation_request_id, actor=actor)
        except (FileNotFoundError, KeyError, DeviceProfileUnavailable) as exc:
            raise ValueError("指定的 MP4 截取准备回执缺失、失效或工具已变化。") from exc
        cut = evidence["measurement"]
        sample = cut["source"]
        start, duration = operation.options["start"], operation.options["duration"]
        if (not math.isfinite(start) or not math.isfinite(duration) or start < 0
                or not 0 < duration <= cut["output"]["duration_seconds"]):
            raise ValueError("截取区间超出指定回执已测的输出时长。")
        source = self._source(actor, plan, asset_id)
        if (source["size_bytes"] > sample["bytes"] or source["seconds"] > sample["video_seconds"]
                or (source["width"], source["height"], source["frame_rate"])
                != (sample["width"], sample["height"], evidence["frame_rate"])
                or start + duration > source["seconds"] + 1e-6):
            raise ValueError("整源或截取区间不在指定准备回执已测的媒体形状内。")
        limits = Mp4CutLimits(**cut["limits"])
        return {
            "preparation": {key: evidence[key] for key in ("request_id", "generation", "checkpoint")},
            "tools": cut["tools"],
            "cpu_policy": cut["cpu_policy"],
            "source": source,
            "limits": vars(replace(limits, max_source_bytes=sample["bytes"], max_packets=sample["packets"],
                                   max_source_seconds=source["seconds"])),
        }

    def _source(self, actor, plan, asset_id):
        from app.task_media_submission_use_case import read_bound_cut_asset

        with self.repository.connection_factory() as conn:
            conn.execute("BEGIN")
            asset = read_bound_cut_asset(self.repository, actor, asset_id, connection=conn)
            if asset.analysis_status != "ready":
                raise ValueError("截取素材尚未完成分析。")
            reference = self.repository.workspace_media_reference_for_asset(actor.workspace_id, asset_id, connection=conn)
            path = self.repository.resolve_clip_media_reference(actor.workspace_id, asset_id, reference, connection=conn)
            if (list(plan.metadata.get("source_references", ())) != [reference]
                    or plan.operations[0].inputs != (str(path),)):
                raise ValueError("截取路径与当前正式素材引用不一致。")
            current = conn.execute(
                """SELECT attempted_digest, provisional_revision, size_bytes
                   FROM material_ingestion_files WHERE workspace_id=? AND file_path=?""",
                (actor.workspace_id, str(path)),
            ).fetchone()
            if current is None or not current["attempted_digest"]:
                raise ValueError("截取素材缺少整源哈希。")
            digest = current["attempted_digest"]
            keys = (material_probe_unit_key(digest, version=2), material_probe_unit_key(digest))
            probe = conn.execute(
                """SELECT unit_key,stage_input_digest,result_json,result_ref FROM material_analysis_checkpoints
                   WHERE workspace_id=? AND asset_id=? AND provisional_revision=? AND source_sha256=?
                     AND unit_key IN (?, ?) ORDER BY unit_key DESC LIMIT 1""",
                (actor.workspace_id, asset_id, current["provisional_revision"], digest, *keys),
            ).fetchone()
            if (probe is None or not isinstance(probe["result_json"], str)
                    or self.repository.probe_records.material_probe_source_verdict(
                        actor.workspace_id, asset_id, digest, probe["unit_key"], conn) != "ready"
                    or probe["stage_input_digest"] != probe["unit_key"].rsplit(":", 1)[-1]
                    or probe["result_ref"] != "sha256:" + hashlib.sha256(probe["result_json"].encode()).hexdigest()
                    or current["size_bytes"] != asset.size_bytes):
                raise ValueError("截取素材的原哈希或探测检查点已失效。")
            profile = MaterialProbeFacts.from_checkpoint_json(probe["result_json"])
        if ("mp4" not in profile.format_name.split(",")
                or [(item.kind, item.codec) for item in profile.streams] != [("video", "h264"), ("audio", "aac")]
                or profile.duration_seconds != asset.duration_seconds):
            raise ValueError("截取素材缺少可核验的 H.264/AAC MP4 整源形状。")
        video, audio = profile.streams
        if (video.avg_frame_rate_fps is None or video.avg_frame_rate_fps != video.r_frame_rate_fps
                or audio.sample_rate != 48000):
            raise ValueError("截取素材的原视频帧率或音频采样率超出测量形状。")
        return {"asset_id": asset_id, "reference": reference, "sha256": digest, "size_bytes": current["size_bytes"],
                "probe_checkpoint": probe["result_ref"], "revision": current["provisional_revision"],
                "seconds": profile.duration_seconds, "width": video.width, "height": video.height,
                "frame_rate": video.avg_frame_rate_fps}

    def bind_grant(self, plan, actor, job, event, context):
        facts = event["input_facts"][BOUND_CUT_FACT]
        grant = context.grant
        if ((grant.owner_type, grant.owner_id, grant.workspace_id, grant.input_digest)
                != ("creation_job", job.id, job.workspace_id, job.input_digest)
                or event["input_digest"] != job.input_digest or context.threads != 1
                or event["resources"]["max_output_bytes"] != context.max_output_bytes):
            raise PermissionError("有界截取没有对应原作业的设备许可。")

        def recheck():
            current = self.seal(plan, actor, facts["preparation"]["request_id"])
            if current != facts:
                raise MediaExecutionError("media_cut_binding_changed", "截取依赖已变化，未发布。",
                                          "重新核对原素材和指定准备回执。")

        context.check()
        recheck()
        limits = context.scheduler.resources.scene_runtime_limits(grant)
        if limits is None or limits.cpu_policy is None or asdict(limits.cpu_policy) != facts["cpu_policy"]:
            raise ValueError("获许可的 CPU 运行策略与截取准备回执不一致。")
        work_limit = min(context.max_output_bytes, facts["limits"]["max_work_bytes"])
        if work_limit <= facts["source"]["size_bytes"]:
            raise ValueError("获许可的临时空间不足以容纳截取源副本及成片。")
        limits = replace(Mp4CutLimits(**facts["limits"]), max_work_bytes=work_limit,
                         max_source_bytes=min(facts["limits"]["max_source_bytes"], work_limit - 1))
        return GrantedMp4Cut(facts, limits, context, recheck)


@dataclass(frozen=True)
class GrantedMp4Cut:
    facts: dict
    limits: Mp4CutLimits
    context: object
    recheck: Callable[[], None]

    def execute(self, operation, source: Path, output: Path):
        adapter = FfmpegAdapter(*(self.facts["tools"][name]["path"] for name in ("ffmpeg", "ffprobe")))
        def run(argv, paths, *, env):
            return self.context.run(argv, env=env, output_paths=paths, output_bytes=self.limits.max_work_bytes)
        binding = self.facts["source"]
        return measure_mp4_cut(adapter, source, output.parent, source_sha256=binding["sha256"],
            source_bytes=binding["size_bytes"], start=operation.options["start"],
            duration=operation.options["duration"], limits=self.limits, run=run, check=self.context.check,
            staged_output=output)
