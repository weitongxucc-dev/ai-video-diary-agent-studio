"""Measure real LatentSync windows through the existing device preparation owner."""

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time

import psutil
import shutil

from app.avatar_lipsync import AvatarAndLipSyncRequest, LatentSync16Provider, PersonProfile, VoiceProfile
from app.creation_execution import CreationExecutionActor, CreationStageGrantContext
from app.device_resource_configuration import _hash_file, backend_identity
from app.device_resource_preparation import DeviceResourcePreparation, OWNER_TYPE, _atomic_json, _profile_lock, _profile_state, _read
from app.device_resource_profile import DeviceProfileUnavailable, _digest, host_budget_from_payload, load_device_resource_profile
from app.device_resource_volumes import sample_write_volumes
from app.device_scheduler import SchedulePolicy, SubmissionIntent, VerifiedWork
from app.granted_subprocess import GrantedCommandError
from app.host_memory import sample_host_memory
from app.host_resource_probes import PresenterBudget, presenter_scope_fields
from app.lipsync_models import LATENTSYNC_16_MODEL_ID
from model_workers.latentsync_16_mlx import CALIBRATION_FILES, FRAME_RATE, RUNTIME_WEIGHTS, SAMPLE_RATE, UNET_FILE, WINDOW_FRAMES

UNIT_KEY = "presenter-prepare:v1"
RECEIPT_PREFIX = "presenter-resource-preparation:"
WIDTH, HEIGHT = 1080, 1920
DURATION = WINDOW_FRAMES / FRAME_RATE
MAX_DURATION = 20 * DURATION


@dataclass(frozen=True)
class PresenterPreparationShape:
    model_id: str = LATENTSYNC_16_MODEL_ID
    width_px: int = WIDTH
    height_px: int = HEIGHT
    duration_seconds: float = DURATION

    def __post_init__(self):
        if (type(self.width_px) is not int or type(self.height_px) is not int
                or type(self.duration_seconds) not in (int, float)
                or (self.model_id, self.width_px, self.height_px) != (LATENTSYNC_16_MODEL_ID, WIDTH, HEIGHT)
                or not 1 / FRAME_RATE <= self.duration_seconds <= MAX_DURATION):
            raise ValueError("当前人物口型准备支持 1080×1920 的 LatentSync 1.6，最长 12.8 秒。")

    @property
    def samples(self):
        return round(self.duration_seconds * SAMPLE_RATE)

    @property
    def frames(self):
        return self.samples * FRAME_RATE // SAMPLE_RATE

    @property
    def windows(self):
        return (self.frames + WINDOW_FRAMES - 1) // WINDOW_FRAMES


@dataclass(frozen=True)
class PresenterPreparationPolicy:
    id: str = "latentsync-1.6-windowed-v1"
    threads: int = 2
    max_timeout_seconds: int = 21600
    timeout_seconds_per_window: int = 1200
    sample_seconds: float = 0.25
    measured_margin: float = 1.25
    available_memory_fraction: float = 0.95


class _PresenterObservation(CreationStageGrantContext):
    def __init__(self, *args, recheck, model_command, **kwargs):
        super().__init__(*args, **kwargs)
        self.recheck, self.model_command = recheck, tuple(model_command)
        self.model_observation = None

    def check(self):
        super().check()
        self.recheck()

    def run(self, command, **kwargs):
        result = super().run(command, **kwargs)
        if self.model_command[1] in command:
            outcome = result.observed_resources
            if (outcome is None or not outcome.rss_sample_complete or not outcome.threads_sample_complete
                    or outcome.peak_rss_bytes is None or outcome.peak_threads is None):
                raise ValueError("人物口型进程采样不完整，无法发布预算。")
            self.model_observation = asdict(outcome)
        return result


