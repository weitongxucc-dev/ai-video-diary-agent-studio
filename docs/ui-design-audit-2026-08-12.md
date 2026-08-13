# AI 视频日记 UI 设计审计与阶段化重构计划

> 状态：Gate 0 已由主线程批准；本文保留为阶段 A 的历史审计基线
> 日期：2026-08-12
> 唯一设计基线：仓库根目录 [`DESIGN.md`](../DESIGN.md)（approved-baseline v1.0）
> 本文记录实施前现状；阶段 A 实现结果与稳定 API 以[组件交付契约](phase-a-component-delivery-contract.md)为准。

## 1. 审计方法与结论

在当前 UI 工作树上启动独立 Web 实例（端口 3100），用真实 Chromium 逐页获取 18 个路由的三尺寸视口截图：1440×900、1180×800、1024×768，共 54 张。1024×768 同时作为紧凑桌面与常见高系统缩放后的逻辑视口门禁；真实 Electron 的 125%/150% 系统缩放仍须在阶段 E 复验，不能以浏览器截图冒充。

自动审计逐页记录：根级横向溢出、被 `hidden/clip` 的内部横向内容、低于 12px 的有效文本、小于 32px 的可见交互目标、禁用控件和页面状态。人工审计覆盖：信息架构、任务优先级、视觉层级、色彩与像素语言、字体与中英文混排、栅格与留白、对齐与最小尺寸、裁切与重叠、导航/控制冗余、状态反馈、键盘与恢复路径。

结论：**当前 UI 不满足 DESIGN.md 的 P0 合并门禁，不应提交第一阶段 WIP。** 这不是单点溢出问题，而是共享 chrome、导航、密度、字体、状态模型和紧凑桌面降级策略尚未形成统一体系。

关键量化证据：

- 54/54 个页面视口的根节点满足 `scrollWidth === clientWidth`，但这不能判定通过。
- 首页在 1440/1180/1024 分别检测到 7/6/3 个存在横向内容且采用 `hidden/clip` 的内部节点；阶段轨道只是其中最显眼的一处。
- 共享壳层页面每个视口普遍有约 20–24 个小于 32px 的可见交互目标；壳层同时常驻 14 个禁用/假控件。
- 首页三尺寸分别有 60/61/61 个小于 12px 的有效文本节点；剪辑计划为 46/46/46；多数共享壳层页为 13–37。
- `/onboarding`、`/settings` 可稳定进入错误页；`/video` 在加载骨架与错误态之间不稳定；`/profile` 在 1024 出现共享视觉样式缺失降级。

运行日志进一步定位到三个只读事实：`/onboarding` 在 `src/lib/onboarding.ts:148` 对缺失的 `setup.items` 调用 `.find`；`/video` 在 `src/app/video/page.tsx:260` 对缺失的 `chain.steps` 读取 `.length`；`/settings` 将 `onChange` 事件处理器跨 Server/Client Component 边界传给目录输入。这些属于 frontend/业务线程的修复范围，本设计线程不改其逻辑；设计侧只负责把 failed/blocked 状态做成可信、可恢复的呈现。

## 2. P0 合并阻断项

以下每项均对应 DESIGN.md 第 8 节，任一未关闭都不得合并。

