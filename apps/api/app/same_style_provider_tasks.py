from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx


COMPLETED_STATES = {
    "completed",
    "complete",
    "success",
    "succeeded",
    "done",
    "finished",
    "published",
}
FAILED_STATES = {"failed", "failure", "error", "errored", "canceled", "cancelled"}
SUBMITTED_STATES = {
    "submitted",
    "queued",
    "pending",
    "running",
    "processing",
    "in_progress",
    "started",
}


def normalize_provider_status(value: object) -> str:
    status = str(value or "").strip().lower()
    if status in COMPLETED_STATES:
        return "completed"
    if status in FAILED_STATES:
        return "failed"
    if status in SUBMITTED_STATES:
        return "submitted"
    return "submitted"


def provider_value(payload: dict[str, object], keys: list[str]) -> object | None:
    for key in keys:
        value = _value_at_path(payload, key)
        if value not in (None, ""):
            return value
    return None


def download_provider_media(
    value: object,
    workspace: Path,
    output_name: str,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 120.0,
) -> Path | None:
    if value in (None, ""):
        return None
    media_value = str(value)
    parsed = urlparse(media_value)
    if parsed.scheme in {"http", "https"}:
        workspace.mkdir(parents=True, exist_ok=True)
        output_path = workspace / output_name
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.get(media_value)
        raise_for_status_with_body(response)
        output_path.write_bytes(response.content)
        return output_path

    local_path = Path(media_value).expanduser()
    if local_path.is_file():
        return local_path
    return None


def fetch_provider_status(
    *,
    status_url_template: str | None,
    provider_job_id: str | None,
    api_key: str | None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 60.0,
) -> dict[str, object] | None:
    if not status_url_template or not provider_job_id:
        return None
    status_url = status_url_template.format(
        job_id=provider_job_id,
        task_id=provider_job_id,
        id=provider_job_id,
    )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(transport=transport, timeout=timeout) as client:
        response = client.get(status_url, headers=headers)
    raise_for_status_with_body(response)
    return response.json()


def raise_for_status_with_body(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = response.text.strip()
        if body:
            message = f"{exc} Response body: {body}"
            raise httpx.HTTPStatusError(
                message,
                request=exc.request,
                response=exc.response,
            ) from exc
        raise


def _value_at_path(payload: object, path: str) -> object | None:
    current = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current
