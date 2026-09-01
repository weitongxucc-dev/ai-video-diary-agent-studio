# macOS 桌面应用

Electron 负责启动本地 Next.js 和 FastAPI API，并管理窗口、端口、日志和子进程生命周期。

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

自动任务当前尚未接入桌面启动链。未来自动化必须以持久化的 `Automation` 和 `AutomationRun` 为事实源，并由可见、可暂停、失败可追溯的运行契约驱动；客户端不得启动旧的内存 Episode 循环。

## 图标和权限

- `assets/icon.icns`：macOS 构建输入。
- `assets/icon.png`：通用 512px 源图。
- `assets/generate_icon.py`：从同一图形规则重新生成上述两份资产。
- `assets/entitlements.plist`：Hardened Runtime/JIT 权限。

中间尺寸 PNG 不进入仓库；需要时由生成脚本在内存中构造。

## 发布

内部包和正式签名 Release 的区别、所需密钥和操作顺序见 [`RELEASE.md`](RELEASE.md)。
