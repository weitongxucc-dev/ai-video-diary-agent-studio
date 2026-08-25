# 后端认证与工作区边界（Beta）

本文档描述 `apps/api` 在 Supabase 分发模式下执行的身份与工作区边界。内部 macOS 测试包显式使用 `NEXT_PUBLIC_AUTH_MODE=local`，只操作保留的本地 workspace；它不经过下述登录流程，也不能用于公开分发。

## 信任模型

- **身份**：Supabase Auth 的永久用户。后端只信任通过 Bearer JWT 验证得到的 `sub`。
- **JWT 验证**：本地离线验证，JWKS 来自
  `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`，只接受 Supabase 的非对称
  `ES256` / `RS256` 签名，并校验 `iss`（默认 `{SUPABASE_URL}/auth/v1`）、
  `aud`（默认 `authenticated`）、`exp` 与 `sub`。
- **工作区解析**：后端使用已验证的用户 access token 和 publishable key 调用
  Supabase Data API `workspace_members` 表，RLS 只返回该用户被授权的工作区，
  并优先 `owner` 角色。
  客户端请求体中的 `user_id` / `workspace_id` 一律忽略。
- **公开端点**：只有真正的“登录前/本机就绪”探测保持公开，包括
  `/api/runtime/health`、`/api/setup/preflight`、`/api/is_setup`、
  `/api/system/status`、`/api/version`、`/api/workflow/status`、
  `/api/content-templates`。其余产品数据端点都需要有效令牌。
- **首启完成**：`POST /api/onboarding/complete` 不接受客户端的
  `ready` 或步骤布尔值。后端先重新运行本机 preflight，只在结果仍为
  `ready` 时幂等创建当日首条 Episode，再用当前用户 access token 调用
  `complete_own_onboarding` RPC。RPC 校验 `auth.uid()` 和工作区成员关系。
- **首启门禁**：Python API 会以当前用户身份读取已解析 workspace 的
  `completed_at`。未完成首启的用户即使直接访问本机 API，也只能调用
  `/api/onboarding/complete` 与 `/api/settings/runtime` 修复首启，不能绕过
  Next.js 的页面门禁访问产品数据。

## HTTP 语义

- **401 Unauthorized**：缺失、格式错误、过期或无法验证的令牌。
- **403 Forbidden**：令牌有效，但该用户没有任何被授权的工作区成员关系、
  无法解析首启状态，或尚未完成首启却访问非修复 API。
- **404 Not Found**：资源属于其它工作区时返回 404，避免泄露资源存在性。

## 数据分区

- SQLite `episodes` 与 `creator_profile` 表新增 `workspace_id` 列，并以
  `(workspace_id, episode_date)` 唯一约束替换原先全局唯一的 `episode_date`。
- 所有持久化记录（episode、创作者画像、偏好状态、信息源同步状态）都按
  `workspace_id` 分区。
- **旧数据安全迁移**：迁移前没有 `workspace_id` 的历史行被归入保留的
  `local` 工作区。任何经过认证的账号都无法看到 `local` 工作区的数据，
  从而杜绝静默的跨账号可见性。
- 未配置 Supabase（`SUPABASE_URL` / `SUPABASE_PUBLISHABLE_KEY` 为空）时，后端保持
  本地单用户模式，所有记录写入 `local` 工作区，行为与旧版本一致。

## 生成链路的失败安全（fail-safe）

- 认证启用后，仓库查询与创作者画像读取都必须从“活动工作区上下文”解析
  workspace，绝不隐式回退到保留的 `local` 工作区。上下文丢失时抛出
  `WorkspaceContextRequiredError`，让生成请求失败而不是把他人画像注入提示词。
- HTTP 中间件为每个产品数据请求设置工作区上下文；后台视频任务由
  `VideoJobRunner.submit` 显式重设上下文；本地自动化 Worker 则用
  `_local_workspace_scope()` 显式声明自己只操作 `local` 工作区。
- 创作者画像（`/api/creator-profile*`）与 LLM 提示词注入
  （`DeepSeekScriptGenerator._profile_context`）都显式按活动工作区读取，
  两个工作区的画像/提示词不会交叉。

## 环境变量

见 `.env.example` 中的“内测账号与工作区”小节：
`NEXT_PUBLIC_AUTH_MODE`、
`SUPABASE_URL`、`SUPABASE_PUBLISHABLE_KEY`、
`SUPABASE_JWT_AUDIENCE`、`SUPABASE_JWT_ISSUER`。

## 管理员创建测试账号

测试者只接触产品账号和初始密码，不接触邮箱或 Supabase。管理员在受信任终端中
临时设置项目 URL 和 Supabase secret key（旧项目也可用 service-role key），然后运行：

```bash
cd apps/api
SUPABASE_URL=https://your-project.supabase.co \
SUPABASE_ADMIN_SECRET_KEY=your-secret-key \
uv run python -m app.account_admin tester01 --generate-password
```

管理员 secret 仅供该命令调用 Supabase Admin API；不要写进仓库、应用 `.env`、
GitHub Actions 构建变量或安装包。应用运行时只需要 URL 和 publishable key。

## 已知风险

- 每次产品数据请求都会向 Supabase Data API 发起一次成员关系查询（网络往返）。
  这是非阻断的性能优化项：当前不缓存以避免任何可能的跨账号缓存污染；
  后续只能引入按用户严格隔离的缓存，不能使用进程级全局缓存。
- 视频/草稿文件仍为本机磁盘路径，不按工作区分区（属本机资源，不跨账号同步）。
- 本地自动化 Worker 在认证启用时仍显式运行于 `local` 工作区（本地单机守护
  进程，不触碰任何登录账号数据）；如内测版不需要本地定时自动化，可考虑在
  认证启用时禁用该 Worker。
