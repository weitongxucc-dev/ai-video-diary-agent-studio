# Productized Video Production Workflow Design

## Goal

Turn AI视频日记 Agent Studio from a capable local prototype into a stable daily production product.

The final user experience should feel like this:

1. The user opens the workbench and immediately sees what is ready, what is blocked, and what to do next.
2. The user can generate, edit, regenerate, approve, and later recover script content without losing prior versions.
3. The user can process either one recorded video or multiple short clips as an explicit production mode.
4. The system produces a traceable final video package: source clips, merge or trim step, silence cut, retake removal, subtitles, cover, and Jianying package.
5. The user can mark a video as published and feed real performance data back into the Agent.
6. The Agent's learning is explainable, reversible, and based on concrete user actions instead of raw like/down counts.
7. The user can inspect history, reopen any episode, rerun a step, and understand source quality or failures.

This remains a local-first single-user creator tool. It is not a multi-user SaaS, an auto-publishing system, or a model training product.

## Final Product Shape

### Main Workbench

The home page becomes a control surface for the current production day.

It should answer:

- Is the system configured enough to produce today's video?
- What is the current episode status?
- What is the next human action?
- Which automated task is currently running?
- What failed, and what action fixes it?

The primary call to action changes by state:

- Missing configuration: go to setup checklist.
- No topic: generate topics.
- Topic ready: review topic.
- Script ready but not approved: edit or approve script.
- Waiting video: record or select video mode.
- Processing: watch job status.
- Draft ready: import or open package.
- Published: record performance.

### Setup And Health

Settings should stop feeling like a raw `.env` editor.

Each dependency gets:

- status: ready, optional, missing, invalid, or degraded;
- affected workflow step;
- last checked time;
- short repair action;
- link to the relevant field.

Core checks:

- DeepSeek API key and model.
- Obsidian path exists and has Markdown files.
- Hermes report path exists and latest report can be parsed.
- external source URLs are configured and can be fetched.
- ffmpeg and ffprobe are available.
- local Whisper Python and model exist.
- Tencent ASR credentials exist, and direct upload limit is visible.
- video inbox and output paths exist.
- draft output path exists.
- Jianying root path exists.
- draft tool availability.

The system may still run in degraded mode, but the UI must be honest about what will be skipped.

## Script Editing And Regeneration

### Requirements

The script page must support real human editing.

The user can:

- edit hook, every section, title options, cover copy, publish copy, and comment prompt;
- save edits;
- approve the edited script;
- regenerate the whole script;
- regenerate one section;
- revert to an earlier saved version;
- see whether the current script came from AI, manual edit, full regeneration, or section regeneration.

### Version Model

Add script version history.

Each `ScriptVersion` stores:

- version id;
- episode id;
- source type: `ai_generated`, `manual_edit`, `full_regeneration`, `section_regeneration`, `revert`;
- full script package snapshot;
- changed section ids;
- user note or reason;
- created time;
- active flag.

The Episode keeps the current active script as before, but old versions are preserved in persistence.

### Regeneration

Full regeneration uses:

- selected topic;
- current source references;
- recent active feedback;
- current preference memory;
- optionally, the previous script as "avoid repeating this exact wording".

Section regeneration uses:

- selected topic;
- full current script;
- target section id;
- user reason;
- local context from neighboring sections.

If LLM is unavailable, the UI should show the reason and keep the current script untouched.

## Video Production Modes

### Modes

Video processing should be explicit about intent.

Supported modes:

- `planned_single`: a scripted episode using one source video.
- `planned_batch`: a scripted episode using multiple ordered clips.
- `ad_hoc_single`: an unscripted one-video production.
- `ad_hoc_batch`: an unscripted multi-clip production.

The UI should describe these in creator language:

- "单条视频精剪": use one recording.
- "多段素材拼剪": combine selected clips in order.
- "跳过选题脚本，直接处理已录视频": ad hoc mode.

### Production Chain

Every processed video should expose a production chain:

```text
source clips
-> optional ordered merge
-> conservative silence cut and speed/loudness pass
-> local Whisper retake scan
-> optional retake removal
-> final subtitle generation
-> cover generation
-> draft package
-> optional Jianying compatibility import
```

The video page should show this chain as status rows, not as hidden files only.

### Source Clip Lifecycle

After a video or batch is processed, source files should not keep looking like untouched pending work.

Add a source file state:

- `pending`
- `processing`
- `processed`
- `archived`
- `skipped`

The first implementation can persist this as Episode-bound processed source names and offer an "archive processed sources" action.

Archiving means moving files from inbox to a configured or default archive directory under the inbox, for example:

```text
01_待处理视频/_已处理/2026-06-04/
```

No source file should be deleted automatically.

## Draft And Publishing

### Draft Page

The draft page should make the fallback model clear:

- source package is the reliable artifact;
- Jianying import is a compatibility bridge;
- manual validation is required after import;
- import status and error are recorded in `manifest.json`.

The user can:

- create or recreate a package from the final processed video;
- try importing one package into Jianying;
- see package contents;
- see subtitle source;
- see ASR status;
- open the package path from the UI when supported by the environment.

### Publish Feedback UI

Add a publish panel when an Episode is draft-ready or published.

Fields:

- publish URL;
- platform, default manual/free text;
- play count;
- like count;
- comment count;
- notes;
- published at.

Submitting this marks the episode as published and creates a feedback signal for learning.

Publishing does not auto-post.

## Feedback Learning, Undo, And Rollback

### Problem

