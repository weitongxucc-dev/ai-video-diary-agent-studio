from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.models import Episode
from app.routers.context import AppContext
from app.schemas import RetakeReportResponse


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/episodes/{episode_id}/retake-report",
        response_model=RetakeReportResponse,
    )
    def get_episode_retake_report(episode_id: str) -> RetakeReportResponse:
        context = get_context()
        return _read_retake_report(context, _episode_or_404(context, episode_id))

    @router.get("/api/retake-reports/latest", response_model=RetakeReportResponse)
    def get_latest_retake_report() -> RetakeReportResponse:
        context = get_context()
        episodes_with_reports = sorted(
            (
                episode
                for episode in context.repo.all()
                if episode.video_dedup_report_path
            ),
            key=lambda episode: episode.updated_at,
            reverse=True,
        )
        for episode in episodes_with_reports:
            try:
                return _read_retake_report(context, episode)
            except HTTPException:
                continue
        raise HTTPException(status_code=404, detail="retake report not found")

    return router


def _episode_or_404(context: AppContext, episode_id: str) -> Episode:
    try:
        return context.repo.get(episode_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc


def _read_retake_report(
    context: AppContext,
    episode: Episode,
) -> RetakeReportResponse:
    if not episode.video_dedup_report_path:
        raise HTTPException(status_code=404, detail="retake report not found")
    try:
        report_root = Path(context.settings.video_output_path).expanduser().resolve()
        report_path = Path(episode.video_dedup_report_path).expanduser().resolve()
        if not report_path.is_relative_to(report_root) or not report_path.is_file():
            raise HTTPException(status_code=404, detail="retake report not found")
        return RetakeReportResponse.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
    except HTTPException:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="retake report not found") from exc
