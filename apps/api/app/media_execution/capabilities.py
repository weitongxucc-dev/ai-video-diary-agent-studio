from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


CAPABILITY_NAMES = (
    "probe", "scenes", "look", "cut", "join", "silence", "fit", "crop", "cropdetect",
    "deinterlace", "denoise", "redact", "sphere", "straighten", "insert", "background",
    "reverse", "stabilize", "sequence", "waveform", "freeze", "pad", "speedramp", "loop",
    "broll", "metadata", "grid", "audio", "sync", "loudness", "caption", "overlay", "graphics",
    "color", "export", "proxy", "check", "report", "render", "batch", "multicam", "verify",
)

IMPLEMENTED = frozenset({"probe", "look", "cut", "join", "fit", "audio", "caption", "export"})


@dataclass(frozen=True)
class ToolDependency:
    name: str
    path: str | None
    status: str
    version: str | None = None
    sha256: str | None = None
    detail: str | None = None
    features: tuple[str, ...] = ()


def _tool(name: str) -> ToolDependency:
    path = shutil.which(name)
    if path is None:
        return ToolDependency(name, None, "missing", detail=f"{name} 未在 PATH 中找到")
    try:
        result = subprocess.run([path, "-version"], capture_output=True, text=True,
                                timeout=10, check=False)
        first = (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else ""
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if result.returncode != 0 or not first:
            return ToolDependency(name, path, "unknown", sha256=digest, detail="版本探测返回了不可解析的结果")
        features: tuple[str, ...] = ()
        if name == "ffmpeg":
            feature_result = subprocess.run([path, "-hide_banner", "-filters"], capture_output=True, text=True,
                                             timeout=15, check=False)
            encoder_result = subprocess.run([path, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                            timeout=15, check=False)
            if feature_result.returncode != 0 or encoder_result.returncode != 0:
                return ToolDependency(name, path, "unknown", version=first, sha256=digest, detail="能力列表探测失败")
            feature_names = set()
            for line in (feature_result.stdout + "\n" + encoder_result.stdout).splitlines():
                parts = line.split()
                if parts and not parts[0].startswith("-") and len(parts) >= 2:
                    feature_names.add(parts[1])
            features = tuple(sorted(feature_names))
        return ToolDependency(name, path, "available", version=first, sha256=digest, features=features)
    except (OSError, subprocess.SubprocessError) as exc:
        return ToolDependency(name, path, "unknown", detail=f"版本探测失败: {exc}")


def detect_dependencies() -> dict[str, ToolDependency]:
    return {name: _tool(name) for name in ("ffmpeg", "ffprobe")}


def capability_catalog(dependencies: dict[str, ToolDependency] | None = None) -> list[dict[str, object]]:
    deps = dependencies or detect_dependencies()
    dependency_status = "available" if all(item.status == "available" for item in deps.values()) else (
        "missing" if any(item.status == "missing" for item in deps.values()) else "unknown")
    feature_requirements = {"caption": ("subtitles", "drawtext"), "fit": ("scale", "pad"),
                            "export": ("libx264",), "audio": ("aac",)}
    return [{
        "name": name,
        "status": (
            "missing" if name not in IMPLEMENTED and dependency_status == "available"
            else ("available" if dependency_status == "available" else dependency_status)
        ) if name not in feature_requirements or any(feature in deps["ffmpeg"].features for feature in feature_requirements[name])
        else "missing",
        "stability": "stable" if name in {"probe", "cut", "join", "fit", "audio", "caption", "export"} else "beta",
        "requires": ["ffmpeg", "ffprobe"],
        "implementation": "曜核媒体适配器" if name in IMPLEMENTED else "registered_pending_adapter",
    } for name in CAPABILITY_NAMES]


def dependency_payload(dependencies: dict[str, ToolDependency]) -> dict[str, dict[str, object]]:
    return {name: asdict(value) for name, value in dependencies.items()}
