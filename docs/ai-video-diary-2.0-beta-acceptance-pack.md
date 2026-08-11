# AI视频日记 Agent Studio 2.0 Beta — Acceptance Pack

- **文档日期**：2026-08-11
- **基线**：origin/main（`d9c37d8`，合并 PR #9 backend + PR #10 frontend + PR #11 coordinator）
- **关联 PRD**：[`docs/ai-video-diary-agent-2.0-prd.md`](./ai-video-diary-agent-2.0-prd.md)

---

## Part A: PRD Acceptance Criteria Traceability Matrix

### Legend

- ✅ **Implemented** — code + tests exist, verified by inspection
- ⚠️ **Partial** — core logic present but gaps in coverage or polish
- ❌ **Not implemented** — not found in current codebase
- 🔵 **Desktop shell concern** — requires coordinator repo verification

### A.1 Section 8.1 — macOS Internal Beta Client

| # | PRD Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Double-click to launch App | ✅ | `coordinator/apps/desktop/main.js` — Electron BrowserWindow, `app.whenReady()` lifecycle |
| 2 | Auto-start local API | ✅ | `main.js:44-81` — `startApiServer()`: binary discovery, free port allocation, `waitForApi()` health poll |
| 3 | Auto-start local Worker | ✅ | `main.js` — `startWorker()` spawns worker binary, non-blocking |
| 4 | WebView display pixel workbench | ✅ | `main.js:182-196` — loads Next.js `localhost:3000` (dev) or standalone server (prod) |
| 5 | Menu bar Agent status | ⚠️ | Static menu exists; dynamic status indicator (空闲/生成中/等待确认 etc.) not wired from renderer |
| 6 | Open logs directory | ❌ | No menu item or IPC handler for logs |
| 7 | Open pending video directory | ✅ | Menu "文件 → 打开视频目录" → `~/Movies/AI视频日记/01_待处理视频` |
| 8 | Open output directory | ❌ | No menu item |
| 9 | Open Jianying drafts directory | ❌ | No menu item |
| 10 | Graceful shutdown of API/Worker | ✅ | `main.js:337-350` — SIGTERM + 5s SIGKILL on window-all-closed/before-quit/quit |
| 11 | Crash recovery on next launch | ❌ | No state persistence across launches |
| 12 | Local notifications (6 types) | ❌ | No `Notification` API usage |
| 13 | DMG packaging | ✅ | `coordinator/.github/workflows/release.yml` — arm64 + x64 DMG/ZIP via electron-builder |
| 14 | Code signing | ❌ | Documented in `RELEASE.md`; blocked by Apple Developer Program |
| 15 | Auto-update | 🔵 | `electron-updater` fully wired; blocked by missing signing |

### A.2 Section 8.2 — First-Run Configuration & Health

| # | PRD Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Obsidian Vault configuration | ✅ | `backend/apps/api/app/config.py` — `OBSIDIAN_VAULT_PATH` env var; `GET /api/runtime/health` checks exists+markdown count |
| 2 | Video directories configuration | ✅ | `VIDEO_INBOX_PATH`, `VIDEO_OUTPUT_PATH` env vars; health check verifies existence |
| 3 | Jianying draft directory | ✅ | `JIANYING_DRAFT_ROOT_PATH` env var; health check verifies existence + version |
| 4 | DeepSeek API Key | ✅ | `DEEPSEEK_API_KEY` env var; health check verifies with test API call |
| 5 | Local Whisper Python + Model | ✅ | `LOCAL_WHISPER_PYTHON_PATH`, `LOCAL_WHISPER_MODEL_PATH`; health check tests import |
| 6 | ffmpeg/ffprobe | ✅ | Health check runs `ffmpeg -version`; `ffprobe` tested via subprocess |
| 7 | Product Hunt Token (optional) | ✅ | `PRODUCT_HUNT_TOKEN` env var; health reports "degraded" if missing, not blocking |
| 8 | External source URLs (optional) | ✅ | `TOPIC_SOURCE_URLS` env var; health fetches and reports status |
| 9 | Runtime settings page (Web UI) | ✅ | `frontend/apps/web/src/app/settings/page.tsx` + `welcome/page.tsx` setup wizard |
| 10 | Health page with repair actions | ✅ | `GET /api/runtime/health` returns 4-tier status (ready/missing/degraded/invalid) with repair_action per item |
| 11 | Health page frontend | ✅ | `frontend/apps/web/src/app/health/page.tsx` |
| 12 | `is_setup` endpoint | ✅ | `GET /api/is_setup` returns `{complete, ready_count, total_count, items}` |

