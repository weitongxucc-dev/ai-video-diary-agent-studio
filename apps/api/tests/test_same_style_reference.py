from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.asr import ASRTranscription, SubtitleSegment
from app.config import Settings
from app.same_style_reference import create_reference_job
from app.same_style_reference import read_reference_job
from app.same_style_reference import YtDlpReferenceDownloader
from app.schemas import SameStyleReferenceJobRequest


class FakeASRTranscriber:
    configured = True

    def transcribe(self, audio_path: str | Path) -> ASRTranscription:
        return ASRTranscription(
            provider="fake_asr",
            text="这个产品三秒就能看懂，先看使用前后对比。",
            segments=[
                SubtitleSegment(
                    start_ms=0,
                    end_ms=2800,
                    text="这个产品三秒就能看懂",
                ),
                SubtitleSegment(
                    start_ms=2800,
                    end_ms=5600,
                    text="先看使用前后对比",
                ),
            ],
        )


class FakeSameStyleScriptGenerator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        request: SameStyleReferenceJobRequest,
        *,
        transcript: str,
        shot_manifest: list[dict[str, object]],
    ) -> dict[str, object]:
        self.calls.append(
            {
                "product_name": request.product_name,
                "transcript": transcript,
                "shot_count": len(shot_manifest),
            }
        )
        return {
            "hook": f"{request.product_name} 的大模型仿写开头",
            "sections": [
                {
                    "title": "同款节奏",
                    "estimated_seconds": 15,
                    "content": "保留原片先给结果的结构，但表达换成自己的产品。",
                }
            ],
            "title_options": [f"{request.product_name} 爆款复刻"],
            "cover_copy": "同款结构\n自有产品",
            "publish_copy": "这是大模型润色后的发布文案。",
            "comment_prompt": "你想复刻哪类爆款？",
        }


def test_reference_job_marks_external_video_links_as_download_required(tmp_path):
    settings = Settings(
        video_inbox_path=str(tmp_path / "inbox"),
        video_output_path=str(tmp_path / "processed"),
        draft_output_path=str(tmp_path / "drafts"),
    )

    result = create_reference_job(
        settings,
        SameStyleReferenceJobRequest(
            reference_url="https://www.douyin.com/video/123",
            product_name="AI视频日记",
            product_brief="面向创作者的视频生产工具",
        ),
        asr_transcriber=FakeASRTranscriber(),
        runner=lambda command: None,
        downloader=lambda reference_url, workspace: None,
        job_id_factory=lambda: "job_external",
    )

    assert result.status == "download_required"
    assert result.source_type == "external_url"
    assert result.audio_path is None
    assert "下载器待接入" in result.message
    assert result.manifest_path is not None
    loaded = read_reference_job(settings, "job_external")
    assert loaded.status == "download_required"
    assert loaded.workspace_path == result.workspace_path


