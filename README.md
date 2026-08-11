# AI视频日记 Agent Studio

Local-first content production Agent for AI video diaries.

Current build delivers:

- Pixel-style Next.js workbench
- FastAPI workflow API with a real LangGraph episode graph
- DeepSeek-backed topic and script generation
- Obsidian vault indexing for local knowledge sources
- Hermes daily radar ingestion for Product Hunt + 观猹 reports
- External link source ingestion, defaulting to Watcha / 观猹
- Product Hunt connector, pending token configuration
- SQLite-backed Episode persistence
- Cross-episode feedback memory for future topic/script prompts
- Preference memory cards from topic/script thumbs feedback
- Reversible feedback events, so preference memory uses active evidence instead of raw like/down counts
- Editable script workspace with manual save, full regeneration, section regeneration, and script version history
- Editable runtime settings page with secret-safe updates
- Local video inbox scanning, conservative long-silence edit plans, ffmpeg preview processing, and Episode-level video binding
- Local Whisper-driven high-confidence retake removal with report tracking, preserved prior videos, and final subtitles regenerated after the refined edit
- Tencent Cloud ASR for real video transcription when credentials are configured
- Draft asset package generation with manifest, Tencent ASR SRT fallback to script-estimated SRT, and 4:3 PNG cover
- Jianying draft import adapter: creates traceable packages, can try a plaintext-compatible bridge into the draft root, and records every result in `manifest.json`
- Local automation worker for daily topic scheduling and unattended pending-video processing
- Human review flow
- 2.0 beta UI foundation with a classic Windows 95/98 pixel shell
- Visual edit-plan workbench at `/edit-plan`, including a video preview window, source markers, edit timeline, edit decision cards, voice定位结果, and Jianying track preview
- 2.0 content templates for 视频日记、AI 工具测评 and 教程演示, with Episode-level template selection
- Content-template-aware Agent prompts for topic generation, script generation, and section regeneration
- Preference-memory-aware Agent prompts: active thumbs feedback is summarized into prefer / avoid signals for future topic, script and section prompts
- Runtime health diagnostics page for DeepSeek, Obsidian, ffmpeg, Whisper, video directories, draft output, Jianying and external sources
- Persisted edit-plan segment decisions for 保留 / 删除 / 复核 with rollback
- Confirmed edit-plan execution: user 保留 / 删除 overrides generate a second-pass `_confirmed` preview before Jianying draft packaging
- Reusable bottom voice command console across the app, with command preview and execution for continuing the flow, regenerating topics/scripts, rewriting script sections, draft creation, and edit-plan segment decisions
- Root-level visual guide: `WINDOWS_PIXEL_UI_GUIDE.md`
- Agent prompt and content intelligence upgrade guide: `docs/ai-video-diary-agent-prompt-strategy.md`
- UI contract checks that reject terminal-style black/green palettes, gradients, large radii, emoji icons, and missing 2.0 edit-plan entry points
- Episode list and history browser at `/history` with status filtering, publish metrics, and platform tracking
- Creator profile at `/profile` with learnable common expressions, banned words, and style preferences
- Source file lifecycle tracking (pending → processing → processed → archived) with archive/unarchive support
- Production chain visualization: 10-stage pipeline from source clips through Jianying import, with per-stage status
- 2.0 beta acceptance pack: `docs/ai-video-diary-2.0-beta-acceptance-pack.md`

Not yet implemented:

- Native Jianying 10.7 encrypted draft authoring without the compatibility bridge
- Native watchdog event listener; current worker uses stable polling
- COS/URL mode for Tencent ASR files larger than the local direct-upload limit
- Real microphone capture; the current command console executes typed text or already-transcribed voice text
- macOS code signing and notarization; DMG is built by CI but requires right-click → Open on first launch
- Desktop menu bar dynamic Agent status indicator, system notifications, and global shortcuts
- Jianying template effects beyond title cards, subtitles, and covers (keyword highlights, BGM, transitions, intro/outro)

## Local Configuration

Create `.env` at the repo root:

```bash
DEEPSEEK_API_KEY=...
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
OBSIDIAN_VAULT_PATH=/Users/Zhuanz/Desktop/鑫哥知识库
PRODUCT_HUNT_TOKEN=...
TENCENTCLOUD_SECRET_ID=...
TENCENTCLOUD_SECRET_KEY=...
TENCENTCLOUD_ASR_REGION=ap-shanghai
TENCENTCLOUD_ASR_ENGINE_MODEL_TYPE=16k_zh_en
HERMES_RADAR_PATH=/Users/Zhuanz/Desktop/product-hunt-raw/daily-reports
TOPIC_SOURCE_URLS=https://watcha.cn/
VIDEO_INBOX_PATH=/Users/Zhuanz/Movies/AI视频日记/01_待处理视频
VIDEO_OUTPUT_PATH=/Users/Zhuanz/Movies/AI视频日记/06_待发布成片
DRAFT_OUTPUT_PATH=/Users/Zhuanz/Movies/AI视频日记/05_剪映草稿
JIANYING_DRAFT_ROOT_PATH=/Users/Zhuanz/Movies/JianyingPro/User Data/Projects/com.lveditor.draft
DATABASE_PATH=/Users/Zhuanz/Movies/AI视频日记/agent.sqlite3
```

