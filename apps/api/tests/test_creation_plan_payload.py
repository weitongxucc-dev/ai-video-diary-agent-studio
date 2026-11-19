"""Pre-source persisted inputs remain valid while explicit source changes bind."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json

import pytest

from app.creation.contracts import CreationTask
from app.conversation_repository import ConversationRepository
from app.creation.execution_input import execution_input_chunks, execution_input_digest
from app.creation.plan_confirmation_use_case import _creation_plan_conversion_intent
from app.creation.plan_contracts import CreationPlan, creation_plan_payload
from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
from app.creation.script_generation_intent import ScriptGenerationIntent
from app.creation.topic_contracts import TopicCandidate, TopicGenerationRun
from app.creation_execution import revision_digest
from app.production import ProductionRepository
from app.task_audio import media_digest


STAMP = datetime(2026, 1, 2, tzinfo=timezone.utc)
# The pre-source wire shape is an independent fixture, not the current
# dataclass's fields with whichever new fields removed by the test.
LEGACY_PLAN = {
    'id': 'plan', 'workspace_id': 'local', 'goal': 'existing goal', 'name': 'existing plan',
    'target_count': 1, 'language': 'zh-CN', 'aspect_ratio': '9:16', 'duration_target_seconds': 30,
    'knowledge_references': [], 'source_references': [], 'selected_voice': None,
    'pronunciation_references': [], 'approved_asset_count': 0, 'approved_clip_count': 0,
    'material_rule_summary': '', 'model_snapshot': [], 'confirmation_points': [], 'blockers': [],
    'preflight_fingerprint': 'fixed', 'selected_person': None, 'presenter_share_target': 0,
    'lipsync_quality': 'fast', 'template_id': None, 'template_version': None, 'template_settings': {},
    'material_matches': [], 'status': 'confirmed', 'creation_task_id': 'task', 'visibility': 'private',
    'owner_user_id': 'member', 'created_at': STAMP, 'confirmed_at': STAMP, 'updated_at': STAMP,
    'creation_intent': None, 'adoption': None, 'duration_mode': 'exact', 'duration_range_seconds': None,
}
SOURCE = {'asset_id': 'source-asset', 'asset_revision': 'fixed-revision',
    'media_ref': 'workspace://source.mp4', 'source_start_seconds': 0,
    'source_end_seconds': 30, 'require_original_audio': True}


@pytest.mark.parametrize('source', [None, SOURCE])
def test_old_execution_and_media_digests_keep_wire_bytes_and_bind_explicit_source(source):
    plan = CreationPlan(**LEGACY_PLAN, source_media=source)
    script = CreationTaskScript('script', 'local', 'task', 'run', 'topic', 'existing script', 'zh-CN',
        [CreationTaskScriptSection('section', '正文', '原文案', '', [])], created_at=STAMP, updated_at=STAMP)
    plan_wire = {**LEGACY_PLAN, **({'source_media': source} if source is not None else {})}
    expected = json.dumps({'script': asdict(script), 'plan': plan_wire},
        ensure_ascii=False, sort_keys=True, default=str).encode()
    expected_digest = hashlib.sha256(expected).hexdigest()
    fields = asdict(script)
    del fields['sections']
    assert b''.join(execution_input_chunks(fields, plan, script.sections)) == expected
    assert execution_input_digest(fields, plan, script.sections) == expected_digest
    assert revision_digest(script, plan) == expected_digest
    assert media_digest(creation_plan_payload(plan)) == media_digest(plan_wire)
    conversion = {key: value for key, value in plan_wire.items()
        if key not in {'id', 'creation_task_id', 'owner_user_id', 'created_at', 'confirmed_at', 'updated_at'}}
    assert _creation_plan_conversion_intent(plan) == conversion
    if source is not None:
        assert revision_digest(script, plan) != revision_digest(script, replace(plan, source_media=None))
        assert revision_digest(script, plan) != revision_digest(script, replace(plan,
            source_media={**source, 'source_start_seconds': 1}))


def test_plan_storage_and_task_goal_revision_preserve_missing_or_null_source(tmp_path):
    repo = ProductionRepository(tmp_path / 'plans.sqlite3', tmp_path / 'library')
    plan = CreationPlan(**LEGACY_PLAN)
    task = CreationTask('task', 'local', plan.id, 'existing task', 'knowledge_explanation', owner_user_id='member')
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id='member')
    with repo._connect() as connection:
        payload = json.loads(connection.execute('SELECT payload FROM creation_plans WHERE id=?', (plan.id,)).fetchone()[0])
    assert 'source_media' not in payload
    assert repo.plan_records.get_creation_plan('local', plan.id).source_media is None
    old_identity = {key: payload[key] for key in ('id', 'goal', 'target_count', 'language', 'aspect_ratio', 'duration_target_seconds')}
    old_identity['request'] = None
    expected = hashlib.sha256(json.dumps(old_identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    records = repo.task_authorization_records
    assert records.task_authorization_goal_revision('local', task.id) == expected
    with repo._connect() as connection:
        connection.execute("UPDATE creation_plans SET payload=json_set(payload,'$.source_media',NULL) WHERE id=?", (plan.id,))
    assert records.task_authorization_goal_revision('local', task.id) == expected
    selected = replace(repo.plan_records.get_creation_plan('local', plan.id), source_media=SOURCE)
    with repo._connect() as connection:
        repo.plan_records.write_knowledge_adopted_plan(connection, 'local', selected)
    assert repo.plan_records.get_creation_plan('local', plan.id).source_media == SOURCE
    source_revision = records.task_authorization_goal_revision('local', task.id)
    assert source_revision != expected
    with repo._connect() as connection:
        repo.plan_records.write_knowledge_adopted_plan(connection, 'local',
            replace(selected, source_media={**SOURCE, 'asset_revision': 'revised'}))
    assert records.task_authorization_goal_revision('local', task.id) != source_revision


def test_persisted_script_generation_input_omits_null_and_binds_real_source(tmp_path):
    repo = ProductionRepository(tmp_path / 'generation.sqlite3', tmp_path / 'library')
    plan = CreationPlan(**LEGACY_PLAN)
    task = CreationTask('task', 'local', plan.id, 'existing task', 'knowledge_explanation',
        owner_user_id='member', selected_topic_id='topic')
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id='member')
    topic = TopicCandidate('topic', 'local', task.id, 'topic-run', 'title', 'angle', 'audience', [], 1,
        selection_status='confirmed', created_at=STAMP)
    run = TopicGenerationRun(topic.run_id, 'local', task.id, plan.id, 'succeeded', {}, 'provided-topic', [],
        started_at=STAMP, finished_at=STAMP)
    with repo._connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        repo.topic_records.save_provided_topic(connection, run, topic)
    case = ScriptGenerationIntent(repo, ConversationRepository(repo.database_path),
        source_inputs=lambda *args, **kwargs: ([], [], None),
        route_snapshot={'execution_location': 'cloud'}, verify_identity=lambda actor: 'ready')
    with repo._connect() as connection:
        connection.execute('BEGIN')
        before = case._capture_input(connection, task, 'member', {'execution_location': 'cloud'})
    assert 'source_media' not in before['requirements']
    with repo._connect() as connection:
        connection.execute("UPDATE creation_plans SET payload=json_set(payload,'$.source_media',NULL) WHERE id=?", (plan.id,))
    with repo._connect() as connection:
        connection.execute('BEGIN')
        assert case._capture_input(connection, task, 'member', {'execution_location': 'cloud'}) == before
    with repo._connect() as connection:
        repo.plan_records.write_knowledge_adopted_plan(connection, 'local', replace(plan, source_media=SOURCE))
    with repo._connect() as connection:
        connection.execute('BEGIN')
        selected = case._capture_input(connection, task, 'member', {'execution_location': 'cloud'})
    assert selected['requirements']['source_media'] == SOURCE
    assert selected['goal_revision'] != before['goal_revision']


def test_provided_topic_after_failed_generation_preserves_both_original_records(tmp_path):
    repo = ProductionRepository(tmp_path / "topics.sqlite3", tmp_path / "library")
    plan = CreationPlan(**LEGACY_PLAN)
    task = CreationTask("task", "local", plan.id, "Existing work", "knowledge_explanation", owner_user_id="member")
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id="member")
    topics = repo.topic_records
    running, _ = topics.start_topic_generation(
        TopicGenerationRun("failed-topic-run", "local", task.id, plan.id, "running", {}, "fixture", []))
    with pytest.raises(ValueError, match="正在生成"):
        topics.confirm_provided_topic("local", task.id, title="原片已有主题",
            expected_task_updated_at=running.updated_at, verified_owner_user_id="member")

    failed_task, failure = topics.fail_topic_generation("failed-topic-run", reason="本机文字模型不可用",
        impact="未生成选题", repair_action="修复连接或填写已有主题", retryable=True)
    kwargs = {"title": "原片已有主题", "expected_task_updated_at": failed_task.updated_at}
    with pytest.raises(PermissionError):
        topics.confirm_provided_topic("local", task.id, **kwargs, verified_owner_user_id="other-member")
    with pytest.raises(ValueError, match="状态已变化"):
        topics.confirm_provided_topic("local", task.id, title=kwargs["title"],
            expected_task_updated_at=running.updated_at, verified_owner_user_id="member")
    adopted = topics.confirm_provided_topic("local", task.id, **kwargs, verified_owner_user_id="member")
    assert (adopted.id, adopted.stage, adopted.stage_status) == (task.id, "script", "not_started")
    assert adopted.stage_reason is None and adopted.confirmed_script_id is None
    assert topics.confirm_provided_topic("local", task.id, **kwargs, verified_owner_user_id="member") == adopted
    provided = topics.get_topic_candidate("local", task.id, adopted.selected_topic_id)
    assert provided.title == kwargs["title"] and provided.selection_status == "confirmed"
    assert topics.get_topic_generation_run("local", provided.run_id).route_snapshot == {
        "operation": "provided_topic", "provider": "none"}
    assert topics.get_topic_generation_run("local", failure.id) == failure
    assert repo.job_records.list_job_runs("local", task.id) == []
