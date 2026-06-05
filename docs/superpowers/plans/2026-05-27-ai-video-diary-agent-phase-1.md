# AI Video Diary Agent Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable front/back separated MVP shell for AI视频日记 Agent Studio with a pixel-style web interface, mocked Agent workflow, and stable API contracts for the later real ingestion/video pipeline.

**Architecture:** This phase creates a local monorepo with a Next.js web app and a FastAPI backend. The backend exposes episode/topic/script workflow APIs backed by an in-memory repository for speed, while the frontend renders the pixel-style daily workbench, topic review, script review, and status panels against those APIs.

**Tech Stack:** Next.js, React, TypeScript, CSS modules/global CSS, FastAPI, Pydantic, pytest, Playwright later for visual verification.

---

## Scope Check

The full PRD contains multiple independent subsystems: knowledge ingestion, external connectors, LangGraph orchestration, video processing, cover generation, and Jianying draft generation. This plan intentionally implements only Phase 1:

```text
Pixel-style frontend shell
Backend API contract
Mock episode workflow
Topic review
Script review
Basic feedback capture
```

This phase does not implement real Obsidian indexing, Product Hunt fetching, FFmpeg processing, or Jianying draft writing. Those get separate plans after this foundation is running.

## File Structure

Create:

```text
package.json
pnpm-workspace.yaml
.gitignore
README.md
apps/api/pyproject.toml
apps/api/app/__init__.py
apps/api/app/main.py
apps/api/app/models.py
apps/api/app/schemas.py
apps/api/app/repository.py
apps/api/app/services.py
apps/api/tests/test_episode_flow.py
apps/web/package.json
apps/web/next.config.mjs
apps/web/tsconfig.json
apps/web/src/app/layout.tsx
apps/web/src/app/page.tsx
apps/web/src/app/topics/page.tsx
apps/web/src/app/script/page.tsx
apps/web/src/app/globals.css
apps/web/src/lib/api.ts
apps/web/src/lib/types.ts
apps/web/src/components/AppShell.tsx
apps/web/src/components/EpisodeProgress.tsx
apps/web/src/components/PixelAgentPanel.tsx
apps/web/src/components/MetricCard.tsx
apps/web/src/components/TopicCandidateCard.tsx
apps/web/src/components/ScriptSectionEditor.tsx
```

Responsibilities:

- `apps/api/app/models.py`: internal domain models and enums.
- `apps/api/app/schemas.py`: request/response schemas for frontend contract.
- `apps/api/app/repository.py`: in-memory episode store for Phase 1.
- `apps/api/app/services.py`: mock Agent behavior for topics, scripts, and feedback.
- `apps/api/app/main.py`: FastAPI routes.
- `apps/web/src/app/*`: Next.js routes.
- `apps/web/src/components/*`: pixel-style reusable UI components.
- `apps/web/src/lib/api.ts`: typed API client.
- `apps/web/src/lib/types.ts`: frontend TypeScript data types.

---

### Task 1: Scaffold The Monorepo

**Files:**
- Create: `package.json`
- Create: `pnpm-workspace.yaml`
- Create: `.gitignore`
- Create: `README.md`

- [ ] **Step 1: Create root package config**

Create `package.json`:

```json
{
  "name": "ai-video-diary-agent-studio",
  "private": true,
  "scripts": {
    "dev:web": "pnpm --dir apps/web dev",
    "build:web": "pnpm --dir apps/web build",
    "lint:web": "pnpm --dir apps/web lint",
    "dev:api": "cd apps/api && uvicorn app.main:app --reload --port 8000",
    "test:api": "cd apps/api && pytest"
  }
}
```

- [ ] **Step 2: Create workspace file**

Create `pnpm-workspace.yaml`:

```yaml
packages:
  - "apps/*"
```

- [ ] **Step 3: Create ignore file**

Create `.gitignore`:

```gitignore
node_modules/
.next/
dist/
__pycache__/
.pytest_cache/
.venv/
.env
.DS_Store
```

- [ ] **Step 4: Create README**

Create `README.md`:

```markdown
# AI视频日记 Agent Studio

Local-first content production Agent for AI video diaries.

Phase 1 delivers:

- Pixel-style Next.js workbench
- FastAPI workflow API
- Mock topic/script generation
- Human review flow

Run API:

```bash
pnpm dev:api
```

Run web:

```bash
pnpm dev:web
```
```

- [ ] **Step 5: Verify root scripts exist**

Run:

```bash
node -e "const p=require('./package.json'); console.log(Object.keys(p.scripts).join('\\n'))"
```

Expected output contains:

```text
dev:web
build:web
lint:web
dev:api
test:api
```

- [ ] **Step 6: Commit**

```bash
git add package.json pnpm-workspace.yaml .gitignore README.md
git commit -m "chore: scaffold ai video diary workspace"
```

---

### Task 2: Build Backend Domain Models

**Files:**
- Create: `apps/api/pyproject.toml`
- Create: `apps/api/app/__init__.py`
- Create: `apps/api/app/models.py`
- Create: `apps/api/app/schemas.py`

- [ ] **Step 1: Create Python project config**

Create `apps/api/pyproject.toml`:

```toml
[project]
name = "ai-video-diary-agent-api"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn>=0.30.0",
  "pydantic>=2.8.0",
  "pytest>=8.0.0",
  "httpx>=0.27.0"
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 2: Create package marker**

Create `apps/api/app/__init__.py`:

```python
"""AI video diary Agent API."""
```

- [ ] **Step 3: Create domain models**

Create `apps/api/app/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class EpisodeStatus(StrEnum):
    CREATED = "created"
    SOURCES_INGESTING = "sources_ingesting"
    TOPICS_READY = "topics_ready"
    TOPIC_APPROVED = "topic_approved"
    SCRIPT_READY = "script_ready"
    SCRIPT_APPROVED = "script_approved"
    WAITING_VIDEO = "waiting_video"
    VIDEO_PROCESSING = "video_processing"
    DRAFT_READY = "draft_ready"
    PUBLISHED = "published"
    FAILED = "failed"


class FeedbackValue(StrEnum):
    UP = "up"
    DOWN = "down"


