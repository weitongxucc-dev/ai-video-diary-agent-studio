"""Material scan and analysis HTTP; ingestion and readiness remain with their original owners."""
from fastapi import APIRouter, Request
from app.schemas import (
    MaterialAnalysisRunResponse,
    MaterialAnalysisRunsResponse,
    MaterialAssetResponse,
    MaterialAssetAssertionResponse,
    MaterialDataGovernanceResponse,
    MaterialEvidenceRefResponse,
    MaterialIndexRecordResponse,
    MaterialLibraryResponse,
    MaterialSegmentLineageResponse,
    MaterialClipReferenceMigrationResponse,
    SemanticFieldDefinitionResponse,
    SemanticFieldDefinitionsResponse,
    MaterialAssertionRequest,
    MaterialAssertionStatusRequest,
    MaterialQualityProfileResponse,
    MaterialIndexRebuildJobResponse,
    MaterialIndexRebuildRequest,
)
from app.materials.data_governance import MaterialAssetAssertion
from uuid import uuid4

def build_material_processing_router(*, workspace_for, reader_for, mode_for, ingestion_for, production_for, library_response_for, asset_response_for, http_error_for, visible_for):
    router = APIRouter()

    @router.post('/api/production/materials/scan', response_model=MaterialLibraryResponse)
    def scan_production_materials(request: Request) -> MaterialLibraryResponse:
        workspace_id = workspace_for()(request)
        viewer_user_id, local_diagnostic = reader_for()(request, workspace_id)
        if mode_for() == 'scheduled':
            ingestion_for()(production_for()).scan_once()
        else:
            production_for().material_intake.scan_library(workspace_id)
        assets, total = production_for().list_assets_page(workspace_id, limit=24, offset=0, include_private=local_diagnostic, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
        return library_response_for()(assets=[asset_response_for()(asset) for asset in assets], total_count=total, limit=24, offset=0, has_more=len(assets) < total)

    @router.post('/api/production/materials/{asset_id}/analyze', response_model=MaterialAssetResponse)
    def analyze_production_material(asset_id: str, request: Request) -> MaterialAssetResponse:
        if mode_for() == 'scheduled':
            ingestion_for()(production_for())
            raise http_error_for()(status_code=503, detail='素材手动分析的调度授权尚未接通。')
        try:
            asset = production_for().analyze_asset(workspace_for()(request), asset_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='material asset not found') from exc
        except ValueError as exc:
            raise http_error_for()(status_code=422, detail=str(exc)) from exc
        return asset_response_for()(asset)

    @router.get('/api/production/materials/{asset_id}/analysis-runs', response_model=MaterialAnalysisRunsResponse)
    def list_material_analysis_runs(asset_id: str, request: Request, limit: int = 20) -> MaterialAnalysisRunsResponse:
        if limit < 1 or limit > 100:
            raise http_error_for()(status_code=422, detail='分析运行查询数量必须在 1 到 100 之间')
        workspace_id = workspace_for()(request)
        try:
            asset = production_for().get_asset(workspace_id, asset_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='material asset not found') from exc
        if not visible_for()(request, workspace_id, asset.visibility, asset.owner_user_id):
            raise http_error_for()(status_code=404, detail='material asset not found')
        runs = production_for().list_material_analysis_runs(workspace_id, asset_id, limit=limit)
        return MaterialAnalysisRunsResponse(
            runs=[
                MaterialAnalysisRunResponse.model_validate(run, from_attributes=True)
                for run in runs
            ]
        )

    @router.get('/api/production/materials/{asset_id}/data-governance', response_model=MaterialDataGovernanceResponse)
    def get_material_data_governance(asset_id: str, request: Request) -> MaterialDataGovernanceResponse:
        workspace_id = workspace_for()(request)
        try:
            asset = production_for().get_asset(workspace_id, asset_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='material asset not found') from exc
        if not visible_for()(request, workspace_id, asset.visibility, asset.owner_user_id):
            raise http_error_for()(status_code=404, detail='material asset not found')
        return MaterialDataGovernanceResponse(
            analysis_runs=[
                MaterialAnalysisRunResponse.model_validate(run, from_attributes=True)
                for run in production_for().list_material_analysis_runs(workspace_id, asset_id, limit=20)
            ],
            evidence=[
                MaterialEvidenceRefResponse.model_validate(item, from_attributes=True)
                for item in production_for().list_material_evidence(workspace_id, asset_id)
            ],
            assertions=[
                MaterialAssetAssertionResponse.model_validate(item, from_attributes=True)
                for item in production_for().list_material_assertions(workspace_id, asset_id)
            ],
            indexes=[
                MaterialIndexRecordResponse.model_validate(item, from_attributes=True)
                for item in production_for().list_material_indexes(workspace_id, asset_id)
            ],
            lineage=[
                MaterialSegmentLineageResponse.model_validate(item, from_attributes=True)
                for item in production_for().list_material_segment_lineage(workspace_id, asset_id)
            ],
            clip_reference_migrations=[
                MaterialClipReferenceMigrationResponse.model_validate(item, from_attributes=True)
                for item in production_for().material_data_governance.list_clip_reference_migrations(workspace_id, asset_id)
            ],
            quality_profiles=[
                MaterialQualityProfileResponse.model_validate(item, from_attributes=True)
                for item in production_for().list_material_quality_profiles(workspace_id, asset_id)
            ],
            index_rebuild_jobs=[
                MaterialIndexRebuildJobResponse.model_validate(item, from_attributes=True)
                for item in production_for().list_material_index_rebuild_jobs(workspace_id, asset_id)
            ],
        )

    @router.post('/api/production/materials/{asset_id}/assertions', response_model=MaterialAssetAssertionResponse)
    def create_material_assertion(asset_id: str, request: Request, payload: MaterialAssertionRequest) -> MaterialAssetAssertionResponse:
        workspace_id = workspace_for()(request)
        owner, _diagnostic = reader_for()(request, workspace_id)
        try:
            asset = production_for().get_asset(workspace_id, asset_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='material asset not found') from exc
        if not visible_for()(request, workspace_id, asset.visibility, asset.owner_user_id):
            raise http_error_for()(status_code=404, detail='material asset not found')
        if payload.segment_id is not None and all(segment.id != payload.segment_id for segment in asset.segments):
            raise http_error_for()(status_code=422, detail='segment not found')
        assertion = MaterialAssetAssertion(
            id=f"asr_{uuid4().hex}", workspace_id=workspace_id, asset_id=asset_id,
            segment_id=payload.segment_id, field_ref=payload.field_ref, value=payload.value,
            unit=payload.unit, qualifiers=payload.qualifiers, evidence_refs=tuple(payload.evidence_refs),
            origin='user_confirmation', confidence=payload.confidence, status=payload.status,
            asserted_by=owner or 'local-diagnostic', supersedes_ref=payload.supersedes_ref,
        )
        try:
            saved = production_for().record_material_assertion(assertion)
        except ValueError as exc:
            raise http_error_for()(status_code=422, detail=str(exc)) from exc
        return MaterialAssetAssertionResponse.model_validate(saved, from_attributes=True)

    @router.put('/api/production/materials/{asset_id}/assertions/{assertion_id}/status', response_model=MaterialAssetAssertionResponse)
    def update_material_assertion_status(asset_id: str, assertion_id: str, request: Request, payload: MaterialAssertionStatusRequest) -> MaterialAssetAssertionResponse:
        workspace_id = workspace_for()(request)
        try:
            asset = production_for().get_asset(workspace_id, asset_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='material asset not found') from exc
        if not visible_for()(request, workspace_id, asset.visibility, asset.owner_user_id):
            raise http_error_for()(status_code=404, detail='material asset not found')
        try:
            saved = production_for().transition_material_assertion(
                workspace_id, asset_id, assertion_id, status=payload.status, expected_status=payload.expected_status,
            )
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='material assertion not found') from exc
        except ValueError as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc
        return MaterialAssetAssertionResponse.model_validate(saved, from_attributes=True)

    @router.post('/api/production/materials/{asset_id}/index-rebuilds', response_model=MaterialIndexRebuildJobResponse)
    def queue_material_index_rebuild(asset_id: str, request: Request, payload: MaterialIndexRebuildRequest) -> MaterialIndexRebuildJobResponse:
        workspace_id = workspace_for()(request)
        try:
            asset = production_for().get_asset(workspace_id, asset_id)
        except KeyError as exc:
            raise http_error_for()(status_code=404, detail='material asset not found') from exc
        if not visible_for()(request, workspace_id, asset.visibility, asset.owner_user_id):
            raise http_error_for()(status_code=404, detail='material asset not found')
        try:
            job = production_for().queue_material_index_rebuild(
                workspace_id, asset_id, index_type=payload.index_type,
                source_revision=payload.source_revision, source_digest=payload.source_digest,
                index_version=payload.index_version,
            )
        except ValueError as exc:
            raise http_error_for()(status_code=422, detail=str(exc)) from exc
        return MaterialIndexRebuildJobResponse.model_validate(job, from_attributes=True)

    @router.get('/api/production/data-governance/fields', response_model=SemanticFieldDefinitionsResponse)
    def list_data_governance_fields(request: Request) -> SemanticFieldDefinitionsResponse:
        workspace_id = workspace_for()(request)
        return SemanticFieldDefinitionsResponse(
            fields=[
                SemanticFieldDefinitionResponse.model_validate(item, from_attributes=True)
                for item in production_for().list_semantic_field_definitions(workspace_id)
            ]
        )
    return router
