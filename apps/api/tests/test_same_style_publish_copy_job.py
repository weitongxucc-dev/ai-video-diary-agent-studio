from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.same_style_publish_copy_job import (
    create_same_style_publish_copy_job,
    read_same_style_publish_copy_job,
)
from app.schemas import SameStylePublishCopyJobRequest


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
            "title_options": ["AI视频日记爆款复刻", "一键追爆怎么做"],
            "cover_copy": "同款结构\nAI视频日记\n追爆",
            "publish_copy": "用自己的产品复刻爆款节奏，不复制原素材。",
            "comment_prompt": "你想复刻哪条爆款？",
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


def test_same_style_publish_copy_job_generates_copy_from_script_draft(tmp_path):
    write_reference_manifest(tmp_path)

    result = create_same_style_publish_copy_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStylePublishCopyJobRequest(
            reference_job_id="reference_job",
            platform="douyin",
        ),
        job_id_factory=lambda: "publish_copy_job",
    )

    assert result.status == "completed"
    assert result.title_options[0] == "AI视频日记爆款复刻"
    assert "#同款复刻" in result.tags
    assert "不复制原素材" in result.publish_copy
    assert result.cover_copy.startswith("同款结构")
    assert Path(result.manifest_path).is_file()

    loaded = read_same_style_publish_copy_job(
        Settings(draft_output_path=str(tmp_path)),
        "publish_copy_job",
    )
    assert loaded.comment_prompt == "你想复刻哪条爆款？"


def test_same_style_publish_copy_job_fails_when_script_draft_is_missing(tmp_path):
    write_reference_manifest(tmp_path, with_script_draft=False)

    result = create_same_style_publish_copy_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStylePublishCopyJobRequest(reference_job_id="reference_job"),
        job_id_factory=lambda: "publish_copy_missing_script",
    )

    assert result.status == "failed"
    assert "发布文案" in result.message
    assert result.title_options == []


def test_same_style_publish_copy_job_routes_create_and_read_job(tmp_path):
    write_reference_manifest(tmp_path)
    main_module.settings = Settings(draft_output_path=str(tmp_path))

    client = TestClient(main_module.app)
    response = client.post(
        "/api/same-style/publish-copy-jobs",
        json={"reference_job_id": "reference_job", "platform": "douyin"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["module_name"] == "publish_copy_generator"

    read_response = client.get(f"/api/same-style/publish-copy-jobs/{body['id']}")
    assert read_response.status_code == 200
    assert read_response.json()["title_options"][0] == "AI视频日记爆款复刻"
