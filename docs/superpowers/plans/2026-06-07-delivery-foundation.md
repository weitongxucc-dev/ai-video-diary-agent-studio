# Delivery Foundation Implementation Plan

> Implementation record for the `feature/delivery-foundation` branch. Steps use checkbox (`- [x]`) syntax for tracking completed delivery work.

**Goal:** Make the 2.0 Web/API beta more deliverable by adding CI and closing the draft-to-publish feedback learning loop.

**Architecture:** Keep `main` runnable and work on `feature/delivery-foundation`. Add CI as repository configuration. Extend publish feedback through the existing EpisodeService and FeedbackEvent model, then expose it through a focused server action and a draft-page publish panel. Keep changes cohesive: backend publication behavior in service/API/schema tests, frontend form behavior in actions/page/contract checks.

**Tech Stack:** FastAPI, Pydantic, pytest, Next.js Server Actions, TypeScript, GitHub Actions, pnpm.

---

### Task 1: CI Quality Gate

**Files:**
- Create: `.github/workflows/ci.yml`

- [x] **Step 1: Add CI workflow**

Create `.github/workflows/ci.yml` with jobs that run:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  verify:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: "pnpm"
      - uses: astral-sh/setup-uv@v5
      - run: corepack enable
      - run: corepack pnpm install --frozen-lockfile
      - run: npm run test:api
      - run: npm run test:web-contract
      - run: npm run test:pixel-style
      - run: npm run build:web
      - run: npm run typecheck:web
      - run: npm run lint:web
```

- [x] **Step 2: Validate YAML-adjacent formatting**

Run:

```bash
git diff --check
```

Expected: no output.

### Task 2: Publish Feedback Learns From User-Confirmed Signals

**Files:**
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/services.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_episode_flow.py`
- Test: `apps/api/tests/test_workspace_pages.py`

- [x] **Step 1: Write failing API test**

Add a test that posts to `/mark-published` with:

```json
{
  "publish_url": "https://www.douyin.com/video/1",
  "play_count": 128,
  "like_count": 12,
  "comment_count": 3,
  "feedback_value": "up",
  "feedback_reason": "开头直接讲冲突，观众更容易停留"
}
```

Assert the episode is `published`, contains a `published_episode` feedback event, and `/api/preferences` returns that reason as a preference.

- [x] **Step 2: Verify RED**

Run:

```bash
npm run test:api -- tests/test_episode_flow.py::test_mark_published_records_feedback_memory
```

Expected: failure because publish feedback is not persisted.

- [x] **Step 3: Implement minimal backend behavior**

Extend `MarkPublishedRequest` with:

```python
feedback_value: FeedbackValue | None = None
feedback_reason: str | None = None
```

Extend `FeedbackEventResponse.target_type` to include `published_episode`.
Update `EpisodeService.mark_published(...)` to append `FeedbackEvent("published_episode", episode.id, feedback_value, feedback_reason)` only when both value and reason are present.
Pass the two request fields through `main.py`.

- [x] **Step 4: Verify GREEN**

Run:

```bash
npm run test:api -- tests/test_episode_flow.py::test_mark_published_records_feedback_memory tests/test_workspace_pages.py::test_preferences_endpoint_turns_feedback_reasons_into_memory_cards
```

Expected: both pass.

### Task 3: Publish Feedback UI

**Files:**
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/app/actions.ts`
- Modify: `apps/web/src/app/draft/page.tsx`
- Modify: `apps/web/scripts/workflow-contract-check.mjs`

- [x] **Step 1: Write failing contract checks**

Update `workflow-contract-check.mjs` to require:

```text
actions.ts contains markPublishedAction
draft/page.tsx contains markPublishedAction
draft/page.tsx contains publish_url
draft/page.tsx contains feedback_reason
```

Run:

```bash
npm run test:web-contract
```

Expected: failure because the publish panel is missing.

- [x] **Step 2: Implement frontend behavior**

Add `markPublishedAction` to `actions.ts`, parsing:

```text
episode_id
publish_url
play_count
like_count
comment_count
feedback_value
feedback_reason
```

Update `markEpisodePublished` in `api.ts` and types to send those fields.
Update `/draft` to load `getTodayEpisode()` alongside draft status and render a publish feedback panel for the current episode.

- [x] **Step 3: Verify frontend contracts**

Run:

```bash
npm run test:web-contract
npm run test:pixel-style
```

Expected: both pass.

### Task 4: Full Verification And Branch Push

**Files:**
- All touched files.

- [x] **Step 1: Full verification**

Run:

```bash
npm run test:api
npm run test:web-contract
npm run test:pixel-style
npm run build:web
npm run typecheck:web
npm run lint:web
```

Expected: all pass.

- [x] **Step 2: Commit**

Run:

```bash
git add .github/workflows/ci.yml package.json apps/api/app/schemas.py apps/api/app/services.py apps/api/app/main.py apps/api/app/workflow.py apps/api/tests/test_episode_flow.py apps/web/src/lib/api.ts apps/web/src/lib/types.ts apps/web/src/app/actions.ts apps/web/src/app/draft/page.tsx apps/web/src/app/learning/page.tsx apps/web/src/app/globals.css apps/web/scripts/workflow-contract-check.mjs docs/superpowers/plans/2026-06-07-delivery-foundation.md
git commit -m "feat: add delivery foundation"
```

- [x] **Step 3: Push branch**

Run:

```bash
git push -u origin feature/delivery-foundation
```
