from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.voice as voice_module
from app.asr import ASRTranscription, ASRTranscriptionError, LocalWhisperTranscriber
from app.auth import SupabaseAuthContext
from app.config import Settings


@pytest.fixture
def tencent_settings(monkeypatch, tmp_path):
    import subprocess
    import tempfile
    import wave
    from app.asr import TencentCloudASRTranscriber
    from app.device_runtime import DeviceRuntime
    def forbidden(*args, **kwargs):
        pytest.fail('transcription protocol evidence forbids actual ASR/media/runtime execution')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'start', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'run_once', forbidden)
    monkeypatch.setattr(TencentCloudASRTranscriber, 'transcribe', forbidden)
    monkeypatch.setattr(main_module, '_creation_runtime', forbidden)
    temporary = tmp_path / 'temporary'
    temporary.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(temporary))
    settings = Settings(
        asr_provider="tencentcloud",
        tencentcloud_secret_id="test-only-id",
        tencentcloud_secret_key="test-only-key",
        local_whisper_python_path=None,
        local_whisper_cli_path=None,
        local_whisper_model_path=None,
        database_path=str(tmp_path/'metadata.sqlite3'), video_output_path=str(tmp_path/'output'),
        model_root_path=str(tmp_path/'models'), temporary_root_path=str(temporary),
        supabase_url=None, supabase_publishable_key=None,
    )
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, 'auth_context', SupabaseAuthContext(settings))
    return settings


@pytest.mark.parametrize("local_state", ["ready", "missing", "failed"])
def test_material_request_uses_only_local_whisper_even_when_tencent_is_configured(
    monkeypatch, tencent_settings, local_state
):
    configured_selector = Mock(side_effect=AssertionError("material request must not select Tencent"))
    monkeypatch.setattr(voice_module, "asr_transcriber_from_settings", configured_selector)
    # Readiness/execution are mocked; the HTTP route and local-only selection are real.
    if local_state != "missing":
        monkeypatch.setattr(LocalWhisperTranscriber, "configured", property(lambda self: True))
    local_calls = []

    def local_transcribe(self, path):
        assert isinstance(self, LocalWhisperTranscriber)
        assert self._settings is tencent_settings
        assert Path(path).read_bytes() == b"test-only-audio"
        local_calls.append(path)
        if local_state == "failed":
            raise ASRTranscriptionError("本地转写失败（测试）")
        return ASRTranscription(provider="local_whisper", text="测试本地草稿", segments=[])

    monkeypatch.setattr(LocalWhisperTranscriber, "transcribe", local_transcribe)
    response = TestClient(main_module.app).post(
        "/api/voice/transcribe",
        files={"audio": ("note.webm", b"test-only-audio", "audio/webm")},
        data={"local_only": "true"},
    )

    configured_selector.assert_not_called()
    assert main_module.settings.asr_provider == "tencentcloud"
    if local_state == "ready":
        assert response.status_code == 200
        assert response.json() == {"transcript": "测试本地草稿", "provider": "local_whisper", "segments": [], "confidence": 0.0}
    else:
        assert response.status_code == 503
        assert "本地" in response.json()["detail"]
        assert "TENCENTCLOUD" not in response.json()["detail"]
        if local_state == "missing":
            assert "直接输入素材说明" in response.json()["detail"]
    assert len(local_calls) == (0 if local_state == "missing" else 1)
    assert all(not Path(path).exists() for path in local_calls)
    if local_state == 'missing':
        empty = TestClient(main_module.app).post('/api/voice/transcribe',
            files={'audio': ('empty.webm', b'', 'audio/webm')}, data={'local_only': 'true'})
        assert empty.status_code == 400 and empty.json()['detail'] == 'Empty audio file'
        configured_selector.assert_not_called()
        assert list(Path(tencent_settings.temporary_root_path).iterdir()) == []


def test_existing_transcription_requests_still_use_the_configured_provider(monkeypatch, tencent_settings):
    configured = Mock()
    configured.configured = True
    configured.transcribe.return_value = ASRTranscription(provider="tencentcloud", text="测试旧消费者", segments=[])
    configured_selector = Mock(return_value=configured)
    monkeypatch.setattr(voice_module, "asr_transcriber_from_settings", configured_selector)
    monkeypatch.setattr(LocalWhisperTranscriber, "transcribe", Mock(side_effect=AssertionError("default must not force local")))

    response = TestClient(main_module.app).post(
        "/api/voice/transcribe", files={"audio": ("legacy.webm", b"test-only-audio", "audio/webm")},
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "tencentcloud"
    configured_selector.assert_called_once_with(tencent_settings)
    configured.transcribe.assert_called_once()
    temporary_input = Path(configured.transcribe.call_args.args[0])
    assert temporary_input.parent == Path(tencent_settings.temporary_root_path)
    assert not temporary_input.exists()
    assert list(temporary_input.parent.iterdir()) == []
