"""Exact profile reference inputs; execution uses the caller's existing Grant.

No arbitrary commands, independent queue or model provider lives here. The
repository supplies member-qualified, source-bound facts and rechecks them
before publishing an immutable derived input.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4


@dataclass(frozen=True)
class ProfileReferenceInput:
    workspace_id: str
    profile_id: str
    profile_version: str
    asset_id: str | None
    segment_id: str | None
    source_path: str
    source_type: str
    kind: Literal["voice_audio", "person_visual"]
    start_seconds: float | None
    end_seconds: float | None
    source_duration_seconds: float | None
    size_bytes: int
    mtime_ns: int
    ctime_ns: int
    inode: int
    device: int

    @classmethod
    def from_source(cls, *, source_path: Path, **facts):
        stat = source_path.stat()
        return cls(source_path=str(source_path.resolve()), size_bytes=stat.st_size,
                   mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns,
                   inode=stat.st_ino, device=stat.st_dev, **facts)

    @property
    def needs_derivation(self) -> bool:
        return ((self.kind == "voice_audio" and Path(self.source_path).suffix.lower() != ".wav")
                or (self.start_seconds is not None and
                    (self.start_seconds != 0 or self.end_seconds != self.source_duration_seconds)))

    @property
    def identity(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class ProfileReferenceArtifact:
    path: Path
    source: ProfileReferenceInput
    identity: str
    derived: bool
    duration_seconds: float | None


def _command(source: ProfileReferenceInput, target: Path, *, threads: int) -> list[str]:
    """Typed audio/video transformations only, never user-supplied filters."""
    start = source.start_seconds or 0
    end = source.end_seconds or source.source_duration_seconds
    if end is None or not math.isfinite(end) or end <= start:
        raise ValueError("派生参考需要有效的准确起止范围")
    common = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-threads", str(threads),
              "-filter_threads", str(threads), "-protocol_whitelist", "file,pipe", "-i", source.source_path]
    if source.kind == "voice_audio":
        return [*common, "-map", "0:a:0", "-af",
                f"atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS",
                "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)]
    if source.source_type != "video":
        raise ValueError("人物时间范围只能来自已登记视频")
    return [*common, "-map", "0:v:0", "-vf",
            f"trim=start={start:.9f}:end={end:.9f},setpts=PTS-STARTPTS",
            "-an", "-c:v", "libx264", "-threads", str(threads),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)]


def _duration(path: Path, source: ProfileReferenceInput, grant_context) -> float:
    if source.kind == "voice_audio":
        with wave.open(str(path), "rb") as audio:
            if audio.getframerate() != 16000 or audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                raise ValueError("派生声音不是有效单声道PCM WAV")
            frames = audio.getnframes()
            if len(audio.readframes(frames)) != frames * 2:
                raise ValueError("派生声音文件已截断")
            actual = frames / audio.getframerate()
            tolerance = 1 / audio.getframerate()
            if not 1 <= actual <= 120:
                raise ValueError("派生声音参考时长必须在1–120秒之间")
    else:
        completed = grant_context.run(["ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,avg_frame_rate", "-of", "json", str(path)])
        if completed.returncode:
            raise ValueError("派生人物视频不能被读取")
        media = json.loads(completed.stdout)
        streams = media["streams"]
        video = next((item for item in streams if item["codec_type"] == "video"), None)
        if video is None or any(item["codec_type"] == "audio" for item in streams):
            raise ValueError("派生人物参考必须是独立视频画面")
        numerator, denominator = map(int, video["avg_frame_rate"].split("/"))
        if numerator <= 0 or denominator <= 0:
            raise ValueError("派生视频帧率无效")
        tolerance = denominator / numerator + .001  # the real source's frame precision
        actual = float(media["format"]["duration"])
    expected = (source.end_seconds or source.source_duration_seconds) - (source.start_seconds or 0)
    if not math.isfinite(actual) or actual <= 0 or abs(actual - expected) > tolerance:
        raise ValueError("派生参考时长与明确范围不一致")
    return actual


def materialize_reference(source: ProfileReferenceInput, *, output_root: Path,
                          grant_context, recheck) -> ProfileReferenceArtifact:
    if grant_context is None:
        raise PermissionError("参考派生需要设备许可")
    recheck()
    # Admission uses registered duration/type and file revision only. Actual
    # stream verification belongs to this same registered, bounded execution.
    if source.source_type == "image":
        from PIL import Image
        with Image.open(source.source_path) as image:
            image.verify()
    elif Path(source.source_path).suffix.lower() != ".wav":
        completed = grant_context.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-show_entries", "format=duration:stream=codec_type,avg_frame_rate", "-of", "json", source.source_path])
        if completed.returncode:
            raise ValueError("来源参考媒体无效，未派生输入")
        media = json.loads(completed.stdout)
        stream_type = "audio" if source.kind == "voice_audio" else "video"
        if not any(item["codec_type"] == stream_type for item in media["streams"]):
            raise ValueError("来源参考缺少所选类型的真实媒体轨")
        duration = float(media["format"]["duration"])
        if (not math.isfinite(duration) or duration <= 0 or
                (source.end_seconds is not None and source.end_seconds > duration)):
            raise ValueError("参考范围超出真实媒体时长")
        video = next((item for item in media["streams"] if item["codec_type"] == "video"), None)
        if video is not None:
            numerator, denominator = map(int, video["avg_frame_rate"].split("/"))
            if numerator <= 0 or denominator <= 0:
                raise ValueError("来源参考视频帧率无效")
            tolerance = denominator / numerator + .001
        else:
            tolerance = .001
        if source.source_duration_seconds is None or abs(duration - source.source_duration_seconds) > tolerance:
            raise ValueError("来源登记时长与真实媒体不一致，请重新登记参考版本")
    if not source.needs_derivation:
        return ProfileReferenceArtifact(Path(source.source_path), source, source.identity,
                                        False, source.source_duration_seconds)
    if not source.asset_id:
        raise ValueError("派生参考必须绑定已登记的稳定素材")
    root = output_root / hashlib.sha256(source.workspace_id.encode()).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    extension = ".wav" if source.kind == "voice_audio" else ".mp4"
    target = root / (source.identity + extension)
    manifest = root / (source.identity + ".json")
    if manifest.is_file() and target.is_file():
        try:
            facts = json.loads(manifest.read_text())
            reusable = (target.stat().st_size > 0 and target.stat().st_size <= grant_context.max_output_bytes
                        and facts["source"] == asdict(source)
                        and facts["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest())
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError("派生参考缓存记录无效，请重新定位参考版本") from exc
        if reusable:
            duration = _duration(target, source, grant_context)
            recheck()
            return ProfileReferenceArtifact(target, source, source.identity, True, duration)
        # A changed cache cannot silently become the old selected input.
        raise ValueError("派生参考缓存已变化，请重新定位参考版本")
    temporary = root / (source.identity + "." + uuid4().hex + extension)
    temporary_manifest = temporary.with_suffix(".json")
    published = False
    try:
        completed = grant_context.run(_command(source, temporary, threads=grant_context.threads),
                                      output_paths=(temporary,))
        if completed.returncode:
            raise ValueError("精确参考派生失败：" + completed.stderr.decode("utf-8", errors="replace")[-1200:])
        if not temporary.is_file() or not 0 < temporary.stat().st_size <= grant_context.max_output_bytes:
            raise ValueError("派生参考输出缺失或超过许可预算")
        duration = _duration(temporary, source, grant_context)
        recheck()
        temporary_manifest.write_text(json.dumps({"identity": source.identity, "source": asdict(source),
            "sha256": hashlib.sha256(temporary.read_bytes()).hexdigest(), "duration_seconds": duration}, sort_keys=True))
        grant_context.check()
        os.replace(temporary, target)
        published = True
        os.replace(temporary_manifest, manifest)
        recheck()
        return ProfileReferenceArtifact(target, source, source.identity, True, duration)
    except Exception as exc:
        from app.device_scheduler import RecoveryBlocked
        if isinstance(exc, RecoveryBlocked):
            # Unknown child release retains scratch, never a successful manifest.
            if published:
                manifest.unlink(missing_ok=True)
            raise
        temporary.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)
        if published:
            target.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
        raise
