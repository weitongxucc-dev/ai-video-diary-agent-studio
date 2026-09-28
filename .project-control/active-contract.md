# 0.11 桌面闭环执行合同

## 当前授权与使用方式

ACTIVE。本文件只保留当前协作边界；已结束批次的指令和旧基线从活动入口移除，历史由 Git、PR、Linear 追溯。不得用历史提交号把现有工作树回退或覆盖。

- **主控切换登记（2026-09-28 08:03）**：用户明确指令"由你担任主控"，唯一主控改为 cmux workspace:1 / surface:5 的 Claude Code 会话（glm-5.3 high）。原 Codex 主控因额度耗尽停止派单/集成；其额度恢复后不自动恢复主控权，如回归仅作普通执行者。实施执行者现为 cmux Pi（surface:1）与 cmux Claude Code（surface:2）；下表 Codex 线程为历史登记，按包逐步改派并在本文件同步，改派前既有候选 SHA 与文件独占仍有效。WIL-72 阶段包自本登记起改派 surface:2 Claude Code：接手前先核 fc12 工作树未提交改动（main.py/task_audio.py/测试，+90/−9，为原 MAKE 针对两项 QA HOLD 的未完修复），先审后续，不得未审丢弃。

**2026-09-28 08:20 增派登记**：用户增开两线程。surface:6（OpenCode/DeepSeek V4 Flash）派只读分析包：v4 原型与现有组件逐页映射差距清单（WIL-55 范围梳理），唯一写入口为主控项目 `external-agents/WIL-55/v4-page-mapping.md`，不改仓库任何文件、不建分支。surface:7（Claude Code/glm-5.1）派 WIL-67 后端身份切片包：按 `work/invite-first-identity-wiring.md` 第 1 条，在 auth.py 增加仅验证 Bearer JWT 的身份方法并新增专项测试；新工作树 `~/.codex/worktrees/auth-identity/original`，分支 `codex/011-auth-first-identity`，基线 60cd74e。auth.py 于本包期间由 surface:7 独占；main.py 窄入口与 web 放行路径待 WIL-72 交还 main.py 后另派，本包只交接口建议。派发契约存 `external-agents/dispatch/WIL-67-auth-identity-slice.md`；Claude Code 终端长消息会丢行，后续派发统一走 dispatch 文件+短指令。

- 主控只负责共享文档、派工、审核、集成与开发分支推送，不编写产品代码。
- 实施使用用户授权的侧边栏可见 Codex 线程及 cmux Pi/Kimi、Claude Code/GLM，不派内部子智能体。保留现有模型配置。
- 集成仓库：`/Volumes/Dujiangyan-VM/AI视频日记/repositories/bd25`；分支 `codex/0.11-development`。每包基线由主控按实际 Git 给出；仅吸收审核通过的具体依赖。开发分支可推送，不自动合并 main。
- 不授权新增付费调用、真实平台发送/发布、签名、公证、真实用户库迁移或覆盖已安装版本。验证使用隔离数据；保护未跟踪记录和其他工作树的未提交修改。
- 长期权威从[文档路由](../docs/README.md)按需读取；完整需求和 Linear 映射见[开发台账](0.11-development-plan.md)。本合同不维护完成率或 CI 快照。

## 文件所有权

下表是当前所有权，不是要求重新执行已结束工作。新包须给出结果、精确文件、依赖和退出条件；同一文件不得多人同时写入。未列出的媒体叶子文件不得因接线需要自动扩权。

| 负责人 | 可见线程 | 所属文件或目录 |
| --- | --- | --- |
| MAT01 素材工程 | 01a0d259-54c3-7621-b055-17ce9146cac4 | apps/api/app/production.py、material_ingestion.py、material_probe.py及对应专项测试 |
| MAKE01 制作流程 | 01a0d259-67ee-75f1-ab83-8982eec022c7 | apps/api/app/main.py、creation_execution.py、schemas.py、assistant_pi.py、apps/api/agent_tools/diary_tools.ts；apps/web/src/components/CreationTaskWorkspace.tsx、src/lib/api.ts、types.ts及相邻专项测试 |
| UI01 桌面实现 | 01a0d259-8b42-7d30-82c5-8834f31842e5 | AppShell、EditorShell.module.css、VideoEditor、MaterialLibraryWorkspace、HomeWorkspace、KnowledgeWorkspace、ExecutionQueueStatus、useEditorPanels及相邻样式/测试；knowledge/page.tsx、loading.tsx；modern-desktop-contract-check.mjs |
| SCHED01 整机调度 | 01a0d29d-226b-7460-8ba6-76f14dc8c56a | device_scheduler.py、device_runtime.py、host_resource_probes.py、granted_subprocess.py及专项测试；database_migrations.py新增调度迁移；api pyproject/uv.lock仅已批准psutil依赖 |
| DESIGN01 界面设计 | 01a0d25f-5080-7991-9e9c-54efde64d4e9 | 自己工作树的 designs/desktop-011/，不写生产组件和DESIGN.md |
| AUTH01 账号共享 | 01a0e1c4-23e1-71b1-b23a-22ea2993e7ef | 账号共享接口及当前实施包明确授权的新增服务/测试/Supabase迁移 |
| QA01 独立验收 | 01a0d25f-53ad-7a83-8197-16f9d0a17aa2 | 自己工作树的 work/qa-011/ 隔离证据；不修改产品代码或共享规则 |

