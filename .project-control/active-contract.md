# 0.11 main 主控与当前执行合同

## 本地主入口

用户于 2026-10-07 明确要求建立 `main` 目录，后续以它为准，并清理重复、闲置的本地目录。

- 唯一主开发与集成入口：`/Volumes/Dujiangyan-VM/AI视频日记/repositories/main`。
- 分支：`main`；上游：GitHub `weitongxucc-dev/ai-video-diary-agent-studio` 的 `origin/main`。
- 建立时核实的产品代码基线：`584ccc362c0e03a7647d655f22014f30e75022b1`。后续最新提交仍以实时 Git/GitHub 为准，本合同不维护完成度副本。
- 本目录有独立 `.git`，不依赖 `original` 的 Git 目录，也不沿用旧目录的私有配置、用户数据库或依赖软链接。
- 产品、领域、架构和安全规则按 [文档路由](../docs/README.md)读取；未合入分支的代码与文档不自动成为本目录的产品权威。

## 当前执行边界

用户于2026-10-08提供并批准独立v3增量，以[DESIGN v5.2](../DESIGN.md)及产品/领域编辑规则为准。已完成的三栏、浮卡、搜索和业务接线保留；不重启整轮重构。63已审交付retain；当前新施工仅下方UI64视觉/时间组织与A65直接草稿编辑，主控独占共享权威、审核、机械集成、PR/Linear。重模型资源暂停继续有效，不做探针、模型、配音/口型、FFmpeg或收费调用。

UI与A当前64/65均已语义REBASE_ACK v3权威与共同867c6bf，原分支保留。旧v5.1普通编辑确认语义不再有效；共享语义再变化只重绑受影响条款，不重启整轮。

