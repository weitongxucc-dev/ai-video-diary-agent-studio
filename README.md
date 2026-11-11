# 曜核 · AI 视频日记

面向非专业销售人员和个人内容创作者的本地视频工作台：整理已有素材，按目标自主创作或共同定稿，在同一剪辑台查看粗剪并通过多轮对话与专业控件精修、输出视频。

## 当前开发入口

当前先完成 **0.11 电脑端 AI 自动剪辑完整闭环**。0.12 简单手机联动保留共同集成范围，按[产品交付边界](docs/product-requirements.md#11-版本交付边界)在桌面核心通过后推进，不能成为桌面验收前置；版本名称保留，不表示已交付，也不以原型示例代替真实服务。

| 要了解什么 | 入口 |
| --- | --- |
| 产品范围、工作空间、共享与验收 | [产品需求](docs/product-requirements.md) |
| 当前开发分工、允许修改的文件与接线限制 | [活动合同](.project-control/active-contract.md) |
| 需求覆盖、验收样本与未决事项 | [版本开发清单](.project-control/0.11-development-plan.md) |
| 界面布局、中文命名和已认可原型 | [设计规范](DESIGN.md) |
| 实施跟踪、审查与自动检查 | [GitHub Issues](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/issues)、[PR](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/pulls)、[CI](https://github.com/weitongxucc-dev/ai-video-diary-agent-studio/actions) |
| Pi、业务内核与存储的分工 | [系统架构](docs/system-architecture.md#315-pi-复用与视频创作-agent) |

开发分支、main 和安装包是不同交付对象。README 不维护完成率、测试次数或最新提交号；判断交付以对应提交、审核证据和运行结果为准。

## 目标使用流程与边界

以下为产品目标，不表示全部能力已实现或通过真实验收；当前施工授权见活动合同，运行结果查对应候选证据。

1. 首次个性化设置均可跳过。按需电脑/手机采样声音、设置人物，或主动扫描电脑并选择候选素材入库；后台分析已接收内容。
2. 给出制作目标让Agent在授权范围自主完成，或通过对话确定最终文案后开始；先获得同一作品的真实粗剪。
3. 通过工作台引用选中对象，通过多轮对话或精确控件修改、试听/播放并撤销。独立业务任务有各自对话，浏览资源与切换任务分别处理。
4. 检查与输出，发布前由用户确认，按实际平台能力记录结果。

知识、人物、声音和模板用于复用；成员私有内容与团队共享内容分开。CLI/MCP及飞书、钉钉的指挥能力属于交付目标，实际可用命令和缺口见[CLI说明](docs/cli.md)，接入规则见[架构](docs/system-architecture.md#316-climcp与平台消息适配)。具体范围由产品需求拥有。本机后台分析与视频制作共用资源，调度模块的局部通过不代表所有媒体入口已接通，更不代表五人体验验收完成。

## 仓库结构

```text
apps/web       Next.js 工作台
apps/api       FastAPI、领域仓储、模型适配和视频处理
apps/desktop   Electron 壳、打包与发布配置
apps/mobile    Flutter 手机伴侣，版本边界见产品需求
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

以下为可选命令入口，不是每次修改需要顺序执行的清单；按[最小验证矩阵](docs/development-workflow.md#7-最小验证矩阵)选择本次风险对应的检查，优先定位到相关测试。

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
