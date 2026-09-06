from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from app.cover_generation import (
    COVER_HEIGHT,
    COVER_WIDTH,
    CoverGenerationRequest,
    CoverGenerationService,
    CoverSafeArea,
    _fit_title,
    unconfigured_image_generation_capability,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS cover typography")
def test_cover_title_balances_chinese_lines():
    _, lines = _fit_title("本地创作，数据更清楚", 848, 1400)
    assert lines == ["本地创作，", "数据更清楚"]


def _write_two_scene_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=360x640:r=25:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=360x640:r=25:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)
def test_real_ffmpeg_selects_requested_frame_and_renders_visible_chinese_title(tmp_path):
    output_root = tmp_path / "task-output"
    output_root.mkdir()
    source = output_root / "current-timeline.mp4"
    _write_two_scene_video(source)
    source_hash = _sha256(source)

    generated = CoverGenerationService(output_root).generate(
        CoverGenerationRequest(
            source_video_path=source,
            output_ref="artifacts/task-1/version-3/cover.png",
            frame_time_seconds=1.25,
            title="别只讲概念",
            safe_area=CoverSafeArea(left=80, top=140, right=80, bottom=180),
        )
    )

    cover = Path(generated.output_path)
    assert cover.is_file() and cover.stat().st_size > 0
    assert _sha256(source) == source_hash
    with Image.open(cover) as image:
        assert image.format == "PNG"
        assert image.size == (COVER_WIDTH, COVER_HEIGHT)
        top_pixel = image.convert("RGB").getpixel((100, 100))
        assert top_pixel[2] > top_pixel[0] * 3
        assert image.convert("RGB").getpixel((100, 1600)) == (24, 25, 23)
        title_region = image.convert("RGB").crop((80, 1200, 1000, 1740))
        colors = title_region.getcolors(maxcolors=title_region.width * title_region.height)
        assert colors is not None
        bright_pixels = sum(
            count for count, (red, green, blue) in colors
            if red > 225 and green > 225 and blue > 225
        )
        assert bright_pixels > 500

    evidence = generated.evidence
    assert evidence.input_digest == source_hash
    assert evidence.input_width == 360
    assert evidence.input_height == 640
    assert evidence.input_duration_seconds == pytest.approx(2.0, abs=0.1)
    assert evidence.engine == "ffmpeg"
    assert evidence.engine_version.startswith("ffmpeg version")
    assert evidence.layout_engine == "Pillow"
    assert evidence.font_path
    if sys.platform == "darwin":
        assert "PingFang" in evidence.font_family
    assert evidence.parameters["frame_time_seconds"] == 1.25
    assert evidence.parameters["safe_area"] == {
        "left": 80,
        "top": 140,
        "right": 80,
        "bottom": 180,
    }
    assert evidence.output_sha256 == _sha256(cover)
    assert evidence.output_size_bytes == cover.stat().st_size
    assert (evidence.output_width, evidence.output_height) == (1080, 1920)
    assert evidence.output_format == "png"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)
def test_jpeg_output_is_nonempty_and_has_matching_evidence(tmp_path):
    output_root = tmp_path / "task-output"
    output_root.mkdir()
    source = output_root / "current-timeline.mp4"
    _write_two_scene_video(source)

    generated = CoverGenerationService(output_root).generate(
        CoverGenerationRequest(
            source_video_path=source,
            output_ref="artifacts/task-1/version-3/cover.jpg",
            frame_time_seconds=0.25,
            title="真实运行",
        )
    )

    with Image.open(generated.output_path) as image:
        assert image.format == "JPEG"
        assert image.size == (1080, 1920)
    assert generated.evidence.output_format == "jpeg"
    assert generated.evidence.output_size_bytes > 0


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)
def test_rejects_invalid_frame_times_without_publishing(tmp_path):
    output_root = tmp_path / "task-output"
    output_root.mkdir()
    source = output_root / "current-timeline.mp4"
    _write_two_scene_video(source)
    service = CoverGenerationService(output_root)

    with pytest.raises(ValueError, match="非负有限数"):
        service.generate(
            CoverGenerationRequest(source, "covers/negative.png", -0.1, "标题")
        )
    with pytest.raises(ValueError, match="早于成片结束"):
        service.generate(
            CoverGenerationRequest(source, "covers/after-end.png", 2.0, "标题")
        )
    assert not (output_root / "covers").exists()


def test_rejects_source_and_output_paths_outside_controlled_root(tmp_path):
    output_root = tmp_path / "task-output"
    output_root.mkdir()
    outside_source = tmp_path / "outside.mp4"
    outside_source.write_bytes(b"not read")
    service = CoverGenerationService(output_root)

    with pytest.raises(ValueError, match="受控任务输出目录"):
        service.generate(
            CoverGenerationRequest(outside_source, "covers/cover.png", 0, "标题")
        )
    inside_source = output_root / "current-timeline.mp4"
    inside_source.write_bytes(b"not probed")
    for output_ref in ("../escape.png", "/tmp/escape.png", "covers\\escape.png"):
        with pytest.raises(ValueError, match="产物引用"):
            service.generate(
                CoverGenerationRequest(inside_source, output_ref, 0, "标题")
            )


def test_rejects_invalid_safe_area_before_running_ffmpeg(tmp_path):
    output_root = tmp_path / "task-output"
    output_root.mkdir()
    source = output_root / "current-timeline.mp4"
    source.write_bytes(b"not probed")

    with pytest.raises(ValueError, match="安全区宽度不足"):
        CoverGenerationService(output_root).generate(
            CoverGenerationRequest(
                source,
                "covers/cover.png",
                0,
                "标题",
                CoverSafeArea(left=600, right=600),
            )
        )


def test_ai_image_generation_is_explicitly_unavailable():
    capability = unconfigured_image_generation_capability()

    assert capability.capability_state == "unavailable"
    assert "ImageGenerationProvider" in capability.failure_reason
    assert "不提供 AI 生图" in capability.impact
    assert "验真" in capability.remediation
