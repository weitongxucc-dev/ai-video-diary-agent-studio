# Script Control And Feedback Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scripts editable, regeneratable, versioned, revertible, and make feedback events reversible so future Agent learning is based on active evidence instead of raw counts.

**Architecture:** Extend the existing Episode aggregate rather than adding a new database table, because the repository already persists Episode JSON in SQLite. Add script version snapshots and feedback event lifecycle fields to the domain model, expose focused FastAPI endpoints, then update the script and learning pages to use those endpoints. Keep generation through the existing `ScriptGenerator` interface, with a new optional section-regeneration method implemented by template and DeepSeek generators.

**Tech Stack:** Python 3.11, FastAPI, dataclasses, SQLite JSON persistence, pytest, Next.js 15, TypeScript, React Server Actions.

---

## Repository Note

This workspace may not contain `.git` metadata. If `git status --short` fails, skip commit steps and use verification checkpoints instead. Do not initialize a repository.

## File Map

Modify:

- `apps/api/app/models.py`: add `ScriptVersion`, `ScriptVersionSource`, and lifecycle fields on `FeedbackEvent`; add `script_versions` to `Episode`.
- `apps/api/app/repository.py`: serialize and restore script versions and upgraded feedback events with backward-compatible defaults.
- `apps/api/app/schemas.py`: expose script versions, script update requests, section regeneration requests, and feedback revert request/response fields.
- `apps/api/app/services.py`: add script edit, script version creation, full regeneration, section regeneration, script version revert, and feedback revert behavior.
- `apps/api/app/workflow.py`: add workflow actions for script update, full regeneration, section regeneration, script version revert, and feedback revert if routing through the graph stays useful.
- `apps/api/app/llm.py`: add `regenerate_section()` to the script generator protocol and implementations.
- `apps/api/app/main.py`: expose endpoints for script editing/regeneration/revert and feedback revert.
- `apps/web/src/lib/types.ts`: add script version and upgraded feedback event types.
- `apps/web/src/lib/api.ts`: add script edit/regenerate/revert and feedback revert API calls.
- `apps/web/src/app/actions.ts`: add server actions for script save, full regenerate, section regenerate, script version revert, and feedback revert.
- `apps/web/src/app/script/page.tsx`: render editable script form, regeneration controls, and version history.
- `apps/web/src/components/ScriptSectionEditor.tsx`: turn read-only textareas into editable form fields and add section regeneration reason input.
- `apps/web/src/app/learning/page.tsx`: show active/reverted feedback and allow undo.
- `README.md`: document script editing, version history, and feedback undo.

Test:

- `apps/api/tests/test_repository.py`
- `apps/api/tests/test_episode_flow.py`
- `apps/api/tests/test_deepseek_llm.py`
- `apps/api/tests/test_workflow_graph.py`

---

### Task 1: Persist Script Versions And Reversible Feedback

**Files:**

- Modify: `apps/api/app/models.py`
- Modify: `apps/api/app/repository.py`
- Test: `apps/api/tests/test_repository.py`

- [ ] **Step 1: Write failing repository tests**

Append these tests to `apps/api/tests/test_repository.py`:

```python
from datetime import date

from app.models import (
    FeedbackEvent,
    FeedbackValue,
    ScriptPackage,
    ScriptSection,
    ScriptVersion,
    ScriptVersionSource,
)


def _script_package(text: str) -> ScriptPackage:
    return ScriptPackage(
        id="script_1",
        hook=f"hook {text}",
        sections=[ScriptSection("s1", "第一段", 12, f"content {text}")],
        title_options=[f"title {text}"],
        cover_copy=f"cover {text}",
        publish_copy=f"publish {text}",
        comment_prompt=f"comment {text}",
    )


def test_repository_persists_script_versions_and_feedback_lifecycle(tmp_path):
    db_path = tmp_path / "episodes.sqlite3"
    repo = EpisodeRepository(today_provider=lambda: date(2026, 6, 4), database_path=db_path)
    episode = repo.today()
    version = ScriptVersion(
        id="sv_1",
        source=ScriptVersionSource.MANUAL_EDIT,
        script=_script_package("edited"),
        changed_section_ids=["s1"],
        reason="我改成自己的口吻",
    )
    event = FeedbackEvent(
        target_type="script_section",
        target_id="s1",
        value=FeedbackValue.DOWN,
        reason="太像营销文案",
        id="fb_1",
        active=False,
        revert_reason="误点",
    )
    episode.script = version.script
    episode.script_versions.append(version)
    episode.feedback_events.append(event)
    repo.save(episode)

    reloaded = EpisodeRepository(today_provider=lambda: date(2026, 6, 4), database_path=db_path)
    restored = reloaded.today()

    assert restored.script_versions[0].id == "sv_1"
    assert restored.script_versions[0].source == ScriptVersionSource.MANUAL_EDIT
    assert restored.script_versions[0].script.sections[0].content == "content edited"
    assert restored.script_versions[0].changed_section_ids == ["s1"]
    assert restored.script_versions[0].reason == "我改成自己的口吻"
    assert restored.feedback_events[0].id == "fb_1"
    assert restored.feedback_events[0].active is False
    assert restored.feedback_events[0].revert_reason == "误点"


def test_repository_restores_old_feedback_events_as_active(tmp_path):
    db_path = tmp_path / "episodes.sqlite3"
    repo = EpisodeRepository(database_path=db_path)
    episode = repo.create()
    episode.feedback_events.append(FeedbackEvent("topic", "topic_1", FeedbackValue.UP, "方向对"))
    repo.save(episode)

    reloaded = EpisodeRepository(database_path=db_path)
    restored = reloaded.get(episode.id)

    assert restored.feedback_events[0].id.startswith("fb_")
    assert restored.feedback_events[0].active is True
    assert restored.feedback_events[0].reverted_at is None
    assert restored.feedback_events[0].revert_reason is None
    assert restored.script_versions == []
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd apps/api && uv run pytest tests/test_repository.py -q
```

