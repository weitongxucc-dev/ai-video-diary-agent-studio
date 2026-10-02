# QA01: integrated MAT scene and SCHED resource monitor `d4519ed`

## Candidate identity and verdict

- Exact integrated candidate: `d4519ed` on parent `a055d1b`. The parent has the same MAT `material_ingestion.py`, `material_probe.py`, `material_scene.py`, and `production.py` blobs as MAT `49fa7b2`, and the same `host_resource_probes.py` and `granted_subprocess.py` blobs as SCHED `7c9414e`. Its earlier feature lineage is MAT `279f492`, MAT refresh `49fa7b2`, SCHED admission `2da0594`, SCHED runtime monitor `7c9414e`.
- **PASS for integration of the bounded scene-window leaf under an explicit calibrated test profile.** Product scene admission remains blocked until a production source/device profile and memory/headroom measurement are configured. Original audio, picture understanding, and authorized retry of terminal v2 probe failures remain outside this slice.

## Independent verification

- Archived the exact `d4519ed` API tree at `work/qa-011/output/mat-sched-d4519ed/apps/api`; did not use the live dirty worktree.
- Ran `uv run --offline --project apps/api pytest -q apps/api/tests/test_device_scheduler.py apps/api/tests/test_device_runtime.py apps/api/tests/test_host_resource_probes.py apps/api/tests/test_material_ingestion.py apps/api/tests/test_material_probe.py apps/api/tests/test_material_scene.py`: **89 passed** in 10.56 seconds.
- The real 32-second long-GOP FFmpeg test now uses a real `HostResourceProbe`, a specified 160×90 MPEG-4 test budget, and grant registration. It resumes only the remaining two windows after restart, assembles cuts without overlap duplication, preserves a matching manual correction and archives a nonmatching one. The test budget explicitly disclaims production calibration.
- New MAT mapping catches `GrantedCommandResourceLimit` before generic `GrantedCommandError`: the scene request becomes failed with `scene_resource_limit` and no scene checkpoint. `GrantedCommandResourceUnavailable` becomes blocked with `resource_pressure` and a one-minute retry, leaves the asset blocked, and commits no checkpoint. Both cases release the scheduler grant in the tests.
- SCHED's real child test covers excess RSS and unavailable psutil measurement after stdout/stderr EOF, proving the wrapper terminates and verifies the process group. MAT's error mapping test injects the exceptions rather than rerunning those real child cases through the full material service; the two boundaries are covered separately.

## Remaining limits

- No production `SceneWindowBudget` is installed; absent or uncalibrated profiles return unknown and cannot grant scene work. The test profile's 64 MiB child RSS cap and 128 MiB reservation are not device peak-memory evidence.
- Runtime RSS/thread sampling is periodic. It mitigates sustained excess but cannot prevent transient peak allocation between samples. Host headroom, timeout and verified process-group cleanup are independent safety bounds.
- The reported frame rate remains a profile selector, not a constant-frame-rate proof or actual decode-frame limit. The invalid nominal-rate-times-window admission bound has been removed.
- The v2 probe recovery is idempotent for historical v1 facts, but a terminal v2 failure still has no trusted manual retry path. A completed scene checkpoint is durable; the full material analysis pipeline is not yet accepted.
