"""macOS-only single-process media scripts; no scheduling or persistence owner.

Seatbelt is a deprecated/private platform interface. This leaf does not enable
scripts without a calibrated media_script policy in the existing resource owner.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.device_scheduler import DeviceScheduler, Grant, RecoveryBlocked
from app.granted_subprocess import run_granted_command
from app.host_resource_probes import HostResourceProbe


class MediaScriptError(ValueError):
    """Invalid sealed inputs, isolation environment or measured output."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _name(value: str) -> None:
    if not value or Path(value).name != value or value in {".", ".."} or "\\" in value or "\x00" in value:
        raise MediaScriptError("input/output name must be one relative filename")


@dataclass(frozen=True)
class ScriptInputRevision:
    name: str
    revision: str
    sha256: str


@dataclass(frozen=True)
class PngOutputContract:
    name: str
    max_bytes: int
    max_width: int
    max_height: int


@dataclass(frozen=True)
class ScriptToolchain:
    python: Path
    stdlib: Path
    python_library: Path
    pillow: Path
    version: str
    digest: str

    @classmethod
    def from_paths(cls, *, python: Path, stdlib: Path, python_library: Path,
                   pillow: Path, version: str) -> ScriptToolchain:
        """Trusted host setup only: fingerprint the exact code roots, never HOME."""
        paths = [python.resolve(strict=True), stdlib.resolve(strict=True),
                 python_library.resolve(strict=True), pillow.resolve(strict=True)]
        if not paths[0].is_file() or not paths[2].is_file() or not paths[1].is_dir() or not paths[3].is_dir():
            raise MediaScriptError("Python/Pillow toolchain is incomplete")
        digest = hashlib.sha256(version.encode())
        for root in paths:
            files = sorted(root.rglob("*")) if root.is_dir() else [root]
            for file in files:
                if file.is_symlink():
                    raise MediaScriptError("toolchain roots must not contain symlinks")
                if file.is_file():
                    digest.update(_json([str(file), _sha(file)]).encode())
        return cls(*paths, version, digest.hexdigest())


@dataclass(frozen=True)
class SealedMediaScript:
    script_source: str
    language: str
    parameters_json: str
    input_revisions: tuple[ScriptInputRevision, ...]
    requested_outputs: tuple[PngOutputContract, ...]
    toolchain: ScriptToolchain
    worker_sha256: str
    digest: str


def seal_media_script(*, script_source: str, language: str, parameters: dict,
                      input_revisions: tuple[ScriptInputRevision, ...],
                      requested_outputs: tuple[PngOutputContract, ...],
                      toolchain: ScriptToolchain) -> SealedMediaScript:
    if language != "python" or not script_source.strip():
        raise MediaScriptError("only nonempty Python scripts are supported")
    try:
        compile(script_source, "<media-script>", "exec")
    except SyntaxError as exc:
        raise MediaScriptError(f"invalid Python syntax at line {exc.lineno}") from None
    parameters_json = _json(parameters)
    names = []
    for item in input_revisions:
        _name(item.name)
        if not item.revision or len(item.sha256) != 64 or any(c not in "0123456789abcdef" for c in item.sha256):
            raise MediaScriptError("input revision and SHA256 are required")
        names.append(item.name)
    if len(names) != len(set(names)):
        raise MediaScriptError("duplicate input name")
    names = []
    for item in requested_outputs:
        _name(item.name)
        if not item.name.endswith(".png") or any(type(v) is not int or v <= 0 for v in (item.max_bytes, item.max_width, item.max_height)):
            raise MediaScriptError("bounded PNG output contracts are required")
        names.append(item.name)
    if not names or len(names) != len(set(names)):
        raise MediaScriptError("at least one unique output is required")
    worker = Path(__file__).parents[1] / "model_workers" / "media_script_worker.py"
    worker_sha256 = _sha(worker)
    manifest = [script_source, language, parameters_json,
                [[v.name, v.revision, v.sha256] for v in input_revisions],
                [[v.name, v.max_bytes, v.max_width, v.max_height] for v in requested_outputs],
                toolchain.version, toolchain.digest, worker_sha256, _sha(Path(__file__))]
    digest = hashlib.sha256(_json(manifest).encode()).hexdigest()
    return SealedMediaScript(script_source, language, parameters_json, input_revisions, requested_outputs, toolchain, worker_sha256, digest)


@dataclass(frozen=True)
class ScriptExecutionLimits:
    timeout_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_input_bytes: int

    def __post_init__(self) -> None:
        if not 0 < self.timeout_seconds < float("inf") or any(type(v) is not int or v < 0 for v in (
                self.max_stdout_bytes, self.max_stderr_bytes, self.max_input_bytes)):
            raise MediaScriptError("finite host execution limits are required")


