# 活动合同：全面重构 R0–R9（2026-10-14）

## 用户决定、入口与范围

用户已批准“按这个方案执行”，要求记住高内聚、低耦合；随后明确允许新建合适的可见实施线程或由主控直接执行。此前整体暂停由本次批准取代，旧线程和旧工作树租约不自动恢复。

唯一主入口：/Volumes/Dujiangyan-VM/AI视频日记/repositories/main，分支main。源码初始基线81a034caad78779e3068d73d1660ffd4bfe8509c；此前六份文档改动是已获批准的规则与方案，必须保留。Git实时状态为准。

整体交付采用 [全面重构方案](refactoring-plan.md) R0–R9；一批完成不替代整体目标，也不要求用户重复批准后续范围内工作。长期要求沿 [AGENTS.md](../AGENTS.md)，具体边界沿 [系统架构](../docs/system-architecture.md)。

为执行用户“及时看线程反馈并给后续安排”的要求，本聊天已接入10分钟线程监督heartbeat，标识automation、名称“曜核重构线程监督与接续”。它只恢复本合同既有工作、审核并接续已授权范围，不扩大施工/数据/发布权限；重要变化才通知。全部R0–R9验收结束后收口停止，用户明确暂停时停止新增施工并暂停监督。调度实际状态以Codex automation为准，此处仅定位该监督，不维护执行进度副本。

允许：源码拆分、必要现存故障修复、锁定依赖准备、隔离测试、实测资源标定、真实媒体/客户端验收和内部候选构建。原安装应用、用户数据库、配置/凭据、素材、声音及已有产物保持；签名、公开发布、覆盖安装、平台外发、删除用户数据和未经具体方案批准的持久格式迁移不在本次授权内。

## 第一波：R0/R1与独立R7

当前可见线程：R1 API为01a13610-8468-7580-9b33-d854f0ad8b7d；R7 Desktop为01a13610-8970-7a50-96f9-244e1e37d481；R0 QA为01a13610-8dcc-7d33-8b7d-75b47619301d。三者均在本main目录执行，host为local。

### R1 API纵向负责人

目标：将短镜头画面预览从HTTP/后台重入耦合中提取为明确业务与执行入口；保持原任务、版本、身份、幂等、提案/采用与媒体产物事实，并修正已知测试夹具。

独占：

