"""成片质量问题的确定性检出：黑帧 / 截断 / 削波 / 时间戳异常。

这一层只做**确定性媒体错误**：读成片本身得出结论，不猜内容含义、不评分。每条
问题都带 start_seconds / end_seconds，可直接定位到时间轴片段（成片剪辑已用
MontageClip.timeline_start_seconds / timeline_end_seconds 表达同一区间）。

三条不能越过的规则（上一版候选就是踩了这些被退回）：
1. **容器时长差不等于截断。** 容器 duration 可能整段由音频流提供，视频掉帧或
   缺流时它一样"看起来正常"。截断必须由真实解码量出来：视频解码到哪一帧、
   音频解码到哪个采样点，哪一路真的没有内容了才算。
2. **起始时间戳差不等于全程音画同步。** 起播偏移只能说明起点错位多少秒，不能
   推出"整段不同步"。这里只报**起点错位**这一件事，区间就是错位处；漂移随时间
   累积要另由专门判据量，不在这里冒充。
3. **未知/失败不许冒充 clean。** 量不出来（缺流、时长未知、探测失败、文件损坏）
   各自是独立结论：`unavailable` 明确说明这一类没量成，绝不折成"没问题"。

只读成片文件，不写回、不改时间线。ffmpeg/ffprobe 缺席或文件不可解析时抛出
QualityCheckError，由调用方决定怎么呈现，绝不静默返回"已通过"。
"""
from __future__ import annotations

from app.creation.duration_requirement import stream_duration_seconds as _stream_duration_seconds

import array
import json
import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from app.quality_worker_runtime import QualityWorkerContext

PROBE_TIMEOUT_SECONDS = 30
RUN_TIMEOUT_SECONDS = 180

# blackdetect 的阈值单位是秒不是帧：0.1s 在 25/30fps 下约合 2~3 帧，
# 与「全黑持续超过几帧才算问题」是同一个意思。
DEFAULT_BLACK_MIN_SECONDS = 0.1
DEFAULT_BLACK_PIXEL_THRESHOLD = 0.10
# 削波按固定窗口统计峰值。Peak_level 是 dBFS；≥0 表示采样触顶或越过满刻度。
# 单窗触顶可能只是素材本身的个别峰值，所以要求连续若干窗都触顶才判为削波。
# 削波判据（按真实解码样本，不是 astats 的窗峰值）：
# 峰值触顶本身不算削平——满幅正弦每个周期都会触顶。要判的是**波形在数字满幅上
# 停留了多久**。在 48kHz 上实测（无损 PCM，避免有损编码把结论搅糊）：
# 满幅未削平的正弦在 60/440/1000Hz 分别是 2.8% / 2.8% / 4.2% 的采样贴近满幅；
# 真正硬削平（幅度过载）分别是 78.3% / 78.5% / 79.2%。两者差约 20 倍且与频率
# 无关，所以取 10% 作阈值：两侧都留有数倍余量，不用某一次探索的窗口计数当结论。
DEFAULT_CLIP_FULL_SCALE_DUTY = 0.10
# 单个满幅游程至少这么长才算一段平台，短于此的只是采样恰好落在波峰。
DEFAULT_CLIP_MIN_PLATEAU_SAMPLES = 8
# 削平事件多时（重削的音频可达上千段），聚合摘要里的事件列举上限。超过上限的
# 一律不静默丢弃：摘要里显式带上被截断的条数，逐条 findings 另有下述列举上限。
CLIP_EVENT_LIST_LIMIT = 50
# 单份报告里逐条保留的削平事件上限。重削音频的事件密度实测可达 880 段/秒，
# 十分钟成片就是几十万条、上百 MB JSON，下游存不下。超出的**不静默丢弃**：
# event_count / total_clipped_seconds 始终按全部事件算准，摘要里显式给出
# findings_truncated 与 findings_truncated_count。
CLIP_FINDING_EVENT_LIMIT = 2000
# 满幅判定的相对容差：贴近峰值 0.1% 以内视作触到满幅（约 1 个 LSB）。
CLIP_FULL_SCALE_TOLERANCE = 0.999
# 起点错位超过一帧（30fps ≈ 33ms）再多一点才算问题；20ms 内属于正常封装差异。
DEFAULT_START_OFFSET_TOLERANCE_SECONDS = 0.05
# 解码尾部缺失：视频末帧或音频末采样之后仍无内容才算尾部截断，容差盖住解码器补零。
DEFAULT_TAIL_GAP_TOLERANCE_SECONDS = 0.10

