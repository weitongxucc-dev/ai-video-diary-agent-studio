"""HTTP entry points for editing an unadopted timeline candidate."""
from contextlib import contextmanager
from dataclasses import asdict
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import FileResponse, JSONResponse

from app.creation.clip_preview import CandidateClipPreviewCommand, CandidateClipPreviewTarget, ClipPreviewUnavailable
from app.creation.editing.timeline_candidates import TimelineCandidateUseCase
from app.montage import creation_task_montage_plan_from_payload


class _VersionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    expected_timeline_version_id: str = Field(min_length=1, max_length=120)
    expected_timeline_sequence: int = Field(ge=1)


class CandidateForkRequest(_VersionBinding):
    candidate_id: UUID


class CandidateCropRequest(_VersionBinding):
    operation_type: Literal["ChangeCrop"]
    request_id: UUID
    clip_id: str = Field(min_length=1, max_length=120)
    fit_mode: Literal["fill", "fit"]
    crop_x: float = Field(ge=0, le=1)
    crop_y: float = Field(ge=0, le=1)


class CandidatePreviewRequest(_VersionBinding):
    request_id: UUID
    clip_id: str = Field(min_length=1, max_length=120)


def _read_model(candidate, version):
    plan = creation_task_montage_plan_from_payload(version.snapshot["montage"])
    return {
        **asdict(candidate),
        "version": {
            "id": version.id,
            "parent_version_id": version.parent_version_id,
            "sequence": version.sequence,
            "clips": [
                {"id": clip.id, "fit_mode": clip.fit_mode, "crop_x": clip.crop_x, "crop_y": clip.crop_y}
                for clip in plan.clips
            ],
        },
    }


@contextmanager
def _candidate_errors():
    try:
        yield
    except KeyError as exc:
        raise HTTPException(404, "剪辑候选或其时间线版本不存在") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ClipPreviewUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def build_task_timeline_candidate_router(*, repository_for, access_for, preview_for,
                                         actor_for, media_for, runtime_for):
    router = APIRouter(prefix="/api/creation-tasks/{task_id}/timeline/candidates")

    @router.get("")
    def list_timeline_candidates(task_id: str, request: Request, limit: int = Query(20, ge=1, le=50),
                                 after_id: UUID | None = None):
        task, member_id = access_for()(task_id, request)
        with _candidate_errors():
            candidates = TimelineCandidateUseCase(repository_for()).list(
                task, member_id, limit=limit + 1, after_id=str(after_id) if after_id is not None else "")
            return {"items": [asdict(candidate) for candidate in candidates[:limit]],
                    "next_after_id": candidates[limit - 1].id if len(candidates) > limit else None}

    @router.get("/{candidate_id}")
    def read_timeline_candidate(task_id: str, candidate_id: UUID, request: Request):
        task, member_id = access_for()(task_id, request)
        with _candidate_errors():
            return _read_model(*TimelineCandidateUseCase(repository_for()).read(task, member_id, str(candidate_id)))

    @router.post("")
    def fork_timeline_candidate(task_id: str, request: Request, payload: CandidateForkRequest):
        task, member_id = access_for()(task_id, request, write=True)
        with _candidate_errors():
            return _read_model(*TimelineCandidateUseCase(repository_for()).fork(
                task, member_id, str(payload.candidate_id), expected_version_id=payload.expected_timeline_version_id,
                expected_sequence=payload.expected_timeline_sequence))

    @router.post("/{candidate_id}/edits/exact")
    def change_timeline_candidate_crop(task_id: str, candidate_id: UUID, request: Request, payload: CandidateCropRequest):
        task, member_id = access_for()(task_id, request, write=True)
        with _candidate_errors():
            return _read_model(*TimelineCandidateUseCase(repository_for()).change_crop(
                task, member_id, str(candidate_id), expected_version_id=payload.expected_timeline_version_id,
                expected_sequence=payload.expected_timeline_sequence, request_id=str(payload.request_id),
                clip_id=payload.clip_id, fit_mode=payload.fit_mode, crop_x=payload.crop_x, crop_y=payload.crop_y))

    @router.post("/{candidate_id}/previews")
    def submit_timeline_candidate_preview(task_id: str, candidate_id: UUID, request: Request,
                                          payload: CandidatePreviewRequest):
        task, _member_id = access_for()(task_id, request, write=True)
        actor = actor_for()(request, task.workspace_id)
        command = CandidateClipPreviewCommand(str(candidate_id), payload.expected_timeline_version_id,
            payload.expected_timeline_sequence, payload.clip_id, str(payload.request_id))
        with _candidate_errors():
            result = preview_for().submit(actor, task_id, command, runtime_for())
        return JSONResponse(status_code=202, content=result)

    @router.get("/{candidate_id}/versions/{version_id}/previews")
    def latest_timeline_candidate_preview(task_id: str, candidate_id: UUID, version_id: str, request: Request,
                                          sequence: int = Query(ge=1), clip_id: str = Query(min_length=1, max_length=120)):
        task, _member_id = access_for()(task_id, request)
        actor = actor_for()(request, task.workspace_id)
        target = CandidateClipPreviewTarget(str(candidate_id), version_id, sequence, clip_id)
        with _candidate_errors():
            return {"preview": preview_for().latest(media_for(), actor, task_id, target)}

    @router.get("/{candidate_id}/versions/{version_id}/previews/{job_id}")
    def read_timeline_candidate_preview(task_id: str, candidate_id: UUID, version_id: str, job_id: str,
                                        request: Request):
        task, _member_id = access_for()(task_id, request)
        actor = actor_for()(request, task.workspace_id)
        with _candidate_errors():
            return preview_for().read(media_for(), actor, task_id, str(candidate_id), version_id, job_id)

    @router.get("/{candidate_id}/versions/{version_id}/previews/{job_id}/video")
    def stream_timeline_candidate_preview(task_id: str, candidate_id: UUID, version_id: str, job_id: str,
                                          request: Request):
        task, _member_id = access_for()(task_id, request)
        actor = actor_for()(request, task.workspace_id)
        with _candidate_errors():
            path = preview_for().video(media_for(), actor, task_id, str(candidate_id), version_id, job_id)
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})

    @router.post("/{candidate_id}/versions/{version_id}/previews/{job_id}/cancel")
    def cancel_timeline_candidate_preview(task_id: str, candidate_id: UUID, version_id: str, job_id: str,
                                          request: Request):
        task, _member_id = access_for()(task_id, request, write=True)
        actor = actor_for()(request, task.workspace_id)
        with _candidate_errors():
            return preview_for().cancel(media_for(), actor, task_id, str(candidate_id), version_id,
                job_id, runtime_for())

    return router