后端省略目录的文件位于 apps/api/app/；前端组件位于 apps/web/src/components/。cmux 当前临时接管的文件以下表为准，原负责人不得同时改。Repository接口交MAT，main和任务父层交MAKE，DDL交SCHED；主控裁决跨域变更。

## 调度与交付边界

- 单个本地重活许可、可信成员和对象授权、真实进程释放是生产接线前提；缺资源或身份事实时不能自行放行。
- 素材元信息探测完成不代表场景、语音、视觉理解完成，不把资产提前标为可用。scheduled手动重试须真实接通授权入口后才能宣称可用。
- 制作队列和状态展示可先集成；尚无受许可媒体执行器时保持dependency，不能把排队接线称为真实连续制作。startup门禁未完成前不启用scheduled生产运行。
- 取消请求不等于子进程已退出；确认进程树释放后才可让出许可。检查点与调度完成使用同连接事务；具体接口由当前代码及架构权威确定。
- 默认legacy与受控scheduled阶段并存期间，禁止同时启动两套重活驱动。全入口迁移仍需单独证明旧绕行路径退役。
- 组件、API、真实媒体、浏览器、安装包和外部模型分别验收；三尺寸为1440×900、1180×800、1024×768。局部通过不标记父范围完成。

## 2026-09-27 用户确认的整体界面实现基线

用户明确认可 `http://127.0.0.1:4327/workspace-dual-v4/owner.html` 为基本达到70分的整体方案。其主账号与 `member.html` 成员版本是桌面UI整体实现基线；不能仅修旧外壳浮卡而遗漏这版页面组织与视觉结构。主控已实际读取该原型入口，源目录由设计负责人提供并固定。

- 保留导航—持续创作助手—核心工作区三栏、可拖动分隔及收起、紧凑字号间距，以及右上精细修改浮卡。首页按这版总量、趋势、行业分布、任务阶段、可点词云、近期作品的组合布局落地；其他导航页逐页对照，不退回长条列表替代方案。
- DESIGN-V5只能在v4上修正已确认的九阶段、交互缺口、密度和权限等问题；未经用户确认不得另起整体布局。先交v4到组件与产品页面的映射、差异及受控改动，不以新原型替代真实组件交付。
- UI-FLOAT是整体方案落地的子项，其局部通过不代表整体UI通过。桌面负责人先给v4与现有组件逐页差距，主控据此分配具体页面；共享CreationTaskWorkspace/API仍由MAKE写，不得跨写。
- 原型的示例数据是布局与交互依据，生产页面接真实服务；缺失能力明确显示状态，不把示例数字或按钮点击当功能已实现。QA除尺寸检查外，要逐页比对已认可v4结构和交互，分开报告示例、真实组件和真实业务链路。


### UI-V4 第一实施包：外壳与首页

UI01独占本包的 `apps/web/src/components/AppShell.tsx`、`EditorShell.module.css`、`HomeWorkspace.tsx`、新增 `HomeWorkspace.module.css`、`useEditorPanels.ts` 及对应AppShell/HomeWorkspace/useEditorPanels专项测试。布局hook仅为v4默认导航188px、助手280px及必要最小宽度、拖动/收起/恢复一致性调整；已有用户手动保存宽度不得静默重置。浮卡修正与首页改动分开提交。不得写MAKE的任务父组件、API/types或后端。

首页按v4组合看板实现，可信现有数据可用，缺聚合接口保留对应结构与诚实空态；隔离组件展示可用显著标注的示例供两身份三尺寸验收。角色未接真实服务不得伪造管理权限。MAKE先提供同源聚合和九阶段接口提案，知识库、模板及其他页面后续逐包指派，不因本包未包含而从范围删除。

### UI-V4 第二实施包：知识库页面

UI01独占 `apps/web/src/app/knowledge/page.tsx`、必要时同步其 `loading.tsx`、新增 `KnowledgeWorkspace.tsx`、`KnowledgeWorkspace.module.css` 及该目录 `__tests__/page.test.tsx`。页面入口保留数据加载，展示组件独立以便隔离示例和测试，不从Next页面入口导出额外组件。按v4分类入口、来源封面网格及可操作详情实施；保留现有搜索、来源信息、原文和真实读端。用途分类不能从文件格式擅自推断，缺分类数据需明确未分类；没有真实引用/版本/纠正接口的操作不得伪造成功。原文及摘要只显示已有事实，不能编造解析、主题关系或问答出处。隔离示例可用于完整布局验收并明确标注。

本包不写 `modern-overrides.css`、API/types、后端或其他页面；旧全局选择器如确实阻断，先提交具体冲突选择器与局部解决证据。第一包与本包分开提交；知识库页面改造不代表知识解析、召回及权限业务完成。


## 当前实施包与依赖

