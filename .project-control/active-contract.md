# vNext 产品权威迁移合同

## 1. 合同状态

- 状态：ACTIVE
- 迁移阶段：NEW_AUTHORITY_FROZEN
- 实现状态：MILESTONE_3_1_ACCEPTED
- 建立日期：2026-08-31
- 建立时源码基线：40f9ea9
- 模式：MIGRATE
- 纵向迁移负责人：主 Agent

本合同替代此前“本地真成片 v0.1”恢复合同。旧合同定义的真实本地成片与验证证据仍可从 Git 历史追溯，但不再定义当前产品对象、交互、范围或验收。

当前产品权威只来自：

- docs/product-requirements.md
- docs/domain-model.md
- docs/system-architecture.md
- DESIGN.md

代码和运行环境仍是实现状态的事实源。权威文档更新不代表对应能力已经实现。

## 2. 当前迁移目标

交付一条以素材智能和自然语言局部改片为核心的纵向闭环：

用户给入的默认可用内容
→ 素材分析和检索
→ 自然语言目标
→ 结构化 Plan
→ 同一 CreationTask 的文案、配音、故事板、字幕和成片
→ 选中镜头或配音片段后用自然语言替换
→ 查看类型化变更、影响范围和预览
→ 确认后增量重算
→ 新 TimelineVersion
→ 撤销、恢复和本地输出

实现阶段不得创建第二套任务、聊天、时间线或媒体事实源。

## 3. 已退役的产品前提

下列内容已在产品权威中 RETIRED，不得继续作为新实现、界面或测试的验收依据：

| 旧前提 | 当前规则 | 状态 |
| --- | --- | --- |
| 每条素材、人物和声音必须填写权利人及证明后才能创作 | 用户给入当前工作区的内容默认可用，用户可主动设置使用限制 | RETIRED_IN_AUTHORITY |
| 自然语言只存在于首页，用于创建任务 | 自然语言贯穿任务，并支持当前选中对象的局部修改 | RETIRED_IN_AUTHORITY |
| 聊天内容或前端状态可以独立保存修改结果 | 命令必须编译为 EditProposal 和 EditOperation，写入 CreationTask 与 TimelineVersion | RETIRED_IN_AUTHORITY |
| 修改一个局部后重新生成整条任务 | 只使真实依赖的下游产物失效并增量重算 | RETIRED_IN_AUTHORITY |
| 旧本地真成片 v0.1 合同继续累积补丁 | 本合同是当前唯一活动合同 | SUPERSEDED |

安全边界没有被退役：Workspace 隔离、文件访问权限、云端数据外发、付费调用、平台账号连接、发布确认和删除确认继续有效。

## 4. 正向交付

下一实施候选必须同时包含：

1. 扩展现有 ProductionRepository、MediaRepository 和任务页，不建立旁路仓储。
2. 至少一种真实素材分析结果可被自然语言和结构化筛选共同检索。
3. Plan 引用当前工作区真实内容、素材、人物声音和能力状态。
4. 任务页提供 ContextCommandBar，并把当前阶段、选中对象和播放位置作为范围上下文。
5. 自然语言被编译为类型化 EditProposal，执行前显示动作、目标、失效依赖和差异。
6. 用户确认后写入 EditOperation 和新的 TimelineVersion。
7. 替换一个镜头或配音片段时，只重新生成或渲染受影响范围。
8. 支持精确替换、撤销、重启恢复、客户端预览和本地保存。
9. 失败持久化原因、影响和唯一修复动作，不伪造成功。

## 5. 必须消失的旧内容

迁移验收前必须退役：

- 面向普通用户的 Episode、Batch、Provider、Transform 等旧名称；
- 旧 production、topics、script、draft 或 Episode/Batch 默认入口；
- Episode、Batch、repo.today 和模板成功 fallback 的新链路写入；
- 每条人物、声音和素材的强制权利证明表单与门禁；
- 只改变聊天或前端内存、未写入 TimelineVersion 的编辑；
- 局部编辑后无差别全量重做的执行路径；
- modern-overrides、pixel 系列或其他继续保护旧 UI 的活跃消费者；
- 指向已退役路由、行为和样式的快捷键、帮助、测试和文案。

退役顺序为：

ACTIVE → COEXISTING → MIGRATED → RETIRED

只有 RETIRED 才能计入完成。

## 6. 验收证据

同一候选必须提供：

- 精确 Git SHA；
- 真实 Workspace、CreationTask 和 TimelineVersion；
- 一次真实自然语言局部替换及其类型化提案；
- 确认前后截图或可见录屏；
- 持久化变更、依赖失效范围和 Worker 日志；
- 新预览和可播放最终 MP4；
- 重启恢复与撤销证据；
- 1024×768、1180×800 和主机最大尺寸的可见排版检查；
- 旧入口、消费者、写路径、fallback、样式和错误测试的不存在证明。

源码能追踪、构建通过、API 存在和静态截图只属于辅助证据，不能替代真实点击与真实副作用。

## 7. 停止规则

- 发现权威冲突时，先 HOLD，实现不得继续沿旧记忆推进。
- 测试入口或辅助权限阻断时，先验证测试工具，不修改产品代码掩盖失败。
- 相同类别连续两次失败且没有新的产品证据时，进入 HOLD 并重新审查目标。
- 模型、媒体引擎、输入数据或运行环境缺失时结论为 BLOCKED，不建立模拟成功 fallback。
- 在完整纵向切片明确前，不把任务拆成大量只写文档、只加测试或只改一层的小任务。

## 8. 下一次派工前置条件

1. 主 Agent 读取本合同和四份权威的目标章节。
2. 对当前代码建立旧消费者、事实源和自然语言入口清单。
3. 选择一个纵向实现负责人，对“自然语言替换一个真实片段并增量出新预览”负责到底。
4. 只有边界清晰且可独立交付时才启用并行调查或独立质量任务。
5. 每次权威变化先更新唯一文档并发出 AUTHORITY_CHANGED；旧合同内容不得继续复制到新任务。

## 9. 里程碑一加速执行波次

本波次只建立可信生产底座，不扩展平台发布、自动任务、全局 UI 或新的行业模板。

里程碑结果：同一 CreationTask 能追加不可变 TimelineVersion，提交可恢复 JobRun，发布可追溯 Artifact；重启后状态一致，旧 Episode/Batch 不再承担生产写入。

权威快照：

- docs/product-requirements.md：e6c866367a05183cb95cea634221e5630f92e4862310ffcf1c2a89a6cc31c6a8
- docs/domain-model.md：2fd05d9bd6f464bde0f4cc746426fb47e81ab0624e2eb70d8b7eb8a9c350b443
- docs/system-architecture.md：b8247fd652e28b09c32e90700c7ddebd849f34a7c3bec4fce64725eaea8bdc1d
- DESIGN.md：3a7bc24d3e5ad9c2810a89260c1ddbaa7476528b0aea3f7a1a5c0ab9a83efe7a

