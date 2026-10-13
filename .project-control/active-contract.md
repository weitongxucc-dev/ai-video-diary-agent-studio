# 活动合同：PI-CONT-120-130（2026-10-13）

## 用户结果、基线与权威

用户已明确授权：先修#120自动接续及#130中栏可见执行，再以真实视频完成连续修改/保存/恢复验收，通过后打包供本人试用；同步现有教学线程，及时保存提交GitHub，并解释全项目哪些已做、本轮做及未做。owner=desktop-v32，branch=codex/desktop-v32-20261010，产品基线c263a112c7e2433d1e45ff392b8c3d3d1cd9f452，实时origin/main=5968e9bf0471a7ff0ea088e87f84febc873de300。original为脏历史checkout，不写、不合并。既有PR#124保持Draft，不自动merge/发布。

S2开发遵循用户2026-10-13最新指令：root担任总控，不再调动内部子Agent写代码；编码、独立审核及实际验收交由侧栏可见Codex线程。root保留共享权威/合同、Git/Issue、集成验收和打包放行决策；具体QA服务及用户任务操作仅在明确交接后由唯一可见验收线程持有。稳定规则绑定架构§3.13、领域§6/7、产品§2.3/2.5/2.6及§14、DESIGN§2/7、开发流程§3.1；不创建新完成状态库、不全局放宽版本/权限/采用/资源门。不改模型供应商或安装依赖，不盲扩保护线/默认资源额度。实际原接口422“CPU音频预算未标定，未启动媒体”后，root核准只对已采用115.788秒单旁白工作量做有限CPU_AUDIO标定：保留原实测进程RSS256MiB/线程8/FFmpeg1线程/15秒/0.1秒监控及512MiB内存保护；仅显式声明真实输入6MiB、时长120.401秒、PCM输出32MiB和临时磁盘64MiB边界，先测正常整机许可下的原音轨试听，未实测不称已标定。宿主CPU91%/空闲内存1GiB及原render2GiB/512MiB门不放宽；若拒绝先读决定、不盲重跑。正常观察已经沿原CPU_RENDER成功，仅有限抽帧/媒体规格，OCR/语义/听感明确不可用，本轮不额外配置OCR预算。原配置不复制凭据/DB，实測通过后才能给同机试用采用对应本机profile。

## 独占分工与协议

### 当前执行归属：新总控唯一接管（2026-10-13）

用户最新明确要求旧主控01a12cf6-92b6-7793-965b-350a44278473退出，由新开线程01a13575-b39d-7230-acad-b68b09cb3c3d（曜核：新总控与真实交付）接管。新主控已HANDOFF_ACK并重读当前归属，三worker已HOLD/FINAL_RELEASE、原app已恢复。此治理事务commit/push与CONTROL_TRANSFER_COMPLETE发送后，新主控01a13575-b39d-7230-acad-b68b09cb3c3d唯一持有主控/共享权威/Git/Issue/服务租约/验收和打包决策权；旧主控01a12cf6-92b6-7793-965b-350a44278473停止派工及源码/共享权威/Git/Issue修改，只发送最后交接ACK。没有扩大产品、资源、数据或发布授权。

当前产品基线1a148dacd420975c4a5c2f764f52e602c23f39c2已push；主控归属提交47adb27e7dd5c69e10900868bb3f6ccc968b4028已核，工作树干净。所有内部Agent租约永久结束，三个可见worker此前所有租约撤销。新总控正常GET原API确认task29/gain0/ready、history29及running0/waiting3与交接回执一致；原app保持运行。下方历史记录不给写权。

### 当前实现：同请求资源回执（CONTROL-142-P1）

用户已要求继续；主控冻结本必要接口后直接实施，不等短preview预算方案，也不在阶段通过时交回。产品基线1a148da、治理前序ba41060；绑定架构§3.12/3.13、领域§6/7、产品§2.5/2.6及DESIGN§2/4.4/7。QA根仍为/Volumes/Dujiangyan-VM/AI视频日记/test-runs/pi-cont-120-130-20261013。CONTROL-142-DESIGN旧只读租约已结束；旧提案仅保留作证据，下述主控修正优先。所有新Owner先按本次治理SHA完成REBASE_ACK，符合后可直接开始所列实现/审核，不另等START；服务、真实媒体、原配置和用户数据操作继续HOLD。