| ID | 严重级别 | 截图／组件证据 | 发现与根因 | 设计决策 | 重构归属 | 验收方式 |
| --- | --- | --- | --- | --- | --- | --- |
| P0-01 | Blocker | [首页 1440](../output/playwright/audit-home-1440x900.png)、[1180](../output/playwright/audit-home-1180x800.png)、[1024](../output/playwright/audit-home-1024x768.png)；`AppShell`、首页 `workbench-grid`、`PixelAgentPanel` | 首页同时突出阶段轨道、今日任务、队列、四指标、当前选题和 Agent 全量状态，十秒内无法稳定识别唯一主任务。根因是用面板和指标代替任务优先级。 | 首屏只保留 Episode 上下文、当前阶段、一个待决策对象和一个主动作；Agent 依据进入按需 Inspector；次级概览最多 2–3 项。 | 阶段 A 壳层 + 阶段 C 首页 | 三尺寸首次观看 10 秒测试能一致回答“对象/Agent/下一步”；首屏仅一个主按钮、一个高注意力状态色。 |
| P0-02 | Blocker | [首页 1180](../output/playwright/audit-home-1180x800.png)、[首页 1024](../output/playwright/audit-home-1024x768.png)、[阶段起点](../output/playwright/ui-phase2-workflow-1024x768-start.png)、[阶段终点](../output/playwright/ui-phase2-workflow-1024x768-end.png)；`EpisodeProgress`、`.main-workspace-scroll` | 根节点无横滚，但内部阶段和其他节点仍采用隐藏/裁切；当前局部滚动修补没有形成所有关键子区域的可达契约。 | 1024 明确降级为“当前阶段 + 可键盘滚动缩略轨道 + 左右提示”；所有局部横滚组件采用统一 `ScrollableRegion`。 | 阶段 A 栅格/滚动组件 | 根和 body 无横滚；枚举所有 `scrollWidth > clientWidth` 节点；不允许 `hidden/clip`；首项、当前项、末项可滚动至完整可见。 |
| P0-03 | Blocker | 所有共享壳层截图；[首页 1440](../output/playwright/audit-home-1440x900.png)；`RetroChrome.tsx:11-61` | 正式 chrome 暴露 `127.0.0.1:3000`，并保留返回/前进/主页、菜单、窗口按钮等 14 个禁用假控件。Windows 被当作黑框戏服，而非交互语法。 | 标题栏、菜单、工具栏只保留真实动作；位置显示 Episode/页面语义，不显示 localhost；无功能装饰直接删除。 | 阶段 A `RetroChrome`/`AppShell` | 三尺寸 chrome 不含 localhost/内部路由；每个可见控件可操作或有明确禁用原因；无“装饰性 disabled”。 |
| P0-04 | Blocker | 所有共享壳层截图；`AppShell.tsx:21` 的 15 项导航、`WorkflowProgress` | 生产步骤、资源、系统、法律和设置同权塞入窄侧栏，并与阶段轨道/队列重复；1024 仍硬塞长导航。 | 左侧最多 8 个生产入口；资源/设置/日志进入菜单或资源中心；紧凑桌面改为图标+tooltip或横向流程导航；入口唯一。 | 阶段 A 导航 IA | 三尺寸首屏导航≤8生产项；同一工作流不在三个位置并列重复；键盘顺序与视觉顺序一致。 |
| P0-05 | Blocker | 自动审计：首页 60/61/61、剪辑计划 46/46/46 个 `<12px` 文本；[健康 1024](../output/playwright/audit-health-1024x768.png)、[知识库 1024](../output/playwright/audit-knowledge-1024x768.png)；`globals.css` | 9–11px 有效正文与点阵字体承担路径、原因、表格内容和长中文；中英文/数字混排字宽和行高不稳定。 | 正文/说明/错误/路径 14px、≥1.55；次要说明 12–13px；点阵字体只用于标题、短标签、按钮和关键数字；任何有效正文不得小于 12px。 | 阶段 A 字体/token | DOM 扫描无有效正文 `<12px`；字体加载成功和失败回退各截图；中文、英文、数字、路径不重叠且长文本可查看。 |
| P0-06 | Blocker | [首页 1440](../output/playwright/audit-home-1440x900.png)、[模板 1024](../output/playwright/audit-templates-1024x768.png)、[学习 1024](../output/playwright/audit-learning-1024x768.png)；`.pixel-card` 及页面嵌套面板 | 大量 2px 黑框、硬阴影、凹凸和卡片套卡片让所有区域同权，像素语言制造噪声。 | 只保留 canvas/surface/subtle/inset 四层；窗口、一级工作区、输入和按钮才使用明确边界；内部优先用分隔线和 12–16px 留白。 | 阶段 A token/Panel + 阶段 C/D 页面 | 首屏容器最多两层；静止内部组无默认硬阴影；灰阶截图仍能分辨主区、辅助区和操作区。 |
| P0-07 | Blocker | [首页 1024](../output/playwright/audit-home-1024x768.png)、[剪辑计划 1024](../output/playwright/audit-edit-plan-1024x768.png)、[模板 1024](../output/playwright/audit-templates-1024x768.png)；`VoiceCommandConsole` | 命令 dock 常驻显示输入、转写、解析、状态和四个按钮，在 768 高度挤压/遮挡页面下部任务；“执行”与页面主动作竞争。 | 默认只保留输入、解析摘要/风险和一个主操作；详细转写、历史、解析进入展开区；dock 与内容拥有独立、明确的空间责任。 | 阶段 A `PixelCommandConsole`/shell grid | 三尺寸 dock 不覆盖内容/焦点；1024 默认高度满足基线；页面主动作与 dock 动作不冲突；展开/收起键盘可用。 |
| P0-08 | Blocker | [Onboarding 错误 1024](../output/playwright/audit-onboarding-1024x768.png)、[Settings 错误 1024](../output/playwright/audit-settings-1024x768.png)、[Video 错误 1440](../output/playwright/ui-baseline-1440x900.png)；`RouteError.tsx:104` | 三个核心流程无法完成；错误卡右上角显示绿色成功勾，正文暴露 JS 异常，恢复动作只给笼统“重试”。根因是业务异常直接落入通用视觉模板，模板本身语义错误。 | 统一 failed/blocked：人话原因、影响范围、具体修复动作、按需技术详情；图标/文字/色彩一致，绝不出现成功勾。 | 阶段 A 状态组件；业务根因由 frontend 线程 | 三尺寸可识别 failed/blocked；无成功图标；键盘可到达修复；错误详情可展开/复制；onboarding/settings/video 核心流程可恢复。 |
| P0-09 | Blocker | [Video 加载 1024](../output/playwright/audit-video-1024x768.png)、[Topics 空态 1024](../output/playwright/audit-topics-1024x768.png)、[Script 空态 1024](../output/playwright/audit-script-1024x768.png) | 加载骨架无对象/进度/超时说明；选题页同屏出现两个“生成今日选题”；关键页缺少默认、空、加载、错误、进行中、待确认的统一状态契约。 | 采用 idle/running/waiting/succeeded/failed/blocked；空态一句原因+一个动作；running 不用假进度；waiting 把具体决定提升为主任务。 | 阶段 A State primitives + 各页面阶段 | 每页按适用状态截图；同一状态只有一个主动作；加载超过 2 秒有说明；失败/阻断均可恢复。 |
| P0-10 | Blocker | [History 1440](../output/playwright/audit-history-1440x900.png)、[1180](../output/playwright/audit-history-1180x800.png)、[1024](../output/playwright/audit-history-1024x768.png)；[Edit plan 1180](../output/playwright/audit-edit-plan-1180x800.png) | 1180/1024 仍将指标栏或 Inspector 留在右侧挤压中央任务；历史页 1024 主列表约半宽，剪辑页 1180 预览宽度不足。根因是断点只缩尺寸，未改变信息结构。 | 1024–1179 默认 Inspector 抽屉，不占主区宽；中央核心任务至少保持可操作宽度；指标改为摘要或详情。 | 阶段 A shell/grid + 阶段 C/D 页面 | 1024、1180、1440 都能完成核心动作；1024 Inspector 默认关闭且可键盘打开；中央无低于页面契约的最小宽度。 |
| P0-11 | Blocker | [Profile 1440](../output/playwright/audit-profile-1440x900.png)、[1180](../output/playwright/audit-profile-1180x800.png)、[1024](../output/playwright/audit-profile-1024x768.png)；`profile/page.tsx:192,208,237` | Profile 绕过 `AppShell`，1180 出现仅标题加载态，1024 呈现近似未加载样式的原生控件，产品身份和操作反馈断裂。 | 画像作为资源/身份页面进入受控壳层；所有加载/默认/保存态使用共享基础组件，样式失败时仍保留可读结构。 | 阶段 B 身份 + 阶段 D 资源 | 三尺寸视觉系统一致；加载、空、编辑、保存中、保存失败可复现；无未样式化原生布局；核心动作≥40px。 |
| P0-12 | Blocker | 自动审计：共享壳层页约 20–24 个 `<32px` 可见交互目标；`RetroChrome`、`AppShell`、页面工具按钮 | 菜单、窗口、工具、图标和紧凑按钮普遍低于设计最小目标；禁用状态缺少原因，焦点契约未被截图验证。 | 主/危险动作≥40px；密集工具≥32px并带 tooltip；焦点统一为 focus 黄；禁用必须有可理解原因。 | 阶段 A Button/IconButton/Focus/Tooltip | 程序扫描目标矩形；Tab 全链路录屏/截图；焦点不被裁切；禁用原因可读；图标按钮有 accessible name。 |
| P0-13 | Blocker | [Health 1024](../output/playwright/audit-health-1024x768.png)、[Draft 1024](../output/playwright/audit-draft-1024x768.png)、[Knowledge 1024](../output/playwright/audit-knowledge-1024x768.png) | 宽表格、四列步骤卡和长混排文本在 1024 仅靠压缩列宽/字号维持，未提供定义列表、详情抽屉或局部表格滚动契约。 | 紧凑桌面按内容类型降级：表格→定义列表/受控局部滚动，步骤→当前步骤+摘要，长内容→可读正文列。 | 阶段 C/D 页面模式 | 1024/1180 的关键字段、动作和末列完整可达；无缩小正文；局部滚动有提示、键盘和首末项断言。 |

