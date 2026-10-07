# 0.11 桌面闭环执行合同

## 唯一主控移交（2026-10-04，覆盖下文所有旧主控/当前安排身份）

用户明确指定cmux workspace9/pane19/surface19接替。唯一新主控身份：workspace_id=5CA420B8-1E90-4F4E-BD28-8A6FD2A9A636，pane_id=D7B149E2-D26B-4E71-9D2F-7CA08FA67100，surface_id=D71D58DD-7EDB-4DBA-8E0E-FFD3CB01B9F5。原Codex01a1019f-5dfa-7f22-9bb8-cbb2a49a9140仅完成本次快照/远程推送/交接通知，最终AUTHORITY_CHANGED后停止所有派工/审核/写入；新主控收到准确交接SHA后开始，禁止双主控。

详见[主控交接](controller-handoff-20261004.md)：包含全部188项Linear状态快照、准确远程候选与待审缺陷、线程/路径归属、下一步。下文旧主控身份/进度仅为历史；实时Git/Linear仍是事实源。用户本次要求同步GitHub/main，仅纳入准确已审核组合0c1c555（代码树与d1f8fc1相同，CI37199772798 PASS）及交接文档；119/159/110本轮WIP仅独立分支，不合main，不等同验收完成。本次同步不恢复未来自动合PR授权，不发布。

交接检查点：119=8a1b46a审核退回、159=4037787未完成/未验、110=ac318d2未接main/目标事实源缺/未验；2/3/6/4/7已停写无服务，待9号恢复。1/5故障通道停用。已批准119→108、159真实成员许可、110知识grant、此前112/116/104/126及98/125局部恢复仍有效，不再次询问；其他HOLD保持。main共享写权恢复顺序3→6→4，一文件一写者；119稳定真实契约后4接108成片检查，不转首启检查。不存在agent_bridge.py，不新增桥接文件扩大范围。原bd25用户dirty文档/控制文件另存codex/controller-state-20261004恢复快照，不整体merge，原树未reset/clean。

沿完整issue实现→集中一次最小相关验证，失败才提前复现，复用通过证据；无依据防御编程/重复全量/假成功禁止。真实库/配置/密钥/未授权模型/发布仍禁；GUI旧策略不重试/绕过，197跳过留UNVERIFIED；automation与scheduled生产OFF。主控合同/Linear/Git，不产品代码、不内部代理，可替换旧上下文执行会话，不改模型配置。


## 当前施工与安排（2026-10-04）

唯一主控为01a1019f-5dfa-7f22-9bb8-cbb2a49a9140；root负责合同、Linear、候选审核和远程PR，不写产品代码。用户已明确“批准修正后的局部恢复”：WIL-112/116/110/104/126下述具名范围解除HOLD，本文后续旧暂停/待批字样不再约束这五项已批准范围；用户本轮明确批准首页98/125具名局部恢复：整机匿名汇总/本人可读明细、趋势/真实存储状态及客户端首页消费，行业字段继续待定义；下文旧98/125待批准文字对此具名范围失效，其他HOLD不变。准确main基线33cc48502b92d3646d603a8093b0f17ee3617851；每单独立codex/分支与worktree，实时进度认Linear/Git，不复活5fcb8ac。

## 实现与验证节奏（用户本轮明确）

> 以一个 issue 的完整目标为推进单位：先完成已确认的全部实现，再集中做一次最小相关测试和必要真实验收，审核关闭后接下一单。不要每完成一个小点就测试、汇报或等待指令；只有定位真实失败时才提前定向复现，未受改动影响的通过证据直接复用。

共享文件稳定提交后可阶段移交，不将交接当再次批准节点；同一issue由主负责人集中汇总候选与验收，多个独立issue可并行推进。未验不Done，达到判据立即收口，不做无依据防御性编程、无意义测试或重复本地全量。