#### 冻结接口与验收

- ResourceProbe.admit返回含verdict/declared/checks/reason/observed_interval_seconds的结构协议，HostResourceProbe返回同一次HostResourceDecision；不接受裸bool的生产兼容或再次采样。continue_unit/released语义不变。DeviceScheduler/build_device_runtime显式传入admission_recorder(conn,request,decision,checked_at)，本包TaskMediaJobs.record_admission在原调度事务验证并append原TaskEvent；写入失败整事务回滚、不发grant。其它明确不属TaskMedia的单元不制造本包回执。
- 事件键封存request/workspace/member/actor scope摘要/task/Job/input/unit/baseVersion/attempt/观察时last_grant_generation/phase/观察类别；初次未grant的last代是null，不能预测next或猜previous代。每代首次unknown及首次refused各一条，最多2条；同类重复采样/GET不刷新值、时点或追加。以此取代旧提案每代仅一条first_non_admitted，避免首次unknown吞掉后来真实拒绝。
- production.py只增加get_task_event可选connection用于同事务去重，以及latest_task_resource_admission(workspace,task,job,request_id,connection=None)的同Job/请求/事件类型有界历史查询（LIMIT 1）；不能扫描全部task events或取全局latest。回读后重新核原actor/input/unit/baseVersion及观察时身份。当前resource_busy取当前attempt/last代首refused，resource_unknown取首unknown；其它情况只回读准确同request历史，缺记录为null。grant代变化、重试、取消及失效不导致旧事件消失，也不复活旧等待。
- TaskMediaJobResponse增加resource_wait: ResourceAdmissionReceipt|null。冻结字段：observation_id/device_request_id/task_id/job_id/input_digest；base_version_id可null；attempt_number≥1、last_grant_generation可null；phase=admission；observation_kind=first_unknown|first_refused；checked_at为观察UTC时点；observed_interval_seconds为正有限秒或null；context=pending_request|history；admitted=null|false。checks最多5项，metric限定cpu_percent/available_memory_bytes/swap_used_bytes/disk_free_bytes/disk_io_bytes_per_second，unit限定percent/bytes/bytes_per_second且与指标对应，actual/minimum/maximum为非负有限数或null，passed为bool或null。summary≤480/repair_action≤160为固定普通中文，明确“首次检查”及其时点；不宣称实时或已获许可。磁盘只给安全聚合值，不暴露路径；缺失/不可靠值明确未知，不伪造0。
- main._task_media_job_response及其GET/list/async/cancel/retry、等待会话和终态直接会话消费者共享安全回读；各自原业务status/结果/采用/权限保持，不为复用helper把expired等历史状态改成成功。已完成来源无同request事实保持null。资源phase=admission不解释后来的运行故障，不从日志、其它Job或QA快照补值，也不改resources_json封存输入。
- Web只消费该typedDTO和服务端安全summary/action/时点；保留taskMediaJobLabel的真实当前状态，pending资源检查与当前等待分别表达，history放现有结果详情，不给取消/失效/成功覆盖旧拒绝。无回执仍用真实中文通用状态，不另建状态库、客户端理由猜测、轮询器或自动重试，不动草稿/原历史/CSS布局。
- 最小后端验证使用pytest隔离TMP：一次真实探针拒绝→同事务回执和等待/无grant媒体；unknown→refused产生2条、同类重复仍2条；写失败回滚；跨actor/Job/input不串；null初代/下一attempt/准入后历史/取消失效/重启回读；GET与等待/终态会话安全投影一致且不泄密。Web直接验证pending/history/unknown/无记录/取消/过期及草稿保持。测试/构建不替代后续同候选真实320/360等待/取消与新片成功链。

#### 独占文件与报告