### 9.1 共享最小契约

- TimelineVersion：id、workspace_id、task_id、parent_version_id、sequence、snapshot、created_by_operation_id、created_at；创建后不可更新或覆盖。
- JobRun：id、workspace_id、task_id、kind、idempotency_key、input_digest、status、progress、attempt、output_refs、failure_reason、impact、repair_action、created_at、started_at、finished_at、updated_at。
- JobRun 状态：queued、running、succeeded、failed、cancelled、interrupted。重启时 running 不得自动成为 succeeded。
- ArtifactRecord：id、workspace_id、task_id、kind、logical_ref、sha256、size_bytes、producer_job_id、input_refs、created_at。
- TaskEvent：id、workspace_id、task_id、job_id、event_type、payload、created_at。
- 文件层只返回逻辑引用、摘要和大小；ProductionRepository 是这些领域记录的唯一数据库写入口。

### 9.2 并行写入所有权

工作包 A：领域与数据脊柱

- Owned：apps/api/app/production.py、apps/api/app/database_migrations.py（如确有需要）、apps/api/tests/test_production_foundation.py。
- 交付：上述领域记录、版本化 schema、Workspace 隔离、TimelineVersion 不可变、Repository 重启恢复。
- 禁止：main.py、schemas.py、Web、Desktop、媒体执行和旧 Episode 仓储。

工作包 B：可靠任务运行与文件产物

- Owned：apps/api/app/task_runtime.py、apps/api/app/artifact_store.py、apps/api/app/video_jobs.py、apps/api/tests/test_video_jobs.py。
- 交付：基于窄 JobStateStore 协议的领取、幂等、取消、中断恢复和失败语义；Artifact 原子发布、摘要和逻辑引用。
- 禁止：ProductionRepository、main.py、schemas.py、Web、Desktop 和外部模型调用。

主 Agent：共享集成

- Owned：apps/api/app/main.py、apps/api/app/schemas.py、活动合同和四份产品权威。
- 在 A/B 接口稳定前不提前编写大量 HTTP 或 UI 测试；集成时只增加一个真实 CreationTask→JobRun→Artifact→重启恢复流程。

独立 Quality 在候选冻结前保持 HOLD，不参与实现。

### 9.3 测试与成本门禁

- 工作线程只跑 owned 能力的最小测试，不运行全量 API/Web、Electron 构建或 DMG。
- 默认补充现有测试文件；只有新独立领域能力无法在现有文件表达时才允许新增测试文件。
- 不测试按钮文案、CSS 类名、内部组件结构、简单字段透传或已退役产品行为。
- 主 Agent 集成后只运行一次共享目标测试；候选冻结后独立 Quality 一次性验收。
- 同类失败连续两次且没有新产品证据时 HOLD；测试工具故障不得通过修改产品代码规避。

### 9.4 里程碑一候选与独立验收

用户已决定暂不并行启动里程碑二、三、四；它们保持未启动。本候选只验收里程碑一。

候选文件 SHA-256：

- apps/api/app/main.py：40744917101c0d8d5eb961edf258c769e2655e52be14391fa27ac33018ebf96e
- apps/api/app/production.py：254760f0ee07568a80a30100964cd8faea59e8fefa8dfb264548aad03376628a
- apps/api/app/database_migrations.py：02b28f016d452b7f64d1e51b70c0a7bfb1c39715a4060535423cd58849b9e883
- apps/api/app/task_runtime.py：84321e65bb9a3fd416237e1ed82301f35457239d75cd8bd9d1ca743b65ab7ba7
- apps/api/app/artifact_store.py：201e68bfe5083382a2f215c752cd8bcbc84c58d9fefa9cce4f30385ee0a2def3
- apps/api/app/video_jobs.py：13e5183720c8997f59e662317e8282a3561ed081d7698b52a1d0e289c92e0cba
- apps/api/tests/test_production_foundation.py：f86da6e3bf7e6b7b79a68060d7d14b442c7728c05cda2d6f8df0aef9fb41c6fb
- apps/api/tests/test_video_jobs.py：267b716a1774ff2c7f0a8fa8227aa495de935364f6c0d52bf5c5d9a5d7ab0490

主 Agent 候选验证：目标两文件 `31 passed`；Python 编译与 scoped `git diff --check` 通过。该结果是候选自检，不代替独立验收。

独立 Quality：`ACCEPT`。八个候选文件哈希全部匹配，独立运行目标测试为 `31 passed`；未检查里程碑二至四、Web、Electron、DMG、外部模型或真实 FFmpeg。

## 10. 里程碑一真实渲染迁移

本波次不改变产品权威，只把现有 CreationTask 成片渲染纵向迁入已验收的生产底座。里程碑二、三、四继续保持未启动。

用户结果：用户在现有任务页确认剪辑后触发渲染，系统以持久 JobRun 执行真实 FFmpeg，发布不可变 MP4 与 SRT，登记 TransformRun、ArtifactRecord、DependencyEdge、TaskEvent，并追加指向成片的新 TimelineVersion；失败不得产生成功状态或伪造产物。

### 10.1 新增共享契约

- TransformRun：id、workspace_id、task_id、job_id、transform_type、input_digest、input_refs、output_refs、engine、parameters、status、started_at、finished_at、failure_reason、remediation。
- DependencyEdge：id、workspace_id、task_id、upstream_ref、downstream_ref、created_by_transform_id、created_at。
- CreationTask 渲染产物逻辑引用固定为 `artifacts/{task_id}/{job_id}/preview.mp4` 与 `artifacts/{task_id}/{job_id}/subtitles.srt`。
- MontageRenderer 只在调用方提供的 job-scoped 暂存目录生成文件；最终发布由 ArtifactStore 完成。
- 成功顺序：JobRun running → FFmpeg/ffprobe 成功 → 文件原子发布 → Artifact/Transform/Dependency/Event 登记 → TimelineVersion 追加 → JobRun succeeded。
- 失败顺序：记录首个真实失败三元组 → JobRun failed → CreationTask/剪辑计划显示失败；不得写成功 TimelineVersion。

### 10.2 并行所有权

工作包 A：Transform 与依赖事实

- Owned：apps/api/app/production.py、apps/api/app/database_migrations.py、apps/api/tests/test_production_foundation.py。
- 交付：TransformRun/DependencyEdge 的 schema v2、Repository、Workspace/Task/Job/Artifact 关联校验、重启持久化。
- 禁止：main.py、montage.py、schemas.py、Web、Desktop、FFmpeg 调用。

工作包 B：CreationTask 渲染暂存边界

- Owned：apps/api/app/montage.py、apps/api/tests/test_montage_pipeline.py。
- 交付：CreationTask 渲染必须接受显式 job-scoped 暂存目录；MP4/SRT 均在该目录生成并继续执行现有真实媒体校验；Batch/Episode 路径行为不得扩大。
- 禁止：production.py、database_migrations.py、main.py、schemas.py、task_runtime.py、artifact_store.py、Web、Desktop。

