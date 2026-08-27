# 认证、首启与工作区边界

状态：安全不变量权威
职责：定义账号、workspace、首启和本地资源的信任边界。公开端点、表结构和环境变量的当前集合以代码、迁移和 .env.example 为准，不在本文复制清单。

## 1. 身份模式

交付配置必须显式声明一种模式：

- local：只用于本机开发或受控诊断；不显示登录，只操作保留的 local workspace，不具备多用户隔离能力。
- supabase：用于交付给独立测试者或用户；界面只显示管理员创建的账号和密码。

Web 分发配置与 API 认证配置必须一致。API 只有在 Supabase URL 和 publishable key 成对有效时才能启用 Supabase 校验；声明为 supabase 但配置缺失时，构建或首启预检必须阻断，不能静默降级为 local。开发者未声明分发模式且没有 Supabase 配置时可以运行 local，但不得把该行为包装成账号分发版。

身份配置入口以 apps/api/app/config.py、Web 认证配置和实际构建 workflow 为准。

## 2. 信任模型

- 后端只信任通过 Bearer JWT 验证得到的用户标识，不信任客户端提交的 user_id、workspace_id、ready 或 completed。
- JWT 只接受配置允许的非对称签名算法，并校验 issuer、audience、过期时间和 subject。
- 后端使用已验证的用户身份解析 workspace membership；客户端不能选择一个自己不属于的 workspace。
- 产品数据端点默认需要身份和已完成首启。登录前健康检查与修复端点采用显式 allowlist，当前列表由 apps/api/app/main.py 和对应测试拥有，本文不复制。
- 资源属于其他 workspace 时返回 404，避免泄露资源存在性。

HTTP 语义：

- 401：令牌缺失、格式错误、过期或无法验证；
- 403：身份有效，但没有可用 workspace、首启未完成或操作不被授权；
- 404：对象不存在，或属于其他 workspace；
- 409：当前状态不允许该命令，或幂等键与已有请求冲突。

## 3. 首启门禁

客户端只能发起“完成首启”，不能提交 ready 结果。服务端必须在当前机器上重新运行核心预检，确认素材输入、成片输出和 ffmpeg 等最小视频链路可用，再幂等创建用户可见的首个创作任务记录并完成 workspace onboarding。

可选的 ASR、知识库连接、剪映、外部模型、图片模型、人物服务和平台连接不作为全局首启硬门禁。它们在对应功能入口做能力级预检，并清楚说明影响范围和修复动作。

未完成首启的用户只能访问身份、核心预检和首启修复所需的有限能力，不能直接调用产品数据端点绕过 Web 页面。

## 4. Workspace 分区

- 所有任务、模板、人物、偏好、来源、素材元数据、自动任务、发布和监控记录都属于一个明确 workspace。
- Repository 的每个查询和写入必须从已验证的活动 workspace 上下文取得范围；不能接受调用方随意传入 workspace 绕过上下文。
- 认证模式中如果活动 workspace 上下文丢失，操作必须失败，不得回退到 local。
- 历史上没有 workspace_id 的本地数据只归入保留的 local workspace；认证账号不能自动继承或看见这些数据。
- 实际表和迁移由 apps/api/app/repository.py、models.py、schemas.py 与 supabase/migrations 共同证明；修改时必须有跨 workspace 负向测试。

## 5. 后台任务

- Worker、视频任务和自动任务在提交时固定 workspace、用户或明确的 local 作用域。
- 任务恢复、重试和定时触发继续使用原 workspace，不能从当前登录用户或全局变量重新猜测。
- 缺少身份或 workspace 上下文时不读写登录账号数据。
- 缓存、模型结果和配音键如果包含私人内容，至少按 workspace 和决定结果的模型/版本隔离。
- 进程级缓存不得产生跨用户命中；任何 membership 缓存必须以已验证用户和 workspace 为键并有有界有效期。

## 6. 本地文件与云端边界

本地视频、音频、图片、草稿、模型、缓存和密钥不会因为有 Supabase 账号就自动上传。workspace 隔离也不等于操作系统用户隔离：同一 macOS 用户下的不同产品账号可能获得相同目录的系统权限。

因此：

- 每个本地文件引用还必须经过工作区授权根目录和用途检查；
- 云端模型或发布平台需要上传内容时，界面说明发送范围并取得相应授权；
- 密钥不进入数据库普通字段、日志、截图、构建变量或安装包；
- 删除本地文件前解析精确目标并说明影响与可恢复性。

## 7. 管理员账号

测试者只接触产品账号和初始密码，不接触邮箱或 Supabase。管理员在受信任终端临时设置项目 URL 和管理员 secret，然后运行：

    cd apps/api
    SUPABASE_URL=https://your-project.supabase.co \
    SUPABASE_ADMIN_SECRET_KEY=your-secret-key \
    uv run python -m app.account_admin tester01 --generate-password

管理员 secret 只供该命令调用 Supabase Admin API；不得写入仓库、应用环境文件、GitHub Actions 普通构建变量或安装包。应用运行时只使用 URL 和 publishable key。

## 8. 最小安全验收

任何认证或 workspace 改动至少证明：

1. 无令牌、坏令牌和过期令牌不能读取产品数据。
2. 用户 A 不能读取、修改、推断或触发用户 B 的对象。
3. 客户端伪造 user_id、workspace_id、ready 或 completed 无效。
4. 声明 supabase 但配置缺失会阻断，不会进入 local。
5. 首启只能由服务端当前预检结果完成。
6. 后台任务、重试和缓存不会丢失或切换 workspace。
7. local 历史数据不会暴露给认证账号。
8. 两个真实测试账号在登录、session 恢复、退出和再次启动后仍保持隔离。
