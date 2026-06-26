from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.same_style_digital_human_job import (
    create_same_style_digital_human_job,
    read_same_style_digital_human_job,
)
from app.schemas import SameStyleDigitalHumanJobRequest


def write_audio_manifest(
    draft_root: Path,
    *,
    audio_job_id: str = "audio_job",
    reference_job_id: str = "reference_job",
    audio_path: Path | None,
) -> Path:
    workspace = draft_root / "_same_style_audio_jobs" / audio_job_id
    workspace.mkdir(parents=True)
    manifest_path = workspace / "audio_job_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": audio_job_id,
                "module_name": "cosyvoice_adapter",
                "status": "completed" if audio_path else "configuration_required",
                "message": "voice generated" if audio_path else "voice pending",
                "reference_job_id": reference_job_id,
                "text": "AI视频日记把爆款结构换成自己的版本",
                "voice_id": "creator_voice",
                "speed": 1.0,
                "provider_job_id": "cosyvoice_task_123",
                "audio_path": str(audio_path) if audio_path else None,
                "duration_seconds": 12.5,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


class FakeDigitalHumanGenerator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def submit(
        self,
        *,
        audio_path: Path,
        avatar_id: str,
        aspect_ratio: str,
        workspace: Path,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "audio_path": str(audio_path),
                "avatar_id": avatar_id,
                "aspect_ratio": aspect_ratio,
            }
        )
        video_path = workspace / "talking_head.mp4"
        video_path.write_bytes(b"fake mp4")
        return {
            "provider_job_id": "heygem_task_123",
            "status": "completed",
            "message": "digital human video generated",
            "video_path": str(video_path),
        }


def test_same_style_digital_human_job_fails_when_audio_is_missing(tmp_path):
    write_audio_manifest(tmp_path, audio_path=None)

    result = create_same_style_digital_human_job(
        Settings(draft_output_path=str(tmp_path), heygem_base_url="http://heygem"),
        SameStyleDigitalHumanJobRequest(audio_job_id="audio_job"),
        generator=FakeDigitalHumanGenerator(),
        job_id_factory=lambda: "digital_job_missing_audio",
    )

    assert result.status == "failed"
    assert "口播音频" in result.message
    assert result.audio_path is None


def test_same_style_digital_human_job_marks_heygem_configuration_required(tmp_path):
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake wav")
    write_audio_manifest(tmp_path, audio_path=audio_path)

    result = create_same_style_digital_human_job(
        Settings(draft_output_path=str(tmp_path), heygem_base_url=None),
        SameStyleDigitalHumanJobRequest(audio_job_id="audio_job"),
        job_id_factory=lambda: "digital_job_config",
    )

    assert result.status == "configuration_required"
    assert result.audio_path == str(audio_path)
    assert "HeyGem" in result.message
    assert Path(result.manifest_path).is_file()

    loaded = read_same_style_digital_human_job(
        Settings(draft_output_path=str(tmp_path)),
        "digital_job_config",
    )
    assert loaded.status == "configuration_required"
    assert loaded.audio_job_id == "audio_job"


def test_same_style_digital_human_job_submits_with_injected_generator(tmp_path):
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake wav")
    write_audio_manifest(tmp_path, audio_path=audio_path)
    generator = FakeDigitalHumanGenerator()

    result = create_same_style_digital_human_job(
        Settings(draft_output_path=str(tmp_path), heygem_base_url="http://heygem"),
        SameStyleDigitalHumanJobRequest(
            audio_job_id="audio_job",
            avatar_id="creator_avatar",
            aspect_ratio="9:16",
        ),
        generator=generator,
        job_id_factory=lambda: "digital_job_submitted",
    )

    assert result.status == "completed"
    assert result.provider_job_id == "heygem_task_123"
    assert result.video_path and Path(result.video_path).is_file()
    assert generator.calls == [
        {
            "audio_path": str(audio_path),
            "avatar_id": "creator_avatar",
            "aspect_ratio": "9:16",
        }
    ]

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["provider_job_id"] == "heygem_task_123"
    assert manifest["video_path"] == result.video_path


def test_same_style_digital_human_job_routes_create_and_read_config_required_job(
    tmp_path,
):
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake wav")
    write_audio_manifest(tmp_path, audio_path=audio_path)
    main_module.settings = Settings(draft_output_path=str(tmp_path), heygem_base_url=None)

    client = TestClient(main_module.app)
    response = client.post(
        "/api/same-style/digital-human-jobs",
        json={"audio_job_id": "audio_job"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "configuration_required"
    assert body["module_name"] == "heygem_digital_human_job"

    read_response = client.get(f"/api/same-style/digital-human-jobs/{body['id']}")
    assert read_response.status_code == 200
    assert read_response.json()["audio_job_id"] == "audio_job"
