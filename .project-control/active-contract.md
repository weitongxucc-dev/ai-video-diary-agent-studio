# M3.2 人物出镜与真实口型迁移合同

## 1. 合同状态

- 状态：ACCEPTED
- 阶段：MILESTONE_3_2_ACCEPTED
- 模式：MIGRATE
- 纵向负责人：主 Agent
- 基线：`e37a79c629a3c1f7b8b4e582d05de8a0dd140938`
- 分支：`codex/milestone-3-2-avatar-lipsync`
- 验收候选：`f43580f`
- 建立日期：2026-09-01

本合同是当前唯一活动执行合同。被替换的长合同只保留在 Git 历史与已合并的 PR #41 中，普通任务不得读取或复制。

当前产品权威仍是：

- `docs/product-requirements.md`
- `docs/domain-model.md`
- `docs/system-architecture.md`
- `DESIGN.md`

本合同只约束 M3.2 的实现和验收，不复制上层产品规则。

## 2. 本阶段唯一用户结果

在同一个 `CreationTask` 中，用户选中一个已配音的时间范围，选择当前人物版本，生成真实人物出镜口型片段；该片段进入当前 `TimelineVersion`，可预览、撤销、重启恢复，并可通过自然语言或精确操作只重做所选片段。

代表性命令：

- “这一段换成 cc 出镜”
- “这一段表情更自然”
- “这里只重做口型，其他都不要动”

执行前必须展示理解结果、目标范围、人物版本、失效产物和预览差异；确认后才写入新版本。

## 3. 已核实的当前事实

- M1–M3.1 已合并到 `origin/main`，PR #41 的合并提交为本合同基线。
- Chinese-CLIP 和 Qwen3-TTS 本地模型已真实就绪。
- `AvatarAndLipSyncProvider`、本地子进程协议、预览运行记录和失败持久化边界已经存在。
- MuseTalk v1.5 的 macOS arm64 本地运行环境、官方模型文件和受控 worker 已配置；任务能力只在运行时与模型文件完整时报告可用。
- 当前 `CreationTask` 已有持久化 `PersonRender` 基础记录、受控媒体读取接口，并可由 `ChangePersonSegment` 写入 `TimelineVersion`。
- 2026-09-02 已用 `person_cc:v1 + voice_cc:v1` 的真实任务配音分段生成 5.496 秒 MP4；`ffprobe` 证明包含 H.264 544×960 视频与 AAC 24kHz 单声道音频，任务媒体接口下载结果与产物 SHA-256 一致。
- 长人物参考会在 worker 内按目标配音时长截取，避免为一个短片段预处理整段参考视频。
- 2026-09-02 已在真实任务 `task_45c4865fc904` 上输入“这一段换成 cc 出镜”，生成并确认 `ChangePersonSegment`，将 `person_render_63cc8446ce609f2a109812ed` 写入第一段镜头；撤销后恢复原镜头，再次确认后复用已验证人物片段。
- 2026-09-02 已对同一真实任务执行“只重做口型”：生成新 `person_render_22e31cf16e8906b414280d91`，与旧人物片段的字节大小和 SHA-256 均不同。
- 新人物片段已绑定独立的 succeeded `JobRun`、`ArtifactRecord(kind=person_render)`、succeeded `TransformRun` 和 5 条上游 `DependencyEdge`。
- 真实撤销后旧 `PersonRender` 恢复；再次应用时提案通过 `reuse_person_render_id` 精确恢复最新人物片段，没有再次运行 MuseTalk。
- 当前成片为 1080×1920、26.633 秒、6,943,350 字节的 H.264 + AAC MP4，并保留烧录中文字幕和 6 段独立 SRT；任务处于 `review/waiting_for_user`。
- API 重启后，当前时间线、新人物片段、成片、SRT 和实时能力投影均已恢复。
- 浏览器实测证明人物命令先出现变更预览，确认前不执行；右栏区分“模型能力”与“片段状态”，并展示真实模型与固定参数。
- 1024×768、1180×800 与主机默认尺寸无横向溢出或不可达按钮；相关 API 测试 64 项、Web 交互测试 10 项、TypeScript、ESLint、Next.js 生产构建均通过。

因此，M3.2 在候选 `f43580f` 上已完成真实人物口型生成、类型化局部修改、精确重生成、撤销/恢复、谱系、最终混剪和 UI 门禁，独立审查结论为 `ACCEPT`，剩余 P0/P1 为零。

## 4. 正向交付契约

### 4.1 唯一事实源

扩展现有 `ProductionRepository`，新增任务内的 `PersonRender` 记录。不得建立第二套人物任务、时间线、媒体仓储或前端内存事实源。

`PersonRender` 至少记录：

- `id`、`workspace_id`、`task_id`；
- 所属时间线片段或目标时间范围；
- 人物档案 ID 与版本；
- 配音片段 ID、音频逻辑引用与输入摘要；
- 作为人物驱动输入的工作区素材引用；
- 模型 ID、动作、表情和口型偏移参数；
- 产物逻辑引用、时长、对齐证据；
- `queued/running/succeeded/failed/unavailable` 状态；
- 失败原因、影响、修复动作与重试信息。

产物文件继续由 `ArtifactRecord` 管理，依赖关系继续由 `DependencyEdge` 管理。

### 4.2 依赖与增量重算

最小依赖链为：

`PersonProfileVersion + NarrationSegment + VisualSource + Parameters`
→ `PersonRender`
→ `TimelineItem`
→ `Preview/FinalVideo`