| 执行者 | 当前结果与独占增量 | 退出条件 |
| --- | --- | --- |
| MAKE01 / WIL-65 | CPU成片渲染；在原范围上增加montage.py、audio_timeline.py、local_portrait.py及专项测试；GLM机械清理已收口，停止其这些文件写入 | 既有配音和已确认剪辑计划产出真实短片；所有子进程同grant，内层作业不再取许可；超时/取消/检查点恢复无重复渲染；不开放全局scheduled |
| SCHED01 / WIL-66 | 原host/runtime范围内为CPU渲染和MAT场景单元提供明确资源kind、预算和准入 | 与消费者约定时长/输入量/线程等实际限制，未知拒绝；真实采样和单许可，不冒用hash许可 |
| AUTH01 / WIL-67 | 新增apps/api/app/device_accounts.py、tests/test_device_accounts.py及独立Supabase新迁移；不写历史迁移、auth.py、source_repository.py、main或SQLiteDDL | 可信设备前提下邀请接受/撤销/过期/幂等和并发五人名额，owner占位，固定目标空间；隔离PG/RLS验证；真实设备注册根仍是交付缺口 |
| MAT01 | 在原范围上新增material_scene.py及专项测试；复用源时间区间的有界场景分析 | 镜头变化小样、真实定位、检查点/重启恢复；不跑完整ASR/VLM，不提前标资产ready；资源字段先与SCHED对齐 |
| UI01 | 新增DesktopVoiceRecorder.tsx、相邻module.css和组件测试 | 真实浏览器录音/停止/试听/重录/释放设备、诚实错误；onRecorded回调交MAKE接线，父层和API不跨写；摄像头/录屏不包含 |
| cmux Pi / WIL-70 | TemplateLibraryWorkspace.tsx、其专项测试，必要时自身module.css | 名称搜索、按视图区分空态、断线无旧模板操作；版本路由保留，专项验证交主控 |
| cmux Claude Code / WIL-71 | PublishingWorkspace.tsx及其专项测试 | 刷新失败与过期响应不展示假新状态，原因/下一步一致；不增加平台发布能力，专项验证交主控 |
| QA01 | 原隔离证据范围；审核上述包 | 按具体候选做必要复验，区分组件/真实媒体/产品闭环；失败只阻断对应包 |

cmux前一批生产叶子和测试规范清理已交付，旧临时写入许可结束；后续派包必须重新限定文件，不得继续沿历史消息写媒体文件。当前包基线由主控消息指定，不能从已结束批次推断。

推进规则：工作者完成首个可审提交后继续已授权剩余范围；依赖未到时完成独立部分并报告具体接口。主控接收后审核、合成、必要复验、推送、同步Linear。候选完成不等于整个版本完成，也不解除生产门禁。后台监控以实际自动化配置为准。


## 本人录音应用阶段包（WIL-72）

MAKE在现有main.py、schemas.py、audio_timeline.py、CreationTaskWorkspace.tsx、lib/api.ts及专项测试范围上，增加task_audio.py及其专项测试独占。基线c0ec4bc，UI录音组件46e7a90及提交竞态修复仍由UI交付，不跨写。当前整条旁白替换是R19阶段包，不取消首次无TTS录音、分段录音及真实导出验收。

复用任务音频持久化与既有编辑提案/确认/版本事务；服务端检查真实容器/时长、成员与任务归属、旧版本、锁定和时长约束。上传仅产生未应用素材，明确确认才替换旁白；不得add叠播冒充替换。人物口型、已分割旁白及无法同步的组合明确不可用。旧视频/依赖预览失效，失败不改变有效版本。探测/转换不得新增绕过统一许可的重活路径；scheduled无许可/预算时保持dependency，不开启生产。新录音进入生成路径后，预览/导出同源验证仍是父范围完成条件，本包不靠保存成功声称成片闭环。

**2026-09-28 增补**：本包执行者由 MAKE（Codex 线程，额度耗尽）改派 cmux Claude Code（surface:2），文件范围与全部门禁不变。当前基线 f70daa8 遭 QA 双 HOLD（① 媒体身份绑定：等长改样本仍 200 应用，禁 GET 无许可全文件 hash 掩盖；② 换旁白后受影响字幕须标复核/失效并处理 artifact_ref，保留人工文字、不误伤音量编辑），修复经独立复验通过前不可集成。

**2026-09-28 集成收口**：双 HOLD 修复候选 bdf686b（surface:2 交付）经主控 diff 亲审 + 干净树隔离复验（15 passed，含四具名证据测试）后已整链 cherry-pick 集成 dev（96af469→8de8641 … bdf686b→4d04d25，零冲突）并推送，WIL-72 转 In Review。遗留如实记录：unavailable 字幕轨前端呈现属 UI 包；真实用户闭环待"录音接父层"包。自本登记起 main.py 不再被本包独占，可派后续窄入口包（WIL-67 接线第 2 步，路由签名以 surface:7 报告的接口建议为准）。WIL-69 候选 6a107b2 经 surface:1 独立复核判可集成，附一行必修测试断言竞态（getByText→findByText），已派 surface:2 于 ui-v4-integration 工作树修复（派发契约 external-agents/dispatch/WIL-69-test-fix.md），新 SHA 到后主控集成。

