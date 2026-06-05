import json
import subprocess
from pathlib import Path

import pytest

from app.asr import (
    ASRTranscription,
    ASRTranscriptionError,
    LocalWhisperTranscriber,
    SubtitleSegment,
)
from app.config import Settings
from app.retakes import RetakeCandidate, RetakeDetection, TimeRange
from app.video import ProcessedVideo
from app.video_pipeline import (
    RetakeVideoRefiner,
    VideoProcessingPipeline,
    video_processing_pipeline_from_settings,
)


class FakeTranscriber:
    def __init__(
        self,
        *,
        configured: bool = True,
        error: Exception | None = None,
    ) -> None:
        self.configured = configured
        self.error = error
        self.calls: list[Path] = []

    def transcribe(self, audio_path: str | Path) -> ASRTranscription:
        self.calls.append(Path(audio_path))
        if self.error is not None:
            raise self.error
        return ASRTranscription(
            provider="fake_whisper",
            text="第一次口播\n第二次口播",
            segments=[
                SubtitleSegment(0, 2_000, "第一次口播"),
                SubtitleSegment(3_000, 5_000, "第二次口播"),
            ],
        )


class FakeDetector:
    def __init__(self, detection: RetakeDetection) -> None:
        self.detection = detection

    def detect(self, segments: list[SubtitleSegment]) -> RetakeDetection:
        return self.detection


def _auto_detection() -> RetakeDetection:
    return RetakeDetection(
        auto_removed=[
            RetakeCandidate(
                remove=TimeRange(1_000, 2_000),
                keep=TimeRange(3_000, 4_000),
                confidence=0.95,
                reason="repeated_take",
            )
        ],
        review_only=[],
    )


def _first_pass_video(output: Path) -> ProcessedVideo:
    path = output / "talk_preview.mp4"
    output.mkdir()
    path.write_bytes(b"first pass")
    return ProcessedVideo(
        file_name=path.name,
        output_path=str(path),
        source_file_name="talk.mov",
        edit_plan_path=str(output / "talk_preview.edit_plan.json"),
        source_file_names=["talk.mov"],
    )


def _recording_runner(calls: list[list[str]]):
    def runner(command: list[str]) -> None:
        calls.append(command)
        Path(command[-1]).write_bytes(b"placeholder")

    return runner


def test_refiner_applies_auto_removal_and_preserves_first_pass_video(tmp_path):
    output = tmp_path / "output"
    first_pass_video = _first_pass_video(output)
    calls: list[list[str]] = []
    refiner = RetakeVideoRefiner(
        output,
        FakeTranscriber(),
        detector=FakeDetector(_auto_detection()),
        runner=_recording_runner(calls),
        duration_reader=lambda path: 6.0,
    )

    refined = refiner.refine(first_pass_video)

    assert refined.file_name == "talk_preview_dedup.mp4"
    assert refined.output_path == str(output / "talk_preview_dedup.mp4")
    assert refined.edit_plan_path == str(output / "talk_preview_dedup.edit_plan.json")
    assert refined.dedup_report_path == str(output / "talk_preview_dedup_report.json")
    assert "concat=n=2:v=1:a=1" in " ".join(calls[-1])
    report = json.loads(Path(refined.dedup_report_path).read_text())
    assert report["status"] == "applied"
    assert report["output_video"] == str(output / "talk_preview_dedup.mp4")
    edit_plan = json.loads(Path(refined.edit_plan_path).read_text())
    assert edit_plan["strategy"] == "whisper_retake_dedup"
    assert edit_plan["removed_retakes"]
    assert edit_plan["keep_ranges"]
    assert edit_plan["source_video"] == str(output / "talk_preview.mp4")
    assert edit_plan["output_video"] == str(output / "talk_preview_dedup.mp4")
    assert edit_plan["report_path"] == str(output / "talk_preview_dedup_report.json")
    assert (output / "talk_preview.mp4").exists()


def test_refiner_skips_unconfigured_transcriber_without_transcribing(tmp_path):
    output = tmp_path / "output"
    first_pass_video = _first_pass_video(output)
    transcriber = FakeTranscriber(configured=False)
    calls: list[list[str]] = []
    refiner = RetakeVideoRefiner(
        output,
        transcriber,
        runner=_recording_runner(calls),
    )

    refined = refiner.refine(first_pass_video)

    assert refined.file_name == first_pass_video.file_name
    assert refined.output_path == first_pass_video.output_path
    assert refined.dedup_report_path == str(output / "talk_preview_dedup_report.json")
    report = json.loads(Path(refined.dedup_report_path).read_text())
    assert report["status"] == "skipped"
    assert report["output_video"] is None
    assert transcriber.calls == []
    assert calls == []


