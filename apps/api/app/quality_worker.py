"""Fixed quality entry: no API composition, database, model or dotenv startup."""
from __future__ import annotations

import hashlib
import json
import marshal
from pathlib import Path
import sys
import time
from types import FunctionType

from app import quality_checks
from app import quality_worker_runtime
from app.quality_worker_runtime import (
    QualityBudget, QualityWorkerContext, QualityWorkerError, file_identity, hash_file, source_receipt,
)


def worker_identity() -> str:
    """Fingerprint deployed executable code, including frozen-module code objects."""
    digest = hashlib.sha256()
    for module in (quality_checks, quality_worker_runtime, sys.modules[__name__]):
        digest.update(module.__name__.encode())
        for name, value in sorted(vars(module).items()):
            if isinstance(value, FunctionType) and value.__module__ == module.__name__:
                digest.update(name.encode())
                digest.update(marshal.dumps(value.__code__))
            elif isinstance(value, type) and value.__module__ == module.__name__:
                for member, method in sorted(vars(value).items()):
                    if isinstance(method, (staticmethod, classmethod)):
                        method = method.__func__
                    if isinstance(method, FunctionType):
                        digest.update((name + '.' + member).encode())
                        digest.update(marshal.dumps(method.__code__))
            elif name.isupper() and isinstance(value, (str, int, float, tuple)):
                digest.update(json.dumps([name, value], ensure_ascii=False).encode())
    digest.update(quality_checks._BLACK_PATTERN.pattern.encode())
    return digest.hexdigest()


def inspect_request(request: dict) -> dict:
    budget = QualityBudget(**request["budget"])
    context = QualityWorkerContext(budget, min(float(request["deadline_monotonic"]),
                                             time.monotonic() + budget.max_timeout_seconds))
    identity = worker_identity()
    if identity != budget.worker_sha256:
        raise QualityWorkerError("质检 worker 与独立标定版本不一致")
    source, check_id = request["source"], request["check_id"]
    path = Path(source["path"])
    if not path.is_absolute() or file_identity(path) != source["file_identity"]:
        raise QualityWorkerError("检查期间成片内容已变化，请重新检查当前成片。")
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
