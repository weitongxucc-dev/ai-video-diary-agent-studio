# 阶段 A 组件交付清单与 API 契约

> 状态：**主线程已批准 / 阶段 A 已收口**
> 基线：仓库根目录 [`DESIGN.md`](../DESIGN.md) v1.0（已与批准基线逐字核对）
> 扩散限制：本提交不修改业务页面；后续页面采用仍需按页面单独验收。
> 验证入口：`/ui-preview` 仅是组件与壳层验收夹具，不是产品页面。

## 1. 交付范围与当前采用状态

| 交付项 | 文件 | 当前状态 | 业务页采用边界 |
| --- | --- | --- | --- |
| `PixelPrimitives` | `apps/web/src/components/PixelPrimitives.tsx` | 新增；稳定 Props 已导出 | 仅 `/ui-preview` 直接使用；`AppShell`、`VoiceCommandConsole`、`RouteError`、`Skeleton` 内部使用 |
| `AppShell` | `apps/web/src/components/AppShell.tsx` | 修改；`children` 兼容旧调用，Inspector/status Props 已导出 | 既有业务页继续通过原有 `<AppShell>{children}</AppShell>` 使用；未向业务页扩散新 props |
| `RetroChrome` | `apps/web/src/components/RetroChrome.tsx` | 修改；仅供 `AppShell` 内部使用 | 业务页禁止直接引用 |
| `VoiceCommandConsole` | `apps/web/src/components/VoiceCommandConsole.tsx` | Controller/View 已拆分；稳定 Props 已导出 | 业务页禁止直接引用 View；现有 API/类型依赖未改变 |
| `RouteError` | `apps/web/src/components/RouteError.tsx` | generic category/domain hint 已收口 | 既有 error boundary 通过 deprecated `hint` 保持兼容；新调用使用 `domainHint` |
| `Skeleton` 组件族 | `apps/web/src/components/Skeleton.tsx` | Props 已导出；旧卡片墙 presets 已 deprecated | 既有 loading 页面兼容保留；新页面禁止使用 deprecated presets |
| `PixelScrollableRegion` | `PixelPrimitives.tsx` 内 | 稳定 API；可选控件/ResizeObserver/Home/End | 仅 `/ui-preview`；业务阶段条/时间线/宽表尚未迁移 |
| 共享样式/token | `apps/web/src/app/globals.css` | 新旧规则并存，阶段 A 规则位于尾部覆盖层 | 只定义共享契约；页面私有旧样式仍待后续阶段迁移 |
| 组件单测 | `PixelPrimitives.test.tsx`、`RetroChrome.test.tsx`、`RouteError.test.tsx`、`Skeleton.test.tsx` | 已执行 | 不改变业务逻辑 |
| 视觉门禁 | `apps/web/scripts/phase-a-browser-check.js`、`pixel-style-contract-check.mjs` | 已执行 | 仅验证 `/ui-preview` 与共享契约 |

### 1.1 API 稳定与采用规则

- 本文批准的 Props 已显式导出；未导出的内部实现仍不构成公共 API。
- `VoiceCommandConsoleView` 是可测试的纯视觉契约；业务逻辑留在 `VoiceCommandConsole` Controller，业务页不直接装配 View。
- 业务页不得直接使用 `.pixel-panel__*`、`.shell-*`、`.voice-command-*` 等内部类名。
- 不通过新增 `variant`、页面专用 class 或 raw color 绕过本文契约；新增能力需先补本文和组件测试。

## 2. 统一 token 与基础规则

| DESIGN.md token | CSS 映射 | 组件使用 |
| --- | --- | --- |
| `canvas #C9C3B4` | `--retro-canvas` | 应用桌面、壳层外背景 |
| `surface #F4EFD9` | `--retro-surface` | 一级工作区、Panel、Button 中性表面 |
| `surface_subtle #ECE6D0` | `--retro-surface-subtle` | 工具栏、导航、Inspector、dock、内部标题区 |
| `inset #FFFDF0` | `--retro-inset` | 输入、轨道承载区、可编辑/可滚动内容 |
| `ink #171717` | `--retro-ink` | 主文字和默认图标 |
| `muted #60656F` | `--retro-muted` | 次级说明、idle 状态 |
| `border #111111` | `--retro-border` | 窗口、可按压/可编辑控件 |
| `border_soft #6F6A5B` | `--retro-border-soft` | 分隔线、一级 Panel、轻边界 |
| `selection #064A9B` | `--retro-selection` | 当前导航、唯一主动作、running 标记 |
| `success #1F8A4C` | `--retro-success` | succeeded 小状态标记 |
| `warning #C28A18` | `--retro-warning` | waiting-for-user 状态标记 |
| `danger #B7332F` | `--retro-danger` | failed/blocked、危险动作 |
| `focus #F0C14B` | `--retro-focus` | 所有键盘焦点，2px outline |
| spacing 4/8/12/16/24/32 | `--space-1`…`--space-6` | 组件内距、组件间距和壳层栅格 |
| display font | `--font-pixel` | 标题、短标签、按钮、导航 |
| body font | `--font-readable` | 14px/1.6 正文、说明、错误、路径、输入 |

