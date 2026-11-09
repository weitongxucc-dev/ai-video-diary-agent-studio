"""成片确定性质量检出（WIL-119）。

只做四类**确定性媒体错误**：黑帧、尾部截断、削波、起点时间戳错位。
三类不能越界的规则各有对应用例：
- 容器时长差不等于截断；
- 起始时间戳差不等于全程音画同步；
- 未知/失败不冒充 clean。

样本是本用例现合成的小视频/音频（≤3 秒，串行执行），只落在 tmp_path；
不打开真实用户库、不跑任何模型、不启动服务。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.quality_checks import (
    QUALITY_KIND_BLACK_FRAME,
    QUALITY_KIND_CLIPPING,
    QUALITY_KIND_TIMESTAMP_OFFSET,
    QUALITY_KIND_TRUNCATION,
    QUALITY_UNAVAILABLE,
    CLIP_EVENT_LIST_LIMIT,
    DEFAULT_CLIP_FULL_SCALE_DUTY,
    QualityCheckError,
    detect_timestamp_offset,
    detect_truncation,
    inspect_media_quality,
    quality_report_evidence,
)
from test_quality_resource_owner import licensed_native_port


@pytest.fixture(autouse=True)
def _licensed_quality_transports(request, monkeypatch):
    port = licensed_native_port(request)
    for name in ("inspect_media_quality", "detect_truncation", "detect_timestamp_offset"):
        monkeypatch.setitem(globals(), name, getattr(port, name))
    monkeypatch.setitem(globals(), "_quality_native_command", port.command)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="确定性媒体检查需要本机 ffmpeg/ffprobe",
)

SAMPLE_SECONDS = 2


def _ffmpeg(arguments: list[str]) -> None:
    globals()["_quality_native_command"](
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments],
        check=True, capture_output=True, timeout=3,
    )


def _normal_video(path: Path, *, seconds: int = SAMPLE_SECONDS) -> Path:
    """正常画面：彩色测试图配正弦音频，两条流等长。"""
    _ffmpeg([
        "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=25:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ])
    return path


def _black_then_color_video(path: Path) -> Path:
    """前 1 秒全黑、后 1 秒有画面：应检出一段黑帧区间。"""
    _ffmpeg([
        "-f", "lavfi", "-i", "color=c=black:size=160x120:rate=25:duration=1",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a]anull[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ])
    return path


def _silent_tail_video(path: Path) -> Path:
    """画面比声音长 1 秒：尾部只有画面没有声音（真实解码位置可量）。"""
    _ffmpeg([
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ])
    return path


def _full_scale_sine_video(path: Path) -> Path:
    """满幅但**没有**削平的正弦：负向样本。

    每个周期都会触到满幅，但波形是圆的、在峰顶一掠而过，绝不能报成削波。
    """
    _ffmpeg([
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
        "-f", "lavfi", "-i", "aevalsrc=0.9999*sin(2*PI*440*t):s=48000:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ])
    return path


def _hard_clipped_video(path: Path) -> Path:
    """真正被削平：幅度过载后顶端被削掉、长时间停在数字满幅上。

    音频直接以无损 PCM 装进容器（不经有损编码）：AAC 会把削平抹平或伪造出来，
    那样测的就不是削平本身了。
    """
    _ffmpeg([
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
        "-f", "lavfi", "-i", "aevalsrc=3.0*sin(2*PI*440*t):s=48000:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le",
        "-map", "0:v", "-map", "1:a", str(path),
    ])
    return path


def _video_only(path: Path) -> Path:
    _ffmpeg([
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ])
    return path


def test_clean_sample_reports_no_issues_and_no_unavailable(tmp_path):
    report = inspect_media_quality(_normal_video(tmp_path / "clean.mp4"))

    assert report.findings == ()
    assert report.unavailable == ()
    assert report.is_clean is True
    assert report.status == "clean"


def test_black_frame_is_located_with_a_time_range(tmp_path):
    report = inspect_media_quality(_black_then_color_video(tmp_path / "black.mp4"))

    black = report.findings_of(QUALITY_KIND_BLACK_FRAME)
    assert black, "前段全黑必须被检出"
    assert black[0].start_seconds < 0.5
    assert black[0].end_seconds >= black[0].start_seconds
    assert black[0].duration_seconds > 0


def test_tail_truncation_is_decided_by_decoded_positions_not_container_duration(
    tmp_path,
):
    """画面比声音长：容器时长取的是最长的一条，不能据此判成正常。"""
    sample = _silent_tail_video(tmp_path / "tail.mp4")
    findings, unavailable = detect_truncation(sample)

    assert unavailable == []
    assert findings, "尾部只有画面没有声音，必须按真实解码位置检出"
    assert findings[0].kind == QUALITY_KIND_TRUNCATION
    assert findings[0].start_seconds < findings[0].end_seconds
    assert "只有画面没有声音" in findings[0].detail
    # 报告区间是成片时间轴内的相对位置，不是容器里的绝对时间。
    assert findings[0].start_seconds >= 0.0
    assert findings[0].end_seconds <= 3.0


def test_missing_audio_stream_is_unavailable_not_truncation(tmp_path):
    """缺流不是截断结论，是"这次量不了"。"""
    sample = _video_only(tmp_path / "video-only.mp4")
    findings, unavailable = detect_truncation(sample)

    assert findings == []
    assert unavailable and unavailable[0].status == QUALITY_UNAVAILABLE
    assert "没有音频流" in unavailable[0].reason


def test_start_offset_is_reported_as_start_offset_only(tmp_path):
    """起点错位只说起点差多少秒，不能说成整段不同步。"""
    # 用 -itsoffset 让音频整体晚 0.6s，并装进 MPEG-TS：MP4 会把音频
    # start_time 改写成 0，测不到真实的起点错位。
    sample = tmp_path / "offset.ts"
    _ffmpeg([
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
        "-itsoffset", "0.6",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-f", "mpegts", str(sample),
    ])
    findings, unavailable = detect_timestamp_offset(sample)

    assert unavailable == []
    assert findings, "起点错位必须被检出"
    detail = findings[0].detail
    assert QUALITY_KIND_TIMESTAMP_OFFSET == findings[0].kind
    assert "不代表整段音画同步" in detail
    # 归一到成片时间轴：容器从 1.4s 开始，报告的应是内容内的相对位置。
    assert findings[0].start_seconds >= 0.0
    assert findings[0].end_seconds <= 1.0


def test_unavailable_never_counts_as_clean(tmp_path):
    """量不成的一类存在时，报告不能是"通过"。"""
    report = inspect_media_quality(_video_only(tmp_path / "video-only.mp4"))

    assert report.unavailable, "缺音频流时削波与截断都量不成"
    assert report.is_clean is False
    assert report.status == QUALITY_UNAVAILABLE


def test_full_scale_but_unclipped_sine_is_not_reported_as_clipping(tmp_path):
    """负向：满幅正弦每个周期都触顶，但不是削平。"""
    report = inspect_media_quality(_full_scale_sine_video(tmp_path / "full-scale.mp4"))

    assert report.findings_of(QUALITY_KIND_CLIPPING) == ()
    assert [item for item in report.unavailable if item.kind == QUALITY_KIND_CLIPPING] == []


def test_hard_clipped_waveform_is_detected_and_located(tmp_path):
    """正向：真正削平的波形要检出，并给出可定位区间。"""
    report = inspect_media_quality(_hard_clipped_video(tmp_path / "clipped.mp4"))

    clipped = report.findings_of(QUALITY_KIND_CLIPPING)
    assert clipped, "被削平的波形必须被检出"
    assert clipped[0].start_seconds >= 0.0
    assert clipped[0].end_seconds > clipped[0].start_seconds
    # 位置来自真实采样序号，不是窗口序号×窗长。
    assert clipped[0].start_seconds <= 2.0
    assert "削平" in clipped[0].detail


def test_unreadable_file_raises_instead_of_reporting_clean(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a media file")

    with pytest.raises(QualityCheckError):
        inspect_media_quality(broken)


def test_frozen_evidence_carries_ranges_status_and_unknown(tmp_path):
    """给 108 的冻结契约：区间、三态与未知项都在证据里，语义不靠猜。"""
    report = inspect_media_quality(_black_then_color_video(tmp_path / "black.mp4"))
    evidence = quality_report_evidence(report)

    assert evidence["status"] in {"issues_found", "clean", QUALITY_UNAVAILABLE}
    assert evidence["is_clean"] is report.is_clean
    assert evidence["kinds_checked"] == [
        "black_frame", "truncation", "clipping", "timestamp_offset",
    ]
    assert all(
        {"kind", "start_seconds", "end_seconds", "duration_seconds", "detail"}
        <= set(item)
        for item in evidence["findings"]
    )
    # 可落库：证据是纯 JSON。
    json.dumps(evidence, ensure_ascii=False)


def test_clipping_summary_aggregates_without_dropping_evidence(tmp_path):
    """重削音频段数上千：摘要可用，但每段事件都还在，且截断显式可见。"""
    report = inspect_media_quality(_hard_clipped_video(tmp_path / "clip.mp4"))
    summary = report.clipping_summary
    assert summary is not None, "检出削平就必须给出聚合摘要"

    clipped = report.findings_of(QUALITY_KIND_CLIPPING)
    # 一条都不许静默丢：要么逐条全在，要么在摘要里显式报出丢了多少条。
    if summary["findings_truncated"]:
        assert len(clipped) == summary["findings_event_limit"]
        assert summary["findings_truncated_count"] == (
            summary["event_count"] - len(clipped)
        )
    else:
        assert summary["event_count"] == len(clipped)
    # 总量永远按全部事件算，不受列举/逐条截断影响。
    assert summary["event_count"] >= len(clipped)
    # 列举有界，但截断必须显式说明丢了多少条。
    assert len(summary["events"]) <= CLIP_EVENT_LIST_LIMIT
    assert summary["events_truncated"] is (
        len(clipped) > CLIP_EVENT_LIST_LIMIT
    )
    if summary["events_truncated"]:
        assert summary["truncated_event_count"] == (
            len(clipped) - len(summary["events"])
        )
    assert summary["first_start_seconds"] == clipped[0].start_seconds
    assert summary["last_end_seconds"] == max(
        item.end_seconds for item in clipped
    )
    assert summary["full_scale_duty"] > DEFAULT_CLIP_FULL_SCALE_DUTY
    # 序列化的冻结契约里也要带上摘要。
    assert quality_report_evidence(report)["clipping_summary"] == summary


def test_no_clipping_means_no_summary(tmp_path):
    """未检出削平时不硬造摘要。"""
    report = inspect_media_quality(
        _full_scale_sine_video(tmp_path / "sine.mp4")
    )
    assert report.clipping_summary is None
