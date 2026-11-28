from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.artifact_store import ArtifactStore
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.conversation_repository import ConversationRepository
from test_source_task_use_case import source_case as source_case  # noqa: F401


@pytest.fixture
def subtitle_http(source_case, tmp_path, monkeypatch):
    case = source_case
    settings = Settings(database_path=str(case.repo.database_path),
        video_inbox_path=str(case.path.parent), video_output_path=str(tmp_path / 'output'),
        temporary_root_path=str(tmp_path / 'temp'), supabase_url=None, supabase_publishable_key=None)
    store = ArtifactStore(tmp_path / 'output')
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'production_repo', case.repo)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main, 'artifact_store', store)
    monkeypatch.setattr(main, 'local_members_service', case.members)
    monkeypatch.setattr(main, 'conversation_repo', ConversationRepository(case.repo.database_path))
    monkeypatch.setattr(main, '_creation_runtime',
        lambda: pytest.fail('manual subtitle metadata must not start device execution'))
    with case.repo._connect() as connection:
        token = connection.execute('SELECT token FROM local_members WHERE member_id=?',
            (case.actor.member_id,)).fetchone()[0]
    return case, TestClient(main.app, headers={'X-Local-Member-Token': token}), store


def create_source(case, client, *, preserve_audio=True):
    payload = {**asdict(case.command), 'request_id': str(uuid4()),
        'expected_asset_updated_at': case.command.expected_asset_updated_at.isoformat(),
        'preserve_audio': preserve_audio}
    response = client.post('/api/source-tasks', json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def binding(version_id, sequence):
    return {'request_id': str(uuid4()), 'expected_timeline_version_id': version_id,
        'expected_timeline_sequence': sequence}


def first_cue(version_id, sequence=1):
    return {**binding(version_id, sequence), 'operation_type': 'AddFirstSubtitleCue',
        'text': 'Original audio', 'start_seconds': .25, 'end_seconds': 1.25, 'language': 'en-US'}


def test_first_cue_http_concurrent_save_replay_undo_redo_and_add_again(subtitle_http):
    case, client, store = subtitle_http
    created = create_source(case, client)
    task_id, starting_id = created['task_id'], created['timeline_version_id']
    base = f'/api/creation-tasks/{task_id}'
    versions = case.repo.timeline_records
    subtitles = case.repo.subtitle_records
    original = versions.get_timeline_version('local', starting_id)
    assert original.snapshot['subtitle_track_ids'] == []
    command = first_cue(starting_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        success, competing = sorted(pool.map(lambda payload: client.post(base + '/edits/exact', json=payload),
            [command, {**command, 'request_id': str(uuid4()), 'text': 'Competing cue'}]),
            key=lambda response: response.status_code)
    assert success.status_code == 200, success.text
    assert competing.status_code == 409, competing.text
    operation = success.json()['operation']
    assert operation['operation_type'] == 'AddFirstSubtitleCue'
    saved = versions.get_timeline_version('local', operation['result_version_id'])
    assert (saved.sequence, saved.parent_version_id) == (2, starting_id)
    track, = subtitles.list_subtitle_tracks('local', task_id, timeline_version_id=saved.id)
    assert saved.snapshot['subtitle_track_ids'] == [track.id]
    assert track.language == 'en-US' and track.source_language is None
    cue, = track.cues
    assert cue.source_cue_id is None and (cue.start_seconds, cue.end_seconds) == (.25, 1.25)
    assert f'00:00:00,250 --> 00:00:01,250\n{cue.text}' in store.resolve_file(track.artifact_ref).read_text()
    for key in ('audio_timeline', 'cues', 'clips', 'duration_seconds', 'aspect_ratio'):
        assert saved.snapshot['montage'][key] == original.snapshot['montage'][key]
    winning_payload = json.loads(success.request.content)
    replay = client.post(base + '/edits/exact', json=winning_payload)
    assert replay.status_code == 200 and replay.json() == success.json()
    receipt = client.get(f"{base}/assistant/edit-requests/{winning_payload['request_id']}?phase=apply")
    assert receipt.json()['result'] == success.json()
    assert len(versions.list_timeline_versions('local', task_id)) == 2
    stale_payload = first_cue(starting_id)
    stale = client.post(base + '/edits/exact', json=stale_payload)
    assert stale.status_code == 409
    detail = stale.json()['detail']
    assert detail['error'] == 'first_subtitle_rejected' and detail['submission_effect'] == 'not_submitted'
    stale_receipt = client.get(f"{base}/assistant/edit-requests/{stale_payload['request_id']}?phase=apply")
    assert stale_receipt.json()['result'] == {'detail': detail}
    assert client.post(base + '/edits/exact', json=first_cue(saved.id, 2)).status_code == 409

    def restore(action, version, sequence):
        result = client.post(base + f'/timeline/{action}', json=binding(version, sequence))
        assert result.status_code == 200, result.text
        return versions.get_timeline_version('local', result.json()['operation']['result_version_id'])

    undone = restore('undo', saved.id, 2)
    assert undone.snapshot['subtitle_track_ids'] == []
    assert undone.snapshot['montage']['audio_timeline'] == original.snapshot['montage']['audio_timeline']
    redone = restore('redo', undone.id, 3)
    redone_track, = subtitles.list_subtitle_tracks('local', task_id, timeline_version_id=redone.id)
    assert redone_track.cues == track.cues
    empty_again = restore('undo', redone.id, 4)
    added_again = client.post(base + '/edits/exact', json=first_cue(empty_again.id, 5))
    assert added_again.status_code == 200, added_again.text
    assert versions.get_timeline_version('local', starting_id) == original
    assert versions.get_timeline_version('local', saved.id) == saved
    assert client.post(base + '/edits/exact', json=winning_payload).json() == success.json()


def test_first_cue_http_rejects_invalid_range_non_owner_and_source_without_audio(subtitle_http):
    case, client, _store = subtitle_http
    created = create_source(case, client)
    task_id, starting_id = created['task_id'], created['timeline_version_id']
    url = f'/api/creation-tasks/{task_id}/edits/exact'
    for change in ({'end_seconds': 12}, {'end_seconds': .25}, {'text': ' '}, {'language': 'fr-FR'}):
        rejected = client.post(url, json={**first_cue(starting_id), **change})
        assert rejected.status_code == 422, rejected.text
    other = case.members.add_member('another editor')
    with case.repo._connect() as connection:
        token = connection.execute('SELECT token FROM local_members WHERE member_id=?',
            (other.member_id,)).fetchone()[0]
    rejected = client.post(url, json=first_cue(starting_id), headers={'X-Local-Member-Token': token})
    assert rejected.status_code in (403, 404), rejected.text
    muted = create_source(case, client, preserve_audio=False)
    rejected = client.post(f"/api/creation-tasks/{muted['task_id']}/edits/exact",
        json=first_cue(muted['timeline_version_id']))
    assert rejected.status_code == 409, rejected.text
    for task in (task_id, muted['task_id']):
        assert len(case.repo.timeline_records.list_timeline_versions('local', task)) == 1
        assert not case.repo.edit_records.list_edit_operations('local', task)
    with case.repo._connect() as connection:
        assert connection.execute('SELECT count(*) FROM subtitle_tracks').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM job_runs').fetchone()[0] == 0