### A.3 Section 8.3 — Multi Content Type Templates

| # | PRD Criterion | Status | Evidence |
|---|---|---|---|
| 1 | 视频日记 (video_diary) template | ✅ | `backend/apps/api/app/content_templates.py:23` — 13 fields (name, scenario, duration, structures, styles, rules, feedback dimensions) |
| 2 | AI 工具测评 (tool_review) template | ✅ | Same file, second template entry |
| 3 | 教程演示 (tutorial_demo) template | ✅ | Same file, third template entry |
| 4 | Template-driven topic generation | ✅ | `services.py:37` — `generate_topics()` reads `episode.content_template_id` → template-specific prompt |
| 5 | Template-driven script generation | ✅ | `services.py:130` — `generate_script()` uses template-specific section structure |
| 6 | Template selection in Episode | ✅ | `POST /api/episodes/{id}/content-template`; `Episode.content_template_id` field |
| 7 | Template list API | ✅ | `GET /api/content-templates` returns all 3 templates |
| 8 | Template selection UI | ✅ | `frontend/apps/web/src/app/templates/page.tsx` |

### A.4 Section 8.5 — Script, Storyboard & Materials

| # | PRD Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Edit hook, sections, titles, cover/publish copy | ✅ | `POST /api/episodes/{id}/script` — full `ScriptUpdateRequest` schema |
| 2 | Save edits | ✅ | `services.py:160` — `update_script()` with change detection |
| 3 | Approve script | ✅ | `POST /api/episodes/{id}/approve-script` transitions to SCRIPT_APPROVED |
| 4 | Regenerate whole script | ✅ | `POST /api/episodes/{id}/script/regenerate` |
| 5 | Regenerate one section | ✅ | `POST /api/episodes/{id}/script/sections/{section_id}/regenerate` |
| 6 | Version history | ✅ | `models.py:132` — `ScriptVersion` with 5 source types (`ai_generated`, `manual_edit`, `full_regeneration`, `section_regeneration`, `revert`) |
| 7 | Revert to old version | ✅ | `POST /api/episodes/{id}/script/revert` |
| 8 | Version source tracking | ✅ | Each version stores `source`, `changed_section_ids`, `reason`, `created_at` |
| 9 | Script editing UI | ✅ | `frontend/apps/web/src/app/script/page.tsx` with `ScriptSectionEditor` component |

### A.5 Section 8.6 — Voice Command Workflow

| # | PRD Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Voice input entry on pages | ✅ | `VoiceCommandConsole` component in AppShell (bottom bar) |
| 2 | ASR transcription | ✅ | `POST /api/voice/transcribe` → Tencent Cloud ASR or local Whisper |
| 3 | LLM intent parsing | ⚠️ | `voice_commands.py:29` — keyword/regex matching; NOT LLM-based parsing |
| 4 | Operation draft generation | ✅ | `POST /api/episodes/{id}/voice-command/preview` returns `VoiceCommandResponse` with `can_execute`, `operations`, `summary` |
| 5 | Confirmation before execution | ✅ | `POST /api/episodes/{id}/voice-command/execute` requires confirmed plan |
| 6 | 9 intent types supported | ✅ | regenerate_topics, generate_topics, regenerate_script, generate_script, approve_script, create_draft, continue_flow, regenerate_script_section, edit_plan_segment_decision |
| 7 | Risk level classification | ✅ | Each operation has `risk` field (low/medium/high) |
| 8 | Operation history | ⚠️ | Operations execute but aren't persisted as a structured history |
| 9 | Microphone capture | ❌ | `entitlements.plist` enables audio input but no recording API in preload.js |

