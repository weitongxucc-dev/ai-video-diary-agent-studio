"""Persistent bounded planning and real SQLite rollback, without models/services."""
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.production import (
    CreationPlan,
    MaterialAsset,
    MaterialSegment,
    MaterialSegmentMatch,
    ProductionRepository,
)
from app.creation.request_contracts import CreationRequestConflict
from app.creation.contracts import CreationTask
import app.database_migrations as migrations

WORKSPACE = "request-test"
OWNER = "verified-member"


@pytest.fixture
def repository(tmp_path):
    return ProductionRepository(tmp_path / "requests.sqlite3", tmp_path / "library")


def create(repository, **changes):
    values = dict(request_key="trusted-turn-key", input_fingerprint="original-input", goal="制作不同主题视频",
        target_count=10000, output_relation="distinct_topics", requirements={"language": "zh-CN"},
        material_scope={"mode": "workspace"}, clarification_questions=[], verified_owner_user_id=OWNER)
    values.update(changes)
    return repository.creation_requests.create_creation_request(WORKSPACE, **values)


def entries(start=1, count=8):
    result = []
    for ordinal in range(start, start + count):
        plan = CreationPlan(id=f"plan-{ordinal}", workspace_id=WORKSPACE, goal=f"第{ordinal}条独立目标",
            name=f"独立视频{ordinal}", target_count=1, language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
            knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
            approved_asset_count=0, approved_clip_count=0, material_rule_summary="隔离仓库规划测试",
            model_snapshot=[], confirmation_points=[], blockers=[], preflight_fingerprint=f"preflight-{ordinal}",
            owner_user_id="untrusted-payload-owner")
        task = CreationTask(id=f"task-{ordinal}", workspace_id=WORKSPACE, plan_id=plan.id, name=plan.name,
            scenario="knowledge_explanation", owner_user_id="untrusted-payload-owner")
        result.append((plan, task))
    return result


def save(repository, request, values=None, fingerprint="window-1", **changes):
    return repository.creation_requests.save_creation_request_window(WORKSPACE, request["id"], expected_revision=request["revision"],
        input_fingerprint=fingerprint, entries=entries() if values is None else values, verified_owner_user_id=OWNER, **changes)


def confirm(repository, request):
    window = request["current_window"]
    return repository.creation_request_adoption.confirm_creation_request_window(WORKSPACE, request["id"], window["id"],
        expected_revision=request["revision"], input_fingerprint=window["input_fingerprint"], verified_owner_user_id=OWNER)


def header(repository, request):
    return repository.creation_requests.get_creation_request(WORKSPACE, request["id"], viewer_user_id=OWNER)