**2026-09-28 无主 issue 改派登记（WIL-66/WIL-68）**：原 Codex 在做的 WIL-66（SCHED 资源预算）与 WIL-68（MAT 有界场景分析）无工人在做，改派 cmux 两线程。surface:9（Claude Code）派 WIL-66 SCHED 资源 kind 注册表与准入：工作树 `~/.codex/worktrees/sched-budget/original`，分支 `codex/011-sched-render-budget`，基线 9628441，独占 device_scheduler.py/device_runtime.py/host_resource_probes.py/granted_subprocess.py 及专项测试；禁 DDL/main.py/schemas.py/creation_execution.py/production.py/material_*/task_audio.py/apps/web/pyproject。surface:10（pi）派 WIL-68 MAT 有界镜头切分与恢复：工作树 `~/.codex/worktrees/mat-scene/original`，分支 `codex/011-mat-scene-bounded`，基线 9628441，独占 material_scene.py + test_material_scene.py（纯新增）；禁 material_ingestion.py/production.py/material_probe.py/device_*/main.py/DDL/apps/web。跨包接口契约：MAT 的 SceneProbeBudget(cpu_threads, max_source_duration_s, sampling_fps) 字段语义与 SCHED 注册表一致（cpu_threads 整型 / wall_clock_s 墙钟秒），互不 import 对方文件。派发契约存 external-agents/dispatch/WIL-66-sched-budget.md 与 WIL-68-mat-scene.md。

**2026-09-28 四包集成收口（WIL-66/67/68/69）**：五个无主/待修线程全部交付，主控已整链 cherry-pick 集成 dev 并推送、同步 Linear In Review。WIL-68（surface:10/pi，e3d132e→6a389a5，material_scene.py+test 纯增量，19 passed）；WIL-69（surface:2，46e7a90..29834db→70f50d6..8998875，DesktopVoiceRecorder 四提交，12/12，真人麦克风未验如实记录）；WIL-67（surface:7，d10f52d→a6e033b，auth.py 纯追加 authenticate_identity，10 passed；device_accounts 核心前波已在 dev，剩 main.py 路由窄入口包）；WIL-66（surface:9，e73c919→aed6644，host_resource_probes 注册表+真实采样+准入，61 passed）。WIL-55（surface:6）纯分析 v4-page-mapping.md，无代码集成。**WIL-65 补收口（surface:12，b257cd6→d53aa84）**：montage/audio_timeline/local_portrait 本已完整（非桩），本包补唯一缺口 `admit_creation_render` 准入接线（montage.py+42 + test_render_admission.py，179 passed）→ In Review。**遗留跨包项**：creation_execution.py 的 cpu_render_facts 仍走 legacy `_sample` 准入、与新注册表 admit_resource 并存，端到端统一接线待后续 MAKE 编排包（creation_execution.py 非本包范围）。

**派工确认通道（主控裁定，2026-09-28）**：pi/OpenCode 不登记在 cmux 会话表，主控读不到其终端。故派工中凡「需主控确认/裁决/回文件清单」的点，执行者**必须写到 `external-agents/<issue>/` 下的 md 文件**，不得只发终端；主控巡检时读该目录新文件（不只读最终报告）。主控主动确认后执行者再继续实施。

**2026-09-28 四线程默认开发池（用户裁定，工作区已重命名）**：账号·接口（workspace:5/surface:16，Claude，后端 auth/main.py 路由/device_accounts）；pi·实现（workspace:4/surface:23，pi 通用实现）；调度·渲染（workspace:2/surface:12，Claude，后端 device_*/montage/creation）；分析·复核（workspace:6/surface:6，OpenCode 只读分析）。原 surface:2（录音接父层，卡草稿）与 surface:10（pi，已移除）退出默认池；其未完成派单改派上表线程。

**2026-09-28 四线程当前派工**：账号·接口/已收口 WIL-67 窄入口（9213556→c0255fd），现派 device-credential-injection（native runtime 注入 DEVICE_ID/DEVICE_PROOF，工作树 device-credential，基线 c0255fd）；调度·渲染/creation_execution 准入统一（creation-unify 工作树，cff2d4d 基线）；pi·实现/录音接父层（recording-parent 工作树，改派自 surface:2）；分析·复核/3 孤儿执行队列提交只读复核（b800b89/68eefe6/743ac02）。

**2026-09-28 第二批集成收口**：调度·渲染 creation_execution 统一（369fd94→5230301，legacy _sample→admit_resource 字段重命名，16 passed）；pi·实现 录音接父层（3765f16→41b9e20，CreationTaskWorkspace 接线 onRecorded，api.ts 无需改——端点已存在，39/41 passed）；pi 知识库页（0c66e73→1268d2f，可操作详情 UI 骨架+未分类一致化，6 passed）。分析·复核判定 3 孤儿执行队列提交（b800b89/68eefe6/743ac02）经合并 #44 已内容等价进主线，**无需集成按重复丢弃**；并警示 codex/011-ui-intake-contract 分支 tip 落后 HEAD，**不得整支合并**（会回退 docs/design/supabase 迁移等）。WIL-65 端到端 admission 已闭环（montage + creation_execution 均走 admit_resource）。

