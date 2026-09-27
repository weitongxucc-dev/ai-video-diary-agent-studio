# QA01: MAKE CPU outer execution `3aa2464` with SCHED CPU admission `dadc618`

## Candidate and verdict

- MAKE implementation `3aa24649b53b500daf92908f4e7a6f5b48f686df` (parent `a816127`); SCHED CPU contract `dadc6188ee62bd7546dda3ab9b1c18ccd88e90f4`. MAKE does **not** contain the SCHED host-probe code blob, so QA archived MAKE and overlaid SCHED's `host_resource_probes.py`/host tests only under `work/qa-011/output/make-3aa2464-sched-dadc618/apps/api`.
- **HOLD** for integration of the authorized pure-material CPU render subset. Two crash/cancel invariants fail in independent QA negatives. Production scheduled mode remains disabled and no calibrated budget or runtime registration was delivered.

## Evidence

- Original `test_creation_execution.py` plus SCHED `test_host_resource_probes.py` on the isolated combination: **26 passed**. The author's 1-second FFmpeg test submits an outer `render:<digest>` request, retries a failure before inner publication, and renders through a grant, but uses a synthetic always-true resource probe.
- QA-only modification of that same test replaces the synthetic probe with a real `HostResourceProbe` and an explicit `CpuRenderBudget` for this 1-second 1080×1920 fixture. It reaches a real grant and FFmpeg. The test budget is for integration evidence, not product calibration.
- The workspace and actor checks in `CreationJobOwnerVerifier._facts` use `workspace_id` on JobRun, task, script, plan and event lookups; media references resolve through the requested workspace. Existing wrong-workspace start test passes. Actual scheduled API actor acquisition rejects authenticated multi-member use and non-legacy workspaces, so multiworkspace scheduled execution is **not** acceptance-tested.

## HOLD 1: outer crash re-renders a completed inner job

The QA-only test injects a single exception from the outer `complete_unit` checkpoint after the inner montage JobRun has status `succeeded` and its media is published. On re-entering `CreationJobUnitExecutor.execute` with the still-held grant, `_render_montage_with_resolved_clip_sources` is called **twice**. The assertion expects one call and fails (`[1, 1]`). This also reproduces with the real host probe. After first publication the current timeline gains subtitle tracks, so `_render_creation_task_montage_for_workspace` recomputes a different input digest and misses its existing successful inner job. The author's retry test only covers failure before inner publication.

## HOLD 2: cancellation between grant validation and JobRun claim strands permit

The QA-only test simulates the API's first durable cancel step, setting the JobRun to `cancelled` after executor `scheduler.assert_grant` but immediately before `claim_job_run`. The claim raises `ValueError` because a cancelled job cannot be claimed. Executor `finally` clears only its in-memory cancellation map; `device_permit.state` remains **`held`** rather than `free`. The failing QA assertion observes the persisted held permit after the executor returns. The separate later cancellation checks and leaf child cleanup do not cover this interval.

## Other limits

- Outer scheduled runtime registration and production `CpuRenderBudget` are absent; `creation_execution_mode='scheduled'` remains disabled at startup. The QA real-host budget only demonstrates compatibility with SCHED `dadc618` for this fixture.
- The scheduled status read skips full hash/FFprobe and trusts persisted artifact metadata plus current file size; a post-publication same-size mutation is not detected by that status read. Full validation does occur inside the grant before outer success.
- Source facts bind path, size, mtime, inode and device at admission, with repeated checks before the grant. There is no immutable source descriptor or final content hash check spanning FFmpeg execution, so source replacement during a live render was not verified here.
- No full API cancel race, multiworkspace scheduled run, packaged desktop run or product acceptance was claimed.