def test_large_goal_is_scalar_then_two_bounded_windows_replay_and_rebuild(repository, monkeypatch):
    monkeypatch.setattr(repository, "list_creation_tasks", lambda *_a, **_kw: pytest.fail("header must aggregate with SQL"))
    request = create(repository)
    assert request["target_count"] == 10000 and request["expanded_count"] == request["confirmed_count"] == 0
    assert request["current_window"] is None and len(json.dumps(request)) < 2000
    with repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creation_request_items").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM creation_tasks").fetchone()[0] == 0
    assert create(repository)["id"] == request["id"]
    planned = save(repository, request)
    assert planned["status"] == "awaiting_confirmation"
    assert planned["expanded_count"] == 8 and planned["confirmed_count"] == 0
    assert all(item["task"] is None for item in repository.creation_requests.list_creation_request_items(WORKSPACE,request["id"],viewer_user_id=OWNER)["items"])
    assert [item["ordinal"] for item in planned["current_window"]["entries"]] == list(range(1, 9))
    assert save(repository, request)["current_window"]["id"] == planned["current_window"]["id"]
    rebuilt = ProductionRepository(repository.database_path, repository.video_library_path)
    assert header(rebuilt, request)["current_window"] == planned["current_window"]
    assert header(rebuilt, request)["confirmed_count"] == 0
    active = confirm(rebuilt, planned)
    assert active["confirmed_count"] == active["expanded_count"] == 8
    assert confirm(rebuilt, planned)["revision"] == active["revision"]
    assert active["task_status_counts"] == {"created": 8}
    assert all(item["task"]["owner_user_id"] == OWNER for item in active["current_window"]["entries"])
    second = save(rebuilt, active, entries(9), fingerprint="window-2")
    assert second["expanded_count"] == 16 and second["confirmed_count"] == 8
    assert second["current_window"]["ordinal_start"] == 9
    final = confirm(rebuilt, second)
    assert final["confirmed_count"] == 16 and final["remaining_count"] == 9984
    page = rebuilt.creation_requests.list_creation_request_items(WORKSPACE, request["id"], limit=5, viewer_user_id=OWNER)
    assert [item["ordinal"] for item in page["items"]] == list(range(1, 6))
    assert page["has_more"] and page["next_after_ordinal"] == 5
    next_page = rebuilt.creation_requests.list_creation_request_items(WORKSPACE, request["id"], after_ordinal=5, limit=100, viewer_user_id=OWNER)
    assert [item["ordinal"] for item in next_page["items"]] == list(range(6, 17))
    assert not next_page["has_more"] and next_page["next_after_ordinal"] is None
    # A live task fact changes after confirmation; the header must read the
    # actual task table instead of its original frozen planning payload.
    with rebuilt._connect() as conn:
        task = json.loads(conn.execute("SELECT payload FROM creation_tasks WHERE id='task-2'").fetchone()[0])
        task["status"] = "failed"
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id='task-2'", (json.dumps(task),))
    assert header(rebuilt, request)["task_status_counts"] == {"created": 15, "failed": 1}


def test_window_cannot_attach_existing_task_identity(repository):
    existing_plan,existing_task = entries(count=1)[0]
    repository.confirm_creation_plan(existing_plan,existing_task,verified_owner_user_id="another-member")
    request = create(repository)
    with pytest.raises(CreationRequestConflict,match="现有对象"):
        save(repository,request,[(existing_plan,existing_task)])
    assert header(repository,request)["current_window"] is None


@pytest.mark.parametrize("count", [0, -1, True, 1.5, 9007199254740992])
def test_total_goal_rejects_non_safe_positive_integer(repository, count):
    with pytest.raises(ValueError, match="正安全整数"):
        create(repository, target_count=count)


def test_maximum_safe_count_still_creates_no_items(repository):
    request = create(repository, target_count=9007199254740991)
    assert request["remaining_count"] == 9007199254740991
    assert repository.creation_requests.list_creation_request_items(WORKSPACE, request["id"], viewer_user_id=OWNER)["items"] == []


def test_one_output_does_not_require_batch_relation_clarification(repository):
    request = create(repository,target_count=1,output_relation=None)
    assert request["status"] == "awaiting_plan"
    assert confirm(repository,save(repository,request,entries(count=1)))["confirmed_count"] == 1


def test_incremental_request_migration_preserves_old_multi_count_plan_without_expansion(tmp_path, monkeypatch):
    database = tmp_path / "old-version.sqlite3"
    with monkeypatch.context() as older_schema:
        older_schema.setattr(migrations, "PRODUCTION_SCHEMA_VERSION", 16)
        older_schema.setattr(migrations, "_MIGRATIONS", tuple(item for item in migrations._MIGRATIONS if item[0] <= 16))
        old = ProductionRepository(database, tmp_path / "library")
        plan,task = entries(count=1)[0]
        plan.target_count = 5
        old.confirm_creation_plan(plan,task,verified_owner_user_id=OWNER)
        with old._connect() as conn:
            before = tuple(conn.execute("SELECT payload FROM creation_plans").fetchone())
    upgraded = ProductionRepository(database,tmp_path / "library")
    assert upgraded.get_creation_plan(WORKSPACE,plan.id).target_count == 5
    with upgraded._connect() as conn:
        assert tuple(conn.execute("SELECT payload FROM creation_plans").fetchone()) == before
        assert conn.execute("SELECT COUNT(*) FROM creation_tasks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM creation_requests").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM production_schema_migrations WHERE version=17").fetchone()[0] == 1
    reopened = ProductionRepository(database,tmp_path / "library")
    assert reopened.get_creation_task(WORKSPACE,task.id).owner_user_id == OWNER


