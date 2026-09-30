# 0.11 桌面闭环执行合同

## 当前授权与主控

ACTIVE。2026-09-29 19:12（Asia/Shanghai）完成交接登记：唯一主控为 Codex 聊天 `01a0ecd6-dc91-7500-8f03-45e2dbde4820`。用户新建主控聊天接续项目；cmux Claude 已书面确认停止全局派单、集成及共享合同写入，且没有更晚的禁止交接指令。原 Codex 聊天也停止派单和集成。

- 交接回执：`/Users/Zhuanz/Documents/Codex/2026-09-24/volumes-dujiangyan-vm-ai-repositories-bd25/external-agents/controller-handoff-20260929-response.md`。回执作交接证据，不定义产品规则。
- 主控负责规划、共享文档、派工、审核、集成与开发分支推送，不编写产品代码。不派内部子智能体；继续使用既有可见 cmux 执行者，不更改模型配置。
- 集成目录 `/Volumes/Dujiangyan-VM/AI视频日记/repositories/bd25`，分支 `codex/0.11-development`。项目保存入口 `/Users/Zhuanz/Documents/AI视频日记` 指向另一旧 checkout，不在那里开展本轮集成。基线逐包给出，不用旧 SHA 回退工作树。
- 开发分支推送授权保留；main 合并、真实平台发送/发布、新增付费调用、签名、公证、真实用户库迁移、覆盖已安装版本不在授权内。测试只用隔离数据，不打开 `~/Movies/AI视频日记/agent.sqlite3`。
- 保护未跟踪的 `.project-control/0.11-bug-backlog.md`、`.project-control/handoff.md`、`work/integration-011-tmp/` 和各工作树的用户改动；不得 reset、清理或覆盖。
- 本文件只保存当前约束和文件归属。已结束批次、测试计数及旧主控登记从活动入口退役，历史由 Git、Linear 和原始交付报告追溯，不重新激活旧 Codex 线程。

## 权威和保留的暂停

长期规则从 [文档路由](../docs/README.md) 逐步读取；完整需求追踪见 [开发台账](0.11-development-plan.md)。0.11 桌面与 0.12 手机分开，追踪总表不是本轮全部必交付。

1. **0.11 Linear 整体重构已批准**：用户最新明确要求按0.11需求重构难懂、过粗的issue，以便多Agent协同。允许主控实际改写标题和描述、重排父子/关联/阻塞、补必要工作包及视图；优先复用现有ID，先保存原始内容及需求索引，保留历史交付和验收记录。旧“仅试点/先审迁移方案”的限制已被本次具体授权替代。本次只重构任务管理，不自动授权产品施工、解除既有HOLD、平台动作或真实数据迁移。Linear仅主控写入。
2. **成员可见性 HOLD**：WIL-110 / WIL-112 / WIL-116 及相关施工保持暂停，不因主控切换或命名整理恢复。候选 `5fcb8ac` 保留、不合入；`sources.py`、`source_repository.py`、`production.py` 和相关可见性迁移/测试不转派。用户已决定新建 private / 存量 shared，旧派单中新建 shared 的文字失效；本次Linear描述已纠正。恢复仍须完成相关权威和具体候选同步、执行者重新绑定，并满足原明确HOLD条件；不重复询问已经确定的默认值。
3. 本地多成员沿用户指定的本地 SQLite 方案核验；既有 Supabase 路径不删除、不自动激活。当前身份绕过面和读写授权缺口不得表述为安全隔离已完成；也不得借交接自行重设计身份架构。安全稳定规则仍见 [认证边界](../docs/auth-workspace-boundary.md)。
4. scheduled 生产运行仍关闭。WIL-92 是只读施工计划，不授予 main.py 写权。启动互斥阻断不等于准入门，OwnerRegistration、CPU预算、真实重试授权和端到端释放须分别核实。不得因接口/测试存在宣称 scheduled 已可用。
5. 真实发布不排期、不执行。发布授权和草稿的本地 mock、真实浏览器本地页、真实平台登录、真实草稿、真实发送分别报告，不能互相替代。平台集合的既有选择不构成账号操作授权。
6. 用户要求测试尽量最短、最小、串行、立即释放资源；不为更稳重复批量跑模型、不并发加载大模型。本轮交叉审核不调用外部模型、不下载模型。

## 当前执行者与独占文件

cmux 的短编号会变化，每次发送前核对 UUID、标题和屏幕。`external-agents` 下文均指上述完整外部协作目录。交付、阻断及要求主控裁决的信息必须落文件，不能只在终端显示。