Current feedback is too shallow. It stores like/down events but does not behave like an intelligent, reversible learning loop.

### Feedback Events

Upgrade feedback events so each event can be active or reverted.

Fields:

- event id;
- target type: topic, script_section, script_version, source_item, published_episode;
- target id;
- value: up, down, edit, publish_signal, revert;
- reason;
- before snapshot where useful;
- after snapshot where useful;
- created at;
- active;
- reverted at;
- revert reason.

### Preference Memory

Build preference cards from active evidence.

Card types:

- liked topic direction;
- avoided topic direction;
- preferred script structure;
- disliked phrase or style;
- recurring manual edit pattern;
- high-performing published pattern;
- low-performing published pattern.

Each card should show:

- signal;
- confidence;
- evidence count;
- latest evidence;
- actions: disable, restore, inspect evidence.

This is not fine-tuning. It is explainable prompt memory.

### Rollback

The user can revert:

- a feedback event;
- a script version;
- a preference card;
- a publish performance entry.

Reverting should not delete history. It marks the item inactive and updates derived memory.

## Sources And Trust

### Source Sync Status

Sources should expose:

- configured status;
- last sync time;
- last success time;
- last failure reason;
- count used in current topic generation;
- whether each source participated in the selected topic.

Manual link ingestion should preserve the ingested item beyond in-memory runtime state.

### Topic Evidence

Topic cards should show not only refs, but why the source matters:

- source title;
- source type;
- excerpt;
- freshness;
- whether it came from Obsidian, Hermes, Product Hunt, Watcha, or manual link.

When topic generation fails, the UI should distinguish:

- LLM not configured;
- no source material;
- source sync failure;
- LLM response invalid;
- network/provider error.

## Episode History

Add an episode history page.

The user can:

- see all episodes by date and day number;
- filter by status;
- open an episode;
- see selected topic, script state, video state, draft package, publish result;
- reopen retake report;
- recreate draft package;
- mark published or update publish metrics;
- inspect source files used.

The current day remains the default workbench, but history is necessary for trust and rollback.

## Error Handling

User-facing errors should be actionable.

Examples:

- ffmpeg missing: show installation/configuration hint and link to Settings.
- local Whisper missing: show exact missing path and allow continuing without retake removal.
- Tencent ASR direct upload too large: explain local fallback or future COS/URL requirement.
- source sync failed: show URL or path and last error.
- Jianying import failed: preserve package and show package path.
- job interrupted: show "safe to retry" when no final artifact was created.

Internal exception names can appear in technical details, but the primary message should be human-readable.

## Implementation Phases

### Phase 1: Script Control And Feedback Foundations

Deliver:

- editable script fields;
- save script edits;
- full script regeneration;
- section regeneration;
- script version history;
- feedback event ids and active/reverted state;
- undo feedback.

Success criteria:

- editing a script persists across reloads;
- approving uses edited text;
- regeneration creates a new version without deleting the previous one;
- reverting restores a prior version;
- reverted feedback no longer influences generated topics/scripts.

### Phase 2: Video Production Chain And Source Lifecycle

Deliver:

- explicit production modes;
- video production chain model and response;
- video page chain display;
- processed source state;
- archive processed sources action.

Success criteria:

- single and batch processing show different intent;
- final output traces back to source clips;
- processed source clips can be archived, not deleted;
- repeated pending video confusion is removed.

### Phase 3: Publish Feedback And History

Deliver:

- publish feedback UI;
- episode history list/detail;
- update publish metrics;
- retake report access from history;
- package and draft access from history.

Success criteria:

- draft-ready episode can be marked published from UI;
- published metrics appear in learning context;
- old episodes can be opened and inspected.

### Phase 4: Source Trust And Smarter Preference Memory

Deliver:

- persisted manual links;
- source sync status and failure reasons;
- source participation metadata;
- preference cards with evidence and disable/restore actions.

Success criteria:

- user can tell why a topic was suggested;
- failed source sync is visible and repairable;
- preference memory is inspectable and reversible.

## Testing Strategy

### Backend

Add focused tests for:

- script edit persistence;
- script version creation and revert;
- full regeneration and section regeneration;
- feedback event active/reverted behavior;
- preference memory excluding reverted feedback;
- production mode persistence;
- processed source archiving;
- publish metric persistence;
- episode history response;
- source sync status and failures.

### Frontend

Add TypeScript and contract coverage for:

- script editable payloads;
- video production modes;
- publish feedback payloads;
- episode history types;
- source status types;
- preference card actions.

### End-To-End Manual Verification

For a real local run:

1. Generate topics.
2. Approve topic.
3. Generate script.
4. Edit script and save.
5. Regenerate one section.
6. Revert to previous script version.
7. Process multiple short clips.
8. Confirm production chain and retake report.
9. Archive processed sources.
10. Generate draft package.
11. Try Jianying import.
12. Mark published with metrics.
13. Confirm learning page reflects active evidence.
14. Revert one feedback signal.
15. Open the episode from history.

## Non-Goals

This design does not add:

- automatic social publishing;
- multi-user accounts;
- cloud storage;
- model fine-tuning;
- automatic deletion of source media;
- native encrypted Jianying 10.7 authoring;
- complex analytics dashboards.

## Open Product Decisions

These can be decided during implementation without changing the architecture:

- whether script section regeneration should use a text prompt field in the first version;
- whether archive path is configurable in Settings or derived from inbox path only;
- whether history page should initially be list-only or include detail in the same route;
- whether preference card disable/restore ships in Phase 1 or Phase 4.

