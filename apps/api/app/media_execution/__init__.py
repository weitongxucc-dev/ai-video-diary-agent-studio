"""Workspace-bound FFmpeg execution for CreationTask media plans."""

from .capabilities import capability_catalog, detect_dependencies
from .contracts import (
    EditPlan,
    MediaExecutionCancelled,
    MediaExecutionError,
    MediaJob,
    MediaOperation,
    MediaProcessReleaseUnverified,
)
from .delegate import FFMPEG_OPERATION, TaskMediaExecutionDelegate
from .entrypoint import MediaExecutionEntrypoint, build_media_execution_entrypoint
from .layer import MediaExecutionLayer

__all__ = [
    "FFMPEG_OPERATION",
    "EditPlan",
    "MediaExecutionCancelled",
    "MediaExecutionEntrypoint",
    "MediaExecutionError",
    "MediaExecutionLayer",
    "MediaJob",
    "MediaOperation",
    "MediaProcessReleaseUnverified",
    "TaskMediaExecutionDelegate",
    "build_media_execution_entrypoint",
    "capability_catalog",
    "detect_dependencies",
]
