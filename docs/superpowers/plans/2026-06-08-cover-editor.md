# Cover Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the 2.1C cover editor slice so a creator can edit cover headline, subtitle, tag, template choice, and preview the result before generating a draft package.

**Architecture:** Keep cover behavior in a focused `cover_workspace` backend module and `cover` router. Persist the first slice through the existing script `cover_copy` field and script version history, because the current draft package already renders `cover.png` from `episode.script.cover_copy`; avoid adding database fields until template persistence needs it.

**Tech Stack:** FastAPI, Pydantic, existing `EpisodeService.update_script`, Next.js App Router server actions, TypeScript API client, existing pixel CSS.

---

### Task 1: Backend Cover Workspace

**Files:**
- Create: `apps/api/app/cover_workspace.py`
- Test: `apps/api/tests/test_cover_workspace.py`

- [ ] **Step 1: Write the failing unit tests**

```python
from app.cover_workspace import (
    cover_copy_from_fields,
    cover_draft_from_episode,
    cover_templates,
)
from app.models import Episode, EpisodeIntakeMode, EpisodeStatus, ScriptPackage
from datetime import date


def _episode_with_script() -> Episode:
    return Episode(
        id="ep_1",
        day_number=7,
        episode_date=date(2026, 6, 8),
        status=EpisodeStatus.SCRIPT_READY,
        intake_mode=EpisodeIntakeMode.PLANNED,
        script=ScriptPackage(
            id="script_1",
            hook="hook",
            sections=[],
            title_options=["标题候选 A"],
            cover_copy="主标题\n副标题\nAI观察",
            publish_copy="publish",
            comment_prompt="comment",
        ),
    )


def test_cover_draft_reads_script_cover_copy():
    draft = cover_draft_from_episode(_episode_with_script())
    assert draft["headline"] == "主标题"
    assert draft["subline"] == "副标题"
    assert draft["tag"] == "AI观察"
    assert draft["preview_aspect_ratio"] == "4:3"
    assert draft["templates"][0]["template_id"] == "pixel_diary"


def test_cover_copy_from_fields_trims_and_limits_lines():
    assert cover_copy_from_fields(" 主标题 ", " 副标题 ", " 标签 ") == "主标题\n副标题\n标签"


def test_cover_templates_are_compact_and_named():
    templates = cover_templates()
    assert [item["template_id"] for item in templates] == [
        "pixel_diary",
        "tool_review",
        "tutorial_steps",
    ]
    assert all(item["name"] for item in templates)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_cover_workspace.py -q`
Expected: `ModuleNotFoundError: No module named 'app.cover_workspace'`.

- [ ] **Step 3: Implement the focused cover workspace module**

Create `apps/api/app/cover_workspace.py` with:

```python
from __future__ import annotations

from copy import deepcopy

from app.models import Episode, ScriptPackage


_COVER_TEMPLATES = (
    {
        "template_id": "pixel_diary",
        "name": "像素日记",
        "description": "适合观点、复盘和日常 AI 视频日记。",
        "accent": "#1d4ed8",
    },
    {
        "template_id": "tool_review",
        "name": "工具测评",
        "description": "突出工具名、判断和推荐等级。",
        "accent": "#0f766e",
    },
    {
        "template_id": "tutorial_steps",
        "name": "教程步骤",
        "description": "突出目标、步骤和最终效果。",
        "accent": "#b45309",
    },
)


def cover_templates() -> list[dict[str, str]]:
    return [dict(item) for item in _COVER_TEMPLATES]


def cover_copy_from_fields(headline: str, subline: str, tag: str) -> str:
    lines = [headline.strip(), subline.strip(), tag.strip()]
    return "\n".join(line for line in lines if line)


def cover_draft_from_episode(episode: Episode) -> dict[str, object]:
    if episode.script is None:
        raise ValueError("generate a script first")
    lines = [line.strip() for line in episode.script.cover_copy.splitlines() if line.strip()]
    headline = lines[0] if len(lines) > 0 else episode.script.title_options[0]
    subline = lines[1] if len(lines) > 1 else "今天的AI产品观察"
    tag = lines[2] if len(lines) > 2 else f"DAY {episode.day_number}"
    template = cover_templates()[0]
    return {
        "episode_id": episode.id,
        "headline": headline,
        "subline": subline,
        "tag": tag,
        "template_id": template["template_id"],
        "preview_aspect_ratio": "4:3",
        "cover_copy": cover_copy_from_fields(headline, subline, tag),
        "templates": cover_templates(),
    }


def script_with_cover_copy(
    script: ScriptPackage,
    *,
    headline: str,
    subline: str,
    tag: str,
) -> ScriptPackage:
    updated = deepcopy(script)
    updated.cover_copy = cover_copy_from_fields(headline, subline, tag)
    return updated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_cover_workspace.py -q`