## 3. 分组 P1

P1 不单独阻断当前设计审核，但进入对应页面重构前必须关闭；若影响核心操作则升级 P0。

| ID | 分组／级别 | 截图／组件证据 | 发现与根因 | 设计决策 | 重构归属 | 验收方式 |
| --- | --- | --- | --- | --- | --- | --- |
| P1-IA-01 | 信息架构 / Major | [Privacy 1024](../output/playwright/audit-privacy-1024x768.png)、[Terms 1024](../output/playwright/audit-terms-1024x768.png) | 法律阅读页仍显示完整生产导航和命令 dock，顶部上下文还写“今日工作台”。 | 法律页使用共享阅读壳或最小产品壳；返回/帮助可达，移除生产命令。 | 阶段 D | 页面标题、导航上下文、命令区与当前任务一致。 |
| P1-IA-02 | 信息架构 / Major | [Templates 1024](../output/playwright/audit-templates-1024x768.png)、[Sources 1024](../output/playwright/audit-sources-1024x768.png) | 资源页仍作为生产步骤同权出现，且页面内再提供“返回工作台”。 | 模板/信息源/知识库归资源中心；上下文返回不是第二套主导航。 | 阶段 D | 全局 IA 卡片排序测试；入口唯一。 |
| P1-VIS-01 | 视觉 / Major | [Learning 1024](../output/playwright/audit-learning-1024x768.png)、[History 1440](../output/playwright/audit-history-1440x900.png) | 红/绿/橙/蓝顶部线、进度方块和图标同时表达静态数量，状态色被装饰化。 | 只有改变决策的状态使用语义色；静态数量使用墨色/系统灰。 | 阶段 C/D | 去色后层级仍成立；同一数据只使用一次状态编码。 |
| P1-VIS-02 | 像素语言 / Major | 全部共享壳层；`PixelIcon`、各类 sprite | 图标比例、细节和色块权重不一，重复装饰降低扫描速度。 | 收敛到统一 14/16/20px 网格；默认单色，文字为主。 | 阶段 A | 图标清单视觉回归；无 emoji/外部贴图混用。 |
| P1-TYPE-01 | 排版 / Major | [Knowledge 1024](../output/playwright/audit-knowledge-1024x768.png)、[Draft 1024](../output/playwright/audit-draft-1024x768.png) | 中英文、数字、路径与技术词连续混排，行长过长，层级靠字体切换而非结构。 | 技术详情按需展开；正文列控制行长；中英文间距和路径折行采用统一规则。 | 阶段 C/D | 真实最长文本三尺寸截图；路径可复制/完整查看。 |
| P1-LYT-01 | 栅格 / Major | [Welcome 1024](../output/playwright/audit-welcome-1024x768.png) | 首启采用独立宽画布与四列步骤，和工作站壳层缺少共同网格；1024 留白和底部动作分配不均。 | 使用 Setup shell，共享 token/控件但保持单任务向导；每屏一个决定。 | 阶段 B | 三尺寸步骤、内容、动作对齐；无需横滚。 |
| P1-LYT-02 | 留白 / Major | [Sources 1024](../output/playwright/audit-sources-1024x768.png)、[Script 1024](../output/playwright/audit-script-1024x768.png) | 空白区域和内容区并非有意的阅读留白，而是固定网格未被任务使用。 | 空态收敛为有明确原因/动作的窄阅读区；可选 Inspector 不占空列。 | 阶段 C/D | 主内容宽度和视觉中心符合页面契约。 |
| P1-INT-01 | 交互 / Major | [Topics 1024](../output/playwright/audit-topics-1024x768.png) | 页面顶部与空态同时提供相同“生成今日选题”主动作。 | 每视图一个主操作；顶栏仅在存在内容时承载批量/新增。 | 阶段 C | 主动作计数断言为 1。 |
| P1-INT-02 | 反馈 / Major | [Edit plan 1440](../output/playwright/audit-edit-plan-1440x900.png) | 多个禁用步骤/控制只显示灰态，未说明阻断依赖或修复入口。 | blocked 状态明确依赖、影响和跳转修复。 | 阶段 C | 禁用原因可见/可聚焦；修复链接可达。 |
| P1-STATE-01 | 加载 / Major | [Video 1024](../output/playwright/audit-video-1024x768.png) | 骨架持续占位但不说明扫描对象、预计更新或取消/重试。 | running 状态描述具体动作和下一次更新，不使用假进度。 | 阶段 C | 2 秒后显示动作说明；超时转 blocked/failed。 |
| P1-STATE-02 | 空态 / Major | [History 1024](../output/playwright/audit-history-1024x768.png)、[Sources 1024](../output/playwright/audit-sources-1024x768.png) | “API 未连接”“尚未拉取”与真正空数据混在同一空态，用户不知道是无内容还是依赖失败。 | empty、blocked、failed 分离；依赖问题进入 blocked 并给修复动作。 | 阶段 D | 状态 fixture 对照截图，文案能说明原因/影响/动作。 |
| P1-UE-01 | 高缩放 / Major | 当前仅有三逻辑视口截图；尚无打包 Electron 125%/150% 系统缩放证据 | 浏览器逻辑视口能发现紧凑布局，但不能验证原生 chrome、DPI、字体栅格和 Electron 菜单。 | 阶段 E 在 Windows/Electron 做 100/125/150% 缩放矩阵；低于 1024 只保留查看、继续、确认、修复。 | 阶段 E | 每比例关键流程截图+键盘录像；字体不糊、主动作不隐藏、无重叠。 |