主 Agent：纵向迁移负责人

- Owned：apps/api/app/main.py、apps/api/app/schemas.py、apps/api/app/artifact_store.py、活动合同。
- 集成现有 `/api/creation-tasks/{task_id}/montage/render`，不得新增第二条渲染路由或第二套事实源。

### 10.3 退役与验收

本切片必须退役：CreationTask 渲染成功时直接把绝对 `output_path/subtitle_path` 当业务事实；未登记 JobRun/Artifact/Transform 的成功；同一逻辑引用静默覆盖。Legacy Batch/Episode 渲染不在本波次删除范围，但不得成为 CreationTask 新链的 fallback。

只运行三组目标测试：production foundation、video jobs、montage pipeline。先用合成媒体完成集成，条件允许时再使用当前已授权本地素材做一次真实 FFmpeg 产物验证；不构建 Web、Electron 或 DMG。候选冻结后由独立 Quality 一次性验收。

### 10.4 冻结候选

候选文件 SHA-256：

- apps/api/app/main.py：af11d91d96340cedcecea1478cc7b3ddabe1ebdacdf385c6a9fab9a0ac09f504
- apps/api/app/montage.py：d1591778811ac43298cdb802fae94ad8c4223e3f2218d1acfd2a9fb8f477e175
- apps/api/app/production.py：6a7102f86c2d45cdc3a25d59b1c37367b1fb1d2beeeda2613670caac1f210440
- apps/api/app/database_migrations.py：981243beba70025bb3ea235f056cdfca7ca514915d3437f140e700ab32b580e5
- apps/api/app/task_runtime.py：84321e65bb9a3fd416237e1ed82301f35457239d75cd8bd9d1ca743b65ab7ba7
- apps/api/app/artifact_store.py：9abd043b5983ccbe9fd276591e5004bc0b41cf852e2af2db36292a23902738e7
- apps/api/app/video_jobs.py：13e5183720c8997f59e662317e8282a3561ed081d7698b52a1d0e289c92e0cba
- apps/api/tests/test_production_foundation.py：755f30e3953f053256fd7b13c74abbe31373b19177fccf0c45863af4cbe3b8e8
- apps/api/tests/test_video_jobs.py：8fd47e95e1e3651db382fc6846a97d859a2454be9a5986c34f2016005a8dda54
- apps/api/tests/test_montage_pipeline.py：4184737b571df1ac5ecbee39ce5c55b37c4c3e666e3272c92ccc8938a75008fa

第一次独立 Quality 结论为 `REJECT`：恢复流程在已发布 MP4/SRT、数据库尚未登记时仍先重跑 FFmpeg。候选已按首个问题修正：恢复入口会先验证该 Job 的两个确定性逻辑产物；两者存在时跳过渲染并续写缺失的领域记录。对应定向测试模拟发布后进程退出，确认重启恢复不再调用渲染器。

主 Agent 修正后验证：三组目标测试 `53 passed`；其中真实 FFmpeg/ffprobe 合成媒体成片测试未跳过。Python 编译、scoped absence search 与 `git diff --check` 通过。该结果是候选自检，不代替第二次独立验收。

第二次独立 Quality：`ACCEPT`。重新冻结的候选哈希匹配；发布后中断窗口可直接复用已验证的 MP4/SRT，补齐数据库谱系并完成原 JobRun，不再重跑 FFmpeg。里程碑二、三、四仍为未启动，本结论不包含 Web、Electron、DMG、外部模型或用户真实素材验收。

## 11. 里程碑二：自然语言替换一个真实镜头

本波次是里程碑二的第一条纵向切片，不宣称完成全部素材智能、PlanCompiler 或自然语言编辑。用户结果：在现有 CreationTask 故事板中选中一个真实镜头，输入“换成另一个镜头”等局部要求，先看到替换对象、候选镜头、失效产物和副作用；确认后通过类型化 `ReplaceClip` 写入同一事实源，追加新的 TimelineVersion，并把旧成片标记为需要重新渲染。

迁移负责人：主 Agent。当前精确控件直接 PATCH 剪辑计划仍为 `COEXISTING`，只允许到后续同源迁移完成；不得把自然语言编辑实现为前端内存、聊天记录、任意文件路径或 FFmpeg 命令。

权威快照：

- docs/product-requirements.md：e6c866367a05183cb95cea634221e5630f92e4862310ffcf1c2a89a6cc31c6a8
- docs/domain-model.md：2fd05d9bd6f464bde0f4cc746426fb47e81ab0624e2eb70d8b7eb8a9c350b443
- docs/system-architecture.md：b8247fd652e28b09c32e90700c7ddebd849f34a7c3bec4fce64725eaea8bdc1d
- DESIGN.md：3a7bc24d3e5ad9c2810a89260c1ddbaa7476528b0aea3f7a1a5c0ab9a83efe7a

### 11.1 冻结接口

- `POST /api/creation-tasks/{task_id}/edits/preview`
  - 输入：`raw_command`、`context_stage`、`selected_object_refs`、`playhead_ms`。
  - 输出：持久化的 EditIntent 与 EditProposal；提案必须包含普通用户可读摘要、目标引用、唯一类型化操作、将失效的产物引用、前后差异和外部副作用。
- `POST /api/creation-tasks/{task_id}/edits/{proposal_id}/confirm`
  - 只确认状态为 proposed 的提案；执行 `ReplaceClip`，持久化 EditOperation，追加 TimelineVersion，CreationTask 指向新版本，剪辑计划回到 ready，旧 output/subtitle 引用从当前计划移除。
- 本切片唯一支持的操作为 `ReplaceClip`。必须存在一个选中的 `clip:{clip_id}`；候选必须来自当前 Workspace 已有的镜头候选且不得与原镜头相同。范围不明确返回 `needs_scope`，没有可信候选返回明确不可用，不猜测文件路径。
- 输入摘要未改变的文案、配音、字幕 cue 和未选中镜头保持不变；当前 Preview/Final output 被列为 invalidated。确认动作不自动调用 FFmpeg，不产生付费或外部副作用。

### 11.2 数据契约

- EditIntent：id、workspace_id、task_id、raw_command、context_stage、selected_object_refs、playhead_ms、intent_type、scope、constraints、confidence、status、created_at。
- EditProposal：id、workspace_id、task_id、intent_id、summary、target_refs、operation、invalidated_refs、preview、external_effects、status、created_at、updated_at。
- EditOperation：id、workspace_id、task_id、proposal_id、operation_type、payload、base_version_id、result_version_id、status、failure_reason、created_at、applied_at。
- EditIntent 与已应用 EditOperation 不可覆盖；Proposal 只允许 `proposed -> confirmed/rejected/expired`。Workspace、Task、Intent、Proposal、TimelineVersion 必须同源。

