# 首次受邀账号的身份入口接线建议

状态：只读接线建议；未改产品代码。观察基线：集成工作树 `8ef879e` 与 WIL-67 候选 `422cd42`。已纳入主控对接受后空间选择的接线决策；供主控分配文件 owner 和验收，不表示邀请产品闭环已完成。

## 阻断证据

- `apps/api/app/auth.py:251-277` 的 `authenticate()` 已先验证 Bearer JWT，却接着要求现成的 `workspace_members`；首次受邀者尚未接受邀请，因此在调用 `device_accept_invite` 前就得到 403。
- `apps/api/app/main.py:579-617` 对所有非公开路径调用上述方法、查询首启状态，并在缺空间时使用 `LEGACY_WORKSPACE_ID` 设置上下文。邀请接受不能走产品数据的空间上下文，也不能把它列为公开端点。
- `apps/web/src/middleware.ts:67-105` 同样在 API 代理前以首启状态阻断请求；`apps/web/src/app/signin/actions.ts:46` 和 `apps/web/src/app/auth/callback/route.ts:50` 登录成功后无条件创建个人空间。邀请首次登录应先接受绑定到预留目标空间的凭证。
- WIL-67 的 `device_accounts.py:68-101,148-162` 已消费带验证 JWT 的 `AuthPrincipal`，没有要求 `workspace_id`；迁移中的 `device_accept_invite` 再以 `auth.uid()`、设备证明、token 摘要、目标预留状态、并发设备行锁和五人上限做数据库判定。当前缺的是到达这个 RPC 的窄入口。

## 建议的最小接线

1. **分开两种认证结果。** 在 `SupabaseAuthContext` 增加仅验证 Bearer JWT 的身份方法，复用现有 `extract_bearer_token()`、`verify_token()`，从已验证 `sub` 构造 `AuthPrincipal(user_id, workspace_id=None, access_token=token)`。保留 `authenticate()` 的现有行为，用于全部产品数据路径；不要让调用者自行填 `user_id`、`workspace_id` 或降级为 local。
2. **只给一个 POST 接受入口身份级通行。** 由 `main.py` owner 定下精确路径（建议 `POST /api/device-invites/accept`）。中间件仅对该方法和路径使用身份方法，跳过 membership/首启查询；直接把无 workspace 的 principal 交给 `DeviceAccountService.accept_invite()`。该路径不设置 workspace 上下文，不触碰任何 SQLite Repository。未配置 Supabase、缺/坏/过期 JWT 仍为 401 或明确不可用，不能进入本地模式；其他所有路径继续走原 `authenticate()` 和首启门禁。邀请 token 只作为 POST 请求体中的一次性凭证，不是身份或空间声明；设备 ID/proof 只从可信 native runtime 注入，不接受请求体或 renderer 自报。
3. **由 RPC 绑定目标，再进入普通空间路径。** RPC 成功返回 `target_workspace_id`，本次接受后即选择该目标空间；在同一 JWT 下重新查询对该目标有效的 membership 后才进入产品页。后续每次产品请求仍须验证当前用户对选中空间的成员权限和首启状态，客户端保存的选中 ID 只是选择，不是授权事实。不得把无 workspace principal 带入产品数据端点，不得以 `LEGACY_WORKSPACE_ID` 顶替。失败按 `DeviceAccountError.kind` 映射：`forbidden`→403、`conflict`→409、`invalid`→400、`unavailable`→503；不创建替代个人空间或假成功。
4. **Web 仅开放对应受限路径。** `routing.ts`/`middleware.ts` 对精确接受 API 路径允许有 JWT、但未首启的请求代理到本机 API；保持未登录不能接受，且本机 API 自己重验 JWT。邀请页面可在未首启状态显示必要输入/错误，普通产品页仍受门禁。邀请登录/回调路径暂不调用 `bootstrapPersonalWorkspace()`；普通登录行为不因这个入口整体放宽。Supabase Auth 的 `type=invite` 是身份提供商邀请，与设备成员邀请 token 不是同一凭证，不能靠它推断设备目标空间。邀请 token 不放入日志、分析或长驻 URL。
5. **已确认的目标空间选择。** 接受邀请后以 RPC 返回且已验证 membership 的目标 workspace 作为本次进入空间。已有个人或其他空间保留，也允许该账号接受邀请；不能因 `resolve_workspace_id()` 优先选 owner 空间/最早记录而跳回旧空间。后续请求逐次验证当前用户对目标空间的成员权限；仅凭 `next`、客户端选中 ID 或默认排序均不能授权进入。具体跨请求选择传递与验证接线仍由主控分配对应 owner。

## 文件 owner 与最小验收

- `auth.py` 当前不在 WIL-67 三文件授权内，需由主控指定唯一写者；`main.py` 由 MAKE 独占；Web 登录、回调、代理路由由主控明确分包。WIL-67 的三文件候选继续冻结；本建议不授权改动共享权威。
- API：无/坏/过期 JWT 在接受入口为 401；有效 JWT 且零 membership 能到 RPC；错设备/错 proof/过期或撤销邀请被拒；相同账号重复接受返回同一目标；同一零 membership 身份请求任何产品数据仍为 403；绝不设置 local workspace 上下文。加入一项直接访问 `127.0.0.1` API 的测试，避免只靠 Next 代理保护。
- Web：首次受邀登录或会话恢复没有自动个人空间；未首启状态可提交接受请求；接受后进入**数据库实际返回且重新验证 membership** 的预留目标，并按该空间首启；普通登录和未登录门禁不回退。另测已有个人或其他 workspace 的受邀账号：原空间保留，接受成功后进入邀请目标，后续请求撤销其目标 membership 时拒绝访问，不能因默认排序跳回旧空间或凭客户端 ID 继续访问。
- 本建议只解决身份入口。首次设备注册/认领、真实多账号桌面闭环和对象级共享仍是独立验收缺口；QA 的组件 PASS 不替代这些结果。