def test_owner_scope_request_identity_and_revision_conflicts(repository):
    request = create(repository)
    for reader in (None, "another-member"):
        with pytest.raises(KeyError):
            repository.creation_requests.get_creation_request(WORKSPACE, request["id"], viewer_user_id=reader)
    with pytest.raises(PermissionError):
        create(repository, verified_owner_user_id=None)
    with pytest.raises(KeyError):
        repository.creation_requests.save_creation_request_window(WORKSPACE, request["id"], expected_revision=1,
            input_fingerprint="window", entries=entries(), verified_owner_user_id="another-member")
    with pytest.raises(CreationRequestConflict):
        create(repository, goal="同key但改变输入")
    with pytest.raises(CreationRequestConflict):
        create(repository, input_fingerprint="different")
    planned = save(repository, request)
    with pytest.raises(CreationRequestConflict):
        save(repository, planned, fingerprint="new-unconfirmed-window")
    with pytest.raises(CreationRequestConflict):
        repository.creation_request_adoption.confirm_creation_request_window(WORKSPACE, request["id"], planned["current_window"]["id"],
            expected_revision=1, input_fingerprint="window-1", verified_owner_user_id=OWNER)
    with pytest.raises(KeyError):
        repository.creation_request_adoption.confirm_creation_request_window(WORKSPACE, request["id"], planned["current_window"]["id"],
            expected_revision=2, input_fingerprint="window-1", verified_owner_user_id="another-member")
    assert header(repository, request)["confirmed_count"] == 0


def test_clarification_and_revision_retire_only_unconfirmed_candidate_window(repository):
    request = create(repository, output_relation=None, clarification_questions=["不同主题还是同主题版本？"])
    with pytest.raises(CreationRequestConflict, match="澄清"):
        save(repository, request)
    revised = repository.creation_requests.revise_creation_request(WORKSPACE, request["id"], expected_revision=1,
        output_relation="series", requirements={"duration_seconds": 60}, material_scope={}, clarification_questions=[], verified_owner_user_id=OWNER)
    assert revised["status"] == "awaiting_plan"
    pending = save(repository, revised)
    changed = repository.creation_requests.revise_creation_request(WORKSPACE, request["id"], expected_revision=pending["revision"],
        output_relation="series", requirements={"duration_seconds": 30}, material_scope={}, clarification_questions=[], verified_owner_user_id=OWNER)
    assert changed["expanded_count"] == 0 and changed["current_window"] is None
    with repository._connect() as conn:
        assert conn.execute("SELECT status FROM creation_request_windows WHERE id=?", (pending["current_window"]["id"],)).fetchone()[0] == "superseded"
        assert conn.execute("SELECT COUNT(*) FROM creation_request_items").fetchone()[0] == 8
    new_pending = save(repository, changed, fingerprint="revised-window")
    assert new_pending["current_window"]["ordinal_start"] == 1
    active = confirm(repository, new_pending)
    with pytest.raises(CreationRequestConflict, match="已有确认窗口"):
        repository.creation_requests.revise_creation_request(WORKSPACE, request["id"], expected_revision=active["revision"],
            output_relation="series", requirements={}, material_scope={}, clarification_questions=[], verified_owner_user_id=OWNER)


