# 内测产品基础：身份、工作区与首启

本文定义当前内测版实际采用的身份和首启边界。身份模式必须显式选择，不能在配置失败时悄悄从安全模式降级。

## 当前内部模式

GitHub 的 `Internal macOS test build` 使用：

```text
NEXT_PUBLIC_AUTH_MODE=local
```

它只面向少量可信测试者：

- 不显示登录或退出流程，直接进入本地工作区。
- 所有 Episode、画像和偏好记录归入保留的 `local` workspace。
- 不需要 Supabase URL 或 Key。
- 首启仍必须完成目录、模型和依赖预检；免登录不等于跳过运行配置。

内部模式不得用于公开分发，也不能声称具备多用户隔离。

## 后续分发模式

Supabase 模式保留在代码中，目标旅程是：

```text
管理员邀请
→ 用户设置密码并登录
→ 服务端创建个人 workspace
→ 完成本地目录与模型预检
→ 服务端确认 onboarding 完成
→ 进入工作台
```

该模式必须满足：

- 关闭公开注册，只允许管理员邀请。
- 浏览器不能伪造用户、workspace、readiness 或 onboarding 完成状态。
- Python API 验证 Bearer JWT，并由服务端解析 workspace membership。
- Supabase secret key 只存在于可信服务端，不得进入 Electron 或 `NEXT_PUBLIC_*`。
- 本地 SQLite 按 workspace 分区；跨 workspace 资源返回 404，避免泄露存在性。

详细安全边界见 [`auth-workspace-boundary.md`](auth-workspace-boundary.md)。

## 数据边界

- 云端：账号、workspace、membership、首启状态和必要的非敏感元数据。
- 本地：视频、Obsidian 原文、剪映草稿、目录授权、AI Key 和媒体处理产物。
- 日志、截图、构建产物和测试证据不得包含密码、token、secret key 或完整私人内容。

## 内测验收

内部模式至少验证：

1. 新机器首次启动能进入首启流程，不需要 Supabase。
2. 配置未完成时能看到缺项、影响和修复入口。
3. 完成配置后进入本地工作台，重启后状态可恢复。
4. API、Web 和 Worker 的启动、异常退出与关闭清理可验证。

启用 Supabase 分发模式前，还必须验证邀请、登录、session 刷新、退出、重复 callback、服务端 onboarding gate 和两位用户的 workspace 隔离。
