# 活动合同：PI-CONT（2026-10-13）

## 目标与授权

用户已明确要求实施完整对象读取、结果观察和自动接续，并核查持续增长/更新/删除的素材与知识检索；开发过程同步既有教学线程，及时保存并提交 GitHub。本合同替换已收口 COMP-LIVE/PI-OPEN 执行包，不恢复旧 lease。owner=/Volumes/Dujiangyan-VM/AI视频日记/repositories/desktop-v32，branch=codex/desktop-v32-20261010；基线8a307df51eef543e0c8d739c16584c8662401c58；fetch后的main=5968e9bf0471a7ff0ea088e87f84febc873de300。保留original脏树。

root负责需求、共享权威、合同、审阅、集成、真实验收、教学与Git交付，不与实施Owner同时写产品文件。当前内部并行按宿主开发指令和用户提供的AGENTS执行，最多三实施Owner，禁止再派下级。每份文件同一时间仅一个Writer；旧实施包均RELEASE。

## 稳定权威

架构§3.8/3.10/3.13、产品§2.3/2.5/2.6、领域§5.2/6/7、认证边界与docs/development-workflow.md。本波不改产品对象/默认许可语义；若实现需要修改这些规则，由Owner提交证据，root按Change处理。临时合同不复制规则。

## 并行拆分与互斥文件

- OBJECTS：apps/api/agent_tools/task_facts.ts、diary_tools.ts及直接对象/工具测试；另独占source_repository.py服务器知识分页检索方法和直接测试、AssistantConversationPanel.tsx及直接测试（仅真实后台回合不出现空用户气泡/编辑空原句，按DESIGN.md§2），非script find_knowledge接共同知识search；script已允许来源范围保持。必要material_tools.ts/retrieval.ts写入仍须先报告root。完整集合上ID/分页读取镜头、字幕轨/条、旁白、音轨/片段、已采用图层；当前版本、单位、关系、计数、明确截断。消费API Owner发布的观察协议，不自行造第二规则。
- OBSERVATION/API：apps/api/app/main.py、schemas.py、quality_inspection.py、task_audio.py、必要新增task_observation.py和直接API/观察测试；所有main/schema整合只归此Owner。OCR实证资源窄包另独占host_resource_probes.py、member_permits.py及test_task_observation_resources.py；预算必填、无生产默认值，root低负载合成校准后方启用OCR。沿原认证/输出/质量及设备协调者提供版本/hash绑定的实际结果观察，禁止任意模型路径读取与原媒体外发。先提交观察/接续接口建议供root核定，再实现。核查素材/知识检索当前库与删除后引用；额外Repository写范围仅root逐项绑定。
- CONTINUATION：apps/api/app/assistant_pi.py、conversation_repository.py、必要新增assistant_goal_continuation.py及直接接续/Pi测试。原目标、身份、版本、待决定与进展持久化；可信业务结果触发有限新Pi回合，保留旧真实结果投影；main整合交给API Owner。不得伪造用户指令/确认，unknown先查原请求；stop/拒绝/撤权/不相关版本变化不得续作。先提交与API Owner可独立整合的调用协议。
- root独占本合同、长期文档、Git/Issue/PR、真实运行与可见验收、临时验收脚本。教学线程只读不拥有产品write lease。

## 验收与旧路径

输入为已授权当前任务目标/明确自造样本，实际读→执行/作业→版本/hash绑定观察→下一有界动作/必要决定→产物与保存；普通用户无需重复说继续。超过8音轨/24片段的尾部可按ID及页读取，图层可发现；素材/知识新增、更新、删除、权限及旧引用分别验证当前事实，不向上下文塞全库。观察技术事实和语义判断分开；没有本地观察能力明确unavailable，不伪装看过画面。

旧目标：摘要截断后再分页、已采用图层不可发现、后台投影作为唯一目标接续、无副作用参数拒绝后固定封死。各Owner以真实消费者退役并给不存在证明；业务结果投影本身保留，是接续的事实输入而非竞争目标系统。GUI既有提案/确认可见界面复用，不新建第二任务/媒体执行器/许可入口。用户必须决定的实际文案与提案采用等沿领域规则；用户决定后自动恢复已批准目标。

