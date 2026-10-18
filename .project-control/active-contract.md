# 当前主控与执行边界

## 1. 本轮目标、授权与唯一负责人

用户于2026-10-18明确批准按已固定完整计划开始实现，主控负责派工、审核和集成，连续推进到真实视频制作、连续修改撤销及退出重启恢复，不逐步重复询问。本次授权取代上一轮文档限定和A/B/C旧冻结状态；只恢复下方新绑定的写权，历史任务清单不生效。

唯一主控：本聊天 `01a1429d-9efe-7491-bcb6-d85f090c1138`。完整范围为[开发清单](0.11-development-plan.md)P0–P9，按其批次推进。首轮绑定见第5节；后续按依赖由主控重新绑定，无须为已批准目标再次请求继续。允许范围内源码、相关测试、本机隔离服务/媒体/桌面构建和真实验收；不包含发布、外部群消息、付费新增服务或破坏用户数据。真实平台验收缺租户条件时仅标该项阻断，继续其他工作。2026-10-18用户进一步批准P0内存准入设计必修及P7 Windows适配/实机验收，按产品§12.1、架构§3.12.1及开发清单§1.1.3推进；不以设计缺陷冒充环境阻断。

2026-10-18用户最新纠偏：当前第一步必须是电脑端AI自动剪辑按产品设计跑通，不能将主控精力转向手机联动。唯一当前主线为Mac上接收目标/采用定稿→资料素材匹配→真实配音与粗剪→剪辑台多轮对话精修和人工交替→局部重算、撤销、输出→退出重启恢复。0.11/0.12共同集成范围保留，但手机配对、跨端传输和两端实机联调暂停，不是桌面核心出口的前置；已有代码、账号、安装与证据原位保留，不新开手机线程占用本阶段资源。Windows适配/实机验收继续在下一阶段。详见[产品§1.1](../docs/product-requirements.md#11-版本交付边界)及[开发清单§1.1](0.11-development-plan.md#11-顺序责任与退出条件)。

本阶段A完成桌面鉴权响应性修复，随后围绕桌面文案、制作入口的真实失败推进；C仅继续桌面Agent登录/首启后的会话恢复、账号隔离和页签精修；B资源实现保留，按真实配音/压力证据续绑。下方手机专属绑定全部暂停，不从旧授权恢复。GitHub同步已获用户授权，不等于公开产品发布。

左侧现有名称、分区、顺序、入口绝对不动；中间持续Agent，右侧业务/作品页签按DESIGN 6.2实现。HTML仅获交互方向认可，视觉明确否定，禁止复制其CSS、组件和视觉布局。正式代表页须过shadcn组件来源、设计规范、可见交互门，再推广其他页面。

2026-10-18用户批准吸收竞品审阅中适用的增量；实施仍沿原P0–P9和本合同写权。准确性、局部重算及多轮精修按[开发清单§4.1](0.11-development-plan.md#41-准确性与局部精修的同候选实证)补强实证，原有资料采用、资源政策与正式UI改动保留并按相关新权威审核。报告的旧基线、日期/Issue排期、统一审批、削减专业编辑、关闭Pi原生工具或发布策略建议不成为当前规则；不新建竞品任务波次或大重构。

主控独占项目共享权威、活动合同及依赖/锁文件；可见实施线程只写第5节范围。不派内部实现子Agent。主控执行集成提交，实施线程不自行提交/重置/清理。现有未跟踪原型、重构报告和test-runs保护原位，不纳入代码提交。媒体/模型/完整打包由主控串行排程；禁止靠调大门限或关闭保护求通过，既有错误策略须按新权威修复，不能借“门限不变”阻止已批准的策略纠正。

## 2. 权威与实施入口

| 议题 | 当前唯一依据 |
| --- | --- |
| 三种创作场景、四项默认决定、外部指挥与产品验收 | [产品需求](../docs/product-requirements.md) |
| 委托范围、采用版本、交付变体、任务与输入身份 | [领域模型](../docs/domain-model.md) |
| 三栏、专业剪辑、全站shadcn交互、左侧资源 | [DESIGN](../DESIGN.md) |
| Pi原生工具与业务工具、检索、CLI/MCP适配、职责边界 | [系统架构](../docs/system-architecture.md) |
| 外部调用身份、文件和执行边界 | [认证边界](../docs/auth-workspace-boundary.md) |
| 当前CLI实际命令及未接通范围 | [CLI说明](../docs/cli.md) |
| 工作包顺序、验收样本与待验索引 | [版本开发清单](0.11-development-plan.md) |

权威描述批准目标，不表示代码已经实现。源审、模型/工具可用、GUI、真实媒体、持久恢复、跨端交付分开判断。报告中的提案以本轮已同步权威为准，不恢复旧20%人物默认、九阶段主导航或每步人工确认。

## 3. 有效基线与仍待验证

实现启动Git为 `0deb3da9`，接手时已跟踪文件干净；`test-runs/`、`designs/yaohe-refactor-status-20261014/`、`designs/yaohe-tabbed-workbench-20261016/`为既有未跟踪资料，保留原位。历史大重构代码阶段已收口，不恢复全仓拆分。

产品候选 `2945df1d` 已在同一任务完成实际出片、两次字幕修改、撤销、原生保存、真正Quit与同候选重启；直接证据为 `/Volumes/Dujiangyan-VM/AI视频日记/acceptance/real-loop-20261016/验收结果.md`。约6.67秒的自有图标/导入录音样本不证明工业成片质量、生成配音、30秒满足率、双语言、视觉/听感、封面、五会话或干净安装。CPU超限后按原门限恢复的证据保留，禁止扩大阈值求通过。

已知差额进入具体工作包：成功目标误报；普通安装依赖与生产资源预算；逐条语言配置；自主采用与聊天定稿入口；现有知识字面检索及素材搜索与视觉向量消费者差距；九阶段主导航；Pi内置工具被禁用；CLI未随包交付、MCP与消息适配未取得交付证据。强杀/睡眠/处理中取消、多任务隔离、复杂素材及完整专业操作标为待验，不从短片成功外推。

### 3.1 本轮只读审查结论与代码待改入口

- Pi：`agent_runtime/session.mjs`固定1.3.0并设置`noTools: "builtin"`；`assistant/catalogue.py`仍有限制为业务工具/当前任务/人工确认及20%默认的提示。SDK、实际插话及显式Skills复用；这些旧策略是后续P1/P5/P8的明确调整对象，不因文档更新视为已修复。
- 外部调用：现有`scripts/yaohe.mjs`有任务/素材/编辑/制作/历史命令，但自然语言目标、随包CLI、全写入独立回执仍待补；当前检索范围未发现产品MCP server或飞书/钉钉适配实现。制作与历史控制未知回执限制仍按实际CLI文档执行。
- 业务边界：`main.py::_prepare_creation_request_window`继承整组要求；生成/确认文案路由持有选题、来源和时长规则；`ProductionRepository`仍组装分析/发现/档案并跨私有资产读取；`CreationTaskWorkspace`混合九阶段导航、选择、编辑执行及回读。按第4节触及消费者收敛，不从行数判定全仓重构失败。
- 检索：知识`source_repository.search_knowledge_items`为字面检索；素材`search_material_segments`为文字/结构条件；视觉向量在分析与故事板路径存在，尚未证明用户搜索的语义链路或当前模型质量。文档不得把向量存在等同素材理解完成。
- UI：当前Base UI底座不违背shadcn（官方有Base实现）；公共`ui/`主要是overlay/reference-chips，页面仍有自有按钮/表单/菜单，不能称全站符合。源审覆盖外壳首页对话、素材、知识/文案/来源、人物声音、模板、剪辑/字幕/输出、定时发布、设置运行成员偏好、首启账号法务；本轮未逐页GUI实测。实际九阶段Tabs须迁移；VideoEditor/AudioTimelineEditor已有不少专业入口，现有“裁剪起点保持原长”不能当完整双边trim。

## 4. 后续实施与必要局部拆分

工作包及真实出口见[开发清单第一节](0.11-development-plan.md#1-下一批用户闭环)。顺序以用户结果为准：运行可信与创作授权→资源检索和统一剪辑台→中英粗剪精修→多任务恢复→跨CLI/MCP调用→消息平台→普通用户交付。独立只读/轻量实现可并行，重型模型/媒体/打包由主控串行排程。

产品实施启动前，主控按对应工作包绑定具体文件、单一纵向负责人、公共接口与退役对象。`main.py`、`schemas.py`、`production.py`、AppShell、共享引用/工具schema及公共UI不能多线同时写入。现有三条可见线程可以重新绑定，但旧任务名称与旧文件清单不自动生效；内部审查Agent不获得实现写权。

有用户价值的边界收敛限定为：

- 方案/定稿/执行采用规则归具体业务用例，让GUI、Pi、CLI、MCP复用，不能由各入口重复确认或重入HTTP路由。
- 素材与知识各持有自己的事实和公开仓储入口；检索索引是可重建派生数据。跨对象保存由单一用例组织同事务，清理被替代的私有仓储调用。
- Agent运行桥与默认提示词/工具注册分工明确；原生工具执行环境负责文件/进程生命周期，作品事实仍经业务命令登记。
- 剪辑工作区按资源浏览、选择上下文、时间线编辑、播放/预览、任务状态组织，组件不自行拼另一套版本和授权规则。
- CLI/MCP/平台适配只负责协议、可信身份与回执传输；不建立第二个任务库、调度器或视频执行器。

仅遇到上述真实消费者时做必要拆分；不以大文件、抽象层数量或全面搬迁为验收目标。新默认入口替代旧入口按Migrate要求完成消费者退役与不存在证明；保留合法旧任务读取和用户数据。

## 5. 当前实施绑定：2026-10-18第一轮

基线为0deb3da9。每条线程先读取本合同及目标权威，核对实时Git，回报REBASE_ACK后直接实施，不等待形式化批准。同仓共享工作树，不重置他人改动。发现超出拥有路径的必需修改，只报精确建议，由主控划转；每个文件始终单写。

| 负责人 | 用户结果、权威与独占文件 | 最小验收与禁止范围 |
| --- | --- | --- |
| A `01a14301-2730-7440-ab61-69fd35d9c341` | P0完成状态与P1创作接收。产品§4.2/8/9、领域的委托/采用、架构§3.14/3.15。独占`apps/api/app/main.py`、`schemas.py`、`assistant/catalogue.py`、`assistant_goal_continuation.py`、`conversation_repository.py`、`creation_requests.py`（若存在）、`production.py`中请求窗口采用/回复转文案/文案确认相关事务、`models.py`中相应创作对象及新增`creation/intent_use_case.py`；仅对应`test_creation_request*`、`test_assistant_goal*`、`test_assistant_conversation_repository.py`、`test_creation_task_script_grounding.py`、`test_assistant_script.py`及新增`test_creation_intent_use_case.py`。 | 自主委托允许采用，不强迫重新选题；讨论不启动；明确最终文案保留版本；逐条要求不丢失；完成与输出检查区分，不伪造观察。复用现有版本/幂等与业务事务。先定向现状/失败再改，相关测试并给实际API可验证入口；不写Web、production.py的素材/检索/档案职责、资源仓储或运行桥，不启动模型/媒体。 |
| B `01a14301-7591-74f1-b240-d3643b78ecfe` | P2续段：文档原件按固定版本保留与受权取回，沿首段Word/Excel解析/出处；OCR/语义继续核对接线。产品§6/7、架构§3.6。独占`apps/api/app/knowledge_files.py`、新`knowledge_parsers/`、`source_repository.py`、`source_types.py`、`source_storage_schema.py`、新`knowledge_file_store.py`，`apps/api/tests/test_knowledge_file_parsing.py`、`test_knowledge_office_ingestion.py`及新`test_knowledge_original_files.py`。 | 实际docx/xlsx文件到ParsedKnowledgeFile及公开仓储入库/检索/出处，损坏文件明确失败，空表/公式/合并格保留真实语义。不伪造OCR或向量；不得写main.py、schemas.py、production.py、依赖锁文件或Web；如需新依赖先给精确必要性/既有可用性，主控管理。 |
| C `01a14301-b182-7a92-a973-66fe11baf450` | P3正式代表页：既有导航打开业务/剪辑页签、持续Agent和真实剪辑操作，DESIGN§2/4/5/7及产品§9。独占`apps/web/src/`与对应Web UI契约检查脚本，仅本任务相关消费者；包含AppShell、共享UI、工作区/上下文及测试。 | 保留左栏全部名称/分组/顺序/入口；同业务去重、不同任务独立、关闭不取消；Agent消息草稿不重建；浏览不静默改提交目标；真实资源引用胶囊到原作品。正式shadcn源组件结合DESIGN token，禁止HTML视觉。代表页先验证再推广；退役九阶段主导航而保留合法内部恢复。不得写API、桌面、依赖/锁文件或权威；不全量打包/跑媒体。 |
| 主控 | 共享文档、接口协调、集成、P0运行诊断及P8 CLI会话消费者；独占`apps/desktop/`、`scripts/`、依赖/锁文件及本轮新隔离验收目录；其余未分配源码暂不写。 | 检查真实依赖、服务和资源首个阻断；需要修复先定位明确消费者并绑定路径。审核A/B/C差额，固定候选后串行真实模型、媒体、原生保存、连续修改撤销和Quit/重启。旧样本只作基线，不能替代30秒/中英/正式UI验收。 |

C拥有Web的纵向迁移实现，主控拥有最终迁移验收。旧目标为CreationTaskWorkspace常驻九阶段导航及旧路由切换导致视图/草稿丢失；替代为页签工作区与同一编辑事实源。迁移期间允许分阶段接入现有编辑器，但不得双写时间线或双目标；代表页通过后迁移剩余活动消费者，最终提供活动代码无旧常驻阶段导航、原路由可恢复既有任务、正式UI同候选真实操作的证据。回滚仅限源码候选，保留全部用户任务及历史。

所有线程交付精确文件、实际验证命令/结果、首个真实失败、未验证项、接口建议，不以测试数或静态连线声称交付。主控审阅后推进下一绑定；任何单包完成不结束完整目标。当前允许轻量并行；真实模型、媒体、打包需主控排程。全产品完成要求开发清单的具体接受点，平台/安装缺条件单列。

A续绑同一纵向消费者：`apps/api/agent_tools/diary/creation-plans.ts`、`topic-tools.ts`、`script-tools.ts`、`runtime.ts`及对应工具契约测试。将已保存的委托和逐条要求传递至同一采用用例，退役固定人工确认与20%人物默认；可信回合身份仍由运行桥传入，不接受模型自报授权。讨论模式保持只读/草稿；定稿按原版本采用。A同时负责B原件仓储稳定后的HTTP薄接线，不复制其存储规则。原生工具执行环境仍未授予A写权。

同一纵向链发现的直接消费者`agent_tools/diary/execution-tools.ts`、`task-context.ts`、`app/assistant/result_projection.py`及其定向测试续归A，阶段采用后回读真实对象，消除仅按回合初始阶段隐藏后续能力的限制。新任务回执必须经宿主可信绑定后才能继续，不以模型更改task_id替代授权。

真实给定主题→写稿失败已定位`app/topics.py`的生成输入验证，A续拥该文案输入/序列化和直接测试消费者：明确给定主题可进入文案创作，不强迫补造模型选题元数据；已有模型选题仍保留其真实角度、大纲和来源。

A同时拥有`app/assistant/projection.py`的文案采用状态投影及对应测试；旧固定“尚待人工确认”提示必须读取实际采用回执，不能反向创造业务事实。

真实任务补充资料的直接断点续归A：在现有`creation/`新增具体资料采用用例，`main.py`/`schemas.py`薄入口、`production.py`的Plan引用与Task事件同事务、对应定向测试。请求携带固定资料id/version、预期Plan/Task版本和稳定request_id；复用公开资料权限读取，追加采用后旧稿/生成记录/产物保持历史原貌，新一轮写稿显式消费新引用。已有制作/已采用文案的下游影响必须明确，不能悄悄替换资料、覆盖旧稿或重建任务；缺失/撤权/冲突拒绝，重放回查同一采用回执。不新建通用指令平台。当前稳定批先交主控集成，再推进此直接消费者。

B续段负责P2素材语义检索的真实业务链，新增独占`app/materials/retrieval.py`及定向测试、`app/materials/assets.py`、`analysis.py`、`contracts.py`、`app/visual_semantics.py`的既有向量读取/查询与受许可执行适配。复用现有ChineseCLIP、素材特征及设备许可，模型调用不得绕过调度或在HTTP搜索同步偷偷加载；索引按素材内容和模型版本失效，当前权限/审核/排除先于召回，弱候选明确标识。`production.py`搜索薄接线与应用组装暂由主控协调，不与A同写；B先交明确调用契约，再绑定直接消费者。禁止擅自启动重模型或下载新模型；资源策略修复按下述P0绑定与新权威执行。同时在既有知识解析拥有范围内修复混合PDF静默丢失扫描页的实际问题，未接通OCR时显式报告缺页，不能宣称整份解析成功。

B当前纵向负责人为P0资源策略必修，兼顾已有查询预算消费者：独占`app/host_resource_probes.py`、`app/member_permits.py`、`app/granted_subprocess.py`、必要的具体平台内存采样模块`app/host_memory.py`及直接定向测试。复用唯一设备协调者，按架构§3.12.1同时修正准入/运行，退役交换绝对总量的普遍独立否决；原身份、版本、进程回收与有效任务/CPU/IO/磁盘边界保留。旧query预算差额保留，主控先审阅集成后通知B续作，禁止混写未审基线。B先完成平台数据源语义、统一判定与边界反例；不猜生产预算、不启动压力/重模型、不改用户配置，不另造调度器。Windows本阶段明确采样与进程适配契约及必要源码差额；具体执行适配在Mac策略源码收口后由主控续绑，不以POSIX实现冒充通过。`main.py`、`production.py`、`schemas.py`仍归A；当前任务/资源DTO接线由主控协调，不跨文件抢写。主控独占实际标定、压力实验、桌面/脚本/依赖及同候选验收；未取得对应实机证据不能宣布修复完成。

B续绑已发现的P0直接消费者`app/profile_preview_execution.py`及新增定向测试`tests/test_profile_preview_memory_recovery.py`：独立声音预览的压力中断必须在通用RecoveryBlocked/失败分支前按同一政策处理，保留原请求并经已有调度器释放、等待和续作；子进程回收未知仍保留保护，不能伪装可恢复。仅修该真实消费者，不扩大至档案或配音业务重构；`narration.py`如有必要差额先交精确证据，由主控另行划转。

手机联合实施绑定（当前暂停，仅保留已实现归属；恢复须主控重新绑定）：

- A独占主机正式连接用例：`app/auth.py`、`device_accounts.py`、新`companion/identity.py`、`companion/routes.py`及`companion/pairing.py`及对应定向测试，以及单一`device_member_context`只读RPC的新迁移源码（主控审核后才应用测试库）；继续独占`main.py`/`schemas.py`薄接线；`apps/api/server.py`修正默认loopback并定向验证，避免缺env时匿名服务全网监听。先保持资料追加候选稳定供主控集成，再实现明确工作区选择的服务端成员/设备权限核验、正式邀请管理入口、`GET /api/companion/session`真实绑定回读和手机采样source接线。同账号手机配对的短期票据与接受归A的新pairing用例；原成员邀请仍只由DeviceAccountService/RPC负责；不得用开发成员替代正式身份、自动登记/认领云端设备或擅自应用云端迁移。进度和媒体读取复用所属权限入口，发现同空间私人任务裸读必须修直接消费者。A同时负责B内存policy/query预算的必要薄组装，具体差额先与主控确认，不能猜标定值；续拥`app/bootstrap.py::bind_device_runtime`实际poll参数薄接线。
- B已交资源12文件暂冻结供主控审核；续拥新`app/mobile_transfers.py`、`app/mobile_transfer_routes.py`、`tests/test_mobile_transfers.py`及`materials/assets.py`的必要接收事务入口。声音压力defer后再写owner的原子性审查差额续归B：仅`device_scheduler.py::defer_unit`可选同事务owner回调、现有直接消费者和故障注入测试。补稳定request_id的完整字节接收、长度/摘要验证、取消/重发/回执对账与原子入库；仅在已有素材所属公开入口内接入，不另建素材事实库。HTTP router用显式身份/仓储依赖，main挂载交A；不写auth/main/production/手机客户端、不运行重模型。先反馈精确wire DTO供主控冻结，接收状态可用有限SQLite记录，不把半文件登记为素材。声音继续复用既有采样事务，不另建声音上传事实源。
- C解除手机只读限制，独占`apps/mobile/lib/`及相关`test/`、Android/iOS原生连接所必需配置；已有Web差额继续冻结待审核。实现正式登录/刷新、二维码可信连接、device/member/workspace持久绑定、手机读取同一采样任务/文本版本与稳定请求、素材完整传输及原请求对账、授权任务/结果回看。先按主控冻结协议接通客户端，不猜字段，不关闭TLS校验；如需依赖提交具体清单，主控管理pubspec/lock。不把Flutter Web当原生通过，不自行启动模拟器/模型/打包。
- 主控独占`apps/desktop/`的可信LAN网关/二维码连接配置、`scripts/`、共享依赖与权威；保持核心API/Web的loopback绑定，只在明确启用、正式身份可用时开放受限HTTPS网关。主控审核全部差额、选择固定候选并串行真实媒体/Android安装、局域网/重启验收。Android是用户指定首验平台，iOS仍独立待验。

共享协议先固定在架构§5.1.1，具体HTTP DTO由A/B交付后由主控审核同步给C；实现单文件单写。前序候选先集成，后序不叠写待审差额；无需用户逐步确认。旧手机worktree不继承写权。

同批直接消费者续绑：A继续负责`main.py`/`production.py`的手机传输公开组合入口与路由挂载，及现有成片下载的预期版本检查/版本回执，不另造产物库。真实文案核查的`support_reference`失败在其既有`topics.py`范围补每段可引用片段映射和有界诊断，不放宽来源约束、不盲重试。C的旧Web候选由主控集成后，续拥`app/settings/page.tsx`、`components/SettingsCreators.tsx`、新`components/CompanionConnection.tsx`及专属CSS/测试、`lib/directoryPicker.ts`类型、必要的独立`lib/api/companion.ts`及现有API导出/代理头透传的最小消费者：在既有设置内提供正式主账号开启/关闭连接、同账号配对二维码和到期重建；使用shadcn组件，不动导航名称、分区、顺序，local模式不能伪装正式连接。手机候选冻结供主控打包后，仅输出版本字段薄接线另行续作。主控拥有原生IPC、实际安装、设备证据与正式身份登记决策。

真实正式安装包的启动/诊断边界续绑：主控独占`apps/desktop/runtime/service-supervisor.js`及其生命周期检查，将登录前进程探针复用已有`/api/version`；A在既有`main.py`范围把workspace运行诊断移出匿名清单并保留合法初始化修复访问；C独占`apps/web/src/middleware.ts`、`lib/auth/routing.ts`和直接测试同步公开/修复路由消费者。不能伪造workspace、绕过登录或将模型能力未就绪作为登录前进程启动条件。

正式登录后的响应性与会话恢复续绑：A独占`main.py::auth_boundary_middleware`及直接认证测试，将既有同步身份、成员/设备与首启核验移出事件循环，复用现有线程池；逐请求权限和错误语义保留，workspace上下文仍在请求主协程设置与清理。不增加权限缓存、不延长超时、不新增鉴权框架。C继续独占Web会话Provider与首启/手机连接的直接消费者，先提交登录前失败残留和错误分类证据，再按主控批准的最小方案修复；不同账号不得继承旧会话。主控负责同一安装候选的登录后实际响应、电脑端创作及退出重启验证；手机错误分类修复暂停，已有diff保留。

## 6. 收口门

桌面真实创作续绑：A在既有首页目标接收、任务回合及`assistant_goal_continuation.py`直接消费者中补自主委托采用任务后的可信接续；讨论模式不自动制作，复用当前会话/任务授权与幂等，不新建调度器。A同时拥有`schemas.py::VoicePreviewRequest`目标语言薄接线及直接测试，复用声音预览用例已有的显式语言参数；双语声音能力声明不能直接充当一次合成的目标语言。C独占`PeopleAndVoiceWorkspace`、`lib/media-profiles-api.ts`及对应类型/测试的试听语言选择与回读消费者，不复用另一语言的试听回执。主控仍独占受控模型实测、候选打包和真实闭环；实验配置不自动成为生产资源档。

B运行CPU绑定更新：1秒窗口实测仍在推理期间因1.345秒整机93.2%终止配音，窗口veto实验未通过，不继续扩大窗口或91阈值。B独占`host_resource_probes.py`、`member_permits.py`、`granted_subprocess.py`、`server.py`受许可启动器直接消费者及定向测试，以Mac自检后台CPU调度替代配音运行的整机百分比否决；成功自检后才exec，子孙继承，失败不执行重任务。保持91%新任务准入、内存/IO/RSS/线程/取消/时限与原进程登记ACK回收协议；累计CPU和进程组样本仍为有界诊断，不将缺指标当0。退役配音旧瞬时和1秒运行veto及无效起始窗口控制，不保留旧兼容分支；其他kind待验独立列出。显式实验策略须绑定背景调度机制与证据，旧窗口档不可默默当新档。B还拥有既有`profile_preview_execution.py`和直接测试，修正声音预览text_chars与制作同口径计入参考文本；不调其他数值。主控独占模型、GUI响应和实验配置验证，B不跑模型或打包。

B持续基础资源观察续绑（替换本轮memory-only线程装配）：独占`host_resource_probes.py`、`host_memory.py`、`device_runtime.py`、`bootstrap.py::bind_device_runtime`及原直接测试。按架构§3.12.1由同一HostResourceProbe持有唯一基础观察线程，以既有1秒节奏驱动同一memory_monitor及既有_admission_io真实累计端点，退役内存专属线程，不能每指标新增线程或事实源。准入/运行仍实时检查；IO采样、历史写入、许可切换串行，身份核验不持该锁；运行IO按request/generation隔离。暂停/执行停止超时仍观察，退出停止并join。保持2秒memory gap/10秒恢复、3秒IO窗口及全部资源阈值，不加配置；未知、设备变化、回退、真实断档必须重积，不补零。直接验证慢核验与单任务可同时积满两窗口、并发、失效与单线程回收，主控独占正式原请求恢复及媒体实测。`creation/execution_control.py`中已集成中性等待文案保持冻结。A/C不写上述消费者。

C资源等待文案续绑：独占`lib/api/task-media.ts`及已有`task-media-api.test.ts`、`AssistantConversationPanel.test.tsx`对应文案断言，将通用resource_busy改为与后端同一句中性等待，不改同请求resource_wait的具体原因/数值或重新采样。不新增测试体系/缓存/重试。

B旧编码重复CPU门续绑：独占`host_resource_probes.py::admit_resource`及`test_host_resource_probes.py`、`test_render_admission.py`相关直接用例；核清仅有当前已受设备许可的两个编码消费者后，退役一分钟load average>=cores的第二次否决。保留声明字段/类型/上界、实际线程ceiling、历史load诊断值及HostResourceProbe当前CPU/内存/IO保护；不加新兼容分支、调阈值、改名或扩大调度范围。非许可CreationTask不得因此获准；主控用同任务有效配音恢复实际编码。

B编码文件超限诊断续绑：独占`granted_subprocess.py`、`test_host_resource_probes.py`直接用例。文件大小超限决定当刻捕获actual_bytes、maximum_bytes、output_count（成功stat且计入求和的文件数），附于既有异常与同request/attempt/generation诊断，终止前固定，不事后重采样；stdout/stderr超限无该文件计量，不暴露路径/命令或另建日志平台。主控保持所有当前预算/CRF，单次取得该证据后再标定，不先抬临时/成片上限；不改montage算法。

配音实测诊断续绑：B在既有`profile_preview_execution.py`、`granted_subprocess.py`及直接测试保留同一request/attempt的运行拒绝决定和有界模型阶段/内存诊断，复用现有日志归属；不能只剩通用`resource_pressure`而丢失判定依据。不记录参考文字、凭据或完整请求，不扩大输出预算或新建监控事实库。原始决定缺失的历史实验保持未知，修复后由主控再安排单次受控验证。

真实文案失败续绑：A独占`topics.py`、`agent_tools/diary/transport.ts`、`script-tools.ts`及直接测试，将逐段核查的固定错误类别、段号及可重试性经现有业务错误回执提供给Agent，区分核查报告契约无效与文案事实无依据；不透传模型原始输出、来源全文或凭据，不放宽事实门、不增加盲重试或超时。文案工具明确`content`仅含实际朗读正文，画面署名与许可说明不得混入旁白。给定主题的公开响应允许真实的无模型快照，不能伪造模型配置来满足DTO。

同一消费者薄接线继续由A负责`main.py`现有文案错误响应；`execution-tools.ts`、`task-context.ts`可复用配音阶段现有候选列表与换声音用例，将明确对话要求绑定当前任务和固定声音版本，仍限定现有配音阶段与状态，不因后来更改默认声音而静默修改已采用任务。C在现有`MaterialLibraryWorkspace`、列表/详情组件及直接测试修复页签恢复后的旧素材快照误选：可见时立即复用既有刷新，刷新完成前不创建新选择引用，既有引用保持冻结版本；不新增缓存或轮询框架。

A新增实际文案约束与接续绑定：在既有`topics.py`、`main.py`、`agent_tools/diary/script-tools.ts`、`runtime.ts`、`conversation_repository.py`及`assistant_goal_continuation.py`直接消费者和定向测试，将宿主核验的本轮真实要求纳入逐段核查，保留原方案约束并在核查后复核输入身份；`content`所有文字都会朗读，角标/署名/制作说明属于画面要求，不得作为旁白采用。文案采用工具复用当前任务回读返回实际阶段与缺项，按原自主制作授权接入已有接续，不让讨论/仅写稿启动制作。没有新工具系统、调度器或核查回退。声音写入回执/CAS方案暂仅只读，具体文件另绑。

配音模型峰值优化续绑：A独占`apps/api/model_workers/qwen3_tts_worker.py`、新增本模型专属的`qwen3_text_embedding.py`及直接测试，按已核对上游调用链使用lazy加载，在主参数首次求值前将622,329,856字节BF16文字表替换为原权重按行只读查表，再求值其余主参数。查行保留原始位、顺序、重复、多维索引、ICL/声音/语言/字典和decoder精度；不改原模型文件、不量化降精度、不加跨调用缓存/通用加载器。明确校验safetensors字段/范围、短读/文件变化及行边界，不能在失败时静默回到全表常驻。测试允许微型MLX/nn.Embedding位级对比与真实权重少量行只读核对，不加载完整模型/不生成音频。主控独占受许可实际模型、峰值、输出与界面连续性实测；旧基线未出音频时不得宣称完整音频等同验证。

生成前文案修订补齐：A额外拥有`creation/contracts.py::can_revise_script`及`main.py`文案核查/保存/确认直接消费者、Pi文案工具及直接测试，使同任务在narration/not_started且尚无配音/蒙太奇/时间线时可按明确要求修订。C在其独占`production.py::confirm_creation_task_script`补同事务的活动作业与旧版本保护；已锁定正文仍依原规则拒绝自动覆盖。修订复用现有核查、采用与版本入口，保留旧正文直到新稿通过；不能通过制造失败、删除任务、直接改数据库或绕过核查修复。讨论只保存草稿，不授权制作。两线先交换最小方法签名，不并发写同一文件。

声音采用回执续绑：C独占`production.py::select_creation_task_narration_voice`及同对象原请求查询、`schemas.py::CreationTaskNarrationVoiceSelectionRequest`/对应回执类型、Web`lib/api/creation-tasks.ts`、`lib/types.ts`、`CreationTaskWorkspace.tsx`的声音选择调用及直接测试。复用既有`task_events`在同一事务校验workspace/member、固定声音版本、任务/方案修订及现有narration阶段，写入声音与不可变请求回执；同请求同输入回读、异输入拒绝，旧版本拒绝。Web使用真实当前修订/UUID并在丢响应时查原请求，不保留绕过版本检查的旧写入口。A仍独占`main.py`，C提交业务接口签名与薄路由建议，由A在现有路由接线；C不得编辑main.py。A当前文案修复完成后接Pi声音选择/原请求查询工具，不提前并发写共享文件。主控串行集成与实测。

C配音准备页与资料加载续绑：在现有`CreationTaskWorkspace.tsx`、`creation-task/NarrationPreparation.tsx`、现有`ScriptEditor`定义所在文件及直接测试，移除can_revise_script对声音控件的互斥隐藏，保留修改文案与采用声音并列；传递完整声音候选并展示真实不可用原因，不把过滤后的空数组称为无档案。只采用声音的按钮明确命名，不能假称已生成；触及按钮复用现有shadcn Button/AppSelect，保持设计token和左栏不变。资料请求未完成时展示读取中，不能提前声称当前无资料；复用现有加载状态，不加缓存/轮询/错误兜底框架。主控负责原生页面复验，C不运行模型或打包。

C镜头待确认误报续绑：独占`creation-task/CreationExecutionPanel.tsx`及直接测试，仅在真实失败/中断/取消失败状态展示失败原因；排队中的waiting_for_user复用现有等待说明，不能按旧任务阶段报“配音没有完成”。紧凑与详情消费者一致；同一文件补queued/waiting_for_user镜头全部确认后的原onResume入口，以真实preview.requirements.can_continue约束，不将普通排队变成可重启、不取消重建作业。尚有待审核镜头时仅指向审核且不误允。不得新增轮询或缓存。声音选择本回合已有execution_action结束刷新，未证实的新刷新缺口不修改。

C新增独占绑定`apps/web/src/components/HomeCommandComposer.tsx`及`__tests__/HomeCommandComposer.test.tsx`：真实首页会话携带任务目标保存并采用文案后，复用既有`onTaskUpdated`刷新当前任务；将真实`script_draft`回执纳入刷新条件，保留会话/目标切换的晚回包保护。不新增缓存、事件总线或轮询层，不变更左侧名称与分区。

C续绑正式模式外壳的直接身份消费者：`hooks/useLocalMemberIdentity.ts`及其定向测试、`components/AppShell.tsx`的必要调用点。仅阻止正式模式启动本地whoami/写入本地成员凭据，保持正式设备角色由已有核验入口获得，不新增另一套身份事实源；本地模式既有行为保留，不改左栏名称或分区。手机及设置候选已提交后保持冻结，后续实机发现的具体缺口再划转。

A局部编辑真实错误归因续绑：独占`creation/editing/exact_preview_use_case.py::source_facts`及现有`test_exact_preview_application.py`直接用例。当前真实InsertClip提交了不存在的clip_id，仓储KeyError正确拒绝，但404被统一包装成素材失效，诱导错误换素材。保留404和所有来源/身份/版本校验，对明确的“当前任务镜头不存在”返回准确的目标镜头缺失提示；其他来源失效仍原语义，不透传路径或任意异常。只修此边界，不扩大Agent重试、放宽事实门或写其他编辑消费者。主控负责用实际当前镜头重试原明确片尾要求。

A图文直接保存真实缺陷续绑：独占`schemas.py::CreationTaskExactEditRequest`、`creation/editing/exact_preview_use_case.py`、`main.py::_save_direct_metadata_edit`的动作序列化直接消费者及`test_typed_assistant_edits_api.py`/`test_exact_preview_application.py`相关测试。真实EditGraphicOverlay add已由TextOverlay生成默认id，但exclude_unset递归序列化丢失id及默认值，直接保存409 KeyError(id)。集中保留已校验图层的完整值/稳定id，其他patch动作仍仅提交显式字段；原请求输入摘要和journal重放语义保持，不能每处复制另一套默认规则或要求模型捏造id。验证最小图层请求经实际HTTP事务保存、同UUID回读不新建图层/版本、撤销，且未触及音频/画面参数不回默认；不运行模型/媒体，不新增通用框架。

B字幕与分镜单位纠正续绑：独占`montage.py::build_for_creation_task`、`creation/narration_stage_use_case.py::retime_for_segments`及原直接辅助消费者、`test_montage_pipeline.py`/`test_task_montage_duration_policy.py`/`test_narration_stage_application.py`直接用例。按产品§8及领域§5.4复用固定配音段身份与真实时长建立叙事区间，字幕48字符/8秒显示cue继续保留；退役每cue必有镜头及按单cue时长重排镜头的旧假设。短字幕不能产生单词闪切，素材不足显式失败，不循环/变速补齐。保留原配音产物、字幕时间/内容、人工镜头和既有版本/撤销事务；局部重生成按真实段落关联，不能给所有段落新ID或改文案。先以真实英文四段7.68/5.36/6.64/5.68秒与10条字幕复现，再补一个局部时长变化反例。核对已有公开重新生成故事板入口及缓存身份，未经明确重新生成不改历史计划；不写A独占main/schemas或另建阶段/事实源，路由若有必需改动先报主控。禁止模型、媒体或打包。

主控文字图层成片续绑：独占`creation_execution.py::cpu_render_facts`及`test_image_render_contract.py`直接用例。真实文字图层已经保存，现有准入却仅准PNG，与既有TextOverlay/Pillow栅格化和正式图层操作不一致。仅放行已校验text图层复用现有栅格化；image仍逐项核验登记来源，未知类型及arrow保持当前阻断。文字纳入原图层数量/画布/manifest输入摘要与同许可临时文件预算，不新增执行器、降画质或改资源阈值。先验证文字改动使摘要变化、无需假PNG源但图层计数不减、混合PNG仍受身份/来源门及原未知类型保护；主控负责当前两文字层实际渲染。A仅只读重算入口方案，未获该文件写权；B仍只写其planner/retime消费者。

A显式故事板重算续绑（原主控cpu_render_facts小修已提交2a3d7192并冻结）：独占`creation/contracts.py`的编辑类型枚举和支持集合中仅登记`RegenerateStoryboard`、`schemas.py`/`main.py`的montage/plan及原请求查询薄入口、`creation/execution_control.py`、`creation_execution.py`既有creation_job/montage_plan派发消费者、`creation/montage_stage_use_case.py`、`production.py`相关公开请求与版本事务；必要时新增本用例专属`creation/storyboard_regeneration.py`组织业务，不新增通用调度/任务库。复用既有TaskRuntime、设备许可与EditChangeSet/confirm_direct_edit事务，request_id+期望timeline版本/序号+采用文案/现有input_digest绑定真实配音和来源。将固定409的旧入口改为202显式排队重算，原请求查询支持丢失202后的定位；同请求同输入回读、异输入/旧CAS拒绝、重启不重复提交。旧计划保持可读至同一事务提交新版本，原快照/Undo和未变字幕、图层、音轨、声音保留；不以删除计划、伪改状态或通用force触发，普通start/resume不重算。旧运行/未释放单元必须拒绝；已停在waiting_for_user的旧制作安排可由调用方先沿现有取消入口解除并回读，不新增用户确认门。新作业仅重算故事板，不偷偷重配音/渲染。A还独占现有execution-tools.ts及runtime工具注册的必要消费者，将明确对话重算接该用例、真实回执和原请求查询；不改Pi通用运行循环。测试仅原execution/creation resource/montage stage/typed editing和工具定向模块，许可前不运行、失败/取消/CAS冲突不覆盖旧稿、提交后恢复/撤销及CN人工稿不自动变化。B只拥有planner/retime及其测试，C冻结；A不编辑B文件、不跑媒体/模型/打包、不写权威。

B编码运行CPU续绑：独占`host_resource_probes.py`的CpuRenderBudget/策略装配/准入运行判定、`member_permits.py`/`granted_subprocess.py`/`server.py`既有受许可启动器CPU策略直接消费者及原对应测试，必要bootstrap仅策略薄装配，不写A的creation_execution/业务重算入口。真实CN六镜头/两文字层编码在normal/可用4.22GB、IO/磁盘通过时因0.104秒整机96.3%>91被终止，属于编码尚保留的瞬时运行veto。复用已验证darwin_background自检/子孙继承机制覆盖纯素材creation_render（其FFmpeg/FFprobe子单元同一许可），退役该kind旧整机百分比运行否决；91%新任务准入及原内存压力/系统余量/RSS/线程/IO/磁盘/取消/90秒保护全部保留。缺少明确策略或自检失败不执行重任务，不默默回退旧veto；其他kind/平台不从本次外推，不能加通用新调度层或抬门限。先沿原预算策略解析给出显式CPU_RENDER_RUNNING_CPU_POLICY_JSON及同一真实证据，验证CPU尖峰下单元仍有进展、背景状态真实回读、临界压力/未知/取消/释放仍拒绝；主控独占正式编码/UI测量。本批不得运行模型、媒体或构建。A只读这些文件，若重算需新jobkind接线先报主控，不能并发写。

B中间片性能续绑：在0653a8ae编码CPU改动已提交冻结后，B独占`montage.py::_render_serial_picture_input`及其中间片专属校验直接消费者、原相关测试。真实1秒/30帧同滤镜同背景策略测量：CRF0单线程19.55秒，rawvideo/NUT单线程2.25秒；两者解码93312000字节像素SHA256一致，4线程触20秒诊断期限不算提速证据。仅将硬切串行内部暂存片替换为自描述NUT/rawvideo，保留yuv420p、尺寸、帧率、精确帧数与无损像素；显式concat时长保障接缝。中间片校验只在该内部路径接受rawvideo/NUT，最终H264/AAC与普通预览H264门不放宽。继续原许可/进程组/系统余量/RSS/线程/IO/磁盘/取消及90秒期限、同一3GiB有限测量额度，不引入新编码器/配置开关、不下调画质或改用户源片。复用既有probe/执行入口，补直接命令与校验拒绝反例；B不跑媒体/模型/构建，完整900帧、接缝、成片和峰值由主控真实验证。A仍独占montage_stage_use_case及creation_execution，不写montage.py。utility调度审查仅只读，未经后续绑定不实施。

故事板回执薄适配续绑：A继续独占`assistant/result_projection.py`、`main.py::_finish_task_assistant_turn`、`schemas.py::CreationTaskAssistantTurnResponse`及直接测试；识别两重算工具的真实回执/未知/拒绝，按原UUID和身份回读并沿原输入隔离投影、持久化专用`storyboard_regeneration`字段，不借用视频完成或通用execution_action。专用结构与C先对齐：`{kind,action,receipt}`或`{kind,request_id,query_url,failure}`；succeeded只表示故事板已存，不表示视频。C解除仅此消费者冻结，独占Web`lib/types/assistant.ts`对应响应类型、`HomeCommandComposer.tsx`既有task刷新条件、`AssistantConversationPanel.tsx`持久回合摘要及对应直接测试，沿原会话/revision晚回包保护，显示真实排队/已保存/失败/原请求未知并不伪造完成。正式组件和token不变、左侧名字分区顺序不动，不新增页面、缓存、轮询或调度；不触碰A/B文件，不跑媒体/模型/全量打包。两线先交换精确接口再实施。

B主动编码优先级纠正（C2，替代前述纯素材编码强制BG绑定）：已冻结的A/C和B中间片实现保留；B重新独占`host_resource_probes.py`的RuntimeCpuPolicy/SceneRuntimeLimits及直接判定装配、`member_permits.py`、`granted_subprocess.py`、`server.py`和原直接测试。固定1秒/30帧两文字合成，原Darwin BG首次18.76秒/预热5.22秒，正nice5且BG0为1.22秒，两个首轮输出MP4 SHA256完全相同；仅是受限诊断而非完整产品验收。按架构§3.12增加显式darwin_nice策略及正nice值（本候选实测5），CPU成片不得继续按BG-only布尔值分发，配音仍沿已验证BG。原许可ACK后、exec前用原生setpriority/getpriority校验指定正nice及BG0，明确固定启动入口、子孙继承与诊断marker；失败/缺策略/未知平台不执行重任务，不复活瞬时CPU veto或静默回BG。类型字段沿唯一策略对象传递，退役CPU成片旧BG-only消费者，不建新执行器/通用调度层。91新准入、原生压力/余量/RSS/线程/IO/磁盘/90秒/取消/PGID释放和质量全部保留。仅直接测试和轻量原生父子优先级回读，不媒体/模型/构建；主控以同候选正式30秒编码、前台输入/播放与压力保护验收。A/C继续冻结不写这些文件。须重读本段及架构§3.12并REBASE_ACK后实施；用户已授权修复真实性能问题，无新用户确认门。

B无损中间片IO纠正（C1，接续已冻结nice实现，替代raw格式专属绑定）：B独占`montage.py::_render_serial_picture_input`、`_probe_serial_picture_input`及`test_montage_pipeline.py`直接用例。75d30d96真实nice任务在两片raw完成后因IO953MB/s>786MB/s被原保护终止，原上限不改。固定30帧诊断FFV1 level3/coder0/context0/g1/slices4/slicecrc1/NUT从93.31MB减至23.14MB，1线程1.67秒/2线程1.00秒；解码像素与原raw/CRF0一致。仅把内部rawvideo替为该明确FFV1参数，保留NUT、yuv420p、30fps、逐片实际帧数、显式concat时长及最终H264/AAC/CRF23门；内部probe显式用context.threads约束FFV1解码。去掉把未压缩字节量当压缩输出下界的开工拒绝，原实际文件合计、每命令-fs、同许可临时额度和未知/缺帧拒绝保留，不加估算式放行/新缓存/新执行器或用户格式兼容层。4线程最终合成实测39总线程触32上限，禁止增加32；2线程合成实测25线程/RSS412MB/0.795秒，仍低于原上限。主控仅将本次固定任务的编码并行度配置为2，维持32总线程、1GiB RSS、90秒、32MiB最终、3GiB临时及所有CPU/内存/IO/磁盘保护，完整产品峰值待实测。B只跑直接测试不媒体/模型/build，A/C及六个nice文件继续冻结；编码质量不降低。

A实时许可HTTP连接复用续绑（C1，授权语义不变）：A独占`device_accounts.py::DeviceAccountService.creation_permit`的传输依赖、`main.py::_verify_creation_identity`薄装配和现有应用启动/关闭的该传输生命周期，以及`test_device_accounts.py`直接适配门/必要本用例专属测试。源审发现每个grant核验新建DeviceAccountService且httpx.post重新建连接；同一原设备/用户/工作区的只读实测fresh请求.608/1.248秒，复用连接的热请求.347/.784秒，均真实RPC返回ready，不能把许可判断缓存。只复用应用拥有的线程安全HTTP连接池，每次仍以当前固定设备证明及当前user/workspace调用原RPC，保持5秒超时、TLS验证、当前身份/撤销/unknown拒绝、独立请求headers/body；不全局保存用户Authorization、不缓存许可结果、不减少核验点或放宽离线/身份规则。应用组装入口明确创建和最后关闭传输，先停使用者再关闭，二次启动能新建；设备服务不反向依赖main，不改数据库/RPC/密钥/角色或其它业务入口。真实keep-alive请求复用且后续false/unknown即时反映；跨工作区/用户参数不混，关闭与重启生命周期可证。仅直接测试不媒体/模型/build、不写B/C文件或共享权威，保留已冻结storyboard功能。

主控完整工作量测量续段（不是性能修复结论）：0b781202/9a04254e候选原90秒在六个中间文件（按计划共900帧）生成后、末次探测/最终合成之前耗尽；临时峰686554581B，IO/RSS/线程/内存门未触发。完整链还包括已测约24秒的最终合成、启动/探测及发布，原90秒不是该工作量的完整校准档。主控允许一次固定180秒完整测量窗口（约90+24+探测及安全余量），用于获得全阶段耗时和输出，保持同一二线程、总32线程、1GiB RSS、IO786234344、最终32MiB、临时3GiB及原压力/取消/PGID保护；不在运行中延长许可，不因到期继续递增，不计为生产档或性能优化通过。A连接复用在源码独立推进，主控当前已打包候选运行不受其脏树影响；最终同候选需在已审核集成源及固定环境再次实测。

C任务版本冲突恢复续绑（C1，现有CAS不放宽）：f314真实英文多轮编辑后，清除过期引用只清胶囊而发送仍携带旧任务版本，后端在建回合前正确拒绝；当前错误消费者只保留草稿/错误，未触发任务回读。C独占`apps/web/src/components/HomeCommandComposer.tsx`现有错误分支及其直接测试、必要现有API错误类型适配；仅在机器可识别的任务版本冲突后复用onTaskUpdated刷新，保留未发送草稿、引用固定版本及可执行提示，等待最新任务事实后由用户核对再发送，不能自动重发/丢草稿/绕过后端CAS，也不能对所有错误盲刷新或造新轮询。核实实际HTTP错误字段再实施，测试真实冲突分类、回读触发、保留草稿及不自动提交，非冲突错误不改；不写API、共享权威、不媒体/模型/build。主控集成并复验该普通交互；当前运行中的独立打包候选不受源码差额影响。A/B其他源码保持冻结。

同一C1版本冲突机器码薄接线归A：仅`main.py`第7864行所在任务助手发送前版本门及直接HTTP测试，将409纯字符串detail变为现有传输已支持的`{error: "creation_task_timeline_version_conflict", message: 原提示}`，保留409、检查条件和拒绝发生在建回合之前。A/C先对齐DTO；无身份/权限/CAS放宽，不做其它错误码治理。其余A源码仍冻结，主控审核后统一构建。

同一集成候选完成真实输入到输出、连续三类精修与人工交替、撤销和重启恢复；正式UI满足左栏保护及shadcn/DESIGN并通过实际可见操作。当前收口先以Mac电脑端核心闭环为准。手机联动恢复后必须按开发清单§1.1.4在同一集成候选完成两端真实输入、制作回看与重启恢复；Android/iOS分别列证据，任一未验不写全平台完成。CLI/MCP及平台按各自真实客户端单列证据。未运行、缺配置、模型不可用或仅测试通过不得写成通过。资源设计缺陷、预算准备未实现和Windows适配未实现不得归为环境阻断；真实配音/视频可工作与高压保护恢复两面均须通过，Windows独立平台实机门移至下一阶段，不作为当前Mac桌面核心收口条件。文档变更检查链接与唯一权威，代码与运行证据分别记录。活动合同只保留当前执行绑定，历史通过Git追溯。