### 11.3 并行所有权

工作包 A：编辑领域与持久化

- Owned：apps/api/app/production.py、apps/api/app/database_migrations.py、apps/api/tests/test_production_foundation.py。
- 交付：三个编辑对象、schema v3、Repository 状态迁移、Workspace/Task/Timeline 关联和重启持久化。
- 禁止：main.py、schemas.py、Web、媒体渲染、旧 Episode/Batch。

工作包 B：任务页上下文命令与变更预览

- Owned：apps/web/src/components/CreationTaskWorkspace.tsx、apps/web/src/components/CreationTaskWorkspace.module.css、apps/web/src/components/__tests__/CreationTaskWorkspace.test.tsx、apps/web/src/lib/api.ts、apps/web/src/lib/types.ts。
- 交付：镜头选择、ContextCommandBar、ChangePreview、确认/取消和刷新；只呈现 API 事实，不在客户端推断替换结果或成功。
- 禁止：API/Python、全局导航、其他页面、全局 CSS、Electron、设计权威。

主 Agent：纵向集成

- Owned：apps/api/app/main.py、apps/api/app/schemas.py、必要的窄 EditCompiler/DependencyGraph 实现、活动合同和集成测试。
- 负责候选选择、类型化执行、旧成片失效、TimelineVersion 追加、接口集成和最终退休责任。

独立 Quality 在候选冻结前保持 HOLD。

### 11.4 验收与停止

- 正向：真实持久化任务上完成 preview → confirm；重启后 Intent、Proposal、Operation、新 TimelineVersion 和已替换镜头一致；未选中镜头与配音保持不变。
- 可见：任务页选中镜头后可输入要求；确认前看见替换前后、失效范围和“不会立即调用模型/渲染”；确认后页面显示需要重新渲染。
- 负向：无选中镜头、跨 Workspace 引用、旧/已确认提案重复确认、无候选和任意路径输入必须被拒绝；源码中不存在前端直接改 montage 状态或自然语言直接执行 FFmpeg。
- 工作线程只跑 owned 最小测试；主 Agent 集成后一次性跑 production foundation、自然语言编辑接口和 CreationTaskWorkspace 目标测试。Web 浏览器可见验收留到同一候选冻结后，不构建 Electron/DMG。
- 同类失败连续两次且没有新产品证据时 HOLD；不得通过 fallback、mock 成功或扩大操作类型绕过首个失败。

### 11.5 里程碑二第一切片候选

候选文件 SHA-256：

- apps/api/app/main.py：c45ae18c04791d0922e67ebebcff4a23d264cfa09d8910ff1b25426988f791a2
- apps/api/app/schemas.py：c6df42414fa05ee422e6580d039293dc3c56fdbf1613b47310790289bb32bf7a
- apps/api/app/production.py：a45bd9329607c9a5f86171cc07364b7111b79378f87858fecf9dc7baece02a3c
- apps/api/app/database_migrations.py：23e0260eb476426b5b4cc01268f0cd96ed0a402d84feace54d4a349db612d59f
- apps/api/tests/test_production_foundation.py：a56782bce4646b558b75872e4fa4fc661680fe797891ab0bd8873c51fab50c9a
- apps/api/tests/test_montage_pipeline.py：59e1da6762a93b56f9508482bff1c94a7d1c8c3cb8a644eff41ac71c1243b935
- apps/web/src/components/CreationTaskWorkspace.tsx：f968f825c374e1c9d9801d3576595a5787f6a60aaf97fe36514873f06a308dd9
- apps/web/src/components/CreationTaskWorkspace.module.css：e24270b1ab5fc4cbaa253c528d5375a82198d8fde5679e86006ffcbd683ce4fd
- apps/web/src/components/__tests__/CreationTaskWorkspace.test.tsx：b4c7955db96ab5b0789fe04142a334cfc9c943b80204dc436ee64cbafd17737e
- apps/web/src/lib/api.ts：6cfe2e06a7b377a8950f798e90d3ade1d87499bf797803476a919a19f5649541
- apps/web/src/lib/types.ts：a62ea1462733b9538cebfb1fa461bea09a1e11ae45bc8c0c3448da8e8bc3bc9d

主 Agent 候选自检：API 目标三组 `54 passed`，Web 任务页 `7 passed`；Python 编译、TypeScript/TSX ESLint、DESIGN.md 轻量校验和 `git diff --check` 通过。临时数据库与本地 Web/API 上完成了真实点击 preview → confirm；确认后提案为 confirmed、ReplaceClip 为 applied、TimelineVersion 由 1 追加到 2、当前成片引用失效、旁白和其他镜头保持，同数据库重启后页面仍显示需要重新渲染。

首个真实可见失败：迁移前旧任务的 output/subtitle 仍是绝对路径，不能成为新 EditProposal 的逻辑引用。候选未放宽路径校验，而是返回 `edit_base_not_migrated`，不写入 EditIntent，并要求先用当前生产链路重新渲染。因此旧任务仍属 `COEXISTING`，不在本切片中宣称退役。

1440×900、1180×800 和 1024×768 可见检查：选中镜头、命令输入、前后差异、失效引用、外部影响和确认/取消可读可达；1024 尺寸已真实点击确认并进入需要重新渲染状态。ChangePreview 有内容时转为文档流并自动滚入视口，不再用大型 sticky 层覆盖故事板；1024–1279 的 Inspector 收起，主工作区未出现异常断词、重叠、横向溢出或不可达主动作。

第一次独立 Quality：`REJECT`。首个问题是用户命令携带 `/tmp/x.mp4`、`/Users/...`、`file://...`、Windows 盘符或 UNC 路径时，编译器会忽略路径并选择第一个服务端候选。候选已修正为在写 Intent 前返回 `edit_path_input_forbidden`，明确说明未读取路径、任务未改变；新集成断言证明该请求后 EditIntent 仍为空。修正后定向用例 `1 passed`，API 目标三组仍为 `54 passed`；本次仅变更 main.py 与既有集成测试中的相应断言。

第二次独立 Quality：`REJECT`。首个修正使用 Unicode `\w` 前置边界，中文紧贴 `/Users/...`、`/tmp/...` 或 `C:\...` 时不会命中。第三候选删除这一复杂正则；因本切片本就不支持从命令指定文件位置，任何 `/` 或 `\` 都在写 Intent 前 fail-closed。既有集成测试现在同时覆盖带空格 POSIX、中文紧贴 POSIX、`file://`、Windows 盘符和 UNC 五类输入，每次都断言 `edit_path_input_forbidden` 且 Intent 为空。修正后定向用例 `1 passed`，API 目标三组仍为 `54 passed`。