Expected: failure because `ScriptVersion` and `ScriptVersionSource` do not exist and `FeedbackEvent` lacks lifecycle fields.

- [ ] **Step 3: Add domain types**

In `apps/api/app/models.py`, add:

```python
from uuid import uuid4


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"
```

Add enum and dataclass near the script models:

```python
class ScriptVersionSource(StrEnum):
    AI_GENERATED = "ai_generated"
    MANUAL_EDIT = "manual_edit"
    FULL_REGENERATION = "full_regeneration"
    SECTION_REGENERATION = "section_regeneration"
    REVERT = "revert"


@dataclass
class ScriptVersion:
    id: str
    source: ScriptVersionSource
    script: ScriptPackage
    changed_section_ids: list[str] = field(default_factory=list)
    reason: str | None = None
    active: bool = True
    created_at: datetime = field(default_factory=utc_now)
```

Extend `FeedbackEvent`:

```python
@dataclass
class FeedbackEvent:
    target_type: str
    target_id: str
    value: FeedbackValue
    reason: str | None = None
    id: str = field(default_factory=lambda: new_id("fb"))
    active: bool = True
    before_snapshot: dict[str, object] | None = None
    after_snapshot: dict[str, object] | None = None
    reverted_at: datetime | None = None
    revert_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
```

Extend `Episode`:

```python
script_versions: list[ScriptVersion] = field(default_factory=list)
```

- [ ] **Step 4: Serialize and restore new fields**

In `apps/api/app/repository.py`, import `ScriptVersion` and `ScriptVersionSource`.

Add helper methods:

```python
    def _script_version_to_dict(self, version: ScriptVersion) -> dict[str, object]:
        return {
            "id": version.id,
            "source": version.source.value,
            "script": self._script_to_dict(version.script),
            "changed_section_ids": version.changed_section_ids,
            "reason": version.reason,
            "active": version.active,
            "created_at": version.created_at.isoformat(),
        }

    def _script_version_from_dict(self, data: dict[str, object]) -> ScriptVersion:
        return ScriptVersion(
            id=str(data["id"]),
            source=ScriptVersionSource(str(data["source"])),
            script=self._script_from_dict(data["script"]),
            changed_section_ids=[str(item) for item in data.get("changed_section_ids", [])],
            reason=str(data["reason"]) if data.get("reason") else None,
            active=bool(data.get("active", True)),
            created_at=datetime.fromisoformat(str(data["created_at"]))
            if data.get("created_at")
            else utc_now(),
        )
```

In `_episode_to_dict()`, add:

```python
"script_versions": [
    self._script_version_to_dict(version)
    for version in episode.script_versions
],
```

Replace feedback serialization with:

```python
"feedback_events": [
    {
        "id": event.id,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "value": event.value.value,
        "reason": event.reason,
        "active": event.active,
        "before_snapshot": event.before_snapshot,
        "after_snapshot": event.after_snapshot,
        "reverted_at": event.reverted_at.isoformat() if event.reverted_at else None,
        "revert_reason": event.revert_reason,
        "created_at": event.created_at.isoformat(),
    }
    for event in episode.feedback_events
],
```

In `_episode_from_dict()`, pass:

```python
script_versions=[
    self._script_version_from_dict(version)
    for version in data.get("script_versions", [])
],
```

Restore feedback events with backward-compatible defaults:

```python
feedback_events=[
    FeedbackEvent(
        target_type=str(event["target_type"]),
        target_id=str(event["target_id"]),
        value=FeedbackValue(str(event["value"])),
        reason=str(event["reason"]) if event.get("reason") else None,
        id=str(event.get("id") or f"fb_{uuid4().hex[:8]}"),
        active=bool(event.get("active", True)),
        before_snapshot=event.get("before_snapshot") if isinstance(event.get("before_snapshot"), dict) else None,
        after_snapshot=event.get("after_snapshot") if isinstance(event.get("after_snapshot"), dict) else None,
        reverted_at=datetime.fromisoformat(str(event["reverted_at"]))
        if event.get("reverted_at")
        else None,
        revert_reason=str(event["revert_reason"]) if event.get("revert_reason") else None,
        created_at=(
            utc_now()
            if not event.get("created_at")
            else datetime.fromisoformat(str(event["created_at"]))
        ),
    )
    for event in data.get("feedback_events", [])
],
```

- [ ] **Step 5: Run repository tests**

Run:

```bash
cd apps/api && uv run pytest tests/test_repository.py -q
```

Expected: all repository tests pass.

---

### Task 2: Add Script Editing And Version Service Behavior

**Files:**

- Modify: `apps/api/app/services.py`
- Modify: `apps/api/app/llm.py`
- Test: `apps/api/tests/test_episode_flow.py`
- Test: `apps/api/tests/test_deepseek_llm.py`

- [ ] **Step 1: Write failing API-flow tests for script edits and revert**

Append to `apps/api/tests/test_episode_flow.py`:

```python
def test_script_can_be_edited_saved_and_reverted():
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    )
    generated = client.post(f"/api/episodes/{episode['id']}/generate-script").json()
    original_hook = generated["script"]["hook"]

    edited_payload = {
        "reason": "改成我的口吻",
        "script": {
            **generated["script"],
            "hook": "这是我手动改过的开头",
            "sections": [
                {**section, "content": "这是我手动改过的段落"}
                for section in generated["script"]["sections"]
            ],
        },
    }
    edited = client.post(
        f"/api/episodes/{episode['id']}/script",
        json=edited_payload,
    )

    assert edited.status_code == 200
    edited_body = edited.json()
    assert edited_body["script"]["hook"] == "这是我手动改过的开头"
    assert edited_body["script_versions"][-1]["source"] == "manual_edit"
    assert edited_body["script_versions"][-1]["reason"] == "改成我的口吻"

    original_version_id = edited_body["script_versions"][0]["id"]
    reverted = client.post(
        f"/api/episodes/{episode['id']}/script/revert",
        json={"version_id": original_version_id, "reason": "回到原始稿"},
    )

    assert reverted.status_code == 200
    reverted_body = reverted.json()
    assert reverted_body["script"]["hook"] == original_hook
    assert reverted_body["script_versions"][-1]["source"] == "revert"
    assert reverted_body["script_versions"][-1]["reason"] == "回到原始稿"
```

