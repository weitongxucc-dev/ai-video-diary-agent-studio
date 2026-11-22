# 曜核 WCE 集成交接文档

> 交接时间：2026-11-22 11:40 UTC
>
> 文档性质：当前集成工作的临时交接入口。产品规则、领域对象、架构边界和设计规则仍以项目权威文档为准。本文件记录接手者需要知道的执行事实、验收缺口和顺序。

## 1. 当前基线

| 项目 | 当前值 |
| --- | --- |
| 集成工作树 | `/Volumes/Dujiangyan-VM/AI视频日记/repositories/wce-integration-audit` |
| 集成分支 | `codex/wce-integration-audit` |
| 集成代码基线 | `06fbdfe4` |
| 交接文档提交 | `4d089c4c`、`70f01752` |
| Pull Request | [PR #181](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/pull/181) |
| PR 状态 | Draft / Open，目标分支 `main` |
| 最近代码 CI | Run `45017079596`，8 个任务全部成功 |
| 文档提交 CI | 文档提交会触发新的 CI，接手时按 PR head 查询最新结果 |
| 主工作区 | `/Volumes/Dujiangyan-VM/AI视频日记/repositories/main`，存在大量用户已有 dirty/untracked，禁止 reset、restore、覆盖或清理 |
| 任务看板 | Multica 负责任务状态和验收证据，当前以 PR、Git、CI 和线程回执为实现事实 |

主工作区与集成工作树必须继续分开。后续共享组装、验证和提交只在集成工作树进行。

## 2. 已完成并已进入集成分支

### 2.1 工作台、作品与 Session

- 应用外壳、Project、CreationTask、Session、新建、历史切换和刷新/重启恢复已经接入。
- 同一 CreationTask 可以有多个独立 Session。
- 同一任务下不同 Session 共享任务级当前作品和媒体结果，切换 Session 不会隐式新建视频任务。
- 不同 CreationTask、不同 workspace/member 的媒体 receipt 和 output 路由已经做隔离。
- 右侧作品台按当前作品和版本读取，不把 Session 标识误当成独立作品。

WCE-3 只读隔离验收覆盖同一任务 Session A/B、跨任务和跨成员场景，定向 API 测试 `16 passed`，无新增代码提交。

### 2.2 Agent 对话时间线

- Pi 事件、SSE、Trace 和 Web 对话栏映射已经接入。
- 已覆盖停止/取消、运行中追加输入、重试、持久化回读、usage receipt、脱敏和 Provider adapter contract。
- OpenFilm 回放 fixture 已验证 33 个事件以及 C0–C8 检查点。
- 私有 reasoning 不进入主阅读流，工具动作和状态由事件派生。
- Web 对话栏及 AssistantSendControl 定向回归通过。

WCE-4 的线程分支曾有一个旧断言没有包含新增 `provider/model` 字段。集成分支的相关测试已更新，当前集成验证通过。真实 Provider API key、真实模型回合、供应商 usage 和账单仍未验证。

对话栏专项基线和回放输入见：

- [`docs/reference/openfilm-chat/SPEC.md`](../reference/openfilm-chat/SPEC.md)
- [`docs/reference/openfilm-chat/replay-fixture.json`](../reference/openfilm-chat/replay-fixture.json)
- [`docs/reference/openfilm-chat/GAP.md`](../reference/openfilm-chat/GAP.md)
- [`docs/reference/openfilm-chat/YAOHE-ADAPTATION.md`](../reference/openfilm-chat/YAOHE-ADAPTATION.md)

### 2.3 媒体执行与公开结果

- FFmpeg Media Execution Layer 已接入 TaskMediaJobs。
- POSIX 进程组取消、超时、重试和外层取消事件透传已经实现。
- `media_output` 公开结果契约已经接入。
- 公开媒体回读会同时检查 ArtifactRecord、TimelineVersion、registered output refs、producer job、实际文件和文件大小。
- 受控 output route 只有在上述事实都存在且一致时才返回可读输出。
- 修复了内层媒体作业创建 child TimelineVersion 后，外层旧 base version 检查错误判定 stale 的问题。
- 取消不创建 ArtifactRecord 或 TimelineVersion，重试使用新的 inner media job。

WCE-6 进程组取消测试连续三次通过。当前真实设备资源档仍未准备，因此正式 HTTP 媒体提交返回 `503/device_profile_unprepared`，不会创建 Job。

### 2.4 Provider 与资源治理

- Provider 目录、入口协议、默认模型、认证字段、可恢复错误、usage 字段和 secret redaction 已完成目录治理。
- Anthropic native Messages runtime 已补齐并复用现有 Pi trace/usage/redaction 边界。
- ffmpeg-skill v3.12.1/MIT 已完成 16 项能力登记，未接通项明确保持 `runnable=false`、`catalog_only` 或 `planned`。
- 编辑器资源面板支持 `connected`、`catalog_only`、`planned`、`failed`，显示版本、来源、运行要求和 adapter 状态。
- 没有真实 adapter 时不显示“添加到作品”，没有用目录记录伪造可执行能力。
- 人物、声音和素材沿现有真实目录/素材入口承接，设备准备由独立 DeviceResourcePreparationPanel 负责。

## 3. 当前必须完成的工作

### P0：完成集成分支收口

1. 检查 PR #181 是否有新的 review comment、冲突或分支漂移。
2. 保持集成分支与远端一致，确认文档提交后的 CI 仍通过。
3. 将本交接文档链接同步到 PR 和 Multica。
4. 在用户确认后将 Draft PR 转为 Ready，合并动作单独执行，不在交接阶段默认合并到 `main`。

验收结果是 PR 的 head、CI 和工作树状态一致，且没有把“集成分支通过”表述成“主分支已发布”。

### P0：设备资源准备后的真实媒体闭环

需要使用同一个已安装桌面应用、同一个 `userData`、同一个设备身份执行。不能在 API 测试中伪造设备档。

执行顺序：

1. `GET /api/device/resource-preparations`，确认 `profile_status`、`preparation_configured`、`media_execution_ready` 和 `production_blocker`。
2. 如果存在 `production_blocker`，先在原应用收束旧制作或设备准备任务。
3. `POST /api/device/resource-preparations`，按实际媒体规格提交 CPU/FFmpeg 准备。
4. 轮询 `GET /api/device/resource-preparations/{id}`，只有 `state=done` 才调用 finalize。
5. `POST /api/device/resource-preparations/{id}/finalize`。
6. 再次读取准备状态，必须满足 `profile_status=available`、`cpu_render` 可用、`media_execution_ready=true`、`production_blocker=null`。
7. 提交真实媒体任务，记录外层 job、内层 media job、request_id、base_version_id、child timeline version、artifact ref 和实际文件。
8. 分别验收成功、取消、超时、失败、重试、版本冲突和 GET 回读。
9. 通过受控 output route 下载并播放最终成片，检查文件摘要、时长、画面和音频。

成功标准：真实提交返回 202，成功结果同时有 TimelineVersion、ArtifactRecord、registered refs 和实际文件。取消/失败不留下虚假产物，重试生成新 job，旧 job 状态和事件可回查。

当前明确阻塞：设备资源档未准备。

### P0：真实 Provider 验证

接口和目录可以先保持现状，Provider key 不阻塞当前接口开发。拿到 key 后按 Provider 逐个验证：

- 凭据保存与脱敏
- endpoint、provider、model 绑定
- 最小真实文本回合
- Pi 事件、SSE、trace 和 usage receipt
- 可恢复错误和停止/重试
- 真实供应商 usage 与费用字段
- 不同 Provider 的模型名、认证方式和 base URL 约束

每一家都要单独记录 provider、model、request_id、usage 状态和失败原因。没有 key 的 Provider 保持 `configured=false` 或未知，不标记为已验证。

### P1：真实桌面/Pi 组合验收

需要在桌面应用实际入口验证一条完整用户路径：

1. 新建工作任务。
2. 新建或切换 Session。
3. 在对话栏提出自然语言剪辑需求。
4. 观察旁白、动作、进度、失败和停止表现。
5. 检查右侧作品台是否显示真实素材、文案、声音、人物、时间线和导出变化。
6. 刷新或重启后恢复同一作品、Session 和版本。
7. 新建第二个任务，确认不会串台。

需要保留截图/录屏、原始请求、job/trace、保存状态和最终作品页证据。组件测试、回放页和文本回执不能替代这一验收。

### P1：作品台和产品布局重构

当前代码已具备基础闭环，但整体产品级布局仍未定稿。接下来需要按页面走查，而不是继续局部补丁：

- 首页/常驻作品台的入口层级和“新建工作任务”路径
- 中间对话栏顶部的作品、Session、范围和动作层级
- 左侧导航、项目/作品/任务/Session 的关系
- 右侧创作工作台的当前版本、预览、时间线和结果入口
- 素材库、人物库、声音库的呈现位置和快捷编辑区
- 空状态、加载、运行、失败、取消、完成、归档和恢复状态
- 工具行、旁白、进度、技术详情和错误文案
- 多个“+”、面板图标、更多菜单、返回和新建动作的语义冲突
- 窄窗口和桌面最小窗口下的层级、滚动和可操作性

本项必须先形成页面级问题清单和目标信息架构，再分批实现。视觉基线归 `DESIGN.md`，产品对象和状态归 `docs/domain-model.md`，不能用截图直接替代产品契约。

### P1：素材库、人物库、声音库

当前资源面板解决了目录状态和采用边界，尚未完成完整的资源工作区设计。需要确定：

- 资源库是全局工作区资产、作品级资产还是任务级临时资产
- 人物、声音、原始素材、音乐、模板和 Skill 的层级关系
- 标签、版本、来源、授权、provenance 和运行要求的编辑位置
- 预览、试听、试用、采用、替换、撤销和回滚入口
- 资源采用后如何显示在右侧作品台和当前 Session
- catalog-only/planned/failed 资源如何解释、筛选和恢复
- 资源被多个任务复用时的版本锁定与影响范围

这部分要先确定事实源、归属和写入事务，再做 UI。不能在面板里增加没有真实后端入口的快捷按钮。

### P1：多 Session 与多 Agent 分支创作

多 Session 的价值应定位为同一作品下的独立创作路径，而不是固定三种风格模板。需要实现：

- Agent 根据用户目标和素材情况提出若干可选方向。
- 用户可接受任意数量的方向，数量由用户或 Agent 动态决定。
- 每个方向拥有独立 Session/分支版本和明确的输入快照。
- 多个分支可以并行生成不同剪辑方案。
- 右侧作品台能够比较、预览、标记候选、选择采用版本。
- 采用前不覆盖当前正式版本，采用后生成新的 TimelineVersion。
- 能回到原始版本，能删除或归档候选分支，能追溯来源和操作。
- 取消某个分支不会取消其他分支，失败只影响对应分支。

最小验收是一个真实素材任务产生两个以上不同方向，用户可以分别预览、比较和选择，且当前正式作品不会被未采用分支覆盖。

### P2：ffmpeg-skill 和 CreatorHub 能力吸收

当前已完成能力登记和资源治理边界，后续要逐项决定宿主适配：

- 先固定曜核自己的 FFmpeg/FFprobe 构建、编码器、滤镜和字幕字体许可。
- 为 probe、抽帧、剪辑、拼接、音频、画幅、字幕、导出分别确认真实入口、输入输出和事务边界。
- 缺少 JobRun、进度、取消、回查、产物登记和版本绑定时保持不可运行。
- CreatorHub 的自动发布、平台适配、发布状态、失败重试和审核信息只能吸收为曜核自己的领域能力，不能直接复制为第二套发布事实源。
- 发布链路必须晚于本地作品、版本和真实成片回读闭环。

### P2：跨端、发布和生产化

- Windows 分支同步和真实 Windows 验收独立核对，不能由 Mac 验收外推。
- Electron 桌面打包、签名、公证和安装升级按桌面文档与 workflow 单独验收。
- 发布平台、账号、权限、草稿、失败重试和审核状态建立独立事实源。
- 多设备资源许可、并发额度、取消和恢复需要真实设备验证。
- 生产环境日志、隐私、secret redaction、错误回执和数据恢复需要做最终检查。

## 4. 推荐集成顺序

1. **收口当前 PR**：确认 CI、PR review、文档同步和分支一致性。
2. **准备真实设备档**：在同一桌面 userData 完成设备准备，不改代码绕过。
3. **验收媒体闭环**：真实提交、取消、重试、回读、下载、播放和版本冲突。
4. **验证 Provider**：按实际拿到的 key 逐家验证真实回合和 usage。
5. **桌面/Pi 组合验收**：完成新建任务、Session、对话、作品台和恢复路径。
6. **页面级产品走查**：冻结信息架构和布局目标，再做 UI 批次。
7. **素材库与多分支创作**：先定资源事实源和分支版本模型，再实现编辑和比较。
8. **ffmpeg-skill/CreatorHub 逐项适配**：每项能力独立提升状态，保持不可用项诚实。
9. **Windows、打包、发布和生产化**：在本地真实闭环稳定后进入跨端和外发。

## 5. 接手时的职责与线程边界

### 总控

- 保持共享契约、集成分支、PR 和验收顺序唯一。
- 只使用左侧可见的执行线程派工，当前限制最多 5 条，优先沿用 WCE-3、WCE-4、WCE-6。
- 收回每条线程的 changed files、commit、测试、持久化/UI/真实运行证据和阻塞项。
- 不把线程完成、组件测试或目录登记表述为整体验收完成。
- 每次集成后同步 PR 和 Multica，记录未验证项。

### WCE-3

负责应用外壳、Project/CreationTask/Session、当前作品绑定、跨 Session/任务隔离和工作台回读。涉及共享契约时先停写并向总控报告。

### WCE-4

负责 Pi 事件、SSE/Trace/UI 映射、停止、追加、重试、Provider adapter contract、usage 和回放。真实 Provider key 和桌面组合验收由总控组织。

### WCE-6

负责 Media Execution Layer、FFmpeg 进程组、取消/超时/重试、TaskMediaJobs delegate、设备资源准备和受控输出回读。真实设备档和最终播放需要总控提供环境后执行。

### Multica

负责看板、任务状态、验收证据索引和阻塞可视化。GitHub PR/commit/CI 仍是代码与构建事实源，Multica 不替代 Git 或运行时证据。

## 6. 接手检查命令

```bash
# 保护主工作区，先确认不要在 main 上清理
cd /Volumes/Dujiangyan-VM/AI视频日记/repositories/main
git status --short --branch

# 进入集成工作树
cd /Volumes/Dujiangyan-VM/AI视频日记/repositories/wce-integration-audit
git status --short --branch
git rev-parse HEAD
git log -5 --oneline

# API 关键回归
cd apps/api
uv run pytest -q --disable-warnings \
  tests/test_task_media_submission_use_case.py \
  tests/test_task_media_api.py \
  tests/test_media_execution_layer.py \
  tests/test_assistant_host_boundaries.py \
  tests/test_assistant_pi.py

# OpenFilm 回放
uv run python scripts/replay_openfilm_acceptance.py

# Web 定向回归与类型检查
cd ../web
corepack pnpm exec vitest run \
  src/components/__tests__/AssistantConversationPanel.test.tsx \
  src/components/__tests__/AssistantSendControl.test.tsx \
  src/components/__tests__/EditorResourceLibraryPanel.test.tsx
corepack pnpm exec tsc --noEmit --pretty false
```

以上命令验证源码和定向测试。真实设备、真实 Provider、真实桌面/Pi 和最终成片仍要按第 3 节的现场流程验收。

## 7. 不能误判为完成的事项

- CI 全通过不代表真实设备媒体链已经通过。
- Provider adapter contract 不代表真实供应商 API、usage 或账单已通过。
- OpenFilm 回放不代表桌面应用实际渲染已经通过。
- `catalog_only`、`planned` 或 `failed` 资源不代表可以采用。
- 逻辑 output ref 不代表可下载文件，只有受控 output route 返回才是可读产物。
- Session 切换成功不代表多分支比较和采用已经完成。
- 页面组件通过不代表产品布局、信息架构和普通用户路径已经完成。

## 8. 当前第一步

接手者先确认：

1. PR #181 的 CI 和 review 状态以当前 PR head 为准。集成代码基线 `06fbdfe4` 的 Run `45017079596` 已 8/8 成功。
2. 设备资源准备接口当前状态和 `production_blocker`。
3. 是否已经拿到任一真实 Provider key。
4. Multica 是否已经记录本交接文档和当前阻塞。
5. 在以上事实确认后，只启动下一条有明确验收入口的线程，保持并行线程数量不超过 5 条。