## 4. 逐页 × 三视口审计矩阵

缩写：`IA` 信息架构/任务优先级；`V/T` 视觉、色彩、像素语言与排版；`L` 栅格、留白、对齐、裁切/重叠；`I/S` 交互、反馈、禁用/加载/空/错态、键盘。每行均以当前真实截图为证据。

| 页面 | IA、V/T、I/S 结论 | 1440×900 | 1180×800 | 1024×768／高缩放逻辑视口 | 级别、根因、归属与验收 |
| --- | --- | --- | --- | --- | --- |
| `/` | IA：六组同权；V/T：边框/指标/小字过载；I/S：阶段、页面动作与 dock 竞争。 | [证据](../output/playwright/audit-home-1440x900.png)：三栏+四指标+全 Agent 表，主任务不唯一。 | [证据](../output/playwright/audit-home-1180x800.png)：Inspector 仍占列，阶段只见前 6 项。 | [证据](../output/playwright/audit-home-1024x768.png)：Inspector 消失但阶段末项需滚动，dock 强占底部。 | P0-01/02/07/10；根因在共享 shell+首页 IA；阶段 A/C；用十秒测试、首末项、主动作计数验收。 |
| `/welcome` | IA：四步清楚但“开始/跳过/下一步”并列；V/T：独立视觉体系、卡片边界过多；I/S：禁用上一步无原因。 | [证据](../output/playwright/audit-welcome-1440x900.png)：内容横向舒展但不是工作站共同壳层。 | [证据](../output/playwright/audit-welcome-1180x800.png)：四列仍可读。 | [证据](../output/playwright/audit-welcome-1024x768.png)：四列开始紧凑，底部三动作竞争。 | P1-LYT-01，若主动作不唯一则 P0-05；根因是 bespoke shell；阶段 B；按每屏一个决定验收。 |
| `/onboarding` | IA：核心首启被通用错误替代；V/T：错误红线与绿色勾冲突；I/S：仅笼统重试，无具体修复。 | [证据](../output/playwright/audit-onboarding-1440x900.png)：错误居中但大量无关 chrome。 | [证据](../output/playwright/audit-onboarding-1180x800.png)：同一错误。 | [证据](../output/playwright/audit-onboarding-1024x768.png)：同一错误且 dock/导航占空间。 | P0-08/09；业务根因 frontend、状态呈现阶段 A/B；六态+恢复路径验收。 |
| `/templates` | IA：三模板同权但选择任务尚清楚；V/T：卡内再套字段框；I/S：当前/可选/禁用语义靠小标签。 | [证据](../output/playwright/audit-templates-1440x900.png)：横向卡墙。 | [证据](../output/playwright/audit-templates-1180x800.png)：卡宽开始压缩。 | [证据](../output/playwright/audit-templates-1024x768.png)：仍硬塞三列且下部被视口截断。 | P0-06/13；阶段 D 资源模式；改列表/主选择+详情，验证核心选择与最长文案。 |
| `/topics` | IA：空态是主任务，但重复生成按钮；V/T：顶部蓝条与主按钮双重强调；I/S：empty 与 blocked 文案混杂。 | [证据](../output/playwright/audit-topics-1440x900.png)：大空态仍有两个主入口。 | [证据](../output/playwright/audit-topics-1180x800.png)：结构未降级。 | [证据](../output/playwright/audit-topics-1024x768.png)：可读但重复动作明显。 | P0-09、P1-INT-01；阶段 C；主按钮=1、empty/blocked fixture 验收。 |
| `/script` | IA：空态下一步相对清楚；V/T：像素标题与灰正文基本可分但壳层过重；I/S：依赖选题的 blocked 语义未明确。 | [证据](../output/playwright/audit-script-1440x900.png)：内容稀少而壳层占主导。 | [证据](../output/playwright/audit-script-1180x800.png)：同构。 | [证据](../output/playwright/audit-script-1024x768.png)：可读，dock 仍竞争。 | P0-07/09；阶段 C；空/编辑/版本/保存失败与 blocked 修复验收。 |
| `/video` | IA：当前动作不稳定；V/T：只见骨架或通用错误；I/S：running 无解释，failed 语义矛盾。 | [证据](../output/playwright/audit-video-1440x900.png)；另见[错误证据](../output/playwright/ui-baseline-1440x900.png)。 | [证据](../output/playwright/audit-video-1180x800.png)：骨架。 | [证据](../output/playwright/audit-video-1024x768.png)：骨架占主区，未见恢复。 | P0-08/09；阶段 C，业务加载根因 frontend；running 超时、failed、blocked、空素材验收。 |
| `/edit-plan` | IA：预览、四阶段、片段、Agent/语音/轨道多中心；V/T：边框与小字密集；I/S：大量禁用无原因。 | [证据](../output/playwright/audit-edit-plan-1440x900.png)：中央预览+右侧多 Inspector。 | [证据](../output/playwright/audit-edit-plan-1180x800.png)：中央明显被右栏挤压。 | [证据](../output/playwright/audit-edit-plan-1024x768.png)：右栏消失，预览下部受 dock/高度限制。 | P0-05/07/10/13；阶段 C；预览/时间轴唯一主对象、抽屉、首末时间点和禁用原因验收。 |
| `/draft` | IA：四步骤卡+轨道清单并列；V/T：警告框、进度线、代码词过密；I/S：待生成与 blocked 未分。 | [证据](../output/playwright/audit-draft-1440x900.png)：流程卡和内容卡竞争。 | [证据](../output/playwright/audit-draft-1180x800.png)：四列压缩。 | [证据](../output/playwright/audit-draft-1024x768.png)：长说明/元数据挤在有限高度。 | P0-05/13；阶段 C；当前步骤+主对象、生成/导入/校验/失败状态验收。 |
| `/history` | IA：空列表与四张零指标并列；V/T：色线/进度块装饰静态零值；I/S：API blocked 被当空态。 | [证据](../output/playwright/audit-history-1440x900.png)：右栏指标浪费宽度。 | [证据](../output/playwright/audit-history-1180x800.png)：指标仍占 280px。 | [证据](../output/playwright/audit-history-1024x768.png)：主列表被压到约半宽。 | P0-10、P1-VIS-01/STATE-02；阶段 C/D；列表全宽、blocked 修复、筛选/空/错误验收。 |
| `/knowledge` | IA：搜索+条目主任务清楚；V/T：长中英路径/摘要行长过长，小字号；I/S：详情查看路径不明显。 | [证据](../output/playwright/audit-knowledge-1440x900.png)：正文跨宽。 | [证据](../output/playwright/audit-knowledge-1180x800.png)：行长仍高。 | [证据](../output/playwright/audit-knowledge-1024x768.png)：长文本密集但无详情抽屉。 | P0-05/13、P1-TYPE-01；阶段 D；最长标题/路径、搜索空、索引失败、详情键盘验收。 |
| `/sources` | IA：同步、生成、识别三个动作并列；V/T：大空白来自固定结构；I/S：empty 与未同步/未配置混淆。 | [证据](../output/playwright/audit-sources-1440x900.png)：动作分散。 | [证据](../output/playwright/audit-sources-1180x800.png)：同构。 | [证据](../output/playwright/audit-sources-1024x768.png)：可见但状态恢复不明确。 | P1-IA-02/LTY-02/STATE-02；阶段 D；动作优先级与 empty/blocked/failed fixture 验收。 |
| `/learning` | IA：四指标+偏好卡+证据列表形成信息墙；V/T：四状态色与块进度装饰；I/S：禁用卡风险/回滚不清。 | [证据](../output/playwright/audit-learning-1440x900.png)：指标与偏好同权。 | [证据](../output/playwright/audit-learning-1180x800.png)：四指标压缩。 | [证据](../output/playwright/audit-learning-1024x768.png)：仍四列，文本紧凑。 | P0-01/06，P1-VIS-01；阶段 D；以一个待处理偏好为主任务，撤销/证据/失败验收。 |
| `/health` | IA：诊断清单是主任务但上方四数字重复；V/T：5 列表格和状态块过密；I/S：缺失依赖的修复链接不够强。 | [证据](../output/playwright/audit-health-1440x900.png)：宽表可扫但指标重复。 | [证据](../output/playwright/audit-health-1180x800.png)：列开始紧缩。 | [证据](../output/playwright/audit-health-1024x768.png)：多行换行、字号小，核心修复列被压。 | P0-05/13；阶段 D；1024 改定义列表/受控横滚，异常置顶、修复动作和键盘验收。 |
| `/settings` | IA：设置任务完全被运行错误阻断；V/T：绿色勾与错误矛盾；I/S：React 技术错误直接暴露。 | [证据](../output/playwright/audit-settings-1440x900.png)：错误。 | [证据](../output/playwright/audit-settings-1180x800.png)：曾出现无 H1/不稳定状态。 | [证据](../output/playwright/audit-settings-1024x768.png)：错误+Issue toast 重复。 | P0-08/09；阶段 D，业务根因 frontend；设置加载/编辑/验证/保存/失败三尺寸验收。 |
| `/profile` | IA：账户/画像页脱离产品 IA；V/T：共享样式不稳定；I/S：原生小控件、加载态不解释。 | [证据](../output/playwright/audit-profile-1440x900.png)：裸页面。 | [证据](../output/playwright/audit-profile-1180x800.png)：仅标题/副标题。 | [证据](../output/playwright/audit-profile-1024x768.png)：未样式化表单。 | P0-11/12；阶段 B/D；受控壳层、六态和最小目标验收。 |
| `/privacy` | IA：阅读页被生产导航/dock包围；V/T：正文可读但边框/像素标题过强；I/S：返回与链接焦点未验证。 | [证据](../output/playwright/audit-privacy-1440x900.png)：阅读列过宽。 | [证据](../output/playwright/audit-privacy-1180x800.png)：生产壳层仍在。 | [证据](../output/playwright/audit-privacy-1024x768.png)：正文宽尚可但底部 dock 无关。 | P1-IA-01；阶段 D；680–760px 阅读列、长链接换行、链接焦点/失败态验收。 |
| `/terms` | IA/V/T/I/S 与 privacy 同类；页面内容与生产命令无关。 | [证据](../output/playwright/audit-terms-1440x900.png)。 | [证据](../output/playwright/audit-terms-1180x800.png)。 | [证据](../output/playwright/audit-terms-1024x768.png)。 | P1-IA-01；阶段 D；共享 LegalDocument 模式和阅读/焦点/失败态验收。 |

