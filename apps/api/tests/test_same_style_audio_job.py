from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx

import app.main as main_module
from app.config import Settings
from app.same_style_audio_job import CosyVoiceHttpGenerator
from app.same_style_audio_job import create_same_style_audio_job
from app.same_style_audio_job import read_same_style_audio_job
from app.schemas import SameStyleAudioJobRequest


def write_reference_manifest(
    draft_root: Path,
    *,
    reference_job_id: str = "reference_job",
    with_script_draft: bool = True,
) -> Path:
    workspace = draft_root / "_same_style_jobs" / reference_job_id
    workspace.mkdir(parents=True)
    script_draft = (
        {
            "hook": "AI视频日记把爆款结构换成自己的版本",
            "sections": [
                {
                    "title": "复刻节奏",
                    "estimated_seconds": 12,
                    "content": "先给结果，再展示产品使用过程。",
                },
                {
                    "title": "转化收口",
                    "estimated_seconds": 8,
                    "content": "最后给出明确行动。",
                },
            ],
            "title_options": ["AI视频日记爆款复刻"],
            "cover_copy": "同款结构",
            "publish_copy": "发布文案",
            "comment_prompt": "你想复刻哪条？",
        }
        if with_script_draft
        else None
    )
    manifest_path = workspace / "reference_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": reference_job_id,
                "module_name": "reference_video_importer",
                "status": "transcribed",
                "message": "ok",
                "request": {
                    "reference_url": "benchmark.mp4",
                    "product_name": "AI视频日记",
                    "product_brief": "创作者视频生产工具",
                    "aspect_ratio": "9:16",
                    "rewrite_mode": "auto",
                },
                "source": {
                    "type": "local_video",
                    "reference_url": "benchmark.mp4",
                    "local_video_path": "/tmp/benchmark.mp4",
                    "downloaded_video_path": None,
                },
                "artifacts": {
                    "workspace_path": str(workspace),
                    "audio_path": None,
                    "subtitle_path": None,
                    "reference_grid_path": None,
                },
                "transcription": {
                    "provider": "fake_asr",
                    "text": "三秒看懂这个产品",
                    "segments": [],
                },
                "shot_manifest": [],
                "reference_frames": [],
                "script_draft": script_draft,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


class FakeCosyVoiceGenerator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def submit(
        self,
        *,
        text: str,
        voice_id: str,
        reference_audio_path: Path | None,
        speed: float,
        workspace: Path,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "text": text,
                "voice_id": voice_id,
                "reference_audio_path": str(reference_audio_path) if reference_audio_path else None,
                "speed": speed,
            }
        )
        audio_path = workspace / "voice.wav"
        audio_path.write_bytes(b"fake wav")
        return {
            "status": "completed",
            "message": "voice generated",
            "audio_path": str(audio_path),
            "duration_seconds": 28.5,
        }


def test_same_style_audio_job_marks_cosyvoice_configuration_required(tmp_path):
    write_reference_manifest(tmp_path)

    result = create_same_style_audio_job(
        Settings(draft_output_path=str(tmp_path), cosyvoice_base_url=None),
        SameStyleAudioJobRequest(reference_job_id="reference_job"),
        job_id_factory=lambda: "audio_job_config",
    )

    assert result.status == "configuration_required"
    assert "CosyVoice" in result.message
    assert result.text.startswith("AI视频日记")
    assert Path(result.manifest_path).is_file()

    loaded = read_same_style_audio_job(
        Settings(draft_output_path=str(tmp_path)),
        "audio_job_config",
    )
    assert loaded.status == "configuration_required"
    assert loaded.reference_job_id == "reference_job"


def test_same_style_audio_job_fails_when_script_draft_is_missing(tmp_path):
    write_reference_manifest(tmp_path, with_script_draft=False)

    result = create_same_style_audio_job(
        Settings(draft_output_path=str(tmp_path), cosyvoice_base_url="http://cosyvoice"),
        SameStyleAudioJobRequest(reference_job_id="reference_job"),
        generator=FakeCosyVoiceGenerator(),
        job_id_factory=lambda: "audio_job_missing_script",
    )

    assert result.status == "failed"
    assert "仿写文案" in result.message


def test_same_style_audio_job_generates_audio_with_injected_generator(tmp_path):
    write_reference_manifest(tmp_path)
    generator = FakeCosyVoiceGenerator()

    result = create_same_style_audio_job(
        Settings(draft_output_path=str(tmp_path), cosyvoice_base_url="http://cosyvoice"),
        SameStyleAudioJobRequest(
            reference_job_id="reference_job",
            voice_id="creator_voice",
            speed=1.08,
        ),
        generator=generator,
        job_id_factory=lambda: "audio_job_generated",
    )

    assert result.status == "completed"
    assert result.audio_path and Path(result.audio_path).is_file()
    assert result.duration_seconds == 28.5
    assert generator.calls[0]["voice_id"] == "creator_voice"
    assert "先给结果" in str(generator.calls[0]["text"])

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["audio_path"] == result.audio_path
    assert manifest["duration_seconds"] == 28.5


