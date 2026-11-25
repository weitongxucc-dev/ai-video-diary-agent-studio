"""Bind a task's MP4 cut to a specific completed preparation and source."""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from app.device_resource_profile import DeviceProfileUnavailable
from app.material_probe import MaterialProbeFacts, material_probe_unit_key
from app.quality_worker_runtime import budget_identity

from .adapter import FfmpegAdapter, is_bound_video_cut
from .contracts import MediaExecutionError
from .cut_disk import CutDiskProgress
from .cut_measurement import Mp4CutLimits, copy_verified_cut_output, measure_mp4_cut


BOUND_CUT_KIND = "task_video_cut"
BOUND_CUT_FACT = "bounded_mp4_cut"


def bound_cut_resource_refusal(resources, budget):
    """Apply the current CPU envelope to all passes of one sealed cut."""
    if budget is None or budget.running_cpu_policy is None:
        return "cut_cpu_budget_unavailable"
    if (resources.get("execution_mode") != "cpu_ffmpeg"
            or resources.get("cpu_policy") != asdict(budget.running_cpu_policy)
            or resources.get("budget_digest") != budget_identity(budget)):
        return "cut_cpu_budget_changed"
    for name in ("source_seconds", "duration_seconds", "timeout_seconds"):
        value = resources.get(name)
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
            return "cut_resource_fields_invalid"
    for name in ("width_px", "height_px", "threads", "input_bytes", "work_bytes", "max_output_bytes"):
        value = resources.get(name)
        if type(value) is not int or value <= 0:
            return "cut_resource_fields_invalid"
    checkpoint = resources.get("preparation_checkpoint")
    if (not isinstance(checkpoint, str) or not re.fullmatch(r"device-resource-preparation:[0-9a-f]{64}", checkpoint)
            or resources["threads"] != 1
            or resources["work_bytes"] != resources["input_bytes"] + resources["max_output_bytes"]
            or resources["duration_seconds"] > resources["source_seconds"]):
        return "cut_resource_fields_invalid"
    # Charge both passes over the source, the encode and the output verification.
    # This is scope accounting. The command runner still enforces the deadline
    # and observes the process against the configured RSS and thread thresholds.
    pixel_seconds = (resources["width_px"] * resources["height_px"]
                     * (2 * resources["source_seconds"] + 2 * resources["duration_seconds"]))
    if (resources["source_seconds"] > budget.max_duration_seconds
            or resources["width_px"] > budget.max_width_px or resources["height_px"] > budget.max_height_px
            or pixel_seconds > budget.max_pixel_seconds or resources["input_bytes"] > budget.max_input_bytes
            or resources["threads"] > budget.max_threads or resources["timeout_seconds"] > budget.max_timeout_seconds
            or resources["work_bytes"] > budget.additional_disk_bytes
            or resources["max_output_bytes"] > budget.max_output_bytes):
        return "declared_resources_exceed_budget"
    return None