用户最新指定先做小范围可信试用，交付渠道以 [产品需求 §1.3](../docs/product-requirements.md#13-交付设备平台与待定选项) 为准；2026-10-07 用户进一步批准按审查缺口开始执行，并提供 `/Users/Zhuanz/Desktop/sk.docx` 中的 MiniMax Key。原只读审查结束；当前写权、操作权及主控真实文字请求的有限例外只来自本节设计任务、下表当前任务及其具名条款，历史候选说明与旧任务不恢复。

用户于 2026-10-07 在本会话指定 main 线程为产品主控，提供两条可见 Codex 执行线程，并明确要求“现在你来安排工作吧”。产品施工仅限下表具名范围；原主控和其他旧派工仍停工，历史授权不自动扩展到本批。

- 唯一主控：main 会话 `670d30a5-7031-4814-9b07-0c1ad334fb7f:a034d4bb-1ff5-4d1b-9aae-d599b402934b`；当前终端 `term_2a22cf6b-53e4-402c-969f-5365d6a15444`。负责共享文档、Linear、候选审核、Git 集成与 PR，不编写产品代码。
- 本批变更标识：`MAIN-RESUME-20261007-01`。产品代码基线 `584ccc362c0e03a7647d655f22014f30e75022b1`；主入口本地说明基线 `b4dd823a5ca05c21c0dc8429eab8c9ee37e11425`。新合同由两条线程读取本文件并语义 `REBASE_ACK`；ACK 满足且无未解决范围冲突后可直接开始已授权动作，无需第二次 START。
- 事实源：当前 main/候选 Git、Linear team WIL 的项目「曜核 0.11｜桌面端完整闭环」、实际测试与具名真实页面；浏览器表面按实际工具和人工操作分别记录。历史[暂停交接](pause-handoff-20261007.md)仅提供保留成果的位置。
- 不整体 merge/rebase 旧混合开发分支，不覆盖脏树。执行线程不得 push、建/合 PR、写 Linear 或共享权威；本地仅提交自己已验证的具名差额。主控可准备独立 PR，main 产品代码只走 PR，不自动合并。
- 除下方主控具名MiniMax Key及用户本人素材定向只读例外，不访问真实用户库、其他真实密钥/登录钥匙串，不激活云身份/凭据，不下载或运行未具名本地模型、不发布/签名/Release、不迁移真实数据、不删除保留工作树/stash。已结案Dispatch不复活；旧任务操作权已结束，新操作仅下方64/65具名范围。主控继续PR96/97及人物许可独立PR的CI/审核与相关Linear维护；用户资源条件恢复后以新具名合同/Dispatch接续完整制作，不从旧记录启动。定时 automation 与 scheduled 生产始终 OFF；无真实 Supabase 迁移/设备注册/测试账号操作权。
- 不派内部子智能体，不新增执行窗口；一文件一写者。彼此可通信，但不得转授写权或替主控改变范围。

## 本批线程、所有权与验收

| 线程 | 工作树与身份 | 当前任务 | 写入边界 |
| --- | --- | --- | --- |
| UI / main-ui-linear | `/Volumes/Dujiangyan-VM/AI视频日记/repositories/main-ui-linear`；`579066f7-9655-4ee7-9473-53d2e2071f2b:b8a4392e-dd5f-4cac-8267-1671e8118151`；`term_0ff98007-d2b6-442e-b45e-7d1b0b4d9811` | 64：v3视觉与时间组织 | 仅64具名Web视觉消费者；无服务/模型权 |
| A / manta | `/Users/Zhuanz/orca/workspaces/main/manta`；`68244901-fc27-4dbd-9381-cc158d3ba9dc:942aa671-d4b4-48cc-925c-cd9e94a383d7`；`term_79a48d73-cb24-4736-a0c2-4e995483ea88` | 65：普通编辑直接持久草稿 | 仅65具名编辑Web/API消费者；无服务/模型权 |
| B / pipefish | `/Users/Zhuanz/orca/workspaces/main/pipefish`；`57ae4a00-4977-4585-8947-5223d803c1c8:83f3ee5f-3d33-49b6-9e03-2cf08374cc2c`；`term_2ee18b71-c3c8-4e56-b551-5f5a765c4298` | 66：v3组合候选必要验收 | 新隔离输入准备；准确候选绑定前无服务/GUI权 |

## 当前v3增量：64/65

用户最新v3交接为本次授权依据。共同产品代码971f77a（PR98最终三类CI成功），叠加本次唯一权威提交后由主控提供准确新基线。旧普通参数“预览影响→确认→应用”规则失效；两线程先读原包增量交接、组件规范，再读DESIGN §3–5、产品§2.3/§9及领域§6–7，语义REBASE_ACK后直接完成所辖定向调整。不改已确认三栏、品牌或领域枚举，不复制示例数据，不整合旧混合分支。分支/脏树保留；新候选从主控准确对象建分支。

UI64负责可独立交付的可读文字/清楚交互态、七类真实状态形状、首页分组、列表显示入口、任务实际比例与共用真实时间尺。owned：globals.css、modern-overrides.css、AppShell.tsx、HomeWorkspace.tsx及CSS、VideoEditor.tsx及CSS、MaterialLibraryWorkspace.tsx、KnowledgeWorkspace.tsx及它们直接CSS/既有测试；其他文件先报告真实必要差额。保留360/320/240四角浮卡底座、父状态与全部已有操作；VideoEditor内VideoClipQuickEditor及普通比例控件直接提交既有onPreview回调（A65将其解释为应用草稿），不新增确认链。不写CreationTaskWorkspace.tsx、精细编辑组件、lib、API或共享权威。真实已有能力缺失明确说明，不新增虚假分类/波形/状态或列表设置。

A65为“普通字段或结构命令→同一CreationTask版本化草稿→撤销/重做→产物依赖有效性”的纵向负责人。owned：CreationTaskWorkspace.tsx、ScriptSectionEditor.tsx、AudioTimelineEditor.tsx、GraphicOverlayEditor.tsx、既有精确编辑/字幕表单直接消费者、lib/api.ts/types.ts中确有必要的直接编辑调用、main.py及production.py中当前编辑/撤销/重做/有效性直接路径、对应既有测试。精确字段在选择/完成输入/结束拖动时直接持久化，不提交未完成值；复用原命令校验、版本和依赖，不新建draft事实源或扫描/兜底/兼容层。自然语言提案一键“应用到草稿”，普通编辑不自动生成；扣费/删除才保留确认，未知费用不扣费，系统授权及阶段动作不套额外产品确认。其他生成/权限/队列/模型/发布不扩权；API确认命名可保留为内部提交，不以改名重建接口。涉及实际媒体预览/生成须与草稿提交分开，模型资源暂停不解除。不得把聊天状态或未持久化React快照称为草稿完成。

65.1直接接口绑定：VideoClipQuickEditor实在UI-owned VideoEditor.tsx，UI64同时改其字段/结构动作提交时机，A65不跨文件写入。真实三轨共尺需A-owned AudioTimelineTracks配合：允许既有组件新增sharedTimeScale可选属性，CreationTaskWorkspace内嵌传true；嵌入时音频跟随父0–真实duration的同一lane和标签宽，不使用独立zoom/滚动尺度，其他音频工作区既有独立缩放保留。UI64负责共同CSS及VideoEditor视频/字幕坐标；A65负责音轨直接消费者，双方仅重绑此差额。不得隐藏整条音轨/旧操作或只靠CSS假称同步。

65.2必要关联变速差额：主控核原TimelineAudioClip duration固定源区间、linked_timeline.retime_clip强制先物化新音频ref，确与普通草稿不生成冲突。允许A65追加audio_timeline.py/linked_timeline.py/task_audio.py实际音频模型/重定时/校验与mix消费者，以及其既有直接测试；仅让草稿保存原授权source_ref/源区间与速度/时间变换，明确试听/渲染时沿既有许可消费者执行。source、timeline和包络时钟一致，不造待生成音频引用、不静默降成仅画面、不新建第二队列/事实源或数据库迁移。lib types直接契约归A。仍无真实媒体执行权；代码路径/最小合法合成数据证明与真实音质输出未验分开。领域§6.3已绑定新草稿表示，读此条语义ACK即可继续。

本单退役目标：普通参数确认链、贴边详情、颜色独自表意及等宽时间段；已有合法底座和未影响成果复用。主控为整体v3验收/退役负责人，64/65各报所辖不存在证据，不凭层级分项关闭整体任务。实施期只做最小已有消费者与真实问题复现，禁止为CSS像素写镜像测试，不重复旧全量/GUI。证据64/65分别写/tmp/main-acceptance-20261008/ui64/及draft65/，短报告列commit、净差额、必要结果和真实剩余。无服务/GUI/个人浏览器、真实库/Key、模型、主机探针、网络安装或收费权；完成后交付准确源码并收回操作权。组合后B只做受影响页面、实际比例、编辑持久化/撤销/失效与费用/删除边界的必要真实可见验收，另绑定准确候选，不重跑全部UI。

### B66：v3同候选必要可见验收

B为零产品写者；仅新/tmp/main-acceptance-20261008/ui66/。先按64/65和DESIGN v5.2及领域版本规则语义ACK，定向复用ui62合法合成DB/owner token和ui45源色卡（只读旧根，mode=ro/uri=True备份，0600合成凭据），保留旧数据。准备无Key/Pi离线/scheduled OFF、完整HOME/XDG/DB/config/library/output/tmp/models隔离和必要代表性任务；不读真实库/媒体/密钥、安装依赖或造费用报价。此准备与编码独立，仅输入准备，不提前运行服务/GUI/模型或使用旧候选验新行为。

主控交付准确v3组合commit后，B核源码与数据、4579/3179空闲，自有嵌入page viewport/键盘可用，精确候选语义ACK即条件START：一次API4579/Web3179，自首服务20分钟绝对租约，不续租/重启，不操作个人浏览器。所有GUI命令明确page，真实控件/鼠标/键盘/viewport/PNG，eval只读几何/焦点，不注入DOM/CSS/事件/响应；普通浏览器动作由Agent自行完成，不请用户拖窗/按键。

最小旅程：1024x700浅色及1280x720代表画面，真实task比例16:9/1:1/9:16等比且预览与三轨首屏；已知不等长片段按真实区间同尺，非等宽装饰。通过真实普通字段改一次时间/比例或字幕→无确认/不生成→新持久版本→刷新回读→撤销/重做→受影响成片/检查有效性；一条离线真实提案点击应用到草稿无第二确认，不冒称外部Pi通过。删除开启真实确认只点取消、无删除；费用未知只查可得说明，不构造金额/发费用请求。代表页首页分组/文字/可见焦点和集合显示配置只影响列表、父圆角卡/选项逐层保留输入。至少一个真实可持久普通修改与版本边界完整，不以fixture/隐藏状态代替UI操作。

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
