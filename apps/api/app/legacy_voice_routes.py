"""Historical voice HTTP and episode selection views; legacy writes remain retired."""
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from app.schemas import VoiceProfilesResponse, VoiceProfileResponse, VoiceProfileUpsertRequest, EpisodeVoiceSelectionResponse

def build_legacy_voice_router(*, records_for, workspace_for, profiles_type_for, profile_type_for, http_error_type_for, capability_for, selection_type_for, episodes_for, authority_type_for, authority_error_for) -> APIRouter:
    router = APIRouter()

    @router.get('/api/production/voices', response_model=VoiceProfilesResponse)
    def list_production_voices(request: Request) -> VoiceProfilesResponse:
        """Read historical voice rows only; new writes use versioned media profiles."""
        voices = records_for().list_voice_profiles(workspace_for()(request))
        return profiles_type_for()(voices=[profile_type_for().model_validate(voice, from_attributes=True) for voice in voices], total_count=len(voices))

    @router.post('/api/production/voices', response_model=VoiceProfileResponse)
    def create_production_voice(request: Request, payload: VoiceProfileUpsertRequest) -> VoiceProfileResponse:
        del request, payload
        raise http_error_type_for()(status_code=410, detail={'error': 'legacy_voice_profile_write_retired', 'impact': '旧声音档案不会再作为创作任务的配音事实源。', 'repair_action': '使用 /api/media/voice-profiles 保存带人物、授权和版本的声音档案。', 'retryable': False})

    @router.put('/api/production/voices/{voice_profile_id}', response_model=VoiceProfileResponse)
    def update_production_voice(voice_profile_id: str, request: Request, payload: VoiceProfileUpsertRequest) -> VoiceProfileResponse:
        del voice_profile_id, request, payload
        raise http_error_type_for()(status_code=410, detail={'error': 'legacy_voice_profile_write_retired', 'impact': '旧声音档案不会再作为创作任务的配音事实源。', 'repair_action': '使用 /api/media/voice-profiles 创建新的声音版本。', 'retryable': False})

    @router.get('/api/production/voices/{voice_profile_id}/media')
    def get_production_voice_media(voice_profile_id: str, request: Request) -> FileResponse:
        workspace_id = workspace_for()(request)
        try:
            records_for().get_voice_profile(workspace_id, voice_profile_id)
        except KeyError as exc:
            raise http_error_type_for()(status_code=404, detail='voice profile not found') from exc
        raise capability_for()('voice_reference_mapping', message='声音参考仅保存为工作区逻辑引用，当前 API 未配置已授权媒体根映射。', impact='不能直接读取或返回声音参考媒体。', repair_action='配置工作区已授权媒体根映射后重试。')

    def _episode_voice_selection_response(workspace_id: str, episode_id: str) -> EpisodeVoiceSelectionResponse:
        identity = records_for().selected_episode_versioned_voice_identity(workspace_id, episode_id)
        if identity is None:
            return selection_type_for()(episode_id=episode_id, voice_profile_id=None, voice_version=None, language=None, authorization_id=None, authorization_version=None, pronunciation_entry_ids=[], source='none')
        inputs = records_for().resolve_episode_narration_inputs(workspace_id, episode_id)
        voice = inputs.voice
        return selection_type_for()(episode_id=episode_id, voice_profile_id=voice.id, voice_version=voice.version, language=voice.language, authorization_id=voice.authorization.id, authorization_version=voice.authorization.version, pronunciation_entry_ids=[entry.id for entry in inputs.pronunciation_entries], source='episode')

    @router.get('/api/production/episodes/{episode_id}/voice', response_model=EpisodeVoiceSelectionResponse)
    def get_episode_voice_selection(episode_id: str, request: Request) -> EpisodeVoiceSelectionResponse:
        workspace_id = workspace_for()(request)
        try:
            episodes_for().get(episode_id, workspace_id=workspace_id)
        except KeyError as exc:
            raise http_error_type_for()(status_code=404, detail='episode not found') from exc
        try:
            return _episode_voice_selection_response(workspace_id, episode_id)
        except authority_type_for() as exc:
            raise authority_error_for()(exc) from exc
    return router
