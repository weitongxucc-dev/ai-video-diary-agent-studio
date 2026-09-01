# 系统架构

本文件定义 AI 视频日记的运行模块、跨进程责任、外部能力适配和关键数据流。产品范围见 product-requirements.md，领域对象与状态见 domain-model.md。

## 1. 架构目标

系统围绕一个 Workspace 和一条 CreationTask 事实链运行，满足：

- 本地优先，网络能力可插拔；
- 原始资产不可变，派生产物可追溯；
- 自然语言与精确编辑共享同一领域操作；
- 局部编辑按依赖图增量重算；
- 模型可以更换，领域状态不依赖供应商；
- 进程中断后任务可恢复；
- 不可用能力诚实失败，不使用隐藏 fallback；
- Web 不能绕过桌面权限提交任意本地路径。

## 2. 运行拓扑

### 2.1 Desktop Shell

Electron 桌面壳负责：

- 应用生命周期和窗口；
- 选择文件、选择目录和保存成片的原生对话框；
- 把用户选择转换为受控文件句柄或工作区引用；
- 启动、监督和终止 API、Worker 及必要的本地模型进程；
- 安全存储 API 密钥和平台凭据；
- 打开文件、播放器和系统通知；
- 退出时清理子进程、端口和临时资源。

桌面壳不实现产品领域规则，也不接受 Renderer 提交任意源绝对路径。

### 2.2 Web Renderer

Web 负责用户界面和短生命周期交互状态：

- 首页目标与预检；
- 素材、内容、人物声音、模板和自动任务管理；
- CreationTask 阶段工作区；
- 播放器、时间线、字幕和属性检查器；
- 自然语言命令输入、影响预览、确认和撤销；
- 运行状态、阻断原因和修复动作。

Web 不把 localStorage、React state 或聊天消息当作业务事实源。

### 2.3 API

API 是同步领域入口，负责：

- Workspace 和对象权限校验；
- Knowledge、Source、Media、Profile、Plan、CreationTask、Template 和 Automation 的仓储访问；
- 命令校验和状态转换；
- 自然语言意图的上下文组装与类型化提案；
- 提案确认后写入 EditOperation 和 TimelineVersion；
- 能力状态、失败证据和修复动作；
- 向 Worker 提交可恢复作业。

API 不执行长时间媒体渲染，也不建立 Episode、Batch 或内存 fallback。

### 2.4 Worker

Worker 执行可恢复的长任务：

- 来源抓取和内容解析；
- 视频转写、分镜和多模态分析；
- 选题、文案、配音、口型、字幕和封面生成；
- 时间线预览、增量渲染和最终编码；
- 平台发布和数据拉取；
- 失败重试、进度和产物登记。

每个作业必须幂等或带明确的重试边界，并把状态、失败原因和产物引用持久化。

### 2.5 Local Model Runtimes

语音、转写、视觉、向量或其他本地模型可以是独立进程。Supervisor 负责：

- 启动和健康检查；
- 模型文件和运行环境检测；
- 并发、显存或内存限制；
- 标准化能力状态；
- 进程退出和资源清理。

## 3. 核心服务

### 3.1 ProductionRepository

ProductionRepository 是 Plan、CreationTask、Script、Narration、Timeline、Output 和任务事件的唯一持久化入口。旧 Episode、Batch、repo.today 或模板成功 fallback 不得进入新写链路。

### 3.2 ContentRepository

管理 KnowledgeItem、SourceConnector、SourceItem、内容版本和 ContentUseState。知识库与信息源可以共享检索基础设施，但必须保留不同来源语义。

### 3.3 MediaRepository

管理 MediaAsset、Clip、标签、分析结果、代理文件和 UsageRecord。文件系统路径只在受信任的本地进程中解析，对 Web 和领域层暴露逻辑引用。

### 3.4 ProfileRepository

管理 PersonProfile、VoiceProfile、PronunciationEntry 和版本。用户导入的档案默认可在当前 Workspace 使用；能力是否可执行由对应模型和运行环境单独决定。

### 3.5 AssetIntelligenceService

把长视频和多模态内容转化为可检索生产单元：

- 转写、说话人和静音识别；
- 镜头边界和关键帧；
- 人物、产品、场景、动作、物体、屏幕内容和文字；
- 质量、画幅、安全裁切和可复用性；
- 索引、标签、缺口和低置信复核。

分析结果写入 MediaRepository，不能只存在于模型响应或缓存。

### 3.6 RetrievalService

按任务规模组合：

- 结构化字段和全文检索；
- 时间范围、标签和来源过滤；
- 可选向量索引和语义重排；
- Workspace、ContentUseState 和任务上下文约束。

小规模数据不强制依赖向量模型；启用向量检索时，索引必须可以从权威内容重建。

### 3.7 PlanCompiler

把用户目标、模板和当前能力编译为 Plan：

- 读取可用内容、素材、人物声音和模型状态；
- 形成内容、视觉、声音、字幕、封面和平台策略；
- 标记确认节点、缺失条件和可执行步骤；
- 在用户确认前不启动有成本或外部副作用的作业。

PlanCompiler 的输出是领域对象，不是聊天回答。

### 3.8 EditCompiler

把自然语言与当前任务上下文编译为 EditIntent、EditProposal 和类型化 EditOperation：

1. Web 发送命令、当前阶段、选中对象和播放位置。
2. API 从 ProductionRepository 读取权威任务和时间线版本。
3. 文字模型只负责理解意图、补全约束和生成候选提案。
4. EditCompiler 校验范围、对象类型、ContentUseState、能力和副作用。
5. Web 展示操作、影响范围、失效依赖和预览差异。
6. 用户确认后，API 写入 EditOperation 并调度 Worker。
7. 完成后生成 TimelineVersion，CreationTask 指向新版本。

