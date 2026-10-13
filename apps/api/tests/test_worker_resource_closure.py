"""Real file-entry import closure in disposable Desktop resource trees."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app import media_script, media_timing
from app.media_script import PngOutputContract, ScriptExecutionLimits, ScriptToolchain
from model_workers import media_timing as shared_timing
from test_packaging_modules import declared_data_inputs


def copied_worker_resources(tmp_path):
    api = Path(__file__).parents[1]
    repository = api.parents[1]
    desktop = json.loads((repository / "apps/desktop/package.json").read_text())
    resource = next(item for item in desktop["build"]["extraResources"] if item["to"] == "model-workers")
    assert resource["filter"] == ["**/*.py"]
    destination = tmp_path / "Resources" / resource["to"]
    source = (repository / "apps/desktop" / resource["from"]).resolve()
    for file in source.rglob("*.py"):
        target = destination / file.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
    assert not (destination.parent / "app").exists(), "the external worker never receives the API tree"
    return destination


@pytest.mark.parametrize("name", ["musetalk_worker.py", "latentsync_worker.py"])
def test_copied_worker_help_runs_without_repo_paths_models_or_app(tmp_path, name):
    workers = copied_worker_resources(tmp_path)
    completed = subprocess.run([sys.executable, "-E", "-s", str(workers / name), "--help"], cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "TMPDIR": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert "--request" in completed.stdout and "--response" in completed.stdout
    assert "--runtime-root" in completed.stdout


def test_worker_and_api_share_one_timing_implementation_and_existing_runner_contract():
    for name in ("MediaTimingError", "probe_avatar_media", "probe_media_duration"):
        assert getattr(media_timing, name) is getattr(shared_timing, name)
    api = Path(__file__).parents[1]
    for name in ("musetalk_worker.py", "latentsync_worker.py"):
        source = (api / "model_workers" / name).read_text()
        assert "from app." not in source and "sys.path.insert" not in source
    imports = {node.module.split(".")[0] for node in ast.walk(ast.parse(Path(shared_timing.__file__).read_text()))
        if isinstance(node, ast.ImportFrom) and node.module}
    imports |= {name.name.split(".")[0] for node in ast.walk(ast.parse(Path(shared_timing.__file__).read_text()))
        if isinstance(node, ast.Import) for name in node.names}
    assert imports <= {"__future__", "json", "math", "subprocess", "fractions", "pathlib"}
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "1.25\n", "")

    assert media_timing.probe_media_duration(Path("synthetic.wav"), runner=runner) == 1.25
    assert calls[0][0][0] == "ffprobe" and calls[0][1]["timeout"] == 30


def test_new_shared_worker_files_enter_existing_candidate_input_filter(tmp_path):
    api = Path(__file__).parents[1]
    repository = api.parents[1]
    workers = copied_worker_resources(tmp_path)
    for name in ("__init__.py", "media_timing.py"):
        assert (workers / name).read_bytes() == (api / "model_workers" / name).read_bytes()
    candidate = tmp_path / "candidate-inputs"
    for relative, content in {
        "apps/desktop/main.js": b"fixture-main",
        "apps/desktop/device-identity.js": b"fixture-device",
        "apps/desktop/preload.js": b"fixture-preload",
        "apps/desktop/runtime/entry.js": b"fixture-runtime",
        "apps/desktop/package.json": b"{}",
        "apps/desktop/assets/config.json": b"{}",
        "apps/desktop/dist/api-server": b"fixture-api-binary",
        "apps/api/agent_tools/diary_tools.ts": b"fixture-tool",
        "apps/web/.next/standalone-bundled/apps/web/server.js": b"fixture-web",
    }.items():
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    shutil.copytree(workers, candidate / "apps/api/model_workers")
    generator = repository / "apps/desktop/scripts/generate-build-identity.js"

    def identity():
        result = subprocess.run(["node", "-e",
            "const {generateBuildIdentity}=require(process.argv[1]); console.log(JSON.stringify(generateBuildIdentity({repositoryRoot:process.argv[2],outputPath:process.argv[3]})));",
            str(generator), str(candidate), str(candidate / "candidate.json")],
            cwd=tmp_path, env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
            capture_output=True, text=True, check=True, timeout=10)
        return json.loads(result.stdout)

    first = identity()
    for name in ("model-workers/__init__.py", "model-workers/media_timing.py"):
        assert any(item["path"] == name for item in first["inputs"])
    helper = candidate / "apps/api/model_workers/media_timing.py"
    helper.write_bytes(helper.read_bytes() + b"\n# changed candidate helper\n")
    assert identity()["candidateInputDigest"] != first["candidateInputDigest"]


def frozen_script_sources(tmp_path, monkeypatch):
    frozen = tmp_path / "_MEIPASS"
    for source, relative in declared_data_inputs():
        target = frozen / relative / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    monkeypatch.setattr(media_script, "__file__", str(frozen / "app/media_script.py"))
    # Sealing only; this nonexistent runtime is never launched or called ready.
    toolchain = ScriptToolchain(Path("/missing/python"), Path("/missing/stdlib"),
        Path("/missing/libpython"), Path("/missing/PIL"), "fixture", "a" * 64)

    def seal():
        return media_script.seal_media_script(script_source="pass", language="python", parameters={}, input_revisions=(),
            requested_outputs=(PngOutputContract("result.png", 4096, 16, 16),), toolchain=toolchain)

    return frozen, seal


def test_frozen_layout_seal_uses_actual_source_and_worker_bytes(tmp_path, monkeypatch):
    frozen, seal = frozen_script_sources(tmp_path, monkeypatch)
    worker = frozen / "model_workers/media_script_worker.py"
    source = frozen / "app/media_script.py"
    first = seal()
    assert first.worker_sha256 == hashlib.sha256(worker.read_bytes()).hexdigest()
    worker.write_bytes(worker.read_bytes() + b"\n# changed worker input\n")
    changed_worker = seal()
    assert changed_worker.digest != first.digest and changed_worker.worker_sha256 != first.worker_sha256
    source.write_bytes(source.read_bytes() + b"\n# changed implementation input\n")
    assert seal().digest != changed_worker.digest


@pytest.mark.parametrize("missing", ["app/media_script.py", "model_workers/media_script_worker.py"])
def test_missing_frozen_source_never_seals_with_guessed_digest(tmp_path, monkeypatch, missing):
    frozen, seal = frozen_script_sources(tmp_path, monkeypatch)
    (frozen / missing).unlink()
    with pytest.raises(FileNotFoundError):
        seal()


def test_worker_changed_after_seal_is_rejected_before_runtime_launch(tmp_path, monkeypatch):
    frozen, seal = frozen_script_sources(tmp_path, monkeypatch)
    sealed = seal()
    worker = frozen / "model_workers/media_script_worker.py"
    worker.write_bytes(worker.read_bytes() + b"\n# changed after seal\n")
    monkeypatch.setattr(media_script.sys, "platform", "darwin")
    with pytest.raises(media_script.MediaScriptError, match="trusted worker changed after sealing"):
        media_script.prepare_media_script(sealed=sealed, input_paths={}, job_directory=tmp_path / "isolated-job",
            limits=ScriptExecutionLimits(1, 100, 100, 0))