统一尺寸：主/危险动作最小高 40px；普通控件 34px；紧凑工具目标不得小于 32×32px；图标通常 16px；圆角为 0。状态不得只靠颜色，必须同时具备文本和形状。

### 2.1 最小尺寸契约

| 组件 | 最小尺寸与收缩规则 |
| --- | --- |
| `PixelPanel` | 不设固定高度；`min-width: 0`，标题、说明和操作允许安全换行，内容决定高度 |
| `PixelButton` | default/主/危险动作高至少 40px；compact 仍不得低于 32px，文字按钮不得仅靠 16px 图标形成目标 |
| `PixelInput` | 输入高至少 34px；label、hint、error 不计入输入目标高度且不得被定高裁切 |
| `PixelStatus` | 标记形状至少 8×8px；组件不设固定高，文字可换行，不承担点击目标 |
| `PixelEmpty/Loading/Blocked/ErrorState` | 不设固定高度；12px 内距、4px 左状态边，动作仍服从 Button 的 40/32px 门槛 |
| `PixelScrollableRegion` | 外层与 viewport 必须 `min-width: 0`；高度由内容决定；不得用固定高度裁掉阶段标题或焦点环 |
| `AppShell` | 支持门禁视口下限 1024×768；主区必须 `min-width: 0`；1024 Inspector 最大 360px 且不得压缩主区 |
| `RetroChrome` | 工具/菜单目标至少 32px；主要文字按钮至少 34px 高、40px 宽；标题栏不得用定高裁掉高缩放文字 |
| `VoiceCommandConsole` | 输入和主动作至少 40px；mic 至少 32×32px；详情区最大高 180px并独立纵滚，不给 dock 设置会裁字的固定总高 |
| `RouteError` | 容器宽 `min(720px, 100%)`，不设固定高度；恢复主动作至少 40px，技术详情允许长词换行 |
| `Skeleton/LoadingShell` | `Skeleton` 尺寸由最终布局显式给出；无默认固定页面高度；`LoadingShell` 由内容撑开，不得以 skeleton 撑出虚假卡墙 |

## 3. `PixelPrimitives`

### 3.1 `PixelPanel`

**职责**：为一个有明确任务意义的一级分组提供标题、说明、操作槽和内容区；不负责业务状态、数据加载或页面布局。

**稳定 API**

```ts
export type PixelPanelProps = {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "aside"; // 默认 section
  headingId?: string;                 // 可覆盖自动生成 id
};
```

**token 映射**：`surface` 主体、`surface_subtle` 标题区、`border_soft` 1px 边界、12/16px 内距；H2 为 pixel 15px/1.35，说明为 readable 13px/1.55。

**适用**：一个主对象、一个表单分组、一个具有独立标题的 Inspector 分组。
**禁止**：每段文字套 Panel；Panel 内连续嵌套 Panel；把统计卡墙包装成 Panel；用 `className` 注入页面私有颜色/阴影。

**状态矩阵**

| 状态 | 契约 |
| --- | --- |
| default | 可选标题/说明/操作，内容区必有 children |
| loading | Panel 本身不拥有 loading；内容使用 `PixelLoadingState` 或结构 skeleton |
| empty/blocked/error | Panel 本身不变色；内容使用对应 State 组件 |
| disabled | 不支持；禁用应落到具体控件 |
| focus | Panel 不可聚焦；内部控件遵循全局 focus token |

**键盘/ARIA**：默认 `<section>`；有 `title` 时自动生成 heading id 并设置 `aria-labelledby`，调用方可通过 `headingId` 覆盖。`as="aside"` 只有真正辅助内容可用。
**测试/截图**：`PixelPrimitives.test.tsx` 验证自动/受控标题关联；`/ui-preview` 的“生产阶段、输入与反馈、基础状态”覆盖；三尺寸截图均有。

### 3.2 `PixelButton`

**职责**：统一主、次、危险动作以及 loading/disabled/focus/pressed 反馈。

**稳定 API**

```ts
export type PixelButtonTone = "primary" | "secondary" | "danger";
export type PixelButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger"; // 默认 secondary
  size?: "default" | "compact";                  // 默认 default
  loading?: boolean;                              // 同时 disabled + aria-busy
};
```

`forwardRef<HTMLButtonElement>`；默认 `type="button"`。

