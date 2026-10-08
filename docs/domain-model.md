# 领域模型

本文件定义 AI 视频日记的业务对象、关系、状态和不变量。界面文案以 DESIGN.md 为准，模块责任以 system-architecture.md 为准，产品范围以 product-requirements.md 为准。

## 1. 聚合边界

Workspace 是顶层隔离边界。知识、来源、素材、人物、声音、任务、模型配置、模板、自动任务和产物都必须归属一个明确 Workspace。

CreationTask 是视频生产的唯一业务聚合根。选题、文案、配音、时间线、字幕、封面、输出和发布状态都通过 task_id 关联到同一条任务，不得再以 Episode、Batch、聊天记录、localStorage 或内存对象建立并行事实源。

## 2. 内容与来源

### 2.1 KnowledgeItem

用户已有或主动确认保存的知识内容。

关键字段：

- id、workspace_id、title、content_type；
- body_ref 或 file_ref；
- source_ref、source_version；
- use_state；
- version、created_at、updated_at。

### 2.2 SourceConnector

外部内容来源的连接配置，例如网站、RSS、Product Hunt、YouTube、B 站、抖音或小红书。

关键字段：

- id、workspace_id、connector_type、display_name；
- connection_state、schedule_policy；
- capability_state、last_sync_at；
- config_ref。

账号凭据只保存安全引用，不进入普通领域字段。

### 2.3 SourceItem

某个来源在某次抓取中获得的原始条目。

关键字段：

- id、workspace_id、connector_id；
- external_id、canonical_url；
- content_type、raw_ref；
- fetched_at、source_version；
- use_state、saved_knowledge_item_id。

SourceItem 可以被确认保存为 KnowledgeItem，也可以把媒体部分登记为 MediaAsset；两种转换都必须保留来源关系。

### 2.4 ContentUseState

用户给入当前 Workspace 的内容默认是 available。可选限制状态为：

- available：可用于当前工作区的人工和自动创作；
- reference_only：只可用于理解和事实参考，不直接进入成片；
- excluded：不得进入检索、生成或成片；
- not_for_automation：可由用户明确选择用于人工创作，不进入自动选片候选（含定时任务）；不自动移除已有时间线引用；
- not_for_publish：可制作预览，不可进入发布产物。

这些状态由用户或明确的导入规则设置。模型可以提示风险，但不得自行把 available 降级、升级或绕过限制。

内容来源与版本是追溯字段，不是默认要求用户填写的逐条权利证明。

## 3. 素材

### 3.1 MediaAsset

用户导入、来源转换或生成流程产生的媒体资产。

关键字段：

- id、workspace_id、media_type；
- original_ref、proxy_ref；
- duration、width、height、codec；
- source_ref、use_state；
- analysis_state、review_state；
- created_at、updated_at。

原始资产不可变。转码、裁剪、抽帧和生成结果必须创建派生产物，不覆盖 original_ref。

### 3.2 Clip

MediaAsset 中可独立检索、预览和使用的时间片段。

关键字段：

- id、asset_id、start_ms、end_ms；
- transcript_ref、speaker_ref；
- system_tags、user_tags；
- quality_score、confidence；
- review_state。

### 3.3 标签与使用记录

首页素材标签数量按 MediaAsset 去重：取该素材的资产用户标签与片段系统/用户标签，同名标签在同一素材内只计一次。点击标签使用相同标签集合的精确匹配，并在服务端分页前筛选；片段数量保留其原语义，不作为素材数量。统计与详情限定当前工作区的现有可读范围，不扩展成员可见性。

- SystemTag：模型或规则产生的主体、人物、产品、场景、动作、物体、画幅和质量标签。
- UserTag：用户创建或修正的标签。
- TagFolder：用户管理标签的分组。
- UsageRecord：记录素材或片段被哪条任务、哪个时间线版本、以什么用途使用。

系统分析不等于用户确认，但 review_state 不构成全部自动分析素材的统一使用门槛。低置信分类、关键事实疑点和影响成片的素材缺口应显式交由人工选择；使用范围始终以 ContentUseState 为准。UserTag 和人工事实纠正保留对象与时间范围，再分析只能替换系统结果，不能清除人工结果。

分析证据必须保留来源模态、原资产时间区间、分析模型/规则版本与用户纠正来源，区分观察与推断。用户素材事实纠正不得被重新分析覆盖；任务内偏好与工作区长期偏好分开，偏好可取消且不能覆盖内容使用限制。具体持久化扩展沿既有 Repository 和版本迁移，不创建旁路素材库。

