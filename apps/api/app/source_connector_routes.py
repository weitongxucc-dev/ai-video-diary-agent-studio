"""Source connector and persisted sync-receipt HTTP adapters."""
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from app.source_types import SourceConnector
from app.schemas import (SourceConnectorsResponse, SourceConnectorResponse, SourceConnectorCreateRequest,
                         SourceRunEvidencesResponse, SourceRunEvidenceResponse, SourceSyncResponse,
                         SourceSyncStatusesResponse, SourceSyncStatusResponse)


def build_source_connector_router(*, records_for, workspace_for, connector_response_for,
                                  run_response_for, sync_for, route_names) -> APIRouter:
    router = APIRouter()

    @router.get("/api/source-connectors", response_model=SourceConnectorsResponse)
    def list_source_connectors(request: Request) -> SourceConnectorsResponse:
        connectors = records_for().list_connectors(workspace_for(request))
        return SourceConnectorsResponse(
            items=[connector_response_for(item) for item in connectors],
            total_count=len(connectors),
        )



    @router.post("/api/source-connectors", response_model=SourceConnectorResponse)
    def create_source_connector(
        request: Request,
        payload: SourceConnectorCreateRequest,
    ) -> SourceConnectorResponse:
        workspace_id = workspace_for(request)
        try:
            connector = records_for().upsert_connector(
                SourceConnector(
                    id=f"connector_{uuid4().hex}",
                    workspace_id=workspace_id,
                    platform=payload.platform,
                    protocol=payload.protocol,
                    account_or_rule=payload.account_or_rule,
                    frequency=payload.frequency,
                    permissions=tuple(item.strip() for item in payload.permissions if item.strip()),
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return connector_response_for(connector)



    @router.get("/api/source-connectors/{connector_id}", response_model=SourceConnectorResponse)
    def get_source_connector(
        connector_id: str,
        request: Request,
    ) -> SourceConnectorResponse:
        try:
            connector = records_for().get_connector(
                workspace_for(request), connector_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="source connector not found") from exc
        return connector_response_for(connector)



    @router.get(
        "/api/source-connectors/{connector_id}/runs",
        response_model=SourceRunEvidencesResponse,
    )
    def list_source_connector_runs(
        connector_id: str,
        request: Request,
    ) -> SourceRunEvidencesResponse:
        workspace_id = workspace_for(request)
        try:
            records_for().get_connector(workspace_id, connector_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="source connector not found") from exc
        runs = records_for().list_runs(workspace_id, connector_id)
        return SourceRunEvidencesResponse(
            items=[run_response_for(run) for run in runs],
            total_count=len(runs),
        )



    @router.get(
        "/api/source-connectors/{connector_id}/runs/latest",
        response_model=SourceRunEvidenceResponse,
    )
    def get_latest_source_connector_run(
        connector_id: str,
        request: Request,
    ) -> SourceRunEvidenceResponse:
        workspace_id = workspace_for(request)
        try:
            records_for().get_connector(workspace_id, connector_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="source connector not found") from exc
        runs = records_for().list_runs(workspace_id, connector_id)
        if not runs:
            raise HTTPException(status_code=404, detail="source run not found")
        return run_response_for(runs[0])



    @router.get(
        "/api/source-connectors/{connector_id}/runs/{run_id}",
        response_model=SourceRunEvidenceResponse,
    )
    def get_source_connector_run(
        connector_id: str,
        run_id: str,
        request: Request,
    ) -> SourceRunEvidenceResponse:
        workspace_id = workspace_for(request)
        try:
            records_for().get_connector(workspace_id, connector_id)
            run = records_for().get_run(workspace_id, run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="source run not found") from exc
        if run.connector_id != connector_id:
            raise HTTPException(status_code=404, detail="source run not found")
        return run_response_for(run)



    @router.post(
        "/api/source-connectors/{connector_id}/sync",
        response_model=SourceSyncResponse,
    )
    def sync_source_connector_endpoint(
        connector_id: str,
        request: Request,
    ) -> SourceSyncResponse:
        workspace_id = workspace_for(request)
        try:
            connector = records_for().get_connector(workspace_id, connector_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="source connector not found") from exc
        run = sync_for(workspace_id, connector)
        return SourceSyncResponse(
            connector=connector_response_for(
                records_for().get_connector(workspace_id, connector_id)
            ),
            run=run_response_for(run),
        )



    @router.get(
        "/api/sources/sync-status",
        response_model=SourceSyncStatusesResponse,
    )
    def get_source_sync_status(request: Request) -> SourceSyncStatusesResponse:
        workspace_id = workspace_for(request)
        statuses = []
        for connector in records_for().list_connectors(workspace_id):
            runs = records_for().list_runs(workspace_id, connector.id)
            latest = runs[0] if runs else None
            latest_success = next((run for run in runs if run.status == "succeeded"), None)
            latest_failure = next((run for run in runs if run.status == "failed"), None)
            statuses.append(
                {
                    "source_type": connector.platform,
                    "configured": connector.connection_status != "capability_unavailable",
                    "last_sync_at": latest.finished_at if latest else None,
                    "last_success_at": latest_success.finished_at if latest_success else None,
                    "last_failure_at": latest_failure.finished_at if latest_failure else None,
                    "error_message": latest_failure.reason if latest_failure else None,
                }
            )
        return SourceSyncStatusesResponse(
            statuses=[
                SourceSyncStatusResponse(**status)
                for status in statuses
            ],
        )



    router.routes = [route for route in router.routes if route.name in route_names]
    return router
