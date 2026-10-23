"""Bounded PCM assembly. The host owns admission, checkpoints and publication."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import time
from datetime import datetime, timezone
import wave


CHUNK = 64 * 1024
FORMAT = {"channels": 1, "sample_rate": 24000, "sample_width": 2}
EMPTY_SHA = hashlib.sha256(b"").hexdigest()


class AssemblyError(Exception):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AssemblyError(code)


def identity(info: os.stat_result) -> dict:
    return {"device": info.st_dev, "inode": info.st_ino}


def header(pcm_bytes: int) -> bytes:
    require(0 <= pcm_bytes <= 0xFFFFFFFF - 36 and pcm_bytes % 2 == 0, "pcm_size_invalid")
    return struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + pcm_bytes, b"WAVE",
                       b"fmt ", 16, 1, 1, 24000, 48000, 2, 16, b"data", pcm_bytes)


def hash_range(fd: int, offset: int, count: int) -> str:
    digest = hashlib.sha256()
    while count:
        part = os.pread(fd, min(CHUNK, count), offset)
        require(bool(part), "short_work_read")
        digest.update(part)
        offset += len(part)
        count -= len(part)
    return digest.hexdigest()


@contextlib.contextmanager
def parent(path: str):
    value = Path(path)
    require(value.is_absolute() and ".." not in value.parts, "path_invalid")
    # Walk directory descriptors so no parent symlink is followed.
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in value.parts[1:-1]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, value.name
    finally:
        os.close(fd)


def same_file(directory: int, name: str, fd: int, *, private: bool = False) -> os.stat_result:
    info = os.fstat(fd)
    linked = os.stat(name, dir_fd=directory, follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode) and identity(info) == identity(linked), "file_identity_changed")
    if private:
        require(info.st_nlink == 1, "work_has_alias")
    return info


def exists(directory: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def publish_json(directory: int, name: str, value: dict) -> None:
    # The monitor can identify this one permitted hard-link publication window.
    # An abandoned pending file is evidence for host reconciliation, not replaceable.
    temporary = f".{name}.pending"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        while data:
            count = os.write(fd, data)
            require(count > 0, "receipt_short_write")
            data = data[count:]
        os.fsync(fd)
        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.fsync(directory)
    finally:
        os.close(fd)
        os.unlink(temporary, dir_fd=directory)


def integer(manifest: dict, key: str) -> int:
    value = manifest.get(key)
    require(type(value) is int and value >= 0, f"{key}_invalid")
    return value


def snapshot(fd: int, manifest: dict) -> dict:
    info = os.fstat(fd)
    previous = integer(manifest, "committed_pcm_bytes")
    expected_header = header(previous)
    actual = os.pread(fd, 44, 0)
    prefix_available = info.st_size >= 44 + previous
    prefix_valid = prefix_available and hash_range(fd, 44, previous) == manifest["committed_pcm_sha256"]
    return {"file_identity": identity(info), "file_size": info.st_size,
            "header_valid": actual == expected_header, "prefix_valid": prefix_valid,
            "tail_bytes": max(0, info.st_size - 44 - previous),
            "length_matches": info.st_size == 44 + previous}


def execute(manifest: dict, observed: dict) -> dict:
    require(manifest.get("version") == 1, "manifest_version_invalid")
    operation = manifest.get("operation")
    require(operation in ("append", "inspect"), "operation_invalid")
    for key in ("unit_id", "job_id", "source_digest", "work_path", "receipt_path", "committed_pcm_sha256"):
        require(isinstance(manifest.get(key), str) and bool(manifest[key]), f"{key}_invalid")
    previous = integer(manifest, "committed_pcm_bytes")
    header(previous)
    require(len(manifest["committed_pcm_sha256"]) == 64, "prefix_digest_invalid")
    expected = manifest.get("expected_file_identity")
    work_path = Path(manifest["work_path"])
    receipt_path = Path(manifest["receipt_path"])
    require(receipt_path.parent == work_path.parent and receipt_path != work_path, "receipt_path_invalid")
    seal_name = work_path.name + ".sealed.json"
    require(receipt_path.name != seal_name, "receipt_path_invalid")
    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    with parent(str(work_path)) as (directory, name), contextlib.ExitStack() as stack:
        if operation == "inspect":
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            stack.callback(os.close, fd)
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            same_file(directory, name, fd, private=True)
            state = snapshot(fd, manifest)
            return {"version": 1, "operation": "inspect", "unit_id": manifest["unit_id"],
                    "job_id": manifest["job_id"], "work_path": str(work_path), **state,
                    "identity_matches": expected == state["file_identity"],
                    "finalized": exists(directory, seal_name)}
        require(type(manifest.get("final")) is bool, "final_invalid")
        require(not exists(directory, seal_name), "work_finalized")
        require(not exists(directory, receipt_path.name), "receipt_already_exists")
        maximum = integer(manifest, "max_new_bytes")
        absolute = integer(manifest, "absolute_max_output_bytes")
        sources = manifest.get("sources")
        require(isinstance(sources, list) and 1 <= len(sources) <= (2 if expected is None else 1), "source_count_invalid")
        opened = []
        appended = 0
        for source in sources:
            source_directory, source_name = stack.enter_context(parent(source["path"]))
            source_fd = os.open(source_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_directory)
            stack.callback(os.close, source_fd)
            info = same_file(source_directory, source_name, source_fd)
            require((info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                    (source["size_bytes"], source["mtime_ns"], source["ctime_ns"]), "source_changed")
            stream = stack.enter_context(os.fdopen(os.dup(source_fd), "rb"))
            reader = stack.enter_context(wave.open(stream, "rb"))
            require(reader.getnchannels() == 1 and reader.getsampwidth() == 2 and
                    reader.getframerate() == 24000 and reader.getcomptype() == "NONE", "source_format_invalid")
            require(type(source["pcm_frames"]) is int and source["pcm_frames"] > 0 and
                    reader.getnframes() == source["pcm_frames"], "source_frames_changed")
            appended += reader.getnframes() * 2
            opened.append((source_directory, source_name, source_fd, info, reader))
        new = expected is None
        header_bytes = 44 if new else 8
        require(appended + header_bytes <= maximum, "incremental_budget_exceeded")
        require(44 + previous + appended <= absolute, "absolute_output_exceeded")
        header(previous + appended)
        if new:
            require(previous == 0 and manifest["committed_pcm_sha256"] == EMPTY_SHA, "new_baseline_invalid")
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            observed["work_created"] = True
        else:
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory)
        stack.callback(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = same_file(directory, name, fd, private=True)
        require(new or expected == identity(info), "work_identity_changed")
        require(not exists(directory, seal_name), "work_finalized")
        require(not exists(directory, receipt_path.name), "receipt_already_exists")
        if not new:
            state = snapshot(fd, manifest)
            require(state["header_valid"] and state["prefix_valid"] and state["length_matches"], "work_baseline_mismatch")
        for _, _, _, source_info, _ in opened:
            require(identity(source_info) != identity(info), "source_aliases_work")
        position = 44 + previous
        appended_digest = hashlib.sha256()

        def write(data: bytes, offset: int) -> None:
            while data:
                count = os.pwrite(fd, data, offset)
                require(count > 0, "work_short_write")
                observed["generated_bytes"] += count
                offset += count
                data = data[count:]

        for source_directory, source_name, source_fd, source_info, reader in opened:
            remaining = reader.getnframes() * 2
            while remaining:
                data = reader.readframes(min(CHUNK, remaining) // 2)
                require(bool(data) and len(data) <= remaining and len(data) % 2 == 0, "source_short_read")
                write(data, position)
                appended_digest.update(data)
                remaining -= len(data)
                position += len(data)
            current = same_file(source_directory, source_name, source_fd)
            require((current.st_size, current.st_mtime_ns, current.st_ctime_ns) ==
                    (source_info.st_size, source_info.st_mtime_ns, source_info.st_ctime_ns), "source_changed")
        require(hash_range(fd, 44, previous) == manifest["committed_pcm_sha256"], "prefix_changed")
        require(hash_range(fd, 44 + previous, appended) == appended_digest.hexdigest(), "appended_data_changed")
        result_header = header(previous + appended)
        if new:
            write(result_header, 0)
        else:
            write(result_header[4:8], 4)
            write(result_header[40:44], 40)
        os.fsync(fd)
        current = same_file(directory, name, fd, private=True)
        require(current.st_size == position and os.pread(fd, 44, 0) == result_header, "output_changed")
        receipt = {"version": 1, "unit_id": manifest["unit_id"], "job_id": manifest["job_id"],
                   "source_digest": manifest["source_digest"], "work_path": str(work_path),
                   "file_identity": identity(current), "format": FORMAT,
                   "previous_pcm_bytes": previous, "pcm_bytes": previous + appended,
                   "pcm_sha256": hash_range(fd, 44, previous + appended),
                   "appended_pcm_bytes": appended, "header_bytes_written": header_bytes,
                   "generated_bytes": observed["generated_bytes"], "file_size": current.st_size,
                   "source_count": len(sources) + (0 if new else 1), "finalized": manifest["final"],
                   "started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                   "elapsed_seconds": time.monotonic() - clock}
        if manifest["final"]:
            publish_json(directory, seal_name, receipt)
        publish_json(directory, receipt_path.name, receipt)
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    observed = {"generated_bytes": 0, "work_created": False}
    try:
        with open(args.manifest, encoding="utf-8") as stream:
            manifest = json.load(stream)
        result = execute(manifest, observed)
    except (AssemblyError, OSError, ValueError, KeyError, TypeError, wave.Error, EOFError) as error:
        code = str(error) if isinstance(error, AssemblyError) else type(error).__name__
        print(json.dumps({"version": 1, "status": "failed", "reason_code": code,
                          "observed": observed, "settlement": "unknown"}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
