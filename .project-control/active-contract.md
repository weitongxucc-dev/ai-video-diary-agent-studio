# 活动合同：全面重构 R0–R9（2026-10-14）

## 用户决定、入口与范围

用户已批准“按这个方案执行”，要求记住高内聚、低耦合；随后明确允许新建合适的可见实施线程或由主控直接执行。此前整体暂停由本次批准取代，旧线程和旧工作树租约不自动恢复。

唯一主入口：/Volumes/Dujiangyan-VM/AI视频日记/repositories/main，分支main。源码初始基线81a034caad78779e3068d73d1660ffd4bfe8509c；此前六份文档改动是已获批准的规则与方案，必须保留。Git实时状态为准。

整体交付采用 [全面重构方案](refactoring-plan.md) R0–R9；一批完成不替代整体目标，也不要求用户重复批准后续范围内工作。长期要求沿 [AGENTS.md](../AGENTS.md)，具体边界沿 [系统架构](../docs/system-architecture.md)。

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

资源采样：先证明受许可进程组、RSS/线程/IO和退出识别可靠，再给API负责人实测建议；不直接执行旧未验证QA脚本，不改全机swap/内存门以放行。配置变更必须绑定样本、候选和测量证据。

## 主控与协作

### R4资料边界首批负责人

可见线程为01a13630-0dc5-71a1-90af-cbbea77dcace，host为local。

独占apps/api/app/sources.py、source_repository.py、knowledge_grants.py、这三个边界直接需要的中立类型/DDL/资料内部模块，以及test_source_repository.py、test_knowledge_grants.py、test_knowledge_grant_revocation.py和对应新增最小边界测试。目标是资料类型、读取/同步用例、持久存储与授权适配责任清楚；解除仓储反向依赖授权服务/同步服务，不改变现有数据库格式、表结构、共享权限、公共对象或同步提交原子性。公共Python导出沿原入口保持，其他消费者迁移由其文件负责人接续；main/production/schema/路由及其他已分配文件只读。素材与人物仓储提取仍归同一个API巨型文件负责人顺序执行，此首批不代替整个R4。

首批SOURCE_RELEASE后接续资料消费者的直接归属：独占topics.py、external_sources.py、product_hunt.py、hermes_radar.py中的来源类型/规则/同步入口导入，以及其原有test_obsidian_topics.py、test_product_hunt.py、test_hermes_radar.py中的最小对应调整。业务实现、HTTP与模型行为不改；不实际遍历vault、请求信息源或调用模型。main/production/schema的导入接续仍归API负责人。

恢复调查已在隔离SQLite复现同来源并发首次保存生成重复条目、并发版本更新唯一键冲突及迟到更新覆盖excluded限制。现追加source_repository.py的save_source_item_to_knowledge/_save_parsed_source_to_knowledge及直接调用的私有连接/写方法、test_source_repository.py和实际并发回归的独占修复写权：在既有同连接事务内完成读取、去重、父版本选择与提交，保留当前限制、所有权、旧固定版本、原网址分组和幂等语义。不新增表/存储格式、自动合并策略或外部同步，不放宽权限；其余旧来源业务仍只读。

### R6工具与CLI首批负责人

R7首批源码释放后，由同一可见线程01a13610-8970-7a50-96f9-244e1e37d481接续R6。独占apps/api/agent_tools/diary_tools.ts、material_tools.ts、其直接需要的实际分组模块和定向测试，以及scripts/yaohe.mjs、yaohe.test.mjs和实际CLI分组模块。首批只拆现有工具注册、可信上下文、HTTP传输与回执、CLI命令职责，保留工具名字/共同schema/公共导出和现有HTTP协议；不开放新动作，不重试未知副作用。assistant_pi.py和后端制作/编辑编排仍不在此写权中，待R2/R3稳定后另行接续。Desktop候选已冻结，不与QA审核同时改动。

