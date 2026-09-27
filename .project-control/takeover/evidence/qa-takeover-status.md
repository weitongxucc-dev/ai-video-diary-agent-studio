# QA01 0.11 candidate takeover status

This status is an evidence handoff, not a product acceptance verdict. QA wrote no product source. Reports and isolated archives are under `/Users/Zhuanz/.codex/worktrees/26aa/original/work/qa-011/`.

## Exact candidate verdicts

| Slice | Frozen candidate | QA verdict | Evidence |
| --- | --- | --- | --- |
| MAKE CPU montage repair | `42cc339f8d63bc73245856b11aad12dceba0b159` (includes audio parent `7245555`) | CPU montage leaf PASS; outer consumer reviewed separately | `qa-011/make-cpu-42cc339-review.md`; real live-cancel QA case plus montage suite 157 passed |
| MAKE outer CPU render integration | `3aa24649b53b500daf92908f4e7a6f5b48f686df` paired in QA with SCHED `dadc6188ee62bd7546dda3ab9b1c18ccd88e90f4` | HOLD: completed inner render repeated after outer checkpoint crash; cancel/claim race strands held permit | `qa-011/make-3aa2464-sched-dadc618-review.md`; original+host 26 passed, two QA negatives fail under real HostResourceProbe test budget |
| MAKE outer recovery repair | `5ee08db524073afff2765e01372db253b33f7c8f` paired with SCHED `dadc6188ee62bd7546dda3ab9b1c18ccd88e90f4` | Previous two HOLD defects fixed; source-byte binding HOLD for equal-size/same-mtime edit | `qa-011/make-5ee08db-sched-dadc618-review.md`; 26 author+host passed, real-host QA crash/cancel pass, content negative fails |
| MAKE local source-change repair | `22f8dbbed5eb6c509ff3f49188a163b971be42a0` paired with SCHED `dadc6188ee62bd7546dda3ab9b1c18ccd88e90f4` | Reviewed pure-material CPU subset PASS under explicit local test budget; production scheduled mode remains off | `qa-011/make-22f8dbb-sched-dadc618-review.md`; three real-host QA cases pass, author+host 27 passed; ctime is local mutation signal only |
| MAT scene original | `279f4924ffb887aa486635633eb2f7d932c6e5a4` | 14s/overlap/restart/manual-correction path PASS; original v1 recovery HOLD | `qa-011/mat-scene-279f492-sched-2da0594-review.md` |
| MAT old-checkpoint repair | `49fa7b2ae05d8c1bcc0c66f998345b928e4cec92` | v1→v2 same-source permitted re-probe PASS; terminal v2 retry still missing | `qa-011/mat-probe-v2-49fa7b2-review.md`; 34 combined related tests passed |
| SCHED scene admission and monitor | `2da05945c2258551599c42bd531c3b9abf3ca85d` + repair `7c9414e` | Monitored-process leaf PASS; product calibration HOLD | `qa-011/sched-scene-7c9414e-review.md`; 43 SCHED tests passed; unmodified pre-repair MAT/SCHED 64 passed/1 test setup failure |
| MAT/SCHED joint scene repair | `d4519ed536ab5535338bd8b76f934083449d63d3` on `a055d1b7d7d7111832c4eda0c13d725ba54ea15a` (parent MAT blobs equal `49fa7b2`, SCHED blobs equal `7c9414e`) | Bounded scene-window integration PASS under explicit test profile; product admission still blocked without calibration | `qa-011/mat-sched-d4519ed-joint-review.md`; six relevant suites 89 passed, including real long-GOP resume and mapped Limit/Unavailable no-checkpoint cases |
| AUTH pre-provisioned device invitations | `422cd4248c2a762a4024165627b66c04e39be8b3` | SQL/RPC slice PASS; device registration/native proof/first-invitee local entry missing | `qa-011/auth-422cd42-review.md`; isolated PostgreSQL 6 passed |

## Current scene blockers

1. MAT `d4519ed` replaced the always-true resource stub in its real FFmpeg scene test with `HostResourceProbe` and an explicit 160×90 MPEG-4 test profile. It also maps measured resource limit to terminal failure and unavailable host/process samples to deferred resource pressure; neither writes a scene checkpoint. The exact integrated tree passed 89 relevant tests.
2. No production `SceneWindowBudget` or measured device/source profile is configured. The test budget is not production calibration. Unknown or uncalibrated sources remain blocked by design.
3. FFprobe avg/r frame rates screen a profile, but are not a VFR decoded-frame cap. SCHED `7c9414e` removes the invalid `rate × window` hard-bound assertion and adds runtime RSS/thread/host pressure checks, 250 ms maximum configured sampling interval, 12s deadline, and verified child-group cleanup. Peak overshoot between samples still depends on calibrated memory margin.
4. The remaining MAT product work includes trusted retry for terminal v2 probe failures, original audio and picture understanding, and full material-analysis acceptance. MAKE `22f8dbb` passes the reviewed local CPU subset, while production scheduled mode and calibration remain closed. Its ctime check is not a content hash or concurrent-read snapshot. AUTH native device/first-invitee entry is outside its reviewed SQL slice.

## QA scope and coordination

Main thread: `01a0d212-4a77-74f3-af12-e7767cba6e03`. SCHED delegation source: `01a0d29d-226b-7460-8ba6-76f14dc8c56a`. All key findings were messaged to both where relevant. Do not treat any report as a cherry-pick request or deploy/cloud authorization.