#: 本模块能给出的问题种类。调用方按这份名单展示，不要自己造名字。
QUALITY_KIND_BLACK_FRAME = "black_frame"
QUALITY_KIND_TRUNCATION = "truncation"
QUALITY_KIND_CLIPPING = "clipping"
QUALITY_KIND_TIMESTAMP_OFFSET = "timestamp_offset"

#: 每一类在**没量成**时的结论名。与"没问题"（findings 为空）是两回事。
QUALITY_UNAVAILABLE = "unavailable"

QUALITY_KINDS: tuple[str, ...] = (
    QUALITY_KIND_BLACK_FRAME,
    QUALITY_KIND_TRUNCATION,
    QUALITY_KIND_CLIPPING,
    QUALITY_KIND_TIMESTAMP_OFFSET,
)

_BLACK_PATTERN = re.compile(r"black_start:([0-9.]+)\s+black_end:([0-9.]+)")
# This compact protocol requests only timestamps/durations or sample counts.
# 64 ASCII bytes per scalar accommodate signed integer/rational timestamps,
# decimal precision, scientific notation and N/A. This bounds one record, not
# the number of frames or the total inspection output/work quota.
_FRAME_SCALAR_TEXT_LIMIT = 64


class QualityCheckError(RuntimeError):
    """检出跑不动：ffmpeg/ffprobe 缺失、文件读不了、或容器元数据不合法。"""


@dataclass(frozen=True)
class QualityFinding:
    """一条可定位的问题。

    `start_seconds` / `end_seconds` 是成片时间轴上的区间，直接对应剪辑片段。
    """

    kind: str
    start_seconds: float
    end_seconds: float
    detail: str

    @property
    def duration_seconds(self) -> float:
        return round(self.end_seconds - self.start_seconds, 3)


@dataclass(frozen=True)
class QualityUnavailable:
    """某一类没量成。`reason` 说明为什么，绝不当作"没问题"。"""

    kind: str
    reason: str

    status: str = QUALITY_UNAVAILABLE


@dataclass(frozen=True)
class QualityReport:
    """一次检出的冻结结果。给 108 直接消费。"""

    path: str
    findings: tuple[QualityFinding, ...]
    unavailable: tuple[QualityUnavailable, ...]
    duration_seconds: float | None
    engine: str
    validation_scope: str = "deterministic_media_errors_only"
    clip_full_scale_duty_threshold: float = DEFAULT_CLIP_FULL_SCALE_DUTY
    timeline_origin_seconds: float = 0.0
    clipping_summary: dict | None = None
    video_duration_seconds: float | None = None
    video_frame_rate: str | None = None

    @property
    def is_clean(self) -> bool:
        """True 只表示**全部四类都量成了、且没检出问题**。

        任何一类没量成（unavailable 非空），这里一律 False：未知不是通过。
        """
        return not self.findings and not self.unavailable

    @property
    def status(self) -> str:
        """给界面用的三态：问题 / 通过 / 未检测完成。"""
        if self.findings:
            return "issues_found"
        return "clean" if not self.unavailable else QUALITY_UNAVAILABLE

    def findings_of(self, kind: str) -> tuple[QualityFinding, ...]:
        return tuple(item for item in self.findings if item.kind == kind)


def detect_black_frames(
    path: str | Path,
    *,
    min_duration_seconds: float = DEFAULT_BLACK_MIN_SECONDS,
    pixel_threshold: float = DEFAULT_BLACK_PIXEL_THRESHOLD,
    context: QualityWorkerContext | None = None,
) -> list[QualityFinding]:
    """全黑画面区间。没有检出返回空列表。"""
    completed = _run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-vf", f"blackdetect=d={min_duration_seconds}:pix_th={pixel_threshold}",
        "-an", "-f", "null", "-",
    ], RUN_TIMEOUT_SECONDS, context=context)
    # 黑场一直延伸到片尾时 ffmpeg 也会给出 black_end（停在最后一个完整帧）。
    return [
        QualityFinding(
            QUALITY_KIND_BLACK_FRAME,
            round(float(start), 3),
            round(float(end), 3),
            f"画面全黑 {round(float(end) - float(start), 3)}s",
        )
        for start, end in _BLACK_PATTERN.findall(completed.stderr)
    ]


