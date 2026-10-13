from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import sqlite3

import pytest

from app.source_repository import SourceRepository
from app.sources import (
    content_allows_use,
    SourceBoundaryError,
    SourceConnector,
    SourceItem,
    SourceSyncFailedError,
    sync_source_connector,
)


def _connector(workspace_id: str, connector_id: str = "connector_1") -> SourceConnector:
    return SourceConnector(
        id=connector_id,
        workspace_id=workspace_id,
        platform="Product Hunt",
        protocol="graphql",
        account_or_rule="saved-products",
        frequency="hourly",
        permissions=("read:posts",),
    )


def _item(workspace_id: str, connector_id: str, item_id: str = "source_1") -> SourceItem:
    return SourceItem(
        id=item_id,
        workspace_id=workspace_id,
        connector_id=connector_id,
        source_label="Product Hunt",
        source_url="https://example.com/post",
        content_type="video",
        author="Example author",
        published_at=datetime(2026, 8, 27, tzinfo=UTC),
        body="A durable source body.",
        subtitles="A durable subtitle track.",
        media_references=("media://clip-1",),
        license="CC-BY-4.0",
        title="A durable source",
        file_reference="workspace://imports/example.mp4",
    )


def test_source_and_knowledge_are_workspace_isolated_and_survive_rebuild(tmp_path):
    database_path = tmp_path / "agent.sqlite3"
    repo = SourceRepository(database_path)
    repo.upsert_connector(_connector("workspace-a"))
    repo.upsert_connector(_connector("workspace-b", "connector_b"))
    source = repo.save_source_items("workspace-a", [_item("workspace-a", "connector_1")])[0]
    knowledge = repo.save_source_item_to_knowledge(
        "workspace-a",
        source.id,
        ownership="user-owned",
        authorization="creator owns this saved research copy",
    )

    assert repo.list_source_items("workspace-b") == []
    assert repo.list_knowledge_items("workspace-b") == []
    with pytest.raises(KeyError):
        repo.get_source_item("workspace-b", source.id)
    with pytest.raises(KeyError):
        repo.get_knowledge_item("workspace-b", knowledge.id)

    rebuilt = SourceRepository(database_path)
    persisted_source = rebuilt.get_source_item("workspace-a", source.id)
    persisted_knowledge = rebuilt.get_knowledge_item("workspace-a", knowledge.id)
    assert persisted_source.body == "A durable source body."
    assert persisted_source.subtitles == "A durable subtitle track."
    assert persisted_source.media_references == ("media://clip-1",)
    assert persisted_source.license == "CC-BY-4.0"
    assert persisted_knowledge.id != persisted_source.id
    assert persisted_knowledge.source_item_id == persisted_source.id
    assert persisted_knowledge.authorization == "creator owns this saved research copy"
    assert persisted_knowledge.ownership == "user-owned"
    with pytest.raises(ValueError):
        rebuilt.upsert_connector(_connector("workspace-b"))


def test_sync_resumes_from_saved_cursor_and_persists_each_run(tmp_path):
    repo = SourceRepository(tmp_path / "agent.sqlite3")
    repo.upsert_connector(_connector("workspace-a"))
    seen_cursors: list[str | None] = []

    def fetch(cursor: str | None):
        seen_cursors.append(cursor)
        return [_item("workspace-a", "connector_1", f"source_{len(seen_cursors)}")], f"cursor-{len(seen_cursors)}"

    first = sync_source_connector(repo, "workspace-a", "connector_1", fetch)
    second = sync_source_connector(repo, "workspace-a", "connector_1", fetch)

    assert seen_cursors == [None, "cursor-1"]
    assert first.cursor_after == "cursor-1"
    assert second.cursor_before == "cursor-1"
    connector = repo.get_connector("workspace-a", "connector_1")
    assert connector.cursor == "cursor-2"
    assert connector.connection_status == "connected"
    assert connector.latest_run_id == second.id
    assert len(repo.list_source_items("workspace-a")) == 2


def test_sync_failure_is_persisted_with_actionable_error_and_is_not_success(tmp_path):
    repo = SourceRepository(tmp_path / "agent.sqlite3")
    repo.upsert_connector(_connector("workspace-a"))

    def fetch(_cursor: str | None):
        raise SourceBoundaryError(
            "rate_limited",
            "Product Hunt rate limit was reached",
            impact="No new product signals are available for this run.",
            repair_action="Wait for the rate window, then retry.",
            retryable=True,
        )

    with pytest.raises(SourceSyncFailedError) as failure:
        sync_source_connector(repo, "workspace-a", "connector_1", fetch)

    run = repo.get_run("workspace-a", failure.value.run.id)
    assert run.status == "failed"
    assert run.reason == "Product Hunt rate limit was reached"
    assert run.impact == "No new product signals are available for this run."
    assert run.repair_action == "Wait for the rate window, then retry."
    assert run.retryable is True
    assert repo.get_connector("workspace-a", "connector_1").connection_status == "rate_limited"


