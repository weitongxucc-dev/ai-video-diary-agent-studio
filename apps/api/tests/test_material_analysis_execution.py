"""Default execution adapter checks; all external command execution is synthetic."""
import json
import subprocess

import pytest

from app.materials.analysis_execution import detect_scene_boundaries, probe_duration
from app.production import ProductionRepository


def test_default_execution_ports_keep_bindings_results_errors_and_scene_edges(tmp_path, monkeypatch):
    calls = []
    outcomes = []

    def command(argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(argv, **outcome)

    monkeypatch.setattr("app.materials.analysis_execution.subprocess.run", command)
    library = tmp_path / "library"
    library.mkdir()
    source = library / "opaque.mp4"
    source.write_bytes(b"synthetic opaque source; command execution replaced")
    repository = ProductionRepository(tmp_path / "defaults.sqlite", library)
    use_case = repository._material_analysis
    assert repository.duration_reader is use_case.duration_reader is probe_duration
    assert use_case.scene_reader is detect_scene_boundaries
    assert not hasattr(repository, "_probe_duration")
    assert not hasattr(repository, "_detect_scene_boundaries")
    assert calls == []  # Construction binds ports without running them.

    outcomes.append({"returncode": 0, "stdout": " 8.25\n", "stderr": ""})
    duration = repository.duration_reader(source)
    assert duration == 8.25 and isinstance(duration, float)
    outcomes.append({"returncode": 0, "stdout": "", "stderr":
        "pts_time:0 pts_time:4.0 pts_time:8.25 pts_time:9 pts_time:2.5 pts_time:2.5"})
    assert use_case.scene_reader(source, duration) == [4.0, 2.5, 2.5]

    missing = FileNotFoundError("synthetic unavailable external executable")
    outcomes.append(missing)
    with pytest.raises(FileNotFoundError) as caught:
        repository.duration_reader(source)
    assert caught.value is missing
    failed_probe = subprocess.CalledProcessError(11, ["synthetic ffprobe"], output="", stderr="probe failed")
    outcomes.append(failed_probe)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        repository.duration_reader(source)
    assert caught.value is failed_probe
    outcomes.append({"returncode": 0, "stdout": "invalid duration", "stderr": ""})
    with pytest.raises(ValueError):
        repository.duration_reader(source)
    outcomes.append({"returncode": 17, "stdout": "partial stdout", "stderr": "scene failed"})
    with pytest.raises(subprocess.CalledProcessError) as caught:
        use_case.scene_reader(source, duration)
    assert (caught.value.returncode, caught.value.output, caught.value.stderr) == (
        17, "partial stdout", "scene failed")
    assert caught.value.cmd == calls[-1]["argv"]
    assert outcomes == []

    override_calls = []

    def custom_duration(path):
        override_calls.append(("duration", str(path)))
        return 12.0

    def custom_scene(path, value):
        override_calls.append(("scene", str(path), value))
        return [6.0]

    overridden = ProductionRepository(tmp_path / "overrides.sqlite", library,
        duration_reader=custom_duration, scene_reader=custom_scene)
    assert overridden.duration_reader is custom_duration
    assert overridden._material_analysis.scene_reader is custom_scene
    assert override_calls == []
    assert overridden.duration_reader(source) == 12.0
    assert overridden._material_analysis.scene_reader(source, 12.0) == [6.0]

    class FalseyPort:
        def __bool__(self):
            return False

        def __call__(self, *args):
            raise AssertionError("The original OR default must replace this falsey port")

    falsey = FalseyPort()
    fallback = ProductionRepository(tmp_path / "falsey.sqlite", library,
        duration_reader=falsey, scene_reader=falsey)
    assert fallback.duration_reader is probe_duration
    assert fallback._material_analysis.scene_reader is detect_scene_boundaries
    assert len(calls) == 6
    (tmp_path / "adapter-observations.json").write_text(json.dumps({
        "default_duration_is_same_public_port": True,
        "default_scene_is_same_port": True,
        "old_private_methods_absent": True,
        "constructors_only_bind_execution_ports": True,
        "explicit_override_identity_and_calls_preserved": override_calls,
        "falsey_override_uses_original_OR_default": True,
        "scene_zero_end_outside_filtered_order_and_duplicates_preserved": True,
        "OSError_and_probe_CalledProcessError_same_instances": True,
        "invalid_duration_ValueError_and_scene_nonzero_original_diagnostics": True,
        "actual_external_boundary_calls": calls,
        "external_execution_is_synthetic": True,
        "real_media_models_host_grant_execution": False,
    }, ensure_ascii=False, indent=2) + "\n")
