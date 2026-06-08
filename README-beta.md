# AI视频日记 Agent Studio 2.0 Beta

本文件随 `AI视频日记-Agent-Studio.dmg` 一起交付，用于 macOS 内测安装和排障。

## 安装

1. 打开 `AI视频日记-Agent-Studio.dmg`。
2. 将 `AI视频日记 Agent Studio.app` 拖入 `Applications`。
3. 首次打开如果提示来自未验证开发者，请在系统设置里允许打开，或在 Finder 中右键 App 后选择打开。

当前 beta 使用本地 ad-hoc 签名，但未使用 Apple Developer ID，也未 notarize，所以 macOS Gatekeeper 拦截仍是预期行为。

## 本机依赖

本 beta 会自动启动本地 API 和 WebView，但仍依赖本机已经具备以下命令：

- `uv`：用于启动 FastAPI API；可选 Worker 也通过它启动。
- `ffmpeg`：用于视频处理。
- `node` 不作为用户手动启动要求；打包后的 WebView 通过 Electron 启动 Next.js standalone 服务。
- Whisper / 腾讯云 ASR 为可选能力，按 App 内配置向导填写。

## 启动前检查

App 启动时会先检查 `uv` 和 `ffmpeg` 是否可用，再启动 API 和 WebView。

如果缺少依赖，主窗口会停在“启动前检查未通过”的提示页，并列出缺失命令。安装缺失命令后重启 App 即可继续；日志目录仍可从菜单栏或托盘菜单打开。

## App 会自动启动什么

启动 App 后，桌面壳会启动：

- API：`uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`
- WebView：`http://127.0.0.1:3000`

默认不会启动常驻 Worker，避免打开 App 时立刻扫描待处理视频并占用启动路径。需要内测后台自动化时，可设置 `AI_VIDEO_DIARY_DESKTOP_AUTOSTART_WORKER=1`，此时会额外启动：

- Worker：`uv run python -m app.automation`

退出 App 时，会关闭 API / 可选 Worker / WebView 子进程。

## 菜单和快捷键

菜单栏和托盘菜单提供：

- 打开工作台。
- 打开日志目录。
- 打开待处理视频目录。
- 打开待发布成片目录。
- 打开草稿输出目录。
- 打开剪映草稿目录。

全局快捷键：

- `Command + Shift + Space`：唤起 Agent Studio 主窗口。

## 默认目录

- 日志：`~/Library/Logs/AI视频日记 Agent Studio`
- 桌面数据库：`~/Library/Application Support/AI视频日记 Agent Studio/agent.sqlite3`
- 桌面运行时配置：`~/Library/Application Support/AI视频日记 Agent Studio/.env`
- 待处理视频：`~/Movies/AI视频日记/01_待处理视频`
- 待发布成片：`~/Movies/AI视频日记/06_待发布成片`
- 草稿输出：`~/Movies/AI视频日记/05_剪映草稿`
- 剪映草稿：`~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft`

## 已知限制

- 当前 beta 使用本机 `uv` 环境启动 API / 可选 Worker，暂未内置独立 Python runtime。
- 当前 beta 只有本地 ad-hoc 签名，未做 Apple Developer ID 签名和 notarize。
- 剪映 10.7 原生加密草稿写入仍为后续项；当前仍以兼容桥和可追溯素材包为主。
- 如启动失败，请先查看日志目录中的 `main.log`、`api.log`、`worker.log`、`web.log`。
