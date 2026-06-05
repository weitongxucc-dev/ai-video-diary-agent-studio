from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Lock


class VideoJobRunner:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="ai-video-job",
        )
        self._lock = Lock()
        self._active_episode_ids: set[str] = set()

    def is_active(self, episode_id: str) -> bool:
        with self._lock:
            return episode_id in self._active_episode_ids

    def submit(self, episode_id: str, job: Callable[[], None]) -> bool:
        with self._lock:
            if episode_id in self._active_episode_ids:
                return False
            self._active_episode_ids.add(episode_id)
        self._executor.submit(self._run, episode_id, job)
        return True

    def _run(self, episode_id: str, job: Callable[[], None]) -> None:
        try:
            job()
        finally:
            with self._lock:
                self._active_episode_ids.discard(episode_id)
