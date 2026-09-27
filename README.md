# 曜核 · AI 视频日记

面向非专业销售人员和个人内容创作者的本地视频工作台：整理已有素材，通过对话确定文案和要求，再制作、修改和输出视频。

## 当前开发入口

本仓库正在开发 **0.11 桌面端完整闭环**；**0.12 是简单手机端**。产品目标不等于已实现能力，原型示例不等于真实服务。

| 要了解什么 | 入口 |
| --- | --- |
| 产品范围、工作空间、共享与验收 | [产品需求](docs/product-requirements.md) |
| 当前开发分工、允许修改的文件与接线限制 | [活动合同](.project-control/active-contract.md) |
| 完整需求清单及 Linear 对应任务 | [0.11 开发台账](.project-control/0.11-development-plan.md#12-011-全量开发台账2026-09-27) |
| 界面布局、中文命名和已认可原型 | [设计规范](DESIGN.md) |
| 代码审查与自动检查 | [开发分支](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/tree/codex/0.11-development)、[PR #44](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/pull/44)、[CI](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/actions) |

开发分支、main 和安装包是不同交付对象。README 不维护完成率、测试次数或最新提交号；判断交付以对应提交、审核证据和运行结果为准。

## 使用流程与边界

1. 接收本地视频，后台整理和分析，用户可补充说明、纠正标签与评价素材。
2. 在创作助手中确定主题、文案和要求，明确确认后开始制作。
3. 预览成片，通过对话或精细修改调整画面、声音、字幕和指向说明。
4. 检查与输出，发布前由用户确认，按实际平台能力记录结果。

知识、人物、声音和模板用于复用；成员私有内容与团队共享内容分开。具体范围由产品需求拥有。本机后台分析与视频制作共用资源，调度模块的局部通过不代表所有媒体入口已接通，更不代表五人体验验收完成。

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
自动任务的范围见产品需求，实际运行入口以当前代码和对应验收为准。

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
