#!/usr/bin/env python3
"""Run a bounded real-media adoption/render/output-observation acceptance.

The script uses the checked-in OpenFilm Glass Pulse audio file, the production
resource-governance store, and the same bounded frame observer used by the
output-observation use case. It writes the MP4 and JSON receipt below
``output/acceptance``; that directory is runtime output and is intentionally
ignored by Git.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

from app.creation.output_observation_use_case import observe_output  # noqa: E402
from app.editing_resource_catalog import editing_resource_catalog  # noqa: E402
from app.editing_resource_routes import build_editing_resource_router  # noqa: E402
from app.materials.resource_governance import (  # noqa: E402
    ResourceLicense,
    ResourceManifest,
    ResourceUsageRecord,
    manifest_digest,
)
from app.production import ProductionRepository  # noqa: E402


def command(args: list[str], *, output_paths: list[Path] | None = None) -> subprocess.CompletedProcess[str]:
    del output_paths  # The observer passes this for grant auditing; this script is local-only.
    return subprocess.run(args, check=True, capture_output=True, text=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "acceptance")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            raise SystemExit(f"缺少 {binary}，无法执行真实媒体验收")

    sound = API_ROOT / "app" / "resource_assets" / "openfilm" / "sound" / "glass-pulse" / "sound.m4a"
    item_json = sound.with_name("item.json")
    source = output_dir / "resource-adoption-source.mp4"
    rendered = output_dir / "resource-adoption-glass-pulse.mp4"
    database = output_dir / "resource-adoption-governance.sqlite"
    library = output_dir / "resource-adoption-library"
    evidence = output_dir / "resource-adoption-glass-pulse-2026-11-13.json"
    source.unlink(missing_ok=True)
    rendered.unlink(missing_ok=True)

    command([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x164e63:s=320x240:r=24:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
    ])
    command([
        "ffmpeg", "-v", "error", "-y", "-i", str(source), "-i", str(sound),
        "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,volume=0.85[a]",
        "-map", "0:v:0", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(rendered),
    ])

    catalog = editing_resource_catalog()
    glass = next(item for item in catalog.resources if item.id == "openfilm:sound-glass-pulse")
    glitch = next(item for item in catalog.resources if item.id == "openfilm:transition-glitch")
    preview_endpoint = next(
        route.endpoint for route in build_editing_resource_router(editing_resource_catalog).routes
        if route.path.endswith("/preview")
    )
    preview = preview_endpoint("openfilm:sound-glass-pulse", 1, "media")
    preview_path = Path(preview.path)
    if not preview_path.is_file() or preview.media_type != "audio/mp4":
        raise RuntimeError("Glass Pulse 预览路由没有解析到真实音频文件")

    workspace_id = "resource-acceptance-workspace"
    task_id = "resource-acceptance-task"
    timeline_id = "timeline-resource-acceptance-v1"
    artifact_ref = str(rendered.relative_to(ROOT))
    trace = {
        "assistant_turn_id": "resource-acceptance-turn",
        "request_id": "resource-acceptance-request",
        "job_id": "resource-render-job",
        "output_id": artifact_ref,
    }
    repo = ProductionRepository(database, library)
    store = repo.resource_governance
    source_revision = {"repository": glass.provenance[0].repository, "revision": glass.provenance[0].revision}
    metadata = {
        "catalog_id": glass.id,
        "resource_type": glass.resource_type,
        "path": str(sound.relative_to(ROOT)),
        "source_sha256": sha256(sound),
        "manifest_path": str(item_json.relative_to(ROOT)),
    }
    manifest = ResourceManifest(
        id="manifest-openfilm-sound-glass-pulse-v1", workspace_id=workspace_id,
        resource_id=glass.id, resource_kind="audio", resource_version=str(glass.version), state="connected",
        source_revision=source_revision,
        digest=manifest_digest(resource_id=glass.id, resource_version=str(glass.version), source_revision=source_revision, metadata=metadata),
        licenses=(ResourceLicense("license-glass-pulse", "asset", glass.provenance[0].license_spdx or glass.provenance[0].license, source_ref=str(item_json.relative_to(ROOT))),),
        metadata=metadata,
    )
    store.save_manifest(manifest)
    records = []
    for stage, kwargs in (
        ("adopted", {"task_id": task_id}),
        ("task_input", {"task_id": task_id}),
        ("timeline", {"task_id": task_id, "timeline_version_id": timeline_id}),
        ("render", {"task_id": task_id, "timeline_version_id": timeline_id, "artifact_ref": artifact_ref}),
    ):
        records.append(store.record_usage(ResourceUsageRecord(
            f"usage-glass-pulse-{stage}", workspace_id, manifest.id, manifest.resource_version, stage,
            metadata=trace, **kwargs,
        )))

    observation = observe_output(
        rendered, sample_times=[0.25, 1.0, 1.75], temporary_root=output_dir / "observation-tmp",
        run_command=command, expected_size=(320, 240), max_output_bytes=512 * 1024,
    )
    observation_id = f"observation-{uuid4()}"
    records.append(store.record_usage(ResourceUsageRecord(
        "usage-glass-pulse-output-observation", workspace_id, manifest.id, manifest.resource_version,
        "output_observation", task_id=task_id, timeline_version_id=timeline_id,
        artifact_ref=artifact_ref, observation_ref=observation_id, metadata=trace,
    )))
    store.update_usage_outcome(workspace_id, records[-1].id, outcome="succeeded", reason="真实 ffmpeg 渲染与有界输出观察通过")
    provenance = store.provenance_for_output(workspace_id, artifact_ref, observation_ref=observation_id)
    report = {
        "schema_version": 1,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "resource": {"id": glass.id, "status": glass.status, "resource_kind": manifest.resource_kind, "version": manifest.resource_version, "source_sha256": metadata["source_sha256"], "preview_status": 200, "preview_content_type": preview.media_type, "preview_path": str(preview_path), "preview_bytes": preview_path.stat().st_size},
        "catalog_only_resource_left_unadopted": {"id": glitch.id, "status": glitch.status, "reason": "转场 adapter 仍为 planned，未进入执行阶段"},
        "render": {"source": str(source), "output": str(rendered), "sha256": sha256(rendered), "size_bytes": rendered.stat().st_size, "artifact_ref": artifact_ref, "command": "ffmpeg color+sine + OpenFilm Glass Pulse sound.m4a + amix"},
        "observation": observation,
        "governance": {"workspace_id": workspace_id, "task_id": task_id, "timeline_version_id": timeline_id, "observation_id": observation_id, "stages": [record.stage for record in records], "provenance_for_output": provenance},
        "unverified": ["真实桌面窗口播放", "真实 provider 模型回合", "真实设备播放", "Glass Pulse 音听感主观验收"],
    }
    evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(evidence)
    print(json.dumps({"output": str(rendered), "sha256": report["render"]["sha256"], "stages": report["governance"]["stages"], "sample_count": len(observation["samples"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