def test_sync_write_is_atomic_when_one_item_is_invalid(tmp_path):
    repo = SourceRepository(tmp_path / "agent.sqlite3")
    repo.upsert_connector(_connector("workspace-a"))

    def fetch(_cursor: str | None):
        return [
            _item("workspace-a", "connector_1", "valid_item"),
            _item("workspace-a", "missing_connector", "invalid_item"),
        ], "cursor-1"

    with pytest.raises(SourceSyncFailedError) as failure:
        sync_source_connector(repo, "workspace-a", "connector_1", fetch)

    assert repo.list_source_items("workspace-a") == []
    run = repo.get_run("workspace-a", failure.value.run.id)
    assert run.status == "failed"
    assert repo.get_connector("workspace-a", "connector_1").cursor is None


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_sync_commit_rolls_back_items_and_run_when_connector_update_fails(tmp_path, status):
    database_path = tmp_path / "sources.sqlite3"
    repo = SourceRepository(database_path)
    repo.upsert_connector(_connector("workspace-a"))
    with sqlite3.connect(database_path) as conn:
        conn.execute("""
            CREATE TRIGGER reject_connector_update BEFORE UPDATE ON source_connectors
            BEGIN SELECT RAISE(ABORT, 'connector update failed'); END
        """)

    with pytest.raises(sqlite3.IntegrityError, match="connector update failed"):
        if status == "succeeded":
            repo.record_sync_success(
                "workspace-a", "connector_1", [_item("workspace-a", "connector_1")], "cursor-1"
            )
        else:
            repo.record_sync_failure(
                "workspace-a", "connector_1", reason="fetch failed", impact="No new items",
                repair_action="Retry", retryable=True,
            )

    reopened = SourceRepository(database_path)
    assert reopened.list_source_items("workspace-a") == []
    assert reopened.list_runs("workspace-a", "connector_1") == []
    connector = reopened.get_connector("workspace-a", "connector_1")
    assert connector.cursor is None
    assert connector.latest_run_id is None
    assert connector.connection_status == "configured"


@pytest.mark.parametrize("has_previous_version", [False, True])
def test_knowledge_head_and_version_roll_back_together_on_snapshot_write_failure(
    tmp_path, has_previous_version,
):
    database_path = tmp_path / "sources.sqlite3"
    repo = SourceRepository(database_path)
    repo.upsert_connector(_connector("workspace-a"))
    source = replace(
        _item("workspace-a", "connector_1"), content_hash="hash-v1",
        file_reference="资料.md", body="第一版原文",
    )
    repo.save_source_items("workspace-a", [source])
    previous = None
    if has_previous_version:
        previous = repo.save_source_item_to_knowledge(
            "workspace-a", source.id, verified_owner_user_id="uploader-a",
        )
        source = replace(source, id="source-v2", content_hash="hash-v2", body="第二版原文")
        repo.save_source_items("workspace-a", [source])
    with sqlite3.connect(database_path) as conn:
        conn.execute("""
            CREATE TRIGGER reject_snapshot_write BEFORE INSERT ON knowledge_item_versions
            BEGIN SELECT RAISE(ABORT, 'snapshot write failed'); END
        """)

    with pytest.raises(sqlite3.IntegrityError, match="snapshot write failed"):
        repo.save_source_item_to_knowledge(
            "workspace-a", source.id, verified_owner_user_id="uploader-a",
        )

    reopened = SourceRepository(database_path)
    items = reopened.list_knowledge_items_readable_by("workspace-a", user_id="uploader-a")
    assert items == ([previous] if has_previous_version else [])
    if previous:
        versions = reopened.list_knowledge_item_versions("workspace-a", previous.id)
        assert [(version.version, version.body) for version in versions] == [("1", "第一版原文")]
    # The independent imported source remains available; no partial knowledge write survives.
    assert reopened.get_source_item("workspace-a", source.id).body == source.body


@pytest.mark.parametrize(
    "field",
    ["platform", "protocol", "account_or_rule", "frequency"],
)
def test_connector_rejects_whitespace_required_fields(tmp_path, field):
    repo = SourceRepository(tmp_path / "agent.sqlite3")

    with pytest.raises(ValueError, match="connector requires"):
        repo.upsert_connector(replace(_connector("workspace-a"), **{field: "  "}))