def test_window_bounds_independent_single_video_and_frozen_payload(repository):
    request = create(repository, target_count=2)
    with pytest.raises(ValueError):
        save(repository, request, entries(count=9))
    with pytest.raises(CreationRequestConflict):
        save(repository, request, entries(count=3))
    values = entries(count=2)
    with pytest.raises(ValueError, match="单片"):
        save(repository, request, [(replace(values[0][0], target_count=2), values[0][1])])
    pending = save(repository, request, values)
    values[0][0].goal = "外部对象稍后被改写"
    assert header(repository, request)["current_window"]["entries"][0]["plan"]["goal"] == "第1条独立目标"
    assert confirm(repository, pending)["confirmed_count"] == 2


def test_failure_after_first_real_plan_insert_rolls_back_entire_window(repository, monkeypatch):
    request = save(repository, create(repository))
    insert = repository.creation_request_adoption.insert_plan
    inserted = []
    def fail_second(conn, plan, task):
        if inserted:
            raise sqlite3.IntegrityError("controlled second insertion failure")
        insert(conn, plan, task)
        assert conn.execute("SELECT COUNT(*) FROM creation_tasks").fetchone()[0] == 1
        inserted.append(task.id)
    monkeypatch.setattr(repository.creation_request_adoption, "insert_plan", fail_second)
    with pytest.raises(sqlite3.IntegrityError):
        confirm(repository, request)
    with repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creation_tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM creation_plans").fetchone()[0] == 0
    current = header(repository, request)
    assert current["revision"] == request["revision"] and current["status"] == "awaiting_confirmation"
    assert current["confirmed_count"] == 0


@pytest.mark.parametrize("change", ["file", "permission", "automation_use"])
def test_confirm_revalidates_frozen_material_references_atomically(repository, tmp_path, change):
    path = tmp_path / "metadata-fixture.mp4"
    path.write_bytes(b"repository-metadata-fixture")
    asset = MaterialAsset(id="asset", workspace_id=WORKSPACE, file_name=path.name, path=str(path), size_bytes=path.stat().st_size,
        modified_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc), duration_seconds=30, analysis_status="ready",
        owner_user_id=OWNER, segments=[MaterialSegment(id="segment", asset_id="asset", start_seconds=0,end_seconds=30,usage_role="b_roll")])
    repository._save_asset(asset)
    plan,task = entries(count=1)[0]
    plan.material_matches = [MaterialSegmentMatch(asset_ref="asset:asset",segment_ref="segment:segment",score=1,reasons=["真实元数据资格"])]
    request = save(repository,create(repository),[(plan,task)])
    if change == "file":
        path.write_bytes(b"changed")
    elif change == "permission":
        asset.owner_user_id = "another-member"
        asset.visibility = "private"
        repository._save_asset(asset)
    else:
        asset.use_state = "not_for_automation"
        repository._save_asset(asset)
    with pytest.raises(PermissionError if change == "permission" else ValueError):
        confirm(repository,request)
    assert header(repository,request)["confirmed_count"] == 0
    with repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creation_tasks").fetchone()[0] == 0


def replacement_entries(request):
    values = entries(start=request["current_window"]["ordinal_start"],count=len(request["current_window"]["entries"]))
    return [(replace(plan,id="replanned-"+plan.id),replace(task,id="replanned-"+task.id,plan_id="replanned-"+plan.id)) for plan,task in values]


