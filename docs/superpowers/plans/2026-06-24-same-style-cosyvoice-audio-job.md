# 同款 CosyVoice 音频任务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn same-style script drafts into a trackable audio generation job that can submit to CosyVoice/TTS when configured and remains observable when not configured.

**Architecture:** Add a `cosyvoice_adapter` task module under the same-style API. The module reads the reference job manifest, derives narration text from `script_draft`, submits to an injectable generator, and persists every outcome as `audio_job_manifest.json`.

**Tech Stack:** FastAPI, Pydantic, existing same-style reference manifests, optional HTTP provider adapter.

---

### Task 1: Backend Job Contract

- [x] **Step 1: Write failing tests**
  Added tests for configuration-required jobs, missing script drafts, injected generator audio output, and POST/GET routes.

- [x] **Step 2: Add schemas**
  Added `SameStyleAudioJobRequest` and `SameStyleAudioJobResponse`.

- [x] **Step 3: Implement job module**
  Added `same_style_audio_job.py` with manifest persistence, script text derivation, configuration-required status, and injectable generator support.

- [x] **Step 4: Register routes**
  Added `POST /api/same-style/audio-jobs` and `GET /api/same-style/audio-jobs/{job_id}`.

### Task 2: Configuration and Workflow

- [x] **Step 1: Add runtime settings**
  Added `COSYVOICE_API_KEY`, `COSYVOICE_BASE_URL`, and `COSYVOICE_VOICE_ID` to settings, env updates, and frontend settings forms.

- [x] **Step 2: Expose workflow capability**
  `GET /api/same-style/workflow` now reports CosyVoice readiness and labels the audio stage as a real task-backed adapter.

### Task 3: Frontend Submission

- [x] **Step 1: Add frontend helpers**
  Added `createSameStyleAudioJob` and `getSameStyleAudioJob`.

- [x] **Step 2: Add Server Action**
  Added `startSameStyleAudioJobAction` to submit the current reference job and optional narration text to the backend.

- [x] **Step 3: Wire page feedback**
  `/same-style-script` now loads `audio_job`, renders an audio-job banner, and submits `reference_job_id` from the audio-generation panel.
