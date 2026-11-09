"""Voice-reference import early refusals with real owner, read and isolated identity."""
import subprocess
import wave

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

import app.main as main
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.device_runtime import DeviceRuntime
from app.local_members import LocalMemberService
from app.production import ProductionRepository


def test_voice_reference_filename_and_empty_bytes_refuse_without_write(tmp_path, monkeypatch):
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append('decode-or-execution')
        pytest.fail('voice reference metadata forbids WAV decode and media/runtime execution')

    # Before actual repository/member seed; empty content must reject before wave.open.
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'start', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'run_once', forbidden)
    monkeypatch.setattr(main, '_creation_runtime', forbidden)
    monkeypatch.setattr(main, 'transcribe_audio_bytes', forbidden)
    monkeypatch.setattr(main.narration_generator, 'generate_for_creation_task', forbidden)
    records = ProductionRepository(tmp_path/'reference.sqlite3', tmp_path/'library')
    members = LocalMemberService(records.database_path)
    _, token = members.whoami(None)
    settings = Settings(database_path=str(records.database_path), video_inbox_path=str(records.video_library_path),
        video_output_path=str(tmp_path/'output'), model_root_path=str(tmp_path/'models'),
        temporary_root_path=str(tmp_path/'temporary'), supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'production_repo', records)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main, 'active_workspace_id', lambda: 'local')
    read_calls = []
    original_read = UploadFile.read

    async def read_spy(self, size=-1):
        read_calls.append((self.filename, size))
        return await original_read(self, size)

    monkeypatch.setattr(UploadFile, 'read', read_spy)
    client = TestClient(main.app, headers={'X-Local-Member-Token': token})  # No lifespan.

    def database():
        with records._connect() as conn:
            return list(conn.iterdump())

    def tree():
        return {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob('*')}

    before_db, before_tree = database(), tree()
    extension = client.post('/api/media/voice-references/import',
        files={'audio': ('reference.mp3', b'opaque', 'audio/mpeg')})
    assert extension.status_code == 422 and extension.json()['detail'] == '请选择 WAV 声音参考文件'
    assert read_calls == []
    empty = client.post('/api/media/voice-references/import', files={'audio': ('reference.wav', b'', 'audio/wav')})
    assert empty.status_code == 422 and empty.json()['detail'] == '声音参考 WAV 不能为空'
    assert read_calls == [('reference.wav', 50 * 1024 * 1024 + 1)]
    assert database() == before_db
    assert tree() == before_tree
    assert forbidden_calls == []
