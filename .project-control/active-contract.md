# 本地真成片 v0.1 恢复合同

状态：`ACTIVE`  
合同版本：`LOCAL-VIDEO-V01-2026-08-30-V1`  
迁移状态：`END_TO_END_ACCEPTED / ABSENCE_VERIFIED / INDEPENDENT_QA_HOLD`（同一候选的本地真成片闭环已通过；独立质量线程未能出具最终结论，因此尚不标记 `MIGRATION_ACCEPTED`）
实现基线：`codex/recovery-local-video-v01@e8b2b17`；打包 build identity `f5540b40827cdc5af0d6a9d68ce91bebccdcdc0f234a9cba2829218ea19548c4`；DMG SHA-256 `6d1ffcc35cf4117c4c5aabdbf0037b2ff8be9c27bc3c18ab9ef5f90fe7c32185`
纵向迁移负责人：主 Agent；任何技术层线程只能提交证据，不能单独宣布完成。

本文件是短期执行合同，不是产品或领域权威。范围、事实源或验收改变时直接替换本文件；达到 `MIGRATION_ACCEPTED`、任务取消或转入外部 Issue/PR 后删除，不归档旧副本。

## 1. 唯一用户结果

用户在本机完成：授权资料与已审核本地素材 → 选题 → 文案 → 真实中文声音克隆 → 素材混剪 → 可修改中文字幕 → 字幕烧录 MP4 与独立 SRT → 原生保存对话框保存到本地。

初版成片停在“等待检查”。人物口型、AI 封面、视频模板、自动任务、平台发布、数据监控、签名、公证和分发不进入本合同。

## 2. 必须重读的权威

- `docs/product-requirements.md#3-产品主链`、`#6-本地优先与数据边界`、`#8-端到端验收`
- `docs/domain-model.md#1-用户语言`、`#24-创作`、`#4-创作任务状态`、`#6-不变量`
- `docs/system-architecture.md#3-数据边界`、`#4-命令任务和事件`、`#7-媒体与存储`
- `DESIGN.md#22-创作任务内部`、`#41-交互与按钮契约`、`#55-创作任务与视频剪辑`
- 迁移、拆分或验收时再读 `docs/development-workflow.md#41-产品级迁移与退役`。

任务只绑定以上章节和当前 Git 基线；不得为了“了解历史”读取 `docs/archive/`、旧 QA 记录或完整聊天。

## 3. 唯一事实源与主接口

- 创作任务、文案、配音引用和剪辑产物只扩展现有 `ProductionRepository`；不得建立第二套仓储或写回 Episode、Batch、`repo.today()`。
- Web 只通过 CreationTask 接口推进任务：任务详情、文案生成/确认、配音声音选择/生成、剪辑计划/片段修改/渲染和成片输出。
- 已确认文案的任务如果因声音缺失停在配音阶段，必须允许用户显式绑定后来登记的已授权声音版本后原地继续；不得要求重做选题、文案或自动选择未确认的声音。该恢复动作使用 `POST /api/creation-tasks/{task_id}/narration/voice`，只更新同一 Plan/CreationTask 的声音引用与配音阻断，不调用模型。
- Electron 只通过 `saveRenderedVideo(taskId)` 和原生保存对话框导出；客户端不得提交任意源文件绝对路径。
- 外部模型、FFmpeg、文件系统和授权失败必须持久化原因、影响与修复动作；禁止系统声音、静音、伪造 WAV、伪造视频或 Template fallback。

## 4. 正向验收

同一不可变候选必须同时证明：

1. 首页创建 Plan 与 CreationTask，进入 `/tasks/{taskId}`。
2. 文字模型分别完成选题和文案生成，用户各确认一次；来源证据和结果重启后仍一致。
3. 已授权中文 WAV、参考文字、声音版本和 Qwen3-TTS 0.6B 真实生成可播放 WAV，没有回退。
4. 只使用已审核且授权的本地镜头生成可编辑故事板；低置信或素材不足时等待人工决定。
5. 用户可在同一 CreationTask 时间线修改并持久化字幕；FFmpeg 将确认后的中文字幕烧录进非空 MP4，同时生成独立 SRT；ffprobe 证明视频流、音频流、时长和尺寸有效，真实抽帧证明字幕在安全区内可见。
6. 客户端能播放 MP4、查看字幕并通过原生保存对话框导出。
7. 1024×768、1180×800 和主机最大尺寸没有横向溢出、遮挡、逐字换行或不可达操作。
8. 退出后子进程和端口清理；重启后仍能找到同一任务和成片。
9. 运行日志和源码证明新链路没有 Episode/Batch 写入。
10. 下表全部旧目标达到 `RETIRED`，并完成不存在证明，才允许 `END_TO_END_ACCEPTED`。