- API编码01a13533-b38c-7430-a2de-cb419e2340d6（曜核：调色预览范围修复）：唯一写apps/api/app/{device_scheduler.py,device_runtime.py,host_resource_probes.py,task_audio.py,schemas.py,main.py,production.py}。直接测试owned：apps/api/tests/{test_host_resource_probes.py,test_device_scheduler.py,test_device_runtime.py,test_device_runtime_wiring.py,test_task_media_grants.py,test_task_media_api.py}。为同一admit返回契约仅准同步真实测试替身：tests/{test_narration_edit_execution.py,test_material_ingestion.py,test_profile_api_contract.py,test_platform_publish.py,test_material_probe.py,_media_preview_test_fixtures.py}；均在apps/api。新增消费者先报具体路径。不写Web/共享文档/profile/预算/Git/Issue，不standalone import main、不用原DB/env、不启服务或真实Job。P1先完成实施与直接最小检查、回报精确diff/未验/RELEASE；P2设计不阻断本包。
- Web编码01a135a1-7931-7d83-8b88-32b5ab01e3bb（曜核：资源等待原因界面实现）：唯一写apps/web/src/lib/{types.ts,api.ts}、components/AssistantConversationPanel.tsx以及现有components/__tests__/AssistantConversationPanel.test.tsx、lib/__tests__/task-media-api.test.ts；仅为真实tsc消费者的既有queuedJob补resource_wait:null，另准components/__tests__/ClipTimelineAudioPreview.test.tsx，不改其行为/断言。按design-md完整读DESIGN一次，复用原组件/标签/细节区域。仅目标测试、tsc和目标lint，不build/启服务/GUI，不写API/共享文档/草稿数据/Git/Issue；报告可见表达设计、精确diff/检查/未验/RELEASE。冻结字段不因后端文件正在写而自猜。
- 独立审核01a13533-cec4-70d2-aa5d-ab5bb6c6aefd：只读本包接口与完成后的实际diff/证据，唯一写QA根visible-review-142-p1.md/json，保留旧visible-review-1a证据。未RELEASE源码不作最终验收，可先查冻结规则及直接消费者；不重复充分检查、不改源码/共享文档/Git/Issue、不服务/GUI/用户数据。首unknown/后refused、同事务、generation历史、两会话业务状态及敏感字段为重点。
- QA01a13533-e91f-7482-acce-f5968e52fae5：前阶段只读趋势和标定计划已RELEASE，原app保持运行；等待主控给同候选服务/GUI/正常可撤销任务操作新租约。此前服务及真实任务租约不自恢复，不再次采样或启动媒体。主控可在其回报后给精确脚本准备范围；新资源profile和swap政策尚未核准，不猜数值或绕许可。

总控独占共享权威/合同、Git/Issue/PR、候选与真实服务/用户任务租约和验收放行。各文件一writer，代码RELEASE后总控审diff并集中相关集成验证；真实候选通过后才打本人包。四项主交付欠账及#142短preview完整标定保持，不把本包诊断回执完成当整个目标完成。

定点启动附件=/Volumes/Dujiangyan-VM/AI视频日记/test-runs/pi-cont-120-130-20261013/主控交接-20261013.md（本机临时附件，不是产品权威）。新主控必须核最后安全状态、HEAD/dirty与用户新动作。user已授权可见线程互相发送交接ACK/证据。当前主交付还未完成：原成功Job自动观察、连续两次真实新片、保存/redo/候选重启及新包欠账；#142短预览准入/同request安全原因是首要阻断，不能把code/test/scope声明/旧输出恢复当交付。#117唯一动态总览，既有DraftPR124不merge/发布。

### 上一阶段已提交实现范围（以下分工/租约均已结束，仅作复用依据）