**token 映射**：primary=`selection/inset`；secondary=`surface_subtle/ink` 中性灰；danger=`danger/inset`；2px `border`；focus 为 2px `focus`；无圆角；轻 1px 硬反馈。

**适用**：明确文字动作；一组最多一个 primary；危险/不可逆操作用 danger 且必须配确认。
**禁止**：用 primary 表示普通链接；同一视图多个同权 primary；仅图标动作（应另立并审核 `PixelIconButton`）；用 disabled 模拟装饰控件。

| 状态 | primary | secondary | danger | ARIA/行为 |
| --- | --- | --- | --- | --- |
| default | 40px 高，蓝底 | ≥34px，中性底 | 40px，高危险红 | 可点击 |
| loading | busy mark + 文本 | 同 | 同 | `aria-busy=true`、原生 disabled |
| empty/blocked/error | 非组件自身状态 | 普通次动作 | 恢复动作一律 primary；danger 仅不可逆操作 | 状态本身使用语义标记，不把恢复动作染红 |
| disabled | 中性灰、无阴影 | 同 | 同 | 原生 `disabled`；调用方必须提供可理解原因 |
| focus | 2px 黄色外框 | 同 | 同 | 原生 Tab 顺序 |
| pressed | 1px 位移+内凹 | 同 | 同 | 原生 active；toggle 应由调用方补 `aria-pressed` |

**测试/截图**：单测覆盖 loading、disabled、恢复回调；视觉门禁验证所有可见控件≥32、主/危险≥40及 focus outline；预览页覆盖 primary/secondary。

### 3.3 `PixelInput`

**职责**：始终可见标签、可读输入、hint/error 就近关联。

**稳定 API**

```ts
export type PixelInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "size"> & {
  label: string;
  hint?: string;
  error?: string;
};
```

`forwardRef<HTMLInputElement>`；自动用 `useId()` 生成 input/message id；`error` 优先于 `hint`。

**token 映射**：`inset` 背景、2px `border`、readable 14px、34px 最小高度；错误用 `danger` 边框/文字；focus 用 `focus`。

**适用**：单行文本、路径显示/输入、搜索字段；标签不得视觉隐藏。
**禁止**：多行正文（需另立 Textarea）；把 label 只放 placeholder；使用 error 色但不给错误文字；秘密值明文回显。

| 状态 | 契约 |
| --- | --- |
| default | label + input；可选 hint |
| loading | 不拥有 loading；组合区域应标 busy，输入视任务禁用或保持可编辑 |
| empty | 空值是正常 default；空态用 `PixelEmptyState`，不能靠 placeholder 解释整个状态 |
| blocked/error | `error` 设置 `aria-invalid=true`，消息通过 `aria-describedby` 关联 |
| disabled | 继承原生 disabled；调用方必须在邻近文本说明原因 |
| focus | 2px 黄色外框，不被父容器裁切 |

**测试/截图**：单测验证 accessible name、invalid、description；预览页“补充要求”；浏览器门禁验证尺寸/focus 基础规则。

### 3.4 `PixelStatus`

**职责**：短状态标签；同时使用文字、8px 形状和语义色，不作为可点击控件。

**稳定 API**

```ts
export type PixelStatusKind =
  | "idle" | "running" | "waiting_for_user"
  | "succeeded" | "failed" | "blocked";

export type PixelStatusProps = {
  kind: PixelStatusKind;
  label?: string;
  className?: string;
};
```

**token 映射**：idle=`muted` 方形；running=`selection` 方形；waiting=`warning` 菱形；succeeded=`success` 方形；failed=`danger` 方形；blocked=`danger` 双框方形；文字 readable 12px/1.35。

**适用**：任务状态、连接结果、等待人工；自定义 label 必须比默认状态更具体。
**禁止**：普通计数、装饰色点、无文字状态、整张卡片着色、作为按钮。

| 状态 | 默认文案 | 形状/色彩 |
| --- | --- | --- |
| idle | 待处理 | muted 方形 |
| running | 进行中 | selection 方形 |
| waiting_for_user | 等待确认 | warning 菱形 |
| succeeded | 已完成 | success 方形 |
| failed | 失败 | danger 方形 |
| blocked | 已阻断 | danger 双框方形 |
| disabled/focus | 不适用 | 非交互，不进 Tab |

**ARIA**：当前为普通 inline 文本，不自带 live region；动态重要状态必须由父级 `role=status/alert` 管理，避免嵌套重复播报。
**测试/截图**：参数化单测覆盖六态文案；预览页覆盖 waiting、idle、running、blocked、failed；Inspector 覆盖 waiting。

### 3.5 State 组件族

`PixelEmptyState`、`PixelErrorState`、`PixelLoadingState`、`PixelBlockedState` 共享内部 `PixelState`，但只导出语义明确的四个入口。

**公共 props**

