# 一键追爆工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first working slice of the 同款脚本生成 page as a LuoGen-style one-screen chase-hot video workbench backed by a real API workflow contract.

**Architecture:** Add a small FastAPI read endpoint that exposes the eight production sections, their implementation module names, and their availability. The Next.js page reads that contract and renders a dense production console while external generators stay explicitly marked as adapter work.

**Tech Stack:** FastAPI, Pydantic, Next.js App Router, TypeScript, existing pixel UI CSS, FFmpeg/ASR/draft capabilities already present in the repo.

---

### Task 1: Backend Workflow Contract

**Files:**
- Create: `apps/api/app/same_style_workflow.py`
- Create: `apps/api/app/routers/same_style.py`
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/app_factory.py`
- Test: `apps/api/tests/test_same_style_workflow.py`
- Test: `apps/api/tests/test_api_modularity.py`

- [x] **Step 1: Add a failing API test**
  Assert `/api/same-style/workflow` returns eight sections: 文案生成, 音频生成, 视频生成, 字幕生成, 背景音乐生成, 标题生成, 封面制作, 视频发布.

- [x] **Step 2: Implement the contract builder**
  Return deterministic module metadata, real readiness for FFmpeg/ASR/LLM/cover/draft, and explicit adapter-missing states for CosyVoice, HeyGem, BGM, and publisher integrations.

- [x] **Step 3: Register the router**
  Include `same_style_router.create_router(get_context)` in `app_factory.include_routers`.

- [x] **Step 4: Run backend tests**
  Run: `cd apps/api && .venv/bin/pytest tests/test_same_style_workflow.py tests/test_api_modularity.py -q`

### Task 2: Frontend Workbench

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/app/same-style-script/page.tsx`
- Modify: `apps/web/src/app/globals.css`

- [x] **Step 1: Add TypeScript API types**
  Mirror the backend contract in `types.ts` and expose `getSameStyleWorkflow()` in `api.ts`.

- [x] **Step 2: Replace the simple page**
  Render the one-screen workbench with benchmark copy controls, audio generation, digital human generation, subtitles, BGM, title/tags, cover, publisher, and a processing status log.

- [x] **Step 3: Add responsive styles**
  Keep the workbench dense on desktop, collapse to one column on mobile, and keep buttons/inputs stable.

- [x] **Step 4: Run frontend verification**
  Run: `corepack pnpm --dir apps/web lint`, `corepack pnpm --dir apps/web exec tsc --noEmit`, and build only when the dev server is stopped.

### Task 3: Documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-06-24-same-style-script-generation-design.md`

- [x] **Step 1: Expand the design spec**
  Document the eight modules, current reuse, missing adapters, and future async job model.

- [x] **Step 2: Verify the page visually**
  Open `/same-style-script`, confirm the left nav entry, the eight sections, and the contract statuses render without overlap.
