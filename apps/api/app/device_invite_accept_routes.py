"""Identity-only device invite acceptance HTTP; trusted credentials and RPC policy remain with their existing owners."""
from fastapi import APIRouter, Request
from pydantic import BaseModel

class DeviceInviteAcceptRequest(BaseModel):
    invite_token: str

class DeviceInviteAcceptResponse(BaseModel):
    target_workspace_id: str
    member_count: int
    already_accepted: bool

def build_device_invite_accept_router(*, service_type_for, settings_for, credential_for, error_type_for, error_status_for, json_response_for, response_for):
    router = APIRouter()

    @router.post('/api/device-invites/accept')
    async def accept_device_invite(request: Request, payload: DeviceInviteAcceptRequest):
        """Accept a device invite for the verified identity (no workspace required).

    The middleware already authenticated the request's identity only
    (``authenticate_identity``) and attached a workspaceless principal. The
    trusted native runtime supplies the DeviceCredential; the Supabase RPC
    re-checks ``auth.uid()``, the device proof and the invite token.
    """
        principal = request.state.principal
        try:
            service = service_type_for()(settings_for(), credential_for()())
            acceptance = service.accept_invite(principal, payload.invite_token)
        except error_type_for() as exc:
            status = error_status_for().get(exc.kind, 500)
            return json_response_for()(status_code=status, content={'error': exc.kind, 'detail': str(exc)})
        return response_for()(target_workspace_id=acceptance.target_workspace_id, member_count=acceptance.member_count, already_accepted=acceptance.already_accepted)
    return router
