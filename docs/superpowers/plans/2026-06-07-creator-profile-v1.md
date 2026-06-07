# Creator Profile V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an editable local Creator Profile and inject its short summary into topic, script, and section prompts.

**Architecture:** Creator Profile lives in a dedicated backend module with a single SQLite table in the local database, so it does not couple runtime settings, feedback memory, or Episode history. The prompt layer accepts an optional profile payload and keeps it compact. The web app exposes `/profile` through a focused settings-style page and server action.

**Tech Stack:** FastAPI, Pydantic, pytest, Next.js Server Actions, TypeScript, pnpm, GitHub Actions verification.

---

### Task 1: Backend Creator Profile Domain And API

**Files:**
- Create: `apps/api/app/creator_profile.py`
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_creator_profile.py`

- [x] **Step 1: Write failing profile storage/API tests**

Add `apps/api/tests/test_creator_profile.py`:

```python
from fastapi.testclient import TestClient

import app.main as main_module
from app.creator_profile import CreatorProfileRepository


client = TestClient(main_module.app)


def test_creator_profile_endpoint_returns_defaults(tmp_path):
    main_module.creator_profile_repo = CreatorProfileRepository(tmp_path / "agent.sqlite3")

    response = client.get("/api/creator-profile")

    assert response.status_code == 200
    body = response.json()
    assert body["identity"]
    assert body["content_domains"]
    assert body["prompt_payload"]["identity"] == body["identity"]