```ts
export type PixelStateAction =
  | { kind: "button"; label: string; tone?: PixelButtonTone; onClick: () => void }
  | { kind: "link"; label: string; tone?: PixelButtonTone; href: string };

export type PixelStateProps = {
  title: string;
  description: string;
  action?: PixelStateAction;
  secondaryAction?: PixelStateAction;
  children?: ReactNode;
  className?: string;
};
```

`PixelLoadingStateProps` 同时移除 `action/secondaryAction`，禁止伪造“加载动作”。主 action 默认 primary，次 action 默认 secondary；danger 必须由调用方明确指定且只用于不可逆操作。

**token 映射**：`surface`、1px `border_soft`、左侧 4px 状态边；loading=`selection`，failed/blocked=`danger`；标题 pixel 15px，正文 readable 14px/1.6。

| 组件 | default 语义 | loading | empty | blocked | error | disabled | focus/ARIA |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `PixelEmptyState` | idle 状态标 + 原因 + 可选下一步 | 不适用 | 自身 | 不适用 | 不适用 | 动作可按业务禁用，但应避免无出口空态 | 容器无 role；动作进 Tab |
| `PixelLoadingState` | 不适用 | `role=status`；说明正在做什么/下次更新 | 不适用 | 不适用 | 不适用 | 不含 action | 父级可 `aria-busy`; 避免另一个 live region |
| `PixelBlockedState` | 不适用 | 不适用 | 不适用 | `role=alert`；依赖/影响/修复 | 不适用 | 修复动作不可无故禁用 | alert 播报；动作进 Tab |
| `PixelErrorState` | 不适用 | 不适用 | 不适用 | 不适用 | `role=alert`；原因/影响/恢复 | 恢复动作不可无故禁用 | alert 播报；动作进 Tab |

**适用**：一个状态解释、一个主要恢复/下一步、可选次级帮助。
**禁止**：把正常内容全塞进 State；吉祥物/大插画；多个同权动作；将技术堆栈放在 description；blocked 和 empty 混用。

**测试/截图**：单测覆盖 roles、button/link discriminated union、primary 恢复动作与回调；预览页“基础状态”在三尺寸覆盖四态。

## 4. `PixelScrollableRegion`

**职责**：为确有真实宽度的阶段轨道、时间线或宽表提供**局部**横向滚动、可见提示、键盘入口和滚动位置状态；不处理页面根溢出。

**稳定 API**

```ts
export type PixelScrollableRegionProps = {
  label: string;       // 必填 accessible name
  children: ReactNode;
  className?: string;
  hint?: string;       // 默认“可横向滚动查看更多”
  showControls?: boolean; // 默认 true；仅溢出时显示
};
```

内部状态：`static | start | middle | end`，输出到 `data-scroll-position`；viewport 输出 `role="region"`、`tabIndex=0`、`data-scroll-region="local-x"`。

**token 映射**：滚动承载区不另加装饰边框；提示 readable 12px `muted`；focus 使用全局 2px `focus`；间距 4px。

**适用**：不能合理重排的时间轴、阶段轨道、多列数据比较。
**禁止**：普通文本/表单；用它包裹整个页面；隐藏滚动条后没有提示；通过 `min-width` 把本可重排内容强制变宽；纵向滚动。

| 状态 | 契约 |
| --- | --- |
| default/static | 内容完整显示；不渲染多余提示/控件 |
| start | 提示和左右按钮出现；左按钮禁用 |
| middle | 提示和左右按钮可用 |
| end | 右按钮禁用；末项应完整可见 |
| loading/empty/blocked/error | 不拥有业务状态；对应内容应在区域外/内采用 State，但不能制造无意义横滚 |
| disabled | 不支持 |
| focus | viewport 可 Tab；浏览器原生方向键/Shift+滚轮；黄色 2px focus |

**ARIA/键盘**：`label` 必填并映射为命名 region；不能把每个小列表都变成 landmark。保留原生方向键/触控滚动，Home/End 到首尾；`ResizeObserver` 同时观察 viewport 与第一个内容根。溢出时按 `showControls` 渲染有名称的左右按钮，默认显示。

**测试/视觉门禁**：单测验证 role/name/tabindex、首尾状态、左右按钮和 Home/End；浏览器脚本验证溢出提示/按钮可见、键盘首尾可达，并断言最后项目完整进入 viewport。

## 5. `AppShell`

**职责**：唯一的生产工作站壳层，组合真实 chrome、≤8 生产导航、页面主滚动区、可选 Inspector 和 Command dock；不读取业务数据、不决定 onboarding/身份/路由状态。

**当前 API**

```ts
export type AppShellProps = {
  children: ReactNode;
  inspector?: ReactNode;
  inspectorTitle?: string; // 默认“当前任务详情”
  workspaceStatus?: RetroChromeWorkspaceStatus; // 默认未知
};
```