当前唯一文件归属（本节覆盖下文所有旧阶段归属）：
用户2026-10-04明确“开始吧，按照你的建议执行”，已批准119→108恢复、真实成员交互任务许可及知识跨workspace grant。旧待具名授权/只读停写文字对此三条具名实施线失效；其他HOLD不变。Linear免费issue上限拒绝新增，复用119/159/110，保留旧交付及验收证据，不因额度停工。
- 2号workspace2/surface14：WIL-119，独立/Volumes/Dujiangyan-VM/AI视频日记/repositories/wil119-quality-detection，codex/wil119-quality-detection，准确d1f8fc16424527938dfe0d125f37820e9df5c172起点。唯一持apps/api/app/quality_checks.py及直属检测专项写权。旧eb7b5a7仅定向参考，不整包恢复。完整真实黑帧/截断/削波/时间戳问题结果与未知/失败语义，禁止时长差冒充截断/起始差冒充全程同步；完整实现后集中一次≤3秒隔离正负及边界验证，交冻结契约给4。
- 3号workspace3/surface11：WIL-159实施＋后续五人验收，独立/Volumes/Dujiangyan-VM/AI视频日记/repositories/wil159-member-permits，codex/wil159-member-permits，准确d1f8fc1起点。唯一初段持main.py、device_runtime.py、device_scheduler.py、creation_execution.py、直属成员许可模块/专项；实现真实LocalMember→许可注册/交互装配/心跳/本人队列/取消释放。持久可信成员/空间，恢复重试复核仍有效，不保存原令牌、不造VerifiedSession；原local-owner硬编码在本交互范围内替换为真实等效或更严身份核验，已包含在本次授权，不重复等待。scheduled生产配置与触发保持OFF。整体一次必要真实会话/许可检查，旧4项证据复用。main/schema稳定提交即停写交6，不等整卡关闭。
- 6号workspace6/surface10：WIL-110知识grant差额，独立/Volumes/Dujiangyan-VM/AI视频日记/repositories/wil110-knowledge-grants，codex/wil110-knowledge-grants，d1f8fc1基底＋root一行领域授权澄清提交。唯一持source_repository.py、sources.py、knowledge_files.py、agent_bridge.py的直属知识grant消费者、直属grant模块/迁移/专项、schemas.py、apps/web/src/app/knowledge/KnowledgeWorkspace.tsx及同名CSS/直属专项、Web api.ts/types.ts直接授予消费者。main.py初段3号，3稳定停写后root承接并交6；之后交4。仅知识条目→同设备指定workspace读/引用＋revoke和真实UI；沿auth§9，保持原空间/所有者/版本，管理员不默认读私有，撤销停新访问、旧任务保留追溯并核后续授权。assistant/main直属消费者必要差额报root定路径，不新造身份/知识系统。完整实现后集中一次真实隔离grant/read/reference/revoke及负向，旧110/112/104证据复用。
- 4号workspace4/surface12：WIL-108已批准，119稳定真实结果契约后承接整单触发/结果/检查状态/定位/旧版本失效；不做空结果或mock前台。目前119依赖未就绪，产品写权未开始；具体独立树在稳定阶段由root建立。后端production.py与直属质量模块届时归4，main/schema等按3→6→4阶段取得，不与前两线程并写。
- 7号workspace7/surface17：168重连/备份只读缺口已接受，其他工作未授权，保持停写，原缺盘PASS不复跑；不自动识别同卷接管，不造备份。
- 1号workspace1/surface16、5号workspace5/surface13：先前执行通道故障停用，全部停写；不向旧surface派工。
- root唯一合同/共享权威/Linear/Git集成/push/PR写者，不写产品代码、不内部代理、不自动合PR。119 HOLD仅解除上述具名检测恢复，其他118/121/97/96等HOLD保留。三个独立单issue分支；同一逻辑文件一写者，稳定提交即移交，不等整卡关闭，不重复确认或ACK报告。每个issue实现完整后一次最小相关测试与必要真实验收；失败才提前定向复现，未验不Done。
已通过110 PR84、98 PR85、125 PR86及126/104/116的既有CI/专项证据复用，98 Done，125/199/197等真实GUI缺口保留；197用户跳过不催问。GUI策略拒绝不重试/换通道，不启动真实库/配置/密钥/模型/发布/自动合PR。定时跟进与scheduled生产仍OFF。

本轮首页接口对齐：counts.key统一visible_assets/analysis_ready/analysis_pending/knowledge_sources/voices/tasks/works；趋势按实际caller_visible范围，不能标成匿名整机；设备运行总量仅计算真实运行/排队，不能含已完成/失败全部接收记录，制作作业统计复用既有job_runs实际表；device_queue冻结counts/records_total/running/queued，后三项不可读为null且counts={}，前端不把未知填0；可进入queue过滤真实非完成任务。成员切换清旧私有结果且旧请求不得写回，重试取当前身份与days，真实task_id跳既有任务路由；保留?new=1创作入口，不另造身份事件体系。以上完整差额实现后集中一次必要相关检查。

首页已批准实施范围（2026-10-04用户选择批准局部恢复）：单一GET /api/production/home?days=7|30，统计scope/source/as_of、真实日趋势、整机允许披露的匿名数量/状态、本人可读队列/待办/成果与真实存储来源。复用既有事实源与110读取规则，不直接透传全pending_files私有路径/名称，不编造industry或task_id、不以可见素材字节和冒充整机用量。3与4以固定接口并行；后端共享文件按6→3阶段移交，api/types明确归4，不等110整卡GUI关闭。原3/5永久停用和surface13不可派工文字对此具名只读阶段失效，其他旧surface禁令仍有效。

交付与依赖：WIL-160在2b9ae3b停写，PR77三CI通过、GUI未验，不重做；PR78/79/80均是单issue草稿，当前差额/基底依实际Git审核，不自动合并。112基底组合已交160/104，补review-base元数据前后代码树相同；116基于112稳定分支，126基于116稳定分支，后续按准确已提交阶段同步，不能混单宣称main独立。112不是116整卡硬前置，112/116不是110全卡施工前置；104历史存储103/134已在main，完整权限验收依110；126无需等权限链。

