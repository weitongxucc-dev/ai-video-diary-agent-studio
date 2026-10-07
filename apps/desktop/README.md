# macOS 桌面应用

Electron 负责启动本地 Next.js 和 FastAPI API，并管理窗口、端口、日志和子进程生命周期。

0.11 的对话助手调用本机已安装的 Pi（需要 Node.js 22.19+）。开发和当前测试包会从 PATH 或当前用户的 nvm 安装中寻找 `pi`；也可在私密运行配置中设置 `AI_VIDEO_DIARY_PI_EXECUTABLE`。业务工具随桌面包提供，Pi 和 Node.js 尚未随包分发；缺失时对话入口会明确报错，原有精确编辑入口仍可使用。对话会话写在所配置模型盘的同级 `agent/` 目录。

安装 Node.js 22.19 或更高版本后执行 `npm install -g @earendil-works/pi-coding-agent`，用 `node --version` 和 `pi --version` 核对可执行程序，再重新打开应用。当前本机协议验证使用 Pi 1.0.4 / Node 24；体检分别显示 Pi、Node 和业务工具文件状态，仅阻断对话助手。文件就绪和 Key 已保存都不等于模型请求或业务 HTTP 工具成功，仍需在对应功能入口验证。

## 开发环境

需要 Node.js 22、pnpm 9.15、Python 3.11、uv 和 ffmpeg。先在仓库根目录安装依赖：

```bash
corepack pnpm install --frozen-lockfile
cd apps/api && uv sync --locked --group dev
```

开发时分别启动 API、Web 和 Electron：

```bash
corepack pnpm dev:api
corepack pnpm dev:web
corepack pnpm --dir apps/desktop dev
```

## 本地构建

```bash
corepack pnpm --dir apps/desktop build:mac
```

该命令会构建 Next.js standalone、PyInstaller API 和 Electron 包。产物位于 `apps/desktop/dist/`，该目录不进入 Git。当前打包客户端只启动 API 和 Web；遗留 Worker 不进入默认构建或包资源。

本机 Python/OpenSSL 和 CPU 架构差异容易产生只能在构建机运行的二进制。发给测试者时，应优先使用 GitHub Actions 的原生 arm64/x64 内测构建，而不是随手转发本机构建。

## 运行结构

```text
Electron main
├── Next.js standalone
├── FastAPI api-server
└── BrowserWindow
```

主进程必须等待 API 和 Web 健康后再加载工作台；退出时关闭其拥有的子进程。打包后健康检查失败应显示可恢复错误，不能留下后台孤儿进程。

打包前 Electron Builder 会生成并随包写入 `build-identity.generated.json`。它对实际进入包的桌面运行文件、API 二进制、模型 worker、已物化的 Web tree 以及打包配置按排序路径和内容计算候选输入摘要；不依赖 Git 状态，因此实际被打包的未跟踪文件同样会改变身份。主进程只接受该元数据，生产模式缺失或无效时会明确拒绝启动。生命周期日志的每条记录、运行时 IPC 和“关于”窗口都显示同一 `candidateInputDigest`；它是候选输入身份，不是 `.app` 文件清单或 DMG SHA。日志还记录 API/Web 的退出 `code` 与 `signal`。日志管道被父进程关闭时，主进程仅忽略对应 stdout/stderr 的 `EPIPE`；其他日志流错误仍会使客户端明确失败并停止服务。日志不记录 `.env`、密钥或模型/用户配置路径。

桌面主进程首次启动会在 userData 下生成并持久化设备身份 `device-identity.generated.json`（`device_id` 为随机 UUID、`proof` 为 64 位小写 hex），并在启动本地 API 子进程时以 `DEVICE_ID`/`DEVICE_PROOF` 环境变量注入（与 Supabase 凭据同源，渲染进程不可观测）。文件缺失视为首次启动而生成；文件存在但畸形则视为运维故障、拒绝启动（对应 API 侧 503 unavailable），不伪造、不降级、不静默重生成。

自动任务当前尚未接入桌面启动链。未来自动化必须以持久化的 `Automation` 和 `AutomationRun` 为事实源，并由可见、可暂停、失败可追溯的运行契约驱动；客户端不得启动旧的内存 Episode 循环。

## 图标和权限

- `assets/icon.icns`：macOS 构建输入。
- `assets/icon.png`：通用 512px 源图。
- `assets/generate_icon.py`：从同一图形规则重新生成上述两份资产。
- `assets/entitlements.plist`：Hardened Runtime/JIT 权限。

中间尺寸 PNG 不进入仓库；需要时由生成脚本在内存中构造。

## 发布

内部包和正式签名 Release 的区别、所需密钥和操作顺序见 [`RELEASE.md`](RELEASE.md)。