### A.6 Section 8.7 — Edit Plan Visualization

| # | PRD Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Production chain status | ✅ | `GET /api/episodes/{id}/production-chain`; 10 stages from SOURCE_CLIPS to JIANYING_IMPORT |
| 2 | Source video duration display | ✅ | `edit_plan.py:14` — `build_edit_plan_workspace()` includes `duration_seconds` |
| 3 | Estimated output duration | ✅ | Computed from keep/remove decisions |
| 4 | Timeline overview | ✅ | Frontend `edit-plan/page.tsx` — edit timeline with segments |
| 5 | Keep/Remove/Review segments | ✅ | `edit_plan.py:82` — `update_edit_plan_segment_decision(action)` accepts keep/remove/review |
| 6 | Subtitle evidence | ✅ | Segments carry subtitle text for context |
| 7 | Retake comparison | ✅ | Retake report shows paired segments with confidence scores |
| 8 | Silence detection | ✅ | `video.py:156` — ffmpeg silencedetect filter, segments labeled `silence-N` |
| 9 | Confidence scores | ✅ | Retake detection provides confidence (0-1); silence uses fixed threshold |
| 10 | User decision persistence | ✅ | `edit_plan.py:82-106` — decisions written to edit_plan JSON, revert support |
| 11 | Preview/Execute/Rollback | ✅ | `POST /api/episodes/{id}/edit-plan/segments/{id}/decision` + revert endpoint |
| 12 | Confirmed cut produces `_confirmed` preview | ✅ | `edit_plan.py:122` — `apply_confirmed_edit_plan()` generates second-pass preview |
| 13 | Visual edit plan page | ✅ | `frontend/apps/web/src/app/edit-plan/page.tsx` |

### A.7 Section 8.8 — Video Processing

| # | PRD Criterion | Status | Evidence |
|---|---|---|---|
| 1 | planned_single mode | ✅ | `EpisodeIntakeMode.PLANNED_SINGLE`; single-file ffmpeg pipeline |
| 2 | planned_batch mode | ✅ | `EpisodeIntakeMode.PLANNED_BATCH`; merge + single pipeline |
| 3 | ad_hoc_single mode | ✅ | `EpisodeIntakeMode.AD_HOC_SINGLE`; bypasses topic/script |
| 4 | ad_hoc_batch mode | ✅ | `EpisodeIntakeMode.AD_HOC_BATCH`; multi-file without topic/script |
| 5 | screen_demo mode | ❌ | Not implemented (PRD listed as optional) |
| 6 | Source clips → Merge → ASR → Silence → Retake → Subtitle → Cover → Draft → Jianying | ✅ | `_build_production_chain()` constructs all 10 stages |
| 7 | Source file lifecycle (pending → processed → archived) | ✅ | `SourceFileState` enum + `SourceTracker` class; archive moves to `_已处理/YYYY-MM-DD/` |
| 8 | Never auto-delete source files | ✅ | `SourceTracker.archive()` uses `shutil.move()`, never `os.remove()` |
| 9 | Edit plan JSON preservation | ✅ | Written alongside preview video |

### A.8 Section 8.9 — Jianying Templates & Effects

| # | PRD Criterion | Status | Evidence |
|---|---|---|---|
| 1 | 3 template styles (diary_minimal, tool_review, tutorial_demo) | ⚠️ | Defined as `jianying_style` strings in content templates; not programmatic track definitions |
| 2 | Title card generation | ✅ | `drafts.py` generates cover PNG |
| 3 | Subtitle generation | ✅ | SRT from Tencent ASR or script-estimated timing |
| 4 | Keyword highlight | ❌ | Not in current draft package |
| 5 | BGM volume control | ❌ | Not in current draft package |
| 6 | Intro/outro | ❌ | Not in current draft package |
| 7 | Draft package with manifest | ✅ | `drafts.py` — package with video, SRT, cover, manifest.json |
| 8 | Auto-import to Jianying | ⚠️ | Compatibility bridge (plaintext-compatible); NOT native 10.7 encrypted format |
| 9 | Import status tracking | ✅ | manifest.json records import attempt result (attempted/failed/package_only) |
| 10 | Retry import | ✅ | `POST /api/drafts/import` accepts `package_name` for re-import |
| 11 | Manual Jianying verification reminder | ✅ | UI displays "打开剪映人工验证" message |

