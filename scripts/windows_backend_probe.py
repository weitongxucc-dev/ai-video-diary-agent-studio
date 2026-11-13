"""Bound a Windows backend experiment; this does not enable production grants.

Run with the API virtualenv (pywin32 and psutil), after moving caches/temp to
the selected data disk. The command receives no shell interpolation.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time


def run(args):
    import msvcrt
    import psutil
    import win32api
    import win32con
    import win32event
    import win32job
    import win32process

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
    from app.host_memory import sample_host_memory

    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    memory_limit = args.memory_mib * 1024**2
    reserve = args.reserve_mib * 1024**2
    commit_reserve = args.commit_reserve_mib * 1024**2
    started = time.monotonic()
    result = {"command": args.command, "memory_limit_bytes": memory_limit,
              "reserve_bytes": reserve, "commit_reserve_bytes": commit_reserve,
              "timeout_seconds": args.timeout, "priority": args.priority, "status": "starting",
              "peak_working_set_bytes": 0, "peak_processes": 0,
              "peak_threads": 0, "returncode": None, "released": False}
    job = win32job.CreateJobObject(None, "")
    process = thread = None
    try:
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] = (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
            win32job.JOB_OBJECT_LIMIT_JOB_MEMORY |
            win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS |
            win32job.JOB_OBJECT_LIMIT_PRIORITY_CLASS |
            win32job.JOB_OBJECT_LIMIT_AFFINITY)
        limits["BasicLimitInformation"]["ActiveProcessLimit"] = 8
        limits["BasicLimitInformation"]["PriorityClass"] = (
            win32process.NORMAL_PRIORITY_CLASS if args.priority == "normal"
            else win32process.BELOW_NORMAL_PRIORITY_CLASS)
        available_cpus = psutil.Process().cpu_affinity()
        limits["BasicLimitInformation"]["Affinity"] = sum(1 << cpu for cpu in available_cpus[:args.cpus])
        limits["JobMemoryLimit"] = memory_limit
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        sample = sample_host_memory()
        result["initial_memory"] = asdict(sample)

        def pressure_reason(sample):
            if sample.pressure not in {"normal", "warning"} or sample.available_bytes is None or sample.commit_headroom_bytes is None:
                return "native_memory_unavailable_or_critical"
            if sample.available_bytes < reserve:
                return "physical_memory_reserve"
            if sample.commit_headroom_bytes < commit_reserve:
                return "commit_memory_reserve"
            return None

        reason = pressure_reason(sample)
        if reason:
            result.update(status="refused", reason=reason)
            return result
        with (root / "stdout.log").open("wb") as stdout, (root / "stderr.log").open("wb") as stderr, open(os.devnull, "rb") as stdin:
            startup = win32process.STARTUPINFO()
            startup.dwFlags |= win32con.STARTF_USESTDHANDLES
            handles = [msvcrt.get_osfhandle(file.fileno()) for file in (stdin, stdout, stderr)]
            startup.hStdInput, startup.hStdOutput, startup.hStdError = handles
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            try:
                process, thread, pid, _ = win32process.CreateProcess(
                    args.command[0], subprocess.list2cmdline(args.command), None, None, True,
                    win32process.CREATE_SUSPENDED | win32process.CREATE_NO_WINDOW,
                    None, str(Path.cwd()), startup)
            finally:
                for handle in handles:
                    os.set_handle_inheritable(handle, False)
            # No backend code can run before it belongs to the bounded job.
            win32job.AssignProcessToJobObject(job, process)
            result["pid"] = pid
            win32process.ResumeThread(thread)
            result["status"] = "running"
            while True:
                accounting = win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)
                if not accounting["ActiveProcesses"]:
                    result["returncode"] = win32process.GetExitCodeProcess(process)
                    result["status"] = "succeeded" if result["returncode"] == 0 else "failed"
                    break
                pids = win32job.QueryInformationJobObject(job, win32job.JobObjectBasicProcessIdList)
                rss = threads = 0
                for member_pid in pids:
                    try:
                        member = psutil.Process(member_pid)
                        rss += member.memory_info().rss
                        threads += member.num_threads()
                    except psutil.NoSuchProcess:
                        continue
                result["peak_working_set_bytes"] = max(result["peak_working_set_bytes"], rss)
                result["peak_processes"] = max(result["peak_processes"], len(pids))
                result["peak_threads"] = max(result["peak_threads"], threads)
                sample = sample_host_memory()
                result["last_memory"] = asdict(sample)
                reason = pressure_reason(sample)
                if time.monotonic() - started > args.timeout:
                    reason = "timeout"
                if sum((root / name).stat().st_size for name in ("stdout.log", "stderr.log")) > 64 * 1024**2:
                    reason = "log_limit"
                if reason:
                    result.update(status="stopped", reason=reason)
                    break
                time.sleep(0.25)
    except BaseException as exc:
        result.update(status="error", error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        # Also kills descendants when their original parent has already exited.
        win32job.TerminateJobObject(job, 124)
        if process is not None:
            # Assignment itself can fail; the suspended process must still exit.
            if win32process.GetExitCodeProcess(process) == 259:
                win32process.TerminateProcess(process, 124)
            win32event.WaitForSingleObject(process, 5000)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            accounting = win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)
            if accounting["ActiveProcesses"] == 0:
                result["released"] = True
                break
            time.sleep(0.05)
        final_limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        result["peak_job_committed_bytes"] = final_limits["PeakJobMemoryUsed"]
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        for handle in (thread, process, job):
            if handle is not None:
                win32api.CloseHandle(handle)
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--memory-mib", type=int, default=5120)
    parser.add_argument("--reserve-mib", type=int, default=1536)
    parser.add_argument("--commit-reserve-mib", type=int, default=2048)
    parser.add_argument("--cpus", type=int, default=4)
    parser.add_argument("--priority", choices=("below-normal", "normal"), default="below-normal",
                        help="Use normal for builds on a busy host; background experiments remain below-normal")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command.pop(0)
    if sys.platform != "win32" or not args.command or any(not math.isfinite(value) or value <= 0 for value in (
        args.timeout, args.memory_mib, args.reserve_mib, args.commit_reserve_mib, args.cpus)):
        parser.error("Requires Windows, positive bounds and an executable command")
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "succeeded" and result["released"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
