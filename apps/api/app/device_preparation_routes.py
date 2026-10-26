"""HTTP adapter for the device preparation application."""
from fastapi import APIRouter, HTTPException, Request

from app.device_preparation_application import DevicePreparationConflict
from app.device_resource_profile import DeviceProfileUnavailable
from app.device_scheduler import RecoveryBlocked
from app.schemas import DevicePreparationRequest


def _respond(action):
    try:
        return action()
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, '设备准备请求不存在') from exc
    except (DeviceProfileUnavailable, DevicePreparationConflict, ValueError, RecoveryBlocked) as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def build_device_preparation_router(*, authorized_actor, application):
    router = APIRouter(prefix='/api/device/resource-preparations')

    @router.post('')
    def prepare(payload: DevicePreparationRequest, request: Request):
        actor = authorized_actor(request)
        return _respond(lambda: application(kind=payload.kind).dispatch('prepare',
            shape=payload.shape.model_dump(), request_id=str(payload.request_id), actor=actor))

    @router.get('/{request_id}')
    def status(request_id: str, request: Request):
        actor = authorized_actor(request)
        return _respond(lambda: application(request_id=request_id, actor=actor).status(request_id, actor))

    @router.post('/{request_id}/finalize')
    def finalize(request_id: str, request: Request):
        actor = authorized_actor(request)
        return _respond(lambda: application(request_id=request_id, actor=actor).finalize(request_id, actor))

    return router
