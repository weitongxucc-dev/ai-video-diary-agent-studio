# Same-Style Subtitle Job

## Scope

Add a task contract for generating `subtitle.srt` inside the same-style workflow. The task uses the rewritten narration text from the audio job when available, and falls back to the reference job's `script_draft`.

## Implemented

1. Backend schema
   Added `SameStyleSubtitleJobRequest` and `SameStyleSubtitleJobResponse`.

2. Backend module
   Added `apps/api/app/same_style_subtitle_job.py` with SRT generation, manifest persistence, missing-source failure handling, and manifest readback.

3. Backend routes
   Added `POST /api/same-style/subtitle-jobs` and `GET /api/same-style/subtitle-jobs/{job_id}`.

4. Frontend workbench
   Added subtitle job types, API helpers, server action, query loading, result banner, and a real "字幕生成" production panel.

## Verification

- `cd apps/api && .venv/bin/pytest tests/test_same_style_subtitle_job.py tests/test_api_modularity.py -q`
- `corepack pnpm --dir apps/web lint`
- `corepack pnpm --dir apps/web exec tsc --noEmit`
- `node apps/web/scripts/same-style-contract-check.mjs`
