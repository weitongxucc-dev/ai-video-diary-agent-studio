"""Finite quality-only worker boundary, inside one already registered process group."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from collections.abc import Callable
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import time


class QualityWorkerError(RuntimeError):
    pass


# This is the ordered inspection algorithm, not a limit on media length.
QUALITY_PHASES = ("metadata", "black_frames", "truncation", "clipping", "finalize")
QUALITY_PHASE_PROTOCOL = "quality_phases_v1"
QUALITY_STREAM_PROTOCOL = "quality_stream_v1"
QUALITY_PHASE_PROTOCOLS = {QUALITY_PHASE_PROTOCOL, QUALITY_STREAM_PROTOCOL}


def quality_phase_request(request: dict, evidence: dict) -> dict:
    """Bind a released unit to the exact, bounded prefix already committed."""
    if request.get("protocol") not in QUALITY_PHASE_PROTOCOLS:
        return request  # Original sealed requests retain their original protocol.
    checkpoints = evidence.get("_phases", {})
    if not isinstance(checkpoints, dict) or set(checkpoints) != set(QUALITY_PHASES[:len(checkpoints)]):
        raise ValueError("质检阶段回执不是完整有序前缀")
    if len(checkpoints) >= len(QUALITY_PHASES):
        raise ValueError("质检已经完成全部阶段")
    digest = hashlib.sha256(json.dumps(checkpoints, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    phase = QUALITY_PHASES[len(checkpoints)]
    prepared = {**request, "phase": phase, "checkpoints": checkpoints, "checkpoint_digest": digest}
    if request.get("protocol") == QUALITY_STREAM_PROTOCOL:
        prepared["resources"] = quality_stream_resources(request, phase, checkpoints)
    return prepared


def quality_stream_resources(request: dict, phase: str, checkpoints: dict) -> dict:
    """Full-file bytes are streaming work, never an allocation or a media cap.

    No trustworthy same-backend/codec/shape throughput is sealed yet, so this is
    one finite preparation attempt. It does not promise completion, renew itself,
    or infer a duration/speed from a container header. EOF counters from a prior
    phase inform the next demand; mono/resampler mapping is checked against PCM.
    """
    budget = QualityBudget(**request["budget"])
    source = request["source"]
    provenance = {"mode": "bounded_preparation", "deadline_source": "sealed_quality_preparation_budget",
        "deadline_seconds": budget.max_timeout_seconds, "coverage": "complete_stream_required"}
    audio = checkpoints.get("truncation", {}).get("decoded_streams", {}).get("audio")
    if phase == "clipping" and audio is not None and audio["complete"]:
        provenance["decoded_audio"] = {"source": "ffprobe_complete_frame_scan",
            "sample_count": audio["sample_count"], "sample_rate": audio["sample_rate"],
            "output_channels": 1, "sample_format": "s16le", "mapping_verification": "pending_pcm_eof",
            "expected_pcm_bytes_per_pass": audio["sample_count"] * 2 if audio["sample_count"] is not None else None}
    return {"kind": "quality_check", "execution_mode": "isolated_quality_python", "read_mode": "stream",
        "phase": phase, "artifact_digest": source["render_ref"], "source_bytes": source["size_bytes"],
        "input_bytes": source["size_bytes"], "size_bytes": source["size_bytes"],
        "timeout_seconds": budget.max_timeout_seconds, "threads": budget.decoder_threads,
        "max_output_bytes": budget.max_report_bytes, "max_stderr_bytes": budget.max_stderr_bytes,
        "budget_digest": budget_identity(budget), "workload_provenance": provenance}


def quality_unit_key(request: dict) -> str:
    base = f"quality:{request['check_id']}"
    return f"{base}:{request['phase']}" if "phase" in request else base


@dataclass(frozen=True)
class QualityBudget:
    max_input_bytes: int
    max_pcm_bytes: int
    max_frame_json_bytes: int
    max_report_bytes: int
    max_stderr_bytes: int
    max_timeout_seconds: float
    decoder_threads: int
    max_process_rss_bytes: int
    max_process_threads: int
    runtime_sample_seconds: float
    additional_memory_bytes: int
    additional_disk_bytes: int
    max_tool_bytes: int
    ffmpeg_path: str
    ffprobe_path: str
    ffmpeg_sha256: str
    ffprobe_sha256: str
    worker_sha256: str

    def __post_init__(self):
        for name in ("max_input_bytes", "max_pcm_bytes", "max_frame_json_bytes", "max_report_bytes",
                     "max_stderr_bytes", "decoder_threads", "max_process_rss_bytes", "max_process_threads",
                     "additional_memory_bytes", "additional_disk_bytes", "max_tool_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"Quality budget requires explicit positive {name}")
        for name in ("max_timeout_seconds", "runtime_sample_seconds"):
            value = getattr(self, name)
            if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Quality budget requires explicit finite {name}")
        if (self.additional_memory_bytes < self.max_process_rss_bytes
                or self.max_process_threads < self.decoder_threads or self.runtime_sample_seconds > .25):
            raise ValueError("Quality process limits must fit reserved memory/threads and bounded sampling")
        for name in ("ffmpeg_path", "ffprobe_path"):
            if not Path(getattr(self, name)).is_absolute():
                raise ValueError("Quality tools require calibrated absolute paths")
        for name in ("ffmpeg_sha256", "ffprobe_sha256", "worker_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", getattr(self, name)):
                raise ValueError("Quality tool/worker identity requires a calibrated SHA-256")


def file_identity(path: Path) -> dict[str, int]:
    if path.is_symlink():
        raise ValueError("质检来源不能是符号链接")
    info = path.stat()
    return {"device": info.st_dev, "inode": info.st_ino, "size_bytes": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


def source_receipt(source: dict, check_id: str) -> dict:
    return {key: source[key] for key in ("workspace_id", "task_id", "timeline_version_id", "source_ref",
        "artifact_id", "render_ref", "size_bytes", "file_identity")} | {"check_id": check_id}


def budget_identity(budget: QualityBudget) -> str:
    return hashlib.sha256(json.dumps(asdict(budget), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def hash_file(path: Path, *, check, max_bytes: int) -> str:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            check()
            size += len(chunk)
            if size > max_bytes:
                raise QualityWorkerError("质检完整哈希超过本次已标定输入上限，检查未完成")
            digest.update(chunk)
    check()
    return digest.hexdigest()


class QualityWorkerContext:
    def __init__(self, budget: QualityBudget, deadline: float, *, resources: dict | None = None):
        if os.getpid() != os.getpgrp() or os.getpid() != os.getsid(0):
            raise QualityWorkerError("质检 worker 必须保留已登记的进程组首进程身份")
        self.budget, self.deadline = budget, deadline
        self.resources = resources
        self.processing = []
        self._verified_tools: set[str] = set()

    def check(self):
        if time.monotonic() >= self.deadline:
            raise QualityWorkerError("质检单元总时限已到，检查未完成")

    def run(self, command: list[str], timeout: float, *, pcm: bool = False,
            stdout_consumer: Callable[[bytes], None] | None = None):
        """Drain a full decoder under one finite permit, with bounded buffering.

        New stream requests bound residency, diagnostics and the process deadline;
        decoded stdout is consumed immediately and counted to EOF. Header-derived
        duration is not an execution allowance. Original sealed bulk requests keep
        their original cumulative quotas. Metadata stdout stays report-bounded.
        """
        if pcm and stdout_consumer is None:
            raise QualityWorkerError("PCM 质检必须使用流式消费者")
        self.check()
        name = command[0]
        if name not in {"ffmpeg", "ffprobe"}:
            raise QualityWorkerError("质检只允许固定 ffmpeg/ffprobe 命令")
        binary = getattr(self.budget, name + "_path")
        if name not in self._verified_tools:
            if hash_file(Path(binary), check=self.check, max_bytes=self.budget.max_tool_bytes) != getattr(self.budget, name + "_sha256"):
                raise QualityWorkerError("质检工具与已标定版本不一致")
            self._verified_tools.add(name)
        argv = [binary, "-threads", str(self.budget.decoder_threads)]
        if name == "ffmpeg":
            argv += ["-filter_threads", str(self.budget.decoder_threads),
                     "-filter_complex_threads", str(self.budget.decoder_threads)]
        argv += command[1:]
        streaming = self.resources is not None and self.resources.get("read_mode") == "stream"
        # No second fixed 30/180-second decoder timer may override the sealed unit.
        deadline = self.deadline if streaming else min(self.deadline, time.monotonic() + timeout)
        caps = {"stdout": (None if streaming and stdout_consumer is not None else
                self.budget.max_report_bytes if streaming else
                self.budget.max_pcm_bytes if pcm else self.budget.max_frame_json_bytes),
                "stderr": self.budget.max_stderr_bytes}
        started = time.monotonic()
        complete = False
        output = {"stdout": bytearray(), "stderr": bytearray()}
        sizes = {"stdout": 0, "stderr": 0}
        # Deliberately inherit the registered Python worker's PGID. The outer
        # granted runner owns termination and proof for this whole descendant group.
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=False)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    self.check()
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(command, timeout)
                    for key, _ in selector.select(min(.05, max(0, deadline - time.monotonic()))):
                        label = key.data
                        cap = caps[label]
                        data = os.read(key.fileobj.fileno(), 65536 if cap is None else min(65536, cap + 1 - sizes[label]))
                        if not data:
                            selector.unregister(key.fileobj)
                        else:
                            sizes[label] += len(data)
                            if caps[label] is not None and sizes[label] > caps[label]:
                                raise QualityWorkerError("质检命令输出超过本次已标定上限，检查未完成")
                            if label == "stdout" and stdout_consumer is not None:
                                stdout_consumer(data)
                            else:
                                output[label].extend(data)
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
            self.check()
            complete = code == 0
            return subprocess.CompletedProcess(command, code, bytes(output["stdout"]), bytes(output["stderr"]))
        finally:
            self.processing.append({"tool": name, "pcm_s16le_mono": pcm,
                "stdout_bytes": sizes["stdout"], "stderr_bytes": sizes["stderr"],
                "elapsed_seconds": time.monotonic() - started, "complete": complete,
                **({"output_sample_rate": int(command[command.index("-ar") + 1]), "output_channels": 1,
                    "sample_format": "s16le"} if pcm else {})})
            if process.poll() is None:
                process.kill()
                process.wait(timeout=self.budget.runtime_sample_seconds)
            process.stdout.close()
            process.stderr.close()