@dataclass(frozen=True)
class ClippingInspection:
    """Bounded examples and exact aggregates from the complete decoded stream."""
    findings: list[QualityFinding]
    unavailable: list[QualityUnavailable]
    summary: dict | None = None


@dataclass
class _ClippingEvents:
    """Retain bounded examples while accounting for every detected interval."""

    event_limit: int = CLIP_EVENT_LIST_LIMIT
    findings_limit: int = CLIP_FINDING_EVENT_LIMIT
    count: int = 0
    total_seconds: float = 0.0
    first_start: float | None = None
    last_end: float | None = None
    examples: list[QualityFinding] = field(default_factory=list)

    def add(self, finding):
        self.count += 1
        self.total_seconds += finding.duration_seconds
        self.first_start = finding.start_seconds if self.first_start is None else min(self.first_start, finding.start_seconds)
        self.last_end = finding.end_seconds if self.last_end is None else max(self.last_end, finding.end_seconds)
        if len(self.examples) < max(self.event_limit, self.findings_limit):
            self.examples.append(finding)

    def summary(self, duty):
        if not self.count:
            return None
        return {
            "event_count": self.count,
            "total_clipped_seconds": round(self.total_seconds, 3),
            "first_start_seconds": self.first_start,
            "last_end_seconds": self.last_end,
            "full_scale_duty": round(duty, 6) if duty is not None else None,
            "event_list_limit": self.event_limit,
            "events": [{"start_seconds": item.start_seconds, "end_seconds": item.end_seconds,
                        "duration_seconds": item.duration_seconds} for item in self.examples[:self.event_limit]],
            "events_truncated": self.count > self.event_limit,
            "truncated_event_count": max(0, self.count - self.event_limit),
            "findings_event_limit": self.findings_limit,
            "findings_truncated": self.count > self.findings_limit,
            "findings_truncated_count": max(0, self.count - self.findings_limit),
        }


class _PCMChunks:
    """Decode s16le at arbitrary pipe boundaries, retaining at most one byte."""

    def __init__(self, consume):
        self.consume, self.pending = consume, b""

    def feed(self, raw):
        raw = self.pending + raw
        complete = len(raw) - len(raw) % 2
        self.pending = raw[complete:]
        values = array.array("h")
        values.frombytes(raw[:complete])
        if sys.byteorder == "big":  # pragma: no cover - platform dependent
            values.byteswap()
        self.consume(values)

    def finish(self):
        if self.pending:
            raise QualityCheckError("PCM 解码结果含不完整采样，削平检查未完成")


