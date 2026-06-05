# Ad Hoc Video Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support already-recorded videos that skip topic and script generation, while keeping the manifest honest about missing ASR or script data.

**Architecture:** Add an `intake_mode` field to Episode so standard planned videos and ad hoc videos are explicit. Add an API action that binds, processes, and packages a video as `ad_hoc_video` without requiring a selected topic or generated script. Update draft artifact generation so ad hoc packages can include ASR subtitles when available, include a fallback cover, and avoid script-estimated subtitles when there is no script.

**Tech Stack:** FastAPI, dataclasses, SQLite JSON payload persistence, pytest, Next.js server actions, TypeScript.

---

### Task 1: Backend Domain And Persistence

**Files:**
- Modify: `apps/api/app/models.py`
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/repository.py`
- Test: `apps/api/tests/test_episode_flow.py`

- [ ] Add `EpisodeIntakeMode` with `PLANNED = "planned"` and `AD_HOC_VIDEO = "ad_hoc_video"`.
- [ ] Add `intake_mode: EpisodeIntakeMode = EpisodeIntakeMode.PLANNED` to `Episode`.
- [ ] Include `intake_mode` in `EpisodeResponse`.
- [ ] Persist and reload `intake_mode`, defaulting old records to `planned`.
- [ ] Add a test that a newly created episode defaults to `planned`.

### Task 2: Ad Hoc Video Service And API

**Files:**
- Modify: `apps/api/app/services.py`
- Modify: `apps/api/app/workflow.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_episode_flow.py`

- [ ] Add a service method to mark an episode as ad hoc and bind a pending video without requiring a script.
- [ ] Add a workflow action `process_ad_hoc_video`.
- [ ] Add endpoint `POST /api/episodes/{episode_id}/process-ad-hoc-video` accepting `ProcessVideoRequest`.
- [ ] The endpoint must process the file, create a draft package, bind the package, and return `draft_ready`.
- [ ] Add a failing test that an episode with no topic and no script can process `raw.mov` into a draft package with `intake_mode = "ad_hoc_video"`.

### Task 3: Honest Ad Hoc Draft Artifacts

**Files:**
- Modify: `apps/api/app/drafts.py`
- Test: `apps/api/tests/test_draft_artifacts.py`

- [ ] For episodes without scripts, attempt ASR if configured.
- [ ] If ASR succeeds, write `subtitle.srt` with `subtitle_source = "tencentcloud_asr"`.
- [ ] If ASR is unavailable or fails and no script exists, do not generate script-estimated subtitles.
- [ ] Always generate a fallback `cover.png` for an Episode, even without a script.
- [ ] Add manifest fields `source_mode` and honest artifact status.

### Task 4: Frontend Types And Video Page Action

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/app/actions.ts`
- Modify: `apps/web/src/app/video/page.tsx`

- [ ] Add `intake_mode` to the `Episode` TypeScript type.
- [ ] Add API client function `processAdHocEpisodeVideo`.
- [ ] Add server action `processAdHocVideoAction`.
- [ ] Add a video page button labelled `直接处理已录视频`.
- [ ] Keep the planned `完整包` path for scripted episodes.

### Task 5: Repair Today’s Misclassified Episode

**Files:**
- Runtime SQLite database only.

- [ ] After code changes, run the ad hoc endpoint for `VID_20260529_122252.mp4`.
- [ ] Confirm the Episode is `draft_ready` with `intake_mode = "ad_hoc_video"`.
- [ ] Confirm manifest `source_mode = "ad_hoc_video"`.
- [ ] Confirm no script-estimated subtitle is created for ad hoc videos when Tencent ASR fails.

### Task 6: Verification

**Files:**
- No code changes.

- [ ] Run `corepack pnpm test:api`.
- [ ] Run `corepack pnpm --dir apps/web exec tsc --noEmit`.
- [ ] Run `corepack pnpm lint:web`.
- [ ] Run `corepack pnpm test:web-contract`.
- [ ] Run `corepack pnpm test:pixel-style`.
- [ ] Run `corepack pnpm build:web`.
