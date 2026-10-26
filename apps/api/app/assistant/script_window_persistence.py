"""Bind a tool's bounded window receipt to its original conversation turn."""
from app.assistant.script_window_receipts import resolve_script_window_request


def record_script_window_receipt(*, conversations, read_original, turn_id,
                                workspace_id, member_id, task_id, value):
    window = resolve_script_window_request(value, task_id=task_id, read_original=read_original)
    return conversations.record_script_window_request(
        turn_id, workspace_id, member_id, task_id, window)