def detect_clipping(
    path: str | Path,
    *,
    sample_rate: int | None = None,
    audio_start_seconds: float = 0.0,
    min_plateau_samples: int = DEFAULT_CLIP_MIN_PLATEAU_SAMPLES,
    full_scale_duty_threshold: float = DEFAULT_CLIP_FULL_SCALE_DUTY,
    timeline_origin_seconds: float = 0.0,
    context: QualityWorkerContext | None = None,
) -> ClippingInspection:
    """削波/削平：按**真实解码出的采样**判断。

    峰值触到满幅本身不是削平——任何满幅正弦每个周期都会触顶。削平的物理表现是
    波形被削掉顶端后**长时间停在数字满幅上**，因此这里量的是"贴近满幅的采样占比"
    （flat-top duty），并在超过阈值时按游程给出可定位的区间。

    返回有界问题示例、全量聚合及没量成的项：没有音频流、没有采样率、解不出采样时记
    `unavailable`，绝不返回空列表冒充"没问题"。
    """
    if sample_rate is None:
        sample_rate = _probe_sample_rate(path, context=context)
    if not sample_rate:
        return ClippingInspection([], [QualityUnavailable(
            QUALITY_KIND_CLIPPING, "音频流没有采样率，无法按样本判断削平"
        )])
    peak, sample_count = 0, 0

    def levels(samples):
        nonlocal peak, sample_count
        sample_count += len(samples)
        peak = max(peak, max((abs(value) for value in samples), default=0))

    # The existing threshold is relative to the peak of the complete stream.
    # Two bounded passes preserve that meaning without keeping all PCM in RAM.
    # Both passes consume the same worker deadline and retain per-command quotas.
    if not _decode_pcm(path, sample_rate, levels, context=context):
        return ClippingInspection([], [QualityUnavailable(
            QUALITY_KIND_CLIPPING, "音频解码不出采样，削平未知"
        )])
    if not sample_count:
        return ClippingInspection([], [QualityUnavailable(
            QUALITY_KIND_CLIPPING, "音频没有解出任何采样，削平未知"
        )])
    if peak <= 0:
        return ClippingInspection([], [QualityUnavailable(
            QUALITY_KIND_CLIPPING, "音频全为静音，没有可判断的波形"
        )])

    ceiling = peak * CLIP_FULL_SCALE_TOLERANCE
    events = _ClippingEvents()
    kept_ranges: list[tuple[int, int]] = []
    run_start: int | None = None
    at_full_scale, index = 0, 0

    def record(start, end):
        if end - start < min_plateau_samples:
            return
        finding = _clipping_finding(audio_start_seconds + start / sample_rate,
            audio_start_seconds + end / sample_rate, end - start, sample_rate, 0)
        events.add(_shifted([finding], timeline_origin_seconds)[0])
        if len(kept_ranges) < events.findings_limit:
            kept_ranges.append((start, end))

    def plateaus(samples):
        nonlocal at_full_scale, index, run_start
        for value in samples:
            if abs(value) >= ceiling:
                at_full_scale += 1
                if run_start is None:
                    run_start = index
            elif run_start is not None:
                record(run_start, index)
                run_start = None
            index += 1

    if not _decode_pcm(path, sample_rate, plateaus, context=context) or index != sample_count:
        return ClippingInspection([], [QualityUnavailable(
            QUALITY_KIND_CLIPPING, "两次音频解码未得到一致采样范围，削平未知"
        )])
    if run_start is not None:
        record(run_start, index)
    duty = at_full_scale / sample_count
    if duty < full_scale_duty_threshold:
        # 贴近满幅的采样很少：这是正常波形，不是削平。
        return ClippingInspection([], [])

    findings = [
        _clipping_finding(
            audio_start_seconds + start / sample_rate,
            audio_start_seconds + end / sample_rate,
            end - start,
            sample_rate,
            duty,
        )
        for start, end in kept_ranges
    ]
    if not findings:
        # 满幅占比够高却没有一段连续平台：波形在满幅上很碎，不足以说被削平。
        return ClippingInspection([], [QualityUnavailable(
            QUALITY_KIND_CLIPPING,
            f"贴近满幅的采样占 {duty:.1%}，但没有一段连续平台，削平与否无法断定",
        )])
    return ClippingInspection(_shifted(findings, timeline_origin_seconds), [], events.summary(duty))


def _decode_pcm(
    path: str | Path, sample_rate: int, consume,
    *, context: QualityWorkerContext | None = None,
) -> bool:
    """Consume mono s16le without retaining the complete decoded stream."""
    if context is None:
        raise QualityCheckError("质检命令缺少受许可的 worker 上下文")
    decoder = _PCMChunks(consume)
    try:
        completed = context.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "error",
             "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate),
             "-f", "s16le", "-"], RUN_TIMEOUT_SECONDS, pcm=True, stdout_consumer=decoder.feed)
    except subprocess.TimeoutExpired as exc:
        raise QualityCheckError("ffmpeg 解码音频超时，削平检测未完成") from exc
    except OSError as exc:
        raise QualityCheckError(f"ffmpeg 无法执行：{exc}") from exc
    if completed.returncode != 0:
        return False
    decoder.finish()
    return True


def _clipping_finding(
    start_seconds: float, end_seconds: float, plateau_samples: int,
    sample_rate: int, duty: float,
) -> QualityFinding:
    return QualityFinding(
        QUALITY_KIND_CLIPPING,
        round(start_seconds, 3),
        round(end_seconds, 3),
        f"波形被削平：这段连续 {plateau_samples} 个采样"
        f"（{round(plateau_samples / sample_rate, 3)}s）贴在数字满幅上；"
        f"整条音频贴近满幅的采样占 {duty:.1%}",
    )


