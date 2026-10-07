# 0.11 main 主控与当前执行合同

## 本地主入口

用户于 2026-10-07 明确要求建立 `main` 目录，后续以它为准，并清理重复、闲置的本地目录。

- 唯一主开发与集成入口：`/Volumes/Dujiangyan-VM/AI视频日记/repositories/main`。
- 分支：`main`；上游：GitHub `weitongxucc-dev/ai-video-diary-agent-studio` 的 `origin/main`。
- 建立时核实的产品代码基线：`584ccc362c0e03a7647d655f22014f30e75022b1`。后续最新提交仍以实时 Git/GitHub 为准，本合同不维护完成度副本。
- 本目录有独立 `.git`，不依赖 `original` 的 Git 目录，也不沿用旧目录的私有配置、用户数据库或依赖软链接。
- 产品、领域、架构和安全规则按 [文档路由](../docs/README.md)读取；未合入分支的代码与文档不自动成为本目录的产品权威。

## 当前执行边界

用户于 2026-10-07 在本会话指定 main 线程为产品主控，提供两条可见 Codex 执行线程，并明确要求“现在你来安排工作吧”。本次仅恢复下表具名范围；原主控和其他旧派工仍停工，历史授权不自动扩展到本批。

- 唯一主控：main 会话 `670d30a5-7031-4814-9b07-0c1ad334fb7f:a034d4bb-1ff5-4d1b-9aae-d599b402934b`；当前终端 `term_2a22cf6b-53e4-402c-969f-5365d6a15444`。负责共享文档、Linear、候选审核、Git 集成与 PR，不编写产品代码。
- 本批变更标识：`MAIN-RESUME-20261007-01`。产品代码基线 `584ccc362c0e03a7647d655f22014f30e75022b1`；主入口本地说明基线 `b4dd823a5ca05c21c0dc8429eab8c9ee37e11425`。新合同由两条线程读取本文件并语义 `REBASE_ACK`；ACK 满足且无未解决范围冲突后可直接开始已授权动作，无需第二次 START。
- 事实源：当前 main/候选 Git、Linear team WIL 的项目「曜核 0.11｜桌面端完整闭环」、实际测试与普通 Chrome 页面。历史[暂停交接](pause-handoff-20261007.md)仅提供保留成果的位置。
- 不整体 merge/rebase 旧混合开发分支，不覆盖脏树。执行线程不得 push、建/合 PR、写 Linear 或共享权威；本地仅提交自己已验证的具名差额。主控可准备独立 PR，main 产品代码只走 PR，不自动合并。
- 不访问真实用户库、真实密钥/登录钥匙串，不激活云身份/凭据，不下载或运行模型、不收费、不发布/签名/Release、不迁移真实数据、不删除保留工作树/stash。定时 automation 与 scheduled 生产始终 OFF。
- 不派内部子智能体，不新增执行窗口；一文件一写者。彼此可通信，但不得转授写权或替主控改变范围。

## 本批线程、所有权与验收

| 线程 | 工作树与身份 | 当前任务 | 写入边界 |
| --- | --- | --- | --- |
| A / manta | `/Users/Zhuanz/orca/workspaces/main/manta`；`68244901-fc27-4dbd-9381-cc158d3ba9dc:942aa671-d4b4-48cc-925c-cd9e94a383d7`；`term_79a48d73-cb24-4736-a0c2-4e995483ea88` | WIL-110 CI 收尾；独立候选审核后再绑定 WIL-108 | 仅下文 WIL-110 owned files 与两项直接消费者测试；共享 main/schema/client/types 本阶段由 A 独占 |
| B / pipefish | `/Users/Zhuanz/orca/workspaces/main/pipefish`；`57ae4a00-4977-4585-8947-5223d803c1c8:83f3ee5f-3d33-49b6-9e03-2cf08374cc2c`；`term_2ee18b71-c3c8-4e56-b551-5f5a765c4298` | WIL-110 隔离人工验收交接准备；用户实际操作后才核验 | 产品源码、仓库文档和原有用户数据只读；只写自己 `/tmp/main-acceptance-20261007/` 下隔离准备与报告 |

