# 0.11 整机重活调度：接线与验收提案

状态：**待主控裁决的实现方案**，不是产品权威，也不是已实现功能。基线 `78ef479b0e80e6778a0a6e1807f69051f9e3d81f`；只依据当前 [PRD 12.1](../../docs/product-requirements.md#121-同一台主机的任务安排011)、[架构 3.12](../../docs/system-architecture.md#312-整机资源调度)、[领域 8.4](../../docs/domain-model.md#84-设备执行安排)、[设计 5.9.1](../../DESIGN.md#591-设备忙碌与执行时间) 和活动合同。硬件两档尚未决定，首版只准一个本地重活单元；性能、让出时间与五人可用性待同设备实测。

## 1. 基线风险和必须封住的入口

| 入口／执行边界 | 当前实证 | 接线要求 |
| --- | --- | --- |
| 自动素材接收、手动补扫、重试 | `material_ingestion.py:50-79` 的一个线程锁住 `scan_once`，`production.py:2559-2674` 扫描后先整文件哈希、批量标 `running`，随后逐文件调用 `analyze_asset`。`main.py:10141-10174` 允许同步手动补扫与重试。 | 轻量发现只登记文件元数据；**哈希也提交重活单元**。稳定候选不得预先批量标运行；实际 grant 后认领一项。重试沿原就绪时间，不插队。 |
| 手动素材分析 | `main.py:10303-10317` 直接调用 `ProductionRepository.analyze_asset`，不经后台锁。 | 与自动入口共用同一请求、输入摘要、许可与检查点；不能同步越过许可。 |
| 素材阶段 | `production.py:2968-3028` 在整文件末尾主要保存 asset；`7030-7077` 的探测/场景 FFmpeg 没有超时；`material_speech.py:46-84` 全片转写；`visual_understanding.py:123-207` 虽按 12 段调用，结果仍留内存；`production.py:6818-6895` 一次性送全部待处理片段给 CLIP。 | 拆为哈希、探测/场景、音频段、视觉帧批、向量批和最终汇总。每单元保存可校验结果再让出；未全部完成时 `analysis_status` 不得为 `ready`。整片场景和语音在改成分段前诚实标不可中断，不能据此通过多人体验。 |
| 连续制作与恢复 | `main.py:12347-12448` 的 start/resume/recovery 交给 `ThreadPoolExecutor(max_workers=1)`；它与 MAT 线程互不排斥。`creation_execution.py:179-218` 仅在 narration/montage_plan/render 阶段前检查取消。`task_runtime.py:93-110`、`production.py:3624-3655` 只防同一 JobRun 重复 claim。 | `JobRun` 保持整条制作事实；每个重活单元由设备协调者准入，阶段之间重新公平选择。线程池只能运行获准单元，不是等待队列。等待外部文字模型结果时不持本地许可。 |
| 制作单步与局部预览 | `main.py:1628-1695` 声音/口型预览；`4305` 配音；`4903` 人物口型；`4935` 剪辑计划；`9451` 成片渲染；`2777-3030`、`6060-8990` 音频/画面/自然语言或精确编辑预览可触发 FFmpeg/本地模型；`7328` 封面生成。 | 在服务层耗资源调用前统一提交请求；同一路径来自手动、连续执行或 Agent 工具时复用父执行上下文，不能二次拿许可或直接同步运行。纯文字/轻量数据库操作不占本地重活。 |
| 其他本地重活 | `main.py:11560` 旧视频处理、`:11695` 语音转写；`:2818` 与 `:4491` GET 波形可触发 FFmpeg；`/api/production/materials/scan` 经 `scan_library` 可能做文件读写。媒体预览服务在 `narration.py:562`、`avatar_lipsync.py:558` 先标 `running` 后同步调用供应者。 | 保留可达时必须接许可，或由主控明确退役/隔离；GET 中的惰性计算也不能成为绕行入口。排队前不把预览事实标成已运行。模型安装等设备维护重活单列后核；未覆盖的可达重型路径不得宣称“全入口”。 |

以上是定向源码检查，不是动态覆盖率证明。后续用路由扫描、负向测试和运行时 instrumentation 核实最终候选。部分模型调用已有独立超时，不能因此推断其整个流程可抢占；`_vlm_execution_lock` 仅约束进程内 VLM，不能承担整机许可。

## 2. 单一协调者和最小持久字段

在现有 Production SQLite 中新增 **调度元数据**，不创建第二个 CreationTask、素材任务或成片状态机。`JobRun/TaskEvent`、`material_ingestion_files/material_assets/material_segment_features` 仍是业务事实；调度行只引用它们。

1. `device_work_requests`：`id`、`workspace_id`、`member_id NULL`（仅服务端已验 `AuthPrincipal.user_id`；后台为 NULL）、`owner_type` (`job_run`/`material_asset`/`media_preview`/其他已存在对象)、`owner_id`、`input_digest`、`unit_key`、`class` (`preview_dependency`/`normal_make`/`background`)、`policy` (`asap`/`idle`/`scheduled`)、`not_before_utc`、`window_spec_json`（IANA 时区、允许的本地起止时间，跨日也适用）、`first_ready_at_utc`、`eligible_since_utc`（当前单元开始合格等待的时间）、`state` (`waiting`/`granted`/`yield_requested`/`blocked`/`done`/`cancelled`)、`wait_reason`、`checkpoint_ref`、`requested_resources_json`、`created_at/updated_at`。唯一键 `(owner_type, owner_id, input_digest, unit_key)`；`member_id` 取服务器验证身份，用户跨工作区仍按同一成员轮转，NULL 后台按 workspace 分组。重试、改时段、重启均不得重置同一单元的 `first_ready_at_utc/eligible_since_utc`；成功运行后下一单元有自己的 `eligible_since_utc`，防止同一素材靠旧年龄连续插队。
2. `device_permit` 单行 `id=1`：`generation` 单调递增、`request_id`、`token_hash`、`holder_host/boot_id/pid/process_start`、`child_group_id/child_process_start`、`granted_at/heartbeat_at`、`state` (`free`/`held`/`reconciling`)、`last_served_group_json`、`preview_burst/normal_burst`。它是**全设备一份**，由 `BEGIN IMMEDIATE` 下的唯一协调者事务修改；`generation+token` 是 fencing 身份，旧执行者不得写检查点或提交成功。首次单重活，不能通过其他 Python 进程的 Semaphore、VLM 锁或线程池再放行。
3. `device_session_activity`：服务器生成的 `session_id`、`member_id`、`workspace_id`、`last_heartbeat_at`、`last_interaction_at`、`playing/recording/editing`，仅存调度状态而不存文字、媒体或路径。客户端必须来自已验证桌面会话；过期心跳不永久占用优先级，也不取消已提交作业。没有可靠活动上报前，`idle` 策略标不可用；可引导用户选择指定时间，不能猜测全机空闲。

最小 Repository 接口（建议新增 `apps/api/app/device_scheduler.py` 承担选择/许可，SQLite DDL 由唯一持久化负责人放在 `database_migrations.py` 或 ProductionRepository 现有迁移入口；不可两处建表）：

```python
submit(owner_ref, input_digest, unit_key, work_class, policy, principal_or_service,
       not_before_utc=None, window=None, resources=None) -> DeviceWorkRequest
next_admissible(now, device_sample) -> DeviceWorkRequest | None
grant(request_id, coordinator_epoch) -> Grant(token, generation, unit_key)  # BEGIN IMMEDIATE + singleton CAS
heartbeat(grant, holder_and_children, resource_sample) -> None
request_yield(grant, reason) -> None
checkpoint_and_release(grant, owner_checkpoint_write, next_unit=None) -> None  # same DB transaction
fail_or_defer(grant, reason, retry_policy) -> None
reconcile_on_startup() -> RecoveryVerdict
view_for(principal, owner_ref=None) -> FilteredScheduleView
```

`submit` 与原业务对象的显式创建在同一事务，接口返回 `已安排` 而非 `正在渲染`。无当前已存在对象或来源身份不可信时拒绝提交。`owner_checkpoint_write` 是对应 MAT/MAKE 在**同一个 Repository 事务**内写业务结果、`TaskEvent` 与调度状态的回调/窄接口；产物文件先落临时路径、校验、原子改名，再落数据库引用。崩溃后的无引用文件当孤儿清理，不得当有效检查点；发生输入摘要、授权、模型版本变化时不复用旧检查点。

## 3. 准入、恢复与失败语义

- 协调进程通过同机独占进程锁保持单领导者，SQLite 单行许可仍以事务 CAS 为最终事实。只有协调者派发已准入单元；请求线程、手动路由、Agent 工具和 executor 都只 `submit`。获得 `Grant` 后，在实际 FFmpeg/模型入口再次核 token、输入版本、授权与设备样本。嵌套媒体函数接收同一 `ExecutionContext(grant, owner_ref, input_digest)`，不得递归排队；父 JobRun 在三个阶段间不持许可。
- 授权前及运行中采样 CPU 压力、可用内存/交换压力、可知的 GPU/统一内存压力、磁盘余量和读写压力；模型常驻占用持续记账。保留系统、桌面、播放与录音余量。硬件未知采用保守线程/帧批/解码参数；测不到关键资源时阻断对应能力或标测量未知，不能用模型文件大小或固定 CPU 百分比作可运行证明。FFmpeg 参数、BLAS/OpenMP/模型 worker 线程、帧尺寸、缓存与临时盘空间纳入设备 profile，profile 须按两档真机分别测定；本轮不写固定数值。
- 许可过期**只触发核查，不自动回收**。启动时核 holder PID+创建时间、boot id、子进程组和实际资源；活着则接管/等待，死且子进程退出并释放常驻占用后清理 lease，再依旧检查点重排。子进程归属不明、核查失败、设备/盘缺失时 `reconciling` 并阻断新重活，状态暴露修复动作；不能向另一个进程发第二份许可。进程包装器应在真正执行模型/FFmpeg 前登记 PID/组并获 ACK，关闭崩溃在 spawn 与登记之间的空窗。
- 每个 grant 只跑一个有界单元：有效结果写入后归还许可、再次公平选择。超过资源预算或有录音/播放保护时发出 `yield_requested`；不可中断的同步调用显示“完成当前步骤后让出”。仅在子进程退出且模型常驻资源已释放或继续计入额度后显示“已让出”。取消先鉴权，排队项立即取消；运行项标请求取消，安全边界后清理临时产物并结束，绝不宣称已立即停止。超时需终止/等待受控子进程组，不能只由 Python 抛异常而留下设备负载。
- 素材 `ready` 仅在全部阶段与源摘要核验后提交；分批视觉、向量、转写结果可以作为 pending asset 的有效局部事实，但不可被成片检索当完整 ready。断盘为等待依赖，失败为有原因的 failed，资源不足为等待资源，窗口未到为等待时间。失败重试有界，沿原 `first_ready_at_utc`；输入失效需重新确认，不能靠重试按钮改 digest 继续。连续制作仍以同一 `JobRun` 和阶段 `TaskEvent` 恢复，有效上游产物不重算、不重复计费。

## 4. 选择规则和时间策略

每次 grant 前先过滤：未取消、输入版本/授权仍有效、依赖就绪、设备资源可容纳、当前窗口可执行、录音/播放保护可容纳。`idle` 还须所有**有效**会话至少 5 分钟无播放/录音/编辑且没有就绪前台重活；会话心跳过期按明确 TTL（建议首测 30 秒后定）移出活跃集合，不替它取消作业。若桌面会话活动源未联通，`idle` 请求保持 `blocked_activity_unknown`，不假放行。

在合格候选中，先履行 `eligible_since_utc` 起已等满 30 分钟的素材单元一次机会；其余按优先预览、普通制作、后台类别。连续 3 个优先预览后让一项已就绪普通制作运行；连续 3 个制作后给已就绪素材一次机会。计数跨重启存于 `device_permit`，只有实际 grant 计数，因资源/窗口不合格者不形成“机会”。同类按服务端 member 身份轮转，再取该组最早 `first_ready_at_utc`；后台无 member 按 workspace 轮转，但创建新 workspace 不能为同一已知 member 换新身份。没有合格候选可空闲，无须占住许可等待未来时刻。

“尽快”只意为普通队列尽早获得资源；“指定时间”是最早开始，不是完成保证。窗口由 IANA 时区和本地钟表达，例如用户**主动选** 22:00–07:00；跨午夜、夏令时与休眠后按下一有效窗口计算。窗口结束不再发新 grant，已运行不可中断单元到边界保存后退出。错过窗口等待下一次，不在白天私自补跑。后台文件发现继续低负载限速，但大范围目录遍历/全文件哈希也要分批和预算。主机开机、磁盘连接、模型可用是夜间执行的必要条件，调度器不改系统开机/休眠设置。

对外只返回本人有权作业的细节；其他 workspace 的占用是汇总信息。调整安排/取消由已验证成员限定本人提交的请求，后台安排由有权操作绑定范围的人修改，管理员角色未定不加全局抢占。可信历史不足时只给排队原因、前方就绪数和更新时间，估时留空。计划可用 API：`POST /api/creation-tasks/{id}/execution/start` 增加时间策略、`PATCH .../execution/schedule`；素材绑定设置增整理策略；`GET .../execution` 与素材状态附过滤后的设备等待事实；桌面会话活动 `POST /api/device/activity` 仅写心跳与布尔状态。具体 URL/schema 由 MAKE/MAT/UI 现有文件负责人核定，主控裁决，不能在协调器里发明第二套业务状态。

## 5. 顺序、独占写入与最小验证

1. **主控先裁决接口和归属**：确定新的协调器与迁移 DDL 唯一写者、既有 owner 表、跨工作区 member 权限、桌面活动来源、设备设置入口；冻结待退役的直接重活路径清单。此阶段不声称整机已可用。
2. 调度负责人在新 `apps/api/app/device_scheduler.py` 和 `apps/api/tests/test_device_scheduler.py`（主控授权后）实现纯选择器、SQLite 请求/许可、进程恢复、设备采样边界；DDL 文件独占写者由主控指定。先用隔离 SQLite 测双进程抢许可、陈旧 token、子进程存活、崩溃重启、窗口跨夜、心跳过期、成员轮换与防饿死。未获文件所有权前不改生产代码。
3. MAT01 独占 `production.py/material_ingestion.py`，把轻量发现、预算内哈希、逐个实际认领、分批持久检查点和已有人工纠正合并接到 `Grant`；先缩短场景/语音不可中断范围，再做视觉与 CLIP 批次恢复。`material_speech.py`、`visual_understanding.py`、FFmpeg 子进程封装若需修改，主控另给**单一**媒体负责人，不由本任务越权写。
4. MAKE01 独占 `main.py/creation_execution.py/schemas.py` 与其现有前端/Agent 文件，改 start/resume/recovery 只提交，按获准单元恢复 `JobRun`，阶段之间归还。逐项接单步、预览、重试及 Agent 工具；将媒体预览状态从“提交时 running”改为实际 grant 后 running，避免错误可见状态。其他 `narration.py/avatar_lipsync.py/montage.py/audio_*` 的子进程控制由主控定单一 owner。UI01 只接可见选择和事实状态，不改许可。
5. QA01 做**同一候选**的路由负向表：自动/手动/重试/单步/Agent/GET 惰性重活没有 token 必须拒绝执行；隔离库故障注入验证恢复和来源边界。真机上五会话测输入/取消响应、播放和录音连续性、峰值 CPU/内存/磁盘、普通制作轮换、素材实际推进、断盘/重启/过期会话/跨夜。验收前先定响应和连续性门槛及空载基线；两档硬件未给出则性能 verdict 为未验证。

提议的退役目标：MAT 批量预标 `running`、全文件末尾唯一保存点、MAKE 整条 JobRun 独占 executor、同步手动重活直通、预览提交时即标 running、进程内锁/线程池被误当全机许可。最终必须在同一候选确认新路径工作，且这些可达绕行入口消失或确实被许可保护。文档和隔离模拟测试只能证明方案与部分逻辑，不能代替真实设备结果。

### 待主控裁决

- 设备设置的现有受权入口及两档目标硬件 profile；未知时严格单重活、关键测量缺失则阻断对应能力。
- 共享 workspace 中“本人任务”的成员授权细则，尤其现有 `cancel` 仅以 workspace 查询的边界；本方案只用已验 `AuthPrincipal.user_id`，不创造管理员角色。
- 哪个单一负责人写 `database_migrations.py` 和媒体子进程封装；MAT/MAKE 只改各自 owned files。
- `GET` 波形、旧 `/api/videos/process`、`/api/voice/transcribe` 与模型安装是否在 0.11 保留；凡可达且耗重资源，都需许可或明确退役，不能留下灰色绕行。
