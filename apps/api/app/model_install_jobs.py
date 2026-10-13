from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

class ModelInstallError(RuntimeError):
    pass


@dataclass
class InstallWriter:
    store: "InstallJobStore"
    handle: BinaryIO

    def close(self) -> None:
        self.handle.close()  # Closing the fd releases flock; the lock file is retained.


class InstallJobStore:
    """One write direction for existing .install-jobs JSON and recovery."""
    def __init__(self, model_root: Path) -> None:
        self.jobs_path = model_root / ".install-jobs"

    def claim_writer(self) -> InstallWriter | None:
        if os.name != "posix":
            raise ModelInstallError("当前平台不能取得模型安装作业写锁，未开始安装或恢复。")
        import fcntl
        self.jobs_path.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.jobs_path / ".writer.lock", os.O_CREAT | os.O_RDWR, 0o600)
        handle = os.fdopen(descriptor, "a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        except BaseException:
            handle.close()
            raise
        return InstallWriter(self, handle)

    def get(self, job_id: str) -> dict[str, object]:
        safe_id = _safe_job_id(job_id)
        path = self.jobs_path / f"{safe_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"找不到模型安装任务：{safe_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ModelInstallError("模型安装任务记录已损坏。")
        return payload

    def write(self, job: dict[str, object], writer: InstallWriter) -> None:
        self._require_writer(writer)
        job_id = _safe_job_id(str(job["id"]))
        _atomic_json(self.jobs_path / f"{job_id}.json", job)

    def update(self, job_id: str, writer: InstallWriter, **updates: object) -> dict[str, object]:
        self._require_writer(writer)
        job = self.get(job_id)
        job.update(updates)
        job["updated_at"] = _utc_now()
        self.write(job, writer)
        return job

    def recover_interrupted(self, writer: InstallWriter) -> list[str]:
        self._require_writer(writer)
        recovered = []
        for path in self.jobs_path.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job.get("status") in {"queued", "running"}:
                    job.update({"status": "interrupted", "phase": "interrupted",
                        "message": "上次安装被中断；重新安装会从已下载断点继续。", "updated_at": _utc_now()})
                    self.write(job, writer)
                    recovered.append(str(job["id"]))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return recovered

    def _require_writer(self, writer: InstallWriter | None) -> None:
        if writer is None or writer.store is not self or writer.handle.closed:
            raise ModelInstallError("模型安装作业写入责任已释放，未写入状态。")


def _safe_job_id(job_id: str) -> str:
    if not job_id or any(char not in "0123456789abcdef" for char in job_id.lower()):
        raise ValueError("模型安装任务 ID 无效。")
    return job_id.lower()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
