# External Review Action Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the technically valid parts of the external AI review into a focused implementation sequence for the next stabilization pass.

**Architecture:** Keep the current web/api/desktop split. Do not mechanically mirror every PRD state; instead add stronger guards, clearer user-facing workflow status, and explicit degradation paths where the implementation intentionally differs from PRD ambition.

**Tech Stack:** FastAPI, Python dataclasses/Pydantic, pytest, Next.js App Router, TypeScript, Electron.

---

## What We Accept From The Review

1. Voice command matching is too permissive for high-impact edit-plan actions.
2. Local Whisper default probing contains a user-specific path and should be portable.
3. Workflow progress needs clearer user-facing state explanations, especially around background jobs and edit/draft transitions.
4. Disabled global search is a real UX debt in a high-density workspace.
5. Background task completion should surface through system/browser feedback, not only polling.
6. Prompt inputs need bounded length and clearer separation between user content and system instructions.
7. Jianying 10.7 native encrypted draft writing should be documented as unsupported unless explicitly solved.

## What We Do Not Accept Literally

1. Do not add 18 `EpisodeStatus` values just because the PRD lists them. Current `EpisodeStatus` plus `VideoJobStatus` plus `GenerationJobStatus` is a valid split; the product gap is explainability, not enum count.
2. Do not treat prompt injection as SQL/XSS. The mitigation is input bounding, prompt compartmentalization, and high-risk action confirmation.
3. Do not prioritize notarization before the local internal loop is stable. It belongs after workflow safety and UX clarity.

---

### Task 1: Harden Voice Command Decisions

**Files:**
- Modify: `apps/api/app/voice_commands.py`
- Modify: `apps/api/tests/test_voice_commands.py`

- [x] **Step 1: Add failing tests for negated destructive commands**

Add tests proving "不要删除 retake-1" resolves to keep or blocked, never remove.

```python
def test_voice_command_does_not_remove_when_delete_is_negated():
    episode = main_module.repo.create()
    episode = main_module.workflow.run("generate_topics", episode)
    episode = main_module.workflow.run(
        "approve_topic",
        episode,
        topic_id=episode.topics[0].id,
    )

    response = client.post(
        "/api/voice/command/preview",
        json={"transcript": "不要删除 retake-1"},
    )

    body = response.json()
    assert body["intent"] != "edit_plan_segment_decision" or all(
        operation["params"].get("action") != "remove"
        for operation in body["operations"]
    )
```

- [x] **Step 2: Run the targeted voice command tests**

Run:

```bash
cd apps/api && uv run pytest tests/test_voice_commands.py -q
```

Expected: the new test fails with the current substring matcher.

- [x] **Step 3: Replace `_matches()` usage for edit-plan decisions with ordered intent rules**

Implement a helper that checks negation before destructive verbs.

```python
NEGATED_REMOVE_PATTERNS = ("不要删除", "别删除", "不要删", "别删", "不要剪掉", "别剪掉")
KEEP_PATTERNS = ("保留", "留下", "不要删", "不要删除", "别删", "别删除")
REMOVE_PATTERNS = ("删除", "剪掉", "去掉", "删掉")
REVIEW_PATTERNS = ("复核", "待确认", "人工看")


def _edit_plan_action_from_text(text: str) -> str | None:
    if _matches(text, *KEEP_PATTERNS):
        return "keep"
    if _matches(text, *NEGATED_REMOVE_PATTERNS):
        return "keep"
    if _matches(text, *REMOVE_PATTERNS):
        return "remove"
    if _matches(text, *REVIEW_PATTERNS):
        return "review"
    return None
```

Then call `_edit_plan_action_from_text(text)` inside `_parse_edit_plan_decision()`.

- [x] **Step 4: Re-run tests**

Run:

```bash
cd apps/api && uv run pytest tests/test_voice_commands.py -q
```

Expected: all voice command tests pass.

---

### Task 2: Remove User-Specific Local Whisper Defaults

**Files:**
- Modify: `apps/api/app/config.py`
- Modify: `apps/api/tests/test_runtime_settings.py`

- [x] **Step 1: Add failing config test**

Assert that default settings do not automatically inject `/Users/Zhuanz/Documents/声迹`.