Expected: `3 passed`.

### Task 2: Backend Cover API

**Files:**
- Modify: `apps/api/app/schemas.py`
- Create: `apps/api/app/routers/cover.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/tests/test_api_modularity.py`
- Test: `apps/api/tests/test_cover_api.py`

- [ ] **Step 1: Write failing API tests**

```python
from fastapi.testclient import TestClient

import app.main as main_module
from app.llm import TemplateScriptGenerator
from app.repository import EpisodeRepository
from app.services import EpisodeService


client = TestClient(main_module.app)


def reset_cover_runtime():
    main_module.repo = EpisodeRepository()
    main_module.service = EpisodeService(script_generator=TemplateScriptGenerator())
    main_module.workflow = main_module.LangGraphEpisodeWorkflow(main_module.service)


def _script_episode():
    reset_cover_runtime()
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    )
    return client.post(f"/api/episodes/{episode['id']}/generate-script").json()


def test_cover_endpoint_returns_editable_cover_draft():
    episode = _script_episode()
    response = client.get(f"/api/episodes/{episode['id']}/cover")
    assert response.status_code == 200
    body = response.json()
    assert body["episode_id"] == episode["id"]
    assert body["headline"]
    assert body["preview_aspect_ratio"] == "4:3"
    assert body["templates"][0]["template_id"] == "pixel_diary"


def test_cover_endpoint_updates_cover_copy_with_version_history():
    episode = _script_episode()
    response = client.post(
        f"/api/episodes/{episode['id']}/cover",
        json={
            "headline": "Agent 失控了吗",
            "subline": "不是模型坏，是没装边界",
            "tag": "AI观察",
            "template_id": "pixel_diary",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["script"]["cover_copy"] == "Agent 失控了吗\n不是模型坏，是没装边界\nAI观察"
    assert body["script_versions"][-1]["source"] == "manual_edit"
    assert body["script_versions"][-1]["reason"] == "封面编辑器更新"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/test_cover_api.py -q`
Expected: `404` for `/api/episodes/{episode_id}/cover`.

- [ ] **Step 3: Add schemas and router**

Add Pydantic models:

```python
class CoverTemplateResponse(BaseModel):
    template_id: str
    name: str
    description: str
    accent: str


class CoverDraftResponse(BaseModel):
    episode_id: str
    headline: str
    subline: str
    tag: str
    template_id: str
    preview_aspect_ratio: str
    cover_copy: str
    templates: list[CoverTemplateResponse]


class CoverUpdateRequest(BaseModel):
    headline: str
    subline: str = ""
    tag: str = ""
    template_id: str = "pixel_diary"
```

Create `apps/api/app/routers/cover.py`:

```python
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from app import episode_media_flow
from app.cover_workspace import cover_draft_from_episode, script_with_cover_copy
from app.routers.context import AppContext
from app.schemas import CoverDraftResponse, CoverUpdateRequest, EpisodeResponse


def create_router(get_context: Callable[[], AppContext]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/episodes/{episode_id}/cover", response_model=CoverDraftResponse)
    def get_episode_cover(episode_id: str) -> CoverDraftResponse:
        context = get_context()
        episode = _episode_or_404(context, episode_id)
        try:
            return CoverDraftResponse.model_validate(cover_draft_from_episode(episode))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/episodes/{episode_id}/cover", response_model=EpisodeResponse)
    def update_episode_cover(
        episode_id: str,
        request: CoverUpdateRequest,
    ) -> EpisodeResponse:
        context = get_context()
        episode = _episode_or_404(context, episode_id)
        if episode.script is None:
            raise HTTPException(status_code=400, detail="generate a script first")
        updated = context.service.update_script(
            episode,
            script_with_cover_copy(
                episode.script,
                headline=request.headline,
                subline=request.subline,
                tag=request.tag,
            ),
            reason="封面编辑器更新",
        )
        return episode_media_flow.episode_response(context, context.repo.save(updated))

    return router


def _episode_or_404(context: AppContext, episode_id: str):
    try:
        return episode_media_flow.get_episode(context, episode_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc
```

- [ ] **Step 4: Wire router in main and modularity test**

