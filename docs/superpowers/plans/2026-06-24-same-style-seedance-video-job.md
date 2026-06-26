# 同款 Seedance 图生视频任务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn `reference_grid.jpg` from the same-style reference importer into a real video-generation job contract that can submit to Seedance when configured and remains observable when not configured.

**Architecture:** Add a focused `seedance_video_job` module and same-style API routes. Keep the external provider behind an injectable generator so tests do not call the network. Persist every task as `video_job_manifest.json`.

**Tech Stack:** FastAPI, Pydantic, existing same-style reference manifests, optional HTTP provider adapter.

---

### Task 1: Backend Job Contract

- [x] **Step 1: Write failing tests**
  Added tests for configuration-required jobs, missing reference grids, injected generator submission, and POST/GET routes.

- [x] **Step 2: Add schemas**
  Added `SameStyleVideoJobRequest` and `SameStyleVideoJobResponse`.

- [x] **Step 3: Implement job module**
  Added `same_style_video_job.py` with manifest persistence, prompt derivation from `script_draft`, configuration-required status, and injectable generator support.

- [x] **Step 4: Register routes**
  Added `POST /api/same-style/video-jobs` and `GET /api/same-style/video-jobs/{job_id}`.

### Task 2: Configuration and Workflow

- [x] **Step 1: Add runtime settings**
  Added `SEEDANCE_API_KEY`, `SEEDANCE_BASE_URL`, and `SEEDANCE_MODEL` to settings, env updates, and frontend settings forms.

- [x] **Step 2: Expose workflow capability**
  `GET /api/same-style/workflow` now reports Seedance readiness and labels the video stage as HeyGem / Duix Avatar / Seedance.

### Task 3: Frontend Submission

- [x] **Step 1: Add frontend helpers**
  Added `createSameStyleVideoJob` and `getSameStyleVideoJob`.

- [x] **Step 2: Add Server Action**
  Added `startSameStyleVideoJobAction` to submit the current reference job and prompt to the backend.

- [x] **Step 3: Wire page feedback**
  `/same-style-script` now loads `video_job`, renders a video-job banner, and submits `reference_job_id` from the video-generation panel.