@dataclass
class SourceRef:
    source_type: str
    title: str
    ref: str


@dataclass
class TopicCandidate:
    id: str
    title: str
    core_viewpoint: str
    reason: str
    audience: str
    outline: list[str]
    score: int
    source_refs: list[SourceRef]
    feedback: FeedbackValue | None = None


@dataclass
class ScriptSection:
    id: str
    title: str
    estimated_seconds: int
    content: str
    feedback: FeedbackValue | None = None


@dataclass
class ScriptPackage:
    id: str
    hook: str
    sections: list[ScriptSection]
    title_options: list[str]
    cover_copy: str
    publish_copy: str
    comment_prompt: str


@dataclass
class Episode:
    id: str
    day_number: int
    episode_date: date
    status: EpisodeStatus
    topics: list[TopicCandidate] = field(default_factory=list)
    selected_topic_id: str | None = None
    script: ScriptPackage | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Create API schemas**

Create `apps/api/app/schemas.py`:

```python
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models import EpisodeStatus, FeedbackValue


class SourceRefResponse(BaseModel):
    source_type: str
    title: str
    ref: str


class TopicCandidateResponse(BaseModel):
    id: str
    title: str
    core_viewpoint: str
    reason: str
    audience: str
    outline: list[str]
    score: int = Field(ge=0, le=100)
    source_refs: list[SourceRefResponse]
    feedback: FeedbackValue | None


class ScriptSectionResponse(BaseModel):
    id: str
    title: str
    estimated_seconds: int
    content: str
    feedback: FeedbackValue | None


class ScriptPackageResponse(BaseModel):
    id: str
    hook: str
    sections: list[ScriptSectionResponse]
    title_options: list[str]
    cover_copy: str
    publish_copy: str
    comment_prompt: str


class EpisodeResponse(BaseModel):
    id: str
    day_number: int
    episode_date: date
    status: EpisodeStatus
    topics: list[TopicCandidateResponse]
    selected_topic_id: str | None
    script: ScriptPackageResponse | None
    created_at: datetime
    updated_at: datetime


class ApproveTopicRequest(BaseModel):
    topic_id: str


class FeedbackRequest(BaseModel):
    target_type: str
    target_id: str
    value: FeedbackValue
    reason: str | None = None
```

- [ ] **Step 5: Verify imports**

Run:

```bash
cd apps/api && python -c "from app.models import Episode; from app.schemas import EpisodeResponse; print('ok')"
```

Expected output:

```text
ok
```

- [ ] **Step 6: Commit**

```bash
git add apps/api
git commit -m "feat: add episode domain models"
```

---

### Task 3: Implement Mock Backend Workflow API

**Files:**
- Create: `apps/api/app/repository.py`
- Create: `apps/api/app/services.py`
- Create: `apps/api/app/main.py`
- Create: `apps/api/tests/test_episode_flow.py`

- [ ] **Step 1: Write failing API tests**

Create `apps/api/tests/test_episode_flow.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_today_episode_flow_generates_topics_and_script():
    today = client.get("/api/episodes/today").json()
    assert today["status"] == "created"

    topics_response = client.post(f"/api/episodes/{today['id']}/generate-topics")
    assert topics_response.status_code == 200
    topics = topics_response.json()["topics"]
    assert len(topics) == 4
    assert topics[0]["source_refs"]

    approved = client.post(
        f"/api/episodes/{today['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    ).json()
    assert approved["status"] == "topic_approved"
    assert approved["selected_topic_id"] == topics[0]["id"]

    script = client.post(f"/api/episodes/{today['id']}/generate-script").json()["script"]
    assert script["hook"]
    assert len(script["sections"]) >= 5


def test_feedback_updates_topic_feedback_value():
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]

    feedback_response = client.post(
        f"/api/episodes/{episode['id']}/feedback",
        json={
            "target_type": "topic",
            "target_id": topics[0]["id"],
            "value": "down",
            "reason": "太泛",
        },
    )

    assert feedback_response.status_code == 200
    updated_topic = feedback_response.json()["topics"][0]
    assert updated_topic["feedback"] == "down"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pnpm test:api
```

Expected: FAIL because `app.main` does not exist.

- [ ] **Step 3: Implement repository**

Create `apps/api/app/repository.py`:

```python
from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from app.models import Episode, EpisodeStatus


class EpisodeRepository:
    def __init__(self) -> None:
        self._episodes: dict[str, Episode] = {}
        self._today_id: str | None = None

    def create(self) -> Episode:
        episode = Episode(
            id=f"ep_{uuid4().hex[:8]}",
            day_number=len(self._episodes) + 1,
            episode_date=date.today(),
            status=EpisodeStatus.CREATED,
        )
        self._episodes[episode.id] = episode
        self._today_id = episode.id
        return episode

    def today(self) -> Episode:
        if self._today_id is None:
            return self.create()
        return self._episodes[self._today_id]

    def get(self, episode_id: str) -> Episode:
        return self._episodes[episode_id]

    def save(self, episode: Episode) -> Episode:
        episode.updated_at = datetime.utcnow()
        self._episodes[episode.id] = episode
        return episode
```

- [ ] **Step 4: Implement mock Agent service**

Create `apps/api/app/services.py`:

