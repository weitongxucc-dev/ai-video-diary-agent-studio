# 当前主控与执行边界

## 1. 目标与授权

用户已批准主控按[开发清单](0.11-development-plan.md)P0–P9派工、审核、集成并持续推进，不逐步询问是否继续。当前第一优先级为Mac电脑端“30秒脱壳机介绍，中英文各一条”：接收目标/采用定稿、素材匹配、真实配音和视频、多轮对话精修与人工交替、局部重算、撤销重做、输出保存、同一候选正常Quit后重启恢复。测试、计划或派工完成均不是完整目标出口。

唯一主控为本聊天 `01a1429d-9efe-7491-bcb6-d85f090c1138`。本合同取代所有历史实施绑定；旧合同、旧线程名称和交接不恢复写权。完整范围与未验证项沿开发清单，历史实现和验收查Git与原始证据，不在本合同复制完成度。

手机联动保留0.11/0.12共同集成范围，但配对、传输、实机联调当前暂停，现有账号、安装、数据和代码原位保留；桌面核心闭环后另绑恢复。Windows适配和实机验收在下一阶段，不作为当前Mac核心出口前置。

允许范围内源码修复、直接测试、隔离本地服务/桌面构建及真实模型/媒体验收；GitHub同步已获授权。范围不包含公开发布、签名分发、外部群消息、新增付费服务、破坏用户数据或擅改账号权限。MiniMax连接复用仅限mobiletest01验收，密钥只留本机受保护存储。素材可用公开网络样本，但必须保留出处和许可，不声称是用户工厂，不编造厂商、型号和性能。

左侧现有名称、分区、顺序及入口绝对不动；中间持续Agent，右侧业务/剪辑页签按DESIGN。已否定HTML仅说明交互方向，不得复制视觉。正式UI沿shadcn和设计规范；源码、测试或截图不替代可见真实操作。

## 2. 唯一权威与执行事实

| 议题 | 入口 |
| --- | --- |
| 产品结果、创作授权、验收 | [产品需求](../docs/product-requirements.md) |
| 对象、输入采用、会话与版本 | [领域模型](../docs/domain-model.md) |
| 三栏、页签、专业编辑和组件 | [DESIGN](../DESIGN.md) |
| Pi、业务边界、检索、资源与程序调用 | [系统架构](../docs/system-architecture.md) |
| 身份、Workspace和文件边界 | [认证边界](../docs/auth-workspace-boundary.md) |
| 工作包、顺序、具体接受点 | [开发清单](0.11-development-plan.md) |
| 当前真实CLI命令 | [CLI说明](../docs/cli.md) |

权威描述目标，不证明已实现。实时SHA/status、源码、服务、CI和实际输出分别是对应事实源。接手先读本合同及相关目标章节，不读取整个docs或历史输出；当前直接验收证据入口为本机`/Volumes/Dujiangyan-VM/AI视频日记/acceptance/phone-loop-20261018/电脑端核心验收.md`，只按其具体链接定向取证，不遍历历史生成物。既有短片、跨候选恢复或独立脚本不代替最终同候选闭环。

## 3. 协作与写入规则

只使用用户已授权的可见A/B/C线程；不派内部实现子Agent。主控拥有共享文档、依赖/锁文件、合同、审核及集成权，实施线程不自行提交、重置或清理。一个文件同时只有一个负责人；未分配源码不写，扩文件先给精确必要性由主控划转。复杂共享契约变化沿project-authority-harness，已批准范围内的局部修复不新增批准链。

优先复用现有用例、仓储事务、Pi能力与唯一设备调度；不因大文件或验收失败恢复全仓重构，不增加并行事实源、调度器或猜测式兼容层。入口只做适配，业务不反向依赖UI/HTTP；跨对象一致写入由单一用例在同事务组织。已发现软件缺陷按事实修复，不列为环境阻断；不能放宽身份、版本或资源保护求绿。

保护现有未跟踪`designs/yaohe-refactor-status-20261014/`、`designs/yaohe-tabbed-workbench-20261016/`及`test-runs/`，不纳入集成、不删除。先核相关diff，不覆盖他人改动。

