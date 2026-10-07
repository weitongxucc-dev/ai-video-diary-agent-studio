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
- 事实源：当前 main/候选 Git、Linear team WIL 的项目「曜核 0.11｜桌面端完整闭环」、实际测试与具名真实页面；浏览器表面按实际工具和人工操作分别记录。历史[暂停交接](pause-handoff-20261007.md)仅提供保留成果的位置。
- 不整体 merge/rebase 旧混合开发分支，不覆盖脏树。执行线程不得 push、建/合 PR、写 Linear 或共享权威；本地仅提交自己已验证的具名差额。主控可准备独立 PR，main 产品代码只走 PR，不自动合并。
- 不访问真实用户库、真实密钥/登录钥匙串，不激活云身份/凭据，不下载或运行模型、不收费、不发布/签名/Release、不迁移真实数据、不删除保留工作树/stash。定时 automation 与 scheduled 生产始终 OFF。
- 不派内部子智能体，不新增执行窗口；一文件一写者。彼此可通信，但不得转授写权或替主控改变范围。

## 本批线程、所有权与验收

| 线程 | 工作树与身份 | 当前任务 | 写入边界 |
| --- | --- | --- | --- |
| A / manta | `/Users/Zhuanz/orca/workspaces/main/manta`；`68244901-fc27-4dbd-9381-cc158d3ba9dc:942aa671-d4b4-48cc-925c-cd9e94a383d7`；`term_79a48d73-cb24-4736-a0c2-4e995483ea88` | 候选保留待审核，无当前产品写权 | 分发与 WIL-100 候选、旧报告只读 |
| B / pipefish | `/Users/Zhuanz/orca/workspaces/main/pipefish`；`57ae4a00-4977-4585-8947-5223d803c1c8:83f3ee5f-3d33-49b6-9e03-2cf08374cc2c`；`term_2ee18b71-c3c8-4e56-b551-5f5a765c4298` | WIL-100 修正候选的失败路径补验，绑定 MAIN-GUI100RETRY-20261007-09 | 产品与共享权威只读；仅下文新临时目录与新 isolated 页面 |

本轮有界修订 `MAIN-CONTINUE-20261007-02`：用户已明确“可以，你执行吧”，并愿协助浏览器事项。A 以已交 WIL-110 候选 `9645d70e0eb94b6dea639b4de5dc28b6034e25e7` 收尾 PR #90 的实际 CI 失败，新增 owned tests 为 `apps/api/tests/test_production_foundation.py` 与 `test_video_templates.py` 的本单直接消费者夹具/断言；不得弱化可信身份、扩张匿名诊断空间或把私有内容改共享以消除失败。先最小复现，区分生产回归与旧测试身份前提，再一次必要专项。B 定向读取该冻结候选或主控后来指定的修复候选，只在自有临时目录准备服务和合成测试数据，提供用户人工 GUI 操作的准确 URL、短步骤、PID/端口及停止方法；这不是代理更换浏览器工具执行被阻断动作。B 不进行导航/点击/键盘操作、不更改策略。用户未实际操作前仅为 readiness，不能宣称 GUI 通过；用户选择暂缓时停止自有服务并保留报告。A/B 阅读本段后语义 ACK 即可开始本轮任务，WIL-108 仍需独立派工。

### A：WIL-110（候选保留，本阶段写权收回）

WIL-110 独立候选固定于 `afd1620c3aaec02727443442146d003539750144` 与 PR #90；本阶段 A 不再修改该分支或该任务的专属代码。后续发现问题由主控重新派具名修复，不从本节旧 owned files 取得继续写权。

目标：在当前 main 产品基线上承接旧 `codex/wil110-knowledge-grants@8fc402a` 的知识授予/撤销净差额，保留主线已合 WIL-159 与 WIL-119。旧树仅定向只读；不恢复整个旧分支，也不搬运已在主线的成员许可文件。

Owned files：`apps/api/app/source_repository.py`、`sources.py`、`knowledge_files.py`（仅必要的固定版本授权消费者）、`knowledge_grants.py`、`knowledge_grant_routes.py`、`main.py`（仅本单挂载与直接读取消费者）、`schemas.py`（仅本单字段）；`apps/api/tests/test_knowledge_grants.py`、`test_knowledge_grant_routes.py`、`test_knowledge_grant_revocation.py` 及已有本单直接消费者专项；`apps/web/src/app/knowledge/KnowledgeWorkspace.tsx`、同名 CSS、该目录本单直接消费者测试、`apps/web/src/lib/api.ts`、`types.ts`（仅本单差额）。未列路径先报主控，不能扩写身份、调度、production、检测算法或全局 UI。