第三次独立 Quality：`ACCEPT`。重绑最终候选后，确认任何斜杠/反斜杠路径在 Intent 前 fail-closed，五类路径及无空格变体均无泄漏；API `54 passed`、Web `7 passed`、scoped ESLint 通过，其余 Workspace/Task/Timeline 同源、原子确认、无即时模型/FFmpeg 副作用、前端不推断成功和精确 PATCH `COEXISTING` 门禁无回退。该 `ACCEPT` 只适用于里程碑二的第一个 ReplaceClip 纵向切片，不代表全部自然语言编译、依赖图增量重算、精确控件迁移或旧任务退役已完成。

## 12. 里程碑二完整收口

本波次完成产品演进顺序中的第二阶段：素材智能、Plan 编译和自然语言局部改片。范围只覆盖视觉镜头的检索、替换、增量重算和版本恢复；人物、声音、字幕、封面、多语言与其精确编辑属于里程碑三，不在本波次扩张。

用户结果：用户用自然语言目标建立 Plan 时能够看到真实命中的工作区镜头；进入同一 CreationTask 后，既可以用自然语言也可以用精确控件替换选中镜头，两者写入同一种 `ReplaceClip` EditOperation 和新的 TimelineVersion，只使当前预览/成片失效；重新渲染得到可播放新预览，重启后状态一致，并可撤销到上一个镜头版本后再次渲染。

迁移负责人：主 Agent。独立 Quality 在候选冻结前保持 HOLD。

权威快照：

- Git identity：40f9ea98499f715845085f6e4aea224f6783d6c6 + 当前受保护脏树
- docs/product-requirements.md：e6c866367a05183cb95cea634221e5630f92e4862310ffcf1c2a89a6cc31c6a8
- docs/domain-model.md：2fd05d9bd6f464bde0f4cc746426fb47e81ab0624e2eb70d8b7eb8a9c350b443
- docs/system-architecture.md：b8247fd652e28b09c32e90700c7ddebd849f34a7c3bec4fce64725eaea8bdc1d
- DESIGN.md：3a7bc24d3e5ad9c2810a89260c1ddbaa7476528b0aea3f7a1a5c0ab9a83efe7a

### 12.1 正向交付

1. MediaRepository 提供同一工作区、已批准镜头的检索入口；结构化条件至少支持用途、标签和时长，自然语言查询从持久化的 description、transcript、system_tags 与 user_tags 计算可解释匹配，不引入必须配置的向量模型。
2. PlanCompiler 不只记录素材数量；Plan 固化本次目标实际命中的逻辑镜头引用、匹配理由和素材缺口。确认 Plan 后这些引用仍可重启恢复。
3. 自然语言镜头替换继续执行 preview → confirm；目标或约束明确时按同一检索规则选择候选，而不是固定取第一个候选。
4. 精确镜头替换、裁剪、排序、锁定和低置信镜头确认不得再直接覆盖 montage payload；它们必须产生类型化 EditOperation、追加 TimelineVersion，并复用同一 Workspace、对象校验、依赖失效与错误语义。无需语言理解的精确动作可以由服务端建立确定性提案并在一次明确用户动作中确认。
5. 每个已确认局部编辑只把真实下游 PreviewOutput/FinalOutput 标记为 invalidated；文案、已确认配音和未修改镜头保持 valid。重新渲染只复用这些有效输入并发布新的逻辑 MP4/SRT 引用和 TimelineVersion。
6. `POST /api/creation-tasks/{task_id}/timeline/undo` 创建可追溯的反向版本，不覆盖或删除历史；撤销后 montage 与 current_timeline_version_id 同时原子切换，当前预览/成片进入需要重新渲染状态。
7. 任务页同时展示真实素材命中理由、自然语言提案、精确镜头控件、失效范围、重新渲染状态、当前版本和撤销动作；所有反馈来自 API 持久事实。

### 12.2 迁移与退役矩阵

| 旧目标 | 当前消费者 | 替代路径 | 删除条件 | 状态 |
| --- | --- | --- | --- | --- |
| 精确镜头 PATCH 直接保存 montage payload | CreationTaskWorkspace、`update_creation_task_montage_clip` | 类型化 EditOperation + TimelineVersion | 所有精确镜头动作同源且旧直接保存测试消失 | RETIRED |
| 自然语言固定选择首个候选 | `preview_creation_task_edit` | 目标约束驱动的同源素材检索 | 无约束仍可推荐，有约束时证明确实命中 | RETIRED |
| Plan 仅保存素材数量与规则摘要 | `_creation_plan_preflight`、CreationPlan | 持久逻辑镜头引用和匹配理由 | Plan 重启后保持具体素材选择 | RETIRED |
| 旧绝对 output/subtitle 阻断编辑 | 迁移前任务 | 当前 ArtifactStore 逻辑引用重新渲染 | 代表性任务已迁入且新编辑链无绝对路径 | RETIRED |
| 无撤销入口 | 任务页与 API | 新反向 TimelineVersion | 真实撤销、重启恢复和再次渲染通过 | RETIRED |

所有目标必须达到 `RETIRED` 才能称里程碑二完成。保留相同 HTTP 路由作为受控边界映射是允许的，但路由内部不得继续承担第二套写入权威。

### 12.3 并行所有权

工作包 A：领域版本、精确编辑与撤销原子性

- Owned：apps/api/app/production.py、apps/api/app/database_migrations.py、apps/api/tests/test_production_foundation.py。
- 交付：通用确认事务、精确操作与撤销所需的持久化/状态不变量；不可覆盖历史，不新增旁路仓储。
- 禁止：main.py、schemas.py、Web、渲染器、权威文档。

工作包 B：任务页同源编辑与版本交互

- Owned：apps/web/src/components/CreationTaskWorkspace.tsx、apps/web/src/components/CreationTaskWorkspace.module.css、apps/web/src/components/__tests__/CreationTaskWorkspace.test.tsx、apps/web/src/lib/api.ts、apps/web/src/lib/types.ts。
- 交付：精确镜头动作调用新同源接口、当前版本/撤销/重新渲染反馈、素材匹配理由；不在客户端推断版本或成功。
- 禁止：API/Python、全局 CSS、其他页面、Electron、设计权威。

主 Agent：素材检索、PlanCompiler、接口集成、渲染闭环、活动合同、集成测试和退休证明。

### 12.4 验收与停止

- 同一真实 Workspace 完成：素材分析 → 自然语言/结构化命中同一镜头 → Plan 固化引用 → CreationTask → 自然语言替换 → 新版本 → 重新渲染新 MP4 → 撤销 → 再渲染；重启后每一步的当前版本和镜头一致。
- 用精确控件完成一次不同的镜头裁剪或替换，证明其 EditOperation、依赖失效和 TimelineVersion 与自然语言同源。
- 对比输入摘要和持久化依赖：配音、文案与未改镜头保持；只有实际依赖新 TimelineVersion 的预览/最终输出重新发布。
- 任务页在 1024×768、1180×800、1440×900 可见可达；ChangePreview、版本、撤销和渲染动作不得造成遮挡、逐字换行或空区域挤占首屏。
- 一次性运行相关 API/Web 测试和真实浏览器交互；随后冻结候选并由独立 Quality 同时审查正向闭环与五项退休目标。Electron/DMG、人物声音、字幕封面和平台能力不在本候选验收。
- 若现有数据结构无法在不建立第二事实源的条件下表达依赖状态，先 HOLD 并提交最小共享契约变更建议；不得用客户端状态或内存缓存代替。