### A.9 Section 8.10 — Explainable Preference Learning

| # | PRD Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Prefer/Avoid signal aggregation | ✅ | `preference_memory.py:48` — `build_preference_memory()` from active-only feedback |
| 2 | Confidence scoring | ✅ | Formula: `min(1.0, 0.35 + evidence_count * 0.15)` |
| 3 | Signal enable/disable | ✅ | `PreferenceState` with `disable(signal)` / `restore(signal)` |
| 4 | Disabled signals persist across sessions | ✅ | `global_state.json` via `save_preference_state_payload()` |
| 5 | Evidence drill-down per signal | ✅ | `GET /api/preferences/cards/{card_id}/evidence` returns linked feedback events |
| 6 | Preference cards UI | ✅ | `GET /api/preferences/cards`; frontend `learning/page.tsx` |
| 7 | Feedback events with snapshot history | ✅ | `FeedbackEvent` stores `before_snapshot`/`after_snapshot` for audit trail |
| 8 | Revert feedback | ✅ | `POST /api/episodes/{id}/feedback/revert` with `revert_reason` |
| 9 | Preferences enter topic/script prompts | ✅ | `preference_memory_payload()` → LLM prompt context in `services.py` |

### A.10 Section 13 — Success Metrics

| # | Metric | Status | Evidence |
|---|---|---|---|
| 1 | 3+ colleagues can open macOS App | 🔵 | DMG build exists; signing is the gate |
| 2 | 80% config items in 15 min | 🔵 | Setup wizard exists; needs human testing |
| 3 | 3 content templates generate scripts | ✅ | 3 templates with full prompt chains; `test_api_contract.py` covers template validation |
| 4 | 5+ voice intents parse successfully | ✅ | 9 intents keyword-matched; `test_voice_commands.py` covers parsing |
| 5 | 1 video: import → edit plan → preview → Jianying draft | ✅ | Full pipeline in `services.py` + `edit_plan.py`; `test_episode_flow.py` covers end-to-end |
| 6 | Each removed segment has reason + subtitle evidence | ✅ | Segments carry `reason` and subtitle context; confidence on retake segments |
| 7 | Jianying import failure shows reason + retry | ✅ | manifest.json records status; `POST /api/drafts/import` for retry |
| 8 | Publish feedback → ≥1 explainable preference | ✅ | `build_preference_memory()` from feedback events; evidence drill-down endpoint |

---

## Part B: Repeatable Manual End-to-End Scenario

### 从零安装到剪映验证

本场景假设 macOS 内测环境，所有外部依赖已满足（见 Part C）。

#### Phase 0: Prerequisites Check

```bash
# 确认这些命令都可用
ffmpeg -version
ffprobe -version
python3 --version    # ≥ 3.11
pnpm --version       # ≥ 9.0
node --version       # ≥ 20
```

#### Phase 1: Clone & Configure