```python
from __future__ import annotations

from uuid import uuid4

from app.models import (
    Episode,
    EpisodeStatus,
    FeedbackValue,
    ScriptPackage,
    ScriptSection,
    SourceRef,
    TopicCandidate,
)


class EpisodeService:
    def generate_topics(self, episode: Episode) -> Episode:
        episode.topics = [
            TopicCandidate(
                id=f"topic_{uuid4().hex[:6]}",
                title="AI Agent 真正的护城河，不是模型而是工作流",
                core_viewpoint="企业和开发者真正的差异化来自可复用、可迭代的工作流设计。",
                reason="结合了你的 Obsidian 笔记、Product Hunt Agent 新品和过往点赞偏好。",
                audience="AI 产品经理、创业者、独立开发者",
                outline=["模型能力同质化", "工作流承载真实业务", "反馈闭环形成壁垒"],
                score=94,
                source_refs=[
                    SourceRef("Obsidian", "AI产品留存问题", "[[AI产品留存问题]]"),
                    SourceRef("Product Hunt", "Agent workflow tool", "producthunt:item:agent-workflow"),
                    SourceRef("历史反馈", "你点赞过产品判断型选题", "preference:product-judgement"),
                ],
            ),
            TopicCandidate(
                id=f"topic_{uuid4().hex[:6]}",
                title="为什么拍照识别卡路里的软件很难长期活下去？",
                core_viewpoint="识别能力只是入口，信任、习惯和付费价值才决定生存。",
                reason="来自你最近关于 AI 健康产品的讨论，并匹配 Product Hunt 上的健康类 AI 新品。",
                audience="AI 创业者、产品经理、健身减脂用户",
                outline=["识别不等于信任", "记录习惯很难养成", "付费价值不够强"],
                score=90,
                source_refs=[
                    SourceRef("Obsidian", "用户习惯比功能强更重要", "[[用户习惯比功能强更重要]]"),
                    SourceRef("观猹", "AI 健康管理工具", "watcha:item:ai-health"),
                ],
            ),
            TopicCandidate(
                id=f"topic_{uuid4().hex[:6]}",
                title="AI 工具为什么越多，普通人反而越不会用？",
                core_viewpoint="工具爆炸带来的不是效率，而是选择成本和流程断裂。",
                reason="符合你偏好的反常识表达，也适合做系列化内容。",
                audience="普通职场人、知识工作者、AI 工具用户",
                outline=["工具太多造成选择压力", "缺少稳定工作流", "需要从任务而不是工具出发"],
                score=87,
                source_refs=[
                    SourceRef("Obsidian", "AI工具不是越多越好", "[[AI工具不是越多越好]]"),
                ],
            ),
            TopicCandidate(
                id=f"topic_{uuid4().hex[:6]}",
                title="普通人做 AI 个人 IP，最重要的不是懂技术",
                core_viewpoint="持续表达真实判断，比罗列工具功能更能建立信任。",
                reason="适合作为 AI视频日记栏目定位说明。",
                audience="想做个人 IP 的技术人和创业者",
                outline=["不要只讲工具", "讲真实问题", "形成稳定栏目"],
                score=82,
                source_refs=[
                    SourceRef("历史反馈", "你偏好真实项目经验", "preference:real-practice"),
                ],
            ),
        ]
        episode.status = EpisodeStatus.TOPICS_READY
        return episode

    def approve_topic(self, episode: Episode, topic_id: str) -> Episode:
        if topic_id not in {topic.id for topic in episode.topics}:
            raise ValueError("topic not found")
        episode.selected_topic_id = topic_id
        episode.status = EpisodeStatus.TOPIC_APPROVED
        return episode

    def generate_script(self, episode: Episode) -> Episode:
        selected = next(topic for topic in episode.topics if topic.id == episode.selected_topic_id)
        episode.script = ScriptPackage(
            id=f"script_{uuid4().hex[:6]}",
            hook=f"今天这个选题我想讲一个真实判断：{selected.core_viewpoint}",
            sections=[
                ScriptSection("hook", "开头 3 秒钩子", 12, f"{selected.title} 这件事，表面看是技术问题，其实是产品生存问题。"),
                ScriptSection("why", "为什么今天聊这个", 24, selected.reason),
                ScriptSection("point_1", "原因 1", 36, selected.outline[0]),
                ScriptSection("point_2", "原因 2", 36, selected.outline[1]),
                ScriptSection("point_3", "原因 3", 36, selected.outline[2]),
                ScriptSection("ending", "结尾金句", 18, "AI 产品最终比拼的不是演示效果，而是能不能进入真实生活和真实工作流。"),
            ],
            title_options=[selected.title, f"第 1 天：{selected.title}", f"一个 AI 产品判断：{selected.core_viewpoint}"],
            cover_copy=selected.title,
            publish_copy=f"AI视频日记今天聊：{selected.title}。我的判断是：{selected.core_viewpoint}",
            comment_prompt="你最近有没有遇到看起来很强、但自己不会天天用的 AI 产品？",
        )
        episode.status = EpisodeStatus.SCRIPT_READY
        return episode

    def apply_feedback(self, episode: Episode, target_type: str, target_id: str, value: FeedbackValue) -> Episode:
        if target_type == "topic":
            for topic in episode.topics:
                if topic.id == target_id:
                    topic.feedback = value
        if target_type == "script_section" and episode.script:
            for section in episode.script.sections:
                if section.id == target_id:
                    section.feedback = value
        return episode
```

- [ ] **Step 5: Implement FastAPI routes**

Create `apps/api/app/main.py`:

```python
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.repository import EpisodeRepository
from app.schemas import ApproveTopicRequest, EpisodeResponse, FeedbackRequest
from app.services import EpisodeService

app = FastAPI(title="AI视频日记 Agent Studio API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

repo = EpisodeRepository()
service = EpisodeService()


@app.get("/api/episodes/today", response_model=EpisodeResponse)
def get_today_episode() -> EpisodeResponse:
    return EpisodeResponse.model_validate(repo.today(), from_attributes=True)


@app.post("/api/episodes", response_model=EpisodeResponse)
def create_episode() -> EpisodeResponse:
    return EpisodeResponse.model_validate(repo.create(), from_attributes=True)


@app.post("/api/episodes/{episode_id}/generate-topics", response_model=EpisodeResponse)
def generate_topics(episode_id: str) -> EpisodeResponse:
    episode = service.generate_topics(repo.get(episode_id))
    return EpisodeResponse.model_validate(repo.save(episode), from_attributes=True)


@app.post("/api/episodes/{episode_id}/approve-topic", response_model=EpisodeResponse)
def approve_topic(episode_id: str, request: ApproveTopicRequest) -> EpisodeResponse:
    try:
        episode = service.approve_topic(repo.get(episode_id), request.topic_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return EpisodeResponse.model_validate(repo.save(episode), from_attributes=True)


@app.post("/api/episodes/{episode_id}/generate-script", response_model=EpisodeResponse)
def generate_script(episode_id: str) -> EpisodeResponse:
    episode = repo.get(episode_id)
    if episode.selected_topic_id is None:
        raise HTTPException(status_code=400, detail="approve a topic first")
    episode = service.generate_script(episode)
    return EpisodeResponse.model_validate(repo.save(episode), from_attributes=True)


@app.post("/api/episodes/{episode_id}/feedback", response_model=EpisodeResponse)
def apply_feedback(episode_id: str, request: FeedbackRequest) -> EpisodeResponse:
    episode = service.apply_feedback(repo.get(episode_id), request.target_type, request.target_id, request.value)
    return EpisodeResponse.model_validate(repo.save(episode), from_attributes=True)
```

