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

1. **Linear 协作试点已批准**：用户在本主控对话明确同意“对齐在制状态、整理三个查看入口、跑通 Insert 功能试点”。允许主控依据现有证据修正活动 issue 的项目归属、状态、标签、关系与失效描述；保留原编号、历史交付及原需求索引，不执行原批量改名清单，不重审所有历史 Done。先建立功能进展、执行安排、需要用户决定/验收三个入口。旧只读命名审批材料不再是本轮执行依据，也不授予旧主控新的写权。
2. **成员可见性 HOLD**：WIL-110 / WIL-112 / WIL-116 及相关施工保持暂停，不因主控切换或命名整理恢复。候选 `5fcb8ac` 保留、不合入；`sources.py`、`source_repository.py`、`production.py` 和相关可见性迁移/测试不转派。用户已记录的新建 private / 存量 shared 与旧派单新建 shared 相冲突，须按明确暂停要求完成口径澄清、权威同步及重新绑定后恢复。
3. 本地多成员沿用户指定的本地 SQLite 方案核验；既有 Supabase 路径不删除、不自动激活。当前身份绕过面和读写授权缺口不得表述为安全隔离已完成；也不得借交接自行重设计身份架构。安全稳定规则仍见 [认证边界](../docs/auth-workspace-boundary.md)。
4. scheduled 生产运行仍关闭。WIL-92 是只读施工计划，不授予 main.py 写权。启动互斥阻断不等于准入门，OwnerRegistration、CPU预算、真实重试授权和端到端释放须分别核实。不得因接口/测试存在宣称 scheduled 已可用。
5. 真实发布不排期、不执行。发布授权和草稿的本地 mock、真实浏览器本地页、真实平台登录、真实草稿、真实发送分别报告，不能互相替代。平台集合的既有选择不构成账号操作授权。
6. 用户要求测试尽量最短、最小、串行、立即释放资源；不为更稳重复批量跑模型、不并发加载大模型。本轮交叉审核不调用外部模型、不下载模型。

## 当前执行者与独占文件

cmux 的短编号会变化，每次发送前核对 UUID、标题和屏幕。`external-agents` 下文均指上述完整外部协作目录。交付、阻断及要求主控裁决的信息必须落文件，不能只在终端显示。

| 执行者（2026-09-29现场定位） | 当前包与工作树 | 保留的产品文件归属及退出条件 |
| --- | --- | --- |
| workspace:9 / surface:26，前端 Pi | 原 WIL-90 执行者，服务错误停机，工作树已核干净 | 本轮写权撤回；不再修改产品或验收夹具。既有交付保留，后续只作历史证据 |
| workspace:2 / surface:12，Claude | WIL-90 / WIL-122 接续，`~/.codex/worktrees/insert-frontend-ui/original` | 独占原前端 owned files 与专项测试；网关响应失败后不再执行环境写入。主控接手独立临时夹具和浏览器验证。不得改后端或解除其 WIL-119 HOLD |
| workspace:4 / surface:23，Pi | WIL-116，`~/.codex/worktrees/agent-recall-memory/original`，候选 `5fcb8ac` | 可见性相关 `sources.py`、`source_repository.py`、`production.py` 及专项测试保留归属；HOLD，禁止新增修改/提交/集成 |
| workspace:5 / surface:16，Claude | WIL-118，`~/.codex/worktrees/wil96/original`，候选 `a59fe1f` | `model_runtime.py`、`model_installer.py`、`runtime_health.py`、`visual_semantics.py` 及原包专项测试；审核退回并 HOLD，不扩权或继续产品写入 |
| workspace:2 / surface:12，Claude | WIL-119，`~/.codex/worktrees/insert-backend-splice/original`，候选 `eb7b5a7` | `apps/api/app/quality_checks.py`、`apps/api/tests/test_quality_checks.py`；审核退回并 HOLD，不把独立检测函数称为自动检查流程已接通 |
| workspace:10 / surface:27，Claude | WIL-121 / WIL-97，`~/.codex/worktrees/wil97/original`，候选 `4302197` | `apps/web/src/components/AudioTimelineEditor.tsx`；审核退回并 HOLD，产品文件保留归属；降噪试听、原声保护、静态检查等未验项不自动关闭 |
| workspace:6 / surface:6，OpenCode | WIL-113 / WIL-115 既有交付，当前可接只读审核 | 无新产品写权；发布相关文件不因既有交付而获得继续施工许可 |
| workspace:3 / surface:11，原 Claude 主控 | 只读命名审批材料 | 只写其已有审批材料；不全局派单、不集成、不写共享合同、不执行 Linear 改名 |
| 本 Codex 主控 | 本合同、派单和审核 | 共享文档唯一写者；main.py、schemas.py、DDL 等未明确移交文件不默认开放；依赖复核后逐包授予 |

后端省略前缀的文件位于 `apps/api/app/`，前端组件位于 `apps/web/src/components/`。本表不授予新的产品实现任务。交叉审核者只能读候选，在自己的报告路径写证据，不成为候选文件写者。

## 当前试点：功能卡到 Insert 实际验收