### 12.5 冻结候选与主 Agent 证据

冻结时间：2026-08-31。候选身份为 Git `40f9ea98499f715845085f6e4aea224f6783d6c6` 上的受保护脏树；以下文件内容哈希共同定义里程碑二候选，不以未提交 Git SHA 冒充候选身份。

- API 运行代码：`artifact_store.py` `9abd043b5983ccbe9fd276591e5004bc0b41cf852e2af2db36292a23902738e7`；`database_migrations.py` `23e0260eb476426b5b4cc01268f0cd96ed0a402d84feace54d4a349db612d59f`；`main.py` `8829ecdd392719de683bfebb47d649a071f777417978aca65e3707f780759df5`；`montage.py` `d1591778811ac43298cdb802fae94ad8c4223e3f2218d1acfd2a9fb8f477e175`；`production.py` `48e14f1884daf99ba72c15c865bccbbb6fb2e3fd7c2f9fca7485dfe808d6546c`；`schemas.py` `a505d6adef702d5426d4e3eaaaecae63d33468c588222a4905ac0af079cadd73`；`task_runtime.py` `84321e65bb9a3fd416237e1ed82301f35457239d75cd8bd9d1ca743b65ab7ba7`；`video_jobs.py` `13e5183720c8997f59e662317e8282a3561ed081d7698b52a1d0e289c92e0cba`。
- Web 运行代码：`production/page.tsx` `f3f6a24664f304866a3cacea3350981ff5b935b715a87d622d0f182e7ad44df8`；`CreateProductionTaskForm.tsx` `ba90685258dc8df2c9466b6d4698f5f1d58b345bb74a39cdae4d5fa0ffada0c9`；`CreationTaskWorkspace.module.css` `5105b7a6ccc281278f49f090e430a6891c29d6223475d71d398a3f4a1ceaade6`；`CreationTaskWorkspace.tsx` `3a6e471a0f7fcabc9279c2ec0c733cfb5fdf446f86c5d078a3cc347da3aea4de`；`api.ts` `87e2948c68363b0b36ad2fae0259e47f6bcdf7c9c8f07ac501606f4325394f8b`；`types.ts` `5a56eacd2b4958e085c16aa4de93e6f33be2530c39302c389771ce40bd787d4d`。
- API 验证代码：`test_montage_pipeline.py` `2d614fe8fde134925eec0b8fd904a287e5643eb97afd338f2e73c78e92a6b0c6`；`test_production_foundation.py` `7918676608f8801483dbb35f989da97d60dad9e8af9f1d0981df5b3b95de0ee3`；`test_runtime_settings.py` `0657dc78565aada7679c39063bc08bdbe4ac54ca6a64bdfe92997e8fefb9fc97`；`test_video_jobs.py` `8fd47e95e1e3651db382fc6846a97d859a2454be9a5986c34f2016005a8dda54`。
- Web 验证代码：`app/__tests__/page.test.tsx` `12c15ebda09649400ea8bc8ad0fec13deff7c6ed4c3b6cdd94612e6ec82d81a0`；`production/__tests__/page.test.tsx` `b34491ec861cd2d34b8672a1da0622f148661ceef59aca2371de4e66f6f90b75`；`AppShell.test.tsx` `8f0c9a24bc291b1cdb6fddb03c39db2c22f761b5405d8fda8329467638090e86`；`CreateProductionTaskForm.test.tsx` `c68a0c0c4c16f930116d6f3b6e8048ca2b6a96ee33154372bdbbc3f3e4ceeb2e`；`CreationTaskTopicWorkspace.test.tsx` `4b8d65da2ace207a71f2ea65bc6bb37e4efda1fda44ba2670434e459d6c2263f`；`CreationTaskWorkspace.test.tsx` `b52978d4f235137c92e75c2a06a62f0da60fe4e256acc827f20b7bfb873e32d4`。

第一次最终独立 Quality 为 `REJECT`：代表性旧任务虽完成编辑和成片版本链，但其历史 Plan 的 `material_matches` 为空；单独完成 8-match 预检不能证明 Plan 确认、CreationTask 关联和重启持久化。候选代码没有因此修改，主 Agent 按同链路门禁重新执行真实用户链。

最终真实用户链使用同一工作区任务 `task_722d7b4dbf7c`：创建前预检返回并展示 8 个素材匹配、评分和理由；确认 Plan 并创建任务后，GET 中仍有 8 个逻辑 `asset:`/`segment:` 引用及理由。任务随后完成真实选题、文案、Qwen3-TTS 配音和 14 镜头故事板；12 个低置信镜头通过 `ConfirmClip` 逐一形成版本，到首个真实成片为版本 14。自然语言“换成另一个人物主镜头”预览列出替换前后、只失效逻辑 MP4/SRT，确认形成版本 15，重渲染形成版本 16；撤销形成版本 17；精确裁剪第二镜头 `0.0 → 0.2` 形成版本 18；再次真实 FFmpeg 渲染形成版本 19。

API 进程完整停止并重新启动后，任务仍为 `review`、版本 19、8 个 Plan 素材匹配、裁剪起点 0.2、Qwen 逻辑配音引用和逻辑 MP4/SRT 输出；两个输出端点均返回 200，页面刷新仍可播放并查看 SRT。下载产物经 `ffprobe` 为 H.264 1080×1920 + AAC、53.500 秒、13,119,201 bytes；SRT 为 1,399 bytes 且时间轴、中文内容非空。第一次 30 秒同链路任务因模型文案超过 Plan 上限被真实门禁阻断，没有伪造配音；第二次文字模型首次返回无效结构时同样未保存无效文案，重试后成功。这两项失败保留为失败语义证据，不作为通过项。

创建页已删除永远显示“尚未检查”的静态 Inspector，避免与真实预检状态冲突并占据工作区。任务页在 1024×768、1180×800、1440×900 的主动作、ChangePreview、版本与撤销均可见可达；新增任务文案的最小字号为 12px。

一次性回归：API 四组 `68 passed`；Web 核心五文件 `29 passed`，页面壳补充三文件 `9 passed`；`tsc --noEmit`、scoped ESLint、现代桌面设计合同、Python 编译和 `git diff --check` 全部通过。该证据是主 Agent 自检，只有独立 Quality 对上述同一内容哈希和新的同链路事实给出 ACCEPT 后，里程碑二才可进入完成状态。

