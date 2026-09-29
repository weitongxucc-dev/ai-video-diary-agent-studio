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

1. **Linear 改名未获批准**：Claude 仅继续完成用户要求的只读审批材料，候选文件 `/tmp/wil-audit/final-approval-2026-09-29-v2.md`。不执行批量改名、描述修改或状态整理；交接登记可写项目讨论，不改任何 issue 字段。WIL-121 为新增工作项，不自动加入原116条命名审批范围。
2. **成员可见性 HOLD**：WIL-110 / WIL-112 / WIL-116 及相关施工保持暂停，不因主控切换或命名整理恢复。候选 `5fcb8ac` 保留、不合入；`sources.py`、`source_repository.py`、`production.py` 和相关可见性迁移/测试不转派。用户已记录的新建 private / 存量 shared 与旧派单新建 shared 相冲突，须按明确暂停要求完成口径澄清、权威同步及重新绑定后恢复。
3. 本地多成员沿用户指定的本地 SQLite 方案核验；既有 Supabase 路径不删除、不自动激活。当前身份绕过面和读写授权缺口不得表述为安全隔离已完成；也不得借交接自行重设计身份架构。安全稳定规则仍见 [认证边界](../docs/auth-workspace-boundary.md)。
4. scheduled 生产运行仍关闭。WIL-92 是只读施工计划，不授予 main.py 写权。启动互斥阻断不等于准入门，OwnerRegistration、CPU预算、真实重试授权和端到端释放须分别核实。不得因接口/测试存在宣称 scheduled 已可用。
5. 真实发布不排期、不执行。发布授权和草稿的本地 mock、真实浏览器本地页、真实平台登录、真实草稿、真实发送分别报告，不能互相替代。平台集合的既有选择不构成账号操作授权。
6. 用户要求测试尽量最短、最小、串行、立即释放资源；不为更稳重复批量跑模型、不并发加载大模型。本轮交叉审核不调用外部模型、不下载模型。

## 当前执行者与独占文件

cmux 的短编号会变化，每次发送前核对 UUID、标题和屏幕。`external-agents` 下文均指上述完整外部协作目录。交付、阻断及要求主控裁决的信息必须落文件，不能只在终端显示。

| 执行者（2026-09-29现场定位） | 当前包与工作树 | 保留的产品文件归属及退出条件 |
| --- | --- | --- |
| workspace:9 / surface:26，前端 Pi | WIL-90，`~/.codex/worktrees/insert-frontend-ui/original` | `apps/web/src/lib/types.ts`、`VideoEditor.tsx`、`CreationTaskWorkspace.tsx` 及其专项测试；继续原已授权 Insert 可见验收，不中断、不重派、不写后端 |
| workspace:4 / surface:23，Pi | WIL-116，`~/.codex/worktrees/agent-recall-memory/original`，候选 `5fcb8ac` | 可见性相关 `sources.py`、`source_repository.py`、`production.py` 及专项测试保留归属；HOLD，禁止新增修改/提交/集成 |
| workspace:5 / surface:16，Claude | WIL-118，`~/.codex/worktrees/wil96/original`，候选 `a59fe1f` | `model_runtime.py`、`model_installer.py`、`runtime_health.py`、`visual_semantics.py` 及原包专项测试；已交付待审，不扩权或继续产品写入 |
| workspace:2 / surface:12，Claude | WIL-119，`~/.codex/worktrees/insert-backend-splice/original`，候选 `eb7b5a7` | `apps/api/app/quality_checks.py`、`apps/api/tests/test_quality_checks.py`；已交付待审，不把独立检测函数称为自动检查流程已接通 |
| workspace:10 / surface:27，Claude | WIL-121 / WIL-97，`~/.codex/worktrees/wil97/original`，候选 `4302197` | `apps/web/src/components/AudioTimelineEditor.tsx`；已交付待审，产品文件保留归属；降噪试听、原声保护、静态检查等未验项不自动关闭 |
| workspace:6 / surface:6，OpenCode | WIL-113 / WIL-115 既有交付，当前可接只读审核 | 无新产品写权；发布相关文件不因既有交付而获得继续施工许可 |
| workspace:3 / surface:11，原 Claude 主控 | 只读命名审批材料 | 只写其已有审批材料；不全局派单、不集成、不写共享合同、不执行 Linear 改名 |
| 本 Codex 主控 | 本合同、派单和审核 | 共享文档唯一写者；main.py、schemas.py、DDL 等未明确移交文件不默认开放；依赖复核后逐包授予 |

后端省略前缀的文件位于 `apps/api/app/`，前端组件位于 `apps/web/src/components/`。本表不授予新的产品实现任务。交叉审核者只能读候选，在自己的报告路径写证据，不成为候选文件写者。

## 下一批：现有候选交叉审核

三包互不写产品文件；共同基线 dev `93e8182`，审核具体候选，不整支合并。精确契约为 `external-agents/dispatch/controller-20260929-review-*.md`。

- surface:12 审模型外接盘候选 `a59fe1f`，写 `external-agents/review-20260929/model-paths.md`。
- surface:16 审质量检测候选 `eb7b5a7`，写 `external-agents/review-20260929/quality-checks.md`。
- surface:6 审音量包络线候选 `4302197`，写 `external-agents/review-20260929/audio-envelope.md`。
- surface:26 保留当前可见验收；surface:27 保留候选等待审核；surface:23 保持 HOLD。

主控先核独占、diff、依赖与失败语义，再选择最小复验。不把执行者自称“独立复现”当成独立审核；未审候选不集成，不按报告的全量绿灯关闭父项。

## 可见与运行验收

- UI 基线是 [DESIGN.md](../DESIGN.md) §5.10 的 workspace-dual-v4 主账号/成员原型：三栏、可拖动收起、右上精细修改浮卡、紧凑中文；旧 product-ui-v2 不恢复，未经确认不另起整体布局。原型示例不进入生产事实。
- 三尺寸1440×900、1180×800、1024×768分别观察；截图、组件、真实API、媒体输出、桌面安装包和产品闭环分别验收。
- 当前 WIL-120「完全没有任务创建入口」的判断有反证：`main.py` 的 `POST /api/creation-plans/confirm` 创建并持久化 CreationTask，HomeCommandComposer/CreateProductionTaskForm 已有调用。现有路径须先复核，不能据缺少 `POST /api/creation-tasks` 新造入口；完整桌面创建到 Insert 流程仍未验收。对应 issue 暂不修改，证据交回命名审批材料负责人。
- 当前 PR45 的旧 head CI 不能证明本轮候选；集成完成后按同一候选实际检查，不沿用旧绿灯。main 不自动合并。
- 取消请求不等于进程退出；释放许可、检查点、媒体产物和失效状态遵循现有架构/领域契约。模型配置、元信息探测、mock成功或保存记录均不等于真实制作完成。legacy 与 scheduled 不得同时启动重活驱动；所有重活沿同一许可、授权和资源预算，不能借 hash/probe 绕行。旧旁白替换后的字幕失效、人工文字保留和成片同源仍须符合领域契约。

## 完成和恢复

本轮交接完成条件：旧主控停止全局动作、唯一主控登记、执行者保留归属、暂停未被解除、下一批契约可执行且已送达。交接不代表0.11完成。剩余关键路径按真实用户旅程和审查结果推进，不把 Linear 子单 Done 自动传播到父范围。
