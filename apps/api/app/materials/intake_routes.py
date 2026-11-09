"""Explicit material intake and directory-discovery HTTP actions.

Existing owners keep their file access, durable receipts, transactions and work.
The host retains current service qualification and analysis-driver lifecycle.
"""
from __future__ import annotations

from app.materials.intake import MaterialIntakeUseCase
from app.materials.source_files import MaterialSourceFileAccess

from contextlib import contextmanager
from typing import Callable, Protocol

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.schemas import (
    MaterialIntakeStatusResponse, MaterialIntakeBindRequest,
    MaterialCopyCompleteRequest, MaterialCopyCompleteResponse,
    MaterialLibraryResponse, MaterialAssetResponse,
    MaterialDiscoveryStartRequest, MaterialDiscoveryControlRequest,
    MaterialDiscoveryReceiveRequest,
)


class MaterialIntakeRecords(Protocol):
    material_intake: MaterialIntakeUseCase
    material_source_files: MaterialSourceFileAccess
    def list_assets_page(self, workspace_id, *, limit, offset, include_private,
                         viewer_user_id, local_diagnostic): ...
    def get_asset(self, workspace_id, asset_id): ...


class MaterialDiscoveryActions(Protocol):
    def roots(self): ...
    def latest(self, workspace_id, owner_user_id): ...
    def start(self, workspace_id, owner_user_id, *, root_ids, exclusions): ...
    def snapshot(self, session_id, workspace_id, owner_user_id, *, offset=0): ...
    def advance(self, session_id, workspace_id, owner_user_id): ...
    def control(self, session_id, workspace_id, owner_user_id, action, *, exclusions): ...
    def receive(self, session_id, workspace_id, owner_user_id, candidate_ids): ...
    def preview_path(self, session_id, candidate_id, workspace_id, owner_user_id): ...


def material_binding_status(workspace_id: str, repository: MaterialIntakeRecords) -> MaterialIntakeStatusResponse:
    directory = repository.material_source_files.material_ingestion_directory_state(workspace_id)
    binding = repository.material_source_files.get_material_ingestion_binding()
    foreign = bool(directory['bound_to_another_workspace']) or (binding is not None and binding[0] != workspace_id)
    bound = binding is not None and binding[0] == workspace_id
    if foreign:
        state = 'unknown'
        message = '素材目录已关联其他工作区；当前工作区不能读取。'
        repair = '切换到原工作区，或先明确解除原绑定。'
    elif directory['configured_path_changed']:
        state = 'changed'
        message = '当前设置的素材目录与本工作区原绑定目录不同。'
        repair = '先检查目录设置并处理原绑定，再重新确认。'
    else:
        state = str(directory['status'])
        message, repair = {'ready': ('素材目录可访问；确认复制完成后才接收并分析。' if bound else '素材目录可访问，等待关联到当前工作区。', None), 'missing': ('找不到当前设置的素材目录。', '连接存储盘或在设置中选择现有目录。'), 'unmounted': ('素材所在的外接盘没有挂载。', '连接原数据盘后重试。'), 'read_only': ('素材目录当前不可写，素材无法进入分析。', '检查磁盘是否以只读方式挂载或目录权限是否被改动，再重新检查。'), 'invalid': ('当前素材目录不可用于自动导入。', '在设置中选择可访问的素材目录。')}.get(state, ('尚未确认素材目录状态。', '检查目录设置后重试。'))
    return MaterialIntakeStatusResponse(bound_to_current_workspace=bound, binding_available=not foreign, configured_directory=None if foreign else str(directory['directory']), bound_directory=binding[1] if bound else None, directory_state=state, directory_message=message, repair_action=repair, scan_interval_seconds=60, file_counts=repository.material_source_files.material_ingestion_state_counts(workspace_id) if bound else {'observing': 0, 'running': 0, 'ready': 0, 'failed': 0, 'blocked_disk': 0}, pending_files=repository.material_source_files.pending_material_copies(workspace_id) if bound else [])


@contextmanager
def material_discovery_errors():
    try:
        yield
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='扫描任务或候选素材不存在。') from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail='无法读取所选位置，请检查目录权限。') from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail='媒体目录暂不可用，请连接原磁盘后重试。') from exc


