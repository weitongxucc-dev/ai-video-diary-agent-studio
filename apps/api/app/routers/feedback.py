from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from fastapi import APIRouter

from app.routers.context import AppContext
from app.schemas import FeedbackEventResponse, FeedbackSummaryResponse


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

    return router