```python
def test_local_whisper_defaults_are_not_user_specific(monkeypatch):
    monkeypatch.delenv("LOCAL_WHISPER_PYTHON_PATH", raising=False)
    monkeypatch.delenv("LOCAL_WHISPER_MODEL_PATH", raising=False)

    settings = Settings.from_env()

    assert not (settings.local_whisper_python_path or "").endswith("Documents/声迹/.venv-whisper/bin/python")
    assert "声迹" not in (settings.local_whisper_model_path or "")
```

- [x] **Step 2: Run the targeted settings tests**

Run:

```bash
cd apps/api && uv run pytest tests/test_runtime_settings.py -q
```

Expected: the new test fails.

- [x] **Step 3: Make Whisper paths opt-in**

Change `_default_local_whisper_python_path()` and `_default_local_whisper_model_path()` to return `None`. Keep environment variables and Settings UI as the official configuration path.

```python
def _default_local_whisper_python_path() -> str | None:
    return None


def _default_local_whisper_model_path() -> str | None:
    return None
```

- [x] **Step 4: Re-run settings and runtime health tests**

Run:

```bash
cd apps/api && uv run pytest tests/test_runtime_settings.py tests/test_diagnostics.py -q
```

Expected: tests pass and runtime health reports local whisper as missing unless configured.

---

### Task 3: Clarify Workflow State Without Inflating EpisodeStatus

**Files:**
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/episode_media_flow.py`
- Modify: `apps/api/tests/test_episode_flow.py`
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/components/EpisodeProgress.tsx`
- Modify: `apps/web/src/components/PixelAgentPanel.tsx`

- [x] **Step 1: Add API test for derived workflow phase**

Add a test that an episode with `topic_job_status=processing` exposes a derived phase label distinct from ordinary `created`.

```python
def test_episode_response_exposes_derived_workflow_phase_for_topic_job():
    episode = client.post("/api/episodes").json()
    response = client.post(f"/api/episodes/{episode['id']}/start-generate-topics")

    body = response.json()

    assert body["workflow_phase"]["id"] == "topics_generating"
    assert "选题" in body["workflow_phase"]["label"]
```

- [x] **Step 2: Define `WorkflowPhaseResponse`**

Add to `apps/api/app/schemas.py`:

```python
class WorkflowPhaseResponse(BaseModel):
    id: str
    label: str
    description: str
```

Add `workflow_phase: WorkflowPhaseResponse` to `EpisodeResponse`.

- [x] **Step 3: Compute phase in `episode_media_flow.py`**

Add a small pure function:

```python
def workflow_phase_for_episode(episode: Episode) -> dict[str, str]:
    if episode.topic_job_status == GenerationJobStatus.PROCESSING:
        return {
            "id": "topics_generating",
            "label": "选题生成中",
            "description": episode.topic_job_message or "后台正在生成候选选题。",
        }
    if episode.video_job_status in {VideoJobStatus.PROCESSING, VideoJobStatus.DRAFTING}:
        return {
            "id": "video_processing",
            "label": "视频处理中",
            "description": episode.video_job_message or "后台正在处理视频。",
        }
    return {
        "id": episode.status.value,
        "label": episode.status.value,
        "description": "当前 Episode 主状态。",
    }
```

Inject this derived value into the dict passed to `EpisodeResponse.model_validate()`.

- [x] **Step 4: Update TypeScript type and UI consumers**

Add to `Episode` in `apps/web/src/lib/types.ts`:

```ts
workflow_phase: {
  id: string;
  label: string;
  description: string;
};
```

Use `episode.workflow_phase.label` in progress/status panels where the UI currently shows raw `episode.status`.

- [x] **Step 5: Re-run API and web type checks**

Run:

```bash
cd apps/api && uv run pytest tests/test_episode_flow.py -q
npm run typecheck:web
```

Expected: both pass.

---

### Task 4: Make Global Search Real Or Remove The Fake Affordance

**Files:**
- Modify: `apps/web/src/components/AppShell.tsx`
- Modify: `apps/web/src/components/RetroChrome.tsx`
- Modify: `apps/web/src/app/globals.css`
- Modify: `apps/web/scripts/workflow-contract-check.mjs`

- [x] **Step 1: Add contract check against disabled global search**

Add to `workflow-contract-check.mjs`:

```js
if (appShell.includes('className="search-box"') && appShell.includes("disabled")) {
  failures.push("Global search must not render as a disabled fake affordance.");
}
if (retroChrome.includes('placeholder="搜索 Episode、选题、素材..."') && retroChrome.includes("disabled")) {
  failures.push("RetroChrome search must not render as a disabled fake affordance.");
}
```

