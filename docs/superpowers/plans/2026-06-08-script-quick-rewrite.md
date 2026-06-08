# Script Quick Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first 2.1A slice: script sections expose five quick rewrite intents that generate structured reasons, run through the existing section regeneration workflow, and preserve script version history.

**Architecture:** Keep the feature small and bounded. Add a focused backend helper for rewrite intents, expose it through a lightweight API response, then render reusable quick-action controls in the script page. Reuse the existing `/script/sections/{section_id}/regenerate` endpoint and `ScriptVersion` storage so no new persistence layer is needed in this slice.

**Tech Stack:** FastAPI, Python pytest, Next.js App Router, React Server Components, server actions, TypeScript, existing pixel UI CSS.

---

## File Map

- Create: `apps/api/app/script_rewrite_intents.py`
  - Owns quick rewrite intent definitions and reason formatting.
- Create: `apps/api/tests/test_script_rewrite_intents.py`
  - Unit tests for intent list, lookup, and reason formatting.
- Modify: `apps/api/app/schemas.py`
  - Add response schemas for quick rewrite intents.
- Modify: `apps/api/app/routers/episode_workflow.py`
  - Add `GET /api/script/rewrite-intents`.
- Modify: `apps/api/tests/test_api_modularity.py`
  - Ensure the new route lives in the workflow router contract.
- Modify: `apps/web/src/lib/types.ts`
  - Add `ScriptRewriteIntent`.
- Modify: `apps/web/src/lib/api.ts`
  - Add `getScriptRewriteIntents()`.
- Modify: `apps/web/src/app/actions.ts`
  - Allow section regeneration action to accept a preformatted `reason`.
- Create: `apps/web/src/components/ScriptQuickRewritePanel.tsx`
  - Reusable pixel action panel for per-section quick rewrite buttons.
- Modify: `apps/web/src/app/script/page.tsx`
  - Fetch quick rewrite intents and render them below each section.
- Modify: `apps/web/src/app/globals.css`
  - Add compact pixel styling for quick rewrite controls.
- Modify: `apps/web/scripts/workflow-contract-check.mjs`
  - Add a contract guard for quick rewrite controls and API wiring.

## Task 1: Backend Rewrite Intent Catalog

**Files:**
- Create: `apps/api/app/script_rewrite_intents.py`
- Create: `apps/api/tests/test_script_rewrite_intents.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_script_rewrite_intents.py`:

```python
from app.script_rewrite_intents import (
    get_script_rewrite_intent,
    script_rewrite_intents,
    script_rewrite_reason,
)


def test_script_rewrite_intents_include_required_shortcuts():
    intents = script_rewrite_intents()
    assert [intent["id"] for intent in intents] == [
        "sharper_angle",
        "more_conversational",
        "more_real_case",
        "shorter",
        "more_review_like",
    ]
    assert intents[0]["label"] == "更犀利"
    assert all(intent["prompt_hint"] for intent in intents)


def test_script_rewrite_reason_formats_intent_and_section_context():
    reason = script_rewrite_reason(
        "shorter",
        section_title="工具坑点",
        user_note="保留真实案例",
    )

    assert reason == "快捷重写：工具坑点 -> 更短；压缩到更短、更紧凑，删掉重复解释，保留关键判断。补充要求：保留真实案例"


def test_script_rewrite_reason_rejects_unknown_intent():
    try:
        get_script_rewrite_intent("unknown")
    except KeyError as exc:
        assert "unknown script rewrite intent" in str(exc)
    else:
        raise AssertionError("unknown rewrite intent should raise KeyError")
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd apps/api && uv run pytest tests/test_script_rewrite_intents.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'app.script_rewrite_intents'`.

- [ ] **Step 3: Implement the catalog**

