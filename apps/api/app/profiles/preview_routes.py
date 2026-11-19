"""Independent voice/avatar preview HTTP; existing services and runtime own execution."""
from dataclasses import replace
import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from app.creation_execution import CreationExecutionActor
from app.models import utc_now
from app.schemas import (MediaPreviewRunResponse, MediaPreviewRunsResponse,
                         VoicePreviewRequest, AvatarLipSyncPreviewRequest)


def build_preview_router(*, records_for, workspace_for, reader_for, actor_for,
                         runtime_for, voice_service_for, avatar_service_for,
                         output_root_for) -> APIRouter:
    router = APIRouter()

    def _preview_output_path(run) -> Path | None:
        if run.status != "succeeded" or not run.output_path:
            return None
        root = Path(output_root_for()).expanduser().resolve()
        path = Path(run.output_path).expanduser().resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None
        if not path.is_file() or path.stat().st_size == 0:
            return None
        return path


    def _media_preview_response(run) -> MediaPreviewRunResponse:
        output_url = (
            f"/api/media/previews/{run.preview_type}/{run.identity}/media"
            if _preview_output_path(run) is not None
            else None
        )
        return MediaPreviewRunResponse(
            id=run.id,
            workspace_id=run.workspace_id,
            preview_type=run.preview_type,
            preview_text=run.preview_input.get("preview_text") if isinstance(run.preview_input.get("preview_text"), str) else None,
            identity=run.identity,
            attempt=run.attempt,
            status=run.status,
            capability_id=run.capability_id,
            model_id=run.model_id,
            language=run.language,
            person_profile_id=run.person_profile_id,
            person_version=run.person_version,
            voice_profile_id=run.voice_profile_id,
            voice_version=run.voice_version,
            authorization_id=run.authorization_id,
            authorization_version=run.authorization_version,
            output_url=output_url,
            reason=run.reason,
            impact=run.impact,
            repair_action=run.repair_action,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )


    def _preview_precondition_error(
        *,
        error: str,
        detail: str,
        impact: str,
        repair_action: str,
        status_code: int = 409,
    ) -> HTTPException:
        return HTTPException(
            status_code=status_code,
            detail={
                "error": error,
                "detail": detail,
                "impact": impact,
                "repair_action": repair_action,
                "retryable": False,
            },
        )


    @router.post("/api/media/previews/voice", response_model=MediaPreviewRunResponse)
    def request_voice_preview(request: Request, payload: VoicePreviewRequest) -> MediaPreviewRunResponse:
        ws = workspace_for(request)
        user, diagnostic = reader_for(request, ws)
        actor = actor_for(request, ws)
        runtime = runtime_for()
        try:
            params = payload.model_dump(exclude={'saved_section'})
            if payload.saved_section is not None:
                from app.creation.saved_section_auditions import SavedSectionAuditions
                params = SavedSectionAuditions(records_for(), voice_service_for().preview_output_path).preview_params(
                    actor, payload.saved_section.model_dump(), params)
            run = voice_service_for().request(repository=records_for(), workspace_id=ws,
                viewer_user_id=user, local_diagnostic=diagnostic, prepare_only=True, member_identity=actor.identity_payload(), **params)
            if run.status == "queued" and not run.device_request_id:
                from app.profile_preview_execution import submit_profile_preview
                run = submit_profile_preview(runtime, run, actor, params)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="voice profile not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _media_preview_response(run)


    @router.post(
        "/api/media/previews/avatar-lipsync",
        response_model=MediaPreviewRunResponse,
    )
    def request_avatar_lipsync_preview(request: Request, payload: AvatarLipSyncPreviewRequest) -> MediaPreviewRunResponse:
        ws = workspace_for(request)
        actor = actor_for(request, ws)
        runtime = runtime_for()
        voice_run = records_for().preview_records.get_media_preview_run(ws, "voice", payload.voice_preview_identity)
        if voice_run is None or not _preview_readable(voice_run, request):
            raise HTTPException(status_code=404, detail="voice preview not found")
        audio_path = _preview_output_path(voice_run)
        if voice_run.status != "succeeded" or audio_path is None:
            raise _preview_precondition_error(error="voice_preview_not_succeeded", detail="需要当前成员已成功的声音预览。",
                impact="未开始人物预览。", repair_action="先完成同一固定声音版本的独立试听。")
        user, diagnostic = reader_for(request, ws)
        params = dict(person_id=payload.person_profile_id, person_version=payload.person_version,
            voice_profile_id=payload.voice_profile_id, voice_version=payload.voice_version,
            audio_path=str(audio_path), audio_cache_key=voice_run.identity, language=voice_run.language,
            motion=payload.motion, expression=payload.expression, authorization_id=payload.authorization_id,
            authorization_version=payload.authorization_version, model_id=payload.model_id, retry=payload.retry)
        try:
            run = avatar_service_for().request(repository=records_for(), workspace_id=ws,
                viewer_user_id=user, local_diagnostic=diagnostic, prepare_only=True, member_identity=actor.identity_payload(), **params)
            if run.status == "queued" and not run.device_request_id:
                from app.profile_preview_execution import submit_profile_preview
                run = submit_profile_preview(runtime, run, actor, params)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="media preview dependency not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _media_preview_response(run)


    def _preview_readable(run, request: Request):
        user, diagnostic = reader_for(request, run.workspace_id)
        if not run.member_identity:
            return diagnostic
        original = CreationExecutionActor.from_event(run.workspace_id, run.member_identity)
        return user == (original.user_id or original.member_id)

    def read_exact_voice_preview(identity: str, run_id: str, request: Request, sha256: str):
        ws = workspace_for(request)
        actor = actor_for(request, ws)
        repo = records_for()
        try:
            with repo._connect() as conn:
                run = repo.preview_records.read(ws, run_id, connection=conn)
        except KeyError as exc:
            raise HTTPException(404, 'voice preview not found') from exc
        if run.preview_type != 'voice' or run.identity != identity or run.member_identity != actor.identity_payload():
            raise HTTPException(404, 'voice preview not found')
        try:
            voice = repo.profile_inputs.resolve_narration_inputs(workspace_id=ws, episode_id=run.preview_input.get('episode_id', 'voice_preview'),
                voice_profile_id=run.voice_profile_id, voice_version=run.voice_version).voice
            from app.profiles.contracts import profile_use_failure
            if (profile_use_failure(voice, content_type='voice', usage_scope='narration') or not voice.authorization
                    or (run.authorization_id, run.authorization_version) != (voice.authorization.id, voice.authorization.version)):
                raise PermissionError
            repo.profile_reference_input(voice, viewer_user_id=actor.user_id or actor.member_id)
            path = voice_service_for().preview_output_path(ws, run.identity)
            if (run.status != 'succeeded' or run.output_path != str(path) or path.resolve() != path
                    or run.output_sha256 not in {None, sha256}):
                raise ValueError
            audio = path.read_bytes()
            if hashlib.sha256(audio).hexdigest() != sha256:
                raise ValueError
        except PermissionError as exc:
            raise HTTPException(403, '原试听声音授权已失效。') from exc
        except (KeyError, ValueError, OSError) as exc:
            raise HTTPException(409, '原试听 PCM 已变化，请重新读取候选。') from exc
        return Response(audio, media_type='audio/wav', headers={'Cache-Control': 'private, no-store'})


    @router.post("/api/media/previews/{preview_type}/{identity}/cancel", response_model=MediaPreviewRunResponse)
    def cancel_media_preview(preview_type: str, identity: str, request: Request):
        ws = workspace_for(request)
        actor = actor_for(request, ws)
        run = records_for().preview_records.get_media_preview_run(ws, preview_type, identity)
        if run is None or not _preview_readable(run, request):
            raise HTTPException(status_code=404, detail="media preview not found")
        if run.status not in {"queued", "running"} or not run.device_request_id:
            raise HTTPException(status_code=409, detail="预览不在可取消的设备请求状态")
        runtime = runtime_for()
        result = runtime.scheduler.cancel(run.device_request_id, actor)
        runtime.registrations["media_preview"].executor.request_cancel(run.device_request_id)
        if result["state"] == "cancelled":
            run = records_for().preview_records.save_media_preview_run(replace(run, status="cancelled", finished_at=utc_now(),
                output_path=None, reason="cancelled", impact="预览已取消，参考和输入保留。", repair_action="需要时显式重新预览。"))
        return _media_preview_response(run)


    @router.get("/api/media/previews", response_model=MediaPreviewRunsResponse)
    def list_media_previews(
        request: Request,
        preview_type: str | None = None,
    ) -> MediaPreviewRunsResponse:
        if preview_type not in {None, "voice", "avatar_lipsync"}:
            raise HTTPException(status_code=422, detail="invalid preview type")
        workspace_id = workspace_for(request)
        latest_by_identity = {}
        for run in records_for().preview_records.list_media_preview_runs(workspace_id, preview_type):
            if not _preview_readable(run, request):
                continue
            current = latest_by_identity.get(run.identity)
            if current is None or run.attempt > current.attempt:
                latest_by_identity[run.identity] = run
        runs = sorted(
            latest_by_identity.values(),
            key=lambda run: (run.finished_at or run.started_at, run.attempt),
            reverse=True,
        )
        return MediaPreviewRunsResponse(
            items=[_media_preview_response(run) for run in runs],
            total_count=len(runs),
        )


    @router.get(
        "/api/media/previews/{preview_type}/{identity}/attempts",
        response_model=MediaPreviewRunsResponse,
    )
    def list_media_preview_attempts(
        preview_type: str,
        identity: str,
        request: Request,
    ) -> MediaPreviewRunsResponse:
        if preview_type not in {"voice", "avatar_lipsync"}:
            raise HTTPException(status_code=422, detail="invalid preview type")
        workspace_id = workspace_for(request)
        if records_for().preview_records.get_media_preview_run(workspace_id, preview_type, identity) is None:
            raise HTTPException(status_code=404, detail="media preview not found")
        runs = [run for run in records_for().preview_records.list_media_preview_attempts(workspace_id, preview_type, identity) if _preview_readable(run, request)]
        return MediaPreviewRunsResponse(
            items=[_media_preview_response(run) for run in runs],
            total_count=len(runs),
        )


    @router.get("/api/media/previews/{preview_type}/{identity}/media")
    def stream_media_preview_output(
        preview_type: str,
        identity: str,
        request: Request,
        run_id: str | None = None,
        sha256: str | None = Query(default=None, pattern=r'^[0-9a-f]{64}$'),
    ) -> Response:
        if run_id is not None or sha256 is not None:
            if preview_type != 'voice' or not run_id or not sha256:
                raise HTTPException(422, '试听播放需要原尝试及 WAV 校验值。')
            return read_exact_voice_preview(identity, run_id, request, sha256)
        if preview_type not in {"voice", "avatar_lipsync"}:
            raise HTTPException(status_code=422, detail="invalid preview type")
        run = records_for().preview_records.get_media_preview_run(
            workspace_for(request), preview_type, identity
        )
        if run is None or not _preview_readable(run, request):
            raise HTTPException(status_code=404, detail="media preview not found")
        output_path = _preview_output_path(run)
        if output_path is None:
            raise HTTPException(status_code=404, detail="media preview output not found")
        media_type = "audio/wav" if output_path.suffix.lower() == ".wav" else "video/mp4"
        return FileResponse(output_path, media_type=media_type, filename=output_path.name)


    return router
