"""PyInstaller spec for the automation worker.

Build::

    cd apps/api
    pyinstaller worker.spec

Output:  dist/worker-server  (single-file executable)

Shares the same Analysis / exclusions as the API server spec.
Only the entry point and output name differ.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ── Paths ────────────────────────────────────────────────────────────────────
_SPEC_DIR = Path(SPECPATH)  # type: ignore[name-defined]  # noqa: F821
_REPO_ROOT = _SPEC_DIR.parent.parent  # backend/

# Entry point
_ENTRY = _SPEC_DIR / "worker.py"

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
            "pyJianYingDraft/assets",
        )
    )

# ── Hidden imports ───────────────────────────────────────────────────────────
_APP_MODULES = [
    f"app.{p.stem}"
    for p in (_SPEC_DIR / "app").glob("*.py")
    if p.stem not in {"__init__"}
]

_WORKER_HIDDEN = [
    "app.automation",
]

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

_DRAFT_TOOL_HIDDEN = [
    "pyJianYingDraft",
    "jianyingdraft",
    "capcut_mate",
]

_LANGGRAPH_HIDDEN: list[str] = []

_HIDDEN_IMPORTS = sorted(
    set(
        _APP_MODULES
        + _WORKER_HIDDEN
        + _UVICORN_HIDDEN
        + _DRAFT_TOOL_HIDDEN
        + _LANGGRAPH_HIDDEN
    )
)

# ── Excludes ────────────────────────────────────────────────────────────────
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
    name="worker-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
