# 0.11 main 主控与当前执行合同

## 本地主入口

用户于 2026-10-07 明确要求建立 `main` 目录，后续以它为准，并清理重复、闲置的本地目录。

- 唯一主开发与集成入口：`/Volumes/Dujiangyan-VM/AI视频日记/repositories/main`。
- 分支：`main`；上游：GitHub `weitongxucc-dev/ai-video-diary-agent-studio` 的 `origin/main`。
- 建立时核实的产品代码基线：`584ccc362c0e03a7647d655f22014f30e75022b1`。后续最新提交仍以实时 Git/GitHub 为准，本合同不维护完成度副本。
- 本目录有独立 `.git`，不依赖 `original` 的 Git 目录，也不沿用旧目录的私有配置、用户数据库或依赖软链接。
- 产品、领域、架构和安全规则按 [文档路由](../docs/README.md)读取；未合入分支的代码与文档不自动成为本目录的产品权威。

## 当前执行边界

用户最新批准 `soft-cards-v3-all-right`，以[DESIGN v5.3](../DESIGN.md) §3–4/§5.10/§7为准，取代v5.2浮层尺寸与居中规则；普通直接草稿、撤销/重做与领域版本语义不变。保留已认可的左/中栏、13px/56px和命名，不推倒重做。64/67/68/69/70/72/73源码保留，B71集中GUI因画幅保存409以failed结算且已全释放；UI74修复已审并组合clean候选3db903441af97f359ccf2180f8f2fb4944bd0542，B75定向补验已结案并retain，保存场景通过，剩余GUI受工具阻断未验；本轮全部服务/GUI权已收回，不从下节旧条件START重新运行。旧B66候选与验收几何失效，Dispatch已fence；准备资料保留，不取得旧START权。主控独占共享权威、审核、机械集成、PR/Linear；不编写产品代码。本轮资源/模型暂停已由用户“继续吧”解除；UI96已完成并释放；UI98已failed部分结案并retain，原账号运行/源码权结束；用户最新要求优先核心对话创作，UI100已源码交付retain；B106已succeeded打包结案并retain，UI107已succeeded结案；B108已succeeded结案并retain，自有运行权结束；当前无执行线程产品/服务权；UI105已交源码并retain，UI102/B103/B104亦已结案，旧源码/核验权结束；B99已部分结案retain、账号运行权结束；B97已succeeded结案、服务/媒体全释放，旧RERENDER_START不恢复；B94/95源码写权及B92/UI93/UI96旧运行权已结束，历史START和租约不恢复。

用户最新纠正收费范围，变更 `MAIN-BYOK-20261008-01`：本地模型与客户自备Pi Key以[产品§2.6](../docs/product-requirements.md#26-本地优先与用户控制)为准；删除确认仍按DESIGN§4执行。付费UI、报价/扣费/充值实现与验收全部停止，不作为试用交付缺口；保留UI73已交代码，不因撤销收费要求回滚删除模态或普通提案单次应用。UI和B保留已交源码与证据；75已结案，旧条件START不复活。用户最新批准开始下方76/77交付任务，只取得各自具名新权限。

本次变更 `MAIN-SOFT-CARDS-20261008-70` 属共享设计增量，用户已直接授权；只有受影响浮层条款重绑。69已完成的草稿差额和64/67/68未影响成果保留，公共浮层的旧几何证据不用于新规则通过结论。

用户最新调整当前阶段为本机安装包真实运行与全流程演示，交付渠道以[产品需求§1.3](../docs/product-requirements.md#13-交付设备平台与待定选项)为准。新电脑安装、依赖自动准备和Intel实机适配后置，不再派施工或将其列作本阶段阻断；本机可复用已有模型与工具，不能把开发浏览器演示当实际安装包通过。三平台自动发布目标保持。此优先级变更不重启B71冻结候选，不扩大媒体、真实账号发送、打包/远端构建或用户数据操作权限。2026-10-07用户进一步批准按审查缺口开始执行，并提供 `/Users/Zhuanz/Desktop/sk.docx` 中的MiniMax Key。当前写权、操作权及主控真实文字请求的有限例外只来自本节设计任务、下表当前任务及其具名条款，历史候选说明与旧任务不恢复。

用户于 2026-10-07 在本会话指定 main 线程为产品主控，提供两条可见 Codex 执行线程，并明确要求“现在你来安排工作吧”。产品施工仅限下表具名范围；原主控和其他旧派工仍停工，历史授权不自动扩展到本批。

- 唯一主控：用户于2026-10-09明确转交main窗格 `ed1ea8f4-8d0d-49cb-abad-0ba2a3b20a8c:f59b1ff5-5526-4ab1-942c-f485845cf255`；实时句柄 `term_f7d3806a-fe63-446c-b5ee-c8b0c3164345`。负责共享文档、Linear、候选审核、Git集成与PR，不编写产品代码。旧主控 `term_2a22cf6b-53e4-402c-969f-5365d6a15444` 交接送达后停止派工和项目/外部写入。沿用Run `run_e3cb6568939f`，由新主控在自己的终端绑定并回读，不创建并行主控或复活旧Dispatch。接手快照见[本次主控交接](controller-handoff-20261009-ed1ea8f4.md)，不替代实时Git或本合同。
- 本批变更标识：`MAIN-RESUME-20261007-01`。产品代码基线 `584ccc362c0e03a7647d655f22014f30e75022b1`；主入口本地说明基线 `b4dd823a5ca05c21c0dc8429eab8c9ee37e11425`。新合同由两条线程读取本文件并语义 `REBASE_ACK`；ACK 满足且无未解决范围冲突后可直接开始已授权动作，无需第二次 START。
- 事实源：当前 main/候选 Git、Linear team WIL 的项目「曜核 0.11｜桌面端完整闭环」、实际测试与具名真实页面；浏览器表面按实际工具和人工操作分别记录。历史[暂停交接](pause-handoff-20261007.md)仅提供保留成果的位置。
- 不整体 merge/rebase 旧混合开发分支，不覆盖脏树。执行线程不得 push、建/合 PR、写 Linear 或共享权威；本地仅提交自己已验证的具名差额。主控可准备独立 PR，main 产品代码只走 PR，不自动合并。
- 除下方主控具名MiniMax Key及用户本人素材定向只读例外，不访问真实用户库、其他真实密钥/登录钥匙串，不激活云身份/凭据，不下载或运行未具名本地模型、不发布/签名/Release、不迁移真实数据、不删除保留工作树/stash。已结案Dispatch不复活；旧任务操作权已结束，UI98/B99/B97运行权已收回，暂不运行账号浏览器或模型/媒体；后续核心聊天实包验证另按具名差额接续，UI96账号运行和B94/95源码权限已结束。主控继续PR96/97及人物许可独立PR的CI/审核与相关Linear维护；用户资源条件恢复后以新具名合同/Dispatch接续完整制作，不从旧记录启动。定时 automation 与 scheduled 生产始终 OFF；无真实 Supabase 迁移/设备注册/测试账号操作权。
- 不派内部子智能体，不新增执行窗口；一文件一写者。彼此可通信，但不得转授写权或替主控改变范围。

## 本批线程、所有权与验收

2026-10-08用户再次确认首个试用版必须包含抖音/小红书/视频号自动发布，不批准用人工上传缩减范围；发布前目标账号和内容确认仍依产品§11。主控以真实制作/修改重出/保存、三平台发布、安装运行作为交付主线；当前UI72只完成已批准收口，不延伸视觉任务。B71等待候选期间可并行做一次只读发布与分发缺口核验：仅当前集成树 `/tmp/main-controller-20261008/ui-v3-integration` 的platform_auth/platform_browser、PublishingWorkspace、API/桌面直接接线及internal-macos-test workflow，结合WIL38/45/113/114/115/169；输出最小可实施顺序与具体账号/外发授权需求到既有ui71目录。无产品写权、服务/GUI/网络/凭据/真实账号或发送权，不等待整轮UI才能指出阻断；此核验不替代71具名START。

| 线程 | 工作树与身份 | 当前任务 | 写入边界 |
| --- | --- | --- | --- |
| UI / main-ui-linear | `/Volumes/Dujiangyan-VM/AI视频日记/repositories/main-ui-linear`；`579066f7-9655-4ee7-9473-53d2e2071f2b:b8a4392e-dd5f-4cac-8267-1671e8118151`；`term_0ff98007-d2b6-442e-b45e-7d1b0b4d9811` | QA-V2-UI-IMPLEMENT：会话共同机制修正 | task_1ad9935077fa / ctx_f2002033bb68；以下新实施范围 |
| A / manta | `/Users/Zhuanz/orca/workspaces/main/manta`；`68244901-fc27-4dbd-9381-cc158d3ba9dc:942aa671-d4b4-48cc-925c-cd9e94a383d7`；`term_79a48d73-cb24-4736-a0c2-4e995483ea88` | 65：额度中断，已fence | 原脏树保留；无新增写权 |
| B / pipefish | `/Users/Zhuanz/orca/workspaces/main/pipefish`；`57ae4a00-4977-4585-8947-5223d803c1c8:83f3ee5f-3d33-49b6-9e03-2cf08374cc2c`；`term_2ee18b71-c3c8-4e56-b551-5f5a765c4298` | QA-V2-B-CORE：人物声音同一持久契约 | task_20846b8a711d / ctx_dda1c040904b；99脏树保留 |
| C / arrowworm | `/Users/Zhuanz/orca/workspaces/main/arrowworm`；`term_d53c0c65-39c8-4edd-af2c-deeb0a842718` | QA-V2-C：模板与知识录入闭环 | task_2325828e95cf / ctx_769828ca4aa2；以下独占消费者；原协议树保留 |
| D / foxface | `/Users/Zhuanz/orca/workspaces/main/foxface`；`term_348145c1-121a-40ef-825a-c0d41739ff88` | QA-V2-D：设置与文件选择契约 | task_7f09829a538d / ctx_549f96617dde；以下独占消费者；原命名树保留 |
| E / integration | `/private/tmp/main-controller-20261007/creation-wave/integration`；`term_f37fee2b-1725-4d4e-bdda-e2ac2271d8e4` | QA-V2-E：任务历史故障与一致性复核 | task_8c8a1fb296c6 / ctx_8e2eb345787b；以下只读/隔离复现；旧集成树不作为候选 |
| F / ui-v51 integration | `/private/tmp/main-controller-20261008/ui-v51/integration`；`term_ad81452e-0f34-47aa-9875-47556cc9f359` | QA-V2-F：共享浮卡、帮助与成员表单 | task_85c462d3da67 / ctx_578dde6f6a58；以下独占消费者；原clean旧候选保留 |

## 当前波次：实机v2累计整改（MAIN-REVIEWV2-20261009）

用户于2026-10-09提供Downloads累计版ZIP并明确要求在GitHub建Issue、确认问题后安排修正，按同类共同机制解决，不按症状逐处补丁。47个U/N来源ID保持；F01/F02、历史候选/风险/设计/L/DEEP行有重叠，不求和成Bug数。评审是现象证据而非产品权威；报告original链接仅溯源，活动规则仍从main/docs/README.md路由。已改善的流式/工具事件/引用/柔光卡/WAV选择器保留。

累计追踪为[GitHub #103](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/103)，#104–112按会话、人物声音、素材资格、任务版本、设置事实、知识录入、模板、共享组件和定时发布机制归组。所有47个新版ID、70行历史来源及R01/R02已有路由；施工与关闭仍逐项记录，建单不表示缺陷全部确认或已经修复。

准确产品基线为`/tmp/main-controller-20261008/ui-v3-integration`的`6d7b0ada06e8389cd5495c2d32501fdb92102e60`，PR102仍draft且未获合入批准。评审包SHA256为`c62f75e9282673c79b71703cc0a28bd43a390886ed7a7887dbfbdb1340c206f0`，manifest289文件无差异；定向证据及累计入口在`/tmp/main-controller-20261009/review-v2/`。既有用户包和试用数据保留，不访问其DB/配置/Key或改设置；原模型/媒体/账号租约不复活。本批授权产品整改，不取得实际模型、个人样本、外发、收费、账号、发布、用户数据迁移或删除许可。

首波复用UI/B终端定向根因定位；UI定位已收读并按以下确认差额接续实施，B仍完成资源定位。每个Issue关闭须逐来源ID回填候选、复现、系统原因、受影响消费者、真实UI及必要持久结果；静态/测试/HTTP/模型/客户端分别记证据。

### QA-V2-UI-IMPLEMENT：会话生命周期与Agent控制共同机制（MAIN-REVIEWV2-CONVERSATION）

定位报告`/tmp/main-acceptance-20261009/review-v2-ui/report.md`已确认首页/素材React本地状态、任务TaskEvent、Pi临时conversation ID三个生命周期未统一，消息整轮后追加、无真实停止、headless不执行斜杠命令与裸Markdown。定位权结束。本次为用户已授权整改的C2共享契约补齐；重读main DESIGN§2新增会话/轮次/命令条款、领域§7.5、架构§9新增持久入口及auth可信成员/对象边界，语义REBASE_ACK后直接实施。B的§3/§4定位不受影响。

UI是纵向实施及退役负责人，从原clean6d7b0ada建立`codex/review-v2-conversation-20261009`，保护原成果。来源N01–N04、U01/U04–U06/N05及相关历史。新增结果：可信成员/空间/范围绑定的应用会话与轮次、导航及刷新可恢复历史和未发送草稿、可发现新建/命名/恢复入口、即时用户消息、生成时可编辑下一条草稿、真实停止并保留部分输出、统一安全Markdown/正文复制/真实工具顺序摘要、页面相关建议。停止不等于取消制作、不自动重试写命令、不增加排队/插话；未知或未接通命令明确说明，禁止模型口头模拟。应用新建/恢复/命名优先真实接通；实际Pi命令/扩展/技能/模板/工具完整盘点形成能力表，其他控制只能映射真实现有入口，缺口如实保留，不造无效按钮或开放任意shell/文件命令。

沿现有SQLite/Repository、可信assistant路由、Pi运行和业务工具建立必要会话持久入口，不建第二Agent/生产状态库。新的聊天轮次只有一个写者，既有TaskEvent业务结果保持；旧任务聊天仅作为明确只读历史投影，不清空或静默迁移用户历史，不读取任意Pi session文件。草稿按合理提交检查点保存，不每个字符产生业务版本或伪装已执行；选择切换后结果归原会话对象，不能覆盖新选择。停止须从服务端关联当前轮次实际子进程并释放，终态回读可核实；可用受控无秘密假进程验证生命周期，不能用停止SSE读取代替进程停止。

唯一owned产品：HomeCommandComposer.tsx、CreationTaskWorkspace.tsx、MaterialLibraryWorkspace.tsx仅会话上下文（AppShell已按下方RELEASE_ACK转F）；lib/api.ts、lib/types.ts；main.py仅会话创建/回读/草稿/命令/停止与三个assistant直接消费者、assistant_pi.py；确有需要的单一会话Repository模块/对应增量schema与生命周期模块、diary_tools.ts仅真实事件投影；必要共享消息组件和对话局部样式及上述直接测试。不改ProfilesRepository/人物声音/平台/任务媒体或调度/共享权威/云身份/桌面。package/lock仅在核无既有安全Markdown渲染能力后允许引入react-markdown和remark-gfm及必要锁文件；不执行安装脚本、不加入其他UI底座。B无以上文件写权，若B后续需要共享入口须等本单交付后具名转交。

owned证据`/tmp/main-acceptance-20261009/review-v2-conversation/`。最小验证：两处Composer即时/流式/下一草稿/安全渲染/复制/工具顺序/未知命令；服务端会话成员空间隔离与范围绑定、历史/草稿刷新恢复、轮次错误/取消与实际受控子进程释放、只读历史兼容。允许已有依赖专项、tsc及隔离测试DB，不运行真实用户DB迁移、Pi/Key、个人素材/模型、账号/GUI/常驻服务或打包。新增必要表/字段只能在可丢弃测试DB验证，原数据保留与旧写者退役有负向证据。交clean候选、净diff、命令能力表、逐ID证据和未验项；源码通过后主控一次集成及具名客户端补验，未完成真实目标不得关闭Issue或称MIGRATION_ACCEPTED。

UI C1确认差额：E定向复核并主控回读准确6d7源码，PrepareMaterialsView请求limit=200而API契约1–100，造成H-B001/B003/B004/L01确定性422。上述UI独占CreationTaskWorkspace.tsx及lib/api.ts范围扩充到此真实消费者：遵守现有分页契约，真实呈现剩余页并可继续读取，或按任务实际引用遍历必要合法页；不得只截断100条假装完整，也不扩大API上限掩盖错误。区分资料就绪事实与当前列表加载/失败/重试状态，失败原动作可重试、当前对象和既有草稿保留。只做必要隔离分页/空/失败恢复直接验证，不重复未变全套会话测试；不更改资格规则或素材系统标签。

UI C1事实差额：N32准确runtime/health使用全局settings，而对话/settings/runtime使用可信当前workspace配置；UI作为main.py唯一写者使health读取同一有效工作区事实，保持现有响应和真实能力探测，隔离两工作区验证，不把最近成功等同全部工具可用。H-L02模板继承覆盖显式30秒也由UI修直接_creation_plan_preflight：显式用户字段优先，未提供才继承模板；模型文字、结构计划、卡片与提交以同一真实计划参数反馈，不用正则猜文本数值或新增另一方案库。真实冲突不能呈现为可确认的一致结果；最小直接模板显式/未提供用例。不得改变来源/声音/人物资格。

### QA-V2-B-CORE：人物声音同一持久契约（MAIN-REVIEWV2-PROFILES）

定位报告`/tmp/main-acceptance-20261009/review-v2-profiles/report.md`已确认UI/API/ProductionRepository/预览/任务贯穿旧必填人物与逐版本授权、设置旧表写端点410。B是F01/F02整个纵向和退役负责人，定位权结束；产品§2.1/§2.2/§2.5/§2.6、领域§2.4/§3/§4新增参考/独立声音条款、DESIGN§5.4新录入条款、auth成员/共享对象边界冻结。用户进一步授权主控自行作范围内判断，并授权复用C/D/E三个可见线程；无人数据、不共享新权限。

分两次互斥源码接续：本单先实现持久与模型直接消费者；待UI交付会话共享入口、D交付设置页后，B基于主控组合候选取得main.py/schemas/Web的第二单，继续跟完录入→保存→重开→预览→任务引用。第一单不单独集成或宣告完整流程，禁止两套可写声音事实长期共存。旧声音410保持；历史数据只读保留，新默认引用只能绑定版本化档案。

B在owned新`/tmp/main-acceptance-20261009/review-v2-profile-core/candidate`从准确6d7b0ada本地clone --shared --no-checkout并建`codex/review-v2-profile-core-20261009`，不修改或切换原pipefish。唯一产品owned：production.py仅人物/版本化声音持久、参考范围解析/默认引用及其直接资格；avatar_lipsync.py的两Profile/真实输入/预览消费者、narration.py、creation_execution.py仅声音与人物独立选择直接消费者；database_migrations.py仅确需的增量/历史payload映射；直接test_media_profile_repository/test_avatar_lipsync/test_narration_commands/test_creation_execution及必要一个本单专项。main.py/schemas.py、全部Web、桌面、assistant及其他产品路径当前禁止写。UI不写production.py/database_migrations.py/上述媒体模块，D独占settings/page.tsx和desktop选择桥。

新参考默认available，独立声音可在无人无模型条件下保存有效WAV参考，人物可保存合法素材/范围；use_state与来源限制分别核实，不能把源的限制丢失到一个宽松枚举。人物关系若填写必须成对且指向同空间有效版本，0%人物旁白不反查人物或口型。既有原片与生成结果、声音版本、真实模型/媒体/设备许可继续区分，登记不偷跑生成。沿现有versioned JSON/Repository保存，不另建档案库；新增字段仅在可丢弃DB验证。不运行真实库迁移或自动复制原件。

历史处理冻结：已有明确use_state、来源限制和共享撤销优先；旧授权记录明示revoked/expired的限制不自动升级available，不删除历史证明；缺默认权利人/证据或pending本身不构成新导入的强制证明门槛。不能凭缺owner推定当前成员拥有旧私有素材，所有真实读取仍须现有成员/共享及安全根边界。失联、无有效源范围或不能可靠映射的历史记录保留并说明重新定位，不猜另一声音或改用户默认；若映射需要额外产品判断，交准确事实，主控自行决策。

最小直接正证：合成WAV合法导入/声音无人无模型登记→GET→重开同版本、合法人物范围引用、默认版本引用与独立旁白消费者；负证：无效媒体/越界/跨空间、实际主动限制/过期共享、旧版本不可变、失联引用不称可生成、生成失败不伪成功。允许隔离测试DB、测试内无秘密受控替身和标准库合成WAV；仅必要时本任务自有合成媒体根的短FFmpeg/FFprobe，不碰个人原件或启动模型/Pi/Key/账号。不开常驻服务/GUI/构建/外发，不新增依赖、资源调度框架或弱化真实授权检查。交clean候选、净diff、明确API/Web后续差额与退役矩阵，现阶段证据仅源码/隔离持久，不关闭#105。

### QA-V2-C：模板与知识录入闭环（MAIN-REVIEWV2-ENTRY）

复用用户新授权arrowworm终端；原协议讨论与clean树保留。本单从6d7b0ada复制干净源码到owned`/tmp/main-acceptance-20261009/review-v2-entry/candidate`的新分支，不在旧协议树施工。先核#109/#110与N15–N17/N22–N24及H-B012/H-D006/D008/L11，main产品§3/§6/§10、领域§2/§9.1、DESIGN§4.2/§5.3/§5.6/§5.9为准。可独占改TemplateLibraryWorkspace.tsx/.module.css、app/templates/page.tsx、app/sources/page.tsx及该页局部组件/样式和上述直接测试；不改AppShell/共享浮卡底座、actions.ts、lib/api/types、main.py/schema、settings或其他页面。真实现有提交方法复用，不另造写接口。

确认后直接修：知识按粘贴/文件/网页的真实意图收起非当前表单，持续订阅独立高级入口；不支持平台改非交互说明或真实说明入口，文件未选明确就地状态/校验。模板详情内绑定所选版本的使用出口，正文和底栏有效；真实视觉样式才给效果预览，仅文案结构给标示示例的内容/结构预览，不拿示意当成片。N24按当前多类目标用户保留技术创作者；C已确认真实目录只有三种技术/日记结构，主控据产品§3/§10及用户自主决策授权补产品介绍/常见问题/客户案例三种具体输入与结构。本单新增唯一owned `apps/api/app/content_templates.py`及直接`apps/api/tests/test_video_templates.py`目录相关差额或一个必要目录专项；复用现有模板生成/版本/风格字段，不造新渲染能力、不改main.py/schema/types或放宽检索资格。最小专项与tsc，必要截图先报可执行入口；不跑真实服务/Pi/媒体/Key/账号/GUI/打包或用户库。交完整消费者差额、来源逐项状态、clean候选及未验；不以源码PASS关闭Issue。

### QA-V2-D：设置与文件选择契约（MAIN-REVIEWV2-SETTINGS）

复用用户新授权foxface终端，原命名树保持；从6d7b0ada到owned`/tmp/main-acceptance-20261009/review-v2-settings/candidate`新分支。纵向核#108来源U07/N29–N32的有效服务配置/状态来源和文件型消费者，main产品§2.5/§2.6、DESIGN§4.4/§5.9、架构§2及desktop README直接边界为准。唯一owned：settings/page.tsx非声音部分及现有设置字段/PathPicker组件、desktop main.js/preload.js仅原生文件/目录选择契约、health/page.tsx、首页品牌消费者仅若不在UI AppShell/HomeComposer文件、直接专项；局部样式只在owned文件/新模块，不改global CSS或UI持有文件。settings声音旧表消费者留待B接续，D交付后整页写权明确转B；不能两人同时写页面。

SQLite字段kind:directory为源码已确认错误，修同类文件型选择对象与取消语义，不替换真实DB或运行迁移；听写按本地/云、Python/cpp选择显示真实对应字段，保留切换配置。制作偏好按实际消费说明字段含义，不造未接通预览。品牌若可选不作依赖告警；需要补齐时直达真实设置。N32沿当前真正有效配置定位，禁止凭最近成功捏造当前健康；若原因在main.py/API schema只报告精确差额交UI当前唯一写者，不前端猜测或只删告警。最小既有专项/tsc/桌面syntax，原生取消/文件选回读留同候选GUI实证；不得改用户配置/Key/数据库、服务/Pi/模型/媒体/账号/GUI/打包/共享权威/远端。交clean候选、源码根因、所有受影响消费者、后续API所需接线及未验。

### QA-V2-E：任务历史故障与对象一致性核验（MAIN-REVIEWV2-TASK-AUDIT）

复用用户新授权integration终端，不把其旧集成树当产品候选、不切换/改树。只读准确6d7b0ada及#107/#112对应H-B001–B017/B025、H-L01–L04/L06/L09/L10/DEEP-07和历史风险，main产品§2.2–2.4/§9/§11、领域§5–§8、DESIGN§4.4/§5.5/§5.8为准。先核分页读取/字幕503原动作、30秒结构计划、清空说明系统标签副作用、发布选择对象与预检/提交绑定；再核31条以上历史首屏和0%人物无关环境。不是整仓库健康审计，不遍历用户输出/所有日志。

仅owned`/tmp/main-acceptance-20261009/review-v2-task-audit/`报告及最小可复现证据；产品写权为零。允许准确候选离线已有专项/隔离TestClient、可丢弃SQLite、纯合成成员/文本/测试fixture；不seed“实际成功”的证明，受控替身不能冒称真实字幕或成片。用户ZIP内history中指定旧实机报告/疑似Bug台账仅定向溯源，历史状态保留，不用旧权限施工。无常驻服务/Pi/Key/真实媒体/模型/发布/用户数据操作；不构建/改共享接口。回报各源ID确认/未复现/已改善、准确共同原因、当前写者涉及文件和最小后续修正，交主控串行转交，不能按UI/B未受影响成果要求重测。若终端实际额度中断，保留报告与当前状态，不创建替代窗格或重复Dispatch。

B C1已确认差额：H-L03 update_segment每次重算_infer_system_tags会将清空人工说明变成覆盖识别事实。B作为production.py唯一写者修说明/字幕/用户标签编辑不覆盖已有分析系统标签；真实重新分析仍沿自身更新规则，不用个人标签补回冒充恢复。最小合成SQLite保存回读覆盖说明清空、已有系统标签及其他字段保持，不动用户数据。U07品牌自由文本未接视觉模板，不作为普通对话/手动制作的全局运行缺失依赖；B在现有readiness消费者去掉该不成立的全局阻断，不抹去具体任务真实要求。声音片段范围核心保存不得当整文件执行；精确派生接线在B第二阶段使用现有类型化FFmpeg/注册子进程/设备许可，不新增旁路执行器。当前核心阶段可据实阻断未具备派生输入的生成，不能因此宣告F01/F02纵向完成。

D C1增量：U02在现有密钥行缩为“移除密钥”、一行次要危险样式，保留现有右侧确认协议；禁止真实删除Key。原生文件/目录选择可改现有directoryPicker.ts桥接和DirectoryPickerButton与既有Electron类型声明的直接字段，仅真实选择对象类型，不接收任意renderer文件路径、不修改权限/存储迁移。上述非声音文件仍D独占。

### QA-V2-F：共享浮卡职责、帮助和成员表单（MAIN-REVIEWV2-SHARED）

用户追加允许窗格5a82f267-06cf-48e1-b2c9-ef42fe1cda2e:1e730caf-177f-4f29-b575-ddad4b70762e；实时句柄term_ad81452e-0f34-47aa-9875-47556cc9f359。原/private/tmp/main-controller-20261008/ui-v51/integration clean70e96fe9保留且不是当前产品。从准确6d7b0ada本地clone --shared --no-checkout到/tmp/main-acceptance-20261009/review-v2-shared/candidate，具名分支codex/review-v2-shared-20261009，不复制ignored/私密配置或整体merge旧树。

F独占HomeCardDialog.tsx与SideCards.module.css仅有无footer的共同正文空间契约；HelpPanel.tsx；MembersAndSpacesWorkspace.tsx/.module.css；对应既有直接测试。不得改AppShell/会话/CreationTaskWorkspace/MaterialLibrary/模板/来源/设置/人物声音/libapi/types/main/schema/认证/全局CSS。来源U03/N38/N39：无真实动作不渲染空footer，正文可完整滚到底；有动作保留统一位置，既有右侧几何、柔光、焦点/层级不重做；帮助由外壳统一标题/关闭，业务小标题可保留，更新旧审核/逐条权利证明帮助为当前产品默认可用与主动限制，保留外发/生成独立边界；成员必填状态和就地错误、提交真实校验一致，角色相关操作密度收敛。R01仅用现有后台源码及可丢弃成员fixture核最后所有者保护，不操作本机真实身份、不擅自改变权限系统；若后台缺保护先回报准确文件与证据由主控具名转交。

重读main DESIGN§3–4/§5.4、领域§4、产品§2.1和auth可信成员边界后语义REBASE_ACK即实施。现有依赖直接组件检查/tsc及隔离fixture可用，无常驻服务/Pi/Key/真实用户数据/成员操作/媒体/模型/GUI/打包/远端写权。owned证据/tmp/main-acceptance-20261009/review-v2-shared/。交clean候选、净diff、逐来源结果与未验；主控统一组合后真实UI补验，不以源码/fixture宣布全部通过。该分工不转授其他线程改共享壳；C只能消费已有footer属性、UI只能消费现有CardDisclosure，若需要共享签名变更向主控报告。

F C1：R01可丢弃SQLite实证两所有者并发移除/降级均通过并留下0 owner；主控回读local_members.py确认检查前无写锁。此为既有“至少一位所有者”不变量的原子性修复，未增加权限或角色，不改成员认证/跨空间规则。F独占增量local_members.py及直接test_local_members相关用例：复用既有BEGIN IMMEDIATE保留事务，从读取目标及owner数量前持有写锁直到写入/回滚完成；并发移除/降级均保留至少一owner，正常可操作路径/错误响应保持。不得仅禁用前端按钮解决后台并发，也不改真实成员/数据库。只做可丢弃并发与既有顺序最小检查；不加载服务/GUI/云身份。记录源R01从“未执行风险”提升为“隔离并发确认缺陷”，真机身份行为仍未验。

### 第二批独立消费者（MAIN-REVIEWV2-FOLLOWUP）

D原设置单207547c已源码审核/retain，settings整页写权结束，保留给B第二阶段。D另接首页消费者：从主控已机械组合的/tmp/main-controller-20261009/review-v2-integration准确f595eaf新clone到/tmp/main-acceptance-20261009/review-v2-home/candidate，分支codex/review-v2-home-20261009。唯一产品owned HomeWorkspace.tsx/HomeWorkspace.module.css及其直接组件专项；不改AppShell/中栏/会话/设置/人物/模板/API/types/全局样式。依main DESIGN§5.1/§7与产品§1.2，处理U08、H-D001/D002、N13首页消费者：保留唯一清楚页面标题，首屏围绕实际待办/进行中/近期可用结果，重复结果不重复呈现，空模块不保留大框；必要统计与图表次要且保留时间/单位/真实可见范围。词云只按真实已有标签结果做简短可读呈现、长观察收详情，不擅自重写系统标签或编造聚合计数，精确点击查询保持。暂无事实显示真实未知/空，不生成示例冒充实库；诊断内容按需展开，不删除必要阻断。不推翻三栏/已接受尺度。仅现有依赖专项/tsc，无服务/GUI/用户数据/模型/账号/打包，交clean候选与逐项未验；截图由统一候选后补。

E只读复核已交并retain，新单修发布消费者H-L04/N28/H-V012机制：同样从f595eaf到/tmp/main-acceptance-20261009/review-v2-publishing/candidate新clone，分支codex/review-v2-publishing-20261009。唯一owned PublishingWorkspace.tsx、必要新增局部模块CSS及其直接既有测试；不改共享ProductWorkspace.module.css、AppShell/api.ts/main/平台worker/账号/持久schema。统一实际可发布task+产物版本选择，列表/下拉/正文/预检/最终提交同一身份；提前文案草稿需清楚归属并保留task+版本的本轮临时草稿，切换不沿用其他任务文本，未提供持久保存不能称刷新可恢复。前提按有效成片→实际账号状态→编辑信息→现有最终确认组织；重查/对象版本或账号变更使旧确认失效，真实后端409门禁保持，不新增自动重发/隐式幂等。普通未就绪对象可只读记录或准备入口，不隐藏混成不可见提交目标。仅受控组件/API替身核多任务/版本/账号/取消/失败，不真实连接、账号浏览器、上传/草稿/发布或权限操作；报告请求快照证明只是fixture。原历史报告复用，不重测未影响字幕/素材。

C来源第一批d16b9c0已交/retain。C新知识核心迁移：从准确f595eaf建立/tmp/main-acceptance-20261009/review-v2-knowledge-core/candidate，分支codex/review-v2-knowledge-core-20261009。唯一owned sources.py/source_repository.py及直接知识仓储/授权专项；只处理自己已有SourceItem/KnowledgeItem表的必要增量use_state持久字段/旧行兼容，不写B所有的database_migrations.py或生产字段，不写main/schema/creation_plan/Web/认证。依已有领域§2.4和产品§2.1补available默认与明确主动限制，归属仍由verified_user_id而非客户端ownership文本决定；authorization/ownership保留历史追溯且普通新导入不要求逐条证明或伪造已授权字符串。真实跨空间KnowledgeGrant及撤销/到期/固定版本/私有读边界保持。excluded不可读/检索/引用；reference_only仍可事实理解，not_for_automation/not_for_publish按具体消费者用途提供真实资格方法，不把阅读默认当发布/自动授权。版本/来源/旧字段保留，不把不可读原件变成本成员的档案。核心新增字段的DDL/回读只沿既有SourceRepository自有表初始化/字段升级位置，使用可丢弃旧格式与新库验证，不另造知识库或权限解析器，不迁移真实用户DB。交精确API/Schema/Plan后续接线接口及必要增量字段；共享入口等UI交付后由主控具名单写者串行接线，不以仓储通过宣布N15完整默认可用已修。

会话UI待M5/M6/M7任务错误/历史消费者接续，知识H-D006集合布局仍待C后续消费者；定时定义N25–27保持当前OFF与待整改，不从任何历史START启动。

当前回执：C知识核心 task_b7f49a0e1a8b / ctx_cd13d71fb16a；D首页 task_67f431a1fd8a / ctx_13f661653b71；E发布消费者 task_fc0ff8ed8748 / ctx_8bc450650430。D/E旧attempt最后只回应Git身份且没有worker_done，精确provider transcript证明该轮已结束；主控按恢复指南abandon保留原终端，原Task原终端显式retry，以上新attempt均已语义ACK。旧ctx_bc1a22f04de8/ctx_eab015c1918e不再有写权。B核心9cd2c53和F定时只读04ec021已源码审核、结算并retain；它们已机械组合进独立候选，main产品代码未改，完整迁移验收仍未成立。

### QA-V2-B-RANGE：独立人物方案引用与精确媒体范围

B核心写权结束。本单复用原B终端，从主控组合树准确267802c新clone到/tmp/main-acceptance-20261009/review-v2-profile-range/candidate，分支codex/review-v2-profile-range-20261009，保护B99脏树。领域§4/§5.1已经明确独立person_profile_ref和voice_profile_ref；B为F01/F02纵向负责人继续完成此机制。唯一owned production.py内既有CreationPlan与编译/持久消费者、creation_execution.py、production.py人物声音参考解析、avatar_lipsync.py、narration.py及现有类型化FFmpeg/注册许可子进程的必要直接接线和专项；可新增一个具体参考派生模块，不建第二执行器/权限系统。main.py/schemas.py/Web/api.ts/types.ts仍UI独占，本单不得改它们；不改C的sources/source_repository、共享权威或平台。

新增selected_person固定id/version独立选择，不从声音关系猜人物，0%人物任务不读人物/口型环境。旧方案读取保留确有绑定的人物身份作为历史投影，不把空关系或其他声音自动补成当前选择。选择、缓存身份、人物生成和编辑读取同一明确版本。兼容差额与后续Schema/API参数交主控单写者接线。

所选音频/视频范围通过既有类型化FFmpeg、granted_subprocess注册和DeviceRuntime许可执行路径派生真实输入，保留原文件只读、asset/segment/起止/源版本及派生产物身份；精确范围才可交给模型，不通过放松核心部分范围阻断或整文件回退完成。派生前重新核可信成员、源可读性/use_state/范围和有效媒体；不能从任意路径或未登记源构造输入。视频取声与人物片段分别形成实际类型，WAV可沿原有效输入；参考文本缺失只阻断需要它的生成。派生缓存复用必须绑定真实源修订/范围/类型并核文件，失败/取消保留档案，不伪造完成。媒体派生与实际模型生成分开，云/收费/发布权限不扩大。

只在owned隔离目录用自制合成媒体验证真实FFmpeg切片/提取、范围精度及原件不变，并通过既有测试Grant证明许可前不执行、取消/失败不留成功结果与注册子进程释放；不申请或运行真实用户DeviceGrant、模型/Pi/Key/用户素材/数据库，不启动常驻服务/GUI/打包。不使用测试输出冒称模型预览或成片；不因模型缺失阻断参考保存。最小受控方案/任务/仓储及取消专项，交clean候选、后续API/Web精确接口和逐项残余；API/Web仍待UI释放后B新单取得，#105仍开放。

F第二单定时入口承诺整改：依用户自主决策授权、产品§2.5及main DESIGN主导航最新条款，定时定义未接执行时普通入口退役、保留只读明确预览；不删除定义、后台接口或0.11真实定时交付目标。F从准确a2e537c的主控组合树到/tmp/main-acceptance-20261009/review-v2-scheduled-preview/candidate新分支codex/review-v2-scheduled-preview-20261009，唯一owned ScheduledTasksWorkspace.tsx/.module.css及直接测试、production/page.tsx仅automations分支。退役普通创建/启动/删除/JSON时间窗/手填内部引用的活跃页面消费者；只读已保存定义显示真实“尚未执行接通”状态，内部字段在按需诊断，保留原数据和业务事实。N25整改证明默认入口与能力一致；N26/27为旧不可依赖编辑消费者退役，不宣称真实排程/时间配置已做通。AppShell主导航及route标签由当前UI唯一写者按同条款改，F不争写，不启动任务/服务/模型/媒体/GUI/打包。不新增开关、模拟任务、倒计时或无效按钮。直接专项核无写API动作、无普通JSON/内部引用输入、数据空/失败/已保存分清；截图统一候选后补。语义REBASE_ACK后实施并交clean/报告，不关闭真实定时交付缺口。

### QA-V2-F-NAV：已释放共享壳的事实与导航消费者

UI在当前task_1ad9935077fa/ctx_f2002033bb68以msg_4ea1db3ed4c1明确RELEASE_ACK，主控独立核git status该文件无差额；AppShell.tsx整文件写权现转F，UI继续其他会话消费者。F复用既有终端，从准确267802c新clone到/tmp/main-acceptance-20261009/review-v2-nav/candidate，分支codex/review-v2-nav-20261009。唯一owned AppShell.tsx、其直接AppShell专项；不改API/types/main、HomeWorkspace(D)、对话(UI)、发布(E)、C/B或全局CSS。

依DESIGN最新主导航条款，移除普通定时主入口，旧URL保留明确只读预览标签，诊断/搜索若保留入口必须明确“定时定义预览”，不可称可运行定时任务。H-B010同文件现有listCreationTasks异步读取吞错且初始[]，修真正loading/empty/error/ready区别及就近原读取重试；已有server传入任务可继续可见，刷新失败不抹去已知任务、不称无任务。晚到请求不能覆盖更新后的任务集合或相反状态，不统一导航回顶。三栏/密度/既有焦点和搜索语义保留，不新建壳、状态库或开关。仅既有受控组件检查/tsc，模拟慢读取/失败/空/已有任务与恢复；真实GUI统一候选后验。本单是壳事实与入口退役，不完成真实调度。交clean候选、逐ID证据和未验；无服务/GUI/模型/媒体/Key/账号/用户数据/打包/远端写权。

F-NAV C1：KeyboardShortcuts.tsx仍以Meta+6和“自动任务”保留普通承诺；现具名同一F写者修改该具体入口及直接专项为明确只读定义预览或退役普通快捷入口，旧URL和真实调度目标保留，不改共享键盘库。当前F task_c90778179888 / ctx_a47522e33ac5，B-RANGE task_b18a434f49dd / ctx_2211ce1469f6，均已语义ACK。

E发布首批906aef36已结算retain，但主控审查尚不接受集成。E新复审单从该clean候选新分支codex/review-v2-publishing-review-20261009继续同一独占PublishingWorkspace/.module.css/直接专项；修复异步复核中的身份与取消一致性：发送时绑定任务/版本/账号/文案确认快照，等待读取过程中切换目标、取消卡片或修改文案，旧请求不得在回包后提交，也不得把较晚旧版本重试写进新选择详情。已发送真实作业不伪称可撤销；请求结果归原对象，失败不自动重发。账号重核若与本轮选定账号不同取消原确认，不静默换成新账号。保留后台409/idempotency，不额外要求用户批准当前已批准源码范围。

原E未执行组件检查因候选无node_modules；本单可只将候选node_modules链接到已存在公开依赖缓存/Users/Zhuanz/orca/workspaces/main/manta/apps/web/node_modules，不安装/下载/复制ignored私密配置，不删除依赖。必须运行既有组件专项与tsc并交实际结果，失败修当前owned差额；新用例需受控迟到读取、切换/取消/账号变化证明create未被调用，而非源码文本镜像。真实账号/浏览器/上传/发布/GUI仍无权，所有页面事实与对象使用原接口。无需重跑无关旧任务核验。

### QA-V2-C-KNOWLEDGE-UI：资料集合的信息与读取机制

C知识核心d88f173a+5a8c5e3已审查/结算retain，39项隔离仓储检查只证明核心；未接API/Schema/Plan的资格消费者仍开放。报告披露其曾额外下载pytest测试依赖、违反本单禁安装约束；主控取消非必要删除确认，保留候选临时依赖，没有用户数据/Key外发记录，不以清理为交付前提。后续只复用具名已存在公开缓存，不重复安装或删除。

C复用原终端，从主控组合f454b31新clone到/tmp/main-acceptance-20261009/review-v2-knowledge-ui/candidate，新分支codex/review-v2-knowledge-ui-20261009。唯一owned apps/web/src/app/knowledge/KnowledgeWorkspace.tsx/.module.css、knowledge/page.tsx和直接既有knowledge/ScriptLibrary专项。main/schema/libapi/types/source-api/来源录入页、会话、共享壳与B核心均不写；来源录入默认证明字段API接线等待UI共享写者交付后具名串行处理。本单不造未接端点或假保存出口。

按DESIGN§5.3/§7、产品§2.1、领域§2.4处理N13和历史H-D006及知识页同类读取：保留资料/已确认文案对象区别，搜索/对象行/空或无结果/加载/失败各有正确职责，默认层退役“尚未接入”工程大块、套层空容器，必要限制保留就近原因/真实下一步，技术参数/绝对路径/ID/诊断按需展开且不删除原证据。可读名称、版本及用户需要的来源保留；真实分类未支持就不展示无动作分类导航或伪分类。搜索可清空/恢复焦点，列表与详情选择不因筛选或慢请求串对象；失败不抹掉已知列表/未提交工作，不伪称空库，原读取可重试。保持已接受三栏/13px/56px/右侧柔光壳和共享footer，不推倒布局。权限/visibility/grant动作不改规则、确认或默认开启，历史快照与来源引用使用既有入口保持。

仅链接/Users/Zhuanz/orca/workspaces/main/manta/apps/web/node_modules公开现有依赖，实际受控组件专项/tsc；迟到详情、空筛选、列表失败/恢复和默认诊断折叠有必要证据，真实GUI统一候选后补。禁止安装下载/服务/GUI/Key/用户库/模型/媒体/账号/打包/远端写入。交clean/净diff/逐ID与残余；N15完整保存、N14对话文案出口及API当前门槛未由本单完成，不关闭它们。

### QA-V2-D-MATERIAL-AUDIT：素材/预检同源资格与选择范围

D首页a0d56a0已结算retain、主控组合ff153f0；新单先纠正一个具体现有首页消费者：HomeWorkspace中的homeTodos不应仅因同任务output_ready就过滤。可看成片与待处理动作是不同业务事实；保留真实待处理原因/行动，可同对象合并或分别显示但不隐藏阻断。唯一产品写权HomeWorkspace.tsx及其直接专项，最多必要局部CSS，不改其他首页消费者/API/任务媒体。用已有成片但仍有当前待办/失败的受控用例证明待办不消失，阶段完成不冒充成片规则保持。

其余是只读N06–N12/N33–N37/N13素材消费者定向诊断：准确ff153f0主控组合树和用户ZIP指定对应TXT/JSON/PNG；活动DESIGN素材/预检与领域§3/use_state、产品默认导入可用规则优先，不扫描历史输出或真实用户数据。不写MaterialLibraryWorkspace/HomeCommandComposer/CreationTaskWorkspace（UI）、main/schema/libapi/types（UI）、production/avatar/narration（B）；不按症状建平行规则。指出搜索结果和库总体/选中失效的真实状态来源、对象引用文件及时间、分析分项、人物/同义标签覆盖归并、候选与可用及语义评分范围、分阶段阻断与修复返回路径；每个ID静态确认/未复现/已改善分开，并给唯一写者及最小后续共同机制。API测试/纯合成fixture可只沿已存在公开依赖，不下载安装、启动服务/GUI/Pi/模型/媒体、访问用户库/Key或账号。读报告不取得媒体操作许可。owned新候选/tmp/main-acceptance-20261009/review-v2-material-audit/candidate从ff153f0新分支codex/review-v2-material-audit-20261009，证据同父目录。交clean首页差额与材料诊断矩阵，不把只读报告算已修。

### QA-V2-F-MEDIA-VIEW：缩略图与原片预览事实

F导航503caa1已结算retain并组合ff153f0。新单从准确ff153f0到/tmp/main-acceptance-20261009/review-v2-media-view/candidate，分支codex/review-v2-media-view-20261009；唯一owned VideoEditor.tsx的ClipThumbnail及原片/成片播放状态直接消费者、必要新增局部缩略图CSS模块和直接专项。UI仍持有VideoEditor.module.css的会话样式，本单不得改该共享文件、CreationTaskWorkspace/libapi/types/main、渲染/时间线/素材数据或其他编辑器。读main DESIGN剪辑/信息层、领域Timeline范围后具名ACK开始。

主控源码确认ClipThumbnail只有video/onLoadedMetadata，读取中/媒体读取失败都为同一灰块，源相同但范围变化未重新定位取帧；源预览缺明确onError状态。处理H-B016与范围一致性：只依实际media事件区分读取中、已定位可用帧、读取失败；缺引用只有真实缺失才称缺素材，失败不得断言文件已丢失或访问权限/密钥错误。文件/人物产物与准确所选起点改变时重置状态/定位，旧media事件不能串新clip或范围，不播放/改写原件。成片预览与原始参考明确不同身份，旧播放错误不得粘到新版本；已有实际onError及视频比例保持，不用AX单一文字判无法播放。必要恢复入口使用现有重新读取/选择对象机制，禁止在故事板外层button中嵌套button，状态读屏可辨而不把全部装饰缩略图重复朗读。保持三轨/56px密度/三栏，不能借此重排编辑器。

只用既有公开manta缓存的受控media事件专项及tsc，不安装下载/真实媒体/服务/模型/Key/GUI/账号/打包；未看到真实画面就不宣称实际播放器故障或图像已通过。交clean、逐来源结果和GUI残余，H-B013/H-B017等完整任务剪辑仍待主控同候选验收。

### QA-V2-C-KNOWLEDGE-REVIEW：补全交付与列表/选择闭环

C knowledge-ui 的worker_done不构成clean交付：准确候选仍f454b31、四文件未提交且report.md缺失；26专项/tsc/差异检查由准确终端记录证实。旧单已retain。本续单复用原C终端、原owned候选与未提交成果，绝不重置覆盖。写权仍只有knowledge/KnowledgeWorkspace.tsx/.module.css、knowledge/page.tsx与直接专项；基于当前diff修必要残差并用候选本地公开身份提交，写父目录report.md。不得安装/下载/服务/模型/用户数据/共享API。

审阅确认文案列表失败保留了旧数据，但没有原读取重试；成功筛选后旧selected详情仍可复用。列表请求按过滤身份隔离，读取失败保留且标明上次条件，成功新范围不能保留已不属于范围的可操作旧详情；来源核对失败须有原操作重试而非永远“正在核对”。默认详情中的原稿/运行/引用内部ID按需诊断，保留可读出处与版本，不删来源记录。资料刷新迟到响应不得覆盖较新成功结果。沿现有局部状态修复，不新增通用请求框架或自动重试；必要受控迟到/成功空筛选/失败→手动重试用例，未变26项不重复。真实GUI与N14/N15 API仍未验。

### QA-V2-E-PUBLISH-REFRESH：同对象重读与异步取消最后差额

E复审0fef1a43已经15专项/tsc通过，旧单retain；主控审阅仍确认refreshAll/selectTask/筛选点击无条件递增latestVersionRequest，但任务/版本不变时useEffect不会再次读取，导致正在读取的真实请求被失效后停在loading或旧详情。续单复用原E候选clean0fef1a43，新具名分支，仅PublishingWorkspace.tsx与直接专项。按同一任务/版本实际读取生命周期修复：主动刷新必须重新读当前详情，点同一任务/相同筛选不得丢有效读取；必要时实际产生新请求，不能清空错误或改为假ready。保留现有确认取消、账号/任务/版本快照及已发作业原归属。两个受控pending/refresh用例足够；不重复未变15专项以外无关检查，不接账号/平台/服务/GUI/外发/Key。交clean提交/追加report及未验；API和共享文件仍禁止写。

F-MEDIA-VIEW已实际ready/turn_started及语义REBASE_ACK，task_d237a52737d4/ctx_8fb689611db4绑定原F终端，写权按上节；不把ready算交付。

### QA-V2-D-MATERIAL-UI：显式选择、筛选结果和分析分项

UI于msg_e32cdff2dfc3实际RELEASE_ACK MaterialLibraryWorkspace.tsx无diff且不再写；主控git回读为空后转D唯一所有权。D前f3a344f已retain并机械集成cb47a2e，F媒体9c1f0e9已审核，组合其后主控准确HEAD为本单基线。D从/tmp/main-controller-20261009/review-v2-integration读取cleanHEAD并记录完整SHA，新clone到/tmp/main-acceptance-20261009/review-v2-material-ui/candidate及codex/review-v2-material-ui-20261009；原foxface和前候选保留。

唯一产品写权MaterialLibraryWorkspace.tsx、其直接MaterialLibrary专项和必要现有局部CSS。HomeCommandComposer、会话组件/hooks/CSS/API/types/main（UI）、CreateProductionTaskForm/production及资格规则不写。仅沿既有materialContext字段做文件简称+范围引用，不能为显示独自加共享字段。D负责N07/N08/N10/N11/N12/N13素材消费者共同机制：搜索/全部筛选/加载/失败/空库与零结果分清；查询一键清空保留其他筛选和焦点，另重置全部；显式用户选择与初始检查器预览/未保存编辑分离，真实选中失效保护保留，晚到请求不串选择/写其他素材；范围显示所属文件及时间。按已有实际speech_analysis/visual_status/ocr_status分项，缺分项证据诚实未知而非伪已完成，视觉完成不能泛称全部声音字幕都已分析。默认接入页面只给用户结果/作用/下一步，必要目录定位与诊断可展开，不删除真实配置或暴露秘密；保留三栏/13px/56px/右侧柔光与原生播放器。

N06全库只读助手仍由UI纵向共享入口后续接线，N09/33资格由B+main后续单，不由本单仅改文案假通过。只缓存manta公开node_modules、受控组件专项/tsc/diff，不安装/服务/GUI/模型/媒体/Key/用户DB/账号/打包/远端。必要测试覆盖默认预览后零结果无假警告、显式选中过滤失效及草稿保留、清除/重置、迟到返回、画面完成但声音未转写摘要；逐ID源码/组件/未验分开。交clean公开身份提交和父目录report，不单独关闭问题。

### QA-V2-C-SCRIPT-SELECTION：不可变版本的来源核对

C知识复审2d204678已30专项/tsc通过、retain。主控源码确认选中同一条目会清空sourcesChecked，但effect仅依selected.id不重读，停留正在核对；成功刷新同ID新version替换selected也不重新核来源、不清旧confirmed。新续单复用clean2d候选、新具名分支，仍原四知识文件及直接ScriptLibrary专项：以条目ID+version确定核对/确认身份，同版本重复选择保留有效核对或真正重读，同ID新版重核出处、取消旧确认，原失败可手动重试；来源迟到不能写其他版本。保留已确认文案库语义、后台事实引用及新任务要求，不造复制确认或新API。必要两个受控用例+tsc，未变结果复用。交clean提交追加报告，禁止安装/服务/GUI/模型/用户数据/远端。

### QA-V2-E-PI-CAPABILITIES：实际注册能力只读盘点

E发布三次审阅源码与17专项通过，retain并机械集成7064da9；现有发布权限及平台行为不扩展。E新只读任务核N04/R02：main-ui-linear的当前会话WIP capabilities表仍硬编码Pi1.1.0和终端命令，assistant_pi实际--print/json并关闭builtin/自动extensions/skills后具名加载业务extension。只读取实际Pi/Node可执行路径、安装包metadata及内置命令注册实现、当前源码启动argv/业务工具注册；对照Downloads指定测试包的工具文件元数据，不读用户config/.env/Key/会话/媒体/全历史，不启动Pi回合/模型/服务，不执行命令/工具/下载。

交/tmp/main-acceptance-20261009/review-v2-pi-capabilities/report.md：actual版本/路径来源，包内容与当前候选区分、终端内置命令与应用本地会话命令/扩展命令/技能/模板/业务工具分别列；真实读取/写入/提取声音能力和需要显式对象或执行许可的边界，哪些实际连接、受控测试、纯缺口。current进程环境未读则不能声称替代配置路径就是实际正在使用。不给任意shell/文件工具新许可，所有产品源码/文档/GitHub/Linear零写权。事实交UI唯一main写者修能力表与真实命令入口，不能拿旧历史数量冒充当前注册。

### QA-V2-F-PREFLIGHT-VIEW：候选层级与阶段阻断

F媒体9c1f0e9已retain并组合54c318d；发布三提交组合7064da9。新单从准确clean7064da925493ccec4d6c560efb3c77ea9546275d隔离clone到/tmp/main-acceptance-20261009/review-v2-preflight-view/candidate，新codex/review-v2-preflight-view-20261009。唯一owned CreateProductionTaskForm.tsx、新局部CSS模块及其直接专项。主控核该文件不在UI当前owned且无diff；共享main/api/types/modern-overrides（UI）和资格production（B）不写，production/page也不写。

按DESIGN预检/领域任务阶段修N34/N35/N37与N13该消费者：match_tier是主题/词语/结构候选，启发分值不做适配百分比，不称检索候选可直接用于制作；预检blocker显示明确阻止创建/进入哪阶段及当前允许的动作范围，真实confirmable控制主按钮，局部可读禁用态和就近原因。后台approved_count当前仍是旧审核筛选口径，不可只换名变成已可用；准确保留到诊断且声明本API口径，真正可用资格计数待B/main单接线后具名复验，N33不假关闭。资料/模型/声音显示真实可用事实，原始ID/参数/绝对路径按需诊断，不删必要错误或隐瞒外发。模板时长是默认偏好，显式输入可调整，前后preview/confirm同实际值，不能靠禁用时长把用户30秒变60秒。

N36修复入口需要源/API和人物声音入口实际接通与目标返回保存，当前不伪造已完成按钮；本单可以准确列reason→现有真实路径、剩余返回条件，不造未实现回调或新API。只缓存公开manta依赖受控候选层级/阶段/禁用/明确时长检查及tsc，不安装/服务/GUI/模型/媒体/Key/用户数据/账号/打包/远端。交clean公开身份提交、逐ID/残余报告；组件不等于GUI或完整预检通过。

### QA-V2-D-MATERIAL-REVIEW：编辑锚点与全体分析事实

D ada64c3已78专项/tsc通过并retain，主控审阅当前源码仍发现两项同类差额：默认检查器片段可编辑description/transcript/usage/collection但没有显式assistantSelection，搜索成功可selectAsset首项并重置这些用户未保存字段；OCR汇总flatMap丢弃understanding缺失片段，使部分OCR ready被说成全部完成。复用clean ada候选新具名分支，唯一owned MaterialLibraryWorkspace及直接专项/必要局部CSS。

把用户实际修改的对象/时间范围作为编辑锚点，与助手显式引用分别保留；筛选/迟到响应不得重定向或抹草稿，真正丢目标才保护提示，普通初始预览仍无假告警。多分项分析必须涵盖全部片段，缺字段不是已完成；混合ready/blocked/failed/unknown如实给部分状态，不用首个状态掩盖其他片段。不改API/资格/全局/助手，必要初始默认项编辑后筛出再返回/不串写、2片段一ready一缺字段的直接用例+tsc，复用未变78项。交clean提交追加report，禁止安装/服务/GUI/Key/媒体/用户数据/远端。

E能力盘点task_e5daffa2962a已failed结算retain：公开安装/源码矩阵成立，误触ZIP中测试会话内容后停止、未完成包元数据核对；没有恢复旧读权限或重跑会话证据。main能力表差额已交UI唯一写者。C知识2d/e5源码审核后机械组合f603abb，真实GUI及N14/N15接线未完成。

### 当前单写者接续：QA-V2-B-PROFILE-API / UI-TASK / C-SOURCE-FORM

UI会话da6f7c7、B范围3c149cb、D素材ada64c3+749820a、F预检84bcd24均已报告、源码审核并retain，原Dispatch写权结束；机械组合候选`/tmp/main-controller-20261009/review-v2-integration`准确`804dcf3`，main产品代码未改。本节优先于上表旧任务摘要。当前仅以下具名新单拥有写权，仍无用户库/Key/真实模型/账号/发布/GUI运行许可；源码与隔离检查不是完整流程完成。

B纵向继续F01/F02，基于804dcf3新clone到`/tmp/main-acceptance-20261009/review-v2-profile-api/candidate`，分支`codex/review-v2-profile-api-20261009`。唯一共享写者：main.py/schemas.py/lib/api.ts/lib/types.ts；产品owned仅人物声音/默认选择/预检及其任务生成回调相关段，另有PeopleAndVoiceWorkspace.tsx/.module.css、profiles/page.tsx、settings/page.tsx声音部分、CreateProductionTaskForm声音人物选择消费者、production.py/avatar_lipsync.py/narration.py/creation_execution.py/profile_reference_media.py的必要接线及直接专项。禁止改会话消费者/assistant_pi、C资料仓储/source-api/来源页、平台、共享壳/全局样式/桌面或不相关main段。一文件一写者；F的创建表单权已经结束。

登记按可信成员/空间读取，授权证明及声音的人物关联可选；两个声音入口使用同一版本化对象、默认固定版本。导入WAV沿真实现有文件桥/注册媒体入口；从现有素材选择真实文件/片段时间范围，显示来源和可试听/预览，禁止手填任意路径或要求旧已审核字段。人物入口稳定，名称与参考必填，版本自动生成，其他信息按真实能力可选。保存有效参考不调用模型；失联保留档案可重新定位，源use_state/限制不丢失。选中人物和声音独立传Plan/任务，0%人物不查口型，所有response处理可空历史证明且不伪造授权。任务配音/人物生成回调传可信reader和真实grant，使用3c149cb的精确派生，不能用整文件代替所选范围。

独立试听/人物短预览目前API直接调用服务且无设备许可，是真实接线缺口。本单按已有架构§3.12补一个具体media_preview业务owner/执行器注册到唯一DeviceRuntime，复用现有MediaPreviewRun和持久队列/注册子进程/预算/取消/恢复；允许必要新增具体profile_preview_execution.py及ProductionRepository自有增量字段和直接测试。绑定实际成员/空间、声音/人物版本、文本/模型/源范围摘要，准入/执行前重核，queued不冒充running或完成；不伪造CreationTask、借creation_job许可、添加第二调度器/内存队列或绕过未标定主机预算。Preview服务和参考派生只接受准确该owner/unit的许可；失败保留参考与文本，真实新文件才成功，原片不得冒充结果。列表/媒体读取仍受真实reader约束。若现有设备许可接口差额需要跨模块变更，交准确事实主控收口，不静默放行。

同一预检接线修N33/36资格消费者：依据领域§3.3和现有material_allows_automation/真实媒体/use_state/范围，沿同一方法供预检与实际自动选片，review_state不作全导入默认门槛，明确拒绝和主动限制仍遵守；候选匹配与可用计数分别返回，不把低语义结构候选静默变为自动选片。N09覆盖规范类别按真实已有分类/同义关系归并，不编造观察。只做必要正负专项，复用未影响既有42/30与合成范围证据。WAV/视频为任务自制合成，临时DB/TestClient/模型替身/测试grant及公开manta依赖可用；不安装、不启动常驻服务/模型/Pi/GUI，不用个人样本/Key/真实DeviceGrant，不改用户默认声音/库。交clean候选、逐ID、退役消费者不存在证明、精确GUI/模型未验；不先以API完成宣布F01/F02完成。

UI另接任务可见事实消费者，基于804dcf3新clone到`/tmp/main-acceptance-20261009/review-v2-task-view/candidate`分支`codex/review-v2-task-view-20261009`。仅CreationTaskWorkspace.tsx、JobProgressPanel.tsx、PersonView和SubtitleView实际所在组件及必要局部CSS、直接专项；不写main/schema/api/types/创建表单/资料/人物列表/B媒体核心/HomeCommandComposer。来源H-B005–009/B011/B013–017/B025、H-DEEP-07、M5/M6/M7：当前阶段原操作失败就近影响/恢复，历史折叠右卡且32条不能淹没主工作；真实完成不显示未知等待估时或整片红，旧完成产物不冒充当前版本就绪；0%人物不报无关口型环境。字幕草稿选段与真实画面定位就近，草稿/输出保存范围准确，显示和保留中文轨独立，失败不清用户草稿，恢复清当前已恢复错误。播放器保留F事实/范围实现，不重复重做；目标窗口三轨/实际比例保持。先复核已有接线，再按真实发现改；503归因未知不能统一指密钥。只受控组件/必要API替身检查和tsc，无真实服务/模型/GUI/媒体/用户数据；N14正文→文案草稿与N06全库对话待共享入口再次释放后具名接线，不造假保存按钮。

C独占来源表单消费者，基于804dcf3新clone到`/tmp/main-acceptance-20261009/review-v2-source-form/candidate`分支`codex/review-v2-source-form-20261009`。owned sources/page.tsx及必要局部组件/CSS、lib/source-api.ts的来源知识输入类型、直接专项；不写main/schema/lib/api.ts/lib/types.ts/生产仓储/知识集合/全局样式。按既有SourceRepository.use_state默认available，普通导入不要求权利人/授权字符串；保留来源、历史许可和真实主动限制，外发/订阅行为边界不放宽。复用同一粘贴/文件/网页录入流程，非当前表单收起，保存失败保留输入/原对象，真实结果回读再成功；prepared文件/格式就地校验，unsupported平台非交互，源技术参数按需诊断。后端当前schema旧必填仍未释放，报告精确API接线差额，不能提交假授权占位字符串绕过，前端专项是替身证据。B结束共享写权后C另接唯一main/schema接线单，届时验证默认available、excluded/restrictions按实际检索/计划用途消费与固定版本/grants不回退。本单仅现有公开缓存/受控检查/tsc，无安装、服务/Pi/GUI/媒体/Key/用户库/远端操作。

当前回执：B-PROFILE-API task_36e5c2cf4607 / ctx_9a8640de4abf；UI-TASK task_696bf5705d60 / ctx_1736a2d44acd；C-SOURCE-FORM task_dd0225de596e / ctx_32bdf6c226be，实际启动均ready/turn_started，UI/C/B均已语义ACK。共享main/schema/api/types仅B写，UI/C其他消费者独立，其他线程当前无产品写权。

主控804dcf3净源码复核发现UI原C1两叶没有进入最终交付：N32 get_runtime_health仍无Request且runtime_health(settings)全局；H-L02模板仍无条件覆盖duration。H-B001实际PrepareMaterialsView已经常量100合法分页并保留has_more/nextOffset及加载/失败/重试，复用既有专项，不按首条旧字串未命中误判未修，不重复施工。此为具体未交付叶，不否定会话42/125和分页成果，Issue仍需同候选可见验收。B当前唯一main写者追加N32该runtime-health入口和必要runtime_health.py消费者/直接专项：沿_workspace_settings_for(current_workspace_id(request))同一有效事实和真实最近调用来源，未配置/待验证/最近成功/当前失败准确区分；两工作区隔离与已删除Key不从旧dotenv复活。H-L02同_creation_plan_preflight显式用户duration优先、未提供才继承模板，preview/confirm同字段/指纹真实一致，禁止只改前端可编辑。最小直接正负验证，不重复未影响会话整套。

E另接QA-V2-ACCEPTANCE-PREP：原终端/原旧集成树保留，仅准确804dcf3只读源码和本机公开可执行/依赖版本。输出`/tmp/main-acceptance-20261009/review-v2-client-prep/`的验收准备：本机构建ARM测试包的实际可复用Electron/Next/PyInstaller/Python/ffmpeg路径、独立userData/HOME/XDG/DB/output配置所需字段和端口/进程退出检查；只定向查现有具名依赖缓存及工具metadata，不扫描下载目录/旧证据/ZIP/用户AppSupport/配置/密钥/媒体。可在owned根用真实ffmpeg/标准库生成标为合成的30秒双画面视频、中文资料和WAV参考，保留来源与时长/范围/哈希，自制样本不是个人声音/模型结果。可写owned验收脚本/无秘密env模板，基于现有API/仓储和可信本地测试成员准备最小样本；不改产品代码/共享权威，不实际运行应用/API/Web/Pi/模型/资源队列、不打包安装下载、不碰原试用数据。需核默认配置不会偷偷触发后台模型、定时/账号外发，缺口准确标明，真实窗口接续仅主控具名运行许可之后。报告可复用检查与候选变化必须重绑的字段，不提前称已通过GUI或完成构建。

B-PROFILE-API C2准确接线差额：msg_dcd1cf6e82ae/msg_d46e1fe30715经主控804dcf3回读确认，B追加独占lib/media-profiles-api.ts（实际人物声音类型/请求模块）、granted_subprocess.py及其最小直接专项。复用原request框架与稳定可读素材分页，不复制API客户端；旧listApprovedMaterials默认审核/第一页24条消费者退役，旧授权/人物必填类型改同一新契约。settings声音直接复用同一真实PeopleAndVoiceWorkspace voices入口可行，legacy actions写入消费者必须退出活跃路径，历史只读保留，不写actions.ts其他设置行为。

granted_subprocess仅将准确owner_type=media_preview与voice:/avatar_lipsync:单位加入既有真实HostResourceProbe/scene_runtime_limits要求及运行中scheduler.assert_grant循环；复用creation_narration/creation_person_render同类已标定资源预算，不放宽cap或参数、不新增采样/运行队列。类型化单位与注册owner绑定，未知owner/单位不能因字符串前缀获得许可；无真实探针/预算即拒绝。必要测试覆盖许可前零exec、撤销/资源越限/超时真实注册子进程组释放和失败无成功产物，原creation_job规则保持；自制媒体/受控Grant仍只是隔离证据，真实设备/模型运行权不扩大。ACK后继续当前Task，不另起重复任务。

B-PROFILE-API C2直接自动选片消费者：主控回读montage.py三处approved限定确认N33预检单改会造成实际执行冲突，追加B独占montage.py仅这些选择消费者及必要直接专项，消费production同一真实用途资格方法，不在montage复制新一套公式。保留ready、可读来源/实际媒体范围、明确rejected/use_state/段exclude与真实主题/结构选择区别；低语义候选仍需实际人工选择，删除全体默认审批门槛不表示模型可随意采用无关镜头。既有渲染、时间轴、预算/输出和UI-TASK不改，准确回读主控候选后ACK继续当前单。

UI-TASK C1真实会话残差：主控804dcf3回读useAssistantConversation.failUnpersistedTurn签名第三参为failure消息，Home/Task调用均传错误说明；切走原会话后该函数PATCH draft_text=错误说明，会把用户原句恢复成错误并可能覆盖该旧会话后写草稿。另beginTurn后等待draftCheckpoint，scope改变直接return可能没有POST也没有收束本地pending轮次。追加UI独占useAssistantConversation.ts、HomeCommandComposer.tsx及直接会话专项，CreationTask调用仍自己owned。待发原句、错误说明和新草稿分别管理，不能拿错误当正文；导航/未建立持久轮次的取消保留原句及状态，不留下假running。晚失败不得覆盖任意较新草稿/新会话，不凭时间猜文本等于同一轮次；已有真实持久轮次可独立继续并恢复。共享API/Repository仍B写；若需原子服务器恢复，请只给具体接口差额，主控交单写者，不暗加无版本覆盖或内存fallback。只新增迟到失败→切新会话/旧会话已有新草稿、checkpoint等待期间切scope的必要受控证据，复用未影响125检查；不重新加载Pi或模型。

UI-TASK b3afb5d已部分源码交付/retain并按公开提交身份机械组合为03abe39。原单报告未包含末尾会话C1；源码JobProgressPanel仍以styles.blocker包全部成功/历史，H-B006未修，不能按报告关闭。新QA-V2-UI-TASK-REVIEW复用原UI终端，从主控03abe39新clone到`/tmp/main-acceptance-20261009/review-v2-task-review/candidate`分支`codex/review-v2-task-review-20261009`；owned hook/HomeCommandComposer/CreationTaskWorkspace/其局部CSS及直接专项，接上段会话原句/晚失败C1，并修正常进度/成功历史的真实默认样式，红只用于当前失败/具体失败记录。必要估时不能在真实已知数值后还统一称无样本，读取后以字段事实显示。历史保持可恢复折叠，32条不占主操作首屏，不另造日志状态源；只有当前相关失败告警在主操作可见。原103/分页不重复重做。共享main/schema/api/types/Repository仍B独占，必要原子恢复接口给主控串行接线。每次新候选本地Git先设公开weitongxucc-dev/227941069+weitongxucc-dev@users.noreply.github.com身份，禁止全局修改。交clean最小差额/未验，不把旧单缺口藏在报告完成结论中。

F新QA-V2-LOCAL-CLIENT-PORT：原F候选retain，仅复用已有终端，从准确03abe39到`/tmp/main-acceptance-20261009/review-v2-client-port/candidate`，分支`codex/review-v2-client-port-20261009`。主控只读lsof确认当前用户原应用持有3000，不能关闭原应用来为验收让路。允许一个必要且显式的独立本地实例端口入口`--local-web-port=<1..65535>`，仅配合已有绝对`--user-data-dir`且确认为local auth构建时使用；默认仍3000，Supabase稳定callback origin保持3000并拒绝该覆盖。不能默默换端口、借启动失败回退到旧服务或改变用户原数据/认证逻辑。所选端口是本独立userData的固定显式origin，避免每次随机端口丢成员凭据；占用即真实报错，启动和BrowserWindow读同一准确新端口。唯一owned desktop/main.js及既有scripts/lifecycle-smoke.js直接专项，必要desktop README接口说明由主控共享写，不改preload/API/Web/身份/自动更新/外链权限或其他桌面路径。语义ACK后仅源码/节点语法和受控stub验证默认/合法local隔离/无userdata/非法或占用端口/Supabase拒绝/失败释放。公开现有依赖，可启动专项自有无秘密loopback Node fixture；不能启动真实应用/API/Web/GUI/打包/Pi/模型或接触用户服务。具体新测试不得绑定真实3000，免干扰用户App；拒绝清楚，所有ownedfixture PID/端口释放。交clean、两种origin的不存在回退证据与未验，后续真实客户端统一候选再具名运行。

当前接续回执：UI-TASK-REVIEW task_a97db98c7173 / ctx_2ff5da8db295已源码交付32f01e9、133项组件/tsc通过并retain；F-LOCAL-CLIENT-PORT task_f7c4d2d59932 / ctx_3664d4a17ddc已源码交付95581f4、26项受控生命周期门禁通过并retain。两者原写权结束，均未实际GUI验收，主控继续差额审查与机械集成。

C-SOURCE-FORM原ctx_32bdf6c226be的provider最终轮明确consumer_fenced后停止，未发worker_done，三文件未提交差额保留。主控按orchestration恢复指南以该正证abandon原attempt，不停止/删除终端或候选；同一task_dd0225de596e可用--retry-of接续原C可见终端及原arrowworm placement。新attempt必须保留并复核既有`/tmp/main-acceptance-20261009/review-v2-source-form/candidate`三文件差额，禁止重新clone覆盖；先核Task/Dispatch新注入身份和本合同。已有10项sources专项正证可复用，公开依赖改复用当前main-ui-linear/apps/web/node_modules（其中已有react-markdown/remark-gfm），仅必要tsc/diff及变动专项，不安装。新候选repo本地设公开Git身份，交clean提交/报告及准确后端接线未验。消费收件箱使用新注入handle和命令；若仍有fence，停止并报告准确错误，不能用其他Run/旧Dispatch绕过。共享main/schema/api/types仍仅B写，原普通导入默认可用性与使用限制边界保持。

QA-V2-UI-CONVERSATION-REVIEW接续：从主控准确aa2e431新clone到`/tmp/main-acceptance-20261009/review-v2-conversation-review/candidate`，原UI终端保留复用，公开本地Git身份。当前仅useAssistantConversation.ts及直接专项可写；32f01e9已组合但不是完整持久草稿修复。主控准确回读bindTurnEvents先按current.id/turn存在return，再记录turn_started身份；切走后的真实持久确认被丢，晚失败仍可能把已发送原句当未发恢复。事件身份必须先按绑定的conversation/localTurn记录，是否当前界面只控制render，不改变真实提交状态；不猜时间/同文字身份。主控也确认恢复PATCH是无条件服务器覆盖；每hook Map不能约束已卸载实例与重进新hook保存的新草稿。先用必要迟到事件及卸载→新实例编辑→原失败的专项正反证复核，修具名客户端身份差额；对服务器CAS/轮次幂等接线仅给最小准确接口/字段方案，不写main/schema/api/types/Repository，B当前仍独占共享入口。方案须沿唯一conversation SQLite事实源，普通输入/轮次checkpoint/失败恢复都处理真实版本，不能把已持久轮次或新草稿恢复成旧原文。报告未接服务器即未关闭N03，不靠减少测试覆盖、刷新或吞错通过。可另只读识别N06/N14与U01/U04真实入口最小纵向接线提议，明确哪些已存在可复用，不施工无权文件。无真实API/Web/Pi/模型/GUI/用户库，复用公开缓存，只必要新增专项；交clean差额和精确接续方案。

QA-V2-D-PRONUNCIATION：主控aa2e431准确回读voice_clone.Qwen3TTSSubprocessProvider.supports_pronunciation_dictionary=False与qwen3_tts_worker._validate_request拒绝全部非空词典，当前People Dictionary却可保存无格式说明，N21为真实上下游能力缺口。用户已授权需求决策，按本次domain§4.3/DESIGN§5.4普通文字朗读替换收口。复用D原终端、原foxface placement，从准确aa2e431新clone到`/tmp/main-acceptance-20261009/review-v2-pronunciation/candidate`分支`codex/review-v2-pronunciation-20261009`，本地公开Git身份。D仅voice_clone.py、model_workers/qwen3_tts_worker.py及直接专项写权；不写B的narration/avatar/main/schema/People/Production/资源预算。沿现有类型化pronunciation_dictionary与已校验入口应用规则，只变model.generate(text=...)的朗读文字，reference_text/原确认文案/字幕及已有rule fingerprint不变；不用删除词典或false→true标志伪装支持。按语言精确或zh-en通用、范围task>person>workspace、最长词优先的原文一次匹配，避免替换结果级联；同词同语言同范围相冲突拒绝而非猜选。普通文字是明确支持格式，不承诺IPA/SSML/任意音素；已有不支持其他语言规则保持。缓存与输出身份消费既有规则版本，不能修改模型ID/默认声音/重量加载/资源许可/运行队列。受控fake provider/runner与worker验证模型实际得到替换文字、原参考文字保持、作用域语言/重叠/无匹配/冲突真实边界和空词典行为；不加载模型/下载/实际媒体生成/真实DeviceGrant/服务/GUI/用户库。交clean差额/旧整词典拒绝消费者退役证据/听感未验。B当前独占People UI，追加N21该词典字段普通文字说明与例子、保存≠试听，复用已有声音短预览入口真实动作；不添加自动试听或无模型成功。D/B仅共享上述格式，各自owned文件不交叉。

QA-V2-F-ASSISTANT-ENTRY：主控aa2e431回读确认U04 Panel仍图标加发送文字矩形，U01 AppShell在设置/人物/成员等默认用无page context的HomeCommandComposer首页通用建议；已有持久会话/Markdown/复制/过程/停止保持。复用F可见终端及原ui-v51 placement，从aa2e431新clone到`/tmp/main-acceptance-20261009/review-v2-assistant-entry/candidate`公开本地身份。F独占AppShell.tsx、HomeCommandComposer.tsx仅page context/建议呈现段、AssistantConversationPanel.tsx仅紧凑发送/可访问状态段、modern-overrides.css仅assistant控件样式段及独立直接专项文件；禁止hook/CreationTask/People/CreateForm/API/全局token/左右栏几何和UI现有专项文件。真实pathname/task stage/当前material selection用于无对话时的少量相关建议，页面导航不更换会话ID、不重置已发记录/草稿；当前没有注册的业务查询不伪装成可以执行的数据操作，可给准确普通页面动作/相关已有入口。点击文字建议仅填草稿，明确导航入口仅导航，不自动写/外发；已有对话不每页重复欢迎。任务建议只承诺当前stage支持的操作，不在选题阶段承诺直接剪辑字幕；素材请求路由N06暂待唯一后端接线，F不得重写标签提交协议。共享发送用统一小型icon按钮，aria-label/title、真实禁用/运行可读状态、键盘焦点保持，停止仍真实现有按钮；仅传文字旧常驻已退役不可修回。U05/U06/N05原成果复用不重复重做。测试仅最小场景/tsc/差额，公开缓存、不安装/服务/模型/Pi/GUI/用户数据；交clean准确受影响页及截图未验。UI当前仅hook与专属专项写者；以后N06/N14对Home提交的写权在本单交付后再串行接续。

QA-V2-UI-DRAFT-CAS：UI-CONVERSATION-REVIEW task_6f66b0be7d3e/ctx_fd98306bec7d已ee70f9e交付retain，切走真实turn_started身份16项通过并组合d53f0cc；受控卸载/重进/旧失败实际复现无条件PATCH覆盖，N03继续开放。主控已按domain§7.5收口唯一持久草稿与发送身份。本单原UI终端从准确d53f0cc新clone至`/tmp/main-acceptance-20261009/review-v2-draft-cas/candidate`，公开身份；当前第一阶段仅conversation_repository.py及其直接pytest写权，沿同一SQLite行加独立draft_version与请求幂等身份，保存/消费/未持久轮次恢复都是真实原子事务，可信workspace/member隔离不变。旧有会话/文字零版本升级保留，重复同请求返回原轮次、不重复业务动作，不按时间或文字猜身份；重用相同请求ID不同内容/范围拒绝，失败后真实terminal不能再回running。构造旧schema/已存草稿升级、跨两个Repository实例并发保存、卸载后晚恢复与新编辑冲突、首次轮次提交/重试仅一次的必要测试。只临时自有SQLite/受控pytest，不改用户数据库；新字段为本次候选源码兼容存量，不授权真实迁移运行。main/schema/api/types仍B唯一写者，Home/Panel/CSS当前F写者；本单第二阶段要等主控准确release相关文件并给组合候选再接API/hook/Home/Task消费者，不能自行改抢锁或以Repo通过交最终完成。第一阶段完成发status含clean差额/实际接口给主控，可继续只读对照旧活跃消费提出退役位置；必要阻塞按新preamble ask，不复活已完成Dispatch。公开缓存、无安装/服务/Pi/模型/GUI/用户数据/远端，完成后绑定同候选真实可见验收再关闭N03。

C恢复精确错误复核：第二attempt ctx_094d4f64acbd已经3bd6622 clean交源与11项/tsc报告，但provider最终轮仍未worker_done。主控回读准确原始check argv发现用了不存在的`term_d53c0c65-39c8-4ced-af2c-deeb0a842718`（4ced误拼），真实启动回执handle为`term_d53c0c65-39c8-4edd-af2c-deeb0a842718`（4edd）；随后--run读取的是协调者收件箱，consumer_fenced指该协调者绑定，不能据此重绑接管主控。原attempt按最终停工正证abandon/retain；同Task精确第三接续仅用新注入的真实4edd handle消费自身mail并提交现有clean差额/报告，不重复源码/测试、不访问Run协调者信箱。若新注入handle和真实start回执不一致，原样报给主控，不自行翻译或改身份；第三失败不得新Task绕路。该准确更正解决的是handle抄写，不授予绕过真实Dispatch fence。

D-PRONUNCIATION task_e7422e8b4b77/ctx_4cd075e6a91b已6a97d84源码交付retain；主控复用真实现有`/tmp/manta-wil108-20261007/venv/bin/python`跑两文件pytest：24通过、1旧cache测试失败。失败要求voice.person_version改变必须重配音，与本次人物/声音独立及既有_voice_cache_identity不消费person_version冲突，不能因此把已批准缓存改回耦合。D新QA-V2-D-PRONUNCIATION-CHECK仅在原clean候选6a97d84上复核其父aa2e431相同case的未影响基线，并更新该直接test_voice_clone专项为人物版本不无故失效、真正声音/模型/语言/词典版本仍失效；不改narration/生产代码或删除词典版本断言。直接两文件运行用上述已安装pytest，不下载/服务/模型/用户库；report追加精确复核结果与原false环境阻断纠正，公开Git clean delta，音频听感仍未验。原提交保留不amend。

QA-V2-C-SOURCE-API：task_dd0225de596e最终ctx_3faea4f2c137已成功3bd6622/11项/tsc交付retain，精确4edd收件箱可用，原写权结束并机械组合97ef234。C新单在原可见终端从97ef234新clone到`/tmp/main-acceptance-20261009/review-v2-source-api/candidate`公开身份。纵向目标是N15/17及N33资料资格：真实粘贴/文件/网页请求use_state落盘默认available；可选旧license不伪造权利证明，保存knowledge从可信member/source派生归属、真实内容版本与来源，旧授权字符串不阻断默认导入；响应真实use_state。固定版本/可信权限与知识grants完整保留，source不是可信身份绕路，普通输入不授权云外发/持续订阅。既有source_repository for_use与content_allows_use复用，main创建预检/选题/assistant知识查找按真实用途消费，自动选择不得带not_for_automation或reference_only媒体；事实参考可用reference_only、publish拒绝not_for_publish，excluded不进入检索/生成/成片，不以去旧审批取消真实限制。当前第一阶段仅新直接专项tests/test_source_input_contract.py及只读接线核查；不写main/schema/API生产文件（B独占），先证明必要旧契约失败和准确复用，不重新实现已通过39项仓储或下载依赖。公开已装pytest`/tmp/manta-wil108-20261007/venv/bin/python`/公开Web缓存可用。阶段1status给最小API delta；只有主控明确B release并给组合候选后，阶段2才可独占main.py/schemas.py这些来源/资格段、source_repository.py必要直接增量、sources/page/source-api输入输出及直接专项。UI draft仓储独立，draft API在本单共享main结案后接续，不能并行抢写。

准确C前端残差也需本纵向收口：saveToKnowledge只在listKnowledgeItems回读ID后成功，但当前该列表按既有excluded谓词刻意不返回排除条目；主动保存excluded不应因真实限制而假报未保存或重复创建。响应/回读须能核对当前可信源ID、知识ID与版本/use_state，区分登记成功与可用于检索，保留使用限制，不通过让excluded进入检索列表修复；选中源在异步期间变化不能将旧成功标在新源上，失败保留原条目/输入、重试真实同一对象。复用已确认ScriptLibrary与knowledge selection的固定版本规则；不改用户资料/删除旧source。无安装/真实服务/模型/Pi/GUI/用户库Key/账号/外发；TestClient/临时DB与受控网页文本替身只是接口证据，最终需要同候选真实UI。

QA-V2-E-SCRIPT-TRANSFER-REVIEW：复用E原终端/原creation-wave placement，仅主控97ef234候选只读和产品§5/8、领域Plan/Script/确认、DESIGN对话/文案库直接章节。N14仍无首页回复→TaskScript草稿→用户确认→ScriptLibrary可见出口，不用“复制”或普通KnowledgeItem保存冒充闭环。准确核查已有assistant/script-context、assistant/script-draft、confirm-script、script-library与CreationTask stages/选题/必要真实来源契约，给一个可实施的最小完整纵向方案：无已有任务时如何保留回复原文和来源身份进入实际建任务流程；已有适用任务时如何保存未确认草稿、再沿原确认与文案库保存出口；不适用阶段如何说明/返回，不自动覆写已确认稿/旧成片，不引入第二文案事实库。判明本地保存是否被cloud_egress误绑定、固定对象/版本/晚响应保护和真实原端点能复用的部分；不要假定去所有阶段门槛即可成功。只报告具体需改文件/接口/最小真实数据回归与尚需模型权限步骤，源码不写、不建新服务/跑API/模型/Pi/GUI/用户数据/安装或读历史ZIP。不扫描全部历史；输出`/tmp/main-acceptance-20261009/review-v2-script-transfer-review/report.md`，准确只读结果 worker_done/retain，主控随后作决定和具名实施而非交用户新计划。

### QA-V2-F-MATERIAL-READ-TOOLS：N06真实只读能力先接线

F入口42017ab已源码交付retain并机械组合01f9ae2，UI会话Repo be9e51a已组合b203122；主控将最新共享词典/CAS/桌面端口说明机械同步为准确197870bc0da163cc6f2afe9f19ad698ff25c43db。B仍唯一main/schema/API/types/People/创建表单写者，UI第二阶段仍HOLD。本单仅复用原F可见线程，从准确197870b clone到`/tmp/main-acceptance-20261009/review-v2-material-read-tools/candidate`公开身份。

来源N06/R02/H-B022，owned assistant_pi.py素材prompt、业务工具能力注册和run_material_turn；agent_tools/material_tools.ts、diary_tools.ts素材注册/检索说明相关段及对应直接专项。无选中时可查询真实可见库的分析状态和检索候选；有选择时可读完整文字分析/现有转写事实/所属文件与时间范围，不只我的标签。调用同一可信业务HTTP读接口，白名单输出必要文字事实、来源、分项状态，不外发绝对路径/原始媒体/整库正文，不臆造声音识别/用途结论。受控请求仍透传原成员/空间认证；零结果、空库、失败分开，完整数量需按真实分页/总计而不是首100条假称全库。局部写入始终要求发送时明确选中对象，注册无选中读能力不能开放模型自行指定ID/路径/成员，不改变B104真实写入回读及系统标签保留。

本阶段可使run_material_turn接受选中范围可空并绑定素材库会话，但API/schema/Home的必选前置当前仍待唯一文件释放，不能声称用户链路已修；不写main/schema/libAPI/types/Home/hook/MaterialLibrary/全局CSS，不动C未来知识检索段、资源许可/权限/领域。补未选库状态、已选内容摘要事实、部分转写/OCR、不同文件同时间范围、无选择修改明确拒绝、HTTP错误与可信请求头的必要直接Node/Python替身测试；不运行Pi/模型/API服务/GUI/媒体/用户库/安装/远端。交clean提交、准确接口 delta 和API前置残差；阶段1完成即按阶段任务worker_done，主控串行具名接API+Home并做同候选GUI，不冒充整N06完成。

B新报告旧同步局部重配音`_regenerate_creation_task_narration_segment`未传真实grant/repository/reader，对范围化声音会被核心门禁拒绝；保留为实际制作编辑接续叶，不借人物编辑许可或新增并行调度器，不因独立试听排队通过关闭整条修改→重出链。

### QA-V2-C-SOURCE-API-IMPLEMENT：共享来源接口正式释放

B msg_a78982b37b5f已明确345d88f clean交付后停止新增产品修改并释放main/schema/API/types；主控机械组合为1993ad062c3f7604614c60beb2159b3717d259ad。B的独立预览终态metadata并发残差需新单补，旧同步局部配音/旧测试来源夹具/UI三人物消费者/N36仍开放，队列分类档案独立试听为normal_make，不凭preview名称升级作品局部预览优先。

C阶段1 task_e3a8068df898/ctx_15d1fec37b07已06d1104 succeeded/retain，仅四条真实旧缺口证据不是修复，旧API字段/限制丢失与excluded假保存均确认。本阶段C新Task从准确1993ad0 clone到`/tmp/main-acceptance-20261009/review-v2-source-api-implementation/candidate`新公开分支，只写main.py来源录入/knowledge response/预检资料/选题与assistant script资料/ScriptLibrary来源资格相关段、schemas.py上述直接字段、source_repository.py必要资格/可信保存版本增量、source-api.ts/sources/page.tsx及直接专项；B已释放libAPI/types但本单不得无关改，必要资料类型只source-api。UI仅Repo/hook待main release，F仅assistant_pi/material_tools/diary_tools素材段；C不得改这些文件或生产仓储/人物/调度/全局CSS/桌面。

三录入use_state校验落盘默认available并响应真实状态；普通保存省略旧ownership/authorization，可信owner_user_id来自member，来源/hash/真实系统版本来自SourceRepository。法定权利人/证明空就是空，不能将上传成员名或伪造授权句写成版权证明；已有证明/引用版本保持。保存excluded使用真实保存回执(id/source/version/use_state)确认，browse仍排除不能假失败或让excluded进入检索。旧手动source重复保存也应识别同一源/真实版本，失败重试不造多个KnowledgeItem；内容hash去重不能返回/覆盖另一成员私人条目或改变grants。只为真实登记与本次来源差额增加必要查询/事务，不造第二事实库或一套通用兜底。

沿既有for_use/content_allows_use按真实purpose贯穿Plan/固定资料版本/topic/script上下文、实际agent_tools/diary_tools.ts的find_knowledge读取GET knowledge或script-context（当前F owned TS，不写但必须核真实调用），ScriptLibrary读取复用：不使用不能读取，reference_only事实参考可用，不用于自动任务不能自动候选，不用于发布不能发布；同一素材资格与B已实现后端保持。移除所有普通内容默认必须旧权利证明的活跃guard（_creation_task_topic_inputs和_script_library_sources等），而非删UI文字。license字符串不是ExternalConsent，blank/unknown不能继续伪装实际外发授权；纯本地保存不按cloud_egress=True强制校验云端。真实外部调用仍遵守产品2.6已有有效连接/必要文字证据/服务与数据类型告知，不新增收费、外发媒体或模型运行许可；对真实scope/version/grants/revocation与用户限制完整重核，provider失败不回退。

实际phase1四项要转为修复后期望测试，可定向copy旧06d1104测试文件再改断言，不整包cherry-pick BUG_PASS。最小三入口/缺文件/不同状态/可信member、excluded receipt/迟到另一源不串状态/同源重试、Plan-topic-script-library默认可用与限制/固定历史版及撤销检查。只TestClient临时DB/文字网页替身、既有pytest/Web cache/tsc；无服务/模型/Pi/GUI/用户数据/Key/安装/账号/外发/远端。交clean准确delta/report和共享main/schema释放回执，UI持久CAS接续仅此结束后，不把fixture当真实界面。

### B独立预览接续 / D旧直接验证夹具

B-PROFILE-API task_36e5c2cf4607/ctx_9a8640de4abf按终态绑定并发残差failed结算并retain，345d88f实际源码组合1993ad0，29接口/门禁+36组件/tsc证据保留，未伪装GUI。C正式来源 task_73c344661ad2/ctx_48b39b372a5d ready，唯一main/schema来源写者；F读素材 task_f69e002c2824/ctx_d735bdd718dd ready且REBASE_ACK，唯一三个素材工具模块写者；UI原CAS第二阶段继续HOLD共享API，不抢文件。

B新QA-V2-B-PREVIEW-BINDING，仅在原clean345d88f candidate新分支codex/review-v2-preview-binding-20261009，owned profile_preview_execution.py、production.py media_preview绑定/持久终态相关段、test_profile_api_contract.py直接专项。共享main/schema/API/types/人物UI写权明确结束归C后再UI，声音和人物生成核心/D词典不写。实证scheduler.submit之后bind之前既有runtime tick执行，service捕获None后终态整payload覆盖真实request_id；终态发布必须与本次grant request_id精确绑定，bind重读保留已完成/失败/取消事实，不回滚状态；采用既有scheduler事务/业务checkpoint，不能新增队列或泛化Hook。必要成功、失败与取消交错直接测试，旧29/36无变化不重跑，恢复未知仍不释放许可。队列分类独立档案试听无当前作品依赖，normal_make保留公平/预算。只临时DB/合成provider、控制runtime时序，无真实资源准入/服务/模型/GUI/用户库/安装/远端；clean delta/report和准确未验，原Task failed不擦除。

D新QA-V2-D-PREVIEW-TEST-FIXTURES，从准确1993ad0 clone到`/tmp/main-acceptance-20261009/review-v2-preview-test-fixtures/candidate`新公开分支；只owned tests/test_media_preview_runs.py、tests/test_media_preview_api.py与确需新增一个私有直接test辅助文件，禁止改任何生产代码、B新test_profile_api_contract.py、main/schema/资源cap或跳过测试。先复核两文件真实既有失败，修旧缺ProfileReference/可信登记/任意无效媒体字节夹具成真实合成WAV/PNG和可信本地测试成员；预览正例沿本次唯一DeviceRuntime/专属grant，HTTP queued再实际受控完成回读，不把queued看作成功，不以宽泛可接受多个status削弱断言。保留来源失联/成员隔离/固定版本/取消/无grant拒绝/模型不可用/历史不可覆盖等真实负例；必要直接复用既有明确fixture helper，不新增产品抽象或第二调度器。已装pytest及测试替身/自有合成媒体可用，无用户素材/Key/模型/服务/Pi/GUI/安装/远端。仅这两直接文件完整测试一次，发现生产缺口报准确case与事实由主控具名，不能为绿测降门禁。交clean提交与受控验证报告，仍不宣称实际模型/GUI或全仓CI通过。

### 当前真实认证读断点：片段检索与原生媒体

F msg_bca6ac695a54及主控1993ad0回读确认search_production_material_segments未传可信reader，ProductionRepository.search_material_segments使用默认全私有list_assets；通用Pi find_materials直接消费该端点，主控预检仅事后过滤也会受未授权条目的排序/limit影响。不是只换文案可修。B当前PREVIEW-BINDING原product文件production.py唯一写者追加仅该search_material_segments签名/首次list_assets reader参数及直接私有隔离专项；C当前main唯一写者追加同一端点和真实_preflight检索调用透传_content_reader/真实plan reader，必要直接HTTP私有成员负例。不事后用前端过滤替代召回前权限；默认review_state不重引入旧门槛，用途限制及范围真实资格保持。B/C需互报精确viewer_user_id/local_diagnostic接口并ACK，本单不开放用户数据或旧未过滤工具运行。

主控另确认People ReferencePlayback、独立预览结果和VideoEditor源媒体为原生src请求，local-member-auth只把成员令牌放localStorage+fetch自定义头；Next local API代理不补该头，backend _local_member_token只读头。新的private档案JSON可读而原生图片/音视频请求匿名会404，是同类媒体读取身份断点。E新QA-V2-E-NATIVE-MEDIA-IDENTITY只owned Web local-member-auth.ts、middleware.ts、必要一个明确的本地原生媒体传输helper与直接测试，原creation-wave树保留，从准确1993ad0新clone到`/tmp/main-acceptance-20261009/review-v2-native-media-identity/candidate`公开分支。不写后端main/schema/权限/生产/People/编辑器/API/types/用户令牌/配置/全局CSS。

沿已有本地可信成员令牌，仅为同源原生GET/HEAD媒体请求同步一个会话cookie传输已有令牌（SameSite=Strict、Path=/api，HTTPS才Secure；与现有JS localStorage同一可见性，不新增令牌/身份/服务或匿名升权）。store/clear实际同步和清除；cookie名按当前原点端口区分避免同主机端口串App，值不进URL/日志/公开证据。Next仅local模式、准确现有媒体路由、请求原X-Local-Member-Token头缺席时将cookie转同一头，明确无效/空的已有头不能被cookie替代，缺cookie不伪造身份；Supabase仍用原服务端session/Authorization，不能继承本地成员。媒体URL和Range原样代理，不整文件blob缓存或复制。断点修正不扩大任何对象可见性，后端仍每请求验证成员和workspace/私有/grant；媒体错误仍真实显示，不回退到旧文件。

E先核当前实际媒体端点及现有store/clear调用，不罗列猜测式通配路径；测试用合成成员字符串、Next Request/Headers替身，验证native GET/HEAD/Range身份透传、store/clear/切成员/端口隔离、显式无效头不回退、Supabase与非媒体POST不注入、没有把令牌写入URL/响应业务正文。必要当前local auth/middleware专项+tsc即可；未运行真实浏览器/API/GUI，源机制和受控代理证据不冒称可播放。无安装/Pi/模型/用户数据/Key/真实Cookie/账号/媒体/服务/远端；clean delta/report交主控同候选真实窗口验收。

D msg_985efbb1c7fa补可信登记后的实际预览负例：既有授权撤销被profile_reference_input先抛ValueError，VoicePreview随后误报reference_audio_invalid；Avatar准备直接抛错而未形成authorization_revoked阻断事实。B当前仍唯一production.py写者具名追加narration.py VoicePreviewService.request、avatar_lipsync.py AvatarPreviewService.request和必要resolve_avatar_lipsync_request小范围接线/其直接test_profile_api_contract专项；D仍仅两旧测试文件写者。把可信成员/空间/私有和真实来源读取校验与生成用途资格分开准确归因，沿现有profile_use_failure/_authorization_failure不另建一套规则；显式撤销/过期/用途限制必须blocked、零provider且不复用旧成功，来源失联才来源错误，权限读不到仍404/403不得泄露private授权事实。实际owner准入/执行和生成服务继续严格资格、来源hash/版本/permit重核，不通过prepare/read模式跳过执行门禁。若必要加入明确for_generation读取参数只给预览准备事实读取，默认生成路径保持严格并测试无许可/撤销零执行，不能静默fallback。只必要新撤销/失联/权限负例，旧29/36不重复；D保留原负例，待B准确clean可串行fetch该净增量后重验受影响项，失败不是改断言理由。无新增运行/用户数据/模型/媒体/服务许可。

### QA-V2-F-PREFLIGHT-RETURN：未创建任务的修复返回闭环

主控已组合B终态绑定0b851cd→5250964、召回前可信读者82414ab→017ecbc、E原生媒体身份349e7bc→11e724d。C源录入b75cf69独立完成，主控在C候选机械追加仅production.py召回差额535df2b，待真实HTTP/Plan专项后释放main/schema给UI；E/D旧单retain源权结束。D55992ae夹具23/25，两个撤销负例保持原断言待B修复，不以报告succeeded宣布通过。

F新单基于准确11e724d新clone到`/tmp/main-acceptance-20261009/review-v2-preflight-return/candidate`，公开分支codex/review-v2-preflight-return-20261009，原终端/旧树保留。唯一owned CreateProductionTaskForm.tsx/.module.css、AppShell.tsx仅必要返回创作入口、production/page.tsx仅草稿/模板参数绑定、一个具体未创建任务表单草稿/返回helper及一个对应返回组件与其直接专项。不得写Home/hook/Task/main/schema/api.ts/types.ts/资料来源页/E认证或共享CSS；主控DESIGN§5.1此新增规则先读REBASE_ACK。不要新持久化API、Task数据库或第二会话状态框架。

修N36/N35及N13此表单消费者：预检按真实reason/stage映射资料、声音、人物、素材、连接的准确现有入口，不猜不存在字段。修复前将目标/名称/数量/时长/现有语言比例/模板固定版本/人物声音固定引用保存到当前浏览器会话的本次稳定草稿UUID，按实际ProductionFoundation.profile.workspace_id和现有已验证local whoami member_id（Supabase使用现有客户端已验证user）隔离；不使用token作为草稿键，不存凭据，不让别的成员/空间恢复。只用户草稿，可撤销，不是已保存任务或已确认方案。只存必要字段/UUID，不复制资料/音频/检查响应；URL仅本次UUID及模板固定引用，正文不上URL。sessionStorage实际边界错误准确就近说明并保留当前输入，不静默fallback或假称可恢复。

资源页经AppShell提供可发现返回创作入口；return_to只接受同原点准确/production?new=1&draft_id=<UUID>及必要模板固定引用，不开放任意外链/credential URL/自由路径。返回读取本成员本空间草稿，回读当前声音人物及实际预检，不复用旧preflight_fingerprint或把点击修复看作完成；用户更改后晚回复不覆盖新输入。初始/模板重设不能覆盖恢复的30秒目标/已改选择；默认声音晚到不能覆盖恢复的显式选择。成功确认真实Plan后只清本次草稿，失败保留；取消/关闭返回语义明确，不能自动调用创建/模型生成。普通业务层显示名称与下一步，必要ID/证明空值在诊断层，不能改资格规则或新索取证明。任务内目标已有业务存储，其消费者留UI后续单写，本单不冒称任务内修复闭环完成。

最小受控Web测试跨资源→返回字段/固定版/30秒保持、相邻两个草稿不串、成员/空间隔离、错误return_to拒绝、storage失败、晚预检/默认声音不覆盖、真实确认成功清自身及失败保留，必要现有表单/AppShell专项+tsc。已有未影响预检/三栏/模板证据复用。使用公开既有cache/mock API/验证身份，不安装、不启动App/API/Pi/模型/GUI，不读取用户DB/Key/媒体/账号/cookie或向远端操作；源码/组件测试不冒称真机。交clean准确SHA/report、读回与残余边界，不用未接通按钮充数。

### QA-V2-UI-DRAFT-CAS 第二阶段正式释放（2026-10-09）

C源码b75cf69→6ea6346、直接f6face1→3035a25已审核机械组合3035a25e077e18422624edd2483bf26d1e5e2175，C原Dispatch task_73c344661ad2/ctx_48b39b372a5d succeeded/retain并释放main/schema。45API/37Web受控PASS；C临时manta缓存缺Markdown不得用于最终组合，主控既有main-ui-linear依赖可用。旧相邻候选测试缺transcript/可信档案来源和TS207真实错误仍待必要修，不标全仓CI通过。D原两个撤销负例已在组合60d7fa6由主控最小重验2PASS，复用原其余23；来源asset excluded归因仍B具名当前单核。F新修复返回仅表单/AppShell，不与本阶段源码重叠。

UI原task_0ecc5c23b3e0/ctx_df85c79f03c8保持同一生命周期，phase1已be9e51a→b203122且8Repo PASS不重跑。现在由UI唯一接 main.py/schemas.py 会话CRUD/开始轮次/失败恢复及素材query绑定段、lib/api.ts/lib/types.ts相关会话/素材字段、conversation_repository.py必要接线、hook/HomeCommandComposer/AssistantConversationPanel/CreationTaskWorkspace及局部CSS和直接专项。基于精确3035a25在原CAS隔离candidate fetch并机械组合源，不丢现有Repo成果，不写F CreateProductionTaskForm/AppShell/返回helper、C source_repository/source-api/来源/知识页、B production/narration/avatar/preview、E native-media认证、共享CSS/桌面。

所有真实draft写入显式expected draft_version CAS，稳定client_request_id贯穿真实本地一次发送/失败重试、流式与HTTP结果；同请求只复用同轮不再次运行工具，新轮消费对应草稿原子写，任何晚失败不能覆盖生成期间新输入。原旧无条件PATCH/主流程create_turn写入要退役，明确给不存在证明，不保留并行宽松路线。草稿冲突保留新值/原句准确提示，确认失败/取消/断流和中途编辑各守真实终态。原流式/即时用户入列/真实停止/可编辑下一句/命令真实分派与Markdown复制保留，修TS207正确task测试类型，不以忽略测试打绿；当前安装版本Pi清单仅源码注册可核，不擅自真实运行模型。

N06当前F eca已组合3b195：run_material_turn.selected_material可空，library_status/search_material_library真实只读工具已实现。但现main/schema/API/Home仍强制选择，必须本阶段接齐。素材页使用同一可信成员/workspace的素材库应用会话，选择改变只调整下一轮上下文，不隐式创建/删除会话或草稿；显式新对话才换会话，旧按资产scope会话仍可被用户从真实会话索引恢复，不造假历史。新增素材库scope使用明确稳定library语义，发送时将明确已选asset/segment与精确范围固定在轮次；服务端当前成员/space真实核source，模型不能选别的对象或路径。无选择只开放全库状态/检索，修改工具要求发送时明确绑定；读/写不误投为创建任务。切选择后晚结果保留原轮次事实但不覆盖当前右侧对象，成功业务结果回读同一对象并刷新当前可见投影，不重复执行。只fixture/工具替身，无真实Pi/Key/用户素材HTTP运行许可。

本阶段追加Task三个旧耦合消费者：PersonAndVoiceFacts、人物Inspector、generatePersonRender使用现Plan.selected_person独立固定版本，绝不从selected_voice.person_profile_id/person_version猜人物；0%目标不触发人物provider，缺人物给准确入口，已选声音仍可旁白。B HTTP/核心已修的可用性和真模型条件不重造前端规则。任务预检中资源修复返回留下一具名纵向单，不抢F手动表单。N14对话转任务文案需Plan/Topic/TaskScript真实来源与确认，不在本阶段用rawReply直接写文案库。

最小全接口CAS/重复请求零重复工具/失败新草稿/跨页恢复/切stage选择及旧资产会话恢复、N06无选择状态/选对象标签真实固定范围与晚回包、独立声音人物/0%专项，必要既有直接Web与tsc；API使用临时DB/TestClient/受控工具/mockgrant不真实模型。scope/source依赖变化只重测受影响，无安装/服务/GUI/用户数据/Key/媒体/账号/发布/远端。必须clean精确SHA/report+旧写入口退役证明及source/fixture与GUI未验，源权重读ACK后立即持续实施，不再等旧主控START。

### 必要组合CI修复：具名测试迁移，不降低领域门禁

主控按实际CI在3035a25一次全API隔离运行：1651PASS、110FAIL、161ERROR，12warnings/3subtests，日志`/tmp/main-controller-20261009/review-v2/backend-3035a25.log`。Ruff0.15.7精确47项，日志`/tmp/main-controller-20261009/review-v2/ruff-3035a25.log`；离线缓存无该版本后仅主控用uvx取得固定测试工具，不改依赖/锁。大量181次旧档案夹具缺真实来源、手动保存旧必填proof字段导致extra_forbidden，以及无读者私有检索被新门禁拒绝。不能据数量全称旧夹具，不复现/审核就改assert；也不为绿CI放宽真实权限/来源/设备许可。

B当前单补本单test_profile_api_contract新来源repository purpose接口替身及Ruff直接规范化，明确追加test_profile_reference_contract仅未用变量/import/行内控制规范化；montage.py只去未用material_allows_automation import，唯一此file写者，不改逻辑。UI当前main UUID未定义与未用CreationPlanVoiceReference，及assistant相关必要旧夹具由UI同file单写，不由别的worker碰。

D新QA-V2-D-PROFILE-FIXTURE-MIGRATION基于3035a25新隔离clone `/tmp/main-acceptance-20261009/review-v2-profile-fixture-migration/candidate`。唯一owned测试test_montage_pipeline.py、test_creation_execution.py、test_media_profile_api.py、test_media_profile_repository.py、test_member_permits.py、test_crop_preview_description.py、test_linked_timeline_reorder.py、test_subtitle_time_sync.py、test_video_templates.py、前单tests/_media_preview_test_fixtures.py仅必要共用合成辅助，及test_media_preview_runs.py两未用变量lint。不写任何生产/API/schema/别的测试，尤其B两profile_contract、UI任何assistant。按真实新档案参考/固定独立人物声音/可信owner和member、当前reader/工作区输入迁移旧positive helper，优先一处共用helper服务多个消费者，不复制来源链；生成测试沿真实独有DeviceRuntime/许可测试替身。保留拒绝/撤销/私有/未知准入/切scope/失败恢复负例；旧被退役依赖的断言可按当前权威改，但须具体说明，不宽泛accept status/skip/noqa。

C新QA-V2-C-SOURCE-TEST-MIGRATION基于3035a25新隔离clone `/tmp/main-acceptance-20261009/review-v2-source-test-migration/candidate`。唯一owned tests/test_knowledge_file_parsing.py、test_knowledge_visibility_api.py、test_content_upload_ownership_http.py、test_web_snapshot.py、test_source_api.py，仅迁移旧save-source请求为当前{}与真实系统版本、可信member/hash固定版本/grants/use_state预期；禁止任何生产/main/schema/API/Web或assistant测试。旧证明门槛预期按新权威调整，不能删跨成员/撤销/复用来源/未知格式错误负例，metadata不得伪造证明。精确同一已失败files最小复验，source_repo45与Web37无关不重跑。

两单仅测试夹具/断言/规范化，不做产品修复；若失败是实际产品缺口，保留准确case/rootcause并交主控指定源owner，不绕过或私有改生产。均使用临时DB/合成媒体/既有依赖和受控provider，不运行真实模型/App/Pi/用户DB/Key/媒体/账号/常驻服务/安装/远端。必要真实ffmpeg合成解码允许自有/tmp范围，queued与完成不能混淆；源目录read仅故障定位，不扫旧历史。先ACK精确file名单再改，受影响file完整一轮必要pytest和准确Ruff；未影响的PASS复用。报告clean准确SHA/改前实际失败类别/改后行为/残余生产缺口，主控最终CI保留独立验收。

### QA-V2-E-READ-STATE-TEST-MIGRATION / D与UI检查边界补充

D前节测试迁移追加仅tests/test_production_foundation.py（其中旧档案/搜索fixture共享单file，整file唯一D）；不要拆给E造成并行写。同一当前root全API证据依然有效，不重跑全仓。UI会话直接tests/test_assistant_script.py/test_assistant_pi.py归当前UI唯一写者（前者旧保存proof fixture需{}，实际CAS新请求字段也由UI一起消费；后者仅一个无用变量及必要phase2直接变化），E不得写。

E新QA-V2-E-READ-STATE-TEST-MIGRATION精确3035a25 clone `/tmp/main-acceptance-20261009/review-v2-read-state-test-migration/candidate`公开分支codex/review-v2-read-state-test-migration-20261009。唯一owned tests/test_material_search_candidates.py、test_agent_seam_regression.py、test_material_automation_gate.py、test_material_ingestion.py、test_material_speech.py、test_material_tag_navigation.py、test_visual_understanding.py、test_local_model_health.py、test_2_0_foundation.py、test_device_runtime_wiring.py、test_workspace_llm_secrets.py、test_qwen_tts_worker.py（最后仅无用payload）。不得任何生产、main/schema、Web或D/C/UI/B测试，发现真产品缺口只精确上报不得自行扩源。

真实召回权限新契约先身份过滤再排序/limit：旧检索/录入模拟需可信可读owner/member或明确测试所需已共享范围，真实入库fixture需对应模态/范围事实；不得给所有调用local_diagnostic=True或include_private=True兜底、假成员ID绕读，特别私有/撤销负例保持。旧update_segment helper缺required transcript时使用该fixture原片段真实值/明确输入，不为了消错清转写。主体身份/当前use_state和自动资格与手动检索分开，旧默认必须approved的期望按活动权威迁移，明确rejected/排除/不自动仍阻断。

N32运行事实用当前工作区Vault/配置/最近真实探测，未验证配置不是当前调用成功或失败；健康测试需要分未配、待真实验证、最近成功、真实失败/诊断，不把旧全局配置/无工作区mock当用户实况，也不能移除失败/修复证据。设备注册只用当前唯一DeviceRuntime真实新增media_preview业务owner，旧精确集合断言要包含真实注册及owner/executor事实，不放宽any-set。成员Key/Vault用既有内存工具替身，固定模型port替身/合成文本/媒体/临时DB允许，绝不真实Key/AppSupport/用户媒体/模型/Pi/App/API服务/GUI/远端/安装。

先读权威/当前失败日志后REBASE_ACK；只这些已失败files必要pytest一次和Ruff，语义不变lint不需新测试。root完整API日志及精确ruff日志前节路径可定向读，逐rootcause解释既有实现/fixture/待源fix，不机械改所有assert。clean准确SHA/report `/tmp/main-acceptance-20261009/review-v2-read-state-test-migration/report.md`；未修产品fail维持原断言，交主控单写者修。主控最终CI与真机验收独立，受控mock不能证明新真实模型/媒体链通。

C测试迁移505f35e已44PASS/Ruff0.15.7PASS/clean交付并retain，main/source写权仍UI阶段2独占。主控实际Web ESLint3errors：UI hook两个prefer-const归UI现单；C source/page helper useStateLabel被规则按Hook误判。C新QA-V2-C-SOURCE-LINT仅从原clean505f35e隔离candidate新公开分支，改apps/web/src/app/sources/page.tsx这个普通格式化函数名称/直接调用为非Hook名字，不改语义/其他file，不用eslint-disable。纯重命名仅精确该file ESLint+diffcheck，不新增镜像test/重跑前44/37/全Web。既有main-ui-linear Web公开cache已含Markdown可临时symlink，不使用缺包mantaCache，不下载/安装/运行服务GUI模型。源码clean净delta/report后释放，原原Arrowworm不改；这是必要实际CI错误，不扩大来源/业务设计。

### QA-V2-C-REPLY-SCRIPT-TRANSFER：N14纵向负责人，分阶段单写

主控采纳当前领域§7.5及DESIGN§2新增对话回复→TaskScript来源移交规则，现B preview全源写权已释放/retain，C lint原单已完成retain。准确组合ead4f2b590513503c69377a88f689cf7bc3c5750；C新Task从此clone到`/tmp/main-acceptance-20261009/review-v2-reply-script-transfer/candidate`公开分支codex/review-v2-reply-script-transfer-20261009。同一纵向负责人直到真实API/UI/任务确认/库返回链源码完成，不以阶段1Repo宣布N14关闭。先REBASE_ACK读取当前产品2.1/2.6、领域5/7.5、DESIGN2及E只读report（其中旧license外发guard建议已被当前C source权威取代，不继续旧证明门槛），报告`/tmp/main-acceptance-20261009/review-v2-reply-script-transfer/report.md`。

阶段1唯一owned production.py仅CreationPlan/TaskScript/ScriptGenerationRun必要来源引用与原子确认/修订方法、tests/test_reply_script_transfer.py新直接专项；不会写B已结束人物/声音/媒体preview/资源/其它生产段。只复用现有Plan/Task/TaskEvent/Script/Run payload和同一SQLite，不建第二文案库/Task表/同步状态框架。来源是服务端核实的conversation_id+已完成turn_id+正文摘要，成员与space由真实身份解析，不接受调用方任意owner/路径/原始工具payload；正文仍从原会话读，暂存引用不复制整份回复。确认Plan时可携该明确origin和稳定client conversion/request id，来源/输入/成员准确相同重试回读同一Plan/Task，冲突409不重建，绑定与创建事务一致，不用浏览器closure冒称服务器无副作用。

已有待确认脚本转入必须实际Plan/selectedTopic/current来源版本一致、Task脚本阶段且未确认、期望现有draft修订与当前相同；已有正文明确用户替换选择才写。稿段来源只真实input_references，不把对话正文当事实证据或自动给所有资料勾上，不允许stage绕过/已确认稿改写/另任务覆盖；正文可由用户在明确草稿入口编辑，原turn引用保持。有效稿保存和用户确认分别沿既有方法，保存成功回读同一稿、确认后库用原save_script_library_entry，不直接 rawReply→库。修订比较用既有持久TaskScript.updated_at精确token并在原事务核Task/稿/Topic/Plan，不以页面实例内存判断；所有相关UI/Agent写入及确认消费在阶段2一起迁移，不能只新增旁路校验而保留当前旧update/confirm任意覆盖。改前/后真实scope/version/取消/失败/重复请求负例保留。

阶段1源码可先独立实现并提交，但主控组合必须等阶段2旧消费者退役后，不能将产生旧签名断点的片段先合成通过。main/schema/libAPI/types/Home/Panel/Task当前唯一UI-CAS写者，明确HOLD这些文件；可只读检查待接线，禁止修改或偷偷复制UI dirty。C阶段1完成仍在同一Task等待主控准确组合/正式release，再独占阶段2 main/schema只originPlan/transfer/ScriptDraft/Confirm/Library响应与libAPI/types/Home/Panel/Task/ScriptEditor必要消费者、knowledge/KnowledgeWorkspace现文案库条目深链及局部CSS/直接专项；此处是待release规划不是现在写权。F表单/AppShell始终不写，E媒体认证不写，D/C/E旧测试迁移files不重写。

阶段2新回复“带入文案草稿”明确原轮次→用户选择现Task或先真实确认Plan→真正Topic确认→Task原文案草稿→手动核对资料和正文→确认→保存文案库准确entry/version/来源Task。先复用真实TaskScript editor/confirm/source模型，必要抽取同一file内现组件为复用组件而非新文案表单事实库。晚回包不切换新Task/会话或清较新草稿，明确版本冲突保留原稿可回查；未知阶段给真实下一步不是无效按钮。已确认稿只能沿既有明确修订流程，不伪造新Task成功。存在准确不可执行条件时保留原回复并标叶，不停止其他独立路径。

允许源实现/临时DB/TestClient/可信合成成员、Task/文字资料、mock模型/工具与旧公开依赖最小验证，不运行真实模型/Pi/用户Key/用户DB/媒体/账号/常驻App/API服务/GUI/远端/安装。阶段1必要Repo真实来源/错scope/非完成turn/固定版本/原子重试、阶段2实际HTTP与Web直接链验证；全模型/GUI真实性仍主控新候选后验，不能自动获得外发许可。阶段2发布前给旧draft/update/confirm消费者不存在证明，confirm可复用版与源链按当前实际产物判断。

### 组合读状态复验与两个真实投影断点

E原只跑一次后改23失败夹具就交，最终源码未验，不能声明完成。主控实际组合7cdaa9c准确受影响11行为files一次必要复验122PASS/1FAIL（日志`/tmp/main-controller-20261009/review-v2/read-state-final.log`），失败test_material_ingestion.py::test_segment_usage_change_updates_persisted_tags_and_search：新用途a_roll保存后system_tags仍用途:b_roll。原一次全API结果保留其准确候选和检查时间，不因局部复验改写。失败后的实际修复允许必要复验，禁止“只跑一次”用作留下未验修订的理由。

当前production.py唯一C REPLY-SCRIPT-TRANSFER阶段1追加仅update_segment用途变化投影：依据新领域§3.3，说明保存不得重算/清识别标签；明确用途变化只同步该真实usage_role派生用途标签，其余系统识别/用户tag/转写完整保留，不恢复整包_seg_system_tags或补个人标签掩盖。C应对此叶单独clean提交可先主控集成，不与Script签名phase1未接线混包。直接既有E失败用例由主控回读组合最小复验；C可复制一份私有新直接tag test但不写E原test_material_ingestion。目标field未变不重算，无新宽泛兼容路径。

主控另源码确认runtime_health._deepseek_item取当前state.detail却丢state.repair_action，固定通用提示；E最近失败测试改去只断言Settings底层record不能证明实际界面投影行动一致。E新QA-V2-E-HEALTH-REPAIR从精确7cdaa9c新clone`/tmp/main-acceptance-20261009/review-v2-health-repair/candidate`公开分支，唯一runtime_health.py该helper与tests/test_local_model_health.py此修复直接测试，使用同一当前workspace有效verification实际repair_action（非空时）供健康投影，未验证/未配既有真实提示保持，成功不制造故障。当前失败输出的建议与state事实一致，不新模型探测/网络/服务或猜字段。测试应断言health_item.repair_action本身，不从业务输出退回隐蔽底层record；保留scope隔离/未验证/真失败/成功证据。

此单只这2files必要pytest/Ruff，原122未影响复用不重跑；原E Task已retain，不用旧Dispatch写，源码/临时SQLite/内存vault/记录替身允许，无App/Pi/模型/Key/用户库/媒体/账号/GUI/常驻服务/安装/远端。交clean准确SHA/report到`/tmp/main-acceptance-20261009/review-v2-health-repair/report.md`。真实材料role剩余由当前C唯一production writer处理，E不能越界。UI会话/F表单/D旧档案夹具都独立继续。

### QA-V2-B-NARRATION-EDIT：局部重配音依原设备队列接续

B前preview已69a4f85 succeeded/retain源权结束，C当前唯一production.py；UI当前唯一main/schema/APItypes/Task，F只表单/AppShell。新B从精确7cdaa9c1948b991e2cda21a1569a4deb26eaed85 clone `/tmp/main-acceptance-20261009/review-v2-narration-edit/candidate`公开分支codex/review-v2-narration-edit-20261009。先REBASE_ACK当前架构3.12新增局部重配音unit规则/领域Script-Narration/版本依赖，不读全旧历史。实证main current RegenerateNarrationSegment确认仍503“尚未接入设备许可”，旧_regenerate...helper无repository/reader/grant参数不能用于范围声音；只保留明确503不是已通闭环。

阶段1只owned creation_execution.py现有creation_job Owner/executor对具名narration_edit单位的输入/准入/执行/终态检查点，及新tests/test_narration_edit_execution.py直接专项。与当前人物编辑成熟路径同一DeviceRuntime/scheduler/JobRun/TaskEvent，不能第二调度器/队列/事实表、借person_edit permit或自由运行生成。只有真实RegenerateNarrationSegment已应用意图/当前确认Script与segment稳定id/语音fixed版/原audio_reference/当前Timeline base/purpose限制/可信成员和workspace及校准NarrationBudget能产生单位。封输入digest含真实source hash/精确范围/rules版本/声音/model/语言/原段/时间线及资源事实；等待与准入重新核，不在admission decode/生成、不给未知资源静默默认。分类该当前任务局部预览，独立档案试听分类normal_make不改。

专属creation_narration资源的narration_edit:<digest>实际Grant上下文传唯一stage callback，现有cancel/timeout/子进程登记/unknown recovery保留；未许可零provider。queued/运行时不改现有timeline、声音或创建撤销版本，成功callback返回同现有person_edit deferred commit(conn)并在scheduler终态同事务校验当前版本→写唯一EditOperation/TimelineVersion及只受影响配音/字幕/镜头依赖→Job/Event；失败/取消保留原fact，不复用旧结果冒充新成功，unknown不释放许可。可以复用两条真实现有edit的checkpoint，不抽通用流程平台或与其他重活逻辑合并。重复同proposal/输入仅同Job/请求；失败显式新proposal重试，同旧单不能复活。

阶段1 main.py/narration.py/production.py/schemas.py/APItypes/Task都HOLD，不写或复制C/UI dirty，不注册可到达但无后端消费者的假成功按钮。可只读现实现/真实操作payload，接口补好和direct tests clean后仍本Task等待准确release，不以Phase1能力宣布局部重配音可用。阶段2在UI-CAS和C-N14 main单写结束后主控具名释放main.py旧_regenerate/helper/confirm/apply/runtimecallback及必要narration generator接线、APItypes/Task queued投影和直接tests，沿既有模型与repo/可信reader materialize精确范围/对应grant与有界combine，不更改不相关TaskScript/人物或放宽预算，退役旧503/无许可路径需同候选不存在证明。

本单只有源码与临时DB/可信合成Task/媒体来源/受控Provider与DeviceRuntime/资源替身专项许可：等待零执行、成功精确回读一次、重复/跨空间/错grant/旧时间线/声音撤销/失败取消/恢复未知不改旧版本、实际checkpoint绑定。sourcefixture是owned临时输入，不是真模型/已出片/GUI；不启动App/API/Pi/模型/真实Key/用户DB/媒体/账号/外发/收费/安装/远端。必要本单专项+Ruff，前音频/人员35/26/8及未影响checks不重跑。交clean精确SHA/report `/tmp/main-acceptance-20261009/review-v2-narration-edit/report.md`，源+接口+GUI未验分开，持续完成当前独立阶段。

### 当前来源修订事实与N14独立审阅

B阶段1输入按现有ProfileReferenceInput完整identity封登记来源与size/mtime_ns/ctime_ns/inode/device、固定档案版本/范围；该identity是事实摘要，不称文件字节SHA。已有持久素材hash仅在已存在且与同次登记修订一致时采用；未具备hash不编造，也不为普通保存新增全文件hash、解码或第二入库流程。当前阶段1不因缺hash停其它独立源码，阶段2仍使用相同Grant下materialize_reference的实际输入核验/派生与前后来源修订再核；与现有来源检查点的字段扩充只能由production.py唯一负责人具名接入，不由B越界。声音narration_edit专属许可的materialize入口由主控在C生产文件释放后再指定，不借person许可，也不声称当前完整范围生成已通。

C阶段1f76021d已worker_done/retain，阶段2需UI-CAS准确完成后新Dispatch显式释放；不向已结束Dispatch发送施工，不先组合仍有可选旧版本写接口的阶段1。其独立tag叶72341dd已组合4d4cee2，原失败及不丢标签专项2PASS；健康叶232c0b已组合2147b83，13PASS与Ruff复用，不关GUI问题。

E新QA-V2-E-REPLY-SCRIPT-REVIEW只读审阅C clean f76021d候选及main当前产品2.1/领域5与7.5/DESIGN2、实际main.py/Agent的update/confirm调用；唯一输出/tmp/main-acceptance-20261009/review-v2-reply-script-review/report.md，不改C产品或测试/共享文档/GitHub。重点真实会话来源还需HTTP校验、当前读者/空间/use_state/固定版本、现有事实依据确认规则是否被新reply旁路削弱、稳定请求重复与当前撤销/修订关系、阶段2必须退役的无条件写消费者，按具体证据给阻断/建议/已证实三类，不因未接Phase2就假报已完成产品故障。不运行测试/模型/App/用户库/Key/媒体/服务/GUI/远端，不复制dirty UI，C阶段2实施仍唯一C。先REBASE_ACK后定向读、报告完成结算；主控审阅决定是否授权修订。

### QA-V2-F-PREFLIGHT-RETURN-CONTINUITY与Web组合门禁

主控组合c121b329实际全Web一次：73files中68PASS/5FAIL，798cases中711PASS/87FAIL，日志/tmp/main-controller-20261009/review-v2/web-full-return.log。80素材、2首页、3AppShellMembersNav为当前会话能力/历史API mock缺新真实接口；1任务保存断言因新增对话status而选中多个元素；1成员Role用旧危险操作名称。UI现单追加唯一MaterialLibraryWorkspace.test.tsx、app/__tests__/page.test.tsx、CreationTaskWorkspace.test.tsx，沿当前真实会话/CASshape修fixture，保留原范围/读写/晚返回/原生保存边界，不改生产fallback或弱化断言。F新单唯一AppShellMembersNav.test.tsx/MembersPageRole.test.tsx，只补当前实际形状及准确控件名称，保留角色/所有者权限负例。

F前860a247已clean组合并retain。实际settings/page.tsx栏目Link丢return_to，sources/page.tsx保存后去knowledge亦丢；TemplateLibraryWorkspace实际creationHref开新的生产表单，修复途中换模板也会丢原目标。F新QA-V2-F-PREFLIGHT-RETURN-CONTINUITY从准确c121b329be886d0afe9bc2cadfd373c9fb3a5d47新隔离clone/tmp/main-acceptance-20261009/review-v2-preflight-return-continuity/candidate，唯一preflight-draft.ts、PreflightDraftReturn.tsx、CreateProductionTaskForm.tsx仅返回上下文/显式换模板、TemplateLibraryWorkspace.tsx仅真实使用模板动作接本草稿，以及这些files直接tests、前段两fixture。AppShell只必要返回组件参数，不写main/APItypes/Home/Task/material/knowledge/source/settings或globalCSS。

沿已有成员/空间sessionStorage草稿，只增加本次明确修复选择的草稿定位标记（scope+UUID+既有有界返回目标），不另建业务对象、通用导航拦截或逐页query补丁。资源allowlist内普通子导航后仍可回原草稿；显式非法return_to须报错不能回退旧标记，跨成员/空间不可见，相邻UUID不能串稿；成功创建/主动放弃只清自己当前定位，存储失败保留当前输入并如实提示。模板使用在当前修复上下文中是明确换所选固定模板的动作，更新同一草稿准确template id/version后返回；保留用户目标/显式30秒/声音人物pin，不因新模板默认值重置、不自动创建。无修复上下文时原模板新建行为不变。未知/失联草稿不得代造有效目标。

先REBASE_ACK后实施；最小实证settings栏目、source→knowledge内部导航仍返回、模板显式换版再返回30秒、旧/并行草稿/不同成员空间/非法URL/存储拒绝、原template使用入口保持。只受控组件/mock与既有公开cache专项/ESLint/类型，非owned旧TS207保持等待UI修复；真实GUI统一候选后验。无模型/Pi/App/API服务/Key/媒体/用户库/账号/安装/远端。报告/tmp/main-acceptance-20261009/review-v2-preflight-return-continuity/report.md，不能复用前85case声称新导航已验通。

### QA-V2-C-REPLY-SCRIPT-CONSUMERS：审阅整改先行，API写权等待

E只读审阅完成/report在/tmp/main-acceptance-20261009/review-v2-reply-script-review/report.md并retain。具体f76021d对source_reply_reference豁免confirm各段非空资料引用，直接test证明空证据也进narration；不予组合。领域7.5已明确：可以保存未完整来源的草稿，但既有确认条件不因reply来源豁免，原editor须能手工选择当前有效引用，不能自动填全库/猜引用或借证明门槛拦普通导入。

C新QA-V2-C-REPLY-SCRIPT-CONSUMERS在原可见终端从准确c121b329 clone新隔离/tmp/main-acceptance-20261009/review-v2-reply-script-consumers/candidate分支codex/review-v2-reply-script-consumers-20261009，再仅fetch/cherry自身f76021d，不拷UI dirty。先重读E审阅与领域7.5/原N14契约、发REBASE_ACK。当前仅production.py中N14仓储整改/资料引用与确认及草稿修订参数、原新tests/test_reply_script_transfer.py、tests/test_script_library.py必要N14相关fixture。仅这一文件owner C，并追加production.materialize_profile_reference voice expected_stages可接同creation_job真正narration_edit:专属许可，保留persona拒绝/工作区/真实grant检查；其最小新单独tests/test_narration_edit_reference_contract.py，不写B executor、D旧fixture或UI main/schema/APItypes。此许可叶单独clean提交可先root组合，N14正文仓储签名整改仍与未来消费者作为整体交付。

main/schema/assistant_pi.py/ScriptEditor/APItypes/Home/Panel/Task/knowledge及相关直接tests现在全部HOLD，UI-CAS唯一写者。完成当前独立Repo整改只发送阶段状态和准确SHA，不worker_done解除纵向任务；本新Task等主控准确UI组合后正式释放再完成N14阶段2。届时将所有活跃UI/Agent update/confirm改mandatory持久updated_at，在同原事务核验；不能保留可选令牌旧API旁路。新转入与重复回读均先核真实当前会话/成员/space/turn完成/当前正文及任务来源版本/use_state，旧引用不是事实依据。只有原editor真实手工补引用、当前来源重核、确认与现库保存/准确entry返回完整接线才源码交付，仍不自动关GUI验收。此处不是当前消费者写权。

只source/临时DB/合成文字与Grant替身直接测试/准确Ruff；没有服务/Pi/App/GUI/模型/Key/用户库/媒体/安装/远端。report/tmp/main-acceptance-20261009/review-v2-reply-script-consumers/report.md；避免对前14/44或独立声音26/8无影响测试重做。主控已完成及remote product c121b329、control a2b89c07回读，后续控制变更仍须新push。

### UI-CAS组合后文件释放与迟到读回修复

UI-CAS原Task已eb863f1 succeeded/retain，52API、182受影响Web+32会话Web、tsc及diffcheck成立，root机械组合准确2a1de3c8fa705b7c2bd07c345acf98ddb322d76e；完整CI/GUI仍未通过。C当前ctx_14f4fd2f3670 N14阶段2正式释放，从此准确组合fetch/rebase其clean仓储整改（只映射自身f760与40f10fa，不重复许可叶4e8已组合），唯一production.py N14、main.py只replyPlan/transfer/ScriptDraft/Confirm/Library相关、schemas相应payload/response、assistant_pi.py仅原script-draft写入持久版本消费、lib/api.ts/types对应、Home/AssistantConversationPanel/CreationTaskWorkspace/ScriptEditor必要实际移交/确认/资料选择/库返回、knowledge/page内实际KnowledgeWorkspace组件相关深链及局部CSS/directtests。F的AppShell/表单/模板/helper不写；B main/narration仍HOLD，D旧foundation/media fixtures不写；不改变已有材料/会话CAS/流式/许可/来源默认可用性规则。

所有活跃TaskScript draft-update/confirm UI与Agent写者强制持久updated_at原事务CAS，来源是当前可信conversation/完成turn/body重读，当前目标Task/Plan/Topic及原有效input references/use_state/reader先核。首次与重复回读访问校验保持，但不能以自己首次成功导致的updated_at变化拒绝合法幂等回读。回复缺来源可保存草稿，确认仍补原条件；原editor显示实际可选当前来源及画面建议输入，允许明确选择而非自动全选，跨任务/已确认版本/迟到目标变更不误写。完整真实Plan确认→Topic确认→已有ScriptDraft→确认→现有Library保存并定位准确entry/sourceTask源码链交付后才worker_done。API/UI直接mock/test与真实GUI区分。

UI新QA-V2-UI-DRAFT-READ-RACES从准确2a1de3c新clone/tmp/main-acceptance-20261009/review-v2-draft-read-races/candidate，唯一useAssistantConversation.ts和newtests/useAssistantConversationReadRaces.test.tsx（与C Panel/Home/Task直接测试不重写）。主控静态定位refresh只核streamRevision无draftRevision，迟到GET可把正在编辑的下一句换成旧serverdraft；failUnpersistedTurn恢复失败catch取catch时新revision再GET，仍可能把提交后较新本地输入换成旧稿；already_persisted分支二次GET后未再核当前scope/会话/草稿，success else还用await前current移除pending回写。先受控复现准确这些迟到交错，再沿原hook revision/scope/conversation fence修复同一类读回覆盖；不可新增状态框架/重试/fallback，服务器草稿CAS不放宽，当前input要保留且GET当前version/turn可正确合并。服务器409也应显示真实冲突不清新字。原stream/rename/resume/即时消息语义保持，不写main/schema/libAPI/types/其它消费者或globalCSS。

两单只源码/合成成员文字/临时DB/mock/缓存直接最小专项、正确Ruff/ESLint/类型，失败后必要修复复验可做，不重复无关52/182/32；无App/Pi/API服务/真实模型/Key/用户库/媒体/账号/GUI/安装/远端。UI报告/tmp/main-acceptance-20261009/review-v2-draft-read-races/report.md；C沿现report。root当前准确Ruff检查/全ESLint结果归相应唯一writer；此release不代表整体RETIRED或问题关闭。

当前root2a1de3c实际Ruff仅tests/test_assistant_pi.py:739未用conversation_id（不是main未用变量，dispatch消息误称已更正），C阶段2追加该直接test file及test_assistant_script.py为N14/Agent真实draft CAS唯一测试owner，原身份/工具/路由断言保持；这两file不由新UI hook任务修改。组合全ESLint实际0errors/53warnings，日志/tmp/main-controller-20261009/review-v2/cas-eslint.log，不冒称无warnings。

### 预检返回组合后的实际消费者释放与检查残差

F返回连续性7610c905已审并机械组合dc4bed2，原Dispatch succeeded/retain，59受控案例通过；真实settings/source导航与GUI未验。C当前N14 ctx_14f4fd2f3670正式追加CreateProductionTaskForm.tsx、production/page.tsx、preflight-draft.ts中必要reply_origin字段/实际消费者及其直接测试：fetch准确dc4bed2，仅组合F净差额，不丢自身N14整改。原表单确认必须传真实conversation_id/turn_id/稳定request_id，由服务端再读可信完成轮次；只存定位不存重复正文/凭据。临时修复草稿按当前成员空间保存此来源，修复返回及显式模板换版保持，普通没有来源的创建维持原行为。不另造Plan表单、跳过预检/Topic/文案资料确认，也不改F marker/模板资格规则。C需REBASE_ACK后继续纵向交付。

root b9ddbd8完整tsc唯一错误是AppShell.test.tsx:38的既有conversation fixture缺mandatory draft_version（日志combined-tsc.log）；F新QA-V2-F-APPSHELL-TYPE仅这个测试文件及其组合后显露的AppShellMembersNav.test.tsx:25同形状fixture，补当前真实字段并准确文件专项，不写AppShell/其他产品/会话hook。F实际24PASS后dc4bed2完整tsc揭示后者，须保留角色/等待/身份拒绝断言；不由其他写者并行改这两文件。UI迟到读取0ee1593已组合b9ddbd8并retain，其5新负例/23相关受控PASS不重做。

D旧profile迁移0cb7b000为测试源码交付，不是CI通过：整指定集379PASS/31FAIL/零setup errors，随后仅模板一个fixture修正PASS。D新QA-V2-D-PROFILE-RESIDUAL保留原具名测试所有权，在原clean candidate新公开分支，定向fetch主控准确dc4bed2后机械组合避免丢已有fixtures；只修当前残差中确实过期的fixture/调用与已退役前提，不重测379未影响案例。领域独立人物固定版本/0%无人物、真正文件身份/许可/确认摘要均按当前源核。授权撤销/来源失联/真实执行失败负例保留，不用额外许可/任意私有可见/宽松assert绕过；生产缺口必须精确交主控，不写生产。报告每类根因、case、最小复现以及仍失败项，clean提交只含具名测试。

E新QA-V2-E-SEARCH-RESIDUAL仅只读实际dc4bed2源码和D准确0cb7b000候选/失败日志；在自有/tmp隔离控制复现四个montage replacement_clip_unavailable及person缓存来源身份残差，确认是fixture失配、召回权限/语义还是实际漏接独立人物。不得写产品/测试仓库文件，不重复全仓，不改D原case、不赋所有私有或诊断权限。临时合成媒体/TestClient/mock许可允许，模型/Pi/App服务/GUI/Key/用户数据/账户/安装/远端仍不允许；交准确根因和必要源writer差额，不冒称已修。

UI新QA-V2-UI-MATERIAL-TURN-FAILURE只读root准确dc4bed2 main.py中_material_assistant_turn真实工具返回后的对象范围/schema校验分支：当前try仅覆盖run_material_turn，后续HTTPException/验证拒绝不一定finish_turn。先在自有/tmp控制复现，无写main/C测试权；若成立只写一个新增tests/test_material_turn_failure.py隔离HTTP负例，准确被拒轮次持久状态/后续一次发送与草稿版本，不弱化结果范围校验。main当前C唯一owner，源修复提案先交主控串行转交，不能新增第二handler/宽泛catch。可信临时成员/库/合成对象和Pi工具替身允许，不真Pi/模型/App服务/GUI/用户数据/Key/媒体/远端；准确失败证据与最小修正建议，不把推测标已证实。

C当前N14 main.py唯一写者追加两个已证实边界叶，不扩整file行为：E search-residual/report.md证实_preview_creation_task_edit候选阶段传reader而第二search_material_segments漏同一可信viewer/local_diagnostic，私有户外段被错滤；只透传同一真实_content_reader，保留实际用途/范围/自动资格/身份过滤，不改全库可见性。D原四个replacement用例保持原断言，另必要跨成员不可见负例。UI msg_f4500fdfb7dd证实素材回读scope拒绝503/schema验证400两种轮次持久running且新请求409；严格结果对象/schema拒绝仍保留，明确处理这些真实结果边界拒绝为failed/安全原因、释放控制，不吞异常/宽泛fallback，也不能恢复已被用户更新的新草稿。UI受控探针16529f5已交并retain，现仅作为Bug复现证据，不组合其期望running/409的断言。test_material_turn_failure.py正式释放给C唯一写者，机械取探针后把最终回归断言改为：仍拒绝非法结果、轮次failed且有安全原因、同请求回读failed零重复工具、生成期间新草稿保留、下一轮合法要求真实可继续；先证原基线红后最小源修绿，不冻结Bug。两个源码叶可先单独公开clean提交供组合，不提前worker_done结束N14纵向Task。若N14后端API/仓储源已完成，可交准确后端叶与接口及RELEASE_ACK main/schema给主控，再由B串行接配音API、C继续Web；不得自行两线程同时写同一file。

E新QA-V2-E-MATERIAL-QUALIFICATION-AUDIT只读root准确fe5ad62的实际任务候选/InsertClip/ReplaceClip/确认消费者（main.py约7260/10162/11162/11790及原legacy候选14000），与production.automatic_segment_eligible、manual use_state/实际来源身份规则核对。root静态已见候选/插入/替换仍用review_status!=approved硬门槛而当前正式自动资格只拒明确rejected；候选与写入必须使用同一真实资格，不能只删修复文字。先定向控制复现：已分析/可读/可创作但旧raw审核字段、主动not_for_automation手动与自动、reference_only/excluded/rejected/失联、另成员私有ID请求预览与确认是否真正拒绝。只自有/tmp诊断与临时合成DB/TestClient/许可工具，不改产品/仓库测试或D fixture；按实际调用确认活跃/退役来源，不默改所有legacy分支。交准确断点/最小同类迁移范围与不存在证明要求，主控再交当前main/production唯一writer；不能预先称未复現安全缺陷成立。没有真实服务/模型/Pi/GUI/Key/用户素材/远端权。

D残差5cbc209已审，与原0cb7b000两测试leaf机械组合准确5bd870d；root固定Ruff0.15.7对12具名测试/helper PASS，不存在安装工具阻断。D已retain，所写测试源权结束。领域4.2按F01明确“参考可先登记，逐字文字可后补，真实引擎缺文字时阻断生成零模型调用”；当前VersionedVoice schema默认空、People“生成前填写”和VoicePreviewService.reference_text_missing已一致，旧required-field测试不是生产保存Bug。

D新QA-V2-D-VOICE-TEXT-REGISTRATION基于准确5bd870d新隔离公开候选，仅tests/test_media_profile_api.py、一个新增tests/test_voice_reference_text_contract.py：把旧负例reference_text空改为当前真正必填display_name空，保留原其余必填/成员/来源校验；新增真实临时成员/有效合成WAV登记省略/空白文字可保存与跨入口回读，不宣布克隆。直接真实VoicePreviewService+可信仓储/许可替身证明缺逐字文字blocked reference_text_missing、零provider且原档案保留；不改产品/main/schema/Narration/People，源条件未满足仍准确失败，不改成any-status或绕reader。必要这几个案例与精准Ruff即可，前受影响23/47/379不重跑。

F新QA-V2-F-QUEUE-IO-WINDOW基于同一5bd870d新隔离公开候选，仅tests/test_creation_execution.py中test_confirmed_script_only_runs_all_queue_stages_and_restores_original_account的3个当前残差None/render_revoked/render_commit_unknown与其共用局部fixture。核实际HostResourceProbe：当前cpu_percent替身立即返回0使interval被忽略，样本只有0.0002s且首次pick期待即时grant；该测试必须沿真实排队/观察窗口等待，不改生产io_window_seconds、limit、判定、实际SQL/进程/Grant或许可负例，不给resources.admit=True。可让既有CPU负载替身尊重实际interval，并使用有界的当前请求pick/等待形成真实3秒证据；unknown/revoked/cancelled不能被等待循环掩盖、不能重复执行provider。先最小残差/准确Ruff，其他未变PASS复用；若真实运行时漏接观察/唤醒须原失败与精确source交主控，不靠伪造时间硬绿。两个新单无App服务/Pi/模型/GUI/用户数据/Key/原媒体/安装/远端权限，仅临时真实ffmpeg/合成worker/临时库。

C N14真实Agent更新版本消费追加仅apps/api/agent_tools/diary_tools.ts中get_current_task取得当前draft.updated_at及create_script_draft实际提交/读回此token，与assistant_pi/API仓储沿同一CAS；不是扩任意工具/文件系统权限。必要既有直接extension验证，未读当前任务/过期token仍真实拒绝，首次没有草稿的初始化与现存草稿更新分开，不保留旧现存draft无条件写路径。新领域4.2参考文字后补只明确现有登记/生成边界，不改变C N14接口。

E前次qualification审阅静态证据可用，但uv run误触自动依赖准备，已停且仅新忽略venv移入owned报告保留，HTTP零结果不可声明PASS。E新QA-V2-E-MATERIAL-QUALIFICATION-REPRO仅自有/tmp新快照，使用已安装绝对Python /tmp/manta-wil108-20261007/venv/bin/python，禁止uv run/install/自动建环境、写公共candidate ignored目录或处理原保留venv；复用准确5bd870d的受控D合成fixture与临时HOME/XDG/DB，仅前段实际raw有效门槛/另成员private已知ID的少数HTTP预览及确认诊断。源/tests仓库只读，输出只精确诊断report/临时脚本，余矩阵保留未验；若现阶段确认需真实模型/媒体则停该项，不借评审扩许可。现有来源/权限真实现象足够后停止扩大扫描。

### N14后端接管与当前素材资格纵向迁移

C后端62b38e4已审，与自身ed39761/14cb462按叶组合准确f706d36f9ed4094a3170970e09f8aec16ceaacf0。60个受控API专项/Ruff证据成立；最终CI/GUI/全部来源不关闭，C仍继续N14 Web/报告。C msg_81b7e3af31d7已RELEASE_ACK main/schema，现正式给B同Task ctx_5948cb0be957：main.py中RegenerateNarrationSegment实际预览/confirm/runtime callback/受许可生成/queued事实，以及schema仅该现有编辑/Job响应，不改已组合N14/CAS/成员/资料接口；narration.py中原生成器必要grant/repo/reader/明确区间接线，现有creation_execution.py本单专属单元可必要调整；对应一个tests/test_narration_edit_api.py及已有test_narration_edit_execution.py直接专项。Web/APItypes/Task仍C唯一，不写，后续再释放。局部重做排队时旧audio/Timeline/EditOperation保留；专属Grant成功在同事务发布真实音频/retiming/EditOperation与Job终态，失败/取消不发布、unknown不释放；不借person permit或平行线程/队列，后台完成不是用户已验收。不复验17未影响阶段1；真实模型/媒体/App/GUI/Key运行仍未授权。

C同时保留production.py仅update_assistant_script_draft/confirm_creation_task_script仓储CAS和其当前N14直接测试：root审阅见这两方法仍expected_updated_at=None且仅非None时校验，违反领域7.5的实际写事务mandatory版本。去掉无条件现存draft update/confirm分支，在原事务强制当前token，首次草稿创建不混作更新；API已接token不需重写main。只精准Repo/当前N14测试修订与必要负例，旧不带token测试夹具归主控下一波具名迁移，不开放兼容fallback。完成后提交净叶并RELEASE_ACK production.py；B在此之前不得写production.py，C从此不得再写main/schema。

E资格HTTP诊断已实证：ready/available且旧raw审核字段导致picker为空、Insert/Replace预览409；另成员private已知ID的preview+confirm均200并实际改Timeline，原任务副本出现越权引用。源报告/tmp/main-acceptance-20261009/review-v2-material-qualification-audit/reproduction-report.md为临时合成证据，不涉及用户资料。B当前main拥有权追加纵向资格迁移，优先此已证实对象边界，再局部重做API；production.py共享手工/自动资格helper待C释放后才写。scope为活跃_creation_task_montage_candidates、_creation_task_insert_clip、_apply_creation_task_montage_clip_update及其所有preview/confirm调用；复用既有真实reader/private-owner/grant判据，每次读取与确认重新校验当前访问，不把候选过滤当写权限。手工和自动必须沿同一实际媒体/状态/范围/文件身份基础资格，手工可用not_for_automation，自动仍拒绝，明确rejected/reference_only/excluded/失联/越界/改变文件保持拒绝。退役所有活跃CreationTask旧approved硬门槛/无reader对象读取与误导审核修复文案；legacy已410写路与无活跃Web读路不顺手改造。一个新增tests/test_material_edit_qualification.py正/负例与D四replacement准确复验，保留真实媒体有效性，不全库include_private或诊断兜底、不深解码绕Grant。交新结果、RETIRED/调用不存在证明、精确来源与未验；不能据此发布或声称真实客户端通过。

### QA-V2-C-SCRIPT-CAS-FINAL：仓储版本门槛收尾与释放

C消费者Task ctx_14f4fd2f3670已交4e4f0a6并retain；D声音文字登记23dc2e4与F真实IO夹具f585cf3也已审，机械候选d83fa80f7561ed3d3e5bcf235c332245837bd314。声音7项通过，IO三个仍失败，不据此关闭Issue或称GUI通过。C旧派工不恢复，原可见C接此具名新单，只独占production.py的update_assistant_script_draft/confirm_creation_task_script及一个新直接tests/test_script_write_cas_contract.py；按上一节实际事务强制版本，首次创建不混作更新，旧无token消费者不得保留兼容后门。从准确d83fa80新隔离候选、公开Git身份，重读领域7.5与本合同，语义ACK后实施。精准仓储缺失/过期/正确token与确认门槛测试、Ruff/diff，提交clean叶并RELEASE_ACK production.py后结束；不写main/schema/Web、既有旧fixture、B执行器或共享文档。服务/Pi/模型/用户数据/GUI/账号/远端写权仍无。

C Web/APItypes/Task写权随旧Task结束正式释放，主控尚未转授其他写者；版本残差按当前准确候选复现后具名处理。B仍独占main/schema/narration/creation_execution，本单production仍C，须等待正式释放。F实证montage waiting_for_user已保存却被执行器记失败：B追加同一现有执行器阶段交接的诊断与最小修正，合法等待保持用户确认，不能把waiting归ready或自动render/降低阈值；复用已有Job/Task/调度终态与继续入口，不造第二状态系统。先报具体可用等待/继续协议和受影响文件，仍不写C production。F已交tests/test_creation_execution.py写权结束，后续成功输入夹具与等待回归须新单，不能硬改期望使低置信镜头自动使用。

### QA-V2-UI-REPLY-NAV-RESIDUAL：组合回归与准确出口

组合a92a0e7146fafe63324b91dd22db6f1dffc6c622的全Web 838 PASS/2 FAIL，tsc通过；需保留原意修两个残差，不用跳过或宽断言。UI原可见终端新单，唯一owned useAssistantConversation.ts、HomeCommandComposer.test.tsx及直接read-race专项、app/production/__tests__/preflight-return.test.tsx；查已消费轮次切任务后draft_version意外从2升3实际来源，源缺陷则最小修原hook，若夹具错误则真实请求版本动态绑定且保留不恢复/不覆盖/晚到隔离断言；预检旧夹具需断言replyOrigin=null符合新增无来源路径。另主控实际读到KnowledgeWorkspace默认collection=sources、ScriptLibraryPanel仅hidden子容器内读取entry_id，故新文案深链不自动打开可见文案集合；UI追加唯一owned app/knowledge/KnowledgeWorkspace.tsx及其直接专项，当前明确entry_id/source_task_id深链选择正确集合与条目，不存在或来源不一致不打开别的对象。C旧Web权已结束；不得改API/types/main/production/B/共享CSS或另造全局导航状态。从准确a92新隔离公开候选，重读DESIGN2/5.1/领域7.5，语义ACK后实施。现成依赖最小受控组件/tsc/Ruff不适用；不重复838未影响测试，不运行服务/Pi/用户资料/GUI/媒体/远端。交clean叶与报告、来源残余和未验，主控统一候选后截图。

## 已结案波次：B92组合修复实际成片补验（2026-10-09）

### B91：冻结许可子进程入口修复（已succeeded结案，源码写权结束）

B89实际1767e11包录音save/adopt和许可故事板V1成立，首次渲染失败、无当前MP4。现有granted_subprocess.py使用sys.executable -c trampoline；冻结sys.executable是api-server，server.py却无该入口分派而直接加载API启动恢复。实际render和外层Job同时process_interrupted、driver checkpoint失败及resume503与此源码机制相符；子进程完整argv未捕获，首因结论保留推断，修后必须实际冻结验证。初次I/O压力是另一同次真实事件，不扩大资源阈值或重写恢复扫描。

B89完成failed结算及物理释放后复用原B可见终端，从clean1767e11建立codex/frozen-grant91-20261009。唯一产品写权apps/api/app/granted_subprocess.py、apps/api/server.py，必要既有apps/api/tests/test_host_resource_probes.py及一个server入口直接测试；不写production.py/migrations（UI90独占）、device_runtime/creation_execution/恢复规则、资源cap、main.py、Web/桌面/依赖/共享文档。沿现有注册子进程→核Grant→许可字节→exec的门禁，给冻结包一个明确的许可子进程入口，在dotenv/API/uvicorn/恢复扫描加载之前分派；不要给服务新增任意Python -c执行入口，不移除子进程身份、取消/超时/组退出校验，也不新增并行调度系统。开发路径保持可用；无需本机或全局Python解释器假设。

仅owned /tmp/main-acceptance-20261009/frozen91/隔离DB/tmp做最小入口与真实POSIX门禁测试：许可登记前不执行目标、合法许可后执行、拒绝/取消不执行、子组释放，冻结入口不导入API或调用恢复；模拟冻结测试不得冒称真实打包验证。无打包/服务/GUI/媒体/模型/Pi/Key/平台/下载权限。交clean候选、净diff、专项及未验；主控机械集成UI90/B91后另具名一次必要API增量入包与实际成片补验，不重做未变Web/视觉矩阵。

### 当前目标补充：素材原子操作与飞书入口（2026-10-09）

用户最新指出不止流式四项：选中素材后一句话改标签，右侧实际变更且刷新回读；随后明确飞书仅举例、本人创建应用、本期不做。产品§1.1明确飞书后置，DESIGN§2保留素材对象对话，架构§9只保留未来消息接入方向；auth既有外部身份规则不变。不派飞书源码、配置、凭据或运行工作，不列为本期缺口。UI102仍完成流式/输入/首次开始scope断点，B104实施素材工具；两者写权互斥，随后由同一UI线程接通素材选择/真实写入/工作台与刷新回读。不从本段扩大代码/云/外发权限。

### UI105：素材选择到对话标签写入与右侧回读（已succeeded源码结案，写权结束）

UI102已交clean候选5b2d940d、B104已交clean候选4f1a6503，主控审核并仅在候选集成分支机械组合为a2752976a2293a4dce07bb072fea08b2c00357f9；源码检查不等于真实Pi或GUI通过。两项原写权结束，B保留原脏树与终端。UI105复用同一UI终端，从准确a2752976本地fetch并新建codex/material105-chat-20261009，保护既有成果；不merge main或旧混合分支。

纵向结果：用户明确选择整条素材或片段，在中栏说“把这个素材的我的标签改成XXX”，同一Pi运行入口调用B104具名工具，真实业务PUT后GET回读，右侧展示同一对象标签，正常刷新仍读取保存值。素材与片段分别绑定，不把自动预选首片段当用户指定范围；整条标签也必须在右侧有实际可读入口。无选择或多选不猜对象，不将其转成新建任务/剪辑提案/二次确认。对话范围与结果只绑定受当前可信成员/工作区权限核实的对象，不接受模型自定owner/workspace/路径。复用既有Pi会话、_run_turn、SSE投影、业务request与MaterialLibrary的replaceAsset/refreshInsights；选择切换后旧回包不覆盖新选择。普通标签写入没有产品收费或多余确认；系统标签与片段其他字段仍依B104现有命令保留。

唯一产品写者UI：assistant_pi.py、agent_tools/diary_tools.ts注册与B104接入、main.py素材助手直接入口/授权/结果投影、schemas.py直接素材请求响应；HomeCommandComposer.tsx、MaterialLibraryWorkspace.tsx、lib/api.ts、lib/types.ts及素材/聊天局部样式，最小相关既有测试。B104模块只在真实接线契约缺口时定向修改并说明；不得写AppShell布局、CreationTaskWorkspace无关消费者、领域/DB迁移/Grant/cap/平台/桌面/依赖/共享文档/GitHub/Linear，不实现飞书。所有源码以准确组合基线为准，不回退已交流式和输入行为。

owned /tmp/main-acceptance-20261009/material105/，最小检查证明可信所选范围、工具真实响应事实投影、组件对应标签刷新和无选择/切换不串对象；允许隔离TestClient/测试库验证现有真实仓储写后GET持久化，不启动网络服务/Pi/Key/GUI/媒体/模型/打包/下载，不读取用户试用数据。提交clean候选、净diff、实际检查、未验证项与后续实际Pi/客户端入口；源级用例不能冒称真实GUI。读取main产品§1.1、DESIGN§2、架构§9与auth成员边界后REBASE_ACK即可施工；最终结案后再具名一次合并实包验收，不重复未受影响的视觉/媒体测试。

### UI109：确认文案后的任务对话测试契约更新（MAIN-SCRIPT109-20261009）

PR102后端CI唯一失败test_assistant_script.py::test_script_draft_stays_unconfirmed_and_assistant_history_is_persisted：旧用例在确认文案后断言六个context_stage一律409；UI102已按用户核心聊天要求开放当前narration任务范围无选中对象的只读/开始对话，不能再全禁。旧mock始终回script_id导致该允许轮被当编辑提案验证而失败；前面的文案草稿、未确认、来源、历史及Key拒绝已通过。不是给生产新增兜底或恢复旧禁令。

复用UI原终端，基于准确clean eea5f306建立codex/script109-test-contract-20261009。唯一写权apps/api/tests/test_assistant_script.py，保持确认文案后禁止script-draft写入与script/topic/不匹配未来阶段拒绝，确认文案ID不变；给新允许的当前narration无对象对话一个合法只读无提案替身，验证200、不返回新script_draft/编辑提案、正确新增两条对话历史。不得靠删除断言掩盖既有不变量；仅调整已批准开始对话对应旧断言，不改产品/类型/依赖、无服务/Pi/GUI/打包。跑此已有测试文件及diff-check，owned /tmp/main-acceptance-20261009/script109/交clean候选/净diff/实际检查；缺口若并非测试旧契约，报告主控而不扩大产品写权。B108运行包冻结不动，根机械集成且证明运行产品blob相等，不重建。

### B108：新包实际API/Pi素材原子验证（MAIN-MATERIAL108-20261009）

结案：task_6b2bd981a37c / ctx_12d7b1c70be7 succeeded，报告/tmp/main-acceptance-20261009/material108/report.md；实际包Pi单请求4.21秒，两个工具开始/完成、2文字增量、最终result，真实PUT后GET、独立GET及停机只读库同值；仅user_tags/updated_at改变。原数据/包/脏树保留，自有40320/40389与Pi组退出、18108空闲；根26文件已知Key扫描0命中、临时keychain已锁。运行权结束，以下条款仅保留证据边界，不能复活START。根已打开并保留用户Downloads新版；其核心GUI、完整创作/重出/保存仍未验，隔离导入分析因PATH缺ffprobe失败。

B106包已审核，7aa1eedd源码与UI107最终eea5f306的运行产品blob完全相等，仅测试替身差额；不重建。用户已批准退出旧试用版，根正常退出旧45104与Next45400后打开Downloads新对话包31698，原userData保留；该用户包只观察，不由B访问其DB/配置/Key。原生工具可截图，但restore后的点击被window_not_focused拒绝，GUI操作和刷新未验，不换DOM/CDP绕过、不继续要求用户人工键盘。

复用B原可见终端，保护pipefish四文件脏树，无产品/共享/远端写权。仅owned /tmp/main-acceptance-20261009/material108/新DB/config/library/tmp/output/agent/log；读取B106实际包api-server及agent-tools只读，API_HOST=127.0.0.1、API_PORT=18108，端口先检查且只停自有新PID/PGID。AUTH_MODE=local、空云配置、scheduled OFF；通过现有whoami建立真实本地成员，不seed已完成任务、复制DB/设备锁或读取用户试用库。只用已授权5秒本人视频/tmp/main-acceptance-20261009/creation92/library/user-video.mp4，复制到新库后通过实际导入接口导入。允许这条既有导入链内的FFprobe元数据、5秒FFmpeg拆分与空模型目录的缺失能力探测；MODEL_ROOT必须指向本单空目录，模型Python路径不指向外接运行环境，不加载或启动本地模型，不跑配音/口型/渲染。不得为了避免分析新增开关、patch或seed素材。原包核心业务接口/实际Pi1.1.0与Node24.16.0，国内MiniMax-M3；最多两个短文字请求，每次180秒，媒体不发送。

先启动实际包API并核健康、同一可信成员与workspace、真实导入素材；发送准备回执并等待root SECRET_READY。根独占已授权named minimax-validation-20261007.llm_api_key的只读及RAM处理，在本单临时原生keychain给实际workspace写具名测试密钥并回读，不向B/聊天/文件/日志输出Key；系统原default/search-list保持。B不能读文档/Keychain或secret文件，仅API按既有SecureStore使用；不用LLM_API_KEY明文env。

单一结果：向现有素材assistant入口明确选择新素材并说“把这个素材的我的标签改成产品演示”，使用真实Pi工具，经现有PUT后GET保存；记录真实SSE文字/工具生命周期顺序、最终material_result、后续独立GET同值与其他字段保持。首个断点即报告真实错误，不伪造Pi结果、不自动重复写、不扩测试矩阵；仅若首请求通过可一次只读澄清/回读。HTTP/SSE/Pi通过不代表用户GUI已通过；不碰真实平台/发布、删除、媒体模型、用户数据。交实际包身份、准确输入/响应摘要、持久化证据、未验及所有自有服务/Pi组释放，根随后锁临时keychain。最长本单运行15分钟，完成即关闭自有API，不留用户包端口占用。

### B106：核心对话组合候选一次本机ARM64测试包（MAIN-CHATPACKAGE106-20261009）

本Dispatch PACKAGE_START准确冻结7aa1eedd7480b45e09561860485eb6a23384005f，源Git为/tmp/main-controller-20261008/ui-v3-integration；UI105已审核并机械ff，根合同不因此取得产品代码写权。

复用保留B终端，原pipefish/platform99脏树原样保护，无产品代码写权。owned仅新/tmp/main-acceptance-20261009/chat106/；依B101已交准备复用B97公开构建依赖/缓存和现有打包流程，不复读或复制私密env/Key、用户DB/设备锁、平台profile或模型。UI105施工期间只准备新的隔离构建目录和非秘密依赖映射；尚无最终候选，不构建。主控审核组合UI105并具名PACKAGE_START准确SHA后，在本新根archive该候选并核源码身份，构建一次该候选Next standalone、PyInstaller API与ARM64 ElectronBuilder目录.app和生成身份；不复用旧Web/API二进制冒称新候选，不运行build.sh的安装步骤，不下载/安装依赖或生成DMG/ZIP/签名/Release。既有依赖不足报真实缺口。

一次最终同候选仅必需集成/打包检查，不重复未变组件/媒体/宽度矩阵。交实际.app路径、准确源码SHA/candidateInputDigest、打包资源与工具TS入口实际包含、必要结果、所有自有构建进程退出与未验范围。无运行Electron/API/Web/Pi/模型/媒体/GUI/账号/Keychain权限，用户当前旧测试包不得退出或改写；真实聊天实包另具名接续。主控最终负责新包分享副本与用户配置隔离，包不含用户Key、模型、素材或测试DB。任务等待只通过本终端收件箱（check不带--run），不恢复97/101旧START。

### UI107：现有语音证据测试素材字段补齐（MAIN-FIXTURE107-20261009）

PR102前端CI唯一失败为MaterialSpeechEvidence.test.tsx的asset替身缺必带user_tags，右侧真实整条标签读取.length报错；真实MaterialAsset/API契约已经必带该数组。复用保留UI终端，基于clean7aa1eedd新建codex/material107-test-fixture-20261009。唯一写权apps/web/src/components/__tests__/MaterialSpeechEvidence.test.tsx，只补这个已有asset替身的user_tags:[]，不改断言/产品组件/API/类型/依赖，不为残缺测试数据增加生产回退。仅运行该文件1项检查和diff-check，owned /tmp/main-acceptance-20261009/fixture107/交clean候选、最小差额与结果。无服务/模型/Pi/GUI/构建/外部写权；B106已冻结包不改变、不重建，后续根核运行blob相等再更新PR。

### UI102：对话真实流式、工具过程与输入修复（已succeeded结案，写权结束）

用户继续实施，并指出无流式、工具执行不可见、空格误发送及核心Pi剪辑闭环未通。UI100准确clean4f4f1563e3beb46eb17d6048e547de8760c11c80已交付28/103专项与tsc、未跑真实Pi/GUI，源码保留而非验收通过；100旧权限结束。复用原UI终端，从该准确候选建立codex/chat102-stream-20261009；先重读当前main DESIGN§2新增对话条款、架构§2/§9、auth目标成员与任务边界，语义REBASE_ACK后直接实施。

单一纵向目标：现有首页与任务/剪辑对话真实增量文字＋真实工具开始/结束/失败过程，工具结果驱动原有提案、阶段/作业回读；空格不发送，Enter/Shift+Enter与中文组合输入正确、发送后输入焦点恢复。沿同一Pi JSON事件与原有assistant入口增加流式响应协商/客户端消费，复用既有业务工具与最终结果校验，不建第二套Agent/会话/调度/状态库。不得模拟打字、转发thinking或原始工具args/result、凭据/环境/路径；必要工具摘要仅当前对象真实结果。现有业务工具默认保留，不能解禁任意read/write/bash绕过草稿/版本/权限。当前精调对话保留选中引用与播放位置；不为流式退回首页空上下文。正常调用结束、失败、断开按真实结果处理；停止聊天不等于取消已经排队制作。禁止自动重试会重复写入的命令。

Owned源码：assistant_pi.py、agent_tools/diary_tools.ts、main.py两个直接assistant入口/结果投影、schemas.py直接assistant响应/事件类型；Web HomeCommandComposer.tsx、CreationTaskWorkspace.tsx、lib/api.ts、lib/types.ts，若真实代理缓冲阻断则middleware.ts仅流式透传；必要聊天局部样式与直接既有assistant/Web测试。不得写领域/持久schema/平台/Grant/cap/桌面/依赖/共享权威/GitHub/Linear；旧global CSS不要改布局或其他消费者。owned /tmp/main-acceptance-20261009/chat102/仅最小证据。直接流式事件/真实业务结果错误分支、两种对话输入与中文组成最小检查；不开模型/Key/Pi/服务/GUI/构建/下载。交clean准确候选、净diff、实际展示与缺口；仅源码检查不能称软件闭环通过。

### B104：选中素材标签工具差额（MAIN-MATERIAL104-20261009）

B103已交准确4f工具白名单/编译命令链与首次开始scope断点；主控已读并交UI102定向修正。B103未收进最后的素材/飞书补充，不能将核验表当全系统覆盖。本任务复用B同一终端，保护pipefish的platform99四文件脏树。只从UI仓库准确4f4f1563本地git clone --shared --no-checkout到owned /tmp/main-acceptance-20261009/material104/candidate，建立具名codex/material104-tools-20261009，不复制ignored/依赖/私密配置，不改原树。主控仍负责最终对象对话纵向集成。

B唯一产品源码写权：新增apps/api/agent_tools/material_tools.ts及一个直接tests/test_assistant_material_tools.py，不编辑diary_tools.ts/assistant_pi/main.py/schemas/Web/依赖/领域/持久化/平台/资源。新模块是本次实际素材助手工具，不建通用Agent框架；与已有扩展注册一次连接由后续UI负责，在此单未连接不得称标签聊天通过。向registerMaterialTools传现有业务request回调及服务器核实的选中asset/segment上下文；模型不接受任意ID/owner/workspace/路径。工具先读选中真实素材，仅显式改“我的标签”，系统标签不改；根据明确替换/添加/移除调用已存在asset tags或segment update命令，保留其他字段，返回同一对象的持久结果和真实变化。不将改标签作为剪辑提案/创建任务；对象不存在/无选择/权限/写失败说明原因，不假成功。区分素材级与片段级并给清晰工具描述；多片段不能猜作用范围。

按main产品§1.1/DESIGN§2/架构§9与auth成员边界，以及准确现有请求/响应/schema实施。最小直接测试在owned隔离环境核绑定路由/标签写回读/不改其他字段及失败不成功；测试替身不是持久GUI验收。只允已有Node/Python直接工具测试，owned依赖缓存可链接公开既有依赖；不开Pi/Key/服务/GUI/模型/媒体/账号/云/下载/构建，不读Pi会话。交clean准确候选+范围净diff、接入函数签名、已准备/未接线与后续实际验证要求，报告material104/report.md。后续UI102写权结束后主控指定同一UI线程接入全链，不发生同时写同一文件。

### B103：Pi工具与创作原子能力覆盖只读核验（已succeeded结案，只读权结束）

复用B原终端，保护platform99四文件脏树，不切换/提交。只读UI仓库准确4f4f1563、当前main架构§2/§9及现有编辑操作消费者链；UI102工作中必须git show固定候选，不能读取混合未提交差额。区分Pi已装内置能力、命令行实际禁用/开放工具、客户端已有但Pi未暴露工具。补核MaterialLibraryWorkspace/AppShell的素材选择→对话上下文、现有asset/segment标签API→回读；飞书仅核已有入口/模块，不读私密配置或云操作。定向核已安装Pi1.1.0公开源码事件格式（文字delta、tool execution start/end）；不运行Pi、不读会话/模型私密配置。

交一张本轮短表：创建方案/任务、选题、文案、读取任务资料素材、修改镜头/比例/字幕/声音、提案应用/撤销/重做、开始/继续/取消、重出/保存，每项准确工具/启用阶段/真实API或草稿命令/客户端结果/证据等级与首个断点。核心追踪一句“把当前镜头调亮一点，保持声音不变”如何得到真实提案、应用版本、重出输入；不能只因注册了工具说能力通，也不能只因禁用文件工具说没有Agent。识别实际断点即可，不广扫或另造平台/框架/长期完成度文档。owned /tmp/main-acceptance-20261009/atoms103/报告与最小准备，不编辑产品/文档/工作树、不开服务/GUI/模型/媒体/账号/Key、不构建/测试/下载。后续实包只等root组合准确候选具名新运行权，不从101或97旧START恢复。

### B101：一次核心对话实包验证的准备（已succeeded结案，准备权结束）

复用B保留终端，仅做准备、不测试；保护99脏树，不切分支、不提交未测平台差额。owned /tmp/main-acceptance-20261009/chat101/0700只写最小启动/验证准备材料，无产品代码权。准确源仍4c，UI100新差额未交不构建。只读核B97实际.app、B92已停止隔离fixture/合法LocalMember/DeviceRuntime原锁、现有5秒本人音视频与B97已验证输出，确定可复用输入与先前已通过步骤；不得复制锁迁移身份、seed任务/旁白/故事板/输出成功状态。原92测试DB仅只读快照/查询，所有实际写入等后续运行具名授权；不读/输出认证值或账号profile，不打开真实用户库。保留旧媒体。

准备单条真实场景：软件聊天提出创作需求，必要方案/文案选择一次→真实开始制作与结果→自然语言一个修改并应用→仅重出/保存当前视频。仅使用既有本人录音纯素材0%，本轮不测Qwen/人物，不改变产品默认；复用现有原件与裁片，避免重复媒体工作。新MiniMax请求仅发送必要文字与业务摘要、不发原媒体，Key由root安全保存，B不接触Key。具名Pi路径/已装公共依赖可只读核实际版本，不能运行Pi/媒体/浏览器/API/Web/Electron/下载/构建或登录钥匙串。

尽快交一份简短可执行准备结果：确切包/输入/配置与数据归属、所需最小构建差额、真实对话将如何进入/看到返回、首个缺失条件。不要新造长期规范/模板/完整测试计划，不重复已通过检查。UI100候选交付前，本单不等待未来条件；交结果worker_done并retain，后续以准确候选复用同终端接续运行，不从旧START恢复。

### UI100：核心对话创作接线（已succeeded源码结案，写权结束）

用户09:47最新明确不做无谓测试，首先关注对话框输入需求能否完成对应操作。暂停账号适配新增工作，B99已按转向failed部分结案：生产视频号verify/persist回读成功，自有34进程均退出；4个未提交源码文件与patch保留，不强制提交未测差额。UI98/99成果不进本次核心基线，不继续三平台浏览器。

复用UI原可见终端，保护98 clean分支，从准确4c748dfc45229b879e9e1daf473b309432899f4c建立codex/chat100-actions-20261009。主控静态已核HomeCommandComposer有创建方案/确认创建，assistant_pi与diary_tools仅提供读上下文/资料/素材、文案草稿与编辑预览，没有启动/继续制作工具；不能将B86两次真实Pi创建和B92/97实际成片分项证据称聊天闭环。

目标是用户对话指令→当前任务的真实创作动作→持久作业/版本/可用结果→界面回读。沿现有HomeCommandComposer、CreationTaskWorkspace、assistant_pi、diary_tools和真实API命令链，补上核心流程所需的动作接线，不另建Agent、调度器、通用任意HTTP调用器或前端事实源。优先处理明确“开始制作／生成成片／修改后重新生成／继续制作／取消制作”；核对新建后选题/文案阶段的聊天入口，若现有工具不能推动实际生成选题/文案，按实际缺口复用既有入口。用户明确选题、确认文案等阶段动作只执行一次，不新增二次确认；自然语言编辑仍选择应用到草稿，不自动生成。不得把收到消息/预览/queued写成执行成功或成片已完成。

源码所有权：apps/api/app/assistant_pi.py、apps/api/agent_tools/diary_tools.ts、apps/api/app/schemas.py中直接assistant请求/响应模型（不是持久schema迁移）；确有现有接口缺口时apps/api/app/main.py的assistant/creation-task直接接线（不改调度/Grant/cap/持久schema/认证规则）；apps/web/src/components/HomeCommandComposer.tsx、CreationTaskWorkspace.tsx及直接必要lib/api.ts、lib/types.ts；直接已有API assistant与Web消费者测试。不改platform_*、AppShell/CSS/视觉、桌面/依赖、共享文档/Linear/GitHub。新owned /tmp/main-acceptance-20261009/chat100/保存最小报告和证据。按活动产品§4/§9/§11、domain目标阶段与auth§2读必要章节，不读全仓或全部历史。

真实操作必须由现有服务端可信成员/工作区和当前任务解析；只允许具名创作动作，不让模型指定任意路径/task/owner，不绕过已确认文案、制作要求、当前输入版本、声音/素材授权或整机许可。已有开始/继续/取消与作业回读接口优先复用；需要用户明确选择的对象不得由模型代确认。没有前提准确给下一步；模型选错或不支持说明具体动作，不能假执行、预先seed状态。没有产品收费，不新增报价/扣费/付费确认；禁止真实发布/删用户数据。

本单先实施当前源码缺口及一次最小直接检查，不重复旧成片/全CI/宽度矩阵。无运行服务、桌面包、浏览器、Pi/Key、模型/媒体/下载权限。交clean候选与相关diff：实际开放的聊天动作、仍需点击的必要步骤、具体未接通项，代码/fixture不得写GUI或真实Pi通过。主控审核后机械集成，再由原B终端仅跑一次同候选真实聊天主线（不从旧START运行）；发生首个真实断点即交原写者修复，不为测试数量扩范围。

### B99：真实账号身份收口（已用户转向结案，源码/运行权结束）

UI98已部分结案、主控已读报告/diff，最终clean09680ab7de78070dda8e67f9c0035669eb593b84基于4c748dfc；connect/verify三平台入口已准备，视频号实际span#finder-uid-copy已观察，但最终生产login/verify/persist未执行，抖音label正则无实证、小红书ID未找到。只接受部分源码与DOM发现，不接受真实账号核验完成。98原15分钟租约已结束，自有浏览器已关闭；新99不是延长旧START。

复用已保留B原可见终端，在pipefish保护97clean分支，以只读本机UI仓库准确09680ab7创建codex/platform99-identity-20261009；不整体merge/rebase旧分支。B独占98所列12个产品文件及其直接测试，UI保持retain无写权。优先移除没有实际DOM依据且实测为零的read_labeled_value及label/selector双模式，复用单一实际观察字段读取，不新增通用猜测或昵称回退。保留能力控制的三平台账号入口和同一login事实源；wechat/xhs的browser_publish/verified仍false，不新增发送权限或改发布队列/schema/cap/打包/依赖/共享规范。

一次明确用户结果：原已登录成员专用profile→生产PlatformAuthService.verify_current→保存并回读同一workspace/member/platform/账号标识。先用已经实测的视频号selector完成真实生产读取与持久化；仅写owned /tmp/main-acceptance-20261009/account99/私有隔离账号数据库、脚本和非秘密报告，profile继续原platform93/profiles原地使用，workspace ui93-readonly-workspace/member ui93-readonly-member，不复制导出会话，不写真实用户库。沿已有staging-venv的Playwright/channel=chrome。语义ACK后直接执行，无第二轮START；模型/媒体/桌面服务已全部停止，本任务只读账号浏览器串行，首浏览器起固定20分钟不延长。账号ID仅在私有0700证据，Git/公开报告不写实际值。

完成视频号后，抖音/小红书各一次定向账号定位：只读取账号身份专门字段及最小tag/id/class/role，同官方域必要iframe。若需官方账号资料入口，使用真实现有可达名称导航，不能猜selector或扫描整页/主页内容。页面渲染等待用已有界限，不将瞬时count=0当能力结论。无可用稳定ID就准确保留缺口；两次同类失败停止旧方法，分析具体原因，不盲目重试。不得读取body/header/全AX树/无关交互内容；不用get-app-state全应用找窗口，不读其他窗口。允许本机已有页面DOM工具在自有profile处理已批准只读页面；遇真正策略/权限拒绝不换工具绕过。若新账号需扫码，由主控通知用户，不代确认账号。

禁止选视频/填表/上传/存平台草稿/发布/改账号设置/读无关历史/读取Key/Pi/模型/下载。普通scope内脚本修正、等待SPA、准确账号入口与必要SQLite伴生文件不逐项请示；真实范围冲突及时问主控。最小必要测试为直接平台auth/browser/publish及未影响Web结果复用；真实读写回执独立于stub/GUI结论。交最终clean候选、完整净diff、实际三平台已核验与未验、所有自有PID/PGID释放证据；不重做媒体/完整构建，不批量关闭父issue。

### UI98：三平台账号接入与真实身份修正（已failed部分结案，源码/运行权结束）

复用UI原可见终端，从准确clean4c748dfc45229b879e9e1daf473b309432899f4c建立具名新分支，先保护原UI90分支/证据。用户已批准三平台首版与真实账号只读，现有候选账号data-e2e不匹配，service/UI硬编码只允许抖音。目标是三平台可从软件打开成员专用浏览器，读取实际稳定账号标识并持久保存原workspace/member/platform归属；不能把显示名当稳定ID或称已发布。唯一源码范围：apps/api/app/platform_auth/service.py、capabilities.py；apps/api/app/platform_browser/{base,douyin,xiaohongshu,channels,playwright_driver}.py；apps/web/src/components/PublishingWorkspace.tsx及其直接既有测试；apps/api/tests/test_platform_auth.py、test_platform_browser.py，以及test_platform_publish.py中直接受login稳定ID契约影响的既有driver替身与身份变化拒绝回归（不改断言以掩盖失败、不扩发布行为）。新owned /tmp/main-acceptance-20261009/account98/写必要准备/测试/报告。main.py/发布队列/发送执行/数据库schema/资源cap/打包/依赖/共享权威只读，不给B97已冻结4c引入变更。按产品§11/认证工作区边界，不改变业务授权。

先读UI93/96报告、现有真实consumer链和对应权威，语义REBASE_ACK后可以定向源码准备与最小必要测试；涉及真实页面locator的实施必须据实际字段/role/容器，不能从AX文字猜DOM、不宽扫body个人内容。三平台connect/verify由能力与实际adapter控制，不新增并行Auth体系；账号打开/核验与发布能力分开，未适配发送的另两平台browser_publish保持false，verified不因login自动true。没有可验证ID明确未核验；状态错误给真实原因，不降级为昵称或伪成功。

实际只读浏览器等root确认B97服务/媒体全释放后发本Dispatch ACCOUNT_ADAPT_START；源码准备可并行。每平台一次，至多总15分钟，复用原93各自相同workspace/member/platform的0700隔离profile原地不复制/导出；沿现有PlaywrightSessionManager/channel=chrome，仅官方原域与必要同官方iframe。用户之前抖音/小红书登录与视频号既有会话保留，不预先要求重新扫码。必要时读取账号可达专门区域/空上传入口，未登录仅报告实际QR由root通知用户。禁止选文件/上传/填文案/保存草稿/发布/改账号设置/读取无关历史/导出会话；遇真权限/策略拒绝不切工具绕过，不下载/变更浏览器。B97运行期间无浏览器/服务/模型/Pi/Key/媒体。实测稳定账号字段/实际DOM及官方frame作用域后定向修正既有adapter和driver，维持当前身份复核和不可重发边界，不新增通用猜测兼容层。

做一次最小相关接口/组件验证与真实只读账号场景，UI可见证据不得用HTTP代替；GUI工具受阻明确未验，不因此反复请用户操作。新源码不进B97包、不重复媒体/Web全构建；必要真实UI新服务需先交具体最小方案由root指派互斥运行。交clean候选/diff、实际账号核验与缺口、最小证据/未验、所有owned浏览器PID/PGID释放；账号ID只留私有证据不进公开GitHub。整个平台发布/父项不自动Done，93/96已通过且未影响证据复用。

### UI96：视频号登录页定向补验（已succeeded结案，运行权结束）

主控已读/tmp/main-acceptance-20261009/platform96/report.md并独立核23868/23883均退出；Dispatch已retain。原隔离profile直接恢复已认证态，无新扫码，读取稳定账号标签与空上传表单、短标题/描述；既有候选data-e2e均不匹配，同host iframe仍需适配。回执和自动发布未验；未选文件/输入/保存/提交，profile保留0700。原93已确认抖音账号标签、小红书空文件控件；本次不重跑它们。

UI93只读报告已交并retain，三个专用浏览器均关闭；抖音账号标签与小红书空file input正证接受，抖音表单/回执、小红书稳定ID/媒体后字段/回执、视频号登录均未验。element_not_found是旧AX快照索引失效，不是权限或策略拒绝，不把它当平台不支持或用户尚未批准。用户本人扫码/账号控件只读批准继续有效。复用UI终端，唯一新root /tmp/main-acceptance-20261009/platform96/ 写准备脚本/非秘密报告与临时证据；仅视频号可以在原93私有微信profile中继续浏览器本机会话写入（保持原workspace/member/platform归属、0700，不复制/导出/迁移会话）。原93另两平台profiles只读保留，不重启。

只读平台运行blob准确2c896f24（与最新4c748df平台目录相同），无产品Git/依赖/共享文档写权；使用已核3.12.13/Playwright1.63.0和现有PlaywrightSessionManager/channel=chrome。开一个官方channels.weixin.qq.com登录新窗口，固定10分钟lease，从fresh页面状态判断当前控件。不要复用已过期AX索引；当前有实际名字的“使用其他头像、昵称或账号”或“加载失败，点击重试”是已批准扫码流程的普通入口/错误恢复，不再问权限。沿现有页面工具在新会话观察实际role/文本/官方iframe host后至多一次登录方式切换、一次明确重试；未发生的旧AX点击不算已执行登录请求。若出现权限/策略拒绝，不切表面绕过；实际一次重试仍失败、验证码、陌生域或新权限则保留具体错误，停止该动作。不得点微信快捷登录、启动/读取桌面微信已有会话、代用户选择账号或确认授权，不运行模型/媒体/产品服务，不下载/改代理/浏览器设置。

二维码实际可见后及时给root临时截图/真实host/窗口，root提示用户本人扫码；扫码后只读账号标识、空上传控件和现有回执入口，不上传/选文件/填草稿/保存/发布。各批消息处理后ACK以取得用户回报。到期或真实拒绝关闭所有自有浏览器/driver并核PID/PGID释放，保留profile，交最小实际结果/未验，不把工具定位失败写成平台能力结论。本次仅补原缺口，不重跑抖音/小红书，不恢复93旧租约。

### B97：诊断差额入包后仅补真实修改重出（已succeeded结案，运行权结束）

主控已完整读/tmp/main-acceptance-20261009/rerender97/report.md，定向核当前实际视频/probe/hash和释放，原queued request获Grant generation3完成V5→V6的1:1视频，5秒1080×1080 H264/AAC、1093742B、hash874f88b3c89fdb794bfeba124280ae962525167ba223038fce575d4e1fb621b9；旧first/undo及媒体保持。最初素材root配置已纠正，同次I/O拒绝后队列自然恢复，无新POST/resume。全部两组28PID/10PGID及3000/65189/50071退出，Dispatch已retain。以下START、白名单与租约仅为已结束操作的历史，不恢复权限；UI98已获ACCOUNT_ADAPT_START，模型/GUI/人物/Pi/发布仍未验。

2026-10-09 09:04定向修订 MAIN-RERENDER97-FIXTURE：静态核验表明free permit仍绑定原DB父目录锁，新路径clone会被现有真实边界拒绝。为避免重复已通过的上游，不改锁规则、不编辑DB，采用原停机92隔离fixture继续真实queued恢复。此前“所有写入仅97且旧92库不变”仅对此测试库/锁白名单由本段替代；它不是用户真实库或迁移。B必须先将旧92 SQLite完整只读backup保存97并记录hash，原first-video.mp4、undo-output.mp4及既有输入保持hash不变。唯一允许旧root写入：creation92/db/fixture.sqlite3及必要-wal/-shm、同目录device-permit.lock、.lock.state及既有原子落盘所需device-permit.lock.state.<uuid>.tmp（成功replace或失败清理按既有实现）；这些具名写入的必要伴生/临时文件不需要逐文件再请示。其余config/userData/日志/tmp/新VIDEO_OUTPUT_PATH/新产物写97。沿原数据库/锁身份保持owner及queued原请求，不造新请求或seed成功；已有逻辑artifact只读复制97。若job仍要求在该白名单外写旧92，停止并交具体路径。用户09:07另已批准正常退出08:37重开的creation89旧测试版（PID95927/子96121），保留数据，允许本次原生退出；不终止其他应用。启动前交新包身份、backup/媒体hash、配置路径核验与无旧进程证据，ACK本修订；服务仍等待本Dispatch的RERENDER_START，固定首服务10分钟lease与原cap不变。UI96已全释放，主控已独立核PID消失；不重开平台浏览器。 首次97包恢复因已登记素材绝对路径与新VIDEO_INBOX_PATH不符而dependency等待（未admit/Grant）。已具名原92/library测试输入本就只读授权；允许修正VIDEO_INBOX_PATH回原92/library，定向核所有持久输入路径，再仅一次正常停97包/重启97包，沿原queued恢复。保持原09:09:25首服务、09:19:25绝对期限及原fresh swap基线，不重新标定/续租；输出/日志仍97，不扫描用户其他资料、不修改库或授权。

从clean4c748dfc45229b879e9e1daf473b309432899f4c复用B终端，唯一owned /tmp/main-acceptance-20261009/rerender97/，无产品源码写权。B94仅改host_resource_probes运行日志，B95纯测试；先证明相对B92准确2c896f24其余Web/desktop/worker/agent运行blob不变。只读复用92已物化Web/公开依赖，按既有PyInstaller/Electron Builder增量API重新产生实际ARM64 .app及新身份，不Next全构建/热换旧包、不DMG/ZIP/签名/下载。准备阶段可与UI96并行，但不开服务/GUI/媒体，实际运行等root确认UI96全释放后发RERENDER_START。

允许只读复制92已停止隔离DB及其真实已确认录音/故事板/编辑V5作为新隔离测试输入，必须记录来源/hash/当前版本，不seed成功或冒称本包新生成上游，不迁移用户真实数据。仅必要已具名92测试媒体只读；测试Key/模型为空、Pi offline/定时OFF。先核clone的旧permit确为free、无活动旧进程，并定向检查输出/日志等本次写入不会指向旧92；若原job或数据绑定要求写旧路径则交具体事实，不能改DB绕过或写旧产物。新DB/ledger/config/输出/日志/所有写入仅97root；原92库、输入和产物不变。

当前合法成员/设备/可信owner归属及V5的真实依赖保持，沿本包DeviceRuntime/Grant仅执行已明确的1:1重出（既有queued恢复或一次真实resume，不能重复创建请求），读取当前MP4到本根，核对应版本/hash、真实ffprobe及保持原9:16文件。新服务起固定10分钟lease，不自动续，串行最多一个重出；继承B92有限CPU实验同一声明上限，fresh启动swap基线+64MiB只用于本新窗口，不抬任何原cap，CPU75/可用至少1.5GiB+render512MiB、磁盘1GiB+render16MiB、IO128MiB/s、最长15秒/1920px/像素秒31104000/2clips/输入输出8MiB/threads1/60s不变。不跑Qwen/人物/Pi/账号/Key或GUI，不恢复92旧租约。阻断时必须从该实际admit日志回读同次数值，后续采样另列；不盲重试/手清Grant/改成功状态。

交实际包身份与未变复用证明、clone真实输入来源、仅重出与当前文件或首因同次诊断、旧92完整性/进程端口全释放。已通过的92录音/首次成片/undo/redo与本次重出分开标注，不重复整轮，不以HTTP冒称GUI或三平台通过。

### UI93：三平台真实账号只读接入与适配证据（已交只读报告，运行权结束）

用户2026-10-09已明确答复“允许账号接入，准备好后提示我扫码”，仅批准新的本机测试Chrome配置打开抖音、小红书、视频号官方创作后台，由用户本人扫码登录，只读核对账号标识、上传控件及已有发布回执页面。不上传/选文件、不填写或保存平台草稿、不点击发布/发送、不修改账号/设置/内容；授权不等于外发批准。此前全局无真实账号条款仅为本条具名范围解除，其他线程无账号操作权。

复用原UI可见终端，准确只读组合2c896f24bad4ff7e2d107edb7ba59a61b33e2749的platform_auth/platform_browser直接消费者，无产品源码/依赖/共享文档/真实用户库写权。唯一写入/tmp/main-acceptance-20261009/platform93/：独立HOME/XDG/tmp/日志和按workspace/member/platform隔离的Chrome profiles（0700），会话只留本机profile，禁止导出cookies/storageState/token/HAR或将凭据写日志/报告/仓库。不复用个人Chrome或旧包账号。允许已有Playwright/channel=chrome现有产品会话路径及必要只读观测脚本，不下载浏览器/依赖，不另造产品连接体系。

先准备owned目录、现有公开工具/官方入口及一次只读操作计划，回报ready；浏览器实际启动等待主控与B92确认媒体空闲后的ACCOUNT_READ_START。这个等待是本机资源互斥，不是再次用户授权。每次仅一个平台/一个Chrome配置，用户本人扫码后只读当前账号稳定标识、发布表单控件以及可达的已有成功/失败回执入口，尽量局部截图/结构化非秘密DOM，不浏览无关内容或整库数据。仅允许现有抖音creator.douyin.com、小红书creator.xiaohongshu.com、视频号channels.weixin.qq.com官方域及各自必要官方登录域；陌生跳转/新权限提示回报，不猜选择器/绕验证码/登录限制。进入空上传表单是读取控件，不选文件或提交；任何实际发送需另具名授权。

准备/扫码窗口真实显示后发root状态，由root提示用户，worker不反复催问、不自动代替扫码。识别昵称与稳定ID区别，无可读稳定ID/回执则明确缺口；DOM/HTTP/截图/用户扫码回执分别记录，不把本次登入与控件存在标成自动发布通过。逐平台关闭owned浏览器释放PID/PGID，保留profile但不删除会话；实际账号浏览器本轮固定30分钟上限，可完成平台先交结果，未扫码/工具拒绝列未验，不把等待当答案。最终交最小选择器/能力缺口、下步所需适配路径、实际证据和未验到report.md并Task结算，root安排具名产品施工/外发验收。当前B92源冻结不因本只读任务失效。

### B95：CI旧测试替身的请求ID（已succeeded结案，写权结束）

主控审clean556b4624e646c20f1e6e5e509649ae1351104301的一行测试ID差额及18项原失败专项，机械组合4c748dfc45229b879e9e1daf473b309432899f4c，推草稿PR101并回读head相同；产品blob相对26911a1未变，新CI37867055308三项全部通过（Backend5m39s、Frontend2m47s、Desktop11s）。报告/tmp/main-acceptance-20261009/resource95/report.md，测试组释放，Dispatch已retain，无新产品或运行权限。

准确26911a1的CI37866253751前端/桌面通过，后端18失败/1671通过/20跳过：全部18失败来自tests/test_creation_resource_contract.py::_verdict的_Request无id，而真实WorkRequest必有id。主控已读该共用替身及实际AttributeError，不为旧测试添加getattr兜底或改准入契约。复用retain的B终端，从clean26911a1339bfb140c6f01fc3a01bea277ed5d90b建立codex/resource95-test-fixture-20261009，仅apps/api/tests/test_creation_resource_contract.py写权，为该既有替身补明确fixture request ID，不更改断言/删除用例或修改任何产品代码。仅/tmp/main-acceptance-20261009/resource95/隔离HOME/XDG/DB/tmp跑该文件失败的18项，无服务/媒体/模型/Pi/Key/GUI/账号/下载/打包或外部写入；不全量本机检查。交clean候选、净diff、必要结果及未验，主控审/集成/CI回读。UI93账号观察继续，不受纯测试差额影响。

### B94：排队拒绝的同次资源诊断（已succeeded结案，写权结束）

主控审核clean e52e25e8b4d4e64b794368409e8907181bb9f8d1，两个owned文件净差额及8项专项；拒绝/未知日志来自原一次采样，bool/None、cap、Grant与等待条件未变。机械集成26911a1339bfb140c6f01fc3a01bea277ed5d90b并推草稿PR101，最新CI待回读；旧2c896f24 CI和B92实际包证据保留各自范围，不冒称新包日志/重出通过。报告/tmp/main-acceptance-20261009/admission94/report.md，测试组已退出，Dispatch已retain。源写权已结束，未有新服务/媒体运行权。UI93继续只读2c896f24的未变platform_auth/platform_browser；集成中只有host探针/测试差额，账号观察和已加载浏览器不受影响。

B92实际重出保持resource_busy/Grant0，HostResourceProbe.admit丢弃既有HostResourceDecision，只留下bool；后续重新采样无法解释原拒绝，是本轮真实诊断缺口。复用已retain的B可见终端，从clean2c896f24建立codex/admission94-diagnostics-20261009。唯一源码写权apps/api/app/host_resource_probes.py及既有apps/api/tests/test_host_resource_probes.py；不写scheduler/runtime/业务/数据库迁移/界面/打包/依赖/共享文档。复用现有结构化decision和项目日志方式，在admit这一实际采样返回拒绝或未知时保留该次request ID、判据/实测值/阈值及未知原因，供现有API日志诊断。不二次采样，不新增持久表、通用观察系统或并行事实源，不改bool/None返回契约，不抬cap、不更改Grant或等待条件，不用日志宣称UI解释已完成，不记录密钥/用户正文/凭据。

仅/tmp/main-acceptance-20261009/admission94/自有隔离测试，最小验证拒绝日志的值与决定来自同一次采样、未知原因不伪造成忙、通过不误报拒绝；复用既有相关资源用例，禁止全量本机检查。无服务/GUI/媒体/模型/Pi/Key/账号/网络下载权限，不与UI93浏览器竞争媒体资源。交clean候选、净diff、必要检查及未验，主控审查后机械集成，再判断必要新包运行；不得复活92旧租约或用日志补丁声称重出完成。UI93只读绑定2c896f24不受这一源诊断补丁影响。

### B92：组合修复的API增量入包与实际成片（已failed结案，运行权结束）

主控回读creation92/report.md、release.json及实际文件证据后结算：准确2c896f24新ARM64包的裸库初始化、录音save/adopt、Grant首次真实5秒H264/AAC成片、画幅编辑及undo/redo通过HTTP/持久库/文件验证；当前V5为1:1且无当前输出，唯一重出请求attempt0/resource_busy/permit free，不能用首次或撤销后的旧输出冒充重出完成。同次准入数值被现有源码丢弃，08:30:27独立采样swap超原cap只证明后续诊断，不冒称排队同次首因。原生GUI只首页可见，任务/录音/编辑/保存GUI及输入匹配/听感未验。08:31:24自有12PID、6PGID、3000/61867端口全释放，固定租约未续；Dispatch已retain，旧START不复活。报告与首次视频仅在/tmp/main-acceptance-20261009/creation92/，不是全产品通过。UI93已获ACCOUNT_READ_START，仍仅账号只读。PR101准确2c896f24的CI37864291532三项均通过；源码合入与全流程完成分开。

B91 task_1a9de32d4beb / ctx_4cdf9a1f3e03已succeeded，主控审clean a2e2f2cf834578eabe98250b8ca86e7439d9bb86四owned路径及22项真实POSIX/模拟冻结专项；未当实际包通过。UI90/B91机械组合clean2c896f24bad4ff7e2d107edb7ba59a61b33e2749，位置/tmp/main-controller-20261008/ui-v3-integration。相对1767e11运行差额仅database_migrations.py、granted_subprocess.py、server.py；Web/桌面/worker/agent运行blob逐路径未变，允许下面只读复用89已物化Web，不重跑Next。新B92读取本条语义ACK后直接按具名完整候选运行；不需再次用户批准或START。

仅B91净候选经主控审、与UI90机械组合并给出完整SHA后，复用B可见线程启动新B92；当前不是START、不恢复89租约。新owned根/tmp/main-acceptance-20261009/creation92/，冻结新输入；相对1767e11的运行差额只允许已审迁移和冻结入口。主控先证明Web/桌面/worker/agent运行输入未变；B可只读复用89公开已物化Web/依赖，不重复Next构建。仅API按当前PyInstaller spec增量重建一次，再走既有Electron Builder重新生成实际候选身份与arm64 .app；不是将二进制热换进旧包并沿用旧digest。所有缓存/生成物/隔离HOME/XDG/userData/DB/library/output/log均归新根，旧89输入/失败DB/包/证据不覆盖，无模型/浏览器/全局下载、DMG/ZIP/签名/Release/Applications写入。

新包真实API/Web启动及全新裸DB/合法本机成员/设备/可信owner，复用89只读五秒本人音视频作为输入，确认单段文案、纯素材0%测试要求、实际录音save→explicit adopt→预检→DeviceRuntime/Grant制作故事板/成片→一个普通可撤销精确修改及undo/redo→仅重出→当前视频读取/保存。输入语义/听感未核保持未验；默认20%不改，不seed Narration/故事板/输出、不跨许可。只用原89有限录音CPU实验caps，fresh swap基线+64MiB；不抬阈值、不运行Qwen/人物/Pi/平台/Key或真实用户库。本阶段重处理串行、既有FFmpeg每步60秒/一线程/15秒成片/8MiB输出等89上限继续；首次服务起固定20分钟租约，不自动续租。

允许新包必要native GUI一次代表任务/保存截图；window_not_found先fresh list-windows核实际selector，window_not_focused按当前工具规范最多一次恢复，失败仅具体验收项未验，不CDP或API冒称GUI、不重复让用户按键/拖窗。API和文件链继续。正常停止仅登记自有PID/PGID，核全部释放。若真实失败，交首因及最小建议，不热修冻结副本/手工清许可锁/反复重试。报告同时列实际包身份、源复用证明、媒体文件与版本及GUI证据类别，主控审核后结算；完整模型/人物/三平台仍未验。

### UI90：CI37862398673的迁移与旧测试替身缺口

task_1e35130530df / ctx_302e5db92ca5已succeeded，交clean5da2fbbfd52b77d049e51503f1e0b4186d806fd0，5授权路径+48/-6。主控审核缺表时重放原v4真实schema后执行v15，保留实际已有generated身份；迁移5/Webm1/Web7专项通过。主控机械集成c446d60，逐blob仅两已批准权威文档与执行源不同。原UI终端retain，下面施工条款只供证据索引，不提供新写权。此前Corepack启动提示如实记录，已停该路径并使用已有Node/Vitest完成专项，无锁/依赖变更。

PR101准确1767e11的远端CI已回读：Desktop syntax成功，Backend五失败／1659通过／20跳过，Frontend七失败／570通过。具名失败日志/tmp/main-controller-20261009/pr101-ci-failed.log。前端六项保存反馈缺listCreationTaskRecordings mock，另一项仍断言已替换的旧提示；后端四项裸auth/shared DB及旧v5迁移遇v15假定narration_segments必在，另一项Webm测试被WAV-only probe替身误读。这是本次真实失败，不能用吞错或修改通过数量掩盖。

复用原UI可见线程，从cleanfe04d81建立codex/recording90-ci-fix-20261009，保留87/88。唯一写权apps/api/app/database_migrations.py、production.py的必要初始化直接消费者及既有tests/test_local_members_auth.py、test_production_foundation.py、test_task_recording.py；Web仅CreationTaskWorkspace.test.tsx和CreationTaskWorkspaceSaveFeedback.test.tsx的既有替身／断言。不改产品文案/页面逻辑、其他认证/发布/配音/资源代码、共享权威或依赖，不写真实库。沿既有迁移机制保证裸共享DB可重复初始化，narration表后建立时确实完成v15，不错误记录已升级、不丢旧generated身份/数据/索引；只处理本次明确表前提，不新增通用兼容框架。Webm替身必须符合容器事实，不能把Webm冒称WAV；前端替身同步真实新增API，不删相关保存反馈覆盖。

仅自有/tmp/main-acceptance-20261009/recording90/隔离HOME/XDG/DB/tmp做这些失败项和必要迁移回归；不全量本机检查。既有Webm用例如需合成最多1秒音频，允许已有FFmpeg/ffprobe单线程单步15秒，只在B89未执行媒体子进程时运行，不碰用户媒体／模型／GUI／服务或下载。报report.md净候选、最小结果和未验。B89保持冻结1767e11实际包继续只读诊断与结算，不更改其源/包、不等待UI90；主控后续机械集成、判断未变证据及必要新包差额，不以旧包验新迁移。

UI88 task_238fb365c116 / ctx_29444be61a36已交cleanfe04d8111e3601f5e05c4ad48da0d0f6ba6f94a6，主控复核预检recorded优先、页面按钮与Inspector真实表达，接受隔离专项且retain。87/88与两处唯一权威差额机械组合为clean1767e11d411e40812d3b7fa7990973c5741e0c1b，位置/tmp/main-controller-20261008/ui-v3-integration、分支codex/recorded-narration-delivery-20261009；运行源码逐blob与fe04d81相同，仅两权威文档不同。87/88旧任务结束，以下旧条文不是新写权。

### B89：新录音候选实际ARM64包及一次媒体闭环（已failed结案，运行权结束）

task_a4b667ebe4c2 / ctx_ff20ae5e926f已failed；主控接受实际录音保存/采用及故事板V1、无当前MP4和冻结入口推断边界。报告/tmp/main-acceptance-20261009/creation89/report.md、release.json及all-owned-process-exits.json已核，主控08:14独立ps查自有包/媒体进程为空。持久Grant3 held是保留的失败状态，不冒称逻辑清锁。复用同一可见B终端的新B91已有input_accepted/turn_started回执，旧89范围不恢复。

复用原B可见终端，保留旧84/86分支和证据，从上述准确1767e11建立codex/recorded-package89-20261009。唯一写入根/tmp/main-acceptance-20261009/creation89/，所有构建、隔离HOME/XDG/userData/DB/config/library/output/cache/tmp/agent/log均在此根；冻结Git输入、排除dotenv，真实用户库/Key/账号不接触。无产品源码写权。因87/88产品源码变化必须沿已验证84路径重建一次ARM64 local.app；可只读复用具名公开已装依赖/缓存，不安装全局、不下载模型/浏览器，不生成DMG/ZIP/签名Release，不覆盖Applications或旧包。

新包启动实际API/Web/Electron，合法本机成员/设备和可信owner归属建立隔离任务，确认一个文案段并明确将本次测试要求设为纯素材0%，不是更改产品默认20%。输入仅86.2已授权两具名本人音视频，保留原件、定向各取最多5秒；可复用86已裁输入但标记为输入，不冒称本次生成。通过本包真实录音保存→明确采用→预检→现有DeviceRuntime/Grant/Worker制作故事板和成片→一个普通可撤销编辑→仅重出→读取/保存当前视频，核当前输入版本、实际文件hash/媒体探测和旧结果失效。不得seed成功Narration/故事板/成片、跳过许可或以TestClient/AST代替实包。准备可用既有repository/API，但可信owner须从真实LocalMember取得并传入确认；页面、HTTP与文件结果分别记录，字幕与本人录音内容匹配/听感未核须保留未验。

不重复86真实Pi创建，不调用Pi/模型/平台，也不读取新旧Key store。重处理串行：录音输入截取单线程每步30秒，实际渲染每步60秒，素材规划10秒。仅本次有限实验复用86的纯素材相关caps：Host CPU75%、保留1.5GiB可用、磁盘至少1GiB、IO128MiB/s；montage额外64MiB/一段两素材/输入4MiB/输出256KiB，render额外512MiB/磁盘16MiB/最长15秒/最高1920px/像素秒31104000/两镜头/8MiB输入输出/一线程。swap总限仅可依据本次启动后的fresh样本加64MiB设置；未知或不足继续真实拒绝，不猜预算、放宽cap或强行Grant。不得跑Qwen或人物模型；Qwen旧5.5GiB准入证据不用于本录音CPU路径，也不能声称模型配音通过。

包启动起固定20分钟租约，构建阶段不计运行租约且不得启动旧包。B只登记/停止自有PID/PGID，证明端口释放；若3000被其他应用占用不自行关闭。用户已批准“允许退出旧测试版”，主控按同一目的复用正常退出授权：本次仅已核实的desktop84 Electron30912（07:49:01启动）及其正常应用退出，保留保存数据、不得强杀、不得访问其库或关闭其他软件。此前主控将此批准解释为仅81实例的限制予以纠正，不扩大至任意进程或平台账号。可用原生computer工具查看新包和操作一次相关入口；已知焦点/AX拒绝保持具体验收未验，不再要求用户Tab/调窗、不换CDP绕过。无下载、真实账号/登录/平台网络/上传/草稿/发送、收费、Supabase、真实数据迁移或外部写入。交report.md：准确候选/包身份、每步真实结果及首因、普通编辑与输出版本/文件、可见证据与具体未验、全部自有进程释放。精确ACK后直接执行，本机包/制作工作已获用户授权，不等新的START；首个真实缺陷给主控交原写者，不偷偷修改源码。

### 已结束的UI87／88与B86范围，仅作证据索引

UI87 task_070705973546 / ctx_dc7706d9c816已交clean96785d0077ba118bdcab812dc176e4bc73a13b0a，接受首次录音保存/显式采用及隔离专项结果，未接受完整页面制作结论。主控实读CreationExecutionPanel仍按selected_voice锁定开始/继续，Inspector仍以缺失模型声音表达录音；源保留，由同一可见线程以下述UI88修复，不重做87、不启动旧Dispatch。

### UI88：有效录音的开始／继续与详情消费者

从clean96785d0建立具名续分支，保留旧提交和报告。唯一写权为apps/web/src/components/CreationTaskWorkspace.tsx、apps/web/src/lib/types.ts、apps/api/app/main.py的execution/preview响应和直接既有专项测试。既有有效recorded Narration与纯素材条件必须在预检和页面一致表达；开始/继续按钮不得仅因selected_voice为空禁用，Inspector显示真实本人录音来源，不伪造voice_id或模型授权。失效来源或人物依赖不放宽，用户默认20%人物目标不修改。共享产品§10/领域§5.4语义不变，沿现有Narration和execution事实源，不新增框架或收费工作。

主控07:42审查消息msg_87d9669653d4和07:46 msg_11dfdd849479未落实于87源码，本单必读并明确ACK。先核现有diff，再直接完成这一已授权首次录音纵向的必要消费者；只补页面开始/继续和真实预检录音表达的最小专项，复用87未影响证据，不运行全量检查、服务、GUI、资源采样、模型、FFmpeg或打包，不访问真实库/Key/平台。报告和证据只写/tmp/main-acceptance-20261009/recording88/；交clean提交、直接差额与检查、明确实包/真实媒体未验。最终冻结后由主控组合并安排新包。

B86 task_84f88d406229 / ctx_37688cc35760已按真实完整制作未达failed结算并retain，报告creation86/report.md。主控复核实际首页、两轮国内Pi工具预检与新任务可信归属回读；配音队列resource_busy/Grant0，包内数值首因未知，独立资源快照低于门槛；原夹具缺owner作为准备限制保留。首次录音409交UI87，实际成片/编辑重出/原生保存未验。主控独立核5组及3端口释放、87具名文件扫描无Key且成员凭证仅预期localStorage、新store锁定。86以下为已结束范围说明，不得复活START/租约；当前仅UI87具名施工，最终新候选冻结后再绑定最新包补验。

用户本轮“继续吧”解除资源/模型验证暂停，变更 `MAIN-REAL-CREATION-20261009-86`。仅本节为当前运行入口；下方79–85及更早各任务均已结案，旧START/租约不得复活。主控共享规则写权和原UI/B可见终端不变，无内部Agent/新窗口。

B86复用pipefish终端，唯一运行负责人，证据与新HOME/XDG/DB/config/library/output/cache/tmp/agent只写 `/tmp/main-acceptance-20261009/creation86/`。准确产品候选30d37603ef292c6dd39daffa75df3119a1e933ba；复用84实际arm64.app（源码91a6279，差额仅两测试文件），不再构建。目标是实际包的合法本机成员/设备/Grant/Worker链：真实配音→剪辑→成片→普通修改→重出→保存，并逐段记录真实产物/输入版本/文件；不以HTTP、合成旧成片或AST提取代替整链通过。API业务操作可用于实际链路诊断，必须与GUI操作证据分开；不API写冒称GUI验收。

允许具名包、已有Node/Pi/FFmpeg、HostResourceProbe/进程采样和有限媒体子进程。已有模型只读：`/Volumes/Dujiangyan-VM/AI视频日记/model-runtime/bin/python`、`/Volumes/Dujiangyan-VM/AI视频日记/models/qwen3-tts-12hz-0.6b-base-8bit`；只读旧35最终report/commands/resource预算及合法合成参考输入可作为配置依据，旧产物不得充作本轮生成。首次Qwen短句使用已实证4GiB RSS/预留、5.5GiB准入，其他字段依据原同次证据与当前实际链路，属于本次有限实验，不写生产标定。取得fresh准入后运行，不因权限已经具名再等START；不足或超限不自动增cap，记录实际拒绝数值。单个配音最长180秒、FFmpeg单步60秒，全部重处理串行，父租约首次启动起20分钟。不得运行其它未具名模型；人物/口型真实一步后续依据具体资产另补本合同，不冒称默认人物链通过。

先使用必要最短真实输入证明链路，不重复UI/全量测试。保留源文件、用户库和旧证据；新隔离库可通过现有repository/API建立合法本机任务与草稿，定时OFF，网络默认无平台操作。Pi Key仅主控按下方具名例外处理；B不得读文档/登录钥匙串/秘密，等主控具名新store完成后最多两轮180秒国内MiniMax文字/工具调用，不上传原始媒体。当前可先完成无Key的资源/模型/媒体链，不能为等待Pi停独立工作。原生GUI工具若再遇已知焦点/AX拒绝，保留具体验收未验，不反复要求用户操作或切换表面绕过。

此单无产品代码写权，首个真实失败给精确调用/同次数值/必要修正，由主控交实施线程；不因局部失败停止独立可做步骤。无下载模型/浏览器、全局安装、真实数据迁移、签名/Release、Supabase/平台账号会话/登录/上传/草稿/发送权；客户BYOK不新增收费工作。只停止登记自有PID/PGID与端口，结束/到期证明释放。交实际包/候选、输入与版本、每步真实结果及首因、输出hash/媒体探测、明确GUI/Pi/人物/发布未验及释放到本新根report.md。先读本节语义REBASE_ACK即可开始，阶段结果只是checkpoint，完成实际可执行目标或明确真实阻断后结算。

86.1 主控仅将已授权validation Key从具名条目读到RAM，按现有workspace_settings原生store保存至本新根 `config/b86.keychain-db` 的local/llm_api_key，配国内cn/M3非秘密设置；不枚举其它条目、不落明文、不传秘密给B。创建新临时store若产生search list副作用立即原样恢复，前后默认与列表须相同。主控核新环境和store后给B非秘密回执，B只让实际包API读取此路径。结束主控锁定该store并RAM扫描本轮具名日志/配置不得含Key。此配置API准备不是GUI保存验收。

86.2 用户此前明确授权本人素材使用，本次只读复用两件原件：`/Volumes/Dujiangyan-VM/AI视频日记/acceptance/real-voice-20260824/reference/reference.wav` 与 `/Volumes/Dujiangyan-VM/AI视频日记/video-inbox/历史成片_待审核/VID_20260529_122252_preview.mp4`。B可复制到新creation86并以单线程FFmpeg有界截取≤5秒（单步≤30秒），保留源stat/原件。经真实已有录音替换/素材登记路径作为独立轻量成片输入，验证剪辑→渲染→修改重出/保存；它是导入已有媒体，不是本轮Qwen生成或人物口型通过，不能替代原真实配音缺口。不得为导入伪造TTS成功/有效版本、原件权属或媒体内容；缺少真实入口时准确报首因，由主控安排必要实现。

86.3 用户批准“允许退出旧测试版”，主控按具名desktop81/PID82431核身份，用NSRunningApplication.terminate正常退出；已正向核82431/82730退出与3000实际bind可用，原用户数据保留。B可先释放本单首启动86865及API自有组后同包/DB/store重开，父deadline仍首次启动07:38:51，不另起20分钟。其他用户进程仍不得停止。证据old-app-normal-quit.json；此原生应用退出不冒称GUI快捷键验收。

### UI87：首次任务录音采用到连续制作的实际缺口

B86实证：首次无故事板任务POST audio/recordings409，但montage也要求Narration，已有本人音频不能走完整链。本单是产品§10电脑录音/§14真实制作的必要差额，原UI终端复用，准确30d3760新干净分支，先核是否已有真实首次采用路径；有则修接线，无则在当前任务事实源内补最小首次录音采用，不造第二条制作路径。先交精确数据/状态/入口方案checkpoint，主控处理需要的共享领域语义；不等待用户重复批准实施目标。

唯一产品写权：main.py录音保存/明确采用/配音完成到连续制作的直接调用，task_audio.py、narration.py、production.py、creation_execution.py及schemas.py中确有必要的直接消费者；Web CreationTaskWorkspace.tsx、AudioTimelineEditor.tsx与lib/api.ts/types.ts的录音专用差额，直接既有录音/制作测试。保留已正确的生成配音、人物许可、队列、撤销/版本、发布和DESIGN，不重构公共层。本人录音是实际task_recording Artifact/Transform输入，只经用户明确采用绑定当前Script/段落；保存不等于采用。真实时长/hash/版本/同任务归属必须可追溯，变化只使相关依赖失效，不把recording伪标为Qwen结果/声音克隆样本，不把正常当前录音强制依赖一个尚不存在的故事板。段落对齐无法确定时说明限制，不猜文字时间轴或自动拆录音。任何新增预算/授权/领域对象先报主控，不恢复未知预算或无Grant生成路径。

证据/tmp/main-acceptance-20261009/recording87/；只做最小相关测试/源码检查，现有缓存依赖复用、不下载/安装/全量测试或打包/服务/GUI/模型。B86独占真实包运行/媒体，UI只用测试隔离输入，不抢文件或实机资源。先语义ACK，再落实真实入口、实际DB回读与新合法单段录音的连续制作衔接，交clean候选/diff/必要证据及未验；最终包与真实媒体验证由主控审核后另绑定，不用旧84包验新差额。UI无push/PR/Linear/共享权威写权。

87.1 主控接受精确checkpoint并完成 `MAIN-RECORDED-NARRATION-20261009-87` 共享语义变更：唯一权威为产品§10和领域§5.4。原UI87实现目标保持，新增Narration来源只在既有对象上表达generated/recorded；录音变体允许voice_profile/model为空，持久真实task_recording源/摘要/时长/当前ScriptSegment与明确采用，旧生成语义不变；纯素材真实渲染可用，人物授权/身份依赖不放宽。本轮先覆盖一个已确认ScriptSegment，无法明确映射的多段录音拒绝并说明，不伪造或猜对齐。UI重读两处及本条语义ACK后直接继续owned完整消费者与schema，不再等用户重复确认。B86冻结84旧包不具备新录音语义，继续已授权旧范围并结算实证，不以其验新差额；新源码冻结后再具名最新包补验。

87.2 B86最新包真实首页仍显示“仅传文字 · 可能收费”，与用户已批准不做产品收费的当前表达冲突。UI87额外唯一写权仅HomeCommandComposer.tsx:278与CreationTaskWorkspace.tsx:835的这两处提示及必要既有直接断言，已配置状态改为“仅传文字”，未连接语义/真实provider状态与服务详情保留；不新增报价、收费、充值或承诺供应商免费。不为这项文案新建镜像测试，最终新包首页截图可直接验可见结果。

87.3 既有narration_segments表profile/model NOT NULL，必须沿apps/api/app/database_migrations.py做具名版本升级以表达录音变体；UI87扩展唯一写权该文件及既有直接迁移测试，保留旧generated行、身份约束、索引/触发器和现有迁移机制。仅自有隔离DB验证，真实用户库迁移仍未授权，不用空字符串冒充模型绕约束。

## 已结案交付波次：B79 / UI80（2026-10-09；以下仅为源码与证据索引）

UI83/85与B84均已审核结案并retain；当前没有运行中的施工或验收Dispatch，旧启动权结束。发布与桌面差额已组合至草稿PR100准确30d3760；最终CI与最新范围以该PR和Linear回执为准。84包准确91a6279，30d3760仅两测试文件差额，包内产品输入相同；实际包/驱动/HTTP与释放证据见/tmp/main-acceptance-20261009/desktop84/report.md。B81的具体验收阻断及首页/重开证据保留于desktop81/report.md，不宣称GUI整体通过。此段为已结案证据；资源/模型暂停已由上方86替代，具名账号外发边界仍保持，不从已结案正文复活START。

用户在最新状态评估后批准“抓紧开始”。本节取代76/77/78的当前施工入口；它们已结案、源码和证据保留，不复活旧Dispatch。基线为准确clean PR99 head `0ed7f38fae0b9451e2adc1ae1302c24f78c4bfe1`（集成树 `/tmp/main-controller-20261008/ui-v3-integration`）；新权威读取本main合同。两条原可见终端复用，不新增窗口/内部Agent；核各自clean，保留旧分支，从本地fetch该候选建立新具名分支，语义REBASE_ACK后直接实施。主控只管文档/审核/机械集成/PR/Linear，不写产品代码。

新head CI37796648239已结束：Frontend/Desktop syntax成功，Backend8 failed/1647 passed/20 skipped。79负责修复这些真实失败；80独立接发布业务。资源/模型/FFmpeg暂停保持，无主机探针、真实模型、媒体生成、用户库/Key/账号会话读取、登录/上传/发送、云身份/真实迁移、签名/Release权。允许本轮隔离假provider/资源/浏览器协议及临时DB测试；自动PR CI另行回读，不以夹具称真实媒体或平台通过。无产品收费，新电脑/Intel后置，main不自动合入。

79已审核结案，准确组合aa1af943ee218a1252a600cf2789d2e9044c1ba0的CI37852806340三项成功（Backend1659 passed/20 skipped，Ruff通过），上一轮8个失败已消除。下一操作入口为正在施工的80及下方81；此CI不代表GUI、真实模型或全产品闭环通过。

### B79：渲染、版本与音频编辑失败修复

目标：解释并修复上述8项失败（bounded render两项、video_ready一项、字幕精确编辑撤销两项、提取原声两项、静音剪辑一项）。沿已有制作/版本/音频链，区分真实实现缺陷与确已违反当前契约的夹具；不得批量松断言、跳过、恢复伪rendered或绕开许可。绑定领域§6.2/§7/§8及产品§9/§14，参考 `/tmp/main-acceptance-20261008/ci78/report.md`；读取最新CI日志，不能只按旧摘要猜原因。

Owned仅 apps/api/app/main.py 中相关渲染有效性/音频预览确认/撤销直接消费者、creation_execution.py、task_audio.py、production.py及必要同路径直接辅助文件（超出这些先报精确路径）；tests/test_creation_execution.py、test_montage_pipeline.py 的8项相关夹具/断言及已有直接用例。不改Web、platform_*、桌面、依赖锁或共享文档。main.py全文件唯一写者是B；80必要router挂载须由主控审核后发准确补丁给B机械应用，不让UI并行写main.py。

最小验证：合法隔离HOME/XDG/DB/output/tmp，复用既有pytest；无媒体的相关用例直接跑，依赖FFmpeg的原8项本地不运行，静态首因和最小替身证据分开，最终真实CI由主控push后回读。不得为这项暂停擅自改媒体测试成永远假的成功；若不能确认首因，报告精确未验及所需条件，不停其他可修项。只做必要改动，不重复全仓库/视觉。交clean候选、净diff、逐失败原因/修正依据、实测命令/未验与自有进程释放到 `/tmp/main-acceptance-20261009/backend79/`；worker不push/写PR或Linear。

### UI80：抖音账号连接到确认发送的真实纵向实现

80为发布迁移纵向负责人。目标是沿当前PublishingWorkspace与platform_browser接本机已安装Chrome的真实独立会话驱动，抖音账号真实状态、当前有效CreationTask成片、用户确认账号/内容、持久发布作业、真实平台响应与重启回读；不只提交协议或演示UI。账号/视频没有证据时如实未连接/未发布，未知远端结果不自动重复发送。用户界面继续当前DESIGN v5.3，不开新视觉重构。

Owned：apps/api/app/platform_browser/、platform_auth/、必要新的platform_publish模块/router及其直接测试；Web PublishingWorkspace.tsx/既有测试、lib/api.ts/types.ts中发布专用增量。使用既有工作区/成员解析、CreationTask/产物读取、JobRun/设备持久任务机制，不造第二套生产事实源或调度框架。必要公开浏览器依赖仅先报告已有依赖可复用情况，获主控精确兼容判断后局部添加；不下载模型/新浏览器。main.py、production.py、settings.py、根锁文件、桌面打包与共享权威不在UI写权；需要的注册入口按现有knowledge_grant_router注入模式给主控精确补丁，审核后由B唯一写者应用。必要新SQLite表可只在自有隔离DB验证，不能打开/迁移用户库；凭据不得沿旧SQLite明文token设计保存，复用系统安全存储/浏览器自身安全会话，不读真实钥匙串。

退役目标：活动UI模拟绑定、localStorage模拟草稿、生产默认MockOAuth/MockDraft、mock远端ID和publish桩。抖音替换必须落实到实际消费者；小红书/视频号暂显示真实尚未接通状态，保留后续目标，不把其mock留作生产回退。只允许测试夹具显式fake。真实账号发送未获授权，代码实现到可执行入口，平台选择器与实际响应不能猜造为已验。方案已核在 `/tmp/main-controller-20261008/platform-publishing-route-review.md`，绑定产品§11、领域§9.3/§10、架构§5.6/§7、认证§4–6。新增需求/权限边界交主控裁决，不转授。

最低验证：假浏览器只验证调用顺序、无确认零上传/发送、陈旧产物/跨空间拒绝、真实结果解析/持久回读与未知结果禁止自动重发；相关Web组件/类型检查及退役消费者不存在检查。若需要查看实际登录前页面/GUI或依赖安装，先给精确动作与路径，由主控在用户授权范围补具名权限；不得因未登录停掉已授权代码实现。无服务/真实浏览器/平台网络/账号操作权，不以组件检查冒称GUI/发布通过。交clean源码、净diff、B所需准确挂载补丁、实际测试与全部未验到 `/tmp/main-acceptance-20261009/publishing80/`，主控审核/集成后另安排具名真实登录和发送。整体发布迁移只在真实目标与旧消费者退役证明完成后接受，源码阶段不关闭WIL45/113/114/115。

80.1 主控已核官方Python Playwright 1.63.0：Python>=3.10，与项目3.11兼容，macOS提供arm64/universal2 wheel，Apache-2.0；官方支持channel=chrome持久独立上下文和PyInstaller。UI80可仅在apps/api/pyproject.toml声明playwright==1.63.0、更新apps/api/uv.lock的必要解算差额；必要的apps/api/pyinstaller.spec只补此真实运行依赖的资源/隐藏导入。根锁、其它依赖版本不顺手升级；核uv sync --locked契约，依赖只下载到新owned staging/venv/cache，不改全局或B复用环境。不要playwright install/下载浏览器，用已装Chrome；本轮不启动浏览器/服务/账号。官方说明与本地兼容核验在主控消息给出，包内driver收集与实际打包仍需后续同候选验证，不能把声明兼容当包通过。

80.2 B79已具名交付clean8fc8427，仅两测试文件；main.py产品blob与0ed7f38相同，其写权在本条收回。UI80成为main.py局部唯一写者，仅发布模块import、router依赖注入、_build_device_components中的platform_publish OwnerRegistration及相关真实状态/产物/成员回调；不修改79渲染/音频消费者。UI80读本条ACK后可以直接落实自身入口，不再等待B应用补丁，主控仍独立审核最终diff。B79源码保留，不整体rebase正在修改的UI分支；主控组合两项源码时再机械保留79测试差额。

80.3 主控核实现有HostResourceProbe._SUPPORTED_KINDS只含material_hash/material_probe_meta，发布浏览器kind会被拒绝。UI80增补唯一写权 apps/api/app/host_resource_probes.py 和既有tests/test_host_resource_probes.py：将platform_publish作为明确的浏览器/网络I/O控制单元接入现有主机CPU/内存/磁盘/IO预算、可信Grant及子进程归属/退出路径，声明真实输入大小和有限操作期限；不冒充material或creation、不跳过资源核验、不增加另一套采样/调度框架。优先复用既有通用准入与进程跟踪；若证据表明通用预算无法涵盖实际浏览器开销，提交准确不变量与最小额外字段，不伪造标定或直接猜生产预算。本轮只能假资源/假driver验证，无真实Host采样、Chrome/FFmpeg/模型执行；未知预算/资源继续明确不可用，源码接通不等于实机准入通过。此新增已是已授权发布纵向的必要接线，不扩展真实账号/外发权；UI只重读80.2/80.3 ACK后继续，B79不受资源实现补充影响。

### UI82：发布候选接入实际应用并修正直接审查缺陷

80已以failed交136ea6393a85604c92ca3a3f82fcfd1f3f825219，只接受源码保留与未接线事实，不接受发布闭环。报告仍采用旧所有权，未落实已授权80.2/80.3；原Dispatch结束，改为本续单。复用原UI终端，保留clean候选建立publishing82分支，不回退0ed7f38、不重做已正确模块。明确唯一写权：80原路径、API main.py的发布import/router注入/产物成员回调/OwnerRegistration/恢复关闭直接消费者、host_resource_probes.py与其既有测试、必要pyinstaller.spec的Playwright资源差额；B81仅桌面，不与这些写权冲突。

完成结果：将80精确main-patch实际实施，修正直接传尚未定义的_platform_publish_task_for_user造成的导入NameError（用现有延迟注入或正确落位）；沿既有HostResourceProbe通用预算接platform_publish的真实size与有限操作deadline，不能用material伪装或猜生产标定；证明真实main入口可导入、router已注册，并用隔离完整repository/DeviceRuntime与合法假资源/浏览器走到持久成功或未知结果。只测试独立模块或apply-check不足以证明接线。

修正两项当前真实缺陷：creator-name昵称不能作为稳定账号标识；任意非空publish-result-id文本不能直接判已发布，应核可解释成功状态和平台ID。无真实平台证据的选择器仍明确候选，不能伪造验证。现有_content_reader支持已验证本地成员令牌，AppShell已有whoami引导、_request_permit_actor支持本地成员；沿这些路径支持当前local包，不伪造用户、不放宽跨空间或启用Supabase。确有契约缺口再报准确条款。

普通业务/授权/输出版本不变。继续遵守80的退役目标、假provider边界、无产品收费和无真实平台/模型/资源运行权限。新报告/tmp/main-acceptance-20261009/publishing82/report.md：准确源码/diff、main实际入口与同一候选最低验证、旧mock消费者不存在、会话/自有进程释放及未验真实平台。旧80源与报告不覆盖。主控负责独立审核和PR，不直接写产品代码。先读本条语义ACK再直接施工；相关有限补充已获授权，不再按80旧正文等主控写main.py。

### B84：最新发布组合增量入包

83已交clean1b73be1并审阅，80/82/83与81两项桌面差额机械组合clean91a6279be78dd4557b0260c0962b2e25c0a9a1db（30改动文件逐blob相同）。81包缺这次发布与Playwright驱动，本单只补这一必要差额，不重复81的首页/重开和已阻断GUI矩阵。复用原B终端，保留81分支，fetch准确组合建立具名分支；在/tmp/main-acceptance-20261009/desktop84/冻结构建本机arm64 local.app。复用已有公开缓存，只有Playwright与其锁中必要依赖及PyInstaller缺失时可下载到新owned缓存/venv；不修改旧环境/全局，不下载浏览器或模型，不发布Release/签名。

无产品源码写权；若真实构建失败报具体首因/所需路径，不绕过collect_all或偷偷移除驱动。复核包内真实Playwright资源/驱动可用及候选身份，启动包内API/Web一次，仅HTTP健康与/api/publishing/platforms实际路由回读；不重复native点击/窗口/截图/主题测试。无Chrome会话、平台网络/账号、资源容量采样、模型/FFmpeg/Pi、Key/用户库或Supabase权限。隔离与 owned进程释放沿81具名方法，保留原证据。生成.app即可，不再生成DMG/ZIP或重开本单。交报告/包身份/实际API回读/所有自有进程释放与准确未验。83线程保留，不在构建期间改候选。

### B81：本机组合包启动与关闭重开（已结案，运行权结束）

79源码8fc8427已经主控审核并机械集成至clean PR99候选aa1af943ee218a1252a600cf2789d2e9044c1ba0，79写权结束。81复用原B终端，保留旧分支，从该准确候选建立具名desktop81分支，读取桌面README/RELEASE相关本机通道和本条后语义ACK直接执行。80发布代码仍施工；81只证明当前组合包启动，不能冒称已包含80或三平台通过。

目标：在新的owned `/tmp/main-acceptance-20261009/desktop81/` 冻结候选，沿UI77已验证构建路径生成本机arm64 `.app`，真正启动包内API/Web/Electron，查看首页/设置/任务入口一次，关闭后证明其子进程退出，再用同一隔离配置重开。复用77的公开依赖缓存/构建方法，不能复用其私密配置或以旧包替代当前候选；只构建arm64本机local诊断包，不签名/公证/上传/Release，不覆盖Applications或用户原包。可先生成.app，成功后再生成分发档案，不为本轮重复新电脑/尺寸/主题矩阵。

启动前必须证明Electron userData、HOME/XDG、dotenv、DB/library/output/models/agent/log/temp全部位于owned根，明确空Key/无云身份，不能假定HOME或Chrome参数已隔离Electron路径。先查现有可用隔离入口；确有隔离或实际启动缺陷时，81唯一写权限于apps/desktop/main.js、preload.js、scripts/内必要直接消费者及其现有直接检查；新增文件或跨到API/Web/pyinstaller/依赖锁须报准确首因由主控补边界。不得为隔离新增大型启动框架或通用兼容层。80独占API main.py和打包spec/锁，81不碰；所有构建发生在owned冻结副本。

本条新增权限仅具名包构建、拥有的Electron/API/Web启动、当前隔离页的必要GUI操作与截图、正常退出和关闭重开；允许读自有进程/端口归属，不允许主机容量探针、模型/TTS/口型/FFmpeg/Pi真实调用、真实Key/账号/用户DB/媒体读取、平台浏览器/登录/上传/发送。没有任务时启动若自动采样或运行媒体，应停止该owned启动并报告实际首因，不能偷偷取消暂停或改业务保护。GUI优先自己使用当前Orca computer工具；工具阻断限具体可见项，不重复让用户拖窗口/按键，不用HTTP或测试替代截图结论。

交准确候选和必要源码diff、实际.app/档案路径/hash/包内身份、启动日志/健康与截图、关闭重开及自有PID/PGID释放证据、失败首因和未验到desktop81/report.md。真实制作与最新发布仍另验；worker不push/写PR/Linear/共享权威。主控审核后再决定最终发布组合的必要增量重建。

## 已结案波次：B76 / UI77

本波次B76/UI77/UI78已具名交付并由主控审核结案，三项Task均succeeded；两条原可见终端retain，旧Dispatch与START不提供新增写入/运行权限。人物源码68f687daeff7e56041a1e88d24a5143fc311156c、包源码1b1d78920e92f16c80b9a25cf2530ee75548a5f6、字幕测试修正f5c2d062a59c57480f8cbc96ab9ce6ee3d725011已机械组合，13个人物路径及两项UI差额blob相同；这里只接受具名源码/构建结果，不接受真实模型/GUI/完整产品完成。UI77包不包含B76，后续须重建。PR99旧head的后端15失败有7项测试接口已定向修正，剩余8项未解决，新组合远端CI另行回读。下一具名实施优先处理这8项，再组合包启动/全旅程与真实发布接线；当前没有启动下一任务，资源/模型/账号外发暂停与不自动合main继续有效。

用户于2026-10-08最新批准“抓紧开始”，实施上轮已解释的集成、人物重生成接线、本机包准备及三平台路线核实。两条原可见线程复用；主控不编写产品代码、不新建窗口/内部Agent，不自动合main。共同产品代码基线为clean3db903441af97f359ccf2180f8f2fb4944bd0542（/tmp/main-controller-20261008/ui-v3-integration），当前main共享权威单独读取，不整体merge旧工作树或覆盖旧分支。先核各自clean，保留历史分支，从本地fetch该完整候选建立新具名分支；语义REBASE_ACK后直接完成，不等待第二次START。

本波次共享不变量：资源暂停仍有效，无主机资源探针、真实模型/TTS/口型/FFmpeg执行；测试只用完整隔离的合法假provider与资源快照，不冒称真实媒体。产品无费用/充值/扣费工作。定时执行OFF，不访问真实用户DB/Key、账号登录/发送、云身份、设备注册或迁移；不下载模型。worker无push/PR/Linear/共享权威写权。具名本地构建仅UI77的新权限，不能延伸为模型/GUI/安装/外发授权。

### B76：WIL-141公开人物编辑未命中缓存时重生成

目标：从现有精确编辑及自然语言提案应用入口，针对当前任务/输入版本/声音段落，把ChangePersonSegment、RegeneratePersonRender未命中可复用产物的请求接入唯一设备持久队列，获当前授权与专属预算后才调用现有人物生成函数，并仅更新目标镜头的不可变版本；排队/失败/取消不伪装完成。主控已读main.py约9007–9177，未命中分支调用_generate_creation_task_person_render_record不传grant_context，正向连续制作约15521已具许可入口；不得删除既有guard或把缺口只改成409测试。

绑定当前main架构§3.12/§5.2/§5.3、认证§5和领域§6.2/§7/§8的目标章节；沿已有JobRun/TaskEvent/device_runtime/CreationExecution，不新建队列/并行草稿系统或改默认人物比例。B为这一局部用户结果负责人。owned：apps/api/app/main.py上述公开确认/精确编辑及队列注册直接接线、creation_execution.py、device_runtime.py、device_scheduler.py、production.py及schemas.py的必要直接消费者；apps/api/tests内现有对应montage/person/creation/device专项；必要Web反馈只apps/web/src/lib/api.ts、types.ts、components/CreationTaskWorkspace.tsx及其既有直接测试。不改HomeCard/AppShell/CSS/桌面/共享文档、根依赖锁或新增数据库迁移；必要共享接口变化先回报精确差额，主控补边界。仅实际需要的文件改动，其他owned不意味着必须修改。

最小验收：公开入口真正提交持久请求且未许可前provider零调用；队列取得合法专属Grant后假provider完成对应人物片段、版本/映射更新，其他配音/字幕/镜头保持；原真实同源缓存复用不退化；陈旧版本、许可撤销、取消/失败不提交伪成功，重复应用不重复生成。复用A53/A60/A63未变专项，只跑受影响直接用例和必要类型检查，不跑全仓库、不添加像素镜像/防御fallback。可以复用/tmp/manta-wil108-20261007/venv的pytest，但完整HOME/XDG/config/DB/tmp/output隔离；实际Host/进程探针替身只放测试，不改生产判断。证据/tmp/main-acceptance-20261008/person76/；交准确clean源码、净diff、实测命令、当前范围与未验真实模型/GUI。无服务/GUI/重模型或媒体执行权。

76.1 主控接受B回报的直接响应差额：人物编辑缓存miss的confirm/exact沿现有执行入口返回HTTP202、queued和已有可信job/request引用；原同步applied响应及合法缓存hit保持。排队不能把proposal标applied、创建目标TimelineVersion或回复已生成；现有Web直接消费者显示已排队并读取该作业真实完成/失败，再更新目标镜头与版本。对应schemas/lib/api/types/CreationTaskWorkspace已在B76所有权内，允许必要响应类型与完成刷新差额；不修改无关普通字段语义、不造另一个状态轮询系统。设备完成、版本提交与请求幂等使用既有事务入口，stale/revoke/cancel拒绝伪成功。该补充只绑定B76，不影响UI77构建目标，无需后者停工或重跑。

76.2 主控核实 HomeCommandComposer.tsx 的 confirmTaskPreview 也是当前确认接口消费者（原无条件回复修改已完成并开启撤销）。B76 所有权扩展到 apps/web/src/components/HomeCommandComposer.tsx 及其既有 __tests__/HomeCommandComposer.test.tsx，仅补 queued 响应显示已排队/等待真实完成、不给本次尚未提交版本开启撤销；同步 applied 行为保持。复用已有 onTaskUpdated 与现有作业视图，不在创作助手新增轮询系统、不修改视觉/共享底座。该必要接口迁移与 B76 同单交付，不另派UI占用同文件；UI77打包不受影响。

76.3 主控定向审核发现 granted_subprocess.py 的 model_unit 仍只识别 narration:/person_render:；新 person_edit: 必须继承同一真实资源探针与运行预算拒绝边界，不能因新unit名称跳过。B76所有权仅再补 apps/api/app/granted_subprocess.py 的该前缀识别和既有 apps/api/tests/test_host_resource_probes.py 中 test_person_model_child_rejects_non_host_probe_before_exec 的直接参数覆盖。资源预算事实本身已有按creation_person_render正确识别，无须修改HostResourceProbe或新增保护体系；检查在拒绝分支中不得启动真实子进程/探针。原模型暂停和其他所有权不变。

76.4 主控接受同一动作失败/取消后显式重新预览的重试需求：现有repair_action明确让用户修复后重预览，不能被终态幂等键永久挡住。B76沿已有JobRun/事件/设备请求实现新proposal的一次新attempt；旧终态proposal不得复活，活跃的同task/member/base/operation重复confirm或exact仍复用同一作业、不重复生成。只补_submit_person_edit及必要既有仓储事务/直接测试，不新增接口、表、迁移或后台自动重试。明确区分旧提案重复与用户新提案重试；活跃去重/创建使用既有事务边界，避免并发检查后同时创建两作业。至少用原合法假provider证明失败→新预览→排队成功与活跃重复复用；原模型/FFmpeg/探针暂停保持。

### UI77：WIL-169本机arm64包准备与有界构建

目标：把已有候选准备成可在用户本机运行的arm64本地测试.app及DMG/ZIP（构建条件允许时），保留现有Pi/Node/Python/FFmpeg/外部模型路径发现与Key安全保存契约，不把模型权重或任何用户配置/Key打进包。只做本机local身份受控诊断；不以它代替独立测试者supabase交付。当前API/Web/桌面原入口与业务契约保留，不新增依赖管理框架或调整视觉。

绑定当前main产品§1.3、desktop README/RELEASE的本机通道及架构§2.1/§2.5；读当前实际脚本和包配置。owned：apps/desktop/main.js、preload.js、device-identity.js、package.json、scripts/中实际构建/独立资源/身份/生命周期消费者与现有直接检查，apps/api/pyinstaller.spec；必要Next standalone配置仅apps/web/next.config文件。UI不写API业务/B76文件、Web页面/视觉、根依赖清单/锁、workflow或共享文档。先核已有依赖并复用；已装pytest环境没有PyInstaller，不按可构建猜测。缺构建工具时可在新owned staging/venv按现有锁或确定包版本安装必要公开构建依赖，允许公开包下载；不能修改全局环境、下载模型或执行未知安装脚本，不为此新建产品依赖管理。

构建和测试目录只/tmp/main-acceptance-20261008/desktop77/，源码分支外另archive准确candidate排除dotenv并核blob；源码修改时先提交准确clean差额，再由该候选archive构建。依赖可只读复用，Next/PyInstaller/Electron缓存、输出、HOME/XDG/config/tmp均为新根，secret/用户DB不进入构建。允许本地arm64目录包及DMG/ZIP有界构建、必要ad-hoc签名与包内容/架构/摘要核验，不使用Apple证书、公证、上传、Release、覆盖/Applications或实际启动Electron/API/Web；本波次不触发完整GUI/模型验收。记构建PID/PGID、失败首因与可恢复步骤，必要构建命令60分钟绝对上限，结束释放自有构建组；不为打包运行媒体或Host资源探针。

先对实际缺口做最小接线，已有正确代码保留；不能只交计划作为完成。交源码SHA/净diff（无差额就明确无源码改动）、实际包路径/SHA256/候选输入摘要/包内依赖边界及直接检查、未验启动与完整制作；不可构建须给精确命令/失败/缺依赖，不冒称安装可用。当前共同候选不含B76并行差额，标清后续机械组合需要重建包；本轮已验证构建步骤复用，不要求再重跑视觉矩阵。

### UI78：PR99后端失败分类与字幕测试接口适配

UI77已交具名源码1b1d78920e92f16c80b9a25cf2530ee75548a5f6及arm64包；新单只复用原UI可见终端，保留该分支和产物。以该准确clean候选建立codex/ci78-subtitle-contract-20261008，读本条语义REBASE_ACK后直接完成。B76继续独占main.py/creation_execution.py及montage/person/creation直接测试，UI78不得在这些文件写入；模型/FFmpeg/主机探针及GUI暂停保持。

目标是消除PR99 CI37791344587已经真实出现的7个字幕时间同步测试替身签名失败，并按当前契约分类其余8项，不为测试改变产品行为。唯一写入文件apps/api/tests/test_subtitle_time_sync.py：TaskAudio.validate的测试替身接受真实draft_only关键字，保留原字幕内容/中英关联/预览无写入/撤销/错误与持久性断言，不删测试、不跳过、不松断言、不改生产兜底。剩余创建执行3项、字幕撤销2项及原声/静音3项仅定向只读源码/失败日志，报告实际不变量、测试数据是否过时、必要修复文件，不能笼统称旧测试。确需产品修改交具体首因给主控，避免与B76写同文件。

只跑该现有字幕测试文件，完整隔离HOME/XDG/config/DB/tmp/output，合法现有provider/探针替身；任何用例触发真实媒体/FFmpeg就不得执行该项，准确保留未验。可复用已有pytest依赖，不安装新依赖、不启动服务/模型/GUI或资源探针。报告/tmp/main-acceptance-20261008/ci78/；交准确clean差额、必要检查命令、15项分类与未验媒体范围。无push/PR/Linear/共享文档权，不重跑全仓库/视觉/打包。这是处理真实CI首因，不扩大防御性程序或补无依据的测试。

### 主控：集成与三平台接入核实

主控审核76/77并机械组合，准备最新UI增量PR及交接，不直接把未验范围写成通过或自动合并。并行只读核抖音/小红书/视频号当前官方接入路线、账号/应用条件和本地浏览器可支持入口，结合B71 release-gaps.md直接缺口，产出可以具名施工的一平台纵向任务及平台差异。当前不登录账号、获取会话/Key、上传素材/草稿、发视频、触发GitHub构建或Release；实际发布仍须用户确认具名账号和内容。模型恢复另需用户明确撤销暂停，不因本波次获准自动恢复。

## 当前增量：UI70 / B71

### UI70：柔光与所有应用级右侧同壳

用户最新交接为本轮授权，迁移纵向负责人UI70，整体验收和退役结论由主控负责。原UI可见终端复用，不建窗口或内部子Agent。以已交634cb936普通草稿源码叠加主控v5.3权威准确commit为基线，主控给完整SHA；保留旧分支与已审差额，不整体merge旧分支、不重做页面。先读新包 `docs/给编程线程的完整增量改动说明_20261008.md`，参考index及相关展开证据，再读DESIGN §3/§4/§5.10/§7，语义REBASE_ACK后直接完成已授权代码目标。

owned：HomeCardDialog.tsx、SideCards.module.css、AppShell.tsx及CSS、CommandPalette.tsx及CSS、globals.css、modern-overrides.css，以及实际浮层调用直接消费者 HomeCommandComposer.tsx、HelpPanel.tsx、KnowledgeWorkspace.tsx、LlmProviderFields.tsx、PeopleAndVoiceWorkspace.tsx、PublishingWorkspace.tsx、HomeWorkspace.tsx、MaterialLibraryWorkspace.tsx、CreationTaskWorkspace.tsx、VideoEditor.tsx与对应直接CSS/现有测试。页面文件仅改浮层职责/父关联/长阅读入口/返回焦点，Publishing仅将旧QR应用弹窗改共用壳，不改账号授权能力。CreationTaskWorkspace/VideoEditor保留69草稿和比例逻辑，仅必要调用边界调整；不改API/lib业务或持久化。其他实际非同壳消费者先报精确文件与必要差额，主控补边界。

先盘点当前真实调用，将已符合/需改/不存在记录于/tmp/main-acceptance-20261008/ui70/短报告。复用现有公共外壳，一处共同工作区尺寸/根子定位，应用级详情/配置/确认/导航搜索/长阅读同360×560/14圆角/56标题/64底栏，短菜单约240不套同壳。无父即根，有父才左12下32；实际工作区标题/安全区测量，保留父挂载状态，窗口变化重算且各层共同尺寸；不照搬208常量、内容高度、中央回退或新大型抽象。保留确认可靠模态/顶层Esc/防点击穿透/取消/可见回焦，删除初始取消；不得拿同样div消除职责。全部卡浅暗同token柔边；栏渐隐边界/对话输入仅质感不改宽度位置；静态聚焦、已有真实事件可有限反馈，减少动态关闭动画。

退役目标：活动320配置壳、480/580中央应用弹窗、内容决定应用卡高、贴边抽屉、长引用/共享冒充240短菜单、仍调用的独立QR中央壳、普通编辑额外确认链及旧弱文字token。只删已被替代且仍活动的局部源码/错误断言，不删用户数据/历史包。允许施工中临时共存，截止本任务交付；未消除项不能称全量迁移完成。回退仅具名源码，不回滚用户数据或自动恢复已退役消费者。

完成同一源码的直接消费者最小测试、类型与diff检查；不写CSS像素镜像、不跑无关全量、重复旧矩阵，不加仅为测试的生产fallback。不得启动服务/GUI/模型/探针/FFmpeg、下载/安装、费用、真实库/Key、共享文档或远端写入。交准确clean候选、净diff、逐页实际消费者和不存在证据；未验GUI明确待71，不以原型/测试代替生产通过。

70.1 同批69审阅实证差额：主控发现 `_creation_render_is_valid` 仅在可选 `render_input_digest` 存在时检查原输入，新测试注册任意fixture job/transform及字节而无sealed输入仍断言undo恢复output_ready，违反已绑定“缺失输入依赖不恢复有效”规则。允许UI70追加 `apps/api/app/main.py` 仅该函数/undo直接调用所需最小差额及 `apps/api/tests/test_montage_pipeline.py` 既有v3撤销用例。撤销恢复必须复用既有render_input_*及实际原输入版本、job/transform/artifact、当前授权，不以文件存在/记录相互相同代替绑定；缺完整事实恢复草稿但成片需生成。沿现有真实渲染输入计算复用必要小函数可行，不新建第二digest事实源/安全层、不触发媒体或模板渲染。只测缺封存输入、有效版本恢复、真实依赖改变拒绝恢复三个已有契约场景；不跑全媒体套件。其他API文件无写权，此条不改变产品权威。

### UI72：70交付未达到完整目标的接续

70源码899b23ab已交clean并保留，主控不认可整体迁移完成：标准config无footer时仍没有64底栏；共同left取包含中栏的desktop-workspace；modal/navigation zIndex和遮罩仍固定；AppShell确认仍关闭quick父卡且同级挂载，全球搜索无真实父关联；70.1人物当前授权依赖复用点尚未落实。旧报告11个最小Web通过，仍有一次扩展运行18失败未分类，不能宣称CI就绪。原UI可见终端从clean899b23ab接续新分支，owned沿70/70.1，不扩大业务/媒体权，不重做保留成果。主控会提供已核可用的原pytest运行时 `/tmp/manta-wil108-20261007/venv/bin/python`；不再按“无pytest”阻断或安装。目标是一次交完整差额和相关检查，不每项单独交付。只对已观察真实缺口编码，现有直接测试中保护退役旧规则的断言按新行为更新，真实失败必须修，不把全部18项猜为既有失败。复用未改正证；无全仓库测试、服务GUI/模型/探针/网络/收费/共享权威写权。证据续/tmp/main-acceptance-20261008/ui70/，加接续说明保留旧report原文。准确净源码交后由主控审核，再B71同候选GUI。

### UI73：72主控审核未接受的差额

72源码d85a5d321a5bfd35c9133ea9130b4e1f9e37c06e已交clean并保留，其几何、搜索真实target父关联和人物undo复核不重做。但HomeCardDialog把所有有父confirmation统一降为非模态，修改测试撤掉aria-modal；DESIGN §4.2要求删除保留模态，不能按有没有parent降级。普通自然语言“选择应用到草稿”属于方案操作，不应继续用confirmation伪装危险确认。原UI可见终端新Dispatch沿clean d85接续：owned HomeCardDialog、CreationTaskWorkspace的EditPreviewCard/localConfirmation职责映射及其既有直接测试、AppShell/SideCards直接测试；KeyboardShortcuts仅真实搜索target回调已有差额保留，API已过且本单不再改。危险确认仍用真实modal/confirmation、aria-modal/顶层Tab约束/取消/遮罩，父层保持；普通提案用config方案卡单次应用，不能加第二次确认。只改实际职责，不造fee字段、收费或权限逻辑，不改已正确普通直接草稿。

72的CreationTaskWorkspace全文件18失败只证明和899父候选相同，父候选本身不是通过基线，不能用于放行。主控已读失败JSON：大部分仍找旧“预览字幕修改/预览裁剪/预览转场/确认修改”或期待preview API；另有比例DTO、录音和重出检查断言。按v5.3既有语义更新这些现有用例/必要mock，保留无自动生成、持久化刷新、撤销/重做、版本失效、删除取消/确认和真实重出预检的实质覆盖；不得删/skip测试或加生产fallback。如果录音确为必要表单一次应用，更新旧“只提案”断言并保留替换范围/失败/版本检查；若真实动作错误先精确指出，不按所有旧断言自动回退新设计。新增zIndex90/111、几何数值镜像断言退役，采用焦点/关闭顺序/父草稿/作用范围功能证据。

复用72 API2项、类型等未变证据；只跑上述实际受影响卡片/任务文件，文件通过后不再重复全仓库或GUI矩阵。新续报/tmp/main-acceptance-20261008/ui70/continuation73.md记录准确clean源码及仍未验事实，整项交付后检查待收消息。无服务/GUI/模型/媒体/主机探针/收费/网络安装/真实库/共享权威或远端写权。B71继续等准确候选，不启动已知确认职责错误的72页面；不要求用户协助普通GUI。

### UI74：B71真实普通编辑失败的定向修复

B71冻结db5ff93937d74a590618387237d8fc787638b1ab实际GUI把画幅16:9改1:1，POST edits/exact因“valid 字幕轨必须有条目、产物和变换证据”失败；合法初始unavailable字幕、当前V5与16:9未改，卡片仍显示1:1。主控已读square-save-result.png与square-after-await.json，源码定位main.py `_clone_subtitle_track` 无条件status=valid且丢失failure/impact/repair字段，ChangeCanvas及普通编辑会调用它；仓储拒绝有效，无权削弱。此前卡片重开已刷新恢复，不据此扩大74。

原UI可见终端保留clean84a3b76分支，从主控组合db5ff939本地fetch建新分支。owned：apps/api/app/main.py仅上述复制/实际普通编辑直接调用；apps/api/tests/test_montage_pipeline.py直接用例；apps/web/src/components/VideoEditor.tsx的CanvasControls、CreationTaskWorkspace.tsx的已有直接编辑结果反馈及CreationTaskWorkspace.test.tsx。其他文件仅报最小必要差额，不自行扩写。沿当前产品/领域版本和DESIGN§4普通直接草稿语义，复制不可用/失败轨保持真实状态和原因、影响、修复动作，不冒称生成成功；真实新生成轨按已有成功产物契约处理。普通画幅/镜头编辑应能提交新持久版本，GET回读、撤销/重做仍保留不可用轨与真实输出失效；仓储valid校验保持。画幅提交失败时控件与保存状态不能冒充已保存新值，沿既有反馈和任务事实源解决，不另建草稿系统或通用回退。

只做一个API相关端到端专项覆盖合法unavailable/failed字幕的普通ChangeCanvas保存→回读→undo/redo及既有valid轨控制、一个实际Web请求失败反馈专项；类型/diff必要检查。不要重复110项/全仓库、CSS像素或无关失败矩阵。现有pytest运行时/tmp/manta-wil108-20261007/venv/bin/python复用，不安装。证据只/tmp/main-acceptance-20261008/ui74/。无服务/GUI/模型/媒体/FFmpeg/探针/联网安装/真实库/Key/收费/共享文档/远端权；不写B71正在运行的archive或DB，不重启其lease。交准确clean源码、净diff及最小检查，主控审后另绑定受影响GUI补验，不从此获取START。

### B71：最新候选集中相关验收

旧B66仅prepare且已fence，无服务/GUI运行，保留/tmp/main-acceptance-20261008/ui66/准备资料。原B可见终端复用，新证据/tmp/main-acceptance-20261008/ui71/；不重造未影响合法色卡或旧库，不写产品代码。读DESIGN v5.3相关几何/编辑/验收与实际领域版本，确认新验收取代66旧几何；语义ACK后先核准备输入，不启动服务。以ui66合法合成隔离准备为来源，所有新写仅ui71，原库只读；保持HOME/XDG/DB/config/library/output/tmp/cache/models/agent隔离、0600合成凭据、无真实Key/Pi离线/scheduled OFF。不扫描个人数据/模型，不造真实生成结果/费用/质量成功。

待主控绑定70+69准确组合SHA、具名服务/租约/数据及page命令后再开启一次集中GUI。本阶段只准备/查静态真实入口，不用旧candidate启动，不安装或运行媒体。相关旅程覆盖1440×900/1280×720/1024×720右侧共同尺寸与根/子/确认搜索、父状态保留、顶层关闭/取消回焦、浅暗/减少动态、普通字段持久化→刷新→撤销/重做→成片/检查版本，真实任务比例和共尺。复用不受影响证据，缺失真实音频/模型/原生项明确未验；不准备或验收费卡。普通GUI由Agent自操，不向用户逐步索要缩放/按键。

### B75：普通画幅保存修复的定向补验

B71已以failed结算：报告实际10 PASS/1 FAIL/6 UNVERIFIED，画幅409为真实FAIL。自有三进程组、4579/3179、page/profile已释放并回读；冻结候选及旧库不变。UI74来源30dbac7213fee38bc9eb9c00bb509bbbd92dc716已审，API4/Web2及类型检查通过；机械集成clean候选3db903441af97f359ccf2180f8f2fb4944bd0542，五个改动文件blob与来源一致。仅源码修复已接受，真实保存补验未通过；复用71未受影响的几何、主题、搜索证据，不重跑整轮。

原B可见终端新Dispatch复用，只写/tmp/main-acceptance-20261008/ui75/。从/tmp/main-controller-20261008/ui-v3-integration准确候选archive排除dotenv并核源码blob；只读ui71/reuse-input.json及直接启动资料，用SQLite mode=ro/uri=True备份其合法V5合成库、复制现有色卡和0600合成owner token，旧库和证据保留。不新增成功产物、字幕valid状态或输出封存；新HOME/XDG/config/DB/library/output/cache/tmp/models/agent隔离，Key空、Pi离线、scheduled OFF、资源门禁不变。无安装、主机探针、模型、媒体生成、费用、真实库/Key/账号、发布、Electron打包或远端权。

准确候选/源码与旧输入核验、语义REBASE_ACK、4579/3179空闲及自有隔离page方法确认后条件START。一次自有API4579/Web3179，仅loopback；首服务起15分钟绝对lease，不续租。登记PID/PGID/starttoken和deadline；仅下述已计划失败场景可停止自己的API并恢复一次，恢复仍受原deadline约束，不能重开Web/租约或对其他服务操作。自己的隔离profile/page，每条GUI明确page；真实控件、键盘、截图，eval只读，不注入DOM/CSS/事件/响应，不操作个人浏览器或请求用户拖窗按键。

只在1280×720补验ui41-task：画幅16:9→1:1直接保存，无多余确认/自动生成；GET及只读库证明新持久版本和unavailable字幕原原因/影响/修复保留，刷新仍1:1，撤销16:9、重做1:1，output_ready仍false。随后停止自有API一次，经GUI改9:16取得真实请求失败，控件恢复已保存1:1、错误可见且持久版本不变；恢复自有API一次后GUI重试成功并刷新回读。若页面离线阻止发送则如实记录该场景未验证，不伪造响应。不启动生成/配音/口型/检查，不把合成草稿证明写成真实成片通过。准备后业务修改仅GUI；HTTP GET、SQLite只读仅辅助证据。

结束/到期释放全部登记自有组/端口及page/profile并回读，证明旧库hash保持和工作树clean。短report.md列同一候选实际PASS/FAIL/UNVERIFIED、PNG、版本与失败反馈、release；真实新缺陷只提交首因证据，无产品修复权。不追加71未验长滚动/三层模态/全尺寸矩阵，不关闭WIL205或整个制作/安装/发布。74及71旧操作权结束。

## 当前v3增量：64/65

用户最新v3交接为本次授权依据。共同产品代码971f77a（PR98最终三类CI成功），叠加本次唯一权威提交后由主控提供准确新基线。旧普通参数“预览影响→确认→应用”规则失效；两线程先读原包增量交接、组件规范，再读DESIGN §3–5、产品§2.3/§9及领域§6–7，语义REBASE_ACK后直接完成所辖定向调整。不改已确认三栏、品牌或领域枚举，不复制示例数据，不整合旧混合分支。分支/脏树保留；新候选从主控准确对象建分支。

UI64负责可独立交付的可读文字/清楚交互态、七类真实状态形状、首页分组、列表显示入口、任务实际比例与共用真实时间尺。owned：globals.css、modern-overrides.css、AppShell.tsx、HomeWorkspace.tsx及CSS、VideoEditor.tsx及CSS、MaterialLibraryWorkspace.tsx、apps/web/src/app/knowledge/KnowledgeWorkspace.tsx及它们直接CSS/既有测试；其他文件先报告真实必要差额。保留360/320/240四角浮卡底座、父状态与全部已有操作；VideoEditor内VideoClipQuickEditor及普通比例控件直接提交既有onPreview回调（A65将其解释为应用草稿），不新增确认链。不写CreationTaskWorkspace.tsx、精细编辑组件、lib、API或共享权威。真实已有能力缺失明确说明，不新增虚假分类/波形/状态或列表设置。

64.1实际状态消费者补充：允许UI64追加PublishingWorkspace.tsx仅现有任务行状态图标映射/导入及必要直接CSS，不改发布能力、账号、存储或API，不制造真实发布通过。已有waiting_for_user等实际状态按DESIGN七类形状表达；只补必要显示，不重测整条发布链路。

A65为“普通字段或结构命令→同一CreationTask版本化草稿→撤销/重做→产物依赖有效性”的纵向负责人。owned：CreationTaskWorkspace.tsx、ScriptSectionEditor.tsx、AudioTimelineEditor.tsx、GraphicOverlayEditor.tsx、既有精确编辑/字幕表单直接消费者、lib/api.ts/types.ts中确有必要的直接编辑调用、main.py及production.py中当前编辑/撤销/重做/有效性直接路径、对应既有测试。精确字段在选择/完成输入/结束拖动时直接持久化，不提交未完成值；复用原命令校验、版本和依赖，不新建draft事实源或扫描/兜底/兼容层。自然语言提案一键“应用到草稿”，普通编辑不自动生成；删除保留确认，普通生成/Pi不增加付费确认，系统授权及阶段动作不套额外产品确认。其他生成/权限/队列/模型/发布不扩权；API确认命名可保留为内部提交，不以改名重建接口。涉及实际媒体预览/生成须与草稿提交分开，模型资源暂停不解除。不得把聊天状态或未持久化React快照称为草稿完成。

65.1直接接口绑定：VideoClipQuickEditor实在UI-owned VideoEditor.tsx，UI64同时改其字段/结构动作提交时机，A65不跨文件写入。真实三轨共尺需A-owned AudioTimelineTracks配合：允许既有组件新增sharedTimeScale可选属性，CreationTaskWorkspace内嵌传true；嵌入时音频跟随父0–真实duration的同一lane和标签宽，不使用独立zoom/滚动尺度，其他音频工作区既有独立缩放保留。UI64负责共同CSS及VideoEditor视频/字幕坐标；A65负责音轨直接消费者，双方仅重绑此差额。不得隐藏整条音轨/旧操作或只靠CSS假称同步。

65.2必要关联变速差额：主控核原TimelineAudioClip duration固定源区间、linked_timeline.retime_clip强制先物化新音频ref，确与普通草稿不生成冲突。允许A65追加audio_timeline.py/linked_timeline.py/task_audio.py实际音频模型/重定时/校验与mix消费者，以及其既有直接测试；仅让草稿保存原授权source_ref/源区间与速度/时间变换，明确试听/渲染时沿既有许可消费者执行。source、timeline和包络时钟一致，不造待生成音频引用、不静默降成仅画面、不新建第二队列/事实源或数据库迁移。lib types直接契约归A。仍无真实媒体执行权；代码路径/最小合法合成数据证明与真实音质输出未验分开。领域§6.3已绑定新草稿表示，读此条语义ACK即可继续。

本单退役目标：普通参数确认链、贴边详情、颜色独自表意及等宽时间段；已有合法底座和未影响成果复用。主控为整体v3验收/退役负责人，64/65各报所辖不存在证据，不凭层级分项关闭整体任务。实施期只做最小已有消费者与真实问题复现，禁止为CSS像素写镜像测试，不重复旧全量/GUI。证据64/65分别写/tmp/main-acceptance-20261008/ui64/及draft65/，短报告列commit、净差额、必要结果和真实剩余。无服务/GUI/个人浏览器、真实库/Key、模型、主机探针、网络安装或收费权；完成后交付准确源码并收回操作权。组合后B只做受影响页面、实际比例、编辑持久化/撤销/失效与删除边界的必要真实可见验收，另绑定准确候选，不重跑全部UI。

### UI67：保留64交付后的定向收口

64源码81b043a67a5f1c32901e4c07e04b1818c787f5cc已审阅、retain，旧Dispatch结束；其视觉、首页、集合与时间组织复用，不宣布整体v3完成。原UI可见终端从该clean源码新分支接续，不重启重构。owned仅VideoEditor.tsx/VideoEditor.module.css、PublishingWorkspace.tsx现有状态图标与既有直接测试；不写CreationTaskWorkspace.tsx或其测试。CreationTaskWorkspace.test.tsx的64比例/区间测试差额保留，后续唯一写者归A65。

完成现有VideoClipInspector普通字段直接提交：合法源时间、画面/关联倍速、裁切、适配等在选择或输入完成时应用草稿，移除普通预览按钮；切开/复制命令直接提交、删除仍确认。CanvasControls不得以每次版本变化key卸载父卡，保留打开/展开/滚动并同步真实当前值。预览必须按任务比例同时适配实际宽高限制，保持首屏预览/三轨；单独aspectRatio style不能代替实际几何。QuickEditor现lastApplied永久拦截同值失败重试须做最小修正，依既有busy/回调完成事实，不新建兼容或通用防护。补64.1已经批准的Publishing实际状态形状，仅显示不宣称发布接通。

先读DESIGN v5.2普通编辑/父卡/比例条款与此段语义ACK；同一授权目标内直接完成，不等待新START。保留64全部有效成果和用户数据，无服务/GUI/模型/探针/网络/收费/共享文档/远端写权。仅必要直接消费者检查及类型/diff检查，复用64未影响结果，不重跑134项或全矩阵。证据/tmp/main-acceptance-20261008/ui67/；准确clean源码及剩余事实交付，由主控组合A65后B66验证。

### UI68：已交67的最后两项差额

67源码2dd9f50已retain，旧操作权结束；先前消息未在结案前消费。原UI终端从clean 2dd9新分支，仅VideoEditor.tsx的CanvasControls真实fit_mode同步及PublishingWorkspace.test.tsx删除本次20行Lucide类名镜像测试。ratio变化不清空已应用适配；撤销/重做读真实clips，父卡挂载/滚动保持。沿用67用户授权，不重构或扩大范围，不重跑15项/GUI/类型全量；既有直接消费者最小核验与diffcheck即可，无服务/模型/网络/共享权威权。证据复用ui67，交准确净提交，A65/B66继续原任务不暂停。

### UI69：额度中断后的原目标接续

A65终端明确usage limit，原Dispatch ctx_675e339c161b已abandon/fence，原manta14文件脏树完整保留；patch SHA256 391627b5b51c3f39c680d6761a4b9b64c066dd4b6099b254fcc04a8d843eb6a1。UI68源码24440e4已retain。现唯一实施者转原UI可见终端，新分支从主控v3组合准确52d05b7接续，不建新窗口。机械应用/tmp/main-controller-20261008/draft65-preserved.patch，相关冲突只按已批准语义解决，保留UI64/67/68成果。目标/操作边界沿A65/65.1/65.2，owned转UI69，A原写权失效；读/tmp/main-controller-20261008/draft65-resume.md的具体未完成点，不重做已完成工作。

仅追加VideoEditor.tsx移除68为jsdom加入的ResizeObserver可用性生产回退，必要模拟放已有src/lib/__tests__/setup.ts测试环境；实际桌面浏览器原生具备，不为测试加生产兼容分支。无服务/GUI/模型/探针/媒体/网络/费用/真实数据/共享文档/远端写权。主控仍独占审核/机械集成/PR/Linear。最小直接检查和类型，未改旧通过复用；同一组合GUI归B66。准确clean源码、短报告和实际剩余完成后交付，不将未提交半成品称完成。

### B66：v3同候选必要可见验收

B为零产品写者；仅新/tmp/main-acceptance-20261008/ui66/。先按64/65和DESIGN v5.2及领域版本规则语义ACK，定向复用ui62合法合成DB/owner token和ui45源色卡（只读旧根，mode=ro/uri=True备份，0600合成凭据），保留旧数据。准备无Key/Pi离线/scheduled OFF、完整HOME/XDG/DB/config/library/output/tmp/models隔离和必要代表性任务；不读真实库/媒体/密钥、安装依赖或造费用报价。此准备与编码独立，仅输入准备，不提前运行服务/GUI/模型或使用旧候选验新行为。

主控交付准确v3组合commit后，B核源码与数据、4579/3179空闲，自有嵌入page viewport/键盘可用，精确候选语义ACK即条件START：一次API4579/Web3179，自首服务20分钟绝对租约，不续租/重启，不操作个人浏览器。所有GUI命令明确page，真实控件/鼠标/键盘/viewport/PNG，eval只读几何/焦点，不注入DOM/CSS/事件/响应；普通浏览器动作由Agent自行完成，不请用户拖窗/按键。

最小旅程：1024x700浅色及1280x720代表画面，真实task比例16:9/1:1/9:16等比且预览与三轨首屏；已知不等长片段按真实区间同尺，非等宽装饰。通过真实普通字段改一次时间/比例或字幕→无确认/不生成→新持久版本→刷新回读→撤销/重做→受影响成片/检查有效性；一条离线真实提案点击应用到草稿无第二确认，不冒称外部Pi通过。删除开启真实确认只点取消、无删除；不准备或验证费用卡。代表页首页分组/文字/可见焦点和集合显示配置只影响列表、父圆角卡/选项逐层保留输入。至少一个真实可持久普通修改与版本边界完整，不以fixture/隐藏状态代替UI操作。

仅对本次合成任务的草稿/版本/撤销与列表设置允许GUI业务写入；准备后不得API/SQL替代GUI写业务，不点生成/试听处理/录音/发布/授权变更/删除执行，无模型、FFmpeg/波形生成、主机探针或付费调用。必要旧色卡浏览器播放允许。实际缺费用/能力/媒体或工具项标UNVERIFIED，不绕过；其他独立可验继续。结束/到期只释放登记自有PID/组/page，证明端口进程退出与旧库未改；短报告给实际PASS/FAIL/未验和源码身份，不重复全部尺寸/主题/浮卡旧矩阵，不关闭整体制作/安装/发布。

## 任务52/53/54：UI收口与人物制作接线

2026-10-08用户批准开始执行。共同产品基线为PR97组合候选`1e0c972665f3cb3b43db9af97abac4033d047477`，本地主控组合树`/tmp/main-controller-20261008/ui-v51/integration`；PR96/97仍未获合入批准。三条线程先读本节及各自直接权威，语义REBASE_ACK后直接执行，不等第二次START。任务完成的精确源码由主控审diff/证据后机械组合；B54只验冻结1e0c972，不把并行修改算入已验范围。

### UI52：首屏质量结果读取

目标：依据ui51首次页面真实质量读取失败与刷新后恢复现象，查清首个失败请求/鉴权/状态初始化原因，必要时修现有Web读取时序。仅改`apps/web/src/components/CreationTaskWorkspace.tsx`质量读取/错误消费者、`apps/web/src/lib/api.ts`对应quality调用及既有直接测试；不改HomeCardDialog/AppShell/CSS、API、领域、安全、密钥或共享文档。现UI树8cc1ddb与1e0c972目标blob相等时可原分支实施，不整体merge/rebase；脏树先报。只读ui51具名report/AX/API日志、目标源码；证据不足不得猜成功或屏蔽错误。只跑能证明实际首因及修复的最小无服务专项；已有通过卡片和全量CI不重跑。首因需后端差额则报告精确建议给主控，不越权；保留真实failed/unavailable/尚未检查含义，不触发检查。临时证据只`/tmp/main-acceptance-20261008/ui52/`。无服务/GUI/Key/Pi/模型/网络调用。报告准确commit、diff、前后证据和需补GUI项；无可信首因则交真实阻断，不伪造修复。

### A53：默认人物连续制作的受许可路径

目标：从已确认文案/当前配音/授权人物版本，接通默认20%人物所需专属预算、唯一设备队列、同源持续核验和人物组合CPU成片。采用既有avatar_lipsync/provider与Grant/子进程边界，不引入第二调度器，不删安全拒绝、不借纯素材预算、不改默认0。未知或未标定预算仍清楚拒绝；测试显式预算仅证接线，不设本机生产默认。直接绑定架构§3.12/§5.2、认证§5/§9、产品§12.1及creation39报告。A为该路径纵向负责人。

owned：`apps/api/app/creation_execution.py`、`member_permits.py`、`host_resource_probes.py`、`main.py`中的creation/person阶段消费者、`avatar_lipsync.py`、`granted_subprocess.py`；既有`apps/api/tests/test_creation_execution.py`、`test_creation_resource_contract.py`、`test_member_permits.py`、`test_device_runtime_wiring.py`、`test_host_resource_probes.py`、`test_avatar_lipsync.py`和`test_granted_subprocess.py`直接范围。其他必要文件先给精确首因与最小扩权建议；不创建通用抽象、配置开关/回退、公共schema或迁移。仅在本树clean后创建`codex/wil26-presenter-permits-20261008`，可从主控组合树本地fetch精确对象并以1e0c972建分支；保留旧分支，不reset/stash/drop/rebase。

最小源码验收：非零目标不再被无条件拒绝，而是必须具备正确人物输入/已标定专属预算/有效同设备Grant；人物输出必须对应当前配音分段/人物版本，成功提交前持续复核原账号/workspace/设备和输入版本。取消/超限/撤权/unknown终止自有子组并释放后才提交或重排，cache复用仍核版本/授权；无预算/跨空间/陈旧输入明确拒绝。沿用现有阶段持久状态与render-only恢复，人物媒体计入真实组合输入和预算。旧无许可person消费者不得成为新连续制作回退；列清实际保留诊断/单步入口及其边界，不扩大修改权限。专项用隔离SQLite/合法合成引用与无模型provider或轻量假子进程证明接线、取消/释放和拒绝，不能称真实模型/成片/设备校准通过。禁止app.main真实配置导入、主机探针、模型/FFmpeg/媒体解码、联网收费/安装/云迁移/GUI或用户媒体读写。证据只`/tmp/main-acceptance-20261008/presenter53/`，本地提交具名净差额，回报真实剩余模型校准与E2E条件。

53.1有限追加：A已提供avatar缓存/输出校验经`media_timing.py`两个probe直接subprocess.run的证据，主控核源码相符。允许仅该文件`probe_avatar_media`/`probe_media_duration`接可选既有runner，使本次人物Grant覆盖ffprobe及剩余时限；不复制算法、不全局monkeypatch、不改变媒体成功条件。既有直接消费者测试归A，不增真实ffprobe/模型执行权；legacy诊断默认路径边界如实列出，新连续制作不得落回默认无Grant。

53/56接口绑定补充：已批准人物工作单元沿现有execution状态返回`current_stage="person_render"`及同名stages事件；digest/分段仅为内部unit_key，不当成对用户阶段名。原narration/montage_plan/render/cover值与null保持，不改CreationTask领域阶段或引入第二状态机。A只owned现有main状态投影；UI唯一owned56下述直接类型/中文显示消费者。这是既有目标的必要接线，任务目标与未实测边界不变，双方阅读本条语义ACK后实施。

### B54：准确候选的UI剩余验收

本单产品写权为零。新根仅`/tmp/main-acceptance-20261008/ui54/`；archive准确1e0c972并逐blob核验。只读ui51合法合成DB/参考媒体/现有依赖，不写旧根；合成owner权限0600、新HOME/XDG/config/DB/cache/output/tmp/models/agent、Key空/Pi离线/scheduled OFF、无云身份。沿用既有Next15.5.18与已安装依赖，准确记录浏览器canary/普通React区别，不安装修改依赖。

优先在启动前读当前computer-use能力；启动一次新的自有API4576/Web3176/Chrome profile，lease自首个服务起最多20分钟，登记PID/PGID/argv/starttoken，只本地loopback。不续租或自动重开。读写只限本合成DB和隔离根；只允许布局/主题/卡片/播放器交互，不业务确认/创建/检查/制作/发布/Key/模型；可播放具名合成色卡不转码生成。重用已过首屏/配置/锁定证据，不重复整轮。

必须收集剩余：1440×900、1280×720、1024×768和1024×700真实画面/实测窗口；浅/暗代表画面；三栏真实拖动及普通刷新宽度记忆；新HomeCard blob嵌套Esc/外部点击仅顶层且父状态保持；Tab实际循环/关闭焦点；播放器播放/暂停/定位及详情收起时必要控制可见。Electron壳仅列后续，当前不启动桌面。几何只辅助，不能用启动尺寸冒充实测viewport。工具拒绝focus/key/resize/window_not_found时保留首个原因、立即回报主控所需一个人工动作；不盲重试、不用CDP/JS/合成键/AppleScript/改权限绕过，不猜产品FAIL。能独立完成的继续，等待用户期间lease不延长；到期或结束仅退出本单已核自有进程组/Chrome，不影响用户浏览器。报告矩阵PASS/FAIL/UNVERIFIED、真实PNG/AX/持久读回、失败首因与release正证；未验证不关闭WIL205/父项、不称RETIRED。

### UI55：首屏修复的认证模式审查补充

UI52准确61e260579bcdd24245b5f95f20dd5ce814b5ae4c已交付并由主控读diff/report/前后日志：本地初次质量401的真实身份顺序首因明确，13直接专项及类型通过，未GUI验。旧52已结案，不恢复。55继续同一目标，owned仅`apps/web/src/lib/api.ts`的quality GET与`src/lib/__tests__/creation-plan-api.test.ts`；基线61e2605。复用现有`lib/auth/routing.ts:isLocalAuthMode`将新本地身份初始化限定在本地模式；Supabase/SSR/已存无效令牌仍沿既有鉴权路径，不能新增本地owner前置条件或匿名重试。核现有模式消费者、证明无localStorage令牌的supabase质量GET不调用whoami，local首屏仍正确等身份、失败仍显式。仅必要前后专项及类型，reuse52未改组件证据，不跑全量或启动服务/GUI/网络/模型，证据`/tmp/main-acceptance-20261008/ui55/`。主控审后机械组合52+55，B54旧冻结证据不覆盖api blob新差额，需新空profile补验。

### UI56：剪辑首屏播放器优先

主控已读UI52/55完整diff/report/前后日志并机械组合至clean`d9605a092dc582c12c3cb6e33e29f8a4355ffe2e`（1e0c972+b57a2e5+d9605a0）；55已结，不恢复其Dispatch。B54真实`theme-light-stable`/`media-play-stable`及`player-direct-before/after`截图显示连续制作文案/要求大面板占满剪辑首屏，播放器画面/控制不可见，主控实际看图相符；旧scroll返回synthetic未证，不能推成“不能滚动”。这项实际可见FAIL归56，DESIGN§5.5/§4.2已要求播放器/故事板优先，不改变产品能力。

从UI树clean后本地fetch主控d9605a0，新分支`codex/ui-v51-player-20261008`，旧UI分支保留；不整体merge/rebase。owned仅`apps/web/src/components/CreationTaskWorkspace.tsx`编辑阶段制作/作业/质量呈现、同名CSS、`VideoEditor.module.css`和既有`CreationTaskWorkspace.test.tsx`直接布局/连续制作专项。将编辑首屏的完整制作文案要求改为紧凑真实状态/入口，详细核对按需用现有圆角卡片或展开；给播放器及控制/横向故事板实际空间，低高度滚动属于所属正文。保留真实预检错误/阻塞/取消/恢复与render-only入口，明确开始必须仍核对实际文案和要求，开详情不是启动或确认，不改变请求/body/权限/领域/默认/配音字段；不移除开始功能或隐藏真实错误来腾空间。开关详情保留播放位置、镜头、草稿和版本，沿已有HomeCard底座不改其层级/焦点实现。

只做必要组件/类型/样式检查，复用旧未改状态证据，不启动服务/GUI/模型/探针/媒体生成、不安装配置或写api.ts/后端/共享权威。证据`/tmp/main-acceptance-20261008/ui56/`，最小前后证据、准确commit/diff和新GUI需求交主控；B54仍只验1e0c972，不能覆盖新布局与质量API差额。主控审并机械组合后再具名安排准确新候选可见补验，不自动合main。

56.1直接消费者追加：A53正在实现的`person_render`真实执行事件原Web未列其类型/中文名，会显示未知/原始内部名。允许UI在`apps/web/src/lib/types.ts`的CreationExecutionStatus.current_stage、`components/ExecutionQueueStatus.tsx`类型/阶段名、CreationTaskWorkspace既有executionStageLabels和各自既有直接测试添加这一个值，中文为“人物口型”；不改业务对象/阶段导航或其他状态。无模型事件测试只证明正确呈现，不能冒称真实人物运行。本批预览修复与这一个必要类型消费者可一次最小联合验证，其余53/56权限不变。

## 任务57：改用自有页面自动化完成可见补验

用户明确要求不要反复请其拖窗口/按键，主控已通过Orca已安装页面自动化在自有about:blank成功设置1280×720并回读innerWidth/innerHeight相等。这是本单新增的浏览器方法授权，不复活B54、不绕过原生电脑控制拒绝。原生OS/Electron行为仍单列未验；页面自动化交互与截图如实标方法，几何读取仅辅助，不能用DOM结构冒充可见PNG。

UI56/56.1准确299066b5de8c39b6910c340af8d03facba46011d已由主控读完整产品/测试diff、report及最终13项/非编辑2项日志，机械集成为clean `d78267e51a17b7086de070f2c7110c37263581c1`（父d9605a0）。52/55/56原写权收回；UI保留等待真实缺陷修复。B54实际6 PASS/1 FAIL/12 UNVERIFIED，FAIL播放器首屏被挤走交56，尺寸/键盘工具阻断不作产品失败；全部自有PID/组和4576/3176于17:35:46已退出，主控独立核验相符，54现为failed/retained，无服务重开权。

B57准确冻结d78267e，只写新`/tmp/main-acceptance-20261008/ui57/`证据、隔离库和临时脚本；archive逐blob核验。仅mode=ro复制ui54具名合成数据库/合法色卡和0600合成token，旧库/hash/证据保持；HOME/XDG/DB/output/cache/tmp/models/agent全新，Key空、Pi离线、scheduled OFF、无个人素材/密钥/云身份。可读ui54准确env/服务启动方法与已安装依赖，不安装框架/依赖，不访问真实main配置/用户库。

先读版本化orca-cli及references/browser.md，在pipefish创建一个自己的about:blank嵌入页，取得稳定browserPageId并每次明确--page；先验证set viewport与keypress正常，再核4577/3177空闲。语义REBASE_ACK与源码/库核验后本条即条件START：一次自有API4577/Web3177，仅loopback，首服务起最长20分钟绝对lease，不续租/自动重开；登记PID/PGID/starttoken。嵌入页只访问自己的本地页面；不连接个人Chrome/CDP、修改权限或系统窗口。Orca exec允许已支持的set viewport、鼠标拖动、keypress与截图操作；业务交互必须走真实页面控件，不通过eval/API改业务、注入点击/键盘事件、替换响应或注入CSS来制造通过。eval只读几何/播放器位置/焦点，不能代替动作或画面证据。若嵌入页面能力不足，保留具体原因报主控，可继续独立项，不再请求用户做普通浏览器动作。

优先空profile首次质量GET及普通刷新不再401，保留真实尚未检查而非质量已通过。新剪辑首屏在1440×900、1280×720、1024×768、1024×700的实际viewport截图，播放器画面/控制与故事板可见，正文滚动可达质量/作业；浅暗代表截图，制作详情完整实际文案要求可读、开关不启动且父镜头/草稿/播放位置/版本保持，焦点归还；真实播放器播放/暂停/定位。补剩余顶层Esc/外部点击/Tab循环、三栏拖动后普通刷新宽度记忆，核1200/1380附近响应式阈值。使用真实嵌入浏览器动作，不重复未改配置保存/锁定业务历史通过项。

不POST/PATCH/PUT/DELETE任务/检查/制作/发布，不确认参数、运行模型/FFmpeg/媒体解码/资源探针/付费外部请求；合法现有色卡浏览器播放允许。结束或到期立即退出已核自有PID/组并关闭自己browserPageId对应标签，实际确认端口/进程退出；保留个人浏览器/其他标签。最终矩阵和PNG/焦点几何/HTTP只读证据分别标PASS/FAIL/UNVERIFIED、真实首因与release证明。未全验不关WIL205/父项，不宣称整体RETIRED或原生OS/Electron通过；人物模型与完整制作仍受暂停约束。

## 任务58：中等窗口任务工具栏重叠修复

主控已实际查看B57冻结d78267e的`light-1440-900.png`、`light-1280-720.png`、`light-1024-700.png`。新播放器与控制/故事板在三个画面可见，旧首屏挤压消除；1280图任务“方案：仅自己可见/方案共享给团队”与右侧操作明显重叠，1440较宽、1024现紧凑分支不同。源码toolbarTitle为长文案/两个可见范围组，h1虽可收缩但固定内容溢入动作，这是现有v5.1响应式修复，不改变授权或共享语义。

UI从clean299066b继续可创建具名`codex/ui-v51-toolbar-20261008`，按本地主控d78267e目标源码blob核一致，不整体merge/rebase旧树。仅owned`CreationTaskWorkspace.tsx`任务toolbar展示、同名CSS、`VideoEditor.module.css`对应toolbar和既有直接组件测试；必要AppShell/EditorShell父契约先给精确首因，不自动扩权。先读取DESIGN相关紧凑标题/卡片/响应式；修当前实际可用宽度下标题、状态、可见范围及操作无重叠。长标题可省略，重要共享操作仍可通过真实入口获得，若折叠使用已有标准卡片或动作入口，不新建浮层底座；任务/方案共享独立handler、状态、失败反馈和权限保持，不能吞掉错误/更改API/默认/播放器父状态。不是简单隐藏所有交互或删除标签来制造通过。

只做必要直接消费者检查/类型与CSS语法，CSS像素修复不写镜像断言；不重跑56未改制作专项/全量或启动服务GUI/模型探针/网络安装。新证据仅`/tmp/main-acceptance-20261008/ui58/`，本地准确净差额commit/report交主控。B57继续原d782矩阵不热换，UI读本段语义ACK直接施工；主控审后组合并另单补工具栏实际画面与可达性，不宣称旧候选覆盖新差额。

58.1搜索初始焦点补充：B57已受控一次重开导航搜索，`search-controlled-initial.json`显示580圆角模态可见但activeElement仍顶部“搜索”按钮；真实点击输入后14次Tab/ShiftTab全部卡内并首尾回绕，证明键盘方法可用，真实缺陷仅新开初始聚焦。主控已读该JSON和实际search-tab-cycle PNG，HomeCard navigation源码已有Tab trap，不能为此另造trap或削弱层级。允许唯一UI写者追加`CommandPalette.tsx`及其现有直接专项；先用既有可挂载真实Portal消费者复现“入口点击→无需人工再次点击即可输入”，使初始化发生在实际输入挂载并React提交完成后，旧搜索/导航/ESC/关闭焦点契约保持。若必须改变共享HomeCard底座，只提交精确首因/最小差额待主控绑定后才写。旧统一卡片无新增写权；不新增dependency/通用hook/延时重试。只为真实失败作最小生命周期验证、复用已过边界循环证据，不跑全量/服务GUI。合入同58准确commit/report，B57不热换，后续同候选新开初始焦点需真实页面补验。

## 任务59：工具栏与首次搜索聚焦差额验收

57冻结d78267e实际24 PASS/2 FAIL/2 UNVERIFIED已接受failed/retain：窄窗口工具栏重叠、搜索初始焦点为真实FAIL；普通滚轮位移和原生OS/Electron未验。18:03:56已退出三自有组、4577/3177空闲及自有page关闭，主控独立ps/lsof与release核验相符。原57租约与操作权结束，不热换旧候选。

58/58.1来源b9a86b9b510b5cdbe80c891c771801bd6b92e9b3已审，机械组合Web候选5cf78c46512fac63a10d75ca6104312091784271；任务/方案共享使用既有config圆角卡和独立handler，打开不写。搜索input callback ref只具组件证据，真实初始聚焦仍待本单，不把受控animation-frame测试冒称浏览器首因证明。53来源99b36607e879eb065438a58c860790308e8f5000已审，机械组合在同Web之上为14fd23da24f685b7d3d4ce22c893f95b409a01af，人物预算、Grant、缓存同源校验、释放专项通过，不具真实模型/生产预算标定证据。53/58准确worker_done succeeded已retain，原写权结束。

B59只验冻结完整组合14fd23da24f685b7d3d4ce22c893f95b409a01af，来源`/tmp/main-controller-20261008/presenter53-integration`。只写新`/tmp/main-acceptance-20261008/ui59/`证据/脚本/隔离库，完整archive排除dotenv并核源码blob；mode=ro备份57合法合成DB，复制0600合成token及ui45/library/source.mp4，旧库与证据保持。完整HOME/XDG/config/DB/library/cache/output/tmp/models/agent隔离，Key空、Pi离线、scheduled OFF，不读真实配置/数据/密钥，不安装依赖。版本化orca browser指南与DESIGN相关章节须重读；共享权威无变化，ACK绑定本单精确候选/暂停/方法。

语义REBASE_ACK、源码/库核验、4578/3178空闲且新自有about:blank嵌入页viewport/keypress探针通过后，本条即条件START：仅一次API4578/Web3178、自首个服务20分钟绝对租约，记录PID/PGID/starttoken，不续租、不自动重启；自己的新隔离profile/page，每条命令显式--page。沿57成功真实控件/鼠标/键盘/viewport/PNG方法，eval只读几何/焦点/播放器，不写业务、注入事件/CSS/响应，不接个人Chrome/CDP或操作系统窗口，不请求用户做普通浏览器动作。

只补58影响项目：1280×720、1024×700、1440×900及一个57重叠断点的真实工具栏截图与无重叠几何，浅暗代表；真实三栏宽度调大后入口/播放器仍可达。共享设置真实打开，任务/方案状态与API事实相符、独立动作/错误槽可见但不点击共享写入，关闭焦点与镜头/暂停位置/未发草稿/当前版本保留。搜索入口点击后无需第二次点击输入，先只读activeElement再实际输入查询、Tab/ShiftTab循环及Esc关闭归还入口；未聚焦必须报真实FAIL，不用先点input制造通过。正常首屏及一次普通刷新quality GET仍200，保留尚未检查；新API只跑普通读取，不生成/执行/授权制作。

复用57未改播放器/主题/嵌套层级/三栏记忆等通过证据，不重跑28项或保存/质量检查/模型。业务POST/PATCH/PUT/DELETE为零；不能用fixture/DOM替代截图。结束或到期只退出登记自有组/page，独立证明4578/3178和PID/组释放、旧库未变、产品树clean；报告准确PASS/FAIL/UNVERIFIED和剩余Electron/滚轮/重模型边界。真实FAIL交唯一UI写者，不自行改产品；用户资源暂停不因本单GUI结束而解除。

## 任务60/61：新候选CI与最小宽度缺陷收口

59继续只验冻结14fd23d，18:15:40首启/18:35:40绝对到期不变，不热换。已真实证搜索首次直接聚焦/输入/Tab/Esc及父状态通过；主控读PNG/焦点记录。新1024×700共享入口x584–658与收起x606–638、撤销x642–674重叠，1280/1440/1379默认宽不重叠。主控已查看toolbar-light-1024.png；该FAIL另交61，不扩大59写权。PR97精确5cf78c4三类CI成功；PR98精确14fd23d前端/桌面成功、后端6 FAIL/1630 PASS/20 skipped，六项均为原人物未许可调用被53新增真实409拒绝，不能简单删安全保护或统一改断言消除失败。

A60仅从clean99b3660继续，在新`codex/presenter-ci-20261008`按14fd23d目标API blob核等。先读本节、53相关权威和主控`presenter53-ci-failed.log`六条真实失败，分清公共人物编辑消费者尚未接队列与内部诊断正向测试缺少有效Grant。owned只`apps/api/tests/test_montage_pipeline.py`上述五个函数/六个参数化case及其必要局部fixture；生产API只读。保留人物替换映射/语言/同源缓存的正向实质覆盖，将必要内部用例迁到真实隔离队列/Grant许可上下文；公共无许可入口应证明409且provider/job/版本/输出零写。不得把所有200改409、删/跳过原正向覆盖、monkeypatch绕过guard或放开生产无许可路径。若确认当前公开编辑范围需要新产品接线，先回报准确缺口与最小实施建议，不在此测试修复单扩写。仅最小相关pytest在完整隔离HOME/XDG/config/DB/tmp/output中使用已装依赖、合法引用/无模型fake provider；该有界直接测试可按其原隔离fixture导入app.main，禁止真实配置/密钥/资源探针/服务/媒体/模型/外部网络。新证据`/tmp/main-acceptance-20261008/presenter60/`，准确clean净提交/report交主控，不重跑全1636；53旧权不复活，重模型暂停不变。

UI61仅从cleanb9a86b9继续，在新`codex/ui-v51-toolbar-min-20261008`按5cf78c4目标Web blob核等；先读DESIGN工具栏/三栏自定义宽度、59准确1024 PNG/geometry与58真实消费者。owned `CreationTaskWorkspace.tsx`任务toolbar、同名CSS、`VideoEditor.module.css`toolbar；若首因在既有父层，有限追加`AppShell.tsx`/同名CSS和`modern-overrides.css`仅当前editor toolbar父布局及其直接响应式选择器，不改AppShell网格/三栏默认/共享卡片底座。以真实剩余内容宽而非只window断点修标题/共享入口/固定动作无重叠；标准入口保留真实能力/共享handler/错误、播放器与父状态，不把动作全部隐藏或加第二浮层。不要新增CSS像素镜像测试，已有直接消费者最小必要检查/类型/CSS语法即可；不重跑已过搜索/全量、起服务/GUI/模型/安装或写后端/共享文档/远端。证据`/tmp/main-acceptance-20261008/ui61/`，新clean提交/report；主控机械组合后新具名剩余窄宽/加宽真实补验，59不热换。

60/61由原A/UI可见终端实施，文件所有权与B59零产品写权不交叠；读本节语义ACK后可直接执行，不复活53/58。主控保留共享文档、候选审核/集成、PR/Linear权，不编写产品代码。

## 任务62：内容宽工具栏修复的准确候选补验

61已交21a2966c41479024a787a0effc2c542a0bbf3bf9，主控审完整三文件24+/4−及report、3共享直接消费者/类型/diffcheck，接受succeeded/retain并收回写权。它只在editor toolbar父容器依实际宽度换行、窄标题组隐藏辅助状态，不丢动作、不改三栏宽度/卡片/搜索；56px固定页头与必要换行有实际冲突。主控在用户既有完整UI实施授权内采用DESIGN §2最小例外：默认56px，剪辑窄区可随整组动作增高，1024×700播放器/控制/故事板仍首屏。唯一权威提交0fe2cd4，不复制到下层规则；B62须REBASE_ACK这条更新，旧“全页头固定56”解释失效。

59结案failed/retain：19 PASS/4 FAIL/1 UNVERIFIED；搜索/只读共享/父状态/quality正证通过，四个FAIL是同一宽度缺陷，错误槽真实失败未验。18:25:10三组/4578/3178/page退出，主控独立ps/lsof及release-final核一致。旧59租约/操作权结束，不热换。

B62冻结634978ae3a57039778e1f8cd8d07356b340fcb52，来源`/tmp/main-controller-20261008/presenter53-integration`，准确组合53 API与新Web/规范；其Web/规范来自PR97新70e96fe9f357b8addd7f2ebb582e9f493bca3732，UI61三个目标blob一致。A60只改测试不改变该服务候选，本GUI不宣称将来测试head已启动过；后续可基于运行blob等价明确复用正证。

只新`/tmp/main-acceptance-20261008/ui62/`，旧59 mode=ro合成库/0600token、ui45合法色卡只读，其余目录全隔离、Key空/Pi offline/scheduled OFF。不写产品/测试/共享权威/远端，不安装/模型/媒体生成/资源探针。完整archive排除dotenv并核blob/DB、旧库hash保持；新自有isolated嵌入page，每条显式--page，先blank viewport/keypress与4579/3179空闲核验。重读DESIGN §2新例外并语义REBASE_ACK后上述条件满足即START一次API4579/Web3179，首服务20分钟绝对租约不续/自动重启，记录PID/PGID/starttoken。

只补61影响：默认1024×700浅暗、真实拖动导航220/对话尽允许宽后1024及1280浅/暗代表、1440常规和1379断点代表。真实PNG+几何证明共享入口、收起、撤销/重做、录音、历史、参数、搜索、帮助等无互相覆盖且可达；增高页头下播放器画面/原生必要控制/故事板仍首屏。共享设置、历史、录音只打开/关闭不执行，验证既有圆角卡及父clip2/暂停8秒/草稿/V4保持/焦点归还；不开始录制/生成/修改。复用59未改搜索初焦/输入/Tab/Esc和共享实际值/quality与57正证，不重复全24/28矩阵或业务保存。普通轮滚仍未验也不得编造。

只Orca真实控件/鼠标/键盘/viewport/PNG，eval仅只读，不注入事件/响应/CSS、不接CDP/个人Chrome或操作OS窗；不请求用户操作浏览器。业务POST/PATCH/PUT/DELETE0，失败错误槽等无本次实证仍标未验。结束/到期只退出注册自有组/page，实证端口/组释放、旧库/blob不变及产品clean；正确本Dispatch完整report/worker_done。真实FAIL给主控/唯一UI写者，不修改产品、不复活61，模型/预算/E2E/Electron安装仍不在通过范围。

## 任务63：公开人物缓存消费者的同源核验缺口

60源7b479ecd6ec74345a1792b6e81e8f4f3ff237ed9（父99b3660）已提交单测试文件，主控已读完整250行净diff与7 PASS/共享fixture1 PASS日志；报告/正确worker_done尚待结算，不复活60写权。其保留无许可公开409/零生成写与真实Grant的内部正向、缓存目标映射/撤销；公开需要新生成的编辑仍未接队列，另记录WIL141，不把本测试单算产品接线。A60新增证据及主控源码确认：main `_person_render_matches_narration`仅核逻辑audio_reference/language/成功谱系，公开预览与confirm缓存分支尚未核53封存的presenter_input_facts，仍可把同路径已变内容当当前。这是53既有“缓存仍核同源”目标的具名直接消费者遗漏，须收口而非带已知缺口放行。

A63在60报告/worker_done被主控接受retain后，原manta可见终端新Dispatch实施；从clean7b479ec创建`codex/presenter-cache-consumer-20261008`，原分支保留。owned只`apps/api/app/main.py`的 `_person_render_matches_narration`与两条预览/confirm直接调用及当前授权输入解析所需局部代码，`tests/test_montage_pipeline.py`对应语言/谱系/缓存与60直接case。53已有`presenter_input_facts`/输入解析/transform参数契约只读复用，不另造事实源/校验算法、hash扫描或schema/迁移。必要其他文件先给准确原因待主控，不改生成预算、队列、默认20%、Web或公开编辑新生成接线。

目标：公开人物缓存只有当前授权人物版本、当前真实配音文件及成功transform封存事实全部匹配才复用。未封存/缺失/失效/同引用音频或人物文件变更（含同长度并恢复mtime）不复用旧ready结果，preview不得给陈旧reuse id，已预览后变更的confirm拒绝并保持DB/provider/输出零写；无变化且正确封存的合法结果仍能公开复用，映射、其他镜头、撤销保持。当前授权解析失败是明确不可复用/拒绝，不增加任意吞错/静默兼容；ctime仅本地变更信号，不称内容哈希保证。

先最小现有消费者复现旧缺口，再修局部共享matcher并仅最小正负验证；允许完整隔离env下已有pytest fixture导入main、真实SQLite/唯一原队列Grant与轻量合成provider/原事实文件修改，禁止真实配置/用户媒体/秘密/服务/GUI/模型/FFmpeg/主机探针/外部调用/依赖安装。新证据仅`/tmp/main-acceptance-20261008/presenter63/`，有真实held才证OS锁/Grant/子组释放，无held不能造释放证据。不重复未改7/20/全1636检查。报告精确源码、前后正负/生产blob范围/释放/未覆盖公开新生成及真实模型，clean净提交后正确本Dispatch worker_done。

62已交11 PASS/0 FAIL/1 UNVERIFIED，主控读完整报告、1024默认与最窄加宽暗色PNG/几何，接受succeeded/retain；18:40:39三组/4579/3179/page释放与主控ps/lsof一致，全部旧62启动权结束。63只API缓存直接消费者，不把62页面说成已运行未来API head；若不影响Web/普通GET且运行blob范围明确，可复用62正证，人物缓存产品实测仍无模型/资源窗口。本轮不新增GUI或向用户请求普通操作。

## 任务38/39/40：设计v5.1实施与独立准备

任务51结算：主控已核ui51完整report、实际closed/reopened PNG/AX与card-comparison，接受准确1e0c972必要GUI5 PASS、可选嵌套关闭1 UNVERIFIED，succeeded并retain。首屏/普通刷新正常，历史V1开关重开、关闭入口焦点及镜头2/草稿/暂停8秒保持；可选嵌套卡已可见但后续window_not_found，结果与焦点未证，不猜产品FAIL或绕过恢复。已装普通React19.2.6包与实际Next15.5.18浏览器19.2.0-canary分别记录，不冒称GUI同CI renderer。61表/旧库hash不变；16:53:32 API23815/Web23816/lease23817/Chrome24159及组、根进程、4575/3175全部退出，主控独立ps/lsof与release.json相符，无续租。任务51以下START只作历史依据，无恢复权。准确1e0c972 CI37752015898 Frontend/Backend/Desktop syntax全部SUCCESS并回读head一致。PR97/WIL-205保留草稿/In Progress及指定尺寸/主题、三栏拖动、完整键盘/播放器/新blob嵌套关闭等未验，不宣布整体RETIRED或自动合入；用户Tab可用反馈已记录。

任务50结算与51冻结：主控已核完整ui50报告/锁版before/trace/after、8cc1ddbf7aff3b15ee8a70f151d5a40c05d71c04/父0af2373、HomeCardDialog仅11增2删，测试blob未改。React19.2.6/jsdom30.0.1/Vitest4.1.10真实单例失败、焦点call-through“入口→关闭→入口→关闭”证提交覆盖；移passive清理后同锁版7卡片专项、类型/diffcheck通过。接受succeeded并retain。机械组合`1e0c972665f3cb3b43db9af97abac4033d047477`，来源`/tmp/main-controller-20261008/ui-v51/integration`，组件与UI提交blob一致，API/lib/actions较PR96不变；与48产品blob有差额，不借用48焦点通过。B51收到本Dispatch具名START后，语义ACK/61表备份/准确完整archive/已有锁版依赖映射/4575及3175空闲检查通过即可一次15分钟新GUI，旧根不复活。真实GUI结果和新head CI尚待核，不声明可合入。

**任务51 / B（MAIN-UI-FOCUS-ACCEPTANCE-20261008-51）**：UI50已定位React19.2.6提交恢复覆盖layout清理焦点，产品HomeCardDialog改为提交后passive清理，源码仍待冻结。B用原pipefish可见终端新Dispatch，仅新`/tmp/main-acceptance-20261008/ui51/`准备；mode=ro备份ui48/db/fixture.sqlite3，61表相等/原hash不变，具名ui48合成owner复制0600不打印；只读合法ui45/library/source.mp4与父素材根，其余HOME/XDG/config/cache/tmp/output/models/agent/store全隔离、Key空/Pi offline/定时OFF。准确源码archive必须等UI50接受后的主控组合SHA START，旧48不复活。

既有依赖只读复用，无安装/build/tests/改配置或源依赖；定向核已有CI锁版React19.2.6依赖能否作为本轮Web运行依赖，允许仅新ui51内readonly symlink映射，不修改已有node_modules，不猜运行版本与CI相同。方法和实际可用版本短PREPARED；若锁版完整运行依赖缺失先报事实，不安装或私改权限。无GUI/服务直到本Dispatch START；无产品/Git/远端/共享权威/用户数据/密钥/模型/媒体/探针权。

START后准确组合一次API4575/Web3175/新Chrome profile，自首次启动15分钟固定到期不续；目标仅首屏/普通刷新无hydration、统一历史卡正常打开关闭重开并关闭焦点返回入口、选择/草稿/父状态保持，必要共用嵌套卡顶层关闭。原生AX/PNG，不CDP/JS/合成键盘/绕过focus拒绝，不再人工或重复保存/锁定/制作/完整矩阵。独立版本边界如实记录，Esc锁版组件专项与GUI点击关闭分开报告。结束退出登记自有PID/PGID/新profile并核本根/4575/3175无残余；短报告准确SHA/版本/PNG与PASS/FAIL/UNVERIFIED、释放后真实worker_done。未验矩阵不升级，WIL-205/PR仍不自动完成/合入。

任务50最小核验补充：UI已证本机Web已装React19.3/jsdom30.1.2/Vitest4.1.11与CI锁19.2.6/30.0.1/4.1.10不同，现有根node_modules/.pnpm具备锁版；静态19.2.6 flushMutationEffects显示layout cleanup焦点后提交恢复可覆盖，尚须实证。允许只用已安装锁版包在Node内存startVitest/resolve aliases做原SideCards hydrate单例修改前→必要修复后专项，不安装/修改依赖或配置文件、不建另一安装环境、不启动服务。相关临时runner/日志仅ui50，alias只解决准确锁版测试运行，不进入产品或workflow；准确版本/命令/失败与修复证据回报，不能把静态推断当已复现。原owned与模型暂停、GUI隔离界限不变。

**任务50 / UI（MAIN-UI-HYDRATION-FOCUS-20261008-50）**：准确254bb3c的CI37750067542 Frontend554 PASS/1 FAIL，Creation/HomeCommand旧两失败已过，仅新SideCards SSR→hydrate Esc焦点在默认waitFor后仍停留hidden卡片关闭按钮，源`ui49/frontend-ci-254bb3c.log`2323–2431。前单本地焦点未复现，不再假定只是等待；B48正常点击关闭真实GUI正证不覆盖此hydrate/Esc CI路径。唯一UI写者从clean0af2373核精确首因，owned仅HomeCardDialog.tsx与SideCards.test.tsx。定向对照本机与CI已有依赖/Node/jsdom/React版本、hydration手动root及焦点捕获/层清理事实，先最小可复现单例与必要既有卡片用例，不本地全量，不安装/另建环境或改workflow/config。

保留零恢复错误、SSR邻接DOM身份、关卡实际不可见、严格返回触发按钮、挂载/草稿/滚动/播放位置与层级规则。禁止强制focus冒称产品通过、删/跳过断言、延长等待、猜测补防护或盲改业务。测试fixture实际有误只能按真实证据修；若产品首因明确仅HomeCardDialog修必要逻辑，产品blob变化须新准确独立GUI，不能热换48。仅新`ui50/`短报告精确commit/父/diff/真实首因与复现/最小复验/必要类型、是否产品blob变化及CI待核；无服务/GUI/模型/媒体/探针/远端共享文档写。语义ACK后实施，本已批准UI目标缺陷内无需另问用户。

任务49结算：主控已核0af2373a84fd1638168fbe89609ac557afc1b6d4/父a512cc4、3测试文件13增10删及完整报告/日志。本地联合修改前2 FAIL/1 PASS（CI焦点未本地复现），修改后3 PASS/91 skipped；等待实际Portal按钮/dialog及关闭焦点条件，严格预览/SSR/父状态/取消不写仍保留，类型与diffcheck通过。接受succeeded并retain。机械组合254bb3c9911e36c6725364e6b8a612a24bfe84ef，patch-id与源码提交相同；较B48所验49ec013只有三测试差额，全部产品blob不变，B48真实GUI可复用，不重开服务。准确新head CI37750067542运行结果以GitHub为准；未宣称通过/合入或整体RETIRED。

任务48结算：主控已读完整短报告、card-comparison及真实首屏/开发菜单/重开PNG，接受准确49ec013补验6 PASS、succeeded并retain；真实首屏和普通刷新无原Hydration入口，历史打开关闭重开、V1/clip2/草稿/暂停8秒及关闭焦点保持，知识/设置显示正常。初次quality读取失败仍单列原未改行为，刷新后尚未检查，不冒称质量通过。61表/旧源hash不变；16:21:56自有API95702/Web95703/lease95704/Chrome96018及组、根进程、4574/3174均退出，主控独立ps/lsof相符，无续约。下面任务48 START仅历史依据，不得恢复。精确CI37748842801已结束Backend/Desktop syntax SUCCESS、Frontend三测试失败，归49；不把GUI6项正证扩为整体矩阵/RETIRED或可合入。

**任务49 / UI（MAIN-UI-HYDRATION-CI-20261008-49）**：准确49ec013的CI37748842801 Frontend ESLint通过、Vitest552 PASS/3 FAIL；实际日志`ui47/frontend-ci.log`定位CreationTaskWorkspace“uses typed operations for replace, reorder, lock, confirmation, and undo”找不到取消、HomeCommandComposer“asks the assistant before creating anything, then confirms the real preflight”找不到制作方案dialog、新SideCards SSR→hydrate用例Esc后焦点仍在关闭卡片而非查看历史。不能猜全部只是测试时序或关闭失败；B48准确真实GUI分项正证保留，正在释放，不热换其源码。

唯一UI写者从clean a512cc479068be25f47205a3234d913b22626991核三失败真实首因和最小联合复现。owned仅HomeCardDialog.tsx，以及既有SideCards.test.tsx、CreationTaskWorkspace.test.tsx、HomeCommandComposer.test.tsx三条失败及其直接必要fixture。必须保留SSR首帧一致、关闭挂载、正常焦点/逐层关闭/取消不写/真实预览契约；若仅异步Portal初始化和requestAnimationFrame完成时序，等待真实用户可见条件后再断言，不加任意sleep/重试、提高超时、删焦点或放宽断言。若实际产品问题，只能在HomeCardDialog修准确首因并回报，不改业务/CSS/API/配置或其它文件。必要最小3用例一次复现→修复后联合复验、改动所需类型；不跑全量或重复无关矩阵/服务/GUI/模型/安装。只新`/tmp/main-acceptance-20261008/ui49/`短报告精确父/commit/diff、是否产品blob改变、真实复现/复验及剩余。主控机械组合同PR97并读新head CI；产品blob不变可复用B48真实GUI，改变须新准确补验。读本段语义ACK后实施，不等待再次许可。

任务47结算及48冻结：主控已核准确a512cc479068be25f47205a3234d913b22626991/父400c3ee，两owned文件64增3删、真实SSR→hydrate修改前失败及修改后7 PASS/类型通过、clean；接受succeeded并retain，不等于GUI通过。已机械组合为`49ec0130560de78eba8b64a81edc85bc5b065f85`（来源`/tmp/main-controller-20261008/ui-v51/integration`），两文件与UI候选blob一致，API/lib/actions较PR96不变。B48须用此完整archive，收到本Dispatch具名START且语义ACK、备份比较、源码核对、4574/3174空闲后才启动；原94bf与46环境不复活。47修复仅初始clientReady/useEffect及active就绪门控，保留关闭挂载与原栈行为。

**任务48 / B（MAIN-UI-HYDRATION-ACCEPTANCE-20261008-48）**：任务46已准确failed结案并retain，主控接受8 PASS / 1 FAIL / 3 UNVERIFIED与16:08:04全部自有资源释放。用户人工滚动、Esc、重开和“Tab可以用”保留，不扩大为完整键盘循环/实际拖动/完整播放器通过；原任务46全部启动权终止。UI47正修唯一已证hydration缺陷，复用B可见终端新Dispatch，唯一新写根`/tmp/main-acceptance-20261008/ui48/`。

先仅准备：mode=ro备份具名`ui46/db/fixture.sqlite3`，逐表相等及旧库hash不变；只复制ui46具名合成owner凭证为新0600文件，不打印。素材仍只读复用已合法的`ui45/library/source.mp4`及其父VIDEO_INBOX_PATH，禁止扫描或改写。HOME/XDG/config/cache/tmp/output/models/agent/不存在测试store全新隔离，排除dotenv、Key空、Pi/model offline、生产定时OFF；现有已装依赖只读复用。此阶段不启动服务/GUI、不写产品/Git/共享文档/远端、不安装/build/test/媒体工具/模型/探针/收费调用。准备好给短PREPARED，等待主控接受47源码并冻结机械组合准确SHA的本Dispatch START；不得用94bf旧候选冒称新验收。

START后一次自有API4574/Web3174/新Chrome profile，自首次服务启动15分钟固定绝对到期不续，先核任务与身份可读再普通打开。目标仅真实首屏/普通刷新不再出现hydration错误、卡片正常打开/关闭/重开及原有父状态保持；共享卡片消费者可定向轻触知识/设置的显示，不重复已过保存/锁定/制作链路。原生AX/截图，无CDP/页面JS/合成键盘或绕过focus拒绝，不再请求用户额外人工。B46未改逻辑正证可复用并准确标候选边界。新真实缺陷报主控，不热换代码；不把HTTP/库比较或错误badge消失单独当整体验收。固定到期或提前完成只停登记自有PID/PGID/新profile，证明本根与4574/3174无残余；短报告准确SHA、PASS/FAIL/UNVERIFIED、实际PNG/AX及释放后worker_done，不关闭WIL-205/父项或宣布整体RETIRED。

**任务47 / UI（MAIN-UI-CARD-HYDRATION-20261008-47）**：B46在准确94bf367真实Chrome读取开发1 Issue正文，`ui46/issue-text.txt`/`issue-readable.json`明确Hydration failed：HomeCardDialog103初始`open=false`隐藏section与服务器VideoEditor_stage div不一致，调用CreationTaskWorkspace2146/TaskPage24。主控已核真实AX错误及准确源码109的document条件Portal，不把浏览器可恢复渲染或用户“可以用”当错误不存在。唯一UI写者基于自身clean400c3ee512a037d06b1621d1d24238143459552d，定向确认根因后修复SSR与首次客户端输出一致、客户端可用后正常Portal。owned仅`apps/web/src/components/HomeCardDialog.tsx`及既有`__tests__/SideCards.test.tsx`；不改业务/API/状态/几何CSS，不另建浮层/依赖或隐藏错误。

保留父卡挂载、历史选择/滚动/草稿/播放器、逐层关闭/焦点和同级替换规则。必要的一次真实SSR→hydrate消费者复现/复验及相关最小已有专项、类型检查即可；无全量/重复已过检查，无服务/GUI/安装/模型/探针/媒体/远端写权。仅新`/tmp/main-acceptance-20261008/ui47/`短报告准确父/commit/diff/首因/验证与未验。既有B46人工滚动/Esc/重开状态正证保留，修改后的首屏/刷新和卡片须主控机械组合再由独立新任务真实验证；当前B46资源已退出，不能热换候选。用户已明确不用继续人工；本单属于已批准UI目标内真实缺陷修复，不新增产品决定。读本段及直接DESIGN §4.2，语义ACK后施工，不等待再次许可。

**任务46 / B（MAIN-UI-INTERACTION-ACCEPTANCE-20261008-46）**：用户最新“可以抓紧吧”确认现有未验交互的继续验收并愿协助3–5分钟，不改变设计或恢复重模型。复用既有B终端的新Dispatch，唯一新写根`/tmp/main-acceptance-20261008/ui46/`，准确冻结94bf3671cb6efcd540e09f83f818a34f363c75e1，来源`/tmp/main-controller-20261008/ui-v51/integration`；按DESIGN §2/4.2/7只补受控历史关闭重开/正文滚动/焦点、三栏拖动收起与记忆、完整播放器可见与普通刷新。原45已退出，绝不重启旧根。无产品/测试/Git/共享文档/Linear/远端写权，UI/A均无新增权。

准备只定向读取45最终`db/fixture-source-local-v2.sqlite3`及0600合成owner-token，mode=ro备份新库并逐表相等；只读复用具名`ui45/library/source.mp4`和其父素材目录作为本轮VIDEO_INBOX_PATH，素材路径保持已验证的合法事实，禁止扫描/分析/改写素材目录或其它文件。复制合成token到新0600文件，不打印；其它HOME/XDG/config/cache/tmp/output/models/agent与不存在的具名测试store全新隔离，排除dotenv、Key空、Pi/model offline、生产定时OFF。准确候选完整archive到新根，现有依赖只读复用，不安装/build/测试/模型/媒体工具/资源探针/收费调用。

本条即此新Dispatch的条件START：语义ACK、准确源码与初始库比较通过、4573/3173确实空闲后，允许一次启动自有API/Web/Chrome新profile，以首次服务启动起15分钟固定绝对到期，不续。须先真实API任务与本地合成身份就绪，再正常打开页面；登记PID/PGID/窗口/实际尺寸与expiry，立即报READY和第一个人工动作所需稳定画面，不等待再次START。优先在实际1024宽窗口将历史卡打开、草稿和镜头选择确立为本轮新基线。原生focused gate失败不重试/绕过；键盘/滚动/拖动由主控逐步提示用户，B收到回报后立即只读AX/PNG回看，不私自改变待验画面。一次动作一次核对；避免准备长报告耗尽人工窗口。

人工动作等待期间可定向读本轮已有状态，不能导航/刷新/改选择/重启；用户未回应是未验，不当完成。关闭后受控重开保留历史版/滚动/镜头/草稿/播放位置须真实对照。必要普通GUI本地选择/草稿/播放允许，不确认参数、撤销业务、制作或检查字幕。用户协助拉高/加宽窗口才能取得指定视口时记录真实几何，不能把启动参数当CSS实测。待人工阶段结束才正常刷新核任务页与quality/404；开发Issue仅在真实出现时展开读正文，不猜原因。已过配置保存/锁定确认/历史可读/API事实不重跑。

到期优先只停自有登记组与新profile，证明全部PID/PGID/根进程及4573/3173无残余，保留用户浏览器、旧库和全部证据。新真实缺陷报主控，不自行改产品，不热换SHA。最终短报告PASS/FAIL/UNVERIFIED、准确证据与释放后worker_done；完整结果和下一步需主控回读，不因PREPARED/READY宣布Done或整体RETIRED。

任务45结算：主控已完整核最终`ui45/report.md`、12项结果及真实PNG/只读比较，接受准确failed并retain。准确94bf候选为5 PASS / 7 UNVERIFIED、无新增已证产品FAIL；1024×838 V1和人工阶段1091×838 V4完整圆角卡、列表/正文及下部字幕/产物均可见，原裁切已修复。草稿及8表持久事实保持，未证明受控关闭重开/滚动/Esc/焦点或选择/时间完整保留，不宣称整体RETIRED。新初始fixture仅素材两处路径改动，原DB/hash不改；媒体200/206及GUI定位8秒、播放暂停9.52244为分项证据，不替代完整播放器PNG。重启/刷新期间两张真实404后入口恢复，具体因果未定；quality恢复不算完整刷新验收，开发1 Issue正文仍未知。六个现旧PID/PGID及ui45根、4572/3172在原14:30:42到期退出，主控独立ps/lsof与release.json相符。任务45全部操作权收回；以下START只保留追溯，无复活/续租权。PR97仍草稿，WIL-205仍In Progress，下一具名窗口只补剩余项。

**任务44 / UI（MAIN-UI-HISTORY-VISIBLE-20261008-44）**：任务41最终PNG `final-history-v1.png` / `final-history-current.png` 确认在实际外窗1024×841（截图推断内容约1024×754）打开历史版本后，面板被压成细条、标题与版本正文不可读；AX读取/切换V1/V4不能当可见通过。由唯一UI写者在自身干净`e7cbf3e702ffdb6339222464e3f84de651dcbf1f`继续本次已授权v5.1迁移，准确组合候选`3d7d7e1d4e02496221808dba99ec1e3c664ab0ed`运行Web与该来源一致。先证明直接父布局/消费者首因，修复历史列表和详情的可读性、关闭入口及有限正文滚动，服从DESIGN §4.2统一圆角卡和剪辑预览优先，不通过隐藏历史、删除版本、放宽断言或假数据解决。owned仅`CreationTaskWorkspace.tsx`/同名CSS/直接测试、`TimelineVersionHistory.tsx`/同名CSS/直接测试；必要`VideoEditor.module.css`父布局仅在首因证据明确后处理，不批量改其他页面。保持真实只读API、当前时间线/对象/播放位置/草稿不因历史查看改变；复用HomeCardDialog，不另建浮层机制。必要最小已有消费者检查及改动所需类型检查，不增加镜像CSS测试，不重跑全量。只写新`/tmp/main-acceptance-20261008/ui44/`报告，无服务/GUI/媒体/模型/探针/安装/远端写权；源码通过后主控机械组合，B另具名有限窗口补这个失败路径，不复活任务41启动权。读取本段与DESIGN指定章节，语义REBASE_ACK后施工；准确commit/父/diff/首因/检查/未验返回主控。设置截图开发模式“1 Issue”尚无对应内容，仅保留待查，不从本任务获得任意修复权。

任务41结算：主控已逐项读`ui41/report.md`及`matrix-results.csv`，接受准确worker_done/outcome failed并保留既有线程。最终`3d7d7e1`矩阵6 PASS / 1 FAIL / 26 UNVERIFIED；已过配置保存/重启与普通锁定确认/刷新/撤销独立证据保留，单次人工Esc仅L3子项通过。E-VERSION真实可见裁切失败交任务44，其他指定高度、键盘、滚动、完整播放器等缺证不算产品失败或通过；原生窗口/像素几何测量类别明确。13:26:35所有21个登记现/旧PID/PGID、根进程和4571/3171已释放，主控ps/lsof另核。任务41及下面START_GUI只作历史依据，无恢复/续租权；新候选补验须新Dispatch和有限窗口。PR97仍草稿，WIL-205仍In Progress，不宣称整体RETIRED。

任务44有界补充：主控已核HomeCardDialog.tsx真实`hidden={!active}`保留挂载及SideCards.module.css `.card {display:flex}`，当前缺显式隐藏规则；按已有“关闭后不可见而保留父状态”契约，唯一UI写者可仅在`SideCards.module.css`增加`.card[hidden] { display:none; }`。不改HomeCardDialog或模态底座其他逻辑，不创建镜像CSS测试，不重复已过类型/无关专项；B45必须真实核历史关闭视觉消失、重开内容/状态及焦点。这是已有交互契约修复，不新增设计决定或其他写权。

**任务45 / B（MAIN-UI-HISTORY-ACCEPTANCE-20261008-45）**：承接任务44的真实可见补验，唯一新根`/tmp/main-acceptance-20261008/ui45/`，不写产品/测试/Git/共享文档/远端。先只做隔离准备，主控尚未冻结修复候选；可复用ui41具名env/服务/PNG方法及已安装Node/venv/Web依赖，禁止新框架/安装/模型/媒体/资源探针。仅只读ui41的`db/fixture-no-narration-v3.sqlite3`（最终V4）及0600合成`owner-token`、合法`library/source.mp4`，在新DB合法备份/复制作为初始测试基线，旧DB/hash/证据保留；无个人素材、真实DB、密钥或登录钥匙串访问权。新HOME/XDG/config/cache/tmp/output/models/agent全隔离，Key为空，生产定时OFF，0job，model/Pi不可用如实显示。API4572/Web3172仅loopback，自己的新Chrome profile/PID/window；冻结准确组合SHA并本Dispatch收到START_GUI后才可启动，最长20分钟绝对租约不续期，所有PID/PGID/端口身份及退出沿ui41已证办法。

任务45优先在实际1024窗口重现E-VERSION并核修复：历史标题、真实V1/V4列表及详情截图可读，关闭/重开与正文滚动、当前版本和任务/对象/草稿/播放位置保持，退出不写timeline或触发制作。可补1280亮暗同一路径及相关播放器可见/真实暂停/定位证据，不重跑配置保存或锁定确认。原生聚焦/尺寸/键盘已知能力限制不重试、不绕过；普通一次Chrome启动尺寸用实际外框/PNG核验，无法取得指定尺寸保留类别。用户若明确愿意协助手动，可由主控逐步提示Esc/Tab/滚动/拖动，B只设置明确当前卡片并只读回看，不能代替用户键盘动作或自动扩大尺寸矩阵；未得到用户配合不停止独立历史验收。可在自有设置页仅展开开发“1 Issue”读取实际内容，具体缺陷报主控，不修改配置或修代码。开始制作/重出/字幕检查重媒体/Keychain写入/供应商调用一律禁止。结束报告实际PASS/FAIL/UNVERIFIED及沿用项，证明自己的全部进程/端口释放；PREPARED不能算补验完成，源码变更先停止旧自有服务、保留证据后由主控重新指定候选。

任务44结算及任务45冻结：主控已审UI最终`400c3ee512a037d06b1621d1d24238143459552d`（父e7cbf3e）5文件80+/57-及真实首因、两个专项/必要刷新复验和类型结果，接受succeeded并retain。准确机械组合`94bf3671cb6efcd540e09f83f818a34f363c75e1`（父3d7d7e1）在`/tmp/main-controller-20261008/ui-v51/integration`，无冲突，API/actions/lib相对PR96 a3差额零，diffcheck通过，准确组合历史两专项通过。上述任务44原写权收回。B45 `ctx_7aa9332cf936`的新PREPARED已审：仅ui41具名合成库mode=ro备份，V4/4版本/2镜头/确认文案、0job/scheduled、旧库hash不改、token0600、色卡hash相等，均不证明GUI。具名START_GUI只使用上述94bf367完整archive，4572/3172自有服务/Chrome、最多20分钟绝对租约不续期，沿45边界执行；用户已明确愿意5分钟逐步手动协助，由主控发单步并等实际回报后B只读核验，不能把沉默当完成。PR97新head CI待实际运行；不借旧3d7全绿当新候选通过，整体RETIRED仍未成立。

任务45初始fixture修正：实际媒体403来自备份的合成`ui41-asset`仍指向旧ui41素材目录，新ui45素材库保护正确拒绝；不改产品保护或读取旧媒体。当前人工Esc核对和关闭/重开截图完成后，B可仅以mode=ro备份当前ui45库到新`db/fixture-source-local-v2.sqlite3`，通过准确候选ProductionRepository既有`_repoint_asset`把该合成记录的path/file_path改为已复制的`ui45/library/source.mp4`，保留ID和其它属性/时间。新库仅初始准备，不修改原验收库，不写时间线、业务作业或真实数据；逐表比较并证明除该路径外无差额、源文件hash不变、0job。随后只停止自有API并指向新库，用相同94bf候选/env重启，登记新旧PID/PGID，沿原14:30:42绝对到期不续；普通GUI刷新再检查媒体/播放器及初始quality401是否重现。没有时间完成时留未验，不延长窗口。初次辅助请求误用Bearer的401不算产品错误，正确本地成员头辅助请求200也不能当浏览器通过。当前94bf CI run37736162435三项SUCCESS，已按实际head回读。

本轮变更`MAIN-DESIGN-IMPLEMENT-20261008-38`，主控Run沿用`run_e3cb6568939f`。绑定当前main准确代码对象`3fa9033608abad72ec30d68e984bf2f92e74d85d`、[DESIGN](../DESIGN.md)§2/3/4/5/7、实际领域状态与对应API。新分支只机械取得该对象，既有分支和报告保留。PR96 `a3e9c7b9dd51927f605272fda8bd6410bd707896`尚未批准合入；UI先基于main实施，主控随后在独立组合候选纳入PR96差额，特别核对CreationTaskWorkspace及设置保存行为。不能将组合候选当main已合入或把本地78个共享记录提交整体push。

**任务38 / UI**：用户可在真实软件使用新三栏、圆角矩形侧边卡片及分层交互，知识库资料集合/搜索/真实详情、设置紧凑属性与真实保存状态、剪辑预览/故事板/真实任务与版本均按v5.1工作。先完成公共外壳与三个代表页，再检查和迁移其它当前消费者；不以原型页面替换真实数据或扩大能力。owned为`apps/web/src/components/AppShell.tsx`、`useEditorPanels.ts`、`EditorShell.module.css`、`HomeCardDialog.tsx`及其直接测试；`apps/web/src/app/globals.css`；knowledge现有组件/样式/直接测试；settings/page.tsx、同目录样式与直接测试、LlmProviderFields及直接测试；CreationTaskWorkspace/同名CSS及已有直接测试。公共卡片的新源码/测试仅为上述真实消费者所需，可在components内新增；其它现有页面/组件的旧浮层消费者只允许完成本迁移所需的直接接线与视觉差额，不改变其业务规则。发现API/actions、持久化、领域、桌面或共享权威差额先报具体符号，不先修改；不安装新组件库。

UI是唯一Web写者及本轮迁移纵向负责人。退役目标：活动外壳旧188/280和64/0收起默认、设置内部固定导航栏、被替代的详情容器和全局Esc穿透/父卡重挂载行为、三个代表页对应旧视觉规则与保护旧规则的断言。必要重要操作仍保留480全局确认，不把所有模态框机械改侧卡。删除条件是新消费者有真实可见正证且保留现有业务行为；用定向源码/消费者检索作旧路径不存在证明。临时共存只限分阶段施工，不能作为整体RETIRED结论；旧候选与已审commit保留作源码回退，禁止用户数据回滚。

最小受影响组件检查先一次运行；提供准确SHA/父、文件差额、测试结果、消费者与退役清单、GUI入口和未验范围。允许现有依赖、无模型测试及新`/tmp/main-acceptance-20261008/ui38/`记录；实施阶段不启动GUI/服务/模型或资源探针。主控审查冻结后B同一候选实际GUI；UI收到准确缺陷继续有界修复和必要复验，不在派工或代码完成时把本目标交回用户。

**任务39 / A（MAIN-CREATION-PREP-20261008-39）**：只读PR96准确候选和`/tmp/main-acceptance-20261008/creation36/report.md`，定向复用既有合法参考的路径/方法，不扫描个人媒体。核对已实现和已过证据、仍缺的纯素材完整成片/Pi确认编辑重出/默认20%人物口型、真实入口、先后依赖、每次预算与退出条件。仅写新`/tmp/main-acceptance-20261008/creation39/`续验步骤；不得读取真实密钥、起服务/媒体子进程/模型/资源探针、安装、修改代码或预算。资源恢复后需新精确组合候选和Dispatch，不重复已有通过专项。

**任务40 / B（MAIN-UI-ACCEPTANCE-PREP-20261008-40）**：独立读取v5.1与main实际消费者、任务37报告的已过保存/重启证据，准备同候选1440/1280/1024、1200/1380边界、亮暗主题、嵌套Esc/外部点击只关顶层、父状态保留/焦点归还、真实资料版本/设置保存/剪辑控制的验收步骤。仅写新`/tmp/main-acceptance-20261008/ui40/`，不改产品/测试或跑服务/GUI/模型/探针；B发现缺口交UI，不自行修Web。主控冻结准确组合候选后另给普通UI有限窗口，隔离HOME/XDG/DB/output与已有合成数据，禁止mock API响应冒称产品链路，明确不覆盖真实模型/发布。每次窗口绑定精确SHA、PID/端口所有权、有限操作与释放；不与重模型并行。

任务39/40的succeeded只结算静态准备，报告分别在creation39/report.md和ui40/report.md；主控已核读并worker-retain，旧Dispatch不复活。UI本轮工作包为[WIL-205](https://linear.app/wiliiam/issue/WIL-205)，父WIL-10保持未完。默认人物的故事板与成片许可预算另有代码缺口，不能只等内存恢复或借纯素材预算通过。

**任务41 / B（MAIN-UI-ACCEPTANCE-20261008-41）**：承接本轮必需的独立真实UI验收，唯一证据根`/tmp/main-acceptance-20261008/ui41/`，pipefish源码分支与既有成果保持。第一阶段可按准确PR96 API对象`a3e9c7b9dd51927f605272fda8bd6410bd707896`准备隔离方法和最小真实仓库fixture；最终服务启动前必须换成主控冻结UI+PR96+当前设计文档组合的完整archive并核API blob不变。仅允许env-i下独立HOME/XDG/config/cache/DB/library/output/tmp/models/agent，排除.env；复用既有已安装venv/Node/Web依赖，不安装/build。准备可导入候选SourceRepository/ProductionRepository/本机成员初始化，创建一个可信合成owner、两条知识资料（其中一条同源V1/V2）与一条合成剪辑任务／两镜头／当前及历史时间线；不启动app.main/服务/GUI/模型/媒体工具/资源探针或生产作业。只读复制任务36具名合法16s色卡`creation36/software-first/library/source.mp4`与其参考方法，合成产物状态必须标为fixture，不冒称连续制作结果；不读取旧DB/token、真实用户素材/密钥。先给PREPARED准确环境、fixture范围、拟命令和退出方法，等待本Dispatch具名START_GUI。

START_GUI当前冻结候选为`3d7d7e1d4e02496221808dba99ec1e3c664ab0ed`（取代ef4/ce9启动权），独立review checkout `/tmp/main-controller-20261008/ui-v51/integration`：准确父链PR96 a3e9c7b→当前设计文档7dfbb72→UI38精确差额ef4b74c（来源3925e9b）→UI42检查脚本ce9a3a1（来源e89ee27）→UI43直接测试3d7d7e1（来源e7cbf3e），工作树干净，API/actions/lib差额零。交叠Creation确认/重出/画幅/复合6项检查通过；不重跑未变172矩阵。任务38源码succeeded已接受并retain，整体RETIRED仍待本同候选GUI。主控对任务41 Dispatch `ctx_955af68806bd`具名发送后才生效：API4571/Web3171、自己的Chrome profile/PID/window、最长30分钟绝对租约不续期，沿任务37已经验证的服务启动／同env API重启／退出方法；端口占用只报不杀他人。实际生产入口app.main、真实API与SQLite，不能mock响应/静态代页。执行ui40最小矩阵（可同一操作覆盖多项），必要普通参数提案／确认／一次撤销可写本隔离fixture，禁止开始制作／重出／模型、任意重媒体预览、收费服务或发布。设置只改非秘密provider/base_url/model；Key为空，既有真实Key证据复用未变后端事实，新界面保存仍需复验。不得创建／读取／修改登录或旧钥匙串，测试store仅本根不存在的具名空路径；如发现必须实际空store则向主控报具体原因后由主控限定处理，B不自动操作Keychain。正常页面GET的既有状态读取可用，但无独立HostResourceProbe或模型实测权。准备和GUI均无产品／共享权威／远端写权。

截图必须来自本候选实际可见页面，API／DB仅辅助持久效应；CSS视口亮暗／三尺寸／断点、嵌套关闭与不穿透、父草稿／滚动／焦点、真实V1/V2、保存重启、剪辑选择／播放／关闭重开逐项PASS/FAIL/UNVERIFIED。第一可见缺陷准确交UI与主控，同一候选可继续独立无依赖项；改变源码必须停止旧服务、保留证据和释放，再换新准确候选。结束只停止登记并核身份的自有PID/PGID，证明组退出、精确根无残余、4571/3171无listener；用户浏览器与数据保留。完整成员权限、Electron安装包、真实Key新GUI保存、完整制作／口型／发布不在本UI通过范围；任务41完整结果含必要GUI及释放，PREPARED不是完成节点。

## 任务36/37实施与验收记录（已结案，操作权收回）

任务43结算：主控已审单文件e7cbf3e702ffdb6339222464e3f84de651dcbf1f并接受succeeded/retain，精确组合3d7d7e1d4e02496221808dba99ec1e3c664ab0ed，相对ce9仅测试2+/1-，全部运行Web/API blob不变，目标case组合复验1通过80跳过。以下任务43原范围为执行历史，不授予继续修改；B41切换完整archive前先释放ce9，保留当前真实证据/fixture，重绑同一Dispatch；可明确按运行blob相同复用已过项，新候选补必要实际入口，未验项仍须真实证据。最终绝对到期仍13:28:13，不续期，禁止绕过focused gate输入；普通自有Chrome启动尺寸需AX内容frame与PNG验证，不能以flag直接计通过。

任务43 / UI（MAIN-UI-COMBINED-ASSERTION-20261008-43）：PR97 ce9前端CI真实554项中553通过、1失败，准确为CreationTaskWorkspace.test.tsx `previews and confirms a selected clip edit using only API facts` 2123旧按钮名“取消”；v5.1局部确认改为“返回详情”，组合PR96后的提案范围触发该路径。唯一UI线程基于自身clean e89ee27只改此直接测试，先对照准确组合ce9的目标case与真实消费者证据；不先判为断言错误，若发现真实取消/确认效果缺陷则提交证据由主控限定源码扩展。只修过时显示断言必须保留未提前确认、取消不写、后续API效果检查，不能禁用测试。一次必要目标复现/复验与diffcheck、clean精确提交、ui43报告；不重复554矩阵/安装/服务/GUI/模型/远端。B41可继续运行源码不变的独立GUI；新的测试提交组合后，旧服务须释放再换完整新SHA，未变运行blob证据可明确复用而非暗换版本。父PR96不自动合入。

任务42 / UI（MAIN-UI-CI-CONTRACT-20261008-42）：主控在组合ef4b74c实际运行CI必需`apps/web/scripts/modern-desktop-contract-check.mjs`，仅3项失败：旧`--sidebar-width: 220px`、旧`--inspector-width: 320px`以及`Modern Desktop System`旧文档标题。唯一UI写者已提交单文件e89ee271243b0e806ef3e7cc8dea6adedf77fb82，主控审查保留不相关真实业务/桌面/旧路径退役检查，接受succeeded并retain，不授予新增修改。机械组合最终候选`ce9a3a12c58e52e6538ba3ff143eeba3baad5335`，相对ef4只有该检查脚本3行差额，运行Web/API完全相同；主控该必需检查复核通过，未变组件证据沿用。草稿PR97依赖PR96，均未合入。B41先释放旧ef4服务与浏览器，保留初次真实知识页截图/焦点拒绝和已成功原生AX语义操作证据，再按同Dispatch新的具名START_GUI使用ce9完整archive；不能沿用旧SHA启动权。新窗口仍30分钟绝对租约，无模型/媒体/Keychain写权，fixture与241API blobs准备不重造；整体RETIRED仍待实际GUI。

主控已接受两条准确worker_done/outcome failed：本轮失败原因是实际资源与依赖阻断，不能改称实现专项失败或整体成功；全部自有验收资源已释放，线程已worker-retain，Run无待回收Dispatch。已审组合候选`a3e9c7b9dd51927f605272fda8bd6410bd707896`在[草稿PR96](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/pull/96)，main未合入；CI结果以实时workflow为准。两次软件准备内存准入拒绝，0start/job/Grant/model，当前完整成片/Pi修改重出/默认人物仍未完成。以下只保留本轮具体范围及真实证据依据，不授予继续启动或修改权；用户释放资源后需新Dispatch绑定精确候选与有限窗口。

用户于2026-10-08批准“可以，你开始吧”：执行已说明的完整成片及一次修改重出计划。PR95已由用户合入；两线程从产品基线 **fec1d13d8583d5c7e37de2467c4705713a0d3d2c** 新建独立分支，原A297c2de、B643c227及所有证据保留，不整体merge旧分支、不覆盖脏树。主控本地e4457a3已含该main；其共享说明仍由主控持有，不要求执行线程复制旧合同全文。语义REBASE_ACK说明目标、所有权和门禁后，直接开始下述已授权实现/最小验证；重模型和GUI需具名资源窗口。

整体可见目标：在真实软件中确认文案、连续生成真实配音/故事板/字幕/可播放视频，再由Pi形成一个修改提案，经用户确认进入后端并产生新输出。先明确选纯素材验基础链，再验现有默认20%人物/口型，不能把纯素材通过当默认组合完成，不能擅改默认。一次失败须显示真实原因并从失败处恢复，取消须释放自有进程和唯一设备许可；保留上游。源代码、HTTP/SQL、模型产物、GUI、音质及真实发布分别记证据，不冒称整体完成。WIL26/142/165保持未完，未满足全部验收不关闭。

**任务36 / A（MAIN-CREATION-20261008-36）**：A对制作结果纵向负责。先复现已证实的普通配音失败仅job/request失败而任务仍running的消费者缺口，按现有领域状态/仓库写入路径修正失败和恢复，不创造另一套状态机。追踪真实app.main的start/resume→DeviceRuntime→narration/montage/render；准备隔离软件入口、有限且显式的本次设备预算，缺少预算须可执行提示，不能将任务35的4GiB实验参数写成生产/Intel标定。owned仅`apps/api/app/creation_execution.py`、`main.py`、`member_permits.py`、`device_accounts.py`、`device_scheduler.py`、`narration.py`、`voice_clone.py`、`host_resource_probes.py`、`granted_subprocess.py`及它们已有的直接测试。montage/renderer/TimelineVersion消费或其它文件发现必要差额时，报准确符号和实际缺口，由主控有界追加；不得先改。禁止改Pi、runtime_settings、Web、桌面、共享文档/SQL/真实配置。最小验证用既有测试复现失败→恢复保留上游及取消释放；不重跑无关全量。提供clean候选、准确diff和首因、真实软件验收准备及资源释放方案。

A可写新`/tmp/main-acceptance-20261008/creation36/`准备文件，旧35只定向读合法参考音频和无秘密方法。env -i且导入前完整隔离HOME/XDG/config/DB/cache/output/temp；只读复用已安装API虚拟环境、模型运行时及唯一Qwen3-TTS0.6B8bit模型，不下载、不安装、不改权重/缓存。先静态准备、最小不加载模型测试，提交PREPARED包含本次精确Narration/Montage/Render/Host全部预算、合法合成文案/素材/参考来源、完整app.main入口和有限命令；不使用AST提取替代软件入口证据。模型、合成参考/FFmpeg媒体准备、产品服务或GUI需主控对本Dispatch的具名窗口放行；每窗口有限次数/时限、实际原元组身份、真实Grant、持续资源检查、自有子组注册和退出/设备锁与permit释放证明。未知/超限停止并报首因，不擅放宽预算或循环重试。默认人物路径与编辑重出有依赖的差额在其具体证据明确后继续安排，不复活旧实验。

**任务37 / B（MAIN-PIENTRY-20261008-37）**：沿现有软件设置和Pi业务工具链追踪“设置国内MiniMax→保存→重启回读→Pi创建任务/提出编辑→确认→后端执行”。先查真实创建/开始工具是否存在、调用哪条现有API、Key实际储存位置与读取路径，给出可执行GUI步骤；发现本范围真实缺口直接修复，不能只有假页面或将自然语言回复当执行结果。owned仅`apps/api/agent_tools/diary_tools.ts`及其现有直接测试、`apps/api/app/runtime_settings.py`及`tests/test_runtime_settings.py`/`test_workspace_llm_secrets.py`，`apps/web/src/app/settings/page.tsx`及其同目录样式/既有测试、`apps/web/src/components/CreationTaskWorkspace.tsx`/同名CSS及已有直接测试。不写A后端、secret底层/安全权威、桌面、共享文档/Linear。其它Pi运行器或共享API缺口报准确差额给主控，不能越界。遵守DESIGN及相关领域章节。复用业务命令/鉴权/设备许可，不把产品Pi任务等同任意操作系统执行线程，不增任意shell/文件/绕过用户确认能力。DeepSeek保留现有支持，不伪称已真实调用。

B初段允许只读源码追踪、具名文件实现及最小无外部模型测试、新`/tmp/main-acceptance-20261008/pi-entry37/`验收准备；不读Key文档/Keychain/真实用户数据、不起GUI/产品服务、不调用收费API/加载本地模型。真实Key仅主控按既有具名授权处理隔离验证；后续冻结候选、A资源释放后由主控在本Dispatch给出GUI/国内MiniMax窗口和秘密输入方式。输出准确业务工具清单、实现候选/专项证据、真实保存重启验收步骤及首次实际缺口。准备或派工不是最终交付节点；主控审核后继续必要集成和完整成片/修改重出验收，PR合入仍逐次批准。

任务36/37有界补充（C1，目标不变）：A已定位`production.py:ProductionRepository.fail_creation_task_stage`自行连接，与`fail_unit`事务嵌套会导致同库写锁；追加A owned `apps/api/app/production.py`仅该方法和复用现有事务所需的最小直接接线，保留旧直接消费者行为，不重构整库。B追加owned `apps/api/tests/test_assistant_pi.py`仅`test_edit_tool_separates_action_and_projects_actual_preview`及直接工具边界专项，禁止改运行器/API专项。B可落实`preview_edit`已声明的先读取任务、每轮失败不自行重试边界：须以执行真实TS工具的未读取拒绝及失败后第二次POST零发送证明，不添加其他猜测防护。

任务36有界追加：用户已批准的“一次Pi修改并重出”进入Migrate，沿既有[架构§5.3/§5.5](../docs/system-architecture.md#53-自然语言局部修改)及领域TimelineVersion权威实施，不变更产品对象或默认业务入口。A仍为纵向负责人，B仅负责具名工作台消费。已定位退役目标为`cpu_render_facts`对所有`created_by_operation_id`的泛化拒绝、`CreationExecution.retry`对已失效输出的succeeded直接返回，以及工作台相应不可重出分支；新路径为用户确认编辑→当前已持久TimelineVersion/计划/字幕事实→既有继续制作设备队列仅render→新产物及新parent版本，原配音与故事板保留。追加A owned `apps/api/tests/test_montage_pipeline.py`仅当前版本、受许可重出直接专项；无需新增montage/renderer写权。A须先冻结失败状态小候选，再实现重出差额，不顺手扩大到人物/独立音轨/图层。

Migrate验收：纯素材crop编辑确认后以真实现有start/resume接线得到新render Grant，新输出属于当前版本、前后可播放且画面变化符合提案；没有新增配音/编排模型运行，重复重出幂等。旧版本/并发版本或源输入变动、失效许可无错误产物/Grant，取消释放、旧输出只作历史不可冒充当前；受许可执行中自产新版本的合法推进不能因新版本摘要反过来误判自身stale。现有拒绝人物/未接音轨/图层保护保留。源码层正负及不存在证明后方可声明旧泛化拒绝RETIRED；整体仍需同一冻结候选真实软件/产物。不得靠只删guard或调用无许可旧render入口完成，不删除用户版本/素材，不静默回滚到旧执行器。临时共存仅冻结候选前的独立分支与历史输出；集成后活跃纯素材重新输出入口全部走同一队列，GUI旧不可用分支也须退役。B在A报告真实后端能力后追加现有工作台动作，不提前写假能力。

任务37设置GUI窗口（非模型，冻结f9ed34c7a57c894711507d253cac81062058c11e）：主控已审PREPARED、521源码blob清单、真实app.main入口及exact新store wrapper。主控可在新`creation36`控制证据根创建/解锁`/tmp/main-acceptance-20261008/pi-entry37/config/task37.keychain-db`空临时钥匙串，保持用户search list/default前后相同；只读取既有具名`minimax-validation-20261007`条目到RAM，以原生密码输入stdin交软件页面保存，不预先用API/脚本写入task37工作区Key。B在本Dispatch具名START_SETTINGS_GUI后仅运行所报services.py start、隔离Chrome新profile与原生GUI非秘密操作；API4567/Web3167、30分钟lease，重启API仅同env/DB/store，允许一次保存后重启及一次空Key保存验证保留。主控输入Key、审安全状态/非秘密配置、RAM秘密扫描并最终锁定新store；B不读取Key、剪贴板、文档或登录store。模型/制作/Pi文字请求/测试连接均不在此窗口内；A只静态源码工作，不执行重模型。两方记录自己的准确PID/PGID，B最后停止服务与浏览器、证明端口释放。保存重启通过仅证明当前软件配置GUI，不证明包内或整链。

任务37真实GUI首因有界修订：主控实际密码输入和保存后，原生task37条目与授权Key相等，非秘密配置已保存；页面URL却显示`保存配置失败：NEXT_REDIRECT`，本轮整体保存GUI判FAIL。源码`apps/web/src/app/actions.ts:updateRuntimeSettingsAction`把成功redirect放在catch捕获区。追加B owned该文件仅此动作及已有直接专项（若存在），按真实控制流修复，禁止顺手改其它server actions；以真实GUI成功保存、API同env重启及回读验证，不新增模拟store或实现镜像测试。旧f9候选及首次FAIL原始证据保留。修复clean新SHA提交主控后可在当前同Dispatch/未延长30分钟租约内，先停止旧自有Web组，建立新准确archive及独立源码目录（不覆盖旧code证据），同env/DB/store重启API/Web到新候选并做一次空Key实际保存保留、API再重启回读；主控另核原生值相等/秘密扫描。原模型、制作、用户数据及外部调用禁令保持；未知释放即停止，不扩大进程终止范围。

任务36发布并发边界C1：A已定位渲染末尾计划与新版本分别提交，`create_timeline_version`没有比较当前父版本；这是已定义当前版本事实源的实际写入竞态。追加A owned `production.py`仅现有`create_timeline_version`及`save_creation_task_montage_plan`的借用connection/当前版本比较，以及已有版本直接专项；由main现有渲染末尾在同一事务比较当前版本并发布计划和新版本，保留独立旧调用的默认行为。不新增事实源/迁移用户数据/全库重构；并发编辑时真实失败后的旧计划写回也不得覆盖新crop。最小真实仓库/许可正负专项覆盖CAS拒绝后新计划与版本完整保留、合法自产版本提交、重复调用，媒体执行替身证据单列，后续同候选真媒体验收保持。

任务36首份软件整链准备窗口：37保存/重启分项和全部自有服务/浏览器释放已由主控接受，task37新store已锁，无模型/收费调用。冻结主控集成候选`61fca1387dd6283b36505acd6db5b1eb010b9a0d`（含A失败恢复、B工具门禁与实际redirect修复）；A未提交的编辑重出差额不混入首次成片。允许A在新`/tmp/main-acceptance-20261008/creation36/software-first/`核准确archive/源码、完整隔离env、13模型stat/runtime，只读复用旧合法合成参考；一次≤30秒/≤1MiB/16秒双色FFmpeg素材准备及既有真实本机owner/授权声音/任务文案草稿夹具。文案为“清晨的阳光洒进房间。泡一杯茶，整理今天的计划，从一件小事开始，认真记录每一次进步。”，Plan15秒/显式纯素材0，CPU Render仅max_duration_seconds15及max_pixel_seconds31104000按输入修改，其它proposed-budgets字段含Narration120/parent180不变；fresh swap+64MiB须取本窗口当次样本。允许一次无模型真实HostResourceProbe和精确OS边界预检，允许实际app.main API4569（无Web/GUI）自有组有限20分钟lease、loopback仅本服务所需连接，不给外域网络/下载/安装/Keychain/其它模型权限。采用现有慢权重读取/DEVNULL边界，不放宽根外写入，写清精确profile；若系统沙盒导致实际软件入口不可行，先报首因不静默去掉。

A在准备窗口可以合法夹具初始化并由真实HTTP确认文案和读取execution/preview，不能先POST start/resume或加载模型。交PREPARED具体源码/env/inputs/probe当次decision及PID/PGID/端口、OS profile和精确一轮整链命令；主控对本Dispatch的START_CREATION36才允许一次原API start→真实Grant→Qwen→确定性montage→受许可FFmpeg/字幕成片，父180秒/各阶段原120/10/60秒，首因停止不自动重试。已编辑输出另90秒窗口须后续冻结重出候选；本节不给人物/真实账号云/GUI/Pi/生产标定。软件媒体窗口期间B仅静态工作，A亦不并行测试/别的重进程。

任务36/37同候选媒体准备C1：A重出53bae98和B工作台5f06e77均已clean/最小专项通过并机械组合，冻结`91143e8ffc84db64c89c6d4ed431ad7a7d186c86`；主控已审范围和B组件测试组释放。首份61fca准备因内存5779849216<5905580032拒绝，0start/job/Grant/model、API/全部helper/permit已释放，原证据保留。允许A在同software-first根建立独立`source-final`精确archive/清单/新env和fresh-prefixed证据，不覆盖原源码/首因；复用已确认合法owner/script/素材/DB及一次已生成双色媒体，不重复造素材、执行确认业务或OS边界检查。相同profile与4569仅本地连接、自有API组/新20分钟lease、预算所有数值不变；一次fresh swap基线和真实HostResourceProbe（最多原3秒持续窗口）在API启动后核实际准入。先回PREPARED及准确PID/完整decision，再等本Dispatch的START_CREATION36；拒绝即停止/释放，不循环测、不加载模型、不调cap。B和A其它测试/GUI/模型此窗口均暂停。首次完整制作父180秒/各阶段120/10/60秒仍具名START后才可执行一次，编辑重出另有限窗口后续放行。

任务36 CI首因修订C1：PR96/91143e8的Frontend和Desktop syntax通过，Backend实际2失败/1614通过/20跳过；两失败为既有`test_subtitle_exact_edit_uses_typed_operation_and_keeps_segmented_narration[False/True]`的重复渲染次数2≠1。A可恢复owned `main.py`仅本次render函数和已有直接专项，定位新增timeline identity对具名无Grant诊断消费者的幂等影响，保持受许可当前版本绑定/CAS/新工作台路径不回退、不复活旧无许可产品入口；最小验证失败的2参数及实际main许可正向消费者，不全量本机测试/服务/模型。新增差额另clean提交并由主控审核组合、更新PR与CI；旧911候选及两次真实准入拒绝/全释放证据保留，本机资源未恢复前无新媒体窗口。

用户于2026-10-08回复本机人物视频与声音均为本人，要求主控直接查找：允许主控定向只读现有素材目录、`legacy-movies/agent.sqlite3`的素材/声音登记元数据，以`mode=ro,uri=True`读取，不访问账号/秘密或修改原库。已定位素材`video-inbox/历史成片_待审核/VID_20260529_122252_preview.mp4`（登记a_roll/approved/rights_confirmed）和默认`acceptance/real-voice-20260824/reference/reference.wav`；允许主控在新`/tmp/main-controller-20261008/creation36/user-media/`仅一次≤30秒、线程1的FFmpeg取前段单帧视觉核验，记录自有PID/退出及原文件stat，不启动模型。人物与真实声音的模型处理/截段须随后具名有限窗口，不扩大A/B当前模型权；纯素材链继续独立推进，用户原件保留。

## A：连续制作与账号许可闭环（候选保留，写权收回）

`MAIN-CREATION-20261007-11`：用户已批准实施审查缺口。准确产品基线 `7289f4fa3aa4d2fb61f9a3a362991a08885d14ca`；manta clean 后新独立 `codex/wil26-creation-permits-20261007`，不带旧候选混合历史。先读本合同、[认证 §5/§9](../docs/auth-workspace-boundary.md#5-后台任务)、[架构 §5.2/§5.3/§3.12](../docs/system-architecture.md#52-新任务) 与 `/tmp/main-controller-20261007/pi-review/review.md`；原账号方案仅作实施建议，以新契约为准。语义 `REBASE_ACK` 后直接实施，不等第二次 START。

目标：真实命令从已确认文案的 start/resume 经唯一设备调度器推进配音、故事板、成片；原始账号/设备/workspace 持续核验及恢复不伪造 local 成员，不靠预先生成上游或旧执行器绕过许可。主控唯一集成者，WIL-26/142 和关联 35/159 的边界分别保留。 A 已 ACK 并进入本单实施后，主控将 WIL-26 从 Backlog 移到 In Progress；WIL-165 既有 In Review 保持，未经对应验收不关闭任何卡。

owned：`apps/api/app/creation_execution.py`、`member_permits.py`、`device_accounts.py`、`main.py`；直接既有 `tests/test_creation_execution.py`、`test_member_permits.py`、`test_device_accounts.py`、`test_device_runtime_wiring.py`。有界修订 A11.1：根据 A 已回报的真实配音子进程缺口，追加 owned `apps/api/app/narration.py`、`voice_clone.py`、`host_resource_probes.py`、`granted_subprocess.py`，以及既有 `tests/test_narration_commands.py`、`test_voice_clone.py`、`test_host_resource_probes.py`、`test_granted_subprocess.py`（若存在）的本单直接消费者；不得扩大到未列的模型 worker、montage 或 renderer，必要差额先报主控。允许新增一份由 `supabase migration new device_creation_permit` 创建的具名 SQL 文件，回报准确路径；不改已有迁移。有界修订 A11.2：根据已复现的撤权后取消已落库但 `view_for` 拒读而跳过取消通知，追加 `apps/api/app/device_scheduler.py` 仅 `cancel` 的最小真实回执及既有 `apps/api/tests/test_device_scheduler.py` 本单取消消费者；不得放宽 `can_view_detail`/普通查询、跨对象 `can_manage` 或队列摘要。取消先核原请求所有权，实际写入后仅回原单元的必要取消状态，不能用捕获异常猜成功；取消/释放后不因此发新许可。其他调度器/运行时/资源/接口路径如确有必要，先交具体证据由主控修订，不顺手扩写。禁止写 B 的 Pi/体检文件、Web、桌面、共享权威、真实配置/数据。

账号权限冻结：仅 native 后端可用当前设备 proof + 原作业设备/账号/workspace 元组读取许可结果；新 RPC 可以明确授予 anon/authenticated 的仅该函数执行权，调用必须校验高熵 proof、设备未退役、成员 active、有效 workspace membership。固定 search_path、撤销 PUBLIC 执行、无表读取 grants、无私人内容/名册/proof/hash 返回；交互命令始终先验证 JWT，后台不持久保存 JWT/refresh token/proof。所有原元组持久化于现有事件及作业，不创建第二名册。云未知阻断新执行/成功提交，授权失效保留任务/资产；取消释放即使成员失效也必须完成。仅消费现有 paused/left，不新增管理 UI 或停用写 RPC，不宣称新设备自动注册或真实云权限通过。

阶段执行与退役：A 为纵向迁移负责人。新阶段必须使用专属的显式设备预算，不借用 FFmpeg 成片预算；未知或未标定保持可执行的就绪失败，测试预算仅证明接线，不成为本机生产默认。配音模型必须在已登记的隔离子进程内加载、推理并随整个进程组退出释放，禁止在 API 常驻；预算至少约束文字/参考音频输入、阶段总时限、进程组内存及主机余量、输出大小，取消/超限/许可失效须终止自有进程组并证明释放。所有配音后处理也携带同一 Grant 和剩余时限，阶段内不得新开无许可 subprocess；未知释放不归还许可或提交成功。故事板只允许已确认文案及授权素材元数据编排，隐藏口型/解码/模型工作仍须专属许可，不能用纯素材通过覆盖全部组合。补充配音/故事板在同一调度/资源授权下的实际执行、持续核验、子进程/许可释放及结果检查；严禁直接删除非render保护或把不支持的动作放到旧线程。受影响 narration/montage/render 消费者统一；旧无许可执行只允许已有诊断专项继续保留，不成为产品默认回退。人物/音轨/图层/独立编辑组合发现资源执行缺口时提交证据和最小下一差额，不放宽预算或冒称支持完整渲染。

退役验收：列出 start/resume/recover、单步重做及现存 `CreationExecution.execute/_work` 消费者；产品入口只能写唯一设备队列，旧无许可默认入口须停用并给出不存在证明。已有诊断专项可显式保留至本候选真实连续制作验收；不作为产品回退，不删除用户作业或媒体。真实模型或云权限未验时只能交接已证明的源码阶段，不能称整体迁移完成。回滚只能回到已具名验证的候选，不能静默重启无许可线程。

最小验证：隔离 HOME/XDG/config/SQLite/model/temp/output；隔离 PostgreSQL 实际 RPC 权限正负（错 proof/设备、paused/left、撤空间、retired、anon直读拒绝），测试进程不碰真实 Keychain。验证 confirmed-script-only 真正触发配音/故事板回调并到 render，重复开始幂等、失败恢复保留上游、取消释放；账号提交/授予/实际执行/非取消提交结果/重开均复核原元组，unknown 不派发，local/跨空间与预算门禁不回退。仅必要回调替身证据不能算真实模型、真实账号 GUI 或成片验收；后续由主控在冻结候选具名验证。源码完成后一次最小直接专项，不全量/构建/收费/云部署，交准确候选/diff/测试/退役边界与释放证明。

## A：已安装 Qwen 本地配音有界实验（证据保留，操作权收回）

任务17已准确失败结案：Grant前名册读路径写初始化触发SQLite锁，模型启动零次；父进程、设备锁和许可已释放，保留失败夹具及报告。本节不再授予实验、模型或重试权；下一实验须新具名候选及START。

`MAIN-TTSMEASURE-20261007-17`：仅在 A11 最终含显式子进程 HOME 隔离的 worker_done 与 clean 候选被主控接受后，用同一 manta 新 Dispatch 执行。准确冻结候选 `702392d11910138f2ef028b64f2bd42c019212d6`，不改产品或文档。本单依据用户明确指示查找并使用外接盘已有模型；旧任务禁止模型的范围被本节唯一具名实验取代，其他限制不变。

只写新 `/tmp/main-acceptance-20261007/qwen-measure/`，archive 本单准确候选 API app/model_workers 后核 blob，导入前 HOME/XDG/DB/config/library/cache/output/models/temp 全隔离。只读复用 `/tmp/manta-wil108-20261007/venv/bin/python` 的 API 依赖与 `/Volumes/Dujiangyan-VM/AI视频日记/model-runtime/bin/python` 的模型运行时；模型仅 `/Volumes/Dujiangyan-VM/AI视频日记/models/qwen3-tts-12hz-0.6b-base-8bit`，在自有 models 创建该准确目录的软链接，不修改权重/安装清单/外接缓存。不得读取其他用户媒体、真实Keychain/账号/密钥或旧数据；不得下载模型、联网、安装升级依赖、起产品服务/浏览器、云调用、真实注册、安排定时生产。

目标是一个受真实 DeviceScheduler Grant 与现有 NarrationGenerator/Qwen3TTSSubprocessProvider 执行链保护的实际短中文配音实验。只允许现有 macOS 内置 say 的合成参考，不下载声音；say/FFmpeg 仅具名夹具准备、自有进程组和30秒界限，不把系统语音产物当 Qwen 输出。参考≤10秒/1MiB、目标文案≤12汉字、一段、产物≤2MiB。参考文字、授权声音/本机owner及素材登记只属隔离夹具。真实 worker 必须是候选 qwen3_tts_worker，不能替身返回结果或绕过资源 probe。

这是实验初始保护上限，尚非生产标定：NarrationBudget 明确 max_text_chars=80、max_section_count=1、max_reference_bytes=1048576、max_duration_seconds=15、max_output_bytes=2097152、max_threads=1、max_timeout_seconds=120、max_process_rss_bytes=2147483648、max_process_threads=128、runtime_sample_seconds=0.1、additional_memory_bytes=2147483648、additional_disk_bytes=8388608。主机至少留1610612736字节可用内存；准入可用必须足以覆盖上述2GiB额外预留及余量，swap上限仅取本次执行前实际值+67108864字节，不把原有swap当模型增长；磁盘至少留1GiB。其余 HostBudget 初始实验上限明确 max_cpu_percent=75、max_disk_io_bytes_per_second=67108864、max_unit_input_bytes=2097152；全部仅本单保护上限，不写生产配置。先用真实探针核准入，不能调低余量、加大预算、伪造 idle/resource 或借CPU render预算硬跑。一次≤120秒，父验收总限180秒，自有子进程组实际退出后才能释放许可。

先交新根准备、真实机器准入测量、预计命令与准确字段供主控只读核对，再等主控本 Dispatch 的 START_MEASURE（避免 GUI 同时占用主机）；START只允许这一次实验。父侧每0.1秒记录实际组RSS/线程、主机可用/交换增量及阶段时间，记录MLX可测内存与不可测项，不仅依据模型文件大小。若真实探针不能准入、缺依赖、worker错误或触及界限，停止并交准确首因/释放，不盲重试、不临时扩大上限。只在该实验成功后形成观测数据和下一生产标定建议，不将一段成功外推到长稿、多用户、Intel或音质。

报告 `/tmp/main-acceptance-20261007/qwen-measure/report.md`：准确SHA、真实Qwen产物及ffprobe信息、时间/峰值/MLX与RSS边界、子进程/组/许可释放、既有权重只读证明、首个阻断及下一最小差额。软件 GUI、云身份、五人设备、人物口型、完整制作仍未验，不关闭卡或远端操作。

## A：本机名册读路径 SQLite 锁修复（候选保留，写权收回）

任务20的87834d3c8fd953e8cb7ea92cb8d0a20d3b8e5700与53项不同相关用例证据已由主控接受，许可/锁释放且clean；本节不再授产品写权。

`MAIN-ROSTERREAD-20261007-20`：任务17 failed worker_done 已由主控接受，报告及真实释放证据保留；同一 manta 新 Dispatch继续已授权流程修复。准确基线 `702392d11910138f2ef028b64f2bd42c019212d6`，在当前clean的连续制作分支追加局部差额，保留旧候选。先读本段、任务17报告最终首因和认证§5/领域§11直接相关章节，语义 REBASE_ACK 后实施，不另等START。

仅 owned `apps/api/app/local_members.py`、既有 `apps/api/tests/test_local_members.py`、`test_local_members_auth.py` 和 `test_member_permits.py` 的直接本单消费者；不改scheduler/creation_execution/member_permits生产逻辑、API契约、Web、模型、共享权威或其他专项。若真正必需跨文件，先提交证据由主控修订。

实际首因是 DeviceScheduler.pick_and_grant 的 BEGIN IMMEDIATE 内持续身份核验，经 MemberDeviceAccess.find_member→list_members 在第二连接 executescript建表/seed及token回填写入同一DB，触发 database is locked。将已有初始化/回填写边界与普通名册/令牌读路径分离，使已有事务中的核验只读取最新持久名册。不能缓存/伪造身份、复用旧成员状态、改变事实源、绕过持续撤权检查、降低锁/准入条件或加等待重试。保留新库自初始化、旧库token一次迁移和稳定性、真正并发五人名额预留、owner保护/成员删除/role更新与现有接口。

用一个真实 LocalMemberService + MemberDeviceAccess 的 DeviceScheduler.pick_and_grant 同库事务直接专项证明不死锁且实际Grant，并对同一可信名册的撤权后阻断作最小验证；资源探针可明确测试夹具，身份不可替身。现有freshDB/token回填/seat并发专项按影响挑选最小复用，不全量、不镜像实现测试。普通读不得发DDL/seed/backfill写；检查真实生产构造与调用时序，不引入通用兼容层。

仅新证据 `/tmp/manta-roster-read-20261007/` 和具名源码写权，无服务、模型、网络/安装、Keychain/真实用户库/账号、push/PR/Linear或重跑17实验。交精确SHA/父/净差额、真实最小检查、初始化与读边界、不扩大身份/权限的证据及释放，报告report.md。模型预算/真实性/GUI/云/完整制作仍未验，本修复不关闭WIL26或宣称整体迁移RETIRED。

## A：修复后同一组合候选有界 Qwen 配音验收（证据保留，操作权收回）

任务22真实Grant后资源复核拒绝而准确failed结案；未证明Qwen加载/推理，无音频，父/子组/许可已释放。原22根/现场只读保留，禁止重试与模型操作。

`MAIN-TTSFIXED-20261007-22`：A20 worker_done与真实Grant/撤权/释放经主控接受，同一manta新Dispatch。产品与权威写权零。冻结主控机械集成候选 `cb26f49e339d303e56f1f881fb60f071ea7e6e59`（独立review工作树 `/tmp/main-controller-20261007/creation-wave/integration`），准确包含PR94已合入f024132、A11的c965e831/702392d1、B19的d4ebf2d、唯一认证§5及A20的87834d3差额。只读此准确Git对象archive并核blob，不用自己旧branch SHA或main未冻结树，也不能修改集成工作树。保留任务17失败根，不原地重试。

仅新 `/tmp/main-acceptance-20261007/qwen-measure-fixed/`，导入前HOME/XDG/DB/config/library/cache/output/models/temp隔离，scheduled生产OFF，不导入main/起服务。只读复用已安装API Python `/tmp/manta-wil108-20261007/venv/bin/python`、模型运行时 `/Volumes/Dujiangyan-VM/AI视频日记/model-runtime/bin/python` 和已装 `models/qwen3-tts-12hz-0.6b-base-8bit`（完整外接盘路径同前17），自有models软链接且缓存/HOME均新根。权重/manifest/runtime只读，不加载其它模型、联网、下载或升级，不触碰真实用户媒体/库/Keychain/账号。

新可信本机owner、授权合成声音和实际已确认一段文案登记于新DB；参考由内置say生成≤10秒/1MiB，目标≤12汉字，say/FFmpeg仅准备且自有进程组各30秒限。不能把系统say当Qwen输出。实际候选DeviceScheduler与MemberDeviceAccess/CreationJobOwnerVerifier持续核验→真实HostResourceProbe→Grant→NarrationGenerator/Qwen3TTSSubprocessProvider→未改qwen3_tts_worker短配音，不能伪造ready/identity/resource或绕过Grant；只这一段，不自动下一阶段/全作业成功。

沿用同一初始实验保护上限且仍非生产预算：NarrationBudget max_text_chars=80,max_section_count=1,max_reference_bytes=1048576,max_duration_seconds=15,max_output_bytes=2097152,max_threads=1,max_timeout_seconds=120,max_process_rss_bytes=2147483648,max_process_threads=128,runtime_sample_seconds=0.1,additional_memory_bytes=2147483648,additional_disk_bytes=8388608；HostBudget max_cpu_percent=75,min_available_memory_bytes=1610612736,max_swap_used_bytes=执行前实际baseline+67108864,min_free_disk_bytes=1073741824,max_disk_io_bytes_per_second=67108864,max_unit_input_bytes=2097152,sample_seconds=0.1。准入可用内存至少3758096384，不能因本机当前紧张降低余量/增大cap/假idle。实际阶段120秒、父总180秒，0.1秒记录组RSS/线程及主机内存/交换增量/阶段时间/采样间隔；MLX allocator不可测明确UNAVAILABLE，不把RSS冒充GPU占用。超限、撤权、失败立即终止本单自有组并以真实锁/许可/组事实证明释放；不重试。

先完成新根准备、准确blob/路径/预算/资源真实预测和拟执行命令报告主控，然后保持PREPARED并等本Dispatch START_MEASURE。B21 GUI/服务未释放前不得模型加载/实测，即使资源预测通过。准入预测不足只报告当下条件不硬跑，主控协调可用内存；一次START只允许一次受准入实验，独占attempt marker避免重复。无首因修复权/无新源码写，不盲重跑旧17。

报告新根report.md，准确组合SHA、实际Qwen音频/ffprobe、首次首因或成功观测、真实父/子组/Grant释放、既有模型stat只读、不外推长稿/Intel/多用户/音质。不关闭卡/远端，不称GUI/云身份/完整制作通过；主控实际人物口型与独立编辑重输出缺口仍留在整体目标中。

## A：资源拒绝准确诊断及退役CI消费者同步（任务24已结束、候选保留，无写/操作权）

`MAIN-RESOURCEREASON-20261007-24`：任务22 failed worker_done与父/子组/许可释放已由主控接受，同一manta新Dispatch恢复已授权纵向修正。保存87834d3原连续制作分支，从主控main Git准确组合对象 `cb26f49e339d303e56f1f881fb60f071ea7e6e59`（integration review branch）建新独立clean `codex/wil26-resource-reasons-ci-20261007`，只机械取得对象，不merge旧混合历史。B23冻结cb26做GUI，产品写权零且与本单无文件重叠。

仅 owned `apps/api/app/host_resource_probes.py`、`apps/api/app/device_scheduler.py` 中assert_grant的拒绝诊断接线、既有 `apps/api/tests/test_host_resource_probes.py`、`test_device_scheduler.py`、`test_montage_pipeline.py` 的直接失败消费者。main/creation_execution/member/声音/avatar/Worker/Web/预算设置/共享权威无写权，真正需要越界先报精确证据。新证据 `/tmp/manta-resource-reasons-20261007/`。

实际阻断：任务22在获得真实Grant generation1并启动子进程后仅0.738秒被continue_unit复核非True拒绝；HostResourceProbe._sample只返回bool/None，未记录当次CPU/可用内存/交换/磁盘空间/IO门槛与未知采样原因，故不能判定具体首因。修正为同一次真实采样绑定的明确决策/拒绝证据，运行中失败能保留准确实际指标与阈值/不可读原因供用户修复与主控标定，不引入泛日志平台或宽泛兜底。新说明须来自触发拒绝的那次probe，而非事后另采/全局旧snapshot。保留全部阈值、未知阻断、单设备许可、持续授权/撤权、取消/组释放、真实预算缺失拒绝；不增加delay/retry/cache-ready，不关闭IO/CPU/内存任何检查，不依当前猜想扩大budget或写生产默认。

CI唯一失败文件test_montage_pipeline.py，共22失败/1550 passed/20 skipped，原日志与简短失败列表在主控creation-wave/pr95-backend-failed.log及pr95-backend-failure-summary.txt。它们仍从旧公开POST montage/plan、montage/render和未接许可的局部配音重做触发先前链路，与退役契约冲突。逐项只同步已真实改变的消费者：公开旧入口必须验证明确拒绝、无重制作/无部分数据成功；仅先前保留的诊断专项可直接使用现有诊断helpers检验原算法/谱系/字幕/撤销，不恢复产品fallback或新增测试开关。保留底层实际谱系/逻辑引用/模板与字幕/声音时钟/产物失效、使用状态及授权限制的有效断言，不能批量把期待200换409、skip/xfail/删除关键验证来过CI。需要新受许可路径才能成立的完成判据准确列缺口，不伪造产品已支持。人物/独立编辑/局部配音真实能力仍是整体待完成目标，不能因为旧测试退役就删除要求。

最小验证：新增准确拒绝/未知分支的直接可复现实证与不放宽阈值断言，现有probe/scheduler受影响专项；只跑22项CI失败参数集合及真正必要的直接下层断言，不重复105或全量矩阵。可以一次无模型真实HostResourceProbe读取以证明诊断与实际测量值对应，0.1秒上下；不复测22、不启动模型/媒体/服务/浏览器、不访真实库/Keychain/云/账号、不安装、push/PR/Linear。本单模型实验写/操作权零。有界修订A24.1：允许22失败集合中既有test_silence_edit_real_preview_confirm_and_undo唯一一次真实FFmpeg/FFprobe专项，仅新证据根内8秒64x64合成视频与8秒合成音频及该既有diagnostic renderer所需输出/帧检查；不改测试输入或帧/像素/时间线/撤销断言来避实证，不运行其它媒体矩阵或模型。整体120秒父watchdog、自有进程组，超时仅终止本单组并核退出，记录实际产物和释放；必要子进程上限可仅由隔离测试启动器约束，不改生产预算/渲染路径。该诊断FFmpeg专项不证明当前产品已恢复独立编辑出片。

交独立SHA/父cb26/严格净差额、每个CI失败对应最终有效判据、精确资源拒绝证据和最小用例、测试释放及clean。主控机械集成原PR95并只重新核新CI；B23未变化GUI/实际Pi证据按blob与消费者影响复用。下一次真实Qwen需新冻结候选/具名根与START，不能从本单拿到模型权。不关闭WIL26/整体RETIRED，不提出源码以外发布批准。

## A：准确资源诊断后的短 Qwen 实测（任务25已结束、证据保留，无写/操作权）

`MAIN-QWENDIAGNOSED-20261007-25`：任务24 succeeded worker_done、严格四路径差额/37个不同专项、十份permit与锁释放已接受；主控已机械cherry-pick45e80fc到准确组合 **b6ecba2f13d99ca7f5d2d29c2846dd2fa7756523**，其parent cb26，产品tree含A24唯一诊断修正。B23 actual GUI所有PID/组/端口与主控秘密扫描/store锁定完成，B26仅静态代码施工，模型窗口测试/GUI保持HOLD。复用同一manta visible终端，新Dispatch；保留45e80原分支/代码clean，本单无产品/测试/权威写权。

只允许新 `/tmp/main-acceptance-20261007/qwen-measure-diagnosed/`，准确b6ec候选archive及blob清单冻结；旧qwen-measure-fixed/A17只读定向复用已明确合法的短参考WAV、无Key环境和测试准备方法，绝不写旧DB/attempt/session。只读已安装外盘Qwen3-TTS0.6B8bit完整模型13文件stat与model-runtime/bin/python；新模型根仅本Qwen软链接，不下载/安装/更改用户模型/运行其他avatar/VL模型。新HOME/XDG/config/cache/library/output/temp/DB/device/agent，production scheduled OFF，设备预算仍未标定，不能修改生产默认。

真实LocalMember owner/MemberDeviceAccess/CreationJobOwnerVerifier与同源真实DeviceScheduler/HostResourceProbe；新的合成task/job原owner/workspace明确，真实授权持久化、真实generation、子PID登记先于exec、进程组检查及释放均不替身。短参考“这是一段测试声音。”与目标“今天阳光很好。”，单段/CPUthreads1；不运行整链或CPU成片/人物。保持A22全部初始实验上限：NarrationBudget文字80/段落1/参考1048576B/音频15s/输出2097152B/线程1/阶段120s/组RSS2147483648B/组threads128/sample0.1s/追加memory2147483648B与disk8388608B；HostBudget CPU75/minavailable1610612736B/maxswap真实执行baseline+67108864B/mindisk1073741824B/maxIO67108864B/s/input2097152B/sample0.1s。准入≥3758096384B，不降余量/放宽cap/关闭任何检查或假idle。父180秒、阶段120秒；取消/超限/首因失败仅终止自己组。离线与写入边界复用既有OS沙盒准确限制，无外部网络/真实库/Key/云/安装/下载/其他模型。

先新根准备、b6ec源码完整blob与模型stat、真实无模型资源预测/预算、拟执行命令/合法owner准备证明报告主控；停PREPARED等本Dispatch START_MEASURE。一次START仅一次attempt marker/真实受准入短Qwen实验，无重试、自动下一阶段或整作业succeeded。若准入拒绝准确当下指标与阈值，不能硬跑。

本轮必须捕获**实际触发拒绝那次**HostResourceRefusal.decision并dataclasses.asdict写本新根measurement，完整declared/checks/reason/采样间隔/门槛留证，产品str仅复用A24自然中文，不用事后liveprobe倒推。其他首因保留真实stack/阶段与子exec ACK、是否已加载/推理的可证事实；无音频不能说Qwen成功。父0.1s遥测组RSS/threads/主机available/swap增量/时间/采样gap；MLX allocator不可读准确UNAVAILABLE，不将RSS冒充GPU。若有真实audio就ffprobe核格式/时长/字节/hash，不以合成或say参考替代输出/音质验收。

最终新根report.md/measurement/release-evidence真实父/子组退出、device.lock非阻塞可重获、ledger与SQLpermit free、原请求/作业准确失败或单阶段结果、源码blobs/13模型stat未变；本地manta45e80 clean/integration b6ec保持。只有实际释放正证后才由主控让B26测试/GUI恢复。失败或成功均交worker_done一次，不自行修首因/改预算或新实验；主控根据当次证据推进真实缺口。不能称人物/长稿/Intel/多人/云身份/完整制作/分发完成，不关闭WIL26。

## A：用户再次释放应用后的短配音实验（任务27已结束，证据保留，无写/操作权）

主控接受failed worker_done msg_777e3eace038：实际可信Grant generation1与四次当下资源决策均true，临时验收沙盒错误拒绝CPython以O_RDWR打开/dev/null，发生于Popen前；无实际模型子PID/exec ACK/音频。另查78119/78117与组退出，锁EX|NB可重获、ledger/SQLpermit free、原请求及作业failed，106源码blob/13模型stat/runtime未变。首因不归给内存或模型算法，旧根保持只读，不重试27。

`MAIN-QWENHEADROOM-20261007-27`：任务25真实拒绝首因/无Grant与模型/锁及许可释放已接受且结束，用户再次明确关闭应用，主控单独观测可用5165252608B（4.81GiB）；这不是未来准入保证。仅复用同一manta可见终端的新Dispatch，产品/专项/权威写权零，45e80分支保留clean。准确冻结主控integration **3d58c1edabee1c590028b1cd6949515575ba225f**，本次Pi差额不影响A25已核模型/调度/资源消费者，archive这一个准确对象并逐blob核对。

只写新 `/tmp/main-acceptance-20261007/qwen-measure-headroom/`；旧diagnosed根仅定向读已合法参考WAV、无Key准备方法与失败诊断，不写旧DB/attempt/证据。完整隔离HOME/XDG/config/cache/library/output/temp/DB/device/agent，models仅具名Qwen只读软链接，既有13模型文件stat与外盘运行时不变。新合法LocalMember/真实MemberDeviceAccess/CreationJobOwnerVerifier/DeviceScheduler/HostResourceProbe及独立task/job；不mock身份、探针、worker或Grant。仅“这是一段测试声音。”合成参考与“今天阳光很好。”单段Qwen，父180秒/阶段120秒，A25段中全部Narration/HostBudget上限和准入3758096384B、OS离线沙盒、持续核验、子登记与释放条件逐项原样生效，swap只用本次实际执行baseline+67108864B，未知拒绝保持。禁止下载/安装、真实库/Key/云/其它模型、整链、人物/成片或生产预算配置修改。

REBASE_ACK后先只做静态archive、合法新夹具/脚本与预算准备，不运行真实资源探针、say/FFmpeg、测试/服务或模型，不与B26 GUI争主机。报准确blobs/env/13stat/拟命令与PREPARED；主控接受B26新GUI全部PID/组/端口退出及秘密处理后再ALLOW_HEADROOM_PROBE，届时一次真实无模型probe回报，收到本Dispatch START_MEASURE才唯一attempt。准入或运行拒绝必须记录触发当次完整decision，不能事后采样倒推/放宽保护/自动重试。

最终report.md/measurement.json/release-evidence.json准确音频或首因、exec ACK、组RSS/threads/available/swap/间隔、MLX不可测边界、父/子组实际退出、device.lock可重获、ledger/SQLpermit free与源码/模型stat不变；失败或成功均一次worker_done且不擅修首因。不把短单段结果当生产标定/Intel/音质/完整制作，不关闭卡；B26与旧所有实验的权威不扩展到本单。

## A：修正实验空设备I/O后的短配音验收（任务28已结束，证据保留，无写/操作权）

主控接受failed worker_done msg_6664ca4ebd88：真实Grant generation1及子93866登记后、exec ACK之前，当次总磁盘I/O72167582.97193675B/s超过67108864B/s（7319552B/0.101424375s），其它指标全部通过；没有Qwen执行或音频，不能把总I/O归给某个进程/模型。93865/93863/93866及组已另查退出，锁可重获且ledger/SQLpermit free，原请求及作业failed，106源码blob/13stat/runtime未变。精确/dev/null正负边界证据保留，旧deviceio根不得重试。

`MAIN-QWENDEVICEIO-20261007-28`：27失败结案及全释放已由主控接受，B26也已结案且GUI/秘密资源全部释放；复用同一manta可见终端的新Dispatch。冻结仍为integration **3d58c1edabee1c590028b1cd6949515575ba225f**；无产品/测试/权威写权、不改45e80 clean分支或生产预算。只新 `/tmp/main-acceptance-20261007/qwen-measure-deviceio/`，真实合法独立owner/task/job/DB及完整隔离；旧headroom只定向读已合法参考WAV和无Key方法，绝不写旧根。106源码blob/13模型stat/外盘运行时核对，models仅本Qwen只读链接。

必要差额只在新实验OS profile：确认/dev/null为系统字符设备，仅允许其精确literal设备读写，使CPython DEVNULL可以O_RDWR打开；所有其它根外持久文件写入与网络deny保持，禁止放宽到/dev目录或外部路径、关闭沙盒或改产品subprocess。先在新沙盒执行一次≤30秒非模型边界probe，自有组：真实DEVNULL+`/usr/bin/true`子进程成功；一次尝试写具名非秘密主控哨兵 `/tmp/main-controller-20261007/creation-wave/deviceio28-denied-sentinel.tmp` 必须被EPERM拒绝且文件不存在；一次127.0.0.1:9连接必须因OS策略EPERM/EACCES拒绝，不能把connection refused当通过。若边界失败停本单、不启动模型，不扩大豁免或自动试错。此哨兵只为主控可撤销测试，禁止碰任何用户文件或其它Key；实际成功创建即失败保留，由主控处理，不自行删除。

REBASE_ACK后可准备并运行该唯一非模型边界probe；B26与27已实际释放，不需新增GUI并行门禁。边界通过且组退出后允许一次原HostResourceProbe真实准入样本，交精确profile/正负证据/106blob/13stat/新合法环境与当次资源decision，再停PREPARED等本Dispatch START_MEASURE。不得以准备probe替代fresh调度准入。模型仍仅任务27同参考/目标单段、A25全部预算与3758096384B准入门槛、阶段120秒/父180秒/持续许可资源检查原样生效，swap本次实际baseline+67108864B。一次START仅一次真实attempt，无自动下一阶段/重试/新cap；其它模型、媒体工具、服务、云、安装/下载与生产设置皆无权。

捕获实际首次失败与同次decision、真实登记/exec ACK/加载或音频证明及RSS/threads/available/swap/间隔，MLX不可测不伪造。最后report/measurement/release-evidence核父/子组退出、device.lock EX|NB可重获、ledger/SQLpermit free、原请求及单阶段作业准确状态、源码/模型stat/runtime与分支clean不变；单段成功不外推生产标定、Intel、音质或完整制作。失败或成功均worker_done一次，禁止擅修产品首因或复试；本单不关闭WIL26、不分发或合main。

## A：据实测校订实验I/O预算后的短配音（任务29已结束，证据保留，无写/操作权）

主控接受failed worker_done msg_e673c558acf6：新根真实准备采样available3650617344B低于3758096384B（缺107479040B），96MiB/s I/O及其它字段检查通过，拒绝后停止，没有START/模型attempt/Grant。3359/3361/3362及组、3415实际退出；SQLpermit初始化free/generation0、零请求、lock/ledger从未创建或持有，原job保持queued/progress0，不伪造释放事务/作业failed。106blob/13stat/runtime及manta45e80/integration3d58 clean保持。主控另看机器可用3.28GiB，已向用户说明仍运行的个人Chrome与ChatGPT进程，等待用户释放稳定余量或选择优先其它流程；本节不授予新probe/重试或其它cap扩大。Pi分项已过且PR95三CI绿，完整制作/配音音频仍未验。

`MAIN-QWENIOBUDGET-20261007-29`：28准确首因及全释放已接受。主控只将**新实验HostBudget.max_disk_io_bytes_per_second改为100663296B/s（96MiB/s）**，覆盖已观测约68.8MiB/s并留有限余量；这是基于当次实证的实验参数调整，不证明I/O来源、硬件可用吞吐或生产标定，不写生产配置/代码、不关闭探针，也不从其它阶段借预算。其余A25全部cap、1610612736B主机余量/3758096384B准入、2GiB组RSS、原持久许可/组threads/输入输出、CPU75、fresh swap+67108864、120秒阶段/180秒父均不变。若新I/O或其它限额仍拒绝，停止且不自行再扩大或重试。

仅同一manta可见线程新Dispatch，新 `/tmp/main-acceptance-20261007/qwen-measure-iobudget/` 与准确 **3d58c1edabee1c590028b1cd6949515575ba225f**，106blob/13模型stat/runtime、45e80与integration clean保持；只读复用deviceio合法参考与无Key方法，新合法独立owner/task/job/DB与完整HOME/XDG/config/cache/library/output/tmp/agent隔离，models仅本Qwen只读链接，scheduled OFF。产品/专项/权威写权零，旧根不写。精确/dev/null profile逻辑与28已实际通过的正负边界复用，仅root字符串换成新根并核归一化文本完全一致；不得其它豁免或重跑完整边界专项。可用一次真实DEVNULL `/usr/bin/true` 无模型准备探针确认新profile加载，自有组≤30秒且证明退出，不联网/写外部哨兵。

REBASE_ACK后准备及上述最小probe、一次真实HostResourceProbe（新96MiB上限其余逐字段相同），交源码/模型/新隔离环境与真实decision供主控核对，停PREPARED等本Dispatch START_MEASURE。一次START仅一次相同短参考/目标单段Qwen，fresh调度准入、子登记/exec ACK/持续核验与拒绝同次asdict必须真实；不自动下一阶段、不mock、下载、安装、云、其它模型、服务或生产修改。最后准确首因或真实音频/ffprobe/hash、实际遥测与MLX不可测边界、父子组/锁/ledgerSQLpermit释放、源码/模型stat未变的report/measurement/release-evidence后worker_done一次，不关闭WIL26、不称完整制作或分发通过。

## A：用户释放主机资源后的短配音（任务30已结束，无写/操作权）

主控接受failed worker_done msg_d4d71b28844f：真实Grant generation1/子39058登记/exec ACK后，IO103529210.82B/s超96MiB/s，其它检查通过；没有音频，不冒称模型加载/推理。39035/39037/39058及组退出、实际lock可重获/ledgerSQL free/原请求job failed/106blob13stat runtime双clean，主控另ps退出。旧resumed根不再写/重试。

`MAIN-QWENRESUMED-20261007-30`：用户已完全退出此前提示的闲置个人应用，主控即时无模型采样available7586316288B（7.07GiB）、swap4764598272B；此值不代替新准入。29失败已结案，旧根不重试；B26结案待命，全部GUI资源已释放。仅复用原manta可见终端的新Dispatch；冻结准确integration **3d58c1edabee1c590028b1cd6949515575ba225f**，manta45e80与integration clean不变，无产品/测试/共享权威写权。

只写新 `/tmp/main-acceptance-20261007/qwen-measure-resumed/`，先archive本候选并核106源码blob/13模型stat/只读外盘runtime。旧iobudget根仅定向只读复用合法短参考WAV和无Key准备方法，不写旧库、报告或attempt。新合法独立LocalMember owner/MemberDeviceAccess/CreationJobOwnerVerifier、已确认合成文案与授权声音、task/job/DB；HOME/XDG/config/data/cache/library/output/tmp/agent/models/dotenv导入前全隔离，env -i，不导入app.main、scheduled OFF。models只链接既有Qwen3-TTS0.6B8bit；不访问真实Key/用户库、联网、安装、下载、其它模型、服务或GUI。

精确/dev/null字符设备literal豁免与28已实际通过的正负边界复用，profile仅新根字符串差额且归一化文本完全一致；其它根外持久写入及network仍deny，不重跑完整边界专项。REBASE_ACK后允许一次≤30秒DEVNULL /usr/bin/true无模型加载probe，证明自有组退出，然后一次真实无模型HostResourceProbe。交新source/profile/合法环境及该次decision/budget/metrics，停PREPARED等本Dispatch START_MEASURE。

预算逐字段与29一致：NarrationBudget max_text_chars80/max_section_count1/max_reference_bytes1048576/max_duration_seconds15/max_output_bytes2097152/max_threads1/max_timeout_seconds120/max_process_rss_bytes2147483648/max_process_threads128/runtime_sample_seconds0.1/additional_memory_bytes2147483648/additional_disk_bytes8388608；HostBudget CPU75/minavailable1610612736/maxswap本次fresh执行baseline+67108864/mindisk1073741824/maxIO100663296B/s/input2097152/sample0.1。准入available≥3758096384B、disk≥1084227584B。96MiB/s仍仅具名实验值，不写生产配置，不增加其它cap、不借成片预算。

START只一次实际短参考“这是一段测试声音。”与目标“今天阳光很好。”配音，父180秒/阶段120秒；真实CreationExecution.start→DeviceScheduler Grant、登记先于exec ACK、持续身份/资源/组监测及释放，不mock/no自动下一阶段或重试。捕获第一真实失败和同次asdict decision、raw stack/阶段/是否真正Qwen exec ACK或产物；provider进入不等于推理。0.1秒遥测RSS/threads/available/swap/间隔，MLX不可测记UNAVAILABLE，不冒充GPU内存。

最后report.md/measurement.json/release-evidence.json交实际音频ffprobe/hash或准确首因、所有父子PID/PGID退出、实际持有的lock EX|NB可重获、ledger/SQLpermit free及原请求/job真实状态；准备未Grant保留queued而不伪造failed，未持锁记N/A。106blob/13stat/runtime及双clean证明后worker_done一次。无论成败均完成释放，不擅改首因或试下一轮；单段成功不等于生产标定、音质、长文/Intel或完整制作，不关闭卡/合main/分发。

## A：按真实I/O峰值校订实验预算的短配音（任务31已结束，无写/操作权）

主控接受failed worker_done msg_f19687ddc60e：exec ACK后1.87868秒，实际0.100521750秒窗口主机合计I/O644134905.995523B/s（614.295MiB/s）超过128MiB/s，CPU17.3/available5696438272/swap4739432448及其它均通过；无音频、模型加载/推理仍未证。51135/51137/51148及组退出、held lock EX|NB可重获/ledgerSQL free/原request及job failed/106blob13stat runtime双clean，主控另ps退出。只保留证据，不再自动递增cap或重试；先核查同窗口逐设备/执行阶段来源和目标机资源预算，不能由全盘短窗口速率推断磁盘容量不足或归给模型。

`MAIN-QWENIO128-20261007-31`：30已实际失败并完整释放：exec ACK后0.11秒窗口IO103529210.82B/s超过96MiB/s；同次CPU43.5/75、available5860278272/3758096384、swap4764598272/4831707136及其它检查全部通过，I/O来源不可归因，没有音频/加载推理证明。主控已独立核39035/39037/39058退出、lock重获/ledgerSQL free、106blob/13stat/runtime双clean，须30 failed worker_done接受后新Dispatch才开始；旧根不重试。B26结案待命，全部GUI资源已释放。仅复用原manta可见终端的新Dispatch；冻结准确integration **3d58c1edabee1c590028b1cd6949515575ba225f**，manta45e80与integration clean不变，无产品/测试/共享权威写权。

只写新 `/tmp/main-acceptance-20261007/qwen-measure-io128/`，先archive本候选并核106源码blob/13模型stat/只读外盘runtime。旧resumed根仅定向只读复用合法短参考WAV和无Key准备方法，不写旧库、报告或attempt。新合法独立LocalMember owner/MemberDeviceAccess/CreationJobOwnerVerifier、已确认合成文案与授权声音、task/job/DB；HOME/XDG/config/data/cache/library/output/tmp/agent/models/dotenv导入前全隔离，env -i，不导入app.main、scheduled OFF。models只链接既有Qwen3-TTS0.6B8bit；不访问真实Key/用户库、联网、安装、下载、其它模型、服务或GUI。

精确/dev/null字符设备literal豁免与28已实际通过的正负边界复用，profile仅新根字符串差额且归一化文本完全一致；其它根外持久写入及network仍deny，不重跑完整边界专项。REBASE_ACK后允许一次≤30秒DEVNULL /usr/bin/true无模型加载probe，证明自有组退出，然后一次真实无模型HostResourceProbe。交新source/profile/合法环境及该次decision/budget/metrics，停PREPARED等本Dispatch START_MEASURE。准备只核未改变的必要项，复用106源归档方法/13stat/既有正负证据及prepare/measure方法，禁止重复测试/GUI/全面资料探索。

只将HostBudget.max_disk_io_bytes_per_second由30的100663296改为134217728B/s（128MiB/s），覆盖真实103529210.82B/s约98.7MiB/s并留有限余量；非生产/硬件吞吐标定，不关闭检查。其它预算逐字段与30一致：NarrationBudget max_text_chars80/max_section_count1/max_reference_bytes1048576/max_duration_seconds15/max_output_bytes2097152/max_threads1/max_timeout_seconds120/max_process_rss_bytes2147483648/max_process_threads128/runtime_sample_seconds0.1/additional_memory_bytes2147483648/additional_disk_bytes8388608；HostBudget CPU75/minavailable1610612736/maxswap本次fresh执行baseline+67108864/mindisk1073741824/maxIO134217728B/s/input2097152/sample0.1。准入available≥3758096384B、disk≥1084227584B。128MiB/s仍仅具名实验值，不写生产配置，不增加其它cap、不借成片预算。

START只一次实际短参考“这是一段测试声音。”与目标“今天阳光很好。”配音，父180秒/阶段120秒；真实CreationExecution.start→DeviceScheduler Grant、登记先于exec ACK、持续身份/资源/组监测及释放，不mock/no自动下一阶段或重试。捕获第一真实失败和同次asdict decision、raw stack/阶段/是否真正Qwen exec ACK或产物；provider进入不等于推理。0.1秒遥测RSS/threads/available/swap/间隔，MLX不可测记UNAVAILABLE，不冒充GPU内存。

最后report.md/measurement.json/release-evidence.json交实际音频ffprobe/hash或准确首因、所有父子PID/PGID退出、实际持有的lock EX|NB可重获、ledger/SQLpermit free及原请求/job真实状态；准备未Grant保留queued而不伪造failed，未持锁记N/A。106blob/13stat/runtime及双clean证明后worker_done一次。无论成败均完成释放，不擅改首因或试下一轮；若仍I/O拒绝，交该次精确触发点及可证局部I/O来源/不可测项建议，不再自动递增cap；单段成功不等于生产标定、音质、长文/Intel或完整制作，不关闭卡/合main/分发。


## A：配音持续压力与等待恢复（任务32已结案，无写/操作权）

主控接受worker_done `msg_3378e2aea31e` 的资源阻断结案：8路径实现及相关专项已审，clean c7cea50机械组合为clean f71fde442b9654681a617065553f59ace171f157，tree相同。真实准备采样available3245096960B低于3758096384B，未发START、无模型attempt/Grant；原job queued/progress0、SQLpermit free/generation0、requests空，准备17726/18051/18052及组退出，lock/ledger未创建记N/A。106blob/13模型stat/runtime不变，主控另核PID及只读SQL；隔离report/release-evidence保留。用户对另一UI工作树3163预览服务的暂停决定仍待回复。GitHub推送持续服务端500，读回远端PR95分支仍3d58c1e/Draft，新源码CI未开始；不将本地修复称已合入或真实音频通过。原用户授权目标仍待完成，资源具备后主控建立新具名Dispatch恢复，不重新询问已有修复范围；下述原任务参数与夹具只作恢复依据，不授予旧Dispatch新动作。

`MAIN-QWENPRESSURE-20261007-32`：用户已审阅主控诊断并明确“可以呀，你要试一下”，批准最小修复和同候选真实短配音。沿用产品§12.1/架构§3.12/领域等待资源语义，这是修正实现，不新增产品规则；31已失败结案/全释放，不复活旧根或逐次增cap。B待命，不新增线程。主控唯一共享文档/审核/集成/PR写者，A唯一产品写者。

源码：manta当前45e80 clean保留，从主控local integration读取准确 **3d58c1edabee1c590028b1cd6949515575ba225f** Git对象，clean确认后新独立 `codex/wil26-io-pressure-recovery-20261007`，不得merge/rebase旧混合历史。owned仅 `apps/api/app/host_resource_probes.py`、`creation_execution.py`、`granted_subprocess.py`、`voice_clone.py`、`apps/api/model_workers/qwen3_tts_worker.py` 及既有直接专项 `apps/api/tests/test_host_resource_probes.py`、`test_creation_execution.py`、`test_granted_subprocess.py`、`test_voice_clone.py`、`test_narration_commands.py`、`test_device_runtime.py`、`test_device_runtime_wiring.py`（存在者）；只改本单必要消费者。不写device_runtime/main/Web/桌面/配置和共享权威；确需跨owned先报证据，不新建调度器/兼容层/开关。

先读本节及 `/tmp/main-controller-20261007/creation-wave/voice-io-solution-review.md`、产品§12.1/架构§3.12/领域“等待资源区别于失败”，语义REBASE_ACK后直接实施，不再问是否继续。目标：短时全盘吞吐峰值不会被冒称磁盘故障；明确持续压力后，受许可子进程有界终止且核组释放，复用已有defer_unit(resource_pressure,未来retry_not_before)，原job保持可恢复/有效上游/等待年龄，驱动继续可处理其它单元，恢复时重新核原身份/当前输入/资源后获新Grant。不得宽泛吞RecoveryBlocked或修改driver generic exception-stop；权限/失效输入/未证明释放/真正模型错误保留原阻断。没有输出不能完成，不能每秒冷启动重试形成忙循环。

I/O：同窗口逐物理盘read/write及计数/请求时间，模型/runtime/输出盘来源能确定才关联；无法测字段明示UNAVAILABLE，Mac无busy_time不能伪造利用率，吞吐高不直接等于拥堵。使用可解释的持续观察而非单0.1秒峰值，保持实时内存/交换/空间/取消/身份保护；不能因未凑窗口而假unknown=true、不关闭I/O或忽略持续超限。先回报具体窗口/准入与运行区分、实测标定方法和低优先级方案，数字不得随失败自动递增；本轮初始IO cap仍134217728B/s，其它31保护保持，必要新标定参数由主控据证据明确。可用macOS单自有子进程taskpolicy/I/O QoS降低优先级；不更改全机设置/用户进程，不把优先级称硬限速，不用SIGSTOP冒充释放，不隐藏预读/加载成本或lazy=True回避保护。

A32.1主控明确本轮实验参数：配音I/O持续窗口3秒，cap仍128MiB/s；完整窗口以前只能按该窗口384MiB的有限累计burst预算核已知实际字节，不假unknown=true。准入必须已有完整真实窗口；**不得在DeviceScheduler的BEGIN IMMEDIATE或普通assert里阻塞等待3秒**，可用同源跨调用实际计数积累，完整窗口不足返回明确测量中/unknown并让driver按原轮询继续，不缓存旧ready/权限结论。运行保持原≤1秒许可复核、0.1秒组保护和快速取消；明确可恢复压力的最早重试30秒，在实际组释放后走已有defer，不无限冷启动。它们仅本候选有界初始配置，不声明生产标定；若必须改scheduler才能避免长锁先报证据，不擅扩大owned。taskpolicy -d throttle只作用自有Qwen子进程，不能称硬限速。

诊断：中止保留已收集的有界stdout/stderr/首个真实decision及实际可证执行阶段。模型worker可输出短phase诊断，不写私密文案/路径或用户内容，不新增用户界面/业务状态。actualACK仅证明放行，不冒称exec/权重加载/推理；真实Qwen音频+ffprobe/hash才该分项成功。

源码完成一次最小直接验证：短峰/持续压力/测量unknown、资源等待与恢复、持续撤权/用户取消及未释放不能重新获准，真实未知故障仍停driver。复用未影响的已有证据，不全量测试/构建/GUI/云/真实Key。交clean提交及准确diff、相关用例与组/锁释放，发CODE_FROZEN后停止产品写，主控审diff并机械组合、明确真实验收SHA。

真实验收根仅新 `/tmp/main-acceptance-20261007/qwen-pressure-recovery/`；候选必须主控冻结的同组合SHA，完整HOME/XDG/config/data/cache/library/output/tmp/agent/models/DB/dotenv隔离及env-i，不导入app.main、scheduled OFF。已有API venv/model-runtime只读，模型仅外盘既有Qwen0.6B8bit13stat（若worker新增计数/诊断导致源码blob数量变化据真实计数，不硬报旧106）；无网络/下载/安装/真实账号Key/用户库/其它模型/服务/GUI。旧io128只定向读合法参考WAV与无Key方法，不写旧根。新真实LocalMember owner、授权声音、已确认文案、CreationExecution.start/CreationJobOwnerVerifier及完整DeviceRuntime/CreationJobUnitExecutor流程，不能直接provider+harness手动failed冒称生产恢复。

先准备实际probe/profile/逐字段预算/真实合法fixture及拟命令供主控核，等本Dispatch START_RUNTIME_MEASURE再模型。精确/dev/null旧边界证据复用、新root归一化一致；仅一次DEVNULL加载probe与必要实际资源样本，不重复完整负向专项。初始NarrationBudget仍文字80/段1/参考1MiB/音频15s/输出2MiB/线程1/阶段120s/组RSS2GiB/组threads128/runtime_sample0.1/追加memory2GiB/disk8MiB；HostCPU75/minavailable1.5GiB/freshswap+64MiB/mindisk1GiB/IO128MiB/s/input2MiB，准入memory3.5GiB。父总180秒；按实际持续窗口选择probe采样可据方案调整，不能使取消/撤权保护失去界限。参考“这是一段测试声音。”/目标“今天阳光很好。”仅一段。

一次START是一个原job的有界真实验收，允许正常资源等待后最多一次重新Grant（总generation最多2、180秒总限，不改cap/重新造job）；每次均fresh准入/真实登记先于exec ACK/持续身份资源组检查，不mock，不强制造资源压力或并行重活。源码专项模拟压力与真实自然运行分开报告，真实未发生等待不可称该场景真机验证。只验narration，不自动执行montage/render/人物，后续阶段保持原queued/blocked，不称整job succeeded。出现真正模型错误/unknown/总限停止交首因，不擅装依赖或修旧根重试。

最终音频ffprobe/hash或准确首因、原job/request真实等待/成功单阶段/失败状态、每次真实Grant/exec/phase/逐盘窗口/RSS/threads/available/swap/间隔、MLX不可测边界、全部父子PID/组退出/held lock EX|NB可重获/ledgerSQLpermit free、源码/13stat/runtime未变与双clean后worker_done一次。不把短句成功当生产配置标定/音质/Intel/全流程，不关闭WIL26或合main。若本单有新真实依赖缺口，主控继续决定最小必要差额，不停在CODE_FROZEN或派工结果。

## A：资源释放后首次真实配音（任务33已结案，无写/操作权）

主控接受failed worker_done msg_a080386a576d：同原job真实generation1压力释放、30秒等待后fresh准入并gen2，原身份/文案/等待年龄保持；自然等待恢复已验，但两代均仅worker_started/importing_runtime/loading_weights，无weights_loaded/音频。首真实IO419176448B/3.0336625s超128MiB/s；第二代861974528B超有限384MiB初始量，CPU/内存/swap/空间均过。disk6读395698176及861503488B为主要量，不能仅据吞吐称拥堵/磁盘故障。最大两代是实验停因，与首资源拒绝分列。55239/55259/55331/56012及组退出、held lock EX|NB重获、ledger/SQL free generation2；原job interrupted/failure null/request blocked resource_pressure、106blob/13stat/runtime双clean未变，主控另核PID。33根只保留证据不再重试；f71远端PR95三项CI均绿/Draft。

`MAIN-QWENPRESSURE-20261007-33`：用户明确“可以啊，你试试吧”，批准暂停3163另一UI工作树的预览服务并继续实际配音。主控复核cwd/当前PID后仅SIGTERM其75454/75470，两者已退出，实际available8.84GiB；不停止其它应用/线程，不删除文件，不恢复预览至模型释放前。32 failed只表示准入准备受资源阻断，模型零次；原修复c7cea50与主控组合 **f71fde442b9654681a617065553f59ace171f157** 已审，双clean且tree相等，不重复30项专项或其它GUI/服务/模型。

仅复用manta现有可见终端的新Dispatch，先语义REBASE_ACK。无产品/测试/共享权威写权，不改预算或下载/安装/网络/真实Key/用户库。允许继续32的首次未启动隔离根 `/tmp/main-acceptance-20261007/qwen-pressure-recovery/`：先将32已接受的具名report.md/measurement.json/release-evidence.json分别保留为prepared32-report.md/prepared32-measurement.json/prepared32-release-evidence.json（仅这3份证据，不删除或复制整根），确认measurement-attempt/measurement-launch/registered-children/grants/commands等实际执行marker不存在；原合法job仍queued、无request、SQLpermit free/generation0、lock/ledger未创建，使用同一个原job，不造新job或清理旧attempt。106源码归档/13stat/runtime/精确DEVNULL profile/六个AST消费者及已修验收栅栏保持，来源只读；可修改隔离验收harness仅本次直接必要报告/计数，不能改source/产品保护。

先做一次新的无模型连续准入观察（必须真实完整≥3秒窗口、短单次采样0.1秒、当次freshswap+64MiB），提交PREPARED actual decision及预算、源/模型未变、原job/许可与拟命令，等**本新Dispatch START_RUNTIME_MEASURE**。所有32实验cap逐字段不变，IO128MiB/s/3秒有限384MiB初始字节量，准入memory3.5GiB、组RSS2GiB/线程128、阶段120秒/父180秒、快速取消/持续身份。无重复DEVNULL负向/专项或GUI，既有准备子组释放证据复用。

START只一个原job、真实完整DeviceRuntime/CreationJobUnitExecutor/当前身份输入/实际现有配音消费者；真实参考与目标仅原短句。自然压力最多generation2、实际释放后30秒fresh再准入，未知/模型错/时间限准确止，不自动增cap/新job。仅narration，后续montage/render不执行。成功须原narration请求done、job queued、permit free与真实有效audio cache同时成立，再停止driver；不可看到文件就取消成功request。最后真实WAV ffprobe/hash或首因/阶段/同次逐盘decision、遥测与所有父子/组、锁/ledger/SQLpermit释放、source/model/runtime与双clean证明，worker_done一次。未发生自然等待不称真机恢复已验；不称音质/Intel/生产标定/完整制作已过，不关闭WIL26/合main。新真实依赖缺口回证据，主控继续最小授权目标。

## A：受许可权重慢读与真实配音（任务34已结束，无写/操作权）

`MAIN-QWENPACED-20261007-34`：延续用户已批准的最小配音修复、实际试验及“生成音频可以慢慢来”的选择；33两次真实首因使继续盲目改cap无效。本单C0局部实现修正，不改变产品/领域/架构、默认业务入口/许可/失败规则，不增加用户费用/权限。B仍零权，无新线程。主控共享合同/审核/机械集成，A唯一产品writer。

源从clean主控integration **f71fde442b9654681a617065553f59ace171f157** 读取本地对象，在manta保留c7cea50后新独立 `codex/qwen-paced-weight-loading-20261007`。owned仅 `apps/api/model_workers/qwen3_tts_worker.py` 与既有 `apps/api/tests/test_voice_clone.py` 的直接专项；不改runtime安装包/任何其它产品路径、HostBudget/Grant/保护/持久状态/共享权威。先读本节、33真实commands/首因逐盘证据、现有worker与本机mlx_audio0.5.0 utils.load_weights 的 .safetensors→mx.load 原链、Darwin SDK fcntl.h F_RDAHEAD=45精确FD语义，语义ACK后实施。

目标是在**已登记、同Grant被监测的Qwen子进程内部**，使用有界单块顺序慢读已安装本模型实际权重文件，所有读成本显式phase/总字节/经过时长并保留原CPU/内存/交换/IO/120秒阶段限制；不把预读藏到准入或无许可prepare，不产生完整权重副本/新模型/cache层，不修改模型/用户文件或系统级设置。初始进程内读速率 **32MiB/s、单块≤1MiB**，按真实累计读取字节与monotonic elapsed节拍，有界可取消；Darwin可仅对自己打开的读FD关闭read-ahead（F_RDAHEAD），不设全盘/其它进程策略。已有loader默认非lazy、strict语义保持；不能以lazy=True推迟/隐藏加载成本或绕过I/O检查。慢读只降低已知加载突发，不宣称严格控制全机或后续loader所有IO，原host probe若仍真实超限照常让出。固定本Qwen模型已有.safetensors；不扩其它格式/模型/网络依赖/兼容层。

仅必要最小直接验证：实际小文件读字节与经过时间/节拍符合有限速率与≤1MiB内存块，取消/子组退出仍可有界；复用未变许可/RSS/身份/driver/短峰等30专项及33真实恢复证据，不全量测试/build/GUI。CODE_FROZEN后停止产品写，交clean提交、准确2路径差额和直接证据；主控审diff机械组合并冻结真实同候选SHA，不能把父f71 CI当新CI。

验收只新 `/tmp/main-acceptance-20261007/qwen-paced-weights/`：从新SHA archive，沿用33完整env-i隔离/106blob真实计数/13模型stat/runtime只读/精确DEVNULL profile与六AST现有消费者、真实LocalMember/授权合成参考及已确认短句/完整DeviceRuntime。33根只定向读参考、已验证无Key方法/恢复证据，不写旧DB/report/attempt；新合法fixture/job、scheduled OFF，不重复GUI/负向probe或不相关资料搜索。无网络/安装/下载/真实用户库/Key/其它模型/服务；3163已暂停，模型窗口不并行重活。

全部Host/Narration实验cap与33逐字段相同，IO128MiB/s/3秒/有限384MiB初始量、准入3.5GiB/组RSS2GiB/threads128、freshswap+64MiB、输入/输出/120秒阶段/180秒父均不改；32MiB/s慢读成本计入其内，不擅增时限。准备唯一无模型合法profile加载（如仅根字符串差复用已通过边界，单DEVNULL≤30s）及fresh完整≥3秒真实准入，交PREPARED等本Dispatch START_RUNTIME_MEASURE。START一个新具名job短参考/目标，仅narration，自然资源等待最多generation2，不盲目第三次/增cap或新job；未知/真正模型错如实停，audio+原narration done/job queued/permit free同时满足才通过，不取消成功request，不执行后续montage/render。

最终真实WAV ffprobe/hash或首因及可证phase/慢读字节时长/逐盘同次decision/遥测，全父子组、held lock EX|NB/ledger/SQLpermit释放、source/13stat/runtime双clean后worker_done一次。若没自然等待不重复宣称新候选该真机场景已发生；单句不代表长文/音质/Intel/生产标定/完整制作。不关闭Linear、不合main；仍有新真实问题先交证据，主控判断最小必要范围。

## A：资源拒绝精确诊断与实际配音（任务35已结案，无写/操作权）

34 failed worker_done `msg_f951bdc472f4` 已接受：两代均读完1986754306B/约59.22s，I/O通过；第一代CPU88.8/75拒绝，自然等待后第二代RSS或线程拒绝，但同次触发值未记录，不能用异步遥测代替。没有音频；原job/request failed，task narration仍running/cache null为保留缺口。全组退出、锁重获/ledger SQL free及source/model/runtime不变已核。34旧根和Dispatch停工保留。

主控接受 succeeded worker_done `msg_a274558ae69b`：同次诊断实测首轮RSS2350383104B超2GiB，第二轮同候选b2dc699真实Qwen音频1.52s/73004B/24kHz经原队列产出，原narration request done/job queued33/permit free联合成立；主控独立ffprobe/hash、只读SQL与全父子ps退出核对。4GiB为隔离实验，未写生产标定；整制作/音质/长稿/Intel/发布仍未验。全部资源释放、106blob/13stat/runtime与双clean保持，原首轮任务失败状态缺口保留。A原可见终端按用户安排retain，B待命；不再运行两旧实验根。

`MAIN-QWENDIAGNOSTICS-20261008-35`：延续用户已批准的最小修复和真实配音试验，C0局部必要差额。A复用原终端，B零权，不新增线程。由clean integration **add80aeed562bb9742633ddc937cb82c9dce9b7d** 本地对象在manta保留a1493a2后新独立 `codex/qwen-resource-diagnostics-20261008`。owned仅 `apps/api/app/granted_subprocess.py` 与既有 `apps/api/tests/test_host_resource_probes.py` 的直接相关专项；目标是在原检查真实拒绝的同一次读取中保存RSS/threads和各自limit，明确是哪项越限，附于原异常和可执行错误文字。保持检查条件/exception类别/组释放/HostBudget/Grant/队列状态原样，不新增采样器、保护开关、回退层或依赖，不改其他产品路径/运行时/模型。修补已观测诊断缺口，不用独立遥测填值。

最小验证沿用现有真实子进程预算专项，准确断言异常保留同次measured/limit/越限字段并且组退出及许可释放；未变源码、慢读、恢复测试不重跑。ACK后直接实施，CODE_FROZEN交clean候选/差额/直接证据，停止产品写；主控审核机械组合后绑定同候选真实验收。

仅新 `/tmp/main-acceptance-20261008/qwen-resource-diagnostics/`；34根定向只读参考、合法隔离方法与证据，禁止写旧根。完整env-i隔离/合法LocalMember及已确认短句/原六AST消费者/DeviceRuntime/106源码blob（实际数核对）/13模型stat/runtime只读/精确DEVNULL profile沿用34。scheduled OFF；无Key/真实用户库/云/联网/安装下载/其他模型/GUI服务；3163继续暂停。预算先全部逐字段沿用34（CPU75/IO128MiB/s与3s完整窗口/准入3.5GiB/组RSS2GiB/threads128/fresh swap+64MiB/120秒阶段/180秒父/慢读32MiB/s），不自动增cap/改状态。prepare最小profile加载及fresh完整≥3s真实准入后停PREPARED，等本Dispatch START_RUNTIME_MEASURE。

首轮仅一次新job短句narration，可自然等待至generation2；记录精确同次资源拒绝/阶段/真实音频及释放。若新诊断揭示有限预算不足，先完整释放并将实测值交主控，停等待；本Dispatch未结案时主控可以具名修订**隔离实验预算**并发明确新START（至多另一次新job，不自动第三轮），不得自行增cap/把实验标定写生产配置。每次实际音频及原narration done/job queued/permit free同时成立才通过，不取消成功请求，不做montage/render。未知/模型异常如实停止。最终真实音频ffprobe/hash或精确首因、全部父子组/实际held锁/ledger SQL释放和source/model/runtime双clean后worker_done一次；无音频不算完成，生产/Intel/音质/整制作仍不外推，不关闭Linear、不push/PR/合main。

任务35.1 有界校准：首轮实测同次RSS2350383104B >2147483648B、threads13≤128，模型loading_weights阶段被2GiB保护终止；慢读/Host检查通过，没有音频。完整释放msg_bce191d7ee37及positive release已核、主控独立ps退出。基于实际加载需求超过原预算与实际可用余量，第二且最后一轮实验仅将NarrationBudget **max_process_rss_bytes与additional_memory_bytes同改4294967296B（4GiB）**，因此真实准入minimum available随原不变量升为 **5905580032B（5.5GiB）**，不得只抬RSS而不保留内存预留；此值是有限探索上限，不冒称已测得峰值或生产标定。原Host可用1.5GiB/CPU75/IO128MiB/s/3s/384MiB/swap fresh+64MiB/threads128/输入输出/慢读32MiB/s/120秒阶段180秒父均不改。

仅当前35同Dispatch内新子根 `/tmp/main-acceptance-20261008/qwen-resource-diagnostics/calibrated-4g/`，同fe6d96d archive/新合法fixture/job，首轮顶层DB/log/report原样保留、禁止重写。按已有完整隔离/DEVNULL profile/106blob/13stat/runtime/六AST消费者及fresh完整准入准备，交PREPARED等第二明确START_RUNTIME_MEASURE；源冻结，新增产品写权零。只一新job、仅narration，自然最多generation2，无第三轮/进一步cap增加。真实成功联合条件、全部释放和未证边界沿用35；失败亦准确首因及资源，不把4GiB当默认产品值。第二轮结束总汇证据在新子根后worker_done一次。

任务35.2 CI直接消费者修正：fe6d96d CI的唯一Backend失败是既有 `apps/api/tests/test_material_scene.py::test_scene_timeout_or_resource_stop_never_commits_a_checkpoint` 通过getattr动态构造旧字符串异常，未提供新的同次测值参数；其余1606通过/20skip，Frontend/Desktop通过。仅临时增加该既有测试文件的具名参数夹具写权，使用当前SceneRuntimeLimits与明确超限测值构造GrantedCommandResourceLimit；其他异常路径原样、不添加兼容构造器/改产品保护。只跑该相关参数专项，单独提交后再次CODE_FROZEN；主控机械组合后把35.1真实试验绑定新准确候选（产品源码行为未变、未改分项证据复用），4GiB trial准备可保留但不得旧SHA启动。原两个产品/测试路径继续冻结无写权，模型仍等待新明确START。

## B：Pi 就绪与设置验收准备（候选保留，写权收回）

B12 候选固定 `bfbfaf8ce1c45ee2fead93b624fdc27bc25c2018`，产品写权已收回；以下范围只解释保留成果。任务 13 不改该候选。

`MAIN-PIREADY-20261007-12`：准确产品基线同上；pipefish clean 后新独立 `codex/wil165-pi-readiness-20261007`。目标：用户填 MiniMax Key 后能明确知道对话依赖是否可用，缺依赖给准确可执行动作；保留 MiniMax 默认与 DeepSeek 主动切换，无静默供应商回退。owned：`apps/api/app/assistant_pi.py`、`runtime_health.py`、`preflight.py`，既有 `tests/test_assistant_pi_failures.py`、`test_runtime_health.py`（若存在）、`test_runtime_settings.py`；有界修订 B12.1 追加互不重叠的既有 `apps/api/tests/test_assistant_pi.py`、`test_setup_preflight.py`，仅补真实 Node 预检前提与功能级依赖不阻断核心首启的直接消费者断言；`apps/desktop/README.md` 仅依赖使用说明，不重复产品权威。其他消费者测试确需增改时先回报准确文件，主控核互斥后修订。

先修旧 `npm i -g @pi/cli` 指令及对应保护旧结果的断言；实际官方/本机 Pi 为 `@earendil-works/pi-coding-agent` 1.0.4、Node >=22.19。只读已安装包的参数/环境引用/JSON协议，核当前 launcher 的最低实际要求，补功能级 Pi/Node/业务工具可用检查与清晰修复动作；可选外部模型不能成为全局首启硬门槛。无依据不新增版本兼容分支，不安装/升级全局 Pi/Node，不改桌面包依赖/workflow或 B 主体之外的 API。普通模型生成不能因为 Pi 缺失被误报为 Key 失败；Key已保存不能当真实请求验证。

用隔离环境做必要缺依赖/参数/环境引用与工具可见性验证；不读 `/Users/Zhuanz/Desktop/sk.docx`、真实 Keychain/账号/配置，不调用真实模型。可准备新的 `/tmp/pipefish-pi-readiness-20261007/` 与最小合成材料/只读候选映射，给主控实际 Key→Pi→HTTP工具验收所需最短命令和精确运行条件。暂不起 GUI/API 服务、不重用旧验收 DB/profile，不在 A 冻结前宣称全链完成。读本合同及认证 §3、PRD §2.6 后语义 ACK 即开始。交候选/精确净差额/最小测试、已安装协议检查、安装包尚不自带 Pi/Node 的真实边界和后续 GUI 条件。

## B：Pi 依赖准备真实页面验收（证据保留，任务已结束）

B13 已完成具名页面验收并释放自有 PID/4537/3137/Chrome/Orca page-profile；本节只解释证据，不取得新服务或页面操作权。

`MAIN-PIGUI-20261007-13`：同一可见 pipefish 线程复用，新 Dispatch；产品代码写权为零。准确冻结 `bfbfaf8ce1c45ee2fead93b624fdc27bc25c2018`，不混入 A 的未冻结源码，不要求等待未变化的创建模块。只写新 `/tmp/main-acceptance-20261007/pi-health/`；archive 排除 dotenv，导入前隔离 HOME/XDG/DB/config/workspace配置/library/output/cache/model/temp。不读真实 Keychain、桌面 Key 文档、账号、旧用户数据；Keychain 测试路径在新根且不初始化/读取登录钥匙串。当前无配置模型、无外部请求、无制作/分析。

允许准备合成目录及本机可信 local owner，先核 API4537/Web3137 空闲、记录自有 PID/PGID/30分钟租约，起唯一隔离 API/Web。已安装 Pi/Node 仅通过准确 executable/PATH 只读复用，禁止安装/升级。新建 scope=isolated Orca 内嵌 browser profile/page，只访问自己的 http://127.0.0.1:3137/health 及必要首启/健康页面；始终准确 page ID，native click/key/snapshot/screenshot，eval 只读 DOM。不得操作用户 profile/cookie/tab，旧 cua_repl 策略路线不重试。

有界修订 B13.1：已实际确认内嵌 AX 的 DisclosureTriangle 无可用 ref，两次有界键盘遍历不改变目标；原生 computer-use 权限/能力实际可用。允许同一验收根下新建一个外部 Chrome 独立 `--user-data-dir`（不克隆用户目录），仅打开同一自有3137健康页；启动记录准确自有PID/PGID，以 `orca computer --app pid:N` 和准确window ID定位，按 computer-use 技能原生控件点击/截图，禁止访问用户其他Chrome进程/窗口/标签或读浏览器私有资料。不添加削弱网页安全的启动参数，不重试旧cua路径、不写DOM或伪造展开。若新路线被策略/权限拒绝停止该项，不改权限。结束只停止自有Chrome进程组并保留证据；本扩充不改变产品写权或模型/Key边界。B应重读本段并REBASE_ACK后再操作外部窗口。

可见验证：已安装依赖情况下显示对话程序/运行环境/业务工具的真实就绪说明，并明确未做模型请求。然后仅停止自有 API，以同一隔离数据重启并将 Pi executable 指向新根内不存在的准确路径，刷新真实页面，展开缺 Pi 项看到正确安装命令及不把缺依赖当密钥拒绝。必要只读 GET 核对 setup preflight 的三个 required 与对话项 required=false，不能将 GET 当首启 GUI 完成；不需要重验未变业务。截图真实来自两个实际环境，不伪造 HTTP 或数据响应。

新页面异常只报告步骤与最小消费者，不获得源码写权。结束释放全部自有 PID/PGID/端口及新页面/profile，证明端口空闲、保留旧证据/用户浏览器。交准确 SHA、真实截图/说明、只读辅助证据及未验项；这只验依赖页面，不是 Key 保存、真实 MiniMax 对话、连续制作、桌面包或账号隔离通过。绑定 DESIGN、认证 §3 及本合同，语义 REBASE_ACK 后直接开始。

## B：国内 MiniMax 默认连接与实际回复格式（候选保留，写权收回）

`MAIN-MINIMAX-20261007-15`：用户已确认国内平台 Key，主控 `.cn/MiniMax-M3` 短请求及冻结B12真实Pi两只读工具实际成功；当前新配置却在四个实际消费者硬编码国际地址，真实Pi回复同时夹带 `<think>` 标记。本单作为当前MiniMax/Pi使用目标的必要差额，同一pipefish可见线程复用；先结束B13并证明资源释放，再新Dispatch/ACK，不从本节抢先写代码。

从准确 B12 `bfbfaf8ce1c45ee2fead93b624fdc27bc25c2018` 的干净树新独立 `codex/wil165-minimax-domestic-20261007`，保留旧B12候选；主控负责最终更新PR94，B不push/PR/Linear。owned仅 `apps/api/app/config.py` 的MiniMax新配置缺省地址、`assistant_pi.py` 的真实M3请求/回复边界；`apps/web/src/app/welcome/page.tsx`、`settings/page.tsx`、`components/LlmProviderFields.tsx` 的新配置默认入口及必要连接说明。直接既有专项 `apps/api/tests/test_runtime_settings.py`、`test_assistant_pi.py`、`test_assistant_pi_failures.py`、`test_local_model_health.py`（PR94 实际唯一 CI 失败是既有 `_feature_gates` 调用缺新增依赖参数，只补该消费者的有效前提，原本地模型门禁不弱化）与 `apps/web/src/components/__tests__/LlmProviderFields.test.tsx`、`apps/web/src/app/settings/__tests__/page.test.tsx`；有界修订 B15.2 追加新 `apps/web/src/app/welcome/__tests__/page.test.tsx`，仅验证真实保存 payload 的新国内缺省、已有国际/自定义地址保留及用户从 DeepSeek 主动切换 MiniMax，不扩充共享测试配置。只增改本单消费者；不新建层/依赖/开关，不改A的main/队列/许可/模型执行或用户现有配置/Keychain。

新MiniMax配置、主动选择MiniMax和首启采用当前官方国内 `https://api.minimax.cn/v1`，M3不变；明确已有/用户输入的国际或自定义URL原样保留，不迁移旧地址或Key、不探Key地区、不在失败后换域/供应商。现有连接地址编辑继续支持国际用户，不新增区域设置层；DeepSeek主动切换及旧用户选择保持。准备页“已保存”仍不等于真实鉴权。

回复格式只基于主控实际见到的M3思考标记：优先核本机Pi1.0.4模型配置/实际SDK请求序列化与当前MiniMax官方 thinking/reasoning_split 契约，让正文和思考分离或明确禁用当前M3思考输出；不按猜测加compat、不靠宽泛正则删正常正文、不修改其他供应商/自定义模型的能力。B可仅用合成Key离线检查真实SDK构造，不调用真实模型或读Key；若SDK接口确需额外consumer先报准确文件。主控证据 `/tmp/main-controller-20261007/creation-wave/pi-live/result.json` 只定向读取状态/格式缺口，Key不在其中。最小直接检查须证明真实序列化字段/正文、默认国内与明确国际保留，不仅镜像常量。此前70条及未变化health截图复用，不重复全量。

候选提交/源码完整后一次最小必要专项交接准确SHA/diff/仍未验项；默认地址UI和真实M3最终正文由主控冻结后具名验证，不抢先重启旧服务或用组件代替GUI。绑定PRD2.6、DESIGN直接命名和本合同；语义REBASE_ACK后直接完成这项必要差额。

## B：最终 MiniMax 地址页面及只读服务（证据保留，操作权收回）

`MAIN-MINIMAXGUI-20261007-16`：B15 已冻结 `50dbab6bb1008f16f3ea53c2bb3f7e4d2fd7e8de` 并交接，产品写权收回；同一 pipefish 可见线程新 Dispatch。先读 main 活动合同本节、DESIGN §5.9、PRD §2.6，语义 REBASE_ACK 后开始。只写新的 `/tmp/main-acceptance-20261007/minimax-final/`，不混入 A、旧证据或用户数据。archive 排除 dotenv，核源码 blob；依赖只读复用、HOME/XDG/config/workspace/DB/library/output/cache/models/temp 全隔离，secret store 仅指定新根内不存在的空测试路径并保护真实钥匙串，不创建/改系统钥匙串或搜索列表。无 Key/外部请求、模型/云、制作、真实配置或产品修改权。

先核 API4547/Web3147 空闲，记录准确自有 PID/PGID 和30分钟租约，起唯一 API/Web；只准备一个可信合成本机 owner、零业务资产/任务及必要空目录。新独立 Chrome profile，不 clone，采用已验证的原生 computer-use、自有 app PID/window；只自己的3147设置/欢迎页。实际页面核新 MiniMax 默认.cn、手动切 DeepSeek 后再选 MiniMax 的.cn及现有地址输入可编辑；截图密钥框为空或掩码。需要复验既有国际/自定义回填时，先停止 API，使用新的合成非密钥配置夹具重启同一隔离环境再读页面，清楚区分准备和 GUI，不 API业务写冒称GUI保存。未变化的 B13依赖健康GUI及70条专项复用，不重复。页无响应只报具体阻断，不写DOM/mock输出或改策略。

为主控真实最终Pi只读复验交准确 API PID/PGID、端口4547、合成owner token的0600文件路径和只读DB路径；经主控指定十分钟窗口与B明确ACK后环境/DB/API保持，允许当前候选真实 read_workspace_context/find_materials HTTP回调，零业务写。主控只使用自己的国内Key在RAM，不交B、不改B服务env或secret store，单轮总限180秒；核最终正文无思考块、工具成功及会话不含Key/token。用新 `/tmp/main-controller-20261007/creation-wave/pi-live-final/`，旧14窗口和证据保持已结束。不得将函数/HTTP证据当软件聊天GUI、真实账号或全制作通过。

B做完页面与窗口后只释放自有服务/Chrome/页面及租约，实际证明端口和所有自有组退出；保留截图及用户浏览器。报告到 `/tmp/main-acceptance-20261007/minimax-final/report.md`，交准确候选、页面证据、只读夹具/HTTP条件、资源证明及未验项，不写Linear/远端或关闭卡。

## B：MiniMax 国内默认的两个 CI 消费者（候选保留，写权收回）

`MAIN-MINIMAXCI-20261007-18`：仅在 B16 页面与最终Pi窗口结束、准确自有资源释放并 worker_done 被主控接受后，同一 pipefish 新 Dispatch。起点精确 `50dbab6bb1008f16f3ea53c2bb3f7e4d2fd7e8de`，当前独立分支保持，先核 clean 并读 main 活动合同本节 REBASE_ACK。只 owned `apps/api/tests/test_deepseek_llm.py` 中 `test_new_text_provider_defaults_to_minimax_without_routing_video_analysis`，以及 `apps/api/tests/test_llm_service_state.py` 中 `test_state_is_process_local_and_survives_nothing_but_the_process`；当前没有其他写者。最新 PR94 Backend CI 为1551 passed/19 skipped，实际唯一两失败是这两个既有消费者硬编码旧.io缺省。

仅修该两处前提：新配置期待已批准的国内.cn；同一连接重读的状态消费者仍使用与被验证连接一致的国内地址，保留 available→清缓存后unverified、不同Key/模型/地址不共享状态与所有非本单断言。不要全文件机械替换.io：明确国际地址是继续支持的真实配置。生产代码、Web、配置/Key、共享文档及A路径写权为零；无服务/浏览器/模型/外部请求/依赖安装权。

只写新 `/tmp/pipefish-minimax-ci-20261007/` 证据，导入前 HOME/XDG/DB/config/library/output/cache/model/temp及Keychain测试根隔离。一次仅运行上述两条现有专项，差额check后只提交两个具名文件，准确父50dbab6；不重复旧13/70条、GUI/Pi请求或本地全量。报告SHA/diff/测试/全部 product source blob与50dbab6相等证明，自有检查进程退出。main负责叠加已经批准的唯一PRD §2.6国内缺省说明、更新同一PR94及远端CI，B不push/PR/Linear、不改权威或关闭卡。报告 `/tmp/pipefish-minimax-ci-20261007/report.md`。

## B：连续制作活跃页面消费者同步（候选保留，写权收回）

任务19候选d4ebf2d308b48530d8c521e3b75c259a593f06fa与14个直接组件证据已由主控接受并释放自有测试进程；保留报告，本节不再授产品写权。

`MAIN-CREATIONUI-20261007-19`：B18 已冻结 a2da7e7，两条CI消费者交接且没有服务；同一 pipefish 新 Dispatch。保留所有 Pi 分支/候选，不改其源码。先 clean 后从准确产品基线 `7289f4fa3aa4d2fb61f9a3a362991a08885d14ca` 新独立 `codex/wil26-queue-ui-20261007`。只 owned `apps/web/src/components/CreationTaskWorkspace.tsx` 与既有 `apps/web/src/components/__tests__/CreationTaskWorkspace.test.tsx` 的本单直接消费者，不改CSS、lib/api、类型、其它组件、API/模型/共享权威或真实数据。读 main 合同本节、DESIGN §5.5、PRD §12.1及A冻结报告退役缺口，再 REBASE_ACK 后实施。

纵向主控绑定 A源码 `702392d11910138f2ef028b64f2bd42c019212d6` 的受许可 start/resume/recover 通道；A已关闭旧POST narration、montage/plan、montage/render及未接许可局部配音重做。当前活跃页面却仍调用这三个单步接口，造成实际409。最小修复：从这些活跃区域退役三个旧heavy按钮及调用，复用已有 CreationExecutionPanel 和 start/resume/cancel/changeExecution，让确认文案及要求后的排队入口在配音、故事板/成片阶段可见且可操作。阶段入口要进入同一明确确认流程；不能将“单步”暗换成自动全流程而沿用原文案，不能收到queued/running就宣称配音或成片已生成。保留声音选择、修订文案、读取已有成片、普通编辑/撤销/录音及已有产物，不移除用户数据或已支持的metadata编辑。

不新增无许可重做/人物/复杂音轨执行。已有默认20%露脸、局部配音重做、独立编辑版本仍是A明确未支持/未验差额，不把禁用或纯素材路径当完整目标。若本最小接线需要后端或新增能力才能实现，交准确证据由主控修订，不添加假回退。既有预检失败要保留首因与可执行动作，不靠隐藏整个工作台掩盖失败。

一文件一写者，A当前任务20与本单owned不重叠。本单只写新 `/tmp/pipefish-creation-queue-ui-20261007/` 证据和owned源码；无模型/服务/浏览器/Key/账号/依赖安装、push/PR/Linear权。A有界真实模型实验占主机时，先读/编辑并把最小测试命令备好，主控通知测量结束后再做CPU/内存较重的组件检查，避免影响实测；没有通知前不build/test。只跑必要直接用户按钮/确认/真实状态消费者，不本地全量或重复原未受影响验收。

交准确SHA/父/净差额、组件最小证据、三个旧调用从活跃组件消失的不存在证明、受限组合保留边界。GUI在A+B同一组合候选由主控另行具名验证，组件不代替GUI。本单不宣称整体迁移已RETIRED/可合入，不关闭WIL26。报告 `/tmp/pipefish-creation-queue-ui-20261007/report.md`。

## B：Pi 真实页面创建及修改验收（证据保留，操作权收回）

任务21创建真实GUI/Pi/持久化通过；编辑在Pi前因夹具缺confirmed_script_id被409正确拒绝，UNVERIFIED。worker_done已接受、12个自有PID/4PGID及端口已释放，最终秘密扫描通过，临时Keychain主控已锁定。旧DB/页面/模型轮次不得恢复。

`MAIN-PITASKGUI-20261007-21`：B19已交付并释放，用同一pipefish新Dispatch，不改其分支/候选。用户要求审查Pi能否在软件内根据指令创建和修改，本单落实这两项必要真实验证。冻结已获用户具名批准并合入的 `f024132fe696bdbb21f3d0e0b81e471a60486b65`（PR94最终b273c883的产品源码）；若工作树尚无对象，仅从main现有.git定向fetch准确对象，不能merge旧分支。

产品与权威写权零，只新 `/tmp/main-acceptance-20261007/pi-tasks/`，导入前隔离HOME/XDG/DB/config/library/output/cache/model/temp/agent及Keychain。只读复用现有已安装Node/Pi与API/Web依赖，不安装或升级、不模型加载。初始准备可信合成local owner、合成已保存知识和一条可用于metadata剪辑修改的合成既有任务/镜头，实际媒体仅短自有色卡；可定向复用之前gui-wil108新建合成夹具的准备方法，不写旧根。新任务必须由这次真实页面创建，既有剪辑任务只能作为明确准备夹具；不能把两者混称一条全制作流程。

先报冻结源码、完整隔离环境（无Key值）、准确API4539/Web3139空闲/自有PID规划、临时钥匙串路径、合成owner凭证0600路径、知识及剪辑准备事实、两条拟定指令和可见按钮路径。真实Key只由主控读取已经具名的 `minimax-validation-20261007` 条目到RAM并按现有原生store写入本新根独立临时Keychain的local/llm_api_key，非秘密配置仍走现有隔离设置；不改登录钥匙串其它条目或真实用户设置；原生create-keychain自动加入搜索列表时，主控将原始列表立即原样恢复并核对默认钥匙串/最终列表不变（仅此系统创建副作用与恢复），不改变已有条目。不落明文env/JSON/session/log，不发送Key给B、截图或聊天。B可让候选API自动读取本单新临时钥匙串的该条目，不自行查询/打印secret。准备报告经主控核对并完成上述Key准备后，本Dispatch收到START_PI_GUI才启动真实文字调用；无START仅准备。

最多两轮真实国内MiniMax-M3 Pi，每轮既有180秒界限，不批量重试、不切换供应商：第一轮从首页GUI输入明确目标生成真实preview_creation_plan，截图待确认和真实阻断；确认前只读确认无新task，GUI点击确认后只读核创建task与owner/workspace并刷新回读。第二轮在上述明确合成剪辑任务的实际页面选中镜头，由GUI向Pi下达一个已支持的剪辑metadata修改（例如限定第一个镜头的裁剪），必须实际read_current_task→preview_edit；截图影响及待确认，确认前原timeline不变，再GUI确认并刷新，GET/SQLite只读对应新的版本与准确变化；无需成片/模型。可一次GUI撤销验证保留已有产物，但不能点击render/narration/person或start/resume等重制作入口。若真实工具/交互/能力首因阻断，停止该项并准确回报，不API代替GUI业务写、mock输出、用正常路径失败触发隐藏fallback或擅加第三轮。

真实表面用现有computer-use驱动本任务新external Chrome profile/PID/window，--use-mock-keychain仅Chrome隔离，不改变API秘密store，不安全弱化参数，不碰用户浏览器/剪贴板/旧标签。native AX与有实际截图依据的点击/键盘可用，eval仅只读，不修改浏览器政策；不能操作时按具体项未验。两轮分别记录真实Pi工具轨迹、最终正文无思考标记、session无Key/成员凭证、实际HTTP结果及截图；截图与日志不暴露秘密。

30分钟自有API/Web/Chrome租约，先核端口，scheduled生产OFF、预算拒绝不放宽。禁止真实云身份/账号/注册、其他Key/真实数据、Qwen/口型/渲染/下载/安装/发布或生产配置写；A22仅准备独立进行，其模型实测须主控先释放本GUI资源。结束实际证明PID/组/端口释放，保留新证据/临时钥匙串不自动删除，主控后处理具名测试secret。报告report.md，创建与编辑分开给通过/未验；这不证明整条制作、真实账号、Key GUI保存、安装包或三平台发布，不关闭整卡。

## B：Pi 剪辑修改补验与组合页面核验（任务23已结束、候选证据保留，无写/操作权）

`MAIN-PIEDITGUI-20261007-23`：任务21分项结果与资源/秘密释放由主控接受，复用创建PASS不再测创建；同一pipefish新Dispatch，仅这次尚未验的编辑。冻结准确组合 `cb26f49e339d303e56f1f881fb60f071ea7e6e59`，从主控integration Git对象只读archive核blobs（包含f024与B19）；不修改分支/候选/代码/权威。仅新 `/tmp/main-acceptance-20261007/pi-edit/`，旧pi-tasks只定向读合成媒体/准备方法及已过创建证据，绝不写旧DB或session。

A22当前占主机实测，本单先只阅读并准备最小命令/夹具结构；收到主控ALLOW_EDIT_PREPARE及A实际释放正证前不启动服务/Chrome/say/FFmpeg/测试或运行准备seed，不影响Qwen观测。随后一次新隔离HOME/XDG/config/DB/library/output/cache/model/temp/agent；模型目录空、scheduled生产OFF、无生产预算放宽。只用已安装Node/Pi/API/Web依赖和旧root合成2秒色卡媒体的明确副本，不执行制作或下载。

**修复初始准备前提**：可信真实LocalMember owner与同工作区/owner的已确认文案、实际confirmed_script_id、已登记素材、两镜头montage、对应当前timeline版本及合成已有产物完整合法建立在新DB。本轮具体已支持修改为ChangeCrop水平裁剪到40%（clip-1 crop_x由0.5到0.4，crop_y=0.5、fit_mode=fill及两个时长、clip-2不变），区别于时间裁剪；初版时间裁剪指令经静态核没有自然语言映射且与固定slot时长语义冲突，已在发送Pi前退役，不称该语义支持。修订B23.2：静态事实表明“画面向左一点”缺明确百分比，不能猜步长；唯一Pi指令改用已核消费者支持的“水平裁剪到40%”，模糊方向保留未支持结论，不扩展产品实现。登记通过既有repository，SQLite初始seed可用但仅这一新根；准备报告必须只读核confirmed_script_id和script.selection_status=confirmed、clip-1/clip-2/timeline确实对应，先静态核API assistant/edit确认要求，不能漏前提再用业务写补救。无人物/声音模型/真实账号；素材/产物仅合成夹具不冒称真实渲染。

准备报告精确SHA/源码blob、完整无Key隔离env、自有API4541/Web3141端口/PID/30分钟租约规划、独立临时Keychain路径、0600 owner token、文案/任务/版本确认前只读基线、拟指令及native GUI路径。主控按已验证原生store路径从已授权validation Key条目到RAM，仅新临时Keychain保存local MiniMax及关联cn/M3设置，最终search list/default保持、不落明文/不交B；B仅候选API自动读这新路径，不自行查询secret。主控核全部前提与Key准备后本Dispatch START_PI_EDIT_GUI才允许唯一一轮真实Pi。

真实新external Chrome独立profile/PID/window，经computer-use native AX/截图依据点击/键盘进入已准备剪辑task、选clip-1并输入明确命令“只修改我选中的第一个镜头 clip-1：水平裁剪到40%，保持两个镜头时长和第二个镜头不变。先读取当前任务，再调用preview_edit给我看影响并等待确认；不要生成声音、人物或重新渲染。” 必须真实read_current_task→preview_edit，展示影响待确认，GUI确认前timeline/clip不变；GUI确认后GET与mode=ro SQLite对应新版本/精确变化且clip-2不变、刷新可读。可一次GUI撤销验证恢复且已有媒体保持；无API代GUI业务写、无模型替身/响应mock/第三轮/重试。如果真实首因阻断，保留准确失败，不伪造编辑成功。

B23.3 有界续验：唯一用户GUI发送内实际read_current_task成功、preview_edit七次拒绝后第八次生成真实待确认提案；保留全部轨迹，准确记为Pi指令编译/表述缺口，不能称顺畅一次成功。主控已看待确认截图，UI显示clip-1水平50%→40%而非宽度收窄；助手正文误述“中心对齐/横向收窄”须判语义失败。仅允许继续现有这一个成功提案的GUI确认、回读、刷新及一次撤销，以独立验证已存在提案持久化；不再次发Pi、不生成新预览或重制作、不删七次拒绝。既有preview_edit实际生成本地最多5秒静音对比属本轮调用的事实，报告必须记录真实预览进程/产物与释放，不能宣称未媒体；不等于整片重输出。源码/Key/既有资源与时限边界不变，整体Pi正确解释尚未通过。

同一组合页面同时截图核：工作台仍保留编辑、播放/旧产物、连续制作真实预检首因/可执行动作；B19退役的三个旧单步按钮不存在，不把queued/running当已出片。只观察/必要导航，不点start/resume/person/narration/render、不制造假作业状态；这类负向可见与模型完整制作分开给结论。

一轮既有180秒限，原生表面受限明确未验，不改政策/用户浏览器/剪贴板，eval只读。结束真实PID/组/端口释放、完整tool轨迹与无思考标记正文、确认前后GUI截图及只读持久对应。主控RAM最终秘密扫描，仅本新tempKeychain后续锁定，不删数据。报告report.md；创建既有证据复用/本轮编辑/组合负向页面/仍未验制作、真实账号、Key GUI保存与分发各自准确；不关闭卡/remote、不宣称整体RETIRED。

## B：Pi 编辑指令与结果语义修复（任务26已结束，证据保留，无写/操作权）

主控已接受同一Task26 succeeded worker_done msg_9693795f064a：643c227源码与最小专项、组合3d58实际国内Pi一次预览、GUI确认/刷新/一次撤销精确持久对应；19PID/5组及4551/3151实际退出，最终75文件/6日志RAM秘密扫描通过且task26-factual新store已锁定。原始LLM与实际API提案事实回复分开留证，PNG只证明真实可见区域；撤销output_ready仍false、历史回复生成时措辞及完整制作/真实Key GUI保存/账号/分发等边界不算通过。WIL-165维持In Review，本段以下仅解释保留成果，不恢复操作或源码写权。

`MAIN-PIEDITSEMANTICS-20261007-26` 绑定既有WIL-165。任务23 failed worker_done、分项持久化/刷新/撤销正证、全部自有PID/端口释放、主控12文件RAM秘密扫描与新具名临时Keychain锁定已接受；仅整体指令正确性FAIL。复用同一pipefish可见线程，不新增执行者。保留d4ebf2d原分支，从主控integration Git准确 `cb26f49e339d303e56f1f881fb60f071ea7e6e59` 建新clean `codex/wil165-pi-edit-semantics-20261007`；A24仅host probe/测试不重叠，后续由主控机械组合，不merge旧历史。

owned只有 `apps/api/app/assistant_pi.py`、`apps/api/agent_tools/diary_tools.ts`、既有 `apps/api/tests/test_assistant_pi.py` 本单直接消费者。无main/编译器/时间线/渲染/资源阈值/Web/桌面/共享文档写权。先读取已通过创建/设置的当前消费者和任务23真实trace，保留国内M3/思考历史/私密保存/创建流程行为。

真实缺口：Pi把纯动作command扩写对象/限制/猜想导致同轮preview_edit七次拒绝，最后成功仍把crop_x位置误称画面宽度。用最小既有工具描述/command参数说明与SYSTEM_PROMPT明确：对象引用/阶段单独传，command只包含用户已明确且编译器支持的动作，不扩写解释或副作用、不猜步长；水平/垂直百分比是裁剪窗口位置，非宽度/面积比例，缺量先澄清；工具失败说明准确阻断，不自行反复改写试错。真实预览的最终说明使用该提案实际before/after位置/operation与影响，不猜“中心对齐/收窄”。工具文本仅投影必要业务事实，不能发送媒体URL/原始文件/Key/整库；已有preview结构详情给UI保持。不得用regex事后改助手正文、硬编码fixture回答、静默fallback或新增LLM抽象/配置开关。若仅提示不足以解决实际重复，先提出准确必要接线证据，不自行扩写main或费用策略。

资源顺序B26.1：A25仍在PREPARED前且未收到START，主控可先ALLOW_PI_FIX_CHECK只运行下述两参数直接专项一次（Node子进程15秒/父30秒自有组），报PID/组释放。A25的START须等该释放正证，任何模型实际窗口不并行测试；GUI仍HOLD到A25实际结束。最小相关专项在新 `/tmp/pipefish-pi-edit-semantics-20261007/`：直接验证工具将引用/动作分开传给现有HTTP端点，并从真实形状返回必要语义/影响，保留提案确认约束；不以字符串镜像测试替代真实LLM验收。只跑本单新增/受影响专项，不重复既有70/13/2或全量，不运行媒体/模型/服务/浏览器。A25短Qwen占主机期间只可编辑/静态核，测试/GUI等收到主控明确释放再运行。

交独立SHA与父cb26、严格三路径差额、最小专项与释放后进入CODE_READY，停新增代码等待主控审核/机械组合与冻结精确候选。真实复验也是本单目标，不能代码完成就终结：A25实际模型资源退出并主控ALLOW_PI_FIX_PREPARE之后，仅新 `/tmp/main-acceptance-20261007/pi-edit-fixed/` 按任务23已验证方法准备完整confirmed_script/两clip/合法timeline/原合成媒体副本与独立HOME/XDG/DB/library/output/cache/models空目录/agent，不能写旧两GUI根。新API4543/Web3143与30分钟租约；新临时Keychain准确task26.keychain-db由主控原生RAM Key保存，B不读密钥。先报准确候选blobs/env/owner0600/DB只读前提/PID/Keychain路径，等本Dispatch START_PI_EDIT_FIX_GUI。

B26.2冻结与准备放行：主控接受253ada37严格三路径与实际Node24.16.0两参数专项2passed/组28807释放，已机械组合到准确 **a3714ddfe3590f27d78c290d573480abf1fabfc7**（parent b6ecba2）；该三路径blob逐个与被测B候选一致，A24四路径与45e80一致。后续GUI仅此准确源码archive。任务25实际准入时3649896448B低于3758096384B、其它检查通过、无Grant/provider/子进程/音频；父30999与launcher30995/组退出、锁/ledger/SQL free已由主控另查，旧根不得重试。B重读并REBASE_ACK后ALLOW_PI_FIX_PREPARE可开始无Key新根/服务/Chrome准备；待主控核完整前提、新原生store与本Dispatch START_PI_EDIT_FIX_GUI前不调用Pi。人物/独立重输出和配音实验均继续HOLD。

唯一180秒真实国内MiniMax-M3 Pi轮，从原生GUI向选中clip-1下达任务23同一句“只修改我选中的第一个镜头 clip-1：水平裁剪到40%，保持两个镜头时长和第二个镜头不变。先读取当前任务，再调用preview_edit给我看影响并等待确认；不要生成声音、人物或重新渲染。” 验实际read_current_task与一次有效preview_edit、正文解释位置准确不误称比例、无无谓内部试错、GUI预览/未确认timeline不变；确认后准确crop_x .5→.4且clip2/时长不变，刷新与一次撤销持久对应。现有端点本地最多5秒静音对比属于本轮实证，记录真实产物/进程，不称无媒体或完整出片；不能新模型调用/重试、API代GUI写或点击重制作。失败首因停止如实回报，不另加轮次。原产物物理保留但output_ready仍false现象记录给后续纵向任务，本单不改时间线/重输出契约。

B26.3真实首因修正：a371唯一真实轮read_current_task→一次preview_edit均成功，位置前后50%→40%解释准确且零试错，未确认原版本不变；但最终自由回复尾句“确认后才会真正出片”误导确认边界，保留FAIL，不因CI绿转通过。停止当前GUI、实际释放全PID/组/端口并主控秘密扫描/store锁定；不再重复23已过且消费者blob未变的确认/刷新/撤销。保持同一Dispatch，不先worker_done再拆新代码单。

释放后恢复原三owned路径最小修正：成功edit_preview的**对用户回复直接依据真实提案summary/实际before.after业务detail与明确确认含义生成**，应用修改和完整出片分开说明；只用现有函数/实际工具结果，不新增通用抽象、兼容分支、fixture硬编码、regex/词语替换清洗。原模型会话最终文本是非权威模型输出，须保留原样供取证；不能把显示回复来自业务事实说成原模型每次保证正确。无需继续用提示词轮番撞相同语义。对象引用/纯动作/一次预览/原始错误及真实Pi思考历史、Key/创建/文案流程保持。泛化到其它操作的确认含义先核实际消费者，不假称局部声音/人物重做支持，不改main/编译器/渲染/状态与权限。

本单直接受影响parser/工具响应专项须最小验证实际提案驱动的回复与真实模型尾句不会作为产品事实返回，保持必要事件/预览/错误断言，复用其余已过用例；交新SHA/严格三路径差额后停写，主控审核机械组合/准确冻结。仅再一次具名新 `/tmp/main-acceptance-20261007/pi-edit-factual/` GUI复验（4551/3151、独立task26-factual.keychain-db、完整新confirmed夹具/本单合成媒体副本与新隔离env/0600owner），旧三个GUI根不能写/重试。原全前提/30分钟租约/主控原生Key准备/本Dispatch START_PI_EDIT_FACTUAL_GUI门禁保持；唯一新候选真实Pi一轮180秒，验同句一次有效preview、用户可见回复符合实际提案而不承诺自动出片、确认前不写和GUI确认/刷新/一次撤销持久对应。模型原文本与实际API显示回复分别给证据和出处，首因失败仍停止不新轮。实际短静音对比与完整渲染区分。新根结束实际PID/组/4551/3151释放、RAM秘密扫描与唯一新store锁定后再最终worker_done；未通过不得结束单元或关闭卡。

B26.4准确冻结与准备放行：主控审核643c2278977e835b22f06a2cc6b793bc69fe0359（parent253ada37）、两路径最小差额及5条相关parser通过/组53459与53741释放；机械组合到 **3d58c1edabee1c590028b1cd6949515575ba225f**（parent a3714dd），三owned blob与B精确一致，其余产品blob未变化。主控另查旧4543/3143无listener及5组退出，最终47文件/6日志RAM秘密扫描通过，task26旧store已锁定。重读并REBASE_ACK后ALLOW_PI_EDIT_FACTUAL_PREPARE允许新具名根完整准备；只读archive这一准确候选，待主控核完整前提/新原生store和START_PI_EDIT_FACTUAL_GUI前不发真实Pi。用户再次关闭应用后当前独立样本可用4.81GiB；A保持待命，模型实测仍等本GUI结束实际释放后另具名派工，不将此样本视为未来准入结果。

结束实际源码/PID/组/4543/3143释放、完整工具轨迹/准确正文截图与只读持久事实、主控具名RAM秘密扫描/临时store锁定后最终worker_done。创建已过证据复用；不称完整制作、真实Key GUI保存/云账号/人物口型/安装/三平台发布通过，不关闭WIL-165或WIL-26。主控仅组合PR95候选，未经对应用户批准不合main。

## 主控真实 MiniMax 验证例外

用户提供文档并批准执行，主控可仅解析该 DOCX 中的唯一 Key，保存到新具名验收工作区 `minimax-validation-20261007` 的系统钥匙串条目；不枚举/读取/修改其他登录钥匙串内容，不改源文档、不迁移旧用户配置。用户已确认 Key 来自国内平台；首次国际接口401保留，后续仅用此 Key 对国内官方文档当前指定 `https://api.minimax.cn/v1` / `MiniMax-M3` 发出完成本轮验证所需的少量短文字/工具请求（先一个短鉴权探针），不再对国际接口重试，不上传原始媒体/整库/Key到其他域或代理，不测试 DeepSeek 的真实付费调用。Key不得写进终端/聊天/线程消息/仓库/构建/持久化 env 明文文件或报告；只保存无秘密的状态、用量、错误类别和结果证据。Pi 真实轮次在冻结候选隔离运行环境由主控具名交接后验证；本地语音/口型模型、真实 Supabase 部署/账号、安装包和分发仍需独立具名安排。该例外不授权 A/B 读文档或真实凭据。

主控 Pi 只读工具轮次 `MAIN-PILIVE-20261007-14`：国内短探针已实际通过。可用冻结 B12 的原始 `assistant_pi.py`，从主控新 `/tmp/main-controller-20261007/creation-wave/pi-live/` 隔离 HOME/XDG/agent/config/model/temp 根启动一次只读真实 `run_plan_turn`，最多180秒；Key仅由本轮新具名钥匙串条目读取到RAM并传给Pi子进程环境，不写持久化env/models/session明文。必须先由 B13 报告其准确自有 API PID/4537、冻结源码及合成数据/合成成员令牌文件，并交付缺Pi真实状态截图；主控与 B 确认最多十分钟只读工具窗口后才发起，B 在窗口内不重启API或改数据。HTTP只调用 read_workspace_context/find_materials 等只读工具，不创建提案/文案/任务、不开始制作、不写 B 的配置或 DB。模型记录由主控独有agent根持有；只保存HTTP/工具成功和最短无秘密回复，不能把这次直接服务调用说成普通GUI、设置页保存、真实账号或连续成片通过。窗口结束立即通知B按B13释放，不默认延长租约。

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

绑定[架构桌面边界](../docs/system-architecture.md)、[认证 §1](../docs/auth-workspace-boundary.md#1-身份模式)、[桌面开发](../apps/desktop/README.md)与[Release 门禁](../apps/desktop/RELEASE.md)。账号配置只采用现有公开 URL/publishable key 变量入口，不读取真实值或将管理凭据引入包。Supabase 技能的真实云操作要求不能扩大本任务授权；本单只在隔离临时文件中验证公开配置生成与缺失拒绝，不查询实际数据库/账号。

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

## A：账号制作许可闭环只读施工方案（方案保留，操作权收回）

本任务已移交主控，仅保留报告，不能从下列建议取得源码或云权限。下一施工须主控先落定具名权限边界与 owned 文件，再独立派工。

具名范围 `MAIN-ACCOUNTPLAN-20261007-10`：用户要求正式可用可分发，已有评估确认真实账号 workspace 的 start/resume/recover 被 local-only permit 拒绝。本阶段只准备可审核的下一单，不获得安全边界修改或真实账号权限。上一 A08 已结束并保留候选；从精确产品 `43cd7e25fb5ce635888cb9d4cd70d7cd9c6e5252` 定向只读 start/resume/recover、auth workspace/member、设备成员许可与持久复核直接消费者，绑定 [认证 §1/§9](../docs/auth-workspace-boundary.md#9-主账号邀请与共享授权)、[领域 §11](../docs/domain-model.md#11-设备成员共享和素材纠正对象) 及架构直接章节。

只写 `/tmp/manta-account-permit-plan-20261007/`，不改仓库/分支/Linear/远端、不导入 main、不读取真实配置/库/钥匙串、不起服务或调用云/模型。交付一个符合既有权威的最小纵向方案：真实账号成员到设备许可的可信来源、准入/持续核验/撤销及恢复路径、必须修改的精确文件、不能复用的旧路径与最小真实验收；分清已有契约的未接线和需要用户新决定的边界。不得删除 local-only 拒绝、伪造 LocalMember-space 绑定或假定持有真实 Supabase 数据。最多三个具体未决点；若既有规则已足够，明确可直接实施的边界，不制造额外审批。当前 B09 局部 GUI 不受影响，不扩写它或要求重验。读取本段并语义 REBASE_ACK 后开始。

## B：WIL-100 修正候选失败路径补验（证据保留，操作权收回）

本任务已结束并释放自有服务与新 profile/page，仅保留证据；不从本节重启服务或页面，后续操作须新具名派工。

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
