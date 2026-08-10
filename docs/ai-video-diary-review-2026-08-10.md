# AI视频日记 2.0 — 前端 + Desktop 深度审查报告

- **审查日期**：2026-08-10
- **审查分支**：`weitongxucc-dev/reviewer`
- **审查范围**：
  - **Frontend**: 17 modified + 24 untracked files in `apps/web/`
  - **Desktop**: `coordinator/apps/desktop/main.js` + `preload.js`
- **审查方法**：13-agent 并行工作流（7 读取 + 3 对抗验证 + 1 合成），355K tokens
- **backend 不在本次审查范围**（backend 已独立审查通过）

---

## BLOCKER（阻断级 — 必须在上线前修复）

### B1. `types.ts:22` — `EpisodeIntakeMode` 枚举值前后端不一致

```
前端: "planned" | "ad_hoc_video" | "ad_hoc_video_batch"        ← 旧值
后端: planned_single | planned_batch | ad_hoc_single | ad_hoc_batch  ← 新值（e79ab1a）
```

- **失败场景**：后端返回 `intake_mode: "planned_single"` → TypeScript 类型拒绝此值 → Episode 列表/详情页类型不安全；前端发送 `intake_mode: "planned"` → 后端 `EpisodeIntakeMode("planned")` 抛 ValueError
- **注意**：前端 `VideoProductionMode` 类型（line 24）已用正确的新值，但 `Episode.intake_mode` 字段仍声明为旧 `EpisodeIntakeMode`

### B2. `types.ts:179-180` — Episode 类型字段名与后端不一致：`publish_platform`/`publish_notes` ≠ `platform`/`notes`

```typescript
// types.ts:179-180 — 前端
publish_platform: string | null;
publish_notes: string | null;

// models.py Episode dataclass + schemas.py EpisodeResponse — 后端
platform: str | None = None
notes: str | None = None
```

- **失败场景**：后端返回 `{"platform": "bilibili", "notes": "首条视频"}` → 前端 `episode.publish_platform` 始终为 `undefined`，`episode.publish_notes` 始终为 `undefined`
- **影响范围**：
  - `draft/page.tsx:102-103` — 发布面板 `initialPlatform`/`initialNotes` 回退到错误的默认值
  - `history/page.tsx:208-209` — Episode 历史列表的平台列永不显示
  - `types.ts:202` `EpisodeHistoryItem.publish_platform` — 同上
- **额外矛盾**：`PublishFeedback` 类型（line 144-152）已正确使用 `platform` 和 `notes`，与 `Episode` 类型自相矛盾

### B3. `api.ts` — 6 个前端 API 调用使用不存在的端点或错误的 method/path

| 前端调用 | 行号 | 后端实际 | 问题 |
|---|---|---|---|
| `GET /api/episodes/history` | 391-393 | **不存在** | 端点缺失 |
| `GET /api/sources/status` | 145 | `GET /api/sources/sync-status` | **路径不同** |
| `POST /api/sources/{key}/retry` | 150-152 | **不存在** | 端点缺失 |
| `GET /api/preferences/cards` | 178-180 | **不存在**（有 `GET /api/preferences`） | 端点缺失 |
| `GET /api/preferences/cards/{id}/evidence` | 182-185 | **不存在** | 端点缺失 |
| `POST /api/preferences/cards/{id}/disable` | 189-192 | `PATCH /api/preferences/disable` | **method + path 都不同** |
| `POST /api/preferences/cards/{id}/restore` | 196-199 | `PATCH /api/preferences/restore` | **method + path 都不同** |
| `GET /api/episodes/{id}/topic-evidence` | 165-167 | **不存在** | 端点缺失 |
| `POST /api/episodes/{id}/production-mode` | 290-295 | **不存在** | 端点缺失 |