@dataclass(frozen=True)
class PreparedMediaScript:
    sealed: SealedMediaScript
    directory: Path
    argv: tuple[str, ...]
    env: dict[str, str]
    limits: ScriptExecutionLimits
    control_hashes: tuple[tuple[str, str], ...]


def _literal(path: Path) -> str:
    return "(literal " + json.dumps(str(path), ensure_ascii=False) + ")"


def _subpath(path: Path) -> str:
    return "(subpath " + json.dumps(str(path), ensure_ascii=False) + ")"


def prepare_media_script(*, sealed: SealedMediaScript, input_paths: dict[str, Path],
                         job_directory: Path, limits: ScriptExecutionLimits) -> PreparedMediaScript:
    if sys.platform != "darwin":
        raise MediaScriptError("macOS Seatbelt runner is unavailable; no unrestricted fallback")
    if set(input_paths) != {v.name for v in sealed.input_revisions}:
        raise MediaScriptError("mapped inputs do not match sealed input revisions")
    # Caller supplies a new exclusive directory. No existing job/user data is overwritten.
    job_directory.mkdir(mode=0o700)
    root = job_directory.resolve(strict=True)
    for name in ("inputs", "out", "tmp", "control"):
        (root / name).mkdir(mode=0o700)
    total = 0
    for item in sealed.input_revisions:
        path = input_paths[item.name]
        if path.is_symlink() or not path.is_file():
            raise MediaScriptError("input mapping must reference a regular file")
        total += path.stat().st_size
        if total > limits.max_input_bytes:
            raise MediaScriptError("mapped input bytes exceed host limit")
        destination = root / "inputs" / item.name
        shutil.copyfile(path, destination)
        if _sha(destination) != item.sha256:
            raise MediaScriptError("mapped input differs from sealed revision")
        destination.chmod(0o400)
    script = root / "control" / "script.py"
    worker = root / "control" / "worker.py"
    request = root / "control" / "request.json"
    script.write_text(sealed.script_source, encoding="utf-8")
    shutil.copyfile(Path(__file__).parents[1] / "model_workers" / "media_script_worker.py", worker)
    if _sha(worker) != sealed.worker_sha256:
        raise MediaScriptError("trusted worker changed after sealing")
    request.write_text(_json({
        "script_path": str(script), "pillow_parent": str(sealed.toolchain.pillow.parent),
        "parameters": json.loads(sealed.parameters_json),
        "inputs": {v.name: str(root / "inputs" / v.name) for v in sealed.input_revisions},
        "outputs": {v.name: str(root / "out" / v.name) for v in sealed.requested_outputs},
        "output_directory": str(root / "out"),
        "max_output_file_bytes": max(v.max_bytes for v in sealed.requested_outputs),
    }), encoding="utf-8")
    tc = sealed.toolchain
    read_roots = [root / "inputs", root / "control", tc.stdlib, tc.pillow,
                  Path("/System/Library/Frameworks"), Path("/System/Library/PrivateFrameworks"),
                  Path("/System/Cryptexes/OS/System/Library/dyld"),
                  Path("/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld"),
                  Path("/System/Volumes/Preboot/Cryptexes/OS/usr/lib")]
    # dyld ignition opens the root directory; Python's importer lists the
    # exact Pillow parent. These literals do not authorize descendants.
    read_files = [tc.python, tc.python_library, tc.pillow.parent,
                  Path("/"), Path("/dev/null"), Path("/dev/urandom")]
    profile = "\n".join([
        "(version 1)", "(deny default)",
        "(deny process-info* sysctl-read)",
        "(allow file-read-metadata " + " ".join(_literal(p) for p in sorted({
            parent for path in read_roots + read_files + [root / 'out', root / 'tmp'] for parent in path.parents
        })) + ")",
        "(allow file-read* " + " ".join(_subpath(p) for p in read_roots + [Path('/usr/lib')]) + " " + " ".join(_literal(p) for p in read_files) + ")",
        "(allow file-map-executable " + " ".join(_subpath(p) for p in read_roots[2:] + [Path('/usr/lib')]) + " " + _literal(tc.python) + " " + _literal(tc.python_library) + ")",
        "(allow file-read* " + _subpath(root / "out") + " " + _subpath(root / "tmp") + ")",
        "(allow file-write* " + " ".join(_literal(root / "out" / v.name) for v in sealed.requested_outputs) + ")",
        # No process-fork, network*, mach-lookup, signal or other filesystem grant.
    ])
    policy = root / "policy.sb"
    policy.write_text(profile, encoding="utf-8")
    argv = (str(tc.python), "-I", "-B", "-S", str(worker), str(request), str(policy))
    controls = tuple((str(p.relative_to(root)), _sha(p)) for p in (script, worker, request, policy))
    return PreparedMediaScript(sealed, root, argv, {"TMPDIR": str(root / "tmp"), "LC_ALL": "C"}, limits, controls)


