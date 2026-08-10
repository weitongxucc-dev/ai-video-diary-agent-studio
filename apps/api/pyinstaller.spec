"""PyInstaller spec for ai-video-diary-agent-api.

Build::

    cd apps/api
    pyinstaller pyinstaller.spec

Output:  dist/api-server  (single-file executable)

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
from typing import Any

# ── Paths ────────────────────────────────────────────────────────────────────
_SPEC_DIR = Path(SPECPATH)  # type: ignore[name-defined]  # noqa: F821
_REPO_ROOT = _SPEC_DIR.parent.parent  # backend/

# Entry point
_ENTRY = _SPEC_DIR / "server.py"

# Assets shipped inside the bundle
_ASSET_DIRS: list[tuple[str, str]] = []

# ── pyJianYingDraft template assets ──────────────────────────────────────────
_jyd_assets = (
    _SPEC_DIR / ".venv" / "lib" / "python3.11"
    / "site-packages" / "pyJianYingDraft" / "assets"
)
if _jyd_assets.is_dir():
    _ASSET_DIRS.append(
        (
            str(_jyd_assets),
            "pyJianYingDraft/assets",  # destination inside the bundle
        )
    )

# ── Hidden imports ───────────────────────────────────────────────────────────
# Every app.* module so the FastAPI router collection works
_APP_MODULES = [
    f"app.{p.stem}"
    for p in (_SPEC_DIR / "app").glob("*.py")
    if p.stem not in {"__init__"}
]

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

# DraftWorkspace probes for these dynamically
_DRAFT_TOOL_HIDDEN = [
    "pyJianYingDraft",
    "jianyingdraft",
    "capcut_mate",
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
        + _DRAFT_TOOL_HIDDEN
        + _LANGGRAPH_HIDDEN
    )
)

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
    binaries=[],
    datas=[(src, dest) for src, dest in _ASSET_DIRS],
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
    a.binaries,
    a.datas,
    [],
    name="api-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # show logs on stdout
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

# ── COLLECT (optional, for debugging) ────────────────────────────────────────
# coll = COLLECT(exe, name="api-server")