- [ ] **Step 6: Run tests**

Run:

```bash
pnpm test:api
```

Expected:

```text
2 passed
```

- [ ] **Step 7: Commit**

```bash
git add apps/api
git commit -m "feat: add mock episode workflow api"
```

---

### Task 4: Scaffold Pixel-Style Next.js App

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/next.config.mjs`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/src/app/layout.tsx`
- Create: `apps/web/src/app/globals.css`

- [ ] **Step 1: Create web package config**

Create `apps/web/package.json`:

```json
{
  "name": "@ai-video-diary/web",
  "private": true,
  "scripts": {
    "dev": "next dev --port 3000",
    "build": "next build",
    "lint": "next lint"
  },
  "dependencies": {
    "next": "^15.0.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "lucide-react": "^0.468.0"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "typescript": "^5.6.0"
  }
}
```

- [ ] **Step 2: Create Next config**

Create `apps/web/next.config.mjs`:

```js
const nextConfig = {
  reactStrictMode: true
};

export default nextConfig;
```

- [ ] **Step 3: Create TypeScript config**

Create `apps/web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "es2022"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }]
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 4: Create root layout**

Create `apps/web/src/app/layout.tsx`:

```tsx
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI视频日记 Agent Studio",
  description: "Pixel-style Agent workbench for AI video diary production"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 5: Create global pixel visual system**

Create `apps/web/src/app/globals.css`:

```css
:root {
  --bg: #faf8f2;
  --panel: #fffdf8;
  --panel-soft: #f7fbff;
  --ink: #18191f;
  --muted: #6d7280;
  --line: #e5dfd2;
  --blue: #236dff;
  --blue-soft: #eaf1ff;
  --cyan: #28b8d5;
  --orange: #ff9d2e;
  --green: #20b879;
  --red: #eb5757;
  --purple: #8b5cf6;
  --radius: 8px;
}

* {
  box-sizing: border-box;
}

html,
body {
  margin: 0;
  min-height: 100%;
  background: var(--bg);
  color: var(--ink);
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  letter-spacing: 0;
}

button,
input {
  font: inherit;
}

.pixel-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
}

.pixel-button {
  display: inline-flex;
  min-height: 40px;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 1px solid #1057d9;
  border-radius: 6px;
  background: var(--blue);
  color: #fff;
  font-weight: 700;
  cursor: pointer;
  box-shadow: inset -2px -2px 0 rgba(0, 0, 0, 0.16);
}

.pixel-button.secondary {
  background: #fff;
  color: var(--ink);
  border-color: var(--line);
  box-shadow: none;
}

.pixel-icon {
  image-rendering: pixelated;
  font-size: 26px;
  line-height: 1;
}
```

- [ ] **Step 6: Install dependencies**

Run:

```bash
pnpm install
```

Expected: dependencies install successfully and `pnpm-lock.yaml` is created.

- [ ] **Step 7: Commit**

```bash
git add apps/web package.json pnpm-workspace.yaml pnpm-lock.yaml
git commit -m "feat: scaffold pixel web app"
```

---

### Task 5: Add Typed API Client

**Files:**
- Create: `apps/web/src/lib/types.ts`
- Create: `apps/web/src/lib/api.ts`

- [ ] **Step 1: Create frontend types**

Create `apps/web/src/lib/types.ts`:

```ts
export type EpisodeStatus =
  | "created"
  | "sources_ingesting"
  | "topics_ready"
  | "topic_approved"
  | "script_ready"
  | "script_approved"
  | "waiting_video"
  | "video_processing"
  | "draft_ready"
  | "published"
  | "failed";

export type FeedbackValue = "up" | "down";

export type SourceRef = {
  source_type: string;
  title: string;
  ref: string;
};

export type TopicCandidate = {
  id: string;
  title: string;
  core_viewpoint: string;
  reason: string;
  audience: string;
  outline: string[];
  score: number;
  source_refs: SourceRef[];
  feedback: FeedbackValue | null;
};

export type ScriptSection = {
  id: string;
  title: string;
  estimated_seconds: number;
  content: string;
  feedback: FeedbackValue | null;
};

export type ScriptPackage = {
  id: string;
  hook: string;
  sections: ScriptSection[];
  title_options: string[];
  cover_copy: string;
  publish_copy: string;
  comment_prompt: string;
};

export type Episode = {
  id: string;
  day_number: number;
  episode_date: string;
  status: EpisodeStatus;
  topics: TopicCandidate[];
  selected_topic_id: string | null;
  script: ScriptPackage | null;
  created_at: string;
  updated_at: string;
};
```

- [ ] **Step 2: Create API client**

Create `apps/web/src/lib/api.ts`:

```ts
import type { Episode, FeedbackValue } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    cache: "no-store"
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function getTodayEpisode(): Promise<Episode> {
  return request<Episode>("/api/episodes/today");
}

export function createEpisode(): Promise<Episode> {
  return request<Episode>("/api/episodes", { method: "POST" });
}

export function generateTopics(episodeId: string): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/generate-topics`, { method: "POST" });
}

export function approveTopic(episodeId: string, topicId: string): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/approve-topic`, {
    method: "POST",
    body: JSON.stringify({ topic_id: topicId })
  });
}

export function generateScript(episodeId: string): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/generate-script`, { method: "POST" });
}