Create `apps/api/app/script_rewrite_intents.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ScriptRewriteIntent:
    id: str
    label: str
    description: str
    prompt_hint: str


_SCRIPT_REWRITE_INTENTS: tuple[ScriptRewriteIntent, ...] = (
    ScriptRewriteIntent(
        id="sharper_angle",
        label="更犀利",
        description="强化冲突和判断，让观点更直接。",
        prompt_hint="把这一段改得更犀利，先给明确判断，再解释原因，减少铺垫。",
    ),
    ScriptRewriteIntent(
        id="more_conversational",
        label="更口语",
        description="减少书面感，更像自然口播。",
        prompt_hint="把这一段改得更口语，像真人边复盘边讲，避免书面腔和营销腔。",
    ),
    ScriptRewriteIntent(
        id="more_real_case",
        label="更真实",
        description="补足真实经历、现场感和具体例子。",
        prompt_hint="把这一段改得更像真实经历，加入具体现场、踩坑或选择依据。",
    ),
    ScriptRewriteIntent(
        id="shorter",
        label="更短",
        description="压缩表达，保留关键判断。",
        prompt_hint="压缩到更短、更紧凑，删掉重复解释，保留关键判断。",
    ),
    ScriptRewriteIntent(
        id="more_review_like",
        label="更像复盘",
        description="强化问题、原因、修正和经验。",
        prompt_hint="把这一段改得更像复盘，讲清问题、原因、修正动作和下次经验。",
    ),
)


def script_rewrite_intents() -> list[dict[str, str]]:
    return [asdict(intent) for intent in _SCRIPT_REWRITE_INTENTS]


def get_script_rewrite_intent(intent_id: str) -> dict[str, str]:
    for intent in _SCRIPT_REWRITE_INTENTS:
        if intent.id == intent_id:
            return asdict(intent)
    raise KeyError(f"unknown script rewrite intent: {intent_id}")


def script_rewrite_reason(
    intent_id: str,
    *,
    section_title: str,
    user_note: str | None = None,
) -> str:
    intent = get_script_rewrite_intent(intent_id)
    reason = f"快捷重写：{section_title} -> {intent['label']}；{intent['prompt_hint']}"
    if user_note and user_note.strip():
        reason = f"{reason}补充要求：{user_note.strip()}"
    return reason
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
cd apps/api && uv run pytest tests/test_script_rewrite_intents.py -q
```

Expected: `3 passed`.

## Task 2: Backend API Exposure

**Files:**
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/routers/episode_workflow.py`
- Modify: `apps/api/tests/test_api_modularity.py`
- Create: `apps/api/tests/test_script_quick_rewrite_api.py`

- [ ] **Step 1: Write the failing API test**

Create `apps/api/tests/test_script_quick_rewrite_api.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_script_rewrite_intents_endpoint_returns_shortcuts():
    response = TestClient(app).get("/api/script/rewrite-intents")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["intents"]] == [
        "sharper_angle",
        "more_conversational",
        "more_real_case",
        "shorter",
        "more_review_like",
    ]
    assert body["intents"][0]["label"] == "更犀利"
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
cd apps/api && uv run pytest tests/test_script_quick_rewrite_api.py -q
```

Expected: fail with status `404`.

- [ ] **Step 3: Add schemas and route**

In `apps/api/app/schemas.py`, add:

```python
class ScriptRewriteIntentResponse(BaseModel):
    id: str
    label: str
    description: str
    prompt_hint: str


class ScriptRewriteIntentListResponse(BaseModel):
    intents: list[ScriptRewriteIntentResponse]
```

In `apps/api/app/routers/episode_workflow.py` imports, add:

```python
from app.script_rewrite_intents import script_rewrite_intents
```

In schema imports, add:

```python
ScriptRewriteIntentListResponse,
```

Inside `create_router`, add:

```python
    @router.get(
        "/api/script/rewrite-intents",
        response_model=ScriptRewriteIntentListResponse,
    )
    def get_script_rewrite_intents() -> ScriptRewriteIntentListResponse:
        return ScriptRewriteIntentListResponse.model_validate(
            {"intents": script_rewrite_intents()}
        )
```

- [ ] **Step 4: Update modularity test**

In `apps/api/tests/test_api_modularity.py`, add route string:

```python
'"/api/script/rewrite-intents"',
```

- [ ] **Step 5: Run API tests for this slice**

Run:

```bash
cd apps/api && uv run pytest tests/test_script_rewrite_intents.py tests/test_script_quick_rewrite_api.py tests/test_api_modularity.py -q
```

Expected: all selected tests pass.

## Task 3: Web Types and API Client

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/scripts/workflow-contract-check.mjs`

