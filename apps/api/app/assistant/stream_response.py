"""Assistant SSE protocol, safe incremental text and reload-aware worker lifetime."""
from __future__ import annotations

import json
import queue
import re
import threading

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.assistant.projection import PublicReasoningFilter, _safe_compaction_event, public_event_at, public_tool_event
from app.schemas import AssistantStreamEvent

class AssistantStreamTextSanitizer:
    """Hold only suffixes that could complete a known secret or sensitive marker."""

    _markers = (
        "authorization: bearer ", "api_key=", "api_key: ", "api-key=", "api-key: ",
        "api key=", "api key: ", "token=", "token: ", "password=", "password: ",
        "secret=", "secret: ", "sk-", "file://", "/users/", "/volumes/", "/private/", "/tmp/", "/var/folders/",
    )
    _sensitive_patterns = (
        re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+"),
        re.compile(r"(?i)\b(?:api[_ -]?key|token|password|secret)\s*[:=]\s*\S+"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        re.compile(r"(?:file://)?(?:/Users/|/Volumes/|/private/|/tmp/|/var/folders/)[^\s,;\"']+", re.IGNORECASE),
    )

    def __init__(self, workspace_id: str, *, known_secrets, safe_text) -> None:
        self._workspace_id = workspace_id
        self._pending = ""
        self._reasoning = PublicReasoningFilter()
        self._text_at: str | None = None
        self._safe_text = safe_text
        self._known_secrets = tuple(secret for secret in known_secrets if secret and len(secret) >= 8)

    def feed(self, delta: str, *, final: bool = False) -> str:
        combined = self._pending + self._reasoning.feed(delta, final=final)
        if final:
            self._pending = ""
            return self._safe_text(combined, self._workspace_id)

        safe_end = len(combined)
        lowered = combined.lower()
        for marker in self._markers:
            max_length = min(len(marker), len(lowered))
            for length in range(max_length, 0, -1):
                if lowered.endswith(marker[:length]):
                    safe_end = min(safe_end, len(combined) - length)
                    break

        for secret in self._known_secrets:
            lowered_secret = secret.lower()
            max_length = min(len(lowered_secret), len(lowered))
            for length in range(max_length, 0, -1):
                if lowered.endswith(lowered_secret[:length]):
                    safe_end = min(safe_end, len(combined) - length)
                    break
            offset = combined.find(secret)
            while offset >= 0:
                end = offset + len(secret)
                if offset < safe_end < end:
                    safe_end = offset
                offset = combined.find(secret, offset + 1)

        for pattern in self._sensitive_patterns:
            for match in pattern.finditer(combined):
                if match.start() < safe_end and (match.end() == len(combined) or match.start() < safe_end < match.end()):
                    safe_end = match.start()

        safe_text = combined[:safe_end]
        self._pending = combined[safe_end:]
        return self._safe_text(safe_text, self._workspace_id) if safe_text else ""

    def activity_event(self, event: dict) -> dict | None:
        kind = event.get("type")
        if kind == "text_delta" and isinstance(event.get("delta"), str):
            self._text_at = public_event_at(event.get("at"))
            delta = self.feed(event["delta"])
        elif kind == "text_end":
            delta = self.feed("", final=True)
        else:
            return public_tool_event(event, lambda value: self._safe_text(value, self._workspace_id))
        if delta:
            return {"type": "text_delta", "delta": delta, **({"at": self._text_at} if self._text_at else {})}
        return None


class AssistantStreamTransport:
    def __init__(self, *, conversations_for, runtime_lock, workers,
                 safe_text, known_secrets_for, forget_control, logger):
        self._conversations_for = conversations_for
        self._runtime_lock = runtime_lock
        self._workers = workers
        self._safe_text = safe_text
        self._known_secrets_for = known_secrets_for
        self._forget_control = forget_control
        self._logger = logger

    def sanitizer(self, workspace_id):
        return AssistantStreamTextSanitizer(workspace_id,
            known_secrets=self._known_secrets_for(workspace_id), safe_text=self._safe_text)

    def response(
        self, execute,
        *,
        workspace_id: str,
        turn_id: str | None = None,
    ) -> StreamingResponse:
        events: queue.Queue[dict[str, object] | None] = queue.Queue()
        disconnected = threading.Event()
        text_sanitizer = self.sanitizer(workspace_id)
        turn_ref = {"id": turn_id}

        def publish(raw_event: dict[str, object]) -> None:
            event_type = raw_event.get("type")
            if event_type == 'context_compaction':
                diagnostic = _safe_compaction_event(raw_event)
                if diagnostic is not None and turn_ref['id']:
                    stream_conversation_repo.append_event(turn_ref['id'],diagnostic)
                return  # Diagnostic history does not extend the existing SSE schema.
            if event_type in {"text_delta", "text_end", "tool_start", "tool_end"}:
                event = text_sanitizer.activity_event(raw_event)
                if event is None:
                    return
            elif event_type == "turn_started":
                event = {key: raw_event[key] for key in ("type", "turn_id", "turn_state", "draft_version") if key in raw_event}
                turn_ref["id"] = str(raw_event["turn_id"])
            elif event_type == "turn_status":
                event = {key: raw_event[key] for key in ("type", "turn_id", "turn_state") if key in raw_event}
            else:
                return
            validated = AssistantStreamEvent.model_validate(event)
            safe_event = validated.model_dump(mode="json", exclude_none=True)
            if turn_ref["id"] and validated.type in {"text_delta", "tool_start", "tool_end"}:
                stream_conversation_repo.append_event(turn_ref["id"], safe_event)
            if not disconnected.is_set():
                events.put(safe_event)

        def work() -> None:
            terminal_event: dict[str, object] | None = None
            try:
                result = execute(publish)
                if turn_ref["id"]:
                    publish({"type": "turn_status", "turn_id": turn_ref["id"], "turn_state": getattr(result, "turn_status", "completed")})
                if not disconnected.is_set():
                    terminal_event = AssistantStreamEvent(
                        type="result", result=result.model_dump(mode="json"),
                    ).model_dump(mode="json", exclude_none=True)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else "对话未能完成，请查看当前任务状态。"
                failure = None
                if (isinstance(exc.detail, dict)
                        and exc.detail.get("error") == "creation_task_timeline_version_conflict"
                        and isinstance(exc.detail.get("message"), str)):
                    detail = exc.detail["message"]
                    failure = {"code": "creation_task_timeline_version_conflict"}
                if turn_ref["id"]:
                    self._forget_control(turn_ref["id"])
                    stream_conversation_repo.finish_turn(turn_ref["id"], "failed", result={
                        "kind": "failure", "message": self._safe_text(detail, workspace_id),
                    })
                if not disconnected.is_set():
                    terminal_event = AssistantStreamEvent(
                        type="error", status=exc.status_code,
                        message=self._safe_text(detail, workspace_id), failure=failure,
                    ).model_dump(mode="json", exclude_none=True)
            except Exception as exc:
                if turn_ref["id"]:
                    self._forget_control(turn_ref["id"])
                    stream_conversation_repo.finish_turn(turn_ref["id"], "failed", result={
                        "kind": "failure", "message": "对话服务未能完整返回结果，请查看当前任务状态。",
                    })
                self._logger.error("assistant stream worker failed (%s)", type(exc).__name__)
                if not disconnected.is_set():
                    terminal_event = AssistantStreamEvent(
                        type="error", status=500,
                        message="对话服务未能完整返回结果，请查看当前任务状态。",
                    ).model_dump(exclude_none=True)
            finally:
                publish({"type": "text_end"})
                if terminal_event is not None and not disconnected.is_set():
                    events.put(terminal_event)
                events.put(None)
                with self._runtime_lock:
                    self._workers.discard(thread)

        with self._runtime_lock:
            stream_conversation_repo = self._conversations_for()
            thread = threading.Thread(target=work, name="assistant-stream-response", daemon=True)
            self._workers.add(thread)

        def body():
            if turn_id:
                started = AssistantStreamEvent(type="turn_started", turn_id=turn_id, turn_state="running")
                yield f"data: {json.dumps(started.model_dump(mode='json', exclude_none=True), ensure_ascii=False, separators=(',', ':'))}\n\n"
            thread.start()
            result_seen = False
            error_seen = False
            try:
                while True:
                    event = events.get()
                    if event is None:
                        if not result_seen and not error_seen:
                            incomplete = AssistantStreamEvent(
                                type="error", message="连接中断，未收到完整的对话结果。",
                            ).model_dump(mode="json", exclude_none=True)
                            yield f"data: {json.dumps(incomplete, ensure_ascii=False)}\n\n"
                        break
                    result_seen = result_seen or event.get("type") == "result"
                    error_seen = error_seen or event.get("type") == "error"
                    yield f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
            finally:
                disconnected.set()

        return StreamingResponse(body(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        })
