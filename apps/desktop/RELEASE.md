# macOS 构建与发布

## 两条构建通道

### 内部测试包

`.github/workflows/internal-macos-test.yml` 只允许手动触发，分别在原生 arm64 和 Intel runner 上构建：

- 显式设置 `NEXT_PUBLIC_AUTH_MODE=local`。
- 使用 uv 管理的 Python 3.11.15 构建 API 与 Worker。
- 校验二进制架构。
- 对 `.app` 做 ad-hoc 签名并启动打包后的 API 健康 smoke。
- 生成 DMG、ZIP 和 `SHA256SUMS.txt`。
- Artifact 仅保留 7 天。

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

## 发布前门禁

1. 使用干净、精确的最新 `main`。
2. 更新 `apps/desktop/package.json` 版本并通过完整 CI。
3. 重新生成内部包，在真实 Electron App 完成首次启动、配置、失败恢复、关闭重开和主要生产链 GUI smoke。
4. 确认签名、公证和自动更新凭据有效。
5. 创建与 package version 一致的 tag；不要复用旧 tag。
6. 下载 Release 产物，复核两个架构、校验和、签名、公证和更新清单。

## 产物

正式 Release 应同时包含：

- arm64 DMG 与 ZIP。
- x64 DMG 与 ZIP。
- 同时引用两个架构 ZIP 的 `latest-mac.yml`。

任何构建日志、截图、Artifact 名称或发布说明都不得包含环境变量值、证书、密码或 token。
