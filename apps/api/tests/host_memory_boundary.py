"""Explicit normal native-pressure boundary for CPU/IO/group tests, not policy acceptance."""
import psutil

from app.host_memory import MemoryDecision, MemorySample
from app.host_resource_probes import SceneRuntimeLimits


class IsolatedMemoryMonitor:
    def check(self, *, system_reserve_bytes, additional_memory_bytes=0, additional_commit_bytes=None):
        required_available_bytes = system_reserve_bytes + additional_memory_bytes
        available = psutil.virtual_memory().available
        try:
            swap = psutil.swap_memory().used
        except (OSError, psutil.Error):
            swap = None
        sample = MemorySample("darwin", 0., "synthetic-test-boundary", "normal", available, None,
            {"pressure": {"value": "normal", "source": "synthetic-test-native-pressure", "unit": "state"},
             "swap_used_bytes": {"value": swap, "unit": "bytes", "source": "test-boundary", "diagnostic_only": True}})
        enough = available >= required_available_bytes
        return MemoryDecision(enough, None if enough else "memory_headroom_insufficient", sample,
                              required_available_bytes, None, 0.)


def isolated_runtime_limits(*args, **kwargs):
    return SceneRuntimeLimits(*args, memory_monitor=IsolatedMemoryMonitor(), **kwargs)