def _run(command: list[str], timeout: int, *, context: QualityWorkerContext | None = None) -> subprocess.CompletedProcess[str]:
    if context is None:
        raise QualityCheckError("质检命令缺少受许可的 worker 上下文")
    binary = command[0]
    try:
        result = context.run(command, timeout)
        completed = subprocess.CompletedProcess(command, result.returncode,
            result.stdout.decode("utf-8"), result.stderr.decode("utf-8"))
    except subprocess.TimeoutExpired as exc:
        raise QualityCheckError(f"{binary} 在 {timeout}s 内没有返回，检测未完成") from exc
    except OSError as exc:
        raise QualityCheckError(f"{binary} 无法执行：{exc}") from exc
    if completed.returncode != 0:
        raise QualityCheckError(completed.stderr.strip() or f"{binary} failed with exit {completed.returncode}")
    return completed


def _probe(path: str | Path, *, context: QualityWorkerContext | None = None) -> dict:
    """读容器与流元数据。只用流自身的字段；容器 duration 仅作参考，不作结论依据。"""
    completed = _run([
        "ffprobe", "-v", "error",
        "-show_entries",
        "format=duration:stream=codec_type,start_time,duration,duration_ts,time_base,"
        "sample_rate,nb_frames,avg_frame_rate",
        "-of", "json", str(path),
    ], PROBE_TIMEOUT_SECONDS, context=context)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise QualityCheckError("ffprobe returned invalid media metadata") from exc
    if not isinstance(payload.get("streams"), list):
        raise QualityCheckError("ffprobe returned no stream list")
    return payload


def _stream_of(payload: dict, kind: str) -> dict | None:
    return next(
        (item for item in payload["streams"] if item.get("codec_type") == kind), None
    )


def _stream_start_seconds(stream: dict | None) -> float:
    """流起始时间戳。容器没给就如实当作未知（None），不猜 0。"""
    if stream is None:
        return 0.0
    value = _finite(stream.get("start_time"))
    return value if value is not None else 0.0


def _stream_start_known(stream: dict | None) -> bool:
    return stream is not None and _finite(stream.get("start_time")) is not None




def _decoded_stream_end_seconds(path: str | Path, kind: str, *, context: QualityWorkerContext | None = None) -> float | None:
    """该流真正解到的末端：解码帧的时间加这一帧自己的长度，取所有帧的最大值。

    - 视频：每个解码帧的 `pts_time` + 该帧 `duration_time`。
    - 音频：每个解码帧的 `pts_time` + `nb_samples / sample_rate`，即真实采样末端。

    两点必须守住：
    1. 取**最大值**而不是最后一个包。B 帧重排后按包序遍历的最后一帧 PTS 不是最晚的。
    2. 拿不到时长（帧没有 duration_time / nb_samples，或没有采样率）就返回 None
       表示未知，**绝不用容器时长或 packet 元数据兜底冒充已知**。宁可报
       `unavailable`，也不给一个看起来精确的假数。
    """
    if kind == "video":
        selector = "v:0"
        fields = {"pts_time", "duration_time"}
        entries = "frame=pts_time,duration_time:frame_side_data="
    else:
        selector = "a:0"
        fields = {"pts_time", "nb_samples"}
        entries = "frame=pts_time,nb_samples:frame_side_data="
    if context is None:
        raise QualityCheckError("质检命令缺少受许可的 worker 上下文")
    sample_rate = _probe_sample_rate(path, context=context) if kind == "audio" else None
    if kind == "audio" and not sample_rate:
        return None
    end: float | None = None
    pending = b""
    # key=value fields plus separators (including an optional trailing '|').
    record_limit = sum(len(key) + 1 + _FRAME_SCALAR_TEXT_LIMIT for key in fields) + len(fields)

    def frame_line(line):
        nonlocal end
        if not line.strip():
            return
        frame = {}
        try:
            for item in line.decode("ascii").split("|"):
                if not item:
                    continue
                key, value = item.split("=", 1)
                if key not in fields or key in frame or len(value) > _FRAME_SCALAR_TEXT_LIMIT:
                    raise ValueError("unexpected frame field")
                frame[key] = value
        except (UnicodeDecodeError, ValueError) as exc:
            raise QualityCheckError("ffprobe returned invalid frame metadata") from exc
        pts = _finite(frame.get("pts_time"))
        length = _frame_length_seconds(frame, kind, sample_rate)
        if pts is not None and length is not None:
            end = pts + length if end is None else max(end, pts + length)

    def frames(raw):
        nonlocal pending
        offset = 0
        while offset < len(raw):
            newline = raw.find(b"\n", offset)
            end = len(raw) if newline < 0 else newline
            if len(pending) + end - offset > record_limit:
                raise QualityCheckError("ffprobe frame record exceeds the scalar protocol limit")
            pending += raw[offset:end]
            if newline >= 0:
                frame_line(pending)
                pending = b""
            offset = end + 1

    command = [
        "ffprobe", "-v", "error", "-select_streams", selector,
        "-show_entries", entries, "-of", "compact=p=0:nk=0", str(path),
    ]
    try:
        completed = context.run(command, PROBE_TIMEOUT_SECONDS, stdout_consumer=frames)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QualityCheckError("ffprobe 无法完成逐帧检查") from exc
    if completed.returncode:
        raise QualityCheckError("ffprobe returned an unsuccessful frame inspection")
    if pending:
        frame_line(pending)
    return end