- **影响范围**：`/history`/`/sources`/`/learning` 三个页面的核心功能全部 404/500
- **注意**：`/api/is_setup`（`api.ts:109`）后端已存在 ✅；`updatePublishMetrics`（`api.ts:385`）使用 `POST` 但后端是 `PUT` — 可能工作但违反 REST 语义

### B4. `actions.ts:132-499` — 全部 28 个 server action 没有任何错误处理

- **失败场景**：API 返回 4xx/5xx → `request()` 抛 Error → Next.js 显示通用错误页面而非用户友好的 Toast
- **多步操作部分失败导致状态不一致**：
  - `approveTopicAndGenerateScript`（line 132）：`approveTopic` 成功 + `generateScript` 失败 → 选题已确认但无脚本，无错误提示
  - `processVideoAndPackageAction`（line 267）：前两步成功 + `createEpisodeDraft` 失败 → 视频已处理但草稿未创建
- **修复建议**：每个 action 包装 try/catch，catch 块中调用 `redirectWithToast(path, "error", message)`

### B5. `main.js:330-334` — Desktop `activate` 事件缺少 `apiReady` 守卫

- **失败场景**：API 启动失败 → `apiReady = false` → macOS 用户点击 Dock 图标 → `activate` 触发 → `createWindow()` 创建窗口指向死 API → 空白/错误页
- **修复建议**：
  ```javascript
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0 && apiReady) {
      createWindow();
    }
  });
  ```

---

## HIGH（高风险 — 建议本迭代修复）

### H1. `page.tsx:48-51` — 首页 `episode` 为 null 时崩溃

```typescript
// line 48 — TypeError: Cannot read properties of undefined (reading '0')
const currentTopic = episode?.topics[0] ?? null;

// line 49-51 — TypeError: undefined.find is not a function
const selectedTopic = episode?.topics.find(
  (topic) => topic.id === episode.selected_topic_id,
);
```

- **失败场景**：API 不可达 → `safelyGetTodayEpisode()` 返回 null → `episode?.topics` 为 `undefined` → `undefined[0]` 和 `undefined.find(...)` 抛 TypeError → 首页崩溃
- `?.` 只保护 `.topics` 访问，不保护后续的 `[0]` 索引和 `.find()` 调用
- **修复**：`episode?.topics?.[0]` 和 `episode?.topics?.find(...)`

### H2. `settings/page.tsx:406,482` — 离线时 `runtime` 为 null 导致崩溃

```typescript
// line 406 — runtime?.status 为 undefined → undefined.external_sources 抛 TypeError
const sourceUrls = runtime?.status.external_sources.urls?.join("\n") ?? "";

// line 482 — 同模式
{runtime?.status.external_sources.urls.length ?? 0}
```

- **修复**：`runtime?.status?.external_sources?.urls?.join("\n")` — 每个 `.` 之前都需要 `?.`

### H3. `settings/page.tsx:120-262` — `configuredOf`/`valueOf` 查找 key 大小写不匹配

- `settingMap` 的 key 来自后端 `RuntimeSettingItem.name`
- 但 `configuredOf("DEEPSEEK_API_KEY")` 等调用使用 UPPER_CASE，而 form `name` 属性使用 lowercase（`"deepseek_api_key"`）
- **结果**：所有 secret 字段永远显示"未配置"/"粘贴新密钥"，即使用户已配置
- **修复**：统一为 lowercase 或确认后端的实际命名格式

### H4. `globals.css:1113-1151` — `.metric-card` 颜色变体 CSS 全部错误

```css
/* green/purple/orange 的 .filled 块全部错误使用 var(--accent-blue) */
.metric-card.green .metric-blocks .filled  { background: var(--accent-blue); }
.metric-card.purple .metric-blocks .filled { background: var(--accent-blue); }
.metric-card.orange .metric-blocks .filled { background: var(--accent-blue); }

/* ::before 装饰条同上 */
.metric-card.green::before  { background: var(--accent-blue); }
.metric-card.purple::before { background: var(--accent-blue); }
.metric-card.orange::before { background: var(--accent-blue); }
```

