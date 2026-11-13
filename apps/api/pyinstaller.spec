"""PyInstaller spec for ai-video-diary-agent-api.

Build::

    cd apps/api
    pyinstaller pyinstaller.spec

Output: dist/api-server (single executable on macOS; service directory on Windows)

Size optimisations:
  * Exclude CPython test suite, tkinter, unittest, numpy, pillow, ruff
  * Exclude pip, setuptools, coverage, pydoc, doctest, bz2, lzma
  * Exclude distutils, concurrent.futures.process (not used at runtime)
  * UPX-compress the final binary

Hidden imports are listed explicitly because uvicorn, langgraph, and the
app itself rely on dynamic import discovery that PyInstaller cannot trace.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any
from PyInstaller.utils.hooks import collect_all

# ── Paths ────────────────────────────────────────────────────────────────────
_SPEC_DIR = Path(SPECPATH)  # type: ignore[name-defined]  # noqa: F821
_REPO_ROOT = _SPEC_DIR.parent.parent  # backend/

# Entry point
_ENTRY = _SPEC_DIR / "server.py"

# Assets shipped inside the bundle
# These exact bare files are read/hash inputs of app.media_script. PyInstaller
# supplies __file__ under _MEIPASS/app, so preserve that declared relative layout.
_ASSET_DIRS: list[tuple[str, str]] = [
    (str(_SPEC_DIR / 'app' / 'template_assets'), 'app/template_assets'),
    (str(_SPEC_DIR / "app" / "media_script.py"), "app"),
    (str(_SPEC_DIR / "model_workers" / "media_script_worker.py"), "model_workers"),
]

# ── Hidden imports ───────────────────────────────────────────────────────────
# Include nested packages used by runtime registrations as well as HTTP imports.
def _application_modules(application_directory: Path) -> list[str]:
    modules = []
    for source in sorted(application_directory.rglob("*.py")):
        parts = source.relative_to(application_directory).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            modules.append("app." + ".".join(parts))
    return sorted(set(modules))


_APP_MODULES = _application_modules(_SPEC_DIR / "app")

# pywintypes loads this dynamically when CredRead converts credential timestamps.
_WINDOWS_HIDDEN = ["win32timezone"] if sys.platform == "win32" else []

# uvicorn dynamic imports (auto protocol / loop / ws selection at runtime)
_UVICORN_HIDDEN = [
    "uvicorn.logging",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.websockets_sansio_impl",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]

# langgraph + pydantic internals (often missed)
_LANGGRAPH_HIDDEN: list[str] = [
    # "langgraph",
    # "langgraph.graph",
    # "langgraph.checkpoint",
]

_HIDDEN_IMPORTS = sorted(
    set(
        _APP_MODULES
        + _UVICORN_HIDDEN
        + _LANGGRAPH_HIDDEN
        + _WINDOWS_HIDDEN
    )
)

# Playwright locates its Node driver relative to its installed package and
# imports implementation modules dynamically; collect its runtime payload as
# well as hidden imports so the approved browser path survives the API bundle.
_PLAYWRIGHT_DATAS, _PLAYWRIGHT_BINARIES, _PLAYWRIGHT_HIDDEN = collect_all("playwright")
_HIDDEN_IMPORTS = sorted(set(_HIDDEN_IMPORTS + _PLAYWRIGHT_HIDDEN))

# ── Excludes: trim large, unused packages ────────────────────────────────────
_EXCLUDES: list[str] = [
    "tkinter",
    "_tkinter",
    "test",
    "unittest",
    "concurrent.futures.process",
    "distutils",
    "setuptools",
    "pip",
    "pkg_resources",
    "numpy",
    "numpy._core",
    "numpy.lib",
    "numpy.random",
    "pillow",
    "PIL.ImageQt",
    "PIL.ImageTk",
    "ruff",
    "pytest",
    "_pytest",
    "coverage",
    "pydoc",
    "doctest",
    "bz2",
    "lzma",
]

# Blacklist binaries we don't need
_EXCLUDE_BINARIES: list[str] = [
    "libtk*",
    "libtcl*",
    "libQt*",
]

# ── Analysis ─────────────────────────────────────────────────────────────────
a = Analysis(  # type: ignore[name-defined]  # noqa: F821
    [str(_ENTRY)],
    pathex=[str(_SPEC_DIR), str(_REPO_ROOT)],
    binaries=_PLAYWRIGHT_BINARIES,
    datas=[(src, dest) for src, dest in _ASSET_DIRS] + _PLAYWRIGHT_DATAS,
    hiddenimports=_HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDES,
    noarchive=False,
    optimize=0,
)

# ── Filter out large unused binaries ─────────────────────────────────────────
def _filter_binaries(pyi_analysis: Any, excludes: list[str]) -> Any:
    """Drop shared libs that match exclude globs."""
    import fnmatch

    filtered = []
    for src_path, name, info in pyi_analysis.binaries:
        if any(fnmatch.fnmatch(name, pat) for pat in excludes):
            continue
        filtered.append((src_path, name, info))
    pyi_analysis.binaries = filtered
    return pyi_analysis


a = _filter_binaries(a, _EXCLUDE_BINARIES)

# ── PYZ ──────────────────────────────────────────────────────────────────────
pyz = PYZ(a.pure, a.zipped_data)  # type: ignore[name-defined]  # noqa: F821

# ── EXE ──────────────────────────────────────────────────────────────────────
exe = EXE(  # type: ignore[name-defined]  # noqa: F821
    pyz,
    a.scripts,
    [] if sys.platform == 'win32' else a.binaries,
    [] if sys.platform == 'win32' else a.datas,
    [('X utf8', None, 'OPTION')] if sys.platform == 'win32' else [],
    name="api-server",
    # A Windows onefile bootloader leaves its Python child alive when the
    # supervised launcher is terminated. Onedir runs the API in that process.
    exclude_binaries=sys.platform == 'win32',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Windows' windowed bootloader replaces stdout/stderr with None. The API
    # is a piped child service; Electron hides its console and owns its logs.
    console=sys.platform == 'win32',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

if sys.platform == 'win32':
    coll = COLLECT(  # type: ignore[name-defined]  # noqa: F821
        exe, a.binaries, a.datas, name="api-server", strip=False, upx=True,
    )
