# AI 视频日记 Agent Studio

面向个人创作者的本地视频生产工作站。它把信息摄入、选题、脚本、视频处理、剪辑决策、剪映草稿和发布反馈串成一条可检查、可确认、可回滚的 Episode 工作流。

## 当前状态

当前主线定位为 **macOS 小范围内部测试版**：

- Next.js 15 工作台、FastAPI API、自动化 Worker 和 Electron 桌面壳已经连通。
- 内部 macOS 构建显式使用 `NEXT_PUBLIC_AUTH_MODE=local`，面向可信测试者，不依赖 Supabase 登录。
- Supabase 邀请登录、JWT 校验和 workspace 隔离仍保留，用于后续分发模式；只有显式配置后才启用。
- GitHub Actions 可以分别生成 arm64 与 x64 内测包；正式发布必须通过 Developer ID 签名和 Apple 公证。

这不是公开发行版。真实客户端验收、签名公证、自动更新和小规模用户反馈仍是发布门禁。

## 核心流程

```text
信息源 / Obsidian
→ 选题确认
→ 脚本生成与版本编辑
→ 视频导入与转写
→ 剪辑计划确认
→ 字幕 / 封面 / 剪映草稿
→ 人工发布
→ 反馈与偏好记忆
```

主要能力包括：

- DeepSeek 驱动的选题、脚本和段落改写。
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
- `DEEPSEEK_API_KEY`：选题和脚本生成。
- `DATABASE_PATH`：本地 Episode 数据库。
- `VIDEO_INBOX_PATH`、`VIDEO_OUTPUT_PATH`、`DRAFT_OUTPUT_PATH`：本地视频流程目录。
- `OBSIDIAN_VAULT_PATH`：可选的本地知识库。
- 腾讯云 ASR、Whisper、Product Hunt 和 Supabase 均为按需能力。

密钥不得提交到 Git、文档、截图或日志。`.env*` 已默认忽略，只有 `.env.example` 可跟踪。

## 验证

```bash
corepack pnpm run test:web-contract
corepack pnpm run test:pixel-style
corepack pnpm run test:web
corepack pnpm run lint:web
corepack pnpm run build:web
corepack pnpm run test:api
```

涉及 UI 时还必须遵循 [`DESIGN.md`](DESIGN.md)，并在 1024、1180、1440 三种桌面尺寸下做真实页面或 Electron 截图验收。

## 已知边界

- 剪映 10.7 加密草稿仍只能使用兼容桥和可追踪素材包，不能声称原生写入成功。
- 真实麦克风采集尚未接入；命令台接收文本或已有转写结果。
- 腾讯 ASR 大文件尚未接入 COS/URL 模式。
- 正式 macOS Release 需要仓库配置完整的签名与公证密钥。

## 当前文档

- [`DESIGN.md`](DESIGN.md)：唯一视觉与交互源文件。
- [`docs/product-requirements.md`](docs/product-requirements.md)：当前产品范围与发布门禁。
- [`docs/beta-product-foundation.md`](docs/beta-product-foundation.md)：内部与分发模式的身份、首启边界。
- [`docs/auth-workspace-boundary.md`](docs/auth-workspace-boundary.md)：Supabase 模式的后端安全边界。
- [`apps/desktop/RELEASE.md`](apps/desktop/RELEASE.md)：内测构建和正式发布流程。

历史计划、旧审查快照和阶段性截图不保存在主线；需要追溯时使用 Git 历史。
