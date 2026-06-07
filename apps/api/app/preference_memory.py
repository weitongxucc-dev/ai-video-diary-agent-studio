from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from app.models import FeedbackEvent, FeedbackValue, new_id, utc_now


@dataclass(frozen=True)
class PreferenceSignal:
    signal: str
    evidence_count: int
    confidence: float


@dataclass(frozen=True)
class PreferenceMemory:
    prefer: list[PreferenceSignal]
    avoid: list[PreferenceSignal]


@dataclass(frozen=True)
class PreferenceCandidate:
    kind: str
    label: str
    content: str
    source_event_ids: list[str]
    evidence_count: int
    confidence: float
    scope: str


@dataclass
class LongTermPreference:
    kind: str
    content: str
    source_event_ids: list[str]
    confidence: float
    scope: str
    id: str = field(default_factory=lambda: new_id("pref"))
    enabled: bool = True
    deleted: bool = False
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_used_at: datetime | None = None


class LongTermPreferenceRepository:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path).expanduser()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def all(self, *, include_deleted: bool = True) -> list[LongTermPreference]:
        with sqlite3.connect(self._database_path) as conn:
            rows = conn.execute("SELECT payload FROM preference_items").fetchall()
        preferences = [
            self._preference_from_dict(json.loads(payload))
            for (payload,) in rows
        ]
        if not include_deleted:
            preferences = [preference for preference in preferences if not preference.deleted]
        return sorted(preferences, key=lambda item: item.updated_at, reverse=True)

    def active(self) -> list[LongTermPreference]:
        return [
            preference
            for preference in self.all(include_deleted=False)
            if preference.enabled and not preference.deleted
        ]

    def confirm(
        self,
        *,
        kind: str,
        content: str,
        source_event_ids: list[str],
        confidence: float,
        scope: str,
    ) -> LongTermPreference:
        normalized_kind = _normalize_kind(kind)
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("preference content is required")
        existing = self._find_by_kind_and_content(normalized_kind, normalized_content)
        if existing is not None:
            existing.source_event_ids = _unique([*existing.source_event_ids, *source_event_ids])
            existing.confidence = max(existing.confidence, round(confidence, 2))
            existing.scope = scope or existing.scope
            existing.enabled = True
            existing.deleted = False
            existing.updated_at = utc_now()
            self._save(existing)
            return existing
        preference = LongTermPreference(
            kind=normalized_kind,
            content=normalized_content,
            source_event_ids=_unique(source_event_ids),
            confidence=round(max(0.0, min(1.0, confidence)), 2),
            scope=scope or "general",
        )
        self._save(preference)
        return preference

    def disable(self, preference_id: str) -> LongTermPreference:
        preference = self.get(preference_id)
        preference.enabled = False
        preference.updated_at = utc_now()
        self._save(preference)
        return preference

    def restore(self, preference_id: str) -> LongTermPreference:
        preference = self.get(preference_id)
        preference.enabled = True
        preference.deleted = False
        preference.updated_at = utc_now()
        self._save(preference)
        return preference

    def delete(self, preference_id: str) -> LongTermPreference:
        preference = self.get(preference_id)
        preference.enabled = False
        preference.deleted = True
        preference.updated_at = utc_now()
        self._save(preference)
        return preference

    def get(self, preference_id: str) -> LongTermPreference:
        with sqlite3.connect(self._database_path) as conn:
            row = conn.execute(
                "SELECT payload FROM preference_items WHERE id = ?",
                (preference_id,),
            ).fetchone()
        if row is None:
            raise KeyError(preference_id)
        return self._preference_from_dict(json.loads(row[0]))

    def _find_by_kind_and_content(
        self,
        kind: str,
        content: str,
    ) -> LongTermPreference | None:
        for preference in self.all():
            if preference.kind == kind and preference.content == content:
                return preference
        return None

    def _init_database(self) -> None:
        with sqlite3.connect(self._database_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS preference_items (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _save(self, preference: LongTermPreference) -> None:
        payload = json.dumps(_preference_to_dict(preference), ensure_ascii=False)
        with sqlite3.connect(self._database_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO preference_items
                    (id, payload, updated_at)
                VALUES (?, ?, ?)
                """,
                (preference.id, payload, preference.updated_at.isoformat()),
            )

    def _preference_from_dict(self, data: dict[str, object]) -> LongTermPreference:
        return LongTermPreference(
            id=str(data["id"]),
            kind=_normalize_kind(str(data["kind"])),
            content=str(data["content"]),
            source_event_ids=[str(value) for value in data.get("source_event_ids", [])],
            confidence=float(data.get("confidence", 0.5)),
            scope=str(data.get("scope") or "general"),
            enabled=bool(data.get("enabled", True)),
            deleted=bool(data.get("deleted", False)),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            updated_at=datetime.fromisoformat(str(data["updated_at"])),
            last_used_at=datetime.fromisoformat(str(data["last_used_at"]))
            if data.get("last_used_at")
            else None,
        )


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


def build_preference_candidates(
    feedback_events: list[FeedbackEvent],
    *,
    limit: int = 20,
) -> list[PreferenceCandidate]:
    groups: dict[tuple[str, str], list[FeedbackEvent]] = {}
    for event in feedback_events:
        if not event.active or not event.reason:
            continue
        content = event.reason.strip()
        if not content:
            continue
        kind = "prefer" if event.value == FeedbackValue.UP else "avoid"
        groups.setdefault((kind, content), []).append(event)
    candidates = [
        PreferenceCandidate(
            kind=kind,
            label="偏好" if kind == "prefer" else "避开",
            content=content,
            source_event_ids=[event.id for event in events],
            evidence_count=len(events),
            confidence=round(min(1.0, 0.35 + len(events) * 0.15), 2),
            scope=_scope_for_events(events),
        )
        for (kind, content), events in groups.items()
    ]
    return sorted(
        candidates,
        key=lambda candidate: (candidate.evidence_count, candidate.confidence),
        reverse=True,
    )[:limit]


def long_term_preference_events(
    preferences: list[LongTermPreference],
) -> list[FeedbackEvent]:
    events: list[FeedbackEvent] = []
    for preference in preferences:
        if preference.deleted or not preference.enabled:
            continue
        events.append(
            FeedbackEvent(
                target_type="preference_memory",
                target_id=preference.id,
                value=FeedbackValue.UP if preference.kind == "prefer" else FeedbackValue.DOWN,
                reason=preference.content,
                id=f"{preference.id}_event",
                active=True,
                created_at=preference.updated_at,
            )
        )
    return events


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


def _preference_to_dict(preference: LongTermPreference) -> dict[str, object]:
    payload = asdict(preference)
    payload["created_at"] = preference.created_at.isoformat()
    payload["updated_at"] = preference.updated_at.isoformat()
    payload["last_used_at"] = (
        preference.last_used_at.isoformat() if preference.last_used_at else None
    )
    return payload


def _normalize_kind(kind: str) -> str:
    if kind not in {"prefer", "avoid"}:
        raise ValueError("preference kind must be prefer or avoid")
    return kind


def _scope_for_events(events: list[FeedbackEvent]) -> str:
    scopes = {_scope_for_target_type(event.target_type) for event in events}
    return scopes.pop() if len(scopes) == 1 else "general"


def _scope_for_target_type(target_type: str) -> str:
    if target_type == "topic":
        return "topic"
    if target_type == "script_section":
        return "script"
    if target_type == "published_episode":
        return "publish"
    return "general"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
