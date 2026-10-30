"""Persist declared input understanding and reject semantic unlock authority; no Pi/model execution."""
import subprocess
from uuid import uuid4
import wave

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.conversation_repository import ConversationRepository
from app.creation.contracts import CreationTask
from app.creation.plan_contracts import CreationPlan
from app.device_runtime import DeviceRuntime
from app.device_scheduler import DeviceScheduler
from app.local_members import LocalMemberService
from app.production import ProductionRepository
from app.schemas import NarrationSemanticReviewRequest


def test_assistant_semantic_receipts_persist_understanding_and_reject_unlock_without_effect(tmp_path, monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append('execution')
        pytest.fail('semantic receipt metadata forbids Pi/model/runtime/grants/media execution')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('_creation_runtime', 'run_task_turn', 'run_plan_turn', 'start_assistant_continuation_driver'):
        monkeypatch.setattr(main, name, forbidden)
    monkeypatch.setattr(main.montage_renderer, 'render', forbidden)
    monkeypatch.setattr(main.narration_generator, 'generate_for_creation_task', forbidden)
    database = tmp_path / 'receipts.sqlite'
    settings = Settings(database_path=str(database), video_inbox_path=str(tmp_path / 'library'),
        video_output_path=str(tmp_path / 'output'), model_root_path=str(tmp_path / 'models'),
        temporary_root_path=str(tmp_path / 'temporary'), draft_output_path=str(tmp_path / 'drafts'),
        jianying_draft_root_path=str(tmp_path / 'jianying'), obsidian_vault_path=None,
        hermes_radar_path=None, local_whisper_python_path=None, local_whisper_cli_path=None,
        local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    repository = ProductionRepository(database, tmp_path / 'library')
    conversations = ConversationRepository(database)
    members = LocalMemberService(database)
    owner, token = members.whoami(None)
    _, other_token = members.join(members.create_invite_code(), 'Other member')
    monkeypatch.setattr(main, 'production_repo', repository)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, 'device_runtime', None)
    plan = CreationPlan(id='plan', workspace_id='local', goal='Read current text state', name='Receipt metadata',
        target_count=1, language='zh-CN', aspect_ratio='16:9', duration_target_seconds=2,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary='', model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint='metadata-only')
    task = CreationTask(id='task', workspace_id='local', plan_id=plan.id,
        name='Receipt metadata', scenario='knowledge_explanation')
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=owner.member_id)
    chat = conversations.create('local', owner.member_id, 'task', task.id)
    message = '只读取当前段落状态，不修改正文。'
    chat = conversations.save_draft(chat['id'], 'local', owner.member_id, message, 0)
    turn, created = conversations.create_turn_idempotent(chat['id'], 'local', owner.member_id,
        message, str(uuid4()), chat['draft_version'], {'kind': 'task', 'task_id': task.id})
    assert created and turn['status'] == 'running'
    client = TestClient(main.app)  # No lifespan; repository declares consumed text turn, no Agent run.
    headers = {'X-Local-Member-Token': token}  # Root input: deliberately no x-assistant-input-id header.
    root = '/api/creation-tasks/task/assistant/'
    understanding = {'assistant_turn_id': turn['id'], 'input_id': turn['id'],
        'kind': 'query', 'summary': '只读取当前段落状态', 'target_refs': ['task:task']}
    response = client.post(root + 'input-understanding', headers=headers, json=understanding)
    assert response.status_code == 200, response.text
    expected = {'input_id': None, 'kind': 'query', 'summary': understanding['summary'],
                'target_refs': understanding['target_refs'], 'business_effect': None}
    assert response.json() == expected
    # The understanding receipt is an expected write; reopen and check it before no-write snapshots.
    reopened = ConversationRepository(database)
    persisted = reopened.get_turn(turn['id'])
    event = {'type': 'assistant_input_understanding', 'input_id': None, 'kind': 'query',
        'summary': understanding['summary'], 'target_refs': understanding['target_refs']}
    assert event in persisted['events'] and persisted['status'] == 'running'
    assert persisted['assistant_text'] == ''

    def facts():
        with repository._connect() as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: sorted((tuple(row) for row in connection.execute(
                'SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr) for name in tables}

    def tree():
        return {str(item.relative_to(tmp_path)): item.read_bytes() if item.is_file() else None
                for item in tmp_path.rglob('*')}

    before_facts, before_tree = facts(), tree()
    for suffix in ('input-understanding', 'narration-semantic-review'):
        missing = client.post(root + suffix, headers=headers, json={})
        assert missing.status_code == 409 and missing.json()['detail'] == '修改判断必须绑定正在运行的可信助手回合'
        forged = client.post(root + suffix, headers=headers,
            json={'assistant_turn_id': turn['id'], 'input_id': 'forged'})
        assert forged.status_code == 409 and forged.json()['detail'] == '修改判断没有绑定当前原输入'
        foreign = client.post(root + suffix, headers={'X-Local-Member-Token': other_token},
            json={'assistant_turn_id': turn['id'], 'input_id': turn['id']})
        assert foreign.status_code == 409 and foreign.json()['detail'] == '类型化编辑不属于当前有效助手轮次'
    # Placeholder references are rejected input only; no model comparison is persisted.
    review = {'request_id': 'refused-review', 'input_id': turn['id'], 'section_id': 'rejected-section',
        'script_id': 'rejected-script', 'expected_segment_version': 'rejected-version',
        'dependency_digest': 'a' * 64, 'base_version_id': None, 'source_text_sha256': 'b' * 64,
        'replacement_text_sha256': 'c' * 64, 'protected_rules': [], 'conclusion': 'uncertain',
        'findings': [{'rule_id': 'fixture-rule', 'before': '原句', 'after': '改句',
            'assessment': 'uncertain', 'reason': '拒绝测试输入，不是模型结论'}],
        'authorized_locked_section_ids': ['rejected-section']}
    assert NarrationSemanticReviewRequest.model_validate(review).findings[0].assessment == 'uncertain'
    refused = client.post(root + 'narration-semantic-review', headers=headers,
        json={**review, 'assistant_turn_id': turn['id']})
    assert refused.status_code == 409 and refused.json()['detail'] == '模型复核不能授予定稿解锁权限'
    assert facts() == before_facts
    assert tree() == before_tree
    assert repository.job_records.list_job_runs('local', task.id) == []
    assert not any(item.event_type == 'narration_semantic_review'
                   for item in repository.execution_records.list_task_events('local', task.id))
    assert calls == []
    client.close()