- [x] **Step 2: Run contract check**

Run:

```bash
npm run test:web-contract
```

Expected: fails before implementation.

- [x] **Step 3: Replace disabled search with bounded navigation search**

Implement a client component `apps/web/src/components/GlobalSearchBox.tsx` that accepts input and routes simple keywords:

```tsx
"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

const routeByKeyword: Array<[string[], string]> = [
  [["选题", "topic"], "/topics"],
  [["口播", "脚本", "script"], "/script"],
  [["封面", "cover"], "/cover"],
  [["视频", "video"], "/video"],
  [["剪辑", "edit"], "/edit-plan"],
  [["草稿", "draft"], "/draft"],
  [["反馈", "publish"], "/publish-feedback"],
];

export function GlobalSearchBox() {
  const router = useRouter();
  const [query, setQuery] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = query.trim().toLowerCase();
    const match = routeByKeyword.find(([keywords]) =>
      keywords.some((keyword) => normalized.includes(keyword)),
    );
    router.push(match?.[1] ?? "/topics");
  }

  return (
    <form className="global-search-form" onSubmit={submit}>
      <input
        className="search-box"
        type="search"
        value={query}
        onChange={(event) => setQuery(event.currentTarget.value)}
        placeholder="搜索页面、选题、素材..."
        aria-label="搜索页面、选题、素材"
      />
    </form>
  );
}
```

Use this component in `AppShell`. In `RetroChrome`, either remove the fake input or make it read-only text without an input affordance.

- [x] **Step 4: Run web checks**

Run:

```bash
npm run test:web-contract
npm run lint:web
npm run typecheck:web
```

Expected: all pass.

---

### Task 5: Add System Notification For Completed Background Jobs

**Files:**
- Modify: `apps/desktop/src/service-manager.js`
- Modify: `apps/desktop/src/main.js`
- Modify: `apps/desktop/scripts/desktop-contract-check.mjs`

- [x] **Step 1: Add desktop contract for Notification usage**

Add checks that `main.js` imports or uses `Notification`, and service status events can trigger it.

```js
if (!main.includes("Notification")) {
  failures.push("Desktop app must use Electron Notification for background job completion.");
}
```

- [x] **Step 2: Run desktop contract**

Run:

```bash
npm run test:desktop-contract
```

Expected: fails before implementation.

- [x] **Step 3: Emit notification when API/Web become ready**

In `main.js`, add a small wrapper:

```js
function notify(title, body) {
  if (!Notification.isSupported()) return;
  new Notification({ title, body }).show();
}
```

Call it when service manager transitions from not ready to ready, and later reuse it for topic/video job completion events.

- [x] **Step 4: Run desktop checks**

Run:

```bash
npm run test:desktop-contract
npm run dist:mac
```

Expected: contract and DMG build pass.

---

### Task 6: Document Jianying Native Draft Boundary

**Files:**
- Modify: `README-beta.md`
- Modify: `docs/ai-video-diary-agent-2.0-prd.md`

- [x] **Step 1: Add an explicit support statement**

Add this wording to beta docs:

```md
剪映草稿导出当前保证生成素材包、manifest、4:3 cover 和可导入资产。剪映 10.7 原生加密草稿写入暂不作为 2.0 内测承诺；如果本机桥接导入失败，素材包仍可手动导入剪映。
```

- [x] **Step 2: Run docs grep**

Run:

```bash
rg "剪映 10.7 原生加密草稿写入暂不作为 2.0 内测承诺" README-beta.md docs/ai-video-diary-agent-2.0-prd.md
```

Expected: both files contain the boundary statement.

---

## Verification Batch

After all tasks:

```bash
npm run test:web-contract
npm run test:pixel-style
npm run lint:web
npm run typecheck:web
npm run test:desktop-contract
npm run test:api
npm run build:web
npm run dist:mac
```

Expected:

- Web contracts pass.
- Pixel style contract passes.
- ESLint and TypeScript pass.
- Desktop contract passes.
- API test suite passes.
- Web production build passes.
- macOS DMG builds.

## Recommended Execution Order

1. Task 1: Voice command safety.
2. Task 2: Local Whisper portability.
3. Task 3: Workflow phase explainability.
4. Task 4: Real global search or remove fake affordance.
5. Task 6: Jianying boundary documentation.
6. Task 5: System notifications.

This order removes real risk before adding polish.
