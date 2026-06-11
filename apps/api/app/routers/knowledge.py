from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter

from app.routers.context import AppContext
from app.schemas import KnowledgeNoteResponse


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/knowledge/notes", response_model=list[KnowledgeNoteResponse])
    def get_knowledge_notes(
        q: str | None = None,
        limit: int = 40,
    ) -> list[KnowledgeNoteResponse]:
        notes = get_context().vault.recent_notes(limit=200)
        if q:
            query = q.lower()
            notes = [
                note
                for note in notes
                if query in note.title.lower()
                or query in note.ref.lower()
                or query in note.excerpt.lower()
            ]
        return [
            KnowledgeNoteResponse.model_validate(note, from_attributes=True)
            for note in notes[: max(1, min(limit, 100))]
        ]

    return router
