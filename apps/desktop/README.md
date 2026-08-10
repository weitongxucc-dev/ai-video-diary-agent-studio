# AI视频日记 Agent Studio — macOS 桌面应用

## 前置依赖

- **Node.js** ≥ 20
- **pnpm** (`npm install -g pnpm`)
- **Python** ≥ 3.11（用于 PyInstaller 打包和图标生成）
- **ffmpeg**（`brew install ffmpeg`）
- **PyInstaller**（`pip install pyinstaller`）
- **Pillow**（`pip install Pillow`，用于图标生成）

## 快速开始（开发模式）

```bash
# 终端 1: 启动后端
cd ../api && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 终端 2: 启动前端
cd ../web && pnpm dev

# 终端 3: 启动 Electron（加载 localhost:3000）
NODE_ENV=development pnpm start
```

## 生产构建

```bash
# 一键构建 .app 和 .dmg
bash build.sh
```

输出在 `dist/mac/` 目录。

## 项目结构

```
apps/desktop/
├── main.js          # Electron 主进程（窗口管理、API 子进程）
├── preload.js       # 预加载脚本（暴露安全 API 给渲染进程）
├── package.json     # 依赖和 electron-builder 配置
├── build.sh         # 一键构建脚本
├── assets/
│   ├── icon.icns              # macOS 应用图标
│   ├── icon.png               # 通用图标
│   ├── entitlements.plist     # macOS 权限声明
│   └── generate_icon.py       # 图标生成脚本
└── dist/
    ├── api-server             # PyInstaller 编译的 API 二进制
    └── mac/                   # electron-builder 输出
```

## 架构

```
┌─────────────────────────────────────┐
│ Electron Main Process               │
│                                     │
│  ┌──────────┐    ┌───────────────┐  │
│  │ Browser  │    │ API Server    │  │
│  │ Window   │◄───│ (Python       │  │
│  │ (Next.js │    │  subprocess)  │  │
│  │  static) │    │ port: random  │  │
│  └──────────┘    └───────────────┘  │
│                                     │
│  Lifecycle:                         │
│  start → spawn API → wait ready     │
│  → create window → load UI          │
│  quit → SIGTERM API → cleanup       │
└─────────────────────────────────────┘
```

## 注意事项

- **ffmpeg** 未打包进 .app（体积太大），用户需单独安装
- **Whisper** 标记为可选依赖，缺失时跳过重录检测
- 首次启动会检测依赖并在欢迎向导中提示
- .dmg 未签名公证，macOS Gatekeeper 会提示"无法验证开发者"——右键打开即可
