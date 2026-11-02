from app.content_identity_http import ContentIdentityHttpAdapter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError

import app.main as main
from app.schemas import VoicePreviewRequest


@pytest.mark.parametrize("language", [None, "zh", "en"])
def test_voice_preview_request_passes_explicit_language_without_inference(monkeypatch, language):
    body = {"voice_profile_id": "voice", "voice_version": "1", "preview_text": "中文 English"}
    if language is not None:
        body["language"] = language
    payload = VoicePreviewRequest.model_validate(body)
    boundary = Mock(side_effect=HTTPException(409, "captured before model execution"))
    monkeypatch.setattr(main, "voice_preview_service", SimpleNamespace(request=boundary))
    monkeypatch.setattr(main, "current_workspace_id", lambda request: "workspace")
    monkeypatch.setattr(ContentIdentityHttpAdapter, 'reader', lambda adapter, request, workspace: ("member", False))
    monkeypatch.setattr(main, "_require_permit_actor", lambda request, workspace:
        SimpleNamespace(identity_payload=lambda: {"member_id": "member"}))
    monkeypatch.setattr(main, "_creation_runtime", lambda: object())
    with pytest.raises(HTTPException, match="captured before model execution"):
        router = main._media_preview_http_router()
        endpoint = next(route.endpoint for route in router.routes if route.name == "request_voice_preview")
        endpoint(Request({"type": "http"}), payload)
    assert boundary.call_args.kwargs["language"] == language


def test_voice_preview_dto_rejects_combined_or_guessed_language():
    with pytest.raises(ValidationError):
        VoicePreviewRequest(voice_profile_id="voice", voice_version="1", preview_text="中文", language="zh-en")
