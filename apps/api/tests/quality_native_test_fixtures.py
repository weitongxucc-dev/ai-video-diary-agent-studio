"""Finite synthetic quality transports under real grants, not device calibration."""

import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


NATIVE_PROGRAM = r"""
import base64,json,sys,time
from dataclasses import asdict
from pathlib import Path
from app.quality_checks import (QualityCheckError, inspect_media_quality, detect_truncation, detect_timestamp_offset)
from app.quality_worker_runtime import QualityBudget, QualityWorkerContext
value=json.loads(Path(sys.argv[1]).read_text())
context=QualityWorkerContext(QualityBudget(**value['budget']),time.monotonic()+value['timeout'])
try:
    if value['operation']=='command':
        result=context.run(value['command'],value['timeout'])
        output={'returncode':result.returncode,'stdout':base64.b64encode(result.stdout).decode(),
            'stderr':base64.b64encode(result.stderr).decode()}
    elif value['operation']=='inspect_media_quality':
        output={'report':asdict(inspect_media_quality(Path(value['path']),context=context,**value['kwargs']))}
    else:
        detector={'detect_truncation':detect_truncation,'detect_timestamp_offset':detect_timestamp_offset}[value['operation']]
        findings,unavailable=detector(Path(value['path']),context=context,**value['kwargs'])
        output={'findings':[asdict(item) for item in findings],'unavailable':[asdict(item) for item in unavailable]}
except QualityCheckError as error:
    output={'quality_error':str(error)}
print(json.dumps(output))
"""


@pytest.fixture
def licensed_quality_native(tmp_path, granted_media):
    from dataclasses import asdict
    from app.creation.quality_execution import run_quality_worker
    from app.granted_subprocess import run_granted_command
    from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe
    from tests.host_memory_boundary import IsolatedMemoryMonitor
    from app.quality_checks import QualityCheckError, QualityFinding, QualityReport, QualityUnavailable
    from app.quality_worker import worker_identity
    from test_quality_resource_owner import fixture_budget

    binaries = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    if not all(binaries.values()):
        pytest.fail("licensed synthetic quality requires ffmpeg and ffprobe")
    identities = {}
    for name, path in binaries.items():
        with Path(path).open("rb") as source:
            identities[name + "_sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
    budget = replace(fixture_budget(), max_input_bytes=8 * 1024**2, max_pcm_bytes=4 * 1024**2,
        max_frame_json_bytes=512 * 1024, max_report_bytes=4 * 1024**2, max_stderr_bytes=64 * 1024,
        max_timeout_seconds=30, max_process_rss_bytes=512 * 1024**2, max_process_threads=16,
        additional_memory_bytes=512 * 1024**2, additional_disk_bytes=32 * 1024**2,
        max_tool_bytes=max(Path(path).stat().st_size for path in binaries.values()),
        ffmpeg_path=binaries["ffmpeg"], ffprobe_path=binaries["ffprobe"],
        worker_sha256=worker_identity(), **identities)
    sequence = 0

    def invoke(operation, *, path=None, command=None, timeout=30, kwargs=None):
        nonlocal sequence
        sequence += 1
        request = tmp_path / f"quality-native-{sequence}.json"
        timeout = min(timeout, budget.max_timeout_seconds)
        request.write_text(json.dumps({"operation": operation, "path": str(path) if path is not None else None,
            "command": command, "timeout": timeout, "kwargs": kwargs or {}, "budget": asdict(budget)}))
        try:
            with granted_media(paths=() if path is None else (path,)) as context:
                result = run_granted_command(context.scheduler, context.grant,
                    [sys.executable, "-c", NATIVE_PROGRAM, str(request)],
                    timeout_seconds=budget.max_timeout_seconds,
                    max_stdout_bytes=budget.max_report_bytes, max_stderr_bytes=budget.max_stderr_bytes,
                    cancel_event=context.cancel_event)
                if result.returncode:
                    raise subprocess.CalledProcessError(result.returncode,
                        [sys.executable, "-c", NATIVE_PROGRAM, str(request)], result.stdout, result.stderr)
                value = json.loads(result.stdout)
                if "quality_error" in value:
                    raise QualityCheckError(value["quality_error"])
                return value
        finally:
            request.unlink(missing_ok=True)

    def command(argv, *, check, capture_output, timeout):
        assert capture_output
        value = invoke("command", command=argv, timeout=timeout)
        result = subprocess.CompletedProcess(argv, value["returncode"],
            base64.b64decode(value["stdout"]), base64.b64decode(value["stderr"]))
        if check:
            result.check_returncode()
        return result

    def inspect(path, **kwargs):
        report = invoke("inspect_media_quality", path=path, kwargs=kwargs)["report"]
        return QualityReport(**{**report,
            "findings": tuple(QualityFinding(**item) for item in report["findings"]),
            "unavailable": tuple(QualityUnavailable(**item) for item in report["unavailable"])})

    def detector(operation, path, **kwargs):
        value = invoke(operation, path=path, kwargs=kwargs)
        return ([QualityFinding(**item) for item in value["findings"]],
                [QualityUnavailable(**item) for item in value["unavailable"]])

    # The API's quality grant requires the real probe and runtime-limit path.
    # Only native-pressure policy is an explicit test boundary; memory/disk and
    # child RSS/thread observations still use actual measurements.
    resources = HostResourceProbe(tmp_path / "studio.sqlite", disk_paths=(tmp_path,),
        budget=HostBudget(100, 1024**2, None, 1024**2, None, budget.max_input_bytes,
            sample_seconds=.01, quality=budget), memory_monitor=IsolatedMemoryMonitor())
    return SimpleNamespace(budget=budget, worker=run_quality_worker, resources=resources,
        processes=HostProcessProbe(), command=command, inspect_media_quality=inspect,
        detect_truncation=lambda path, **kwargs: detector("detect_truncation", path, **kwargs),
        detect_timestamp_offset=lambda path, **kwargs: detector("detect_timestamp_offset", path, **kwargs))
