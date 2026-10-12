import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.creator_profile import (
    CreatorProfile,
    CreatorProfileConflict,
    change_creator_preference,
    get_creator_profile,
    get_or_create_profile,
    upsert_creator_profile,
)


def change(database_path, *, owner="alice", revision=0, key="tone", value="自然", turn="turn-1", quote="记住，我喜欢自然的语气"):
    return change_creator_preference(
        database_path, workspace_id="workspace", owner_user_id=owner,
        key=key, value=value, expected_revision=revision,
        source_turn_id=turn, source_quote=quote,
    )


def test_remember_survives_reopen_and_forget_removes_only_selected_preference(tmp_path):
    database = tmp_path / "memory.sqlite"
    initial = get_or_create_profile(database, "workspace", owner_user_id="alice")
    assert initial.revision == 0
    saved = change(database)
    reopened = get_creator_profile(database, "workspace", owner_user_id="alice")
    assert reopened.id == initial.id == saved.id
    assert reopened.revision == 1
    assert reopened.style_preferences == {"tone": "自然"}
    assert reopened.last_preference_change == {
        "turn_id": "turn-1", "key": "tone", "value": "自然", "quote": "记住，我喜欢自然的语气",
    }
    change(database, revision=1, key="pace", value="舒缓", turn="turn-2", quote="也记住节奏舒缓")
    forgotten = change(database, revision=2, value=None, turn="turn-3", quote="忘记语气偏好")
    assert forgotten.revision == 3
    assert get_creator_profile(database, "workspace", owner_user_id="alice").style_preferences == {"pace": "舒缓"}
    assert forgotten.last_preference_change["value"] is None


def test_same_workspace_members_and_other_workspace_never_inherit_profiles(tmp_path):
    database = tmp_path / "memory.sqlite"
    change(database)
    assert get_creator_profile(database, "workspace", owner_user_id="bob") is None
    bob = get_or_create_profile(database, "workspace", owner_user_id="bob")
    assert bob.style_preferences == {}
    change(database, owner="bob", value="专业", quote="记住我的语气专业")
    assert get_creator_profile(database, "workspace", owner_user_id="alice").style_preferences == {"tone": "自然"}
    assert get_creator_profile(database, "workspace", owner_user_id="bob").style_preferences == {"tone": "专业"}
    assert get_creator_profile(database, "elsewhere", owner_user_id="alice") is None
    assert get_creator_profile(database, "workspace") is None


def test_cas_rejects_stale_manual_update_and_stale_action_after_other_update(tmp_path):
    database = tmp_path / "memory.sqlite"
    stale = get_or_create_profile(database, "workspace", owner_user_id="alice")
    change(database)
    stale.common_expressions = ["过期输入"]
    with pytest.raises(CreatorProfileConflict):
        upsert_creator_profile(stale, database, "workspace", owner_user_id="alice", expected_revision=0)
    current = get_creator_profile(database, "workspace", owner_user_id="alice")
    current.banned_words = ["夸张"]
    upsert_creator_profile(current, database, "workspace", owner_user_id="alice", expected_revision=1)
    with pytest.raises(CreatorProfileConflict):
        change(database)
    with pytest.raises(CreatorProfileConflict):
        change(database, revision=1)
    stored = get_creator_profile(database, "workspace", owner_user_id="alice")
    assert stored.revision == 2
    assert stored.common_expressions == []
    assert stored.banned_words == ["夸张"]
    assert stored.style_preferences == {"tone": "自然"}


def test_immediate_same_action_replay_has_no_new_revision_or_timestamp(tmp_path):
    database = tmp_path / "memory.sqlite"
    saved = change(database)
    replayed = change(database)
    assert replayed == saved
    assert change(database, revision=saved.revision) == saved
    with pytest.raises(CreatorProfileConflict):
        change(database, value="专业")
    with pytest.raises(CreatorProfileConflict):
        change(database, quote="不同的来源原句")
    assert get_creator_profile(database, "workspace", owner_user_id="alice") == saved
    forgotten = change(database, revision=1, value=None, turn="turn-2", quote="忘记语气偏好")
    assert change(database, revision=1, value=None, turn="turn-2", quote="忘记语气偏好") == forgotten


