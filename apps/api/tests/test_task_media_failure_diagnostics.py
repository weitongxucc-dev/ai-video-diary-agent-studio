"""Finite domain refusals survive the original job settlement without exposing raw errors."""
from dataclasses import replace
import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.creation.audio_ingress_use_case import AudioIngressRejected
from app.creation.editing.audio_edit_use_case import AudioEditRejected
from app.creation.editing.command_compile import CommandParseRejected
from app.creation.editing.metadata_use_case import EditPreviewRejected
from app.creation.media_result_use_case import MediaResultRejected, MediaResultUseCase
from app.creation.output_observation_use_case import OutputObservationRejected
from app.media.picture_preview import PicturePreviewRejected
from app.media_script_jobs import MediaScriptPreviewRejected
from app.production import ProductionRepository
from test_task_media_grants import case


@pytest.fixture(autouse=True)
def no_media(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('failure diagnostics exercise metadata and settlement only')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def failures(repo):
    return [event for event in repo.execution_records.list_task_events('local', 'task')
            if event.event_type == 'task_media_failed']


@pytest.mark.parametrize('operation,owner,rejection', [
    ('audio_listen', 'audio_ingress', AudioIngressRejected(409, '原录音文案尚未确认')),
    ('audio_edit_preview', 'audio_edit', AudioEditRejected(422, '音频范围超出原素材')),
    ('output_observation', 'output_observation', OutputObservationRejected(404, '原成片已不存在')),
    ('media_script_preview', 'media_script', MediaScriptPreviewRejected('脚本来源版本已变化')),
    ('visual_edit_preview', 'visual_preview', PicturePreviewRejected(422, '所选字体未安装')),
    ('compound_selection_preview', 'compound_selection', PicturePreviewRejected(403, '所选来源不在原授权范围')),
    ('exact_edit_preview', 'exact_preview', EditPreviewRejected(409, '时间线版本已变化')),
    ('command_edit_preview', 'command_preview', CommandParseRejected(422, '请明确要修改的镜头')),
])
def test_typed_refusal_survives_settlement_restart_and_scoped_reads(tmp_path, operation, owner, rejection):
    repo, actor, state, factory, build, media = case(tmp_path)
    def reject(*args):
        state['calls'] += 1
        raise rejection
    setattr(media, owner, SimpleNamespace(execute=reject))
    runtime = build(media)
    try:
        job = media.submit(runtime, actor, 'task', operation, {}, 'edit_preview')
        assert runtime.run_once()
        saved = repo.job_records.get_job_run('local', job.id)
        assert saved.status == 'failed' and saved.failure_reason == rejection.detail
        event, = failures(repo)
        assert event.job_id == job.id and event.payload['status_code'] == rejection.status_code
        assert event.payload['detail'] == rejection.detail
        with repo._connect() as conn:
            request = conn.execute('SELECT * FROM device_work_requests').fetchone()
            assert request['owner_id'] == job.id and request['state'] == 'failed'
            assert event.payload['device_request_id'] == request['id']
            assert event.payload['grant_generation'] == request['last_grant_generation']
            assert conn.execute('SELECT state FROM device_permit').fetchone()[0] == 'free'
        assert media.submit(runtime, actor, 'task', operation, {}, 'edit_preview').id == job.id
        assert not runtime.run_once() and state['calls'] == 1
        reopened = ProductionRepository(repo.database_path, repo.video_library_path)
        media.repository = reopened
        reader = MediaResultUseCase(reopened, SimpleNamespace())
        receipt = reader.receipt(media, saved, actor, runtime.scheduler)
        assert receipt['job_id'] == job.id and receipt['status'] == 'failed'
        assert receipt['failure_reason'] == rejection.detail and receipt['result'] is None
        assert reader.list(media, reopened.task_records.get_creation_task('local', 'task'), actor,
                           runtime.scheduler)['items'][0]['failure_reason'] == rejection.detail
        with pytest.raises(MediaResultRejected) as denied:
            reader.receipt(media, saved, replace(actor, member_id='other-member'), runtime.scheduler)
        assert denied.value.status_code == 404
        assert failures(reopened)[0].payload == event.payload
    finally:
        runtime.stop(0)


def test_structured_refusal_is_finite_and_preserves_unknown_effect_across_retry(tmp_path):
    dirty = '来源变化 Bearer abc-private https://example.test/private /Users/test/private.wav token=private-token\n'
    detail = {'error': 'edit_request_result_unknown', 'reason': dirty, 'impact': '原成果保留',
              'repair_action': '查询原请求', 'retryable': False, 'submission_effect': 'unknown',
              'raw_response': 'unpublishable-secret', 'nested': {'token': 'unpublishable-secret'}}
    repo, actor, state, factory, build, media = case(tmp_path)
    def reject(*args):
        state['calls'] += 1
        raise EditPreviewRejected(409 if state['calls'] == 1 else 422, detail)
    media.exact_preview = SimpleNamespace(execute=reject)
    runtime = build(media)
    try:
        job = media.submit(runtime, actor, 'task', 'exact_edit_preview', {}, 'edit_preview')
        assert runtime.run_once()
        saved = repo.job_records.get_job_run('local', job.id)
        assert saved.status == 'failed' and saved.failure_reason.startswith('来源变化')
        assert saved.impact == detail['impact'] and saved.repair_action == detail['repair_action']
        first, = failures(repo)
        assert first.payload['detail']['submission_effect'] == 'unknown'
        assert first.payload['detail']['retryable'] is False
        public = json.dumps(first.payload, ensure_ascii=False) + saved.failure_reason
        for secret in ('abc-private', 'example.test', '/Users/test', 'private-token', 'unpublishable-secret', '\n'):
            assert secret not in public
        runtime.scheduler.retry_existing(first.payload['device_request_id'], actor, reason='failed')
        assert runtime.run_once()
        events = sorted(failures(repo), key=lambda event: event.payload['grant_generation'])
        assert [event.payload['status_code'] for event in events] == [409, 422]
        assert events[0].id != events[1].id
        assert all(event.job_id == job.id for event in events)
        with repo._connect() as conn:
            assert conn.execute('SELECT count(*) FROM job_runs').fetchone()[0] == 1
            assert conn.execute('SELECT count(*) FROM device_work_requests').fetchone()[0] == 1
            assert conn.execute('SELECT state FROM device_permit').fetchone()[0] == 'free'
    finally:
        runtime.stop(0)


@pytest.mark.parametrize('rejection', [RuntimeError('Bearer must-not-leak /Users/test/private'),
                                       HTTPException(422, 'http-secret-not-a-domain-refusal'),
                                       PicturePreviewRejected(422, 'wrong-owner-secret')])
def test_unrecognized_error_never_exposes_its_message(tmp_path, rejection):
    def reject(state):
        raise rejection
    repo, actor, state, factory, build, media = case(tmp_path, action=reject)
    runtime = build(media)
    try:
        job = media.submit(runtime, actor, 'task', 'audio_listen', {}, 'audio_listen')
        assert runtime.run_once()
        saved = repo.job_records.get_job_run('local', job.id)
        assert saved.status == 'failed' and saved.failure_reason == type(rejection).__name__
        assert failures(repo) == []
        with repo._connect() as conn:
            assert conn.execute('SELECT state FROM device_permit').fetchone()[0] == 'free'
    finally:
        runtime.stop(0)


def test_workspace_secret_is_resolved_and_removed_before_failure_writer(tmp_path):
    secret = 'opaque-workspace-credential'
    def reject(state):
        raise AudioIngressRejected(422, f'来源拒绝 {secret}，请核对原输入')
    repo, actor, state, factory, build, media = case(tmp_path, action=reject)
    calls = []
    def known_secrets(workspace_id):
        calls.append(workspace_id)
        # Credential lookup must finish before fail_unit owns the SQLite writer.
        with repo._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
        return (secret,)
    media.known_failure_secrets_for = known_secrets
    runtime = build(media)
    try:
        job = media.submit(runtime, actor, 'task', 'audio_listen', {}, 'audio_listen')
        assert runtime.run_once()
        saved = repo.job_records.get_job_run('local', job.id)
        event, = failures(repo)
        assert calls == ['local'] and saved.status == 'failed'
        assert saved.failure_reason == '来源拒绝 [已隐藏]，请核对原输入'
        assert secret not in json.dumps(event.payload, ensure_ascii=False)
        assert event.payload['detail'] == saved.failure_reason
    finally:
        runtime.stop(0)
