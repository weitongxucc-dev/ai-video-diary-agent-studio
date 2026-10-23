# 当前主控与执行边界

## 1. 当前目标与授权

2026-10-21用户明确恢复实施：按已更新计划优先打通Pi通用操作、产物登记和真实作品闭环。主控负责派工、审核、集成与必要真实验证，不在每步结束后重新请示。基于10bc6f1f及当前保留diff推进，先复用现有能力修实际断点，不大重构、不扩大测试。2026-10-23用户授权因上下文过长移交主控；唯一主控现为 `01a166a6-d27e-7791-9780-1ee9eb9ede43`，原主控 `01a1429d-9efe-7491-bcb6-d85f090c1138` 在交接完成后停止派工及写入。接续事实见[本次交接](takeover/2026-10-23-coordinator.md)，原目标和权限边界不变；旧文档冻结解除范围仅限下表，本合同不授权用户数据删除、模型训练或无关平台开发。

完整顺序见[计划§1.1.1](0.11-development-plan.md#111-固定推进批次)，本批出口按§1.1.1a与§1.1.1b。原生执行必须与同一任务的登记、预览、采用、修改及恢复同时接通；必要证据和版本修复不后置。原中英各30秒目标保留，先形成可审候选再串行跑真实链；手机/Windows继续后置。独立设计研究原授权不变，左侧名称分区与正式设计规范不变。

## 2. 第一阶段共同约定

以下为同一交付的绑定与接受口径，稳定规则只由链接权威拥有：

1. **候选**：固定实际源码提交、构建输入标识、运行配置和素材/模型版本。当前工作树中的未提交实现不是已验收候选；相关变更后只重验受影响链路，最终结果对应同一候选，不能拼接不同构建冒充通过。
2. **归属**：任务、Conversation、Pi session和Job依[领域§7.5](../docs/domain-model.md#75-对话会话与轮次)分别记录。通用操作绑定原任务/对话/输入、工作目录、许可输入和产物；跨任务共享只用受权版本引用，后台事件不取当前打开的聊天。
3. **自主执行与登记**：按[架构§3.15.1](../docs/system-architecture.md#3151-原生工具业务工具与创作执行)复用原生工具及执行适配，允许自行读资料、写改脚本、运行、读错和调整，不为验收另写专用入口或固定命令白名单。结果进入原任务Artifact、预览与采用；进程、临时文件、消耗及停止/回收状态可见，聊天取消不冒充执行已停止。
4. **版本与撤销**：按[领域§6.2](../docs/domain-model.md#62-timelineversion)核验目标及实际依赖。无关片段发布不误冲突，真正依赖变化须拦截/重评；撤销恢复本次修改及依赖，保留其他片段后续成果。首段预览、完整时间线与最终合成采用关系须一致。
5. **预算与恢复**：任务档案、执行回执和检查点从提交起保存；目标/单元/预算分离，重试/重启不清零，未知先对账。费用口径见[产品§12.2](../docs/product-requirements.md#122-自主制作的费用边界)，许可与退出见[认证§5](../docs/auth-workspace-boundary.md#5-后台任务)；已有24小时任务授权、保存暂停与有效授权接续保持，资源保护不放宽。
6. **真实路径**：按[计划§1.1.1a](0.11-development-plan.md#111a-本次批准的优先修复与尺度验证)运行一项未预设专用接口的需求及另一项未专门适配的相近需求；贯通自主执行、首段实播、受约束修改、冲突正反面、局部撤销及中断恢复。C不人工补业务步骤，A负责前端接线，G审核同一候选相关diff。通过仅证明对应场景，随后继续原中英各30秒目标，不外推任意指令/时长。

## 3. 唯一写入表

本表恢复A/B限定实现写权并保护已有diff。一个文件同一时间仅一个负责人；跨边界先提交接口需求，由主控调整归属，不互改。两人先固定通用执行与登记的最小调用契约，再并行实现；主控不与A/B同写产品文件。

| 负责人 | 本批独占范围与交付 |
| --- | --- |
| 主控 | 共享文档、契约裁定、diff审核、集成提交与运行候选。需要主控直接修源码时先从原负责人取得明确交接。C取得运行窗口后主控不并行操纵同一应用或启动媒体。 |
| A `01a14301-2730-7440-ab61-69fd35d9c341` | Pi与用户界面纵向负责人，新增独占 `apps/api/agent_runtime/`、`apps/api/agent_tools/controlled_codemode.ts`；原生工具适配、宿主可信上下文、取消与实际产物回读由A接通。`apps/api/agent_tools/diary/`、`apps/api/app/assistant/`、`apps/api/app/assistant_pi.py`（仅运行回调签名及透传）、`apps/api/app/conversation_repository.py`、`apps/api/app/assistant_goal_continuation.py`；前端 `apps/web/src/components/creation-task/`、`CreationTaskWorkspace.tsx`及其module.css、`AssistantConversationPanel.tsx`、`useAssistantConversation.ts`、`AssistantConversationProvider.tsx`、`apps/web/src/lib/api/{assistant,creation-tasks,task-media}.ts`、`apps/web/src/lib/types/{assistant,creation-tasks,task-edits}.ts`、`apps/web/src/lib/creation-task-status.ts`；仅相应现有assistant/typed工具及对应组件测试。补持久等待/理解分类/后台业务调用、实际上下文与语义复核、右侧段预览/修改状态/回执/采用关系。不得写B后端文件。 |
| B `01a14301-7591-74f1-b240-d3643b78ecfe` | 共同编辑与执行用例负责人，新增独占 `apps/api/app/artifact_store.py`、`media_script.py`、`media_script_jobs.py`、`bootstrap.py`（受许可运行器及host/member接线已交A，见§3），按需在creation目录内补原生执行/产物登记用例。复用现有Job/TaskEvent/Artifact及受许可进程，提供A调用的业务契约，不限为PNG专用入口。`apps/api/app/creation/`、`production.py`、`schemas.py`、`creation_execution.py`、`narration.py`、`main.py`（仅必要组装/HTTP适配）、`device_scheduler.py`；仅对应既有creation_execution/narration/edit用例测试。复用原段、Job、Artifact、版本与依赖，补最小局部文案修订/配音字幕镜头接续、有声段产物、版本发布和中断/预算恢复。对A提供明确类型化入参/回执，main不承载业务。不得写A路径或更改保护门限、验收数据。 |
| C `01a14301-b182-7a92-a973-66fe11baf450` | 恢复轻量只读准备权，无产品代码写权；主控交付明确候选与独占运行窗口后执行真实验收：独占主控交付候选的GUI操作、媒体播放、正常Quit/重启和指定安全点中断；只写既有验收目录下 `desktop-c-20261020-*` 定向证据。先准备同一任务的接受步骤和现有入口定位，不启动旧候选负载；收到新候选后串行执行，不人工补文案/镜头/内部ID或改库凑通过。 |
| G `01a15854-9259-7231-977f-32bec26fd119` | 恢复只读定点审查：先复核保留B改动的三个既有阻断与本批运行/登记接口边界，随后审明确diff；规则/语义边界、用量证据、职责和事务、采用版本与未知回执；不重复审全仓，不写源码/测试/共享文档、不运行重活。 |
| 设计语言 `01a1582b-200c-7220-8187-5925f6082fc5` | 用户独立授权范围保持，独占 `designs/yaohe-desktop-language-20261020/`；完整590项研究不缩成抽样，未采用候选不进入DESIGN或产品。本批不写该目录。 |

新增文件 `apps/api/app/creation/segment_production_use_case.py` 与 `task_budget_use_case.py` 属B既有待审diff，不因文件存在认定已集成。设计研究保持用户独立授权范围，涉及新交互规则须按新DESIGN核对；不因本轮文档变更授权生产UI改版。

B独占本轮轻量原生进程实证文件 `/Volumes/Dujiangyan-VM/AI视频日记/acceptance/phone-loop-20261018/desktop-c-20261020-native-smoke.json`，记录工具启动、隔离、停止与资源测量；该文件不属于C的作品验收结论，不授权B操作桌面、模型或制作真实作品。

B已明确交接并停止写入 `apps/api/app/creation/section_timeline.py`，本批此单文件改由A独占：复用已有时间联动，负责目标段音画、字幕与实际依赖的纯转换；B继续独占仓储、事务、EditOperation与撤销入口接线。其余所有权不变。

### 2026-10-23 完整业务操作与共同事实批次

用户在本次主控明确批准：先将基础问题收敛到现有架构与计划、区分已有/缺失，再安排实现；范围覆盖制作、修改、指定撤销与重启恢复，不只修换声，不另造万能中间层。依[架构§3.14](../docs/system-architecture.md#314-业务用例执行契约与组装)、[§3.15.3](../docs/system-architecture.md#3153-证据可达性与结果闭环)、[领域§6.2](../docs/domain-model.md#62-timelineversion)及[计划§1.1.1c](0.11-development-plan.md#111c-基础问题按业务动作收口)重绑定。当前基线为`7c354ef6`和保留diff；源码与运行状态以Git和原验收证据为准，旧交接及已经结束的升级任务不恢复施工命令。

- **保留复用**：A已完成的声音选择错误透传，B已完成的有界合并及排队声音改选共同提交均保留，按G定点审查结果集成；不是本批最终出口。原授权、固定版本、累计预算与保护值不变，不修改真实任务数据凑通过。
- **A独占补充**：接回此前Pi升级临时归主控的四文件；除上表原范围，继续拥有 `apps/api/app/materials/contracts.py`、`materials/retrieval.py`、`topics.py`、`apps/web/src/lib/api/creation-transport.ts` 及直接相关既有测试，新增明确拥有 `apps/api/agent_tools/task_facts.ts`、`apps/web/src/components/TimelineVersionHistory.tsx`及其module.css、`apps/web/src/lib/types/timeline-history.ts`、`apps/web/src/components/HomeCommandComposer.tsx`及`apps/web/src/lib/api.ts`（仅既有历史入口/首页撤销消费者的稳定请求、分页、指定操作和真实回执，不改版；本批执行就绪原回执补齐时，窄解冻 `types/task-edits.ts`、`types/timeline-history.ts`、`creation-task/stage-labels.ts`、任务页/首页/对话面板及Pi相应既有消费者，复用同一说明，不另建状态）。本批先核对象/版本、操作结果与Pi/UI实际消费者，再沿B具体业务契约接通正文/局部编辑/指定撤销/恢复。不能由工具自行重建业务成功、刷新版本绕冲突，或再加一套对象/完成数据库。
- **B独占补充**：继续原范围，`apps/api/server.py`受许可子进程入口已交A，见下一条。后端完整业务操作负责人，沿现有制作、段落、编辑、采用、历史和恢复用例修共同根因，负责原请求读回及必要HTTP/schema适配。段读面复用原脚本生成记录中的固定来源绑定；`source_repository.py`窗口方法已由B明确停写交主控。指定撤销必须保留后来无关用户修改，不能用后来后台发布替代验收。`section_timeline.py`仍归A，需要变动时先交明确接口需求；不得覆盖A文件。
- **受许可PCM组装分工**：仅沿原Job/TaskEvent/设备许可扩展固定格式PCM的未发布工作文件追加，规则见[架构§3.7.1](../docs/system-architecture.md#371-长内容的有界执行)。B明确停写后，A独占既有 `apps/api/app/granted_subprocess.py`、`host_resource_probes.py`、`member_permits.py`、`apps/api/server.py` 及直接相关 `tests/test_host_resource_probes.py`、`tests/test_member_permits.py` 的模式/许可/运行器接线；B保留 `creation_execution.py`、`narration.py`、原Job/账本/检查点与 `tests/test_creation_execution.py`，向A提供可信基线校验接口；独立执行文件 `apps/api/model_workers/pcm_assembly.py` 已由B明确停写交主控，仅此文件，不改其他model_workers。复用标准库PCM读取并明确无现成追加API，不增加第三方依赖或调度器；绝对上限、累计消耗和原任务不变。允许既有测试临时目录内一次轻量合成PCM夹具的真实受许可子进程检查，验证格式/累计/中断及资源观测；不启动模型、桌面或用户媒体，不能用此声称生产标定或成片通过。G定点审基线来源、不可变前缀、未知对账、最终发布恢复及旧重写消费者退出。
- **主控独占源码窄交接**：B明确停写后，主控接 `apps/api/app/source_repository.py` 的固定来源只读窗口方法、`apps/api/app/source_types.py` 的原样来源身份纯函数及 `apps/api/tests/test_knowledge_grants.py` 对应验证；不改资料写入或表结构。B负责原 `creation/plan_preflight_use_case.py` 身份函数退役及main/直接测试导入适配，段sources用例仍属B，不互写。主控另接B已明确交出的 `apps/api/model_workers/pcm_assembly.py` 固定PCM执行器及 `apps/api/tests/test_pcm_assembly.py` 的独立临时夹具检查；A拥有runner/许可接线，B拥有Job/账本和恢复，执行器不注册Artifact或调度。
- **G**：先完成保留授权差额复审，再只读审本批文档/实际diff及真实消费者；关注完整变化集、失败回滚、跨场景复用、实际依赖、原请求结果和旧旁路退出，不扩全仓审查或运行负载。
- **C**：仅只读准备。固定候选及主控明确交付独占窗口后，沿同作品制作→第一次局部修改→第二次无关用户修改→指定撤销第一次→正常Quit/重启读回串行运行。原中英各30秒及自主执行接受点保持，不人工补脚本/镜头/内部ID或改库。

本次共享语义更新期间A/B保留diff、停止新增写入并完成定向根因核查；主控完成文档检查后发精确重绑定。A/B先回REBASE_ACK说明可复用实现、差额、文件及最小验证，只有明确收到该批差额实施任务才恢复写入；不从前段历史条款自行启动。共享契约先由B给出具体入参/返回/查询身份，A确认实际消费者后主控解除对应实现依赖；独立部分按授权继续。主控独占共享文档与集成，设计研究和保护目录不变。

## 4. 交付与运行

A/B按§3本批重绑定及实施任务推进；已明确授权的独立差额持续完成，不逐步请求用户确认。G仅定点审；C先核实本机真实入口/账号/素材/模型及原任务，不启动旧候选媒体或重复审源码。原生运行与登记的入参必须绑定可信原任务、对话、输入、授权、工作目录和版本；注册、预览、采用分别回执。共享契约有冲突时只暂停相关写入并交主控裁定，不盲目重做。

A/B完成最小相关检查后给出diff与结果；主控审核并记录可复现源码候选后交C串行真实验证。C遇软件错误立即给原请求/版本/具体回执，A/B修后仅复验受影响部分；不人工补业务步骤，不靠测试数、脚本文件或旧视频宣告完成。通用自主链与相近需求、首段到整片、局部修改/撤销/恢复按计划验收，资源保护不放宽。验证依[AGENTS第4–6节](../AGENTS.md#4-避免无效-token-与无效工作)选择，不固定叠加、不预设新增测试文件。
原本机验收入口为 `/Volumes/Dujiangyan-VM/AI视频日记/acceptance/phone-loop-20261018/电脑端核心验收.md`；只按当前需要定向读取证据。MiniMax仅用于mobiletest01验收，既有素材及来源许可不扩张；用户指定模板目录见计划。Mac优先，手机与Windows按依赖后置，不以缺后续平台阻断当前桌面闭环。

保护 `.gitignore`及未跟踪设计目录，不纳入产品提交；A/B保留diff只在按职责审核后集成，不覆盖或丢弃；不删除用户数据、安装、原始素材或历史证据。文档更新、派工或局部测试通过均不代表产品完成。
