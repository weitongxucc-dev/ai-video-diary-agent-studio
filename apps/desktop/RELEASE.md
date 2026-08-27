# macOS 构建与发布

## 两条构建通道

### 内部测试包

`.github/workflows/internal-macos-test.yml` 只允许手动触发，分别在原生 arm64 和 Intel runner 上构建：

- `auth_mode` 必须显式选择 `local` 或 `supabase`；默认值 `local` 只用于本机开发或受控诊断。
- 交付给独立测试者的包必须选择 `supabase`，并由 GitHub Actions variables 或 secrets 提供 Supabase URL 与 publishable key。
- 分发配置只包含可公开的 URL 和 publishable key；service-role、secret key、账号密码和管理凭据不得进入安装包。
- 使用 uv 管理的 Python 3.11.15 构建 API 与 Worker。
- 校验二进制架构。
- 对 `.app` 做 ad-hoc 签名并启动打包后的 API 健康 smoke。
- 生成 DMG、ZIP 和按架构命名的 `SHA256SUMS-<arch>.txt`。
- Actions Artifact 成功时保留 7 天；配额不可用时，产物会转存到与提交绑定的 GitHub 草稿预发布，不公开展示。

这类包只供可信测试者使用，不具备 Developer ID 信任和 Apple 公证，不能作为公开 Release。

### 正式 Release

推送 `v<version>` tag 会触发 `.github/workflows/release.yml`。Tag 必须与 `apps/desktop/package.json` 的 `version` 完全一致。

工作流会硬性检查以下 GitHub Actions secrets：

- `CSC_LINK`
- `CSC_KEY_PASSWORD`
- `APPLE_ID`
- `APPLE_APP_SPECIFIC_PASSWORD`
- `APPLE_TEAM_ID`

缺少任一项都会失败，不允许生成“看起来成功”的未签名 Release。每个架构必须通过 `codesign`、Gatekeeper `spctl` 和 `stapler` 验证，之后才会统一发布 DMG、ZIP 和 `latest-mac.yml`。

正式发布必须使用 `supabase` 身份模式。发布前必须对实际 `release.yml` 生成的配置做包内检查：账号登录可用，URL 和 publishable key 存在，且不包含任何管理密钥。文档声明不能替代 workflow 的实际配置。

## 发布前门禁

1. 使用干净、精确的最新 `main`。
2. 确认发布 workflow 显式使用 `supabase` 并具备完整的公开连接配置。
3. 更新 `apps/desktop/package.json` 版本并通过完整 CI。
4. 重新生成账号模式测试包，在真实 Electron App 完成登录、首次启动、配置、失败恢复、关闭重开和主要生产链 GUI smoke。
5. 确认签名、公证和自动更新凭据有效。
6. 创建与 package version 一致的 tag；不要复用旧 tag。
7. 下载 Release 产物，复核两个架构、身份模式、包内秘密边界、校验和、签名、公证和更新清单。

## 产物

正式 Release 应同时包含：

- arm64 DMG 与 ZIP。
- x64 DMG 与 ZIP。
- 同时引用两个架构 ZIP 的 `latest-mac.yml`。

任何构建日志、截图、Artifact 名称或发布说明都不得包含环境变量值、证书、密码或 token。
