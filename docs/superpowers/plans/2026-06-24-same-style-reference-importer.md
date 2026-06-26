# 同款参考导入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the 文案生成 block in `/same-style-script` into a real reference-video import entry point.

**Architecture:** Keep the workflow contract endpoint, add a focused `reference_video_importer` module and POST route. The first real slice supports local benchmark videos under configured media directories, extracts `audio_16k.wav`, optionally runs ASR, writes a manifest, and returns a clear `download_required` status for external Douyin links until a downloader adapter is added.

**Tech Stack:** FastAPI, Pydantic, existing FFmpeg command helpers, existing ASR abstraction, Next.js Server Actions.

---

### Task 1: Backend Reference Importer

**Files:**
- Create: `apps/api/app/same_style_reference.py`
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/routers/same_style.py`
- Test: `apps/api/tests/test_same_style_reference.py`
- Test: `apps/api/tests/test_api_modularity.py`

- [x] **Step 1: Write failing tests**
  Added tests for external-link `download_required`, local video audio extraction + ASR manifest, and POST route response.

- [x] **Step 2: Implement request/response schemas**
  Added `SameStyleReferenceJobRequest`, `SameStyleReferenceJobResponse`, and shot manifest item response models.

- [x] **Step 3: Implement `create_reference_job`**
  Resolves local videos under configured roots, extracts audio with FFmpeg, optionally transcribes via ASR, writes `reference_manifest.json`, and returns typed job output.

- [x] **Step 4: Register POST route**
  Added `POST /api/same-style/reference-jobs` to `apps/api/app/routers/same_style.py`.

- [x] **Step 5: Register GET route**
  Added `GET /api/same-style/reference-jobs/{job_id}` to read saved manifests back into the workbench.

- [x] **Step 6: Generate same-style script draft**
  Reference jobs with ASR transcript now include `script_draft` with hook, sections, title options, cover copy, publish copy, and comment prompt.

- [x] **Step 7: Extract script generator adapter**
  Added `same_style_script_generator` with DeepSeek structured JSON generation when configured and a deterministic fallback when the model is unavailable.

- [x] **Step 8: Persist observable failures**
  Invalid local paths, downloader errors, and FFmpeg extraction errors now return `failed` jobs and write manifests for debugging instead of bubbling as untracked server errors.

- [x] **Step 9: Extract reference frames**
  Added a shared FFmpeg frame extraction command and saved `reference_frames` into the job response and manifest for later multi-frame reference images.

- [x] **Step 10: Build reference grid image**
  Added a Pillow contact-sheet builder that saves `reference_grid.jpg` when extracted frames are valid, and surfaces that path in API/front-end state.

### Task 2: Frontend Submission

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/app/actions.ts`
- Modify: `apps/web/src/app/same-style-script/page.tsx`
- Modify: `apps/web/src/app/globals.css`

- [x] **Step 1: Add frontend types and API helper**
  Added `SameStyleReferenceJobRequest`, `SameStyleReferenceJob`, and `createSameStyleReferenceJob`.

- [x] **Step 2: Add Server Action**
  Added `startSameStyleReferenceJobAction` to submit form data to the API and redirect back with job status.

- [x] **Step 3: Wire 文案生成 form**
  Made the 文案生成 panel a real form and added a result banner for `transcribed`, `audio_ready`, `download_required`, and `failed`.

- [x] **Step 4: Read saved job after redirect**
  The page reads `reference_job` from search params, fetches the manifest, and fills 对标文案 / 仿写文案 from `transcript` and `script_draft`.

### Task 3: Verification

- [x] **Step 1: Backend tests**
  `cd apps/api && .venv/bin/pytest tests/test_same_style_reference.py tests/test_same_style_workflow.py tests/test_api_modularity.py -q`

- [x] **Step 2: Frontend checks**
  `corepack pnpm --dir apps/web lint`
  `corepack pnpm --dir apps/web exec tsc --noEmit`

- [x] **Step 3: Browser flow**
  Submitted a Douyin URL from `/same-style-script` and verified the page redirects with `reference_status=download_required` and shows the result banner.
