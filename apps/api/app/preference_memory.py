from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.models import FeedbackEvent, FeedbackValue


@dataclass(frozen=True)
class PreferenceSignal:
    signal: str
    evidence_count: int
    confidence: float


@dataclass(frozen=True)
class PreferenceMemory:
    prefer: list[PreferenceSignal]
    avoid: list[PreferenceSignal]


def build_preference_memory(
    feedback_events: list[FeedbackEvent],
    *,
    limit: int = 6,
) -> PreferenceMemory:
    prefer_counts: Counter[str] = Counter()
    avoid_counts: Counter[str] = Counter()
    for event in feedback_events:
        if not event.active or not event.reason:
            continue
        reason = event.reason.strip()
        if not reason:
            continue
        if event.value == FeedbackValue.UP:
            prefer_counts[reason] += 1
        elif event.value == FeedbackValue.DOWN:
            avoid_counts[reason] += 1
    return PreferenceMemory(
        prefer=_signals(prefer_counts, limit),
        avoid=_signals(avoid_counts, limit),
    )


def preference_memory_payload(
    feedback_events: list[FeedbackEvent],
    *,
    limit: int = 6,
) -> dict[str, list[dict[str, object]]]:
    memory = build_preference_memory(feedback_events, limit=limit)
    return {
        "prefer": [_signal_payload(signal) for signal in memory.prefer],
        "avoid": [_signal_payload(signal) for signal in memory.avoid],
    }


def _signals(counts: Counter[str], limit: int) -> list[PreferenceSignal]:
    return [
        PreferenceSignal(
            signal=signal,
            evidence_count=count,
            confidence=round(min(1.0, 0.35 + count * 0.15), 2),
        )
        for signal, count in counts.most_common(limit)
    ]


def _signal_payload(signal: PreferenceSignal) -> dict[str, object]:
    return {
        "signal": signal.signal,
        "evidence_count": signal.evidence_count,
        "confidence": signal.confidence,
    }