- **结果**：绿/紫/橙色变体卡片全部显示为蓝色，颜色区分完全失效

### H5. `FormErrorBanner.tsx:56` — `findInvalidField` 的有效性检查逻辑反转

```typescript
function findInvalidField(form) {
  if (!form) return null;
  if (!form.checkValidity()) {  // BUG: checkValidity() 返回 false 时 form 无效
    return null;                // 但这里却 return null — 正是在需要搜索时退出了
  }
  // 搜索 invalid 字段的代码只在 form 有效时执行（永远找不到）
}
```

- **修复**：`if (form.checkValidity()) { return null; }` — 去掉 `!`

### H6. `history/page.tsx:80-97` — 日期解析无防御，无效日期崩溃整页

- `formatDate`/`formatLongDate` 调用 `new Date(value)` 对空字符串/malformed 字符串产生 `Invalid Date`，`Intl.DateTimeFormat.format()` 抛 `RangeError` 崩溃整页
- 影响 lines 123（summarizeItem）、194（日期显示）、212（发布时间）

### H7. `AppShell.tsx:32,49,65,72` — SSR 期间 `usePathname()` 返回 null 导致崩溃

- Next.js App Router 的 `usePathname()` 在 SSR 初始渲染时返回 `null`
- `pathname.startsWith(item.href)` 在 `pathname === null` 时抛 TypeError
- 虽然客户端 hydration 后会恢复，但每次页面加载都有布局抖动和控制台错误

### H8. `topics/page.tsx:352-353` — URL `topic_id` 不匹配时静默回退到第一个 topic

- 用户通过 URL `?topic_id=nonexistent` 访问 → 页面静默显示第一个 topic 而非报错
- 显示的 topic 与 URL 参数不一致，action form 使用显示 topic 的 id 而非 URL 参数 → 数据不一致

### H9. `video/page.tsx:58,63,71` — 用 try/catch 吞 null 访问而非显式 null guard

- `getTodayEpisode()` 返回 null 时 → `episode.id` 抛 TypeError → 被 try/catch 吞掉
- 应该用显式 null 检查代替异常路径

### H10. Frontend 24 个未跟踪文件未提交

- `history/page.tsx`, `privacy/page.tsx`, `terms/page.tsx`, `welcome/page.tsx`, 4 个 `error.tsx`, 8 个 `loading.tsx`
- `HelpPanel.tsx`, `KeyboardShortcuts.tsx`, `PixelToast.tsx`, `PixelTooltip.tsx`, `RouteError.tsx`, `FormErrorBanner.tsx`, `Skeleton.tsx`, `WorkflowProgress.tsx`
- `helpPanel.ts`
- **git clone 后 `pnpm build:web` 失败**：`AppShell.tsx` import 这些文件但不存在

---

## MEDIUM（中风险）

### M1. `actions.ts:119-121` — `scriptFromFormData` 不验证 `estimated_seconds` 是否为有效数字

- `Number("abc")` 返回 `NaN`，JSON.stringify 序列化为 `null` → 后端收到 null 而非有效数字

### M2. `api.ts:223-231` vs `api.ts:280-288` — `archiveSources` 和 `archiveEpisodeVideoSources` 完全重复

- 调用同一端点、相同参数、相同返回类型 — 删除一个

### M3. `topics/page.tsx:162` — outline item 文本作为 React key，相同文本产生 duplicate key

### M4. `learning/page.tsx:316,319` — 组合 key 可能重复（相同 label + signal）

### M5. `history/page.tsx:197-198` — `topic_title` 在 strong 和 summarizeItem 中各渲染一次，重复显示

### M6. `history/page.tsx:218-248` — 历史页导航链接不带 episode ID

- "查看素材包" 链接到 `/draft`、"查看重录报告" 链接到 `/video`，都不带 episode 上下文 → 死胡同 UX

