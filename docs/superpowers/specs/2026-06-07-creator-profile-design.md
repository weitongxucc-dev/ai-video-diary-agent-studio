# Creator Profile V1 Design

## Goal

Creator Profile v1 makes the Agent aware of the creator's stable voice, content boundaries, preferred expressions, banned phrases, and example style references. It should reduce generic AI copy without turning feedback memory or runtime settings into a mixed responsibility module.

## Scope

This version adds an editable local profile, a short profile summary for prompts, and a `/profile` workbench page. It does not add semantic memory search, model fine-tuning, microphone capture, or automatic audience-performance analysis.

## Architecture

The backend owns Creator Profile as a small independent module:

- `app.creator_profile` defines the dataclass, default values, update validation, SQLite persistence, and prompt payload builder.
- `app.schemas` exposes request/response shapes.
- `app.main` exposes `GET /api/creator-profile` and `POST /api/creator-profile`.
- Topic and script generators accept an optional `creator_profile` argument and inject only the short prompt payload, not long raw samples.
- The automation worker receives a profile provider so scheduled topic generation uses the saved local profile too.

This keeps profile state separate from runtime settings and preference memory:

- runtime settings answer "how the app connects to tools";
- preference memory answers "what the user recently confirmed or rejected";
- creator profile answers "who the creator is and how they usually speak".

## Data Model

Creator Profile stores:

- `identity`: one short description of the creator.
- `content_domains`: topics the creator often covers.
- `tone_keywords`: stable voice traits.
- `preferred_phrases`: expressions or patterns to reinforce.
- `banned_phrases`: words, cliches, or structures to avoid.
- `good_examples`: short example snippets that represent the desired voice.
- `bad_examples`: short example snippets that should be avoided.
- `updated_at`: server-generated timestamp.

The prompt payload stays compact:

```json
{
  "identity": "技术型个人创作者，关注 AI 工具、自动化和真实项目复盘。",
  "content_domains": ["AI 产品", "自动化", "个人创作流程"],
  "voice": ["直接", "真实", "少营销腔"],
  "prefer": ["先讲真实问题，再给判断"],
  "avoid": ["空泛鸡汤", "震惊体"],
  "style_examples": {
    "good": ["我今天踩到一个很具体的坑..."],
    "bad": ["家人们，这个神器太炸裂了..."]
  }
}
```

## API

`GET /api/creator-profile` returns the persisted profile or defaults.

`POST /api/creator-profile` accepts all editable fields and returns the saved profile. List fields are trimmed, empty entries are dropped, duplicated entries are collapsed in first-seen order, and long lists are capped to keep prompt context small. Updates are accepted only from local clients, matching the runtime settings safety boundary.

## Persistence

Creator Profile is stored in the same local SQLite database configured by `DATABASE_PATH`, but in its own `creator_profile` table. The table has one logical row with `id = "default"`, a JSON payload, and an `updated_at` timestamp. This keeps user content data local and durable without adding fields to Episode history or mixing profile state into runtime `.env` settings.

## Frontend

`/profile` is a focused workbench page:

- left side: editable form with identity, domains, tone, preferred phrases, banned phrases, good examples, and bad examples;
- right side: summary cards explaining what will enter prompts;
- sidebar navigation exposes "创作者画像";
- submit uses a server action and returns to `/profile`.

The page follows the existing Windows 95/98 pixel interface: dense fields, hard borders, no marketing hero, no nested decorative cards.

## Testing

Backend tests cover:

- default profile response;
- update trimming/deduplication/persistence;
- clearing identity and list fields;
- local-only update protection;
- topic prompt includes `creator_profile`;
- script prompt includes `creator_profile`;
- section rewrite prompt includes `creator_profile`;
- service passes profile into topic/script generation.
- scheduled automation passes the saved profile into topic generation.

Frontend contract tests cover:

- `/profile` is present and enabled in the sidebar;
- API client exposes `getCreatorProfile` and `updateCreatorProfile`;
- server actions expose `updateCreatorProfileAction`;
- `/profile` page contains the expected editable fields.

## Error Handling

If the profile table is missing, the repository creates it and returns defaults. If the stored payload is corrupt, it falls back to defaults and lets the next save rewrite a valid row. Invalid request shapes are rejected by Pydantic. The prompt layer must work when no profile is configured.

## Review Notes

No placeholders remain. The scope is intentionally limited to local editable profile and prompt injection; audience comments, semantic retrieval, and native microphone capture remain separate 2.0 follow-up tasks.
