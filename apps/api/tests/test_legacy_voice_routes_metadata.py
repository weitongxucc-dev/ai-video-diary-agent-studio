"""Historical voice metadata/refusals only; no voice reference or narration execution."""
from app.profiles.input_resolution import ProfileInputResolver
from copy import deepcopy
from datetime import date
import subprocess
import wave

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.device_runtime import DeviceRuntime
from app.models import Episode, EpisodeStatus
from app.production import ProductionRepository
from app.profiles.legacy_voice import LegacyVoiceProfileStore
from app.repository import EpisodeRepository
from test_legacy_voice_store import voice


def test_legacy_voice_metadata_and_retired_write_refusals(tmp_path, monkeypatch):
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append('execution-or-media')
        pytest.fail('legacy voice metadata forbids media, runtime and positive narration resolution')

    # Before isolated DB/in-memory seed; no audio files are constructed or read.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'start', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'run_once', forbidden)
    monkeypatch.setattr(ProfileInputResolver, 'resolve_episode_narration_inputs', forbidden)
    monkeypatch.setattr(main, '_creation_runtime', forbidden)
    monkeypatch.setattr(main, 'transcribe_audio_bytes', forbidden)
    monkeypatch.setattr(main.narration_generator, 'generate_for_creation_task', forbidden)
    records = ProductionRepository(tmp_path / 'legacy.sqlite3', tmp_path / 'library')
    records.save_voice_profile(voice('default', 'local', default=True))
    records.save_voice_profile(voice('new', 'local'))
    records.save_voice_profile(voice('foreign', 'foreign', default=True))
    episodes = EpisodeRepository()
    episodes._episodes = {'episode': Episode('episode', 1, date(2026, 10, 30), EpisodeStatus.CREATED)}
    before_episodes = deepcopy(episodes._episodes)
    monkeypatch.setattr(main, 'production_repo', records)
    monkeypatch.setattr(main, 'repo', episodes)
    monkeypatch.setattr(main, 'current_workspace_id', lambda request: request.headers.get('X-Test-Workspace', 'local'))
    monkeypatch.setattr(LegacyVoiceProfileStore, 'save_voice_profile', forbidden)
    client = TestClient(main.app)  # No lifespan/runtime startup.

    def dump():
        with records._connect() as conn:
            return list(conn.iterdump())

    before = dump()
    listing = client.get('/api/production/voices')
    assert listing.status_code == 200, listing.text
    assert listing.json()['total_count'] == 2
    assert [item['id'] for item in listing.json()['voices']] == ['default', 'new']
    assert listing.json()['voices'][0]['is_default'] is True
    foreign = client.get('/api/production/voices', headers={'X-Test-Workspace': 'foreign'})
    assert foreign.status_code == 200 and [item['id'] for item in foreign.json()['voices']] == ['foreign']
    payload = {'name': 'retired write', 'reference_audio_path': 'workspace://opaque-reference.wav',
               'reference_text': 'logical metadata only', 'consent_confirmed': True}
    for method, path in [('POST', '/api/production/voices'), ('PUT', '/api/production/voices/default')]:
        retired = client.request(method, path, json=payload)
        assert retired.status_code == 410, retired.text
        assert retired.json()['detail']['error'] == 'legacy_voice_profile_write_retired'
        invalid = client.request(method, path, json={**payload, 'reference_audio_path': 'https://invalid.example/voice.wav'})
        assert invalid.status_code == 422, invalid.text
    missing = client.get('/api/production/voices/missing/media')
    assert missing.status_code == 404 and missing.json()['detail'] == 'voice profile not found'
    existing = client.get('/api/production/voices/default/media')
    assert existing.status_code == 501 and existing.json()['detail']['capability_id'] == 'voice_reference_mapping'
    assert client.get('/api/production/voices/default/media', headers={'X-Test-Workspace': 'foreign'}).status_code == 404
    assert client.get('/api/production/episodes/missing/voice').status_code == 404
    selected = client.get('/api/production/episodes/episode/voice')
    assert selected.status_code == 200, selected.text
    assert selected.json() == {'episode_id': 'episode', 'voice_profile_id': None, 'voice_version': None,
        'language': None, 'authorization_id': None, 'authorization_version': None,
        'pronunciation_entry_ids': [], 'source': 'none'}
    assert dump() == before and episodes._episodes == before_episodes

    # Historical unversioned selection seed exercises the real identity rejection, never the positive resolver.
    with records._connect() as conn:
        conn.execute('INSERT INTO episode_voice_selections (workspace_id,episode_id,voice_profile_id,voice_version,updated_at) VALUES (?,?,?,?,?)',
                     ('local', 'episode', 'default', None, '2026-10-30T00:00:00+00:00'))
    before_legacy_selection = dump()
    blocked = client.get('/api/production/episodes/episode/voice')
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['detail']['error'] == 'legacy_voice_selection_requires_migration'
    assert dump() == before_legacy_selection and episodes._episodes == before_episodes
    assert forbidden_calls == []