## 5. 组件拆分与重构归属

不增加第二套设计 token。批准后将现有实现收敛到 DESIGN.md 已定义组件：

| 层 | 组件/契约 | UI 线程责任 | frontend/业务线程责任 |
| --- | --- | --- | --- |
| Token | 色彩、字号、行高、4/8/12/16/24/32 间距、边框、焦点、断点 | 映射现有 CSS 变量；消除页面临时 token | 无 |
| Chrome | `RetroChrome`、真实菜单/工具栏、Episode 上下文 | 结构、尺寸、视觉、键盘与降级 | 提供真实动作/路由语义 |
| Shell | `AppShell`、生产导航、主区、Inspector drawer、dock | 唯一滚动责任、三断点、区域可访问性 | 当前路由、权限、流程状态 |
| Primitives | Button/IconButton/Input/Select/StatusTag/Panel/ScrollableRegion | 尺寸、状态、焦点、图标和视觉回归 | 动作与验证结果 |
| States | Empty/Error/Loading/Blocked/Confirm | 六态呈现、文案槽位、恢复动作结构 | 人话原因、影响、修复动作、技术详情 |
| Patterns | WorkflowRail、TaskPanel、Inspector、DataTable/DefinitionList、Timeline、CommandConsole | 响应式模式与预览门禁 | 数据、执行、确认、回滚 |
| Pages | Setup、Workbench、Episode 主线、资源/系统、Legal | 按批准顺序组合共享模式 | 数据加载、状态机、业务动作 |

