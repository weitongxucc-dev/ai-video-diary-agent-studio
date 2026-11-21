"""Workspace-bound FFmpeg execution for CreationTask media plans."""

from .capabilities import capability_catalog, detect_dependencies
from .contracts import EditPlan, MediaExecutionError, MediaJob, MediaOperation
from .entrypoint import MediaExecutionEntrypoint, build_media_execution_entrypoint
from .layer import MediaExecutionLayer

__all__ = [
    "EditPlan",
    "MediaExecutionError",
    "MediaJob",
    "MediaExecutionLayer",
    "MediaOperation",
    "MediaExecutionEntrypoint",
    "build_media_execution_entrypoint",
    "capability_catalog",
    "detect_dependencies",
]
