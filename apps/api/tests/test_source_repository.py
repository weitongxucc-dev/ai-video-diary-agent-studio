from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.source_repository import SourceRepository
from app.sources import (
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


@pytest.mark.parametrize(
    "field",
    ["platform", "protocol", "account_or_rule", "frequency"],
)
def test_connector_rejects_whitespace_required_fields(tmp_path, field):
    repo = SourceRepository(tmp_path / "agent.sqlite3")

    with pytest.raises(ValueError, match="connector requires"):
        repo.upsert_connector(replace(_connector("workspace-a"), **{field: "  "}))


@pytest.mark.parametrize("field", ["ownership", "authorization", "version"])
def test_knowledge_write_rejects_whitespace_required_fields(tmp_path, field):
    repo = SourceRepository(tmp_path / "agent.sqlite3")
    repo.upsert_connector(_connector("workspace-a"))
    source = repo.save_source_items("workspace-a", [_item("workspace-a", "connector_1")])[0]

    with pytest.raises(ValueError, match="knowledge item requires"):
        repo.save_source_item_to_knowledge(
            "workspace-a",
            source.id,
            **{field: "  "},
            **{
                name: value
                for name, value in {
                    "ownership": "user-owned",
                    "authorization": "approved",
                    "version": "1",
                }.items()
                if name != field
            },
        )