最低检查：每包最小直接测试；合流后一次相关API/工具集成检查，再正常已配置模型＋隔离数据的同候选实际异步接续与可见产物、stop/拒绝/重复/旧版本/重启验证。Mock/静态/单测不替代正常模型与媒体。不可验证项单独标记，独立实现继续，不把部分路径算整个AG4完成。

## 运行、交付与禁止项

root沿既有文字连接使用正常模型；验收在独立合成库，不复制私人DB、凭据或原媒体。沿用户此前明确批准的临时退出/验证后恢复授权，若唯一设备协调者要求交接，root正常退出原应用、验证结束后恢复；不替换已安装应用、不取消用户活跃作业。先核当前进程和设备实际决定；未知资源不盲重跑/加预算。新供应商、购买、签名分发、平台发布、数据删除不在范围；不安装REA或新增依赖。分阶段保存并推既有分支，更新#118/#119/#120及#117，保持Draft PR #124，未完成验收不关闭Issue。

## 集成审阅绑定

OBJECTS与CONTINUATION包均已RELEASE；追加的conversation_repository.py直接接续修复、useAssistantConversation.ts直接刷新修复及各自测试也已RELEASE，分别解决旧成功回执假进展和采用后停止查询造成后台回合不可见。API Owner仍独占main等原文件，整合实际制作JobRun终态消费者、真实采用后的合法阶段重绑、仅422无副作用参数纠正、主机关机与真人stop分开、成片发现不依赖质量检查，以及运行中观察输出文件限额。均属当前纵向目标的必要集成修正，无新增执行器/队列或授权语义。

API最终集成包及main.py/test_assistant_goal_api.py窄重绑均已RELEASE。已采用DeleteClip的真实删除证明随同已核验版本链保留，后续渲染结果不会恢复已删除选择；正常API双接续直接回归已纳入同候选检查。root收回这些文件用于审阅、提交与正常模型/GUI验收，实施Owner只读待命。

正常模型验收追加窄包：CONTINUATION独占assistant_pi.py、conversation_repository.py、assistant_goal_continuation.py及直接测试，修复同一原目标已成功图片来源被重复生成，携带真实已完成结果和当前检查点。OBSERVATION/API独占main.py及test_assistant_goal_api.py/test_assistant_script.py，修复真实提案成功预览后未采用却进入渲染的接续门，并消费CONTINUATION发布的已完成来源协议；CI旧测试夹具必须提供真实绑定，不放宽生产校验。原目标/实际采用语义、最多六回合、预算、身份和版本规则均保持。两Owner不得交叉写文件，不运行正常模型或占唯一设备；root继续负责实际采用、同候选验收、文档/Git与恢复原应用。

上述正常模型追加窄包均已RELEASE；root收回审阅与真实验收。实际来源复用不产生新作业/进展，普通作业GET的stale校验保持；同目标的待完成预览与待采用提案挡住其他观察事件。只读审阅发现的跨版本finish消费与采用检查点覆盖已修复，最小相关检查通过，正常模型与普通GUI验收仍由root在同一候选完成，不据测试数标完整AG4。

正常GUI验收追加Web窄包：OBJECTS独占useAssistantConversation.ts、CreationTaskWorkspace.tsx及各自直接测试useAssistantConversationReadRaces.test.tsx、CreationTaskWorkspace.test.tsx。已确认后台采用→新版本出片→最终观察真实发生，但中栏24次业务查询暂停后，实际采用消费者只刷新工作区，未唤醒当前会话。修复按捕获的会话/任务/提案绑定刷新并重新启用原有界查询，保留输入草稿、scope/identity及晚回包保护，不新增无限轮询、授权或队列。root保留合同、Git及真实验收，其他产品文件无新增lease。

CI窄包：OBSERVATION/API仅重新取得main.py和test_assistant_goal_api.py，修复38344749382已报告的两处Ruff未使用异常变量/导入fixture重定义；保持原行为及fixture约束。其余文件无授权写入；修复后做相同Ruff及直接回归并RELEASE。
