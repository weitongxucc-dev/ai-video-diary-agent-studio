import hashlib
import os
import errno
from pathlib import Path

import pytest

from app.artifact_store import ArtifactStore


class Cancelled(RuntimeError):
    pass


def test_declared_temporary_path_is_the_actual_link_source(tmp_path, monkeypatch):
    root = tmp_path / "store"
    store = ArtifactStore(root)
    source = tmp_path / "source"
    source.write_bytes(b"packet-copy-result")
    temporary = root / "sealed.tmp"
    observed = []
    original_link = os.link

    def link(src, dst, **kwargs):
        assert src == temporary
        assert src.read_bytes() == source.read_bytes()
        original_link(src, dst, **kwargs)
        assert src.stat().st_ino == dst.stat().st_ino
        observed.append(dst)

    monkeypatch.setattr(os, "link", link)
    result = store.publish_file("final/result.mp4", source, temporary_path=temporary,
                                check=lambda: None)
    assert observed == [root / "final/result.mp4"]
    assert result.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.size_bytes == source.stat().st_size
    assert not temporary.exists()
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) == ["final/result.mp4"]


def test_copy_cancellation_removes_only_own_temporary_and_never_publishes(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    source = tmp_path / "source"
    source.write_bytes(b"x" * (2 * 1024 * 1024))
    temporary = tmp_path / "store/sealed.tmp"

    def check():
        if temporary.exists() and temporary.stat().st_size >= 1024 * 1024:
            raise Cancelled()

    with pytest.raises(Cancelled):
        store.publish_file("result", source, temporary_path=temporary, check=check)
    assert not temporary.exists()
    assert not (tmp_path / "store/result").exists()


@pytest.mark.parametrize("phase", ["hash", "commit"])
def test_hash_and_commit_remain_cancellable(tmp_path, monkeypatch, phase):
    store = ArtifactStore(tmp_path / "store")
    temporary = tmp_path / "store/sealed.tmp"
    original_describe = store._describe_stream
    state = {"hashing": False, "finished_hash": False, "hash_checks": 0}

    def describe(*args):
        state["hashing"] = True
        result = original_describe(*args)
        state["hashing"] = False
        state["finished_hash"] = True
        return result

    def check():
        if state["hashing"]:
            state["hash_checks"] += 1
            if phase == "hash" and state["hash_checks"] == 2:
                raise Cancelled()
        if phase == "commit" and state["finished_hash"]:
            raise Cancelled()

    monkeypatch.setattr(store, "_describe_stream", describe)
    with pytest.raises(Cancelled):
        store.publish("result", lambda f: f.write(b"x" * (2 * 1024 * 1024)),
                      temporary_path=temporary, check=check)
    assert not temporary.exists()
    assert not (tmp_path / "store/result").exists()


@pytest.mark.parametrize("existing_kind", ["file", "symlink"])
def test_explicit_temporary_never_overwrites_existing_path(tmp_path, existing_kind):
    store = ArtifactStore(tmp_path / "store")
    original = tmp_path / "original"
    original.write_bytes(b"preserve")
    temporary = tmp_path / "store/sealed.tmp"
    if existing_kind == "symlink":
        temporary.symlink_to(original)
    else:
        temporary.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        store.publish("result", lambda f: f.write(b"new"), temporary_path=temporary)
    assert temporary.read_bytes() == original.read_bytes() == b"preserve"
    assert not (tmp_path / "store/result").exists()


def test_explicit_temporary_rejects_outside_and_symlinked_parent(tmp_path):
    root = tmp_path / "store"
    store = ArtifactStore(root)
    (root / "real").mkdir()
    (root / "alias").symlink_to(root / "real", target_is_directory=True)
    for temporary in (tmp_path / "outside.tmp", root / "alias/sealed.tmp"):
        with pytest.raises(ValueError):
            store.publish("result", lambda f: f.write(b"new"), temporary_path=temporary)
        assert not temporary.exists()
    with pytest.raises(ValueError):
        store.publish("alias/new/result", lambda f: f.write(b"new"),
                      temporary_path=root / "sealed.tmp")
    assert not (root / "real/new").exists()


def test_same_content_replays_and_different_content_preserves_immutable_target(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    original = store.publish_bytes("result", b"original")
    temporary = tmp_path / "store/sealed.tmp"
    assert store.publish("result", lambda f: f.write(b"original"), temporary_path=temporary) == original
    with pytest.raises(FileExistsError, match="不同内容"):
        store.publish("result", lambda f: f.write(b"changed"), temporary_path=temporary)
    assert store.resolve_file("result").read_bytes() == b"original"
    assert not temporary.exists()


def test_root_replacement_receives_neither_publication_nor_cleanup(tmp_path):
    root = tmp_path / "store"
    store = ArtifactStore(root)
    temporary = root / "sealed.tmp"
    displaced = tmp_path / "displaced"

    def write(target):
        target.write(b"original")
        root.rename(displaced)
        root.mkdir()
        temporary.write_bytes(b"replacement")

    with pytest.raises(OSError, match="断开或被替换"):
        store.publish("result", write, temporary_path=temporary)
    assert temporary.read_bytes() == b"replacement"
    assert (displaced / "sealed.tmp").read_bytes() == b"original"
    assert not (root / "result").exists()


def test_temporary_replacement_is_not_removed_as_own_cleanup(tmp_path):
    root = tmp_path / "store"
    store = ArtifactStore(root)
    temporary = root / "sealed.tmp"

    def write(target):
        target.write(b"original")
        temporary.rename(root / "original.tmp")
        temporary.write_bytes(b"replacement")

    with pytest.raises(OSError, match="临时文件已被替换"):
        store.publish("result", write, temporary_path=temporary)
    assert temporary.read_bytes() == b"replacement"
    assert (root / "original.tmp").read_bytes() == b"original"
    assert not (root / "result").exists()


def test_identity_read_failure_closes_created_descriptor_without_unproven_cleanup(tmp_path, monkeypatch):
    root = tmp_path / "store"
    store = ArtifactStore(root)
    temporary = root / "sealed.tmp"
    original_open, original_stat = os.open, Path.stat
    opened = []

    def open_and_replace(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        if Path(path) == temporary:
            opened.append(descriptor)
            temporary.rename(root / "original.tmp")
            temporary.write_bytes(b"someone else's file")
        return descriptor

    def fail_parent_identity(path, *args, **kwargs):
        if opened and path == temporary.parent:
            raise OSError(errno.EIO, "parent identity unavailable")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_and_replace)
    monkeypatch.setattr(Path, "stat", fail_parent_identity)
    with pytest.raises(OSError, match="parent identity unavailable"):
        store.publish("result", lambda f: pytest.fail("writer must not run"), temporary_path=temporary)
    assert len(opened) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(opened[0])
    assert closed.value.errno == errno.EBADF
    assert temporary.read_bytes() == b"someone else's file"
    assert (root / "original.tmp").read_bytes() == b""
    assert not (root / "result").exists()
