# QA01: MAT scene `279f492` + SCHED scene admission `2da0594`

## Frozen candidate and verdict

- MAT `279f4924ffb887aa486635633eb2f7d932c6e5a4`; SCHED `2da05945c2258551599c42bd531c3b9abf3ca85d` on CPU predecessor `dadc618`. The combined API was assembled only in `work/qa-011/output/mat-scene-combined/apps/api`.
- **HOLD for scene admission/integration**, pending a legacy checkpoint recovery route and a defensible bound for actual VFR decode work. The 14-second window implementation and restart behavior pass their tested contract. Full media analysis remains blocked because original audio and picture understanding are not implemented here.

## Passing evidence

- `scene_windows(32.4)` yields logical `[0,14)`, `[14,28)`, `[28,32.4)` with scan durations 14, 15, 5 seconds. `parse_scene_showinfo` only keeps a cut in its logical interval, so an overlap cut at 14 belongs to the later window. A set removes duplicate timestamps within a window; the disjoint logical intervals remove cross-window duplicates.
- The real FFmpeg test creates a 32-second, 5 fps MPEG-4 source with GOP 250 and cuts at 5 and 20 seconds. After first-window checkpoint and repository restart, only two windows remain; assembly yields `[0,5)`, `[5,17)`, `[17,20)`, `[20,32)`. It retains a matching human description and saves a nonmatching correction to `material_unmatched_corrections`.
- Candidate resources match SCHED field names and configured maxima. Without a configured `SceneWindowBudget`, admission returns unknown. With the test budget, bounded case admits and overwidth, overrate, overscan, missing rate and undersized sample-frame allowance are rejected or unknown as appropriate.
- Isolated run: `uv run --offline --project apps/api pytest -q apps/api/tests/test_material_scene.py apps/api/tests/test_material_probe.py apps/api/tests/test_host_resource_probes.py` → **33 passed**. The reported 43 tests were not reproduced as one claimed set; the actual combined selection above is explicit.

## HOLD 1: completed v1 metadata has no recovery path

`MaterialProbeFacts.from_checkpoint_json` now requires `avg_frame_rate_fps` and `r_frame_rate_fps` in each stream. Previously completed `probe-meta:v1` checkpoints omit them. `pending_material_scene_dependencies` silently skips those checkpoints on `ValueError`; `_material_scene_source` also rejects them. The probe unit key remains `probe-meta:v1`, and the existing request is already complete, so periodic submission does not re-execute that metadata stage for the same source. A previously ingested unchanged file therefore cannot reach scene work. Give the revised probe facts a new stage identity with an explicit replay/migration, or another authorized re-probe route, and test an old completed checkpoint through recovery.

## HOLD 2: nominal rate does not bound decode frames

MAT sets `source_frame_rate_fps` only when rounded ffprobe `avg_frame_rate` equals rounded `r_frame_rate`. SCHED uses this value times `scan_seconds` as `max_nominal_decode_frames`. Equality of two container/stream rate descriptors does not establish constant frame rate or a maximum number of frames inside each 15-second window; rounding to three decimals weakens even descriptor equality. `fps=2,trim=end_frame=30` bounds *output samples after decoding*, not input frames decoded. The 12-second subprocess timeout limits wall time after launch, but does not validate the promised decode-frame/memory admission bound. Require a verified source-window decode limit or reframe the budget as a measured/calibrated codec, size, time and memory cap without asserting a frame bound.

## Limits

No real VFR stress source was executed in this pass. The rate issue follows from the admitted inputs and the filter order, and needs a fix plus an adversarial fixture before PASS. The restart test uses a permissive resource stub; the paired SCHED test is a separate unit admission check, not an end-to-end host-load measurement.