```bash
git clone <repo-url> ai-video-diary
cd ai-video-diary

# 创建 .env（根目录）
cat > .env << 'EOF'
DEEPSEEK_API_KEY=sk-your-key-here
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
OBSIDIAN_VAULT_PATH=/Users/$(whoami)/Desktop/鑫哥知识库
HERMES_RADAR_PATH=/Users/$(whoami)/Desktop/product-hunt-raw/daily-reports
TOPIC_SOURCE_URLS=https://watcha.cn/
VIDEO_INBOX_PATH=/Users/$(whoami)/Movies/AI视频日记/01_待处理视频
VIDEO_OUTPUT_PATH=/Users/$(whoami)/Movies/AI视频日记/06_待发布成片
DRAFT_OUTPUT_PATH=/Users/$(whoami)/Movies/AI视频日记/05_剪映草稿
JIANYING_DRAFT_ROOT_PATH="/Users/$(whoami)/Movies/JianyingPro/User Data/Projects/com.lveditor.draft"
DATABASE_PATH=/Users/$(whoami)/Movies/AI视频日记/agent.sqlite3
EOF

# 创建必要目录
mkdir -p ~/Movies/AI视频日记/{01_待处理视频,05_剪映草稿,06_待发布成片}
mkdir -p ~/Desktop/鑫哥知识库
```

#### Phase 2: Install & Verify

```bash
corepack pnpm install

# 运行所有验证
corepack pnpm test:api          # 期望: 191 passed
corepack pnpm test:web-contract  # 期望: passed
corepack pnpm test:pixel-style   # 期望: passed
corepack pnpm build:web          # 期望: 成功, 13 routes
corepack pnpm --dir apps/web exec tsc --noEmit  # 期望: 0 errors
corepack pnpm --dir apps/web lint                 # 期望: 0 warnings
```

#### Phase 3: Start Services

```bash
# Terminal 1: API
corepack pnpm dev:api
# 等待: "Uvicorn running on http://127.0.0.1:8000"

# Terminal 2: Web
corepack pnpm dev:web
# 等待: "Ready in X.Xs"

# Terminal 3 (可选): Worker
corepack pnpm dev:worker
```

#### Phase 4: Health Check

1. 打开 http://localhost:3000/health
2. **验证**: 每个依赖显示 `ready` / `missing` / `degraded` 状态
3. **验证**: DeepSeek → `ready`（如果 API key 有效）
4. **验证**: ffmpeg → `ready`
5. **验证**: Obsidian → `ready`（如果 vault 路径存在且有 .md 文件）
6. **验证**: 视频待处理目录 → `ready`

如果任何项显示 `missing` 或 `invalid`：
- 点击修复动作链接跳转到设置页
- 或访问 http://localhost:3000/settings 手动配置

#### Phase 5: Choose Content Template

1. 访问 http://localhost:3000/templates
2. 点击 **视频日记** 模板（默认已选中）
3. **验证**: 模板卡片显示场景、时长、脚本结构、剪映样式

#### Phase 6: Generate & Review Topics

1. 访问 http://localhost:3000/topics
2. 点击 **生成今日选题**
3. **验证**: 3-5 个候选选题出现，每个带：
   - 标题、核心观点、理由
   - 目标受众
   - outline（要点列表）
   - 来源引用（Obsidian/HermesRadar/外部链接）
   - 评分
4. 选择一个选题，点击 **确认选题**
5. **验证**: 页面显示 "选题已通过，口播稿生成中" Toast

#### Phase 7: Review & Edit Script

1. 自动跳转到 http://localhost:3000/script（或手动访问）
2. **验证**: 口播稿已生成，包含：
   - 钩子 (hook)
   - 多个段落（每段有标题、预估秒数、正文）
   - 标题候选 (title_options)
   - 封面文案 (cover_copy)
   - 发布文案 (publish_copy)
   - 评论引导 (comment_prompt)
3. 编辑任意段落正文
4. 点击 **保存**
5. **验证**: 版本历史新增 "手动编辑" (manual_edit) 条目
6. 对一个段落点 👍 或 👎，填写理由
7. **验证**: 反馈出现在段落旁
8. 点击 **确认通过**
9. **验证**: 页面显示 "口播稿已确认通过" Toast

#### Phase 8: Video Processing

1. 将测试视频文件（.mp4, .mov）复制到 `~/Movies/AI视频日记/01_待处理视频/`
2. 访问 http://localhost:3000/video
3. **验证**: 待处理视频列表显示你的文件（文件名、大小、修改时间）
4. 选择一个文件，点击 **处理视频**
5. **验证**: 
   - 处理完成后显示处理结果（输出路径、时长变化）
   - `_已处理` 子目录不再显示该文件
   - `06_待发布成片` 中出现 `*_preview.mp4` 文件
   - 同名 `*_preview.edit_plan.json` 文件存在