## 6. 阶段化重构计划

### Gate 0：主线程批准审计与页面契约

- 审核本报告的 P0/P1、页面顺序、组件边界和验收方式。
- 现有四个 WIP 文件继续冻结，不合并、不扩展；不得把阶段条局部 CSS 当作阶段 A 起点。
- 主线程明确 UI 与 frontend 的文件所有权，特别是 `AppShell`、状态组件和 onboarding 页面组合点。

退出门槛：P0 清单、三尺寸页面契约、六态 fixture 方案和 owner 均被确认。

### 阶段 A：设计基线落地与共享工作站壳层

范围：token/字体加载、统一 PixelIcon、Button/Input/Panel/StatusTag、Error/Empty/Loading/Blocked、`RetroChrome`、`AppShell`、Inspector drawer、Command dock、ScrollableRegion，以及截图/裁切/最小目标/重叠测试。

关键决策：

- 一个视图一个主任务；chrome 只保留真实动作。
- 1440 为完整三栏；1180 为标准桌面但 Inspector 可按需；1024 默认 Inspector 抽屉、生产导航降级、dock 收敛。
- 根、主区、Inspector、dock、局部横滚各自只有一个滚动责任。
- 绝不通过 `clip`、小字、负 margin、绝对定位或缩放塞内容。

