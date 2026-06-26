from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.same_style_publish_job import create_same_style_publish_job, read_same_style_publish_job
from app.schemas import SameStylePublishJobRequest


def write_bgm_manifest(draft_root: Path, video_path: Path, job_id: str = "bgm_job") -> Path:
    workspace = draft_root / "_same_style_bgm_jobs" / job_id
    workspace.mkdir(parents=True)
    manifest_path = workspace / "bgm_job_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": job_id,
                "module_name": "bgm_mix_job",
                "status": "completed",
                "message": "ok",
                "digital_human_job_id": "digital_job",
                "video_job_id": None,
                "source_video_path": str(video_path),
                "bgm_source": "none",
                "bgm_path": None,
                "mixed_video_path": str(video_path),
                "strategy": "none",
                "volume": 0.18,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def write_cover_manifest(draft_root: Path, cover_path: Path, job_id: str = "cover_job") -> Path:
    workspace = draft_root / "_same_style_cover_jobs" / job_id
    workspace.mkdir(parents=True)
    manifest_path = workspace / "cover_job_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": job_id,
                "module_name": "cover_artifact_writer",
                "status": "completed",
                "message": "ok",
                "reference_job_id": "reference_job",
                "publish_copy_job_id": "publish_copy_job",
                "cover_path": str(cover_path),
                "cover_source": "pixel_template",
                "cover_copy": "封面",
                "template_id": "pixel_diary",
                "width": 1200,
                "height": 900,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def write_publish_copy_manifest(draft_root: Path, job_id: str = "publish_copy_job") -> Path:
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
                "reference_job_id": "reference_job",
                "platform": "douyin",
                "audience": "",
                "title_options": ["AI视频日记爆款复刻"],
                "tags": ["#AI视频日记", "#同款复刻"],
                "publish_copy": "发布文案",
                "cover_copy": "封面",
                "comment_prompt": "评论引导",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def write_subtitle_manifest(
    draft_root: Path,
    subtitle_path: Path,
    job_id: str = "subtitle_job",
) -> Path:
    workspace = draft_root / "_same_style_subtitle_jobs" / job_id
    workspace.mkdir(parents=True)
    manifest_path = workspace / "subtitle_job_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": job_id,
                "module_name": "subtitle_artifact_writer",
                "status": "completed",
                "message": "ok",
                "reference_job_id": "reference_job",
                "audio_job_id": "audio_job",
                "subtitle_path": str(subtitle_path),
                "subtitle_source": "audio_job_text",
                "style": "douyin_clear",
                "font_name": "PingFang SC",
                "font_color": "#fff4d8",
                "outline_color": "#111111",
                "max_duration_seconds": 60,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_same_style_publish_job_writes_manual_review_package(tmp_path):
    video_path = tmp_path / "mixed.mp4"
    cover_path = tmp_path / "cover.png"
    video_path.write_bytes(b"video")
    cover_path.write_bytes(b"cover")
    write_bgm_manifest(tmp_path, video_path)
    write_cover_manifest(tmp_path, cover_path)
    write_publish_copy_manifest(tmp_path)

    result = create_same_style_publish_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStylePublishJobRequest(
            bgm_job_id="bgm_job",
            cover_job_id="cover_job",
            publish_copy_job_id="publish_copy_job",
            platforms=["douyin", "xiaohongshu"],
        ),
        job_id_factory=lambda: "publish_job",
    )

    assert result.status == "ready_for_review"
    assert result.final_video_path == str(video_path)
    assert result.cover_path == str(cover_path)
    assert result.title == "AI视频日记爆款复刻"
    assert result.platforms == ["douyin", "xiaohongshu"]
    assert Path(result.manifest_path).is_file()

    loaded = read_same_style_publish_job(Settings(draft_output_path=str(tmp_path)), "publish_job")
    assert loaded.status == "ready_for_review"


def test_same_style_publish_job_burns_subtitle_before_review_package(tmp_path):
    video_path = tmp_path / "mixed.mp4"
    subtitle_path = tmp_path / "subtitle.srt"
    cover_path = tmp_path / "cover.png"
    video_path.write_bytes(b"video")
    subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\n字幕\n", encoding="utf-8")
    cover_path.write_bytes(b"cover")
    write_bgm_manifest(tmp_path, video_path)
    write_subtitle_manifest(tmp_path, subtitle_path)
    write_cover_manifest(tmp_path, cover_path)
    write_publish_copy_manifest(tmp_path)
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> None:
        calls.append(command)
        Path(command[-1]).write_bytes(b"final video")

    result = create_same_style_publish_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStylePublishJobRequest(
            bgm_job_id="bgm_job",
            subtitle_job_id="subtitle_job",
            cover_job_id="cover_job",
            publish_copy_job_id="publish_copy_job",
            platforms=["douyin"],
        ),
        runner=fake_runner,
        job_id_factory=lambda: "publish_job_subtitled",
    )

    assert result.status == "ready_for_review"
    assert result.subtitle_job_id == "subtitle_job"
    assert result.final_video_path is not None
    assert Path(result.final_video_path).name == "final_video.mp4"
    assert Path(result.final_video_path).is_file()
    assert "subtitles=" in " ".join(calls[0])


def test_same_style_publish_job_fails_without_final_video(tmp_path):
    write_publish_copy_manifest(tmp_path)

    result = create_same_style_publish_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStylePublishJobRequest(publish_copy_job_id="publish_copy_job"),
        job_id_factory=lambda: "publish_job_missing_video",
    )

    assert result.status == "failed"
    assert "最终视频" in result.message


def test_same_style_publish_job_routes_create_and_read_job(tmp_path):
    video_path = tmp_path / "mixed.mp4"
    cover_path = tmp_path / "cover.png"
    video_path.write_bytes(b"video")
    cover_path.write_bytes(b"cover")
    write_bgm_manifest(tmp_path, video_path)
    write_cover_manifest(tmp_path, cover_path)
    write_publish_copy_manifest(tmp_path)
    main_module.settings = Settings(draft_output_path=str(tmp_path))

    client = TestClient(main_module.app)
    response = client.post(
        "/api/same-style/publish-jobs",
        json={
            "bgm_job_id": "bgm_job",
            "cover_job_id": "cover_job",
            "publish_copy_job_id": "publish_copy_job",
            "platforms": ["douyin"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready_for_review"
    assert body["module_name"] == "social_auto_upload_adapter"

    read_response = client.get(f"/api/same-style/publish-jobs/{body['id']}")
    assert read_response.status_code == 200
    assert read_response.json()["title"] == "AI视频日记爆款复刻"
