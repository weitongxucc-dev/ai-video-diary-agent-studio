from __future__ import annotations

from pathlib import Path

import httpx

from app.same_style_provider_tasks import (
    download_provider_media,
    fetch_provider_status,
    normalize_provider_status,
    provider_value,
)


def test_normalize_provider_status_accepts_common_remote_states():
    assert normalize_provider_status("succeeded") == "completed"
    assert normalize_provider_status("processing") == "submitted"
    assert normalize_provider_status("queued") == "submitted"
    assert normalize_provider_status("error") == "failed"
    assert normalize_provider_status("unexpected") == "submitted"


def test_provider_value_reads_nested_payload_fields():
    payload = {
        "data": {
            "task": {
                "id": "remote-task",
                "result": {"video_url": "https://cdn.example.com/video.mp4"},
            }
        }
    }

    assert provider_value(payload, ["data.task.id"]) == "remote-task"
    assert provider_value(payload, ["data.task.result.video_url"]) == "https://cdn.example.com/video.mp4"


def test_download_provider_media_downloads_remote_url_to_workspace(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://cdn.example.com/voice.wav"
        return httpx.Response(200, content=b"voice-bytes")

    transport = httpx.MockTransport(handler)
    result = download_provider_media(
        "https://cdn.example.com/voice.wav",
        tmp_path,
        "voice.wav",
        transport=transport,
    )

    assert result == tmp_path / "voice.wav"
    assert result.read_bytes() == b"voice-bytes"


def test_download_provider_media_returns_existing_local_path(tmp_path):
    local_media = tmp_path / "generated.mp4"
    local_media.write_bytes(b"video")

    result = download_provider_media(str(local_media), tmp_path, "video.mp4")

    assert result == local_media


def test_fetch_provider_status_accepts_task_id_placeholder():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ark.example.com/tasks/remote-task"
        return httpx.Response(200, json={"status": "succeeded"})

    result = fetch_provider_status(
        status_url_template="https://ark.example.com/tasks/{task_id}",
        provider_job_id="remote-task",
        api_key="provider-key",
        transport=httpx.MockTransport(handler),
    )

    assert result == {"status": "succeeded"}