class BoundMp4CutUseCase:
    def __init__(self, repository, preparation, task_authorizations):
        self.repository = repository
        self.preparation = preparation
        self.task_authorizations = task_authorizations

    def seal(self, plan, actor, preparation_request_id: str, *, budget) -> tuple[dict, dict]:
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
            preparation = self.preparation()
            evidence = preparation.read_mp4_cut_measurement(
                database_path=self.repository.database_path, request_id=preparation_request_id, actor=actor)
        except (FileNotFoundError, KeyError, DeviceProfileUnavailable) as exc:
            raise ValueError("指定的 MP4 截取准备回执缺失、失效或工具已变化。") from exc
        cut = evidence["measurement"]
        if budget is None or budget.running_cpu_policy is None:
            raise ValueError("有界截取缺少当前 CPU 进程和磁盘预算。")
        if asdict(budget.running_cpu_policy) != cut["cpu_policy"]:
            raise ValueError("当前 CPU 策略与指定截取准备回执不一致。")
        if (cut["wall_seconds"] >= budget.max_timeout_seconds
                or any(stage["resources"]["peak_rss_bytes"] > budget.additional_memory_bytes
                       or stage["resources"]["peak_threads"] > budget.max_process_threads
                       for stage in cut["stages"].values())):
            raise ValueError("指定截取测量已超过当前进程或总时限额度。")
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
        source_bytes = source["size_bytes"]
        output_bytes = min(budget.max_output_bytes, budget.additional_disk_bytes - source_bytes,
                           limits.max_work_bytes - source_bytes)
        if output_bytes <= 0:
            raise ValueError("现有磁盘额度无法同时容纳截取源副本和成片。")
        work_bytes = source_bytes + output_bytes
        facts = {
            "preparation": {key: evidence[key] for key in ("request_id", "generation", "checkpoint")},
            "tools": cut["tools"],
            "cpu_policy": cut["cpu_policy"],
            "source": source,
            "limits": vars(replace(limits, max_source_bytes=source_bytes, max_packets=sample["packets"],
                                   max_source_seconds=source["seconds"], max_work_bytes=work_bytes)),
        }
        resources = {
            "kind": BOUND_CUT_KIND, "execution_mode": "cpu_ffmpeg", "threads": 1,
            "budget_digest": budget_identity(budget), "cpu_policy": cut["cpu_policy"],
            "preparation_checkpoint": evidence["checkpoint"], "source_seconds": source["seconds"],
            "width_px": source["width"], "height_px": source["height"], "duration_seconds": duration,
            "input_bytes": source_bytes, "work_bytes": work_bytes, "max_output_bytes": output_bytes,
            "timeout_seconds": budget.max_timeout_seconds,
            "output_root": str(Path(preparation.write_paths["output"]).resolve()),
        }
        return facts, resources

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
        resources = event["resources"]
        grant = context.grant
        if ((grant.owner_type, grant.owner_id, grant.workspace_id, grant.input_digest)
                != ("creation_job", job.id, job.workspace_id, job.input_digest)
                or event["input_digest"] != job.input_digest or context.threads != 1
                or resources["max_output_bytes"] != context.max_output_bytes):
            raise PermissionError("有界截取没有对应原作业的设备许可。")

        def recheck():
            current = self.seal(plan, actor, facts["preparation"]["request_id"],
                                budget=context.scheduler.resources.budget.cpu_render)
            if current != (facts, resources):
                raise MediaExecutionError("media_cut_binding_changed", "截取依赖已变化，未发布。",
                                          "重新核对原素材和指定准备回执。")

        context.check()
        recheck()
        limits = context.scheduler.resources.scene_runtime_limits(grant)
        if limits is None or limits.cpu_policy is None or asdict(limits.cpu_policy) != facts["cpu_policy"]:
            raise ValueError("获许可的 CPU 运行策略与截取准备回执不一致。")
        progress = context.scheduler.resources.track_cut_disk(grant)
        return GrantedMp4Cut(facts, Mp4CutLimits(**facts["limits"]), Path(resources["output_root"]),
                             context, recheck, progress)


@dataclass(frozen=True)
class GrantedMp4Cut:
    facts: dict
    limits: Mp4CutLimits
    output_root: Path
    context: object
    recheck: Callable[[], None]
    disk_progress: CutDiskProgress

    def execute(self, operation, source: Path, output: Path):
        if not output.resolve().is_relative_to(self.output_root):
            raise ValueError("截取暂存路径不在获许可的产物卷。")
        adapter = FfmpegAdapter(*(self.facts["tools"][name]["path"] for name in ("ffmpeg", "ffprobe")))
        def run(argv, paths, *, env):
            return self.context.run(argv, env=env, output_paths=paths, output_bytes=self.limits.max_work_bytes)
        binding = self.facts["source"]
        measured = measure_mp4_cut(adapter, source, output.parent, source_sha256=binding["sha256"],
            source_bytes=binding["size_bytes"], start=operation.options["start"],
            duration=operation.options["duration"], limits=self.limits, run=run, check=self.context.check,
            staged_output=output, disk_progress=self.disk_progress)
        self.disk_progress.verified(output, measured["output"]["bytes"])
        return measured

    def publish(self, store, logical_ref, path, summary):
        if store.storage_root != self.output_root:
            raise ValueError("截取发布路径与获许可的产物卷不一致。")
        temporary = path.with_name(f".{path.name}.publish")

        def check():
            self.context.check_execution_limits()
            self.context.scheduler.resources.check_cut_publication_disk(self.context.grant)

        def write(target):
            self.disk_progress.bind("publication", temporary, target)
            copy_verified_cut_output(path, target, expected_bytes=summary["bytes"],
                                     expected_sha256=summary["sha256"], check=check)

        published = store.publish(logical_ref, write, temporary_path=temporary, check=check)
        self.disk_progress.published()
        return published
