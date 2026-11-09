"""Production workspace profile and foundation HTTP; persistence and readiness remain owner-owned."""
from fastapi import APIRouter, Request
from app.production import ProductionProfile
from app.schemas import ProductionFoundationResponse, ProductionProfileResponse, ProductionProfileUpdateRequest

def build_production_profile_router(*, records_for, catalog_for, voice_spec_for, replace_for, workspace_for, reader_for, foundation_type_for, profile_response_type_for, sources_configured_for, settings_for, vault_for, batch_response_for, montage_response_for, montages_for, episodes_for, episode_status_for, http_error_type_for, profile_type_for, route_names) -> APIRouter:
    router = APIRouter()

    def production_profile_with_model_status(workspace_id: str) -> ProductionProfile:
        profile = records_for().get_profile(workspace_id)
        status = catalog_for().status(voice_spec_for())
        model_status = 'ready' if status.ready else 'failed' if status.state == 'invalid' else 'blocked'
        return replace_for()(profile, voice_model_status=model_status, voice_model_message=status.message)

    @router.get('/api/production/foundation', response_model=ProductionFoundationResponse)
    def get_production_foundation(request: Request) -> ProductionFoundationResponse:
        workspace_id = workspace_for()(request)
        viewer_user_id, local_diagnostic = reader_for()(request, workspace_id)
        profile = production_profile_with_model_status(workspace_id)
        return foundation_type_for()(profile=profile_response_type_for().model_validate(profile, from_attributes=True), readiness=records_for().foundation_readiness.readiness(workspace_id, profile=profile, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic, topic_sources_configured=sources_configured_for()(settings_for(), vault_for())), recent_batches=[batch_response_for()(batch) for batch in records_for().list_batches(workspace_id)[:8]], recent_montage_plans=[montage_response_for()(plan) for plan in montages_for().recent(workspace_id, limit=8)], eligible_episodes=[{'id': episode.id, 'title': episode.script.title_options[0] if episode.script and episode.script.title_options else f'Episode {episode.day_number}', 'status': episode.status} for episode in episodes_for().all(workspace_id=workspace_id) if episode.script is not None and episode.status in {episode_status_for().SCRIPT_APPROVED, episode_status_for().WAITING_VIDEO, episode_status_for().VIDEO_PROCESSING, episode_status_for().DRAFT_READY, episode_status_for().PUBLISHED}][:50])

    @router.put('/api/production/profile', response_model=ProductionProfileResponse)
    def update_production_profile(request: Request, payload: ProductionProfileUpdateRequest) -> ProductionProfileResponse:
        workspace_id = workspace_for()(request)
        current = records_for().get_profile(workspace_id)
        updates = payload.model_dump()
        legacy_voice_fields = {'voice_name': str(updates['voice_name']).strip(), 'voice_reference_path': updates['voice_reference_path'], 'voice_reference_text': str(updates['voice_reference_text']).strip(), 'voice_consent_confirmed': bool(updates['voice_consent_confirmed'])}
        current_legacy_voice_fields = {'voice_name': current.voice_name, 'voice_reference_path': current.voice_reference_path, 'voice_reference_text': current.voice_reference_text, 'voice_consent_confirmed': current.voice_consent_confirmed}
        submitted_legacy_voice_fields = payload.model_fields_set.intersection(legacy_voice_fields)
        if any((legacy_voice_fields[field] != current_legacy_voice_fields[field] for field in submitted_legacy_voice_fields)):
            raise http_error_type_for()(status_code=410, detail={'error': 'legacy_profile_voice_write_retired', 'impact': '生产档案中的旧声音字段不会再用于创作任务或新声音登记。', 'repair_action': '使用 /api/media/voice-profiles 保存带人物、授权和版本的声音档案。', 'retryable': False})
        duration_min = int(updates['target_duration_min_seconds']) if updates['target_duration_min_seconds'] is not None else current.target_duration_min_seconds
        duration_max = int(updates['target_duration_max_seconds']) if updates['target_duration_max_seconds'] is not None else current.target_duration_max_seconds
        if duration_min > duration_max:
            raise http_error_type_for()(status_code=400, detail='目标时长下限不能大于上限')
        voice_runtime_status = catalog_for().status(voice_spec_for())
        profile = profile_type_for()(workspace_id=workspace_id, workspace_name=str(updates['workspace_name']).strip(), brand_name=str(updates['brand_name']).strip(), primary_platform='douyin', aspect_ratio='9:16', target_duration_min_seconds=duration_min, target_duration_max_seconds=duration_max, brand_template_name=str(updates['brand_template_name']).strip(), brand_colors=[str(item).strip() for item in updates['brand_colors'] if str(item).strip()], subtitle_style=str(updates['subtitle_style']).strip(), intro_text=str(updates['intro_text']).strip(), outro_text=str(updates['outro_text']).strip(), background_music_path=updates['background_music_path'], voice_name=current.voice_name, voice_reference_path=current.voice_reference_path, voice_reference_text=current.voice_reference_text, voice_consent_confirmed=current.voice_consent_confirmed, voice_model_status='ready' if voice_runtime_status.ready else 'failed' if voice_runtime_status.state == 'invalid' else 'blocked', voice_model_message=voice_runtime_status.message, source_urls=[str(item).strip() for item in updates['source_urls'] if str(item).strip()], use_obsidian=bool(updates['use_obsidian']), use_hermes=bool(updates['use_hermes']), factuality_gate_enabled=bool(updates['factuality_gate_enabled']), cloud_enhancement_enabled=bool(updates['cloud_enhancement_enabled']))
        saved = records_for().save_profile(profile)
        return profile_response_type_for().model_validate(saved, from_attributes=True)
    router.routes = [route for route in router.routes if route.name in route_names]
    return router
