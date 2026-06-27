from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx

import app.main as main_module
from app.config import Settings
from app.same_style_video_job import SeedanceHttpVideoGenerator
from app.same_style_video_job import create_same_style_video_job
from app.same_style_video_job import read_same_style_video_job
from app.schemas import SameStyleVideoJobRequest


def write_reference_manifest(
    draft_root: Path,
    *,
    reference_job_id: str = "reference_job",
    reference_grid_path: Path | None,
    script_hook: str = "AI视频日记把爆款结构换成自己的版本",
) -> Path:
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
                    "audio_path": str(workspace / "audio_16k.wav"),
                    "subtitle_path": str(workspace / "subtitle.srt"),
                    "reference_grid_path": str(reference_grid_path)
                    if reference_grid_path is not None
                    else None,
                },
                "transcription": {
                    "provider": "fake_asr",
                    "text": "三秒看懂这个产品",
                    "segments": [],
                },
                "shot_manifest": [],
                "reference_frames": [],
                "script_draft": {
                    "hook": script_hook,
                    "sections": [
                        {
                            "title": "复刻节奏",
                            "estimated_seconds": 12,
                            "content": "先给结果，再展示产品使用过程。",
                        }
                    ],
                    "title_options": ["AI视频日记爆款复刻"],
                    "cover_copy": "同款结构",
                    "publish_copy": "发布文案",
                    "comment_prompt": "你想复刻哪条？",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


class FakeSeedanceGenerator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def submit(
        self,
        *,
        reference_grid_path: Path,
        prompt: str,
        aspect_ratio: str,
        duration_seconds: int,
        workspace: Path,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "reference_grid_path": str(reference_grid_path),
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "duration_seconds": duration_seconds,
            }
        )
        return {
            "provider_job_id": "seedance_task_123",
            "status": "submitted",
            "message": "Seedance task submitted",
        }


def test_same_style_video_job_marks_seedance_configuration_required(tmp_path):
    grid_path = tmp_path / "reference_grid.jpg"
    grid_path.write_bytes(b"fake grid")
    write_reference_manifest(tmp_path, reference_grid_path=grid_path)

    result = create_same_style_video_job(
        Settings(draft_output_path=str(tmp_path), seedance_api_key=None),
        SameStyleVideoJobRequest(reference_job_id="reference_job"),
        job_id_factory=lambda: "video_job_config",
    )

    assert result.status == "configuration_required"
    assert result.reference_grid_path == str(grid_path)
    assert "Seedance" in result.message
    assert Path(result.manifest_path).is_file()

    loaded = read_same_style_video_job(
        Settings(draft_output_path=str(tmp_path)),
        "video_job_config",
    )
    assert loaded.status == "configuration_required"
    assert loaded.reference_job_id == "reference_job"


def test_same_style_video_job_fails_when_reference_grid_is_missing(tmp_path):
    write_reference_manifest(tmp_path, reference_grid_path=None)

    result = create_same_style_video_job(
        Settings(draft_output_path=str(tmp_path), seedance_api_key="key"),
        SameStyleVideoJobRequest(reference_job_id="reference_job"),
        generator=FakeSeedanceGenerator(),
        job_id_factory=lambda: "video_job_missing_grid",
    )

    assert result.status == "failed"
    assert "宫格参考图" in result.message


def test_same_style_video_job_submits_with_injected_generator(tmp_path):
    grid_path = tmp_path / "reference_grid.jpg"
    grid_path.write_bytes(b"fake grid")
    write_reference_manifest(tmp_path, reference_grid_path=grid_path)
    generator = FakeSeedanceGenerator()

    result = create_same_style_video_job(
        Settings(draft_output_path=str(tmp_path), seedance_api_key="key"),
        SameStyleVideoJobRequest(
            reference_job_id="reference_job",
            prompt="保留同款镜头节奏，替换成 AI视频日记。",
            duration_seconds=6,
            aspect_ratio="9:16",
        ),
        generator=generator,
        job_id_factory=lambda: "video_job_submitted",
    )

    assert result.status == "submitted"
    assert result.provider_job_id == "seedance_task_123"
    assert generator.calls == [
        {
            "reference_grid_path": str(grid_path),
            "prompt": "保留同款镜头节奏，替换成 AI视频日记。",
            "aspect_ratio": "9:16",
            "duration_seconds": 6,
        }
    ]

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["provider_job_id"] == "seedance_task_123"
    assert manifest["prompt"] == "保留同款镜头节奏，替换成 AI视频日记。"


def test_same_style_video_job_refreshes_submitted_manifest_from_status_endpoint(tmp_path):
    grid_path = tmp_path / "reference_grid.jpg"
    grid_path.write_bytes(b"fake grid")
    write_reference_manifest(tmp_path, reference_grid_path=grid_path)
    workspace = tmp_path / "_same_style_video_jobs" / "video_job_pending"
    workspace.mkdir(parents=True)
    manifest_path = workspace / "video_job_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "video_job_pending",
                "module_name": "seedance_video_job",
                "status": "submitted",
                "message": "submitted",
                "reference_job_id": "reference_job",
                "reference_grid_path": str(grid_path),
                "prompt": "提示词",
                "aspect_ratio": "9:16",
                "duration_seconds": 6,
                "provider_job_id": "remote_video_1",
                "video_path": None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_status_fetcher(provider_job_id: str, workspace: Path) -> dict[str, object]:
        video_path = workspace / "seedance_video.mp4"
        video_path.write_bytes(b"video")
        return {
            "status": "succeeded",
            "message": "done",
            "video_path": str(video_path),
        }

    result = read_same_style_video_job(
        Settings(draft_output_path=str(tmp_path)),
        "video_job_pending",
        status_fetcher=fake_status_fetcher,
    )

    assert result.status == "completed"
    assert result.video_path and Path(result.video_path).is_file()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "completed"