只针对真实问题和明确不变量施工，最小隔离直接验证，复用有效证据，不跑重复/无关全量或为数量测试。禁止真实用户库、真实配置/Keychain/密钥、模型下载/未授权推理/收费、平台登录/发布、签名/Release及自动合PR；其他HOLD和scheduled OFF不变。GUI仅桌面Chrome，既有策略拒绝不重试、不换通道，GUI项UNVERIFIED，197用户跳过字幕不再催问。一个逻辑文件一个写者，阶段稳定SHA即停写移交，不因未验GUI或整卡状态机械闲置，不新增无必要窗口、ACK或报告。

## 执行线程交接当前槽（2026-10-04）

用户因上下文过长明确要求换六个可见执行会话，旧窗口完成交接后关闭。唯一主控不变：`01a1019f-5dfa-7f22-9bb8-cbb2a49a9140`。本槽和“当前执行者”表定义派工身份；下文2026-10-03的旧surface、候选阶段和派单只作证据追溯，不恢复旧窗口写权。
- 六个当前接替surface：1→16、2→14、3→11、4→12、6→10、7→17；完整UUID及最小交接见[执行者交接](executor-handoff.md)。原5号知识链转7号，workspace5未派工surface13也停写。不得向旧surface1/2/3/4/5/7/8或13派新任务。
- 旧执行者已逐一最终回复停写、无自有运行服务，可关闭旧pane/tab；保留源码、DB、证据，不关闭整个workspace误关新surface。root只读核100/101/127/128/131/132/199七个独立树均干净；此前128“脏树”摘要已过时。bd25原用户改动仍保留，不reset/clean。
- 所有开发必须绑定Linear实际实施issue，明确一个负责人、必要路径、用户结果和退出判据。只针对真实问题/契约修改，不做无依据防御性编程、重复或无关全量测试，复用有效证据，达标即收口。没有就绪施工范围不制造线程忙碌。
- 131用途UI、16使用记录、165配置/密钥以及103/134/135/105已获本轮批准并交付；交接阶段待确认派单已失效。其他明确HOLD保持，后续仅认顶部具名当前施工范围。Chrome策略阻断仍保留，不重试或换通道。
- 定时跟进已按用户要求停用，交接不恢复automation。Linear和Git实时状态是事实源，交接文件不作为完成度副本。
- 主控已实际发送/提交/读取六个新surface回复，全部确认已读各自实时Linear并接班；12张相关卡已同步当前责任窗口，原状态与判据保留。执行者交接完成，无新增产品施工或测试。

## 主控交接当前槽（2026-10-03）

用户本轮明确指定新主控聊天 `01a1019f-5dfa-7f22-9bb8-cbb2a49a9140`（标题“设为新的主控线程”）。旧主控 `01a0ecd6-dc91-7500-8f03-45e2dbde4820` 自本次登记起停止新增派工、产品/合同/Linear/PR写入；只完成具名交接消息、六窗口同步和既有automation转交。唯一派工/审核/共享合同写者已转新主控，不能同时两人指挥。