- [ ] **Step 2: Write failing tests for full and section regeneration**

Append:

```python
def test_script_full_regeneration_creates_new_version():
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    )
    generated = client.post(f"/api/episodes/{episode['id']}/generate-script").json()

    response = client.post(
        f"/api/episodes/{episode['id']}/script/regenerate",
        json={"reason": "需要更犀利一点"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["script"]["id"] != generated["script"]["id"]
    assert body["script_versions"][-1]["source"] == "full_regeneration"
    assert body["script_versions"][-1]["reason"] == "需要更犀利一点"


def test_script_section_regeneration_replaces_only_target_section():
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    )
    generated = client.post(f"/api/episodes/{episode['id']}/generate-script").json()
    section_id = generated["script"]["sections"][0]["id"]
    untouched = generated["script"]["sections"][1]["content"]

    response = client.post(
        f"/api/episodes/{episode['id']}/script/sections/{section_id}/regenerate",
        json={"reason": "这一段要更像日记"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["script"]["sections"][0]["content"] != generated["script"]["sections"][0]["content"]
    assert body["script"]["sections"][1]["content"] == untouched
    assert body["script_versions"][-1]["source"] == "section_regeneration"
    assert body["script_versions"][-1]["changed_section_ids"] == [section_id]
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
cd apps/api && uv run pytest tests/test_episode_flow.py::test_script_can_be_edited_saved_and_reverted tests/test_episode_flow.py::test_script_full_regeneration_creates_new_version tests/test_episode_flow.py::test_script_section_regeneration_replaces_only_target_section -q
```

Expected: 404 or validation failures because endpoints and version fields do not exist yet.

- [ ] **Step 4: Extend script generator protocol and template implementation**

In `apps/api/app/llm.py`, update `ScriptGenerator`:

```python
class ScriptGenerator(Protocol):
    def generate(
        self,
        topic: TopicCandidate,
        day_number: int,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptPackage:
        ...

    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptSection:
        ...
```

Add to `MissingScriptGenerator`:

```python
    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptSection:
        raise LLMConfigurationError("DEEPSEEK_API_KEY is not configured")
```

Add to `TemplateScriptGenerator`:

```python
    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptSection:
        current = next(
            section for section in current_script.sections if section.id == section_id
        )
        reason_suffix = f" 用户要求：{reason}" if reason else ""
        return ScriptSection(
            id=current.id,
            title=current.title,
            estimated_seconds=current.estimated_seconds,
            content=(
                f"{current.content} 这一版改成更贴近日记的表达，"
                f"围绕「{topic.core_viewpoint}」讲得更具体。{reason_suffix}"
            ),
            feedback=current.feedback,
        )
```

- [ ] **Step 5: Add DeepSeek section regeneration test**

Append to `apps/api/tests/test_deepseek_llm.py`:

```python
from app.models import ScriptPackage, ScriptSection


def test_deepseek_script_generator_regenerates_one_section():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "id": "point_1",
                                    "title": "原因 1",
                                    "estimated_seconds": 36,
                                    "content": "这是重新生成后的单段内容。",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    generator = DeepSeekScriptGenerator(
        Settings(deepseek_api_key="secret", deepseek_model="deepseek-v4-flash"),
        transport=httpx.MockTransport(handler),
    )
    topic = TopicCandidate(
        id="topic_1",
        title="AI 工具为什么越多越难用？",
        core_viewpoint="选择成本和流程断裂才是核心问题。",
        reason="来自真实知识库证据。",
        audience="AI 创作者",
        outline=["选择成本", "流程断裂", "回到任务"],
        score=90,
        source_refs=[SourceRef("Obsidian", "工具选择", "[[工具选择]]")],
    )
    current_script = ScriptPackage(
        id="script_1",
        hook="旧开头",
        sections=[ScriptSection("point_1", "原因 1", 36, "旧内容")],
        title_options=["标题"],
        cover_copy="封面",
        publish_copy="发布",
        comment_prompt="评论",
    )

    section = generator.regenerate_section(
        topic,
        current_script,
        "point_1",
        reason="更像日记",
    )

    assert section.id == "point_1"
    assert section.content == "这是重新生成后的单段内容。"
    assert "只输出 JSON" in captured["payload"]["messages"][0]["content"]
    assert "更像日记" in captured["payload"]["messages"][1]["content"]
```

- [ ] **Step 6: Implement DeepSeek section regeneration**

In `DeepSeekScriptGenerator`, add:

```python
    def regenerate_section(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        section_id: str,
        reason: str | None = None,
        feedback_events: list[FeedbackEvent] | None = None,
    ) -> ScriptSection:
        if not self._settings.deepseek_api_key:
            raise LLMConfigurationError("DEEPSEEK_API_KEY is not configured")
        current = next(
            section for section in current_script.sections if section.id == section_id
        )
        payload = {
            "model": self._settings.deepseek_model,
            "messages": [
                {"role": "system", "content": self._section_system_prompt()},
                {
                    "role": "user",
                    "content": self._section_user_prompt(
                        topic,
                        current_script,
                        current,
                        reason,
                        feedback_events or [],
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
            "max_tokens": 900,
        }
        url = f"{self._settings.deepseek_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self._settings.deepseek_api_key}"}
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            response = client.post(url, json=payload, headers=headers)
        try:
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMResponseError("DeepSeek did not return valid section JSON") from exc
        return ScriptSection(
            id=str(data.get("id") or current.id),
            title=str(data.get("title") or current.title),
            estimated_seconds=int(data.get("estimated_seconds") or current.estimated_seconds),
            content=str(data["content"]),
            feedback=current.feedback,
        )
```

Add helper prompts:

```python
    def _section_system_prompt(self) -> str:
        return (
            "你是一个中文 AI 视频日记口播编导。只输出 JSON，不要 Markdown，不要解释。"
            "JSON 字段必须包含 id、title、estimated_seconds、content。"
            "只重写用户指定段落，保持真人日记感，不要营销腔。"
        )

    def _section_user_prompt(
        self,
        topic: TopicCandidate,
        current_script: ScriptPackage,
        current_section: ScriptSection,
        reason: str | None,
        feedback_events: list[FeedbackEvent],
    ) -> str:
        return json.dumps(
            {
                "topic": {
                    "title": topic.title,
                    "core_viewpoint": topic.core_viewpoint,
                    "reason": topic.reason,
                    "outline": topic.outline,
                },
                "current_script": {
                    "hook": current_script.hook,
                    "sections": [
                        {
                            "id": section.id,
                            "title": section.title,
                            "content": section.content,
                        }
                        for section in current_script.sections
                    ],
                },
                "target_section": {
                    "id": current_section.id,
                    "title": current_section.title,
                    "estimated_seconds": current_section.estimated_seconds,
                    "content": current_section.content,
                },
                "rewrite_reason": reason,
                "recent_feedback": [
                    {
                        "target_type": event.target_type,
                        "value": event.value,
                        "reason": event.reason,
                    }
                    for event in feedback_events[-10:]
                    if event.active
                ],
            },
            ensure_ascii=False,
        )
```

- [ ] **Step 7: Implement service methods**

In `apps/api/app/services.py`, import `ScriptPackage`, `ScriptSection`, `ScriptVersion`, `ScriptVersionSource`, and `new_id`.

Add private helpers:

```python
    def _selected_topic(self, episode: Episode) -> TopicCandidate:
        if episode.selected_topic_id is None:
            raise ValueError("approve a topic first")
        return next(topic for topic in episode.topics if topic.id == episode.selected_topic_id)

    def _record_script_version(
        self,
        episode: Episode,
        source: ScriptVersionSource,
        script,
        changed_section_ids: list[str] | None = None,
        reason: str | None = None,
    ) -> Episode:
        for version in episode.script_versions:
            version.active = False
        episode.script_versions.append(
            ScriptVersion(
                id=new_id("sv"),
                source=source,
                script=script,
                changed_section_ids=changed_section_ids or [],
                reason=reason,
                active=True,
            )
        )
        episode.script = script
        episode.status = EpisodeStatus.SCRIPT_READY
        return episode
```

Update `generate_script()` after generated script:

```python
        script = self._script_generator.generate(
            selected,
            day_number=episode.day_number,
            feedback_events=(feedback_history or []) + episode.feedback_events,
        )
        self._record_script_version(
            episode,
            ScriptVersionSource.AI_GENERATED,
            script,
            reason="initial generation",
        )
        episode.status = EpisodeStatus.SCRIPT_READY
        return episode
```

Add methods:

```python
    def update_script(
        self,
        episode: Episode,
        script: ScriptPackage,
        reason: str | None = None,
    ) -> Episode:
        if episode.script is None:
            raise ValueError("generate a script first")
        changed = [
            section.id
            for section in script.sections
            if next((old.content for old in episode.script.sections if old.id == section.id), None)
            != section.content
        ]
        return self._record_script_version(
            episode,
            ScriptVersionSource.MANUAL_EDIT,
            script,
            changed_section_ids=changed,
            reason=reason,
        )

    def regenerate_script(
        self,
        episode: Episode,
        feedback_history: list[FeedbackEvent] | None = None,
        reason: str | None = None,
    ) -> Episode:
        selected = self._selected_topic(episode)
        script = self._script_generator.generate(
            selected,
            day_number=episode.day_number,
            feedback_events=[
                event
                for event in (feedback_history or []) + episode.feedback_events
                if event.active
            ],
        )
        return self._record_script_version(
            episode,
            ScriptVersionSource.FULL_REGENERATION,
            script,
            reason=reason,
        )

    def regenerate_script_section(
        self,
        episode: Episode,
        section_id: str,
        feedback_history: list[FeedbackEvent] | None = None,
        reason: str | None = None,
    ) -> Episode:
        if episode.script is None:
            raise ValueError("generate a script first")
        selected = self._selected_topic(episode)
        replacement = self._script_generator.regenerate_section(
            selected,
            episode.script,
            section_id,
            reason=reason,
            feedback_events=[
                event
                for event in (feedback_history or []) + episode.feedback_events
                if event.active
            ],
        )
        replaced = False
        sections: list[ScriptSection] = []
        for section in episode.script.sections:
            if section.id == section_id:
                sections.append(replacement)
                replaced = True
            else:
                sections.append(section)
        if not replaced:
            raise ValueError("script section not found")
        script = ScriptPackage(
            id=new_id("script"),
            hook=episode.script.hook,
            sections=sections,
            title_options=episode.script.title_options,
            cover_copy=episode.script.cover_copy,
            publish_copy=episode.script.publish_copy,
            comment_prompt=episode.script.comment_prompt,
        )
        return self._record_script_version(
            episode,
            ScriptVersionSource.SECTION_REGENERATION,
            script,
            changed_section_ids=[section_id],
            reason=reason,
        )

    def revert_script_version(
        self,
        episode: Episode,
        version_id: str,
        reason: str | None = None,
    ) -> Episode:
        version = next(
            (candidate for candidate in episode.script_versions if candidate.id == version_id),
            None,
        )
        if version is None:
            raise ValueError("script version not found")
        return self._record_script_version(
            episode,
            ScriptVersionSource.REVERT,
            version.script,
            changed_section_ids=[section.id for section in version.script.sections],
            reason=reason,
        )
```

- [ ] **Step 8: Run service/API flow tests**

Run:

```bash
cd apps/api && uv run pytest tests/test_episode_flow.py::test_script_can_be_edited_saved_and_reverted tests/test_episode_flow.py::test_script_full_regeneration_creates_new_version tests/test_episode_flow.py::test_script_section_regeneration_replaces_only_target_section tests/test_deepseek_llm.py::test_deepseek_script_generator_regenerates_one_section -q
```

