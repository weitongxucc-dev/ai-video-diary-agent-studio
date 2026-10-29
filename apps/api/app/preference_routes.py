"""Feedback-derived preference views and explicit signal toggles."""
from collections import Counter

from fastapi import APIRouter, Request
from app.preference_memory import PreferenceState, build_preference_memory
from app.repository import load_preference_state_payload, save_preference_state_payload
from app.schemas import (
    FeedbackSummaryResponse, FeedbackEventResponse, PreferenceItemResponse,
    PreferencesResponse, PreferenceToggleRequest, PreferenceCardResponse,
    PreferencesCardsResponse, PreferenceEvidenceItemResponse, PreferenceEvidenceResponse,
)


def build_preference_router(*, episodes_for, workspace_for, route_names) -> APIRouter:
    router = APIRouter()

    @router.get("/api/feedback/summary", response_model=FeedbackSummaryResponse)
    def get_feedback_summary(request: Request) -> FeedbackSummaryResponse:
        ws = workspace_for(request)
        all_event_pairs = [
            (episode.id, event)
            for episode in episodes_for().all(workspace_id=ws)
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
    def get_preferences(request: Request) -> PreferencesResponse:
        ws = workspace_for(request)
        events = [
            event
            for episode in episodes_for().all(workspace_id=ws)
            for event in episode.feedback_events
            if event.reason and event.active
        ]
        pref_state = PreferenceState.from_payload(
            load_preference_state_payload(workspace_id=ws)
        )
        memory = build_preference_memory(events, limit=20, preference_state=pref_state)
        preferences = [
            PreferenceItemResponse(
                label=label,
                signal=signal.signal,
                evidence_count=signal.evidence_count,
                confidence=signal.confidence,
                enabled=signal.enabled,
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
        )


    @router.patch(
        "/api/preferences/disable",
        response_model=PreferencesResponse,
    )
    def disable_preference(
        request: Request,
        body: PreferenceToggleRequest,
    ) -> PreferencesResponse:
        ws = workspace_for(request)
        pref_state = PreferenceState.from_payload(
            load_preference_state_payload(workspace_id=ws)
        )
        pref_state.disable(body.signal)
        save_preference_state_payload(pref_state.to_payload(), workspace_id=ws)
        return _rebuilt_preferences_response(pref_state, workspace_id=ws)


    @router.patch(
        "/api/preferences/restore",
        response_model=PreferencesResponse,
    )
    def restore_preference(
        request: Request,
        body: PreferenceToggleRequest,
    ) -> PreferencesResponse:
        ws = workspace_for(request)
        pref_state = PreferenceState.from_payload(
            load_preference_state_payload(workspace_id=ws)
        )
        pref_state.restore(body.signal)
        save_preference_state_payload(pref_state.to_payload(), workspace_id=ws)
        return _rebuilt_preferences_response(pref_state, workspace_id=ws)


    def _rebuilt_preferences_response(
        pref_state: PreferenceState,
        workspace_id: str,
    ) -> PreferencesResponse:
        events = [
            event
            for episode in episodes_for().all(workspace_id=workspace_id)
            for event in episode.feedback_events
            if event.reason and event.active
        ]
        memory = build_preference_memory(events, limit=20, preference_state=pref_state)
        preferences = [
            PreferenceItemResponse(
                label=label,
                signal=signal.signal,
                evidence_count=signal.evidence_count,
                confidence=signal.confidence,
                enabled=signal.enabled,
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
        )


    @router.get("/api/preferences/cards", response_model=PreferencesCardsResponse)
    def get_preferences_cards() -> PreferencesCardsResponse:
        events = [
            event
            for episode in episodes_for().all()
            for event in episode.feedback_events
            if event.reason and event.active
        ]
        pref_state = PreferenceState.from_payload(load_preference_state_payload())
        memory = build_preference_memory(events, limit=20, preference_state=pref_state)
        all_cards = [
            PreferenceCardResponse(
                id=f"{label}::{signal.signal}",
                label=label,
                signal=signal.signal,
                category=label,
                evidence_count=signal.evidence_count,
                confidence=signal.confidence,
                enabled=signal.enabled,
                latest_evidence_at=None,
            )
            for label, signals in (
                ("偏好", memory.prefer),
                ("避开", memory.avoid),
            )
            for signal in signals
        ]
        cards = all_cards[:20]
        return PreferencesCardsResponse(
            total_count=len(cards),
            enabled_count=sum(1 for c in cards if c.enabled),
            disabled_count=sum(1 for c in cards if not c.enabled),
            cards=cards,
        )


    @router.get(
        "/api/preferences/cards/{card_id}/evidence",
        response_model=PreferenceEvidenceResponse,
    )
    def get_preference_card_evidence(card_id: str) -> PreferenceEvidenceResponse:
        """Return evidence items for a preference signal."""
        episodes = episodes_for().all()
        items = [
            PreferenceEvidenceItemResponse(
                feedback_event_id=ev.id,
                episode_id=ep.id,
                episode_date=ep.episode_date,
                target_type=ev.target_type,
                target_id=ev.target_id,
                value=ev.value.value,
                reason=ev.reason or "",
                created_at=ev.created_at,
            )
            for ep in episodes[:50]
            for ev in ep.feedback_events
            if ev.active and ev.reason and card_id in ev.reason
        ][:10]
        return PreferenceEvidenceResponse(
            preference_id=card_id,
            total=len(items),
            items=items,
        )


    router.routes = [route for route in router.routes if route.name in route_names]
    return router
