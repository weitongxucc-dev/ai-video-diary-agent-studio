# AI视频日记 Agent Studio PRD

## 1. 产品概述

AI视频日记 Agent Studio 是一个面向个人 IP 创作者的自主进化内容生产 Agent。它每天主动从用户本地 Obsidian 知识库、外部 AI 产品信息源、手动链接和后续接入的 MCP/CLI/API 工具中摄入信息，融合用户过往内容偏好，生成可复核的选题、口播稿、封面和剪映草稿。

第一版目标不是全自动发布，而是把一条视频从“选题”推进到“剪映草稿箱可人工发布”。

核心链路：

```text
多源信息摄入
-> 知识融合
-> 每日选题生成
-> 人工复核
-> 口播文案生成
-> 人工复核
-> 人工录制并上传视频
-> AI 转写/字幕/剪辑/加速/封面
-> 生成剪映草稿
-> 人工发布
-> 反馈回流
```

## 2. 产品目标

### 2.1 用户目标

- 每天获得 3-5 个结合个人知识库和外部 AI 产品动态的高质量视频选题。
- 能看到每个选题的来源依据，而不是盲目接受 AI 生成。
- 一键确认选题后，获得适合 2-4 分钟视频日记的口播稿。
- 录制视频后，把文件放到指定位置，系统自动完成转写、字幕、基础剪辑、加速、封面和剪映草稿生成。
- 通过赞、踩、修改、发布数据反馈，让 Agent 越来越理解自己的内容偏好。

### 2.2 产品目标

- 将用户每天的视频生产时间压缩到确认选题、录制视频、最终发布三个动作。
- 建立“知识库 -> 选题 -> 视频 -> 反馈 -> 偏好进化”的闭环。
- 第一版验证 Obsidian + Product Hunt + 观猹/AI 产品站 + 手动链接能否稳定产生有价值选题。
- 第一版验证自动生成剪映草稿箱项目的技术可行性。

### 2.3 非目标

第一版不做：

- 自动发布抖音。
- 多用户 SaaS。
- 复杂团队协作。
- 商业化计费。
- 全网无限爬虫。
- 自动美颜。
- 自动训练或 fine-tune 专属模型。
- 复杂剪映模板市场。
- 传统 SaaS 营销风格首页。
- 纯聊天机器人式主界面。

## 3. 目标用户

第一目标用户是项目发起人本人：

- 长期记录 AI 产品、AI 工具、AI 项目实践。
- 希望通过“AI视频日记”形成个人 IP。
- 拥有本地 Obsidian 知识库。
- 关注 Product Hunt、观猹等 AI 产品信息源。
- 愿意人工确认关键内容，但不想重复处理字幕、封面、剪辑和草稿导入。

后续可扩展用户：

- AI 产品经理。
- 独立开发者。
- 创业者。
- 技术型内容创作者。
- 行业顾问型知识博主。

## 4. 第一版 MVP 范围

### 4.1 必须支持

1. Obsidian 本地只读索引
   - 用户配置 Vault 路径。
   - 系统扫描 Markdown 文件。
   - 提取标题、正文、标签、双链、外链、更新时间。
   - 忽略 `.obsidian` 配置目录。
   - 生成摘要、主题、观点、可拍摄角度。

2. 外部信息源
   - Product Hunt 官方 API 接入。
   - 观猹/Watcha 类 AI 产品站公开页面或手动链接解析。
   - 手动链接 Inbox。
   - 每条外部信息转成素材卡。

3. 每日选题生成
   - 支持固定时间生成选题。
   - 每天生成 3-5 个选题。
   - 每个选题显示推荐理由和引用来源。
   - 每个选题支持通过、踩、重写方向。

4. 口播稿生成
   - 选题确认后生成 2-4 分钟口播稿。
   - 输出开头 3 秒钩子、正文结构、结尾金句、标题候选、发布文案、评论引导、封面文案。
   - 支持按段落赞、踩、重写。

5. 视频上传或文件夹监听
   - 支持前端上传视频。
   - 支持监听本地固定文件夹。
   - 识别新视频后绑定到当前 episode。

6. 视频处理
   - 提取音频。
   - 转写。
   - 生成 SRT 字幕。
   - 保守剪掉长停顿。
   - 轻微加速。
   - 音量标准化。
   - 输出 preview 视频。

7. 封面生成
   - 第一版使用固定模板。
   - 支持 Day N、主标题、三个观点、人物小截图。
   - 输出 4:3 封面图。

8. 剪映草稿生成
   - 优先接入 CapCut Mate。
   - 备选 pyJianYingDraft。
   - 创建草稿。
   - 添加视频、字幕、封面、标题贴纸。
   - 保存到剪映草稿箱。