本轮有界修订 `MAIN-CONTINUE-20261007-02`：用户已明确“可以，你执行吧”，并愿协助浏览器事项。A 以已交 WIL-110 候选 `9645d70e0eb94b6dea639b4de5dc28b6034e25e7` 收尾 PR #90 的实际 CI 失败，新增 owned tests 为 `apps/api/tests/test_production_foundation.py` 与 `test_video_templates.py` 的本单直接消费者夹具/断言；不得弱化可信身份、扩张匿名诊断空间或把私有内容改共享以消除失败。先最小复现，区分生产回归与旧测试身份前提，再一次必要专项。B 定向读取该冻结候选或主控后来指定的修复候选，只在自有临时目录准备服务和合成测试数据，提供用户人工 GUI 操作的准确 URL、短步骤、PID/端口及停止方法；这不是代理更换浏览器工具执行被阻断动作。B 不进行导航/点击/键盘操作、不更改策略。用户未实际操作前仅为 readiness，不能宣称 GUI 通过；用户选择暂缓时停止自有服务并保留报告。A/B 阅读本段后语义 ACK 即可开始本轮任务，WIL-108 仍需独立派工。

### A：WIL-110

目标：在当前 main 产品基线上承接旧 `codex/wil110-knowledge-grants@8fc402a` 的知识授予/撤销净差额，保留主线已合 WIL-159 与 WIL-119。旧树仅定向只读；不恢复整个旧分支，也不搬运已在主线的成员许可文件。

Owned files：`apps/api/app/source_repository.py`、`sources.py`、`knowledge_files.py`（仅必要的固定版本授权消费者）、`knowledge_grants.py`、`knowledge_grant_routes.py`、`main.py`（仅本单挂载与直接读取消费者）、`schemas.py`（仅本单字段）；`apps/api/tests/test_knowledge_grants.py`、`test_knowledge_grant_routes.py`、`test_knowledge_grant_revocation.py` 及已有本单直接消费者专项；`apps/web/src/app/knowledge/KnowledgeWorkspace.tsx`、同名 CSS、该目录本单直接消费者测试、`apps/web/src/lib/api.ts`、`types.ts`（仅本单差额）。未列路径先报主控，不能扩写身份、调度、production、检测算法或全局 UI。