## 5. 旧系统退役矩阵

| 旧目标 | 当前消费者 | 替代路径 | 删除条件 | 当前状态 |
| --- | --- | --- | --- | --- |
| `/topics`、`/script`、`/draft` | 对应 App Router 页面、`app/actions.ts`、旧链接和认证测试假设 | `/tasks/{taskId}` 内的选题、文案、剪辑与成片阶段 | 新纵向闭环在同一候选真实通过 | `RETIRED` |
| `/api/episodes/*` 创作写接口与 `EpisodeRepository` 新写入 | 选题、文案、配音、视频、草稿、发布旧流程 | CreationTask 接口与 `ProductionRepository` | 新链路无 Episode 写入；旧写接口无活动消费者 | `RETIRED` |
| `/api/drafts/package`、`/api/drafts/import` | 旧 Draft 页面、API 客户端、Server Actions 和测试 | CreationTask montage/render/output 与原生保存 | MP4/SRT 真实生成和保存通过 | `RETIRED` |
| `/api/production/batches*` 及 Episode 驱动剪辑入口 | `/production`、`ProductionTaskControls`、旧 montage API | 单个 CreationTask 的剪辑计划和渲染 | 新剪辑与恢复从 CreationTask 完成 | `RETIRED` |
| `repo.today()` 与旧自动 Episode 循环 | onboarding、draft status、automation 和旧 API | 显式 workspace + CreationTask；本轮不启用自动任务 | 没有默认/最近/今日 fallback | `RETIRED` |
| 旧快捷键、帮助入口、用户名称、死 CSS 和保护旧产品行为的测试 | 活动 Web 与测试代码中的剩余命中 | 当前 AppShell、DESIGN 和 CreationTask 工作台 | 剩余命中逐项分类，活动消费者归零 | `RETIRED` |

允许共存仅用于读取既有历史数据或尚未迁移的非本合同功能；不得产生新的 Episode/Batch 业务写入。共存到期条件是第 4 节正向验收通过，之后必须进入删除和不存在证明。

## 6. 基线能力真实性（2026-08-31）

