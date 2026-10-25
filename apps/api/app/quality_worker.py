"""Fixed quality entry: no API composition, database, model or dotenv startup."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time

from app import quality_checks
from app import quality_worker_runtime
from app.quality_worker_runtime import (
    QualityBudget, QualityWorkerContext, QualityWorkerError, file_identity, hash_file, source_receipt,
    QUALITY_PHASE_PROTOCOLS, QUALITY_STREAM_PROTOCOL, quality_phase_request,
)


def worker_identity(*, check=None) -> str:
    """Pin the deployed binary, or the ordered three source files in development."""
    paths = ([Path(sys.executable)] if getattr(sys, 'frozen', False) else
        [Path(module.__file__) for module in (quality_checks, quality_worker_runtime, sys.modules[__name__])])
    digest = hashlib.sha256()
    if check is not None:
        check()
    for path in paths:
        with path.open('rb') as stream:
            while chunk := stream.read(65536):
                if check is not None:
                    check()
                digest.update(chunk)
    if check is not None:
        check()
    return digest.hexdigest()


def inspect_request(request: dict) -> dict:
    budget = QualityBudget(**request["budget"])
    streaming = request.get("protocol") == QUALITY_STREAM_PROTOCOL
    resources = None
    if streaming:
        prepared = quality_phase_request(request, {"_phases": request["checkpoints"]})
        if request["resources"] != prepared["resources"]:
            raise QualityWorkerError("质检流处理声明与固定来源和检查点不一致")
        resources = prepared["resources"]
    context = QualityWorkerContext(budget, min(float(request["deadline_monotonic"]),
        time.monotonic() + (resources["timeout_seconds"] if streaming else budget.max_timeout_seconds)), resources=resources)
    identity = worker_identity(check=context.check)
    if identity != budget.worker_sha256:
        raise QualityWorkerError("质检 worker 与独立标定版本不一致")
    source, check_id = request["source"], request["check_id"]
    path = Path(source["path"])
    if not path.is_absolute() or file_identity(path) != source["file_identity"]:
        raise QualityWorkerError("检查期间成片内容已变化，请重新检查当前成片。")
    if request.get("protocol") in QUALITY_PHASE_PROTOCOLS:
        # Verify the sealed prefix before consuming it; no offset or serialized
        # hashlib internals are trusted as a substitute for the final full hash.
        expected = quality_phase_request(request, {"_phases": request["checkpoints"]})
        if (request["phase"], request["checkpoint_digest"]) != (expected["phase"], expected["checkpoint_digest"]):
            raise QualityWorkerError("质检阶段与原检查点不一致")
        phase = request["phase"]
        digest = None
        if phase in {"metadata", "finalize"}:
            digest = hash_file(path, check=context.check,
                max_bytes=source["size_bytes"] if streaming else budget.max_input_bytes)
            if digest != source["render_ref"]:
                raise QualityWorkerError("成片与登记产物不匹配，请重新生成。")
        data = quality_checks.inspect_quality_phase(path, phase, request["checkpoints"],
            context=context, **request["thresholds"])
        context.check()
        if file_identity(path) != source["file_identity"]:
            raise QualityWorkerError("检查期间成片内容已变化，请重新检查当前成片。")
        result = {"phase": phase, "checkpoint_digest": request["checkpoint_digest"],
            "data": data, "receipt": source_receipt(source, check_id), "worker_sha256": identity}
        if digest is not None:
            result["file_sha256"] = digest
        if phase == "finalize":
            result["evidence"] = result.pop("data")
        elif streaming:
            result["data"]["_processing"] = context.processing
            if phase == "clipping":
                runs = [item for item in context.processing if item["pcm_s16le_mono"]]
                prior = request["checkpoints"].get("truncation", {}).get("decoded_streams", {}).get("audio")
                result["data"]["decoded_pcm"] = {"passes": [
                    {"sample_count": item["stdout_bytes"] // 2, "sample_rate": item["output_sample_rate"],
                     "channels": item["output_channels"], "sample_format": item["sample_format"],
                     "complete": item["complete"]} for item in runs],
                    "frame_sample_mapping_matches": (runs[0]["stdout_bytes"] == prior["sample_count"] * 2
                        and runs[0]["output_sample_rate"] == prior["sample_rate"]
                        if runs and prior and prior["sample_count"] is not None else None)}
        return quality_checks.fit_quality_result(result, budget.max_report_bytes, check=context.check) if streaming else result
    before = hash_file(path, check=context.check, max_bytes=budget.max_input_bytes)
    if before != source["render_ref"]:
        raise QualityWorkerError("成片与登记产物不匹配，请重新生成。")
    report = quality_checks.inspect_media_quality(path, context=context, **request["thresholds"])
    after = hash_file(path, check=context.check, max_bytes=budget.max_input_bytes)
    if after != before or file_identity(path) != source["file_identity"]:
        raise QualityWorkerError("检查期间成片内容已变化，请重新检查当前成片。")
    return {"evidence": quality_checks.quality_report_evidence(report),
            "receipt": source_receipt(source, check_id), "worker_sha256": identity}


def main(arguments: list[str]) -> int:
    if len(arguments) != 2 or not arguments[1].isdigit() or int(arguments[1]) <= 0:
        raise ValueError("quality worker requires one sealed request and its finite byte limit")
    limit = int(arguments[1])
    with Path(arguments[0]).open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise QualityWorkerError("质检封存请求超过有限大小")
    request = json.loads(raw)
    budget = QualityBudget(**request["budget"])
    try:
        result = inspect_request(request)
    except (QualityWorkerError, quality_checks.QualityCheckError, OSError, ValueError, TypeError, KeyError) as exc:
        result = {"error": str(exc)}
    encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
    if len(encoded) > budget.max_report_bytes:
        result = {"error": "质检报告超过本次已标定上限，检查未完成"}
        encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 1 if "error" in result else 0
