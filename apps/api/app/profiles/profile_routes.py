"""Versioned person/voice profile HTTP and fixed default selection."""
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from app.profiles.contracts import PersonProfile, ProfileReference, VoiceProfile as VersionedVoiceProfile
from app.schemas import validate_logical_media_reference
from app.schemas import (PersonProfilesResponse, PersonProfileResponse, PersonProfileUpsertRequest,
                         VersionedVoiceProfileResponse, VersionedVoiceProfilesResponse,
                         VersionedVoiceProfileUpsertRequest, FixedProfileReference)


def build_profile_router(*, records_for, workspace_for, reader_for, voice_response_for,
                         person_response_for, read_facts_for, required_text_for,
                         optional_texts_for, authorization_for, write_error_for,
                         route_names) -> APIRouter:
    router = APIRouter()

    def _readable_profiles(request: Request, items):
        user_id, diagnostic = reader_for(request, workspace_for(request))
        return [item for item in items if records_for().profile_readable(item, user_id, diagnostic)]



    @router.get("/api/media/default-voice", response_model=VersionedVoiceProfileResponse | None)
    def get_default_media_voice(request: Request):
        voice = records_for().get_default_versioned_voice(workspace_for(request))
        user, diagnostic = reader_for(request, workspace_for(request))
        return voice_response_for(voice, request) if voice and records_for().profile_readable(voice, user, diagnostic) else None



    @router.put("/api/media/default-voice", response_model=VersionedVoiceProfileResponse)
    def set_default_media_voice(request: Request, payload: FixedProfileReference):
        selected = payload
        ws = workspace_for(request)
        user, diagnostic = reader_for(request, ws)
        try:
            voice = records_for().set_default_versioned_voice(ws, selected.profile_id, selected.version,
                viewer_user_id=user, local_diagnostic=diagnostic)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return voice_response_for(voice, request)



    @router.get("/api/media/{profile_kind}/{profile_id}/reference")
    def stream_profile_reference(profile_kind: Literal["person-profiles", "voice-profiles"], profile_id: str, request: Request, version: str):
        ws = workspace_for(request)
        try:
            profile = (records_for().get_person_profile(ws, profile_id, version) if profile_kind == "person-profiles"
                       else records_for().get_versioned_voice_profile(ws, profile_id, version))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="profile not found") from exc
        path, reason, _, _ = read_facts_for(profile, request)
        if path is None:
            raise HTTPException(status_code=409, detail=reason)
        return FileResponse(path)



    @router.get("/api/media/person-profiles", response_model=PersonProfilesResponse)
    def list_media_person_profiles(request: Request) -> PersonProfilesResponse:
        people = _readable_profiles(request, records_for().list_person_profiles(workspace_for(request)))
        return PersonProfilesResponse(
            items=[person_response_for(item, request) for item in people],
            total_count=len(people),
        )



    def _profile_save_use_state(workspace_id, payload, *, person):
        if "use_state" in payload.model_fields_set:
            return payload.use_state
        try:
            previous = (records_for().get_person_profile(workspace_id, payload.id) if person
                        else records_for().get_versioned_voice_profile(workspace_id, payload.id))
        except KeyError:
            return payload.use_state
        return previous.use_state



    @router.post("/api/media/person-profiles", response_model=PersonProfileResponse)
    def save_media_person_profile(
        request: Request,
        payload: PersonProfileUpsertRequest,
    ) -> PersonProfileResponse:
        workspace_id = workspace_for(request)
        try:
            saved = records_for().save_person_profile(
                PersonProfile(
                    id=required_text_for(payload.id, "person id"),
                    workspace_id=workspace_id,
                    display_name=required_text_for(payload.display_name, "display name"),
                    version=payload.version or f"v-{uuid4().hex}",
                    avatar_reference=validate_logical_media_reference(
                        payload.avatar_reference,
                        field="avatar reference",
                        allowed_schemes={"asset", "workspace"},
                    ),
                    background_capabilities=optional_texts_for(
                        payload.background_capabilities, "background capabilities"
                    ),
                    shot_capabilities=optional_texts_for(
                        payload.shot_capabilities, "shot capabilities"
                    ),
                    authorization=authorization_for(workspace_id, payload.authorization) if payload.authorization else None,
                    reference=ProfileReference(**payload.reference.model_dump()) if payload.reference else ProfileReference(),
                    use_state=_profile_save_use_state(workspace_id, payload, person=True),
                ), verified_owner_user_id=reader_for(request, workspace_id)[0],
                local_diagnostic=reader_for(request, workspace_id)[1],
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise write_error_for(exc) from exc
        return person_response_for(saved, request)



    @router.get(
        "/api/media/person-profiles/{person_id}",
        response_model=PersonProfileResponse,
    )
    def get_media_person_profile(
        person_id: str,
        request: Request,
        version: str | None = None,
    ) -> PersonProfileResponse:
        try:
            person = records_for().get_person_profile(
                workspace_for(request), person_id, version
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="person profile not found") from exc
        return person_response_for(person, request)



    @router.get(
        "/api/media/person-profiles/{person_id}/versions",
        response_model=PersonProfilesResponse,
    )
    def list_media_person_profile_versions(
        person_id: str,
        request: Request,
    ) -> PersonProfilesResponse:
        workspace_id = workspace_for(request)
        try:
            records_for().get_person_profile(workspace_id, person_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="person profile not found") from exc
        items = _readable_profiles(request, records_for().list_person_profile_versions(workspace_id, person_id))
        return PersonProfilesResponse(
            items=[person_response_for(item, request) for item in items],
            total_count=len(items),
        )



    @router.get("/api/media/voice-profiles", response_model=VersionedVoiceProfilesResponse)
    def list_media_voice_profiles(request: Request) -> VersionedVoiceProfilesResponse:
        voices = _readable_profiles(request, records_for().list_versioned_voice_profiles(workspace_for(request)))
        return VersionedVoiceProfilesResponse(
            items=[voice_response_for(item, request) for item in voices],
            total_count=len(voices),
        )



    @router.post("/api/media/voice-profiles", response_model=VersionedVoiceProfileResponse)
    def save_media_voice_profile(
        request: Request,
        payload: VersionedVoiceProfileUpsertRequest,
    ) -> VersionedVoiceProfileResponse:
        workspace_id = workspace_for(request)
        person_pair = (payload.person_profile_id, payload.person_version)
        if not {"person_profile_id", "person_version"} & payload.model_fields_set:
            try:
                previous = records_for().get_versioned_voice_profile(workspace_id, payload.id)
                person_pair = (previous.person_profile_id, previous.person_version)
            except KeyError:
                pass  # A new independent voice has no historical person restriction.
        try:
            saved = records_for().save_versioned_voice_profile(
                VersionedVoiceProfile(
                    id=required_text_for(payload.id, "voice profile id"),
                    workspace_id=workspace_id,
                    person_profile_id=person_pair[0],
                    person_version=person_pair[1],
                    version=payload.version or f"v-{uuid4().hex}",
                    language=payload.language,
                    reference_audio_path=validate_logical_media_reference(
                        payload.reference_audio_path,
                        field="reference audio path",
                        allowed_schemes={"asset", "workspace"},
                    ),
                    reference_text=payload.reference_text,
                    display_name=payload.display_name,
                    model_id=payload.model_id,
                    authorization=authorization_for(workspace_id, payload.authorization) if payload.authorization else None,
                    reference=ProfileReference(**payload.reference.model_dump()) if payload.reference else ProfileReference(),
                    use_state=_profile_save_use_state(workspace_id, payload, person=False),
                ), verified_owner_user_id=reader_for(request, workspace_id)[0],
                local_diagnostic=reader_for(request, workspace_id)[1],
            )
        except (KeyError, ValueError, PermissionError) as exc:
            if isinstance(exc, PermissionError):
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            if isinstance(exc, KeyError):
                raise HTTPException(status_code=404, detail="person profile not found") from exc
            raise write_error_for(exc) from exc
        return voice_response_for(saved, request)



    @router.get(
        "/api/media/voice-profiles/{voice_profile_id}",
        response_model=VersionedVoiceProfileResponse,
    )
    def get_media_voice_profile(
        voice_profile_id: str,
        request: Request,
        version: str | None = None,
    ) -> VersionedVoiceProfileResponse:
        try:
            voice = records_for().get_versioned_voice_profile(
                workspace_for(request), voice_profile_id, version
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="voice profile not found") from exc
        return voice_response_for(voice, request)



    @router.get(
        "/api/media/voice-profiles/{voice_profile_id}/versions",
        response_model=VersionedVoiceProfilesResponse,
    )
    def list_media_voice_profile_versions(
        voice_profile_id: str,
        request: Request,
    ) -> VersionedVoiceProfilesResponse:
        workspace_id = workspace_for(request)
        try:
            records_for().get_versioned_voice_profile(workspace_id, voice_profile_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="voice profile not found") from exc
        items = _readable_profiles(request, records_for().list_versioned_voice_profile_versions(
            workspace_id, voice_profile_id
        ))
        return VersionedVoiceProfilesResponse(
            items=[voice_response_for(item, request) for item in items],
            total_count=len(items),
        )



    router.routes = [route for route in router.routes if route.name in route_names]
    return router
