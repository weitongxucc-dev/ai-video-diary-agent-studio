# 活动合同：曜核一句话创作、批量拆解与素材标签

## 2026-10-12 PI-OPEN：按已审方案实施并同步教学

用户最新明确授权按[0.11计划 §4.2](0.11-development-plan.md#42-2026-10-12pi-开放能力脚本扩展与外部-agent-接入方案)实施 Pi 接入改造，开发阶段同步教学线程并及时提交 GitHub。本绑定接续此前只读审阅；下方历史包不恢复写 lease。owner=/Volumes/Dujiangyan-VM/AI视频日记/repositories/desktop-v32；branch=codex/desktop-v32-20261010；起点16670415e41e2a4cac9ea10acec76d76d9b56153，包含已保存方案c823d3c及main的手机整合5968e9b。原original脏树与运行中的用户实例保留。

用户随后因Linear容量限制选择GitHub Issues；本批总任务为[ #117](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues/117)，PI-01–06对应#118–#123。Issue维护动态状态/阻断/候选与实证，不与仓库重复写进度；该管理入口变更不改变已绑定的产品接口、文件Owner或执行许可。

root独占共享权威、合同、集成验证及Git保存，不写产品代码。当前第一波以“换第二镜头并保留旁白”贯通读取、类型化提案、实际预览、应用、新版本与撤销；随后依次闭合编排、事件接续、CLI、隔离媒体脚本及工具复用。中间派工或检查点不是再次征询继续的节点。稳定规则绑定架构§3.8/3.12/3.13、领域§7、产品§2.3及认证文件边界；实施线程先完成语义REBASE_ACK。

文件所有权：API实施线程01a12a6c-9159-70f3-aec3-10e7919a207b独占main.py、schemas.py、production.py、conversation_repository.py及直接API/Repository测试；Pi实施线程01a12a7e-52a9-7241-a0af-db4ef06b91ff独占assistant_pi.py、agent_tools及直接宿主/工具测试；脚本线程01a12ab8-212b-77e0-b261-11ff92e05fc8独占新增media_script.py、确需的model_workers/media_script_worker.py与test_media_script.py/test_media_script_isolation.py，交付供既有业务Owner调用的叶模块及真实短隔离机制检查，不跨写任务、资源、中央API或共享文档。Web types/API/UI、任务媒体执行/资源策略、CLI入口的后续写权按已批准方案由root在相应接口稳定后逐包绑定，不与上述writer同写。教学线程01a13039-45e5-76d0-8fbb-8ff3c02b0e6f只读授课，不参与产品写入；不派内部子代理。

PI-02代码编排采用installed Pi 1.1公开createCodemodeExtension的models:false选项，保留可信扩展加载与业务子调用，禁模型直连及整机builtin Bash。PI-05当前macOS首包选择Seatbelt单进程Python/Pillow候选：可信Python -I -B -S bootstrap在任何模型代码执行前一次sandbox_init安装完整deny-default/no-exec/no-fork/no-network策略，失败退出，不退回外层sandbox-exec精确Python reexec放行；PID/session-leader及grant身份保持。可信FFmpeg仍由已有业务执行入口处理。Seatbelt接口及sandbox-exec已弃用，实证只覆盖当前平台候选，不宣称跨平台/签名分发；实际边界与随包运行时缺口明示。输出限固定literal文件，首包不开放模型可写tmp；真实必要scratch及目录quota必须由Owner验证，RLIMIT_FSIZE不作为总目录quota。允许自造canary、极小图像与本机测试监听器的低负载隔离检查，不触私库/凭据；现有资源档未知时生产函数明确不可执行，先回报精确集成接口，不冒用旧FFmpeg预算。

第一波接口：新增当前任务编辑能力读取，返回来自CreationTaskExactEditRequest的真实schema、预览可执行动作与前置/原因；Pi可按需读取细粒度任务对象和作业结果。类型化预览复用/api/creation-tasks/{task_id}/edits/exact/preview，来源/基础版本由可信入口绑定并执行前重新校验；模型参数不覆盖原句、成员/空间或当前目标快照。提案应用沿现有confirm入口补稳定请求重放与真实结果查询。精确GUI既有调用保持，原句入口作为同一领域动作的消费者，不再是Pi唯一动作表达。Pi工具侧消费共同schema，读取/预览工具可跨阶段发现；已确认文案/应用决定与生成动作仍沿既有对象规则，不从代码编排推断授权。

变更接受条件：真实对象/参数→类型化提案→可检查预览→应用→持久版本→撤销与重启回读；严格单位/无效字段/旧版/跨成员空间拒绝，重复应用/回执丢失能返回原结果，已有精确编辑回归。工具声明/fixture通过不替代当前模型与真实UI/产物验证。候选和最小相关检查先保存；合流后同候选集成，教学同步已证实的变化、原理和未完成项。

迁移与退役由root纵向验收：旧目标为preview_edit仅两个替换字段的模型主入口、阶段作为唯一工具能力门、一次预览后固定停住/只靠UI轮询的接续，以及逐端复制的动作规则。旧路径在尚有未迁移消费者时只能显式有界共存，共用原Repository/动作校验，不作为typed失败时的自动fallback；相应操作在同候选完成真实预览/应用/保存/撤销后移除旧默认注册/消费者，最终检索与运行清单确证不存在。回滚以已验证的版本和开发候选为界，不删除用户历史提案、素材或数据，不恢复已撤销授权。CLI/脚本新增路径与退役证明分别记证据，未完成退役不称整体改造完成。

运行与外部范围：沿既有授权使用当前文字连接；新验证用独立库与明确合成/授权样本，不复制私人DB或更改用户模型凭据。短预览/脚本必须受同一设备许可、真实隔离和有界资源保护，不放开整机Shell、不盲增预算或安装依赖。旧用户素材/声音/数据删除、平台外发、新供应商/费用、签名与公开分发不在本绑定；不替换当前已安装应用。root在完成已授权实现与必要验证前持续推进，遇真实外部条件仅标记对应验收，不把静态通过当运行完成。

## 2026-10-12 接续与本轮文件归属

本轮 PR #115 修复与合入授权（C1）：用户明确选择“授权当前线程修复并合并”。当前线程独占该 PR 合并阻断的源码、对应测试及 lint 修正；以 `d055d07` 为起点，保留已批准产品/领域/设计/安全规则，验证通过后通过 PR 合入 GitHub main 并同步本地主入口。此授权覆盖本轮主控直接修复的例外，不恢复其他实施 lease，不包含真实用户数据迁移、模型调用、平台发布、签名或分发。其余原工作树及正在运行的用户实例保持；本轮源码修复例外在 PR 合入和本地主入口同步后结束。

2026-10-12 GitHub 保存授权：用户要求当前代码及时保存并提交 GitHub。root 审核本工作分支现有源码、测试和共享文档后创建开发检查点并推送 `codex/desktop-v32-20261010`；历史单独保存授权已由上述本轮修复与合入授权接续，发布仍不在范围内。私人数据、声音样本、模型、安装包和本机验证证据不入库。检查点保留尚未通过的真实配音与两分钟成片验收，不以提交或推送作为完成证明。旧实施 lease 保持 RELEASE/HOLD；本轮修复仅按上述当前授权执行。

### 当前续作：配音资源复核与启动退出诊断

NARRATION-RESOURCE-20261012（C2，用户已批准）：本轮替代已 RELEASE/HOLD 包中的配音固定3GiB运行余量与完整限速预读绑定，架构规则见 system-architecture.md §3.12 新增配音启动/运行段落。范围仅 creation_narration：准入仍检查原1GiB系统底线+2GiB新增预算，真实受许可运行改用原1GiB系统底线，并继续原进程RSS/线程/交换/CPU/IO、输出、设备锁、有限超时及确证回收检查；其他能力规则不改。正式原模型和codec精度保持，旧半精度QA不进入产品。

实施包已由可见线程01a12a6c-9159-70f3-aec3-10e7919a207b REBASE_ACK并RELEASE，无续写lease；交付范围仅 apps/api/app/host_resource_probes.py 的配音启动/运行内存判定、apps/api/model_workers/qwen3_tts_worker.py 的完整预读退役、apps/api/tests/test_host_resource_probes.py 与 test_voice_clone.py 的直接相关检查，以及 test_qwen_tts_worker.py 正常加载fixture中旧预读mock的必要退役。不新增供应商/参数开关/第二执行器，不改 granted_subprocess、其他资源能力、用户数据或配置。原生成方法、真实加载阶段日志、缓存限制及其他能力保护保持；root审阅后进入同源真实配音验证。

root独占架构/合同、既有校准QA脚本与集成运行：源码稳定后先用原模型、固定9秒本人参考音频及原20秒输入做一次隔离真实许可试验；本次先保留90秒、1.5GiB进程RSS与16线程，其余原保护不变。完整音频、真实峰值/时长、许可与进程确证释放才算该验收通过。若新限制被实测触发，只沿具体失败证据制定下一有界包络，不无限原样重试、猜测放宽或自动切模型；未完成配音不配置正式任务预算。后续已确认script_1a6f964741c4/task_93d78477016b的真实配音和成片仍属已授权目标。代码与验证按阶段提交并推送当前GitHub工作分支。

NARRATION-CAPACITY C1：原20秒policy-r1在11.42秒真实加载中由GrantedCommandResourceLimit明确拒绝1610792960字节大于原1.5GiB；此时最低主机可用5765562368字节，CPU/线程/IO未作为首因，全部许可/登记组释放已确证。root沿已批准的实测预算校正，下一单次20秒隔离包络将进程RSS及对应启动新增预留同时限定3GiB，保留原1GiB运行系统底线、90秒、16线程、CPU/IO/交换/输出/磁盘/父进程与回收边界。只允许QA校准文件中的这一明确有限组合，默认旧包络及其他能力不变；不是正式预算，也不增加正式产品开关。原模型精度/输入保持；本次如失败，按首因复核，不无限逐档放大。root唯一QA/合同writer，产品源码lease继续RELEASE。

NARRATION-TARGET C1：policy-r2原模型短配音8.8秒已完成，整体20.16秒，100ms采样进程组峰值2106998784字节/16线程，内部MLX峰值6468268321字节，最低主机可用3483582464字节；这些计数口径分别保留，不相加或互相冒充。配音产物已提交，单次QA随后取消后续制作，许可/组释放确证；不称整片作业成功。root下一单次完整正文隔离验收只读取native-m3-disabled-good保存回执中用户已采用script_1a6f964741c4/task_93d78477016b的原4段，以正文479字符+每段52字符参考共687字符和同9秒参考为固定输入。既有QA脚本可增加仅该固定文档的绑定，保留分段/标题/正文，隔离库重建而非复制私人DB。120秒目标包络保留3GiB RSS及启动预留、16线程、全部主机/回收保护；按短样本完整耗时20.16/8.8×120约275秒加余量设有限300秒，PCM片段与合并输出总上限16MiB。默认20秒试验不改，不进入半精度；完整输出及每段有界模型计数、当前源码一致和释放通过后才把实际预算接入真实桌面任务。

NARRATION-TARGET-THREAD C1：120-policy-r1真实完成原正文前三段，第四段在70.80秒由QA采样17总线程超过16明确停止；采样RSS峰值2421358592字节、最低主机可用2031681536字节未触及相应线，全部登记组和许可释放已确证。root下一单次完整原正文包络将总进程线程上限及同一QA停止线明确限定20，以17实测留少量库辅助线程余量；逻辑计算线程/RAYON/BLAS仍为1，CPU/IO、3GiB RSS/启动预留、1GiB系统底线、300秒、固定正文/精度与所有输出/回收约束保持。该数值只用于这一已授权目标的有限校准，不恢复旧20秒包络或其他能力写lease，不无限增加上限。

NARRATION-TEMPO C1：120-policy-r2完整原4段自然配音81.559625秒、83.63秒实际生成通过，采样RSS2424913920/17线程，主机可用由6918701056降到最低2527313920字节，内部MLX峰值8285304509字节；源一致、正常音频和全部释放通过。用户已选择“保留原稿，放慢配音至约2分钟”。可见媒体线程01a12a6c-9159-70f3-aec3-10e7919a207b唯一续apps/api/app/narration.py现有compile_narration_command/_delivery_tempo的明确倍率语法及直接相关现有测试：在已实现atempo/缓存/分段重做链支持明确0.5–2.0有限倍率，保留已有慢一点/快一点及严格拒绝未知/冲突/非有限/越界指令。原文、样本、生成方法、API形状、授权/CAS、原音高保留与联动事实源不变，不新增默认自动拉长、第二执行器或供应商。root将用约0.7倍速（原81.6秒对应约116.5秒）沿正常API实际验证；worker不启动模型/服务/构建、访问用户数据、改共享入口或派下级，检查后RELEASE。

root可依据完整配音观测把既有DEVICE_NARRATION字段接入当前私有配置：4段/687字符/120秒/3GiB进程RSS/20总线程/逻辑1/16MiB输出；全单元实测83.6秒，以180秒有限时限覆盖运行余量；按主机实测约4.09GiB可用变化，将启动新增预留设4.5GiB，运行安全底线仍1GiB。只备份同一私有配置目录并CAS保存既有其他字段，不复制数据库/声音，不改变设备身份/凭据/系统权限。桌面3ba0首日志打开未完成，Native启动项未通过；已核并清理其2997/3000失败实例。在单一正常API/同一私有库/现有身份及共享锁中继续真实任务，不以此宣称Native启动通过、不搭第二设备执行器；root拥有配置/运行/输出验收，桌面只读线程保持无源码lease。

NARRATION-PRERENDER C1：真实原任务已在正常API生成4段79.57秒旁白与草稿TimelineVersion；21个低置信笔记本/设备示意镜头待人工选择。用户要求保留原稿放慢至约2分钟，但现RegenerateNarrationSegment预览要求rendered_version_required，造成调速必须先渲染的额外顺序。可见媒体线程唯一续main.py仅该配音预览/受许可确认链及test_narration_edit_api.py、test_narration_edit_execution.py、test_narration_edit_reference_contract.py相关检查：当有已确认Script、持久配音与当前草稿TimelineVersion且所选段属当前版本时，允许成片前配音修改，以现有前后音频/时间变化展示影响；不伪造视频对比，不放宽其他编辑/素材确认/授权/CAS/资源/释放规则，不另建入口、执行器或事实源。已有渲染分支复用；缺失/旧版/跨成员/身份失效仍拒绝。root不写这些产品文件；同一正常API实际验证后再审镜头，不自动确认低置信素材。worker无模型/服务/构建/用户数据/下级权限，检查后RELEASE。

NARRATION-PRERENDER实施lease已RELEASE；root接管审核、GitHub保存和同一正常API的真实任务验证，其他产品源码lease不恢复。

CPU-SOURCE-SET C1：用户已采用21镜头示意方案；正常API真实渲染声明434414532输入字节并由HostResourceDecision以declared_resources_exceed_budget拒绝原122682302字节输入线。实际只含同一配音及3个授权视频，共约95MB，重复镜头仍由clip_count/像素秒/真实运行IO控制。可见媒体线程01a12a6c-9159-70f3-aec3-10e7919a207b唯一续apps/api/app/creation_execution.py仅cpu_render_facts的输入来源集合去重及tests/test_creation_execution.py最小相关检查：同一解析路径只记录一份源文件stat事实和输入字节，完整montage中的21镜头关联保持；来源集合变化自然产生新render摘要，不改旧排队请求资源或手动写作业。保留授权/文件修订/版本/CPU/内存/IO/输入上限/释放及其他资源类，不新增抽象/入口/执行器/兼容路径。root独占配置、合同、真实运行和Git；worker不访问用户数据，不运行模型/服务/构建或派下级，完成检查后立即RELEASE。

CPU-SOURCE-SET实施lease已RELEASE；root接管差额审核、GitHub保存及正常API实际渲染。既有其他产品文件lease不恢复。

CODEC-QA C1：r6已完成权重加载并进入推理，MLX weights_loaded活跃2054050572/cache0，首因仍为原3GiB主机余量；整型主权重及BF16不变。root决定先做可撤销的隔离精度实验，不修改正式生产模式或宣称声音数值等价。可见线程01a12a6c-9159-70f3-aec3-10e7919a207b仅独占evidence/edit-chain-20261011/qwen_codec_fp16_candidate.py，以当前qwen3_tts_worker.py为固定基线，只对已安装codec的encoder神经网络与decoder浮点参数及必要输入精度作明确实验；保留encoder quantizer统计/派生_embedding/_c2的F32，量化主模型BF16/U32、完整9秒样本、原ref_text与ICL方法、正文/分段和原所有资源保护不动。须在首次解码trace前操作真正decoder模块，不猜普通set_dtype能覆盖派生/compiled对象，不修改依赖、原模型文件或生产worker/缓存身份、不加fallback。无法通过现有模块接口作小改就明确失败，不复制外部codec库。线程仅源码/轻量隔离检查，不实际模型、媒体、服务、用户库、构建或配置；RELEASE后root一次20秒原包络验收和听感检查，实验产物只作QA，不能作为正式任务配音或当前候选完成。root独占原calibrate_creation_resources.py的仅此精确实验worker路径绑定及SHA记录，默认原worker不变，不另建执行器或绕过许可；无生产写lease。

QWEN-THREAD C1：用户再次退出应用后，原20秒r5起始可用约5.5GiB；最低可用3999514624高于原3GiB，实际运行检查先拒绝进程组20线程大于16。可见线程01a12a6c-9159-70f3-aec3-10e7919a207b仅续apps/api/app/voice_clone.py的Qwen3TTSSubprocessProvider._run_command受许可子进程环境及tests/test_voice_clone.py直接目标测试写权：已安装Qwen post_load_hook使用AutoTokenizer，本机tokenizers二进制及官方bench均消费RAYON_NUM_THREADS，现受许可环境只限OMP/OpenBLAS/VECLIB而遗漏Rayon。仅补RAYON_NUM_THREADS=str(grant_context.threads)，不改无许可路径或加第二开关；池在新子进程启动前受许可限制。该遗漏是否为20线程全部根因仍须一次实际验证，不能先宣称解决。原CPU、16线程、1.5GiB RSS、3GiB余量、90秒与设备锁/释放保护均不动，不改worker/模型/供应商/依赖或其它源码，不真实模型/服务/数据/构建。目标检查后RELEASE，root同候选实际校验；其它lease保持RELEASE/HOLD。

root的同一QA文件仅补r5暴露的诊断捕获缺项：运行期线程预算异常也带有原command_diagnostics；只观察同workspace/job/database的真实Qwen _run_command异常并沿已有固定阶段/有界数字白名单记录，不能把此记录混入HostResourceRefusal判定，不重调许可/模型、捕获原始正文或改变任何保护/退出策略。

CAPACITY-READONLY（已完成，保持HOLD）：用户释放内存后，原20秒包络单次r4仍在模型加载期因可用内存2796306432小于原3221225472中断；不继续原样重试或放宽保护。可见线程01a12a6c-9159-70f3-aec3-10e7919a207b只读指定r3/r4回执、Qwen worker及真实已安装加载器、许可与资源检查，无源码、QA、服务、模型、真实库写权。未证实重复模型驻留或预算算术双计；完整预读后实际加载器再读取不等于两份驻留。root只在现有calibrate_creation_resources.py保存原HostResourceRefusal已有command_diagnostics的固定QWEN_PHASE和有界QWEN_MEMORY数字，原始stderr、正文、路径及凭据不保存；不新增模型/媒体调用或改变包络。下一次原20秒短配音须先有明显更充足的起始可用内存，操作计划暂按约6GiB核对后单次带阶段计数验收；这不是改变原3GiB运行余量、1.5GiB进程RSS或其他保护线，未实测仍不能标定或启用真实任务预算。

STARTUP-EXIT-DIAGNOSTIC C1（已RELEASE，无续写授权）：可见线程01a12a7e-52a9-7241-a0af-db4ef06b91ff仅apps/desktop/main.js的failLifecycleLog失败退出链及直接async目标测试。上一实机首写超时后最后只有lifecycle-log-failed，不能证明错误对话框返回或app.exit已调用；精确补三个固定stderr检查点：错误对话框调用返回后、stopAllServices完成后、app.exit(1)调用前。保留原控制流、退出码、同步对话框、日志与用户数据路径、60秒门禁、单实例锁及所有资源规则，不做超时/权限/日志路径或退出策略调整，不写harness端口/cleanup、其他产品文件或用户配置。root负责候选构建和一次实际启动/退出取证；其它源码lease继续RELEASE/HOLD。

用户要求继续。现有MiniMax-M3路由已实时确认；官方OpenAI SDK文档说明reasoning_split只分离输出，M3默认thinking开启且可显式disabled，thinking tokens计入max_tokens。已有真实核查4799/4800 token用于thinking且无报告；提升预算/等待后仍失败。root本轮只恢复SOURCE线程01a12ab8-212b-77e0-b261-11ff92e05fc8对topics.py的CreationTask Script.generate与_check_script请求及直接目标tests写权：仅精确MiniMax-M3加入thinking.type=disabled，保留reasoning_split和原单report工具；不改共享config helper、其它model/legacy/topic请求。8192/120暂保留用于一次修正对比，不再继续放大。全事实/制作约束/quote绑定/权限/CAS/完整报告判定仍严格执行，不以关闭思考代替核查。保持生成一次+核查一次、无自动重试/修稿或新supplier/凭据。线程仅隔离检查，root真实good/bad/自动生成/保存与GUI验证；其它源码lease均RELEASE/HOLD。文案版本未确认前不启动新任务配音。

M3控制包已RELEASE；实际109秒稿4.68秒保存，无据稿422且原稿未变。同一SOURCE的Script.generate中文输出预算提示与report_schema、直接tests包已RELEASE，无续写授权：自动任务task_2582144a5fb3两次完整报告分别估171.2/177.9秒，未保存；停止原样再试。对zh-CN，未指定模板时请求最多5段（域/手工合法1–20不变），按实际目标与CHINESE_CHARACTERS_PER_SECOND给每段明确字符预算并反映到content.maxLength及user requirements；模板段数依现契约精确消费。只有生成请求更具体地分配全局已有时长预算，runtime总时长/语言/完整报告/源事实/字段/权限/CAS判定不放松、不新增per-section运行拒绝。EN/混合与其它入口保持。不增调用/重试/自动修稿/新参数config，max_tokens/time/thinking已冻结；root一次自动生成实测。用户已明确采用script_1a6f964741c4实际正文，沿task_93d78477016b、voice_cc/v2、9:16、0%人物推进配音和成片，不再以文案未确认阻断该任务；配音预算必须先在原资源保护下实测通过，不能用临时包络冒充标定。

用户在聊天01a12cf6-92b6-7793-965b-350a44278473批准开始上一轮下一步安排，该聊天root接续为本轮唯一协调与验收负责人。实现owner、HEAD及既有外部操作/私人数据边界保持下节约束；旧主控聊天因上下文超限不再派单。已RELEASE的QUOTE-TRANSPORT与QA-REFUSAL交主控审阅、目标检查及实测，不重新实施。

本轮目标仍是一句新目标到真实两分钟、中文与英文字幕成片及局部修改/撤销/保存/重启回读；三个互斥工作包为文案来源与保存、真实阶段容量/连续执行、正常桌面启动。root负责共享文档、审核、真实模型/媒体、构建安装和同候选GUI验收，不编写产品源码。批量/标签旧HOLD保持，不新增供应商、放宽资源保护、复制私人库或公开发布。

- STARTUP-12（已RELEASE，无续写lease）：可见线程01a12a7e-52a9-7241-a0af-db4ef06b91ff独占apps/desktop/main.js及直接相关lifecycle测试。锁前后检查点已RELEASE并装入a5e候选，正常LaunchServices尚无记录且采样为uv_fs_open；同包直接binary工作台/API可用，锁耗时3.5ms。现续同窄lease补关键初始化及日志打开前的固定stderr阶段，由root用open的stdout/stderr接管到精确证据文件比较；不猜具体打开路径或根因。保留appId/userData/锁及身份，禁止绕过单实例锁、清空数据/锁文件或开启FDA。线程仅源码与隔离目标测试，不构建/安装/启动真实服务。

STARTUP-12-ASYNC（已RELEASE，以下仅保留该包范围）：7369正常启动的独立内部stdio实测明确卡在首次fs.appendFileSync，未进入锁。TCC仅AllFiles预检拒绝，不证明需FDA/RemovableVolumes授权，不能改权限或签名requirement。原writer续main.js/目标lifecycle测试：将原同一路径/JSONL日志改为单一有序异步写入，启动门禁以真实首条写入完成为准；日志挂起按既有启动时限明确失败/可执行错误，不伪造成功、不静默丢日志、不递归写故障logger，不让后台服务启动后无限待写。保留单实例锁与原数据路径/设备身份；先异步首条日志成功再进入原锁/服务流程。失败显示明确日志边界与修复动作，安全终止本实例；日志权限外部条件仍须同候选验证。其他包继续，线程不真实服务/数据/构建/权限修改。
- SOURCE-12（已RELEASE，无续写lease）：现有正常粘贴→SourceItem→KnowledgeItem固定版本已由root原生保存真实事实/边界说明；本轮首片用新请求/新Plan引用该版，原120任务/Plan/未确认稿保留，不增加Plan来源修订/历史字段或私库重绑。a5e原生bad稿核查已422 script_fact_unsupported且原稿未变。可见线程01a12ab8-212b-77e0-b261-11ff92e05fc8窄历史clip fixture已RELEASE并复验；现仅独占CreationRequestPanel.tsx、必要types.ts/creation-request-api.ts相关DTO及目标Web测试，补正常显式选择现有声音版本→沿现PATCH requirements.selected_voice/CAS保存本请求→新窗口重新核对的消费者。复用现可读声音及正常服务端预检；不改global default、不新API/schema、不自动认领/myvoice猜测。保护其他要求/goal草稿，失败不写成功，已确认窗口不能改共同要求。其他产品源码lease保持RELEASE；线程不真实数据/模型/服务/构建/安装/下级。SOURCE历史回查/视频解析入资料的方案仍是未实施缺口，不作为第一条成片的前置工程。

SOURCE-PROTOCOL-12（已RELEASE，以下仅保留该包范围）：声音Panel三文件已RELEASE，root集成。新原生task_ee98ebf0d9d2固定真实知识/voice v2、120秒/9:16/0%已建；点击选题实际content_json_invalid，而同包核查单report工具已实测正确拒绝bad稿。同线程现仅独占topics.py的CreationTask选题与文案生成两个真实OpenAI-compatible入口及直接相邻provider目标测试/必要正向fixtures，沿单次无副作用报告工具结构返回，退役这两入口的json_object/content报告解析。保持原候选/分段/引用/语种/时长校验、生成一次与既有核查一次、公开API与权限/CAS；无工具执行/回填/循环/自动重试/换供应商，无其他legacy或文案库生成器写权。root实际验证新Task选题→候选文案/有据稿保存；未confirm实际Script前不启动这条任务制作。

SOURCE-SCORE-12：前包已RELEASE并装入7483，实际单报告已越过解析但选题评分校验拒绝，未保存候选。仅选题评分拒绝点安全类型/有限数值/存在性/字符串长度诊断及一条评分意义提示已交付RELEASE，不记录正文/凭据，不coerce或放宽评分。root新候选一次实测据真实诊断继续，未确认稿保留。Async启动包已RELEASE；root完整lifecycle首测由自己在跑的App占用3000而正确拒绝，释放该实例后再验，不改端口或harness行为。
SOURCE-LENGTH-12（已RELEASE，以下仅保留该包范围）：root发现原窗口模型额外加入单asset/八a_roll限制，已按CreationRequest原始requirements经正常新窗口建立task_93d78477016b，旧任务保留。该Task实际选题已成功并确认，但5784文案生成48.62秒后因report length被拒绝，未保存草稿。原SOURCE可见线程仅续topics.py的generate文案prompt与直接目标测试：在未指定模板段数时建议较少完整段落和简短画面意图，减少报告重复；有模板段数仍严格服从，合法1–20及原字段、时长、来源、审查/CAS不变。不增加4800输出上限、调用次数、重试或修稿，不改模型/参数/validator/事实与业务契约。root同候选重新实测；实现线程不服务/数据/模型/构建/安装。其他源码lease保持RELEASE／HOLD。

SOURCE-GENERATE-CEILING-12（已RELEASE，以下仅保留该包范围）：上述提示包已RELEASE，5062正常API实测53.01秒后仍明确report length，草稿为空；两次实测证明本次生成在现4800上限未完整返回。root据已授权文案闭环明确批准仅将CreationTask Script.generate的单次max_tokens由4800改为8192，再一次实际核验；不据截断猜测内部原因，不改提示、模型、温度、超时、来源/字段/时长/核查/CAS、供应商或凭据。核查仍一次4800，选题仍3200；无自动重试/修稿/工具执行/循环。原SOURCE线程只写该参数及直接现有断言、最小目标检查后RELEASE；root负责实际模型/构建/安装。此执行预算决定替换上一段生成4800约束，不涉及CPU/TTS/内存停止线或其它路径。
- CAPACITY-12（已RELEASE，无续写lease）：root沿原120秒包络实测CPU音频r3成功、真实产物/正常终态及全部许可释放，不能推导新TTS/渲染通过。CPU画面20秒r1触及原线程停止线（19>16），内存/磁盘线未触及，释放已核。QA线程01a12a6c-9159-70f3-aec3-10e7919a207b先只读此精确回执和montage.py实际受许可FFmpeg构造，定位超线程发生步骤/缺失限制，提交最小修正或一次必要取证方案；无新增产品/QA写权，不改线程停止线、预算或采样规则。其他预览源码lease保持RELEASE。

CAPACITY-12-SUBTITLES（已RELEASE，以下仅保留该包范围）：只读核已证实字幕每个时段各建循环PNG输入，20秒五张图增加五个管线，120秒会增加三十个。该QA线程现独占montage.py字幕排版到FFmpeg输入的直接路径与对应最小renderer目标测试；将现有已排版时段图沿一个有时序的透明字幕输入消费，退役CreationTask每cue一个loop输入，保持双语布局、样式、空档、首末帧及图形层级。复用job暂存/当前grant与产物链，不增队列/预算/模型或改变16线程停止线。普通图形叠加和其他未受影响路径保持；若同一helper自然共享消费者，报告影响而不另建兼容实现。仅隔离源码/结构目标检查，真实同包络20/120媒体由root执行；不运行媒体/模型/服务/用户DB/配置/构建/安装/外部操作/下级。RELEASE后root核正向同字幕结果与旧逐cue输入不存在，并补montage.py实测哈希。

直接权威绑定：产品§14、架构§3.12/3.13、领域任务/来源语义；启动另绑定apps/desktop/README.md与RELEASE.md。证据沿用edit-chain-20261011精确目录，新实测使用新的明确命名输出。源码、隔离检查、安装、真实媒体和整个闭环分别报告；已存在检查不无故重复。

CAPACITY-12-CROP（已RELEASE，以下仅保留该包范围）：r7在主编码开始后触及原512MiB（554090496），主机可用3540320256且无host拒绝，字幕流仍逐帧解码1080×1920全画布。原QA可见线程仅续montage.py的CreationTask单一字幕输入及直接目标renderer测试：以所有字幕帧非透明像素的共同范围裁去外部全透明区域；所有时段/空档帧维持同一尺寸，按原坐标叠回。范围按偶数像素外扩/裁界，避免yuv420 overlay位置取整造成位移。保留全部非透明像素、双语字体样式/描边阴影/空档首末帧/层级，主画面1080×1920/30fps/x264预设CRF及普通图形路径不改；不增全局限值/预算/配置或降质量。须用完整画布重建验证像素与alpha一致及共同尺寸/坐标后RELEASE，root原包络20/120实测。线程不媒体/模型/服务/数据/构建/安装。

CAPACITY-12-ENDPOINT（已RELEASE，以下仅保留该包范围）：裁剪已RELEASE且RGBA重建通过，r8实际仍触及512MiB（657424384）而主机余量充足，不能称裁剪已解决容量。root据有限主画面/apad同钟与FFmpeg同步队列的只读证据，批准原QA线程仅在CreationTask build_command以T=Σ(round(end×30)−round(start×30))/30的输出-t替换-shortest；复用现apad同一终点。Legacy输出策略、字幕/主画面过滤器、x264/音频参数、产品音视频流时长验证、所有许可/准入/停止线不变，不增加其它队列参数或放宽质量/校验。目标检查覆盖分数边界同钟、Legacy及音视频过早结束仍拒绝；root实测正常产物/末帧/声音尾部/RSS前不称同步队列为根因。完成最小检查后RELEASE；线程不真实媒体/模型/服务/数据/构建/安装。

SOURCE-REVIEW-CEILING-12（已RELEASE，以下仅保留该包范围）：109秒估算的四段有据稿修订实测核查length，安全诊断明确completion_tokens=4800、reasoning_tokens=4799、tool_call_count=null，原约80秒草稿未变。root批准原SOURCE线程仅将_check_script单次核查max_tokens4800→8192及直接旧断言；生成8192、选题3200、provider/温度/超时/提示/片段绑定/判定/权限/CAS/次数均不变，无重试修稿或工具执行。完成最小检查后RELEASE；root安装后对同一修订再作一次正常保存核验，不把更高上限当成功。CPU/TTS/内存/线程等保护线不变。

SOURCE-SCRIPT-DEADLINE-12（已RELEASE，以下仅保留该包范围）：a213同一109秒修订90.20秒transport_failed、原稿未变；ScriptGenerator默认请求时限90.0且实际工厂未覆盖。root批准原SOURCE线程仅将该类默认timeout90.0→120.0以匹配已绑定8192单次报告预算，保留调用者显式timeout覆盖。只更新必要现有断言、最小目标检查后RELEASE；不扩大到其它类/全局配置，不改变params/prompt/schema/权限/CAS/次数/生成或事实判定，不增retry/fallback。此时限是文字服务请求等待；CPU/TTS/设备许可/内存/媒体时限保护不变。root同一修订再核前不宣称网络根因或成功。

## 2026-10-11 用户批准与边界

用户已认可上一轮七项方案“都是没问题的，可以做的”，补充一条请求可制作5条视频、按需要澄清关键条件，以及VLM识别/手动自定义/按习惯建议标签。此前Pi/界面合同的源码lease全部释放；旧合同保留在本轮精确证据目录 prior-execution-contract.md 作为历史证据，不能恢复旧派工。

owner=/Volumes/Dujiangyan-VM/AI视频日记/repositories/desktop-v32；branch codex/desktop-v32-20261010；HEAD d3fe1240aa6197219105c6a42264fe1014637b47，全部既有dirty改动保留。root唯一产品解释、共享权威、集成、真实状态/用户配置/安装writer。MiniMax既有TokenPlan文字调用授权继续；原始媒体保持本地。不得复制私人DB、放宽版本/授权/CPU/TTS准入、改密钥/供应商、删除用户数据或公开发布；临时FDA已经off，保持off。Supabase/手机/照片/120秒不足须按具体验收明示，不以20秒已有任务重渲染证明。

## 本轮纵向结果与迁移

root为迁移验收负责人。用户纠正5只是示例，数量可能为100或10000，并要求从第一性原理先冻结统一系统设计。当前批量与标签写lease均HOLD且已ACK，候选diff保留；禁止按五条特例、一次N项数组或一次创建N任务继续实施。先完成Goal/约束/世界状态/计划/执行/证据/记忆的统一设计与接口审查，再按重新绑定的纵向用户闭环恢复。这不暂停用户整体目标，也不将已写候选或原20秒样本当完成。

2026-10-11 用户已要求“开始做”，并优先询问用户偏好的持续记忆。双层循环按架构第3.13节，开发安排按[既有0.11计划第4.1节](0.11-development-plan.md#41-2026-10-11目标驱动-agent-主线开发安排)继续。当前启动 MEMORY-01 纵向包：沿 CreatorProfile 持久化可信成员的明确偏好，实际接入 Pi 每轮读取、保存/忘记及既有档案界面；域语义绑定领域第4.5节。root同时核执行连续性与请求主线后续入口，不恢复旧批量/标签 lease。

MEMORY-01：root独占main.py、schemas.py、assistant_pi.py、types.ts、共享权威及API/宿主集成测试；后端worker独占creator_profile.py与新test_creator_memory_repository.py；工具worker独占diary_tools.ts与新test_assistant_memory_tools.py；界面worker独占profile/page.tsx、profile/ProfileWorkspace.module.css、profile-api.ts、SettingsCreators.tsx及新增目标Web测试。所有既有dirty先读diff，不覆盖。worker不下级、不运行服务/模型/构建、不访问真实用户库；目标测试使用独立库。允许对既有creator_profile表做保留数据的成员归属/修订号扩展；不新增万能记忆事实源，不为验收打开用户私人DB。

接口绑定：CreatorProfile响应新增revision（默认0用于旧记录）；人工PATCH携expected_revision。GET /api/assistant/memory 返回当前成员档案的revision/common_expressions/banned_words/style_preferences；PATCH同路径只更新或忘记单一偏好，参数action=remember|forget、key、value（remember时必填）、expected_revision、assistant_turn_id、source_quote。服务端绑定当前running轮次和可信成员，并核source_quote来自该轮用户原句；只有明确保存/忘记语义可写，建议或临时任务要求不得默写。工具回读后才能声称已保存；重放同一轮相同动作不得重复。源记录只保留必要轮次/指令依据；档案数据作为资料注入，不作系统指令。旧Episode偏好不再由新Pi偏好工具自动消费，旧自动学习写入口改为不可执行并移除界面动作，历史数据保留。

验收：隔离库新成员明确保存→新会话读取→宿主/仓库重建后仍在→当前要求覆盖默认→忘记后不再注入；同空间双成员隔离、CAS冲突和相同动作重放；真实Pi文字回合与GUI可见回读另验。上述范围内源码lease替换只读规划绑定；其他计划包开始时由root冻结接口与互斥文件，不因本包解除HOLD而取得全库写权。

AG0-R1局部绑定：现有记录不足以确定原生渲染停顿的首个异常，不冒认根因。工具worker释放MEMORY文件后，独占creation_execution.py与新test_render_pressure_recovery.py，先定向复现“产物已提交后的可恢复HostResourceRefusal”是否停止全driver；若复现，只复用既有安全defer/释放核验并保留产物，后续再验证、收尾，不重渲染。不捕获未知采样/声明错误/未证实释放，不自动重启driver或放宽准入。root此间不写creation_execution.py；其余API/UI/记忆集成继续。专项模拟压力只证明该失败路径，不替代原生无重启持续成片验收。

MEMORY-01-LEGACY局部退役：界面worker独占apps/web/src/app/learning/page.tsx、apps/web/src/app/actions.ts及对应redirect测试，将旧普通学习页转到/profile，去除旧Episode偏好卡的UI/server-action消费者；保留历史数据及仍有真实消费者的其他动作，不改中央API。root补旧标签候选已存在的可选label共享DTO以修构建差额，不恢复标签worker lease，不因此声明完整标签/检索已验收。

MEMORY-01实际回合差额：MiniMax会重排引号，来源原句不再由模型逐字传回；工具参数仅action/key/value，宿主从本轮原指令及真实轮次构造source_quote，API仍绑定当前running轮次、成员、明确意图与CAS。普通Model上下文/读写工具正文去除档案ID及修订号，传输和UI保存内部保留必要修订。只读user.md从当前成员同一数据库投影，入口GET /api/assistant/memory/user.md，界面worker沿既有lease添加导出已保存偏好动作，不自动加载/导入任意文件。真实验收只用独立库/目录，复用既有MiniMax连接只读获取凭据；不复制/改写私人DB或原工作区配置/密钥。

批量请求：保留用户数量和明确约束；“5条”不能写死成1。一次请求需区分不同主题/同主题不同版本/系列分集；缺少影响成片的关系、时长/素材等关键决定时先提出少量具体问题，已明确或已保存偏好不重复询问。CreationRequest候选承载持久总目标与共同约束，目标数量、规划窗口和运行并发分别表示；只在有限窗口内形成可审阅目标并建立独立Plan+CreationTask，不一次产生N个计划/任务，不把固定批次大小当用户数量上限。每条Plan的生产目标为一条。数量/素材范围/请求身份固定，重试不重复建五条，不绕过各条文案与生成/发布确认；汇总从真实子任务读取，不当第二视频事实源。退役首页target_count=1写死与旧多条数量只生成一个任务的活跃消费者；不批量迁移/复制旧私人任务。

标签：沿现有素材Repository、system/user tags及理解证据扩展。VLM识别的短标签需对应实际片段/观察；人工标签和纠正重分析后保留；助手建议单独明确待采纳，不默写用户标签。标签与自然描述被搜索和选片实际消费，技术字段/ID不常驻给普通用户。任务选择先满足主题/主体/动作，偏好不覆盖事实、当前要求和使用限制。

品牌：统一曜核1.0并使用手机线程已选Logo；保留appId、用户数据目录、认证、更新/路径身份，不因改名重建数据。源资产来自 /Users/Zhuanz/.codex/worktrees/yaohe-flutter-mobile/original/apps/mobile/branding/yaohe-app-icon.png；手机树只读，不发送跨线程消息。

## 所有权与验证

2026-10-11 ATOM/REQUEST/VIEW并行波：用户要求逐项核E01–E18是否可调用、继续持久外层/有限内层以及竖屏弹窗适配。root持有纵向集成、main.py/schemas.py/assistant_pi.py/diary_tools.ts/前端请求API与共享权威；startup独占production.py、database_migrations.py及新增test_creation_requests.py，实现ProductionRepository中的持久请求与至多8项窗口、CAS、原子确认和有界分页，不另建任务事实源，不运行真实库迁移。loop_architecture_review只读核18项手动→模型工具→编译→参数→确认/版本→渲染，结果写本轮精确证据atomic-edit-audit-20261011.md，不改源码。workspace_facts独占VideoEditor.tsx/VideoEditor.module.css/VideoEditorMedia.module.css及目标Web测试，修原片竖屏在镜头弹窗中的横向裁切，保持完整画面、比例及低高度控制可达；不改制作语义。前三者不得下级、访问私人库/配置、启动服务/模型、打包安装或改其他writer文件。root先完成现安装候选的原生提案复验，随后接入请求API/消费者；窗口持久化通过不算完整事件loop或任意数量成片通过。旧批量/标签lease仍不自动恢复。

VIEW-PORTRAIT四文件已RELEASE；root接集成。workspace_facts转REQUEST-UI，独占新CreationRequestPanel.tsx/.module.css、新lib/creation-request-api.ts、HomeCommandComposer.tsx和新增目标测试，沿root共享DTO/API接真实目标、关系澄清、有界具体目标输入/方案确认/真实任务链接；恢复按request id重新GET，不以聊天旧response作为当前状态。root独占types.ts/api.ts及手动新建表单的后续接入。历史单片方案只读恢复保留，新的数量请求不写死单条、不复制同一目标凑数。方案首窗不等于完整规划或外loop交付。

REQUEST-01/REPLAN与REQUEST-UI已RELEASE；root接接口/模型/同候选验证。ATOM只读表已冻结，不代表18项媒体通过。loop_architecture_review转E18-CONTROL，独占diary_tools.ts及新增test_assistant_edit_control_tools.py，保留root已写的请求工具，补当前版本提案读取/步骤选择/明确取消、整个上一编辑撤销重做；root持有其API/DTO/宿主归一化/UI消费者。模型不传身份/原句/路径或直接确认，proposal索引只来自当前实际有限列表；服务器核当前turn/原句/任务版本，整个上一编辑撤销不能冒复合中单项逆编辑。E06受许可媒体链缺口未解除，不能移除资源保护冒可用。

EDIT-CHAIN源码lease已全部RELEASE，root进入同候选集成/原生阶段。真实MiniMax发现并关闭“模型把明确排序对象填成界面选择”的差额：preview_edit退役command/context_stage/selected_refs模型参数，原句及默认选择只由宿主绑定，仅替换候选对由模型从本轮实际列表选择。镜头标签在task轮次按写后回读回执归一化普通回复，不显示内部ID/用途代码，不将失败说成保存。83个内核/接口相关场景、后续67个宿主/工具相关场景分别通过（重叠，不相加）；最终实际MiniMax同会话交换→确认/撤销、选材替换→确认/撤销、片段标签→回读均通过，隔离GUI原句交换提案、应用、撤销已实际操作；fixture角色与真实用户数据分开。GUI夹具原媒体路由不可播放，不作媒体成片证据。用户另确认真实12文件复制完成，root沿已安装版本精确revision copy-complete API接收12条，分析持续进行，不伪造ready；当前进展从实时API读取。

EDIT-CHAIN-01（2026-10-11 用户真实失败对话）：root纵向负责同一聊天在任务/素材导航后仍能读取当前工具与可信选中对象，查候选→换镜头/交换镜头提案→确认应用→回读版本，以及给选中镜头对应素材添加用户明确标签。先以现有EditIntent/Proposal/Operation和素材Repository为唯一操作入口，不绕过权限、文件修订、授权或资源准入，不把历史成功工具称为示例。退役旧素材引用覆盖明确任务镜头、历史工具描述决定当前能力及自然替换独有已渲染限制；模型每轮获得当前工具与对象事实。Pi动态allowlist每次更新，不能冒认Pi会话缓存工具集为根因。上述API、宿主和任务界面worker源码lease已全部释放；root接管相关源码、共享DTO、工具、权威与集成/原生验收。最后原生可见差额由root局部修改VideoEditor.module.css：长文件名不能撑开对比列；等宽可收缩列和完整换行沿DESIGN规则，不扩大重设计。worker不访问私人库/配置，不跑服务/模型/构建或下级。任务快照只从单一可信clip绑定asset+segment双修订；生成的人物来源不可猜或降级整片。run_task_turn新可选material_selection使用服务端snake_case快照，宿主转换为现有camelCase工具选择。preview_edit可传只读目标候选中选择的replacement_asset_id/replacement_segment_id，服务端重新核自动选材资格/时间槽/当前版本；无候选明确阻断。交换使用ReorderTimelineItems同一版本原子提交，复用既有重排联动规则，人物联动明确说明；不能将一次移动冒充非相邻交换。正向验收隔离同一会话切页、1↔2及非相邻交换、候选替换、明确标签写入和回读；负向验收旧版本/跨成员/变更文件不能执行，历史缺失工具不能污染当前能力。原生安装沿既有授权，源码与安装包结果分别记录。前面的Memory/持续请求/素材检索主线保持授权，不恢复旧批量标签lease。

root持有docs/产品领域架构DESIGN、desktop、全局样式/打包、用户配置与真实数据；子任务按主控消息获得独占源码lease，无子代理、无构建/安装/真实模型/用户数据改动。共享文件同一时间只一个writer，所有差额须先报告。

请求验证覆盖数字/中文数量、关系缺失、时长作用范围、“今天发送”的真实素材范围、已明确不重复追问，以及窗口幂等/事务/权限。100/10000用于总目标及有界展开压力验收，不把样例值当分支或承诺全部成片。标签核观察与人工/建议来源、重分析保留、实际搜索/排序及普通可见文案。按开发安排先完成新目标一句话两分钟的闭环，再验有限真实批量、人物和其他制作模式；统一Web/API集成与隔离真实API/可见GUI，最后同候选原生MiniMax指令、任务/标签及媒体回读。实际成片/120秒/新配音按实际资源逐项验证，阻断不冒称PASS，也不停止独立工作。程序安装沿既有授权，只清理核对过的旧程序及重复包。证据目录：/Volumes/Dujiangyan-VM/AI视频日记/test-runs/desktop-v32-01a1235a/evidence/creation-request-tags-20261011。

## 2026-10-11 EDIT-CHAIN 本轮交付收口

最终已安装候选9257a74cdefdaef087bc46798d6008c8f4b6997753dac72e9e4e6cd57c8772df。root已完成源真实模型有限请求/整步编辑控制、原生实际选材替换提案、最终包重启后恢复同一提案和竖屏完整预览；没有应用该原生提案或新整片渲染。03acf仅中间模型候选，新包仅追加Logo直接读取修正，运行后与DMG内应用签名校验通过。现有18项、完整事件外loop、120秒从零/中文英文字幕最终输出仍未验收，不能宣称全部完成。

startup仅获精确edit-chain-20261011证据目录验收说明.md与atomic-edit-audit顶部时点说明的写lease；不写源码/共享权威/私人数据。该文档包完成后RELEASE。旧批量/标签HOLD不恢复，后续按已有AG主线冻结下一纵向接口。程序清理沿既有授权，已核5旧app+2旧DMG删除约2.41GB，用户库/素材/输出未删；新app与新DMG保留。

startup两份精确验收证据文档写lease已RELEASE；root已审阅并核相对链接、18行及候选边界。最终说明沿已有优先级：先闭合一句话两分钟/中文英文字幕基础成片，缺失原子编辑按独立纵向包补齐，不能要求所有18项作为基础成片前置。

## 2026-10-11 继续主线：PREVIEW-GRANT / TWO-MINUTE

本轮目标是闭合一句话两分钟基础成片；用户已要求继续，已有模型调用和本机安装授权沿用。共享权威绑定架构§3.12/§3.13、领域§5.1a/§7、产品§9.3及计划AG0–AG4。当前owner/HEAD不变，保护既有dirty。按当前工程路由，root持有产品解释、共享文档、派工、审核、集成及真实配置/模型/原生验收，不与实施者同写产品文件。

PREVIEW-GRANT可见线程01a12a6c-8c7d-7943-9763-4cb0c44e758a为唯一预览执行链及本轮main消费者实施者，独占apps/api/app/main.py、task_audio.py、audio_timeline.py、montage.py、creation_execution.py及本包新增目标测试。其他FFmpeg helper须先提交直接消费者证据并获得文件交接。退役TaskAudio.publish/图片预览绕过DeviceRuntime直接produce以及媒体子进程无grant的活跃调用；复用已有JobRun/TransformRun/Artifact与单设备协调者，不新增并行任务/队列事实源，不放宽预算/CPU/授权。先提出持久输入、actor、资源类、重放及等待返回语义的具体接口，root确认后再实现；没有许可不得启动媒体，嵌套复用同一上下文，不持父许可再等待子许可。失败/取消/过期输入不得写成功或覆盖新版本，产物提交后按既有安全恢复，不重跑。仅用独立库及最小相关测试，不跑真实模型/服务/构建/安装/用户DB/配置，不push、merge、删除或派下级。正向核授权预览真实媒体和持久回读；负向核预算/身份/版本/拒绝/取消/重启与无原始subprocess旁路。整体同候选原生与成片由root收口。ChangeCrop/ChangeClipSpeed保留原选择镜头开头最多5秒静音对比，不得改整片渲染却沿用旧文案；按真实clip/window及canvas声明资源，视频独立核产物，不要求静音片有音轨。main预检固定候选只从现有automatic_material_segments的usable asset/segment集合检索及保留，不能将建议用途当当前有效用途，也不放宽Repository确认或改用户角色。

TWO-MINUTE可见线程01a12a6c-9159-70f3-aec3-10e7919a207b仅只读核一条新120秒目标从真实素材范围/档案/预检到Plan、文案、声音、montage、render、quality和双语输出的当前消费者，给最小可执行路径及精确缺口，不研究全库。无源码/共享权威/私人DB/配置/运行服务/模型写权；唯一可写精确证据edit-chain-20261011/two-minute-flow-findings.md，不把旧26.5秒产物或fixture当120秒验收。两包不写同文件；shared语义由root裁定，旧批量/标签HOLD不恢复。

### 本轮接口冻结与文件交接

root已接受PREVIEW-GRANT的具体方案：类型化持久任务媒体操作退役produce闭包；沿creation_job的JobRun+TaskEvent保存actor、操作、基础版本、候选快照及逻辑输入，获准后才认领。CPU音频使用独立真实资源声明/预算，不能虚构宽高或借TTS预算；画面沿既有CPU渲染预算。共用现有DeviceRuntime/grant上下文及可证明的子进程释放，重建按持久输入恢复，已提交产物只核验不重渲染。202回执统一job_id/status/result_kind/result_url、operation/target_refs/input_digest/created_at及权限过滤的device_request_id/waiting_reason；只有成功结果回读才给可播放URL/既有预览对象。result_url只指当前任务固定媒体job回读入口；列表按当前actor有界读取，默认20最大40、cursor为真实job_id。重开只对task/base/选择匹配项显示明确恢复入口，点击后回读同JobRun，不重POST或自动覆盖新草稿；失败/取消/输入失效分别显式返回。相同actor/操作/base/固定输入的重放复用原JobRun，不能保留随机UUID作为幂等事实。草稿提案仍可立即返回，但媒体尚待安排不能冒称真实预览完成。单个JobRun的执行/结果/取消沿现有状态，不加竞争成片状态机。

PREVIEW-GRANT新增独占schemas.py、host_resource_probes.py、member_permits.py、subtitle_alignment.py、silence_edits.py、audio_waveform.py及对应本包目标测试；其余原范围不变，但montage.py已交接给TWO-MINUTE，在后者RELEASE前不写。CPU音频预算具体字段先按实际操作提出，不改变用户配置，不用默认宽松值或现有别类预算放行。

TWO-MINUTE只读发现包已RELEASE，转TASK-DURATION局部实现：独占montage.py及新增test_task_montage_duration_policy.py，修CreationTask故事板采用当前Plan时长而非档案90秒默认；不改工作区档案/资源预算，不重复音频或填无关镜头凑长。旧非CreationTask消费者保留本来时长来源，CreationTask入口显式传任务上限；接口差额发给root，由PREVIEW-GRANT唯一main.py writer接入。实际音频超过Plan仍拒绝，字幕不延长短音频，实际片长以真实音频为准；最小回归覆盖Plan120/Profile90及更短/超长/无效分段时钟。只用独立库，不运行媒体模型、真实服务/配置或下级。

PREVIEW-UI五文件候选已RELEASE，root接集成；同owner仅续lib/api.ts及目标测试，修实际等待原因中文文案与失败/取消终态不作为恢复候选。已交付消费者覆盖AudioTimelineEditor.tsx、VideoEditor.tsx、CreationTaskWorkspace.tsx、lib/api.ts、lib/types.ts和本包目标Web测试，消费上述202/成功回读及持久JobRun：等待不装作错误或成功，不在未完成时播放URL/展示应用确认；回读须保留任务/base/选择上下文，过期结果不覆盖新编辑。采用现有作业进度/取消和普通等待文案，不另建全局队列/永久Inspector，不重设计。源码或隔离组件通过不是原生交付；同候选原生与媒体由root验收。三包互斥，schema/API差额仅PREVIEW-GRANT变更，root批准后通知UI；其他旧HOLD不恢复。

TASK-DURATION的montage.py/目标测试已RELEASE；main消费者及旧fixture由PREVIEW-GRANT接，不能把纯函数9项通过当完整API120秒通过。该可见线程完成PREVIEW-AGENT，diary_tools.ts、assistant_pi.py及新增test_assistant_media_preview_tools.py/test_assistant_media_preview_host.py已RELEASE给root集成。该线程后续只读预览链交叉审阅，并仅编写已指定的两份资源标定QA文件；不写产品源码或运行模型/服务。修已复现静态契约差额：preview_edit现在直接解引用preview.proposal，而新API媒体202没有proposal。工具收到真实JobRun回执须返回明确待资源/处理中及details.kind=task_media_job，宿主归一化“已安排，预览未完成”，保留单轮一次写限制、不盲重放。TaskAssistant响应由backend schema writer添加可选media_job，UI在持久job成功回读时恢复真实预览；数据型提案仍保持原行为。模型不传任务/原句/身份/job路径；持久结果权限和base由API重核。仅目标隔离工具/宿主检查，不真实服务/模型、配置、DB或下级。

PREVIEW-UI线程id为01a12a7e-52a9-7241-a0af-db4ef06b91ff；五种result_kind及get/cancel/list输入恢复语义由backend冻结，不能靠猜测202对象构造成功。当前root原生保存的一条新120秒目标已持久化。用户已明确确认“属于我，允许接入并保持私有”；旧本机资料按auth-workspace-boundary§9的显式本机接入规则执行，不能借匿名诊断放宽正常制作条件。

LOCAL-ADOPTION可见线程01a12ab8-212b-77e0-b261-11ff92e05fc8已RELEASE原包；该线程后续独占新增test_creation_preflight_material_qualification.py及production.py仅search_material_segments签名/segment循环，核预检→窗口确认同一素材资格。该内部方法新增eligible_segment_refs:set[tuple[str,str]]|None，评分/limit前按现有usable集合过滤；普通搜索不传时不变，不另建资格规则/automatic布尔开关。main唯一writer消费同一集合及asset范围，query/fallback上限8；检索包已交付，无续写权。测试含同一asset超过8个不合格建议片段不挤掉合格低排序项。root已review并真实应用，原v1/12素材业务内容回查相同，保持私有。检索C1已RELEASE；新源码仅SOURCE-GROUNDING由该线程独占topics.py、script provider相邻测试及新增test_creation_task_script_grounding.py，main Input/内容失败分类/提示版本已在序列化writer RELEASE后正式交接。production仅fail_script_generation签名与route_snapshot availability分支获同包唯一lease，provider_failed默认True；内容/核查拒绝保留provider_attempted真实True但不将可达连接改failed，run仍failed且不保存新稿。其他production及main消费者不动。真实新草稿发现无据备份/性能/口型声明，保持未确认；沿既有provider生成一次与有限核查一次，事实只来自固定source.text，目标/提纲/风格不是既成事实，无自动修稿或重试，不增provider/表/第二事实源。模型检查仍不是人工或确定性语义证明；权限/语言/时长/JSON既有门禁保持，内容拒绝不误记连接坏。精确范围为root已核的12个本机素材与voice_cc/v1的原声音来源。只接入local的现有无上传者记录至当前已验证的本机owner成员，逐项CAS核原payload/visibility/文件修订，不认领已归他人或跨workspace对象。素材原ID、字段/片段分析/标签/用途/排除状态保持，登记同文件逻辑来源，归属改动存私有接入回执；声音保留v1，原WAV不复制，登记稳定私有来源并新建v2，不改人物/默认声音/旧任务/已有授权。使用Repository内原事务与最小持久回执、显式维护CLI，禁止新增普通制作/读取的自动认领回退。已交付原包范围为production.py、database_migrations.py、新增维护CLI和目标测试，现交接root；不改main/schema/消费者、不接触真实DB/配置、不跑真实模型/服务/构建/安装/下级。外部manifest只保存CAS摘要与必要标识/修订，原payload快照只保存在原库私有receipt。root审阅隔离原子性、跨成员/跨空间、变更输入拒绝与幂等测试后，才用精确manifest在真实库应用；root不复制私人数据库、不删除源或失败样本。预览三个工作包继续，不受本接入规则影响。

资源标定QA写lease仅由01a12a6c-9159-70f3-aec3-10e7919a207b持有：edit-chain-20261011/calibrate_creation_resources.py与calibration-interface.json；不写产品代码或执行媒体/模型/服务/用户配置。root接受后沿真实全机锁及真实探针执行明确临时停止包络，基线20秒与目标120秒按实测判定，不改ledger、不私建并行全机锁、不得自动放宽内存/CPU。

PROBE-EXIT C1由01a12a6c-8c7d-7943-9763-4cb0c44e758a仅独占HostProcessProbe._state出生metadata异常分支及目标tests：same boot下metadata失败时，只有exact登记PGID的signal0明确ESRCH可释放；组仍存在/EPERM/其他未知、已知PID重用及holder规则保持。此包不改资源预算、模型、主API或用户数据；r4观测已记录退出phase、整组必须另核，最终root沿原包络实测。

PROBE-EXIT 原源码 lease 已 RELEASE；r5 仍出现 unknown，当前只读取证，不续写兜底。QA 线程01a12a6c-9159-70f3-aec3-10e7919a207b独占本轮证据目录 calibrate_creation_resources.py 的首次 unknown 诊断及 calibration-interface.json：当次记录真实 _state、birth 异常与 exact PGID signal0，不以事后 PID 消失证明整组释放，不放宽 QA 停止线，不运行真实用户模型/服务/库。PROBE 线程只读复核可能根因；root持有下一次真实校准。

QA-REAP C1：r6 原始查询已证实 birth 匹配而 signal0 为 EPERM；root 三个自建同 UID 子进程组复现 macOS zombie 未 wait 时 EPERM、wait 后 ESRCH。产品进程与资源准入规则保持，EPERM不算released。QA线程仅修非侵入观测的取消时机：当次原 unknown 只有明确同boot/出生/UID/PGID匹配且状态为zombie的登记组，可记录“等待父进程回收”，按原100ms采样继续，最多1秒；其他unknown或取证不全立即取消，1秒仍未回收也取消。所有资源停止线继续采样且不变，不额外等待/重试媒体，不改变许可或产品probe verdict，完成仍须全部真实ESRCH和正常终态。自建僵尸正向、存活进程/出生不匹配/权限未知/超时负向后RELEASE，再由root按原20秒包络实测。

QA-SNAPSHOT C1：20秒r7已通过，120秒首测在原unknown后的ps取证前已回收，因空metadata而被取消。QA线程仅续两份既有QA文件：取证不足时最多刷新一次同request/generation的真实permit快照及原children_released调用；只有新快照的原probe给出确定released/active，或既有同generation free证明，才按新快照继续监测。保留旧unknown记录，不把它改成released；新结果仍unknown/异常、出生不匹配或许可换代立即停止。无sleep/媒体重试/放宽停止线，不等待额外硬件调用，不改变产品probe、资源、数据或作业规则。测试真实回收后的确定新状态、仍未知及PID/许可变更拒绝，RELEASE后root复测120秒。

QA-REFUSAL C1：120-r2观察器无错误，设备事件明确先deferred/resource_pressure，后QA尾部清理取消。QA线程只续两份既有QA文件，记录清理前job/request状态及事件；用执行线程的非侵入原调用trace捕获仅当前QA grant的真实HostResourceRefusal.decision（原因、数值checks、采样区间）。不替换probe、不加许可/调用/重试，不读写真实用户库或采集token/正文；产品、预算、停止线及等待规则不变。PROBE线程仅只读隔离合成库和执行链建议。root据实际拒绝决定下一测，不能将尾部清理标成最初根因。

用户新增未来 CLI 化及 Dock 图标设计审查。开发安排新增 AG10，接口沿同一业务动作/提案/版本/作业，不新增第二执行器或修改当前主线。UI 线程01a12a7e-52a9-7241-a0af-db4ef06b91ff仅只读批准手机标识、现桌面资产与指定 Dock 截图，提出小尺寸辨识改进；没有生产图标、手机源码或品牌权威写 lease。当前品牌规则仍按 DESIGN.md §2.5。

SOURCE-GROUNDING C1：root真实五段核查finish_reason=length且completion_tokens=2400；旧稿仍未确认。01a12ab8-212b-77e0-b261-11ff92e05fc8仅续topics.py的_check_script prompt/max_tokens及新grounding目标测试，核全部事实但失败段可报告最重要一项短issue；有界输出上限4800，与已有生成上限相同，真实截断/无效仍拒绝且不重试、不保存。其他源码lease不恢复；root负责下一真实MiniMax回合。

SOURCE-DIAGNOSTIC C1：上包RELEASE后root38项通过，直连真实核查正确拒绝无据性能承诺，但安装73f0保存API仍script_review_invalid。该线程只续topics.py核查拒绝分支/标准logger及目标测试：固定原因枚举、finish_reason白名单、正文/思考字段长度、tool_calls数、有界usage整数和段序；无任何正文、密钥、headers、route、原ID或模型输入，不变HTTP文案/shape、调用次数、参数或validator判定。root据下一实际日志再决定，不继续猜测加预算。

SOURCE-QUOTE C1：a255真实核查完整stop但第三段issue_quote失败，原稿未变。01a12ab8-212b-77e0-b261-11ff92e05fc8独占topics.py仅_check_script/check_draft/旧quote helper退役及test_creation_task_script_grounding.py；相邻fixture限test_creation_task_topic_provider.py的review/script正向handler、test_assistant_script.py的draft持久回合、test_reply_script_transfer.py的HTTP plan-confirm回放。仅内部核查协议改为原文片段选择：当前候选各字段/总标题和已授权source.text逐字≤200连续切片，顺序完整可还原，输入不重复全文；ID绑定index/field或source alias、区间与文本digest，局部resolve回原文。supports只回candidate_quote_id/source_quote_id；issues只回kind/candidate_quote_id/reference_ids/短说明。严格核当前段/总标题和当前允许来源，旧文本quote协议退役，无模糊匹配/双协议/额外AI或自动重试。原先验校验、全部事实/约束覆盖、数量/布尔一致、4800与1检查、公开HTTP/保存/CAS/权限/日志隐私不变。check_draft先验仍执行，但核实际待保存candidate以免trim后的不同原文绑定。无其他源码/数据/模型/构建权限；root负责实际bad稿内容拒绝与good稿待确认验证。AG2分析文字集成仍仅已回报只读建议，未获此包写lease。

QUOTE-TRANSPORT C1：3122真实完整stop报告仍content_json拒绝；MiniMax官方Provider-Verifier §09明确M3不支持json_object，§13/14提供工具结构路径。同线程续同窄文件/fixtures，仅review请求用单一无副作用submit_script_review工具和现结构JSONschema、tool_choice=required，移除review response_format；结果仅接finish_reason=tool_calls与恰好一个同名function的JSON arguments。该工具仅作为结果容器，不执行/回填/增agent工具或循环；原文本content报告路径退役，无双协议/猜JSON/fence修补/重试。保持原片段resolve、全部事实/约束/引用/数量、单HTTP检查4800、公开API/CAS/权限/安全日志/provider/model。生成和选题请求不在此lease扩大，root新候选实际验收，失败不伪造保存。