| 执行者（2026-09-29现场定位） | 当前包与工作树 | 保留的产品文件归属及退出条件 |
| --- | --- | --- |
| workspace:9 / surface:26，前端 Pi | 原 WIL-90 执行者，服务错误停机，工作树已核干净 | 本轮写权撤回；不再修改产品或验收夹具。既有交付保留，后续只作历史证据 |
| workspace:2 / surface:12，Claude | WIL-90 / WIL-122 接续，`~/.codex/worktrees/insert-frontend-ui/original` | 原前端任务已验收；当前cmux清单已无此窗口，原工作树产品干净。原前端写权撤回，本轮精确移交下表；保留其后端 WIL-119 HOLD |
| workspace:4 / surface:23，Pi | WIL-116，`~/.codex/worktrees/agent-recall-memory/original`，候选 `5fcb8ac` | 可见性相关 `sources.py`、`source_repository.py`、`production.py` 及专项测试保留归属；HOLD，禁止新增修改/提交/集成 |
| workspace:5 / surface:16，Claude | WIL-118，`~/.codex/worktrees/wil96/original`，候选 `a59fe1f` | `model_runtime.py`、`model_installer.py`、`runtime_health.py`、`visual_semantics.py` 及原包专项测试；审核退回并 HOLD，不扩权或继续产品写入 |
| workspace:2 / surface:12，Claude | WIL-119，`~/.codex/worktrees/insert-backend-splice/original`，候选 `eb7b5a7` | `apps/api/app/quality_checks.py`、`apps/api/tests/test_quality_checks.py`；审核退回并 HOLD，不把独立检测函数称为自动检查流程已接通 |
| workspace:10 / surface:27，Claude | WIL-121 / WIL-97，`~/.codex/worktrees/wil97/original`，候选 `4302197` | `apps/web/src/components/AudioTimelineEditor.tsx`；审核退回并 HOLD，产品文件保留归属；降噪试听、原声保护、静态检查等未验项不自动关闭 |
| workspace:6 / surface:6，OpenCode | WIL-113 / WIL-115 既有交付，当前可接只读审核 | 无新产品写权；发布相关文件不因既有交付而获得继续施工许可 |
| workspace:3 / surface:11，原 Claude 主控 | 只读命名审批材料 | 只写其已有审批材料；不全局派单、不集成、不写共享合同、不执行 Linear 改名 |
| 本 Codex 主控 | 本合同、派单和审核 | 共享文档唯一写者；main.py、schemas.py、DDL 等未明确移交文件不默认开放；依赖复核后逐包授予 |

后端省略前缀的文件位于 `apps/api/app/`，前端组件位于 `apps/web/src/components/`。本表不授予新的产品实现任务。交叉审核者只能读候选，在自己的报告路径写证据，不成为候选文件写者。

## 已验收的参照流程与本轮看板重构

用户已验收 Insert 并认可开发过程可读。WIL-122 保存产品验收结论，WIL-89/90 保存工程交付，WIL-29 保存视频剪辑的其余范围；具体证据见 Linear 和 `external-agents/controller-linear-pilot/insert-runtime-20260929.md`。本合同不复制完成度。

用户随后明确要求按0.11需求重构整个Linear。现行入口为项目文档「从这里开始｜开发进度、待决定与待验收」和「0.11 工作地图与原需求索引」；后者原为“试点通过后的下一轮安排”，已原位替代待审迁移提案。功能卡承载验收，子工作包分别承载实施、方案和核验；未来储备包在Backlog，进入具体安排后再进入执行视图。Linear主控单写，具体状态与依赖以实时issue为准。

2026-09-30 用户提供三个新 cmux 窗口并明确“可以，你安排吧”，授权下表实施和核验。主控按[开发流程](../docs/development-workflow.md)绑定候选、线程、文件集合和真实验收；原产品文件归属及HOLD保持。同一共享文件只授予一个写者。UI可以直接实现或使用Stitch辅助，按DESIGN.md §5.10验收真实页面。

### 2026-09-30 第二批：布局、保存反馈与输出核验

用户已明确批准前述4项开发、2项独立核验。本批源码基线7c7101f36252e04b5fae2ddfc173104659c25d91，派单唯一入口 `external-agents/batch-20260930-next/` 的下列 dispatch.md。主控审核、集成、推送开发分支，不写产品代码。原前端窗口已不在当前cmux树，原工作树干净；撤回旧前端写权，按精确文件集合移交。WIL119及其他HOLD保持。