- continuation Owner（pi_objects）：apps/api/app/main.py、assistant_pi.py、assistant_goal_continuation.py、agent_tools/diary_tools.ts及其直接test_assistant_goal_api.py/test_assistant_goal_continuation.py/test_assistant_pi.py/test_assistant_pi_failures.py/test_assistant_typed_tools.py。必要现有对话repo/DTO变更先向root报告具体路径，核准后独占。真实start/resume回执核验后立即持久既有locator/proof；普通制作Job与TaskMediaJob分别查询；模型中断不丢原作业来源，原成功结果可在未失效目标中继续观察。失败/停止/新指令/撤权/旧版本/unknown保持原语义。已完成的有限制作/观察链与无进展清楚区分，不凭模型、空steps或旧文件宣称任意要求全部完成。向Web发布实际可用的goal/event投影字段及可信错误契约，不发原载荷或隐藏推理。
- Web Owner（execution_ui）：AssistantConversationPanel.tsx、AssistantConversation.css、useAssistantConversation.ts、AssistantConversationProvider.tsx及直接ConversationPanel/ReadRaces测试；apps/web/src/lib/types.ts/api.ts只允许本轮会话event/goal字段与解析消费者。复用原目标、真实工具事件、提案/版本/作业/观察结果；展示当前目标、真实步骤、产物及等待/失败/恢复原因；读取/提案/采用/排队/运行/完成分开。不新建进度状态库、不改全局三栏、不抢未发送草稿、不显示原始工具载荷/路径/凭据/推理。旧失败历史保留但不充当已恢复后当前阻断。遵循design-md：DESIGN.md完整读一次。缺API字段报告，先用现有真实字段独立实现，不自造成功。
- coverage Owner（pi_capability_audit）：只读代码、Git及GitHub#103–#140已有正文/评论与开发计划覆盖；仅写本轮QA目录coverage-review.md/json。按用户结果归组，明确已有实现/已验证样本/未完整验收/本轮/后续/待决定/0.12；工单OPEN不等于零实现。root更新既有总工单#117为唯一动态总览入口，不新增永久完成度文档。不得改源码、共享文档、Issue或运行服务。

每文件一writer；子Agent不得下级派工、Git提交或服务/用户数据操作。共享接口先发具体增量，由root核定。报告changed files、最小测试、首个实际拒绝、未验证项及RELEASE。root审核diff后集中一次相关集成验证；单个子包完成不结束用户任务。

已核准 continuation Owner 窄增加 conversation_repository.py：现有result合并及运行中登记，模型失败/重启仍保留宿主核验的execution/proof/goal；无新表/queue。execution路由新增可选assistant_turn_id query不得视为可信授权，沿现owner/task/运行turn scope与版本/实际采用/原真人要求核验实际Job后关联，不准把裸UUID或query授给其他目标。有限链完成条件另报root审核，空steps、模型报告或旧产物均不足。

已核准同Owner的 schemas.py 仅AssistantStreamEvent可选failure字段，服务器固定reason/repair_action两键安全投影，沿现sanitizer和长度边界；SSE与历史相同，不接原载荷/物理路径/凭据/隐藏推理。completed_output_checkpoint只为同现有goal的宿主核验refs/measurement/unavailable投影，scope固定production_and_output_observation；无新status enum/独立完成事实，不能清除原未完成要求或凭它宣布全部原目标成功。

## 本轮已提交窄修记录（旧文件租约均已结束）

root核准 continuation Owner 独占 assistant_pi.py、conversation_repository.py 及原直接测试：read_creation_execution 必须进入真正工具消费set；纯读取以宿主真实状态说明查询，不采信模型“已提交恢复”，也不凭旧execution结果登记新waiting goal。只有与宿主持久真实提交proof相同Job的执行才登记；先真实提交再读取同Job仍保留write动作归属。不改权限、供应商、validator、重试或状态枚举。

root核准 Web Owner 独占 VideoEditor.tsx、VideoEditorMedia.module.css 及直接媒体测试：原21镜头缩略图各保留video解码器是本轮真实内存拒绝的可消除来源，改为同文件私有串行读取真实起点帧、低分辨率canvas显示并立即清源释放；错误、换素材、卸载和排队取消也释放。主播放器不变，无新持久cache/worker/依赖/预算。单测不能证明具体释放量，须同候选正常21镜头GUI及资源测量/真实完整渲染验证。专业剪辑台多轨/监听/变速/关键帧整体仍为#132–#135后续，必要缩略图故障纳入本轮。

