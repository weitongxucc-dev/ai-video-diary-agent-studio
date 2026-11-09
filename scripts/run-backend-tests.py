#!/usr/bin/env python3
"""Run a complete platform suite, partitioned into sorted round-robin shards."""

import argparse
import fnmatch
import json
import os
from pathlib import Path
import subprocess
import sys


API = Path(__file__).resolve().parents[1] / "apps" / "api"
# These files include real Darwin CPU admission or memory-pressure monitoring.
DARWIN_FILES = frozenset({
    "tests/test_host_resource_probes.py",
    "tests/test_creation_audio_mix.py",
    "tests/test_task_media_grants.py",
    "tests/test_member_permits.py",
    "tests/test_material_probe.py",
    "tests/test_material_scene.py",
    "tests/test_narration_stage_application.py",
    "tests/test_device_resource_preparation.py",
})
IGNORED_DIRECTORIES = ("*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}", "__pycache__")


def test_files():
    files = []
    for directory, dirs, names in os.walk(API / "tests"):
        dirs[:] = [name for name in dirs if not any(fnmatch.fnmatchcase(name, pattern) for pattern in IGNORED_DIRECTORIES)]
        files.extend((Path(directory) / name).relative_to(API).as_posix() for name in names
                     if fnmatch.fnmatchcase(name, "test_*.py") or fnmatch.fnmatchcase(name, "*_test.py"))
    return sorted(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("portable", "darwin"), default="portable")
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--total", type=int, default=3)
    parser.add_argument("--junitxml", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.total < 1 or not 1 <= args.shard <= args.total:
        parser.error("shard must be between 1 and total, and total must be positive")
    all_files = test_files()
    missing = DARWIN_FILES.difference(all_files)
    if missing:
        parser.error(f"Darwin suite files not found: {sorted(missing)}")
    files = [path for path in all_files if (path in DARWIN_FILES) == (args.suite == "darwin")]
    if args.suite == "darwin" and sys.platform != "darwin" and not args.dry_run:
        parser.error("Darwin native tests require a real macOS runner")
    shards = [files[index::args.total] for index in range(args.total)]
    if any(not shard for shard in shards):
        parser.error("each shard must contain test files")
    selected = shards[args.shard - 1]
    argv = [sys.executable, "-m", "pytest", "-ra", "--tb=short", "--durations=20",
            f"--junitxml={args.junitxml}", *selected]
    print(json.dumps({"suite": args.suite, "discovered_files": len(all_files), "shard": args.shard, "total": args.total, "total_files": len(files),
                      "shard_counts": [len(shard) for shard in shards], "files": selected,
                      "cwd": str(API), "argv": argv}, indent=2), flush=True)
    if args.dry_run:
        return 0
    args.junitxml.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(argv, cwd=API)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == "__main__":
    sys.exit(main())