| 用户动作 | 当前证据等级 | 状态与行为 |
| --- | --- | --- |
| 创建任务、选题、确认文案 | `PERSISTED_EFFECT_CONFIRMED`（既有验证任务） | 可继续使用，但最终验收必须在同一候选重跑 |
| 文案失败与恢复 | `FAILURE_PERSISTED / RECOVERY_CONFIRMED` | `task_f2b0470c0ee2` 暴露模型改写长证据引用 ID 的 502；服务端拒绝且未保存无效文案。修复改为服务端短别名到权威引用 ID 的闭合映射，未知别名仍拒绝；同一任务随后从文案恢复点完成配音、剪辑、成片、保存和重启 |
| 最终 App 全新任务冷启动 | `REAL_INTERACTION_CONFIRMED / END_TO_END_ACCEPTED` | 固定候选 `e8b2b17 / f5540b…` 从首页创建 `task_fa6751ee08c7`，真实预检后恰好产生 1 条成功选题运行和 1 条成功文案运行，均使用 `deepseek-v4-flash`；用户确认后以 `cc / v1` 和 Qwen3-TTS 生成 20.4 秒 WAV，确认 2 个低置信镜头，生成 4 镜头故事板、独立 SRT 与烧录字幕的 MP4。任务停在 `review / waiting_for_user`，退出重启后任务、文案、配音、故事板和播放器全部恢复 |
| 回看选题和文案、全局搜索、阶段导航 | `REAL_INTERACTION_CONFIRMED@66fad77` | 1024/1180/1440 已验证，不代表成片完成 |
| Qwen3-TTS 中文声音克隆 | `REAL_OUTPUT_CONFIRMED` | 已使用用户授权的 `cc` 人物/声音版本生成约 20.16 秒有效 WAV；未使用系统声音、静音或伪造回退 |
| 配音阶段补选声音并原地恢复 | `PERSISTED_EFFECT_CONFIRMED / REAL_INTERACTION_CONFIRMED` | 已在同一 CreationTask 绑定授权声音并原地继续，没有重做已确认文案 |
| 故事板、FFmpeg MP4/SRT | `REAL_OUTPUT_CONFIRMED / REAL_INTERACTION_CONFIRMED` | 已生成 6 个镜头的可编辑故事板、独立 SRT 与烧录中文字幕的 1080×1920 H.264/AAC MP4；ffprobe 证明时长约 20.23 秒且音视频流有效 |
| Electron 原生保存 | `REAL_INTERACTION_CONFIRMED / REAL_OUTPUT_CONFIRMED` | 最终打包候选已打开原生保存框并导出 `/Users/Zhuanz/Movies/task_d860f92365c5.mp4`；文件 5,178,349 bytes，SHA-256 `30ac5bed7a8313c0bdf41b8493cba6f629289ff802e0f7ad31d070d8422986d1`，与 API 成片逐字节一致 |
| 旧 Web 入口与可见名称 | `ABSENCE_VERIFIED` | `/topics`、`/script`、`/draft` 在真实浏览器均为 404；`/production` 仅保留 CreationTask 创建和列表，任务页无 Episode/Batch 可见名称 |
| 旧 API 写事实源 | `RUNTIME_CALL_CONFIRMED / ABSENCE_VERIFIED` | 当前 API 对旧写命名空间统一返回 410，CreationTask 仍返回 200；旧仓储 writer、隐式 GET 写回和 Episode 自动循环已移除 |
| 当前回归门禁 | `STATIC_WIRING_CONFIRMED` | API 全量 355 项、Web 全量 175 项、TypeScript、ESLint（0 error）与 Desktop lifecycle smoke 通过；1024×768、1180×800、1440×900 无横向溢出或不可达主操作 |
| 打包客户端 | `REAL_TARGET_VERIFIED` | 最终候选运行时自报 build identity `f5540b…`，API/Web 均 `ready`，CreationTask 返回 200、旧 Episode POST 返回 410。播放器真实加载 1080×1920、20.533 秒 H.264/AAC MP4；WAV 为 24kHz PCM、20.4 秒；SRT 有 4 条字幕，抽帧可见烧录字幕。原生保存导出 `/Users/Zhuanz/Movies/task_fa6751ee08c7.mp4`，与源成片 SHA-256 同为 `2d510a800f8b992505e4b4c288ee399e43497db0383ae1a5718f62aafe466b84`。1024×768、1180×800、1440×900 无横向溢出且保存按钮可达；原生退出后子进程和端口清理，重启后全部恢复。完整 App bundle 仍未签名，因此只允许本机小范围体验，Release 明确 `NOT ACCEPTED` |
| 独立质量复核 | `HOLD` | 唯一独立质量线程在固定候选验收中再次超过限定时间，未给出一次性 ACCEPT/REJECT，已按停止规则中断。主线程证据不能替代独立结论；不再启动质量循环，也不因此修改已通过的产品链路 |

## 7. 阻断、停止和授权

- DeepSeek、授权声音、参考 WAV/文字、Qwen3-TTS 环境或模型、FFmpeg、输出目录、移动硬盘和素材授权任一缺失时，结论为 `BLOCKED`，不得伪造成功。
- 测试入口冲突时先判断测试工具；同类问题连续两次没有新产品证据，立即 HOLD，不追加 QA 编号。
- 只允许本机 macOS arm64、小范围测试和合法素材。禁止签名、公证、上传、平台发布、创建密钥、改变外部权限或删除用户数据。
- 实现阶段保持一个纵向负责人；只有纵向链路完成后才启用一个独立质量线程验收。

## 8. 最小验证与合同结束

实现阶段先验证相关 API/仓储/组件，再在开发环境完成一次真实闭环；之后只构建一次 App/DMG。构建、类型检查、静态路径或单元测试不能替代真实点击、持久化 WAV、MP4/SRT 和原生保存证据。

合同只有在 `REAL_TARGET_VERIFIED → OLD_PATH_DISABLED → OLD_PATH_REMOVED → ABSENCE_VERIFIED → MIGRATION_ACCEPTED` 全部完成后结束。届时删除本文件；如果用户改变范围或事实源，旧合同立即失效并用新合同整体替换。