### 3.4 BusinessObject 与素材关联

BusinessObject 是当前 Workspace 内用户管理的业务名称条目，类型为产品、菜品或人物；最小字段为 id、workspace_id、kind、name。人物业务名称与 PersonProfile 的出镜形象/声音用途分开，关联不创建出镜或声音档案，不改变生成授权。名称条目可由用户选择已有或新增后显式关联到素材；素材保存对象 ID 关联，可回读名称与类型，不把名称写成系统事实标签。关联的素材与名称条目必须属于同一工作区。只修改用户明确选择的素材关联，保留其文字说明、公共背景、分析及人工纠正；不把一条关联默认为整个导入批次的共同事实。不包含 CRM、详细资料管理或跨成员共享改造。

## 4. 人物、声音与语言

### 4.1 PersonProfile

可在任务中选择的人物形象档案。

关键字段：

- id、workspace_id、display_name；
- version、reference_asset_ids；
- supported_shots、supported_backgrounds；
- use_state、capability_state。

用户导入的人物参考默认可用于当前工作区，不要求每个版本重复填写权利人和证明。用户设置的 use_state 必须被任务遵守。

### 4.2 VoiceProfile

可用于配音或人物口型的声音档案。

关键字段：

- id、workspace_id、display_name；
- version、reference_audio_ref、reference_text；
- languages、pronunciation_dictionary_id；
- model_ref、use_state、capability_state。

声音克隆必须使用真实参考音频和已配置模型。模型或运行环境缺失时返回明确不可用状态，不得回退到系统声音、静音或伪造音频。

### 4.3 PronunciationEntry

特定词语在语言、人物或声音版本下的发音规则。

### 4.4 ExternalConsent