权威绑定：[领域 §10/§11](../docs/domain-model.md#10-核心不变量)、[安全 §2/§4/§9](../docs/auth-workspace-boundary.md#9-主账号邀请与共享授权)、[产品 §6.1](../docs/product-requirements.md#61-知识库)、[设计 §5.3](../DESIGN.md#53-知识库)。同时读取 main 根 AGENTS 与直接涉及的架构边界，不复制整套文档到工作树。

行为与验收：有效授权统一约束列表、详情、检索/助手、固定版本和新引用；撤销阻断新读取，保留原空间/所有者/private 与旧版本出处。没有真实同设备目标事实源时明确不可用，拒绝请求且零 grant 落库；local 如实标注 `same_workspace`，不造成员空间绑定，不把同空间夹具当跨空间通过。跨 workspace 真实 E2E 按既有用户裁定继续 UNVERIFIED。

最小验证：完整承接后一次本单仓储/路由/固定版撤销和知识页直接消费者专项；当前 main 的 WIL-159 四文件不回退，WIL-119 检测模块不丢失；不重复旧全量 137/23，不因旧分支中间态扩大范围。凡导入 main，必须事先使用隔离 HOME/XDG、数据库、配置与产物根，禁止裸导入触碰真实数据。GUI 由 B 在本候选冻结并具名交接后串行验证，缺证据不能 Done。

回报：净差额文件、准确候选 SHA/diff、最小实际测试、权限/撤销结果、保留主线内容证明、GUI/E2E 未验项、自有 PID/端口释放。不得自行开始 WIL-108。

### A：WIL-108（候选保留，本阶段写权收回）

本阶段撤销本节所有产品写权，保留候选与已有证据。下列原具名实施边界只解释保留成果，不能据此继续修改、重启任务或开始下一单；新任务只从上表及后续具名合同取得授权。

复用旧模块 `20df87f` 和 stash `wil108-wiring-wip-paused-20261007`，不改旧树/stash。完成真实触发、持久状态/结果、API 响应、页面展示与片段定位、重生成后旧结论失效；失败/不可用绝不显示无问题。只消费已在 main 的 WIL-119 冻结检测契约，不重写算法。待 WIL-110 候选审核后，由主控绑定独立分支/准确基线及 owned files；不将两单混成一个 PR。

下一单具名绑定 `MAIN-WIL108-20261007-03`：用户已批准上述继续顺序，主控仅在 PR #90 修复候选检查收口后发起新 Dispatch。A 先确认现有 WIL-110 `afd1620c3aaec02727443442146d003539750144` 已保存且工作树干净，再在 manta 新建 `codex/wil108-main-20261007`，准确基线为当前 `origin/main` 产品提交 `584ccc362c0e03a7647d655f22014f30e75022b1`。保留旧分支与独立 PR #90；WIL-108 PR 只含本单差额，未来主控按 110→108 审核集成，不整体恢复旧混合分支。

Owned files：`apps/api/app/quality_inspection.py`（旧模块的必要适配）、`main.py`（本单触发/查询和直接失效消费者）、`schemas.py`（本单请求/结果）；`apps/api/tests/test_quality_inspection.py`、`test_quality_inspection_api.py`；`apps/web/src/lib/api.ts`、`types.ts`（本单字段）、`apps/web/src/components/CreationTaskWorkspace.tsx`、同名 CSS、`apps/web/src/components/__tests__/CreationTaskWorkspace.test.tsx` 和本单直接专项。其他路径先报主控；`quality_checks.py` 的 WIL-119 算法、production/身份/调度与共享文档只读。不改 WIL-110 分支、旧树或 stash。

权威绑定：[产品 §9.3/§11](../docs/product-requirements.md#93-精确编辑)、[领域 §6/§8.3](../docs/domain-model.md#6-时间线与产物)、[设计 §5.5](../DESIGN.md#55-创作任务与视频剪辑)、[架构 §3.10](../docs/system-architecture.md#310-qualityevaluationservice)。结果只能来自当前任务有权访问的成片与真实检测，Web 不提交任意文件路径；沿用既有可信成员/空间边界。检查中、失败、无问题、发现问题、不可用与旧版本失效须分别可见；问题时间区间可定位当前播放器片段，结果持久回读。新版本/新成片不能沿用旧“无问题”结论。模型建议不伪装确定性媒体结果；既有预算与制作准入边界保持。

最小验证：隔离导入与数据库下的流程/路由/状态/版本失效和直接 UI 专项；短合成媒体经真实 WIL-119 检测至少证明实际问题能进入本单流程及定位，mock 清单不能代替此项。不运行模型、真实用户库、真实制作或扩展全量检测算法测试。B 在候选冻结后另行准备人工普通页面核验，组件检查不替代 GUI；缺环境仅标对应项未验。报告独立 SHA/net diff、实际检测与持久结果证据、权限/旧版本失效、GUI 未验项和自有资源释放，禁止自行 push/PR/Linear/扩权或启动下一单。

### B：WIL-108（证据保留，本阶段操作权收回）

本阶段不再启动本节页面或服务，不从本节取得新浏览器操作授权；保留已有证据和线程。新的 WIL-100 页面只遵守下文具名范围。

有界修订 `MAIN-GUI108-20261007-04`：用户已批准继续实施及浏览器事项，并指出 Orca 本身具有截图/Computer Use 能力；主控已对用户打开的内嵌页面实际取得 snapshot/截图。后续 WIL-108 仍需主控审核冻结候选后发新 Dispatch，本节不自行开始服务或验收。A 的代码合同与所有权不变。

本任务允许 B 创建一个新的 `scope=isolated` 浏览器 profile，仅供本任务使用；不得导入/克隆/清理用户已有 profile、cookie 或登录状态。使用版本匹配的 Orca 原生内嵌浏览器命令，仅对自己新建的隔离 `http://127.0.0.1:3117/` 标签执行 snapshot、截图和页面交互；必须指定准确 page ID，不操作或关闭用户已有标签。先在自有页面做无害交互探针，不修改工具/浏览器策略，不重试旧 `cua_repl` 请求头策略拒绝路径；若本路线收到策略拒绝，停止受影响操作并回报。这里是主控对新具名本地验收范围的调整，不声称旧工具策略恢复，也不能冒称普通 Chrome。

B 仅写自有 `/tmp/main-acceptance-20261007/gui-wil108/`，冻结代码 archive 和隔离 HOME/XDG/DB/config/library/output/cache 后导入；产品树与共享权威只读。只准备短合成视频、两片段和必要的任务/时间线/登记产物夹具；准备与 readiness 不等于 GUI 验收。真实页面点击检查、等待真实 WIL-119 结果、点击问题定位并只读观察播放器时间与选择、刷新回读；通过既有 UI 做一个最小时间线变更，核对旧结果失效。准备后的业务动作来自 GUI，API/SQLite 仅只读核对，不以直接业务写入替代失败交互。不能出现的瞬态或环境阻断逐项记未验，不用假延时或 mock 冒充。

服务端口为 API 4517 / Web 3117，记录自有 PID 归属和短时租约。不得真实制作、模型、云凭据、生产预算/调度放宽或访问真实用户数据。结束仅停止自有服务与测试标签，可清理本任务新建的 isolated profile，实际证明 PID/端口释放，保留用户浏览器。报告冻结 SHA、实际浏览器表面、可见截图、实际 UI 路径、只读持久证据、未验项和释放；不写 Linear/远端、不 Close 产品卡。

### A：交付关键路径只读评估

具名范围 `MAIN-READINESS-20261007-06`：用户于本会话明确“可以，你执行吧，另外你要给我一个评估，当前项目的完成度是多少了？我们的时间非常紧张了”，并明确本版本须“正式可用，可分发”，不要求估计最晚交付时间。A 定向只读核当前 main 产品提交 `43cd7e25fb5ce635888cb9d4cd70d7cd9c6e5252`，评估符合 0.11 产品范围、真实安装使用与项目 Release 门禁的可分发版本。演示与内部测试包证据可复用，但不替代该交付终点；不把实施单 Done、旧 CI 或静态接线当整体可交付，不将目标缩为演示版。

以[产品 §1.1/§14/§15.1](../docs/product-requirements.md#14-产品验收)、[桌面开发入口](../apps/desktop/README.md)、[发布门禁](../apps/desktop/RELEASE.md)与实际 workflow/精确目标代码核可执行入口，最多定位六项关键阻断及依赖、已有证据和缺少的真实验证。只读取本任务直接需要的当前文件及 GitHub 构建/Release 元数据；不读真实配置、密钥值、用户库、旧 output 或 archive，不下载产物、构建/签名、触发 workflow、起服务、调用模型或付费能力。只写自有 `/tmp/manta-project-readiness-20261007/`，不改仓库/Linear/远端/共享权威，不在该调查中修产品。主控负责实时 Linear 统计与最终百分比解释，A 不猜测百分比或完成时间。

交付具体文件/符号或 workflow 证据、能复用的结果、最短验证序列及未验项；发现可能阻断 WIL-100 的当前配置/消费者问题只发证据给 B 和主控，不授写权。读取本合同并语义 REBASE_ACK 后即可开始，无需二次 START。

### A：分发打包源码修正（候选保留，写权收回）

本阶段候选 `8b1b591c071e288c25e5f8a5c931918eeaa49659` / PR #92 保留，A 不再从本节取得产品写权；不在下一单混入两条 workflow。

具名范围 `MAIN-DISTRIBUTION-20261007-07`：用户确认 0.11 须正式可用可分发，主控已只读核实两条构建通道与当前桌面 API/Web 拓扑及账号分发规则不一致。本任务只准备必要源码差额与可审核 PR，不由“可分发”目标推导真实发布、凭据调用或签名授权。前一只读评估 Task 须先由主控接受并结束，再新 Dispatch；旧评估报告保持，只写它的后续证据目录。

从准确 `origin/main@43cd7e25fb5ce635888cb9d4cd70d7cd9c6e5252` 在 manta 建独立 `codex/desktop-distribution-workflows-20261007`，先核干净并保留 WIL-108/110 分支。Owned 仅 `.github/workflows/internal-macos-test.yml` 与 `release.yml`：前者移除已无构建/包内来源的旧 worker-server 检查，保留原生 app/API 架构、签名及健康 smoke；后者复用现有 desktop Web/API 构建和分发配置脚本，移除不存在的 worker.spec 输入与旧 Worker 步骤，显式账号模式与公开连接配置并在缺失时真实失败。保持双架构、tag/version 校验、五项签名凭据硬门禁、codesign/spctl/stapler、发布依赖及双 ZIP 更新清单。不改 runtime/auth/数据库/业务规则/预算/模型/版本号、产品名称或共享文档，不复活旧 Worker，不添加默认凭据、local 回退或伪成功。

绑定[架构桌面边界](../docs/system-architecture.md)、[认证 §1](../docs/auth-workspace-boundary.md#1-认证模式)、[桌面开发](../apps/desktop/README.md)与[Release 门禁](../apps/desktop/RELEASE.md)。账号配置只采用现有公开 URL/publishable key 变量入口，不读取真实值或将管理凭据引入包。Supabase 技能的真实云操作要求不能扩大本任务授权；本单只在隔离临时文件中验证公开配置生成与缺失拒绝，不查询实际数据库/账号。

最小验证：解析两条 YAML、核实际命令/脚本/产物引用、隔离配置生成成功及声明账号模式但缺少配置时失败，保留安全/架构门禁的差额核对与 diff check；不写镜像实现的测试、不全量复测产品。允许复用已有工具/依赖，不新安装。不得触发 internal/release workflow、打 tag、签名、公证、构建/下载安装包、发布、改 GitHub 配置/密钥、读取真实配置/钥匙串/用户库或调用云账号/模型。不能在本轮取得的安装、账号制作、媒体/性能、签名公证实证明确未验，不声称正式可分发已经通过。需要其他源码变更仅报主控，不能扩写。交付独立 SHA、两文件 diff、实际最小检查、失败/未验边界；A 不 push/PR/Linear，由主控审核提交独立 PR 并按原合同请求具名合入批准。B 的冻结 43cd7e2 页面与业务验收不受本 workflow 差额影响，无需重验未变化产品。

### A：WIL-100 保存失败原因局部修正（候选保留，写权收回）

候选 `22446607d11c88d5cca6051c98ee36127f0d00a9` 已移交，本阶段不从本节继续修改；主控审核与新的 B 验收使用该冻结候选。

具名范围 `MAIN-NOTEERROR-20261007-08`：当前真实隔离页面停止 API 后，保存请求收到 502、代理 detail 为 `API server unreachable`；素材页丢弃英文原因，只显示泛化保存失败。用户已批准执行 WIL-100，验收仍要求失败原因可见与草稿保留。本修正不改变产品目标或接口。上一分发 Task 已结束并保留候选，再对同一可见 A 线程发新 Dispatch。

从产品 `43cd7e25fb5ce635888cb9d4cd70d7cd9c6e5252` 建独立 `codex/wil100-save-error-20261007`；Owned 仅 `apps/web/src/components/MaterialLibraryWorkspace.tsx` 和 `apps/web/src/components/__tests__/MaterialLibraryWorkspace.test.tsx`。基于真实 502/已知 detail 展示普通中文服务连接原因与可执行重试提示，保留草稿、既有中文服务端原因和真实保存回读；不得猜测所有失败为连接故障，不显示原始英文技术错误，不吞错、不改 API/client/middleware、身份、持久层、CSS、配置或共享权威。绑定 [设计 §2.5/§5.2](../DESIGN.md#25-功能命名) 和本单 Linear 现行判据。

仅补最小现有组件专项证明真实返回类型/已知 detail 对应的可见原因、草稿与无成功提示，并复用已过独立说明/公共背景等证据；不重跑全量、不安装新依赖。产品落地只由主控审核独立 PR 并请求具名合入批准。A 不 push/PR/Linear，不启动服务、浏览器或真实制作，不访问用户数据/凭据；自有报告只写 `/tmp/manta-wil100-save-error-20261007/`。GUI 由 B 原任务结束、主控冻结新候选后另行具名，A 不以组件测试代替页面通过。须核干净、保留旧候选，读取本段并语义 REBASE_ACK 后开始。

### B：WIL-100 冻结 main 的隔离页面验收（证据保留，操作权收回）

旧任务已结束并释放自有资源，本节只解释冻结证据，不取得新服务或页面操作权。后续只按下文新绑定补验。

有界修订 `MAIN-GUI100-20261007-05`：上述用户继续指令批准本单验收。冻结代码为 `43cd7e25fb5ce635888cb9d4cd70d7cd9c6e5252`，不沿用旧 `584ccc3` 服务或现存数据库。允许新建一个 `scope=isolated` Orca 内嵌浏览器 profile，仅对本任务新建的 `http://127.0.0.1:3127/` 标签导航、snapshot/截图、点击和键盘输入，始终指定准确 page ID；不导入/克隆/清理用户已有 profile/cookie/标签。先做自有页无害探针；旧 cua_repl 请求头策略路线仍禁止重试，不修改策略或权限，不声称其恢复；本路线如被策略拒绝，停止受影响操作并报告。

只写自有 `/tmp/main-acceptance-20261007/gui-wil100/`。从冻结 Git 对象 archive 且排除 dotenv，在导入前显式隔离 HOME/XDG/DB/config/library/output/cache/model/temp 与真实钥匙串；准备短合成素材 A/B/C、正常本地可信 owner 和合法登记记录，不启动分析模型/制作。初始数据只能用于准备；随后保存、切换与业务修改必须来自 GUI，API/SQLite 只做 GET 和 `mode=ro` 核对，不用直接业务写入替代失败操作。

真实页面完成：A/B 分别填写并保存不同整条素材说明；只选 A/B 保存公共背景，C 与两份独立说明保持；切换、刷新及离开重进后正确回读；保存空说明合法。在自有页面保留编辑草稿，短暂停止本任务自有 API 后通过 GUI 点击保存，再恢复同一隔离配置/数据库的自有 API，验证错误原因、草稿保持和恢复重试（不改浏览器策略），不能造成虚假成功。不能合法制造的失败项明确未验，不用 API/组件结果冒称 GUI。既有仓储/API/组件证据复用，不重跑全量。发现真实产品缺陷仅报告具体步骤、当前事实与最小 owned files 建议，产品代码写权仍为零。

直接权威：[产品 §7](../docs/product-requirements.md#7-素材智能)、[领域 §11](../docs/domain-model.md#11-设备成员共享和素材纠正对象)、[设计 §5.2](../DESIGN.md#52-素材库)。API4527/Web3127 启动前核空闲，具名自有 PID/PGID 与短时租约；结束只停止自有进程、测试标签/profile，实际证明 PID 与端口释放，保留用户浏览器。报告冻结源码身份、可见截图、UI 保存路径、只读持久对应、未验项和释放；不写 Linear/远端、不关闭产品卡。读取本合同并语义 REBASE_ACK 后即可开始。

后续 128/199/157/165/125 逐单派发，不从本单获得操作权；197 已跳过的字幕下载确认不重试，159 未标定硬拒绝不放宽，134 的 Done/GUI 差异仅报告。WIL-110 与 WIL-108 本阶段均保留既有成果，不取得新写权。

## B：WIL-100 修正候选失败路径补验

具名范围 `MAIN-GUI100RETRY-20261007-09`：原 B Task 已结案并释放全部自有 PID、4527/3127 与两组 page/profile；已通过独立说明、所选公共背景、C 不变、切换/刷新/重进及空说明证据继续保留，只补原标准 4 的失败原因缺口及修正后的恢复重试。产品冻结 A 独立候选 `22446607d11c88d5cca6051c98ee36127f0d00a9`，准确父为 `43cd7e25fb5ce635888cb9d4cd70d7cd9c6e5252`；差额仅素材组件一句已知错误翻译与既有专项。不得混入分发候选或操作旧测试页。

只写自有 `/tmp/main-acceptance-20261007/gui-wil100-retry/`，旧 gui-wil100/fixture-2 仅定向只读复用合成素材与准备方法，原证据/DB不修改；在新根准备独立合法素材 DB、可信 local owner 和隔离配置。archive 排除 dotenv，导入前 HOME/XDG/DB/config/library/output/cache/model/temp/钥匙串隔离，CPU 未标定拒绝保持。先核自有端口 API4527/Web3127 空闲，记录 PID/PGID/30分钟租约。新建 scope=isolated Orca 内嵌 profile/page，禁止 clone 用户或旧 profile；准确 page ID、无害探针、native点击/键盘/snapshot/截图，eval只读DOM。旧 cua_repl 策略路径继续禁止重试，本路线如策略拒绝则停止该项。

实际 GUI 在 A 说明框输入不同草稿，仅停自己的 API，点击保存取得真实502；截图显示中文本地服务连接原因和可执行重试、草稿保留及无成功。停止阶段 SQLite 只以 mode=ro/uri=True 确认未写入，native network只读观察状态且不存header/token。恢复同一环境与DB API，只通过 GUI 重试，截图成功和错误消失；刷新回读新A说明，GET/DB只读核对A保存与B/C不受影响。初始准备之后不得 API/SQL 业务写入，不造错误/延时或直接修改状态。其他已通过路径不重验，不跑全量、不调用模型/制作/真实账号/用户数据/发布。

回报准确 Git 字节/变更复用边界、实际可见与持久证据、失败/未验项；最终仅释放新任务自有服务/lease/PID与新page/profile，证明4527/3127空闲，保留用户浏览器和旧证据。产品代码、共享文档、Linear与远端均无写权。读本段并语义 REBASE_ACK 后开始；未经过主控接受前不把本单 Done，也不从此取得128/199或发布/设备测试权限。

## 保留与退役边界

- `original`：保留用户未提交内容、旧工作树的共享 Git 历史及本次仍在使用它的会话。
- `bd25`：保留未合入的 `codex/0.11-development` 文档、交接、未跟踪内容及占用它的会话；它不再是默认主开发或集成入口。
- `wil108-clip-inspection`、`wil110-knowledge-grants`：保留未合入代码、WIL-108 本地接线 stash 及可见执行者的停工现场，不因主目录整理而删除或自动集成。
- 旧 CI、主控交接同步、权限验收、质量检测旧候选及音量修复目录：仅在 Git 提交、未提交内容、忽略文件、软链接和进程依赖已核实，且完整归档校验通过后移除目录；恢复信息保存在仓库外的本地归档。

归档不进入本仓库活动文档、检索或默认启动路径。旧目录仍被会话占用、支撑其他工作树或含未合入工作时，继续保留；后续退役须重新核实这些条件。