退出门槛：独立壳层测试页在三尺寸及 loading/empty/error/blocked 下通过全部 P0 技术门禁；正文无 `<12px`；可见工具目标≥32、主动作≥40。

### 阶段 B：首次使用、身份与工作区

顺序：`/welcome` → `/onboarding` → `/profile`。UI 线程只负责 Setup shell、步骤、状态和表单模式；frontend 线程负责身份、目录授权、模型配置、依赖检查和路由状态。

退出门槛：每屏一个决定；阻断项不能跳过；失败有具体修复；进入工作台前状态可信；三尺寸默认/加载/错误/进行中/待确认截图齐全。

### 阶段 C：工作台与 Episode 主线

顺序：`/` → `/topics` → `/script` → `/video` → `/edit-plan` → `/draft` → `/history`。每次只重构一个可验收工作面；先保证对象、Agent 动作、下一步，再添加次级信息。

退出门槛：每页三尺寸能完成核心动作；Inspector 在 1024 不抢宽；时间线/阶段条首末项可达；失败/blocked 可恢复；命令执行可预览、确认、显示结果并提供重试/回滚。

### 阶段 D：资源、健康、设置与阅读页

顺序：`/templates` → `/knowledge` → `/sources` → `/learning` → `/health` → `/settings` → `/privacy` → `/terms`。统一资源列表、搜索、定义列表/表格降级、配置表单、诊断清单和 LegalDocument。

