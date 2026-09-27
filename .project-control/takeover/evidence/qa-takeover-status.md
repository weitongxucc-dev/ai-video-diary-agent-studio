# QA01 0.11 candidate takeover status

This status is an evidence handoff, not a product acceptance verdict. QA wrote no product source. Reports and isolated archives are under `/Users/Zhuanz/.codex/worktrees/26aa/original/work/qa-011/`.

## Exact candidate verdicts

| Slice | Frozen candidate | QA verdict | Evidence |
| --- | --- | --- | --- |
| MAKE CPU montage repair | `42cc339f8d63bc73245856b11aad12dceba0b159` (includes audio parent `7245555`) | CPU montage leaf PASS; main CreationTask consumer not submitted/accepted | `qa-011/make-cpu-42cc339-review.md`; real live-cancel QA case plus montage suite 157 passed |
| MAT scene original | `279f4924ffb887aa486635633eb2f7d932c6e5a4` | 14s/overlap/restart/manual-correction path PASS; original v1 recovery HOLD | `qa-011/mat-scene-279f492-sched-2da0594-review.md` |
| MAT old-checkpoint repair | `49fa7b2ae05d8c1bcc0c66f998345b928e4cec92` | v1→v2 same-source permitted re-probe PASS; terminal v2 retry still missing | `qa-011/mat-probe-v2-49fa7b2-review.md`; 34 combined related tests passed |
| SCHED scene admission and monitor | `2da05945c2258551599c42bd531c3b9abf3ca85d` + repair `7c9414e` | Monitored-process leaf PASS; product calibration HOLD | `qa-011/sched-scene-7c9414e-review.md`; 43 SCHED tests passed; unmodified pre-repair MAT/SCHED 64 passed/1 test setup failure |
| MAT/SCHED joint scene repair | `d4519ed` on `a055d1b` (parent MAT blobs equal `49fa7b2`, SCHED blobs equal `7c9414e`) | Bounded scene-window integration PASS under explicit test profile; product admission still blocked without calibration | `qa-011/mat-sched-d4519ed-joint-review.md`; six relevant suites 89 passed, including real long-GOP resume and mapped Limit/Unavailable no-checkpoint cases |
| AUTH pre-provisioned device invitations | `422cd4248c2a762a4024165627b66c04e39be8b3` | SQL/RPC slice PASS; device registration/native proof/first-invitee local entry missing | `qa-011/auth-422cd42-review.md`; isolated PostgreSQL 6 passed |

## Current scene blockers

1. MAT `d4519ed` replaced the always-true resource stub in its real FFmpeg scene test with `HostResourceProbe` and an explicit 160×90 MPEG-4 test profile. It also maps measured resource limit to terminal failure and unavailable host/process samples to deferred resource pressure; neither writes a scene checkpoint. The exact integrated tree passed 89 relevant tests.
2. No production `SceneWindowBudget` or measured device/source profile is configured. The test budget is not production calibration. Unknown or uncalibrated sources remain blocked by design.
3. FFprobe avg/r frame rates screen a profile, but are not a VFR decoded-frame cap. SCHED `7c9414e` removes the invalid `rate × window` hard-bound assertion and adds runtime RSS/thread/host pressure checks, 250 ms maximum configured sampling interval, 12s deadline, and verified child-group cleanup. Peak overshoot between samples still depends on calibrated memory margin.
4. The remaining MAT product work includes trusted retry for terminal v2 probe failures, original audio and picture understanding, and full material-analysis acceptance. MAKE main CreationTask consumer and AUTH native device/first-invitee entry are also outside the reviewed slices.

## QA scope and coordination

Main thread: `01a0d212-4a77-74f3-af12-e7767cba6e03`. SCHED delegation source: `01a0d29d-226b-7460-8ba6-76f14dc8c56a`. All key findings were messaged to both where relevant. Do not treat any report as a cherry-pick request or deploy/cloud authorization.