export function sendFeedback(
  episodeId: string,
  targetType: string,
  targetId: string,
  value: FeedbackValue,
  reason?: string
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/feedback`, {
    method: "POST",
    body: JSON.stringify({
      target_type: targetType,
      target_id: targetId,
      value,
      reason
    })
  });
}
```

- [ ] **Step 3: Type-check web app**

Run:

```bash
pnpm --dir apps/web build
```

Expected: build fails only because no pages exist yet. There should be no type errors from `src/lib`.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/lib
git commit -m "feat: add typed frontend api client"
```

---

### Task 6: Build Pixel App Shell

**Files:**
- Create: `apps/web/src/components/AppShell.tsx`
- Create: `apps/web/src/components/PixelAgentPanel.tsx`

- [ ] **Step 1: Create AppShell**

Create `apps/web/src/components/AppShell.tsx`:

```tsx
import Link from "next/link";
import { Bell, BookOpen, FileText, Home, Settings, Sparkles, Video } from "lucide-react";

const navItems = [
  { href: "/", label: "今日工作台", icon: "💻" },
  { href: "/topics", label: "选题池", icon: "🧾" },
  { href: "/script", label: "口播稿", icon: "📋" },
  { href: "/video", label: "视频处理", icon: "🎞️" },
  { href: "/draft", label: "剪映草稿", icon: "✂️" },
  { href: "/knowledge", label: "知识库", icon: "🗂️" },
  { href: "/sources", label: "信息源", icon: "🌐" },
  { href: "/learning", label: "反馈学习", icon: "💙" },
  { href: "/settings", label: "设置", icon: "⚙️" }
];

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="pixel-logo">🤖</span>
          <div>
            <strong>AI视频日记</strong>
            <span>Agent Studio</span>
          </div>
        </div>
        <nav>
          {navItems.map((item) => (
            <Link key={item.href} href={item.href} className="nav-item">
              <span className="pixel-icon">{item.icon}</span>
              <span>{item.label}</span>
            </Link>
          ))}
        </nav>
        <div className="sidebar-note">
          <span>专注创作，</span>
          <span>每天进步一点点 ✨</span>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb">今日工作台</div>
          <div className="topbar-center">📅 Day 128 · 2025-05-20 · 星期二</div>
          <div className="topbar-actions">
            <div className="search-box">搜索 Episode、选题、素材...</div>
            <Bell size={20} />
            <span className="avatar">👨🏻‍💻</span>
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}
```

- [ ] **Step 2: Create PixelAgentPanel**

Create `apps/web/src/components/PixelAgentPanel.tsx`:

```tsx
type AgentTask = {
  label: string;
  progress: number;
  icon: string;
};

const tasks: AgentTask[] = [
  { label: "Obsidian 同步", progress: 100, icon: "💎" },
  { label: "Product Hunt 同步", progress: 100, icon: "🅿️" },
  { label: "选题生成", progress: 100, icon: "💡" },
  { label: "口播稿生成", progress: 35, icon: "📋" },
  { label: "剪映草稿", progress: 0, icon: "✂️" }
];

export function PixelAgentPanel() {
  return (
    <aside className="agent-panel pixel-card">
      <h2>✦ Agent 状态</h2>
      <div className="agent-robot" aria-hidden="true">🤖</div>
      <p className="agent-copy">
        今天我已读取 <strong>18</strong> 条新笔记，同步 <strong>6</strong> 个 AI 产品，生成 <strong>4</strong> 个选题，正在等待你确认。
      </p>
      <div className="agent-task-list">
        {tasks.map((task) => (
          <div className="agent-task" key={task.label}>
            <span>{task.icon}</span>
            <span>{task.label}</span>
            <div className="progress-track">
              <div className="progress-fill" style={{ width: `${task.progress}%` }} />
            </div>
            <strong>{task.progress === 0 ? "待开始" : `${task.progress}%`}</strong>
          </div>
        ))}
      </div>
      <div className="preference-summary">
        <h3>偏好学习摘要</h3>
        <p>偏好主题：AI工具实测 / 个人成长 / 创作方法</p>
        <p>偏好标题：观点型、反差型</p>
      </div>
    </aside>
  );
}
```

- [ ] **Step 3: Add shell CSS**

Append to `apps/web/src/app/globals.css`:

```css
.app-shell {
  display: grid;
  min-height: 100vh;
  grid-template-columns: 232px 1fr;
}

.sidebar {
  display: flex;
  flex-direction: column;
  gap: 20px;
  border-right: 1px solid var(--line);
  background: #fffdf8;
  padding: 18px 16px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 48px;
}

.brand strong {
  display: block;
  font-size: 18px;
}

.brand span:last-child {
  display: block;
  color: var(--muted);
  font-size: 12px;
}

.pixel-logo {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid var(--ink);
  border-radius: 6px;
  background: var(--blue-soft);
  box-shadow: inset -2px -2px 0 rgba(0, 0, 0, 0.12);
}

.sidebar nav {
  display: grid;
  gap: 8px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 50px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: 7px;
  color: var(--ink);
  text-decoration: none;
  font-weight: 650;
}

.nav-item:first-child,
.nav-item:hover {
  border-color: #b8cdee;
  background: var(--blue-soft);
  color: #1c5bd7;
}

.sidebar-note {
  margin-top: auto;
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 14px;
  color: var(--muted);
  font-size: 13px;
}

.sidebar-note span {
  display: block;
}

.main-area {
  min-width: 0;
}

.topbar {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  min-height: 72px;
  border-bottom: 1px solid var(--line);
  background: #fffdf8;
  padding: 0 24px;
}

.breadcrumb {
  font-size: 20px;
  font-weight: 800;
}

.topbar-center {
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  padding: 8px 16px;
  font-weight: 700;
}

.topbar-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 14px;
}

.search-box {
  width: 300px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  color: #b2b0aa;
  padding: 10px 12px;
}

.avatar {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 50%;
  background: var(--blue-soft);
}

.agent-panel {
  display: grid;
  gap: 18px;
  padding: 22px;
}

.agent-panel h2,
.agent-panel h3 {
  margin: 0;
}

.agent-robot {
  display: grid;
  place-items: center;
  min-height: 118px;
  font-size: 74px;
  image-rendering: pixelated;
}

