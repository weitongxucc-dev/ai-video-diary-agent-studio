"""Media provider capability HTTP; readiness and quality selection remain provider-owned."""
from typing import Literal
from fastapi import APIRouter
from app.schemas import MediaCapabilityResponse

def build_media_capability_router(*, voice_for, unavailable_for, lipsync_for, response_type_for) -> APIRouter:
    router = APIRouter()

    @router.get('/api/media/capabilities/voice-clone', response_model=MediaCapabilityResponse)
    def get_voice_clone_capability() -> MediaCapabilityResponse:
        if not voice_for().ready:
            raise unavailable_for()('voice_clone', message=voice_for().message, impact='声音复制未执行，不能产出或返回配音媒体。', repair_action='安装并配置本地声音复制运行环境后重新查询。')
        return response_type_for()(capability_id='voice_clone', available=True, model_id=voice_for().model_id, message=voice_for().message, impact=None, repair_action=None)

    @router.get('/api/media/capabilities/avatar-lipsync', response_model=MediaCapabilityResponse)
    def get_avatar_lipsync_capability(quality: Literal['high', 'fast']='fast') -> MediaCapabilityResponse:
        provider = lipsync_for()(quality)
        if not provider.ready:
            raise unavailable_for()('avatar_lipsync', message=provider.message, impact='人物与口型步骤未完成，不能生成或返回人物视频。', repair_action='配置明确的本地人物口型运行环境后重新查询。')
        return response_type_for()(capability_id='avatar_lipsync', available=True, model_id=provider.model_id, message=provider.message, impact=None, repair_action=None)
    return router