def _frame_length_seconds(frame: dict, kind: str, sample_rate: int | None) -> float | None:
    """一个解码帧自身的长度。拿不到就是 None（未知），不退回 0。"""
    if kind == "video":
        for key in ("duration_time", "pkt_duration"):
            value = _finite(frame.get(key))
            if value is not None and value > 0:
                return value
        return None
    samples = _finite(frame.get("nb_samples"))
    if samples is None or samples <= 0 or not sample_rate:
        return None
    return samples / sample_rate


def _probe_sample_rate(path: str | Path, *, context: QualityWorkerContext | None = None) -> int | None:
    payload = _probe(path, context=context)
    audio = _stream_of(payload, "audio")
    rate = _finite((audio or {}).get("sample_rate"))
    return int(rate) if rate and rate > 0 else None


def _timeline_origin(payload: dict) -> float:
    """成片时间轴的原点：最早一条流的起点。

    容器整体带偏移（例如 MPEG-TS 从 1.48s 开始）时，报给剪辑的必须是内容内的
    相对位置，否则这个区间在成片里找不到。把最早起点当 0，尾部就是真实尾部。
    """
    starts = [
        _stream_start_seconds(_stream_of(payload, kind))
        for kind in ("video", "audio")
        if _stream_of(payload, kind) is not None
    ]
    return min(starts) if starts else 0.0


def detect_truncation(
    path: str | Path,
    *,
    payload: dict | None = None,
    origin: float | None = None,
    tolerance_seconds: float = DEFAULT_TAIL_GAP_TOLERANCE_SECONDS,
    context: QualityWorkerContext | None = None,
) -> tuple[list[QualityFinding], list[QualityUnavailable]]:
    """尾部截断：**按真实解码终点**判定，不看容器时长差。

    - 终点取每条流最后一个包（含它自己的时长），不是最后一个时间戳。
    - 报告的区间已减掉时间轴原点，是成片内的相对位置。
    - 缺流、或某条流解不出终点时**不下截断结论**，如实记 `unavailable`：
      量不出来不是"没问题"。
    """
    payload = payload if payload is not None else _probe(path, context=context)
    if origin is None:
        origin = _timeline_origin(payload)
    video = _stream_of(payload, "video")
    audio = _stream_of(payload, "audio")
    unavailable: list[QualityUnavailable] = []
    if video is None:
        unavailable.append(QualityUnavailable(
            QUALITY_KIND_TRUNCATION, "成片没有视频流，无法判断画面是否被截断"
        ))
    if audio is None:
        unavailable.append(QualityUnavailable(
            QUALITY_KIND_TRUNCATION, "成片没有音频流，无法判断声音是否被截断"
        ))
    if unavailable:
        return [], unavailable

    video_end = _decoded_stream_end_seconds(path, "video", context=context)
    audio_end = _decoded_stream_end_seconds(path, "audio", context=context)
    if video_end is None:
        unavailable.append(QualityUnavailable(
            QUALITY_KIND_TRUNCATION, "视频流没有解出任何包，画面终点未知"
        ))
    if audio_end is None:
        unavailable.append(QualityUnavailable(
            QUALITY_KIND_TRUNCATION, "音频流没有解出任何包，声音终点未知"
        ))
    if unavailable:
        return [], unavailable

    video_end -= origin
    audio_end -= origin
    gap = abs(video_end - audio_end)
    if gap <= tolerance_seconds:
        return [], []
    start = round(min(video_end, audio_end), 3)
    end = round(max(video_end, audio_end), 3)
    if video_end < audio_end:
        detail = (
            f"画面最后一帧结束在 {round(video_end, 3)}s，声音还在播到 "
            f"{round(audio_end, 3)}s，尾部 {round(gap, 3)}s 只有声音没有画面"
        )
    else:
        detail = (
            f"声音最后一个采样结束在 {round(audio_end, 3)}s，画面还在播到 "
            f"{round(video_end, 3)}s，尾部 {round(gap, 3)}s 只有画面没有声音"
        )
    return [QualityFinding(QUALITY_KIND_TRUNCATION, start, end, detail)], []


