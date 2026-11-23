from pathlib import Path

import pytest

from app.artifact_store import (
    ArtifactStore,
    StorageRebindEntry,
    StorageRebindError,
    WorkspaceStorageRebind,
)


def _entry(store: ArtifactStore, logical_ref: str) -> StorageRebindEntry:
    published = store.describe(logical_ref)
    return StorageRebindEntry(
        logical_ref=logical_ref,
        sha256=published.sha256,
        size_bytes=published.size_bytes,
    )


def _stage_target(source: ArtifactStore, target: Path, logical_ref: str) -> None:
    destination = target.joinpath(*logical_ref.split("/"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.resolve_file(logical_ref).read_bytes())


def test_prepare_and_commit_rebinds_only_after_verified_target_and_persistence(tmp_path):
    source_root = tmp_path / "source"
    target_root = tmp_path / "external" / "artifacts"
    source = ArtifactStore(source_root)
    published = source.publish_bytes("artifacts/task/video.mp4", b"real-bytes")
    target_root.mkdir(parents=True)
    _stage_target(source, target_root, published.logical_ref)

    binding_calls: list[tuple[Path, tuple[int, int]]] = []
    rebind = WorkspaceStorageRebind(source)
    plan = rebind.prepare(target_root, [_entry(source, published.logical_ref)])
    receipt = rebind.commit(plan, lambda root, identity: binding_calls.append((root, identity)))

    assert receipt.previous_root == source_root.resolve()
    assert receipt.active_root == target_root.resolve()
    assert binding_calls == [(target_root.resolve(), plan.target_identity)]
    assert receipt.active_identity == plan.target_identity
    assert source.storage_root == target_root.resolve()
    assert source.resolve_file(published.logical_ref) == target_root / "artifacts/task/video.mp4"
    assert (source_root / "artifacts/task/video.mp4").read_bytes() == b"real-bytes"


def test_commit_keeps_old_root_when_persisting_binding_fails(tmp_path):
    source = ArtifactStore(tmp_path / "source")
    published = source.publish_bytes("artifact.bin", b"bytes")
    target_root = tmp_path / "target"
    target_root.mkdir()
    _stage_target(source, target_root, published.logical_ref)
    plan = WorkspaceStorageRebind(source).prepare(target_root, [_entry(source, published.logical_ref)])

    with pytest.raises(RuntimeError, match="persist failed"):
        WorkspaceStorageRebind(source).commit(
            plan, lambda _root, _identity: (_ for _ in ()).throw(RuntimeError("persist failed"))
        )

    assert source.storage_root == (tmp_path / "source").resolve()
    assert source.resolve_file(published.logical_ref).read_bytes() == b"bytes"


def test_prepare_rejects_changed_or_missing_target_content(tmp_path):
    source = ArtifactStore(tmp_path / "source")
    published = source.publish_bytes("artifact.bin", b"bytes")
    target_root = tmp_path / "target"
    target_root.mkdir()
    destination = target_root / "artifact.bin"
    destination.write_bytes(b"different")

    with pytest.raises(StorageRebindError, match="摘要或大小不匹配"):
        WorkspaceStorageRebind(source).prepare(target_root, [_entry(source, published.logical_ref)])


def test_prepare_rejects_symlink_or_unwritable_root(tmp_path):
    source = ArtifactStore(tmp_path / "source")
    published = source.publish_bytes("artifact.bin", b"bytes")
    target_root = tmp_path / "target"
    target_root.mkdir()
    _stage_target(source, target_root, published.logical_ref)
    symlink = tmp_path / "target-link"
    symlink.symlink_to(target_root, target_is_directory=True)

    with pytest.raises(StorageRebindError, match="绝对且非符号链接目录"):
        WorkspaceStorageRebind(source).prepare(symlink, [_entry(source, published.logical_ref)])


def test_empty_entries_are_only_allowed_for_an_empty_source_root(tmp_path):
    empty_source = ArtifactStore(tmp_path / "empty-source")
    empty_target = tmp_path / "empty-target"
    empty_target.mkdir()
    plan = WorkspaceStorageRebind(empty_source).prepare(empty_target, [])
    WorkspaceStorageRebind(empty_source).commit(plan, lambda _root, _identity: None)
    assert empty_source.storage_root == empty_target.resolve()

    populated_source = ArtifactStore(tmp_path / "populated-source")
    populated_source.publish_bytes("artifact.bin", b"bytes")
    populated_target = tmp_path / "populated-target"
    populated_target.mkdir()
    with pytest.raises(StorageRebindError, match="迁移清单不能为空"):
        WorkspaceStorageRebind(populated_source).prepare(populated_target, [])


def test_commit_rejects_target_replacement_after_binding_persist(tmp_path):
    source = ArtifactStore(tmp_path / "source")
    published = source.publish_bytes("artifact.bin", b"bytes")
    target_root = tmp_path / "target"
    target_root.mkdir()
    _stage_target(source, target_root, published.logical_ref)
    plan = WorkspaceStorageRebind(source).prepare(target_root, [_entry(source, published.logical_ref)])
    persisted: list[tuple[Path, tuple[int, int]]] = []

    def persist_and_replace(root: Path, identity: tuple[int, int]) -> None:
        persisted.append((root, identity))
        moved = root.with_name("target-old")
        root.rename(moved)
        root.mkdir()

    with pytest.raises(StorageRebindError, match="提交期间被替换"):
        WorkspaceStorageRebind(source).commit(plan, persist_and_replace)

    assert persisted == [(target_root.resolve(), plan.target_identity)]
    assert source.storage_root == (tmp_path / "source").resolve()


def test_startup_verification_rejects_a_replaced_persisted_root(tmp_path):
    source = ArtifactStore(tmp_path / "source")
    target_root = tmp_path / "target"
    target_root.mkdir()
    identity = (target_root.stat().st_dev, target_root.stat().st_ino)
    assert WorkspaceStorageRebind.verify_persisted_binding(target_root, identity) == target_root.resolve()

    replaced = target_root.with_name("target-old")
    target_root.rename(replaced)
    target_root.mkdir()
    with pytest.raises(StorageRebindError, match="身份已变化"):
        WorkspaceStorageRebind.verify_persisted_binding(target_root, identity)