- apps/api/app/main.py、production.py、schemas.py、task_audio.py、creation_execution.py。
- apps/api/app/member_permits.py、host_resource_probes.py、montage.py、visual_effects.py。
- 本批实际需要的apps/api/app/creation/**、apps/api/app/media/**和边界契约文件。
- apps/api/tests/test_task_media_api.py、test_task_media_grants.py、test_task_clip_preview.py、test_creation_resource_contract.py、test_host_resource_probes.py、test_visual_effects.py、test_creation_audio_mix.py，以及对应新增定向测试。

其他文件修改先报具体依赖，由主控在本合同分配；不编辑共享文档、Web、Desktop、Mobile或CLI。不修改私人dotenv，也不在未标定时给出猜测资源预算。

必读：AGENTS第1.1节；架构第3.8、3.12与本次新增第3.14节；领域第6、7、8节；方案第4、6、7节。第一批必须跟踪“用户指令 → 目标/版本 → 预览作业 → 实际产物 → 采用/撤销 → 旧路径退役”，不只搬函数。

验收：现有外部协议保持；该路径执行不构造HTTP Request或调用路由；业务/执行输入显式包含原身份、版本与许可；预览前不改草稿；采用使用原子保存；重复/晚回包/取消/重启保持；真实资源和媒体验证单列，不把fixture计为标定。

### R7 Desktop负责人

目标：拆分桌面壳的服务监督、候选/设备身份、日志、原生媒体和IPC职责，保留现有行为。API bootstrap仍归API负责人，本线程不跨写。

独占：apps/desktop/main.js、其直接需要的apps/desktop/runtime/**或其他实际新模块、apps/desktop/scripts/*-test.js与lifecycle-smoke.js中必要测试，以及package.json/构建文件中新增模块进入包的最小接线。

不得修改：API、Web、Mobile、appId、用户数据根、已有设备身份、凭据存储规则、候选摘要算法、签名/发布权限；不安装或启动覆盖原用户应用。

必读：AGENTS第1.1节，apps/desktop/README.md与实际目标源，架构第2.1节，方案R7与第7–9节。使用现有测试与隔离输入；不能只拆成多个文件仍靠可变万能全局对象相互调用。公共IPC及启动/关闭顺序保持。

### R0 独立审核与QA

目标：确认隔离依赖/测试入口，检查R1/R7的职责与事务风险，建立代表用例证据；实施者交出稳定候选后才给出源码审核和同候选真实验收结论。

可写：本批test-runs/refactor-20261014/**中的验证证据和必要脚本、忽略的外接盘测试依赖环境。产品源码和共享文档只读；新增产品回归测试建议交给其源码负责人。

依赖准备：优先查当前配置；必要时按uv.lock/pnpm-lock.yaml恢复main忽略的依赖，不修改锁文件、不碰模型/用户运行环境。测试读取conftest隔离事实；没有可信隔离时不导入生产main。

真实验收入口：只使用本批明确创建的独立测试目录/数据库/端口，fixture先通过后才进入授权素材用例；不复制原用户数据库、账号或密钥。需要现有真实任务/来源时先报告精确对象和读取方式，由主控绑定；本批验证不发到外部平台。

独立Electron原生壳执行已绑定：QA只写test-runs/refactor-20261014/electron-native-shell/**，按其native-shell-plan.md的新fixture项目复制已冻结Desktop源与锁定依赖，输出未签名QA专用dir包；profile、samples、saved与stub全部在该新目录。Web origin固定http://localhost:47329，CDP调试固定47331，API由自建stub独立端口且不使用原8000/3000。只用自建合成输入和fake成员，正常原生选择/保存对话框、真实窗口、退出/重启及本批设备身份稳定分项验证。fixture身份与产品安装隔离，不覆盖原应用、读其profile、访问签名keychain、修改Gatekeeper/TCC/系统权限或请求真实账号/模型；不执行更新/签名/发布。具体builder id/version/digest与实际资源沿上述准备方案并留证；静态API/Web stub必须明确标为壳证据，不能算同候选产品制作闭环。只有身份确认的本批PID/进程组可清理，实测失败留具体验收阻断，其他独立工作继续。

资源采样：先证明受许可进程组、RSS/线程/IO和退出识别可靠，再给API负责人实测建议；不直接执行旧未验证QA脚本，不改全机swap/内存门以放行。配置变更必须绑定样本、候选和测量证据。

## 主控与协作

### R4资料边界首批负责人

可见线程为01a13630-0dc5-71a1-90af-cbbea77dcace，host为local。

独占apps/api/app/sources.py、source_repository.py、knowledge_grants.py、这三个边界直接需要的中立类型/DDL/资料内部模块，以及test_source_repository.py、test_knowledge_grants.py、test_knowledge_grant_revocation.py和对应新增最小边界测试。目标是资料类型、读取/同步用例、持久存储与授权适配责任清楚；解除仓储反向依赖授权服务/同步服务，不改变现有数据库格式、表结构、共享权限、公共对象或同步提交原子性。公共Python导出沿原入口保持，其他消费者迁移由其文件负责人接续；main/production/schema/路由及其他已分配文件只读。素材与人物仓储提取仍归同一个API巨型文件负责人顺序执行，此首批不代替整个R4。

首批SOURCE_RELEASE后接续资料消费者的直接归属：独占topics.py、external_sources.py、product_hunt.py、hermes_radar.py中的来源类型/规则/同步入口导入，以及其原有test_obsidian_topics.py、test_product_hunt.py、test_hermes_radar.py中的最小对应调整。业务实现、HTTP与模型行为不改；不实际遍历vault、请求信息源或调用模型。main/production/schema的导入接续仍归API负责人。

恢复调查已在隔离SQLite复现同来源并发首次保存生成重复条目、并发版本更新唯一键冲突及迟到更新覆盖excluded限制。现追加source_repository.py的save_source_item_to_knowledge/_save_parsed_source_to_knowledge及直接调用的私有连接/写方法、test_source_repository.py和实际并发回归的独占修复写权：在既有同连接事务内完成读取、去重、父版本选择与提交，保留当前限制、所有权、旧固定版本、原网址分组和幂等语义。不新增表/存储格式、自动合并策略或外部同步，不放宽权限；其余旧来源业务仍只读。

资料事务修复集成后，API负责人已FILE_RELEASE_ACK且无WIP：将avatar_lipsync.py及实际新建profiles/contracts.py从API独占范围转交本线程。只迁出人物/声音/授权的已有值契约和纯规则，保留字段、默认、日期/使用限制、公共导出与原模型执行；不修改compile_person_edit_command、实际文件/模型处理或运行协议。对应test_avatar_lipsync.py、test_profile_reference_media.py中最小边界测试及新增纯契约测试允许；production/main/schema与其他活跃消费者的直接导入由API负责人接续。迁出的定义在avatar中删除，原导出引用同一对象；仓储从中立契约取类型、模型模块仅消费它。当前源码与旧固定授权/档案版本保持，存储格式/权限均不改。

素材六实体/六純规则按R4-material-type-edge-review-14已验证消费者方案，由API负责人独占materials/contracts.py及production原定义退役/导入接线，一次落地唯一实现；本线程另独占新增test_material_contracts.py，等待实际合同存在后验证构造、asdict/JSON、Pydantic响应、解码及分析模块不加载的纯值边界。实际观察对象仍由生产解码边界构造，字段注解只作TYPE_CHECKING；当前没有裸get_type_hints消费者，不新增动态反射兜底或强行拆分析器。source中旧asset模块的消费者直导入接续按确切文件再绑定，不能越过main/production/schema或API的其它独占写权。

### R6工具与CLI首批负责人

R7首批源码释放后，由同一可见线程01a13610-8970-7a50-96f9-244e1e37d481接续R6。独占apps/api/agent_tools/diary_tools.ts、material_tools.ts、其直接需要的实际分组模块和定向测试，以及scripts/yaohe.mjs、yaohe.test.mjs和实际CLI分组模块。首批只拆现有工具注册、可信上下文、HTTP传输与回执、CLI命令职责，保留工具名字/共同schema/公共导出和现有HTTP协议；不开放新动作，不重试未知副作用。assistant_pi.py和后端制作/编辑编排仍不在此写权中，待R2/R3稳定后另行接续。Desktop候选已冻结，不与QA审核同时改动。

2026-10-14恢复时追加R6宿主适配写权：独占assistant_pi.py、其实际提取的assistant/**模块，以及test_assistant_pi_failures.py、test_pi_instruction_closure.py和直接相关宿主预读取/运行时体检测试。先修复新工具子目录缺失仍被体检报告ready的已复现缺口，再拆进程运行、轮次停止、业务事件投影与可信上下文组装；保持公开函数、env/会话/工具白名单、真实错误与停止语义，不改变后端编辑/制作用例、认证或模型协议，不调用真实账号模型。原工具首批先提供SOURCE_RELEASE与摘要，不与新宿主改动混淆候选。新协议/共享契约变化仍先报主控。

宿主集成后接续已复现R9资源闭包：独占app/media_timing.py、model_workers/musetalk_worker.py、latentsync_worker.py、实际共享计时模块及其包入口，相关test_musetalk_worker.py中的最小计时边界测试及新增独立资源测试（test_avatar_lipsync.py保持只读，不与R4测试租约交叉）。PyInstaller spec与test_packaging_modules.py从主控交回本线程，允许添加图片脚本实际worker与可信source摘要文件的必要data输入；若实际资源布局需改Desktop package.json/generate-build-identity.js，先给准确接线由主控绑定。计时实现只有一处，API与独立worker实际加载它，不复制完整API、不注入仓库sys.path；同候选资源进入已有候选摘要。不改模型参数、许可、权重、工具链配置、source摘要算法、签名或发布，不调用用户模型。优先隔离复制布局+真实worker --help、同候选封存资源输入检查，完整冻结二进制未跑时单列。

资源子批集成后接续R9安装器：独占model_installer.py、其实际需要的安装作业存储/执行模块及test_model_installer.py。已在隔离线程复现构造新实例把仍运行的原作业误写interrupted；先将恢复写入移出构造，显式启动恢复与活跃作业责任分开，安装状态只有一个写负责人，保存格式与原协议保持。API主控实例构造/重载接线仍由API负责人修改，须协调该必要consumer后才算集成完成。下载/pip执行可按真实边界拆分，但不下载/启动用户模型、不读取私人配置、不删除模型或新建状态事实库。新配置/格式或公共能力改变先报主控；纯值规格与配置模块此时只读，不能因首个缺陷自动扩大为重写所有配置。

### R5 Web负责人

可见线程为01a13626-c206-74f1-a258-ae4d9d86c7a8，host为local。

在既有HTTP/schema与用户交互保持的条件下，独占apps/web/src/components/CreationTaskWorkspace.tsx、MaterialLibraryWorkspace.tsx、直接提取的新组件/控制器、apps/web/src/lib/api.ts、types.ts及对应业务分组文件和定向前端测试。不改API/Desktop/Mobile、认证/存储协议、DESIGN或其他共享权威。当前R1承诺外部响应兼容；需要新增/改变契约时先由主控冻结。按阶段传明确的只读事实与必要动作，不把完整workspace或万能setter/deps传给所有子组件。保留选择、播放、草稿、持续会话、撤销及真实等待/失败表达；不重设计界面。读AGENTS1.1、DESIGN与方案R5；代码与三尺寸真实GUI分项验收。

首批已集成后，隔离GUI复现音轨导入/试听控制把字幕轨推到首屏外，违反DESIGN既有剪辑三轨同时可见要求。追加VideoEditor.tsx、AudioTimelineEditor.tsx、VideoEditor.module.css及相应定向测试的独占最小布局/职责修复：同一控制器拥有音频交互，三轨沿同一时间尺，控制工具不能挤掉必要轨道；播放器真实比例、选择/播放/草稿、导入/试听/取消保持。只修既有可见契约，不重设计三栏或新增设置开关；1024×700及两个已失败尺寸和桌面代表截图分项复核，新源码摘要与首批分开。

布局集成后的只读探针已复现成功完整任务回读仍保留旧缓存入口、同任务can_undo=false回读后首页撤销仍可点击。追加AppShell.tsx、HomeCommandComposer.tsx、其直接必要的有限只读任务事实/事件模块，以及对应AppShell/HomeCommandComposer定向测试独占修复权。完整成功快照更新当前可读集合，失败保留最后确认记录，局部任务事件不得冒充完整快照；命令状态以当前服务器版本/事实为准，草稿、对话与导航保持。复用已有任务/版本协议，不改认证/权限、持久格式或首启业务规则，不新增通用状态库。源和截图接受后继续核对剩余外围消费者。

此批集成后已证实无生产消费者的CreationTaskTopicWorkspace.tsx、其module.css和旧同名测试，由本线程独占退役。保留workflow-contract-check.mjs对旧入口的负向防回流检查，当前CreationTaskWorkspace选题消费者不改；最小活跃选题/工作流检查与生产导入不存在证明完成后标RETIRED，不删除用户历史或运行数据。

主控独占AGENTS.md、docs中的活动权威、.project-control中的合同/计划；可直接实现尚未分配的模块，但必须先登记文件。可见实施线程身份在创建回执后补入本合同，只有当前角色独占范围有效。

主控接续apps/desktop/README.md与docs/cli.md的当前源码职责/调用索引，沿既有权限及分发说明，只登记已存在入口与资源目录，不维护完成度副本。

本波主控另独占R8手机结构：apps/mobile/lib/ui/companion_app.dart及实际提取的页面/控制器文件、apps/mobile/lib/data/local_media_draft.dart、local_media_draft_controller.dart、voice_sample_io.dart、voice_sample_controller.dart，以及对应test中的最小必要调整和apps/mobile/README.md的职责索引。保持host_api/mobile_store既有公共协议、包名和安全存储键；不改主机接入、不实现假上传/登录、不启动真实账号会话。先完成纯结构与现有定向验证，原生/GUI证据未执行时单列。

R9打包闭包中PyInstaller嵌套模块清单首批已由主控集成，后续spec及test_packaging_modules.py写权转交上述R6/R9资源负责人；主控只审核集成，不同时改写。应用标识、签名、发布、依赖排除策略和用户安装保持原界限；打包清单检查不冒充二进制真实启动。

Web首批SOURCE_RELEASE并集成后，主控接手其lib/api/transport.ts、creation-transport.ts、materials.ts、task-media.ts和新增定向请求边界测试；Web实施线程在这些文件只读，继续独占剪辑布局子批。将JSON、流式、FormData和Blob请求复用同一既有身份注入入口，保留各调用的原URL、请求体/取消信号、响应及错误语义；FormData不得写JSON Content-Type，不新增身份/空间或兼容分支。该API内部收敛不改R5的产品布局或服务端协议，新摘要分项记录。

角色互斥：API巨型文件和共同schema由一个负责人连续接续；Desktop独立文件并行；QA不写产品源码。共享契约变更提交证据建议，由主控更新权威并通知受影响线程；旧合同必须重新绑定。不得自行创建内部子智能体。

报告包含：候选Git/diff、确切文件/符号、相关命令与结果、首个失败、结构/持久/媒体/GUI的证据等级、剩余消费者和未验证项。当前阶段报告不等于全面完成。

共享main的Git提交由主控统一收口；实施者不自行stage/commit其他并行候选，完成当前批后交出SOURCE_RELEASE、确切文件及源摘要，该批写权交回主控审核集成。必要的集成修复须绑定具体文件并通知原负责人，实际变化另记候选与相关验证；新增文件同样纳入候选身份。尚未释放的独占修改可继续，不因等待其他角色的验证而重复全量测试。

## 退役、回滚与后续

### API后续写权：R2、R3及R4资产/档案

R1版本修复源码经主控/QA解除BLOCK后，原API线程继续独占原范围和新增apps/api/app/creation/editing/**、creation/contracts.py、creation/transactions.py、creation/projection.py、media/contracts.py、media/jobs.py、media/rendering.py、materials/**、profiles/**（排除已转交R4的contracts.py）、api/**及bootstrap.py中的实际模块；不先建空目录。新增现有文件写权为audio_timeline.py、linked_timeline.py、compound_edits.py、subtitle_edits.py、subtitle_alignment.py、subtitle_tracks.py、subtitle_style.py、graphic_overlays.py、graphic_sources.py、transitions.py、silence_edits.py、device_runtime.py、device_scheduler.py、task_runtime.py、granted_subprocess.py、artifact_store.py、media_script_jobs.py、assistant_goal_continuation.py和conversation_repository.py中与本次目标回执直接有关的部分。对应既有动作/事务/执行/接续测试允许最小相关调整。R4资料/档案契约独占文件、Pi/CLI适配及Web/Desktop/Mobile保持互斥。

R2按活跃动作形成具体输入、计算与联合变更集，保留一个版本/提案/跨轨保存事务。R3按媒体用途接续持久执行，显式携带原身份/版本/许可/取消/回执，逐组退役伪造HTTP、反射与路由重入；任务/设备检查点原子边界保持。R4素材/人物的中立类型、写入负责人和存储计算逐项迁出production，不让仓储依赖模型执行。R7后端bootstrap最后移出导入期运行组装。每个稳定子批先交源码、定向测试、退役证明及精确摘要供集成；同候选真实媒体门禁缺口单列，不能因此停止其他独立的已授权结构工作。对外协议、权限、存储格式和用户数据均保持。变更公共共享契约先报主控，不能以此授权签名发布或平台外发。

R2首子批集成后，API负责人接续其余镜头替换/分割/排序、关联和复合编辑、字幕结构/时间/样式的具体用例及撤销/恢复；追加narration.py的中立档案契约直接导入接线，以及test_crop_preview_description.py/test_montage_pipeline.py/test_assistant_goal_api.py这三个实际共享夹具的最小契约接续写权（R6的test_pi_instruction_closure.py保持只读）。提案测试不伪称媒体已生成；媒体测试用同一合法人物/声音产物和显式合成用途预算，不改生产默认、实际资源门或授权规则。对应R2稳定一组交源码后继续R3执行及bootstrap，不以单组结束或文档ACK停止施工。

镜头实际影响范围子批集成后，API负责人下一小批接续main安装器的显式startup恢复/同owner重载/落盘前guard，及creation/projection.py的列表与详情共同任务版本事实。config.py仅追加ModelCatalog所消费六项路径的唯一纯解析函数与prospective参数读取，不读取/修改私人dotenv，不重写身份/secret投影；Settings.from_env和写前候选复用同一规则，现runtime_health.model_catalog_from_settings保持既有组装归属。对应test_config_atomic.py/test_runtime_settings.py/test_timeline_history.py和最小主入口集成测试可写；R9四个安装器源保持冻结，不与其原负责人并写，必须连同主消费者验收才收口。runtime_settings.py/workspace_settings.py的存储/钥匙串此时只读，用既有写接口在明确保护范围内提交。后续materials/contracts.py与其已准备测试按既有API/R4独占分工一次接续。

R1旧目标：仅该路径的伪造HTTP执行、路由反射/重入、隐含业务上下文和重复写入口；其他尚未迁移动作逐项分类，不能因一个动作通过就删除整套执行协议。R7旧目标：main中迁出的具体实现及其重复状态/全局依赖。

同一事实只有一个写方向；短期导入映射在本批消费者全部切换时退役。源码回滚保留所有用户数据和新历史，不reset用户脏树，也不重放未知作业。

R0/R1达到门禁后由主控继续分配R2–R9，并按实际文件释放/接续写权；独立的已授权工作不因某个真实环境阻断全部停止。最终需要同候选核心真实闭环、所有旧目标RETIRED及权限/数据/恢复证据。

旧暂停交接见 [pause-20261013](takeover/pause-20261013.md)，只供已保存WIP与未验证项定位，不提供新的授权。
