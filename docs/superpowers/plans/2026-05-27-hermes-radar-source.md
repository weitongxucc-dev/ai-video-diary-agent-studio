# Hermes Radar Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest Hermes daily Product Hunt + 观猹 reports as real topic sources for the AI 视频日记 Agent.

**Architecture:** Keep Hermes as the upstream crawler and treat its Markdown reports as source artifacts. Add a focused backend client that parses `daily-reports/YYYY-MM-DD.md` into `TopicSourceItem` records, expose status counts, and include those records in DeepSeek topic generation alongside Obsidian and other sources.

**Tech Stack:** FastAPI, Python dataclasses, Pydantic, pytest, Next.js TypeScript UI.

---

### Task 1: Hermes Report Parser

**Files:**
- Create: `apps/api/app/hermes_radar.py`
- Test: `apps/api/tests/test_hermes_radar.py`

- [ ] **Step 1: Write failing parser tests**

Create tests that write a sample Hermes Markdown report containing two Product Hunt entries and two 观猹 entries, then assert that `HermesRadarSourceClient.fetch_items()` returns four `TopicSourceItem` objects with stable source types, titles, refs, and excerpts.

- [ ] **Step 2: Run the parser tests red**

Run: `cd apps/api && uv run pytest tests/test_hermes_radar.py -v`

Expected: fail because `app.hermes_radar` does not exist.

- [ ] **Step 3: Implement parser**

Create `HermesRadarSourceClient` with:
- configurable report directory
- date-aware latest report resolution
- Markdown section parsing for `Product Hunt 今日热门` and `观猹 产品库精选`
- stable item limit
- graceful empty result if directory or report is missing

- [ ] **Step 4: Run parser tests green**

Run: `cd apps/api && uv run pytest tests/test_hermes_radar.py -v`

Expected: pass.

### Task 2: Settings And Status

**Files:**
- Modify: `apps/api/app/config.py`
- Modify: `apps/api/app/schemas.py`
- Test: `apps/api/tests/test_hermes_radar.py`

- [ ] **Step 1: Write failing status tests**

Assert that `Settings(hermes_radar_path=...)` exposes a `hermes_radar` public status object with `configured`, `exists`, `path`, `latest_report`, `product_hunt_count`, `watcha_count`, and `total_count`.

- [ ] **Step 2: Run status tests red**

Run: `cd apps/api && uv run pytest tests/test_hermes_radar.py -v`

Expected: fail because status fields do not exist.

- [ ] **Step 3: Implement settings/status fields**

Add `HERMES_RADAR_PATH` env support, defaulting to `~/Desktop/product-hunt-raw/daily-reports`, and a Pydantic response model for frontend consumption.

- [ ] **Step 4: Run status tests green**

Run: `cd apps/api && uv run pytest tests/test_hermes_radar.py -v`

Expected: pass.

### Task 3: Topic Generation Integration

**Files:**
- Modify: `apps/api/app/topics.py`
- Test: `apps/api/tests/test_hermes_radar.py`

- [ ] **Step 1: Write failing integration tests**

Assert that `_external_topic_sources(Settings(hermes_radar_path=...))` includes Product Hunt and 观猹 items from the Hermes report, even without `PRODUCT_HUNT_TOKEN`.

- [ ] **Step 2: Run integration tests red**

Run: `cd apps/api && uv run pytest tests/test_hermes_radar.py -v`

Expected: fail because the external source pipeline does not read Hermes reports yet.

- [ ] **Step 3: Add Hermes source provider**

Update topic generator configuration so Hermes reports count as a valid source and are merged before generic HTML sources.

- [ ] **Step 4: Run API tests green**

Run: `corepack pnpm test:api`

Expected: pass.

### Task 4: Pixel UI Status

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/components/PixelAgentPanel.tsx`

- [ ] **Step 1: Update frontend types**

Add `hermes_radar` to `SystemStatus`.

- [ ] **Step 2: Update Agent 状态 panel**

Show Hermes 雷达 as a real source with total/Product Hunt/观猹 counts and report file status.

- [ ] **Step 3: Verify frontend contracts**

Run:
- `corepack pnpm --dir apps/web exec tsc --noEmit`
- `corepack pnpm lint:web`
- `corepack pnpm test:web-contract`
- `corepack pnpm test:pixel-style`

Expected: pass.

### Task 5: Documentation And Runtime Check

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document Hermes integration**

Add `HERMES_RADAR_PATH` and explain that Product Hunt/观猹 can come from Hermes daily Markdown reports without a Product Hunt token.

- [ ] **Step 2: Run full verification**

Run:
- `corepack pnpm test:api`
- `corepack pnpm --dir apps/web exec tsc --noEmit`
- `corepack pnpm lint:web`
- `corepack pnpm test:web-contract`
- `corepack pnpm test:pixel-style`
- `corepack pnpm build:web`

Expected: pass.
