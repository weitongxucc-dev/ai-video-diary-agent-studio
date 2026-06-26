# Same-Style Publish Asset Jobs

## Scope

Finish the non-provider publish asset stages for the same-style workflow: title/publish copy, cover image, and a safe publish package for manual review.

## Implemented

1. Publish copy job
   Added `publish_copy_generator` schemas, backend module, routes, frontend API helpers, server action, result banner, and a real title generation panel.

2. Cover job
   Added `cover_artifact_writer` schemas, backend module, routes, frontend API helpers, server action, result banner, and a real cover generation panel. The implementation reuses the existing Pillow pixel cover renderer.

3. Publish package job
   Added `social_auto_upload_adapter` schemas, backend module, routes, frontend API helpers, server action, result banner, and a publish panel that writes a `ready_for_review` manifest instead of auto-posting without human approval.

## Verification

- `cd apps/api && .venv/bin/pytest tests/test_same_style_publish_copy_job.py tests/test_same_style_cover_job.py tests/test_same_style_publish_job.py tests/test_api_modularity.py -q`
- `corepack pnpm --dir apps/web lint`
- `corepack pnpm --dir apps/web exec tsc --noEmit`
- `node apps/web/scripts/same-style-contract-check.mjs`