- [ ] **Step 1: Write failing contract checks**

In `apps/web/scripts/workflow-contract-check.mjs`, add checks:

```js
if (!typesFile.includes("ScriptRewriteIntent")) {
  failures.push("Types must expose ScriptRewriteIntent.");
}

if (!apiFile.includes("getScriptRewriteIntents")) {
  failures.push("API client must fetch script rewrite intents.");
}

if (!apiFile.includes("/api/script/rewrite-intents")) {
  failures.push("API client must call /api/script/rewrite-intents.");
}
```

- [ ] **Step 2: Run contract and verify RED**

Run:

```bash
CI=true corepack pnpm test:web-contract
```

Expected: fail with missing `ScriptRewriteIntent` and API client messages.

- [ ] **Step 3: Add types**

In `apps/web/src/lib/types.ts`, add:

```ts
export type ScriptRewriteIntent = {
  id: string;
  label: string;
  description: string;
  prompt_hint: string;
};

export type ScriptRewriteIntentList = {
  intents: ScriptRewriteIntent[];
};
```

- [ ] **Step 4: Add API client**

In `apps/web/src/lib/api.ts`, import the new types and add:

```ts
export function getScriptRewriteIntents(): Promise<ScriptRewriteIntentList> {
  return request<ScriptRewriteIntentList>("/api/script/rewrite-intents", {
    next: { revalidate: 300 },
  });
}
```

- [ ] **Step 5: Run contract and typecheck**

Run:

```bash
CI=true corepack pnpm test:web-contract
CI=true corepack pnpm typecheck:web
```

Expected: both pass.

## Task 4: Server Action Reason Formatting

**Files:**
- Modify: `apps/web/src/app/actions.ts`

- [ ] **Step 1: Write failing contract check**

In `apps/web/scripts/workflow-contract-check.mjs`, add:

```js
if (!actionsFile.includes('formData.get("reason")')) {
  failures.push("Section regeneration action must accept quick rewrite reason from form data.");
}
```

- [ ] **Step 2: Run contract and verify RED**

Run:

```bash
CI=true corepack pnpm test:web-contract
```

Expected: fail with missing quick rewrite reason message.

- [ ] **Step 3: Update action**

In `regenerateScriptSectionAction`, read:

```ts
const reason = String(formData.get("reason") ?? "").trim();
```

Then pass:

```ts
reason || "用户要求重写口播段落"
```

to `regenerateEpisodeScriptSection`.

- [ ] **Step 4: Run contract**

Run:

```bash
CI=true corepack pnpm test:web-contract
```

Expected: pass.

## Task 5: Script Quick Rewrite UI

**Files:**
- Create: `apps/web/src/components/ScriptQuickRewritePanel.tsx`
- Modify: `apps/web/src/app/script/page.tsx`
- Modify: `apps/web/src/app/globals.css`
- Modify: `apps/web/scripts/workflow-contract-check.mjs`

- [ ] **Step 1: Write failing contract checks**

In `apps/web/scripts/workflow-contract-check.mjs`, read the new component file and add checks:

```js
const quickRewritePanel = await readFile(new URL("../src/components/ScriptQuickRewritePanel.tsx", import.meta.url), "utf8").catch(() => "");

if (!quickRewritePanel.includes("script-quick-rewrite-panel")) {
  failures.push("ScriptQuickRewritePanel must render a quick rewrite panel.");
}

if (!quickRewritePanel.includes('name="reason"')) {
  failures.push("Quick rewrite buttons must submit a structured reason.");
}

if (!scriptPage.includes("getScriptRewriteIntents")) {
  failures.push("/script must fetch script rewrite intents.");
}

if (!scriptPage.includes("ScriptQuickRewritePanel")) {
  failures.push("/script must render quick rewrite controls.");
}
```

- [ ] **Step 2: Run contract and verify RED**

Run:

```bash
CI=true corepack pnpm test:web-contract
```

Expected: fail with quick rewrite UI messages.

- [ ] **Step 3: Create component**

Create `apps/web/src/components/ScriptQuickRewritePanel.tsx`:

