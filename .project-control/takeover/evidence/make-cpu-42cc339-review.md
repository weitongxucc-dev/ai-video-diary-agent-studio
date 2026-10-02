# QA01: MAKE CPU render repair `42cc339`

## Frozen candidate and verdict

- Candidate: `42cc339f8d63bc73245856b11aad12dceba0b159` (includes audio leaf parent `7245555`), archived at `work/qa-011/output/make-cpu-42cc339/apps/api`.
- **PASS for the CPU montage leaf and the two prior HOLD defects.** This is not an acceptance verdict for the unsubmitted `main.py` / `creation_execution.py` consumer path or complete CreationTask delivery.

## Evidence

- QA-only `test_qa_montage_grant.py` starts a real, grant-registered fake FFmpeg process group and cancels it while live. It now passes: the group is released and `plan.status != "rendering"`. The repair catches the runtime cancellation and marks a granted plan `failed` before rethrowing.
- `test_montage_pipeline.py` generates a real FFmpeg H.264/AAC output and verifies that a 1-second output is rejected against a 5-second plan. The repair checks **both** stream durations; a missing or nonfinite duration is rejected. The `+0.1s` tolerance is narrow enough for the demonstrated truncation case, though exact frame/audio rounding near the limit has not been exhaustively tested.
- Isolated run: `uv run --offline --project apps/api pytest -q apps/api/tests/test_qa_montage_grant.py apps/api/tests/test_montage_pipeline.py` → **157 passed**. No whole-suite or application-flow claim.

## Scope

The output validator establishes minimum audio/video stream duration and expected canvas/codecs. It does not attest semantic timeline content. The main task execution consumer and atomic publish remain separate pending candidate review.