def test_refiner_reports_no_changes_without_auto_removed_candidates(tmp_path):
    output = tmp_path / "output"
    first_pass_video = _first_pass_video(output)
    calls: list[list[str]] = []

    def unexpected_duration_read(path: Path) -> float:
        raise AssertionError("duration should not be read without auto removals")

    refiner = RetakeVideoRefiner(
        output,
        FakeTranscriber(),
        detector=FakeDetector(RetakeDetection(auto_removed=[], review_only=[])),
        runner=_recording_runner(calls),
        duration_reader=unexpected_duration_read,
    )

    refined = refiner.refine(first_pass_video)

    assert refined.file_name == first_pass_video.file_name
    assert refined.output_path == first_pass_video.output_path
    assert refined.dedup_report_path == str(output / "talk_preview_dedup_report.json")
    report = json.loads(Path(refined.dedup_report_path).read_text())
    assert report["status"] == "no_changes"
    assert report["output_video"] is None
    assert not (output / "talk_preview_dedup.mp4").exists()
    assert len(calls) == 1


def test_pipeline_refines_result_of_batch_processing(tmp_path):
    output = tmp_path / "output"
    first_pass_video = _first_pass_video(output)
    processor_calls: list[list[str]] = []
    refined_video = ProcessedVideo(
        file_name="talk_preview_dedup.mp4",
        output_path=str(output / "talk_preview_dedup.mp4"),
        source_file_name="talk.mov",
    )

    class FakeProcessor:
        def process_batch(self, file_names: list[str]) -> ProcessedVideo:
            processor_calls.append(file_names)
            return first_pass_video

    class FakeRefiner:
        def __init__(self) -> None:
            self.calls: list[ProcessedVideo] = []

        def refine(self, video: ProcessedVideo) -> ProcessedVideo:
            self.calls.append(video)
            return refined_video

    refiner = FakeRefiner()
    pipeline = VideoProcessingPipeline(FakeProcessor(), refiner)

    result = pipeline.process_batch(["part-1.mov", "part-2.mov"])

    assert processor_calls == [["part-1.mov", "part-2.mov"]]
    assert refiner.calls == [first_pass_video]
    assert result == refined_video


def test_pipeline_refines_result_of_single_video_processing(tmp_path):
    output = tmp_path / "output"
    first_pass_video = _first_pass_video(output)
    processor_calls: list[str] = []
    refined_video = ProcessedVideo(
        file_name="talk_preview_dedup.mp4",
        output_path=str(output / "talk_preview_dedup.mp4"),
        source_file_name="talk.mov",
    )

    class FakeProcessor:
        def process(self, file_name: str) -> ProcessedVideo:
            processor_calls.append(file_name)
            return first_pass_video

    class FakeRefiner:
        def __init__(self) -> None:
            self.calls: list[ProcessedVideo] = []

        def refine(self, video: ProcessedVideo) -> ProcessedVideo:
            self.calls.append(video)
            return refined_video

    refiner = FakeRefiner()
    pipeline = VideoProcessingPipeline(FakeProcessor(), refiner)

    result = pipeline.process("talk.mov")

    assert processor_calls == ["talk.mov"]
    assert refiner.calls == [first_pass_video]
    assert result == refined_video


def test_pipeline_factory_uses_local_whisper_transcriber(tmp_path):
    settings = Settings(video_output_path=str(tmp_path / "output"))

    pipeline = video_processing_pipeline_from_settings(settings)

    assert isinstance(pipeline.refiner.transcriber, LocalWhisperTranscriber)


def test_refiner_writes_skipped_report_when_scan_transcription_fails(tmp_path):
    output = tmp_path / "output"
    first_pass_video = _first_pass_video(output)
    calls: list[list[str]] = []
    refiner = RetakeVideoRefiner(
        output,
        FakeTranscriber(error=ASRTranscriptionError("whisper failed")),
        runner=_recording_runner(calls),
    )

    refined = refiner.refine(first_pass_video)

    assert refined.output_path == first_pass_video.output_path
    report = json.loads(Path(refined.dedup_report_path).read_text())
    assert report["status"] == "skipped"
    assert report["output_video"] is None
    assert "whisper failed" in report["message"]
    assert len(calls) == 1