## 4. 当前实施绑定（本轮批准，取代暂停前绑定）

按开发清单§1.1.1a推进；方案修订、错误回执、短事务为三个独立轻量工作流，集成后立即进入真实目标试验，不等全部远期功能。实施线程先重读本节和本次权威差异，REBASE_ACK后直接实施。已有声音补丁保留并复核；旧认证修复已集成，不重复改造。

| 负责人 | 独占生产文件与直接验证 |
| --- | --- |
| A `01a14301-2730-7440-ab61-69fd35d9c341` | `apps/api/agent_tools/diary/creation-plans.ts`、`workspace-tools.ts`、`recipe-tools.ts`；`apps/api/app/assistant/catalogue.py`、`apps/api/app/creation/plan_preflight_use_case.py`；`apps/api/tests/test_creation_request_api.py`、`test_creation_plan_preflight_use_case.py`、`test_assistant_edit_capabilities.py`及新增`test_creation_plan_revision_tools.py`、`apps/api/agent_tools/tests/creation-intake.test.mjs`（仅查询参数兼容请求桩，保留原断言）。复用既有请求仓储，补方案读取、应用新要求/重规划/采用及声音发现，保证参数不被静默忽略。 |
| B `01a14301-7591-74f1-b240-d3643b78ecfe` | `apps/api/agent_tools/diary/transport.ts`、`runtime.ts`、`execution-tools.ts`（仅perform的提交前动作标记与对应回执）；`apps/api/app/assistant/projection.py`、`turn.py`；`apps/api/tests/test_assistant_pi_failures.py`、`test_assistant_pi.py`（仅SDK参数校验回执既有断言）及新增`test_assistant_business_failure_receipts.py`。补安全结构化错误、持久回执、提交效果与超时归因；不改A工具或main路由，所需业务错误由A按共同约定返回。 |
| C `01a14301-b182-7a92-a973-66fe11baf450` | `apps/api/app/device_scheduler.py`、`apps/api/app/creation_execution.py`（仅Owner核验相关）、`apps/api/tests/test_device_scheduler.py`、`test_creation_execution.py`。补绑`apps/api/app/`下`device_runtime.py`、`profile_preview_execution.py`、`task_audio.py`、`material_ingestion.py`、`materials/retrieval.py`、`automation_runtime.py`、`platform_publish/queue.py`、`creation/cover_use_case.py`、`creation/quality_execution.py`、`creation/quality_inspection_use_case.py`、`profiles/records.py`（仅档案所属store公开只读事务观察，复用既有授权规则），仅拆分现有Owner完整核验与短事务本地核验，及新增`apps/api/tests/test_scheduler_owner_local_checks.py`；`apps/api/tests/test_quality_resource_owner.py`仅更新最终候选新鲜采样对应计数断言。不改变业务授权策略/其他用例，不引入对公共调用者开放的跳过认证开关；main回调需接线先报主控协调A。 |
| 主控 | 权威/合同/计划、审核和集成；接回`apps/api/app/main.py`与`schemas.py`（A已交回），仅跨线程错误事件/受控Owner回调接线及当前创建修订的集成修复；`apps/api/app/production.py`仅既有request_key只读回查公开入口及档案store本地观察的薄转发，复用当前请求表和唯一档案存储；`apps/api/tests/test_assistant_conversations.py`及新增`test_business_failure_event_routes.py`；`apps/desktop/`、`scripts/`及本机验收目录。其他生产文件需先显式绑定；不与B写runtime，不同时编辑各线程测试。 |

目录内同名文件以上表完整父目录为准。不得自行提交、修改共享权威/依赖/锁文件、启动模型/媒体/完整构建或修改用户验收库；只运行直接轻量测试。生产资源标定、构建与媒体由主控串行安排，构建来自已审核提交；未触及的正式Web构建可复用，实际候选按build identity核对，不以旧SHA当新结论。