局部修改人物、表情、动作或口型偏移时，只使目标 `PersonRender`、引用它的时间线片段和最终预览失效；不得重做无关文案、配音、字幕或镜头。

配音片段改变时，仅使依赖该配音的 `PersonRender` 及其下游失效。

### 4.3 统一操作路径

自然语言与精确控制必须编译到同一组类型化操作：

- `ChangePersonSegment`
- `RegeneratePersonRender`

操作通过现有 `EditIntent` → `EditProposal` → `EditOperation` → `TimelineVersion` 路径确认和持久化，支持撤销与重启恢复。

### 4.4 任务接口

任务级接口必须位于现有 CreationTask 路径下，至少覆盖：

- 读取当前人物片段和能力状态；
- 预览人物片段变更；
- 确认类型化变更；
- 生成或显式重试真实 `PersonRender`；
- 安全读取任务内人物视频产物。

客户端不得提交任意源文件绝对路径。

### 4.5 任务界面

- 中栏以视频预览和时间线片段为主，不增加重复标题、解释性大卡片或空白占位。
- 用户选择人物片段后，右栏显示人物版本、声音版本、语言、动作、表情、口型偏移、能力状态和唯一修复动作。
- 能力不可用时按钮禁用并说明原因；不得显示看似可执行但只会失败的主按钮。
- `>=1280px` 使用右侧 Inspector；`1024–1279px` 使用抽屉，不挤压主工作区。

## 5. 产品边界

- 用户放入当前工作区的素材、人物与声音默认可用于本地创作；本阶段不重新引入逐条授权证明表单作为任务门禁。
- 工作区隔离、文件访问、云端外发、付费调用和删除确认仍是有效安全边界。
- 首版只验收中文、单人物、单片段局部重做。
- AI 封面、多语言、人物切换转场、模板、自动任务、发布与监控不进入 M3.2。
- 不在本阶段重新设计全局导航或无关页面。

## 6. 必须退役

| 旧对象或行为 | 替代结果 | 完成状态 |
| --- | --- | --- |
| 档案页口型预览被当作任务成片能力 | 任务内 `PersonRender` | RETIRED |
| API 固定不可用且没有可配置真实 provider | 显式配置、校验和报告的本地 provider | RETIRED |
| 人物修改只改变界面状态 | 类型化操作和新 `TimelineVersion` | RETIRED |
| 局部人物修改触发整条任务重做 | 精确依赖失效和增量重算 | RETIRED |
| 无真实模型时仍提供可点击生成入口 | 禁用并显示唯一修复动作 | RETIRED |
| 任务流继续强制逐条授权证明 | 工作区输入默认可创作 | RETIRED |

状态只能按 `ACTIVE → COEXISTING → MIGRATED → RETIRED` 推进；只有 `RETIRED` 才计入完成。

## 7. 真实运行门禁

进入人物口型实现前必须选定一个许可明确、可在本机 macOS arm64 运行、能接收受控 JSON 请求并输出有效 MP4 与对齐证据的真实引擎。

只有同时满足以下条件才可将能力标记为 available：

- 运行时、worker 和固定版本模型均存在；
- 模型来源、版本、许可、文件清单和校验值已登记；
- 对受控输入生成了非空有效 MP4；
- `ffprobe` 证明视频流、时长和编码有效；
- 输出路径位于任务允许的产物根目录；
- provider 返回真实时长与口型对齐证据。

缺失任一条件时状态为 `BLOCKED` 或 `UNAVAILABLE`，不得建立回退成功路径。

## 8. 同一候选验收

M3.2 只有在同一 Git SHA 上同时满足下列条件才完成：

1. 一个真实 `CreationTask` 已有确认文案、真实中文配音和可编辑时间线。
2. 选中一个片段并输入“这一段换成 cc 出镜”。
3. 提案准确展示目标、人物版本、参数、失效范围与不会改变的内容。
4. 用户确认后生成真实人物口型 MP4，并保存 `PersonRender`、`ArtifactRecord`、`DependencyEdge`、`EditOperation` 和新 `TimelineVersion`。
5. 仅目标人物片段及真实下游被重算，其他配音、字幕和镜头摘要保持一致。
6. 客户端可播放人物片段与合成预览；重启后状态和产物仍可恢复。
7. 撤销后恢复前一时间线版本。
8. 最终 MP4 保留音频和字幕，并通过 `ffprobe`。
9. 1024×768、1180×800 与主机最大尺寸无裁切、遮挡、逐字换行或不可达按钮。
10. 第 6 节旧行为均达到 `RETIRED`，并有源码、路由和 UI 消费者的不存在证明。

源码可追踪、fixture、mock、单元测试和静态截图只能作为辅助证据，不能代替真实模型输出和真实点击。

## 9. 停止规则

- 尚未选定或安装真实口型引擎时，停止在 `MILESTONE_3_2_PREFLIGHT`，不得堆叠空 UI。
- 同类失败连续两次且没有新产品证据时进入 HOLD，先复核事实源和工具。
- 发现共享产品、领域、架构或设计权威有误时先 `HOLD_FOR_AUTHORITY_UPDATE`，更新唯一权威后再继续。
- 测试环境阻断只可记为 `BLOCKED/UNKNOWN`，不得记为 PASS。
- M3.2 完成前不启动封面、多语言、模板、自动任务、发布或新 DMG 打包。
