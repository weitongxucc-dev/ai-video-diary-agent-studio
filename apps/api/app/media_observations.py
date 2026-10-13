"""Shared speech and visual observation values, independent of model execution."""


from dataclasses import dataclass, field
from typing import Literal


OCR_RETRIEVAL_MIN_CONFIDENCE = 0.8


@dataclass(frozen=True)
class SubtitleSegment:
    start_ms: int
    end_ms: int
    text: str


@dataclass
class MaterialSpeechAnalysis:
    status: Literal["ready", "blocked", "failed"]
    source_fingerprint: str = ""
    model_id: str = ""
    observations: list[SubtitleSegment] = field(default_factory=list)
    message: str = ""


@dataclass
class SegmentUnderstanding:
    status: Literal["ready", "blocked", "failed"]
    fingerprint: str = ""
    model_id: str = ""
    sample_times: list[float] = field(default_factory=list)
    summary: str = ""
    facts: list[dict] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    ocr_status: Literal["ready", "blocked", "failed"] = "blocked"
    ocr_model_id: str = ""
    ocr_frames: list[dict] = field(default_factory=list)
    message: str = ""