### M7. `Welcome/page.tsx:271-277` — `onSkipFinish` 与 `onFinish` 完全相同的回调，无语义区分

### M8. `HelpPanel.tsx:473` — 帮助面板缺少焦点管理（打开时不聚焦面板内，关闭时不归还焦点）

### M9. `WorkflowProgress.tsx:127-131` — API 失败静默降级为"今日 Episode 待创建"，用户无法区分加载失败 vs 无数据

### M10. `RouteError.tsx:28,31,34` — 错误消息过滤过度激进

- 长于 240 字符的消息被完全隐藏（应截断而非丢弃）
- `STACK_LINE_PATTERN` 将 `"Failed at step 3"` 误判为堆栈跟踪
- `FILE_PATH_PATTERN` 将 `"at startup"` 误判为文件路径

### M11. `main.js:60-63` — Desktop `spawn()` 缺少 `error` 事件处理器

- 如果二进制在 `fs.accessSync` 检查后变为不可执行（TOCTOU），`spawn` 的 async error 事件无人处理 → Electron 主进程崩溃

### M12. `main.js:145-153` — `open-path` IPC 无路径白名单

- 渲染器可传递任意存在的文件路径给 `shell.openPath`，虽然有 `contextIsolation` 缓解，但如果渲染器被 XSS 攻破可打开任意文件

### M13. `main.js:118-127` — `stopApiServer` 被注册 3 次（window-all-closed + before-quit + quit），定时器累积泄露

---

## LOW（低风险/建议）

### L1. `PixelToast.tsx:71,176` — 每个 toast 有两个并行的自动关闭定时器（provider + 组件），功能性无害但潜在 race

### L2. `PixelTooltip.tsx:48-52` — slug 截断到 40 字符无唯一性后缀，可能产生重复 DOM ID

### L3. `PixelTooltip.tsx:29` — 当 child 已有 `aria-label` 时，tooltip 的 label 被静默丢弃

### L4. `PixelTooltip.tsx:63-68` — trigger span 无可聚焦性（无 `tabIndex`），键盘用户无法触发

### L5. `PixelToast.tsx:153,187` — 嵌套 live region（viewport `role="status"` + 每个 toast `aria-live="polite"`），屏幕阅读器播报两次

### L6. `Skeleton.tsx:28-33` — `role="status"` 不适合占位符，应用 `aria-hidden="true"`

### L7. `HelpPanel.tsx:393-402` 与 `KeyboardShortcuts.tsx:26-35` — `isEditable` 逻辑重复

### L8. `RouteError.tsx:96-97` — `reset()` 抛异常时无错误处理

### L9. `error.tsx:18`（root）— 错误描述硬编码为"加载今日 Episode 失败"，但此边界捕获所有路由的错误（如 `/privacy` 页面崩溃也显示此信息）

### L10. `loading.tsx:12`（root）— 加载文字硬编码"正在加载今日工作台..."，但作为所有无自定义 loading 路由的 fallback

### L11. `settings/page.tsx:339-347` — 目录选择器使用 DOM 遍历查找 text input，依赖 CSS class name，结构变更时静默失效

### L12. `AppShell.tsx:49,72` — `startsWith` 路由匹配对 `/draft` vs 未来 `/draft-history` 等路径会产生误匹配

### L13. `main.js:83-116` — `waitForApi` 无指数退避，固定 1s 重试 30 次

### L14. `main.js:79` — `waitForApi` reject 时不杀死孤儿 API 进程

### L15. `preload.js:16` — `onOpenHelp` 无 `removeListener` 方法，组件重复挂载时监听器累积

---

## Desktop 专项

### 确认问题