def validate_prepared_media_script(prepared: PreparedMediaScript) -> None:
    """Re-read frozen inputs before launch; no latest-version substitution."""
    sealed = prepared.sealed
    current_seal = seal_media_script(script_source=sealed.script_source, language=sealed.language,
        parameters=json.loads(sealed.parameters_json), input_revisions=sealed.input_revisions,
        requested_outputs=sealed.requested_outputs, toolchain=sealed.toolchain)
    if current_seal.digest != sealed.digest:
        raise MediaScriptError("script executor or sealed contract changed before launch")
    for name, digest in prepared.control_hashes:
        path = prepared.directory / name
        if path.is_symlink() or _sha(path) != digest:
            raise MediaScriptError("prepared script/control changed after sealing")
    tc = prepared.sealed.toolchain
    current = ScriptToolchain.from_paths(python=tc.python, stdlib=tc.stdlib,
        python_library=tc.python_library, pillow=tc.pillow, version=tc.version)
    if current.digest != tc.digest:
        raise MediaScriptError("toolchain changed after sealing")
    for item in prepared.sealed.input_revisions:
        path = prepared.directory / "inputs" / item.name
        if path.is_symlink() or _sha(path) != item.sha256:
            raise MediaScriptError("staged input changed before launch")


@dataclass(frozen=True)
class MeasuredScriptOutput:
    name: str
    sha256: str
    bytes: int
    width: int
    height: int


def verify_media_script_outputs(prepared: PreparedMediaScript) -> tuple[MeasuredScriptOutput, ...]:
    directory = prepared.directory / "out"
    expected = {v.name for v in prepared.sealed.requested_outputs}
    if {p.name for p in directory.iterdir()} != expected:
        raise MediaScriptError("output directory differs from the sealed output contract")
    result = []
    for item in prepared.sealed.requested_outputs:
        path = directory / item.name
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise MediaScriptError("output must be a regular nonlinked file")
        size = path.stat().st_size
        if size > item.max_bytes:
            raise MediaScriptError("output bytes exceed contract")
        with path.open("rb") as file:
            header = file.read(24)
        if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            raise MediaScriptError("output is not PNG")
        width, height = struct.unpack(">II", header[16:24])
        if not (0 < width <= item.max_width and 0 < height <= item.max_height):
            raise MediaScriptError("PNG dimensions exceed contract")
        try:
            with Image.open(path) as image:
                if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                    raise MediaScriptError("only single-frame PNG output is supported")
                image.load()
        except (OSError, SyntaxError) as exc:
            raise MediaScriptError("PNG pixels cannot be decoded") from exc
        result.append(MeasuredScriptOutput(item.name, _sha(path), size, width, height))
    for item in prepared.sealed.input_revisions:
        if _sha(prepared.directory / "inputs" / item.name) != item.sha256:
            raise MediaScriptError("staged input changed during execution")
    return tuple(result)


def run_media_script(*, prepared: PreparedMediaScript, scheduler: DeviceScheduler,
                     grant: Grant, cancel_event: threading.Event | None = None) -> tuple[MeasuredScriptOutput, ...]:
    scheduler.assert_grant(grant)
    if grant.input_digest != prepared.sealed.digest:
        raise MediaScriptError("grant does not bind this sealed script")
    resources = scheduler.resources
    if not isinstance(resources, HostResourceProbe):
        raise RecoveryBlocked("media script requires the existing real resource probe")
    facts = resources._grant_resources(grant)
    if not facts or facts.get("kind") != "media_script" or resources.scene_runtime_limits(grant) is None:
        raise RecoveryBlocked("media_script resource policy is not calibrated; execution unavailable")
    validate_prepared_media_script(prepared)
    limits = prepared.limits
    result = run_granted_command(scheduler, grant, prepared.argv, timeout_seconds=limits.timeout_seconds,
        max_stdout_bytes=limits.max_stdout_bytes, max_stderr_bytes=limits.max_stderr_bytes,
        cancel_event=cancel_event, env=prepared.env,
        output_paths=[prepared.directory / "out" / v.name for v in prepared.sealed.requested_outputs],
        max_output_file_bytes=sum(v.max_bytes for v in prepared.sealed.requested_outputs))
    if result.returncode != 0:
        raise MediaScriptError(f"isolated script exited {result.returncode}")
    return verify_media_script_outputs(prepared)
