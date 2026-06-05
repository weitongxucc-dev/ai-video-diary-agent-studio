from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Sequence

from app.asr import SubtitleSegment


MIN_EFFECTIVE_CHARS = 8
MAX_GAP_MS = 30_000
MAX_WINDOW_SEGMENTS = 3
AUTO_SIMILARITY = 0.84
ABORTED_AUTO_SIMILARITY = 0.76
REVIEW_SIMILARITY = 0.62

_ADJACENT_GAP_MS = 1_000
_ASR_TEXT_REPLACEMENTS = {
    "最后的一句话": "最后一句话",
}


@dataclass(frozen=True)
class TimeRange:
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class RetakeCandidate:
    remove: TimeRange
    keep: TimeRange
    confidence: float
    reason: str


@dataclass(frozen=True)
class RetakeDetection:
    auto_removed: list[RetakeCandidate]
    review_only: list[RetakeCandidate]


@dataclass(frozen=True)
class _TextWindow:
    first_segment: int
    after_last_segment: int
    time_range: TimeRange
    text: str


class RetakeDetector:
    def detect(self, segments: Sequence[SubtitleSegment]) -> RetakeDetection:
        windows = self._windows(sorted(segments, key=lambda segment: segment.start_ms))
        auto_removed: list[RetakeCandidate] = []
        review_only: list[RetakeCandidate] = []

        for index, earlier in enumerate(windows):
            for later in windows[index + 1 :]:
                if earlier.after_last_segment > later.first_segment:
                    continue
                gap_ms = later.time_range.start_ms - earlier.time_range.end_ms
                if gap_ms < 0 or gap_ms > MAX_GAP_MS:
                    continue

                similarity = _text_similarity(earlier.text, later.text)
                candidate = RetakeCandidate(
                    remove=earlier.time_range,
                    keep=later.time_range,
                    confidence=round(similarity, 3),
                    reason="repeated_take",
                )
                if similarity >= AUTO_SIMILARITY:
                    auto_removed.append(candidate)
                elif (
                    gap_ms <= _ADJACENT_GAP_MS
                    and similarity >= ABORTED_AUTO_SIMILARITY
                    and len(later.text) >= len(earlier.text)
                ):
                    auto_removed.append(
                        RetakeCandidate(
                            remove=candidate.remove,
                            keep=candidate.keep,
                            confidence=candidate.confidence,
                            reason="interrupted_take",
                        )
                    )
                elif gap_ms <= _ADJACENT_GAP_MS and similarity >= REVIEW_SIMILARITY:
                    review_only.append(
                        RetakeCandidate(
                            remove=candidate.remove,
                            keep=candidate.keep,
                            confidence=candidate.confidence,
                            reason="possible_retake",
                        )
                    )

        merged_auto_removed = _merge_auto_removed(_deduplicate(auto_removed))
        merged_auto_removed = _expand_auto_removed_keep_ranges(
            merged_auto_removed,
            segments,
        )
        return RetakeDetection(
            auto_removed=merged_auto_removed,
            review_only=_review_candidates_without_auto_removals(
                _deduplicate(review_only),
                merged_auto_removed,
            ),
        )

    def _windows(self, segments: Sequence[SubtitleSegment]) -> list[_TextWindow]:
        windows: list[_TextWindow] = []
        for first_segment in range(len(segments)):
            for segment_count in range(1, MAX_WINDOW_SEGMENTS + 1):
                after_last_segment = first_segment + segment_count
                if after_last_segment > len(segments):
                    break
                selected = segments[first_segment:after_last_segment]
                text = _normalize_text("".join(segment.text for segment in selected))
                if len(text) < MIN_EFFECTIVE_CHARS:
                    continue
                windows.append(
                    _TextWindow(
                        first_segment=first_segment,
                        after_last_segment=after_last_segment,
                        time_range=TimeRange(
                            start_ms=selected[0].start_ms,
                            end_ms=selected[-1].end_ms,
                        ),
                        text=text,
                    )
                )
        return windows


def retake_detection_payload(report: RetakeDetection) -> dict[str, object]:
    return {
        "auto_removed": [_candidate_payload(candidate) for candidate in report.auto_removed],
        "review_only": [_candidate_payload(candidate) for candidate in report.review_only],
    }