Expected: still fail because API schemas and routes are not wired yet.

---

### Task 3: Add API Schemas, Routes, And Workflow Actions

**Files:**

- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/workflow.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_workflow_graph.py`
- Test: `apps/api/tests/test_episode_flow.py`

- [ ] **Step 1: Write failing workflow node test**

Append to `apps/api/tests/test_workflow_graph.py`:

```python
def test_workflow_exposes_script_edit_and_feedback_revert_actions():
    workflow = LangGraphEpisodeWorkflow(EpisodeService())

    assert "update_script" in workflow.node_names()
    assert "regenerate_script" in workflow.node_names()
    assert "regenerate_script_section" in workflow.node_names()
    assert "revert_script_version" in workflow.node_names()
    assert "revert_feedback" in workflow.node_names()
```

- [ ] **Step 2: Run workflow test and verify failure**

Run:

```bash
cd apps/api && uv run pytest tests/test_workflow_graph.py::test_workflow_exposes_script_edit_and_feedback_revert_actions -q
```

Expected: failure because these node names do not exist.

- [ ] **Step 3: Add schema models**

In `apps/api/app/schemas.py`, import `ScriptVersionSource`.

Add:

```python
class ScriptUpdateRequest(BaseModel):
    script: ScriptPackageResponse
    reason: str | None = None


class ScriptRegenerateRequest(BaseModel):
    reason: str | None = None


class ScriptSectionRegenerateRequest(BaseModel):
    reason: str | None = None


class ScriptVersionRevertRequest(BaseModel):
    version_id: str
    reason: str | None = None


class FeedbackRevertRequest(BaseModel):
    feedback_event_id: str
    reason: str | None = None


class ScriptVersionResponse(BaseModel):
    id: str
    source: ScriptVersionSource
    script: ScriptPackageResponse
    changed_section_ids: list[str]
    reason: str | None
    active: bool
    created_at: datetime
```

Extend `FeedbackEventResponse`:

```python
    episode_id: str | None = None
    id: str
    active: bool
    before_snapshot: dict[str, object] | None
    after_snapshot: dict[str, object] | None
    reverted_at: datetime | None
    revert_reason: str | None
```

Extend `EpisodeResponse`:

```python
    script_versions: list[ScriptVersionResponse]
```

- [ ] **Step 4: Add script request conversion helper**

In `apps/api/app/main.py`, import request schemas and `ScriptPackage`, `ScriptSection`.

Add:

```python
def _script_package_from_response(script) -> ScriptPackage:
    return ScriptPackage(
        id=script.id,
        hook=script.hook,
        sections=[
            ScriptSection(
                id=section.id,
                title=section.title,
                estimated_seconds=section.estimated_seconds,
                content=section.content,
                feedback=section.feedback,
            )
            for section in script.sections
        ],
        title_options=[*script.title_options],
        cover_copy=script.cover_copy,
        publish_copy=script.publish_copy,
        comment_prompt=script.comment_prompt,
    )
```

- [ ] **Step 5: Add workflow actions**

In `apps/api/app/workflow.py`, extend `WorkflowAction` with:

```python
"update_script",
"regenerate_script",
"regenerate_script_section",
"revert_script_version",
"revert_feedback",
```

Add fields to `EpisodeWorkflowState`:

```python
script: Any
section_id: str
version_id: str
feedback_event_id: str
reason: str | None
```

Add node names and methods:

```python
graph.add_node("update_script", self._update_script)
graph.add_node("regenerate_script", self._regenerate_script)
graph.add_node("regenerate_script_section", self._regenerate_script_section)
graph.add_node("revert_script_version", self._revert_script_version)
graph.add_node("revert_feedback", self._revert_feedback)
```

Implement:

```python
    def _update_script(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.update_script(
                state["episode"],
                state["script"],
                reason=state.get("reason"),
            )
        }

    def _regenerate_script(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.regenerate_script(
                state["episode"],
                feedback_history=state.get("feedback_history"),
                reason=state.get("reason"),
            )
        }

    def _regenerate_script_section(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.regenerate_script_section(
                state["episode"],
                state["section_id"],
                feedback_history=state.get("feedback_history"),
                reason=state.get("reason"),
            )
        }

    def _revert_script_version(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.revert_script_version(
                state["episode"],
                state["version_id"],
                reason=state.get("reason"),
            )
        }

    def _revert_feedback(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.revert_feedback(
                state["episode"],
                state["feedback_event_id"],
                reason=state.get("reason"),
            )
        }