第二次最终独立 Quality：`ACCEPT`。审查绑定合同 `67b41311f79e66984c916f6bbef1f3009a51234f47058fd8b7b46d8c4f1119d8` 和 §12.5 的 24 个冻结候选文件哈希；独立确认重启后的 `task_722d7b4dbf7c` 为 `review` / Timeline v19，Plan 持久保留 8 条有效逻辑素材命中及理由，Qwen 旁白、14 镜头、第二镜头 0.2 秒裁剪和 MP4/SRT 均可恢复；媒体为 H.264 1080×1920 + AAC、53.500 秒，SRT 有 14 个中文 cue。里程碑二据此进入 `ACCEPTED`；本结论不扩展到里程碑三、Electron/DMG 或全产品。

## 13. 里程碑三第一切片：分段声音与字幕同源编辑

本波次只交付一个纵向结果：用户选择一段配音或字幕后，可以用自然语言或精确控件提出修改，先看到变更和影响范围，确认后只重算对应声音/字幕及真实下游，并形成可撤销、可重启恢复的新 TimelineVersion。人物口型、封面和多语言继续 HOLD，待本切片成为可信上游后另行派发。

迁移负责人：主 Agent。独立 Quality 在候选冻结前保持 HOLD。

权威快照：

- Git identity：40f9ea98499f715845085f6e4aea224f6783d6c6 + 当前受保护脏树
- docs/product-requirements.md：e6c866367a05183cb95cea634221e5630f92e4862310ffcf1c2a89a6cc31c6a8
- docs/domain-model.md：2fd05d9bd6f464bde0f4cc746426fb47e81ab0624e2eb70d8b7eb8a9c350b443
- docs/system-architecture.md：b8247fd652e28b09c32e90700c7ddebd849f34a7c3bec4fce64725eaea8bdc1d
- DESIGN.md：3a7bc24d3e5ad9c2810a89260c1ddbaa7476528b0aea3f7a1a5c0ab9a83efe7a

### 13.1 冻结对象与接口

1. 在现有 ProductionRepository 内持久化 `NarrationSegment`；每个已确认文案段落拥有稳定 `id`、`source_section_id`、文字、逻辑音频引用、时长、采样率、模型、`voice_profile_id`、`voice_version`、语言和状态。不得建立第二套 narration 仓储。API 读模型额外投影服务端生成的 `audio_url`，客户端不得从逻辑引用推导文件位置。
2. `NarrationCue` 是同一时间线中的字幕段，使用稳定 cue id 并通过 `narration_segment_id` 关联声音段。字幕文本、开始/结束时间和换行属于 TimelineVersion 快照，不由客户端另存。
3. 新增两种类型化操作：`RegenerateNarrationSegment` 与 `UpdateSubtitleCue`。自然语言和精确控件都必须编译为这两种操作，经 EditProposal/ChangePreview 确认后原子写入 EditOperation、TimelineVersion 和当前 montage/narration 事实。
4. 精确编辑继续使用 `POST /api/creation-tasks/{task_id}/edits/exact`；自然语言继续使用 preview → confirm。客户端不推断失效对象或成功。
5. `RegenerateNarrationSegment` 确认后进入该段待生成状态；调用真实 Qwen3-TTS 只生成该段，随后拼合分段音频并更新相关 cue 时间、镜头时间和输出失效状态。其他声音段及其逻辑引用保持有效。
6. `UpdateSubtitleCue` 只改变该 cue 文本/换行，只使字幕和输出失效；配音和镜头保持有效。字幕字符上限由服务端契约决定，前端不得静默截断。
7. 撤销恢复完整 narration-segment、cue、montage 和依赖状态快照，不覆盖历史版本。
8. 分段试听固定使用 `GET /api/creation-tasks/{task_id}/narration/segments/{segment_id}/audio`；它只解析当前工作区已持久化的逻辑音频引用，不接受客户端文件路径。

### 13.2 迁移与退役矩阵

| 旧目标 | 当前消费者 | 替代路径 | 删除条件 | 状态 |
| --- | --- | --- | --- | --- |
| `/montage/cues/{cue_id}` 直接改 montage payload | Web 字幕 textarea、API 路由 | `UpdateSubtitleCue` EditOperation + TimelineVersion | Web 消费者归零；旧路由只可映射同源操作或删除；直接写测试消失 | RETIRED |
| 整条配音只有一个不可定位 artifact | narration response、montage plan/render | 持久化 NarrationSegment + 可拼合任务配音 | 每个文案段有稳定音频事实且重启恢复 | RETIRED |
| 修改一段声音必须重做整条配音 | narration generate | `RegenerateNarrationSegment` 局部模型调用和拼合 | 真实证据证明未改段复用且仅下游失效 | RETIRED |
| 自然语言只识别镜头替换 | EditCompiler | 可选择 narration/cue 的类型化提案 | 两种上下文真实 preview/confirm 通过 | RETIRED |
| 撤销只恢复 montage 镜头快照 | timeline undo | 恢复声音段、字幕段和 montage 的统一快照 | 声音/字幕编辑后撤销和重启恢复通过 | RETIRED |

只有五项均达到 `RETIRED`，同一候选真实生成分段 Qwen 音频、重渲染有效 MP4/SRT，并通过不存在证明，本切片才可进入 `MILESTONE_3_1_ACCEPTED`。

### 13.3 并行所有权

工作包 A：分段声音领域与原子版本

- Owned：apps/api/app/production.py、apps/api/app/database_migrations.py、apps/api/tests/test_production_foundation.py。
- 交付：NarrationSegment 的持久化与 workspace/task/script 谱系；两种新 EditOperation 类型；原子确认/撤销可保存和恢复 narration、subtitle、montage 快照；明确失效引用。
- 禁止：main.py、schemas.py、Web、Qwen/FFmpeg 调用、权威文档、旧 Episode/Batch。

工作包 B：任务页分段编辑体验

- Owned：apps/web/src/components/CreationTaskWorkspace.tsx、apps/web/src/components/CreationTaskWorkspace.module.css、apps/web/src/components/__tests__/CreationTaskWorkspace.test.tsx、apps/web/src/lib/api.ts、apps/web/src/lib/types.ts。
- 交付：选择配音段/字幕段、试听与可读状态、自然语言上下文、精确字幕保存全部调用同源编辑接口；删除 direct subtitle PATCH 客户端；反馈只来自 API。
- 禁止：API/Python、其他页面、全局 CSS、Electron、设计权威。

主 Agent：schemas/main 接口、EditCompiler、真实 Qwen 分段生成与拼合、montage 时间重排、集成测试、退役证明和候选冻结。

### 13.4 可观察验收与停止规则

