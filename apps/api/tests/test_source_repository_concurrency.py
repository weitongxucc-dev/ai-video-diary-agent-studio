"""Concurrent public saves must preserve one knowledge identity and current restrictions."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from app.source_repository import SourceRepository
from app.source_types import KnowledgeItem, SourceConnector, SourceItem


class DecisionGateRepository(SourceRepository):
    """Pause real saves after a decision, without replacing SQL or returned rows."""

    def __init__(self, path, on_connect, on_decision):
        self.scope = threading.local()
        self.on_connect = on_connect
        self.on_decision = on_decision
        super().__init__(path)

    def _connect(self):
        connection = super()._connect()
        self.scope.connection = connection
        role = getattr(self.scope, "role", None)
        if role:
            self.on_connect(role)
        return connection

    def _validate_knowledge_item(self, item: KnowledgeItem):
        super()._validate_knowledge_item(item)
        role = getattr(self.scope, "role", None)
        if role and not self.scope.decided:
            self.scope.decided = True
            self.on_decision(role, self.scope.connection.in_transaction)

    def call(self, role, action):
        self.scope.role = role
        self.scope.decided = False
        try:
            return action(self)
        finally:
            self.scope.role = None


def _source(item_id, content_hash, body):
    return SourceItem(
        id=item_id, workspace_id="workspace-a", connector_id="fixture-files",
        source_label="file_import", source_url=None, content_type="text/markdown",
        author=None, published_at=None, body=body, subtitles=None, media_references=(),
        license="user_provided", title="资料", file_reference="资料.md", content_hash=content_hash,
    )


def _seed(path, items):
    repository = SourceRepository(path)
    repository.upsert_connector(SourceConnector(
        id="fixture-files", workspace_id="workspace-a", platform="knowledge_files",
        protocol="local_file", account_or_rule="fixture-only", frequency="on_demand",
        permissions=("read:source_items",),
    ))
    repository.save_source_items("workspace-a", items)
    return repository


def _save(repository, source_id):
    return repository.save_source_item_to_knowledge(
        "workspace-a", source_id, verified_owner_user_id="uploader-a",
    )


def _concurrent_saves(path, first_source_id, second_source_id):
    first_decided = threading.Event()
    second_connected = threading.Event()
    second_decided = threading.Event()
    first_finished = threading.Event()

    def on_connect(role):
        if role == "second":
            second_connected.set()

    def on_decision(role, transaction_active):
        if role == "first":
            first_decided.set()
            # A locked decision serializes the second save. Without that lock,
            # exercise the valid schedule where both saves have selected a parent.
            ready = second_connected if transaction_active else second_decided
            assert ready.wait(5), "second save did not reach the controlled boundary"
        else:
            second_decided.set()
            if not transaction_active:
                assert first_finished.wait(5), "first save did not commit"

    repository = DecisionGateRepository(path, on_connect, on_decision)

    def first_save():
        try:
            return repository.call("first", lambda repo: _save(repo, first_source_id))
        finally:
            first_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_save)
        assert first_decided.wait(5), "first save did not select its decision"
        second = pool.submit(repository.call, "second", lambda repo: _save(repo, second_source_id))
        return first.result(timeout=8), second.result(timeout=8)


@pytest.mark.parametrize("content_hash", [None, "hash-v1"])
def test_concurrent_first_saves_return_one_knowledge_item(tmp_path, content_hash):
    path = tmp_path / "sources.sqlite3"
    _seed(path, [_source("input", content_hash, "原文")])

    first, second = _concurrent_saves(path, "input", "input")

    assert first.id == second.id
    reopened = SourceRepository(path)
    assert [row.id for row in reopened.list_knowledge_items("workspace-a")] == [first.id]
    assert reopened.get_knowledge_item("workspace-a", first.id).version == "1"
    versions = reopened.list_knowledge_item_versions("workspace-a", first.id)
    assert [row.version for row in versions] == (["1"] if content_hash else [])


@pytest.mark.parametrize("same_update", [False, True])
def test_concurrent_updates_preserve_complete_fixed_versions(tmp_path, same_update):
    path = tmp_path / "sources.sqlite3"
    repository = _seed(path, [
        _source("initial", "hash-v1", "第一版"),
        _source("update-a", "hash-v2", "第二版甲"),
        _source("update-b", "hash-v3", "第二版乙"),
    ])
    original = _save(repository, "initial")
    repository.set_knowledge_item_visibility("workspace-a", original.id, "shared")

    first, second = _concurrent_saves(path, "update-a", "update-a" if same_update else "update-b")

    assert first.id == second.id == original.id
    reopened = SourceRepository(path)
    head = reopened.get_knowledge_item("workspace-a", original.id)
    assert head.version == ("2" if same_update else "3")
    expected = [("1", "hash-v1", "initial", "第一版"), ("2", "hash-v2", "update-a", "第二版甲")]
    if not same_update:
        expected.append(("3", "hash-v3", "update-b", "第二版乙"))
    versions = reopened.list_knowledge_item_versions("workspace-a", original.id)
    assert [(row.version, row.content_hash, row.source_item_id, row.body) for row in versions] == expected
    for version, content_hash, source_item_id, body in expected:
        fixed = reopened.get_knowledge_reference_readable_by(
            "workspace-a", original.id, version, user_id="uploader-a",
        )
        assert (fixed.content_hash, fixed.source_item_id, fixed.body) == (content_hash, source_item_id, body)
    assert head.owner_user_id == "uploader-a"
    assert reopened.get_knowledge_item_visibility("workspace-a", head.id) == "shared"


def test_source_update_cannot_overwrite_concurrent_excluded_restriction(tmp_path):
    path = tmp_path / "sources.sqlite3"
    repository = _seed(path, [
        _source("initial", "hash-v1", "第一版"), _source("update", "hash-v2", "第二版"),
    ])
    original = _save(repository, "initial")
    importer_decided = threading.Event()
    restriction_connected = threading.Event()
    restriction_committed = threading.Event()

    def on_connect(role):
        if role == "restriction":
            restriction_connected.set()

    def on_decision(role, transaction_active):
        importer_decided.set()
        ready = restriction_connected if transaction_active else restriction_committed
        assert ready.wait(5), "restriction write did not reach the controlled boundary"

    observed = DecisionGateRepository(path, on_connect, on_decision)

    def restrict():
        try:
            return observed.call("restriction", lambda repo: repo.set_knowledge_item_use_state(
                "workspace-a", original.id, "excluded",
            ))
        finally:
            restriction_committed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        update = pool.submit(observed.call, "importer", lambda repo: _save(repo, "update"))
        assert importer_decided.wait(5), "importer did not select the parent"
        restriction = pool.submit(restrict)
        update.result(timeout=8)
        assert restriction.result(timeout=8).use_state == "excluded"

    reopened = SourceRepository(path)
    head = reopened.get_knowledge_item("workspace-a", original.id)
    assert (head.version, head.use_state) == ("2", "excluded")
    assert [row.version for row in reopened.list_knowledge_item_versions("workspace-a", head.id)] == ["1", "2"]
    assert reopened.list_knowledge_items_readable_by("workspace-a", user_id="uploader-a") == []
