"""Obsidian note browsing HTTP; file discovery, parsing and modification-time ordering retain their Vault owner."""
from fastapi import APIRouter
from app.schemas import KnowledgeNoteResponse

def build_knowledge_notes_router(*, vault_for, response_for):
    router = APIRouter()

    @router.get('/api/knowledge/notes', response_model=list[KnowledgeNoteResponse])
    def get_knowledge_notes(q: str | None=None, limit: int=40) -> list[KnowledgeNoteResponse]:
        notes = vault_for().recent_notes(limit=200)
        if q:
            query = q.lower()
            notes = [note for note in notes if query in note.title.lower() or query in note.ref.lower() or query in note.excerpt.lower()]
        return [response_for().model_validate(note, from_attributes=True) for note in notes[:max(1, min(limit, 100))]]
    return router