class PresenterResourcePreparation(DeviceResourcePreparation):
    def __init__(self, *, model_catalog, provider, policy=PresenterPreparationPolicy(), **kwargs):
        super().__init__(policy=policy, **kwargs)
        self.root = self.root / "presenter"
        self.catalog, self.provider = model_catalog, provider

    def _shape_payload(self, shape):
        return asdict(shape)

    def _load(self, path=None):
        return load_device_resource_profile(path or self.profile_path, device=self.device,
            backend_identities=lambda name, budget: backend_identity(name, budget,
                settings=self.settings, model_catalog=self.catalog))

    def _identity(self):
        if not isinstance(self.provider, LatentSync16Provider) or self.provider.runtime.backend != "mlx":
            raise DeviceProfileUnavailable("当前人物口型准备需要已安装的 Apple MLX LatentSync 1.6。")
        if self.provider.temporary_root != Path(self.write_paths["temporary"]):
            raise DeviceProfileUnavailable("人物口型运行时的实际临时卷与设备准备记录不一致。")
        identity = backend_identity("presenter", None, settings=self.settings, model_catalog=self.catalog)
        if not identity:
            raise DeviceProfileUnavailable("LatentSync 1.6 MLX 安装、模型权重或执行器未通过验证。")
        return identity

    def _fixtures(self):
        root = self.provider.runtime.root / "calibration"
        result = {}
        for name, expected in CALIBRATION_FILES.items():
            path = (root / name).resolve()
            if not path.is_file() or _hash_file(path) != expected:
                raise DeviceProfileUnavailable("缺少校验通过的无个人资料人物口型标定片段。")
            result[name] = {"path": str(path), "sha256": expected, "bytes": path.stat().st_size}
        return result

    def _prepare_new(self, shape, actor, *, plan_id, request_binding):
        if not self.profile_path.is_file():
            raise DeviceProfileUnavailable("请先准备这台设备的主机资源档。")
        with _profile_lock(self.profile_path):
            current = self._load()
            revision, profile_digest = _profile_state(self.profile_path)
        identity, fixtures = self._identity(), self._fixtures()
        memory = sample_host_memory()
        reserve = current.budget.min_available_memory_bytes
        if memory.pressure in {"critical", "unknown"} or memory.available_bytes is None:
            raise DeviceProfileUnavailable("当前系统内存压力无法支持人物口型标定。")
        headroom = math.floor((memory.available_bytes - reserve) * self.policy.available_memory_fraction)
        rss = min(8 * 1024**3, headroom)
        # The worker materializes only one checkpoint at each inference stage.
        resident_weights = max(UNET_FILE[1], RUNTIME_WEIGHTS[0][1])
        if rss < resident_weights:
            raise DeviceProfileUnavailable(
                f"按现有设备保留量，本次可用 {max(0, rss) / 1024**3:.2f} GiB，"
                f"低于 LatentSync 1.6 的最大单阶段权重体积 {resident_weights / 1024**3:.2f} GiB。")
        raw_bytes = shape.width_px * shape.height_px * 4 * shape.frames
        timeout = min(self.policy.max_timeout_seconds, 300 + shape.windows * self.policy.timeout_seconds_per_window)
        limits = PresenterBudget(
            max_section_count=1, max_input_bytes=raw_bytes + sum(item["bytes"] for item in fixtures.values()),
            max_duration_seconds=shape.duration_seconds, max_output_bytes=raw_bytes,
            max_threads=self.policy.threads, max_timeout_seconds=timeout,
            max_process_rss_bytes=rss, max_process_threads=(psutil.cpu_count() or 1) * 8 + 16,
            runtime_sample_seconds=self.policy.sample_seconds, additional_memory_bytes=rss,
            additional_disk_bytes=raw_bytes * 4, bound_model_id=shape.model_id, max_source_width_px=shape.width_px,
            max_source_height_px=shape.height_px, preparation_scope="windowed_probe_v1",
        )
        bootstrap = replace(current.budget, presenter=limits)
        disks = sample_write_volumes(self.write_paths,
            {"database": 0, "output": raw_bytes, "temporary": limits.additional_disk_bytes + raw_bytes},
            reserve_bytes=bootstrap.min_free_disk_bytes, verify_writable=True)
        if not all(item["passed"] for item in disks.values()):
            raise DeviceProfileUnavailable("人物口型标定临时卷或输出卷空间不足。")
        plan = {"format_version": 1, "id": plan_id, "kind": "presenter", "device": asdict(self.device),
                "request_binding": request_binding, "actor_identity": actor.identity_payload(),
                "workspace_id": actor.workspace_id, "member_id": actor.member_id, "shape": asdict(shape),
                "policy": asdict(self.policy), "bootstrap_budget": asdict(bootstrap), "inputs": fixtures,
                "backends": {"presenter": identity}, "write_paths": self.write_paths,
                "expected_revision": revision, "expected_profile_digest": profile_digest}
        plan["input_digest"] = _digest(plan)
        _atomic_json(self.root / "plans" / f"{plan_id}.json", plan)
        return plan_id, bootstrap

    def bootstrap_budget(self, plan_id):
        return host_budget_from_payload(self._plan(plan_id)["bootstrap_budget"])

    def intent(self, plan_id, actor):
        plan = self._plan(plan_id)
        if not self._can_access(plan, actor):
            raise PermissionError("人物口型标定属于其他成员。")
        return SubmissionIntent(plan["workspace_id"], OWNER_TYPE, plan_id, plan["input_digest"], UNIT_KEY, SchedulePolicy())

    def _resources(self, plan):
        budget = PresenterBudget(**plan["bootstrap_budget"]["presenter"])
        return {"kind": "creation_person_render", "execution_mode": "isolated_avatar_lipsync",
                "section_count": 1, "input_bytes": sum(item["bytes"] for item in plan["inputs"].values()),
                "duration_seconds": budget.max_duration_seconds, "max_output_bytes": budget.max_output_bytes,
                "threads": budget.max_threads, "timeout_seconds": budget.max_timeout_seconds,
                **presenter_scope_fields(budget, plan["shape"]["model_id"])}

    def verify_submission(self, intent, actor):
        if intent != self.intent(intent.owner_id, actor):
            raise PermissionError("人物口型标定提交与原计划不符。")
        plan = self._plan(intent.owner_id)
        return VerifiedWork(plan["workspace_id"], OWNER_TYPE, plan["id"], plan["input_digest"],
                            UNIT_KEY, plan["member_id"], "normal_make", self._resources(plan))

    def _recheck(self, plan, actor):
        if not self._can_access(plan, actor):
            raise PermissionError("人物口型标定成员权限已变更。")
        if (plan["device"] != asdict(self.device) or self.write_paths != plan["write_paths"]
                or self._identity() != plan["backends"]["presenter"] or self._fixtures() != plan["inputs"]):
            raise ValueError("人物口型标定模型、输入或设备位置已变更。")

    def check_current(self, request, conn):
        try:
            plan = self._plan(request.owner_id)
            if ((request.workspace_id, request.member_id, request.owner_type, request.unit_key, request.input_digest)
                    != (plan["workspace_id"], plan["member_id"], OWNER_TYPE, UNIT_KEY, plan["input_digest"])
                    or request.resources != self._resources(plan)
                    or _profile_state(self.profile_path) != (plan["expected_revision"], plan["expected_profile_digest"])):
                return "stale"
            self._recheck(plan, CreationExecutionActor.from_event(plan["workspace_id"], plan["actor_identity"]))
            return "ready"
        except PermissionError:
            return "unauthorized"
        except (OSError, ValueError, KeyError, DeviceProfileUnavailable):
            return "dependency"

    def execute(self, grant, scheduler):
        plan = self._plan(grant.owner_id)
        actor = CreationExecutionActor.from_event(plan["workspace_id"], plan["actor_identity"])
        limits = self.bootstrap_budget(plan["id"]).presenter
        shape = PresenterPreparationShape(**plan["shape"])
        started = time.monotonic()
        context = _PresenterObservation(scheduler, grant, started + limits.max_timeout_seconds,
            limits.max_output_bytes, limits.max_threads, recheck=lambda: self._recheck(plan, actor),
            model_command=(str(self.provider.runtime_path), str(self.provider.worker_path)))
        try:
            context.check()
            with tempfile.TemporaryDirectory(prefix="presenter-preparation-", dir=self.write_paths["temporary"]) as work:
                output = Path(work) / "sample.mp4"
                visual, audio = (plan["inputs"][name]["path"] for name in ("visual.mp4", "driver.wav"))
                if shape.samples != round(DURATION * SAMPLE_RATE):
                    audio_file = Path(work) / "driver.wav"
                    executable = shutil.which("ffmpeg")
                    if not executable:
                        raise ValueError("人物口型标定缺少 FFmpeg。")
                    converted = context.run([executable, "-v", "error", "-nostdin", "-stream_loop", "-1",
                        "-i", audio, "-af", f"atrim=end_sample={shape.samples},asetpts=PTS-STARTPTS",
                        "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(audio_file)],
                        output_paths=(audio_file,))
                    if converted.returncode:
                        raise ValueError("无法生成与实际配音时长匹配的合成标定音轨。")
                    audio = str(audio_file)
                effective_audio_sha256 = _hash_file(audio)
                person = PersonProfile("presenter-calibration", actor.workspace_id, "Synthetic calibration", "v1",
                                       visual, (), ())
                voice = VoiceProfile("presenter-calibration-voice", actor.workspace_id, "v1", "en", audio, "")
                request = AvatarAndLipSyncRequest(actor.workspace_id, person, voice, audio,
                    effective_audio_sha256, "en", "natural", "neutral", None,
                    limits.bound_model_id, visual)
                result = self.provider.render(request=request, output_path=output, grant_context=context)
                if result.status != "succeeded" or result.artifact_path != str(output.resolve()):
                    raise ValueError(result.remediation_action or "人物口型实测未返回成片。")
                context.check()
                media = json.loads(result.alignment_evidence)
                if (context.model_observation is None or media.get("backend") != "apple_mlx"
                        or media.get("engine") != limits.bound_model_id or media.get("rendered_frames") != shape.frames
                        or media.get("audio_conditioning_samples") != shape.samples
                        or (media.get("width"), media.get("height")) != (WIDTH, HEIGHT)
                        or media.get("window_count") != shape.windows or type(media.get("scratch_file_bytes")) is not int
                        or type(media.get("peak_mlx_allocation_bytes")) is not int
                        or not 0 < media["peak_mlx_allocation_bytes"] <= limits.additional_memory_bytes
                        or not 0 < media["scratch_file_bytes"] <= limits.additional_disk_bytes
                        or not 0 < output.stat().st_size <= limits.max_output_bytes):
                    raise ValueError("人物口型标定结果与计划的实际窗口、音轨采样数或观测数据不匹配。")
                receipt = {"format_version": 1, "plan_id": plan["id"], "request_id": grant.request_id,
                           "generation": grant.generation, "input_digest": grant.input_digest,
                           "device": plan["device"], "backends": plan["backends"], "shape": plan["shape"],
                           "inputs": plan["inputs"], "elapsed_seconds": time.monotonic() - started,
                           "model_observation": context.model_observation,
                           "driver_sha256": effective_audio_sha256,
                           "media": {**media, "sha256": _hash_file(output), "bytes": output.stat().st_size}}
                digest = _atomic_json(self.root / "receipts" / f"{grant.request_id}-{grant.generation}.json", receipt)
            scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                                    checkpoint_write=lambda *_: RECEIPT_PREFIX + digest)
        except (GrantedCommandError, OSError, ValueError, KeyError, DeviceProfileUnavailable) as exc:
            _atomic_json(self.root / "failures" / f"{grant.request_id}-{grant.generation}.json",
                {"kind": type(exc).__name__, "detail": str(exc)[-1500:], "elapsed_seconds": time.monotonic() - started,
                 "model_observation": context.model_observation})
            scheduler.fail_unit(grant, reason="presenter_preparation_failed", failure_write=lambda *_: None)

    def status(self, *, database_path, request_id, actor):
        row, plan = self._request(database_path=database_path, request_id=request_id, actor=actor)
        result = {"id": row["id"], "plan_id": plan["id"], "state": row["state"], "wait_reason": row["wait_reason"]}
        if row["state"] == "failed" and row["wait_reason"] == "presenter_preparation_failed":
            path = self.root / "failures" / f"{row['id']}-{row['last_grant_generation']}.json"
            if path.is_file():
                failure = _read(path)
                result["failure_reason"] = failure["detail"]
                result["repair_action"] = "释放资源后按同一模型和画幅重新标定。"
        elif row["state"] == "done":
            receipt = self._completed_receipt(row, plan)
            observed, media = receipt["model_observation"], receipt["media"]
            result["measurement"] = {
                "scope": "measured_invocation", "model_id": media["engine"], "backend": media["backend"],
                "source_width_px": media["width"], "source_height_px": media["height"],
                "model_resolution_px": media["inference_resolution"], "frames": media["rendered_frames"],
                "windows": media["window_count"], "measured_duration_seconds": receipt["shape"]["duration_seconds"],
                "elapsed_seconds": receipt["elapsed_seconds"],
                "peak_process_group_rss_bytes": observed["peak_rss_bytes"],
                "peak_process_group_threads": observed["peak_threads"],
                "peak_mlx_allocation_bytes": media["peak_mlx_allocation_bytes"],
                "scratch_file_bytes": media["scratch_file_bytes"], "output_bytes": media["bytes"],
                "output_sha256": media["sha256"],
            }
        return result

    def _completed_receipt(self, row, plan):
        raw = (self.root / "receipts" / f"{row['id']}-{row['last_grant_generation']}.json").read_bytes()
        receipt = json.loads(raw)
        if (row["checkpoint_ref"] != RECEIPT_PREFIX + hashlib.sha256(raw).hexdigest()
                or (row["workspace_id"], row["member_id"], row["unit_key"], row["input_digest"])
                   != (plan["workspace_id"], plan["member_id"], UNIT_KEY, plan["input_digest"])
                or json.loads(row["resources_json"]) != self._resources(plan)
                or any(receipt[key] != plan[key] for key in ("device", "backends", "shape", "inputs", "input_digest"))
                or (receipt["plan_id"], receipt["request_id"], receipt["generation"])
                   != (plan["id"], row["id"], row["last_grant_generation"])):
            raise DeviceProfileUnavailable("人物口型标定回执与原计划、许可或模型不一致。")
        return receipt

    def finalize(self, *, database_path, request_id, actor):
        row, plan = self._request(database_path=database_path, request_id=request_id, actor=actor)
        if row["state"] != "done":
            raise DeviceProfileUnavailable("人物口型标定尚无完成回执。")
        receipt = self._completed_receipt(row, plan)
        self._recheck(plan, actor)
        observed = receipt["model_observation"]
        if (not observed["rss_sample_complete"] or not observed["threads_sample_complete"]
                or type(observed["peak_rss_bytes"]) is not int or type(observed["peak_threads"]) is not int):
            raise DeviceProfileUnavailable("人物口型标定采样不完整。")
        original = self.bootstrap_budget(plan["id"]).presenter
        margin = plan["policy"]["measured_margin"]
        rss = min(original.max_process_rss_bytes, math.ceil(observed["peak_rss_bytes"] * margin))
        allocation = math.ceil(receipt["media"]["peak_mlx_allocation_bytes"] * margin)
        reserved_memory = min(original.additional_memory_bytes, max(rss, allocation))
        prepared = replace(original, preparation_scope="windowed_measured_v1",
            max_process_rss_bytes=rss, additional_memory_bytes=reserved_memory,
            max_process_threads=min(original.max_process_threads, math.ceil(observed["peak_threads"] * margin)),
            max_timeout_seconds=min(original.max_timeout_seconds, receipt["elapsed_seconds"] * margin))
        return self._publish_profile(plan, prepared, receipt)

    def _publish_profile(self, plan, prepared, receipt):
        with _profile_lock(self.profile_path):
            document = _read(self.profile_path)
            if document["profile_id"] == plan["id"]:
                return self._load()
            if (document["revision"], _digest(document)) != (plan["expected_revision"], plan["expected_profile_digest"]):
                raise DeviceProfileUnavailable("设备档已更新，人物口型回执不能覆盖新版本。")
            old = deepcopy(document)
            document.update(profile_id=plan["id"], revision=document["revision"] + 1)
            document["budget"]["presenter"] = asdict(prepared)
            document["backends"]["presenter"] = plan["backends"]["presenter"]
            evidence = {
                "format_version": 1, "device": plan["device"], "backend_identity": plan["backends"]["presenter"],
                "budget_digest": _digest(asdict(prepared)), "method": plan["policy"]["id"],
                "input_range": {"shape": plan["shape"], "scope": "one_measured_invocation"},
                "observations": receipt,
            }
            record = plan["id"] + "_presenter"
            digest = _atomic_json(self.profile_path.parent / "resource-profile-evidence" / f"{record}.json", evidence)
            document.setdefault("evidence", {})["presenter"] = {"record_id": record, "sha256": digest}
            _atomic_json(self.profile_path.parent / "resource-profile-history" / f"{_digest(old)}.json", old)
            candidate = self.profile_path.with_name("." + plan["id"] + ".candidate.json")
            try:
                _atomic_json(candidate, document)
                if self._load(candidate).budget.presenter != prepared:
                    raise DeviceProfileUnavailable("候选人物口型预算未通过设备档校验。")
                _atomic_json(self.profile_path, document)
            finally:
                candidate.unlink(missing_ok=True)
        return self._load()
