from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.config import Settings
from app.same_style_cover_job import create_same_style_cover_job, read_same_style_cover_job
from app.schemas import SameStyleCoverJobRequest


def write_reference_manifest(draft_root: Path, reference_job_id: str = "reference_job") -> Path:
    workspace = draft_root / "_same_style_jobs" / reference_job_id
    workspace.mkdir(parents=True)
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
                "script_draft": {
                    "hook": "三秒看懂 AI 视频日记",
                    "sections": [
                        {
                            "title": "展示步骤",
                            "estimated_seconds": 10,
                            "content": "先贴链接，再换产品。",
                        }
                    ],
                    "title_options": ["AI视频日记爆款复刻"],
                    "cover_copy": "同款结构\nAI视频日记\n追爆",
                    "publish_copy": "发布文案",
                    "comment_prompt": "你想复刻哪条？",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def write_publish_copy_manifest(
    draft_root: Path,
    *,
    job_id: str = "publish_copy_job",
    reference_job_id: str = "reference_job",
) -> Path:
    workspace = draft_root / "_same_style_publish_copy_jobs" / job_id
    workspace.mkdir(parents=True)
    manifest_path = workspace / "publish_copy_job_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": job_id,
                "module_name": "publish_copy_generator",
                "status": "completed",
                "message": "ok",
                "reference_job_id": reference_job_id,
                "platform": "douyin",
                "audience": "",
                "title_options": ["AI视频日记爆款复刻"],
                "tags": ["#同款复刻"],
                "publish_copy": "发布文案",
                "cover_copy": "封面主标题\n封面副标题\nAI观察",
                "comment_prompt": "评论引导",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_same_style_cover_job_generates_png_from_publish_copy_job(tmp_path):
    write_reference_manifest(tmp_path)
    write_publish_copy_manifest(tmp_path)

    result = create_same_style_cover_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStyleCoverJobRequest(
            reference_job_id="reference_job",
            publish_copy_job_id="publish_copy_job",
            template_id="pixel_diary",
        ),
        job_id_factory=lambda: "cover_job",
    )

    assert result.status == "completed"
    assert result.cover_source == "pixel_template"
    assert result.cover_copy.startswith("封面主标题")
    with Image.open(result.cover_path) as image:
        assert image.size == (1200, 900)

    loaded = read_same_style_cover_job(Settings(draft_output_path=str(tmp_path)), "cover_job")
    assert loaded.width == 1200


def test_same_style_cover_job_falls_back_to_reference_cover_copy(tmp_path):
    write_reference_manifest(tmp_path)

    result = create_same_style_cover_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStyleCoverJobRequest(reference_job_id="reference_job"),
        job_id_factory=lambda: "cover_job_reference",
    )

    assert result.status == "completed"
    assert result.cover_copy.startswith("同款结构")
    assert Path(result.cover_path).is_file()


def test_same_style_cover_job_routes_create_and_read_job(tmp_path):
    write_reference_manifest(tmp_path)
    main_module.settings = Settings(draft_output_path=str(tmp_path))

    client = TestClient(main_module.app)
    response = client.post(
        "/api/same-style/cover-jobs",
        json={"reference_job_id": "reference_job"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["module_name"] == "cover_artifact_writer"

    read_response = client.get(f"/api/same-style/cover-jobs/{body['id']}")
    assert read_response.status_code == 200
    assert read_response.json()["cover_source"] == "pixel_template"