.agent-copy {
  margin: 0;
  color: #3a3f4a;
  line-height: 1.8;
}

.agent-task-list {
  display: grid;
  gap: 12px;
}

.agent-task {
  display: grid;
  grid-template-columns: 26px 120px 1fr 54px;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.progress-track {
  height: 7px;
  overflow: hidden;
  border-radius: 999px;
  background: #ebe7df;
}

.progress-fill {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--cyan), var(--green));
}

.preference-summary {
  border-top: 1px solid var(--line);
  padding-top: 14px;
  color: var(--muted);
  font-size: 13px;
}
```

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/components apps/web/src/app/globals.css
git commit -m "feat: add pixel app shell"
```

---

### Task 7: Build Today Workbench Page

**Files:**
- Create: `apps/web/src/components/EpisodeProgress.tsx`
- Create: `apps/web/src/components/MetricCard.tsx`
- Create: `apps/web/src/app/page.tsx`

- [ ] **Step 1: Create progress component**

Create `apps/web/src/components/EpisodeProgress.tsx`:

```tsx
const steps = [
  { label: "信息摄入", icon: "📥", state: "已完成" },
  { label: "选题生成", icon: "💡", state: "已完成" },
  { label: "选题复核", icon: "📋", state: "进行中" },
  { label: "口播稿", icon: "📄", state: "待处理" },
  { label: "视频等待", icon: "☁️", state: "待处理" },
  { label: "AI处理", icon: "🤖", state: "待处理" },
  { label: "封面", icon: "🖼️", state: "待处理" },
  { label: "剪映草稿", icon: "✂️", state: "待处理" },
  { label: "发布反馈", icon: "🛩️", state: "待处理" }
];

export function EpisodeProgress() {
  return (
    <section className="episode-progress pixel-card">
      {steps.map((step, index) => (
        <div className="progress-step" key={step.label}>
          <span className="step-icon">{step.icon}</span>
          <strong>{step.label}</strong>
          <span className={`step-state ${step.state === "进行中" ? "active" : ""}`}>{step.state}</span>
          {index < steps.length - 1 ? <span className="step-arrow">→</span> : null}
        </div>
      ))}
    </section>
  );
}
```

- [ ] **Step 2: Create metric card**

Create `apps/web/src/components/MetricCard.tsx`:

```tsx
export function MetricCard({ icon, value, label, tone }: { icon: string; value: string; label: string; tone: string }) {
  return (
    <div className={`metric-card pixel-card ${tone}`}>
      <span className="pixel-icon">{icon}</span>
      <div>
        <strong>{value}</strong>
        <span>{label}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create workbench page**

Create `apps/web/src/app/page.tsx`:

```tsx
import { AppShell } from "@/components/AppShell";
import { EpisodeProgress } from "@/components/EpisodeProgress";
import { MetricCard } from "@/components/MetricCard";
import { PixelAgentPanel } from "@/components/PixelAgentPanel";
import { getTodayEpisode, generateTopics } from "@/lib/api";

export default async function HomePage() {
  const episode = await getTodayEpisode();

  return (
    <AppShell>
      <div className="workbench-grid">
        <div className="workbench-main">
          <EpisodeProgress />

          <section className="task-banner pixel-card">
            <div>
              <h1>今天你要做什么</h1>
              <p>确认 1 个选题，生成口播稿，然后录制今天的视频日记。</p>
            </div>
            <div className="pixel-person">🧑🏻‍💻</div>
            <a className="pixel-button" href="/topics">继续今天的流程 →</a>
          </section>

          <section className="metric-grid">
            <MetricCard icon="🗂️" value="18" label="新增素材" tone="blue" />
            <MetricCard icon="💡" value="4" label="生成选题" tone="green" />
            <MetricCard icon="✅" value="1" label="已确认" tone="purple" />
            <MetricCard icon="🔥" value="27" label="发布连续天数" tone="orange" />
          </section>

          <section className="current-topic pixel-card">
            <div>
              <span className="section-kicker">当前选题</span>
              <h2>{episode.topics[0]?.title ?? "还没有生成今日选题"}</h2>
              <p>{episode.topics[0]?.core_viewpoint ?? "进入选题池生成今天的候选选题。"}</p>
            </div>
            <div className="topic-actions">
              <a className="pixel-button secondary" href="/topics">查看详情</a>
              <a className="pixel-button" href="/script">生成口播稿</a>
            </div>
          </section>
        </div>
        <PixelAgentPanel />
      </div>
    </AppShell>
  );
}
```

- [ ] **Step 4: Add workbench CSS**

Append to `apps/web/src/app/globals.css`:

```css
.workbench-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 16px;
  padding: 20px;
}

.workbench-main {
  display: grid;
  gap: 16px;
}

.episode-progress {
  display: grid;
  grid-template-columns: repeat(9, minmax(82px, 1fr));
  align-items: start;
  gap: 8px;
  padding: 20px 14px;
}

.progress-step {
  position: relative;
  display: grid;
  justify-items: center;
  gap: 6px;
  text-align: center;
  font-size: 13px;
}

.step-icon {
  font-size: 28px;
}

.step-state {
  color: var(--muted);
  font-size: 12px;
}

.step-state.active {
  color: var(--blue);
  font-weight: 800;
}

.step-arrow {
  position: absolute;
  right: -10px;
  top: 20px;
  color: var(--muted);
}

.task-banner {
  display: grid;
  grid-template-columns: 1fr 120px 220px;
  align-items: center;
  gap: 20px;
  padding: 22px;
}

.task-banner h1,
.task-banner p {
  margin: 0;
}

.task-banner h1 {
  margin-bottom: 8px;
}

.pixel-person {
  font-size: 72px;
  text-align: center;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}

.metric-card {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 86px;
  padding: 16px;
}

.metric-card strong {
  display: block;
  font-size: 28px;
}

.metric-card span:last-child {
  color: var(--muted);
}

.metric-card.blue {
  background: #f5f9ff;
}

.metric-card.green {
  background: #f3fff9;
}

.metric-card.purple {
  background: #faf7ff;
}

