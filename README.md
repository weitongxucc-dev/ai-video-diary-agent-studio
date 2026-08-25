# AI 视频日记 Agent Studio

**本地 AI 内容生产工作站。** 面向由一名操作员负责出片的知识内容团队，把维护的信息源、授权声音、已审核素材、自动选题、文案、视频处理和剪映草稿串成一条可检查、可阻断、可回滚的生产链。

## 当前状态

当前主线定位为 **macOS 小范围内部测试版**：

- Next.js 15 工作台、FastAPI API、自动化 Worker 和 Electron 桌面壳已经连通。
- 内部 macOS 构建显式使用 `NEXT_PUBLIC_AUTH_MODE=local`，面向可信测试者，不依赖 Supabase 登录。
- 分发模式只向用户呈现账号与密码；Supabase JWT 和 workspace 隔离作为内部实现，只有显式配置后才启用。
- GitHub Actions 可以分别生成 arm64 与 x64 内测包；正式发布必须通过 Developer ID 签名和 Apple 公证。

这不是公开发行版。真实客户端验收、签名公证、自动更新和小规模用户反馈仍是发布门禁。

## 目标生产流程

```text
团队维护的信息源
→ 事实门禁下的自动选题与文案
→ 授权声音配音
→ 已审核素材库选片
→ 固定品牌模板编排
→ 9:16 初版成片（默认 90 秒，时长可配置）
→ 最终成片审核
→ MP4 / 字幕 / 剪映兼容草稿
```

主要能力包括：

- 原始素材与已审核可用素材的分层管理。
- ffmpeg 镜头切分、时长探测和可恢复的分析状态。
- 固定品牌档案、多个授权声音档案、默认/临时声音选择、来源池和生产批次门禁。
- 片段系统标签、自定义标签夹、素材使用频次和覆盖缺口管理。
- 文案分段、已审核镜头检索、可追溯匹配评分和低置信人工确认。
- FFmpeg 9:16 时间线渲染、旁白合成及独立 SRT 字幕输出。
- 无云端 Key 时的本地来源型选题/文案降级，以及用户自带 DeepSeek Key 的可选增强。
- Obsidian、Hermes 日报、Product Hunt 与外部链接信息源。
- SQLite Episode 持久化、Creator Profile 和可撤销偏好记忆。
- ffmpeg、本地 Whisper 与腾讯云 ASR 视频处理链路。
- 可人工覆盖并回滚的剪辑计划。
- 字幕、封面、素材清单和剪映兼容草稿包。
- 本地运行健康检查、配置修复入口和自动化 Worker。

## 仓库结构

```text
apps/web       Next.js 工作台
apps/api       FastAPI、工作流、视频处理和 Worker
apps/desktop   Electron 壳、打包与发布配置
supabase       分发模式所需的数据库迁移
docs           当前有效的产品与安全文档
```

## 本地开发

需要 Node.js 22、pnpm 9.15、Python 3.11、uv 和 ffmpeg。

```bash
corepack pnpm install --frozen-lockfile
cd apps/api && uv sync --locked --group dev && cd ../..
cp .env.example .env
```

分别启动：

```bash
corepack pnpm dev:api
corepack pnpm dev:web
corepack pnpm dev:worker
```

访问 `http://localhost:3000`。桌面开发和打包见 [`apps/desktop/README.md`](apps/desktop/README.md)。

## 配置

完整模板见 [`.env.example`](.env.example)。最小可用配置是：

- `NEXT_PUBLIC_AUTH_MODE=local`：仅用于可信的本机开发/内部测试；否则必须配置 Supabase 模式。
- `DEEPSEEK_API_KEY`：可选的云端文案增强；未配置时使用本地来源型生成器。
- `DATABASE_PATH`：本地 Episode 数据库。
- `VIDEO_INBOX_PATH`、`VIDEO_OUTPUT_PATH`、`DRAFT_OUTPUT_PATH`：本地视频流程目录。
- `OBSIDIAN_VAULT_PATH`：可选的本地知识库。
- 腾讯云 ASR、Whisper、Product Hunt 和 Supabase 均为按需能力。

密钥不得提交到 Git、文档、截图或日志。`.env*` 已默认忽略，只有 `.env.example` 可跟踪。

## 验证

```bash
corepack pnpm run test:web-contract
corepack pnpm run test:design-system
corepack pnpm run test:web
corepack pnpm run lint:web
corepack pnpm run build:web
corepack pnpm run test:api
```

涉及 UI 时还必须遵循 [`DESIGN.md`](DESIGN.md)，并在 1024、1180、1440 三种桌面尺寸下做真实页面或 Electron 截图验收。

## 已知边界

- Chinese-CLIP 多帧向量提取、缓存失效和文案匹配已经接入独立模型进程；自动放行阈值仍需真实素材基准校准。
- Qwen3-TTS 授权声音克隆、参考文字校验及真实配音时长回填已经接入；安装包不内置权重，首次配置时由产品内下载器安装到用户选择的模型目录。当前没有云端声音克隆降级，未安装本地模型时该能力不可用。
- 时间线与 FFmpeg 渲染已接入，但整个批次的无人值守执行器尚未接入。
- 剪映 10.7 加密草稿仍只能使用兼容桥和可追踪素材包，不能声称原生写入成功。
- 真实麦克风采集尚未接入；命令台接收文本或已有转写结果。
- 腾讯 ASR 大文件尚未接入 COS/URL 模式。
- 正式 macOS Release 需要仓库配置完整的签名与公证密钥。

## 当前文档

- [`DESIGN.md`](DESIGN.md)：唯一视觉与交互源文件。
- [`docs/product-requirements.md`](docs/product-requirements.md)：当前产品范围与发布门禁。

安装包只包含轻量模型 worker，不包含大模型权重。Chinese-CLIP 下载约 0.75GB，Qwen3-TTS 下载约 1.99GB，另需为独立 Python 运行时和缓存预留空间。两者在「设置 → 本地模型安装」中逐个安装；安装器会先要求用户确认许可证、检查剩余空间，并将运行环境、权重、断点文件与 pip/Hugging Face/Torch 缓存放在所选模型目录附近。`/Volumes/<name>` 未挂载时会阻止安装，不会静默改用内置硬盘。
- [`docs/beta-product-foundation.md`](docs/beta-product-foundation.md)：内部与分发模式的身份、首启边界。
- [`docs/auth-workspace-boundary.md`](docs/auth-workspace-boundary.md)：Supabase 模式的后端安全边界。
- [`apps/desktop/RELEASE.md`](apps/desktop/RELEASE.md)：内测构建和正式发布流程。

历史计划、旧审查快照和阶段性截图不保存在主线；需要追溯时使用 Git 历史。
