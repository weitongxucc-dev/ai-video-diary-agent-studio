from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from subprocess import CalledProcessError
from tempfile import TemporaryDirectory

from app.asr import (
    ASRTranscriber,
    LocalWhisperTranscriber,
)
from app.config import Settings
from app.media_commands import (
    build_cut_video_command,
    build_extract_audio_command,
    read_media_duration_seconds,
    run_media_command,
)
from app.retakes import RetakeDetection, RetakeDetector, retake_detection_payload
from app.video import FfmpegVideoProcessor, ProcessedVideo


class RetakeVideoRefiner:
    def __init__(
        self,
        output_path: str | Path,
        transcriber: ASRTranscriber,
        detector: RetakeDetector | None = None,
        runner: Callable[[list[str]], None] | None = None,
        duration_reader: Callable[[Path], float] | None = None,
    ) -> None:
        self.output_path = Path(output_path).expanduser()
        self.transcriber = transcriber
        self.detector = detector or RetakeDetector()
        self.runner = runner or run_media_command
        self.duration_reader = duration_reader or read_media_duration_seconds

    def refine(self, video: ProcessedVideo) -> ProcessedVideo:
        source_path = Path(video.output_path).expanduser()
        report_path = self.output_path / f"{source_path.stem}_dedup_report.json"
        output_path = self.output_path / f"{source_path.stem}_dedup.mp4"
        self.output_path.mkdir(parents=True, exist_ok=True)

        if not self.transcriber.configured:
            self._write_report(
                report_path,
                self._report_payload(
                    status="skipped",
                    video=video,
                    output_path=None,
                    message="Local Whisper is not configured",
                ),
            )
            return self._with_report(video, report_path)

        try:
            with TemporaryDirectory(prefix="ai_video_dedup_") as temporary_dir:
                audio_path = Path(temporary_dir) / f"{source_path.stem}.wav"
                self.runner(build_extract_audio_command(source_path, audio_path))
                transcription = self.transcriber.transcribe(audio_path)
            detection = self.detector.detect(transcription.segments)
        except Exception as exc:
            self._write_report(
                report_path,
                self._report_payload(
                    status="skipped",
                    video=video,
                    output_path=None,
                    message=f"Retake scan skipped: {exc}",
                ),
            )
            return self._with_report(video, report_path)

        report_payload = self._report_payload(
            status="no_changes",
            video=video,
            output_path=None,
            detection=detection,
            message="No high-confidence repeated takes were found",
        )
        if not detection.auto_removed:
            self._write_report(report_path, report_payload)
            return self._with_report(video, report_path)

        try:
            duration = self.duration_reader(source_path)
        except (OSError, CalledProcessError, ValueError) as exc:
            self._write_report(
                report_path,
                self._report_payload(
                    status="skipped",
                    video=video,
                    output_path=None,
                    detection=detection,
                    message=f"Retake scan skipped: {exc}",
                ),
            )
            return self._with_report(video, report_path)

        keep_ranges = self._keep_ranges(detection, duration)
        if not keep_ranges:
            error = ValueError("retake removals would remove the entire video")
            self._write_report(
                report_path,
                {
                    **report_payload,
                    "status": "failed",
                    "output_video": str(output_path),
                    "message": f"Retake refinement failed: {error}",
                },
            )
            raise error
        edit_plan_path = self.output_path / f"{source_path.stem}_dedup.edit_plan.json"
        self._write_edit_plan(
            edit_plan_path,
            report_path,
            source_path,
            output_path,
            detection,
            keep_ranges,
        )
        try:
            self.runner(build_cut_video_command(source_path, output_path, keep_ranges))
        except (OSError, CalledProcessError) as exc:
            self._write_report(
                report_path,
                {
                    **report_payload,
                    "status": "failed",
                    "output_video": str(output_path),
                    "message": f"Retake refinement failed: {exc}",
                },
            )
            raise

        self._write_report(
            report_path,
            {
                **report_payload,
                "status": "applied",
                "output_video": str(output_path),
                "message": "High-confidence repeated takes were removed",
            },
        )
        return ProcessedVideo(
            file_name=output_path.name,
            output_path=str(output_path),
            source_file_name=video.source_file_name,
            edit_plan_path=str(edit_plan_path),
            source_file_names=[*video.source_file_names],
            dedup_report_path=str(report_path),
        )

    def _report_payload(
        self,
        *,
        status: str,
        video: ProcessedVideo,
        output_path: Path | None,
        message: str,
        detection: RetakeDetection | None = None,
    ) -> dict[str, object]:
        detection_payload = retake_detection_payload(
            detection or RetakeDetection(auto_removed=[], review_only=[])
        )
        return {
            "status": status,
            "source_video": video.output_path,
            "output_video": str(output_path) if output_path is not None else None,
            **detection_payload,
            "message": message,
        }

    def _with_report(self, video: ProcessedVideo, report_path: Path) -> ProcessedVideo:
        return ProcessedVideo(
            file_name=video.file_name,
            output_path=video.output_path,
            source_file_name=video.source_file_name,
            edit_plan_path=video.edit_plan_path,
            source_file_names=[*video.source_file_names],
            dedup_report_path=str(report_path),
        )

    def _write_report(self, report_path: Path, payload: dict[str, object]) -> None:
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_edit_plan(
        self,
        edit_plan_path: Path,
        report_path: Path,
        source_path: Path,
        output_path: Path,
        detection: RetakeDetection,
        keep_ranges: list[tuple[float, float]],
    ) -> None:
        payload = {
            "strategy": "whisper_retake_dedup",
            "removed_retakes": retake_detection_payload(detection)["auto_removed"],
            "keep_ranges": [
                {"start": start, "end": end, "duration": round(end - start, 3)}
                for start, end in keep_ranges
            ],
            "source_video": str(source_path),
            "output_video": str(output_path),
            "report_path": str(report_path),
        }
        edit_plan_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _keep_ranges(
        self,
        detection: RetakeDetection,
        duration: float,
    ) -> list[tuple[float, float]]:
        removed_ranges = sorted(
            (
                candidate.remove.start_ms / 1_000,
                candidate.remove.end_ms / 1_000,
            )
            for candidate in detection.auto_removed
        )
        keep_ranges: list[tuple[float, float]] = []
        cursor = 0.0
        for start, end in removed_ranges:
            start = max(cursor, min(start, duration))
            end = max(start, min(end, duration))
            if start > cursor:
                keep_ranges.append((round(cursor, 3), round(start, 3)))
            cursor = end
        if cursor < duration:
            keep_ranges.append((round(cursor, 3), round(duration, 3)))
        return [(start, end) for start, end in keep_ranges if end > start]

class VideoProcessingPipeline:
    def __init__(
        self,
        processor: FfmpegVideoProcessor,
        refiner: RetakeVideoRefiner,
    ) -> None:
        self.processor = processor
        self.refiner = refiner

    def process(self, file_name: str) -> ProcessedVideo:
        return self.refiner.refine(self.processor.process(file_name))

    def process_batch(self, file_names: list[str]) -> ProcessedVideo:
        return self.refiner.refine(self.processor.process_batch(file_names))


def video_processing_pipeline_from_settings(
    settings: Settings,
    processor: FfmpegVideoProcessor | None = None,
) -> VideoProcessingPipeline:
    return VideoProcessingPipeline(
        processor or FfmpegVideoProcessor(settings),
        RetakeVideoRefiner(
            settings.video_output_path,
            LocalWhisperTranscriber(settings),
        ),
    )
