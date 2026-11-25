"""Measure a bounded H.264/AAC MP4 cut with an already granted command port."""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import time
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from .adapter import FfmpegAdapter, VideoCut, verify_video_cut, video_frame_intervals
from .contracts import MediaExecutionError


@dataclass(frozen=True)
class Mp4CutLimits:
    width: int
    height: int
    max_decode_pixels: int
    max_source_seconds: float
    max_source_bytes: int
    max_work_bytes: int
    max_packets: int

    def __post_init__(self):
        integers = (self.width, self.height, self.max_decode_pixels, self.max_source_bytes,
                    self.max_work_bytes, self.max_packets)
        if (any(type(value) is not int or value <= 0 for value in integers)
                or self.width * self.height > self.max_decode_pixels
                or self.max_source_bytes >= self.max_work_bytes
                or not math.isfinite(self.max_source_seconds) or self.max_source_seconds <= 0):
            raise ValueError("MP4 cut measurement requires finite positive limits")


def _refused(reason: str, detail: str) -> MediaExecutionError:
    return MediaExecutionError(reason, "MP4 截取测量未完成。", detail)


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _digest(path: Path, expected_bytes: int, check, *, copy_to=None) -> str:
    digest = hashlib.sha256()
    check()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected_bytes:
            raise _refused("media_cut_source_changed", "文件大小或类型与本次测量绑定不符。")
        remaining = expected_bytes
        while remaining:
            check()
            chunk = source.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)
            digest.update(chunk)
            if copy_to is not None:
                copy_to.write(chunk)
        check()
        if (remaining or source.read(1) or _identity(os.fstat(source.fileno())) != _identity(info)
                or _identity(path.lstat()) != _identity(info)):
            raise _refused("media_cut_source_changed", "读取期间文件发生变化。")
    return digest.hexdigest()


_PROBE_ENTRIES = (
    "frame=media_type,stream_index,best_effort_timestamp,duration,pkt_duration,width,height,pix_fmt,nb_samples,channels:"
    "stream=index,codec_type,codec_name,time_base,start_pts,duration_ts,avg_frame_rate,nb_read_packets,sample_rate,channels:"
    "format=start_time,duration:format_tags=major_brand"
)
_CPU_ACK = re.compile(
    rb"GRANT_CPU_POLICY mode=(?:darwin_background state=verified background=1|"
    rb"darwin_nice state=verified nice=(?:[1-9]|1[0-9]|20) background=0) errno=0"
)
_AUDIO_CLOCK = Fraction(1, 48000)
# Allow the MP4 edit-list clock to round the end of the AAC track.
_AUDIO_END_TOLERANCE = Fraction(1, 1000)


def _read_probe(payload: dict, limits: Mp4CutLimits, *, source: bool) -> tuple[dict, dict]:
    streams = payload["streams"]
    if (payload["format"].get("tags", {}).get("major_brand") not in {"isom", "mp41", "mp42", "avc1"}
            or [(track.get("codec_type"), track.get("codec_name")) for track in streams] != [
                ("video", "h264"), ("audio", "aac")]):
        raise _refused("media_cut_mp4_shape", "测量需要一条 H.264 视频轨及一条 AAC 音轨。")
    video, audio = streams
    try:
        video_packets, audio_packets = (int(track["nb_read_packets"]) for track in streams)
        packets = video_packets + audio_packets
        if packets > limits.max_packets:
            raise _refused("media_cut_packet_limit", f"已读 {packets} 个包，上限为 {limits.max_packets}。")
        frames = payload["frames"]
        pictures = [frame for frame in frames if frame["stream_index"] == video["index"]]
        samples = [frame for frame in frames if frame["stream_index"] == audio["index"]]
        if (not pictures or not samples or len(frames) != len(pictures) + len(samples)
                or len(pictures) != video_packets or not len(samples) <= audio_packets <= len(samples) + 1
                or any((frame.get("width"), frame.get("height"), frame.get("pix_fmt"))
                       != (limits.width, limits.height, "yuv420p") for frame in pictures)):
            raise ValueError("unexpected decoded shape or incomplete packet coverage")
        video_media = {"streams": [video], "frames": pictures,
                       "format": {"start_time": "0"} if source else payload["format"]}
        intervals = video_frame_intervals(video_media)
        video_duration = int(video["duration_ts"]) * Fraction(video["time_base"])
        if (int(video["start_pts"]) != 0 or intervals[0][1] != 0
                or intervals[-1][2] != video_duration):
            raise ValueError("video is not on a complete zero-origin clock")
        if (int(audio["start_pts"]) != 0 or Fraction(audio["time_base"]) != _AUDIO_CLOCK
                or int(audio["sample_rate"]) != 48000 or audio["channels"] != 2):
            raise ValueError("audio is not zero-origin 48 kHz stereo AAC")
        audio_end = 0
        for frame in samples:
            timestamp = int(frame["best_effort_timestamp"])
            frame_duration = int(frame.get("duration", frame.get("pkt_duration")))
            if (frame["channels"] != 2 or frame["nb_samples"] != 1024
                    or timestamp != audio_end or not 0 < frame_duration <= 1024):
                raise ValueError("unexpected or discontinuous AAC frame")
            audio_end += frame_duration
        if (audio_end != int(audio["duration_ts"])
                or abs(audio_end * _AUDIO_CLOCK - video_duration) > _AUDIO_END_TOLERANCE):
            raise ValueError("audio and video coverage differ")
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise _refused("media_cut_mp4_shape", f"媒体轨道、帧或时钟超出测量形状。{exc}") from exc
    return video_media, {"packets": packets, "video_frames": len(pictures), "audio_frames": len(samples),
                         "video_seconds": float(video_duration), "audio_seconds": float(audio_end * _AUDIO_CLOCK)}


