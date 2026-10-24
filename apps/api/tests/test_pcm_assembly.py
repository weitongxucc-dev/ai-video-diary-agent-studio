"""Small real files verify append accounting and immutable recovery boundaries."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import wave

import pytest


WORKER = Path(__file__).parents[1] / "model_workers" / "pcm_assembly.py"
spec = importlib.util.spec_from_file_location("pcm_assembly", WORKER)
pcm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pcm)


def source(path, samples):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(samples)
    info = path.stat()
    return {"path": str(path), "size_bytes": info.st_size, "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns, "pcm_frames": len(samples) // 2}


def manifest(tmp_path, sources):
    return {"version": 1, "operation": "append", "unit_id": "request:1", "job_id": "job_original",
            "source_digest": "original-inputs", "work_path": str(tmp_path / "working.wav"),
            "receipt_path": str(tmp_path / "unit-1.json"), "expected_file_identity": None,
            "committed_pcm_bytes": 0, "committed_pcm_sha256": pcm.EMPTY_SHA,
            "sources": sources, "max_input_count": 2, "max_new_bytes": 1000, "absolute_max_output_bytes": 1000, "final": False}


def run(value):
    return pcm.execute(value, {"generated_bytes": 0, "work_created": False})


def continuation(value, receipt, new_source):
    result = copy.deepcopy(value)
    result.update(unit_id="request:2", receipt_path=str(Path(value["work_path"]).parent / "unit-2.json"),
                  expected_file_identity=receipt["file_identity"], committed_pcm_bytes=receipt["pcm_bytes"],
                  committed_pcm_sha256=receipt["pcm_sha256"], sources=[new_source])
    return result


def test_cli_stream_append_seal_and_read_only_inspection(tmp_path):
    a, b, c = b"\x01\x00" * 30, b"\x02\x00" * 40, b"\x03\x00" * 50
    value = manifest(tmp_path, [source(tmp_path / "a.wav", a), source(tmp_path / "b.wav", b)])
    value["max_new_bytes"] = len(a + b) + 44
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(value))
    completed = subprocess.run([sys.executable, str(WORKER), "--manifest", str(manifest_path)],
                               capture_output=True, text=True, timeout=5, check=True)
    first = json.loads(completed.stdout)
    assert first["generated_bytes"] == len(a + b) + 44
    assert first["source_count"] == 2
    value = continuation(value, first, source(tmp_path / "c.wav", c))
    value.update(final=True, max_new_bytes=len(c) + 8, absolute_max_output_bytes=44 + len(a + b + c))
    second = run(value)
    assert second["generated_bytes"] == len(c) + 8
    assert second["source_count"] == 2
    work = Path(value["work_path"])
    assert work.read_bytes() == pcm.header(len(a + b + c)) + a + b + c
    assert second["pcm_sha256"] == hashlib.sha256(a + b + c).hexdigest()
    assert json.loads(Path(value["receipt_path"]).read_text()) == second
    assert json.loads(Path(str(work) + ".sealed.json").read_text()) == second
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    inspect = {**value, "operation": "inspect", "committed_pcm_bytes": second["pcm_bytes"],
               "committed_pcm_sha256": second["pcm_sha256"]}
    state = run(inspect)
    assert all(state[key] for key in ("prefix_valid", "header_valid", "length_matches", "identity_matches", "finalized"))
    assert before == {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    with pytest.raises(pcm.AssemblyError, match="work_finalized"):
        run(value)


@pytest.mark.parametrize("damage", ["prefix", "tail", "source", "alias", "incremental", "absolute"])
def test_recovery_rejects_changed_inputs_without_repair_or_receipt(tmp_path, damage):
    value = manifest(tmp_path, [source(tmp_path / "a.wav", b"\x01\x00" * 10)])
    first = run(value)
    value = continuation(value, first, source(tmp_path / "b.wav", b"\x02\x00" * 10))
    work = Path(value["work_path"])
    if damage == "prefix":
        with work.open("r+b") as stream:
            stream.seek(44)
            stream.write(b"\x09\x00")
    elif damage == "tail":
        with work.open("ab") as stream:
            stream.write(b"\x09\x00")
        state = run({**value, "operation": "inspect"})
        assert state["tail_bytes"] == 2 and state["prefix_valid"] and not state["length_matches"]
    elif damage == "source":
        with Path(value["sources"][0]["path"]).open("ab") as stream:
            stream.write(b"changed")
    elif damage == "alias":
        info = work.stat()
        value["sources"] = [{"path": str(work), "size_bytes": info.st_size,
                             "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns, "pcm_frames": 10}]
    elif damage == "incremental":
        value["max_new_bytes"] = 27
    else:
        value["absolute_max_output_bytes"] = 83
    before = work.read_bytes()
    with pytest.raises(pcm.AssemblyError):
        run(value)
    assert work.read_bytes() == before
    assert not Path(value["receipt_path"]).exists()


def test_receipt_replay_cannot_append_again_and_symlink_is_rejected(tmp_path):
    value = manifest(tmp_path, [source(tmp_path / "a.wav", b"\x01\x00" * 10)])
    first = run(value)
    work = Path(value["work_path"])
    before = work.read_bytes()
    with pytest.raises(pcm.AssemblyError, match="receipt_already_exists"):
        run(value)
    assert work.read_bytes() == before
    next_value = continuation(value, first, source(tmp_path / "b.wav", b"\x02\x00" * 10))
    work.rename(tmp_path / "moved.wav")
    work.symlink_to(tmp_path / "moved.wav")
    with pytest.raises(OSError):
        run(next_value)
    assert (tmp_path / "moved.wav").read_bytes() == before


def test_interrupted_append_preserves_unknown_tail_and_original_checkpoint(tmp_path, monkeypatch):
    value = manifest(tmp_path, [source(tmp_path / "a.wav", b"\x01\x00" * 10)])
    first = run(value)
    value = continuation(value, first, source(tmp_path / "b.wav", b"\x02\x00" * 10))
    actual_write = pcm.os.pwrite

    def interrupt_header(fd, data, offset):
        if offset < 44:
            raise OSError("simulated interruption after PCM write")
        return actual_write(fd, data, offset)

    monkeypatch.setattr(pcm.os, "pwrite", interrupt_header)
    observed = {"generated_bytes": 0, "work_created": False}
    with pytest.raises(OSError):
        pcm.execute(value, observed)
    assert observed["generated_bytes"] == 20
    state = run({**value, "operation": "inspect"})
    assert state["prefix_valid"] and state["header_valid"] and state["tail_bytes"] == 20
    assert not state["length_matches"]
    assert not Path(value["receipt_path"]).exists()
    with pytest.raises(pcm.AssemblyError, match="work_baseline_mismatch"):
        run(value)
    assert Path(value["work_path"]).stat().st_size == 84


def test_explicit_three_input_cap_and_checkpoint_slot_are_enforced(tmp_path):
    sources = [source(tmp_path / f"source-{i}.wav", b"\x01\x00" * 4) for i in range(3)]
    value = manifest(tmp_path, sources)
    with pytest.raises(Exception, match="source_count_invalid"):
        run(value)
    value["max_input_count"] = 3
    receipt = run(value)
    assert receipt["source_count"] == 3
    more = manifest(tmp_path, sources[:2])
    more.update(unit_id="request:2", receipt_path=str(tmp_path / "unit-2.json"),
        expected_file_identity=receipt["file_identity"], committed_pcm_bytes=receipt["pcm_bytes"],
        committed_pcm_sha256=receipt["pcm_sha256"])
    with pytest.raises(Exception, match="source_count_invalid"):
        run(more)
    more["max_input_count"] = 3
    assert run(more)["source_count"] == 3