`children` 是主任务区；Inspector 仅接受与当前页面相关的动作、依据、影响、版本或阻断。当前标题由内部静态 `routeLabels` 推导。

**token/栅格映射**：canvas/surface/subtle 四层；1440 sidebar 208px、Inspector 320px；1180 sidebar 72px、Inspector 标准栏 300px；1024–1179 sidebar 72px、Inspector 为最大 360px overlay drawer；主区 `min-width:0`。顶部与 dock 固定在 grid 行中，不使用 fixed 覆盖。

**适用**：生产页和资源/系统页的稳定工作站 chrome。
**禁止**：welcome/onboarding/profile 未经阶段 B 契约直接套用；Inspector 展示全局健康长表；页面自己再造第二套主导航/dock；通过 children 操纵 shell class。

| 状态 | 1440/1180 | 1024 | 规则 |
| --- | --- | --- | --- |
| default，无 Inspector | 主区占剩余宽 | 72px 图标导航 + 主区 | 主区独立纵滚 |
| default，有 Inspector | 默认展开，320/300px | 默认关闭，按钮打开 drawer | 1024 打开前后主区宽度不变 |
| loading/empty/blocked/error | 壳层稳定，children 呈现状态 | 同 | dock/导航不覆盖唯一恢复动作 |
| disabled | Shell 不支持 disabled | 同 | 不得整页“灰掉” |
| focus | 导航→topbar→主区→dock，黄色 focus | drawer 不 trap；Escape/关闭/backdrop 后焦点回触发器 | 非模态 complementary 契约 |

**滚动责任**：sidebar=`sidebar-y`；主区=`main-y`；Inspector=`inspector-y`；Command details=`command-details-y`；局部横滚必须由 `PixelScrollableRegion` 声明。壳层根和 grid 容器只负责裁定区域，不承载隐藏的可滚内容。

**ARIA/键盘**：生产导航 `aria-label`，当前入口 `aria-current=page`；Inspector 是命名 `aside/complementary`，不是 modal/dialog，不设 `aria-modal` 且不 focus trap；开关有 `aria-controls/expanded`；1024 Escape、关闭按钮或 backdrop 均关闭，焦点返回触发器。

**测试/截图**：浏览器脚本验证 8 项导航、无 root overflow、dock 不覆盖、1024 drawer 不缩主区/关闭不留 backdrop；`/ui-preview` 三尺寸；真实 `/topics` 三尺寸只作共享壳层集成证据。

## 6. `RetroChrome`

**职责**：提供稳定、真实可操作的标题栏、应用菜单和工作区工具栏；Windows 语法体现在层级、选择和按下反馈，不模拟无功能系统控件。

**稳定 API**

```ts
export type RetroChromeWorkspaceStatus = {
  kind: "unknown" | "connected" | "attention" | "failed";
  label: string;
};
export type RetroChromeProps = {
  todayLabel: string;
  contextLabel: string;
  workspaceStatus?: RetroChromeWorkspaceStatus;
};
```

仅由 `AppShell` 调用。菜单固定为创作、资源、知识、运行、设置、帮助；工具栏提供 `router.back()` 与工作台链接。

**token 映射**：titlebar=`selection/inset`；菜单=`surface`；toolbar=`surface_subtle`；位置=`inset`；真实按钮 34px 高、最小 40px 宽；焦点黄。

**适用**：工作站全局壳层。
**禁止**：业务页直接引用；localhost/API/path；假的最小化/最大化/关闭；装饰性 disabled；页面私有菜单。

| 状态 | 契约 |
| --- | --- |
| default | 所有链接/按钮可操作；未提供 status 时只显示“工作区状态未知” |
| loading/empty/blocked/error | Chrome 不变成状态卡；仅显示 owner 传入的受控状态与人话 label |
| disabled | Chrome 内禁止装饰性 disabled；真实不可用动作应隐藏或提供原因 |
| focus | 所有菜单、返回、工作台、帮助可 Tab，2px focus |

**ARIA/键盘**：菜单是命名 nav；工具栏当前为 `div aria-label`，不是 `role=toolbar`；返回按钮有文本；图标为补充。主线程需决定是否采用 `role=toolbar` 及方向键 roving tabindex。
**测试/截图**：单测验证默认未知、受控 connected、无静态假报、无 localhost/127.0.0.1、无 disabled、返回/帮助可执行；三尺寸 preview/topics 截图。

## 7. `VoiceCommandConsole`

**职责**：常驻低频 Command dock，执行“输入/录音 → 转写 → 预览 → 确认执行”；详情可折叠且不覆盖主任务。它不是聊天区。

**稳定 API**