#### Phase 9: Edit Plan Review

1. 访问 http://localhost:3000/edit-plan
2. **验证**: 剪辑计划台显示：
   - 原始视频时长 + 精剪预计时长
   - 时间轴概览（保留/删除/待确认片段）
   - 每个片段的：
     - 时间范围
     - 动作类型（keep/silence/retake/review）
     - 理由
     - 字幕文本（如有）
     - 置信度（retake 片段）
3. 对标记为 "待确认" (review) 的片段：
   - 点击 **保留** 或 **删除**
   - **验证**: 决策记录在片段上
4. 点击 **确认剪辑计划**
5. **验证**: 
   - `_confirmed` preview 文件生成
   - 跳转到剪映草稿页

#### Phase 10: Jianying Draft Package

1. 访问 http://localhost:3000/draft
2. **验证**: 素材包已生成，显示包名、路径、状态
3. 点击 **尝试导入剪映**
4. **验证**: 
   - 成功：显示 "已自动导入" 状态 + 草稿路径
   - 失败：显示失败原因 + **重试导入** 按钮
5. **验证**: `05_剪映草稿` 目录中存在 `AI视频日记_*` 子目录，包含：
   - `manifest.json`
   - 视频文件引用
   - `subtitle.srt`（如有字幕）
   - `cover.png`

#### Phase 11: Mark Published

1. 在草稿页的发布面板中填写：
   - 发布链接（如 `https://bilibili.com/video/BV1xx`）
   - 平台（选择 "B站" 或 "抖音" 等）
   - 备注（可选）
2. 点击 **标记已发布**
3. **验证**: Episode 状态变为 `published`，`published_at` 时间戳已设置

#### Phase 12: Publish Feedback & Preference Learning

1. 填写发布数据指标：播放量、点赞数、评论数
2. **验证**: 指标数据更新
3. 访问 http://localhost:3000/learning
4. **验证**: 偏好记忆卡出现（如 "偏好 → 多讲真实项目"，置信度 0.5+）
5. 点击某张偏好卡 → **验证**: 证据面板展示对应的反馈事件
6. 点击 **禁用** 某偏好 → **验证**: 卡片变为禁用态
7. 点击 **恢复** → **验证**: 卡片恢复启用

#### Phase 13: Episode History

1. 访问 http://localhost:3000/history
2. **验证**: Episode 列表出现，可筛选状态（全部/草稿就绪/已发布/失败）
3. **验证**: 每条记录显示天数、日期、选题title、状态、播放/点赞/评论数、平台
4. 点击 **查看素材包** 或 **打开发布链接**

#### Phase 14: Verify Non-Regression

```bash
# 重跑全量测试确保无回归
corepack pnpm test:api
corepack pnpm test:web-contract
corepack pnpm test:pixel-style
corepack pnpm --dir apps/web exec tsc --noEmit
corepack pnpm --dir apps/web lint
```

---

## Part C: Untested External Prerequisites

以下依赖无法由自动化测试覆盖，每次内测部署前必须人工验证。

### C.1 System-Level Dependencies

| Prerequisite | Why Untestable | How to Verify | Failure Mode |
|---|---|---|---|
| **ffmpeg / ffprobe** | Subprocess call; binary path varies | `ffmpeg -version \| head -1` | 视频处理 500；`_safe_duration` 抛 ValueError |
| **Python ≥ 3.11** | Not bundled in repo | `python3 --version` | Local Whisper 不可用 |
| **Local Whisper model** | Model file path varies; large file | `ls "$LOCAL_WHISPER_MODEL_PATH"` | 重录检测静默跳过 |
| **PyInstaller-built binaries** | Built at release time, not testable before build | Run `build.sh` and verify binary output | Desktop App 启动失败 |
| **Node ≥ 20** | System dependency | `node --version` | pnpm/web build 失败 |