9. 反馈学习
   - 记录选题赞踩。
   - 记录文案段落赞踩。
   - 记录用户手动修改后的版本。
   - 记录最终是否发布。
   - 生成用户偏好摘要，供后续选题和脚本生成使用。

### 4.2 第一版验收标准

1. 用户在 Obsidian 写一条新笔记，系统能在索引后用于次日选题。
2. 系统能每天固定时间生成 3-5 个带来源依据的选题。
3. 用户确认选题后，系统能生成完整口播稿和发布素材。
4. 用户上传或放入一个视频后，系统能自动生成字幕、封面和 preview 视频。
5. 系统能创建一个剪映草稿箱项目，里面包含视频、字幕、封面和标题贴纸。
6. 用户对选题或文案的赞踩会影响下一次生成结果。

## 5. 用户流程

### 5.1 每日内容流程

```text
系统定时运行
-> 摄入新增信息
-> 生成今日选题池
-> 用户打开今日工作台
-> 查看选题来源和推荐理由
-> 用户确认一个选题
-> Agent 生成口播稿
-> 用户复核并修改
-> 用户确认稿件
-> 用户录制视频
-> 用户上传视频或放到监听文件夹
-> Agent 自动处理视频
-> 用户预览字幕、封面、剪辑结果
-> 用户确认生成剪映草稿
-> Agent 写入剪映草稿箱
-> 用户打开剪映人工发布
-> 用户在系统里标记发布结果
-> Agent 更新偏好记忆
```

### 5.2 选题生成逻辑

选题 Agent 每天读取：

- 最近新增 Obsidian 笔记。
- 高频标签和历史主题。
- Product Hunt 今日产品。
- 观猹/Watcha 新品和热门产品。
- 手动链接 Inbox。
- 历史已发布视频。
- 用户赞踩和修改记录。
- 发布表现数据。

然后输出选题候选：

```text
标题
一句话观点
推荐理由
关联来源
适合受众
可讲结构
争议点
推荐指数
```

### 5.3 自主进化逻辑

第一版不做模型训练，而是做偏好记忆：

```text
用户喜欢的主题
用户不喜欢的主题
用户偏好的标题风格
用户偏好的口播结构
用户常修改的问题
用户发布后表现较好的内容类型
```

这些偏好会在生成选题和文案时注入 prompt，并参与候选排序。

## 6. 前端页面

第一版前端为前后端分离 Web App。

### 6.0 前端视觉原则

第一版 Web 界面采用“像素风生产控制台”风格。视觉参考是带像素图标、像素 Agent 形象、浅色工作台、流程进度条和高信息密度卡片的桌面应用界面。

必须体现：

- 左侧像素图标导航。
- 顶部状态栏和搜索框。
- 今日工作台主流程进度条，每个步骤配像素风图标。
- 右侧 Agent 状态面板，使用像素机器人形象表达当前进展。
- 卡片边框清晰、圆角克制、背景浅色。
- 人工待办用醒目的按钮和橙色/蓝色状态强调。
- 自动任务用进度条、状态点和活动流表达。
- 空状态、完成状态、失败状态可以使用小型像素插画或吉祥物。

不采用：

- 大面积营销式 hero。
- 玻璃拟态和过度渐变。
- 通用后台模板感。
- 只靠文字列表表达状态。
- 聊天窗口占据主界面。

### 6.1 今日工作台

展示今日 episode 的完整进度：

```text
信息摄入 -> 选题生成 -> 选题复核 -> 口播稿 -> 视频等待
-> AI 处理 -> 封面 -> 剪映草稿 -> 发布反馈
```

核心任务是告诉用户当前需要做什么。

### 6.2 选题池

展示今日 3-5 个选题卡片。每张卡片包含：

- 标题。
- 一句话观点。
- 推荐指数。
- 来源依据。
- 适合受众。
- 3 分钟结构。
- 操作：通过、踩、换角度、重写。

### 6.3 口播稿复核

结构化展示：

- 开头 3 秒钩子。
- 背景段。
- 原因/观点 1。
- 原因/观点 2。
- 原因/观点 3。
- 总结金句。
- 标题候选。
- 发布文案。
- 评论引导。

每段支持赞、踩、重写、手动编辑。

### 6.4 视频处理

展示：

- 视频上传或监听状态。
- 转写进度。
- 字幕生成状态。
- 初剪状态。
- 加速状态。
- 封面生成状态。
- preview 视频。

### 6.5 剪映草稿

展示：

- 草稿名称。
- 草稿路径。
- 草稿状态。
- 生成时间。
- 包含素材清单。
- 操作：重新生成、打开目录、标记已发布。

### 6.6 知识库

展示 Obsidian 和外部信息源的索引情况：

- Vault 路径。
- 已索引笔记数。
- 最近同步时间。
- 最近新增笔记。
- 标签分布。
- 可用于选题的观点数量。