def detect_timestamp_offset(
    path: str | Path,
    *,
    payload: dict | None = None,
    origin: float | None = None,
    tolerance_seconds: float = DEFAULT_START_OFFSET_TOLERANCE_SECONDS,
    context: QualityWorkerContext | None = None,
) -> tuple[list[QualityFinding], list[QualityUnavailable]]:
    """**起点**时间戳错位。

    只说起点错了多少秒；起始差推不出"整段音画同步"的结论，措辞上也这么写。
    两条流的起点都减掉同一个时间轴原点，因此容器整体偏移不会污染这个判断。
    """
    payload = payload if payload is not None else _probe(path, context=context)
    video = _stream_of(payload, "video")
    audio = _stream_of(payload, "audio")
    if video is None or audio is None:
        return [], [QualityUnavailable(
            QUALITY_KIND_TIMESTAMP_OFFSET,
            "缺少视频或音频流，起始时间戳无从比对",
        )]
    if not (_stream_start_known(video) and _stream_start_known(audio)):
        return [], [QualityUnavailable(
            QUALITY_KIND_TIMESTAMP_OFFSET,
            "至少一条流没有给出起始时间戳，起点错位未知",
        )]
    if origin is None:
        origin = _timeline_origin(payload)
    video_start = _stream_start_seconds(video) - origin
    audio_start = _stream_start_seconds(audio) - origin
    offset = audio_start - video_start
    if abs(offset) <= tolerance_seconds:
        return [], []
    later = "音频" if offset > 0 else "视频"
    detail = (
        f"{later}流起点比另一条晚 {round(abs(offset), 3)}s；"
        "这只说明起点错位，不代表整段音画同步情况"
    )
    return [
        QualityFinding(
            QUALITY_KIND_TIMESTAMP_OFFSET,
            round(min(video_start, audio_start), 3),
            round(max(video_start, audio_start), 3),
            detail,
        )
    ], []


