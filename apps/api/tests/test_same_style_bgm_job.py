from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.same_style_bgm_job import create_same_style_bgm_job, read_same_style_bgm_job
from app.schemas import SameStyleBgmJobRequest


def write_digital_human_manifest(
    draft_root: Path,
    *,
    job_id: str = "digital_job",
    video_path: Path | None,
) -> Path:
    workspace = draft_root / "_same_style_digital_human_jobs" / job_id
    workspace.mkdir(parents=True)
    manifest_path = workspace / "digital_human_job_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": job_id,
                "module_name": "heygem_digital_human_job",
                "status": "completed" if video_path else "failed",
                "message": "ok",
                "audio_job_id": "audio_job",
                "audio_path": "/tmp/voice.wav",
                "avatar_id": "creator",
                "aspect_ratio": "9:16",
                "provider_job_id": "heygem_task",
                "video_path": str(video_path) if video_path else None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_same_style_bgm_job_fails_without_source_video(tmp_path):
    result = create_same_style_bgm_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStyleBgmJobRequest(digital_human_job_id="missing"),
        job_id_factory=lambda: "bgm_job_missing_video",
    )

    assert result.status == "failed"
    assert "成片视频" in result.message
    assert result.mixed_video_path is None


def test_same_style_bgm_job_can_skip_bgm_and_keep_source_video(tmp_path):
    source_video = tmp_path / "talking_head.mp4"
    source_video.write_bytes(b"video")
    write_digital_human_manifest(tmp_path, video_path=source_video)

    result = create_same_style_bgm_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStyleBgmJobRequest(
            digital_human_job_id="digital_job",
            strategy="none",
        ),
        job_id_factory=lambda: "bgm_job_none",
    )

    assert result.status == "completed"
    assert result.source_video_path == str(source_video)
    assert result.mixed_video_path == str(source_video)
    assert result.bgm_source == "none"

    loaded = read_same_style_bgm_job(Settings(draft_output_path=str(tmp_path)), "bgm_job_none")
    assert loaded.status == "completed"


def test_same_style_bgm_job_runs_ffmpeg_mix_with_bgm_path(tmp_path):
    source_video = tmp_path / "talking_head.mp4"
    source_video.write_bytes(b"video")
    bgm_path = tmp_path / "bgm.mp3"
    bgm_path.write_bytes(b"music")
    write_digital_human_manifest(tmp_path, video_path=source_video)
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> None:
        calls.append(command)
        Path(command[-1]).write_bytes(b"mixed video")

    result = create_same_style_bgm_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStyleBgmJobRequest(
            digital_human_job_id="digital_job",
            strategy="upload",
            bgm_path=str(bgm_path),
            volume=0.22,
        ),
        runner=fake_runner,
        job_id_factory=lambda: "bgm_job_mix",
    )

    assert result.status == "completed"
    assert result.bgm_source == str(bgm_path)
    assert result.mixed_video_path and Path(result.mixed_video_path).is_file()
    assert "amix=inputs=2" in " ".join(calls[0])
    assert "volume=0.22" in " ".join(calls[0])


def test_same_style_bgm_job_selects_first_library_track(tmp_path):
    source_video = tmp_path / "talking_head.mp4"
    source_video.write_bytes(b"video")
    library = tmp_path / "bgm_library"
    library.mkdir()
    (library / "readme.txt").write_text("ignore", encoding="utf-8")
    first_track = library / "ambient.mp3"
    first_track.write_bytes(b"music")
    write_digital_human_manifest(tmp_path, video_path=source_video)
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> None:
        calls.append(command)
        Path(command[-1]).write_bytes(b"mixed video")

    result = create_same_style_bgm_job(
        Settings(draft_output_path=str(tmp_path), bgm_library_path=str(library)),
        SameStyleBgmJobRequest(
            digital_human_job_id="digital_job",
            strategy="library",
            volume=0.2,
        ),
        runner=fake_runner,
        job_id_factory=lambda: "bgm_job_library",
    )

    assert result.status == "completed"
    assert result.bgm_path == str(first_track)
    assert result.bgm_source == "library"
    assert str(first_track) in calls[0]


def test_same_style_bgm_job_resolves_named_library_track(tmp_path):
    source_video = tmp_path / "talking_head.mp4"
    source_video.write_bytes(b"video")
    library = tmp_path / "bgm_library"
    library.mkdir()
    selected_track = library / "launch.wav"
    selected_track.write_bytes(b"music")
    write_digital_human_manifest(tmp_path, video_path=source_video)

    def fake_runner(command: list[str]) -> None:
        Path(command[-1]).write_bytes(b"mixed video")

    result = create_same_style_bgm_job(
        Settings(draft_output_path=str(tmp_path), bgm_library_path=str(library)),
        SameStyleBgmJobRequest(
            digital_human_job_id="digital_job",
            strategy="library",
            bgm_path="launch.wav",
        ),
        runner=fake_runner,
        job_id_factory=lambda: "bgm_job_named_library",
    )

    assert result.status == "completed"
    assert result.bgm_path == str(selected_track)


def test_same_style_bgm_job_routes_create_and_read_job(tmp_path):
    source_video = tmp_path / "talking_head.mp4"
    source_video.write_bytes(b"video")
    write_digital_human_manifest(tmp_path, video_path=source_video)
    main_module.settings = Settings(draft_output_path=str(tmp_path))

    client = TestClient(main_module.app)
    response = client.post(
        "/api/same-style/bgm-jobs",
        json={"digital_human_job_id": "digital_job", "strategy": "none"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["module_name"] == "bgm_mix_job"

    read_response = client.get(f"/api/same-style/bgm-jobs/{body['id']}")
    assert read_response.status_code == 200
    assert read_response.json()["mixed_video_path"] == str(source_video)