def test_use_state_is_persisted_and_controls_knowledge_consumption(tmp_path):
    database_path = tmp_path / "agent.sqlite3"
    repo = SourceRepository(database_path)
    repo.upsert_connector(_connector("workspace-a"))
    source = repo.save_source_items("workspace-a", [_item("workspace-a", "connector_1")])[0]
    repo.set_source_item_use_state("workspace-a", source.id, "excluded")
    assert repo.list_source_items_for_use(
        "workspace-a", purpose="reference"
    ) == []
    with pytest.raises(KeyError):
        repo.get_source_item_for_use("workspace-a", source.id, purpose="reference")
    source = repo.set_source_item_use_state("workspace-a", source.id, "reference_only")
    assert repo.get_source_item_for_use(
        "workspace-a", source.id, purpose="understanding"
    ).id == source.id
    item = repo.save_source_item_to_knowledge(
        "workspace-a", source.id, verified_owner_user_id="user-alice"
    )

    assert item.use_state == "reference_only"
    assert item.ownership == ""
    assert item.authorization == ""
    assert repo.get_knowledge_item_for_use(
        "workspace-a", item.id, purpose="understanding", user_id="user-alice"
    ).id == item.id
    assert [row.id for row in repo.list_knowledge_items_for_use(
        "workspace-a", purpose="understanding", user_id="user-alice"
    )] == [item.id]
    assert repo.list_knowledge_items_for_use(
        "workspace-a", purpose="media", user_id="user-alice"
    ) == []
    for purpose in ("media", "publish"):
        with pytest.raises(KeyError):
            repo.get_knowledge_item_for_use(
                "workspace-a", item.id, purpose=purpose, user_id="user-alice"
            )

    rebuilt = SourceRepository(database_path)
    assert rebuilt.get_source_item("workspace-a", source.id).use_state == "reference_only"
    assert rebuilt.get_knowledge_item("workspace-a", item.id).use_state == "reference_only"
    rebuilt.set_knowledge_item_use_state("workspace-a", item.id, "excluded")
    assert rebuilt.list_knowledge_items_readable_by(
        "workspace-a", user_id="user-alice"
    ) == []
    with pytest.raises(KeyError):
        rebuilt.get_knowledge_item_readable_by(
            "workspace-a", item.id, user_id="user-alice"
        )
    rebuilt.set_knowledge_item_use_state("workspace-a", item.id, "available")
    assert rebuilt.get_knowledge_item_readable_by(
        "workspace-a", item.id, user_id="user-alice"
    ).use_state == "available"


def test_parsed_source_restriction_is_copied_to_new_knowledge_item(tmp_path):
    repo = SourceRepository(tmp_path / "agent.sqlite3")
    repo.upsert_connector(_connector("workspace-a"))
    source = repo.save_source_items(
        "workspace-a",
        [replace(_item("workspace-a", "connector_1"), content_hash="hash-v1")],
    )[0]
    source = repo.set_source_item_use_state("workspace-a", source.id, "not_for_publish")

    item = repo.save_source_item_to_knowledge(
        "workspace-a", source.id, verified_owner_user_id="user-alice"
    )

    assert item.use_state == "not_for_publish"
    assert item.owner_user_id == "user-alice"


def test_old_source_and_knowledge_rows_upgrade_as_available(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    repo = SourceRepository(database_path)
    repo.upsert_connector(_connector("workspace-a"))
    source = repo.save_source_items("workspace-a", [_item("workspace-a", "connector_1")])[0]
    item = repo.save_source_item_to_knowledge(
        "workspace-a", source.id, ownership="historic", authorization="historic"
    )
    with repo._connect() as conn:
        conn.execute("ALTER TABLE source_items DROP COLUMN use_state")
        conn.execute("ALTER TABLE knowledge_items DROP COLUMN use_state")

    upgraded = SourceRepository(database_path)
    assert upgraded.get_source_item("workspace-a", source.id).use_state == "available"
    assert upgraded.get_knowledge_item("workspace-a", item.id).use_state == "available"


def test_source_reimport_does_not_reset_an_excluded_knowledge_item(tmp_path):
    repo = SourceRepository(tmp_path / "agent.sqlite3")
    repo.upsert_connector(_connector("workspace-a"))
    first_source = replace(
        _item("workspace-a", "connector_1", "source-v1"),
        content_hash="hash-v1",
    )
    repo.save_source_items("workspace-a", [first_source])
    item = repo.save_source_item_to_knowledge("workspace-a", first_source.id)
    repo.set_knowledge_item_use_state("workspace-a", item.id, "excluded")

    same_content_source = replace(
        first_source, id="source-copy", use_state="available"
    )
    repo.save_source_items("workspace-a", [same_content_source])
    duplicate = repo.save_source_item_to_knowledge("workspace-a", same_content_source.id)
    assert duplicate.id == item.id
    assert duplicate.use_state == "excluded"

    changed_source = replace(
        first_source,
        id="source-v2",
        body="Updated body",
        content_hash="hash-v2",
        use_state="available",
    )
    repo.save_source_items("workspace-a", [changed_source])
    updated = repo.save_source_item_to_knowledge("workspace-a", changed_source.id)
    assert updated.id == item.id and updated.version == "2"
    assert updated.use_state == "excluded"
    assert repo.list_knowledge_items_readable_by("workspace-a", user_id=None, local_diagnostic=True) == []


@pytest.mark.parametrize(
    ("state", "purpose", "allowed"),
    [
        ("available", "automatic_selection", True),
        ("available", "publish", True),
        ("reference_only", "understanding", True),
        ("reference_only", "media", False),
        ("reference_only", "automatic_selection", False),
        ("reference_only", "publish", False),
        ("not_for_automation", "reference", True),
        ("not_for_automation", "automatic_selection", False),
        ("not_for_publish", "media", True),
        ("not_for_publish", "publish", False),
        ("excluded", "reference", False),
    ],
)
def test_content_use_states_qualify_the_intended_consumer(state, purpose, allowed):
    assert content_allows_use(state, purpose) is allowed