Import and include `cover_router` in `apps/api/app/main.py`, and add route literals to `apps/api/tests/test_api_modularity.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/test_cover_workspace.py tests/test_cover_api.py tests/test_api_modularity.py -q`
Expected: all selected tests pass.

### Task 3: Web Cover Client And Page

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/app/actions.ts`
- Create: `apps/web/src/app/cover/page.tsx`
- Modify: `apps/web/src/components/AppShell.tsx`
- Modify: `apps/web/src/app/draft/page.tsx`
- Modify: `apps/web/src/app/globals.css`
- Modify: `apps/web/scripts/workflow-contract-check.mjs`

- [ ] **Step 1: Add failing web contract checks**

Require:

```javascript
// workflow-contract-check.mjs
files.cover = new URL("../src/app/cover/page.tsx", import.meta.url)
// checks:
"getEpisodeCover"
"updateEpisodeCover"
"updateCoverAction"
"/api/episodes/${episodeId}/cover"
"CoverDraft"
"CoverTemplate"
"cover-preview-frame"
"cover-editor-form"
'href: "/cover"'
```

Run: `CI=true corepack pnpm test:web-contract`
Expected: contract failures for missing cover client/page/nav/styles.

- [ ] **Step 2: Add TypeScript types and API client**

Add:

```ts
export type CoverTemplate = {
  template_id: string;
  name: string;
  description: string;
  accent: string;
};

export type CoverDraft = {
  episode_id: string;
  headline: string;
  subline: string;
  tag: string;
  template_id: string;
  preview_aspect_ratio: string;
  cover_copy: string;
  templates: CoverTemplate[];
};
```

Add API functions:

```ts
export function getEpisodeCover(episodeId: string): Promise<CoverDraft> {
  return request<CoverDraft>(`/api/episodes/${episodeId}/cover`);
}

export function updateEpisodeCover(
  episodeId: string,
  payload: { headline: string; subline: string; tag: string; template_id: string },
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/cover`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
```

- [ ] **Step 3: Add server action**

Add `updateCoverAction(formData)` in `apps/web/src/app/actions.ts`, reading `episode_id`, `headline`, `subline`, `tag`, `template_id`, then redirecting `/cover`.

- [ ] **Step 4: Add `/cover` page**

Create a server page that loads today episode and cover draft, shows:
- 4:3 preview frame using headline/subline/tag.
- Form fields for headline/subline/tag.
- Template radio buttons.
- Save button.
- Links back to `/script` and `/draft`.

- [ ] **Step 5: Add nav and draft CTA**

Add `navItems` entry `{ href: "/cover", label: "封面", icon: "cover" }` after `/script`, and add a `/draft` CTA to open the cover editor.

- [ ] **Step 6: Add CSS**

Define `.cover-page`, `.cover-preview-frame`, `.cover-editor-form`, `.cover-template-grid`, `.cover-template-option`, and responsive constraints.

- [ ] **Step 7: Run web checks**

Run:

```bash
CI=true corepack pnpm test:web-contract
CI=true corepack pnpm test:pixel-style
CI=true corepack pnpm typecheck:web
CI=true corepack pnpm lint:web
CI=true corepack pnpm build:web
```

Expected: all pass.

### Task 4: Browser Verification And Commit

**Files:**
- No code changes unless verification exposes a layout issue.

- [ ] **Step 1: Run full API suite**

Run: `cd apps/api && uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Start local API and Web**

Run API on `8000`, Web on `3000`.

- [ ] **Step 3: Use Playwright to inspect `/cover`**

Open `http://localhost:3000/cover`, snapshot, and save a full-page screenshot to `output/playwright/cover-editor.png`.

- [ ] **Step 4: Stop local servers**

Send Ctrl-C to both sessions.

- [ ] **Step 5: Commit and push**

```bash
git add docs/superpowers/plans/2026-06-08-cover-editor.md apps/api apps/web
git commit -m "feat: add cover editor workspace"
git push origin feature/creator-profile
```

---

## Self-Review

- Spec coverage: implements the 2.1C cover editor slice from the personal creator workflow design: editable cover fields, template preview, and draft package cover source through existing `cover_copy`.
- Scope control: does not implement subtitle editing, complex bitmap manipulation, platform upload, or persistent advanced cover layout state.
- Type consistency: backend uses `CoverDraftResponse`, `CoverUpdateRequest`; frontend uses `CoverDraft`, `CoverTemplate`; route is consistently `/api/episodes/{episode_id}/cover`.
