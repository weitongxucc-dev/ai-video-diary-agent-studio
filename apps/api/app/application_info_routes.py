"""Public application information HTTP; configuration, workflow metadata and build probes remain host-owned."""
from fastapi import APIRouter
from app.schemas import SystemStatusResponse, WorkflowStatusResponse

def build_application_info_router(*, system_type_for, settings_for, workflow_type_for, workflow_for, process_for, file_for, route_names) -> APIRouter:
    router = APIRouter()

    @router.get('/api/system/status', response_model=SystemStatusResponse)
    def get_system_status() -> SystemStatusResponse:
        return system_type_for().model_validate(settings_for().public_status())

    @router.get('/api/workflow/status', response_model=WorkflowStatusResponse)
    def get_workflow_status() -> WorkflowStatusResponse:
        return workflow_type_for()(framework='LangGraph', node_names=workflow_for().node_names(), human_review_nodes=workflow_for().human_review_nodes())

    @router.get('/api/version')
    async def get_version():
        """Return the current API version, build info, and completed phases."""
        from pathlib import Path as _Path
        version = '0.1.0'
        pyproject_path = _Path(file_for()).resolve().parent.parent / 'pyproject.toml'
        if pyproject_path.exists():
            for line in pyproject_path.read_text(encoding='utf-8').splitlines():
                stripped = line.strip()
                if stripped.startswith('version'):
                    version = stripped.split('=', 1)[1].strip().strip('"')
                    break
        git_commit = ''
        build_date = ''
        try:
            result = process_for().run(['git', 'log', '-1', '--format=%H,%aI'], capture_output=True, text=True, timeout=5, check=False, cwd=_Path(file_for()).resolve().parents[3])
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split(',', 1)
                git_commit = parts[0]
                build_date = parts[1] if len(parts) > 1 else ''
        except (OSError, process_for().SubprocessError):
            pass
        return {'version': version, 'build_date': build_date, 'git_commit': git_commit, 'phases_completed': ['phase-a-script-control-feedback-foundations', 'phase-b-production-modes-chain-source-lifecycle', 'phase-c-publish-feedback-episode-history', 'phase-d-source-sync-preference-cards', 'phase-0-emergency-hardening']}
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