@pytest.mark.parametrize("confirmed_before", [0,8])
def test_replan_pending_window_preserves_prior_confirmed_tasks_and_common_requirements(repository,confirmed_before):
    request = create(repository)
    if confirmed_before:
        request = confirm(repository,save(repository,request))
    pending = save(repository,request,entries(confirmed_before+1),fingerprint="pending-window")
    replaced = repository.creation_requests.save_creation_request_window(WORKSPACE,pending["id"],
        expected_revision=pending["revision"],input_fingerprint="fresh-preflight-window",
        entries=replacement_entries(pending),replace_window_id=pending["current_window"]["id"],verified_owner_user_id=OWNER)
    assert replaced["current_window"]["id"] != pending["current_window"]["id"]
    assert replaced["current_window"]["ordinal_start"] == confirmed_before+1
    assert replaced["expanded_count"] == confirmed_before+8 and replaced["confirmed_count"] == confirmed_before
    assert replaced["requirements"] == pending["requirements"] and replaced["material_scope"] == pending["material_scope"]
    with pytest.raises(CreationRequestConflict,match="窗口已变化"):
        repository.creation_requests.save_creation_request_window(WORKSPACE,pending["id"],expected_revision=replaced["revision"],
            input_fingerprint="another-preflight",entries=replacement_entries(pending),
            replace_window_id=pending["current_window"]["id"],verified_owner_user_id=OWNER)
    with repository._connect() as conn:
        assert conn.execute("SELECT status FROM creation_request_windows WHERE id=?",(pending["current_window"]["id"],)).fetchone()[0] == "superseded"
        assert conn.execute("SELECT COUNT(*) FROM creation_tasks").fetchone()[0] == confirmed_before
    assert confirm(repository,replaced)["confirmed_count"] == confirmed_before+8
    page = repository.creation_requests.list_creation_request_items(WORKSPACE,request["id"],viewer_user_id=OWNER)
    assert len(page["items"]) == confirmed_before+8
    assert [item["ordinal"] for item in page["items"]] == list(range(1,confirmed_before+9))


def test_replan_same_fingerprint_is_readback_but_stale_revision_and_window_are_rejected(repository):
    pending = save(repository,create(repository))
    arguments = dict(expected_revision=pending["revision"],input_fingerprint="window-1",
        entries=replacement_entries(pending),replace_window_id=pending["current_window"]["id"],verified_owner_user_id=OWNER)
    replay = repository.creation_requests.save_creation_request_window(WORKSPACE,pending["id"],**arguments)
    assert replay["current_window"] == pending["current_window"] and replay["revision"] == pending["revision"]
    for changes in ({"expected_revision":pending["revision"]-1},{"replace_window_id":"old-window"},{"verified_owner_user_id":"another-member"}):
        with pytest.raises((CreationRequestConflict,KeyError)):
            repository.creation_requests.save_creation_request_window(WORKSPACE,pending["id"],**{**arguments,**changes})
    active = confirm(repository,pending)
    with pytest.raises(CreationRequestConflict,match="已确认"):
        repository.creation_requests.save_creation_request_window(WORKSPACE,pending["id"],**{**arguments,"expected_revision":active["revision"]})


@pytest.mark.parametrize("confirmed_before",[0,8])
def test_replan_insert_failure_rolls_back_supersession_and_keeps_original_pending_window(repository,confirmed_before):
    request = create(repository)
    if confirmed_before:
        request = confirm(repository,save(repository,request))
    pending = save(repository,request,entries(confirmed_before+1,count=2),fingerprint="pending-window")
    with repository._connect() as conn:
        conn.executescript(f"""CREATE TRIGGER fail_replanned_second_item BEFORE INSERT ON creation_request_items
            WHEN NEW.ordinal={confirmed_before+2} BEGIN SELECT RAISE(ABORT,'controlled replacement insert failure'); END;""")
    with pytest.raises(sqlite3.IntegrityError):
        repository.creation_requests.save_creation_request_window(WORKSPACE,pending["id"],expected_revision=pending["revision"],
            input_fingerprint="new-preflight",entries=replacement_entries(pending),
            replace_window_id=pending["current_window"]["id"],verified_owner_user_id=OWNER)
    restored = header(repository,pending)
    assert restored["current_window"] == pending["current_window"] and restored["revision"] == pending["revision"]
    assert restored["expanded_count"] == confirmed_before+2 and restored["confirmed_count"] == confirmed_before
    with repository._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creation_request_items").fetchone()[0] == confirmed_before+2
        assert conn.execute("SELECT COUNT(*) FROM creation_request_windows WHERE status='superseded'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM creation_tasks").fetchone()[0] == confirmed_before
