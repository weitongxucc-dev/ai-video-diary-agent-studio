# 当前主控与执行边界

## 1. 当前目标与授权

2026-10-21用户批准按已收口方案进入实施，先交付有约束局部修改的真实闭环，不继续改写长方案。唯一主控为本聊天 `01a1429d-9efe-7491-bcb6-d85f090c1138`，实施基线 `main@14a0bf77`，实时Git优先。此前审议只读限制解除，旧§3.2绑定全部退役，源码与已有产物保留；本表是唯一当前写权。

首条接受链：同一多片段作品首段有声视频可播放，后续制作中用户提出“第1段紧凑一点但保留卖点”，Pi结合实际上下文形成可执行修改；经历一次受控失败/中断及恢复，最终采用正确版本，进度与有依据的用量记录可核对。预算暂停和恢复从第一条链纳入。仅证明该场景，不外推所有指令或任意时长。随后继续原目标：Mac一次目标中英各30秒、连续精修、撤销重做和Quit重启恢复；手机与Windows后置。

## 2. 直接权威

- 输入、采用与语义检查：[产品§4.2/9.2](../docs/product-requirements.md#42-对话与操作习惯)；计量与额度：[产品§12.2](../docs/product-requirements.md#122-自主制作的费用边界)。
- 版本和消息语义：[领域§7](../docs/domain-model.md#7-自然语言编辑)；Pi决策与执行分工：[架构§3.15.2](../docs/system-architecture.md#3152-粗剪多轮精修与创作方法)。
- 前端按[DESIGN](../DESIGN.md)现有组件与布局，不改左侧名称和分区，不采用被否定HTML，不擅自使用未批准设计候选。
- 工作范围与接受点：[开发清单§1.1.1a](0.11-development-plan.md#111a-本次批准的优先修复与尺度验证)。验证按[AGENTS第4–6节](../AGENTS.md#4-避免无效-token-与无效工作)，不预设新增测试文件或固定叠加检查。

## 3. 唯一写入表

| 负责人 | 独占路径与职责 |
| --- | --- |
| 主控 | 本合同、开发清单及必要共享权威，原验收入口；审核、集成提交和候选打包。无产品代码写权；不与C同时操作候选。 |
| A `01a14301-2730-7440-ab61-69fd35d9c341` | Pi与用户界面纵向负责人。`apps/api/agent_tools/diary/`、`apps/api/app/assistant/`、`apps/api/app/conversation_repository.py`、`apps/api/app/assistant_goal_continuation.py`；前端 `apps/web/src/components/creation-task/`、`CreationTaskWorkspace.tsx`及其module.css、`AssistantConversationPanel.tsx`、`useAssistantConversation.ts`、`AssistantConversationProvider.tsx`、`apps/web/src/lib/api/{assistant,creation-tasks,task-media}.ts`、`apps/web/src/lib/types/{assistant,creation-tasks,task-edits}.ts`、`apps/web/src/lib/creation-task-status.ts`；仅相应现有assistant/typed工具及对应组件测试。补持久等待/理解分类/后台业务调用、实际上下文与语义复核、右侧段预览/修改状态/回执/采用关系。不得写B后端文件。 |
| B `01a14301-7591-74f1-b240-d3643b78ecfe` | 共同编辑与执行用例负责人。`apps/api/app/creation/`、`production.py`、`schemas.py`、`creation_execution.py`、`narration.py`、`main.py`（仅必要组装/HTTP适配）、`device_scheduler.py`；仅对应既有creation_execution/narration/edit用例测试。复用原段、Job、Artifact、版本与依赖，补最小局部文案修订/配音字幕镜头接续、有声段产物、版本发布和中断/预算恢复。对A提供明确类型化入参/回执，main不承载业务。不得写A路径或更改保护门限、验收数据。 |
| C `01a14301-b182-7a92-a973-66fe11baf450` | 独立真实验收，无产品代码写权。独占主控交付候选的GUI操作、媒体播放、正常Quit/重启和指定安全点中断；只写既有验收目录下 `desktop-c-20261020-*` 定向证据。先准备同一任务的接受步骤和现有入口定位，不启动旧候选负载；收到新候选后串行执行，不人工补文案/镜头/内部ID或改库凑通过。 |
| G `01a15854-9259-7231-977f-32bec26fd119` | 只读定点审明确diff：规则/语义边界、用量证据、职责和事务、采用版本与未知回执；不重复审全仓，不写源码/测试/共享文档、不运行重活。 |
| 设计语言 `01a1582b-200c-7220-8187-5925f6082fc5` | 用户独立授权范围保持，独占 `designs/yaohe-desktop-language-20261020/`；完整590项研究不缩成抽样，未采用候选不进入DESIGN或产品。本批不写该目录。 |

路径中的非完整文件名均位于该格前述目录；新增文件、共享测试或范围外修改先报精确路径，由主控绑定后施工，不自行扩张。A/B先快速固定接口差额后并行实施，不另造调度器、通用状态系统或Agent运行时。前端实现明确归A，C只验收。保留已确认任务授权24小时、退出保存暂停、有效授权原目标接续；不重复审批或放宽资源保护。正式改稿尊重用户锁定；未知用量不能补0，费用不得用运行时长伪装。

## 4. 交付与运行

A/B先回报新绑定理解、必要接口和最小风险检查并直接实施，不等待再次START；首个实际缺口超出路径时只报事实。旧消费者随本次替换收敛，不顺手大重构。实现冻结后G审核，主控集成并构建一个共同候选，C对同一任务及明确版本链取得真实媒体、界面、失败/恢复与计量来源证据。无新变化不重复构建/测试；失败先保留原请求回执、进程/版本事实，再定点修复。

现有本机验收入口为 `/Volumes/Dujiangyan-VM/AI视频日记/acceptance/phone-loop-20261018/电脑端核心验收.md`；定向读当前证据，不遍历旧输出。新候选前不重跑模型。MiniMax仅用于mobiletest01验收，既有素材与来源许可沿原批准；用户指定模板参考目录 `/Users/Zhuanz/Documents/视频剪辑/output/opc-20261011` 不自动证明与脱壳机相关。

保护 `.gitignore` 与未跟踪设计目录，不纳入提交；不删除用户数据、安装、原始素材或历史证据。任务派出、代码通过或短片样例通过均不是全产品完成。