| 可见执行者 | 工作与写权 | 验收/退出 |
| --- | --- | --- |
| workspace13 / surface737EB193-23B8-422A-9DC1-8E8F0EB3D1A5 | WIL174/172已交付；173写权撤回。主控因反复上下文整理中断其运行，现场核实产品树干净，未有173产品改动；不得恢复旧派单 | 3002测试服务与布局工作树移交workspace14；证据保留。主控现场交接记录见layout/controller-review.md |
| workspace15 / surface70D0DA2B-4062-4A12-8FC9-4D6B396B9C8F | WIL175 已交付停写；~/.codex/worktrees/wil175-save-feedback/original；本包产品写权退役，交付及主控审核见WIL-175目录 | 原生核验已收口；不得按旧派单继续修改或重启服务 |
| workspace14 / surfaceEABE9E01-E2D9-4F69-BC8D-93927990BCEE | 原输出核验已退役；现在唯一接续WIL173，复用 ~/.codex/worktrees/wil95-evidence-wording/original，HEAD57ef24a；只写AppShell.tsx、EditorShell.module.css、VideoEditor.module.css、AppShell.test.tsx；派单layout/WIL-173-takeover.md | 接管自有Web3002，可在本包tmp起隔离API4402；不得写原输出核验目录或4401旧库；候选+真实三尺寸横竖视频核验后交主控，结束释放 |

仅主控写Linear与共享合同。前批95/55/99已交付停写，旧报告和证据保留，99最终收尾落盘差异由原执行者核对补齐，不重复测试。新任务不得按旧派单恢复写入。文件排队不设成产品阻塞。实际核验失败仍可交付核验工作，但父功能不自动Done。

保护旧体验环境/tmp/wil122-verify、/tmp/wil90-demo和本轮前批/tmp/wil99-20260930，只能定向只读或复制到新包tmp。不打开真实用户库。无模型下载/新增付费推理/真实平台动作，验收夹具不改变产品事实。

## 已交付候选：审核收口边界

三份交叉审核均已落盘并给出 HOLD；主控把具名问题和证据回填对应 Linear。它们不是本轮 Insert 试点的自动返工授权；不合入，不开启新产品写入，保留作者归属。原审核共同基线 dev `93e8182`，具体报告如下。

- surface:12 审模型外接盘候选 `a59fe1f`，写 `external-agents/review-20260929/model-paths.md`。
- surface:16 审质量检测候选 `eb7b5a7`，写 `external-agents/review-20260929/quality-checks.md`。
- surface:6 审音量包络线候选 `4302197`，写 `external-agents/review-20260929/audio-envelope.md`。
- surface:12 的原前端写权已按第二批精确移交；surface:27 保留被退回候选，不自动返工；surface:23 保持 HOLD。

主控先核独占、diff、依赖与失败语义，再选择最小复验。不把执行者自称“独立复现”当成独立审核；未审候选不集成，不按报告的全量绿灯关闭父项。

## 可见与运行验收

- UI 基线是 [DESIGN.md](../DESIGN.md) §5.10 的 workspace-dual-v4 主账号/成员原型：三栏、可拖动收起、右上精细修改浮卡、紧凑中文；旧 product-ui-v2 不恢复，未经确认不另起整体布局。原型示例不进入生产事实。
- 三尺寸1440×900、1180×800、1024×768分别观察；截图、组件、真实API、媒体输出、桌面安装包和产品闭环分别验收。
- 当前 WIL-120「完全没有任务创建入口」的判断有反证：`main.py` 的 `POST /api/creation-plans/confirm` 创建并持久化 CreationTask，HomeCommandComposer/CreateProductionTaskForm 已有调用。主控本轮纠正 issue 的错误前提，保留追溯；完整桌面创建到 Insert 流程仍须真实验收，不能据缺少 `POST /api/creation-tasks` 新造入口。
- 当前 PR45 的旧 head CI 不能证明本轮候选；集成完成后按同一候选实际检查，不沿用旧绿灯。main 不自动合并。
- 取消请求不等于进程退出；释放许可、检查点、媒体产物和失效状态遵循现有架构/领域契约。模型配置、元信息探测、mock成功或保存记录均不等于真实制作完成。legacy 与 scheduled 不得同时启动重活驱动；所有重活沿同一许可、授权和资源预算，不能借 hash/probe 绕行。旧旁白替换后的字幕失效、人工文字保留和成片同源仍须符合领域契约。

## 完成和恢复

本轮看板重构完成条件：原需求索引可追溯、活动工作都有功能归属和类型化完成判据、真实依赖无循环、旧描述退出执行且HOLD保留、功能与执行视图可读。管理重构不代表0.11完成；后续施工仅按上表具体授权进行。剩余关键路径按真实用户旅程和审查结果推进，不把 Linear 子单 Done 自动传播到父范围。