```

- [ ] **Step 6: Add main routes**

In `apps/api/app/main.py`, add:

```python
@app.post("/api/episodes/{episode_id}/script", response_model=EpisodeResponse)
def update_episode_script(episode_id: str, request: ScriptUpdateRequest) -> EpisodeResponse:
    try:
        episode = workflow.run(
            "update_script",
            get_episode_or_404(episode_id),
            script=_script_package_from_response(request.script),
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/script/regenerate", response_model=EpisodeResponse)
def regenerate_episode_script(
    episode_id: str,
    request: ScriptRegenerateRequest,
) -> EpisodeResponse:
    try:
        episode = workflow.run(
            "regenerate_script",
            get_episode_or_404(episode_id),
            feedback_history=feedback_history_for(episode_id),
            reason=request.reason,
        )
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (LLMResponseError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/script/sections/{section_id}/regenerate", response_model=EpisodeResponse)
def regenerate_episode_script_section(
    episode_id: str,
    section_id: str,
    request: ScriptSectionRegenerateRequest,
) -> EpisodeResponse:
    try:
        episode = workflow.run(
            "regenerate_script_section",
            get_episode_or_404(episode_id),
            section_id=section_id,
            feedback_history=feedback_history_for(episode_id),
            reason=request.reason,
        )
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (LLMResponseError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/script/revert", response_model=EpisodeResponse)
def revert_episode_script_version(
    episode_id: str,
    request: ScriptVersionRevertRequest,
) -> EpisodeResponse:
    try:
        episode = workflow.run(
            "revert_script_version",
            get_episode_or_404(episode_id),
            version_id=request.version_id,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return episode_response(repo.save(episode))
```

- [ ] **Step 7: Run API flow tests**

Run:

```bash
cd apps/api && uv run pytest tests/test_episode_flow.py::test_script_can_be_edited_saved_and_reverted tests/test_episode_flow.py::test_script_full_regeneration_creates_new_version tests/test_episode_flow.py::test_script_section_regeneration_replaces_only_target_section tests/test_workflow_graph.py::test_workflow_exposes_script_edit_and_feedback_revert_actions -q
```

Expected: script tests pass or feedback revert node still fails until Task 4.

---

### Task 4: Add Feedback Revert And Active-Only Learning

**Files:**

- Modify: `apps/api/app/services.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/schemas.py`
- Test: `apps/api/tests/test_episode_flow.py`

- [ ] **Step 1: Write failing feedback revert tests**

Append to `apps/api/tests/test_episode_flow.py`:

```python
def test_feedback_can_be_reverted_and_is_excluded_from_future_generation():
    class CapturingTopicGenerator:
        def __init__(self) -> None:
            self.feedback_events = []

        def generate(self, day_number, feedback_events=None):
            self.feedback_events = feedback_events or []
            return TemplateTopicGenerator().generate(day_number, feedback_events)

    generator = CapturingTopicGenerator()
    main_module.service = EpisodeService(
        script_generator=TemplateScriptGenerator(),
        topic_generator=generator,
    )
    main_module.workflow = main_module.LangGraphEpisodeWorkflow(main_module.service)
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    feedback = client.post(
        f"/api/episodes/{episode['id']}/feedback",
        json={
            "target_type": "topic",
            "target_id": topics[0]["id"],
            "value": "down",
            "reason": "这个方向太泛",
        },
    ).json()["feedback_events"][-1]

    reverted = client.post(
        f"/api/episodes/{episode['id']}/feedback/revert",
        json={"feedback_event_id": feedback["id"], "reason": "误点"},
    )

    assert reverted.status_code == 200
    body = reverted.json()
    assert body["feedback_events"][-1]["active"] is False
    assert body["feedback_events"][-1]["revert_reason"] == "误点"

    client.post(f"/api/episodes/{episode['id']}/regenerate-topics")
    assert [event.reason for event in generator.feedback_events] == []
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
cd apps/api && uv run pytest tests/test_episode_flow.py::test_feedback_can_be_reverted_and_is_excluded_from_future_generation -q
```

Expected: failure because `/feedback/revert` endpoint and active filtering do not exist.

- [ ] **Step 3: Implement active feedback filtering**

In `apps/api/app/main.py`, change `feedback_history_for()`:

```python
def feedback_history_for(episode_id: str) -> list[FeedbackEvent]:
    return [
        event
        for episode in repo.all()
        if episode.id != episode_id
        for event in episode.feedback_events
        if event.active
    ][-30:]
```

In `EpisodeService.generate_topics()` and `regenerate_topics()`, pass only active events:

```python
feedback_events=[
    event
    for event in (feedback_history or []) + episode.feedback_events
    if event.active
],
```

In `generate_script()` use the same active filtering.

- [ ] **Step 4: Implement service feedback revert**

In `apps/api/app/services.py`, add:

```python
    def revert_feedback(
        self,
        episode: Episode,
        feedback_event_id: str,
        reason: str | None = None,
    ) -> Episode:
        event = next(
            (candidate for candidate in episode.feedback_events if candidate.id == feedback_event_id),
            None,
        )
        if event is None:
            raise ValueError("feedback event not found")
        event.active = False
        event.reverted_at = utc_now()
        event.revert_reason = reason
        if event.target_type == "topic":
            for topic in episode.topics:
                if topic.id == event.target_id:
                    topic.feedback = None
        if event.target_type == "script_section" and episode.script:
            for section in episode.script.sections:
                if section.id == event.target_id:
                    section.feedback = None
        return episode
```

- [ ] **Step 5: Add feedback revert route**

In `apps/api/app/main.py`, add:

```python
@app.post("/api/episodes/{episode_id}/feedback/revert", response_model=EpisodeResponse)
def revert_episode_feedback(
    episode_id: str,
    request: FeedbackRevertRequest,
) -> EpisodeResponse:
    try:
        episode = workflow.run(
            "revert_feedback",
            get_episode_or_404(episode_id),
            feedback_event_id=request.feedback_event_id,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return episode_response(repo.save(episode))
```

Update `/api/preferences` to consider only active events:

```python
events = [
    event
    for episode in repo.all()
    for event in episode.feedback_events
    if event.reason and event.active
]
```

Update `/api/feedback/summary` counts to expose active events only:

```python
events = [
    event
    for episode in repo.all()
    for event in episode.feedback_events
    if event.active
]
```

Populate `episode_id` in `recent_events` explicitly:

```python
recent_events=[
    FeedbackEventResponse.model_validate(
        {
            **event.__dict__,
            "value": event.value,
            "episode_id": episode.id,
        }
    )
    for episode in repo.all()
    for event in episode.feedback_events
    if event.active
][:20]
```

- [ ] **Step 6: Run feedback tests**

Run:

```bash
cd apps/api && uv run pytest tests/test_episode_flow.py::test_feedback_can_be_reverted_and_is_excluded_from_future_generation tests/test_episode_flow.py::test_feedback_updates_topic_feedback_value -q
```

Expected: both tests pass.

---

### Task 5: Update Frontend Types, API, And Actions

**Files:**

- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/app/actions.ts`

- [ ] **Step 1: Add TypeScript types**

In `apps/web/src/lib/types.ts`, add:

```ts
export type ScriptVersionSource =
  | "ai_generated"
  | "manual_edit"
  | "full_regeneration"
  | "section_regeneration"
  | "revert";

export type ScriptVersion = {
  id: string;
  source: ScriptVersionSource;
  script: ScriptPackage;
  changed_section_ids: string[];
  reason: string | null;
  active: boolean;
  created_at: string;
};
```

Extend `FeedbackEvent`:

```ts
  episode_id: string | null;
  id: string;
  active: boolean;
  before_snapshot: Record<string, unknown> | null;
  after_snapshot: Record<string, unknown> | null;
  reverted_at: string | null;
  revert_reason: string | null;
```

Extend `Episode`:

```ts
  script_versions: ScriptVersion[];
```

- [ ] **Step 2: Add API clients**

In `apps/web/src/lib/api.ts`, add:

```ts
export function updateEpisodeScript(
  episodeId: string,
  script: ScriptPackage,
  reason?: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/script`, {
    method: "POST",
    body: JSON.stringify({ script, reason: reason ?? null }),
  });
}

export function regenerateEpisodeScript(
  episodeId: string,
  reason?: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/script/regenerate`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

export function regenerateEpisodeScriptSection(
  episodeId: string,
  sectionId: string,
  reason?: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/script/sections/${sectionId}/regenerate`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

export function revertEpisodeScriptVersion(
  episodeId: string,
  versionId: string,
  reason?: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/script/revert`, {
    method: "POST",
    body: JSON.stringify({ version_id: versionId, reason: reason ?? null }),
  });
}

export function revertFeedbackEvent(
  episodeId: string,
  feedbackEventId: string,
  reason?: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/feedback/revert`, {
    method: "POST",
    body: JSON.stringify({ feedback_event_id: feedbackEventId, reason: reason ?? null }),
  });
}
```

- [ ] **Step 3: Add server actions**

In `apps/web/src/app/actions.ts`, import the new API functions and `ScriptPackage`.

Add:

```ts
function scriptFromFormData(formData: FormData): ScriptPackage {
  const sectionIds = formData
    .getAll("section_id")
    .filter((value): value is string => typeof value === "string");
  return {
    id: requiredString(formData, "script_id"),
    hook: requiredString(formData, "hook"),
    sections: sectionIds.map((sectionId) => ({
      id: sectionId,
      title: requiredString(formData, `section_title:${sectionId}`),
      estimated_seconds: Number(requiredString(formData, `section_seconds:${sectionId}`)),
      content: requiredString(formData, `section_content:${sectionId}`),
      feedback: null,
    })),
    title_options: formData
      .getAll("title_option")
      .filter((value): value is string => typeof value === "string")
      .map((value) => value.trim())
      .filter(Boolean),
    cover_copy: requiredString(formData, "cover_copy"),
    publish_copy: requiredString(formData, "publish_copy"),
    comment_prompt: requiredString(formData, "comment_prompt"),
  };
}

export async function saveScriptAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const reason = formData.get("reason");
  await updateEpisodeScript(
    episodeId,
    scriptFromFormData(formData),
    typeof reason === "string" ? reason : undefined,
  );
  redirect("/script");
}

export async function regenerateScriptAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const reason = formData.get("reason");
  await regenerateEpisodeScript(episodeId, typeof reason === "string" ? reason : undefined);
  redirect("/script");
}

export async function regenerateScriptSectionAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const sectionId = requiredString(formData, "section_id");
  const reason = formData.get("reason");
  await regenerateEpisodeScriptSection(
    episodeId,
    sectionId,
    typeof reason === "string" ? reason : undefined,
  );
  redirect("/script");
}