```tsx
import type { ScriptRewriteIntent, ScriptSection } from "@/lib/types";

type ScriptQuickRewritePanelProps = {
  action: (formData: FormData) => void | Promise<void>;
  intents: ScriptRewriteIntent[];
  section: ScriptSection;
};

export function ScriptQuickRewritePanel({
  action,
  intents,
  section,
}: ScriptQuickRewritePanelProps) {
  return (
    <section className="script-quick-rewrite-panel" aria-label={`${section.title} 快捷重写`}>
      <div className="script-quick-rewrite-copy">
        <span className="section-kicker">快捷重写</span>
        <strong>选择一个方向，Agent 会生成新版本</strong>
      </div>
      <div className="script-quick-rewrite-actions">
        {intents.map((intent) => (
          <form action={action} key={intent.id}>
            <input name="section_id" type="hidden" value={section.id} />
            <input
              name="reason"
              type="hidden"
              value={`快捷重写：${section.title} -> ${intent.label}；${intent.prompt_hint}`}
            />
            <button className="pixel-button secondary compact" title={intent.description} type="submit">
              {intent.label}
            </button>
          </form>
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Render component on script page**

In `apps/web/src/app/script/page.tsx`, import:

```tsx
import { ScriptQuickRewritePanel } from "@/components/ScriptQuickRewritePanel";
import { getScriptRewriteIntents } from "@/lib/api";
```

Load intents:

```tsx
  const rewriteIntentList = await getScriptRewriteIntents().catch(() => ({ intents: [] }));
```

Render after `ScriptSectionEditor`:

```tsx
<ScriptQuickRewritePanel
  action={regenerateScriptSectionAction}
  intents={rewriteIntentList.intents}
  section={section}
/>
```

- [ ] **Step 5: Add CSS**

Add to `apps/web/src/app/globals.css`:

```css
.script-quick-rewrite-panel {
  display: grid;
  gap: 8px;
  border: 2px solid var(--border-strong);
  background: var(--bg-panel);
  padding: 10px;
  box-shadow: var(--shadow-hard);
}

.script-quick-rewrite-copy {
  display: grid;
  gap: 4px;
}

.script-quick-rewrite-copy strong {
  font-size: 12px;
}

.script-quick-rewrite-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pixel-button.compact {
  min-height: 30px;
  padding: 5px 8px;
  font-size: 12px;
}
```

- [ ] **Step 6: Run web checks**

Run:

```bash
CI=true corepack pnpm test:web-contract
CI=true corepack pnpm typecheck:web
CI=true corepack pnpm lint:web
```

Expected: all pass.

## Task 6: Final Verification

**Files:**
- All touched files.

- [ ] **Step 1: Run full API tests**

Run:

```bash
cd apps/api && uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run full web checks**

Run:

```bash
CI=true corepack pnpm test:web-contract
CI=true corepack pnpm test:pixel-style
CI=true corepack pnpm typecheck:web
CI=true corepack pnpm lint:web
```

Expected: all checks pass.

- [ ] **Step 3: Commit and push**

Run:

```bash
git add apps/api/app/script_rewrite_intents.py apps/api/app/schemas.py apps/api/app/routers/episode_workflow.py apps/api/tests/test_script_rewrite_intents.py apps/api/tests/test_script_quick_rewrite_api.py apps/api/tests/test_api_modularity.py apps/web/src/lib/types.ts apps/web/src/lib/api.ts apps/web/src/app/actions.ts apps/web/src/components/ScriptQuickRewritePanel.tsx apps/web/src/app/script/page.tsx apps/web/src/app/globals.css apps/web/scripts/workflow-contract-check.mjs docs/superpowers/plans/2026-06-08-script-quick-rewrite.md
git commit -m "feat: add script quick rewrite controls"
git push origin feature/creator-profile
```

Expected: commit and push succeed.

## Self-Review

- Spec coverage: this plan implements the first recommended slice, `2.1A 脚本快捷重写`.
- Scope control: subtitle editor, cover editor, quality gate, performance insights, content calendar, and voice command expansion are intentionally out of scope.
- Coupling check: new backend intent logic lives in `script_rewrite_intents.py`; UI control lives in `ScriptQuickRewritePanel.tsx`; existing regeneration workflow remains the execution boundary.
- TDD check: each behavior starts with a failing test or contract check before implementation.