```ts
export type VoiceCommandConsoleProps = {
  contextLabel?: string; // 默认“全局工作区”
};

export type VoiceCommandViewState =
  | "idle" | "recording" | "transcribing" | "previewing" | "executing"
  | "waiting_for_user" | "blocked" | "failed" | "succeeded";

export type VoiceCommandConsoleViewProps = { /* 受控值、preview 与事件回调 */ };
```

`VoiceCommandConsole` 是 Controller，继续使用既有 API/hook且未改数据契约；`VoiceCommandConsoleView` 是不导入 API 的纯受控 View。

**token/尺寸映射**：dock=`surface_subtle`；输入=`inset`、40px；mic 32×32px；预览主动作 40px；详情最大高 180px且自身纵滚；状态复用六态 token。

**适用**：全局低频语音/文本生产指令，必须先预览。
**禁止**：页面主表单、聊天历史、未经预览直接执行、高风险动作无影响/范围、默认展开占据第二工作区。

| 状态 | 状态标/界面 | 动作契约 |
| --- | --- | --- |
| default/idle | 等待输入；详情收起 | `预览指令` 为唯一主动作；展开为次动作 |
| recording | running + 计时；mic `aria-pressed=true` | 点击停止；预览禁用 |
| loading/transcribing | running 文案 | mic/预览按阶段禁用；不显示假进度 |
| waiting_for_user | waiting；预览结果展开 | 仅此时出现 `确认并执行` |
| succeeded | succeeded 文案；如有 next_url 跳转 | 结果应可见；回滚契约尚未定义 |
| blocked | blocked 文案 | 详情显示 API 返回摘要；具体修复入口尚依赖业务响应 |
| error | failed 文案 | 当前错误显示在详情；恢复路径主要为修正/重试预览 |
| disabled | 录音/处理期间按真实原因禁用 | 禁止装饰性 disabled |
| focus | 输入 Enter=预览；按钮原生 Tab；展开按钮有 expanded | 详情无 focus trap，因其为内联展开区 |

**ARIA/键盘**：section 有 `aria-label=语音指令控制台`；输入有 accessible name；mic 有动态 name 和 pressed；详情开关有 controls/expanded；Enter 只触发预览；状态容器以单一 `role=status aria-live=polite` 节制播报。

**测试/截图**：View 单测覆盖录音、预览、waiting、blocked、failed、succeeded、Enter 与受控输入；视觉脚本验证展开/收起、dock 不覆盖；preview 三尺寸默认态。

## 8. `RouteError`

**职责**：Next route error boundary 的安全适配器；过滤堆栈、路径和秘密，映射到统一 `PixelErrorState`，提供重新加载、返回工作台和可折叠技术详情。

**公开 API（已导出）**

```ts
export type RouteErrorProps = {
  error: Error & { digest?: string };
  reset: () => void;
  title?: string;
  errorCategory?: "generic" | "network" | "permission" | "configuration" | "dependency";
  domainHint?: string;
  hint?: string; // deprecated compatibility alias
  detail?: string;
};

export function sanitizeMessage(raw?: string): string | null;
```

**token 映射**：复用 failed state；宽度最大 720px；正文 readable 14px；技术详情可换行；无成功图标。

**适用**：route-level `error.tsx`，以及无法由局部表单恢复的页面错误。
**禁止**：表单字段错误、普通 empty、blocked 依赖；直接显示 stack/path/key；用 toast 取代恢复面板。

| 状态 | 契约 |
| --- | --- |
| default/error | `role=alert`，标题、用户提示、重新加载、返回工作台 |
| technical detail | 默认折叠；过滤危险内容；可显示 digest |
| loading/empty/blocked | 不适用；使用对应 State |
| disabled | 恢复按钮不得无解释禁用 |
| focus | summary、恢复、返回均可 Tab；焦点黄 |

默认 `errorCategory="generic"`，不假设 API、网络或本机依赖故障；`domainHint` 优先于 category 默认文案和兼容 `hint`。恢复动作固定为 primary。

**测试/截图**：单测覆盖敏感信息过滤、generic/category/domain hint、截断、标题/详情/digest、primary reset、返回链接、无成功勾；`/ui-preview` 显示基础 Error。

## 9. `Skeleton` 与 `LoadingShell`

### 9.1 公开 API

```ts
export type SkeletonProps = {
  width?: string | number;
  height?: string | number;
  variant?: "block" | "text" | "circle";
  className?: string;
  style?: CSSProperties;
  ariaLabel?: string;
};

export type LoadingShellProps = {
  label?: string; // 默认“加载中...”
  children?: ReactNode;
};

/** @deprecated */ MetricSkeletonRow({ count = 4 })
ListSkeleton({ count = 4, itemClassName? })
/** @deprecated */ SidebarSkeletonCards({ count = 3 })
```

所有稳定 Props 类型已导出。两个旧 card-wall preset 只为现有业务 loading 页保留兼容；新页面在 TypeScript/IDE 中会收到 deprecated 提示。