- 实时远程main：4209bf19c2faec8e4c49f5cb67cd9d09210f494f；PR69/68/63已分别于北京时间20:48/20:50/20:52外部合入，主控没有合并。原三PR的CI证据按范围复用，尚无新main整体CI或GUI通过证据。bd25开发树脏改动及未跟踪handoff/backlog/work均保护，不reset/clean，不从旧original复制规则或开展集成。
- WIL132：准确5d3eb2e456cde57135448ca04eec1e69d96e14bc，PR69 MERGED、三CI PASS、实际Linear附件已核；普通桌面Chrome类型/组合/刷新/分页完整确认范围PASS，Linear Done。父16现Backlog/受阻，详情使用记录缺实施，同一集成候选整体旅程未完；PR69已合main，Done不表示整版交付。隔离根/tmp/yaohe-wil132-acceptance-5d3eb2e的原ready.json与logs保存证据，77159/77160/77213正常SIGTERM退出，4497/3097无监听已实时核，不恢复或重复验收。
- WIL131：准确3d9240c2b94460127c55ee398a85b4a0d255363d，PR68 MERGED、三CI PASS；自动Done已恢复In Review/受阻。后端用途→标签重算修复通过，但普通片段面板无usage_role编辑控件，仅回传旧值，GUI无法执行b_roll→a_roll。最小新UI实施范围已向用户提出，尚无批准，不能自行创建/编码。新workspace6/pane8/surface8已接替退出旧会话，其/tmp/yaohe-wil131-acceptance-3d9240c只有准确源码/启动准备，未初始化fixture/启动服务，现已停写。production.py生成用途:a_roll系统标签且查询包含system_tags，不因缺usage_role原字段谓词另扩搜索后端。
- WIL197：已合main的只读历史查看分项已核；旧seq2真实成片实际取得64,879字节MP4、hash与原生保存一致，人工seq3无输出为当前，7类关联表digest不变。字幕普通链接GET200但Chrome下载回执无证据，用户明确“可以，你跳过吧”，记UNVERIFIED、不再等用户/重复重试，仍In Review。/tmp/yaohe-011-acceptance-1d04fa5原ready.json/logs保存证据，12203/12204/12235已释放、4495/3095空闲。194/195/196已Done，不复测。
- WIL127：PR63 MERGED、原候选b5faa946af81005708f7adb5b714220fb0843a1f；原CI PASS，真实录音/ASR仍未验，主控已将自动Done恢复In Review，不能造假录音或调用未授权模型。旧混合PR47/45不是交付入口，不自动处理冲突或合并。
- 六个执行者均无产品写权并已停写；space20/6的main4209bf1四卡共用素材准备及短HTTP就绪检查已接受，自有服务已释放。space14/4的WIL167准备冻结等待Chrome恢复。space18/1的WIL138、space15/5的WIL135方案保留，未决规则/模型/HOLD不据方案开工。space19/3已交WIL16使用记录定位：现只有计数/日期，明细表无读出口；domain§3.3要求任务/版本/用途回查，新增独立实施范围待批准，本父卡已转Backlog/受阻，不把计数当完整记录或造rendered计划冒充真实成片。
- 四卡共用环境已接受：/tmp/yaohe-materials-acceptance-4209bf1/ready.json，准确源码五处与main逐字相同；29媒体hash、源库不变、新库hash及资产路径已核。短启动后的首页/library/API及Web代理素材GET均200无重定向、无自定身份头，local/A/B/C正确。98536/442/443已正常SIGTERM退出，4499/3099空闲由主控核；停止DBhash6544e32489646f629fe0c6a0c08f32ba3b8a03b5cbbecb11f4e02743c110ee58，启动初始化改变库文件但29素材payload未变，绑定/任务/作业/版本/产物均空。原始告警no such table: episodes保留，不修产品、不补legacy表或重跑就绪。6已冻结停写，GUI仍UNVERIFIED，Chrome恢复后另具名放槽。
- 后续GUI只用用户指定桌面Google Chrome，唯一操作者由新主控具名指定；不使用Orca内置浏览器。Chrome普通HTTP页此前已实测可操作，当前可用性见下述WIL167工具阻塞；chrome://downloads内部协议被策略拒绝，不换表面/通道绕过。用户库、密钥、付费模型、发布系统与HOLD禁令不变。
- 当前GUI工具阻塞：同一桌面Chrome扩展id3可绑定，读取tabs仍返回“Unable to load browser request-header policy”。这是策略加载失败，未取得页面准入，不冒称明确站点拒绝或连接恢复；本轮停止，不反复调用、不换通道绕过。所有GUI仍UNVERIFIED，服务暂不放行，独立隔离准备可完成。chrome://downloads的既有明确拒绝不解除。
- WIL167夹具导入例外：主控核准确候选creation_plans只有id/workspace_id/payload/updated_at；源隔离plan额外visibility=shared是旧库字段。允许space14导入该plan时明确按上述四列投影，在ready记录被省略字段和值，源库不变；creation_tasks/material_assets的真实visibility及workspace_id全部保留，不扩身份/可见性。沿已回滚空目标库完成同一事务，不重建库或重复复制9份媒体；其他不匹配直接报具体差额，不猜测兼容或改产品schema。
- WIL167隔离准备已完成并接受：主控核任务、四个版本、八条字幕轨、声音/成片及9份媒体hash；两条material_assets.file_path及payload.path已映射至本根media下同hash文件，路径存在且一致。其他字段/快照/逻辑引用及旧库不变，ready已记录映射。space14已停写，冻结待工具恢复，不启动服务；准备通过不等于GUI通过。

## 当前授权与主控

ACTIVE。2026-10-03 用户明确指定并授权移交：唯一主控为 Codex 聊天 `01a1019f-5dfa-7f22-9bb8-cbb2a49a9140`（设为新的主控线程）。原主控 `01a0ecd6-dc91-7500-8f03-45e2dbde4820` 已停止新增派工/审核/集成和共享状态写入，仅完成交接同步；新主控先核当前合同、实时Git/Linear/六窗口，再在既有确认范围内持续推进。主控变更不扩大实施、HOLD或合并权限。

- 2026-09-29历史交接回执（不是当前主控授权）：`/Users/Zhuanz/Documents/Codex/2026-09-24/volumes-dujiangyan-vm-ai-repositories-bd25/external-agents/controller-handoff-20260929-response.md`。回执作交接证据，不定义产品规则。
- 主控负责规划、共享文档、派工、审核、集成与开发分支推送，不编写产品代码。不派内部子智能体；继续使用既有可见 cmux 执行者，不更改模型配置。
- 集成目录 `/Volumes/Dujiangyan-VM/AI视频日记/repositories/bd25`，分支 `codex/0.11-development`。项目保存入口 `/Users/Zhuanz/Documents/AI视频日记` 指向另一旧 checkout，不在那里开展本轮集成。基线逐包给出，不用旧 SHA 回退工作树。
- 用户最新要求覆盖旧自动合并授权：未经用户明确要求不得合并PR。新产品/测试施工先绑定已确认的实施issue、独立分支与单一范围PR；没有对应实施issue时先提出标题、描述和验收，等确认再编码。已存在的混合开发分支保留，不新增产品差额或以改标题冒称完成迁移；验收/只读诊断继续，历史候选迁移另行审核。真实平台发送/发布、新增付费调用、签名、公证、真实用户库迁移、覆盖已安装版本仍不在授权内。测试只用隔离数据，不打开 `~/Movies/AI视频日记/agent.sqlite3`。
- 保护未跟踪的 `.project-control/0.11-bug-backlog.md`、`.project-control/handoff.md`、`work/integration-011-tmp/` 和各工作树的用户改动；不得 reset、清理或覆盖。
- 本文件只保存当前约束和文件归属。已结束批次、测试计数及旧主控登记从活动入口退役，历史由 Git、Linear 和原始交付报告追溯，不重新激活旧 Codex 线程。