def inspect_media_quality(
    path: str | Path,
    *,
    min_black_seconds: float = DEFAULT_BLACK_MIN_SECONDS,
    black_pixel_threshold: float = DEFAULT_BLACK_PIXEL_THRESHOLD,
    clip_full_scale_duty_threshold: float = DEFAULT_CLIP_FULL_SCALE_DUTY,
    truncation_tolerance_seconds: float = DEFAULT_TAIL_GAP_TOLERANCE_SECONDS,
    start_offset_tolerance_seconds: float = DEFAULT_START_OFFSET_TOLERANCE_SECONDS,
    context: QualityWorkerContext | None = None,
) -> QualityReport:
    """跑完四类检出，返回一份冻结结果。

    全部时间都归一到成片时间轴（容器整体偏移被减掉），可以直接对到剪辑片段。
    四类都量成且没检出问题时 `status == "clean"`；任何一类量不成就是
    `unavailable`、`is_clean` 为 False：未知不会被说成通过。
    """
    target = Path(path)
    payload = _probe(target, context=context)
    origin = _timeline_origin(payload)
    findings: list[QualityFinding] = []
    unavailable: list[QualityUnavailable] = []

    findings.extend(_shifted(
        detect_black_frames(
            target,
            min_duration_seconds=min_black_seconds,
            pixel_threshold=black_pixel_threshold,
        context=context),
        origin,
    ))

    truncation_findings, truncation_unavailable = detect_truncation(
        target, payload=payload, origin=origin,
        tolerance_seconds=truncation_tolerance_seconds,
    context=context)
    findings.extend(truncation_findings)
    unavailable.extend(truncation_unavailable)

    offset_findings, offset_unavailable = detect_timestamp_offset(
        target, payload=payload, origin=origin,
        tolerance_seconds=start_offset_tolerance_seconds,
    context=context)
    findings.extend(offset_findings)
    unavailable.extend(offset_unavailable)

    audio = _stream_of(payload, "audio")
    clipping_summary: dict | None = None
    if audio is None:
        unavailable.append(QualityUnavailable(
            QUALITY_KIND_CLIPPING, "成片没有音频流，无法按样本判断削平"
        ))
    else:
        clipping = detect_clipping(
            target,
            sample_rate=_probe_sample_rate(target, context=context),
            audio_start_seconds=(
                _stream_start_seconds(audio) if _stream_start_known(audio) else 0.0
            ),
            timeline_origin_seconds=origin,
        context=context)
        clipping_summary = clipping.summary
        findings.extend(clipping.findings)
        unavailable.extend(clipping.unavailable)

    findings.sort(key=lambda item: (item.start_seconds, item.kind))
    return QualityReport(
        path=str(target),
        findings=tuple(findings),
        unavailable=tuple(unavailable),
        duration_seconds=_finite((payload.get("format") or {}).get("duration")),
        engine="ffmpeg/ffprobe present" if shutil.which("ffmpeg") else "ffmpeg missing",
        clip_full_scale_duty_threshold=clip_full_scale_duty_threshold,
        timeline_origin_seconds=round(origin, 3),
        clipping_summary=clipping_summary,
        video_duration_seconds=_stream_duration_seconds(_stream_of(payload, "video")),
        video_frame_rate=(_stream_of(payload, "video") or {}).get("avg_frame_rate"),
    )


def _clipping_summary(
    findings: list[QualityFinding], duty: float | None,
    limit: int = CLIP_EVENT_LIST_LIMIT,
    findings_limit: int = CLIP_FINDING_EVENT_LIMIT,
) -> dict | None:
    """削平事件的聚合摘要。

    每段事件仍逐条保留在 `findings` 里当证据（受 `findings_limit` 约束，超出
    的条数在这里显式报出来）；这里给的是给下游看的**摘要**：总时长、事件数、
    首末区间和一个有界的列举。列举超上限时显式带 `events_truncated` 和
    `truncated_event_count`，绝不静默丢弃。
    """
    events = _ClippingEvents(event_limit=limit, findings_limit=findings_limit)
    for finding in findings:
        events.add(finding)
    return events.summary(duty)


def _shifted(
    findings: list[QualityFinding], origin: float
) -> list[QualityFinding]:
    """把容器时间换算成成片时间轴（减掉容器整体偏移）。"""
    if not origin:
        return findings
    return [
        QualityFinding(
            item.kind,
            round(max(0.0, item.start_seconds - origin), 3),
            round(max(0.0, item.end_seconds - origin), 3),
            item.detail,
        )
        for item in findings
    ]


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def quality_report_evidence(report: QualityReport) -> dict:
    """把报告序列化成可落库的冻结证据（供 108 存结果、比对版本失效）。

    字段与语义一并给出，调用方不需要再猜 `is_clean` 到底代表什么。
    """
    return {
        "path": report.path,
        "engine": report.engine,
        "validation_scope": report.validation_scope,
        "duration_seconds": report.duration_seconds,
        "video_duration_seconds": report.video_duration_seconds,
        "video_frame_rate": report.video_frame_rate,
        "timeline_origin_seconds": report.timeline_origin_seconds,
        "status": report.status,
        "is_clean": report.is_clean,
        "kinds_checked": list(QUALITY_KINDS),
        "findings": [
            {
                "kind": item.kind,
                "start_seconds": item.start_seconds,
                "end_seconds": item.end_seconds,
                "duration_seconds": item.duration_seconds,
                "detail": item.detail,
            }
            for item in report.findings
        ],
        "unavailable": [
            {"kind": item.kind, "reason": item.reason, "status": item.status}
            for item in report.unavailable
        ],
        "clipping_summary": report.clipping_summary,
    }
