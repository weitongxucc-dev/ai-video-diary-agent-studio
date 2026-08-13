# 后端认证与工作区边界（Beta）

本文档描述 `apps/api` 后端在分发内测版时执行的身份与工作区边界。

## 信任模型

- **身份**：Supabase Auth 的永久用户。后端只信任通过 Bearer JWT 验证得到的 `sub`。
- **JWT 验证**：本地离线验证，JWKS 来自 `{SUPABASE_URL}/auth/v1/jwt/jwks`，
  校验 `iss`（默认 `{SUPABASE_URL}/auth/v1`）、`aud`（默认 `authenticated`）、
  `exp` 与 `sub`。
- **工作区解析**：后端使用 `SUPABASE_SECRET_KEY` 调用 Supabase Data API
  `workspace_members` 表，服务端解析该用户被授权的工作区，优先 `owner` 角色。
  客户端请求体中的 `user_id` / `workspace_id` 一律忽略。
- **公开端点**：只有真正的“登录前/本机就绪”探测保持公开，包括
  `/api/runtime/health`、`/api/setup/preflight`、`/api/is_setup`、
  `/api/system/status`、`/api/version`、`/api/workflow/status`、
  `/api/content-templates`。其余产品数据端点都需要有效令牌。

## HTTP 语义

- **401 Unauthorized**：缺失、格式错误、过期或无法验证的令牌。
- **403 Forbidden**：令牌有效，但该用户没有任何被授权的工作区成员关系
  （或工作区解析失败）。
- **404 Not Found**：资源属于其它工作区时返回 404，避免泄露资源存在性。

## 数据分区

- SQLite `episodes` 与 `creator_profile` 表新增 `workspace_id` 列，并以
  `(workspace_id, episode_date)` 唯一约束替换原先全局唯一的 `episode_date`。
- 所有持久化记录（episode、创作者画像、偏好状态、信息源同步状态）都按
  `workspace_id` 分区。
- **旧数据安全迁移**：迁移前没有 `workspace_id` 的历史行被归入保留的
  `local` 工作区。任何经过认证的账号都无法看到 `local` 工作区的数据，
  从而杜绝静默的跨账号可见性。
- 未配置 Supabase（`SUPABASE_URL` / `SUPABASE_SECRET_KEY` 为空）时，后端保持
  本地单用户模式，所有记录写入 `local` 工作区，行为与旧版本一致。

## 环境变量

见 `.env.example` 中的“内测账号与工作区”小节：
`SUPABASE_URL`、`SUPABASE_PUBLISHABLE_KEY`、`SUPABASE_SECRET_KEY`、
`SUPABASE_JWT_AUDIENCE`、`SUPABASE_JWT_ISSUER`。

## 已知风险

- 每次产品数据请求都会向 Supabase Data API 发起一次成员关系查询（网络往返）。
  后续可加入按用户缓存。
- 脚本生成时注入的“创作者画像”提示词仍从 `local` 工作区读取；将工作区
  上下文贯穿 LLM 生成链路是后续工作。
- 视频/草稿文件仍为本机磁盘路径，不按工作区分区（属本机资源，不跨账号同步）。