def test_reference_job_downloads_external_video_when_downloader_is_available(tmp_path):
    drafts = tmp_path / "drafts"
    commands: list[list[str]] = []
    downloaded_urls: list[str] = []

    def fake_downloader(reference_url: str, workspace: Path) -> Path:
        downloaded_urls.append(reference_url)
        downloaded_video = workspace / "downloaded_reference.mp4"
        downloaded_video.write_bytes(b"downloaded video")
        return downloaded_video

    def fake_runner(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(b"fake wav")

    result = create_reference_job(
        Settings(
            video_inbox_path=str(tmp_path / "inbox"),
            video_output_path=str(tmp_path / "processed"),
            draft_output_path=str(drafts),
        ),
        SameStyleReferenceJobRequest(
            reference_url="https://www.douyin.com/video/123",
            product_name="AI视频日记",
            product_brief="面向创作者的视频生产工具",
        ),
        asr_transcriber=FakeASRTranscriber(),
        runner=fake_runner,
        downloader=fake_downloader,
        job_id_factory=lambda: "job_downloaded",
    )

    assert downloaded_urls == ["https://www.douyin.com/video/123"]
    assert result.status == "transcribed"
    assert result.source_type == "external_url"
    assert result.local_video_path and result.local_video_path.endswith("downloaded_reference.mp4")
    assert commands[0][3] == result.local_video_path

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["source"]["type"] == "external_url"
    assert manifest["source"]["downloaded_video_path"] == result.local_video_path


def test_yt_dlp_downloader_uses_python_module_when_executable_is_missing(tmp_path):
    commands: list[list[str]] = []

    def fake_runner(command: list[str]) -> None:
        commands.append(command)
        (tmp_path / "reference.mp4").write_bytes(b"downloaded video")

    downloader = YtDlpReferenceDownloader(
        executable_lookup=lambda name: None,
        module_lookup=lambda name: True,
        python_executable="/venv/bin/python",
        runner=fake_runner,
    )

    result = downloader("https://www.douyin.com/video/123", tmp_path)

    assert result == (tmp_path / "reference.mp4").resolve()
    assert commands[0][:3] == ["/venv/bin/python", "-m", "yt_dlp"]
    assert commands[0][-1] == "https://www.douyin.com/video/123"


def test_yt_dlp_downloader_passes_cookie_file_when_configured(tmp_path):
    commands: list[list[str]] = []
    cookies_path = tmp_path / "cookies.txt"
    cookies_path.write_text("# Netscape cookies", encoding="utf-8")

    def fake_runner(command: list[str]) -> None:
        commands.append(command)
        (tmp_path / "reference.mp4").write_bytes(b"downloaded video")

    downloader = YtDlpReferenceDownloader(
        executable_lookup=lambda name: "/usr/local/bin/yt-dlp",
        cookies_path=str(cookies_path),
        runner=fake_runner,
    )

    result = downloader("https://www.douyin.com/video/123", tmp_path)

    assert result == (tmp_path / "reference.mp4").resolve()
    assert "--cookies" in commands[0]
    assert str(cookies_path) in commands[0]


def test_reference_job_writes_failed_manifest_when_local_video_is_invalid(tmp_path):
    settings = Settings(
        video_inbox_path=str(tmp_path / "inbox"),
        video_output_path=str(tmp_path / "processed"),
        draft_output_path=str(tmp_path / "drafts"),
    )

    result = create_reference_job(
        settings,
        SameStyleReferenceJobRequest(
            reference_url="missing.mp4",
            product_name="AI视频日记",
        ),
        asr_transcriber=FakeASRTranscriber(),
        runner=lambda command: None,
        job_id_factory=lambda: "job_missing_local",
    )

    assert result.status == "failed"
    assert result.source_type == "local_video"
    assert "参考视频" in result.message
    assert result.manifest_path is not None
    assert Path(result.manifest_path).is_file()

    loaded = read_reference_job(settings, "job_missing_local")
    assert loaded.status == "failed"
    assert loaded.message == result.message


def test_reference_job_writes_failed_manifest_when_downloader_fails(tmp_path):
    settings = Settings(
        video_inbox_path=str(tmp_path / "inbox"),
        video_output_path=str(tmp_path / "processed"),
        draft_output_path=str(tmp_path / "drafts"),
    )

    def failing_downloader(reference_url: str, workspace: Path) -> Path | None:
        raise RuntimeError("yt-dlp exited with status 1")

    result = create_reference_job(
        settings,
        SameStyleReferenceJobRequest(
            reference_url="https://www.douyin.com/video/123",
            product_name="AI视频日记",
        ),
        asr_transcriber=FakeASRTranscriber(),
        runner=lambda command: None,
        downloader=failing_downloader,
        job_id_factory=lambda: "job_download_failed",
    )

    assert result.status == "failed"
    assert result.source_type == "external_url"
    assert "yt-dlp exited with status 1" in result.message

    loaded = read_reference_job(settings, "job_download_failed")
    assert loaded.status == "failed"
    assert loaded.reference_url == "https://www.douyin.com/video/123"


def test_reference_job_writes_failed_manifest_when_audio_extraction_fails(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source_video = inbox / "benchmark.mp4"
    source_video.write_bytes(b"fake video")
    settings = Settings(
        video_inbox_path=str(inbox),
        video_output_path=str(tmp_path / "processed"),
        draft_output_path=str(tmp_path / "drafts"),
    )

    def failing_runner(command: list[str]) -> None:
        raise RuntimeError("ffmpeg exited with status 1")

    result = create_reference_job(
        settings,
        SameStyleReferenceJobRequest(
            reference_url="benchmark.mp4",
            product_name="AI视频日记",
        ),
        asr_transcriber=FakeASRTranscriber(),
        runner=failing_runner,
        job_id_factory=lambda: "job_audio_failed",
    )

    assert result.status == "failed"
    assert result.local_video_path == str(source_video)
    assert "ffmpeg exited with status 1" in result.message
    assert result.audio_path is None

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["source"]["local_video_path"] == str(source_video)


def test_reference_job_extracts_audio_transcribes_and_writes_manifest(tmp_path):
    inbox = tmp_path / "inbox"
    processed = tmp_path / "processed"
    drafts = tmp_path / "drafts"
    inbox.mkdir()
    processed.mkdir()
    source_video = inbox / "benchmark.mp4"
    source_video.write_bytes(b"fake video")
    commands: list[list[str]] = []

    def fake_runner(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(b"fake wav")

    result = create_reference_job(
        Settings(
            video_inbox_path=str(inbox),
            video_output_path=str(processed),
            draft_output_path=str(drafts),
        ),
        SameStyleReferenceJobRequest(
            reference_url="benchmark.mp4",
            product_name="AI视频日记",
            product_brief="面向创作者的视频生产工具",
            aspect_ratio="9:16",
            rewrite_mode="auto",
        ),
        asr_transcriber=FakeASRTranscriber(),
        runner=fake_runner,
        job_id_factory=lambda: "job_local",
    )

    assert result.status == "transcribed"
    assert result.source_type == "local_video"
    assert result.local_video_path == str(source_video)
    assert result.audio_path and Path(result.audio_path).is_file()
    assert result.subtitle_path and Path(result.subtitle_path).is_file()
    assert result.transcript == "这个产品三秒就能看懂，先看使用前后对比。"
    assert result.script_draft is not None
    assert "AI视频日记" in result.script_draft.hook
    assert result.script_draft.sections[0].title == "复刻节奏"
    assert commands[0][:3] == ["ffmpeg", "-y", "-i"]

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["request"]["product_name"] == "AI视频日记"
    assert manifest["transcription"]["provider"] == "fake_asr"
    assert manifest["shot_manifest"][0]["purpose"] == "口播节奏参考"
    assert manifest["script_draft"]["title_options"][0].startswith("AI视频日记")

    loaded = read_reference_job(
        Settings(
            video_inbox_path=str(inbox),
            video_output_path=str(processed),
            draft_output_path=str(drafts),
        ),
        "job_local",
    )
    assert loaded.id == result.id
    assert loaded.script_draft is not None
    assert loaded.script_draft.publish_copy == result.script_draft.publish_copy

    main_module.settings = Settings(
        video_inbox_path=str(inbox),
        video_output_path=str(processed),
        draft_output_path=str(drafts),
    )
    response = TestClient(main_module.app).get(
        "/api/same-style/reference-jobs/job_local"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == result.transcript
    assert body["script_draft"]["sections"][0]["title"] == "复刻节奏"


def test_reference_job_uses_same_style_script_generator(tmp_path):
    inbox = tmp_path / "inbox"
    drafts = tmp_path / "drafts"
    inbox.mkdir()
    source_video = inbox / "benchmark.mp4"
    source_video.write_bytes(b"fake video")
    script_generator = FakeSameStyleScriptGenerator()

    def fake_runner(command: list[str]) -> None:
        Path(command[-1]).write_bytes(b"fake wav")

    result = create_reference_job(
        Settings(
            video_inbox_path=str(inbox),
            video_output_path=str(tmp_path / "processed"),
            draft_output_path=str(drafts),
        ),
        SameStyleReferenceJobRequest(
            reference_url="benchmark.mp4",
            product_name="AI视频日记",
            product_brief="面向创作者的视频生产工具",
        ),
        asr_transcriber=FakeASRTranscriber(),
        runner=fake_runner,
        script_generator=script_generator,
        job_id_factory=lambda: "job_script_generator",
    )

    assert script_generator.calls == [
        {
            "product_name": "AI视频日记",
            "transcript": "这个产品三秒就能看懂，先看使用前后对比。",
            "shot_count": 2,
        }
    ]
    assert result.script_draft is not None
    assert result.script_draft.hook == "AI视频日记 的大模型仿写开头"

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["script_draft"]["publish_copy"] == "这是大模型润色后的发布文案。"


def test_reference_job_extracts_reference_frames_into_manifest(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source_video = inbox / "benchmark.mp4"
    source_video.write_bytes(b"fake video")
    commands: list[list[str]] = []

    def fake_runner(command: list[str]) -> None:
        commands.append(command)
        output = Path(command[-1])
        if output.name == "audio_16k.wav":
            output.write_bytes(b"fake wav")
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        (output.parent / "frame_0001.jpg").write_bytes(b"frame 1")
        (output.parent / "frame_0002.jpg").write_bytes(b"frame 2")

    result = create_reference_job(
        Settings(
            video_inbox_path=str(inbox),
            video_output_path=str(tmp_path / "processed"),
            draft_output_path=str(tmp_path / "drafts"),
        ),
        SameStyleReferenceJobRequest(
            reference_url="benchmark.mp4",
            product_name="AI视频日记",
        ),
        asr_transcriber=FakeASRTranscriber(),
        runner=fake_runner,
        job_id_factory=lambda: "job_frames",
    )

    assert any("fps=1" in " ".join(command) for command in commands)
    assert len(result.reference_frames) == 2
    assert result.reference_frames[0].path.endswith("frame_0001.jpg")
    assert result.reference_frames[0].purpose == "视觉风格参考"

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["reference_frames"][1]["path"].endswith("frame_0002.jpg")


def test_reference_job_builds_reference_grid_image_when_frames_are_valid(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source_video = inbox / "benchmark.mp4"
    source_video.write_bytes(b"fake video")

    def fake_runner(command: list[str]) -> None:
        output = Path(command[-1])
        if output.name == "audio_16k.wav":
            output.write_bytes(b"fake wav")
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        for index in range(4):
            Image.new("RGB", (80, 120), (index * 40, 80, 120)).save(
                output.parent / f"frame_{index + 1:04d}.jpg"
            )

    result = create_reference_job(
        Settings(
            video_inbox_path=str(inbox),
            video_output_path=str(tmp_path / "processed"),
            draft_output_path=str(tmp_path / "drafts"),
        ),
        SameStyleReferenceJobRequest(
            reference_url="benchmark.mp4",
            product_name="AI视频日记",
        ),
        asr_transcriber=FakeASRTranscriber(),
        runner=fake_runner,
        job_id_factory=lambda: "job_reference_grid",
    )

    assert result.reference_grid_path is not None
    assert Path(result.reference_grid_path).is_file()

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["artifacts"]["reference_grid_path"] == result.reference_grid_path


def test_reference_job_builds_product_reference_grid_when_product_image_is_provided(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source_video = inbox / "benchmark.mp4"
    source_video.write_bytes(b"fake video")
    product_image = inbox / "product.jpg"
    Image.new("RGB", (120, 90), (230, 40, 80)).save(product_image)

    def fake_runner(command: list[str]) -> None:
        output = Path(command[-1])
        if output.name == "audio_16k.wav":
            output.write_bytes(b"fake wav")
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        for index in range(4):
            Image.new("RGB", (80, 120), (index * 40, 80, 120)).save(
                output.parent / f"frame_{index + 1:04d}.jpg"
            )

    result = create_reference_job(
        Settings(
            video_inbox_path=str(inbox),
            video_output_path=str(tmp_path / "processed"),
            draft_output_path=str(tmp_path / "drafts"),
        ),
        SameStyleReferenceJobRequest(
            reference_url="benchmark.mp4",
            product_name="AI视频日记",
            product_image_path="product.jpg",
        ),
        asr_transcriber=FakeASRTranscriber(),
        runner=fake_runner,
        job_id_factory=lambda: "job_product_grid",
    )

    assert result.product_image_path == str(product_image)
    assert result.product_reference_grid_path is not None
    assert Path(result.product_reference_grid_path).is_file()

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["artifacts"]["product_image_path"] == str(product_image)
    assert manifest["artifacts"]["product_reference_grid_path"] == result.product_reference_grid_path


def test_reference_job_fails_when_product_image_path_is_invalid(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source_video = inbox / "benchmark.mp4"
    source_video.write_bytes(b"fake video")

    result = create_reference_job(
        Settings(
            video_inbox_path=str(inbox),
            video_output_path=str(tmp_path / "processed"),
            draft_output_path=str(tmp_path / "drafts"),
        ),
        SameStyleReferenceJobRequest(
            reference_url="benchmark.mp4",
            product_name="AI视频日记",
            product_image_path="missing-product.jpg",
        ),
        asr_transcriber=FakeASRTranscriber(),
        runner=lambda command: None,
        job_id_factory=lambda: "job_missing_product",
    )

    assert result.status == "failed"
    assert "产品参考图" in result.message


def test_same_style_reference_endpoint_accepts_external_links(monkeypatch):
    class MissingDownloader:
        def __call__(self, reference_url: str, workspace: Path) -> Path | None:
            return None

    monkeypatch.setattr(
        "app.same_style_reference.YtDlpReferenceDownloader",
        MissingDownloader,
    )
    client = TestClient(main_module.app)

    response = client.post(
        "/api/same-style/reference-jobs",
        json={
            "reference_url": "https://www.douyin.com/video/123",
            "product_name": "AI视频日记",
            "product_brief": "面向创作者的视频生产工具",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "download_required"
    assert body["module_name"] == "reference_video_importer"
