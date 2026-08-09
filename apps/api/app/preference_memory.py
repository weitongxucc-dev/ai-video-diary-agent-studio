from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.models import FeedbackEvent, FeedbackValue


@dataclass(frozen=True)
class PreferenceSignal:
    signal: str
    evidence_count: int
    confidence: float
    enabled: bool = True


@dataclass(frozen=True)
class PreferenceMemory:
    prefer: list[PreferenceSignal]
    avoid: list[PreferenceSignal]


@dataclass
class PreferenceState:
    disabled_signals: set[str] = field(default_factory=set)

    def disable(self, signal: str) -> None:
        self.disabled_signals.add(signal)

    def restore(self, signal: str) -> None:
        self.disabled_signals.discard(signal)

    def is_enabled(self, signal: str) -> bool:
        return signal not in self.disabled_signals

    def to_payload(self) -> dict[str, object]:
        return {"disabled_signals": sorted(self.disabled_signals)}

    @classmethod
    def from_payload(cls, payload: dict[str, object] | None) -> PreferenceState:
        if payload is None:
            return cls()
        return cls(
            disabled_signals={str(s) for s in payload.get("disabled_signals", [])},
        )


def build_preference_memory(
    feedback_events: list[FeedbackEvent],
    *,
    limit: int = 6,
    preference_state: PreferenceState | None = None,
) -> PreferenceMemory:
    disabled = preference_state.disabled_signals if preference_state else set()
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
        prefer=_signals(prefer_counts, limit, disabled),
        avoid=_signals(avoid_counts, limit, disabled),
    )


def preference_memory_payload(
    feedback_events: list[FeedbackEvent],
    *,
    limit: int = 6,
    preference_state: PreferenceState | None = None,
) -> dict[str, list[dict[str, object]]]:
    memory = build_preference_memory(
        feedback_events, limit=limit, preference_state=preference_state,
    )
    return {
        "prefer": [_signal_payload(signal) for signal in memory.prefer],
        "avoid": [_signal_payload(signal) for signal in memory.avoid],
    }


def _signals(
    counts: Counter[str], limit: int, disabled: set[str],
) -> list[PreferenceSignal]:
    return [
        PreferenceSignal(
            signal=signal,
            evidence_count=count,
            confidence=round(min(1.0, 0.35 + count * 0.15), 2),
            enabled=signal not in disabled,
        )
        for signal, count in counts.most_common(limit)
    ]


def _signal_payload(signal: PreferenceSignal) -> dict[str, object]:
    return {
        "signal": signal.signal,
        "evidence_count": signal.evidence_count,
        "confidence": signal.confidence,
        "enabled": signal.enabled,
    }