文字模型不能直接访问任意文件路径、执行 Shell 或构造任意 FFmpeg 命令。媒体操作由受控、类型化的执行器生成。

### 3.9 DependencyGraphService

维护 ScriptSegment、NarrationSegment、SubtitleCue、TimelineItem、PreviewOutput 和 FinalOutput 的依赖。

每次编辑计算：

- 直接变更对象；
- 需要失效的下游节点；
- 可以保持 valid 的已确认产物；
- 需要重新预览或最终渲染的范围。

Worker 只处理 invalidated 节点。输入摘要未变的 valid 产物可以复用。

### 3.10 QualityEvaluationService

在预览和输出阶段执行可解释检查：

- 音视频流、时长和文件完整性；
- 音量、静音、字幕时间和遮挡；
- 画幅、安全区、裁切和平台规格；
- 文案事实来源、重复、节奏和语言一致性；
- 模型或媒体能力不可用；
- 用户偏好与模板约束。

自动评分只能提出问题和建议，不能替代用户对内容质量的最终确认。

### 3.11 PreferenceService

从用户明确选择、接受、撤销和模板保存中提取可解释偏好。偏好用于排序和默认值，不能静默覆盖 ContentUseState、事实来源或外部副作用确认。

## 4. 能力接口

外部模型和平台通过窄接口适配，领域层不直接依赖供应商 SDK。

建议能力接口：

- TopicGenerator；
- ScriptGenerator；
- EmbeddingProvider；
- VisionAnalyzer；
- SpeechToTextProvider；
- VoiceCloneProvider；
- LipSyncProvider；
- ImageGenerationProvider；
- MediaRenderer；
- PublishConnector；
- AnalyticsConnector。

每个实现统一返回：

- capability_state：available、degraded 或 unavailable；
- execution_id；
- output_refs；
- usage 和成本信息；
- failure_reason、impact、remediation。

允许的降级必须是产品明确支持的替代选择，并由用户知情；不得在失败时偷偷切回旧事实源、旧模型或伪造产物。

## 5. 关键数据流

### 5.1 内容与素材导入

原生选择器或 SourceConnector
→ Desktop/API 建立 Workspace 引用
→ ContentRepository 或 MediaRepository
→ Worker 分析与索引
→ 可检索 KnowledgeItem、SourceItem、MediaAsset 和 Clip

### 5.2 新任务

首页目标或模板
→ PlanCompiler 预检
→ Plan 预览
→ 用户确认
→ CreationTask
→ 选题与文案确认
→ 配音、故事板、时间线、字幕和封面
→ PreviewOutput

### 5.3 自然语言局部修改

命令 + 选择上下文
→ EditCompiler
→ EditProposal
→ 用户确认
→ EditOperation
→ DependencyGraph invalidation
→ Worker 增量生成或渲染
→ TimelineVersion
→ 新预览

### 5.4 精确编辑

时间线控件
→ 类型化 EditOperation
→ 与自然语言相同的校验、依赖失效、版本和渲染路径

### 5.5 本地输出

当前 TimelineVersion
→ MediaRenderer
→ MP4、字幕和产物证据
→ ffprobe 或等价校验
→ 客户端播放
→ Desktop 原生保存对话框

### 5.6 发布与监控

FinalOutput + PlatformVariant
→ 发布前预检
→ 用户确认目标账号和外发内容
→ PublishJob
→ PublishConnector
→ 平台状态
→ AnalyticsConnector 定时拉取
→ AnalyticsSnapshot

## 6. 存储

### 6.1 数据库

SQLite 或等价本地数据库保存结构化领域对象、状态、版本、事件、依赖和逻辑文件引用。Repository 是唯一写入口，不允许各模块维护结构不同的旁路数据库。

### 6.2 文件存储

Workspace 文件区建议区分：

- originals：不可变原始资产；
- proxies：预览代理；
- analysis：转写、关键帧和索引派生数据；
- artifacts：配音、字幕、口型、封面和渲染结果；
- outputs：用户可检查的最终输出；
- temp：可回收临时文件。

数据库保存逻辑引用和摘要。真实路径由本地受信任层解析，迁移 Workspace 时可重新绑定。

### 6.3 任务与事件

长任务状态必须可恢复。进度事件用于界面展示，但最终状态以持久化任务和产物为准。进程崩溃后不能把 running 自动解释为 completed。

## 7. 安全与外部边界

- 所有 API 写入校验 Workspace 一致性。
- Renderer 只能使用 Desktop 或 API 返回的受控引用。
- API 密钥和账号凭据使用系统安全存储。
- 云端模型调用明确展示数据类型、供应商和成本。
- ContentUseState 约束素材用途；它与文件访问权限、账号连接和平台发布确认不是同一概念。
- 删除、覆盖、付费调用和平台发布保留审计事件。
- 日志不得记录密钥、完整凭据或不必要的用户内容。

## 8. 迁移与退役

产品迁移采用有限状态机：

LEGACY_INVENTORIED
→ NEW_AUTHORITY_FROZEN
→ VERTICAL_SLICE_IMPLEMENTED
→ REAL_CLIENT_VERIFIED
→ OLD_PATH_DISABLED
→ OLD_PATH_REMOVED
→ ABSENCE_VERIFIED
→ MIGRATION_ACCEPTED

旧路径只有在入口、消费者、写接口、fallback、样式和保护旧行为的错误测试全部移除后才算 RETIRED。新界面存在、API 可追踪、构建成功或静态测试通过都不能单独证明迁移完成。

当前实现状态不记录在本文。代码、Git、运行服务和活动任务合同分别提供实时证据。
