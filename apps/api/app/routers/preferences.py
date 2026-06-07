from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from app.preference_memory import (
    build_preference_candidates,
    build_preference_memory,
    long_term_preference_events,
)
from app.routers.context import AppContext
from app.schemas import (
    FeedbackEventResponse,
    FeedbackSummaryResponse,
    PreferenceConfirmRequest,
    PreferenceItemResponse,
    PreferencesResponse,
)


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/feedback/summary", response_model=FeedbackSummaryResponse)
    def get_feedback_summary() -> FeedbackSummaryResponse:
        context = get_context()
        all_event_pairs = [
            (episode.id, event)
            for episode in context.repo.all()
            for event in episode.feedback_events
        ]
        all_event_pairs = sorted(
            all_event_pairs,
            key=lambda item: item[1].created_at,
            reverse=True,
        )
        active_events = [event for _, event in all_event_pairs if event.active]
        counts = Counter(event.value for event in active_events)
        return FeedbackSummaryResponse(
            total_count=len(active_events),
            up_count=counts.get("up", 0),
            down_count=counts.get("down", 0),
            recent_events=[
                FeedbackEventResponse.model_validate(
                    {**event.__dict__, "episode_id": episode_id}
                )
                for episode_id, event in all_event_pairs[:20]
            ],
        )

    @router.get("/api/preferences", response_model=PreferencesResponse)
    def get_preferences() -> PreferencesResponse:
        return _preferences_response(get_context())

    @router.post("/api/preferences/confirm", response_model=PreferencesResponse)
    def confirm_preference(request: PreferenceConfirmRequest) -> PreferencesResponse:
        context = get_context()
        try:
            context.preference_repo.confirm(
                kind=request.kind,
                content=request.content,
                source_event_ids=request.source_event_ids,
                confidence=request.confidence,
                scope=request.scope,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _preferences_response(get_context())

    @router.post("/api/preferences/{preference_id}/disable", response_model=PreferencesResponse)
    def disable_preference(preference_id: str) -> PreferencesResponse:
        context = get_context()
        try:
            context.preference_repo.disable(preference_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="preference not found") from exc
        return _preferences_response(get_context())

    @router.post("/api/preferences/{preference_id}/restore", response_model=PreferencesResponse)
    def restore_preference(preference_id: str) -> PreferencesResponse:
        context = get_context()
        try:
            context.preference_repo.restore(preference_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="preference not found") from exc
        return _preferences_response(get_context())

    @router.post("/api/preferences/{preference_id}/delete", response_model=PreferencesResponse)
    def delete_preference(preference_id: str) -> PreferencesResponse:
        context = get_context()
        try:
            context.preference_repo.delete(preference_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="preference not found") from exc
        return _preferences_response(get_context())

    return router


def _preferences_response(context: AppContext) -> PreferencesResponse:
    events = [
        event
        for episode in context.repo.all()
        for event in episode.feedback_events
        if event.reason and event.active
    ]
    long_term_preferences = context.preference_repo.all()
    active_memory_events = long_term_preference_events(context.preference_repo.active())
    memory = build_preference_memory([*events, *active_memory_events], limit=20)
    confirmed_keys = {
        (preference.kind, preference.content)
        for preference in long_term_preferences
        if not preference.deleted
    }
    candidates = build_preference_candidates(events, limit=20)
    preferences = [
        PreferenceItemResponse(
            label=label,
            signal=signal.signal,
            evidence_count=signal.evidence_count,
            confidence=signal.confidence,
        )
        for label, signals in (
            ("偏好", memory.prefer),
            ("避开", memory.avoid),
        )
        for signal in signals
    ]
    return PreferencesResponse(
        total_feedback_count=len(events),
        preferences=preferences[:20],
        candidates=[
            {
                **candidate.__dict__,
                "confirmed": (candidate.kind, candidate.content) in confirmed_keys,
            }
            for candidate in candidates
        ],
        long_term_preferences=[
            _long_term_preference_response(preference)
            for preference in long_term_preferences
        ],
    )


def _long_term_preference_response(preference) -> dict[str, object]:
    return {
        "id": preference.id,
        "kind": preference.kind,
        "label": "偏好" if preference.kind == "prefer" else "避开",
        "content": preference.content,
        "source_event_ids": preference.source_event_ids,
        "confidence": preference.confidence,
        "scope": preference.scope,
        "enabled": preference.enabled,
        "deleted": preference.deleted,
        "created_at": preference.created_at,
        "updated_at": preference.updated_at,
        "last_used_at": preference.last_used_at,
    }
