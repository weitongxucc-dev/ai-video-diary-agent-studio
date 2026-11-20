"""Notify a live task turn after the original business owner confirms a save."""

from contextlib import contextmanager
import logging
from threading import Lock

logger = logging.getLogger(__name__)


class WorkspaceSaveNotifications:
    def __init__(self):
        self._lock = Lock()
        self._listeners = {}

    @contextmanager
    def listen(self, workspace_id, member_id, task_id, turn_id, emit):
        key = (workspace_id, member_id, task_id, turn_id)
        with self._lock:
            self._listeners[key] = emit
        try:
            yield
        finally:
            with self._lock:
                if self._listeners.get(key) is emit:
                    del self._listeners[key]

    def publish(self, workspace_id, member_id, task_id, turn_id, event_id):
        key = (workspace_id, member_id, task_id, str(turn_id))
        with self._lock:
            emit = self._listeners.get(key)
            if emit is None:
                return
            try:
                emit({"type": "workspace_saved", "status": "saved", "task_id": task_id, "event_id": event_id})
            except Exception as cause:
                # A streaming failure cannot change the already committed business result.
                logger.error("Could not notify the live task turn of a workspace save (%s)", type(cause).__name__)


def notify_workspace_save(request, workspace_id, member_id, task_id, turn_id, event_id):
    notifications = getattr(request.app.state, "workspace_save_notifications", None)
    if notifications is not None:
        notifications.publish(workspace_id, member_id, task_id, turn_id, event_id)