.metric-card.orange {
  background: #fff8ee;
}

.current-topic {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 20px;
  padding: 22px;
}

.section-kicker {
  color: var(--purple);
  font-weight: 800;
}

.current-topic h2 {
  margin: 10px 0;
  font-size: 24px;
}

.current-topic p {
  margin: 0;
  color: var(--muted);
  line-height: 1.8;
}

.topic-actions {
  display: flex;
  align-items: end;
  gap: 12px;
}
```

- [ ] **Step 5: Run page locally**

Run API:

```bash
pnpm dev:api
```

Run web in another terminal:

```bash
pnpm dev:web
```

Expected: `http://localhost:3000` renders a pixel-style workbench with sidebar, progress flow, metric cards, task banner, and Agent panel.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app apps/web/src/components
git commit -m "feat: build pixel daily workbench"
```

---

### Task 8: Build Topic Review Page

**Files:**
- Create: `apps/web/src/components/TopicCandidateCard.tsx`
- Create: `apps/web/src/app/topics/page.tsx`

- [ ] **Step 1: Create topic card**

Create `apps/web/src/components/TopicCandidateCard.tsx`:

```tsx
import type { TopicCandidate } from "@/lib/types";

export function TopicCandidateCard({ topic }: { topic: TopicCandidate }) {
  return (
    <article className="topic-card pixel-card">
      <div className="topic-card-header">
        <span className="score-badge">{topic.score}</span>
        <span>{topic.feedback === "down" ? "已踩" : topic.feedback === "up" ? "已赞" : "待复核"}</span>
      </div>
      <h2>{topic.title}</h2>
      <p>{topic.core_viewpoint}</p>
      <div className="source-row">
        {topic.source_refs.map((source) => (
          <span key={`${source.source_type}-${source.ref}`} className="source-badge">
            {source.source_type}
          </span>
        ))}
      </div>
    </article>
  );
}
```

- [ ] **Step 2: Create topic page**

Create `apps/web/src/app/topics/page.tsx`:

```tsx
import { AppShell } from "@/components/AppShell";
import { TopicCandidateCard } from "@/components/TopicCandidateCard";
import { generateTopics, getTodayEpisode } from "@/lib/api";

export default async function TopicsPage() {
  let episode = await getTodayEpisode();
  if (episode.topics.length === 0) {
    episode = await generateTopics(episode.id);
  }
  const selected = episode.topics[0];

  return (
    <AppShell>
      <div className="topic-page">
        <section className="topic-list">
          {episode.topics.map((topic) => (
            <TopicCandidateCard key={topic.id} topic={topic} />
          ))}
        </section>

        <section className="topic-detail pixel-card">
          <span className="section-kicker">推荐选题</span>
          <h1>{selected.title}</h1>
          <p>{selected.reason}</p>
          <h3>3 分钟结构</h3>
          <ol>
            {selected.outline.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ol>
          <h3>来源依据</h3>
          <div className="evidence-list">
            {selected.source_refs.map((source) => (
              <div className="evidence-item" key={source.ref}>
                <strong>{source.source_type}</strong>
                <span>{source.title}</span>
                <code>{source.ref}</code>
              </div>
            ))}
          </div>
          <div className="topic-actions">
            <a className="pixel-button" href="/script">通过并生成口播稿</a>
            <button className="pixel-button secondary" type="button">踩这个选题</button>
            <button className="pixel-button secondary" type="button">换一个角度</button>
          </div>
        </section>
      </div>
    </AppShell>
  );
}
```

- [ ] **Step 3: Add topic CSS**

Append to `apps/web/src/app/globals.css`:

```css
.topic-page {
  display: grid;
  grid-template-columns: 390px 1fr;
  gap: 16px;
  padding: 20px;
}

.topic-list {
  display: grid;
  gap: 12px;
  align-content: start;
}

.topic-card {
  display: grid;
  gap: 12px;
  padding: 16px;
}

.topic-card-header {
  display: flex;
  justify-content: space-between;
  color: var(--muted);
  font-size: 13px;
}

.score-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 42px;
  height: 26px;
  border-radius: 6px;
  background: var(--blue-soft);
  color: var(--blue);
  font-weight: 800;
}

.topic-card h2 {
  margin: 0;
  font-size: 18px;
}

.topic-card p {
  margin: 0;
  color: var(--muted);
  line-height: 1.6;
}

.source-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.source-badge {
  border: 1px solid var(--line);
  border-radius: 5px;
  background: #fff;
  padding: 4px 8px;
  color: var(--purple);
  font-size: 12px;
  font-weight: 700;
}

.topic-detail {
  padding: 26px;
}

.topic-detail h1 {
  max-width: 780px;
  margin: 10px 0 14px;
  font-size: 32px;
  line-height: 1.25;
}

.topic-detail p,
.topic-detail li {
  color: #343944;
  line-height: 1.8;
}

.evidence-list {
  display: grid;
  gap: 10px;
  margin-bottom: 24px;
}

.evidence-item {
  display: grid;
  grid-template-columns: 110px 1fr auto;
  gap: 12px;
  align-items: center;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  padding: 12px;
}

.evidence-item code {
  color: var(--muted);
}
```

- [ ] **Step 4: Verify topic page**

Run:

```bash
pnpm dev:web
```

Expected: `http://localhost:3000/topics` shows 4 topic cards, source badges, source evidence, and pixel-style review actions.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/topics apps/web/src/components/TopicCandidateCard.tsx apps/web/src/app/globals.css
git commit -m "feat: add topic review page"
```

---

### Task 9: Build Script Review Page

**Files:**
- Create: `apps/web/src/components/ScriptSectionEditor.tsx`
- Create: `apps/web/src/app/script/page.tsx`

- [ ] **Step 1: Create script section component**

Create `apps/web/src/components/ScriptSectionEditor.tsx`:

```tsx
import type { ScriptSection } from "@/lib/types";

