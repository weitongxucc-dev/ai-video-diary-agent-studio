"""Read source-clock evidence using current material visibility and version."""
from fastapi import APIRouter, HTTPException, Query, Request
from app.materials.evidence import MaterialEvidenceConflict


def build_material_evidence_router(*, records_for, workspace_for, reader_for):
    router = APIRouter()

    @router.get('/api/production/materials/{asset_id}/evidence')
    def read_material_evidence(asset_id: str, request: Request,
            kind: str = "segments", expected_updated_at: str | None = None,
            start_seconds: float = Query(0, ge=0), end_seconds: float | None = Query(None, gt=0),
            offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=20)):
        workspace_id = workspace_for(request)
        member_id, diagnostic = reader_for(request, workspace_id)
        try:
            return records_for().material_assets.read_evidence(
                workspace_id, asset_id, member_id=member_id, local_diagnostic=diagnostic,
                kind=kind, expected_updated_at=expected_updated_at, start_seconds=start_seconds,
                end_seconds=end_seconds, offset=offset, limit=limit)
        except KeyError as exc:
            raise HTTPException(404, "素材不存在或无权读取") from exc
        except MaterialEvidenceConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
