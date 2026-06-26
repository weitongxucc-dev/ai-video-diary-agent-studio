# Same-Style BGM Job

## Scope

Add a task contract for the same-style BGM stage. The job reads a completed digital-human or Seedance video task, either records that BGM is skipped or mixes a local BGM file into the video with FFmpeg, and persists `bgm_job_manifest.json`.

## Implemented

1. Media command
   Added `build_mix_bgm_command()` using FFmpeg `amix` with configurable BGM volume.

2. Backend schema
   Added `SameStyleBgmJobRequest` and `SameStyleBgmJobResponse`.

3. Backend module
   Added `apps/api/app/same_style_bgm_job.py` with source-video resolution, skip-BGM handling, local BGM mix handling, FFmpeg runner injection, failure manifests, and manifest readback.

4. Backend routes
   Added `POST /api/same-style/bgm-jobs` and `GET /api/same-style/bgm-jobs/{job_id}`.

5. Frontend workbench
   Added BGM job types, API helpers, server action, query loading, result banner, and a real BGM production panel.

## Verification

- `cd apps/api && .venv/bin/pytest tests/test_same_style_bgm_job.py tests/test_media_commands.py tests/test_api_modularity.py -q`
- `corepack pnpm --dir apps/web lint`
- `corepack pnpm --dir apps/web exec tsc --noEmit`
- `node apps/web/scripts/same-style-contract-check.mjs`
