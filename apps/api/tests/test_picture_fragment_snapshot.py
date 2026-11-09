"""Real rollback-journal contention; declared files never start media workers."""
import base64
from dataclasses import replace
import sqlite3
from threading import Event, Thread
import time
from types import SimpleNamespace

import pytest

from app.artifact_store import ArtifactStore
from app.creation.contracts import ArtifactRecord, JobRun, SubtitleCue, SubtitleTrack, TimelineVersion, TransformRun
from app.creation.editing.metadata_use_case import MetadataEditUseCase, default_subtitle_style
from app.creation.picture_fragment_assembly import PictureFragmentAssembly
from app.media.picture_preview import PicturePreviewRenderer
from app.models import utc_now
from app.task_audio import TaskAudio
from test_metadata_edit_use_case import metadata_state
from test_source_task_use_case import source_case as source_case


def _snapshot_case(case, tmp_path, *, layered):
    receipt = case.use_case.create(case.command, case.actor)
    task = case.repo.task_records.get_creation_task('local', receipt['task_id'])
    store = ArtifactStore(tmp_path / 'artifacts')
    audio = TaskAudio(case.repo, store, tmp_path / 'audio')
    metadata = MetadataEditUseCase(case.repo, store, output_root=tmp_path / 'outputs', audio_service=audio)
    renderer = PicturePreviewRenderer(case.repo, store, audio, metadata, None,
        verify_identity=lambda _: pytest.fail('snapshot reads must not observe external identity'),
        presenter_model_ids={}, output_root=tmp_path / 'outputs',
        portrait_worker_path=tmp_path / 'unused-worker', temporary_root=tmp_path / 'work')
    job = case.repo.job_records.create_job_run(JobRun('picture-snapshot-job', 'local', task.id,
        'creation_render', 'snapshot-request', 'snapshot-digest'))
    if layered:
        # Fixed 1x1 PNG and SRT are declared inputs, not model or render output.
        image = store.publish_bytes(f'artifacts/{task.id}/source.png', base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZ1kAAAAASUVORK5CYII='))
        source_job = case.repo.job_records.create_job_run(JobRun('png-source-job', 'local', task.id,
            'graphic_import', 'png-metadata-request', image.sha256))
        case.repo.job_records.claim_job_run('local', source_job.id)
        case.repo.execution_records.create_artifact_record(ArtifactRecord('png-source-record', 'local', task.id,
            'graphic_source', image.logical_ref, image.sha256, image.size_bytes, source_job.id, []))
        observed = store.resolve_file(image.logical_ref).stat()
        case.repo.lineage_records.create_transform_run(TransformRun('png-source-transform', 'local', task.id,
            source_job.id, 'graphic_import', image.sha256, [], [image.logical_ref], 'declared-png-fixture',
            parameters={'width':1, 'height':1, 'file_revision':{'size_bytes':observed.st_size,
                'mtime_ns':observed.st_mtime_ns, 'ctime_ns':observed.st_ctime_ns}}, status='succeeded',
            started_at=utc_now(), finished_at=utc_now()))
        case.repo.job_records.finish_job_run('local', source_job.id, status='succeeded', output_refs=[image.logical_ref])
        srt = store.publish_bytes(f'artifacts/{task.id}/declared.srt',
            '1\n00:00:00,000 --> 00:00:01,000\n原字幕\n'.encode())
        payload = case.repo.get_creation_task_montage_plan('local', task.id)
        payload['graphic_overlays'] = [{'id':'declared-png', 'kind':'image', 'source_ref':image.logical_ref,
            'name':'declared PNG', 'start_seconds':0, 'end_seconds':1}]
        case.repo.montage_plan_adoption.save_creation_task_montage_plan('local', task.id, payload['id'], payload)
        version = TimelineVersion('layered-version', 'local', task.id, receipt['timeline_version_id'], 2,
            {'montage':payload, 'subtitle_track_ids':['declared-track']}, None)
        case.repo.timeline_records.create_timeline_version(version)
        case.repo.subtitle_records.create_subtitle_track(SubtitleTrack('declared-track', 'local', task.id,
            version.id, 'zh-CN', None, default_subtitle_style(), [SubtitleCue('declared-cue', None, '原字幕', 0, 1)],
            'timeline', None, srt.logical_ref, 'declared-subtitle-input', 'valid'))
    service = PictureFragmentAssembly(case.repo, store, tmp_path / 'work', metadata=metadata,
        picture_renderer=renderer, render_budget=None, packet_copy_assembly=None)
    return service, job, audio


@pytest.mark.parametrize('layered', [False, True], ids=['original-without-subtitles', 'subtitle-and-png'])
def test_picture_snapshot_finishes_while_writer_waits_without_nested_readers(source_case, tmp_path, monkeypatch, layered):
    case = source_case
    service, job, audio = _snapshot_case(case, tmp_path, layered=layered)
    connect = sqlite3.connect
    writer_started, writer_done = Event(), Event()
    errors = []
    marker = utc_now().isoformat()
    def write():
        try:
            with connect(case.repo.database_path, timeout=5) as writer:
                writer.execute('BEGIN IMMEDIATE')
                writer.execute('UPDATE creation_tasks SET updated_at=? WHERE id=?', (marker, job.task_id))
                writer_started.set()
                writer.commit()  # Waits for the existing read snapshot, like cancellation.
        except BaseException as exc:
            errors.append(exc)
        finally:
            writer_done.set()
    with case.repo._connect() as snapshot:
        assert snapshot.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        snapshot.execute('BEGIN')
        snapshot.execute('SELECT payload FROM creation_tasks WHERE id=?', (job.task_id,)).fetchone()
        writer = Thread(target=write)
        writer.start()
        try:
            assert writer_started.wait(2), errors
            deadline = time.monotonic() + 2
            while True:
                # Prove SQLite's real PENDING writer has begun refusing new readers.
                probe = connect(case.repo.database_path, timeout=0)
                try:
                    probe.execute('SELECT payload FROM creation_tasks').fetchone()
                except sqlite3.OperationalError as exc:
                    assert 'locked' in str(exc)
                    break
                finally:
                    probe.close()
                assert not writer_done.is_set() and time.monotonic() < deadline, errors
                writer_done.wait(.001)
            with monkeypatch.context() as guard:
                def forbidden(*args, **kwargs):
                    pytest.fail('a borrowed picture snapshot must never open a nested SQLite connection')
                guard.setattr(sqlite3, 'connect', forbidden)
                result = service._inputs(job, case.actor, connection=snapshot)
                task, montage, captions, graphics, sources, images, files, sidecar = result
                assert task.id == job.task_id and montage.script_id is None
                assert montage.clips[0].source_path == case.reference
                assert sources[case.reference]['file_identity']['size_bytes'] == case.path.stat().st_size
                assert files and bool(captions) == bool(images) == bool(graphics) == layered
                assert ('原字幕' in sidecar) == layered
                # The original audio source resolves, authorizes, and reads metadata on the same snapshot.
                assert audio.source('local', task.id, case.reference, actor=case.actor, connection=snapshot) == case.path
                sources = audio._mix_sources('local', task.id, montage.audio_timeline, actor=case.actor,
                    budget=SimpleNamespace(max_input_bytes=1024), connection=snapshot)
                assert sources['inputs'][0]['duration_seconds'] == 9.25
                assert not writer_done.is_set()
        finally:
            snapshot.rollback()
            writer.join(5)
        assert writer_done.is_set() and not writer.is_alive() and not errors
    with case.repo._connect() as conn:
        assert conn.execute('SELECT updated_at FROM creation_tasks WHERE id=?', (job.task_id,)).fetchone()[0] == marker


def test_snapshot_still_rejects_actual_material_use_revocation(source_case, tmp_path, monkeypatch):
    case = source_case
    service, job, _audio = _snapshot_case(case, tmp_path, layered=True)
    case.repo._save_asset(replace(case.asset, use_state='excluded'))
    with case.repo._connect() as snapshot:
        snapshot.execute('BEGIN')
        with monkeypatch.context() as guard:
            guard.setattr(sqlite3, 'connect', lambda *args, **kwargs: pytest.fail('nested connection'))
            with pytest.raises(ValueError, match='使用状态'):
                service._inputs(job, case.actor, connection=snapshot)
        snapshot.rollback()


def test_rendered_subtitle_projection_uses_the_original_snapshot_plan(tmp_path, monkeypatch):
    state = metadata_state(tmp_path)
    # An explicitly seeded rendered predecessor exercises the supported SRT projection.
    state.plan.status = 'rendered'
    state.plan.subtitle_path = state.track.artifact_ref
    version = replace(state.base, snapshot={'transform_run_id':'declared-render-transform'})
    with state.repo._connect() as snapshot:
        snapshot.execute('BEGIN IMMEDIATE')
        snapshot.execute('DELETE FROM subtitle_tracks')
        snapshot.execute("UPDATE creation_plans SET payload=json_set(payload,'$.language','en-US') WHERE id=?",
            (state.task.plan_id,))
        with monkeypatch.context() as guard:
            guard.setattr(sqlite3, 'connect', lambda *args, **kwargs: pytest.fail('nested subtitle connection'))
            track, = state.case.current_subtitle_tracks('ws', state.task,
                current_version=version, montage=state.plan, connection=snapshot)
        assert track.language == 'en-US' and track.artifact_ref == state.track.artifact_ref
        assert track.cues[0].text == '原字幕' and track.status == 'valid'
        snapshot.rollback()
