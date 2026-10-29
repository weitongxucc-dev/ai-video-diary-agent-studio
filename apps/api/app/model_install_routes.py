"""Local model catalog, install request and persisted job HTTP views."""
from fastapi import APIRouter, HTTPException, Request
from app.model_installer import ModelInstallError
from app.schemas import ModelCatalogResponse, ModelInstallRequest, ModelInstallJobResponse


def build_model_install_router(*, catalog_for, installer_for, is_local_host) -> APIRouter:
    router = APIRouter()

    @router.get("/api/models", response_model=ModelCatalogResponse)
    def list_local_models() -> ModelCatalogResponse:
        return ModelCatalogResponse(
            models=[status.as_dict() for status in catalog_for().list_statuses()]
        )



    @router.post(
        "/api/models/{model_id}/install",
        response_model=ModelInstallJobResponse,
        status_code=202,
    )
    def install_local_model(
        request: Request,
        model_id: str,
        payload: ModelInstallRequest,
    ) -> ModelInstallJobResponse:
        client_host = request.client.host if request.client else None
        if not is_local_host(client_host):
            raise HTTPException(
                status_code=403,
                detail="local models can only be installed from a local client",
            )
        try:
            job = installer_for().start(
                model_id,
                license_accepted=payload.license_accepted,
            )
        except ModelInstallError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ModelInstallJobResponse.model_validate(job)



    @router.get(
        "/api/model-install-jobs/{job_id}",
        response_model=ModelInstallJobResponse,
    )
    def get_model_install_job(job_id: str) -> ModelInstallJobResponse:
        return ModelInstallJobResponse.model_validate(installer_for().get_job(job_id))



    return router