只有发生云端数据外发、平台账号连接、平台发布或其他需要确认的外部副作用时，才记录对应确认。它不是媒体、人物或声音默认可用性的前置对象；客户自备模型连接遵循[产品第2.6节](product-requirements.md#26-本地优先与用户控制)。

## 5. 制作方案与任务

### 5.1 Plan

由用户目标和当前 Workspace 状态编译出的可执行制作方案。

关键字段：

- id、workspace_id、goal；
- content_type、audience、language；
- duration_target、aspect_ratio、target_platforms；
- knowledge_refs、source_refs、asset_refs；
- person_profile_ref、voice_profile_ref；
- topic_strategy、script_style、visual_strategy；
- presenter_share_target（制作方案六档 0%、20%、40%、60%、80%、100% 的露脸时长目标；默认 20%）；
- lipsync_quality（口型制作档位 high=LatentSync、fast=MuseTalk；新方案默认 high，旧计划读取为 fast。PersonRender 的 model_id 与音频、人物、版本一起决定缓存身份；不能跨档位复用）；
- subtitle_strategy、cover_strategy；
- confirmation_points、blocking_conditions；
- status、version。

Plan 必须引用真实可用对象。用户确认 Plan 后创建或推进 CreationTask。
露脸占比是视觉策略约束，不是独立时间线或已完成口型的证明。StoryBoard/TimelineVersion 从实际片段起止时间计算露脸占比；人物片段只可引用与当前配音版本匹配的 PersonRender 或保留原声同步的原片。补充素材片段继续引用原素材并共享配音轨。目标与实际不同须保留原因，不能把无口型的人物画面计作已同步出镜。

### 5.2 CreationTask

视频生产全过程的唯一聚合根。

关键字段：

- id、workspace_id、plan_id、title；
- stage、stage_state、status；
- current_topic_id、current_script_id；
- current_narration_id、current_timeline_version_id；
- output_refs、blocking_issue；
- created_at、updated_at。

任务阶段为：

1. preparation：资料准备；
2. topic：选题；
3. script：文案；
4. narration：配音；
5. person_lipsync：人物与口型；
6. montage：视频剪辑；
7. subtitle_cover：字幕与封面；
8. review_output：检查与输出；
9. publish_analytics：发布与数据。

阶段状态为：

- not_started；
- working；
- waiting_user；
- blocked；
- ready；
- completed；
- skipped。

skipped 必须记录不适用原因；blocked 必须记录原因、影响和修复动作。

连续制作沿同一任务记录文案版本、要求确认和开始意图；确认方案不能替代确认实际 Script。阶段执行与恢复读取持久化状态及有效产物，重复开始不重复生成同一作业。用户变更文案后旧的确认和相关下游结果按依赖失效；应用重启不得以失去会话内存为由重建另一任务。正常内部阶段可连续推进，发布确认仍独立保留。

### 5.3 Topic 与 Script

Topic 保存候选标题、核心判断、目标受众、事实引用和确认状态。

Script 保存结构化文案和段落。每个 ScriptSegment 有稳定 id，并记录正文、事实引用、预计时长、说话人和确认状态。局部编辑依赖稳定段落 id，不以纯文本位置作为唯一定位。

### 5.4 Narration

Narration 关联一个已确认的 Script 版本。NarrationSegment 与 ScriptSegment 一一或多对一对应，记录音频引用、真实时长、语言和处理状态；来源在现有 Narration 事实中明确区分 `generated`（生成配音）与 `recorded`（已明确采用的任务录音），不建立第二套任务或时间线事实源。

`generated` 关联 VoiceProfile 版本与实际模型运行，既有生成记录保持该语义。`recorded` 关联同一工作区、同一任务的 `task_recording` Artifact/Transform、源引用与内容摘要，以及用户明确选定的当前 ScriptSegment；VoiceProfile 和模型可为空，不冒称声音克隆或模型生成。未知来源不得按录音或生成成功处理。

保存录音与采用录音是两个动作。保存只产生录音资产；明确采用才提交当前文案版本绑定的 Narration/段落事实，并沿现有连续制作进入故事板。首次采用不依赖一个尚不存在的故事板。录音真实时长决定对应段落时长；没有明确分段映射时不能按文字或总时长猜测切分。文案、源文件或采用范围变化时，相关下游按现有版本依赖失效，未受影响结果保留。

纯素材渲染读取并核验真实录音输入，不强制录音伪装成某个克隆声音。人物口型仍按既有人物、声音授权与身份匹配契约核验；录音采用不扩大口型模型支持或授予人物/声音使用权，缺少该链所需依赖时明确阻断。

## 6. 时间线与产物

### 6.1 Timeline

Timeline 是成片编辑的逻辑时间线，包含视频、人物、配音、音乐、字幕、图片和图形轨道。

### 6.2 TimelineVersion

每次已应用到草稿的自然语言提案或有效精确编辑创建一个不可变 TimelineVersion；普通控件直接提交确定性命令，不必先创建面向用户的提案确认。草稿仍由CreationTask及其当前版本持久化，不新增聊天或内存事实源。

关键字段：

- id、task_id、parent_version_id；
- edit_operation_ids；
- track_snapshot_ref；
- invalidated_artifact_refs；
- preview_output_ref、created_at。

CreationTask 只指向当前版本。撤销通过切换或创建反向版本完成，不覆盖历史版本。重做恢复最近撤销前的快照，并创建新的版本；撤销后有新的内容编辑时，旧重做入口失效。

### 6.3 TimelineItem

时间线中的最小可编辑对象。它引用 Clip、NarrationSegment、SubtitleCue、PersonRender、CoverAsset 或其他派生产物，并记录起止时间、裁切、布局、音量和效果参数。

音轨中的片段引用实际音频产物，分别保存源区间和时间线区间、轨道关联、音量/效果参数及关键点。波形是源音频的可重建派生数据，不是音轨事实源。片段重排、删除、变速及转场必须明确关联对象和时间变换；字幕与口型不能沿用已失效的声音时轴。预览、混音和最终渲染读取同一 TimelineVersion。关联变速以原音频引用、源区间和版本化速度/时间变换表达草稿，不以提前生成的新音频引用作为编辑前提；明确试听或渲染时才物化处理结果。真实时长、音量包络与关联字幕读取该变换，撤销恢复同一组源区间和变换。

人物口型镜头排序的默认联动遵循 [产品精确编辑规则](product-requirements.md#93-精确编辑)。来源引用用于识别关联配音及字幕，不能仅按移动后时间重叠自动改绑到另一句；镜头、关联音频源区间与字幕显示区间在同一 TimelineVersion 中记录变换，确认和撤销读取同一组关联。

时间变化中的画面效果须保留自身时钟：分割截取原效果进度，关联变速同步改变其推进速度，不在新接缝重新开始；明确修改效果参数时才以当前片段重新建立该效果范围。效果时钟与片段一同版本化，撤销恢复二者。

### 6.4 Subtitle、Cover 与 PlatformVariant

- SubtitleTrack：语言、样式和 SubtitleCue 集合；
- SubtitleCue：文本、起止时间、说话人和强调标记；
- CoverAsset：封面图、标题、安全区和生成来源；
- PlatformVariant：基于同一任务输出的画幅、码率、字幕和发布文案变体。

用户仅调整中文字幕条目的时间且正文不变时，通过明确的来源关联同步对应英文字幕时间，保留译文正文；来源关联缺失或有歧义时拒绝猜测并提示检查。两轨修改在同一 TimelineVersion 中确认和撤销，不改变配音内容。此规则不将中文正文修改自动解释为译文仍然有效。

按配音校准字幕只调整显示时间，不以识别结果替换已确认正文。明确校准双语时，先对齐原语言字幕，再通过译文与原句的来源关联同步译文时间；现有译文条目的来源关联缺失或有歧义时不得猜测。普通校准直接保存两轨到同一 TimelineVersion 并支持撤销，保留语言显示开关。

## 7. 自然语言编辑

### 7.1 EditIntent

用户自然语言经过理解后形成的结构化意图。

关键字段：

- id、task_id、raw_command；
- context_stage、selected_object_refs、playhead_ms；
- intent_type、scope；
- constraints、confidence；
- status。

典型 intent_type 包括：

- update_script；
- replace_visual；
- trim_or_reorder；
- regenerate_narration；
- update_subtitle；
- change_person；
- change_style；
- change_duration_or_format。

### 7.2 EditProposal

EditIntent 编译出的待确认提案，必须说明：

- 系统理解的动作；
- 目标对象和时间范围；
- 计划写入的类型化操作；
- 将失效或重新生成的依赖；
- 可预览差异；
- 是否产生外部副作用。

提案状态为 proposed、needs_scope、confirmed、rejected 或 expired。

### 7.3 EditOperation

由普通精确控件直接提交或用户应用提案后的确定性领域操作，例如 ReplaceClip、UpdateScriptSegment、RegenerateNarrationSegment、UpdateSubtitleCue、ChangeCrop 或 ReorderTimelineItems。

跨对象复合修改在同一基础版本上完整编译，自然语言先展示提案，应用时原子写入一个新版本；任一子操作失败不得保留部分修改，撤销恢复整组变更。

文字模型只能产生或修正 EditIntent 和 EditProposal，不得直接写文件路径、执行任意 FFmpeg 命令或绕过领域校验修改时间线。

### 7.4 编辑状态机

自然语言：interpreted → proposed → confirmed → running → applied

这里confirmed表示用户已选择应用提案，不要求额外确认弹窗。精确控件直接提交EditOperation并应用为版本化草稿，复用相同校验、依赖和撤销/重做。草稿应用不自动生成；媒体生成是独立执行动作。删除确认的用户流程见[产品编辑规则](product-requirements.md#23-自然语言与精确编辑共享同一事实源)。

异常分支为：

- needs_scope：范围不明确，等待用户补充或选择对象；
- failed：执行失败，保留旧时间线版本并记录修复动作；
- reverted：已通过新版本撤销。

## 8. 变换与依赖

### 8.1 TransformRun

记录一次分析、生成或渲染：

- transform_type；
- input_refs、output_refs；
- model_or_engine、parameters；
- status、started_at、finished_at；
- failure_reason、remediation。

所有生成产物必须能通过 TransformRun 追溯输入和执行条件。

### 8.2 DependencyEdge

表达产物之间的依赖。例如：

ScriptSegment → NarrationSegment → SubtitleCue

ScriptSegment + NarrationSegment + Clip → TimelineItem

TimelineVersion → PreviewOutput → FinalOutput

编辑操作只使依赖图中受影响的下游节点 invalidated。未受影响且仍满足输入摘要的节点保持 valid。

### 8.3 产物状态

- valid：输入和依赖未变化，可直接复用；
- invalidated：上游发生变化，需要重算；
- running：正在生成；
- failed：生成失败；
- unavailable：当前能力或环境不存在；
- superseded：已有新版本替代。

### 8.4 设备执行安排

执行安排是已有作业或素材分析的调度元数据，不是第二份CreationTask或成片状态。持久事实包含工作区、服务端成员身份（后台任务可无成员）、已有执行对象引用、输入版本摘要、工作类别、最早执行时间/允许窗口、原始就绪时间、等待原因、当前执行单元与最后有效检查点、资源许可/持有者标识及恢复信息。策略来自产品需求第12.1节，不由客户端或模型自行提高优先级。

“等待设备空闲”“等待指定时间”“等待资源”“等待依赖”“准备让出”“已让出”区别于失败和完成：请求让出时当前单元仍在运行；确认其检查点及资源释放后才能表示已让出。已取消或输入版本失效的请求不得重新获得许可。再次安排只改变调度元数据，不绕过原文案、制作、模型外发或发布确认，不抹掉历史等待时间。

## 9. 模板、自动任务与发布

### 9.1 Template

Template 保存可复用的 Plan 约束、内容结构、风格、素材选择规则、人物声音偏好、字幕封面策略和确认节点。内置模板与用户模板使用同一领域结构，用户模板必须版本化。

模板版本不可变，按 Workspace、模板 ID、版本定位；记录来源任务及时间线版本，但不复制来源文案、选题、镜头 ID、产物或完成状态。新 Plan 固化所用模板版本及设置，之后修改模板不能改变已有任务。

复用必须重新检索本次目标的资料与镜头，并重新检查指定人物、声音版本和模型能力；依赖缺失给出修复原因，不静默切换。画面适配保存可复用的显示方式，不把旧镜头的裁切位置应用到新素材。字幕保存样式偏好，不保存旧字幕正文或时轴。尚未执行的风格、封面或模型设置不得在 UI 中宣称已经复用。

调色偏好仅在来源任务所有镜头的曝光、对比度、饱和度和冷暖一致时保存为统一设置，应用到新素材；逐镜头差异不取平均或任意选取一个镜头。裁切、变速、淡入淡出与人脸处理不属于统一调色。缺少调色设置的旧模板保留新素材原色。

### 9.2 Automation

Automation 关联一个模板、触发规则、来源范围和确认策略。

状态为 active、paused、running、waiting_review、blocked 或 failed。自动任务不得绕过 ContentUseState、模型外发确认或发布确认。

### 9.3 PublishJob 与 AnalyticsSnapshot

PublishJob 记录目标平台、账号引用、PlatformVariant、用户确认、排队、发布和失败状态。AnalyticsSnapshot 保存某次拉取的表现数据并关联发布任务和时间。

## 10. 核心不变量

1. 所有业务对象必须属于明确 Workspace；跨工作区引用仅在有效显式共享授权范围内成立，原空间和所有者不变。授权规则见[认证边界第9节](auth-workspace-boundary.md#9-主账号邀请与共享授权)。
2. CreationTask 是生产链路唯一事实源；Episode、Batch 和聊天状态不得继续承担用户生产写入。
3. 用户给入工作区的内容默认 available；只有显式限制才能改变其使用范围。
4. 来源和版本必须保留，但逐条权利人或证据不是默认创作前置字段。
5. 原始资产不可覆盖；派生产物必须有 TransformRun。
6. 自然语言和精确编辑必须编译为同一组 EditOperation，并写入 TimelineVersion。
7. 尚未选择“应用到草稿”的 EditProposal 不得改变任务或产物；普通精确命令不以提案弹窗作为写入前提。
8. 局部编辑只使真实下游依赖失效，不得无理由重做整条任务。
9. 不可用能力必须返回 unavailable 或 blocked，不能使用虚假成功 fallback。
10. 平台发布、云端外发和删除等外部副作用按风险显式确认，客户自备模型连接依产品第2.6节。
11. 任务失败保留已经确认且仍有效的上游结果。
12. 旧对象只有在消费者、写路径、入口、样式和错误测试全部移除并通过不存在证明后，才达到 RETIRED。

## 11. 设备成员、共享和素材纠正对象

Workspace 仍是私有工作边界；设备成员关系承载使用名额和产品角色，工作空间关系承载操作范围，两者不可互代。团队共享范围有明确归属，跨空间可用性通过授权关系表达；授权不改写原所有者。邀请记录表达目标设备/空间、邀请人、到期、撤销、接受人及接受结果，不把二维码文本当用户身份。权限不变量见[认证边界第9节](auth-workspace-boundary.md#9-主账号邀请与共享授权)。

素材补充沿现有 MediaAsset/Clip 扩展：导入批次背景、关联业务对象、带时间区间的观察、用户纠正及其适用范围/来源/版本、用户评价、禁止自动选用、索引版本。观察和纠正不覆盖原始来源；评分与禁止使用分别表达。候选引用绑定资产、片段、原始时间、证据和版本，避免“03镜头”被解释为文件名或跨任务序号。

知识引用沿 KnowledgeItem/SourceItem 记录快照版本、页码/段落/表格位置及冲突关联；文案引用固定相应证据版本。声音克隆参考与当前视频录音是不同用途的音频资产，当前录音关联具体任务/段落。共享人物、声音与模板仍保留版本和使用授权。

录入、分析和制作的进度引用后台作业事实；预计等待与处理时长分别记录计算依据及更新时间，未知时为未知。页面关闭不改变作业状态；整机摘要只是按权限计算的视图，不成为第二套任务事实源。具体字段与迁移在实施合同中评审，不把此处概念名称当现有表/API。