root另核准 Web Owner 临时独占 creation_execution.py 与直接test_creation_execution.py，仅实际CPU render的CreationJobUnitExecutor.execute异常分支复用已有detail.reason/error抽取，避免新Job.failure_reason保存Python原字典字符串。无需新DTO/前端解析/旧历史改写；失败仍failed，许可释放，上游保持。

root核准 continuation Owner 必要渲染组合修复：独占 montage.py、host_resource_probes.py、member_permits.py 及各直接测试（包括tests/conftest.py及test_timed_subtitle_render_input.py的同一真实grant/预算合成consumer）；另核准 _media_preview_test_fixtures.py、test_task_clip_preview.py、test_creation_resource_contract.py、test_creation_audio_mix.py、test_creation_execution.py 仅新必需构造字段的直接消费者更新（最后文件前Owner已RELEASE）；直接配置消费者新增必需字段须具体报备。实际21输入进程峰值2.148GiB且运行重复要求整份2GiB追加预留已确证。仅多镜头hard-cut/no-transition改为逐镜头lossless H264静音中间片及同Job相对ffconcat；原fade/颜色/帧端点时钟、全局字幕/graphic、混音、最终验证保持，xfade仍原消费者。同grant及原199秒总deadline；中间片合计含音频/字幕/manifest不得越既有512MiB追加磁盘，final仍原99,638,206字节，不降质量/改变validator/为每镜头重借预算。CPU render启动仍原1GiB系统余量+2GiB新增峰值；运行保留1GiB余量且实际进程RSS不得越原2GiB，并保留CPU91%/swap/IO/身份/许可门。新增显式总进程线程profile，缺失fail-closed，不给任意默认；本机候选51线程仅源于已测实际21输入最大值，采样0.1秒沿现HostBudget，必须先有限实测再完整片验收。原.env不复制/不扩大RSS资源额；root唯一profile配置/真实数据/服务/Git owner。架构§3.12新增此有限CPU成片资源不变量；旧上下文合同须重读ACK。

root核准 Web Owner 必要调色预览范围修复：独占main.py及test_creation_audio_mix.py/test_task_media_grants.py/test_visual_effects.py。真实AdjustVisualEffects仅clip2却执行21镜头115.788秒，标签称最多5秒；root已正常取消自己的该测试MediaJob，原v28/sequence28无采用/修改，不能记为单片标定。普通短预览复用原clip_preview及silent/max5；资源事实按同真实clip_ids代表镜头封存。长淡出仅对应完整镜头窗口的只读图/音轨/字幕/图层投影，保持时钟及尾帧；事实与执行同一稳定投影、无随机漂移。深拷贝中处理窗口外锁，原目标锁/原对象/5秒validator/权限/资源保持。多目标代表预览、独立镜头未覆盖相邻转场时标签明确，不假称全图已检查。无新DTO/预算默认/业务状态库。真实e13部分执行每注册子进程已采峰值248.67MiB/6线程（不等于进程组/完整峰值），两次运行拒绝CPU94/91.6>91，根本预算不调整；独立工程#141及#128追踪。

## 验收与旧目标

技术：原start回执已核验→模型回合中断→原Job真实成功→持久来源保留→未失效目标从新事实继续检查；两次修改、提案实际采用、同版本/hash观察、stop/拒绝/新指令使旧目标失效、重启及unknown原请求查询；不重复制作。退役目标是依赖模型最终返回才记录制作来源及误用媒体Job查询制作Job的消费者，不改正常历史/旧数据；同候选正向与窄负向验证。

可见：正常模型和GUI在原栏宽显示目标/步骤/当前状态/具体错误/恢复及检查范围；重启、切页、离开最新回复和编辑下一草稿保持；没有假百分比或排队完成勾。