| 行号 | 问题 | 严重度 |
|---|---|---|
| 330-334 | `activate` 事件缺少 `apiReady` 守卫（同 B5） | BLOCKER |
| 60-63 | `spawn()` 缺少 `error` 事件处理器 → 主进程崩溃风险 | MEDIUM |
| 145-153 | `open-path` 无路径白名单/遍历限制 | MEDIUM |
| 118-127 | `stopApiServer` 3 次注册 + 定时器累积 | MEDIUM |
| 186-196 | `apiPort=0` 时生产模式 `loadURL` 到 `127.0.0.1:0` 必定失败 | LOW |
| 188+192 | 生产模式双重加载（先 loadURL 再 loadFile），页面闪烁 | LOW |
| 132-134 | `get-api-port` 返回 0 而非 null/拒绝当 API 不可用时 | LOW |
| 83-116 | `waitForApi` 固定 1s 重试，无退避 | LOW |
| 79 | `waitForApi` reject 时孤儿 API 进程 | LOW |
| 65-71 | stdout/stderr 监听器从不移除 | LOW |
| 353-355 | 第二实例静默退出，无用户反馈 | LOW |
| 16 (preload) | `onOpenHelp` 无 `removeListener`，监听器累积 | LOW |

### 通过项 ✅

| 检查项 | 结论 |
|---|---|
| `main.js` + `preload.js` 语法 | ✅ 有效 |
| `contextIsolation: true` + `nodeIntegration: false` | ✅ 安全默认 |
| IPC 契约：5 handler ↔ 5 preload 方法 | ✅ 100% 覆盖 |
| `open-help` menu → preload 事件通道 | ✅ 已连接 |
| 单实例锁 `requestSingleInstanceLock()` | ✅ 正确 |
| `startApiServer` 失败路径 | ✅ ErrorBox + 不创建窗口 |
| `setWindowOpenHandler` 外部链接 | ✅ 浏览器打开 + deny 新窗口 |

---

## 结论汇总

| 级别 | 数量 | 关键发现 |
|---|---|---|
| 🔴 BLOCKER | 5 | B1 intake_mode 枚举不一致、B2 publish_platform/notes 字段名不匹配、B3 9 个端点不存在/路径错误、B4 28 个 action 无错误处理、B5 Desktop activate 无 apiReady 守卫 |
| 🟠 HIGH | 10 | H1 首页 null crash、H2 设置页 null crash、H3 设置 key 大小写不匹配、H4 CSS 颜色变量错误、H5 FormErrorBanner 逻辑反转、H6 日期解析 crash、H7 SSR pathname null crash、H8 topic URL 静默回退、H9 try/catch 吞 null、H10 24 个文件未跟踪 |
| 🟡 MEDIUM | 13 | M1 NaN 透传、M2 函数重复、M3-M4 重复 key、M5 重复渲染、M6 死胡同链接、M7 死代码、M8 焦点管理缺失、M9 静默降级、M10 过滤过度、M11 spawn error 缺失、M12 路径无白名单、M13 定时器泄露 |
| 🔵 LOW | 15 | L1-L15 代码卫生、a11y、边界情况 |

### 修复优先级

1. **B1 + B2**（本周）：前后端类型契约对齐 — 不修复则数据管道断裂
2. **B3 + B4**（本周）：补齐端点 + action 错误处理 — history/sources/learning 页面核心功能不可用
3. **B5**（本周）：Desktop activate 守卫 — 一行修复
4. **H10**（本周）：git add 未跟踪文件 — 否则他人无法构建
5. **H1-H9**（本迭代）：null 安全 + 逻辑 bug + CSS 修复
6. **M1-M13**（下迭代）：代码质量加固
7. **L1-L15**（持续改进）：a11y + 代码卫生

### 积极发现

- 前端 HelpPanel/Toast/ErrorBoundary/Skeleton/WorkflowProgress UX 基础设施显著提升了用户体验完整性
- PixelTooltip/PixelToast/FormErrorBanner 组件设计思路正确，主要问题在细节实现（a11y、边界情况）
- Desktop IPC 契约一致性 100%，contextIsolation 等 Electron 安全最佳实践到位
- 所有 lint/build/contract/pixel-style 检查全部通过
