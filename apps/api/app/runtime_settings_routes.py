"""Runtime settings and onboarding HTTP; original persistence and reload lifecycle remain host-owned."""
from fastapi import APIRouter
import os
from fastapi import HTTPException, Request
from app.auth import OnboardingStateError
from app.model_installer import ModelInstallError
from app.repository import LEGACY_WORKSPACE_ID
from app.runtime_settings import RuntimeSettingsSaveError
from app.workspace_settings import WorkspaceSecretStoreError
from app.schemas import OnboardingCompletionResponse, RuntimeHealthResponse, RuntimeSettingsResponse, RuntimeSettingsUpdateRequest, SetupPreflightResponse

def build_runtime_settings_router(*, workspace_for, settings_for, auth_for, workspace_settings_for, health_for, setup_for, storage_preflight_for, local_host_for, device_values_for, env_updates_for, settings_type_for, catalog_builder_for, installer_for, reload_lock_for, reload_allowed_for, prepare_reload_for, quiesce_for, save_for, activate_for, env_path_for, route_names) -> APIRouter:
    router = APIRouter()

    @router.get('/api/runtime/health', response_model=RuntimeHealthResponse)
    def get_runtime_health(request: Request) -> RuntimeHealthResponse:
        workspace_id = workspace_for()(request)
        return RuntimeHealthResponse.model_validate(health_for()(workspace_settings_for()(workspace_id), workspace_id))

    @router.get('/api/setup/preflight', response_model=SetupPreflightResponse)
    def get_setup_preflight() -> SetupPreflightResponse:
        """Run a native setup preflight and return a stable consumable structure.

    Returns overall ready|blocked with per-check details including
    status, summary, impact, repair_action, and optional settings_target.
    Never leaks secrets or absolute sensitive content.
    """
        return SetupPreflightResponse.model_validate(setup_for()(settings_for()))

    @router.post('/api/onboarding/complete', response_model=OnboardingCompletionResponse)
    def complete_onboarding(request: Request) -> OnboardingCompletionResponse:
        """Initialize the trusted workspace after its minimum storage check."""
        principal = getattr(request.state, 'principal', None)
        if auth_for().enabled and (principal is None or not principal.user_id or (not principal.workspace_id)):
            raise HTTPException(status_code=403, detail='workspace context is required')
        preflight = storage_preflight_for()(settings_for())
        if preflight.get('overall') != 'ready':
            raise HTTPException(status_code=409, detail='workspace storage is not ready')
        if not auth_for().enabled:
            return OnboardingCompletionResponse(workspace_id=LEGACY_WORKSPACE_ID, completed=True)
        try:
            auth_for().complete_onboarding(principal.user_id, principal.workspace_id, principal.access_token or '')
        except OnboardingStateError as exc:
            raise HTTPException(status_code=503, detail='unable to persist onboarding state') from exc
        return OnboardingCompletionResponse(workspace_id=principal.workspace_id, completed=True)

    @router.get('/api/settings/runtime', response_model=RuntimeSettingsResponse)
    def get_runtime_settings(request: Request) -> RuntimeSettingsResponse:
        return RuntimeSettingsResponse.model_validate(workspace_settings_for()(workspace_for()(request)).safe_runtime_settings(workspace_for()(request)))

    @router.post('/api/settings/runtime', response_model=RuntimeSettingsResponse)
    def update_runtime_settings(request: Request, payload: RuntimeSettingsUpdateRequest) -> RuntimeSettingsResponse:
        client_host = request.client.host if request.client else None
        if not local_host_for()(client_host):
            raise HTTPException(status_code=403, detail='runtime settings can only be updated from a local client')
        workspace_id = workspace_for()(request)
        submitted = payload.model_dump()
        device_values = device_values_for()(submitted)
        try:
            with reload_lock_for():
                reload_allowed_for()()
                prospective_environment = {**os.environ, **env_updates_for()(device_values)}
                prospective_settings = settings_type_for().from_env(prospective_environment)
                candidate_catalog = catalog_builder_for()(prospective_settings)
                with installer_for().guard_catalog_update(candidate_catalog):
                    prepared = prepare_reload_for()(prospective_settings)
                    with quiesce_for()() as drivers:
                        changed = save_for()(workspace_id, env_path_for(), submitted)
                        if changed:
                            try:
                                activate_for()(prepared, drivers)
                            except Exception as exc:
                                raise HTTPException(status_code=503, detail='配置已保存，运行重载失败；请检查运行状态。') from exc
        except RuntimeSettingsSaveError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except WorkspaceSecretStoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ModelInstallError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RuntimeSettingsResponse.model_validate(workspace_settings_for()(workspace_id).safe_runtime_settings(workspace_id))

    @router.get('/api/is_setup')
    async def is_setup():
        """Check whether the minimum backend configuration is ready."""
        current_settings = settings_type_for().from_env()
        preflight = setup_for()(current_settings)
        missing = [str(check['id']) for check in preflight['checks'] if check['status'] in {'missing', 'invalid'} and check['required']]
        configured = preflight['overall'] == 'ready'
        return {'configured': configured, 'missing': missing, 'next_step': '/welcome' if missing else ''}
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
