# AI 视频日记 Agent Studio

**本地优先的 AI 视频生产工作站。** 把自己的内容、外部信息源、本地素材、授权人物与声音、模型服务和平台连接组织成可审核、可复用、可定时运行的生产链。

## 目标生产流程

```text
知识库 + 外部信息源 + 本地素材 + 人物与声音
→ 首页自动搭建可运行方案
→ 素材 → 选题 → 制作 → 审核 → 发布 → 监控
→ 成功任务保存为视频模板
→ 视频模板创建自动任务并持续运行
```

产品范围与验收见 [`docs/product-requirements.md`](docs/product-requirements.md)；
领域名词、设计和架构分别以
[`docs/domain-model.md`](docs/domain-model.md)、[`DESIGN.md`](DESIGN.md) 和
[`docs/system-architecture.md`](docs/system-architecture.md) 为准。

## 仓库结构

```text
apps/web       Next.js 工作台
apps/api       FastAPI、领域仓储、模型适配和视频处理
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
```

访问 `http://localhost:3000`。桌面开发和打包见 [`apps/desktop/README.md`](apps/desktop/README.md)。
旧 Episode 自动化 Worker 不属于当前默认运行链，不要为本地开发单独启动。
新的自动任务将在 `CreationTask`、`Automation` 和持久化运行记录接入后提供独立入口。

## 配置

完整模板见 [`.env.example`](.env.example)。身份模式必须显式选择；账号和
workspace 边界见
[`docs/auth-workspace-boundary.md`](docs/auth-workspace-boundary.md)。密钥不得提交到
Git、文档、截图或日志；`.env*` 默认忽略，只跟踪 `.env.example`。

## 验证

```bash
corepack pnpm run test:web-contract
corepack pnpm run test:design-system
corepack pnpm run test:web
corepack pnpm run lint:web
corepack pnpm run build:web
corepack pnpm run test:api
```

涉及 UI 时必须遵循 [`DESIGN.md`](DESIGN.md)。涉及发布时以实际 workflow
和 [`apps/desktop/RELEASE.md`](apps/desktop/RELEASE.md) 为准。

## 文档

权威关系和渐进式加载路由见 [`docs/README.md`](docs/README.md)。历史计划、
旧审查快照和阶段性截图使用 Git、PR、Actions 或 Release 追溯。