补充当前真实试验发现的最小绑定：B独占`apps/api/app/topics.py`与`apps/api/tests/test_creation_task_script_grounding.py`，仅收紧既有核查报告引用schema、补安全有界拒绝子类诊断及直接测试，保留事实/引用/段落校验，不自动纠正模型引用、不增加盲重试。主控在`schemas.py`补既有素材检索结果的`evidence_excerpt`与`needs_confirmation`两项透传，不增加新检索器。

另绑A：`apps/api/app/assistant_goal_continuation.py`及其直接测试`apps/api/tests/test_assistant_goal_continuation.py`，仅新建目标的真实输出时长验收与可执行未完成事实投影；复用已有目标接续和InsertClip等编辑原子能力，不能手工补尾、伪改元数据、将旧方案时长强加给后续剪短编辑。main薄接线仍主控负责。若直接测试入口名称不同，先回报精确路径，不扩写其他文件。

C补绑`apps/api/tests/test_device_runtime.py`，仅将既有Owner测试夹具同步当前本地复核协议，并将虚拟时钟改为随实际观察tick而非每次内存读取推进，保留原行为断言与生产资源门槛。

B补绑`apps/api/agent_tools/diary/script-tools.ts`与`apps/api/agent_tools/tests/script-failure-feedback.test.mjs`，保留既有文案拒绝中服务器验证过的字段、实际段号、片段序号和字段摘要；同步其已绑定topics/transport/projection/直接测试。main错误出口由主控薄透传。既有核查已收集的问题按原上限12项完整回传，每项绑定本次稿件摘要；经格式验证的核查说明可作为最多300字符的非指令数据保留，沿现有凭据/路径清理，不传原始响应、未知字段或异常正文。无效定位不得携带该项说明；不新增核查器/诊断系统，不放宽事实、引用、授权或报告有效性检查，不将画面/标题拒绝误称正文拒绝。

A补绑`apps/api/app/creation/intent_use_case.py`及新增`apps/api/tests/test_creation_script_goal_authority.py`：仅沿已采用请求的现有原目标/来源/任务绑定，为两条文案入口解析可信用户目标；目标委托不将Agent自拟文案当固定正文。复用公开仓储，源不匹配明确拒绝，不新增存储或放宽可见性。main调用由主控负责；topics仍B独占。

A补绑`apps/api/app/conversation_repository.py`与新增`apps/api/tests/test_assistant_execution_input_source.py`：仅修复已采用目标的制作提交来源核验，沿已有creation_adoption回到真实首页输入，不能把内部任务目标根当真人输入。复用仓储公开入口，保留新真人输入、已消费追加输入、中断、身份、任务及目标修订的失效保护；两条派生任务不互相替代。主控负责main薄接线及已有路由测试；不新增状态表或并行授权规则。

上述A来源绑定扩至同一缺陷的后续消费：派生child实际消费追加输入后，沿既有`_register_task_goal`和输入回执登记新目标修订并继续接收业务结果；保留原采用来源，不让未消费/撤回/中断输入、无实际业务效果的文本或兄弟任务继承授权。验证写在该新增来源测试中，不新增目标存储。

C接回`apps/api/app/assistant/turn.py`、`apps/api/agent_tools/diary/transport.ts`（B已结束前批），补绑`apps/api/app/assistant/process_runner.py`、`apps/api/agent_runtime/host.mjs`，仅按架构§3.14的实际模型/请求阶段计时及SDK取消接线；不调大180/120/20秒既定限制，不新增重试或任务调度，不改B当前topics/script工具。直接验证独占新增`apps/api/tests/test_assistant_phase_deadlines.py`及`apps/api/agent_runtime/tests/phase-deadlines.test.mjs`，可运行原直接回归但不改其他测试。main由主控补已知接续异常的安全落库，并独占`apps/api/tests/test_assistant_goal_api.py`对应回归；未知异常保留通用兜底。

C另接回`apps/api/agent_tools/diary/runtime.ts`及补绑`registration.ts`，仅沿既有AsyncLocalStorage调用域传递SDK实际toolCallId给请求阶段事件，防止并行工具互相延长等待；不改变业务参数、工具授权或回执投影。B不再写这两个文件。

