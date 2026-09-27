# MAKE01 CPU render handoff

## Candidate and scope

- Worktree: `/Users/Zhuanz/.codex/worktrees/fc12/original`
- Branch: `codex/011-makejob-cpu-render`
- Base: `44ea483`
- Reviewable implementation HEAD: `22f8dbb` (source-identity follow-up on `5ee08db`; earlier QA candidate `3aa2464`)
- Working tree: clean after implementation commit; this handoff update is a separate documentation commit.
- Owned files changed: `apps/api/app/creation_execution.py`, `apps/api/app/main.py`, `apps/api/app/montage.py`, `apps/api/app/audio_timeline.py`, `apps/api/tests/test_creation_execution.py`, `apps/api/tests/test_montage_pipeline.py`, `apps/api/tests/test_audio_timeline.py`.

## Implemented

- For an existing confirmed script, narration artifact, and montage plan, a pure material render request derives stable `render:<digest>` and resource facts, admits through an explicit outer `creation_render` job permit, and runs the existing inner montage render under that same grant. No second device permit is requested for the inner render.
- The granted leaf path registers FFprobe and FFmpeg children, bounds remaining total time, thread count, and output bytes, and checks the finished media before outer success. Interrupted/short montage output becomes a failed inner render.
- The outer JobRun terminal result and stage TaskEvent are persisted together; retry after a forced failure is covered by an integration test using real one-second generated media and the actual grant-aware subprocess path.
- Existing narration/plan inputs are reused. This path does not regenerate upstream model outputs.
- QA crash-window repair (`5ee08db`): the published timeline version records the original subtitle input and exact outer request. Recovery recomputes the inner digest from current plan data plus that original subtitle input, verifies the successful inner JobRun, transform, output references and published subtitle record, then lets outer validation verify the video. It therefore does not rerun FFmpeg solely because output subtitle tracks appeared after publication.
- QA cancellation repair (`5ee08db`): if the outer JobRun is cancelled between the initial grant check and claim, the executor completes a cancelled checkpoint. Scheduler release still checks all registered children before clearing the permit.
- Source-identity repair: the bounded source manifest includes local `ctime_ns` alongside path, size, mtime, inode and device. The owner recomputes those cheap stats at admission, grant checks and checkpoint. An equal-length local rewrite with restored mtime therefore invalidates the old grant. `ctime_ns` is a change signal, **not** a content hash; no unlicensed full-file hash is run in GET or admission.

## Admission limits and rejected combinations

- CPU render limits must be supplied explicitly to `CreationJobOwnerVerifier`; absent limits keep render as a dependency. The isolated test supplies limits and a permissive synthetic resource probe, so it does **not** establish real host CPU budget admission.
- This subset accepts existing narration plus pure material clips and ordinary captions. It rejects person render/presenter target, beauty/skin processing, graphic overlays, and a non-null `audio_timeline` before requesting a permit. The audio leaf is grant-capable, but full audio-timeline combination is not admitted by this subset.
- Scheduled production runtime registration and calibrated CPU budget are still missing. `creation_execution_mode="scheduled"` still raises at startup. No production activation claim.

## Verification already run

From `/Users/Zhuanz/.codex/worktrees/fc12/original/apps/api`:

```sh
.venv/bin/python -m pytest tests/test_creation_execution.py -q --disable-warnings
# 16 passed
.venv/bin/python -m pytest tests/test_montage_pipeline.py -q -k 'render' --disable-warnings
# 39 passed, 117 deselected
.venv/bin/python -m pytest tests/test_audio_timeline.py -q --disable-warnings
# 18 passed
uvx ruff check app/creation_execution.py app/main.py app/montage.py app/audio_timeline.py tests/test_creation_execution.py tests/test_montage_pipeline.py --select F821,F823,F841 --output-format concise
# pass
git diff --check
# pass
```

The integration test simulates a crash after inner success and before outer checkpoint, then verifies exactly one FFmpeg render across recovery. A separate parameterized branch overwrites one source byte without changing size, restores mtime and retains inode/device, then verifies changed ctime blocks the old grant and it is safely deferred. An independently edited timeline version also blocks recovery; restoring the original version permits reuse. The same test injects cancellation at claim and verifies the permit is released without running the render.

QA's read-only montage cancellation/short-output case was reproduced and passed after `42cc339`; its temporary copied test was removed from this worktree. The local `.venv` needed `psutil==7.2.2`, already declared in `pyproject.toml`/`uv.lock`; this produced no repository change.

## Integration and remaining review

1. Integrate with SCHED candidate `dadc618` (`CpuRenderBudget` and `creation_render` probe schema), set an explicit calibrated budget, register `CreationJobOwnerVerifier` and `CreationJobUnitExecutor` in the runtime, then verify admission with the real host probe. Do not activate scheduled mode solely from this candidate.
2. QA should rerun its real `HostResourceProbe` crash/cancellation cases against `5ee08db` plus SCHED `dadc618`. Local regression covers the exact crash gap and claim cancellation with the synthetic resource probe and real registered FFmpeg children, but the combined SCHED branch remains unverified here.
3. Independently check other outer cancellation races and multiworkspace behavior. The leaf cancellation/timeout behavior is covered by QA, but there is no full scheduled runtime test.
4. Review the trust assumption in scheduled status reads: they use persisted artifact metadata plus file stat; full hash and FFprobe happen inside the grant before success.
5. Source consistency boundary: local stat identity includes ctime and is rechecked at scheduler boundaries, including final checkpoint. It catches ordinary local rewrites even when size and mtime are restored. It is not cryptographic content identity, cannot prove immutability during one FFmpeg read, and may have different guarantees on other filesystems/platforms. There is no immutable file-descriptor snapshot. A stronger content claim would require a licensed full-file verification or immutable staged inputs under the grant.
6. Broader one-scheduler coverage of manual render, preview, Agent, and other heavy paths remains outside this pure-material CPU subset.

No full test suite, packaged desktop run, or production switch was performed.