### 6.7 信息源

管理：

- Obsidian。
- Product Hunt。
- 观猹/Watcha。
- 手动链接 Inbox。
- 后续 MCP/CLI/API 工具。

每个信息源显示同步状态、失败原因和参与选题权重。

### 6.8 反馈学习

展示：

- 用户偏好摘要。
- 近期点赞主题。
- 近期踩过主题。
- 常见修改倾向。
- 选题表现。
- 脚本表现。

## 7. 后端架构

推荐技术栈：

```text
Frontend: Next.js / React
Backend: FastAPI
Agent Orchestration: LangGraph
LLM: OpenAI / Claude / DeepSeek 可配置
Transcription: Whisper / OpenAI Speech-to-Text
Video: FFmpeg
Draft: CapCut Mate / pyJianYingDraft
Database: SQLite first, PostgreSQL later
Vector Search: Chroma / Qdrant / sqlite-vec
File Watcher: Python watchdog
```

### 7.1 LangGraph 工作流

```text
daily_trigger
-> ingest_sources
-> update_knowledge_index
-> generate_topic_candidates
-> human_review_topic
-> generate_script
-> human_review_script
-> wait_for_video_upload
-> transcribe_video
-> generate_edit_plan
-> process_video
-> generate_cover
-> create_jianying_draft
-> human_publish
-> collect_feedback
-> update_preferences
```

人工暂停节点：

- human_review_topic
- human_review_script
- human_publish

### 7.2 核心数据对象

Episode：

```text
id
day_number
date
status
selected_topic_id
script_id
video_path
preview_video_path
cover_path
draft_path
published_status
created_at
updated_at
```

SourceItem：

```text
id
source_type
source_url_or_path
title
summary
tags
raw_content
extracted_points
published_at
indexed_at
```

TopicCandidate：

```text
id
episode_id
title
core_viewpoint
reason
sources
audience
outline
score
feedback
```

PreferenceMemory：

```text
id
type
content
evidence
confidence
updated_at
```

## 8. API 草案

```text
GET /api/episodes/today
POST /api/episodes
POST /api/episodes/{id}/generate-topics
POST /api/episodes/{id}/approve-topic
POST /api/episodes/{id}/generate-script
POST /api/episodes/{id}/approve-script
POST /api/episodes/{id}/upload-video
POST /api/episodes/{id}/process-video
POST /api/episodes/{id}/create-draft
POST /api/episodes/{id}/mark-published

GET /api/sources
POST /api/sources/sync
POST /api/sources/links

GET /api/knowledge/notes
GET /api/preferences
POST /api/feedback
```

## 9. 风险与应对

### 9.1 剪映草稿兼容风险

风险：剪映草稿结构可能变化，导致自动写入失败。

应对：

- 第一阶段先做技术 Spike。
- 先验证 1 个视频 + 1 个 SRT + 1 张封面能否进入剪映草稿箱。
- CapCut Mate 优先，pyJianYingDraft 备选。
- 如果自动导入失败，降级为保留 preview.mp4、SRT、cover.png 和素材包，并提供重试导入与人工验证路径。

### 9.2 外部站点抓取风险

风险：观猹/Watcha 等站点可能无 API，页面结构变化。

应对：

- Product Hunt 走官方 API。
- 观猹先支持手动链接解析和轻量公开页面抓取。
- 对每个信息源加失败状态和手动重试。

### 9.3 选题质量风险

风险：AI 生成选题泛泛而谈。

应对：

- 强制显示来源依据。
- 强制结合 Obsidian 笔记和外部产品动态。
- 支持踩和原因反馈。
- 建立“已用过观点”去重机制。

### 9.4 自主进化过度承诺

风险：用户以为系统会自动训练成一个专属模型。

应对：

- 第一版明确为偏好记忆和规则归纳，不做 fine-tune。
- 前端展示“Agent 学到了什么”，提高可解释性。

## 10. 里程碑

### Milestone 1: 内容 Agent 闭环

- Obsidian 索引。
- 手动链接 Inbox。
- Product Hunt 接入。
- 每日选题生成。
- 选题人工复核。
- 口播稿生成。

### Milestone 2: 反馈学习

- 选题赞踩。
- 文案段落赞踩。
- 修改版本保存。
- 偏好摘要生成。

### Milestone 3: 视频处理

- 上传视频。
- 转写。
- SRT 字幕。
- FFmpeg 初剪、加速、音量标准化。
- preview 输出。

### Milestone 4: 封面和剪映草稿

- 4:3 封面模板。
- 人物小截图。
- CapCut Mate / pyJianYingDraft 接入。
- 剪映草稿箱生成。

### Milestone 5: 发布回流

- 人工标记已发布。
- 播放、点赞、评论等数据手动录入。
- 选题表现分析。