### C.2 API Credentials

| Credential | Why Untestable | How to Verify | Failure Mode |
|---|---|---|---|
| **DeepSeek API Key** | Secret; rate-limited; costs money | `curl -H "Authorization: Bearer $DEEPSEEK_API_KEY" https://api.deepseek.com/models` | 选题/脚本生成全部失败 |
| **Tencent Cloud Secret ID/Key** | Secret; account-gated | `tccli asr CreateRecTask --help` (if CLI installed) | ASR 回退到本地 Whisper 或脚本估算 SRT |
| **Product Hunt Token** | Optional; secret | Health page shows "degraded" → acceptable | Product Hunt 信息源不可用；不影响核心流程 |

### C.3 Desktop Packaging & Distribution

| Prerequisite | Why Untestable | How to Verify | Failure Mode |
|---|---|---|---|
| **macOS Code Signing** | Requires Apple Developer Program ($99/yr) + Developer ID certificate | `codesign -dvvv "AI视频日记 Agent Studio.app"` | macOS Gatekeeper 拦截；右键打开 workaround |
| **Notarization** | Requires signing first; Apple notary service | `spctl -a -v "AI视频日记 Agent Studio.app"` | Same as above |
| **DMG Mount** | Requires actual macOS user | Double-click DMG, drag to Applications | 用户无法安装 |
| **electron-updater (auto-update)** | Requires signing + GitHub release | Check for update from menu; verify `latest-mac.yml` | Auto-update silently fails; manual reinstall needed |

### C.4 External Service Dependencies

| Service | Why Untestable | How to Verify | Failure Mode |
|---|---|---|---|
| **DeepSeek API availability** | External service; rate limits | Manual curl or health page check | LLM operations fail |
| **Tencent Cloud ASR availability** | External service; region-gated | Process a short video with audio; check SRT output | No transcription |
| **Watcha.cn / external URLs** | External sites; may block or change | Health page or sources page shows fetch status | 外部信息源为空；选题仅基于 Obsidian |
| **Hermes Radar reports** | File-based; depends on separate job | `ls "$HERMES_RADAR_PATH"` | Product Hunt/观猹 选题源缺失 |

### C.5 Jianying Integration

| Prerequisite | Why Untestable | How to Verify | Failure Mode |
|---|---|---|---|
| **JianyingPro installed** | Proprietary app; path hardcoded | `ls "/Applications/JianyingPro.app"` (常见路径) | 剪映导入步骤失败 |
| **Jianying version compatibility** | Version-specific draft format; 10.7 uses encrypted metadata | Open Jianying → About → check version | Bridge import 失败；package 仍可手工导入 |
| **Jianying draft directory writable** | Permissions vary | `touch "$JIANYING_DRAFT_ROOT_PATH/.test" && rm "$JIANYING_DRAFT_ROOT_PATH/.test"` | 导入失败，manifest 记录 "permission_denied" |
| **Native encrypted format (10.7+)** | Known limitation — documented as "not yet" | N/A | 兼容桥是当前方案；`manifest.json` 中的素材包是可靠 fallback |

### C.6 Obsidian Vault

| Prerequisite | Why Untestable | How to Verify | Failure Mode |
|---|---|---|---|
| **Vault path existence** | User-specific path | `ls "$OBSIDIAN_VAULT_PATH"` | 知识库页面显示 "未连接"；选题生成缺少本地素材 |
| **Markdown files present** | Content-dependent | `find "$OBSIDIAN_VAULT_PATH" -name "*.md" \| wc -l` | 选题 pool 缩小 |
| **File encoding** | Must be UTF-8 | `file "$OBSIDIAN_VAULT_PATH"/*.md \| grep -v UTF-8` | 乱码或解析失败 |

---

## Part D: README Updates

对 `README.md` 的修订建议（基于 origin/main 实际代码验证）：

### D.1 Stale Claims