用户批准的是现有工作接续，不是重做产品或全量迁移看板。主控负责 Linear 功能卡、在制事实对齐、证据回填和集成；用户再次批准执行下一步后，surface:12 接手 WIL-90 前端；随后该线程也遇到网关错误，主控接手隔离验收环境和浏览器验证，不编写产品代码；原 surface:26 已停在 provider 错误，工作树干净、无相关子进程，不保留写权。若需修产品代码，只在其原 owned files 内实施；后端或领域语义新增变更先报主控。最小用户链路：真实任务页面选中镜头 → 添加片段 → 预览 → 确认 → 顺序与版本正确 → 撤销/重做 → 预览和导出对应；三尺寸验证新增交互。测试只用隔离库与短合成素材，禁止真实库、付费模型、真实发布。现有创建入口先查 `POST /api/creation-plans/confirm`，不能依 WIL-120 的错误推断另造入口。

本轮恢复授权：用户明确“可以的，你执行吧”，已覆盖技术排障与必要执行者移交。上一轮任务页 404、无效夹具和无效 MP4 是修复起点，不再作为停止整个试点的理由。产品基线 93e8182，后续集成提交仅文档（验收时 a384d10）；禁止为迁就测试数据更改产品模型。新执行者读取指定合同并 ACK 后继续，使用当前数据模型和短合成有效视频，不调用外部模型；主控逐条跑真实页面。当前环境位于 `/tmp/wil122-verify`，使用独立数据库和真实短合成视频/测试音；任务 `wil122-real-media`，Web 4400 / API 4401。当前验收库为 `/tmp/wil122-verify/verified.sqlite3`；不得重新执行种子脚本覆盖用户正在体验的任务。旧 `/tmp/wil90-demo` 原样保留，不能恢复旧无效夹具。所有可见性、发布、质量/模型/包络线其他 HOLD 原样保留。

用户已明确验收 Insert 并认可开发过程可读，授权推进下一步。当前转入剩余工作核对与下一批范围/迁移安排，验收结论与运行证据由 WIL-122 和 `external-agents/controller-linear-pilot/insert-runtime-20260929.md` 保存，不在本合同复制完成度。下一批建议见 Linear 文档「试点通过后的下一轮安排｜范围与迁移建议」；本阶段只读核验，不因此解除可见性、发布、scheduled 或三份退回候选的 HOLD，不授予未移交文件写权。结构迁移和相应施工按该具体安排审核后执行。已有隔离任务不重复播种或清除；需要修改时先核原文件负责人及最新授权。

一个功能卡持续承载用户结果，WIL-89/90 作为施工交付关联或子项；R25/WIL-29 保留范围索引。子项 Done 不自动关闭功能卡。候选/集成、组件/API、真实页面、输出媒体、桌面包分别登记；缺证据就保留未验，不把 HTTP 200 或截图存在提升为产品验收。工具可选，沿用 DESIGN.md §5.10，UI 不强制经过 Stitch。

## 已交付候选：审核收口边界

三份交叉审核均已落盘并给出 HOLD；主控把具名问题和证据回填对应 Linear。它们不是本轮 Insert 试点的自动返工授权；不合入，不开启新产品写入，保留作者归属。原审核共同基线 dev `93e8182`，具体报告如下。

- surface:12 审模型外接盘候选 `a59fe1f`，写 `external-agents/review-20260929/model-paths.md`。
- surface:16 审质量检测候选 `eb7b5a7`，写 `external-agents/review-20260929/quality-checks.md`。
- surface:6 审音量包络线候选 `4302197`，写 `external-agents/review-20260929/audio-envelope.md`。
- surface:12 保留 Insert 前端产品归属，主控接手临时环境并执行可见验收；surface:27 保留被退回候选，不自动返工；surface:23 保持 HOLD。

主控先核独占、diff、依赖与失败语义，再选择最小复验。不把执行者自称“独立复现”当成独立审核；未审候选不集成，不按报告的全量绿灯关闭父项。

## 可见与运行验收

- UI 基线是 [DESIGN.md](../DESIGN.md) §5.10 的 workspace-dual-v4 主账号/成员原型：三栏、可拖动收起、右上精细修改浮卡、紧凑中文；旧 product-ui-v2 不恢复，未经确认不另起整体布局。原型示例不进入生产事实。
- 三尺寸1440×900、1180×800、1024×768分别观察；截图、组件、真实API、媒体输出、桌面安装包和产品闭环分别验收。
- 当前 WIL-120「完全没有任务创建入口」的判断有反证：`main.py` 的 `POST /api/creation-plans/confirm` 创建并持久化 CreationTask，HomeCommandComposer/CreateProductionTaskForm 已有调用。主控本轮纠正 issue 的错误前提，保留追溯；完整桌面创建到 Insert 流程仍须真实验收，不能据缺少 `POST /api/creation-tasks` 新造入口。
- 当前 PR45 的旧 head CI 不能证明本轮候选；集成完成后按同一候选实际检查，不沿用旧绿灯。main 不自动合并。
- 取消请求不等于进程退出；释放许可、检查点、媒体产物和失效状态遵循现有架构/领域契约。模型配置、元信息探测、mock成功或保存记录均不等于真实制作完成。legacy 与 scheduled 不得同时启动重活驱动；所有重活沿同一许可、授权和资源预算，不能借 hash/probe 绕行。旧旁白替换后的字幕失效、人工文字保留和成片同源仍须符合领域契约。

## 完成和恢复

本轮交接完成条件：旧主控停止全局动作、唯一主控登记、执行者保留归属、暂停未被解除、下一批契约可执行且已送达。交接不代表0.11完成。剩余关键路径按真实用户旅程和审查结果推进，不把 Linear 子单 Done 自动传播到父范围。
