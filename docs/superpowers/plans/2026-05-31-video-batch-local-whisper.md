# Video Batch And Local Whisper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Process ordered batches of short recorded clips into one draft-ready video and automatically fall back to the existing local Whisper installation when Tencent Cloud ASR is unavailable.

**Architecture:** Extend the ad hoc Episode model with an ordered list of source files and a dedicated `ad_hoc_video_batch` mode. The FFmpeg processor first normalizes and concatenates batch inputs, then reuses the existing conservative silence-cut and speed pipeline. Draft generation uses a fallback ASR transcriber chain: Tencent Cloud first, then local `openai-whisper` through the existing `声迹` virtual environment and `small.pt` model.

**Tech Stack:** FastAPI, dataclasses, SQLite JSON persistence, FFmpeg, openai-whisper CLI module, pytest, Next.js server actions, TypeScript.

---

### Task 1: Local Whisper Fallback

**Files:**
- Modify: `apps/api/app/config.py`
- Modify: `apps/api/app/asr.py`
- Modify: `apps/api/app/drafts.py`
- Modify: `apps/api/app/schemas.py`
- Test: `apps/api/tests/test_tencent_asr.py`
- Test: `apps/api/tests/test_draft_artifacts.py`

- [ ] Add configurable local Whisper Python and model paths, auto-detected by `Settings.from_env()`.
- [ ] Add `LocalWhisperTranscriber` that runs `python -m whisper` and parses the generated SRT.
- [ ] Add `FallbackASRTranscriber` that tries Tencent Cloud and then local Whisper.
- [ ] Use the fallback chain by default in `DraftWorkspace`.
- [ ] Record `local_whisper` as the subtitle source when local transcription succeeds.

### Task 2: Ordered Video Batch Processing

**Files:**
- Modify: `apps/api/app/video.py`
- Test: `apps/api/tests/test_video_pipeline.py`

- [ ] Add `process_batch(file_names)` with at least two validated inbox videos.
- [ ] Normalize resolution, frame rate, and audio before FFmpeg concat.
- [ ] Feed the merged source through the existing silence-cut, speed, and loudness pipeline.
- [ ] Record ordered source files in the edit plan and processed video result.

### Task 3: Batch Episode API

**Files:**
- Modify: `apps/api/app/models.py`
- Modify: `apps/api/app/repository.py`
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/services.py`
- Modify: `apps/api/app/workflow.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_episode_flow.py`

- [ ] Add `ad_hoc_video_batch` intake mode and ordered `video_source_file_names`.
- [ ] Persist and expose the ordered list.
- [ ] Add `POST /api/episodes/{episode_id}/process-ad-hoc-video-batch`.
- [ ] Clear planned topic/script context and generate one honest draft package.

### Task 4: Frontend Batch Entry

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/app/actions.ts`
- Modify: `apps/web/src/app/video/page.tsx`
- Modify: `apps/web/src/app/settings/page.tsx`

- [ ] Add TypeScript support for batch mode and ordered source files.
- [ ] Add a batch API client and server action.
- [ ] Add checkboxes and `合并处理已选短视频` on the video page.
- [ ] Show local Whisper status in settings.

### Task 5: Verification

**Files:**
- No code changes.

- [ ] Run targeted red-green tests for local Whisper and batch processing.
- [ ] Run `corepack pnpm test:api`.
- [ ] Run TypeScript, lint, frontend contract, pixel-style contract, and production build checks.
- [ ] Run a real local Whisper transcription against today’s preview and regenerate the ad hoc draft package with real subtitles.