export async function revertScriptVersionAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const versionId = requiredString(formData, "version_id");
  const reason = formData.get("reason");
  await revertEpisodeScriptVersion(
    episodeId,
    versionId,
    typeof reason === "string" ? reason : undefined,
  );
  redirect("/script");
}

export async function revertFeedbackAction(formData: FormData) {
  const episodeId = requiredString(formData, "episode_id");
  const feedbackEventId = requiredString(formData, "feedback_event_id");
  const reason = formData.get("reason");
  await revertFeedbackEvent(
    episodeId,
    feedbackEventId,
    typeof reason === "string" ? reason : undefined,
  );
  redirect("/learning");
}
```

- [ ] **Step 4: Run frontend type check**

Run:

```bash
corepack pnpm --dir apps/web exec tsc --noEmit
```

Expected: TypeScript errors remain until UI components use the new fields correctly.

---

### Task 6: Make Script Page Editable With Version Controls

**Files:**

- Modify: `apps/web/src/app/script/page.tsx`
- Modify: `apps/web/src/components/ScriptSectionEditor.tsx`

- [ ] **Step 1: Update `ScriptSectionEditor`**

Replace the read-only textarea with form fields compatible with `saveScriptAction`:

```tsx
export function ScriptSectionEditor({
  section
}: {
  section: ScriptSection;
}) {
  return (
    <section className="script-section pixel-card">
      <div className="script-section-header">
        <div>
          <strong>{section.title}</strong>
          <span>
            预计 {section.estimated_seconds}s · {getFeedbackLabel(section.feedback)}
          </span>
        </div>
        <div className="feedback-actions" aria-label={`${section.title} 反馈操作`}>
          <input name="section_id" type="hidden" value={section.id} />
          <input name={`section_title:${section.id}`} type="hidden" value={section.title} />
          <input
            name={`section_seconds:${section.id}`}
            type="hidden"
            value={String(section.estimated_seconds)}
          />
        </div>
      </div>
      <textarea
        aria-label={`${section.title} 口播稿内容`}
        defaultValue={section.content}
        name={`section_content:${section.id}`}
      />
    </section>
  );
}
```

Move thumbs feedback and section regeneration forms to `script/page.tsx`, outside the save form, so nested forms are avoided.

- [ ] **Step 2: Update script page main form**

In `apps/web/src/app/script/page.tsx`, import:

```ts
import {
  approveScriptAction,
  regenerateScriptAction,
  regenerateScriptSectionAction,
  revertScriptVersionAction,
  saveScriptAction,
  sendScriptSectionFeedbackAction,
} from "@/app/actions";
```

Wrap editable content in:

```tsx
<form action={saveScriptAction} className="script-edit-form">
  <input name="episode_id" type="hidden" value={episode.id} />
  <input name="script_id" type="hidden" value={script.id} />
  <section className="script-hook pixel-card">
    <span className="section-kicker">开头 3 秒</span>
    <textarea aria-label="开头 3 秒" name="hook" defaultValue={script.hook} />
  </section>
  {script.sections.map((section) => (
    <ScriptSectionEditor key={section.id} section={section} />
  ))}
  {script.title_options.map((title) => (
    <input key={title} name="title_option" type="hidden" value={title} />
  ))}
  <textarea name="cover_copy" defaultValue={script.cover_copy} />
  <textarea name="publish_copy" defaultValue={script.publish_copy} />
  <textarea name="comment_prompt" defaultValue={script.comment_prompt} />
  <input name="reason" type="hidden" value="用户手动编辑口播稿" />
  <button className="pixel-button" type="submit">保存口播稿修改</button>