def measure_mp4_cut(adapter: FfmpegAdapter, source: Path, work_root: Path, *,
                    source_sha256: str, source_bytes: int, start: float, duration: float,
                    limits: Mp4CutLimits, run, check) -> dict:
    """Run one measurement with a caller-owned grant and shared deadline.

    ``run(argv, paths, env=...)`` is the existing preparation command port. It
    must bound both pipes, total bytes at ``paths`` (to ``max_work_bytes`` or
    less), the deadline and child resources. ``check`` checks that same grant
    and deadline during local file I/O. The caller owns the private scratch root.
    """
    if (type(source_bytes) is not int or not 0 < source_bytes <= limits.max_source_bytes
            or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)):
        raise _refused("media_cut_input_limit", "截取源缺少有效摘要或整源大小超出测量上限。")
    if (not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration <= 0
            or start + duration > limits.max_source_seconds + 1e-6):
        raise _refused("media_cut_range_limit", "截取区间超出测量时长上限。")

    started = time.monotonic()
    input_limits = ("-f", "mov", "-protocol_whitelist", "file", "-enable_drefs", "0", "-max_streams", "2",
                    "-codec_whitelist", "h264,aac", "-max_pixels", str(limits.max_decode_pixels),
                    "-err_detect", "explode")
    short_analysis = ("-analyzeduration", "1", "-max_probe_packets", "1", "-fpsprobesize", "0")
    stages = {}
    with TemporaryDirectory(prefix="mp4-cut-", dir=work_root) as work:
        snapshot, output = Path(work) / "source.mp4", Path(work) / "cut.mp4"
        with snapshot.open("xb") as destination:
            if _digest(source, source_bytes, check, copy_to=destination) != source_sha256:
                raise _refused("media_cut_source_changed", "整源 SHA-256 与本次测量绑定不符。")
        snapshot.chmod(0o400)
        snapshot_identity = _identity(snapshot.lstat())
        check()
        snapshot_seconds = time.monotonic() - started

        def run_stage(name: str, command: tuple[str, ...]):
            check()
            if _identity(snapshot.lstat()) != snapshot_identity:
                raise _refused("media_cut_source_changed", "截取期间测量副本已变更。")
            phase_start = time.monotonic()
            result = run(command, (snapshot, output), env={})
            seconds = time.monotonic() - phase_start
            check()
            if _identity(snapshot.lstat()) != snapshot_identity:
                raise _refused("media_cut_source_changed", "截取期间测量副本已变更。")
            if result.returncode or any(not _CPU_ACK.fullmatch(line) for line in result.stderr.splitlines()):
                raise _refused("media_cut_command_failed", f"{name} 返回错误或解码异常。")
            observed = result.observed_resources
            if (observed is None or not observed.rss_sample_complete or not observed.threads_sample_complete
                    or observed.peak_rss_bytes is None or observed.peak_threads is None):
                raise _refused("media_cut_sampling_incomplete", f"{name} 未获得完整的 RSS 和线程采样。")
            stages[name] = {"seconds": seconds, "command": command, "stdout_bytes": len(result.stdout),
                            "resources": asdict(observed)}
            return result

        def probe(name: str, path: Path, *, source: bool):
            analysis = ("-nofind_stream_info",) if source else short_analysis
            result = run_stage(name, (
                adapter.ffprobe, "-v", "error", *input_limits, *analysis, "-threads", "1",
                "-read_intervals", f"%+#{limits.max_packets + 1}", "-show_frames", "-count_packets",
                "-show_entries", _PROBE_ENTRIES, "-of", "json=compact=1", str(path),
            ))
            return _read_probe(adapter.parse_probe(result.stdout), limits, source=source)

        source_media, source_facts = probe("source_probe", snapshot, source=True)
        source_duration = Fraction(source_media["streams"][0]["duration_ts"]) * Fraction(
            source_media["streams"][0]["time_base"])
        cut = VideoCut.from_source(source_media, start, duration)
        if source_duration > Fraction(str(limits.max_source_seconds)) or cut.source_end > source_duration:
            raise _refused("media_cut_range_limit", "整源或截取区间超出已核验的时长范围。")
        prepared = adapter.video_cut("cut", snapshot, output, cut, threads=1,
                                     input_limits=(*input_limits, *short_analysis, "-filter_complex_threads", "1", "-xerror"))
        run_stage("encode", prepared.command)
        output_info = output.lstat()
        output_bytes = output_info.st_size
        if not stat.S_ISREG(output_info.st_mode) or not 0 < output_bytes <= limits.max_work_bytes - source_bytes:
            raise _refused("media_cut_output_limit", "截取成片超出有限磁盘用量或为空。")
        output_media, output_facts = probe("output_probe", output, source=False)
        output_facts.update(verify_video_cut(output_media, cut))
        output_facts.update(bytes=output_bytes, sha256=_digest(output, output_bytes, check))
        if _identity(output.lstat()) != _identity(output_info):
            raise _refused("media_cut_output_changed", "截取成片在核验期间已变更。")
        check()
        return {
            "limits": asdict(limits),
            "source": {**source_facts, "bytes": source_bytes, "sha256": source_sha256,
                       "width": limits.width, "height": limits.height},
            "selection": [float(cut.source_start), float(cut.source_end)],
            "snapshot_seconds": snapshot_seconds, "stages": stages, "output": output_facts,
            "wall_seconds": time.monotonic() - started,
        }