## 执行效率（2026-10-01 用户明确要求）

- 不做无依据的防御性编程：只修真实问题、已定义业务不变量和实际边界；不顺带增加猜测式兜底、兼容层或泛化抽象。
- 每项验证必须回答具体问题。优先复用未受改动影响的已有证据；不为数量、覆盖率或报告形式新增测试，不重复跑已通过且未变化的检查。
- 主控先审diff与执行者证据，只有集成确实改变相关行为、证据不足或发现新失败时才追加定向验证。不默认再由第二人完整重测；真实入口必要验收保留，未验不得报通过。
- 派单一次给清结果、文件归属、交付判据和实际阻断。低风险修正直接推进，不反复要求已确认的ACK、不为例行进展增建文档或issue。
- 达到既定交付判据立即收口；非阻断优化不能无限延长审核。以版本关键依赖和可用功能排优先级，不以线程占满、测试数或关卡数量充当进度。

## 权威和保留的暂停

长期规则从 [文档路由](../docs/README.md) 逐步读取；完整需求追踪见 [开发台账](0.11-development-plan.md)。0.11 桌面与 0.12 手机分开，追踪总表不是本轮全部必交付。

1. **0.11 Linear 整体重构已批准**：用户最新明确要求按0.11需求重构难懂、过粗的issue，以便多Agent协同。允许主控实际改写标题和描述、重排父子/关联/阻塞、补必要工作包及视图；优先复用现有ID，先保存原始内容及需求索引，保留历史交付和验收记录。旧“仅试点/先审迁移方案”的限制已被本次具体授权替代。本次只重构任务管理，不自动授权产品施工、解除既有HOLD、平台动作或真实数据迁移。Linear仅主控写入。
2. **成员可见性 HOLD**：WIL-110 / WIL-112 / WIL-116 及相关施工保持暂停，不因主控切换或命名整理恢复。候选 `5fcb8ac` 保留、不合入；`sources.py`、`source_repository.py`、`production.py` 的可见性范围和相关迁移/测试不转派；用户已批准的 WIL-102 素材登记、WIL-136 文案保存复用、WIL-100素材说明保存及本轮明确批准的WIL-199标签统计/准确分页筛选、WIL-128名称对象/素材关联局部例外及本轮用户明确批准的WIL131 update_segment、WIL132 list_assets_page局部例外按当前表执行。用户已决定新建 private / 存量 shared，旧派单中新建 shared 的文字失效；本次Linear描述已纠正。恢复仍须完成相关权威和具体候选同步、执行者重新绑定，并满足原明确HOLD条件；不重复询问已经确定的默认值。
   用户本轮明确批准WIL101自动候选判据局部例外，仅新增机器自动选片判据及直接消费者；人工选片、可见性、数据迁移及其他保留候选继续HOLD。domain§2.4已据批准范围明确自动选片含定时任务，保留用户明确人工选择和已有时间线，不扩大其他状态限制。
3. 本地多成员沿用户指定的本地 SQLite 方案核验；既有 Supabase 路径不删除、不自动激活。当前身份绕过面和读写授权缺口不得表述为安全隔离已完成；也不得借交接自行重设计身份架构。安全稳定规则仍见 [认证边界](../docs/auth-workspace-boundary.md)。
4. scheduled 生产运行仍关闭。WIL-92 是只读施工计划，不授予 main.py 写权。启动互斥阻断不等于准入门，OwnerRegistration、CPU预算、真实重试授权和端到端释放须分别核实。不得因接口/测试存在宣称 scheduled 已可用。
5. 真实发布不排期、不执行。发布授权和草稿的本地 mock、真实浏览器本地页、真实平台登录、真实草稿、真实发送分别报告，不能互相替代。平台集合的既有选择不构成账号操作授权。
6. 用户要求测试尽量最短、最小、串行、立即释放资源；不为更稳重复批量跑模型、不并发加载大模型。本轮交叉审核不调用外部模型、不下载模型。

## 仍保留的候选与边界

