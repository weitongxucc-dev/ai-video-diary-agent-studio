# 同款外链下载器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let external benchmark video links enter the same reference import pipeline when `yt-dlp` is available.

**Architecture:** Keep `reference_video_importer` as the orchestration point. Add a small `YtDlpReferenceDownloader` callable that downloads an external URL into the job workspace, then reuse the existing local-video audio extraction and ASR code path.

**Tech Stack:** Python, FastAPI, existing FFmpeg/ASR helpers, `yt-dlp` executable when installed.

---

### Task 1: Downloader Behavior

**Files:**
- Modify: `apps/api/app/same_style_reference.py`
- Modify: `apps/api/tests/test_same_style_reference.py`

- [x] **Step 1: Write failing test**
  Added a test that injects a fake downloader, writes `downloaded_reference.mp4`, and expects the external URL to continue through audio extraction, ASR, and manifest writing.

- [x] **Step 2: Implement downloader injection**
  `create_reference_job` now accepts an optional downloader callable. External URLs use the injected downloader in tests or `YtDlpReferenceDownloader` in production.

- [x] **Step 3: Implement `YtDlpReferenceDownloader`**
  Detects `yt-dlp` with `shutil.which`, runs `yt-dlp --no-playlist --restrict-filenames -f bv*+ba/b --merge-output-format mp4`, and returns the downloaded video file from the job workspace.

- [x] **Step 3.1: Add project-local fallback**
  Added `yt-dlp` to the API project dependencies and taught the downloader to fall back to `python -m yt_dlp` when the global command is missing.

- [x] **Step 4: Persist waiting jobs**
  External links with missing `yt-dlp` now still write `reference_manifest.json`, so the job id remains readable after refresh.

### Task 2: Capability Status

**Files:**
- Modify: `apps/api/app/same_style_workflow.py`
- Modify: `apps/api/tests/test_same_style_workflow.py`

- [x] **Step 1: Add failing workflow test**
  The workflow response must include a `yt_dlp` capability with `ready` or `missing` status.

- [x] **Step 2: Expose status**
  `GET /api/same-style/workflow` now reports whether either the `yt-dlp` executable or Python `yt_dlp` module is available.

### Task 3: Verification

- [x] **Step 1: Run downloader tests**
  `cd apps/api && .venv/bin/pytest tests/test_same_style_reference.py tests/test_same_style_workflow.py -q`

- [x] **Step 2: Restart API and verify live response**
  Restarted FastAPI and confirmed `/api/same-style/workflow` shows `yt_dlp` as `missing` on this machine, while external URL POST returns `download_required` with a workspace path.

### Task 4: Failure Observability

- [x] **Step 1: Add failing tests for importer errors**
  Covered invalid local video paths, downloader failures, and FFmpeg audio extraction failures.

- [x] **Step 2: Persist failed jobs**
  `create_reference_job` now returns `failed` responses and writes `reference_manifest.json` instead of letting importer errors bubble as API 500s.

- [x] **Step 3: Surface failure details**
  `/same-style-script` now uses the loaded job status, message, and manifest path in the reference import banner.