**职责**：Skeleton 只保留最终布局几何；`LoadingShell` 提供 running 状态的对象说明和 `aria-busy/live` 容器。

**token 映射**：skeleton=`surface_subtle`，无硬阴影/装饰动画；loading 状态复用 `PixelLoadingState`。

**适用**：已知最终结构的短暂加载；列表、文本、预览框的结构占位。
**禁止**：用 skeleton 伪造进度；长时间加载无对象说明；四张 metric skeleton 预设被当作所有页面标准；圆形 skeleton 暗示不存在的头像/指标。

| 状态 | 契约 |
| --- | --- |
| default Skeleton | 默认 `aria-hidden=true`，不产生屏幕阅读器噪声 |
| labelled Skeleton | `role=status` + aria-label；只在它本身就是唯一加载反馈时使用 |
| LoadingShell | `aria-busy=true`、`aria-live=polite`，显示具体 label 与更新说明 |
| empty/blocked/error | 不适用，切换到对应 State，不同时显示 skeleton |
| disabled/focus | 不适用，非交互 |

**测试/截图**：Skeleton 测试覆盖 variant、尺寸、aria-hidden/label、LoadingShell label/children和组合数量；`/ui-preview` 覆盖 `PixelLoadingState`。新页面使用与最终布局一致的结构 fixture，不再采用 deprecated card presets。

## 10. 总状态覆盖矩阵

`✓` 已实现且有至少单元或视觉证据；`P` 表示状态由父组件组合而非组件自身持有；`—` 不适用。

| 组件 | default | loading | empty | blocked | error | disabled | focus | 当前证据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PixelPanel | ✓ | 组合 | 组合 | 组合 | 组合 | — | 内部控件 | unit + preview 3尺寸 |
| PixelButton | ✓ | ✓ | — | 作为动作 | 作为动作 | ✓ | ✓ | unit + browser gate |
| PixelInput | ✓ | 由父级组合 | 值为空 | ✓ error API | ✓ error API | 原生 | ✓ | unit + preview |
| PixelStatus | 六态 | ✓ | idle | ✓ | ✓ | — | — | 参数化 unit + preview |
| PixelEmptyState | — | — | ✓ | — | — | 具体动作控制 | ✓（动作） | unit + preview |
| PixelLoadingState | — | ✓ | — | — | — | — | — | unit + preview |
| PixelBlockedState | — | — | — | ✓ | — | 具体动作控制 | ✓（动作） | unit + preview |
| PixelErrorState | — | — | — | — | ✓ | 具体动作控制 | ✓（动作） | unit + preview |
| ScrollableRegion | ✓ | 组合 | 组合 | 组合 | 组合 | — | ✓ | unit + 首末项视觉断言 |
| AppShell | ✓ | 稳定壳层 | 稳定壳层 | 稳定壳层 | 稳定壳层 | — | ✓（drawer） | preview/topics 3尺寸 + Esc 焦点归还 |
| RetroChrome | ✓ | 受控状态 | — | 受控状态 | 受控状态 | 禁止装饰禁用 | ✓ | unit + 3尺寸 |
| VoiceCommandConsole | ✓ | ✓ | 输入为空 | ✓ | ✓ | ✓ | ✓ | View 六态 unit + 视觉开合 |
| RouteError | — | — | — | — | ✓ | — | ✓ | unit + preview error |
| Skeleton/LoadingShell | 结构占位 | ✓ | — | — | — | — | — | 15 unit；部分视觉 |

## 11. 截图与测试索引

### 11.1 组件预览路由

- 路由：`/ui-preview`
- [1440×900](../output/playwright/phase-a-preview-1440x900.png)
- [1180×800](../output/playwright/phase-a-preview-1180x800.png)
- [1024×768](../output/playwright/phase-a-preview-1024x768.png)

覆盖：chrome、8 项生产导航、主任务、Inspector、Command dock 默认/开合、Panel、Button、Input、Status、Empty/Loading/Blocked/Error、ScrollableRegion。

### 11.2 真实共享壳层集成证据

- 路由：`/topics`（只验证共享壳层集成，不代表该业务页完成页面重构）
- [1440×900](../output/playwright/phase-a-topics-1440x900.png)
- [1180×800](../output/playwright/phase-a-topics-1180x800.png)
- [1024×768](../output/playwright/phase-a-topics-1024x768.png)

### 11.3 自动门禁

`phase-a-browser-check.js` 当前验证：