退出门槛：资源入口不再污染生产导航；1024 宽表内容可达；empty/blocked/failed 分离；法律页无生产 dock；设置可保存和恢复。

### 阶段 E：真实 Electron 与高缩放验收

- 在打包 Electron 中验证 1440×900、1180×800、1024×768。
- Windows 100%、125%、150% 系统缩放；低于 1024 只保证查看、继续、确认和阻断修复，并显示桌面精剪提示。
- 验证原生标题栏/菜单、字体栅格、DPI、键盘菜单、焦点、窗口缩放、恢复路径。

退出门槛：三尺寸/三缩放截图和键盘录像；无不可达裁切、重叠或唯一主动作丢失；Electron 与 Web 的页面契约一致。

## 7. 统一验收门禁

每页、每个适用状态、每个尺寸均执行：

1. 10 秒内识别当前对象、Agent 状态、唯一下一步。
2. `html/body` 无根级横向滚动，但此项只作为最低条件。
3. 枚举所有可见 `scrollWidth > clientWidth` 节点；只有显式标记的局部滚动容器允许存在，并具备提示、ARIA、键盘、首/当前/末项可达断言。
4. 检测 `clip/hidden` 截断、元素矩形重叠、dock 覆盖、Inspector 抢占、焦点框裁切。
5. 有效正文≥12px；长中文、英文、数字、路径在字体加载成功/失败时均可读。
6. 主/危险动作目标≥40px；密集工具≥32px且有 tooltip；主按钮每视图最多一个。
7. 状态同时使用文字、图标/形状、语义色；failed/blocked 包含原因、影响、修复和按需技术详情。
8. 默认、空、加载、错误、进行中、待确认六态中适用者均有真实截图和 fixture。
9. 截图文件名统一：`route-state-1440x900.png`、`route-state-1180x800.png`、`route-state-1024x768.png`；局部轨道另存 `scroll-start/end`。
10. 执行 DESIGN.md 要求的 `test:web-contract`、`test:pixel-style`、`lint:web`、`build:web`；构建通过不能替代视觉通过。

## 8. 当前证据边界

- 本轮 54 张截图来自 Web 开发实例，覆盖真实当前数据/失败态，但没有人为伪造全部六态 fixture。
- 自动指标是问题探测器，不是视觉真值：例如根无横滚仍可能有不可达裁切；目标尺寸统计需结合控件角色判断。
- 真实 Electron、Windows 125%/150% 缩放、完整键盘路径尚未验证，已作为 P1-UE-01 和阶段 E 门禁明确保留。
- 本报告没有修改 CSS、组件、页面、API、路由或业务状态，也没有提交现有 WIP。