def test_same_style_video_job_routes_create_and_read_config_required_job(tmp_path):
    grid_path = tmp_path / "reference_grid.jpg"
    grid_path.write_bytes(b"fake grid")
    write_reference_manifest(tmp_path, reference_grid_path=grid_path)
    main_module.settings = Settings(draft_output_path=str(tmp_path), seedance_api_key=None)

    client = TestClient(main_module.app)
    response = client.post(
        "/api/same-style/video-jobs",
        json={"reference_job_id": "reference_job"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "configuration_required"
    assert body["module_name"] == "seedance_video_job"

    read_response = client.get(f"/api/same-style/video-jobs/{body['id']}")
    assert read_response.status_code == 200
    assert read_response.json()["reference_job_id"] == "reference_job"


def test_seedance_generator_uses_ark_content_generation_payload(tmp_path):
    grid_path = tmp_path / "reference_grid.jpg"
    grid_path.write_bytes(b"fake grid")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        payload = json.loads(request.content.decode("utf-8"))
        captured["payload"] = payload
        image_url = payload["content"][1]["image_url"]["url"]
        assert image_url.startswith("data:image/jpeg;base64,")
        return httpx.Response(
            200,
            json={
                "id": "seedance-task",
                "status": "queued",
                "message": "queued",
            },
        )

    generator = SeedanceHttpVideoGenerator(
        Settings(
            seedance_api_key="ark-key",
            seedance_base_url="https://ark.cn-beijing.volces.com/api/v3/video_generation",
            seedance_model="doubao-seedance-2-0-260128",
        ),
        transport=httpx.MockTransport(handler),
    )

    result = generator.submit(
        reference_grid_path=grid_path,
        prompt="保留同款镜头节奏",
        aspect_ratio="9:16",
        duration_seconds=6,
        workspace=tmp_path,
    )

    assert captured["url"] == "https://ark.cn-beijing.volces.com/api/v3/video_generation"
    assert captured["authorization"] == "Bearer ark-key"
    assert captured["payload"]["model"] == "doubao-seedance-2-0-260128"
    assert captured["payload"]["content"][0] == {
        "type": "text",
        "text": "保留同款镜头节奏",
    }
    assert captured["payload"]["content"][1]["type"] == "image_url"
    assert captured["payload"]["content"][1]["role"] == "first_frame"
    assert captured["payload"]["ratio"] == "9:16"
    assert captured["payload"]["duration"] == 6
    assert result["provider_job_id"] == "seedance-task"
    assert result["status"] == "submitted"


def test_seedance_job_includes_provider_error_body(tmp_path):
    grid_path = tmp_path / "reference_grid.jpg"
    grid_path.write_bytes(b"fake grid")
    write_reference_manifest(tmp_path, reference_grid_path=grid_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": "ModelNotOpen",
                    "message": "model service is not activated",
                },
            },
            request=request,
        )

    generator = SeedanceHttpVideoGenerator(
        Settings(
            seedance_api_key="ark-key",
            seedance_base_url="https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks",
            seedance_model="doubao-seedance-2-0-260128",
        ),
        transport=httpx.MockTransport(handler),
    )

    result = create_same_style_video_job(
        Settings(draft_output_path=str(tmp_path)),
        SameStyleVideoJobRequest(reference_job_id="reference_job"),
        generator=generator,
    )

    assert result.status == "failed"
    assert "ModelNotOpen" in result.message
    assert "model service is not activated" in result.message


def test_same_style_video_job_reads_ark_video_url_from_nested_output(tmp_path):
    grid_path = tmp_path / "reference_grid.jpg"
    grid_path.write_bytes(b"fake grid")
    write_reference_manifest(tmp_path, reference_grid_path=grid_path)
    workspace = tmp_path / "_same_style_video_jobs" / "video_job_ark"
    workspace.mkdir(parents=True)
    manifest_path = workspace / "video_job_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "video_job_ark",
                "module_name": "seedance_video_job",
                "status": "submitted",
                "message": "submitted",
                "reference_job_id": "reference_job",
                "reference_grid_path": str(grid_path),
                "prompt": "提示词",
                "aspect_ratio": "9:16",
                "duration_seconds": 6,
                "provider_job_id": "seedance-task",
                "video_path": None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_status_fetcher(provider_job_id: str, workspace: Path) -> dict[str, object]:
        video_path = workspace / "seedance_video.mp4"
        video_path.write_bytes(b"video")
        return {
            "status": "succeeded",
            "output": {"video_url": str(video_path)},
        }

    result = read_same_style_video_job(
        Settings(draft_output_path=str(tmp_path)),
        "video_job_ark",
        status_fetcher=fake_status_fetcher,
    )

    assert result.status == "completed"
    assert result.video_path == str(workspace / "seedance_video.mp4")