**2026-09-28 第三批派工（用户裁定 UI 基准 = owner.html）**：pi·实现/WIL-55 UI-FLOAT 浮卡+三尺寸（wil55-float 工作树，先回文件清单确认再改）；调度·渲染/WIL-60 MAT 统一许可接入（wil60-mat-license 工作树，承接 9e2ebef，依赖 WIL-57 已 Done）；分析·复核/WIL-12 素材接收只读核验。账号·接口仍在 device-credential 注入。WIL-56（DESIGN-V5）按用户裁定：v4 owner.html 为唯一基准，仅修 v4 已确认缺口、不另起布局。

**2026-09-28 第四批派工（全线程已空后重排）**：分析·复核/WIL-56 v4 缺口清单（只读，对照 owner.html vs 现有组件，供 pi 后续按单修正）；调度·渲染/verify_retry 受权调整接口（SCHED，让素材手动重试接通可信授权，verify-retry-auth 工作树）。pi·实现待 WIL-56 清单出后按单修 v4 缺口；账号·接口代码侧已完，剩余端到端 Supabase RPC 真机连通属用户验收。

**2026-09-28 本地多成员（用户裁定：本地 SQLite 建表，不联网不用 Supabase，保持简单）**：三步——① 建地基（local_members 表 + 成员 CRUD + 本地邀请码，账号·接口已派，local-members 工作树）；② 建页面（成员与空间页 + 邀请加入，pi 待①完成）；③ 加过滤（内容加 visibility 私有/共享字段 + 按角色过滤）。现有 Supabase 多用户路径（auth.py/device_accounts.py/迁移）保留不删、不激活。角色就 owner/member 两档、可见度就 private/shared 两档，不搞复杂权限。

**2026-09-28 第五批收口**：调度·渲染定时任务后端（e95085b→d575266，scheduled_tasks 表+CRUD，迁移 12，8 passed）；pi·实现 WIL-56 P1 小修（bedf068→cce1a78，选题编辑标题+检查清单，45 passed）；分析·复核箭头/密度分析（arrow-density-analysis.md，只读）。**迁移冲突已解**：本地多成员（迁移 11）与定时任务（迁移 11）撞版本，主控裁定本地多成员=11、定时任务=12、PRODUCTION_SCHEMA_VERSION=12，67 passed 验证通过。WIL-79/80 In Review。

**2026-09-28 第六批派工**：调度·渲染/箭头 overlay 后端（ArrowOverlay 模型 + PIL 渲染，WIL-82，arrow-overlay-backend 工作树，前端后续接）；pi·实现/密度对齐（v4 紧凑令牌，WIL-83，density-align 工作树）；账号·接口/本地多成员第②步成员与空间页面（WIL-84，members-page 工作树）。分析·复核留空（刚交完箭头/密度分析）。

**2026-09-28 第五批派工（WIL-56 缺口继续 + 本地多成员并行）**：pi·实现/WIL-56 P1 小修（选题编辑标题 §1.2 + 检查逐项清单 §1.7，WIL-79，wil56-p1-tweaks 工作树）；调度·渲染/定时任务后端第一片（scheduled_tasks 表 + CRUD 不接执行，WIL-80，scheduled-tasks-backend 工作树）；分析·复核/箭头 overlay 接线 + 密度对齐走查（只读，WIL-81）。账号·接口仍在本地多成员地基（WIL-73）。WIL-56 阶段缺口（§1.1+§1.8）已交付 8b2c618→a5639b9 集成、WIL-78 In Review。

**2026-09-28 第四批收口（核验类零代码 + 缺口清单）**：调度·渲染核验 SCHED 受权接口已存在（OwnerVerifier/retry_existing/wake_existing/change_policy 均在 dev，38 passed）→ 无新代码，WIL-60 缺口①更正为"接口已存在，剩余是 material owner+AUTH+MAKE"；分析·复核产出 WIL-56 v4 缺口清单（P0 六项：发布阶段缺失/箭头 overlay 缺失/资料阶段无视图/成员与空间缺失/权限模型缺失/定时任务 stub；P1/P2 详列；依赖风险标注）。pi·实现已派 WIL-56 阶段缺口（§1.1 资料视图 + §1.8 发布结构，CreationTaskWorkspace.tsx）。剩余 P0 后端依赖项（§1.5A 箭头后端、§1.8 发布后端、§4/§5 权限多成员、§6 定时任务）需后端规划，其中 §5 权限/多成员为单用户→多成员架构级变更，需主控/用户先定方向。

**2026-09-28 第三批收口（含核验类零代码交付）**：调度·渲染核验 WIL-60 最小闭环已由先前 MAT 线程在 dev（e7e077a→c8cdc2a→11e77f4→cff3522→4169682 均祖先，135 passed）→ WIL-60 In Review，剩余 verify_retry 需 SCHED 受权接口等跨包缺口如实记录；分析·复核核验 WIL-12 核心能力真闭环（9 能力全 ✅，122 passed）→ WIL-12 In Review，仅 scheduled 整机启动未接线属下一代边界；账号·接口 web 放行（f00b109→a13fbae）集成 → WIL-67 设备邀请链路三块齐（后端路由+凭据注入+web 放行）。pi·实现 WIL-55 UI-FLOAT 方案已确认（只做内容层 VideoClipQuickEditor，不碰 AppShell），实施中。

