from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.same_style_subtitle_job import (
    create_same_style_subtitle_job,
    read_same_style_subtitle_job,
)
from app.schemas import SameStyleSubtitleJobRequest


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
            "hook": "三秒看懂 AI 视频日记怎么复刻爆款",
            "sections": [
                {
                    "title": "展示步骤",
                    "estimated_seconds": 10,
                    "content": "先贴对标链接，再替换自己的产品卖点。",
                }
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
                "transcription": {"provider": "fake_asr", "text": "原文案", "segments": []},
                "shot_manifest": [],
                "reference_frames": [],
                "script_draft": script_draft,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def write_audio_manifest(
    draft_root: Path,
    *,
    audio_job_id: str = "audio_job",
    reference_job_id: str = "reference_job",
    text: str = "AI视频日记可以把爆款视频结构替换成自己的产品脚本。",
) -> Path:
    workspace = draft_root / "_same_style_audio_jobs" / audio_job_id
    workspace.mkdir(parents=True)
    manifest_path = workspace / "audio_job_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": audio_job_id,
                "module_name": "cosyvoice_adapter",
                "status": "completed",
                "message": "voice generated",
                "reference_job_id": reference_job_id,
                "text": text,
                "voice_id": "creator_voice",
                "speed": 1.0,
                "provider_job_id": "cosyvoice_task_123",
                "audio_path": str(workspace / "voice.wav"),
                "duration_seconds": 12.5,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_same_style_subtitle_job_writes_srt_from_audio_job_text(tmp_path):
    write_reference_manifest(tmp_path)
    write_audio_manifest(tmp_path)

    result = create_same_style_subtitle_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStyleSubtitleJobRequest(
            reference_job_id="reference_job",
            audio_job_id="audio_job",
            style="douyin_clear",
        ),
        job_id_factory=lambda: "subtitle_job_audio",
    )

    assert result.status == "completed"
    assert result.subtitle_source == "audio_job_text"
    assert Path(result.subtitle_path).is_file()
    assert "爆款视频结构" in Path(result.subtitle_path).read_text(encoding="utf-8")

    loaded = read_same_style_subtitle_job(
        Settings(draft_output_path=str(tmp_path)),
        "subtitle_job_audio",
    )
    assert loaded.status == "completed"
    assert loaded.audio_job_id == "audio_job"


def test_same_style_subtitle_job_falls_back_to_reference_script_draft(tmp_path):
    write_reference_manifest(tmp_path)

    result = create_same_style_subtitle_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStyleSubtitleJobRequest(reference_job_id="reference_job"),
        job_id_factory=lambda: "subtitle_job_reference",
    )

    assert result.status == "completed"
    assert result.subtitle_source == "reference_script_draft"
    assert "三秒看懂" in Path(result.subtitle_path).read_text(encoding="utf-8")


def test_same_style_subtitle_job_fails_when_no_subtitle_text_is_available(tmp_path):
    write_reference_manifest(tmp_path, with_script_draft=False)

    result = create_same_style_subtitle_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStyleSubtitleJobRequest(reference_job_id="reference_job"),
        job_id_factory=lambda: "subtitle_job_missing_text",
    )

    assert result.status == "failed"
    assert "字幕文本" in result.message
    assert result.subtitle_path is None


def test_same_style_subtitle_job_routes_create_and_read_job(tmp_path):
    write_reference_manifest(tmp_path)
    write_audio_manifest(tmp_path)
    main_module.settings = Settings(draft_output_path=str(tmp_path))

    client = TestClient(main_module.app)
    response = client.post(
        "/api/same-style/subtitle-jobs",
        json={"reference_job_id": "reference_job", "audio_job_id": "audio_job"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["module_name"] == "subtitle_artifact_writer"

    read_response = client.get(f"/api/same-style/subtitle-jobs/{body['id']}")
    assert read_response.status_code == 200
    assert read_response.json()["subtitle_source"] == "audio_job_text"
