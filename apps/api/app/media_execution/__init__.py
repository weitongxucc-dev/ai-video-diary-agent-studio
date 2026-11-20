"""Workspace-bound FFmpeg execution for CreationTask media plans."""

from .capabilities import capability_catalog, detect_dependencies
from .contracts import EditPlan, MediaExecutionError, MediaJob, MediaOperation
from .layer import MediaExecutionLayer

__all__ = [
    "EditPlan",
    "MediaExecutionError",
    "MediaJob",
    "MediaExecutionLayer",
    "MediaOperation",
    "capability_catalog",
    "detect_dependencies",
]