def _candidate_payload(candidate: RetakeCandidate) -> dict[str, object]:
    return {
        "remove": _time_range_payload(candidate.remove),
        "keep": _time_range_payload(candidate.keep),
        "confidence": round(candidate.confidence, 3),
        "reason": candidate.reason,
    }


def _time_range_payload(time_range: TimeRange) -> dict[str, float]:
    return {
        "start": round(time_range.start_ms / 1_000, 3),
        "end": round(time_range.end_ms / 1_000, 3),
    }


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    for source, replacement in _ASR_TEXT_REPLACEMENTS.items():
        normalized = normalized.replace(source, replacement)
    normalized = re.sub(r"[嗯呃额]+", "", normalized)
    return "".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", normalized))


def _text_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _deduplicate(candidates: Sequence[RetakeCandidate]) -> list[RetakeCandidate]:
    by_remove_range: dict[TimeRange, RetakeCandidate] = {}
    for candidate in candidates:
        current = by_remove_range.get(candidate.remove)
        if current is None or _candidate_priority(candidate) > _candidate_priority(current):
            by_remove_range[candidate.remove] = candidate
    return sorted(
        by_remove_range.values(),
        key=lambda candidate: (candidate.remove.start_ms, candidate.remove.end_ms),
    )


def _candidate_priority(candidate: RetakeCandidate) -> tuple[int, float, int]:
    return (
        candidate.keep.start_ms,
        candidate.confidence,
        candidate.keep.end_ms - candidate.keep.start_ms,
    )


def _merge_auto_removed(candidates: Sequence[RetakeCandidate]) -> list[RetakeCandidate]:
    merged: list[RetakeCandidate] = []
    for candidate in candidates:
        if not merged or candidate.remove.start_ms > merged[-1].remove.end_ms:
            merged.append(candidate)
            continue

        previous = merged[-1]
        merged_end_ms = max(previous.remove.end_ms, candidate.remove.end_ms)
        available_keeps = [
            item for item in (previous, candidate) if item.keep.start_ms >= merged_end_ms
        ]
        preferred_keep = min(
            available_keeps or [previous, candidate],
            key=lambda item: (
                item.keep.start_ms,
                -item.confidence,
                -(item.keep.end_ms - item.keep.start_ms),
            ),
        )
        merged[-1] = RetakeCandidate(
            remove=TimeRange(
                start_ms=previous.remove.start_ms,
                end_ms=merged_end_ms,
            ),
            keep=preferred_keep.keep,
            confidence=max(previous.confidence, candidate.confidence),
            reason=preferred_keep.reason,
        )
    return merged


def _expand_auto_removed_keep_ranges(
    candidates: Sequence[RetakeCandidate],
    segments: Sequence[SubtitleSegment],
) -> list[RetakeCandidate]:
    expanded: list[RetakeCandidate] = []
    for candidate in candidates:
        keep_end_ms = candidate.keep.end_ms
        absorbed_segments = 0

        for segment in segments:
            if segment.end_ms <= keep_end_ms:
                continue

            if segment.start_ms - keep_end_ms > _ADJACENT_GAP_MS:
                break

            keep_end_ms = segment.end_ms
            absorbed_segments += 1
            if absorbed_segments >= 2:
                break

        if keep_end_ms == candidate.keep.end_ms:
            expanded.append(candidate)
        else:
            expanded.append(
                RetakeCandidate(
                    remove=candidate.remove,
                    keep=TimeRange(
                        start_ms=candidate.keep.start_ms,
                        end_ms=keep_end_ms,
                    ),
                    confidence=candidate.confidence,
                    reason=candidate.reason,
                )
            )
    return expanded


def _review_candidates_without_auto_removals(
    review_only: Sequence[RetakeCandidate],
    auto_removed: Sequence[RetakeCandidate],
) -> list[RetakeCandidate]:
    return [
        candidate
        for candidate in review_only
        if not any(_overlaps(candidate.remove, auto.remove) for auto in auto_removed)
    ]


def _overlaps(left: TimeRange, right: TimeRange) -> bool:
    return left.start_ms < right.end_ms and right.start_ms < left.end_ms