def test_same_style_audio_job_refreshes_submitted_manifest_from_status_endpoint(tmp_path):
    write_reference_manifest(tmp_path)
    workspace = tmp_path / "_same_style_audio_jobs" / "audio_job_pending"
    workspace.mkdir(parents=True)
    manifest_path = workspace / "audio_job_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "audio_job_pending",
                "module_name": "cosyvoice_adapter",
                "status": "submitted",
                "message": "submitted",
                "reference_job_id": "reference_job",
                "text": "口播文本",
                "voice_id": "creator",
                "speed": 1.0,
                "provider_job_id": "remote_audio_1",
                "audio_path": None,
                "duration_seconds": None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_status_fetcher(provider_job_id: str, workspace: Path) -> dict[str, object]:
        audio_path = workspace / "voice.wav"
        audio_path.write_bytes(b"voice")
        return {
            "status": "succeeded",
            "message": "done",
            "audio_path": str(audio_path),
            "duration_seconds": 11.5,
        }

    result = read_same_style_audio_job(
        Settings(draft_output_path=str(tmp_path)),
        "audio_job_pending",
        status_fetcher=fake_status_fetcher,
    )

    assert result.status == "completed"
    assert result.audio_path and Path(result.audio_path).is_file()
    assert result.duration_seconds == 11.5
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "completed"


def test_same_style_audio_job_routes_create_and_read_config_required_job(tmp_path):
    write_reference_manifest(tmp_path)
    main_module.settings = Settings(draft_output_path=str(tmp_path), cosyvoice_base_url=None)

    client = TestClient(main_module.app)
    response = client.post(
        "/api/same-style/audio-jobs",
        json={"reference_job_id": "reference_job"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "configuration_required"
    assert body["module_name"] == "cosyvoice_adapter"

    read_response = client.get(f"/api/same-style/audio-jobs/{body['id']}")
    assert read_response.status_code == 200
    assert read_response.json()["reference_job_id"] == "reference_job"


def test_cosyvoice_generator_uses_dashscope_speech_synthesizer_payload(tmp_path):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "request_id": "dashscope-request",
                "output": {
                    "audio": {
                        "url": "https://cdn.example.com/voice.wav",
                        "id": "audio-id",
                    }
                },
            },
        )

    generator = CosyVoiceHttpGenerator(
        Settings(
            cosyvoice_api_key="dashscope-key",
            cosyvoice_base_url="https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
            cosyvoice_model="cosyvoice-v3.5-flash",
            cosyvoice_voice_id="longan_v3",
        ),
        transport=httpx.MockTransport(handler),
    )

    result = generator.submit(
        text="三秒看懂这个产品",
        voice_id="longan_v3",
        reference_audio_path=None,
        speed=1.0,
        workspace=tmp_path,
    )

    assert captured["url"] == "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
    assert captured["authorization"] == "Bearer dashscope-key"
    assert captured["payload"] == {
        "model": "cosyvoice-v3.5-flash",
        "input": {
            "text": "三秒看懂这个产品",
            "voice": "longan_v3",
            "format": "wav",
            "sample_rate": 24000,
        },
    }
    assert result["provider_job_id"] == "dashscope-request"
    assert result["status"] == "completed"
    assert result["audio_url"] == "https://cdn.example.com/voice.wav"


def test_same_style_audio_job_reads_dashscope_audio_url_from_nested_output(tmp_path):
    write_reference_manifest(tmp_path)
    workspace = tmp_path / "_same_style_audio_jobs" / "audio_job_dashscope"
    workspace.mkdir(parents=True)
    manifest_path = workspace / "audio_job_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "audio_job_dashscope",
                "module_name": "cosyvoice_adapter",
                "status": "submitted",
                "message": "submitted",
                "reference_job_id": "reference_job",
                "text": "口播文本",
                "voice_id": "creator",
                "speed": 1.0,
                "provider_job_id": "dashscope-request",
                "audio_path": None,
                "duration_seconds": None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_status_fetcher(provider_job_id: str, workspace: Path) -> dict[str, object]:
        audio_path = workspace / "voice.wav"
        audio_path.write_bytes(b"voice")
        return {
            "status": "succeeded",
            "output": {"audio": {"url": str(audio_path)}},
        }

    result = read_same_style_audio_job(
        Settings(draft_output_path=str(tmp_path)),
        "audio_job_dashscope",
        status_fetcher=fake_status_fetcher,
    )

    assert result.status == "completed"
    assert result.audio_path == str(workspace / "voice.wav")