- WIL118：a59fe1f，wil96/original；model_runtime.py、model_installer.py、runtime_health.py、visual_semantics.py及原包测试保持HOLD，不恢复写入或集成。
- WIL119：旧eb7b5a7继续隔离，不整体复活；用户本轮已批准2号在独立wil119-quality-detection具名恢复检测修复及直属专项，见顶部当前归属。
- WIL121/WIL97：4302197，wil97/original；AudioTimelineEditor.tsx及原包关联范围保持HOLD。
- WIL110/112/116：5fcb8ac，agent-recall-memory/original，按上文成员可见性HOLD执行。仅用户已批准的WIL102素材登记、WIL136文案、WIL100素材说明及本轮WIL199标签统计/筛选、WIL128对象关联局部例外按当前表执行；本轮131 update_segment与132 list_assets_page局部例外亦已获用户批准；其他issue不得改动保留文件。可见性语义及候选仍HOLD。
- 旧候选报告位于external-agents/review-20260929/；只按具体问题读取，旧workspace编号不是当前执行者身份。
- 真实可见验收沿DESIGN §5.10 workspace-dual-v4；模型配置、mock、结构分析和成功保存记录不等于真实制作或产品验收。保留现有exact端点，不自行退役外部消费者。
- 只释放本次明确归属的进程，旧/tmp/wil122-verify、/tmp/wil90-demo及历史证据不删除。GUI及重型验证串行，轻量独立准备可并行。

## 当前批次：验收收口与文案复用实施（2026-10-03）

148授权代表旅程已接受Done，不重复已通过范围。146旧7f4f30f候选曾在排序后混音SIGSEGV11；WIL188新候选f583185已通过普通页面的排序前后出片、同源音量和撤销，旧失败保留为历史证据。真实人物口型、生产包与main整体验收仍未通过；不能把候选分项通过当版本交付。

指定CI36990086067：Frontend与Desktop syntax通过；Backend有4个主机负载准入拒绝、15个test_subtitle_time_sync夹具未找到错误。根因日志已由主控读取；旧CI失败原因不可追溯，不套用新结论。主机负载是否误测是尚未证实的解释；不放宽产品资源门禁。用户已明确回复“三个都同意”。三项已创建：WIL-188人物排序出片与音量、WIL-189字幕共享测试数据、WIL-190容量测试输入。分别独立分支和单一范围PR，主控负责push/创建PR/验证Linear实际附件，不授自动合并。新增实现采用主控派单给出的准确工作树，旧混合分支不继续写产品。

147只读前置报告已交：停顿裁切、纯素材画面速度已有路径；关联变速改变声音节奏且音乐/音效规则待定；重配的锁、派生引用和旧人物产物存在静态风险，未当成实际运行失败，也不自动转施工。本轮先准备纯素材画面变速的独立实际验收，不依赖未定规则/外部模型。

| 执行者（唯一当前surface） | Linear任务与下一结果 | 当前写权及阶段退出 |
| --- | --- | --- |
| workspace6 / pane10 / surface10 | 当前main33cc485的WIL126接收/确认/补扫/重启依赖核验。 | 本次只读；GUI及模型冻结，不重包、不复测、不写产品。131已外部合PR70，未验分项保留。 |
| workspace3 / pane11 / surface11 | 上下文失效，WIL16已停写交1号；无新增差额。 | 已明确确认完全干净/无服务，撤销所有16写权，不再搜索/编码/ACK循环；本窗口暂不派新施工。 |
| workspace2 / pane14 / surface14 | 当前main33cc485的WIL110读取权限及98真实依赖核验。 | 本次只读；165已外部合PR72，GUI/包内配置未验；137/142缺声音/许可，不探测或推理。 |
| workspace4 / pane12 / surface12 | 当前main33cc485的WIL104固定知识引用消费依赖核验。 | 本次只读；105 PR76三CI通过、GUI未验；不改135/105候选或重新测试。 |
| workspace1 / pane16 / surface16 | WIL160真实持久化作业进度与耗时；准确main33cc485新独立树，详见顶部派单。 | 本单直接API/schema/client/types/CreationTaskWorkspace唯一写者；production.py及调度/成员HOLD只读；138仍未决。 |
| workspace7 / pane17 / surface17 | 当前main33cc485的WIL112/116真实存储默认、版本及先后依赖核验。 | 本次只读；103/134已外部合PR73/74，未验分项保留；旧候选5fcb8ac不复活，不读真实库或裸import。 |
| 旧surface1/2/3/4/5/7/8及13 | 已交接停写退出；workspace5退出本轮。 | 不派新单、不恢复写权，不关闭整个workspace误关新surface。 |
| 主控 | 核当前Linear实际范围、文件阶段释放、diff/候选及单一PR实际关联；Chrome恢复时四卡→167串行验收。 | 用户“可以，开始安排工作吧”已批准本轮131UI/16回查/165隔离，具体实现无需再次确认。只主控更新共享合同/Linear、push/PR；不编写产品、不内部代理、不自动合并。父16额度例外承接当前施工，部分通过不整卡Done。 |

