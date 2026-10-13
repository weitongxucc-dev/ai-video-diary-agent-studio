"""Small real decoded outputs; no service/model or production stores."""
import subprocess

import pytest
from PIL import Image

from app.task_observation import observe_output


def command(argv, **_bounded_outputs):
    return subprocess.run(argv, check=True, capture_output=True, timeout=15)


def test_actual_video_samples_are_bounded_measurements_not_semantic_claims(tmp_path):
    video = tmp_path / "red.mp4"
    command(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=160x120:r=10:d=1",
             "-threads", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)])
    result = observe_output(video, sample_times=[], temporary_root=tmp_path / "samples", run_command=command,
                            max_output_bytes=1024 * 1024, expected_size=(160, 120))
    assert len(result["samples"]) == 3
    assert result["media"]["duration_seconds"] == 1
    assert all(frame["pixel_mean_rgb"][0] > 240 and frame["pixel_mean_rgb"][1] < 10 for frame in result["samples"])
    assert {item["scope"] for item in result["unavailable"]} == {"ocr", "visual_semantics", "speech_and_listening"}
    assert not any("path" in frame or "bytes" in frame for frame in result["samples"])
    assert list((tmp_path / "samples").iterdir()) == []
    with pytest.raises(ValueError, match="登记输出"):
        observe_output(video, sample_times=[], temporary_root=tmp_path, run_command=command,
                       max_output_bytes=1024 * 1024, expected_size=(1920, 1080))
    with pytest.raises(ValueError, match="实际输出时长"):
        observe_output(video, sample_times=[1], temporary_root=tmp_path, run_command=command,
                       max_output_bytes=1024 * 1024, expected_size=(160, 120))


def test_png_source_actual_size_checked_before_decode_and_not_applied(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (1800, 900), "blue").save(source)
    result = observe_output(source, sample_times=[0], temporary_root=tmp_path, run_command=command,
                            max_output_bytes=1024 * 1024, expected_size=(1800, 900))
    assert (result["samples"][0]["width"], result["samples"][0]["height"]) == (1280, 640)
    assert result["media"]["width"] == 1800 and result["media"]["duration_seconds"] is None
    assert result["samples"][0]["object_refs"] == []
    with pytest.raises(ValueError, match="时间 0"):
        observe_output(source, sample_times=[1], temporary_root=tmp_path, run_command=command,
                       max_output_bytes=1024 * 1024, expected_size=(1800, 900))


@pytest.mark.parametrize("times", [[float("inf")], [-1], [0, 0], [0, .1, .2, .3]])
def test_samples_reject_unbounded_requests_before_running(tmp_path, times):
    with pytest.raises(ValueError):
        observe_output(tmp_path / "missing.mp4", sample_times=times, temporary_root=tmp_path,
                       run_command=lambda *_: pytest.fail("invalid samples must not run"),
                       max_output_bytes=1, expected_size=(160, 120))