**跨包待裁定（不阻断，供 WIL-65 接线前对齐）**：WIL-66 注册表 mat_scene_probe 上限 max_source_duration_s≤1800/wall_clock_s≤3600，而 WIL-68 SceneProbeBudget 默认 3600/3630——MAT 默认预算会被 SCHED 上限拒绝；字段名已一致，接线包须统一默认值或上限。另 ui-v4-integration 分支尚有 3 个未集成提交（b800b89/68eefe6/743ac02 执行队列事实功能 ExecutionQueueStatus），属另一功能、未经 WIL-69 复核，待单独审。

**2026-09-28 第七批收口与派工（WIL-82/83 集成、WIL-84 暂缓、发布平台已定）**：用户已裁定首批发布平台=抖音/小红书/微信（仅定平台集合，未授权真实发布；已写回 WIL-45 待决定区，继续阻塞 WIL-38）。WIL-82 箭头后端（861e43e→**1a86386**，8 passed）与 WIL-83 密度对齐（789b1b5→**d69343e**，vitest 40 files/314 passed；合并树未复跑 tsc，如实记录）已 diff 亲审、零冲突 cherry-pick 合入 dev 并推送 c5334b1→d69343e。WIL-84 成员页面候选（members-page 1adf73f）**暂缓合入**：前端 role prop 默认 owner 而后端 /api/members 六路由无 owner 校验、无 visibility 过滤，等第③步后 rebasing 集成（裁定已发 WIL-84 评论）。新派工：账号·接口/本地多成员第③步（owner 校验 + visibility 过滤，契约 external-agents/dispatch/members-auth-step3.md，第0步设计提案先审后 DDL，工作树 members-auth，基线 d69343e）；pi·实现/箭头前端接线（契约 external-agents/dispatch/arrow-overlay-frontend.md，types.ts+GraphicOverlayEditor，工作树 arrow-frontend，基线 d69343e）。分析·复核本轮留空。**登记时点两份派单消息与 Linear 状态写入（WIL-82/83 评论+置 In Review 已置、两笔新建子 issue）被权限 classifier 拦截，待用户逐项授权后重发**。

**2026-09-28 第八批收口与派工（WIL-86 集成、WIL-85 设计批复、四线程满载）**：WIL-86 箭头前端（**9b1ed51→1922bb5**，4 文件 +101/−14，零冲突 cherry-pick，合并树复验 vitest 318 passed + tsc 通过，补上 WIL-83 未在合并树跑 tsc 的欠账；VideoEditor.tsx:72 一行越权修经主控裁定**接受**——联合收窄后 TS2339 唯一不放宽契约的方案）→ In Review。WIL-85 第③步设计提案**批复通过**：三裁决点均按推荐（方案 A 匿名放行+绕过面如实入册/creation_tasks+material_assets 两表列表过滤/randomblob 回填存量），批复存 external-agents/members-auth/design-approval.md，含 4 条补充要求（whoami 切 B 预留/迁移幂等测试/诚实边界/join 局部模型）。**第八批派工**：账号·接口/按批复实施 WIL-85（members-auth 工作树继续）；pi·实现/定时任务管理页真 CRUD 接线（dispatch/scheduled-tasks-ui.md，新工作树 scheduled-ui，基线 1922bb5，TaskType/TriggerMode 以后端 Literal 为准、诚实显示"执行未接线"不伪造运行时间）；调度·渲染/箭头 NL 映射（dispatch/overlay-nl-arrow.md，仅 graphic_overlays.py+测试，基线 1922bb5，add 模式不得造默认坐标）；分析·复核/前端 token 接线差距分析（dispatch/members-frontend-wiring-analysis.md，只读，产出 frontend-wiring-analysis.md 为 WIL-84 rebase 集成备料）。**遗留微包排队（不丢）**：CreationTaskWorkspace.tsx:1134-1135 芯片箭头误标一行修（WIL-56 范围）+ WIL-83 第 7 项三级文字 11px 档——待攒够同批派 pi polish 微包，不单开 issue。

**2026-09-28 第九批收口与派工（四线程反馈审完，两合入两在途）**：四线程全部交付，主控逐一审核。**已合入推送**：①定时任务管理页（pi，`dca7608`→主树 `75f062a`，5 文件零越权、真 CRUD 接线、诚实标注"执行未接线"，合并树 vitest 324 passed + tsc 通过）；②箭头 NL 映射（调度·渲染，`f8a5b37`→主树 `ae11033`，仅 graphic_overlays.py+测试、add 模式改引导报错不造默认坐标，10 passed + ruff）。**在途（审完待收尾）**：WIL-85 第③步（账号·接口，`db56e8b`）代码审过——偏离「迁移13 空 SQL + token/visibility 列放运行时 PRAGMA 守卫 ensure」经核验**理由充分可接受**（SQLite 无 ADD COLUMN IF NOT EXISTS、test_device_runtime 裸库跑迁移链会 no such table，静态 ALTER 必炸；功能结果一致且幂等）；但引入 3 个 `test_audio_timeline.py` 位置式 INSERT（:163/:304，5 值撞 6 列）连带失败，**已扩权派本包修这 2 处为显式列名 INSERT**，新 SHA 到后合入。**分析产出（无代码）**：分析·复核交 `frontend-wiring-analysis.md`——WIL-84 候选 `1adf73f` rebase 1922bb5 **零文本冲突**（AppShell/api.ts blob IDENTICAL），建议两包方案（PkgA cherry-pick 候选 → PkgB token 接线），硬约束 B 不得先于 WIL-85 合入；作为 WIL-84 集成包依据。**新派工**：箭头门禁一行修派调度·渲染（main.py:6736 词表 `("标题","图片","标识")` 追加"箭头"+测试，工作树 arrow-nl-gate，基线 ae11033）——这是 arrow-nl 报告点出的产品层生效缺口。**基线失败入 backlog**：主树复验确认 4 个历史遗留（test_device_scheduler 2 条断言 schema 版本未跟上 12、test_device_accounts 2 条中文 PG 错误串 locale），非本批引入、单独记录不阻塞。