def test_refiner_writes_skipped_report_when_detector_fails(tmp_path):
    output = tmp_path / "output"
    first_pass_video = _first_pass_video(output)
    calls: list[list[str]] = []

    class FailingDetector:
        def detect(self, segments: list[SubtitleSegment]) -> RetakeDetection:
            raise RuntimeError("detector failed")

    refiner = RetakeVideoRefiner(
        output,
        FakeTranscriber(),
        detector=FailingDetector(),
        runner=_recording_runner(calls),
    )

    refined = refiner.refine(first_pass_video)

    assert refined.output_path == first_pass_video.output_path
    report = json.loads(Path(refined.dedup_report_path).read_text())
    assert report["status"] == "skipped"
    assert report["output_video"] is None
    assert "detector failed" in report["message"]
    assert len(calls) == 1


def test_refiner_reports_failed_and_reraises_when_second_cut_fails(tmp_path):
    output = tmp_path / "output"
    first_pass_video = _first_pass_video(output)
    calls: list[list[str]] = []

    def runner(command: list[str]) -> None:
        calls.append(command)
        if command[-1].endswith("_dedup.mp4"):
            raise subprocess.CalledProcessError(1, command)
        Path(command[-1]).write_bytes(b"placeholder")

    refiner = RetakeVideoRefiner(
        output,
        FakeTranscriber(),
        detector=FakeDetector(_auto_detection()),
        runner=runner,
        duration_reader=lambda path: 6.0,
    )

    with pytest.raises(subprocess.CalledProcessError):
        refiner.refine(first_pass_video)

    report = json.loads((output / "talk_preview_dedup_report.json").read_text())
    assert report["status"] == "failed"
    assert report["output_video"] == str(output / "talk_preview_dedup.mp4")


def test_refiner_reports_failed_when_auto_removal_covers_entire_video(tmp_path):
    output = tmp_path / "output"
    first_pass_video = _first_pass_video(output)
    calls: list[list[str]] = []
    detection = RetakeDetection(
        auto_removed=[
            RetakeCandidate(
                remove=TimeRange(0, 6_000),
                keep=TimeRange(6_000, 7_000),
                confidence=0.95,
                reason="repeated_take",
            )
        ],
        review_only=[],
    )
    refiner = RetakeVideoRefiner(
        output,
        FakeTranscriber(),
        detector=FakeDetector(detection),
        runner=_recording_runner(calls),
        duration_reader=lambda path: 6.0,
    )

    with pytest.raises(ValueError, match="remove the entire video"):
        refiner.refine(first_pass_video)

    report = json.loads((output / "talk_preview_dedup_report.json").read_text())
    assert report["status"] == "failed"
    assert report["output_video"] == str(output / "talk_preview_dedup.mp4")
    assert len(calls) == 1


def test_refiner_merges_adjacent_auto_removals_into_complement_ranges(tmp_path):
    output = tmp_path / "output"
    first_pass_video = _first_pass_video(output)
    calls: list[list[str]] = []
    detection = RetakeDetection(
        auto_removed=[
            RetakeCandidate(
                remove=TimeRange(1_000, 2_000),
                keep=TimeRange(3_000, 4_000),
                confidence=0.95,
                reason="repeated_take",
            ),
            RetakeCandidate(
                remove=TimeRange(2_000, 3_000),
                keep=TimeRange(4_000, 5_000),
                confidence=0.94,
                reason="repeated_take",
            ),
        ],
        review_only=[],
    )
    refiner = RetakeVideoRefiner(
        output,
        FakeTranscriber(),
        detector=FakeDetector(detection),
        runner=_recording_runner(calls),
        duration_reader=lambda path: 6.0,
    )

    refined = refiner.refine(first_pass_video)

    assert "concat=n=2:v=1:a=1" in " ".join(calls[-1])
    edit_plan = json.loads(Path(refined.edit_plan_path).read_text())
    assert edit_plan["keep_ranges"] == [
        {"start": 0.0, "end": 1.0, "duration": 1.0},
        {"start": 3.0, "end": 6.0, "duration": 3.0},
    ]