真实视频：沿已有用户确认文案/授权素材/声音，在独立验收项目中完成实际视频、两次自然语言修改、精确操作/撤销、保存MP4/SRT与重启回读；当前授权不能替代新实际Script或提案采用。先做只读预检：若真实素材/声音或能力条件缺失，明确具体缺口，不以5秒合成样本替代。2026-10-13实查原4段Script、已采用115.788秒generated旁白、21镜头及3原素材均存在，单段录音采用接口不适用于它，local_adoption也不是完整图迁移。root核准改用原工作区task_93d78477016b的正常exact预览/采用/成片/undo与重启回读做可撤销真实编辑验收；不复制DB、不重绑owner、不合段、不伪造录音或新TTS，不改其他用户任务。验收范围明确是已批准真实视频的编辑闭环，不能据此声称从空白全新配音也已验收。原device.running=0，旧成功Job与3条input_stale历史完整保留。按原同设备协调者依次验证；用户已授权临时退出原app并恢复，先核无活跃用户作业，不取消用户工作。输入和输出只在本机，不上传私人媒体/DB。

打包：功能与真实验收完成后读取桌面README/RELEASE及workflow执行实际适用门禁，生成本轮开发验收安装包供本人试用；不把部分能力包宣传为正式0.11全产品完成，不扩大到三平台发送/签名发布。本轮明确试用授权优先于旧范围；财务/账号或不可替代门禁阻断单独标明，不伪造CI通过。临时QA服务/子进程释放后恢复原app。

## 外部交付与本轮非目标

及时提交/push已有分支，维护PR#124及#117/#120/#130真实进度；未验收不关Issue。教学既有thread=01a13039-45e5-76d0-8fbb-8ff3c02b0e6f；发送失败则留本课材料并如实报告，不新建重复教学线程。结束按workflow将剩余工作交回Issue/PR并删除本合同，Git保留历史。

不实现全部#128字段、#129中文OCR/字形、#132–#135通用多轨/监听/变速/关键帧、#136–#140偏好/批量/手机/待定设计/平台外发，不新增广泛研究。已有能力仅为当前真实链路必要故障做窄修，新增范围先报root。全项目状态来自代码、Issue、Git、CI、Release和真实证据，开发计划只维护范围/归属，不记录第二份实时完成率。


## 上一阶段资源阻断与已提交收尾记录（租约已撤销）

真实候选2dfa9f2的单镜头声明已为1.652秒/360×640/静音，实际admission仍沿既有整片新增2GiB与全机swap上限而拒绝；未产生clip媒体/提案，未通过整片正向。QA已正常取消自己的排队cal，经最新版本和原提案证据以正常CAS仅undo本轮−3dB到v29 ready；旧MP4/SRT实际字节保持及v27/v28历史完整。自有API/Web/进程租约已释放，原0.11test4 app已恢复。未修改原private.env/门限，未打包。资源复核为只读报告，不能凭空给preview更小RSS/线程/timeout数值，当前策略仍有效。

上一阶段旧总控曾确认独立审核所见#130具体可见缺陷，授可见编码线程01a13533-b38c-7430-a2de-cb419e2340d6新窄租约：只写apps/web/src/components/AssistantConversationPanel.tsx、apps/web/src/lib/api.ts及现有直接components/__tests__/AssistantConversationPanel.test.tsx。复用已有taskMediaJobLabel的同一语义，将默认本轮结果中的resource_busy/user_cancelled译为用户可理解状态，取消不伪装处理失败/成功；保留真实Job/步骤/采用/草稿/范围。无CSS/layout/DTO/backend或资源门改动，不用全局其他Job指标/QA日志/猜字段补memory/swap原因。DESIGN/design-md相关要求遵守，最小检查后RELEASE，root审核提交/push；必要真实可见验收由原QA线程在新SHA及新租约后执行，编码线程不得构建/运行服务/改用户数据。

可见审核线程01a13533-cec4-70d2-aa5d-ab5bb6c6aefd仅写原visible-review.md/json，读取新run真实排队/取消截图与当前消费者，明确API尚未保存/返回同request实际资源拒绝细节。短preview资源profile与同request安全原因投影记录在Issue，不扩大到任意默认、更改整片预算或省略当前检查。后续原Job真正成功自动观察及新试用包仍须实际证明；本轮不得以旧输出恢复、波形成功、scope声明或单测替代。
