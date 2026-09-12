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
- not_for_automation：可人工使用，不可被定时任务自动选用；
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

- SystemTag：模型或规则产生的主体、人物、产品、场景、动作、物体、画幅和质量标签。
- UserTag：用户创建或修正的标签。
- TagFolder：用户管理标签的分组。
- UsageRecord：记录素材或片段被哪条任务、哪个时间线版本、以什么用途使用。

系统分析不等于用户确认。低置信分类和影响成片的素材缺口必须等待人工选择。

分析证据必须保留来源模态、原资产时间区间、分析模型/规则版本与用户纠正来源，区分观察与推断。用户素材事实纠正不得被重新分析覆盖；任务内偏好与工作区长期偏好分开，偏好可取消且不能覆盖内容使用限制。具体持久化扩展沿既有 Repository 和版本迁移，不创建旁路素材库。

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

只有发生云端数据外发、平台账号连接、平台发布、付费调用或其他外部副作用时，才记录对应确认。它不是媒体、人物或声音默认可用性的前置对象。

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
- subtitle_strategy、cover_strategy；
- confirmation_points、blocking_conditions；
- status、version。

Plan 必须引用真实可用对象。用户确认 Plan 后创建或推进 CreationTask。

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

### 5.3 Topic 与 Script

Topic 保存候选标题、核心判断、目标受众、事实引用和确认状态。

Script 保存结构化文案和段落。每个 ScriptSegment 有稳定 id，并记录正文、事实引用、预计时长、说话人和确认状态。局部编辑依赖稳定段落 id，不以纯文本位置作为唯一定位。

### 5.4 Narration

Narration 关联一个 Script 版本和 VoiceProfile 版本。NarrationSegment 与 ScriptSegment 一一或多对一对应，记录音频引用、时长、语言、模型运行和生成状态。

## 6. 时间线与产物

### 6.1 Timeline

Timeline 是成片编辑的逻辑时间线，包含视频、人物、配音、音乐、字幕、图片和图形轨道。

### 6.2 TimelineVersion

每次已确认的自然语言或精确编辑创建一个不可变 TimelineVersion。

关键字段：

- id、task_id、parent_version_id；
- edit_operation_ids；
- track_snapshot_ref；
- invalidated_artifact_refs；
- preview_output_ref、created_at。

CreationTask 只指向当前版本。撤销通过切换或创建反向版本完成，不覆盖历史版本。重做恢复最近撤销前的快照，并创建新的版本；撤销后有新的内容编辑时，旧重做入口失效。

### 6.3 TimelineItem

时间线中的最小可编辑对象。它引用 Clip、NarrationSegment、SubtitleCue、PersonRender、CoverAsset 或其他派生产物，并记录起止时间、裁切、布局、音量和效果参数。

音轨中的片段引用实际音频产物，分别保存源区间和时间线区间、轨道关联、音量/效果参数及关键点。波形是源音频的可重建派生数据，不是音轨事实源。片段重排、删除、变速及转场必须明确关联对象和时间变换；字幕与口型不能沿用已失效的声音时轴。预览、混音和最终渲染读取同一 TimelineVersion。

### 6.4 Subtitle、Cover 与 PlatformVariant

- SubtitleTrack：语言、样式和 SubtitleCue 集合；
- SubtitleCue：文本、起止时间、说话人和强调标记；
- CoverAsset：封面图、标题、安全区和生成来源；
- PlatformVariant：基于同一任务输出的画幅、码率、字幕和发布文案变体。

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
- 是否产生付费调用或外部副作用。

提案状态为 proposed、needs_scope、confirmed、rejected 或 expired。

### 7.3 EditOperation

确认后的确定性领域操作，例如 ReplaceClip、UpdateScriptSegment、RegenerateNarrationSegment、UpdateSubtitleCue、ChangeCrop 或 ReorderTimelineItems。

文字模型只能产生或修正 EditIntent 和 EditProposal，不得直接写文件路径、执行任意 FFmpeg 命令或绕过领域校验修改时间线。

### 7.4 编辑状态机

interpreted → proposed → confirmed → running → applied

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

## 9. 模板、自动任务与发布

### 9.1 Template

Template 保存可复用的 Plan 约束、内容结构、风格、素材选择规则、人物声音偏好、字幕封面策略和确认节点。内置模板与用户模板使用同一领域结构，用户模板必须版本化。

模板版本不可变，按 Workspace、模板 ID、版本定位；记录来源任务及时间线版本，但不复制来源文案、选题、镜头 ID、产物或完成状态。新 Plan 固化所用模板版本及设置，之后修改模板不能改变已有任务。

复用必须重新检索本次目标的资料与镜头，并重新检查指定人物、声音版本和模型能力；依赖缺失给出修复原因，不静默切换。画面适配保存可复用的显示方式，不把旧镜头的裁切位置应用到新素材。字幕保存样式偏好，不保存旧字幕正文或时轴。尚未执行的风格、封面或模型设置不得在 UI 中宣称已经复用。

### 9.2 Automation

Automation 关联一个模板、触发规则、来源范围和确认策略。

状态为 active、paused、running、waiting_review、blocked 或 failed。自动任务不得绕过 ContentUseState、模型外发确认或发布确认。

### 9.3 PublishJob 与 AnalyticsSnapshot

PublishJob 记录目标平台、账号引用、PlatformVariant、用户确认、排队、发布和失败状态。AnalyticsSnapshot 保存某次拉取的表现数据并关联发布任务和时间。

## 10. 核心不变量

1. 所有业务对象必须属于明确 Workspace，跨工作区引用无效。
2. CreationTask 是生产链路唯一事实源；Episode、Batch 和聊天状态不得继续承担用户生产写入。
3. 用户给入工作区的内容默认 available；只有显式限制才能改变其使用范围。
4. 来源和版本必须保留，但逐条权利人或证据不是默认创作前置字段。
5. 原始资产不可覆盖；派生产物必须有 TransformRun。
6. 自然语言和精确编辑必须编译为同一组 EditOperation，并写入 TimelineVersion。
7. 未确认的 EditProposal 不得改变任务或产物。
8. 局部编辑只使真实下游依赖失效，不得无理由重做整条任务。
9. 不可用能力必须返回 unavailable 或 blocked，不能使用虚假成功 fallback。
10. 平台发布、云端外发、付费调用和删除等外部副作用按风险显式确认。
11. 任务失败保留已经确认且仍有效的上游结果。
12. 旧对象只有在消费者、写路径、入口、样式和错误测试全部移除并通过不存在证明后，才达到 RETIRED。