本批冻结接口（技术施工契约；业务语义见当前domain-model§3.3/3.4）：
- 2026-10-03 16:28用户两项均批准：131/132按当前表立即施工。旧只读准备、131整文件HOLD及132待确认字样不再约束这两个已批准局部；可见性/HOLD其余部分保持。PRD§7/domain§3.1/3.3/DESIGN§5.2语义不变，原宽卡需求已保存追溯评论并转父15/16继续保留，不丢弃。root核6真实释放后才通知2取得production.py写权。
- 本轮空闲线程只读准备：1/space18核main597054a的WIL138已有发音词典/逐句重配入口与最小真实差额，不运行模型/测试或编码；3/space19核同main的WIL101评价/禁用及131人工事实读取链，仅提出production.py局部例外/独立实施边界，不解除HOLD；4/space14核WIL165既有文字服务配置的密钥持久化边界，提出独立系统安全存储范围，不读真实密钥值、不改系统设置或编码。只交短反馈，不增建报告/issue/派工层，不互相审核或重复旧验收。2已交199停写待命，6已收口167继续待命，7原会话锁不动。root据证据提出需要用户确认的新范围，未确认不创建或实施。
- 2026-10-03本轮续推：2已提交199为6d49738并明确释放共享Web文件，root核工作树干净。5即刻获准在128树接入MaterialLibraryWorkspace.tsx、lib/api.ts、lib/types.ts及直接相关既有测试；复用现有详情与已写组件，不等待199合main，不复制199差额，不扩批次/可见性。本单仍由5唯一写入和提交，root最后承接已合主干历史。代码/定向证据交付后停止，真实GUI未验不可Done。禁止再次全量API测试；1353项运行是多余范围，今后只新增专项/直接消费者必要检查。
- WIL127当前候选aa2c855105478f4646760aad6fe99ba70cd7006e，PR63基线已恢复main597054a，差额仍仅127八文件；此前6582f73与当前整树逐字相同，仅承接外部squash的主干历史。三项CI均通过，127真实录音/ASR/GUI仍未验；4只做165只读准备，不重复127产品测试。
- 2026-10-03 13:31阶段释放：3已交199后端并停止写production.py/main.py/schemas.py，5获准在其128独立树写上述文件仅BusinessObject/素材关联局部及一个test_material_business_objects.py，按冻结接口保存/回读和工作区边界做最小验证；仍不写共享Web父页/client/types，等待2释放。199后端5项专项通过，原相关检查74通过，仅作其API证据，GUI未验。3不得继续写入；2只做199前端/组合审核和提交，不修改Python。root负责异常差额协调。
- 当前main已实时变为597054ae20cba0c1b8524f1ea216f560899d001a，PR64/WIL198已由外部合入、CI全通过；root没有执行PR合并。新树仍保护当前差额，不为这项测试修复重跑产品验收；最终交付由root承接其历史并核PR单一范围。
- WIL199后端响应定义实际位于apps/api/app/schemas.py；root补充仅MaterialTagStatResponse.asset_count字段的局部写权给3（与既定响应契约一致），不扩其他schema。WIL128后续响应/请求定义也只在已冻结对象接口的对应schema局部，须由root在阶段放行时一并指定。
- WIL199继续GET /api/production/materials/insights，tags每项新增asset_count（保留segment_count原语义）；GET /api/production/materials新增精确tag查询，COUNT及分页同谓词。前后端必须一致，不造并行统计源或旧片段数fallback。
- WIL128 BusinessObject最小{id,kind,name}，kind为product/dish/person，归属当前workspace。GET /api/production/business-objects -> {items,total_count}；POST同路由body {kind,name} -> 单条；GET /api/production/materials/{asset_id}/business-objects -> {items,total_count}；PUT同路由body {business_object_ids:[...]} -> {items,total_count}。既有workspace解析与局部Repository统一保存，多条关联显式选择；不复用PersonProfile。必要结构只在隔离SQLite创建/兼容检查，不打开或迁移真实库。
- 新组件的props可按上述已冻结语义组织为objects/selected IDs、创建/保存回调与pending/error；不另建客户端/Repository抽象。真实父页接入须等共享文件释放；迟到响应不误绑切换后的素材。
- 文件顺序是资源协调，不设199阻塞128产品关系；root接到释放即可让128下一独立阶段继续，不能因为等待共享父UI停掉已获准后端阶段。

当前重型/GUI顺序：1构建169准确main8bcc64f单arm64隔离目录包→主控核包身份与隔离启动→具名交可用验收者做同包启动/重开/媒体/原生保存。7原TUI因旧cwd失效，原窗口按相同会话恢复又遇到other-app占用锁，当前不承担派单；不删除会话锁、不终止其他app或更改权限/模型，6作为后备，须具名放槽。旧150/193/136/171服务和tab均已释放。139仍缺真实录音条件，150仍缺文件访问；后续尽量在同候选包验证，不注入假录音、不绕权限。各组只释放自己精确PID；下一槽须root具名通知，轻量只读可并行。

WIL136定向CI修正：PR57 Frontend既有知识页5项因新子面板使用useRouter而缺AppRouter测试上下文失败，root已核job110936332566日志。仅workspace1追加允许修改apps/web/src/app/knowledge/__tests__/page.test.tsx，为已增加的真实子面板提供正确router和文案库读取测试替身，保留原知识页断言；不在产品中try/catch隐藏错误，不添加兜底。只重测此文件与现有文案库专项，交新SHA后刷新136隔离候选，旧17cdee证据不称新SHA通过。

193当前3b81305和136当前b120132真实页面分项与CI已PASS，171准确db3a9a5剩余一次输出/播放已PASS。2026-10-03实时确认对应PR56/57/53均已合main8bcc64f；原候选证据按范围复用，6只核main组合差额，不重复整套GUI。新的包候选以本批冻结main为准，不把开发树660f519或旧包冒称当前main。

