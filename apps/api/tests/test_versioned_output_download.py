"""Version fences and response receipts over registered files; no media execution."""
from dataclasses import replace
import hashlib

import pytest

import app.main as main
from app.artifact_store import ArtifactStore
from app.creation.contracts import ArtifactRecord, JobRun, TimelineVersion
from app.creation.contracts import CreationTask
from app.production import ProductionRepository
from test_companion_identity_http import linked as linked, USER, WORKSPACE
from test_reply_script_transfer import make_plan


@pytest.fixture
def output(linked, tmp_path, monkeypatch):
    repository = ProductionRepository(tmp_path / 'output.sqlite3', tmp_path / 'library')
    plan = replace(make_plan(), workspace_id=WORKSPACE, id='plan', creation_task_id=None)
    task = CreationTask(id='task', workspace_id=WORKSPACE, plan_id=plan.id, name='版本下载', scenario='output')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=USER)
    store = ArtifactStore(tmp_path / 'outputs')
    job = repository.job_records.create_job_run(JobRun('job', WORKSPACE, task.id, 'fixture', 'fixture', 'fixture'))
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'artifact_store', store)

    def version(number, *, rendered=True, registered=True):
        refs = []
        for suffix, kind, body in [('mp4', 'rendered_video', f'video-{number}'.encode()),
                                    ('srt', 'subtitle_track', f'subtitles-{number}'.encode())]:
            ref = f'artifacts/task/output-{number}.{suffix}'
            store.publish_bytes(ref, body)
            if registered:
                repository.execution_records.create_artifact_record(ArtifactRecord(f'artifact-{number}-{suffix}', WORKSPACE,
                    task.id, kind, ref, hashlib.sha256(body).hexdigest(), len(body), job.id, []))
            refs.append(ref)
        snapshot = {'montage': {'status': 'rendered' if rendered else 'ready',
            'output_path': refs[0], 'subtitle_path': refs[1]}, 'output_refs': refs if rendered else []}
        result = TimelineVersion(f'v{number}', WORKSPACE, task.id, f'v{number-1}' if number > 1 else None,
            number, snapshot, None)
        repository.timeline_records.create_timeline_version(result)
        return result

    version(1)
    return linked.client, repository, store, version


@pytest.mark.parametrize('suffix,body', [('', b'video-1'), ('/subtitles', b'subtitles-1')])
def test_fixed_version_download_returns_its_actual_receipt_and_range(output, suffix, body):
    client, _, _, _ = output
    path = '/api/creation-tasks/task/output' + suffix + '?expected_version_id=v1'
    response = client.get(path)
    assert response.status_code == 200 and response.content == body, response.text
    assert response.headers['X-Timeline-Version-Id'] == 'v1' and response.headers['X-Timeline-Sequence'] == '1'
    part = client.get(path, headers={'Range': 'bytes=1-3'})
    assert part.status_code == 206 and part.content == body[1:4]
    assert part.headers['X-Timeline-Version-Id'] == 'v1'


def test_old_request_cannot_silently_receive_new_version(output):
    client, _, _, version = output
    version(2)
    response = client.get('/api/creation-tasks/task/output?expected_version_id=v1')
    assert response.status_code == 409, response.text
    current = client.get('/api/creation-tasks/task/output?expected_version_id=v2')
    assert current.status_code == 200 and current.content == b'video-2', current.text
    assert current.headers['X-Timeline-Version-Id'] == 'v2'


@pytest.mark.parametrize('values', [{'rendered': False}, {'registered': False}])
def test_new_version_without_valid_registered_output_never_falls_back_to_old(output, values):
    client, repository, _, version = output
    version(2, **values)
    root = repository.database_path.parent

    def facts():
        with repository._connect() as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: sorted((tuple(row) for row in connection.execute(
                'SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr) for name in tables}

    def tree():
        return {str(item.relative_to(root)): item.read_bytes() if item.is_file() else None
                for item in root.rglob('*')}

    before_facts, before_tree = facts(), tree()
    response = client.get('/api/creation-tasks/task/output?expected_version_id=v2')
    assert response.status_code == 409, response.text
    expected = ('当前版本尚无可下载的有效产物，请等待该版本制作完成。' if values.get('rendered') is False
                else '当前版本产物尚未登记。')
    subtitles = client.get('/api/creation-tasks/task/output/subtitles?expected_version_id=v2')
    assert subtitles.status_code == 409 and subtitles.json()['detail'] == expected
    assert facts() == before_facts
    assert tree() == before_tree


def test_version_change_while_resolving_file_rejects_download(output, monkeypatch):
    client, _, store, version = output
    original = store.resolve_file
    def resolve(ref):
        result = original(ref)
        version(2)
        return result
    monkeypatch.setattr(store, 'resolve_file', resolve)
    response = client.get('/api/creation-tasks/task/output?expected_version_id=v1')
    assert response.status_code == 409, response.text
