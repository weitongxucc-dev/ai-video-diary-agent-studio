# QA01: MAT legacy probe recovery `49fa7b2`

## Frozen candidate and verdict

- Candidate `49fa7b2ae05d8c1bcc0c66f998345b928e4cec92`, parent MAT scene `279f492`; tested with SCHED admission `2da0594` overlaid only in `work/qa-011/output/mat-scene-v2-combined/apps/api`.
- **PASS for the former v1-checkpoint recovery HOLD.** MAT/SCHED scene admission remains **HOLD** on the separate nominal-rate resource issue from `mat-scene-279f492-sched-2da0594-review.md`. A v2 probe failure has no authorized manual retry path yet, so this is not complete media-analysis recovery.

## Evidence

- Historical v1 probe JSON without rate fields is parsed under its original canonical shape and identified as needing refresh. The existing v1 checkpoint and completed request remain untouched. The preferred key for that same source/revision becomes `probe-meta:v2`, giving scheduler submission a distinct executable unit.
- The new test commits a v1 checkpoint, replaces only its result with a canonical old-schema payload, submits and completes a v2 grant, then sees exactly one scene candidate. It checks both checkpoint rows remain, repeated submission does not create another request, a restarted repository chooses v2, user tags survive, and a changed source invalidates scene candidates.
- Current v1 checkpoints with rate fields keep v1. New parsed media with unknown rates has explicit null fields, so it does not trigger a repeating v2 upgrade; scene `check_current` returns dependency. Malformed v1 checkpoints are not silently promoted into a new grant.
- Isolated run: `uv run --offline --project apps/api pytest -q apps/api/tests/test_material_scene.py apps/api/tests/test_material_probe.py apps/api/tests/test_host_resource_probes.py` → **34 passed**.

## Limits

The recovery test uses a synthetic same-source file and injects the historical checkpoint rather than replaying an old deployed database. It exercises repository, scheduler and checkpoint identity, but stubs actual ffprobe execution for the v2 result. The unchanged 32-second real FFmpeg scene/resume test also passes in the same run. Actual cloud/device release integration was not attempted.