def test_sources_follow_each_current_key_and_manual_edits_keep_only_unchanged_sources(tmp_path):
    database = tmp_path / "memory.sqlite"
    change(database)
    change(database, revision=1, key="pace", value="舒缓", turn="turn-2", quote="记住节奏舒缓")
    reopened = get_creator_profile(database, "workspace", owner_user_id="alice")
    assert reopened.preference_sources == {
        "tone": {"source_turn_id": "turn-1", "source_quote": "记住，我喜欢自然的语气"},
        "pace": {"source_turn_id": "turn-2", "source_quote": "记住节奏舒缓"},
    }
    forgotten = change(database, revision=2, value=None, turn="turn-3", quote="忘记语气偏好")
    assert set(forgotten.preference_sources) == {"pace"}
    change(database, revision=3, value="直接", turn="turn-4", quote="记住语气直接")
    manual = get_creator_profile(database, "workspace", owner_user_id="alice")
    manual.style_preferences["tone"] = "专业"
    manual.preference_sources["pace"] = {"source_turn_id": "forged", "source_quote": "伪造来源"}
    upsert_creator_profile(manual, database, "workspace", owner_user_id="alice", expected_revision=4)
    stored = get_creator_profile(database, "workspace", owner_user_id="alice")
    assert stored.preference_sources == {
        "pace": {"source_turn_id": "turn-2", "source_quote": "记住节奏舒缓"},
    }
    assert stored.last_preference_change == {}
    del stored.style_preferences["pace"]
    upsert_creator_profile(stored, database, "workspace", owner_user_id="alice", expected_revision=5)
    assert get_creator_profile(database, "workspace", owner_user_id="alice").preference_sources == {}


def legacy_database(database, *, workspace_column=True):
    with sqlite3.connect(database) as connection:
        scope = ", workspace_id TEXT NOT NULL DEFAULT 'local'" if workspace_column else ""
        connection.execute(f"CREATE TABLE creator_profile (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL{scope})")
        for index in range(2):
            payload = json.dumps({"id": f"legacy-{index}", "common_expressions": [f"历史-{index}"], "style_preferences": {"tone": "历史"}})
            if workspace_column:
                connection.execute("INSERT INTO creator_profile VALUES (?,?,?,?)", (f"legacy-{index}", payload, f"2026-01-0{index + 1}", "workspace"))
            else:
                connection.execute("INSERT INTO creator_profile VALUES (?,?,?)", (f"legacy-{index}", payload, f"2026-01-0{index + 1}"))


@pytest.mark.parametrize("workspace_column", [True, False])
def test_migration_preserves_all_legacy_rows_without_exposing_them_to_members(tmp_path, workspace_column):
    database = tmp_path / "legacy.sqlite"
    legacy_database(database, workspace_column=workspace_column)
    workspace = "workspace" if workspace_column else "local"
    assert get_creator_profile(database, workspace, owner_user_id="alice") is None
    member = get_or_create_profile(database, workspace, owner_user_id="alice")
    assert member.style_preferences == {}
    historical = get_creator_profile(database, workspace)
    assert historical.id == "legacy-1"
    assert historical.owner_user_id is None
    assert historical.revision == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id FROM creator_profile WHERE owner_user_id IS NULL ORDER BY id").fetchall() == [("legacy-0",), ("legacy-1",)]
        assert connection.execute("SELECT COUNT(*) FROM creator_profile").fetchone()[0] == 3


def test_original_workspace_api_reads_and_updates_only_its_unowned_profile(tmp_path):
    database = tmp_path / "legacy.sqlite"
    original = get_or_create_profile(database, workspace_id="workspace")
    original.common_expressions = ["旧工作区接口"]
    upsert_creator_profile(original, database, workspace_id="workspace")
    change(database)
    assert get_creator_profile(database, workspace_id="workspace").common_expressions == ["旧工作区接口"]
    assert get_creator_profile(database, "workspace", owner_user_id="alice").common_expressions == []
    assert get_creator_profile(database, "other") is None
    assert get_or_create_profile(database, "workspace").id == original.id


def test_concurrent_first_creation_and_schema_migration_produce_one_member_row(tmp_path):
    database = tmp_path / "legacy.sqlite"
    legacy_database(database)
    barrier = Barrier(8)

    def create(_):
        barrier.wait()
        return get_or_create_profile(database, "workspace", owner_user_id="alice")

    with ThreadPoolExecutor(max_workers=8) as pool:
        profiles = list(pool.map(create, range(8)))
    assert len({profile.id for profile in profiles}) == 1
    assert all(profile.revision == 0 and profile.style_preferences == {} for profile in profiles)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM creator_profile WHERE owner_user_id='alice'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM creator_profile WHERE owner_user_id IS NULL").fetchone()[0] == 2


def test_existing_member_and_legacy_ids_cannot_be_replaced_by_another_owner(tmp_path):
    database = tmp_path / "memory.sqlite"
    legacy = get_or_create_profile(database, "workspace")
    alice = get_or_create_profile(database, "workspace", owner_user_id="alice")
    for profile in (legacy, alice):
        with pytest.raises(CreatorProfileConflict):
            upsert_creator_profile(profile, database, "workspace", owner_user_id="bob", expected_revision=0)
    with pytest.raises(CreatorProfileConflict):
        upsert_creator_profile(CreatorProfile(id="second-alice"), database, "workspace", owner_user_id="alice", expected_revision=0)
    assert get_creator_profile(database, "workspace", owner_user_id="bob") is None
    assert get_creator_profile(database, "workspace").id == legacy.id
    assert get_creator_profile(database, "workspace", owner_user_id="alice").id == alice.id
