"""Video-intake HTTP metadata and early refusals, without video processing."""
import os
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.asr import LocalWhisperTranscriber, TencentCloudASRTranscriber
from app.config import Settings
from app.device_runtime import DeviceRuntime
from app.drafts import DraftWorkspace
from app.schemas import DraftStatusResponse, PendingVideoResponse
from app.video import FfmpegVideoProcessor, VideoInbox
from app.video_pipeline import RetakeVideoRefiner, VideoProcessingPipeline, video_processing_pipeline_from_settings


def test_video_intake_metadata_and_processing_refusals(tmp_path, monkeypatch):
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append('media-or-runtime')
        pytest.fail('video-intake metadata forbids processing, ASR and runtime execution')

    # Install before isolated directory/owner seed; AnyIO/TestClient threads remain available.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    for owner, names in (
        (DeviceRuntime, ('start', 'run_once')),
        (VideoProcessingPipeline, ('process', 'process_batch')),
        (FfmpegVideoProcessor, ('process', 'process_batch')),
        (RetakeVideoRefiner, ('refine',)),
        (LocalWhisperTranscriber, ('transcribe',)),
        (TencentCloudASRTranscriber, ('transcribe',)),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    for name in ('_creation_runtime', '_start_device_runtime', 'run_task_turn', 'run_plan_turn'):
        monkeypatch.setattr(main, name, forbidden)

    inbox, output, drafts, jianying = (tmp_path / name for name in ('inbox', 'output', 'drafts', 'jianying'))
    for path in (inbox, output, drafts, jianying):
        path.mkdir()
    # Opaque inventory bytes: no generated/decoded video or claimed output.
    (inbox / 'old.mov').write_bytes(b'old')
    (inbox / 'new.mp4').write_bytes(b'new')
    (inbox / 'notes.txt').write_text('ignore', encoding='utf-8')
    os.utime(inbox / 'old.mov', (1_700_000_000, 1_700_000_000))
    os.utime(inbox / 'new.mp4', (1_800_000_000, 1_800_000_000))
    settings = Settings(video_inbox_path=str(inbox), video_output_path=str(output),
                        draft_output_path=str(drafts), jianying_draft_root_path=str(jianying))
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'video_inbox', VideoInbox(inbox))
    monkeypatch.setattr(main, 'video_pipeline', video_processing_pipeline_from_settings(settings))
    monkeypatch.setattr(main, 'draft_workspace', DraftWorkspace(settings))
    client = TestClient(main.app)  # No lifespan/startup.

    def files():
        return {str(path.relative_to(tmp_path)): path.read_bytes()
                for path in tmp_path.rglob('*') if path.is_file()}

    before = files()
    pending = client.get('/api/videos/pending')
    assert pending.status_code == 200, pending.text
    videos = [PendingVideoResponse.model_validate(item) for item in pending.json()]
    assert [video.file_name for video in videos] == ['new.mp4', 'old.mov']
    assert all(video.size_bytes == 3 and video.path == str(inbox / video.file_name) for video in videos)
    status = client.get('/api/drafts/status')
    assert status.status_code == 200, status.text
    draft = DraftStatusResponse.model_validate(status.json())
    assert draft.processed_videos == [] and draft.packages == []
    assert draft.environment.output_path == str(drafts) and draft.environment.output_exists
    assert draft.environment.processed_video_count == 0 and draft.environment.package_count == 0
    assert draft.environment.jianying_root_path == str(jianying) and draft.environment.jianying_root_exists
    assert not draft.environment.editable_draft_supported
    assert client.get('/api/videos/processed/missing.mp4').status_code == 404
    with monkeypatch.context() as missing_ffmpeg:
        missing_ffmpeg.setattr(shutil, 'which', lambda command: None)
        rejected = client.post('/api/videos/process', json={'file_name': ''})
        assert rejected.status_code == 503, rejected.text
        assert 'ffmpeg' in rejected.json()['detail']['error']
    with monkeypatch.context() as declared_ffmpeg:
        declared_ffmpeg.setattr(shutil, 'which', lambda command: '/declared-only/ffmpeg')
        rejected = client.post('/api/videos/process', json={'file_name': ''})
        assert rejected.status_code == 400, rejected.text
        assert rejected.json()['detail'] == 'file_name is required'
    assert files() == before
    assert forbidden_calls == []
