from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}


@dataclass(frozen=True)
class PendingVideo:
    file_name: str
    path: str
    size_bytes: int
    modified_at: datetime


@dataclass(frozen=True)
class ProcessedVideo:
    file_name: str
    output_path: str
    source_file_name: str
    edit_plan_path: str | None = None
    source_file_names: list[str] = field(default_factory=list)
    dedup_report_path: str | None = None


class VideoInbox:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def pending_videos(self) -> list[PendingVideo]:
        if not self.path.exists() or not self.path.is_dir():
            return []

        videos = [
            self._to_pending_video(item)
            for item in self.path.iterdir()
            if item.is_file() and item.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
        ]
        return sorted(videos, key=lambda video: video.modified_at, reverse=True)

    def _to_pending_video(self, path: Path) -> PendingVideo:
        stat = path.stat()
        return PendingVideo(
            file_name=path.name,
            path=str(path),
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )


class FfmpegVideoProcessor:
    def __init__(
        self,
        settings: Settings,
        runner: Callable[[list[str]], None] | None = None,
        silence_detector: Callable[[Path], list[tuple[float, float]]] | None = None,
        duration_reader: Callable[[Path], float] | None = None,
    ) -> None:
        self.inbox_path = Path(settings.video_inbox_path).expanduser()
        self.output_path = Path(settings.video_output_path).expanduser()
        self.runner = runner or self._run_command
        self.silence_detector = silence_detector or self._detect_silences
        self.duration_reader = duration_reader or self._probe_duration

    def process(self, file_name: str) -> ProcessedVideo:
        input_path = self._validated_input_path(file_name)
        return self._process_input_path(
            input_path,
            output_stem=input_path.stem,
            source_file_name=file_name,
            source_file_names=[file_name],
        )

    def process_batch(self, file_names: list[str]) -> ProcessedVideo:
        if len(file_names) < 2:
            raise ValueError("at least two video files are required")
        if len(set(file_names)) != len(file_names):
            raise ValueError("duplicate video files are not allowed")
        input_paths = [
            self._validated_input_path(file_name)
            for file_name in file_names
        ]
        self.output_path.mkdir(parents=True, exist_ok=True)
        batch_stem = f"{input_paths[0].stem}_batch_{len(input_paths)}"
        merged_path = self.output_path / f"{batch_stem}_merged.mp4"
        self.runner(self._build_batch_merge_command(input_paths, merged_path))
        return self._process_input_path(
            merged_path,
            output_stem=batch_stem,
            source_file_name=file_names[0],
            source_file_names=[*file_names],
            edit_plan_extra={
                "source_files": [*file_names],
                "merged_source_path": str(merged_path),
            },
        )

    def _validated_input_path(self, file_name: str) -> Path:
        if Path(file_name).name != file_name:
            raise ValueError("invalid video file name")
        if Path(file_name).suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            raise ValueError("unsupported video file")

        input_path = self.inbox_path / file_name
        if not input_path.exists() or not input_path.is_file():
            raise ValueError("video file not found")
        return input_path

    def _process_input_path(
        self,
        input_path: Path,
        output_stem: str,
        source_file_name: str,
        source_file_names: list[str],
        edit_plan_extra: dict[str, object] | None = None,
    ) -> ProcessedVideo:
        self.output_path.mkdir(parents=True, exist_ok=True)
        output_file = self.output_path / f"{output_stem}_preview.mp4"
        edit_plan_path = self.output_path / f"{output_stem}_preview.edit_plan.json"
        silences = self._long_silences(input_path)
        keep_ranges = self._keep_ranges(silences, self._safe_duration(input_path))
        self._write_edit_plan(edit_plan_path, silences, keep_ranges, edit_plan_extra)
        command = self._build_cut_command(input_path, output_file, keep_ranges)
        self.runner(command)
        return ProcessedVideo(
            file_name=output_file.name,
            output_path=str(output_file),
            source_file_name=source_file_name,
            edit_plan_path=str(edit_plan_path),
            source_file_names=source_file_names,
        )

    def _run_command(self, command: list[str]) -> None:
        subprocess.run(command, check=True)

    def _long_silences(self, input_path: Path) -> list[tuple[float, float]]:
        return [
            (round(start, 3), round(end, 3))
            for start, end in self.silence_detector(input_path)
            if end - start >= 0.8
        ]

    def _safe_duration(self, input_path: Path) -> float:
        try:
            return self.duration_reader(input_path)
        except (subprocess.CalledProcessError, ValueError):
            return 0.0

    def _keep_ranges(
        self,
        silences: list[tuple[float, float]],
        duration: float,
    ) -> list[tuple[float, float]]:
        if duration <= 0 or not silences:
            return []
        keep_ranges: list[tuple[float, float]] = []
        cursor = 0.0
        padding = 0.15
        for start, end in silences:
            cut_start = max(cursor, start + padding)
            cut_end = max(cut_start, end - padding)
            if cut_start > cursor:
                keep_ranges.append((round(cursor, 3), round(cut_start, 3)))
            cursor = cut_end
        if cursor < duration:
            keep_ranges.append((round(cursor, 3), round(duration, 3)))
        return [
            (start, end)
            for start, end in keep_ranges
            if end - start >= 0.25
        ]

    def _write_edit_plan(
        self,
        edit_plan_path: Path,
        silences: list[tuple[float, float]],
        keep_ranges: list[tuple[float, float]],
        extra_payload: dict[str, object] | None = None,
    ) -> None:
        payload = {
            "strategy": "conservative_silence_cut_then_1.05x_speed",
            "removed_silences": [
                {"start": start, "end": end, "duration": round(end - start, 3)}
                for start, end in silences
            ],
            "keep_ranges": [
                {"start": start, "end": end, "duration": round(end - start, 3)}
                for start, end in keep_ranges
            ],
            **(extra_payload or {}),
        }
        edit_plan_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _build_cut_command(
        self,
        input_path: Path,
        output_file: Path,
        keep_ranges: list[tuple[float, float]],
    ) -> list[str]:
        base = ["ffmpeg", "-y", "-i", str(input_path)]
        if not keep_ranges:
            return [
                *base,
                "-filter:v",
                "setpts=PTS/1.05",
                "-filter:a",
                "atempo=1.05,loudnorm",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                str(output_file),
            ]

        trim_parts: list[str] = []
        concat_inputs: list[str] = []
        for index, (start, end) in enumerate(keep_ranges):
            trim_parts.append(
                f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{index}]"
            )
            trim_parts.append(
                f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{index}]"
            )
            concat_inputs.append(f"[v{index}][a{index}]")
        filter_complex = (
            ";".join(trim_parts)
            + ";"
            + "".join(concat_inputs)
            + f"concat=n={len(keep_ranges)}:v=1:a=1[vcat][acat];"
            + "[vcat]setpts=PTS/1.05[v];[acat]atempo=1.05,loudnorm[a]"
        )
        return [
            *base,
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            str(output_file),
        ]

    def _build_batch_merge_command(
        self,
        input_paths: list[Path],
        output_file: Path,
    ) -> list[str]:
        inputs = [
            argument
            for input_path in input_paths
            for argument in ["-i", str(input_path)]
        ]
        filters: list[str] = []
        concat_inputs: list[str] = []
        for index in range(len(input_paths)):
            filters.append(
                f"[{index}:v]scale=720:1280:force_original_aspect_ratio=decrease,"
                f"pad=720:1280:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1[v{index}]"
            )
            filters.append(
                f"[{index}:a]aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
            )
            concat_inputs.append(f"[v{index}][a{index}]")
        filter_complex = (
            ";".join(filters)
            + ";"
            + "".join(concat_inputs)
            + f"concat=n={len(input_paths)}:v=1:a=1[v][a]"
        )
        return [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            str(output_file),
        ]

    def _detect_silences(self, input_path: Path) -> list[tuple[float, float]]:
        command = [
            "ffmpeg",
            "-i",
            str(input_path),
            "-af",
            "silencedetect=noise=-38dB:d=0.8",
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        output = f"{result.stdout}\n{result.stderr}"
        starts = [
            float(match.group(1))
            for match in re.finditer(r"silence_start:\s*([0-9.]+)", output)
        ]
        ends = [
            float(match.group(1))
            for match in re.finditer(r"silence_end:\s*([0-9.]+)", output)
        ]
        return list(zip(starts, ends, strict=False))

    def _probe_duration(self, input_path: Path) -> float:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_path),
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return float(result.stdout.strip())