`HERMES_RADAR_PATH` points to the Markdown reports generated by the Hermes AI 产品日报 job.
When a report such as `2026-05-27.md` exists, the API parses Product Hunt and 观猹 entries into topic sources.
This lets the topic Agent use real daily radar material even when `PRODUCT_HUNT_TOKEN` is not configured.

`TOPIC_SOURCE_URLS` accepts comma-separated URLs. These pages are converted into source items for topic generation.
Product Hunt requires `PRODUCT_HUNT_TOKEN`; without it, the UI reports the missing token instead of inventing products.
The draft page creates a local asset package and manifest under `DRAFT_OUTPUT_PATH`.
It also exposes a "try import to Jianying" action. The action writes a plaintext-compatible draft through the available draft tool, syncs root metadata when possible, and records whether the attempt imported, failed, or fell back to package-only mode.
The package includes:

- the processed video reference
- `subtitle.srt`, generated by Tencent Cloud ASR when configured; otherwise generated from the approved script timing estimate
- `cover.png`, a 4:3 pixel-style cover
- `manifest.json`, including the honest Jianying status

Tencent Cloud ASR uses `CreateRecTask` + `DescribeTaskStatus` for long audio recognition.
The first implementation extracts `audio_16k.wav` from the processed video and sends it by local data upload when it is below the configured byte limit.
For longer videos, use COS/URL mode in a later iteration instead of pretending transcription succeeded.

It does not claim native encrypted Jianying 10.7 draft authoring. Jianying 10.7 stores current drafts with encrypted metadata, so this project treats the bridge result as a compatibility import that must be opened and verified manually. The source package remains the reliable fallback.

Run API:

```bash
corepack pnpm dev:api
```

Run web:

```bash
corepack pnpm dev:web
```

Run the local automation worker:

```bash
corepack pnpm dev:worker
```

## Verification

Run verification:

```bash
corepack pnpm test:api
corepack pnpm test:web-contract
corepack pnpm test:pixel-style
corepack pnpm build:web
corepack pnpm --dir apps/web exec tsc --noEmit
corepack pnpm --dir apps/web lint
```

If `pnpm` is already on `PATH`, the equivalent shorter commands also work:

```bash
pnpm test:api
pnpm test:web-contract
pnpm test:pixel-style
pnpm build:web
pnpm --dir apps/web exec tsc --noEmit
pnpm --dir apps/web lint
```

Visit:

- http://localhost:3000 — 今日工作台
- http://localhost:3000/welcome — 首次配置向导
- http://localhost:3000/templates — 内容模板（视频日记 / AI 工具测评 / 教程演示）
- http://localhost:3000/topics — 选题池
- http://localhost:3000/script — 口播稿编辑
- http://localhost:3000/video — 视频处理
- http://localhost:3000/edit-plan — 剪辑计划台
- http://localhost:3000/draft — 剪映草稿与发布面板
- http://localhost:3000/history — Episode 历史
- http://localhost:3000/learning — 反馈学习与偏好记忆
- http://localhost:3000/profile — Creator Profile
- http://localhost:3000/sources — 信息源管理
- http://localhost:3000/knowledge — 知识库浏览
- http://localhost:3000/health — 运行健康
- http://localhost:3000/settings — 运行时设置

The UI should follow `WINDOWS_PIXEL_UI_GUIDE.md`: classic Windows 95/98 pixel desktop software, not a generic SaaS dashboard, terminal console, or decorative retro skin.

## Episode State Machine

Episodes flow through 11 states:

```
CREATED → SOURCES_INGESTING → TOPICS_READY → TOPIC_APPROVED
→ SCRIPT_READY → SCRIPT_APPROVED → WAITING_VIDEO
→ VIDEO_PROCESSING → DRAFT_READY → PUBLISHED
                                               ↘ FAILED (any stage)
```

Four intake modes determine how video enters the pipeline:

- `planned_single` — one recorded video for an approved topic+script
- `planned_batch` — multiple clips merged and processed together
- `ad_hoc_single` — single spontaneous video; skips topic/script
- `ad_hoc_batch` — multiple spontaneous clips; skips topic/script

## Desktop Shell

An Electron-based macOS desktop shell lives in the `coordinator` repository
(`coordinator/apps/desktop/`). It manages the API/Worker/Next.js lifecycle,
provides native menus (open video dir, open vault), and builds to DMG via
electron-builder. GitHub Actions CI produces arm64 + x64 DMG artifacts on
tagged releases. Code signing is documented in `RELEASE.md` but not yet
configured — first-time users must right-click → Open to bypass Gatekeeper.
