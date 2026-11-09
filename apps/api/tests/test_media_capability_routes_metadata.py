"""Real unavailable-provider HTTP metadata; never executes a model or renderer."""
import subprocess
import wave

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.auth import SupabaseAuthContext
from app.avatar_lipsync import (UnavailableAvatarAndLipSyncProvider, SubprocessAvatarAndLipSyncProvider,
                               LatentSyncAvatarAndLipSyncProvider)
from app.config import Settings
from app.device_runtime import DeviceRuntime
from app.model_installer import ModelInstaller
from app.model_runtime import ModelCatalog
from app.voice_clone import (UnavailableVoiceCloneProvider, Qwen3TTSSubprocessProvider,
                             voice_clone_provider_from_catalog)


def test_media_capability_unavailable_metadata_and_quality_refusal(tmp_path, monkeypatch):
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append('execution')
        pytest.fail('capability metadata forbids model/install/runtime/media execution')

    # Before missing catalog/provider construction; readiness/property reads remain real.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'start', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'run_once', forbidden)
    monkeypatch.setattr(ModelInstaller, 'start', forbidden)
    for provider_type in (UnavailableVoiceCloneProvider, Qwen3TTSSubprocessProvider):
        monkeypatch.setattr(provider_type, 'generate', forbidden)
    for provider_type in (UnavailableAvatarAndLipSyncProvider, SubprocessAvatarAndLipSyncProvider,
                          LatentSyncAvatarAndLipSyncProvider):
        monkeypatch.setattr(provider_type, 'render', forbidden)
    monkeypatch.setattr(main, '_creation_runtime', forbidden)
    settings = Settings(database_path=str(tmp_path/'metadata.sqlite3'), model_root_path=str(tmp_path/'models'),
        video_output_path=str(tmp_path/'output'), temporary_root_path=str(tmp_path/'temporary'),
        supabase_url=None, supabase_publishable_key=None)
    catalog = ModelCatalog(tmp_path/'models', tmp_path/'runtime/bin/python',
                           tmp_path/'visual-worker.py', tmp_path/'voice-worker.py')
    voice = voice_clone_provider_from_catalog(catalog, temporary_root=tmp_path/'temporary')
    avatar = UnavailableAvatarAndLipSyncProvider()
    high_quality = UnavailableAvatarAndLipSyncProvider('隔离的高质量人物运行环境未配置。')
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main, 'model_catalog', catalog)
    monkeypatch.setattr(main, 'voice_clone_provider', voice)
    monkeypatch.setattr(main, 'avatar_lipsync_provider', avatar)
    monkeypatch.setattr(main, 'latentsync_provider', high_quality)
    client = TestClient(main.app)  # No lifespan. Original _lipsync_provider stays intact.

    def tree():
        return {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob('*')}

    before = tree()
    voice_response = client.get('/api/media/capabilities/voice-clone')
    assert voice_response.status_code == 501, voice_response.text
    assert voice_response.json()['detail'] == {
        'error': 'capability_unavailable', 'capability_id': 'voice_clone', 'detail': voice.message,
        'impact': '声音复制未执行，不能产出或返回配音媒体。',
        'repair_action': '安装并配置本地声音复制运行环境后重新查询。', 'retryable': False}
    avatar_response = client.get('/api/media/capabilities/avatar-lipsync')
    assert avatar_response.status_code == 501, avatar_response.text
    assert avatar_response.json()['detail'] == {
        'error': 'capability_unavailable', 'capability_id': 'avatar_lipsync', 'detail': avatar.message,
        'impact': '人物与口型步骤未完成，不能生成或返回人物视频。',
        'repair_action': '配置明确的本地人物口型运行环境后重新查询。', 'retryable': False}
    assert 'media_url' not in voice_response.json()['detail']
    assert 'media_url' not in avatar_response.json()['detail']
    invalid = client.get('/api/media/capabilities/avatar-lipsync?quality=invalid')
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()['detail'][0]['loc'] == ['query', 'quality']
    assert tree() == before
    assert forbidden_calls == []
