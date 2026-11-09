"""Desktop shutdown admission HTTP; common-budget stopping and application lifecycle state retain their owners."""
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Request
from pydantic import BaseModel

class DesktopShutdownRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    exit_request_id: UUID
    deadline_at_utc: datetime

def build_desktop_shutdown_router(*, shutdown_path, app_for, ipaddress_for, regex_for, hmac_for, http_error_for, datetime_for, utc_for, time_for, lock_for, shutdown_type_for, runtime_for, automation_for, ingestion_for, assistant_for, stop_assistant_for, conversations_for, stop_auth_for, stop_permit_for):
    router = APIRouter()

    @router.post(shutdown_path)
    def desktop_shutdown(payload: DesktopShutdownRequest, request: Request):
        token = app_for().state.desktop_control_token
        supplied = request.headers.get('X-Yaohe-Desktop-Control', '')
        try:
            loopback = request.client is not None and ipaddress_for().ip_address(request.client.host).is_loopback
        except ValueError:
            loopback = False
        if not loopback or not regex_for().fullmatch('[0-9a-f]{64}', token) or (not regex_for().fullmatch('[0-9a-f]{64}', supplied)) or (not hmac_for().compare_digest(token, supplied)):
            raise http_error_for()(status_code=403, detail='本次启动的桌面退出控制凭据无效')
        deadline = payload.deadline_at_utc
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise http_error_for()(status_code=422, detail='退出截止必须包含UTC时区')
        remaining = (deadline - datetime_for().now(utc_for())).total_seconds()
        if remaining <= 0:
            raise http_error_for()(status_code=408, detail='退出请求已超期')
        deadline_monotonic = time_for().monotonic() + min(30.0, remaining)
        with lock_for():
            if app_for().state.desktop_shutdown is None:
                app_for().state.desktop_shutdown = shutdown_type_for()(runtime=runtime_for(), automation=automation_for(), ingestion=ingestion_for(), assistant=assistant_for(), stop_assistant=stop_assistant_for(), conversations=conversations_for(), close_clients=lambda: (stop_auth_for()(), stop_permit_for()()))
            shutdown = app_for().state.desktop_shutdown
        try:
            return shutdown.execute(str(payload.exit_request_id), deadline_monotonic)
        except ValueError as exc:
            raise http_error_for()(status_code=409, detail=str(exc)) from exc
    return router
