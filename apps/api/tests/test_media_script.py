"""Contract validation; no synthetic grant is used to authorize execution."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app import media_script
from app.device_scheduler import RecoveryBlocked
from app.media_script import (
    MediaScriptError, PngOutputContract, ScriptExecutionLimits, ScriptInputRevision,
    ScriptToolchain, prepare_media_script, run_media_script, seal_media_script,
    verify_media_script_outputs,
)


def seal(**overrides):
    # Contract fixture only. Nothing launches this deliberately nonexistent runtime.
    args = dict(script_source="print(parameters)", language="python", parameters={"color": "red"},
        input_revisions=(), requested_outputs=(PngOutputContract("image.png", 4096, 16, 16),),
        toolchain=ScriptToolchain(Path("/missing/python"), Path("/missing/stdlib"),
            Path("/missing/libpython.dylib"), Path("/missing/PIL"), "fixture", "a" * 64))
    return seal_media_script(**(args | overrides))


@pytest.mark.parametrize("override", [
    {"script_source": "print('different')"}, {"parameters": {"color": "blue"}},
    {"input_revisions": (ScriptInputRevision("in.png", "revision2", "b" * 64),)},
    {"requested_outputs": (PngOutputContract("image.png", 2048, 16, 16),)},
    {"toolchain": ScriptToolchain(Path("/missing/python"), Path("/missing/stdlib"),
        Path("/missing/libpython.dylib"), Path("/missing/PIL"), "fixture", "b" * 64)},
])
def test_seal_binds_every_execution_input(override):
    assert seal().digest != seal(**override).digest


def test_parameters_are_frozen_by_value_and_order_is_canonical():
    parameters = {"b": [1], "a": "yes"}
    sealed = seal(parameters=parameters)
    parameters["b"].append(2)
    assert sealed.parameters_json == '{"a":"yes","b":[1]}'
    assert sealed.digest == seal(parameters={"a":"yes", "b":[1]}).digest


@pytest.mark.parametrize("name", ["../escape.png", "/tmp/escape.png", "x/y.png", "x\\y.png", ".", ""])
def test_only_fixed_relative_output_filenames(name):
    with pytest.raises(MediaScriptError):
        seal(requested_outputs=(PngOutputContract(name, 4096, 16, 16),))


def test_modified_input_is_rejected_before_any_launch(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_bytes(b"changed")
    sealed = seal(input_revisions=(ScriptInputRevision("in.png", "r1", "a" * 64),))
    # Portable contract unit: allow construction of the Darwin candidate only.
    # Input mismatch exits before any policy/runtime launch; OS evidence belongs
    # to test_media_script_isolation.py and remains Darwin-only.
    with monkeypatch.context() as context:
        context.setattr(media_script.sys, "platform", "darwin")
        with pytest.raises(MediaScriptError, match="sealed revision"):
            prepare_media_script(sealed=sealed, input_paths={"in.png":source},
                job_directory=tmp_path / "job", limits=ScriptExecutionLimits(1, 100, 100, 100))


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_unsupported_platform_is_rejected_without_staging_or_launch(tmp_path, monkeypatch, platform):
    with monkeypatch.context() as context:
        context.setattr(media_script.sys, "platform", platform)
        with pytest.raises(MediaScriptError, match="Seatbelt runner is unavailable"):
            prepare_media_script(sealed=seal(), input_paths={}, job_directory=tmp_path / "job",
                limits=ScriptExecutionLimits(1, 100, 100, 100))
    assert not (tmp_path / "job").exists()


def prepared(tmp_path):
    (tmp_path / "out").mkdir()
    Image.new("RGB", (8,8), "green").save(tmp_path / "out" / "image.png")
    return SimpleNamespace(sealed=seal(), directory=tmp_path)


def test_output_measurement_ignores_self_report_and_rejects_corruption(tmp_path):
    job = prepared(tmp_path)
    measured, = verify_media_script_outputs(job)
    data = (tmp_path / "out" / "image.png").read_bytes()
    assert measured.sha256 == hashlib.sha256(data).hexdigest()
    assert measured.bytes == len(data) and (measured.width, measured.height) == (8,8)
    (tmp_path / "out" / "image.png").write_bytes(data[:30])
    with pytest.raises(MediaScriptError):
        verify_media_script_outputs(job)


@pytest.mark.parametrize("case", ["unexpected", "symlink", "oversize", "dimensions"])
def test_output_contract_is_host_measured(tmp_path, case):
    job = prepared(tmp_path)
    target = tmp_path / "out" / "image.png"
    if case == "unexpected":
        (tmp_path / "out" / "extra").write_text("unrequested")
    elif case == "symlink":
        target.unlink()
        target.symlink_to(tmp_path / "other")
    elif case == "oversize":
        target.write_bytes(b"x" * 4097)
    else:
        Image.new("RGB", (17,8)).save(target)
    with pytest.raises(MediaScriptError):
        verify_media_script_outputs(job)


@pytest.mark.parametrize("kind", [None,"task_audio","creation_render","media_script"])
def test_missing_script_resource_policy_never_launches(tmp_path, monkeypatch, kind):
    job = prepared(tmp_path)
    probe = object.__new__(media_script.HostResourceProbe)
    monkeypatch.setattr(probe, "_grant_resources", lambda grant: None if kind is None else {"kind":kind})
    monkeypatch.setattr(probe, "scene_runtime_limits", lambda grant: None)
    scheduler = SimpleNamespace(resources=probe, assert_grant=lambda grant: None)
    grant = SimpleNamespace(input_digest=job.sealed.digest)
    monkeypatch.setattr(media_script, "run_granted_command", lambda *a,**k: pytest.fail("unlicensed launch"))
    with pytest.raises(RecoveryBlocked, match="not calibrated"):
        run_media_script(prepared=job, scheduler=scheduler, grant=grant)


def test_nonfinite_execution_limits_are_rejected():
    with pytest.raises(MediaScriptError):
        ScriptExecutionLimits(float("nan"),100,100,100)


def test_invalid_code_has_a_defined_error_without_source_payload():
    with pytest.raises(MediaScriptError, match="syntax at line 1") as error:
        seal(script_source="synthetic-secret ! invalid")
    assert "synthetic-secret" not in str(error.value)


def test_control_tamper_is_rejected_before_runtime_or_launch(tmp_path):
    path = tmp_path / "control.json"
    path.write_bytes(b"original")
    job = SimpleNamespace(directory=tmp_path,
        sealed=seal(),
        control_hashes=(("control.json",hashlib.sha256(b"original").hexdigest()),))
    path.write_bytes(b"changed")
    with pytest.raises(MediaScriptError, match="control changed"):
        media_script.validate_prepared_media_script(job)