</form>
```

Keep the approve button in its own form:

```tsx
<form action={approveScriptAction}>
  <input name="episode_id" type="hidden" value={episode.id} />
  <button className="pixel-button" type="submit">
    确认脚本，等待录制视频
  </button>
</form>
```

- [ ] **Step 3: Add full regeneration control**

Add a form near script actions:

```tsx
<form action={regenerateScriptAction}>
  <input name="episode_id" type="hidden" value={episode.id} />
  <input name="reason" type="hidden" value="重新生成整篇口播稿" />
  <button className="pixel-button secondary" type="submit">
    重新生成整篇
  </button>
</form>
```

- [ ] **Step 4: Add section regeneration controls**

For each section, add a non-nested form:

```tsx
<form action={regenerateScriptSectionAction} className="section-regenerate-form">
  <input name="episode_id" type="hidden" value={episode.id} />
  <input name="section_id" type="hidden" value={section.id} />
  <input
    name="reason"
    placeholder="例如：更像日记、少一点营销感"
    type="text"
  />
  <button className="pixel-button secondary" type="submit">
    重写本段
  </button>
</form>
```

- [ ] **Step 5: Add script version history**

In the script aside, render:

```tsx
<h3>版本历史</h3>
{episode.script_versions.slice().reverse().map((version) => (
  <form action={revertScriptVersionAction} key={version.id}>
    <input name="episode_id" type="hidden" value={episode.id} />
    <input name="version_id" type="hidden" value={version.id} />
    <input name="reason" type="hidden" value="从版本历史恢复" />
    <p>
      {version.source} · {new Date(version.created_at).toLocaleString("zh-CN")}
      {version.active ? " · 当前" : ""}
    </p>
    <button className="pixel-button secondary" disabled={version.active} type="submit">
      恢复此版本
    </button>
  </form>
))}
```

- [ ] **Step 6: Run frontend verification**

Run:

```bash
corepack pnpm --dir apps/web exec tsc --noEmit
corepack pnpm --dir apps/web lint
```

Expected: both pass after fixing any JSX typing issues.

---

### Task 7: Add Learning Page Undo

**Files:**

- Modify: `apps/web/src/app/learning/page.tsx`

- [ ] **Step 1: Import action**

Add:

```ts
import { revertFeedbackAction } from "@/app/actions";
```

- [ ] **Step 2: Render active/reverted status and undo**

For recent events, replace the event row body with:

```tsx
<div>
  <strong>{event.target_type === "topic" ? "选题反馈" : "口播段落反馈"}</strong>
  <span>
    {event.value === "up" ? "喜欢" : "不喜欢"} · {formatDate(event.created_at)}
    {event.active ? "" : " · 已撤销"}
  </span>
  <p>{event.revert_reason || event.reason || "没有填写原因。"}</p>
  {event.active ? (
    <form action={revertFeedbackAction}>
      <input name="episode_id" type="hidden" value={event.episode_id} />
      <input name="feedback_event_id" type="hidden" value={event.id} />
      <input name="reason" type="hidden" value="用户在反馈学习页撤销" />
      <button className="pixel-button secondary" type="submit">
        撤销这条反馈
      </button>
    </form>
  ) : null}
</div>
```

- [ ] **Step 3: Run typecheck**

Run:

```bash
corepack pnpm --dir apps/web exec tsc --noEmit
```

Expected: pass.

---

### Task 8: Documentation And Full Verification

**Files:**

- Modify: `README.md`

- [ ] **Step 1: Update README Phase 1 bullets**

Add bullets:

```markdown
- Editable script workspace with manual save, full regeneration, section regeneration, and script version history
- Reversible feedback events so preference memory uses active evidence instead of raw like/down counts
```

- [ ] **Step 2: Run focused backend tests**

Run:

```bash
cd apps/api && uv run pytest tests/test_repository.py tests/test_episode_flow.py tests/test_deepseek_llm.py tests/test_workflow_graph.py -q
```

Expected: all focused tests pass.

- [ ] **Step 3: Run full API test suite**

Run:

```bash
corepack pnpm test:api
```

Expected: all API tests pass.

- [ ] **Step 4: Run frontend checks**

Run:

```bash
corepack pnpm --dir apps/web exec tsc --noEmit
corepack pnpm --dir apps/web lint
node apps/web/scripts/workflow-contract-check.mjs
node apps/web/scripts/pixel-style-contract-check.mjs
corepack pnpm build:web
```

Expected: all commands pass.

- [ ] **Step 5: Manual UI smoke check**

Start services:

```bash
corepack pnpm dev:api
corepack pnpm dev:web
```

Visit:

- `http://localhost:3000/script`
- `http://localhost:3000/learning`

Expected:

- Script textareas are editable.
- Saving script preserves edits after reload.
- Regenerating a section creates a new version.
- Version history can restore an older version.
- Feedback rows show active/reverted state.
