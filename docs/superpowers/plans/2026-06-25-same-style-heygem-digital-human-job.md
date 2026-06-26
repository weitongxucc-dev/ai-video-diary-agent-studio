# Same-Style HeyGem/Duix Digital Human Job

## Scope

Add a task contract for generating a talking-head video from an existing same-style audio job. The task reads `voice.wav` from a CosyVoice audio manifest, submits it to a configurable HeyGem/Duix-compatible HTTP endpoint, and persists every outcome as `digital_human_job_manifest.json`.

## Implemented

1. Backend schema
   Added `SameStyleDigitalHumanJobRequest` and `SameStyleDigitalHumanJobResponse`.

2. Backend module
   Added `apps/api/app/same_style_digital_human_job.py` with injectable provider support, configuration-required handling, missing-audio failure handling, manifest persistence, and manifest readback.

3. Backend routes
   Added `POST /api/same-style/digital-human-jobs` and `GET /api/same-style/digital-human-jobs/{job_id}`.

4. Runtime configuration
   Added `HEYGEM_API_KEY`, `HEYGEM_BASE_URL`, and `HEYGEM_AVATAR_ID` to backend settings, runtime settings updates, and frontend settings/setup forms.

5. Workflow status
   Added a `heygem` capability and made the video stage reflect either Seedance or HeyGem availability.

6. Frontend workbench
   Added digital-human job types, API helpers, server action, query loading, result banner, and a dedicated "数字人口播视频" production panel.

## Verification

- `cd apps/api && .venv/bin/pytest -q`
- `corepack pnpm --dir apps/web lint`
- `corepack pnpm --dir apps/web exec tsc --noEmit`
- `node apps/web/scripts/workflow-contract-check.mjs && node apps/web/scripts/pixel-style-contract-check.mjs && node apps/web/scripts/same-style-contract-check.mjs`