**2026-09-28 第十批收口与派工（三包合入推送 + PkgB 派出）**：**WIL-85 第③步**（账号·接口，`db56e8b`→主树 `ccaa04f`）经审核合入——第③步偏离「迁移 13 内 randomblob 回填」已按第九批裁定接受（token/visibility 列落运行时 PRAGMA 守卫，幂等），连带修 `test_audio_timeline.py` 位置式 INSERT（`bb4c8e0`）一并合入。**arrow-nl-gate**（调度·渲染，`b1260cf`→主树 `a52726c`）一行修 + 路由级测试合入。**WIL-84 PkgA**（pi，`1adf73f`→主树 `a90320a`，6 文件 +359）合入——**api.ts 出现真实文本冲突**（scheduled-ui 与 members 两段各自末尾追加撞位），解法：两段全保留，在 `deleteScheduledTask` 后补其缺失的收尾 `}`，members 段保留自带收尾；核对 `api.ts` 恰 +50 = 候选原始追加量，零丢失零重复，合并树 `tsc --noEmit` 通过 + vitest 42 files/333 passed（基线 324 + 成员 9）。三包均已推送（`a90320a` = dev HEAD = origin）。**如实修正一处分析误判**：分析·复核 `frontend-wiring-analysis.md` §1 判「rebase 零文本冲突、blob IDENTICAL」，该结论对 AppShell 成立、**对 api.ts 不成立**（其比较的是成员候选与基线，未纳入已合入的 scheduled-ui 追加）；不影响 PkgA 结论。**新派工**：pi·实现/WIL-84 PkgB 前端 token 接线（契约 external-agents/dispatch/members-frontend-token-wiring.md，工作树 members-token，基线 a90320a）——后端第③步的 visibility 过滤已就绪但浏览器从未携带令牌，过滤在 UI 上是空转的；派单写死「无令牌时绝对不加 `X-Local-Member-Token` header」（方案 A 下乱加会 401）与四条诚实边界（SSR 首屏仍按 owner 渲染/方案 A 绕过面照旧不得宣称已安全隔离/媒体 URL 无 header 依赖单条 GET 不过滤的前提/whoami 过渡态）。**登记时点 WIL-84 的 Linear 集成证据评论写入被权限 classifier 拦截**（[External System Writes]）。用户口头授权后主控重发**仍被拦**——根因是 `settings.json` / `settings.local.json` 中**一条 MCP 规则都没有**，Linear 写操作无「预先放行」档，每次都落自动分类器，而分类器看不到对话上下文。**已解决**：用户选定最小范围，在 `~/.claude/settings.local.json` 的 `permissions.allow` 加 `mcp__linear-server__save_comment` + `mcp__linear-server__save_issue` 两条（JSON 已校验合法，38 条 allow）。**明确不放** `save_project` / `save_milestone` / `save_issue_label` / `save_project_label` / `share_issue` / `merge_diff`——按用户「最小权限」原则，尤其 `merge_diff` 能直接合代码、`share_issue` 能把内部 issue 给外部人。**不使用 `bypassPermissions`**：本会话已证明该拦截层有真实价值（拦下一次 `cat >` 覆盖 `api.ts` 的不可逆写入，替代方案定点编辑达成同一结果）。**验证**：加规则后两笔 Linear 写入均成功落地。**WIL-84 状态有意保持 In Progress 不置 In Review**——PkgB（前端 token 接线）仍在途，置 In Review 会与评论正文「整体仍不算完成」自相矛盾；待 PkgB 合入复验后再收口。

**2026-09-28 第十二批收口与派工（两包合入推送 + WIL-84 仍未收口 + 主控亲验抓出功能缺口）**：**基线修复 A**（调度·渲染，`6e351cd`→主树 `353515d`，`test_device_scheduler.py` +2/−2）与 **WIL-84 PkgB 前端令牌接线**（pi，`3e81cfc`→主树 `61c89e9`，8 文件 +385/−12）**均已 diff 亲审 + 零冲突 cherry-pick 合入并推送** `a90320a..61c89e9`。合并树主控复验：后端 `test_device_scheduler`+`test_device_runtime` **30 passed**（22+8，与执行者报告数字一致）、`tsc --noEmit` 通过、`vitest` **44 files/344 passed**（与报告一致）。A 包两处断言仍是严格 `== PRODUCTION_SCHEMA_VERSION`（未削弱成 `>=`、未删行、未擅自改测试名），执行者在报告里主动自省「若 `PRODUCTION_SCHEMA_VERSION` 被写小测试会跟着变松」——该口子主控记下，**不单独开包**，待有真实触发条件时在 `database_migrations.py` 侧补「_MIGRATIONS 末版本必须等于 PRODUCTION_SCHEMA_VERSION」断言。PkgB 逐行审过 `local-member-auth.ts` 与三处 request 封装：「无令牌返回 `{}`」硬约束落在 `local-member-auth.ts:78`（杜绝空串被当非法凭据 401），`api.ts` 令牌注入恰 5 处 + 1 import，四条诚实边界全部落进代码注释，**未出现「已安全隔离」类表述**。