A接回`apps/api/tests/test_assistant_pi_failures.py`、`test_assistant_pi.py`，仅把旧非流式`subprocess.run`模型桩适配为统一阶段runner，保留原失败归因、安全和停止断言；不得恢复生产双运行路径或伪造模型成功。真实版本探测仍用原subprocess桩；阶段超时文案沿实际新语义，未知写入断言不能放宽。

## 5. 共同接入约定与接受点

A/B失败接口沿HTTP detail与既有工具details.failure，最少安全error/code、reason、repair_action；新字段按有限白名单保留实际阶段、提交效果及原请求/对象定位。不透传凭据、路径、原始异常或上游body；已有script failure消费者保持兼容。A提供具体窗口版本/输入摘要冲突而非一律409文字；B不根据未知5xx断言零副作用，未知结果先查原请求。需共享schema或测试文件扩权先由主控登记，不暗中多写。

A接受：已有proposed窗口传新goals会保存实际新版，不自动确认旧窗口；读/修订/重规划/采用可由Agent完成，原委托下无人工中间门；讨论不创建任务、相同采用不重复、旧修订被拒绝、所选资源变化/撤权拒绝，无关资源不随意使采用失效。只对证实的依赖过宽做最小修改，不能移除执行前核验。六次409的精确历史原因仍未知，新试验必须保留首个实际拒绝。

B接受：业务拒绝在模型和持久事件均可读，网络断开/未提交/明确拒绝/未知效果可区分；安全字段不泄露原始数据；慢工具或业务冲突不被整体180秒超时覆盖成模型服务失败。不靠延长timeout或增加盲重试通过。

C接受：慢远程身份核验/等待式采样期间普通写事务能完成；未到retry时间不触发昂贵核验；观察后取消/源版本变化不授予旧工作；身份未知、资源unknown仍拒绝；文件许可账本与SQLite之间的崩溃恢复、单一重任务许可、执行前核验和公平性保留。不缓存授权、不加长锁超时、不新增调度器。

实施中发现需要后续输入来源、配音/字幕时长或接续修复，由主控沿既有P0–P9追加精确绑定；不是结束当前目标或重新询问继续的理由。

## 6. 集成、真实验收与长时长边界

本轮核心出口：全新会话的一次自然语言目标，自主产生中文/英文各30秒的新作品，真实配音、画面、字幕、出处许可可核实；不人工传内部ID、代写文案、补镜头审批/尾段或修数据库。旧两条精修/恢复证据只作回归参考，不算本轮自动完成。

先审diff和直接测试，再同一已冻结候选执行目标。遇失败先读取具体业务回执和原请求实际状态，修软件后形成新候选，不重复同样失败输入盲跑。有效上游按实际依赖复用，原失败请求和产物保留历史。中英都完成后再做连续不同类型精修、撤销/重做、保存/实播、正常Quit、同二进制和userData重启回读/再播放；中途换包不算同候选恢复。

既有180秒、6镜头、1080p/30fps、2编码线程/32总线程、1GiB渲染RSS、32MiB输出及3GiB临时额度是固定工作量实验档，不是生产profile；原native压力/余量、IO、进程组回收保留。生产档需实测有效运行/峰值/临时空间，不能因为长片按比例扩大内存或在失败后随意加时。不得关闭用户其他应用求通过。

30分钟/3000分钟沿架构§3.7.1和计划§1.1.1a安排，不改600秒上限冒充支持。当前优先修复不新增分片引擎；用已有段/作业/依赖完成有界执行所需的最小消费者接线，缺口按真实量测逐项绑定。长内容真实出口独立于30秒；规模模拟与静态设计不算50小时全长制作验收。手机恢复和Windows仍按第1节优先级。

主控完成权威差异、链接、独占文件与diff检查后提交文档基线，向既有三线程发AUTHORITY_CHANGED。实现按最小相关验证审核后集成同步；完成的是具体接受点，未验证的专业操作/检索/生产压力/跨端不得外推通过。
