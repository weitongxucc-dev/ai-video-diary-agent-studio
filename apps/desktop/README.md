# macOS 桌面应用

Electron 负责启动本地 Next.js、FastAPI API 和自动化 Worker，并管理窗口、端口、日志和子进程生命周期。

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

该命令会构建 Next.js standalone、PyInstaller API/Worker 和 Electron 包。产物位于 `apps/desktop/dist/`，该目录不进入 Git。

本机 Python/OpenSSL 和 CPU 架构差异容易产生只能在构建机运行的二进制。发给测试者时，应优先使用 GitHub Actions 的原生 arm64/x64 内测构建，而不是随手转发本机构建。

## 运行结构

```text
Electron main
├── Next.js standalone
├── FastAPI api-server
├── automation worker-server
└── BrowserWindow
```

主进程必须等待服务健康后再加载工作台；退出时关闭所有子进程。打包后健康检查失败应显示可恢复错误，不能留下后台孤儿进程。

## 图标和权限

- `assets/icon.icns`：macOS 构建输入。
- `assets/icon.png`：通用 512px 源图。
- `assets/generate_icon.py`：从同一图形规则重新生成上述两份资产。
- `assets/entitlements.plist`：Hardened Runtime/JIT 权限。

中间尺寸 PNG 不进入仓库；需要时由生成脚本在内存中构造。

## 发布

内部包和正式签名 Release 的区别、所需密钥和操作顺序见 [`RELEASE.md`](RELEASE.md)。
