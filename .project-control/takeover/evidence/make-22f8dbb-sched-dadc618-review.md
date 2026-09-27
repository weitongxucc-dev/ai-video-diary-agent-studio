# QA01: MAKE local source-change repair `22f8dbb` with SCHED `dadc618`

## Candidate and verdict

- MAKE `22f8dbbed5eb6c509ff3f49188a163b971be42a0` (parent `7c68f9c`, preceding MAKE repair `5ee08db`), paired in isolated QA archives with SCHED CPU host admission `dadc6188ee62bd7546dda3ab9b1c18ccd88e90f4`.
- **PASS for the reviewed pure-material CPU render subset under an explicit local test budget.** The prior crash/cancel defects and the requested ordinary local same-length source rewrite case are resolved. Production scheduled mode is still disabled; no device-calibrated production budget or runtime registration was delivered.

## Independent verification

- Exact MAKE candidate and SCHED host files were archived under `work/qa-011/output/make-22f8dbb-qa/apps/api`. The QA-only test uses an actual `HostResourceProbe`, `CpuRenderBudget`, grant and one-second FFmpeg media.
- Three independent QA cases passed: (1) after inner montage publication and a single outer checkpoint failure, recovery rendered once and completed the outer checkpoint; (2) cancel immediately before JobRun claim left `device_permit.state='free'`; (3) changing one MP4 byte in place, keeping file length/inode and restoring original `mtime_ns`, caused `scheduler.assert_grant(old_grant)` to reject. The test then deferred the stale request and verified permit release.
- The unmodified author candidate plus SCHED host tests passed **27** related tests. The author source-change branch independently checked `ctime_ns` changed after an equal-length rewrite and that old grant rejected; its independent timeline version case rejected the old grant.

## Contract boundary

`cpu_render_facts` now records `ctime_ns` alongside path, size, `mtime_ns`, inode and device. That detects an ordinary local in-place write even if the mtime is restored. It is a **local mutation signal**, not a content hash, immutable file snapshot or proof against privileged filesystem/metadata rollback. A concurrent rewrite during a read remains outside this reviewed slice. No unpermitted whole-file hash was introduced.

The scheduled API actor still blocks authenticated multiworkspace use, and product `creation_execution_mode='scheduled'` remains off. The QA budget is only a test envelope; it is not production device calibration. Full CreationTask delivery, other heavy paths and packaged desktop acceptance were not tested.