class MaterialIntakeHTTPAdapter:
    def __init__(self, *, records_for: Callable[[], MaterialIntakeRecords],
                 discovery_for: Callable[..., MaterialDiscoveryActions],
                 workspace_for, reader_for, member_scope, ingestion_for,
                 execution_mode, asset_response, discovery_exclusions, wake_received):
        self._records_for = records_for
        self._discovery_for = discovery_for
        self._workspace_for = workspace_for
        self._reader_for = reader_for
        self._member_scope = member_scope
        self._ingestion_for = ingestion_for
        self._execution_mode = execution_mode
        self._asset_response = asset_response
        self._discovery_exclusions = discovery_exclusions
        self._wake_received = wake_received


    def get_material_intake_status(self, request: Request) -> MaterialIntakeStatusResponse:
        repository = self._records_for()
        workspace_id = self._workspace_for(request)
        self._reader_for(request, workspace_id)
        return material_binding_status(workspace_id, repository)

    def bind_material_intake(self, request: Request, payload: MaterialIntakeBindRequest) -> MaterialIntakeStatusResponse:
        workspace_id = self._workspace_for(request)
        repository = self._records_for()
        service = self._ingestion_for(repository)
        try:
            repository.material_source_files.bind_material_ingestion(workspace_id, expected_directory=payload.expected_directory, verified_owner_user_id=self._reader_for(request, workspace_id)[0])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.wake()
        return material_binding_status(workspace_id, repository)

    def confirm_material_copy_complete(self, request: Request, payload: MaterialCopyCompleteRequest) -> MaterialCopyCompleteResponse:
        workspace_id = self._workspace_for(request)
        repository = self._records_for()
        service = self._ingestion_for(repository)
        try:
            service.confirm_copy_complete(workspace_id, relative_path=payload.relative_path, expected_revision=payload.expected_revision.model_dump(), confirmation_source=payload.confirmation_source, verified_owner_user_id=self._reader_for(request, workspace_id)[0])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail='文件已移除，请重新检查素材目录。') from exc
        return MaterialCopyCompleteResponse(confirmed=True, intake=material_binding_status(workspace_id, repository))

    def scan_material_intake(self, request: Request) -> MaterialLibraryResponse:
        workspace_id = self._workspace_for(request)
        viewer_user_id, local_diagnostic = self._reader_for(request, workspace_id)
        repository = self._records_for()
        status = material_binding_status(workspace_id, repository)
        if not status.bound_to_current_workspace:
            raise HTTPException(status_code=409, detail='请先在当前工作区绑定素材目录')
        if status.directory_state != 'ready':
            raise HTTPException(status_code=409, detail=status.directory_message)
        self._ingestion_for(repository).scan_once()
        assets, total = repository.list_assets_page(workspace_id, limit=24, offset=0, include_private=local_diagnostic, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
        return MaterialLibraryResponse(assets=[self._asset_response(asset) for asset in assets], total_count=total, limit=24, offset=0, has_more=len(assets) < total)

    def retry_material_intake(self, asset_id: str, request: Request) -> MaterialAssetResponse:
        workspace_id = self._workspace_for(request)
        repository = self._records_for()
        service = self._ingestion_for(repository)
        if self._execution_mode() == 'scheduled':
            raise HTTPException(status_code=503, detail='素材手动重试的调度授权尚未接通。')
        status = material_binding_status(workspace_id, repository)
        if status.directory_state != 'ready':
            raise HTTPException(status_code=409, detail=status.directory_message)
        try:
            repository.material_intake.retry_material_ingestion(workspace_id, asset_id)
            asset = repository.get_asset(workspace_id, asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='素材不属于当前工作区') from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.wake()
        return self._asset_response(asset)

    def material_discovery_roots(self, request: Request):
        workspace_id, owner = self._member_scope(request)
        with material_discovery_errors():
            discovery = self._discovery_for()
            return {'roots': discovery.roots(), 'exclusions': self._discovery_exclusions(discovery, workspace_id, owner)}

    def latest_material_discovery(self, request: Request):
        workspace_id, owner = self._member_scope(request)
        with material_discovery_errors():
            return self._discovery_for().latest(workspace_id, owner)

    def start_material_discovery(self, request: Request, payload: MaterialDiscoveryStartRequest):
        workspace_id, owner = self._member_scope(request)
        with material_discovery_errors():
            return self._discovery_for().start(workspace_id, owner, root_ids=payload.root_ids, exclusions=payload.exclusions)

    def get_material_discovery(self, session_id: str, request: Request, offset: int=0):
        workspace_id, owner = self._member_scope(request)
        with material_discovery_errors():
            return self._discovery_for().snapshot(session_id, workspace_id, owner, offset=offset)

    def advance_material_discovery(self, session_id: str, request: Request):
        workspace_id, owner = self._member_scope(request)
        with material_discovery_errors():
            return self._discovery_for().advance(session_id, workspace_id, owner)

    def control_material_discovery(self, session_id: str, request: Request, payload: MaterialDiscoveryControlRequest):
        workspace_id, owner = self._member_scope(request)
        with material_discovery_errors():
            return self._discovery_for().control(session_id, workspace_id, owner, payload.action, exclusions=payload.exclusions)

    def receive_discovered_materials(self, session_id: str, request: Request, payload: MaterialDiscoveryReceiveRequest):
        workspace_id, owner = self._member_scope(request)
        with material_discovery_errors():
            repository = self._records_for()
            result = self._discovery_for(repository).receive(session_id, workspace_id, owner, payload.candidate_ids)
            self._wake_received(repository)
            return result

    def preview_discovered_material(self, session_id: str, candidate_id: str, request: Request):
        workspace_id, owner = self._member_scope(request)
        with material_discovery_errors():
            path = self._discovery_for().preview_path(session_id, candidate_id, workspace_id, owner)
            return FileResponse(path, filename=path.name, content_disposition_type='inline')


def build_material_intake_router(adapter_for, route_names: tuple[str, ...] | None = None) -> APIRouter:
    """Register each route at its original position among neighboring families."""
    router = APIRouter()

    @router.get('/api/production/materials/intake', response_model=MaterialIntakeStatusResponse)
    def get_material_intake_status(request: Request) -> MaterialIntakeStatusResponse:
        return adapter_for().get_material_intake_status(request)

    @router.post('/api/production/materials/intake/bind', response_model=MaterialIntakeStatusResponse)
    def bind_material_intake(request: Request, payload: MaterialIntakeBindRequest) -> MaterialIntakeStatusResponse:
        return adapter_for().bind_material_intake(request, payload)

    @router.post('/api/production/materials/intake/copy-complete', response_model=MaterialCopyCompleteResponse)
    def confirm_material_copy_complete(request: Request, payload: MaterialCopyCompleteRequest) -> MaterialCopyCompleteResponse:
        return adapter_for().confirm_material_copy_complete(request, payload)

    @router.post('/api/production/materials/intake/scan')
    def scan_material_intake(request: Request) -> MaterialLibraryResponse:
        return adapter_for().scan_material_intake(request)

    @router.post('/api/production/materials/{asset_id}/intake/retry')
    def retry_material_intake(asset_id: str, request: Request) -> MaterialAssetResponse:
        return adapter_for().retry_material_intake(asset_id, request)

    @router.get('/api/production/materials/discovery/roots')
    def material_discovery_roots(request: Request):
        return adapter_for().material_discovery_roots(request)

    @router.get('/api/production/materials/discovery/latest')
    def latest_material_discovery(request: Request):
        return adapter_for().latest_material_discovery(request)

    @router.post('/api/production/materials/discovery', status_code=201)
    def start_material_discovery(request: Request, payload: MaterialDiscoveryStartRequest):
        return adapter_for().start_material_discovery(request, payload)

    @router.get('/api/production/materials/discovery/{session_id}')
    def get_material_discovery(session_id: str, request: Request, offset: int=0):
        return adapter_for().get_material_discovery(session_id, request, offset)

    @router.post('/api/production/materials/discovery/{session_id}/advance')
    def advance_material_discovery(session_id: str, request: Request):
        return adapter_for().advance_material_discovery(session_id, request)

    @router.post('/api/production/materials/discovery/{session_id}/control')
    def control_material_discovery(session_id: str, request: Request, payload: MaterialDiscoveryControlRequest):
        return adapter_for().control_material_discovery(session_id, request, payload)

    @router.post('/api/production/materials/discovery/{session_id}/receive')
    def receive_discovered_materials(session_id: str, request: Request, payload: MaterialDiscoveryReceiveRequest):
        return adapter_for().receive_discovered_materials(session_id, request, payload)

    @router.get('/api/production/materials/discovery/{session_id}/candidates/{candidate_id}/preview')
    def preview_discovered_material(session_id: str, candidate_id: str, request: Request):
        return adapter_for().preview_discovered_material(session_id, candidate_id, request)

    if route_names is not None:
        router.routes = [route for route in router.routes if route.name in route_names]
    return router
