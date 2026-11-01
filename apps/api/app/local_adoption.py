"""Explicit local maintenance; never imported by login or production routes.

Run from apps/api with the existing Python environment. Manifest files contain
identities, revisions and digests only. Private record bodies remain in SQLite.
"""
from __future__ import annotations
from app.local_adoption_maintenance import LocalAdoptionMaintenance, _local_adoption_digest

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path




def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("dry-run", "apply"):
        sub = subcommands.add_parser(command)
        sub.add_argument("--database", type=Path, required=True)
        sub.add_argument("--media-root", type=Path, required=True)
        sub.add_argument("--member-id", required=True)
        sub.add_argument("--manifest", type=Path, required=True)
        if command == "dry-run":
            sub.add_argument("--operation-id", required=True)
            sub.add_argument("--asset-id", action="append", required=True)
            sub.add_argument("--voice-id", required=True)
            sub.add_argument("--voice-version", required=True)
            sub.add_argument("--new-voice-version", required=True)
            sub.add_argument("--authorization-basis", required=True)
        else:
            sub.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "dry-run":
            manifest = LocalAdoptionMaintenance.plan_local_adoption(args.database, args.media_root,
                member_id=args.member_id, operation_id=args.operation_id, asset_ids=args.asset_id,
                voice_id=args.voice_id, voice_version=args.voice_version,
                new_voice_version=args.new_voice_version, authorization_basis=args.authorization_basis)
            # Do not overwrite a reviewed manifest; it is the explicit approval input.
            fd = os.open(args.manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(manifest, output, ensure_ascii=False, indent=2)
                output.write("\n")
            print(json.dumps({"status": "dry_run", "manifest_sha256": _local_adoption_digest(manifest),
                "asset_ids": [item["id"] for item in manifest["assets"]],
                "voice_id": args.voice_id, "voice_version": args.new_voice_version}, ensure_ascii=False))
        else:
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            result = LocalAdoptionMaintenance.apply_local_adoption(args.database, args.media_root,
                member_id=args.member_id, manifest=manifest, manifest_sha256=args.manifest_sha256)
            print(json.dumps({"status": "applied", **result}, ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        # Only our fixed reason codes are safe to print. OS/SQLite errors can
        # contain private filenames, row bodies or SQL bound values.
        reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("local_adoption_") else type(exc).__name__
        print(json.dumps({"status": "failed", "reason": reason}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