权威绑定：[领域 §10/§11](../docs/domain-model.md#10-核心不变量)、[安全 §2/§4/§9](../docs/auth-workspace-boundary.md#9-主账号邀请与共享授权)、[产品 §6.1](../docs/product-requirements.md#61-知识库)、[设计 §5.3](../DESIGN.md#53-知识库)。同时读取 main 根 AGENTS 与直接涉及的架构边界，不复制整套文档到工作树。

行为与验收：有效授权统一约束列表、详情、检索/助手、固定版本和新引用；撤销阻断新读取，保留原空间/所有者/private 与旧版本出处。没有真实同设备目标事实源时明确不可用，拒绝请求且零 grant 落库；local 如实标注 `same_workspace`，不造成员空间绑定，不把同空间夹具当跨空间通过。跨 workspace 真实 E2E 按既有用户裁定继续 UNVERIFIED。

最小验证：完整承接后一次本单仓储/路由/固定版撤销和知识页直接消费者专项；当前 main 的 WIL-159 四文件不回退，WIL-119 检测模块不丢失；不重复旧全量 137/23，不因旧分支中间态扩大范围。凡导入 main，必须事先使用隔离 HOME/XDG、数据库、配置与产物根，禁止裸导入触碰真实数据。GUI 由 B 在本候选冻结并具名交接后串行验证，缺证据不能 Done。

回报：净差额文件、准确候选 SHA/diff、最小实际测试、权限/撤销结果、保留主线内容证明、GUI/E2E 未验项、自有 PID/端口释放。不得自行开始 WIL-108。

### A 下一单：WIL-108（排队，未派工）

复用旧模块 `20df87f` 和 stash `wil108-wiring-wip-paused-20261007`，不改旧树/stash。完成真实触发、持久状态/结果、API 响应、页面展示与片段定位、重生成后旧结论失效；失败/不可用绝不显示无问题。只消费已在 main 的 WIL-119 冻结检测契约，不重写算法。待 WIL-110 候选审核后，由主控绑定独立分支/准确基线及 owned files；不将两单混成一个 PR。

下一单具名绑定 `MAIN-WIL108-20261007-03`：用户已批准上述继续顺序，主控仅在 PR #90 修复候选检查收口后发起新 Dispatch。A 先确认现有 WIL-110 `397a56f311fdc1962abe345ae23aef6f00a20568` 已保存且工作树干净，再在 manta 新建 `codex/wil108-main-20261007`，准确基线为当前 `origin/main` 产品提交 `584ccc362c0e03a7647d655f22014f30e75022b1`。保留旧分支与独立 PR #90；WIL-108 PR 只含本单差额，未来主控按 110→108 审核集成，不整体恢复旧混合分支。

Owned files：`apps/api/app/quality_inspection.py`（旧模块的必要适配）、`main.py`（本单触发/查询和直接失效消费者）、`schemas.py`（本单请求/结果）；`apps/api/tests/test_quality_inspection.py`、`test_quality_inspection_api.py`；`apps/web/src/lib/api.ts`、`types.ts`（本单字段）、`apps/web/src/components/CreationTaskWorkspace.tsx`、同名 CSS、`apps/web/src/components/__tests__/CreationTaskWorkspace.test.tsx` 和本单直接专项。其他路径先报主控；`quality_checks.py` 的 WIL-119 算法、production/身份/调度与共享文档只读。不改 WIL-110 分支、旧树或 stash。

权威绑定：[产品 §9.3/§11](../docs/product-requirements.md#93-精确编辑)、[领域 §6/§8.3](../docs/domain-model.md#6-时间线与产物)、[设计 §5.5](../DESIGN.md#55-创作任务与视频剪辑)、[架构 §3.10](../docs/system-architecture.md#310-qualityevaluationservice)。结果只能来自当前任务有权访问的成片与真实检测，Web 不提交任意文件路径；沿用既有可信成员/空间边界。检查中、失败、无问题、发现问题、不可用与旧版本失效须分别可见；问题时间区间可定位当前播放器片段，结果持久回读。新版本/新成片不能沿用旧“无问题”结论。模型建议不伪装确定性媒体结果；既有预算与制作准入边界保持。

最小验证：隔离导入与数据库下的流程/路由/状态/版本失效和直接 UI 专项；短合成媒体经真实 WIL-119 检测至少证明实际问题能进入本单流程及定位，mock 清单不能代替此项。不运行模型、真实用户库、真实制作或扩展全量检测算法测试。B 在候选冻结后另行准备人工普通页面核验，组件检查不替代 GUI；缺环境仅标对应项未验。报告独立 SHA/net diff、实际检测与持久结果证据、权限/旧版本失效、GUI 未验项和自有资源释放，禁止自行 push/PR/Linear/扩权或启动下一单。

### B：环境与 WIL-100 验收

先核实当前桌面 Chrome 的合法访问能力与历史“请求头策略加载失败”的真实错误/原因。不得重试未改变的已拒绝路径，不换表面绕过安全或 URL 策略；无新证据的同根因两次失败后停止该路线。可按版本匹配 computer-use/orca-cli 的只读发现确认工具、窗口、权限与错误，不擅自改浏览器策略/权限/安全配置。

确有可用且未被拒绝的验收路线时，先完成第零权限/窗口/无害点击/键盘探针；成功后仅对冻结 `584ccc3` 准备最小隔离合成素材，不读真实用户库，不运行分析模型。真实页面完成 WIL-100：A/B 各自说明、公共背景只作用于所选素材、另一素材不变、刷新/重进后的回读与失败语义。保存动作来自普通 UI；API/SQLite 只能只读核对结果。HTTP 就绪/fixture 不算 GUI 通过。

直接权威：[产品 §7](../docs/product-requirements.md#7-素材智能)、[领域 §11](../docs/domain-model.md#11-设备成员共享和素材纠正对象)、[设计 §5.2](../DESIGN.md#52-素材库)、[开发 §4.1/§7](../docs/development-workflow.md)。测试根 `/tmp/main-acceptance-20261007/`；有服务时记录准确 PID/端口和释放证据，重型环境串行，不占用 A 自有服务。

若环境不可用，交精确首个错误、工具/策略来源、已尝试路线、最小解除条件及未验清单；该报告可完成本次诊断任务，但不关闭 WIL-100 或其他产品卡。后续 128/199/157/165/125 逐单派发，不广测；197 已跳过的字幕下载确认不重试，159 未标定硬拒绝不放宽，134 的 Done/GUI 记录差异仅报主控，不自动改状态。

## 保留与退役边界

- `original`：保留用户未提交内容、旧工作树的共享 Git 历史及本次仍在使用它的会话。
- `bd25`：保留未合入的 `codex/0.11-development` 文档、交接、未跟踪内容及占用它的会话；它不再是默认主开发或集成入口。
- `wil108-clip-inspection`、`wil110-knowledge-grants`：保留未合入代码、WIL-108 本地接线 stash 及可见执行者的停工现场，不因主目录整理而删除或自动集成。
- 旧 CI、主控交接同步、权限验收、质量检测旧候选及音量修复目录：仅在 Git 提交、未提交内容、忽略文件、软链接和进程依赖已核实，且完整归档校验通过后移除目录；恢复信息保存在仓库外的本地归档。

归档不进入本仓库活动文档、检索或默认启动路径。旧目录仍被会话占用、支撑其他工作树或含未合入工作时，继续保留；后续退役须重新核实这些条件。