workspace4原WIL171重复准备已停止且未建新DB；原/tmp/wil171-ref-fix-db3a9a5部分准备保留。171新合法引用夹具已完成输出/播放，legal-fixture/evidence/release.json及4423/3023空闲已核；不得据旧派单重建/重跑。4现仅按表核132。

GitHub实时已确认PR49在2026-10-02 20:42合入e203893、PR48在20:43合入8065912；远程main8065912。主控随后仅同步这四个测试文件到开发660f519与188头8fbc3af并推送，产品源码及188专项测试与已验证候选完全相同，PR50相对开发基线仍只两文件，新CI37008771179的Backend/Frontend/Desktop syntax均通过；PR47仍开放作旧混合分支记录，不作为本轮交付入口；PR50已关闭，由main定向PR51替代。146真人口型与147剪停顿分别缺真实样本，不将测试音/纯色当通过；父项不自动Done。所有测试/runtime只用独立HOME/config/SQLite；不打开真实库。旧HOLD/付费/发布/签名/自动合并禁令不变；主控唯一合同/Linear及远程交付写入者。

## 已批准交付范围补充（2026-10-02）

用户明确同意：WIL-188纳入人物镜头/配音/中英字幕联动必要前置代码并与音量修复共同交付；新增实施WIL-191裁切真实前后预览，复用已通过WIL-148证据。本次仅C1合同范围调整，不改产品规则；两项独立分支/PR，无自动合并。两项main.py与test_montage_pipeline.py顺序写入，先2后4；裁切不挂硬产品阻塞。本单Done不自动关闭146/父29；缺真实口型样本照原记录。

## 派单发送必须闭环

主控向cmux发送文本后，另发送提交键，再读该窗口确认消息已提交并出现开始执行/工具调用/明确回复；仅有工具OK或输入框文字不算派单成功。Codex粘贴确认与提交可分两次Enter，只有观察到仍停在输入框时才补一次，不盲目重复提交；运行中明确排队的消息不重发。通常无需用户手动回车。权限/产品选择提示仍交用户处理，不以回车代替批准。

PR46已合入远程main，合并提交5dd69ed124d03bf08cecd6aeb66916551728e23b。指定候选7d834d3的Backend、Frontend、Desktop syntax全部通过，范围审核完成；已核GitHub状态MERGED、远程main指向该合并提交，main完整树等于已验证候选。GraphQL首次502及Merge already in progress是接口响应异常，标准REST合并成功；不再保留为当前阻断。开发分支已承接该main历史，现行合同登记单独保留，不覆盖工作树。146新施工现已接收在开发分支，未带入本次main；各线程现有产品基线和文件归属不变，不为无关历史同步重测或重新ACK。不打tag、不签名、不真实发布/迁移或付费。合main不等于0.11整体验收，149/153/123等剩余缺口照现有卡继续。保护用户脏树、旧证据及真实库，已通过部分不滚动扩大范围。

PR、候选及验收进度以GitHub、Linear和现行派工为准；上文旧CI、候选与合并记录只解释历史，不定义当前阻塞。PR53/54/56/57已实际合入，原“未合main”的阶段记录失效；主控仍无自动合并授权。171/182及其他父项按具体范围和交付证据判断，不自动等同0.11整体验收。

本轮只读核验132报告已收：101评分属真实局部依赖，131不阻塞所有现有读入口；音频片段定位可独立，仅提出新实施范围等待用户确认，不解除整体依赖或文件HOLD。160报告已收：Electron退出重开并入169同包验收；真实排队/scheduled保持OFF。首页阶段刻度冒充百分比仅提出新修复范围等待用户确认。新范围未确认前任何线程不得编码或编造编号。169唯一包构建PASS、运行UNVERIFIED；报告位于/Volumes/Dujiangyan-VM/AI视频日记/work/wil169-build-8bcc64f/REPORT.md。

169启动准备2026-10-03已审核：Electron33的--user-data-dir覆盖userData/sessionData/Crashpad；包缺app-update.yml故现有代码跳过联网检查；HOME不能覆盖macOS Foundation日志/缓存路径。root取消此前自加的系统日志/cache必须全部入新根的过严限制，保留真实用户数据隔离及禁止读取真实库；只放行6的具名受控启动和退出重开，不需要新issue或产品开关。报告/tmp/wil169-launch-8bcc64f/launch-readiness.md。

169本轮真实进展：准确包首次启动和隔离存储回读PASS，root经原生应用菜单Quit正常退出，6已核自有子进程退出和55365/3000释放，正在同参数重开。root批准6定向复用/tmp/wil193-ui-10bf/evidence/construction.json具名隔离测试样本及两段2秒视频/合法测试声音，复制到自己的新媒体目录，按现有制作对象构造一个明确标为验收fixture的任务（不模型、不冒称生成/本人声音成功）。唯一169 DB/fixture写者仍6；初始化报告与GUI操作证据分开，不以API初始化算GUI创建/模型通过。仅在本次测试根进行，先记录源/目标hash与实际登记方式，再由root在真实桌面包打开任务、生成新输出/保存/退出重开。真实声音质量及从零完整创作仍UNVERIFIED。
