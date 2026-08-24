# Changelog

只记录已经进入当前主线的重要变化；详细历史以 Git commit 和已合并 PR 为准。

## Unreleased — internal beta baseline

### 2026-08-14

- 合并 Supabase 邀请登录、JWT 校验、onboarding 和 workspace 隔离。
- 为可信内测包增加显式本地身份模式；Supabase 模式继续保留给后续分发版本。
- 加固 macOS 打包：固定 uv 管理的 Python 3.11.15，分别构建 arm64/x64 API 与 Worker。
- 正式 Release 强制 Developer ID 签名、Apple 公证及 Gatekeeper/stapler 验证。
- 内部 macOS 构建加入架构检查、API 健康 smoke、DMG/ZIP 与 SHA-256 清单。

### 当前发布门禁

- 在真实 Electron App 完成首次启动、目录授权、失败恢复、关闭重开和主要生产链 GUI 验收。
- 配置正式签名、公证与自动更新所需凭据。
- 生成新的未过期内测包并完成小范围用户反馈。