export function ScriptSectionEditor({ section }: { section: ScriptSection }) {
  return (
    <section className="script-section pixel-card">
      <div className="script-section-header">
        <div>
          <strong>{section.title}</strong>
          <span>预计 {section.estimated_seconds}s</span>
        </div>
        <div className="feedback-actions">
          <button type="button">👍</button>
          <button type="button">👎</button>
          <button type="button">重写本段</button>
        </div>
      </div>
      <textarea defaultValue={section.content} />
    </section>
  );
}
```

- [ ] **Step 2: Create script page**

Create `apps/web/src/app/script/page.tsx`:

```tsx
import { AppShell } from "@/components/AppShell";
import { ScriptSectionEditor } from "@/components/ScriptSectionEditor";
import { generateScript, generateTopics, approveTopic, getTodayEpisode } from "@/lib/api";

export default async function ScriptPage() {
  let episode = await getTodayEpisode();
  if (episode.topics.length === 0) {
    episode = await generateTopics(episode.id);
  }
  if (!episode.selected_topic_id) {
    episode = await approveTopic(episode.id, episode.topics[0].id);
  }
  if (!episode.script) {
    episode = await generateScript(episode.id);
  }

  const script = episode.script;

  return (
    <AppShell>
      <div className="script-page">
        <main className="script-main">
          <section className="script-hook pixel-card">
            <span className="section-kicker">开头 3 秒</span>
            <h1>{script.hook}</h1>
          </section>
          {script.sections.map((section) => (
            <ScriptSectionEditor key={section.id} section={section} />
          ))}
        </main>

        <aside className="script-side pixel-card">
          <h2>发布素材</h2>
          <h3>标题候选</h3>
          {script.title_options.map((title) => (
            <p key={title}>{title}</p>
          ))}
          <h3>封面文案</h3>
          <p>{script.cover_copy}</p>
          <h3>发布文案</h3>
          <p>{script.publish_copy}</p>
          <h3>评论引导</h3>
          <p>{script.comment_prompt}</p>
          <button className="pixel-button" type="button">确认脚本，等待录制视频</button>
        </aside>
      </div>
    </AppShell>
  );
}
```

- [ ] **Step 3: Add script CSS**

Append to `apps/web/src/app/globals.css`:

```css
.script-page {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 16px;
  padding: 20px;
}

.script-main {
  display: grid;
  gap: 12px;
}

.script-hook {
  padding: 22px;
}

.script-hook h1 {
  margin: 10px 0 0;
  font-size: 28px;
  line-height: 1.35;
}

.script-section {
  padding: 18px;
}

.script-section-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.script-section-header span {
  display: block;
  color: var(--muted);
  font-size: 13px;
}

.feedback-actions {
  display: flex;
  gap: 8px;
}

.feedback-actions button {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  padding: 6px 10px;
  cursor: pointer;
}

.script-section textarea {
  width: 100%;
  min-height: 96px;
  resize: vertical;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  padding: 12px;
  color: var(--ink);
  line-height: 1.8;
}

.script-side {
  display: grid;
  gap: 12px;
  align-content: start;
  padding: 20px;
}

.script-side h2,
.script-side h3,
.script-side p {
  margin: 0;
}

.script-side h3 {
  margin-top: 8px;
}

.script-side p {
  color: var(--muted);
  line-height: 1.7;
}
```

- [ ] **Step 4: Verify script page**

Run:

```bash
pnpm dev:web
```

Expected: `http://localhost:3000/script` shows a structured script editor with hook, sections, feedback buttons, and side panel materials.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/script apps/web/src/components/ScriptSectionEditor.tsx apps/web/src/app/globals.css
git commit -m "feat: add script review page"
```

---

### Task 10: Verification And Visual Acceptance

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run API tests**

Run:

```bash
pnpm test:api
```

Expected:

```text
2 passed
```

- [ ] **Step 2: Build frontend**

Run:

```bash
pnpm build:web
```

Expected: Next.js build succeeds.

- [ ] **Step 3: Manually verify pages**

Start API:

```bash
pnpm dev:api
```

Start web:

```bash
pnpm dev:web
```

Open and verify:

```text
http://localhost:3000
http://localhost:3000/topics
http://localhost:3000/script
```

Acceptance:

```text
Sidebar uses pixel-style icons.
Topbar resembles an Agent Studio control panel.
Daily progress uses icon-based workflow steps.
Right panel has pixel robot Agent status.
Cards have light backgrounds and crisp borders.
Primary action button is visually obvious.
No marketing hero page appears.
No chat window dominates the interface.
```

- [ ] **Step 4: Update README with Phase 1 usage**

Append to `README.md`:

```markdown
## Phase 1 Verification

1. Run API:

```bash
pnpm dev:api
```

2. Run web:

```bash
pnpm dev:web
```

3. Visit:

- http://localhost:3000
- http://localhost:3000/topics
- http://localhost:3000/script

The UI should look like a pixel-style AI production workbench, not a generic SaaS dashboard.
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add phase 1 verification steps"
```

---

## Follow-Up Plans After Phase 1

Create separate implementation plans for:

1. **Knowledge Ingestion Plan**
   - Obsidian Vault indexing.
   - Markdown parsing.
   - Manual link Inbox.
   - Product Hunt API.
   - 观猹/Watcha source adapter.

2. **LangGraph Agent Plan**
   - Real workflow graph.
   - Human interrupt/resume.
   - Checkpointing.
   - Preference memory.

3. **Video Pipeline Plan**
   - Upload and folder watching.
   - Whisper/OpenAI transcription.
   - SRT generation.
   - FFmpeg trim/speed/loudnorm.

4. **Cover And Draft Plan**
   - Pixel-compatible cover template.
   - Video screenshot extraction.
   - CapCut Mate/pyJianYingDraft spike.
   - Jianying draft delivery page.

## Self-Review Checklist

- Spec coverage: Phase 1 covers the approved PRD's UI shell, topic review, script review, feedback entry points, and frontend/backed separation.
- Scope control: real ingestion, video processing, and Jianying draft generation are explicitly moved to follow-up plans.
- Pixel style: implementation tasks include pixel app shell, robot panel, icon workflow, and visual acceptance criteria.
- Type consistency: backend and frontend both use episode, topic, script, and feedback terms.
- Completion detail: all Phase 1 files and commands are specified.