- 1440×900、1180×800、1024×768；
- html/body 无根横向溢出；
- 已声明滚动区域无不可达 `hidden/clip`；
- 可见交互目标≥32px，主/危险动作≥40px；
- focus outline 可见且≥2px；
- 阶段轨道末项滚动后完整可见；
- 溢出时提示/左右按钮可见，Home/End 可达首尾；
- dock 展开/收起；
- dock 不覆盖主区；
- chrome 无 disabled；
- 1024 Inspector 非模态、不改变主区宽度，Esc 关闭后不残留 backdrop 且焦点回触发器。

最终测试数量与截图生成时间以 PR 验证结果为准；组件套件包含 `PixelPrimitives`、`RetroChrome`、`RouteError`、`Skeleton` 与 `VoiceCommandConsoleView`。

## 12. 旧组件弃用与迁移清单

以下只定义方向，本轮不删除、不改业务调用。

| 旧组件/模式 | 决策 | 迁移目标 | 原因与限制 |
| --- | --- | --- | --- |
| `MetricCard` | **计划弃用** | 决策相关数字改为普通摘要；确需指标时另审 DataSummary | DESIGN.md 限制无 action 指标，现有四卡墙制造同权噪声 |
| `PixelAgentPanel` | **计划弃用为全局面板** | 页面相关内容迁到 `AppShell.inspector`；全局健康进 `/health` | 现有长状态表重复工作流并抢主任务 |
| `PixelBlockMeter` | **限制使用/迁移** | 真实长任务迁未来 `PixelProgressBlocks`；静态计数改状态文字 | 现有像素块常用于未知/静态状态，伪造精度 |
| `EpisodeProgress` | **计划迁移** | 基于 `PixelScrollableRegion` 的 `WorkflowRail` | 当前页面专用阶段条缺共享可达 API；本轮保持 HEAD 未修改 |
| `WorkflowProgress` | **计划弃用** | 生产导航的当前态 + 单一 WorkflowRail | 与导航、EpisodeProgress 重复工作流 |
| `FormErrorBanner` | **计划迁移** | 局部表单错误用 `PixelErrorState` 的轻量 variant 或 Field error | 当前与 RouteError/Toast 状态体系分裂；轻量 variant 尚未批准 |
| raw `.pixel-card` | **逐步弃用** | `PixelPanel` 或语义 State | 页面可随意嵌套、投影和着色，无法守住两层边界 |
| raw `.pixel-button` | **逐步弃用** | `PixelButton`；链接动作需审核 `PixelAction`/Link API | class 无类型约束，无法保证 loading/disabled/focus/尺寸 |
| `Skeleton` 的 `MetricSkeletonRow` | **已标记 deprecated** | 与目标页面几何一致的结构 skeleton | 预设四指标强化卡墙；仅保留旧页兼容 |
| `SidebarSkeletonCards` | **已标记 deprecated** | Inspector/列表的具体 skeleton fixture | 通用“卡片”形状不保证最终布局稳定；仅保留旧页兼容 |
| `PixelTooltip` | **保留但收口** | 未来 `PixelIconButton` 必须组合 tooltip | 不应包裹禁用装饰控件；需验证键盘/aria-describedby |
| `PixelMascot` | **限制保留** | 只用于一个 Agent 状态或空态 | 每屏最多一个，不作为卡片装饰 |
| `PixelIcon` | **保留并后续规范化** | 同一 14/16/20px 网格 | 当前 CSS sprite 细节仍需图标资产审计 |

## 13. 已批准决策落实表

| # | 决策 | 实现证据 |
| --- | --- | --- |
| 1 | secondary 中性灰；恢复 primary；danger 仅不可逆 | CSS + State action 单测 |
| 2 | State action 为 button/link discriminated union | `PixelStateAction`，不再接受任意 `ReactNode` |
| 3 | Panel 自动关联 heading | 自动 id/`aria-labelledby` + 覆盖 id 单测 |
| 4 | 横滚按钮、ResizeObserver、Home/End | primitive unit + browser reachability gate |
| 5 | 1024 非模态 complementary，Esc + 焦点归还 | AppShell + browser gate |
| 6 | Chrome 受控 workspaceStatus，默认未知 | RetroChrome unit + style contract |
| 7 | Command View/Controller 与六态 View 测试 | `VoiceCommandConsoleView` + unit |
| 8 | RouteError category/domainHint，generic 默认 | RouteError unit |
| 9 | 两个 skeleton card preset 立即 deprecated | JSDoc `@deprecated`，旧页兼容保留 |
| 10 | 收口后导出稳定 Props | primitives/shell/chrome/command/error/skeleton type exports |

## 14. 阶段 A 退出边界

- 本提交只交付共享设计系统、壳层、验收夹具、测试与文档。
- 不修改 `app/page`、welcome/onboarding/profile/settings、API、server action 或业务数据契约。
- `/ui-preview` 是组件视觉 fixture，不进入生产导航。
- 主线程完成三尺寸视觉审查前，不在业务页扩散新 primitives、Inspector/status props 或 Command View。