def test_creator_profile_endpoint_trims_dedupes_and_persists(tmp_path):
    main_module.creator_profile_repo = CreatorProfileRepository(tmp_path / "agent.sqlite3")

    response = client.post(
        "/api/creator-profile",
        json={
            "identity": "  技术型个人创作者  ",
            "content_domains": [" AI 产品 ", "AI 产品", "", "自动化"],
            "tone_keywords": ["直接", "真实", "直接"],
            "preferred_phrases": ["先讲真实问题"],
            "banned_phrases": ["家人们", "太炸裂了"],
            "good_examples": ["我今天踩到一个具体的坑。"],
            "bad_examples": ["这个神器太炸裂了。"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["identity"] == "技术型个人创作者"
    assert body["content_domains"] == ["AI 产品", "自动化"]
    assert body["tone_keywords"] == ["直接", "真实"]
    assert body["prompt_payload"]["avoid"] == ["家人们", "太炸裂了"]

    persisted = client.get("/api/creator-profile").json()
    assert persisted["identity"] == "技术型个人创作者"
    assert persisted["content_domains"] == ["AI 产品", "自动化"]
```

- [x] **Step 2: Verify RED**

Run:

```bash
npm run test:api -- tests/test_creator_profile.py
```

Expected: failure because `app.creator_profile` and `/api/creator-profile` do not exist.

- [x] **Step 3: Implement `creator_profile.py`**

Create a frozen dataclass `CreatorProfile`, mutable update dataclass or function, and `CreatorProfileRepository`.
The repository should:

- create a `creator_profile` table with one logical `default` row;
- return defaults when the row is missing or corrupt;
- trim strings;
- dedupe list fields in first-seen order;
- cap list fields to 12 items;
- persist the profile payload as JSON plus `updated_at`;
- expose `prompt_payload(profile)`.

- [x] **Step 4: Add schemas and routes**

Add `CreatorProfileResponse` and `CreatorProfileUpdateRequest` in `apps/api/app/schemas.py`.
In `apps/api/app/main.py`, instantiate:

```python
creator_profile_repo = CreatorProfileRepository(settings.database_path)
```

Expose:

```python
@app.get("/api/creator-profile", response_model=CreatorProfileResponse)
def get_creator_profile() -> CreatorProfileResponse: ...

@app.post("/api/creator-profile", response_model=CreatorProfileResponse)
def update_creator_profile(payload: CreatorProfileUpdateRequest) -> CreatorProfileResponse: ...
```

Reject `POST /api/creator-profile` from non-local clients using `is_local_client_host`, and reload `creator_profile_repo` inside `_reload_runtime_dependencies()`.

- [x] **Step 5: Verify GREEN**

Run:

```bash
npm run test:api -- tests/test_creator_profile.py
```

Expected: 2 tests pass.

### Task 2: Prompt Context Integration

**Files:**
- Modify: `apps/api/app/topics.py`
- Modify: `apps/api/app/llm.py`
- Modify: `apps/api/app/services.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/automation.py`
- Test: `apps/api/tests/test_2_0_prompt_context.py`
- Test: `apps/api/tests/test_automation_worker.py`

- [x] **Step 1: Write failing prompt tests**

Extend `apps/api/tests/test_2_0_prompt_context.py` with tests that call:

- `DeepSeekTopicGenerator.generate(..., creator_profile=profile)`
- `DeepSeekScriptGenerator.generate(..., creator_profile=profile)`
- `DeepSeekScriptGenerator.regenerate_section(..., creator_profile=profile)`

Assert each user prompt contains:

```python
assert user_prompt["creator_profile"]["identity"] == "技术型个人创作者"
assert "先讲真实问题" in user_prompt["creator_profile"]["prefer"]
assert "家人们" in user_prompt["creator_profile"]["avoid"]
assert "优先遵守 creator_profile" in user_prompt["requirements"]
```

Add a service test that captures `creator_profile` passed into topic/script generators.

- [x] **Step 2: Verify RED**

Run:

```bash
npm run test:api -- tests/test_2_0_prompt_context.py
```

Expected: failure because generator signatures do not accept `creator_profile`.

- [x] **Step 3: Extend generator protocols and prompts**

Update `TopicGenerator`, `TemplateTopicGenerator`, `MissingTopicGenerator`, `DeepSeekTopicGenerator`, `ScriptGenerator`, `TemplateScriptGenerator`, `MissingScriptGenerator`, and `DeepSeekScriptGenerator` to accept:

```python
creator_profile: CreatorProfile | None = None
```

Add `"creator_profile": creator_profile_prompt_payload(creator_profile)` to topic, script, and section user prompts. Add a requirement line:

```text
优先遵守 creator_profile：保持创作者稳定口吻，强化 prefer，避开 avoid 和 bad examples。
```

- [x] **Step 4: Pass profile through service and main**

Update `EpisodeService.generate_topics`, `generate_script`, `regenerate_script`, and `regenerate_script_section` with optional `creator_profile`.
In `apps/api/app/main.py`, pass `creator_profile_repo.load()` into workflow calls for topic/script generation.
In `apps/api/app/automation.py`, inject a profile provider so scheduled topic generation uses the saved local profile.

- [x] **Step 5: Verify GREEN**

Run:

```bash
npm run test:api -- tests/test_2_0_prompt_context.py tests/test_creator_profile.py
```

Expected: all selected tests pass.

### Task 3: Frontend Profile Page And Contracts

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/app/actions.ts`
- Create: `apps/web/src/app/profile/page.tsx`
- Modify: `apps/web/src/components/AppShell.tsx`
- Modify: `apps/web/src/app/globals.css`
- Modify: `apps/web/scripts/workflow-contract-check.mjs`

- [x] **Step 1: Write failing frontend contract checks**

Update `workflow-contract-check.mjs` to require:

- AppShell contains `href: "/profile"`;
- API client contains `getCreatorProfile` and `updateCreatorProfile`;
- actions contain `updateCreatorProfileAction`;
- `/profile/page.tsx` contains `identity`, `content_domains`, `tone_keywords`, `banned_phrases`, `good_examples`, `bad_examples`.

Run:

```bash
npm run test:web-contract
```

Expected: failure because the frontend profile route does not exist.

- [x] **Step 2: Implement frontend API, action, and page**

Add TypeScript types:

```ts
export type CreatorProfile = {
  identity: string;
  content_domains: string[];
  tone_keywords: string[];
  preferred_phrases: string[];
  banned_phrases: string[];
  good_examples: string[];
  bad_examples: string[];
  prompt_payload: Record<string, unknown>;
  updated_at: string;
};

export type CreatorProfileUpdate = Omit<CreatorProfile, "prompt_payload" | "updated_at">;
```

Add API client functions:

```ts
export function getCreatorProfile(): Promise<CreatorProfile>;
export function updateCreatorProfile(payload: CreatorProfileUpdate): Promise<CreatorProfile>;
```

Add `updateCreatorProfileAction(formData)` that parses newline-separated fields. It must submit empty arrays and empty strings instead of omitting blank fields, so users can clear profile entries.

Create `/profile` page with a settings-style form and summary side cards.

- [x] **Step 3: Verify frontend GREEN**

Run:

```bash
npm run test:web-contract
npm run test:pixel-style
npm run typecheck:web
npm run lint:web
```

Expected: all pass.

### Task 4: Full Verification, Docs, Commit, Push

**Files:**
- Modify: `README.md`
- Modify: `docs/ai-video-diary-agent-prompt-strategy.md`
- All touched files.

- [x] **Step 1: Update docs**

Update README current build list to include Creator Profile v1 and `/profile` route.
Update the prompt strategy doc current status so Creator Profile is no longer listed as unimplemented.

- [x] **Step 2: Full verification**

Run:

```bash
npm run test:api
npm run test:web-contract
npm run test:pixel-style
npm run build:web
npm run typecheck:web
npm run lint:web
git diff --check
```

Expected: all pass.

- [x] **Step 3: Commit and push**

Run:

```bash
git add .
git commit -m "feat: add creator profile"
git push -u origin feature/creator-profile
```

Expected: branch pushed to origin.