2026-10-14恢复时追加R6宿主适配写权：独占assistant_pi.py、其实际提取的assistant/**模块，以及test_assistant_pi_failures.py、test_pi_instruction_closure.py和直接相关宿主预读取/运行时体检测试。先修复新工具子目录缺失仍被体检报告ready的已复现缺口，再拆进程运行、轮次停止、业务事件投影与可信上下文组装；保持公开函数、env/会话/工具白名单、真实错误与停止语义，不改变后端编辑/制作用例、认证或模型协议，不调用真实账号模型。原工具首批先提供SOURCE_RELEASE与摘要，不与新宿主改动混淆候选。新协议/共享契约变化仍先报主控。

### R5 Web负责人

可见线程为01a13626-c206-74f1-a258-ae4d9d86c7a8，host为local。

在既有HTTP/schema与用户交互保持的条件下，独占apps/web/src/components/CreationTaskWorkspace.tsx、MaterialLibraryWorkspace.tsx、直接提取的新组件/控制器、apps/web/src/lib/api.ts、types.ts及对应业务分组文件和定向前端测试。不改API/Desktop/Mobile、认证/存储协议、DESIGN或其他共享权威。当前R1承诺外部响应兼容；需要新增/改变契约时先由主控冻结。按阶段传明确的只读事实与必要动作，不把完整workspace或万能setter/deps传给所有子组件。保留选择、播放、草稿、持续会话、撤销及真实等待/失败表达；不重设计界面。读AGENTS1.1、DESIGN与方案R5；代码与三尺寸真实GUI分项验收。

首批已集成后，隔离GUI复现音轨导入/试听控制把字幕轨推到首屏外，违反DESIGN既有剪辑三轨同时可见要求。追加VideoEditor.tsx、AudioTimelineEditor.tsx、VideoEditor.module.css及相应定向测试的独占最小布局/职责修复：同一控制器拥有音频交互，三轨沿同一时间尺，控制工具不能挤掉必要轨道；播放器真实比例、选择/播放/草稿、导入/试听/取消保持。只修既有可见契约，不重设计三栏或新增设置开关；1024×700及两个已失败尺寸和桌面代表截图分项复核，新源码摘要与首批分开。

主控独占AGENTS.md、docs中的活动权威、.project-control中的合同/计划；可直接实现尚未分配的模块，但必须先登记文件。可见实施线程身份在创建回执后补入本合同，只有当前角色独占范围有效。

本波主控另独占R8手机结构：apps/mobile/lib/ui/companion_app.dart及实际提取的页面/控制器文件、apps/mobile/lib/data/local_media_draft.dart、local_media_draft_controller.dart、voice_sample_io.dart、voice_sample_controller.dart，以及对应test中的最小必要调整和apps/mobile/README.md的职责索引。保持host_api/mobile_store既有公共协议、包名和安全存储键；不改主机接入、不实现假上传/登录、不启动真实账号会话。先完成纯结构与现有定向验证，原生/GUI证据未执行时单列。

主控另独占R9打包闭包：apps/api/pyinstaller.spec及test_packaging_modules.py，修正当前只枚举app顶层模块的构建清单，使实际拆出的Python包及动态消费者进入同一候选；不改应用标识、签名、发布、依赖排除策略或安装用户应用。打包清单检查不冒充二进制真实启动。

角色互斥：API巨型文件和共同schema由一个负责人连续接续；Desktop独立文件并行；QA不写产品源码。共享契约变更提交证据建议，由主控更新权威并通知受影响线程；旧合同必须重新绑定。不得自行创建内部子智能体。

报告包含：候选Git/diff、确切文件/符号、相关命令与结果、首个失败、结构/持久/媒体/GUI的证据等级、剩余消费者和未验证项。当前阶段报告不等于全面完成。

共享main的Git提交由主控统一收口；实施者不自行stage/commit其他并行候选，完成当前批后交出SOURCE_RELEASE、确切文件及源摘要，该批写权交回主控审核集成。必要的集成修复须绑定具体文件并通知原负责人，实际变化另记候选与相关验证；新增文件同样纳入候选身份。尚未释放的独占修改可继续，不因等待其他角色的验证而重复全量测试。

## 退役、回滚与后续

### API后续写权：R2、R3及R4资产/档案

R1版本修复源码经主控/QA解除BLOCK后，原API线程继续独占原范围和新增apps/api/app/creation/editing/**、creation/contracts.py、creation/transactions.py、creation/projection.py、media/contracts.py、media/jobs.py、media/rendering.py、materials/**、profiles/**、api/**及bootstrap.py中的实际模块；不先建空目录。新增现有文件写权为audio_timeline.py、linked_timeline.py、compound_edits.py、subtitle_edits.py、subtitle_alignment.py、subtitle_tracks.py、subtitle_style.py、graphic_overlays.py、graphic_sources.py、transitions.py、silence_edits.py、avatar_lipsync.py、device_runtime.py、device_scheduler.py、task_runtime.py、granted_subprocess.py、artifact_store.py、media_script_jobs.py、assistant_goal_continuation.py和conversation_repository.py中与本次目标回执直接有关的部分。对应既有动作/事务/执行/接续测试允许最小相关调整。R4资料首批独占文件、Pi/CLI适配及Web/Desktop/Mobile保持互斥。

R2按活跃动作形成具体输入、计算与联合变更集，保留一个版本/提案/跨轨保存事务。R3按媒体用途接续持久执行，显式携带原身份/版本/许可/取消/回执，逐组退役伪造HTTP、反射与路由重入；任务/设备检查点原子边界保持。R4素材/人物的中立类型、写入负责人和存储计算逐项迁出production，不让仓储依赖模型执行。R7后端bootstrap最后移出导入期运行组装。每个稳定子批先交源码、定向测试、退役证明及精确摘要供集成；同候选真实媒体门禁缺口单列，不能因此停止其他独立的已授权结构工作。对外协议、权限、存储格式和用户数据均保持。变更公共共享契约先报主控，不能以此授权签名发布或平台外发。

R1旧目标：仅该路径的伪造HTTP执行、路由反射/重入、隐含业务上下文和重复写入口；其他尚未迁移动作逐项分类，不能因一个动作通过就删除整套执行协议。R7旧目标：main中迁出的具体实现及其重复状态/全局依赖。

同一事实只有一个写方向；短期导入映射在本批消费者全部切换时退役。源码回滚保留所有用户数据和新历史，不reset用户脏树，也不重放未知作业。

R0/R1达到门禁后由主控继续分配R2–R9，并按实际文件释放/接续写权；独立的已授权工作不因某个真实环境阻断全部停止。最终需要同候选核心真实闭环、所有旧目标RETIRED及权限/数据/恢复证据。

旧暂停交接见 [pause-20261013](takeover/pause-20261013.md)，只供已保存WIP与未验证项定位，不提供新的授权。