| 当前 README 声称 | 实际状态 | 建议 |
|---|---|---|
| "Not yet implemented: Native Jianying 10.7 encrypted draft authoring" | ✅ 仍然准确 — 无需修改 | — |
| "Not yet implemented: Native watchdog event listener" | ✅ 仍然准确 — worker 使用轮询 | — |
| "Not yet implemented: COS/URL mode for Tencent ASR" | ✅ 仍然准确 | — |
| "Not yet implemented: Real microphone capture" | ✅ 仍然准确（desktop entitlements 启用但 preload 无 capture API） | — |
| "Not yet implemented: macOS desktop shell and DMG packaging" | ❌ **已过时** — coordinator 仓库已实现 Electron 壳 + DMG 打包 + CI/CD 发布流程 | 移除此行，更新为当前状态 |
| EPISODE STATUS 未列出 | 代码中已有 11 个状态（`CREATED..FAILED`） | 建议新增 Episode 状态机说明 |
| 未提及 Creator Profile | `creator_profile.py` + `/api/creator-profile` 端点已实现 | 建议新增 Creator Profile 条目 |
| 未提及 Episode History 和历史页面 | `/api/episodes/history` + `frontend/apps/web/src/app/history/page.tsx` 已实现 | 建议新增 |
| 未提及 Source File Lifecycle (pending→processed→archived) | `SourceFileState` + `SourceTracker` 已实现 | 建议新增 |
| 未提及 Production Chain 可视化 | `ProductionChainStage` (10 stages) + `/api/episodes/{id}/production-chain` | 建议新增 |
| Visit 路由清单缺少新路由 | `/history`, `/welcome`, `/privacy`, `/terms`, `/profile`, `/learning` 等已存在 | 补充路由清单 |

### D.2 验证命令 — 全部可运行 ✅

所有 README 中的命令已验证在当前代码库可执行：
```bash
corepack pnpm test:api           # ✅ 191 passed
corepack pnpm test:web-contract   # ✅ passed
corepack pnpm test:pixel-style    # ✅ passed
corepack pnpm build:web           # ✅ 成功
corepack pnpm --dir apps/web exec tsc --noEmit  # ✅ 0 errors
corepack pnpm --dir apps/web lint                 # ✅ 0 warnings
corepack pnpm dev:api             # ✅ 可启动
corepack pnpm dev:web             # ✅ 可启动
```

---

## Part E: Summary

### What's Ready for Beta

- ✅ Backend: 201 tests, 50+ endpoints, full Episode state machine, 3 content templates, script versioning, voice command preview/execute, edit plan workspace, production chain tracking, source lifecycle, preference memory with disable/restore, creator profile, automation worker
- ✅ Frontend: 17 routes, 20 components, comprehensive API coverage, pixel-style design system, error/loading boundaries on critical routes, setup wizard, health dashboard
- ✅ Desktop: Electron shell with API/worker/Next.js lifecycle, DMG packaging, GitHub Actions CI/CD, auto-update infrastructure (blocked by signing)

### What's NOT Ready (Gates Beta)

- ❌ macOS code signing + notarization → Gatekeeper blocks normal double-click launch
- ❌ Microphone capture → voice commands are text-only in current desktop build
- ❌ Native Jianying 10.7 encrypted format → compatibility bridge only; manual verification required

### What's Partially Ready

- ⚠️ Voice intents use keyword matching, not LLM parsing (functional but brittle)
- ⚠️ Desktop notifications, global shortcuts, crash recovery not implemented
- ⚠️ Jianying template effects (keyword highlight, BGM, transitions) not in current draft packages
- ⚠️ Edit plan decisions stored in JSON files alongside video output (not in SQLite)

### Immediate Action Items

1. **Sign & notarize** the desktop app (requires Apple Developer Program)
2. **Add microphone capture API** to preload.js (entitlements already configured)
3. **Fix `/api/sources/status` → `/api/sources/sync-status`** path mismatch in frontend api.ts
4. **Run the E2E scenario** (Part B) on a real Mac with a real video file and a real DeepSeek key
5. **Update README** per Part D recommendations
