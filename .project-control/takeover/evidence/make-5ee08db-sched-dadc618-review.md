# QA01: MAKE CPU recovery repair `5ee08db` with SCHED `dadc618`

## Candidate and verdict

- MAKE `5ee08db524073afff2765e01372db253b33f7c8f` (parent `8d8050d`, implementation predecessor `3aa2464`), paired in QA archives with SCHED CPU host-probe contract `dadc6188ee62bd7546dda3ab9b1c18ccd88e90f4`. No product source was edited.
- **The two earlier MAKE HOLD defects are fixed. The requested source-byte change invariant remains HOLD for equal-size, same-mtime edits.** Scheduled production mode stays disabled, without a configured device budget/runtime registration.

## Verified fixes

- Author candidate plus SCHED host tests: **26 passed** on the frozen combined tree.
- QA's original crash negative was rerun against the fixed product files using an actual `HostResourceProbe` and explicit one-second `CpuRenderBudget`. After the inner montage JobRun was `succeeded` and the outer checkpoint callback failed once, re-entering the same grant left the FFmpeg render-call count at **one**. The recovery branch binds the saved original subtitle inputs, inner job, transform, output refs and artifact metadata; the outer checkpoint then completes.
- QA's cancel-before-claim negative, adapted to inspect persisted state after a normal cancelled return, passes: the JobRun cancellation injected immediately before `claim_job_run` leaves `device_permit.state='free'`. The executor's new handler calls `complete_unit(cancelled=True)`, which still verifies registered child release before freeing the permit.
- The candidate's size-changing source edit and independently created timeline version both reject the old grant. Its full real-host integration case passed in the QA-only copy. The QA budget is test evidence, not production calibration.

## Remaining HOLD: source content is not bound

`cpu_render_facts` hashes each source's path, size, `mtime_ns`, inode and device, but not its bytes or a trusted content digest. QA changed one actual byte of the source MP4 in place, kept the length and inode, and restored the original `mtime_ns`; `scheduler.assert_grant(old_grant)` **did not reject**. The QA expectation for `PermissionError` failed. This is a content change, not a metadata-only edit; `ctime_ns` changed but is not in the manifest. The same admission gap applies if bytes change between checks during a live render. A content identity or immutable input snapshot is needed if the permit must bind actual source bytes.

## Scope

- The scheduled API actor still only permits legacy local identity; authenticated multiworkspace scheduled execution is blocked. Workspace-filtered repository checks were inspected but no product multiworkspace run was accepted.
- The QA real-host test used explicit synthetic limits for one 1080×1920, one-second render. Production `CpuRenderBudget`, runtime registration, package run, and broad heavy-path scheduling were not verified.
- Artifact and media integrity are checked within the grant before outer success. The scheduled status read's lighter same-size check remains a separate product limitation.