**主控亲读代码抓出的功能缺口（本轮最重要的产出）**：PkgB 报告主动标出、主控独立核实属实——`api.ts:923` `joinLocalMember` 返回类型声明为 `Promise<LocalMember>`，**丢掉了后端 `POST /api/members/join`（main.py:890）实际返回的 `member_token`**；`MembersAndSpacesWorkspace.tsx:76` join 成功分支也未调 `storeLocalMemberToken`。**后果**：成员用邀请码「加入空间」后，前端全程不带 `X-Local-Member-Token` → 请求全为匿名 → 后端按方案 A 以 **owner** 放行。**即本地多成员当前是「加了人但没生效」，WIL-84 不得收口、不得置 In Review。** 据此派 **PkgC**（pi，沿用 members-token 工作树，基线 `61c89e9` 与其工作树内容一致无需 rebase）：join 成功存令牌 + 返回类型如实反映后端 + 追加「join 后刷新身份」以免成员继续看到 owner 专属导航（禁止用整页 reload 绕过）。禁写 types.ts（除确认无现成类型且经主控裁断）、css、AppShell、后端。

**新派工（只读）**：调度·渲染/**scheduled 生产门禁盘点**（`dispatch/scheduled-production-gate-audit.md`，工作树 sched-gate-audit，基线 `61c89e9`）——执行合同「startup 门禁未完成前不启用 scheduled 生产运行」立了许久但**从未盘点差什么**，本包查 5 项（启动门禁本体是否存在／已实现准入清单是否真串成一条链／生产开启闸口已通 vs 断着／verify_retry 等手动重试是否真接到授权入口／**关闭态是否真的关闭、有无「没配就当开」的危险默认值**），输出可勾选清单，**禁止产出「scheduled 已可用」结论、禁止建议开生产**。**该包的 `cmux send` 终端通知被权限 classifier 拦截**（[External System Writes]「cmux send 派活是外部系统写操作」），派单文件已落盘但通知未送达，**待主控手动投递或用户放行后再发**；未换工具/换写法绕过。

**状态纪律**：WIL-84 **有意保持 In Progress**（PkgC 未交）；WIL-85 仍 In Review。账号·接口的基线修复 B（pg-locale）与分析·复核的可见性对账（members-audit）**报告目录仍空，在途未交**。

**2026-09-28 第十一批派工（三条空闲线程满载 + 修正 backlog 定性）**：pi·实现仍在 WIL-84 PkgB（在途，工作树已有 `local-member-auth.ts` + `hooks/`，未交付）。主控在派基线修复前**亲读代码核对了 4 条基线失败的真实成因**，修正了第九批"断言陈旧"的粗略定性：①`test_device_scheduler.py:598/:644` 并非漏改数字——两条测试用 `if version == N: break` **伪造停在旧版本的库**来验证迁移续跑，真正保护的不变量（请求字段未改坏 / 索引重建 / 检查点幂等与事务回滚）**现在都是通过的**，失败的 `max(version)==10` 只是"迁移跑到顶"的旁证，且测试名 `v8_to_v10` 同批陈旧；②`test_device_accounts.py:174` 匹配的是 **PG 自己的错误消息**（同文件 :179/:185 匹配的是自定义 `raise` 文案故通过），被本机中文 locale 打断。**新派工**：调度·渲染/基线失败 A（工作树 baseline-tests，基线 a90320a，**只改 tests/test_device_scheduler.py**，断言须跟随 `PRODUCTION_SCHEMA_VERSION` 常量、强度只许保持不许削弱，定性有误则停下只交报告）；账号·接口/基线失败 B（工作树 pg-locale，**只改 tests/test_device_accounts.py**，:174 改 locale 无关断言、约束名须实测不得猜，本机跑不了 PG 就如实报未复验）；分析·复核/本地多成员可见性对账（工作树 members-audit，**只读**，逐路由列全方案 A 绕过面、查单条 GET 是否不过滤、查 knowledge_items/video_templates/creation_plans 三表有无可见性字段、查存量 randomblob 回填后 visibility 实际默认值、查前端在 PkgB 合入前 member 视角与 owner 有无区别，**禁止产出「已安全隔离」类结论**）。三包均不单开 issue，计入 `.project-control/0.11-bug-backlog.md` / WIL-73 对账。**遗留微包排队（不丢）**：CreationTaskWorkspace.tsx:1134-1135 芯片箭头误标一行修 + WIL-83 第 7 项三级文字 11px 档——攒批派 pi polish 微包，不单开 issue。
