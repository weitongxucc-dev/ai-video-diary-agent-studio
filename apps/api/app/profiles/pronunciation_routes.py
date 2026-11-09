"""Pronunciation entry and version HTTP; original profile records retain policy."""
from fastapi import APIRouter, HTTPException, Request
from app.profiles.contracts import PronunciationEntry
from app.schemas import PronunciationEntriesResponse, PronunciationEntryResponse, PronunciationEntryUpsertRequest


def _pronunciation_entry_response(entry: PronunciationEntry) -> PronunciationEntryResponse:
    return PronunciationEntryResponse.model_validate(entry, from_attributes=True)


def build_pronunciation_router(*, records_for, workspace_for, required_text_for,
                               write_error_for) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/media/pronunciation-entries",
        response_model=PronunciationEntriesResponse,
    )
    def list_media_pronunciation_entries(request: Request) -> PronunciationEntriesResponse:
        entries = records_for().profile_records.list_pronunciation_entries(workspace_for(request))
        return PronunciationEntriesResponse(
            items=[_pronunciation_entry_response(item) for item in entries],
            total_count=len(entries),
        )


    @router.post(
        "/api/media/pronunciation-entries",
        response_model=PronunciationEntryResponse,
    )
    def save_media_pronunciation_entry(
        request: Request,
        payload: PronunciationEntryUpsertRequest,
    ) -> PronunciationEntryResponse:
        workspace_id = workspace_for(request)
        try:
            scope_reference = (
                required_text_for(payload.scope_reference, "scope reference")
                if payload.scope_reference is not None
                else None
            )
            saved = records_for().profile_records.save_pronunciation_entry(
                PronunciationEntry(
                    id=required_text_for(payload.id, "pronunciation entry id"),
                    workspace_id=workspace_id,
                    term=required_text_for(payload.term, "term"),
                    language=payload.language,
                    target_pronunciation=required_text_for(
                        payload.target_pronunciation, "target pronunciation"
                    ),
                    scope=payload.scope,
                    scope_reference=scope_reference,
                    version=required_text_for(payload.version, "pronunciation version"),
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="person profile not found") from exc
        except ValueError as exc:
            raise write_error_for(exc) from exc
        return _pronunciation_entry_response(saved)


    @router.get(
        "/api/media/pronunciation-entries/{entry_id}",
        response_model=PronunciationEntryResponse,
    )
    def get_media_pronunciation_entry(
        entry_id: str,
        request: Request,
        version: str | None = None,
    ) -> PronunciationEntryResponse:
        try:
            entry = records_for().profile_records.get_pronunciation_entry(
                workspace_for(request), entry_id, version
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="pronunciation entry not found") from exc
        return _pronunciation_entry_response(entry)


    @router.get(
        "/api/media/pronunciation-entries/{entry_id}/versions",
        response_model=PronunciationEntriesResponse,
    )
    def list_media_pronunciation_entry_versions(
        entry_id: str,
        request: Request,
    ) -> PronunciationEntriesResponse:
        workspace_id = workspace_for(request)
        try:
            records_for().profile_records.get_pronunciation_entry(workspace_id, entry_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="pronunciation entry not found") from exc
        items = records_for().profile_records.list_pronunciation_entry_versions(workspace_id, entry_id)
        return PronunciationEntriesResponse(
            items=[_pronunciation_entry_response(item) for item in items],
            total_count=len(items),
        )


    return router
