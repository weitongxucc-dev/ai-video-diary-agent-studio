# QA01: SCHED scene runtime monitor `7c9414e`

## Frozen candidate and verdict

- SCHED `7c9414e` (parent `2da0594`) combined in QA only with MAT `49fa7b2`: `work/qa-011/output/mat-sched-7c9414e/apps/api`.
- **PASS for the SCHED monitored-process leaf. HOLD for the MAT/SCHED scene consumer until MAT maps the new resource errors and the joint test uses an actual host probe.** There is no production `SceneWindowBudget` in this candidate, so it does not make scene work runnable on a customer device.

## Evidence

- SCHED-focused run: `uv run --offline --project apps/api pytest -q apps/api/tests/test_device_scheduler.py apps/api/tests/test_device_runtime.py apps/api/tests/test_host_resource_probes.py` → **43 passed**.
- The real negative test starts a grant-registered process that closes stdout/stderr then allocates 80 MB, exceeding a 24 MiB test RSS cap. Both measured-excess and psutil-unavailable variants terminate and reap the process group in under 2 seconds; subsequent scheduler fail/defer releases the grant. Runtime checks continue after both output streams reach EOF.
- `run_granted_command` requires `HostResourceProbe` and a configured scene budget for `material_asset`/`scene-window:v1` before spawning. The monitor checks exact registered child identity, summed live process-tree RSS and thread count, host available memory and swap at an interval capped at 250 ms. An unavailable sample stops the child; uncertain process-group release raises `RecoveryBlocked` through the existing cleanup path.
- The admission check no longer treats ffprobe nominal rate times window duration as a decoded-frame bound. It retains codec, source byte/size/rate profile screening, 15-second scan/sample/output caps, thread cap and 12-second child deadline. No-budget admission returns unknown.

## Integration findings

- Unmodified MAT `test_real_scene_windows_resume_from_checkpoint_and_assemble_source_ranges` still uses an always-true resource stub. On the combined candidate it fails closed at `run_granted_command` with `RecoveryBlocked('场景子进程缺少真实资源探针')`: 64 passed, 1 failed in the joint selection. This is a stale cross-module test setup, not evidence that a real calibrated request is admitted.
- In the QA-only archived test, replacing that stub with a real `HostResourceProbe` and an explicit 160×90 MPEG-4 test profile makes the same real 32-second FFmpeg/resume test pass (1 passed). This profile was chosen for test integration; it is not a measured production calibration.
- MAT `run_granted_scene` currently catches both new `GrantedCommandResourceLimit` and `GrantedCommandResourceUnavailable` through generic `GrantedCommandError`, marking `scene_failed` terminal. The latter is an environmental sampling failure and should have a distinct defer path. The next MAT exception-mapping candidate must be reviewed with a real grant and released process group.

## Practical limit

Sampling bounds detection latency to at most the configured 250 ms plus scheduling delay; it cannot promise zero transient RSS overshoot or count a child that has escaped the observed process tree between samples. The timeout and process-group cleanup provide separate bounds. The repository contains only the 160×90 MPEG-4 test profile; no supported source profile, measured peak memory/headroom margin, or production budget has been supplied for product acceptance.
