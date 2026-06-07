# Publish Comment Insights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build PRD 2.0 P1 publish comment insight flow so pasted post-publish comments become explainable, user-confirmed preference candidates.

**Architecture:** Keep comment parsing in a focused backend module and persist insights as `FeedbackEvent` records with publish scope. Reuse the existing preference candidate and long-term memory confirmation flow instead of creating a parallel memory system.

**Tech Stack:** FastAPI, Pydantic, pytest, Next.js App Router server actions, TypeScript.

---

### Task 1: Backend Comment Insight Contract

**Files:**
- Create: `apps/api/tests/test_comment_insights.py`
- Create: `apps/api/app/comment_insights.py`
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/preference_memory.py`

- [x] **Step 1: Write the failing API test**

```python
def test_publish_comments_create_confirmable_preference_candidates(tmp_path):
    repo, _ = reset_state(tmp_path)
    episode = repo.today()
    repo.save(episode)

    response = client.post(
        f"/api/episodes/{episode.id}/comment-insights",
        json={
            "comments_text": "\n".join(
                [
                    "开头真实经历很抓人，想继续看",
                    "这个教程步骤很清楚",
                    "这条太官方了，像广告",
                    "先讲实测结论会更好",
                ]
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert {item["content"] for item in body["created_candidates"]} == {
        "喜欢真实经历开头",
        "教程类视频要步骤清楚",
        "不喜欢太官方的表达",
        "工具测评要先讲实际体验",
    }
    preferences = client.get("/api/preferences").json()
    assert {candidate["scope"] for candidate in preferences["candidates"]} == {"publish"}
    assert all(candidate["confirmed"] is False for candidate in preferences["candidates"])
```

- [x] **Step 2: Run the failing test**

Run: `corepack pnpm test:api apps/api/tests/test_comment_insights.py -q`
Expected: FAIL because `/api/episodes/{episode_id}/comment-insights` does not exist.

- [x] **Step 3: Implement minimal backend flow**

Add `CommentInsight` and `extract_comment_insights(comments_text: str)` in `apps/api/app/comment_insights.py`; map explainable Chinese signals into `prefer` / `avoid` insights and ignore empty or duplicate comments. Add `CommentInsightsRequest` / `CommentInsightsResponse` schemas. Add `POST /api/episodes/{episode_id}/comment-insights` to create publish-scoped `FeedbackEvent` records with target type `published_comment_insight`. Extend `_scope_for_target_type()` so this target maps to `publish`.

- [x] **Step 4: Verify backend green**

Run: `corepack pnpm test:api apps/api/tests/test_comment_insights.py -q`
Expected: PASS.

### Task 2: Frontend Comment Insight Entry

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/app/actions.ts`
- Modify: `apps/web/src/app/draft/page.tsx`
- Modify: `apps/web/src/app/learning/page.tsx`
- Modify: `apps/web/src/app/__tests__/contract.test.ts`

- [x] **Step 1: Write failing contract checks**

```ts
expect(draftPage).toContain("评论洞察");
expect(draftPage).toContain("comments_text");
expect(actions).toContain("analyzeCommentInsightsAction");
expect(api).toContain("analyzeCommentInsights");
expect(learningPage).toContain("评论洞察");
```

- [x] **Step 2: Run the failing web contract test**

Run: `corepack pnpm test:web-contract`
Expected: FAIL because the comment insight form and API client are not wired yet.

- [x] **Step 3: Implement frontend flow**

Add `analyzeCommentInsights()` API client and `analyzeCommentInsightsAction()`. Add a bounded textarea beside the publish feedback block on `/draft`; submit redirects to `/learning`. Update the learning target label so `published_comment_insight` renders as `评论洞察`.

- [x] **Step 4: Verify frontend green**

Run: `corepack pnpm test:web-contract && corepack pnpm typecheck:web && corepack pnpm lint:web`
Expected: PASS.

### Task 3: Full Verification And Handoff

**Files:**
- Modify: `docs/ai-video-diary-agent-2.0-prd.md`

- [x] **Step 1: Update PRD status**

Mark publish comment insight as implemented and keep P2/native draft plus desktop DMG items listed as remaining.

- [x] **Step 2: Run full verification**

Run:

```bash
corepack pnpm test:api
corepack pnpm test:web-contract
corepack pnpm test:pixel-style
corepack pnpm typecheck:web
corepack pnpm lint:web
corepack pnpm build:web
git diff --check
```

Expected: all commands exit 0.

- [x] **Step 3: Visual verification**

Open `/draft` and `/learning` in a browser, verify the comment textarea is bounded, text does not overflow, and generated candidates are visible in the confirmation area.

- [ ] **Step 4: Commit and push**

Run:

```bash
git add apps/api apps/web docs
git commit -m "feat: add publish comment insights"
git push
```
