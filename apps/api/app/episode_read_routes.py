"""Historical Episode read views; repository scope and legacy write retirement stay unchanged."""
from fastapi import APIRouter
from pathlib import Path
from fastapi import HTTPException, Request
from app.models import Episode, EpisodeIntakeMode, EpisodeStatus
from app.schemas import EditPlanResponse, EpisodeHistoryItemResponse, EpisodeHistoryResponse, EpisodeListResponse, EpisodeResponse, EpisodeSummaryResponse, ProductionChainResponse, RetakeReportResponse, SourceFileStatesResponse, TopicEvidenceItemResponse, TopicEvidenceResponse


def build_episode_read_router(*, episodes_for, output_root_for, edit_plan_for,
                              route_names) -> APIRouter:
    router = APIRouter()

    def get_episode_or_404(episode_id: str) -> Episode:
        try:
            return episodes_for().get(episode_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="episode not found") from exc



    def episode_response(episode: Episode) -> EpisodeResponse:
        return EpisodeResponse.model_validate(
            episode,
            from_attributes=True,
        )



    @router.get("/api/episodes/{episode_id}", response_model=EpisodeResponse)
    def get_episode(episode_id: str, request: Request) -> EpisodeResponse:
        return episode_response(get_episode_or_404(episode_id))



    @router.get("/api/episodes/{episode_id}/retake-report", response_model=RetakeReportResponse)
    def get_episode_retake_report(episode_id: str) -> RetakeReportResponse:
        return _read_retake_report(get_episode_or_404(episode_id))



    @router.get("/api/episodes/{episode_id}/edit-plan", response_model=EditPlanResponse)
    def get_episode_edit_plan(episode_id: str) -> EditPlanResponse:
        episode = get_episode_or_404(episode_id)
        return EditPlanResponse.model_validate(
            edit_plan_for()(episode, output_root_for())
        )



    @router.get("/api/retake-reports/latest", response_model=RetakeReportResponse)
    def get_latest_retake_report() -> RetakeReportResponse:
        episodes_with_reports = sorted(
            (episode for episode in episodes_for().all() if episode.video_dedup_report_path),
            key=lambda episode: episode.updated_at,
            reverse=True,
        )
        for episode in episodes_with_reports:
            try:
                return _read_retake_report(episode)
            except HTTPException:
                continue
        raise HTTPException(status_code=404, detail="retake report not found")



    def _read_retake_report(episode: Episode) -> RetakeReportResponse:
        if not episode.video_dedup_report_path:
            raise HTTPException(status_code=404, detail="retake report not found")
        try:
            report_root = Path(output_root_for()).expanduser().resolve()
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



    @router.get(
        "/api/episodes/{episode_id}/production-chain",
        response_model=ProductionChainResponse,
    )
    def get_episode_production_chain(episode_id: str) -> ProductionChainResponse:
        episode = get_episode_or_404(episode_id)
        return ProductionChainResponse(
            episode_id=episode.id,
            intake_mode=episode.intake_mode,
            chain=[
                {
                    "stage": step.stage,
                    "status": step.status,
                    "started_at": step.started_at,
                    "finished_at": step.finished_at,
                    "detail": step.detail,
                }
                for step in episode.video_production_chain
            ],
        )



    @router.get(
        "/api/episodes/{episode_id}/source-file-states",
        response_model=SourceFileStatesResponse,
    )
    def get_episode_source_file_states(episode_id: str) -> SourceFileStatesResponse:
        episode = get_episode_or_404(episode_id)
        return SourceFileStatesResponse(
            episode_id=episode.id,
            files=[
                {"file_name": name, "state": state}
                for name, state in episode.source_file_states.items()
            ],
        )



    @router.get("/api/episodes", response_model=EpisodeListResponse)
    def list_episodes(
        status: EpisodeStatus | None = None,
        intake_mode: EpisodeIntakeMode | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EpisodeListResponse:
        episodes = episodes_for().all()
        filtered = [
            ep for ep in episodes
            if (status is None or ep.status == status)
            and (intake_mode is None or ep.intake_mode == intake_mode)
        ]
        page = filtered[offset : offset + limit]
        return EpisodeListResponse(
            episodes=[
                EpisodeSummaryResponse(
                    id=ep.id,
                    day_number=ep.day_number,
                    episode_date=ep.episode_date,
                    status=ep.status,
                    intake_mode=ep.intake_mode,
                    content_template_id=ep.content_template_id,
                    selected_topic_id=ep.selected_topic_id,
                    video_source_file_name=ep.video_source_file_name,
                    video_job_status=ep.video_job_status,
                    video_job_message=ep.video_job_message,
                    draft_package_name=ep.draft_package_name,
                    publish_url=ep.publish_url,
                    platform=ep.platform,
                    published_at=ep.published_at,
                    created_at=ep.created_at,
                    updated_at=ep.updated_at,
                )
                for ep in page
            ],
            total=len(filtered),
        )



    @router.get("/api/episodes/history", response_model=EpisodeHistoryResponse)
    def list_episode_history(
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> EpisodeHistoryResponse:
        episodes = episodes_for().all()
        sorted_episodes = sorted(episodes, key=lambda ep: ep.created_at, reverse=True)

        # Apply status filter if provided
        if status:
            try:
                status_enum = EpisodeStatus(status)
                filtered = [ep for ep in sorted_episodes if ep.status == status_enum]
            except ValueError:
                filtered = sorted_episodes
        else:
            filtered = sorted_episodes

        total_all = len(sorted_episodes)
        page = filtered[offset : offset + limit]

        def _make_title(ep: Episode) -> str:
            selected = next((t for t in ep.topics if t.id == ep.selected_topic_id), None)
            if selected:
                return selected.title
            return f"第{ep.day_number}期 {ep.episode_date}"

        def _topic_title(ep: Episode) -> str | None:
            selected = next((t for t in ep.topics if t.id == ep.selected_topic_id), None)
            return selected.title if selected else None

        def _script_title(ep: Episode) -> str | None:
            if ep.script:
                return ep.script.hook or ep.script.title_options[0] if ep.script.title_options else None
            return None

        items = [
            EpisodeHistoryItemResponse(
                id=ep.id,
                day_number=ep.day_number,
                episode_date=ep.episode_date,
                title=_make_title(ep),
                status=ep.status,
                created_at=ep.created_at,
                updated_at=ep.updated_at,
                platform=ep.platform,
                notes=ep.notes,
                topic_title=_topic_title(ep),
                script_title=_script_title(ep),
                intake_mode=ep.intake_mode,
                publish_url=ep.publish_url,
                play_count=ep.play_count,
                like_count=ep.like_count,
                comment_count=ep.comment_count,
                draft_package_path=ep.draft_package_name,
                video_dedup_report_path=ep.video_dedup_report_path,
                published_at=ep.published_at,
            )
            for ep in page
        ]
        return EpisodeHistoryResponse(
            items=items,
            total_count=total_all,
            filtered_count=len(filtered),
        )



    @router.get(
        "/api/episodes/{episode_id}/topic-evidence",
        response_model=TopicEvidenceResponse,
    )
    def get_episode_topic_evidence(episode_id: str) -> TopicEvidenceResponse:
        """Return topic evidence (source refs) from the selected topic."""
        episode = get_episode_or_404(episode_id)
        selected = next((t for t in episode.topics if t.id == episode.selected_topic_id), None)
        items = [
            TopicEvidenceItemResponse(
                source_key=sf.ref or sf.title,
                source_type=sf.source_type,
                title=sf.title,
                ref=sf.ref,
            )
            for sf in selected.source_refs
        ] if selected else []
        return TopicEvidenceResponse(
            episode_id=episode_id,
            total=len(items),
            items=items,
        )



    router.routes = [route for route in router.routes if route.name in route_names]
    return router
