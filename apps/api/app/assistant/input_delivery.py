"""Join Pi safe points to the product's persistent input receipts."""
from __future__ import annotations
from collections.abc import Callable
from app.assistant.inputs import AssistantInputConflict


class AssistantInputDelivery:
    def __init__(self, repository, *, conversation_id: str, workspace_id: str,
                 member_id: str, turn_id: str, verify: Callable[[dict], dict] | None = None, consume=None):
        self.repository = repository
        self.owner = (conversation_id, workspace_id, member_id)
        self.turn_id = turn_id
        self.verify = verify
        self.consume=consume
        self.reserved_input: str | None = None

    def __call__(self, phase: str, input_id: str | None = None, mode: str | None = None, *, cancel_event=None, deadline_monotonic=None):
        rows = self.repository.list_inputs(*self.owner)
        if phase == "next":
            if self.reserved_input is not None:
                return None
            item = next((row for row in rows if row["target_turn_id"] == self.turn_id
                         and row["status"] == "accepted" and (mode == "any" or row["mode"] == mode)), None)
            if item is None:
                return None
            self.reserved_input = item["input_id"]
            return {key: item[key] for key in ("input_id", "message", "mode")}
        item = next((row for row in rows if row["input_id"] == input_id
                     and row["target_turn_id"] == self.turn_id), None)
        if phase == "consume":
            if input_id != self.reserved_input:
                raise AssistantInputConflict("SDK 安全点不属于已递送的追加输入。")
            self.reserved_input = None
            if item is None or item["status"] != "accepted":
                return None
            try:
                verified = (self.consume(item,cancel_event=cancel_event,deadline_monotonic=deadline_monotonic)
                    if self.consume is not None else self.verify(item))
            except (ValueError, PermissionError, KeyError) as error:
                try:
                    self.repository.transition_input(*self.owner, input_id, "rejected", reason=str(error))
                except AssistantInputConflict:
                    # Withdrawal or turn settlement already supplied the receipt.
                    pass
                return None
            if self.consume is not None:
                return verified  # Its owner transaction already consumed and sealed this input.
            try:
                self.repository.transition_input(*self.owner, input_id, "consumed")
            except AssistantInputConflict:
                # A withdrawal won the transaction while authorization was read.
                return None
            return verified
        if phase == "complete":
            if item is not None and item["status"] == "consumed":
                self.repository.transition_input(*self.owner, input_id, "completed")
            return None
        raise AssistantInputConflict("未知的 SDK 输入安全点。")
