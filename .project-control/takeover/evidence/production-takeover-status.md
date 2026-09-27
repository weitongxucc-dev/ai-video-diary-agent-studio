# MAKE01 CPU render handoff

## Candidate and scope

- Worktree: `/Users/Zhuanz/.codex/worktrees/fc12/original`
- Branch: `codex/011-makejob-cpu-render`
- Base: `44ea483`
- Reviewable implementation HEAD: `3aa2464` (preceded by `1b4b231`, `7245555`, `42cc339`, `a816127`)
- Working tree before this handoff file: clean. This handoff file is a separate documentation commit.
- Owned files changed: `apps/api/app/creation_execution.py`, `apps/api/app/main.py`, `apps/api/app/montage.py`, `apps/api/app/audio_timeline.py`, `apps/api/tests/test_creation_execution.py`, `apps/api/tests/test_montage_pipeline.py`, `apps/api/tests/test_audio_timeline.py`.

## Implemented

- For an existing confirmed script, narration artifact, and montage plan, a pure material render request derives stable `render:<digest>` and resource facts, admits through an explicit outer `creation_render` job permit, and runs the existing inner montage render under that same grant. No second device permit is requested for the inner render.
- The granted leaf path registers FFprobe and FFmpeg children, bounds remaining total time, thread count, and output bytes, and checks the finished media before outer success. Interrupted/short montage output becomes a failed inner render.
- The outer JobRun terminal result and stage TaskEvent are persisted together; retry after a forced failure is covered by an integration test using real one-second generated media and the actual grant-aware subprocess path.
- Existing narration/plan inputs are reused. This path does not regenerate upstream model outputs.

## Admission limits and rejected combinations

- CPU render limits must be supplied explicitly to `CreationJobOwnerVerifier`; absent limits keep render as a dependency. The isolated test supplies limits and a permissive synthetic resource probe, so it does **not** establish real host CPU budget admission.
- This subset accepts existing narration plus pure material clips and ordinary captions. It rejects person render/presenter target, beauty/skin processing, graphic overlays, and a non-null `audio_timeline` before requesting a permit. The audio leaf is grant-capable, but full audio-timeline combination is not admitted by this subset.
- Scheduled production runtime registration and calibrated CPU budget are still missing. `creation_execution_mode="scheduled"` still raises at startup. No production activation claim.

## Verification already run

From `/Users/Zhuanz/.codex/worktrees/fc12/original/apps/api`:

```sh
.venv/bin/python -m pytest tests/test_creation_execution.py -q --disable-warnings
# 15 passed
.venv/bin/python -m pytest tests/test_montage_pipeline.py -q -k 'render' --disable-warnings
# 39 passed, 117 deselected
.venv/bin/python -m pytest tests/test_audio_timeline.py -q --disable-warnings
# 18 passed
uvx ruff check app/creation_execution.py app/main.py app/montage.py app/audio_timeline.py tests/test_creation_execution.py tests/test_montage_pipeline.py --select F821,F823,F841 --output-format concise
# pass
git diff --check
# pass
```

QA's read-only montage cancellation/short-output case was reproduced and passed after `42cc339`; its temporary copied test was removed from this worktree. The local `.venv` needed `psutil==7.2.2`, already declared in `pyproject.toml`/`uv.lock`; this produced no repository change.

## Integration and remaining review

1. Integrate with SCHED candidate `dadc618` (`CpuRenderBudget` and `creation_render` probe schema), set an explicit calibrated budget, register `CreationJobOwnerVerifier` and `CreationJobUnitExecutor` in the runtime, then verify admission with the real host probe. Do not activate scheduled mode solely from this candidate.
2. Review/reproduce crash recovery at the gap after inner JobRun publication and before outer checkpoint, proving no duplicate FFmpeg. The existing retry test covers failure before inner publication, not that gap.
3. Independently check full outer cancellation races and multiworkspace behavior. The leaf cancellation/timeout behavior is covered by QA, but there is no full scheduled runtime test.
4. Review the trust assumption in scheduled status reads: they use persisted artifact metadata plus file stat; full hash and FFprobe happen inside the grant before success.
5. Review source-file mutation between admission and use. Source stats enter the digest; there is no immutable file-descriptor snapshot.
6. Broader one-scheduler coverage of manual render, preview, Agent, and other heavy paths remains outside this pure-material CPU subset.

No full test suite, packaged desktop run, or production switch was performed.
