from __future__ import annotations

import threading

class SessionLocks:
    """Keep the existing per-member/session exclusion without owning turns."""
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def get(self, session_id: str):
        with self._guard:
            return self._locks.setdefault(session_id, threading.Lock())
