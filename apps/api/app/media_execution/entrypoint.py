"""Explicit seam for the future API/Worker assembly.

The current host still owns route registration and device scheduling. This
adapter keeps that wiring outside the media execution package while making the
connection factory and output root explicit for the eventual owner binding.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from .contracts import EditPlan
from .layer import MediaExecutionLayer
from .worker import FfmpegWorker


class MediaExecutionEntrypoint:
    def __init__(self, layer: MediaExecutionLayer) -> None:
        self.layer = layer

    def capabilities(self) -> dict[str, object]:
        return self.layer.capabilities()

    def submit(self, plan: EditPlan | dict) -> dict[str, object]:
        return self.layer.submit(plan)

    def cancel(self, job_id: str, workspace_id: str | None = None) -> dict[str, object]:
        return self.layer.cancel(job_id, workspace_id)

    def read(self, workspace_id: str, job_id: str):
        return self.layer.read(workspace_id, job_id)

    def resume(self, job_id: str, workspace_id: str | None = None) -> dict[str, object]:
        return self.layer.resume(job_id, workspace_id)

    def retry(self, job_id: str, workspace_id: str | None = None) -> dict[str, object]:
        return self.layer.retry(job_id, workspace_id)


def build_media_execution_entrypoint(
    *, repository, output_root: str | Path,
    connection_factory: Callable[[], sqlite3.Connection],
    worker: FfmpegWorker | None = None,
) -> MediaExecutionEntrypoint:
    """Build the independently testable owner without registering routes.

    The future bootstrap owner must provide the repository's public connection
    factory. It then binds this entrypoint beside the existing TaskMediaJobs
    owner and schedules work through the existing creation-job device owner.
    """
    return MediaExecutionEntrypoint(MediaExecutionLayer(
        repository, output_root, worker=worker, connection_factory=connection_factory))