- 用一个短任务完成：生成分段配音 → 选择一段输入“这句读慢一点” → 查看仅该段及下游失效 → 确认 → 真实生成新段 → 拼合 → 重渲染；未改段的 logical ref/hash 保持。
- 精确修改一个字幕 cue 并换行，证明配音与镜头不变、SRT/MP4 更新；自然语言“把这句字幕改成……”得到同一种 `UpdateSubtitleCue`。
- 撤销最近一次声音或字幕编辑后，当前版本、对应段、SRT 与渲染状态一致；API 进程重启后仍一致。
- Qwen、FFmpeg 或模型输入缺失时明确 BLOCKED，不用整条旧音频、系统音、静音、假 WAV 或客户端状态回退。
- UI 在 1024×768、1180×800、1440×900 中当前选中对象、变更预览、唯一主动作和失败修复可见；不得新增重复标题、静态解释大块或空 Inspector。
- 子线程只跑 owned 最小测试；主 Agent 合流后一次跑目标 API/Web、真实短音频/渲染和可见验收。相同根因连续两次失败且没有新产品证据时 HOLD。

### 13.5 冻结候选与同一任务证据

候选身份：Git `40f9ea98499f715845085f6e4aea224f6783d6c6` + 下列受保护脏树文件；在独立 Quality 结论前只能称 `MILESTONE_3_1_CANDIDATE_FROZEN`。

| 文件 | SHA-256 |
| --- | --- |
| `apps/api/app/production.py` | `63cae5c138b336547e901debeb4bd00541d5b07c7612425faf84261fd3b61961` |
| `apps/api/app/database_migrations.py` | `049cb1b0c9893de21654391a6fb76e61163fc619826c3962c8e1969d04d3c273` |
| `apps/api/app/schemas.py` | `9fd0d93c9e3439d6c73a667990c9408c6e190e063998c8ac5f1831d54ae86dee` |
| `apps/api/app/montage.py` | `0ec580904b04d5b78dc088fef3a20d20396728577a5704bd7a7920691c4a5db5` |
| `apps/api/app/narration.py` | `5a323560dec8a3c06a90403de6d14d2aa43fbfdd563d2eee745beb9bdf132513` |
| `apps/api/app/main.py` | `795f52f91a2c3b913e5198d42761fe610fb8eef372af894422ebf83d7a9cf65b` |
| `apps/api/tests/test_production_foundation.py` | `e1ac47ed9ada46a0e59b9482e3bc14d32d78eb9aea18bba482bb8fc9b7ce745b` |
| `apps/api/tests/test_montage_pipeline.py` | `a96e4f73f270f633887e2b80281b1bc6d183a972387a601968c44db5e6050b04` |
| `apps/web/src/components/CreationTaskWorkspace.tsx` | `3974212b06c29ca0aab5c5678baf9e13e5b737429a5865f6fd78882ee62b518e` |
| `apps/web/src/components/CreationTaskWorkspace.module.css` | `54f6601219c4c416d4bbfccf0d0279933b861e96ba9c4b80beea71c2e78703a6` |
| `apps/web/src/components/__tests__/CreationTaskWorkspace.test.tsx` | `09e84f1caa753b459e1332d0b8fd472221f0e210c7026c4c2dc917507f78dee4` |
| `apps/web/src/lib/api.ts` | `ef32fa3259e7b176f7f3380f884e5a14f902df0d570cc8766cb5ef168534c953` |
| `apps/web/src/lib/types.ts` | `fe8944967130280d86a75c40c890520403d31740410e33f8d7939142509b2176` |

同一真实任务 `task_45c4865fc904` 的可复核事实：

- DeepSeek 选题和文案均真实执行。初次八段文案超过 45 秒时明确失败；压缩并重新确认六段文案后，仓储保证同一任务只有 `script_ea6daecc4b76` 处于 confirmed，旧文案降为 draft。
- Qwen3-TTS `qwen3-tts-12hz-0.6b-base-8bit` 生成六个持久 `NarrationSegment`，声音 `voice_cc/v1`，拼合音频 26.535458 秒。选择首段输入“这句读慢一点”后编译为 `RegenerateNarrationSegment`；只替换首段逻辑音频引用并更新时长为 5.495458 秒，另外五段引用逐项保持不变。
- 精确编辑 cue 2 保存显式换行；选择 cue 3 输入“把这句字幕改成：信息和知识都留在本地。”后编译为同一种 `UpdateSubtitleCue`。两次修改都保持六段配音与六个镜头有效，只使 SRT/MP4 失效。
- 字幕修改后生成新 TimelineVersion；真实撤销恢复前一版本，API 进程重启后任务、当前版本、已确认文案和六段声音均保持。随后重新应用字幕修改并渲染当前候选。
- 当前 Timeline 为 `timeline_job_768ab13238214cbd91ccd9c7b8252ba2`，序号 18，任务停在 `review/waiting_for_user`。MP4 为 H.264 1080×1920 + AAC 48 kHz，时长 26.633008 秒、6,743,609 bytes、SHA-256 `da6733ef446cf546b7979ffb4419c4045e152ea15b0c2f2cfa45ee185d3ebac2`；SRT 有六个 cue、保留 cue 2 的两行文本并包含 cue 3 的自然语言改写，SHA-256 `02d6a3e248238b2700ff482535e88102e81ef9eea34fe60fd9eeffe35c9d0591`。
- 渲染输入摘要包含 `creation-task-montage-render/v2`，字幕渲染合同改变后不会误复用旧缓存产物。
- 任务页在 1024×768、1180×800、1440×900 均无水平溢出；小尺寸隐藏 Inspector，中栏可独立滚动；1440×900 右栏随当前选中配音段、字幕段或镜头显示真实参数、证据和影响，不再是空静态侧栏。
- 冻结前最小回归：API 目标测试 54 passed；任务页测试 9 passed；TypeScript、目标 ESLint、Python `py_compile` 与 `git diff --check` 通过。未构建 Electron 或 DMG，本结论不扩展到人物口型、封面、多语言或发布。

### 13.6 独立 Quality 结论

独立 Quality 对合同 SHA-256 `9d3d9580eba303306b6133336f620809b15f4b35a798a9ad14d4022d1a5d6c65` 及 §13.5 的 13 个冻结文件哈希进行只读复核，结论为 `ACCEPT`：哈希一致；API 54 项与 Web 9 项测试、TypeScript 和目标 ESLint 通过；重启后真实任务保持 `review/waiting_for_user` 和 Timeline v18；时间线证据证明只改变首段声音引用而其余五段保持，字幕编辑均为 `UpdateSubtitleCue`，撤销恢复统一快照；Web 旧字幕直接写入消费者归零，遗留 cue PATCH 只映射同源类型化操作，客户端只使用服务端 `audio_url`；渲染摘要包含 v2 合同；Inspector 使用当前选中对象的真实 API 参数；最终 MP4/SRT 的媒体信息、哈希、六个中文 cue、显式换行和自然语言改写均与冻结记录一致。

里程碑 3.1 据此进入 `MILESTONE_3_1_ACCEPTED`。本结论不扩展到 Electron/DMG、人物口型、封面、多语言或发布，也不代表完整里程碑三已完成。
